"""WP4b 数据效率曲线：EPPE 训练子集 n × {从零, EPE预训练→EPPE微调} × 种子。

命题：物理规律跨架构迁移 ⇒ EPPE 新架构只需极少数据。
- Stage 1 主曲线：n ∈ {10,30,100,300,4052(全量)}；scratch（EPPE-only）vs finetune
  （先在全部 EPE train 上训 Stage 1，再在 EPPE 子集上微调，epoch 预算相同）。
  finetune 两个协议并列（protocol 列）：lowlr=lr 降 1/10（原协议）；full-lr=lr/epoch/
  早停与 scratch 完全相同，唯一差异=权重初始化（干净对照，复核 n≥300 反转是否
  低 lr 欠训练所致）。种子：n≤100 → {seed_base,+1,+2}；n≥300 → {seed_base}。
- 全流水线锚点（1 组）：n=100、seed_base，含 Stage 2（EPE-only Stage 2 预训练一次
  作微调初始化；Stage 2 从零/微调均 epoch≤40），评估 test EPPE e2e full RMSE(dB)。
- 测试恒用 EPPE test split（357 样本）保证可比；子集从 EPPE train split 分层抽样
  （按 t_aln 类分层，比例分配+最大余数，n≥5 时每类≥1），无 test 泄漏。
- 训练经子进程驱动 train_stage1_mason_subset.py / train_stage2_mason_subset.py
  （既有 train_stage1/2_mason.py 的 CLI 不支持预训练初始化，且正由另一任务加固，
  故用语义对齐的轻量入口统一两条模式的代码路径，保证 scratch/finetune 可比）。
- 幂等：run 目录已有 eval json 则跳过，可安全重跑续跑。

产物：
    surrogate_data_mason/evals/data_efficiency.json  （全记录）
    surrogate_data_mason/evals/data_efficiency.xlsx  （Origin 画图用；sheet1 stage1_curve /
        sheet2 pipeline_anchor / sheet3 readme）

用法：
    uv run python scripts/mason_surrogate/run_data_efficiency.py --phase all
    uv run python scripts/mason_surrogate/run_data_efficiency.py --phase aggregate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

PROJECT_ROOT = Path(__file__).resolve().parents[2]  # backend/
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
for p in (PROJECT_ROOT, SCRIPTS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

SCRIPT_S1 = SCRIPTS_DIR / "mason_surrogate" / "train_stage1_mason_subset.py"
SCRIPT_S2 = SCRIPTS_DIR / "mason_surrogate" / "train_stage2_mason_subset.py"

SEED_BASE = 20260913
NS = (10, 30, 100, 300, 4052)
SMALL_N_MAX = 100  # n≤100 → 3 种子
ANCHOR_N = 100
T_ALN_CLASSES = (50.0, 80.0, 100.0, 120.0, 150.0)

DEFAULT_CFG = {
    "data": "surrogate_data_mason/unified_dataset.npz",
    "out_root": "checkpoints_mason/dataeff",
    "eval_out": "surrogate_data_mason/evals/data_efficiency.json",
    "xlsx_out": "surrogate_data_mason/evals/data_efficiency.xlsx",
    "epochs": 300,
    "patience": 30,
    "lr": 1e-3,
    "batch_size": 256,
    "epochs_stage2": 40,
    "patience_stage2": 20,
    "lr_stage2": 1e-3,
    "max_freq": 1.0,
    "seed_base": SEED_BASE,
    "ns": list(NS),
    "finetune_protocols": "lowlr,full-lr",
}


# ---------------------------------------------------------------- 子集抽样


def stratified_subset(labels: NDArray, n: int, seed: int) -> NDArray[np.int64]:
    """按标签分层抽样 n 个下标：比例分配 + 最大余数，n≥类数时每类≥1；确定性。"""
    labels = np.asarray(labels)
    if n > len(labels):
        raise ValueError(f"n={n} 超过候选池 {len(labels)}")
    if n == len(labels):
        return np.arange(len(labels))
    classes = np.unique(labels)
    counts = {c: int((labels == c).sum()) for c in classes}
    raw = {c: counts[c] * n / len(labels) for c in classes}
    alloc = {c: int(np.floor(raw[c])) for c in classes}
    if n >= len(classes):
        for c in classes:
            alloc[c] = max(alloc[c], 1)
    rem = n - sum(alloc.values())
    order = sorted(classes, key=lambda c: raw[c] - np.floor(raw[c]), reverse=True)
    i = 0
    while rem > 0:
        c = order[i % len(order)]
        if alloc[c] < counts[c]:
            alloc[c] += 1
            rem -= 1
        i += 1
    rng = np.random.default_rng(seed)
    sel = [rng.permutation(np.flatnonzero(labels == c))[: alloc[c]] for c in classes]
    return np.sort(np.concatenate(sel))


def curve_subset_rows(data: dict[str, NDArray], n_eppe: int, seed: int) -> dict[int, NDArray]:
    """曲线子集行号：{0: EPPE train 分层子集, 1: EPPE val 全集, 2: EPPE test 全集}。"""
    split, flag = data["split"], data["x7"][:, 0]
    pool = np.flatnonzero((split == 0) & (flag == 1))
    sel_local = stratified_subset(data["x7"][pool, 1], n_eppe, seed)
    return {
        0: pool[sel_local],
        1: np.flatnonzero((split == 1) & (flag == 1)),
        2: np.flatnonzero((split == 2) & (flag == 1)),
    }


def pretrain_rows(data: dict[str, NDArray]) -> dict[int, NDArray]:
    """预训练行号：{0: EPE train, 1: EPE val, 2: EPE test + EPPE test（零样本迁移评估）}。"""
    split, flag = data["split"], data["x7"][:, 0]
    return {
        0: np.flatnonzero((split == 0) & (flag == 0)),
        1: np.flatnonzero((split == 1) & (flag == 0)),
        2: np.flatnonzero(split == 2),
    }


def write_subset_npz(data: dict[str, NDArray], rows: dict[int, NDArray], out_path: Path) -> Path:
    """按行号重写子集 npz：split 列按 rows 键重打标签，其余数组按行拷贝。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    order = np.concatenate([rows[s] for s in sorted(rows)])
    new_split = np.concatenate([np.full(len(rows[s]), s, dtype=np.int8) for s in sorted(rows)])
    arrays = {}
    for key, arr in data.items():
        if key == "split":
            arrays[key] = new_split
        elif arr.ndim >= 1 and arr.shape[0] == len(data["split"]):
            arrays[key] = arr[order]
        else:
            arrays[key] = arr
    np.savez(out_path, **arrays)
    return out_path


