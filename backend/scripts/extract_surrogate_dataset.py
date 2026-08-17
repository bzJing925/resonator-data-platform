"""代理模型训练数据提取（规格 §5，v4）。

用法：
    uv run python scripts/extract_surrogate_dataset.py \
        --db-url sqlite:////Users/jingbozuo/.aln-data/aln-data.db \
        --data-root /Users/jingbozuo/.aln-data \
        --batch-no '#2' \
        --out ./surrogate_data
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import create_engine, select

# 把 backend 目录加入路径，以便 import app（同 train_sparse_reconstructor.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import AlgorithmConfig  # noqa: E402
from app.core.sparse_sampler import read_z_mag_db  # noqa: E402
from app.ml.surrogate.baseline import BaselineTable  # noqa: E402
from app.ml.surrogate.extract import (  # noqa: E402
    _group_tuple,
    assemble_npz,
    build_device_record,
    clean_records,
    compute_norm_stats,
    fill_sparse_base,
    select_per_cell,
    split_groups,
)
from app.models import Batch, Device, MappingEntry  # noqa: E402

log = logging.getLogger("extract_surrogate")

_PARAM_COLS = list(AlgorithmConfig.surr_param_cols)


def _fetch_rows(db_url: str, batch_no: str) -> list[dict]:
    """查询批次器件行，JOIN mapping_entries 取 has_pf/mark；剔除关联失败的行。"""
    engine = create_engine(db_url)
    cols = [
        Device.id,
        Device.eg,
        Device.fl,
        Device.area_um2,
        *[getattr(Device, c) for c in _PARAM_COLS],
        Device.s_param_path,
        Device.s_param_port,
        Device.mark,
        Batch.batch_no,
        Batch.mapping_id,
        MappingEntry.has_pf,
    ]
    stmt = (
        select(*cols)
        .select_from(Device)
        .join(Batch, Device.batch_id == Batch.id)
        .join(
            MappingEntry,
            (MappingEntry.mark == Device.mark) & (MappingEntry.mapping_id == Batch.mapping_id),
        )
        .where(
            Batch.batch_no == batch_no,
            Device.eg.is_not(None),
            Device.fl.is_not(None),
            Device.area_um2.is_not(None),
            Device.s_param_path.is_not(None),
        )
        .order_by(Device.id.asc())
    )
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(stmt).mappings().all()]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="代理模型训练数据提取")
    ap.add_argument("--db-url", required=True)
    ap.add_argument("--data-root", required=True, help="DATA_ROOT（含 files/ 子目录）")
    ap.add_argument("--batch-no", required=True)
    ap.add_argument("--out", default="./surrogate_data")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0, help="调试：只处理前 N 条（0=全部）")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    files_dir = Path(args.data_root) / "files"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)  # baseline.npz 先于 assemble_npz 落盘

    rows = _fetch_rows(args.db_url, args.batch_no)
    if args.limit > 0:
        rows = rows[: args.limit]
    log.info("查询到 %d 条器件记录（已 ⋈ mapping_entries，关联失败已剔除）", len(rows))

    # 基准网格缺档预检（避免跑完昂贵的多进程阶段才在 BaselineTable.build 报错）
    base_rows = [r for r in rows if r["eg"] == 0.0 and r["fl"] == 0.0]
    pfs = {int(r["has_pf"]) for r in base_rows}
    areas = {int(r["area_um2"]) for r in base_rows}
    combos = {(int(r["has_pf"]), int(r["area_um2"])) for r in base_rows}
    if not base_rows or len(combos) != len(pfs) * len(areas):
        raise SystemExit(
            f"基准（eg=0/fl=0）网格缺档：pf 档 {sorted(pfs)}、面积档 {len(areas)} 个，"
            f"实际 (pf, area) 组合 {len(combos)} ≠ {len(pfs)}×{len(areas)}"
        )

    # 频率轴：探测前 20 个可读文件，取 (fmin, fmax) 众数范围
    # （防单条扫频范围异常的文件把全数据集插值到错误轴上）
    probes = []
    for row in rows:
        if len(probes) >= 20:
            break
        p = files_dir / str(row["batch_no"]) / str(row["s_param_path"])
        if not p.exists():
            continue
        try:
            axis, _ = read_z_mag_db(
                p, port=row.get("s_param_port") or "S11", target_n_freq=AlgorithmConfig.surr_n_freq
            )
        except Exception:
            continue  # 存在但不可读：跳过
        probes.append(((round(float(axis[0]), 6), round(float(axis[-1]), 6)), axis))
    if not probes:
        raise SystemExit("没有任何可读的 S 参数文件")
    modal_key = Counter(k for k, _ in probes).most_common(1)[0][0]
    freq_axis = next(a for k, a in probes if k == modal_key)
    log.info(
        "频率轴: %.3f - %.3f GHz, %d 点（探测 %d 个文件，众数端点 %s）",
        freq_axis[0],
        freq_axis[-1],
        len(freq_axis),
        len(probes),
        modal_key,
    )

    # 多进程构建记录（build_device_record 为顶层函数，可 pickle）
    records: list[dict] = []
    with ProcessPoolExecutor(max_workers=args.workers) as exe:
        futs = [
            exe.submit(build_device_record, row, files_dir, freq_axis, AlgorithmConfig.surr_k)
            for row in rows
        ]
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            if rec is not None:
                records.append(rec)
            if i % 2000 == 0:
                log.info("进度 %d/%d，成功 %d", i, len(rows), len(records))
    log.info("成功构建 %d 条记录", len(records))

    # 清洗（单元内 → 趋势面 → 物理非法）
    kept, dropped = clean_records(records)
    log.info("清洗: 保留 %d，剔除 %d", len(kept), len(dropped))

    # 基准表（用全部合法基准格记录，不受抽样限制）+ 回填
    baseline = BaselineTable.build(kept)
    baseline.save(out_dir / "baseline.npz")
    for r in kept:
        r["params_base"], _ = baseline.lookup(r["area_um2"], r["has_pf"])
    fill_sparse_base(kept, baseline)

    # 每格抽样（v4：≤ surr_per_cell_max 条/格）
    selected = select_per_cell(kept)
    log.info(
        "每格抽样: %d → %d（每格 ≤ %d）",
        len(kept),
        len(selected),
        AlgorithmConfig.surr_per_cell_max,
    )

    # 物理组划分 + 统计 + 落地
    groups = sorted({_group_tuple(r) for r in kept})
    splits = split_groups(groups)
    stats = compute_norm_stats(kept, splits)
    assemble_npz(
        selected, groups, splits, stats, dropped, out_dir, freq_axis, AlgorithmConfig.surr_k
    )
    n_train = sum(1 for g in groups if splits[g] == "train")
    n_val = sum(1 for g in groups if splits[g] == "val")
    n_test = sum(1 for g in groups if splits[g] == "test")
    log.info(
        "完成: %d 物理组（train %d / val %d / test %d）→ %s",
        len(groups),
        n_train,
        n_val,
        n_test,
        args.out,
    )


if __name__ == "__main__":
    main()
