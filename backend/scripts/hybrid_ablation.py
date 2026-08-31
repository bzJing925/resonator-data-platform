"""混合训练消融实验：仿真补充数据能否填充实测未覆盖的插值区域？

三组配置 × 多种子（默认 0/1/2）：
- baseline : 纯实测（对照）
- hybrid63 : 实测 + 全部 63 个仿真补充格（权重 0.3）
- hybrid43 : 实测 + 43 个仿真格（留出 20 格做"缝隙泛化"测试）

评估口径：
1. 实测 test 组 fs/fp MAE（MHz）——仿真补充是否伤害实测格精度；
2. 留出 20 个仿真格上 fs/fp MAE（vs 校准伪标签）——缝隙填充能力。
   hybrid63 在口径 2 上是组内参考（训练见过这些格），不作公平对比；
   公平对比是 baseline vs hybrid43（两者都没见过这 20 格）。

用法：
    uv run python scripts/hybrid_ablation.py --seeds 0 1 2
产物：ckpt_ablation/results.json（各配置各种子的指标 + 汇总）
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from train_surrogate_stage1 import SimSupplementDataset  # noqa: E402

from app.ml.surrogate.dataset import SurrogateDataset  # noqa: E402
from app.ml.surrogate.model import BaselineResidualMLP  # noqa: E402

DATA = ROOT / "surrogate_data"
SIM = DATA / "sim_supplement.npz"
ABL = ROOT / "ckpt_ablation"
HOLDOUT = 20
SPLIT_SEED = 42  # 缝隙留出名单固定，只有训练种子变化


def split_sim() -> tuple[Path, Path]:
    """63 格 → 43 训练 + 20 留出（固定名单）。"""
    out_tr = DATA / "sim_supplement_train43.npz"
    out_ho = DATA / "sim_supplement_holdout20.npz"
    if out_tr.exists() and out_ho.exists():
        return out_tr, out_ho
    with np.load(SIM) as z:
        x, p3, cells = z["x_struct"], z["params3"], z["cells"]
    rng = np.random.default_rng(SPLIT_SEED)
    idx = rng.permutation(len(x))
    ho, tr = idx[:HOLDOUT], idx[HOLDOUT:]
    np.savez(out_tr, x_struct=x[tr], params3=p3[tr], cells=cells[tr])
    np.savez(out_ho, x_struct=x[ho], params3=p3[ho], cells=cells[ho])
    print(f"仿真格切分：训练 {len(tr)} / 留出 {len(ho)}（seed={SPLIT_SEED}）", flush=True)
    return out_tr, out_ho


def train_one(out_dir: Path, sim_npz: Path | None, seed: int) -> None:
    ckpt = out_dir / "stage1.pt"
    if ckpt.exists():
        return  # 断点续跑
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "train_surrogate_stage1.py"),
        "--data",
        str(DATA),
        "--out",
        str(out_dir),
        "--seed",
        str(seed),
    ]
    if sim_npz is not None:
        cmd += ["--sim-npz", str(sim_npz)]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:], flush=True)
        print(r.stderr[-3000:], flush=True)
        raise RuntimeError(f"训练失败: {out_dir}")


def load_model(ckpt_path: Path) -> BaselineResidualMLP:
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    model = BaselineResidualMLP(
        in_dim=ckpt["in_dim"],
        hidden=ckpt["hidden"],
        n_res_blocks=ckpt["n_res_blocks"],
        n_params=ckpt["n_params"],
        k=ckpt["k"],
    )
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model


def eval_measured(model: BaselineResidualMLP, ds: SurrogateDataset) -> tuple[float, float]:
    """实测 test 组 fs/fp MAE（MHz）。"""
    dl = DataLoader(ds, batch_size=1024)
    preds, trues = [], []
    with torch.no_grad():
        for b in dl:
            out = model(b["x"], b["params_base"], b["sparse_base"])
            preds.append(ds.denorm_params(out["params"]).numpy())
            trues.append(ds.denorm_params(b["y_params"]).numpy())
    p, t = np.concatenate(preds), np.concatenate(trues)
    return (
        float(np.abs(p[:, 0] - t[:, 0]).mean() * 1000),
        float(np.abs(p[:, 1] - t[:, 1]).mean() * 1000),
    )


def eval_sim(
    model: BaselineResidualMLP, sim_npz: Path, train_ds: SurrogateDataset
) -> tuple[float, float]:
    """仿真格上 fs/fp MAE（MHz，vs 校准伪标签）。"""
    ds = SimSupplementDataset(str(sim_npz), train_ds, str(DATA))
    with torch.no_grad():
        out = model(ds.x, ds.params_base, torch.zeros(len(ds), ds.k))
    pred = train_ds.denorm_params(out["params"]).numpy()
    with np.load(sim_npz) as z:
        p3 = z["params3"]
    return (
        float(np.abs(pred[:, 0] - p3[:, 0]).mean() * 1000),
        float(np.abs(pred[:, 1] - p3[:, 1]).mean() * 1000),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    args = ap.parse_args()

    sim_tr, sim_ho = split_sim()
    ABL.mkdir(parents=True, exist_ok=True)
    configs = [("baseline", None), ("hybrid63", SIM), ("hybrid43", sim_tr)]

    test_ds = SurrogateDataset(str(DATA), split="test")
    train_ds = SurrogateDataset(str(DATA), split="train")
    results: dict[str, list[dict]] = {name: [] for name, _ in configs}
    for name, sim in configs:
        for seed in args.seeds:
            out_dir = ABL / f"{name}_s{seed}"
            train_one(out_dir, sim, seed)
            model = load_model(out_dir / "stage1.pt")
            m_fs, m_fp = eval_measured(model, test_ds)
            h_fs, h_fp = eval_sim(model, sim_ho, train_ds)
            results[name].append(
                {"seed": seed, "test_fs": m_fs, "test_fp": m_fp, "gap_fs": h_fs, "gap_fp": h_fp}
            )
            print(
                f"{name} seed={seed}: 实测test fs {m_fs:.2f}/fp {m_fp:.2f} | "
                f"留出仿真格 fs {h_fs:.2f}/fp {h_fp:.2f} MHz",
                flush=True,
            )

    summary = {}
    for name, rows in results.items():
        agg = {}
        for k in ("test_fs", "test_fp", "gap_fs", "gap_fp"):
            v = np.array([r[k] for r in rows])
            agg[k] = {"mean": float(v.mean()), "std": float(v.std()), "values": v.tolist()}
        summary[name] = agg
    report = {
        "seeds": args.seeds,
        "holdout_cells": HOLDOUT,
        "split_seed": SPLIT_SEED,
        "note": "gap_* 为留出 20 仿真格 vs 校准伪标签；hybrid63 的 gap_* 是组内参考（训练见过）",
        "runs": results,
        "summary": summary,
    }
    (ABL / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("\n=== 汇总（mean ± std, MHz）===")
    print(f"{'配置':<10} {'test fs':>16} {'test fp':>16} {'gap fs':>16} {'gap fp':>16}")
    for name in results:
        s = summary[name]
        keys = ("test_fs", "test_fp", "gap_fs", "gap_fp")
        cells = [f"{s[k]['mean']:6.2f} ± {s[k]['std']:.2f}" for k in keys]
        print(f"{name:<10} {cells[0]:>16} {cells[1]:>16} {cells[2]:>16} {cells[3]:>16}")
    print(f"\n报告: {ABL / 'results.json'}")


if __name__ == "__main__":
    main()
