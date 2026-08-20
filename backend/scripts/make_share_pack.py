"""同事侧分享包制作：snp zip → 参数分享包（MB 级，可传 GitHub）。

与平台摄取管线完全同源（DutProcessor + extract_resonator_params），
输出仅含提取参数（不含原始 snp）——静态网站只展示参数，不丢任何网页可见信息。

用法（在 backend/ 目录下）：
    uv run python scripts/make_share_pack.py 数据.zip --batch-no '#5' \
        --mapping ../mappings/1_ELB003.xlsx -o 分享包_5.zip

分享包内容：
    devices.csv.gz   每行一个器件端口的提取参数（与平台 devices 表同构）
    meta.json        批次信息 + 格式版本 + sha256 校验
    mapping.json     解析后的对照表条目（CI 侧合并用）
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.filename import parse_filename  # noqa: E402
from app.core.mapping import load_mapping  # noqa: E402
from app.workers.pipeline.processor import DutProcessor  # noqa: E402

FORMAT_VERSION = 1

# 分享包列（= devices 表减去 id/batch_id/s_param_path；merge 侧再赋 id/batch_no）
PACK_COLUMNS = [
    "original_filename", "display_name", "mark", "wafer", "folder_name", "coord",
    "x", "y", "eg", "fl", "ag", "pf", "area_n", "area_um2",
    "fs_ghz", "fp_ghz", "zs_ohm", "zp_ohm", "qs", "qp", "qs_bodeq", "qp_bodeq",
    "dbqs", "dbqp", "bodeq_fitted", "bodeq_smooth", "bodeq_raw", "fbode_ghz",
    "k2eff_pct", "fp2_ghz", "fs2_ghz", "zp2_ohm", "zs2_ohm", "deembedded", "s_param_port",
]

_CAL_KEYWORDS = ("OPEN", "SHORT", "WO", "WS")


def _looks_like_calibration(name: str) -> bool:
    upper = name.upper()
    return any(kw in upper for kw in _CAL_KEYWORDS)


def _wafer_from_batch_no(batch_no: str) -> int | None:
    import re

    m = re.search(r"\.(\d+)$", batch_no)
    return int(m.group(1)) if m else None


def make_share_pack(
    zip_path: str | Path,
    batch_no: str,
    mapping_path: str | Path,
    out_path: str | Path,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    workers: int = 1,
) -> dict:
    """zip → 分享包。返回 meta dict（含统计）。与 pipeline_batch_task 同流程（无去嵌）。"""
    zip_path = Path(zip_path)
    mapping_path = Path(mapping_path)
    if not zip_path.exists():
        raise SystemExit(f"zip 不存在: {zip_path}")
    if not mapping_path.exists():
        raise SystemExit(f"对照表不存在: {mapping_path}")

    mapping = load_mapping(mapping_path)
    if not mapping:
        raise SystemExit(f"对照表为空或解析失败: {mapping_path}")
    print(f"对照表 {mapping_path.name}: {len(mapping)} 条")

    wafer = _wafer_from_batch_no(batch_no)
    processor = DutProcessor(compress_raw=False, keep_deembed_temp=False)

    tmp = Path(tempfile.mkdtemp(prefix=f"sharepack_{batch_no.strip('#')}_"))
    rows: list[dict] = []
    failures: list[str] = []
    try:
        print(f"解压 {zip_path.name} ...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(tmp)

        # 扫描分类（与 pipeline_batch_task 一致：跳过校准件）
        items: list[dict] = []
        n_types = {"s1p": 0, "s2p": 0}
        for p in sorted(tmp.rglob("*")):
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in (".s1p", ".s2p"):
                continue
            if parse_filename(p.name).is_calibration or _looks_like_calibration(p.name):
                continue
            item_type = "s2p" if ext == ".s2p" else "s1p"
            n_types[item_type] += 1
            items.append(
                {"type": item_type, "path": str(p), "s_param_relpath": str(p.relative_to(tmp))}
            )
        if not items:
            raise SystemExit("zip 内未发现可处理的 .s1p/.s2p 器件文件")
        print(f"发现 {len(items)} 个 DUT（s2p: {n_types['s2p']}, s1p: {n_types['s1p']}）")

        for i, item in enumerate(items, 1):
            result = processor.process(item, mapping, wafer, None, tmp)
            rows.extend(result["rows"])
            failures.extend(result["failures"])
            if i % 200 == 0 or i == len(items):
                print(f"  处理 {i}/{len(items)}，失败 {len(failures)}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if not rows:
        raise SystemExit("全部器件提取失败，未生成分享包")

    for r in rows:
        r.pop("s_param_path", None)  # 网站不展示原文件路径

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # devices.csv.gz
    import io

    text_buf = io.StringIO()
    writer = csv.DictWriter(text_buf, fieldnames=PACK_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in PACK_COLUMNS})
    csv_bytes = gzip.compress(text_buf.getvalue().encode("utf-8"), compresslevel=6)

    mapping_json = {
        "name": mapping_path.stem,
        "entries": [
            {
                "mark": e.mark,
                "description": e.description,
                "eg": e.eg,
                "fl": e.fl,
                "ag": e.ag,
                "area_s11": e.area_s11,
                "area_s22": e.area_s22,
                "has_pf": e.has_pf,
            }
            for e in sorted(mapping.values(), key=lambda x: x.mark)
        ],
    }

    process_type = (
        "BOTH" if n_types["s1p"] and n_types["s2p"] else ("S2P" if n_types["s2p"] else "S1P")
    )
    meta = {
        "format_version": FORMAT_VERSION,
        "batch_no": batch_no,
        "mapping_name": mapping_path.stem,
        "f_start_ghz": f_start_ghz,
        "f_end_ghz": f_end_ghz,
        "deembedded": False,
        "process_type": process_type,
        "device_count": len(rows),
        "failure_count": len(failures),
        "created_at": datetime.now(UTC).isoformat(),
        "generator": f"make_share_pack.py@{FORMAT_VERSION}",
        "sha256_devices_csv_gz": hashlib.sha256(csv_bytes).hexdigest(),
    }

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))
        zout.writestr("mapping.json", json.dumps(mapping_json, ensure_ascii=False))
        zout.writestr("devices.csv.gz", csv_bytes)
        if failures:
            zout.writestr("failures.txt", "\n".join(failures))

    size_mb = out_path.stat().st_size / 1024 / 1024
    print(f"分享包已生成: {out_path}  ({size_mb:.1f} MB, {len(rows)} 行, 失败 {len(failures)})")
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(description="snp zip → 参数分享包")
    ap.add_argument("zip", help="snp 压缩包路径")
    ap.add_argument("--batch-no", required=True, help="批次号（如 '#5'，网站上必须唯一）")
    ap.add_argument("--mapping", required=True, help="对照表 xlsx/csv 路径")
    ap.add_argument("-o", "--out", required=True, help="输出分享包路径（.zip）")
    ap.add_argument("--f-start", type=float, default=None, help="频率下限 GHz（可选）")
    ap.add_argument("--f-end", type=float, default=None, help="频率上限 GHz（可选）")
    args = ap.parse_args()
    make_share_pack(
        args.zip, args.batch_no, args.mapping, args.out, args.f_start, args.f_end
    )


if __name__ == "__main__":
    main()