def seeds_for(n: int, seed_base: int) -> list[int]:
    return [seed_base, seed_base + 1, seed_base + 2] if n <= SMALL_N_MAX else [seed_base]


def finetune_protocols(cfg: dict) -> list[tuple[str, str, float]]:
    """finetune 协议列表：(run 目录 mode 键, protocol 标签, lr 相对 scratch 的倍率)。

    - lowlr：原协议，lr=1/10（epoch 预算相同）；n≥300 曾出现欠训练反转（见 readme）。
    - full-lr：lr/epoch/早停与 scratch 完全相同，唯一差异=权重初始化（干净对照）。
    目录命名：lowlr 沿用 s1_finetune_*（向后兼容既有结果），full-lr 用 s1_finetune_full_lr_*。
    """
    out: list[tuple[str, str, float]] = []
    for p in str(cfg.get("finetune_protocols", "lowlr,full-lr")).split(","):
        p = p.strip()
        if not p:
            continue
        if p == "lowlr":
            out.append(("finetune", "lowlr", 0.1))
        elif p == "full-lr":
            out.append(("finetune_full_lr", "full-lr", 1.0))
        else:
            raise ValueError(f"未知 finetune 协议: {p}")
    return out


# ---------------------------------------------------------------- 子进程驱动


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def run_script(script: Path, args: list[str], log_path: Path) -> None:
    """以当前 venv 解释器子进程跑训练脚本，日志落盘；失败时带日志尾部报错。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(script), *args]
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as lf:
        lf.write(f"cmd: {' '.join(cmd)}\nstart: {datetime.now(UTC).isoformat()}\n")
        lf.flush()
        proc = subprocess.run(cmd, cwd=PROJECT_ROOT, stdout=lf, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    if proc.returncode != 0:
        tail = "\n".join(log_path.read_text(encoding="utf-8").splitlines()[-30:])
        raise RuntimeError(f"训练子进程失败 rc={proc.returncode} log={log_path}\n{tail}")
    print(f"  done [{dt:.0f}s] log={log_path.name}", flush=True)


def s1_run_dir(out_root: Path, mode: str, n: int | None, seed: int) -> Path:
    if mode == "pretrain":
        return out_root / "s1_pretrain"
    return out_root / f"s1_{mode}_n{n}_s{seed}"


def run_stage1(cfg: dict, npz: Path, out_dir: Path, seed: int, lr: float,
               init_from: Path | None = None, init_config: Path | None = None) -> Path:
    eval_path = out_dir / "stage1_eval.json"
    if eval_path.exists():
        print(f"  skip (exists): {out_dir.name}", flush=True)
        return eval_path
    args = [
        "--data", str(npz), "--out", str(out_dir),
        "--epochs", str(cfg["epochs"]), "--patience", str(cfg["patience"]),
        "--lr", str(lr), "--batch-size", str(cfg["batch_size"]), "--seed", str(seed),
    ]
    if init_from is not None:
        args += ["--init-from", str(init_from)]
        if init_config is not None:
            args += ["--init-config", str(init_config)]
    print(f"  run {out_dir.name} (lr={lr}, init={'yes' if init_from else 'no'})", flush=True)
    run_script(SCRIPT_S1, args, out_dir / "train.log")
    return eval_path


def run_stage2(cfg: dict, npz: Path, out_dir: Path, s1_dir: Path, seed: int, lr: float,
               init_from: Path | None = None, init_config: Path | None = None) -> Path:
    eval_path = out_dir / "stage2_eval.json"
    if eval_path.exists():
        print(f"  skip (exists): {out_dir.name}", flush=True)
        return eval_path
    args = [
        "--data", str(npz), "--out", str(out_dir),
        "--stage1-ckpt", str(s1_dir / "stage1.pt"),
        "--stage1-config", str(s1_dir / "stage1_config.json"),
        "--epochs", str(cfg["epochs_stage2"]), "--patience", str(cfg["patience_stage2"]),
        "--lr", str(lr), "--batch-size", str(cfg["batch_size"]), "--seed", str(seed),
        "--max-freq", str(cfg["max_freq"]),
    ]
    if init_from is not None:
        args += ["--init-from", str(init_from)]
        if init_config is not None:
            args += ["--init-config", str(init_config)]
    print(f"  run {out_dir.name} (lr={lr}, init={'yes' if init_from else 'no'})", flush=True)
    run_script(SCRIPT_S2, args, out_dir / "train.log")
    return eval_path


# ---------------------------------------------------------------- 指标收集


def collect_stage1_metrics(eval_path: str | Path) -> dict:
    d = json.loads(Path(eval_path).read_text(encoding="utf-8"))
    m = d["eval"]["eppe"]
    return {
        "fs2_mae_mhz": m["fs_mae_mhz"],
        "fp2_mae_mhz": m["fp_mae_mhz"],
        "k2eff2_mae_pp": m["k2eff_mae_pp"],
        "sparse_rmse_db": m["sparse_rmse_db"],
        "best_epoch": d["best_epoch"],
        "train_seconds": d["train_seconds"],
        "n_test": m["n_test"],
    }


def collect_stage2_metrics(eval_path: str | Path) -> dict:
    d = json.loads(Path(eval_path).read_text(encoding="utf-8"))
    m = d["eval"]["eppe"]
    return {
        "stage2_full_rmse_db": m["full_rmse_db"],
        "stage2_gt_sparse_full_rmse_db": m["gt_sparse_full_rmse_db"],
        "stage2_best_epoch": d["best_epoch"],
        "stage2_train_seconds": d["train_seconds"],
        "stage2_n_test": m["n_test"],
    }


# ---------------------------------------------------------------- 阶段


def _load_data(cfg: dict) -> dict[str, NDArray]:
    with np.load(cfg["data"]) as d:
        return {k: d[k] for k in d.files}


def prepare_subsets(cfg: dict, data: dict[str, NDArray]) -> dict[tuple, Path]:
    """快照式写出全部子集 npz（防统一数据集被并发重建导致口径漂移）。返回 {(kind,n,seed): path}。"""
    sub_dir = Path(cfg["out_root"]) / "subsets"
    paths: dict[tuple, Path] = {}
    pre = sub_dir / "pretrain.npz"
    if not pre.exists():
        write_subset_npz(data, pretrain_rows(data), pre)
    paths[("pretrain", None, None)] = pre
    for n in cfg["ns"]:
        for seed in seeds_for(n, cfg["seed_base"]):
            p = sub_dir / f"sub_n{n}_s{seed}.npz"
            if not p.exists():
                write_subset_npz(data, curve_subset_rows(data, n, seed), p)
            paths[("curve", n, seed)] = p
    return paths


def phase_curve(cfg: dict) -> list[dict]:
    out_root = Path(cfg["out_root"])
    data = _load_data(cfg)
    paths = prepare_subsets(cfg, data)
    base = cfg["seed_base"]
    rows: list[dict] = []

    print("== [curve] EPE 预训练（一次，供全部 finetune 复用）==", flush=True)
    pre_dir = s1_run_dir(out_root, "pretrain", None, base)
    pre_eval = run_stage1(cfg, paths[("pretrain", None, None)], pre_dir, base, cfg["lr"])
    pre_m = collect_stage1_metrics(pre_eval)
    rows.append({"n_eppe_train": 0, "mode": "zeroshot", "protocol": "n/a", "seed": base,
                 "out_dir": str(pre_dir), **pre_m})

    for n in cfg["ns"]:
        for seed in seeds_for(n, base):
            npz = paths[("curve", n, seed)]
            out_dir = s1_run_dir(out_root, "scratch", n, seed)
            ev = run_stage1(cfg, npz, out_dir, seed, cfg["lr"])
            m = collect_stage1_metrics(ev)
            rows.append({"n_eppe_train": n, "mode": "scratch", "protocol": "n/a",
                         "seed": seed, "out_dir": str(out_dir), **m})
            print(f"  n={n} scratch s={seed}: fs2={m['fs2_mae_mhz']:.2f}MHz "
                  f"sparse={m['sparse_rmse_db']:.3f}dB", flush=True)
            for dir_mode, proto, lr_fac in finetune_protocols(cfg):
                out_dir = s1_run_dir(out_root, dir_mode, n, seed)
                # 微调只迁移权重：归一化统计在 EPPE 子集上重拟合（与 scratch 同预处理，
                # 只隔离权重初始化差异）。EPE 统计会让 EPPE 目标落在预训练输出
                # 分布 ~7σ 之外（fs 3-8GHz vs 10-20GHz），低 lr 下无法收敛。
                ev = run_stage1(cfg, npz, out_dir, seed, cfg["lr"] * lr_fac,
                                init_from=pre_dir / "stage1.pt")
                m = collect_stage1_metrics(ev)
                rows.append({"n_eppe_train": n, "mode": "finetune", "protocol": proto,
                             "seed": seed, "out_dir": str(out_dir), **m})
                print(f"  n={n} finetune/{proto} s={seed}: fs2={m['fs2_mae_mhz']:.2f}MHz "
                      f"sparse={m['sparse_rmse_db']:.3f}dB", flush=True)
    return rows


def phase_anchor(cfg: dict) -> list[dict]:
    out_root = Path(cfg["out_root"])
    base = cfg["seed_base"]
    n = ANCHOR_N
    npz = out_root / "subsets" / f"sub_n{n}_s{base}.npz"
    pre_npz = out_root / "subsets" / "pretrain.npz"
    s1_scratch = s1_run_dir(out_root, "scratch", n, base)
    s1_pre = s1_run_dir(out_root, "pretrain", None, base)
    protos = finetune_protocols(cfg)
    for d in [s1_scratch, s1_pre, *[s1_run_dir(out_root, dm, n, base) for dm, _, _ in protos]]:
        if not (d / "stage1.pt").exists():
            raise RuntimeError(f"锚点组依赖缺失（先跑 --phase curve）: {d}")

    rows: list[dict] = []
    print("== [anchor] EPE-only Stage 2 预训练（一次）==", flush=True)
    s2_pre = out_root / "s2_pretrain"
    ev_pre = run_stage2(cfg, pre_npz, s2_pre, s1_pre, base, cfg["lr_stage2"])
    pre2 = collect_stage2_metrics(ev_pre)
    rows.append({"n_eppe_train": 0, "mode": "zeroshot", "protocol": "n/a", "seed": base,
                 "out_dir": str(s2_pre),
                 **collect_stage1_metrics(s1_pre / "stage1_eval.json"), **pre2})

    out_dir = out_root / f"s2_scratch_n{n}_s{base}"
    ev = run_stage2(cfg, npz, out_dir, s1_scratch, base, cfg["lr_stage2"])
    row = {"n_eppe_train": n, "mode": "scratch", "protocol": "n/a", "seed": base,
           "out_dir": str(out_dir),
           **collect_stage1_metrics(s1_scratch / "stage1_eval.json"),
           **collect_stage2_metrics(ev)}
    rows.append(row)
    print(f"  anchor scratch: stage2_full={row['stage2_full_rmse_db']:.3f}dB", flush=True)

    for dir_mode, proto, lr_fac in protos:
        s1_dir = s1_run_dir(out_root, dir_mode, n, base)
        out_dir = out_root / f"s2_{dir_mode}_n{n}_s{base}"
        # 同 Stage 1 口径：只迁移权重（--init-from），cond 统计在子集上重拟合
        ev = run_stage2(cfg, npz, out_dir, s1_dir, base, cfg["lr_stage2"] * lr_fac,
                        init_from=s2_pre / "stage2.pt")
        row = {"n_eppe_train": n, "mode": "finetune", "protocol": proto, "seed": base,
               "out_dir": str(out_dir),
               **collect_stage1_metrics(s1_dir / "stage1_eval.json"),
               **collect_stage2_metrics(ev)}
        rows.append(row)
        print(f"  anchor finetune/{proto}: stage2_full={row['stage2_full_rmse_db']:.3f}dB",
              flush=True)
    return rows


# ---------------------------------------------------------------- 汇总产物


def readme_lines(seed_base: int) -> list[str]:
    return [
        "WP4b 数据效率曲线（IUS 2026 修订）",
        "命题: 物理规律跨架构迁移 ⇒ EPPE 新架构只需极少数据",
        "测试集: 恒为 EPPE test split (n=357), 与训练子集无重叠",
        "子集抽样: EPPE train split 内按 t_aln 类分层 (比例+最大余数, 每类≥1), 种子确定性",
        f"种子: n≤100 → {{{seed_base},+1,+2}}; n≥300 → {{{seed_base}}}",
        "mode=scratch: EPPE 子集从零训练, 归一化统计由子集 train 拟合 (protocol 列=n/a)",
        "mode=finetune: 全部 EPE train 预训练 → EPPE 子集微调; 只迁移权重,",
        "  归一化统计与 scratch 同样在 EPPE 子集 train 拟合;",
        "  protocol=lowlr: lr=1/10 (原协议, epoch 预算相同);",
        "  protocol=full-lr: lr/epoch/早停与 scratch 完全相同, 唯一差异=权重初始化",
        "方法学说明: lowlr 在 n≥300 出现反转 (微调反输 scratch) 是低 lr 下 epoch 预算内",
        "  欠训练所致 (lr 1e-4 × ≤300 epoch 的有效步数不足以走完剩余适应空间);",
        "  full-lr 为干净对照协议, 主结论以 full-lr 为准, lowlr 保留作透明对照",
        "mode=zeroshot: 仅 EPE 预训练, 直接在 EPPE test 评估 (n_eppe_train=0)",
        "Stage1: 统一 MLP (x7→7参数+256稀疏), epochs≤300, patience=30, lr=1e-3, AdamW",
        "Stage2 (锚点组): SparseReconstructor, epochs≤40, patience=20, max_freq=1.0",
        "  (沿用 WP4a compare 结论); finetune Stage2 lr 协议同 Stage1 (lowlr=1e-4,",
        "  full-lr=1e-3)",
        "指标: fs2/fp2 MAE (MHz), k2eff2 MAE (pct-pt), sparse RMSE (dB, GT 位置),",
        "  stage2_full_rmse_db = e2e (Stage1 预测 cond+稀疏) 全网格 RMSE (dB)",
        f"生成日期: {datetime.now(UTC).date().isoformat()}",
    ]


def write_xlsx(curve_rows: list[dict], anchor_rows: list[dict], out_path: str | Path,
               readme: list[str]) -> Path:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "stage1_curve"
    cols = ["n_eppe_train", "mode", "protocol", "seed", "fs2_mae_mhz", "fp2_mae_mhz",
            "k2eff2_mae_pp", "sparse_rmse_db", "best_epoch", "train_seconds"]
    ws.append(cols)
    for r in sorted(curve_rows, key=lambda r: (r["n_eppe_train"], r["mode"],
                                               r.get("protocol", ""), r["seed"])):
        ws.append([r.get(c) for c in cols])
    ws2 = wb.create_sheet("pipeline_anchor")
    cols2 = ["n_eppe_train", "mode", "protocol", "seed", "fs2_mae_mhz", "fp2_mae_mhz",
             "k2eff2_mae_pp", "sparse_rmse_db", "stage2_full_rmse_db",
             "stage2_best_epoch", "stage2_train_seconds"]
    ws2.append(cols2)
    for r in anchor_rows:
        ws2.append([r.get(c) for c in cols2])
    ws3 = wb.create_sheet("readme")
    for line in readme:
        ws3.append([line])
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


def phase_aggregate(cfg: dict) -> dict:
    out_root = Path(cfg["out_root"])
    base = cfg["seed_base"]
    protos = finetune_protocols(cfg)
    curve_rows: list[dict] = []
    pre_dir = s1_run_dir(out_root, "pretrain", None, base)
    if (pre_dir / "stage1_eval.json").exists():
        curve_rows.append({"n_eppe_train": 0, "mode": "zeroshot", "protocol": "n/a",
                           "seed": base, "out_dir": str(pre_dir),
                           **collect_stage1_metrics(pre_dir / "stage1_eval.json")})
    missing = []
    for n in cfg["ns"]:
        for seed in seeds_for(n, base):
            variants = [("scratch", "n/a"), *[(dm, p) for dm, p, _ in protos]]
            for dir_mode, proto in variants:
                d = s1_run_dir(out_root, dir_mode, n, seed)
                ev = d / "stage1_eval.json"
                if ev.exists():
                    mode = "scratch" if dir_mode == "scratch" else "finetune"
                    curve_rows.append({"n_eppe_train": n, "mode": mode, "protocol": proto,
                                       "seed": seed, "out_dir": str(d),
                                       **collect_stage1_metrics(ev)})
                else:
                    missing.append(d.name)
    anchor_rows: list[dict] = []
    s2_pre = out_root / "s2_pretrain"
    if (s2_pre / "stage2_eval.json").exists():
        anchor_rows.append({"n_eppe_train": 0, "mode": "zeroshot", "protocol": "n/a",
                            "seed": base, "out_dir": str(s2_pre),
                            **collect_stage1_metrics(pre_dir / "stage1_eval.json"),
                            **collect_stage2_metrics(s2_pre / "stage2_eval.json")})
    variants = [("scratch", "n/a"), *[(dm, p) for dm, p, _ in protos]]
    for dir_mode, proto in variants:
        d = out_root / f"s2_{dir_mode}_n{ANCHOR_N}_s{base}"
        s1d = s1_run_dir(out_root, dir_mode, ANCHOR_N, base)
        if (d / "stage2_eval.json").exists():
            mode = "scratch" if dir_mode == "scratch" else "finetune"
            anchor_rows.append({"n_eppe_train": ANCHOR_N, "mode": mode, "protocol": proto,
                                "seed": base, "out_dir": str(d),
                                **collect_stage1_metrics(s1d / "stage1_eval.json"),
                                **collect_stage2_metrics(d / "stage2_eval.json")})
        else:
            missing.append(d.name)

    readme = readme_lines(base)
    doc = {
        "generated_at": datetime.now(UTC).isoformat(),
        "task": "WP4b data-efficiency: EPPE subset size × {scratch, EPE-pretrain→finetune}",
        "seed_base": base,
        "source_data": cfg["data"],
        "source_data_sha256": sha256_file(cfg["data"]) if Path(cfg["data"]).exists() else None,
        "config": {k: cfg[k] for k in ("ns", "epochs", "patience", "lr", "batch_size",
                                       "epochs_stage2", "patience_stage2", "lr_stage2",
                                       "max_freq", "finetune_protocols")},
        "protocol": {line.split(":")[0]: line for line in readme},
        "stage1_curve": curve_rows,
        "pipeline_anchor": anchor_rows,
        "missing_runs": missing,
    }
    eval_out = Path(cfg["eval_out"])
    eval_out.parent.mkdir(parents=True, exist_ok=True)
    eval_out.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    xlsx = write_xlsx(curve_rows, anchor_rows, cfg["xlsx_out"], readme)
    print(f"saved: {eval_out}\nsaved: {xlsx}", flush=True)
    if missing:
        print(f"WARN 缺失 run: {missing}", flush=True)
    print_summary(curve_rows, anchor_rows)
    return doc


def print_summary(curve_rows: list[dict], anchor_rows: list[dict]) -> None:
    print("\n== Stage 1 数据效率曲线（test EPPE, n=357）==")
    print(f"{'n':>5} {'mode':<9} {'protocol':<8} {'seed':>10} {'fs2_MHz':>9} {'fp2_MHz':>9} "
          f"{'k2_pp':>7} {'sparse_dB':>9} {'ep':>4} {'sec':>6}")
    for r in sorted(curve_rows, key=lambda r: (r["n_eppe_train"], r["mode"],
                                               r.get("protocol", ""), r["seed"])):
        print(f"{r['n_eppe_train']:>5} {r['mode']:<9} {r.get('protocol', ''):<8} "
              f"{r['seed']:>10} "
              f"{r['fs2_mae_mhz']:>9.2f} {r['fp2_mae_mhz']:>9.2f} {r['k2eff2_mae_pp']:>7.3f} "
              f"{r['sparse_rmse_db']:>9.3f} {r['best_epoch']:>4} {r['train_seconds']:>6.0f}")
    if anchor_rows:
        print("\n== 全流水线锚点（n=100, 含 Stage 2）==")
        print(f"{'mode':<9} {'protocol':<8} {'fs2_MHz':>9} {'sparse_dB':>9} "
              f"{'s2_full_dB':>10} {'s2_ep':>5}")
        for r in anchor_rows:
            print(f"{r['mode']:<9} {r.get('protocol', ''):<8} {r['fs2_mae_mhz']:>9.2f} "
                  f"{r['sparse_rmse_db']:>9.3f} "
                  f"{r['stage2_full_rmse_db']:>10.3f} {r['stage2_best_epoch']:>5}")


def main() -> None:
    ap = argparse.ArgumentParser(description="WP4b 数据效率曲线编排")
    ap.add_argument("--phase", choices=["curve", "anchor", "aggregate", "all"], default="all")
    ap.add_argument("--data", default=DEFAULT_CFG["data"])
    ap.add_argument("--out-root", default=DEFAULT_CFG["out_root"])
    ap.add_argument("--eval-out", default=DEFAULT_CFG["eval_out"])
    ap.add_argument("--xlsx-out", default=DEFAULT_CFG["xlsx_out"])
    ap.add_argument("--epochs", type=int, default=DEFAULT_CFG["epochs"])
    ap.add_argument("--patience", type=int, default=DEFAULT_CFG["patience"])
    ap.add_argument("--lr", type=float, default=DEFAULT_CFG["lr"])
    ap.add_argument("--batch-size", type=int, default=DEFAULT_CFG["batch_size"])
    ap.add_argument("--epochs-stage2", type=int, default=DEFAULT_CFG["epochs_stage2"])
    ap.add_argument("--patience-stage2", type=int, default=DEFAULT_CFG["patience_stage2"])
    ap.add_argument("--lr-stage2", type=float, default=DEFAULT_CFG["lr_stage2"])
    ap.add_argument("--max-freq", type=float, default=DEFAULT_CFG["max_freq"])
    ap.add_argument("--seed-base", type=int, default=DEFAULT_CFG["seed_base"])
    ap.add_argument("--ns", default=",".join(str(n) for n in NS),
                    help="逗号分隔的 EPPE 子集规模")
    ap.add_argument("--finetune-protocols", default=DEFAULT_CFG["finetune_protocols"],
                    help="逗号分隔: lowlr (lr/10) 与/或 full-lr (与 scratch 同 lr)")
    args = ap.parse_args()
    cfg = {**DEFAULT_CFG, **{k: v for k, v in vars(args).items()}}
    cfg["ns"] = [int(x) for x in str(args.ns).split(",") if x]

    if cfg["phase"] in ("curve", "all"):
        phase_curve(cfg)
    if cfg["phase"] in ("anchor", "all"):
        phase_anchor(cfg)
    if cfg["phase"] in ("aggregate", "all"):
        phase_aggregate(cfg)


if __name__ == "__main__":
    main()
