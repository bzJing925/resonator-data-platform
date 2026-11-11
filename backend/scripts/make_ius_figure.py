"""IUS 论文用 pred-vs-true 对比图：当前生产 checkpoint（含 zp_corrector）。

从 test 组 208 器件按全谱 RMSE 分位选 6 个代表（q10/q30/q50/q70/q90/q97），
输出矢量 PDF + 600dpi PNG 到 paper/ius2026/figures/。
用法：.venv/bin/python scripts/make_ius_figure.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from app.ml.surrogate.infer import SurrogatePredictor  # noqa: E402

PAPER_FIG_DIR = Path(
    "/Users/jingbozuo/00.PROJECTS/ALN-DATA/aln-data-master/paper/ius2026/figures"
)


def main() -> None:
    data_dir = Path("surrogate_data")
    ckpt_dir = Path("checkpoints_surrogate")
    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    freq = np.asarray(meta["freq_ghz"])
    with np.load(data_dir / "dataset.npz") as z:
        npz = {k: z[k] for k in z.files}
    test_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == "test"}
    idx = [i for i, ci in enumerate(npz["group_idx"]) if ci in test_groups]

    pred = SurrogatePredictor(
        ckpt_dir / "stage1.pt",
        ckpt_dir / "stage2_ft.pt",
        ckpt_dir / "stage2_config.json",
        data_dir / "baseline.npz",
        data_dir / "meta.json",
    )

    preds: dict[int, np.ndarray] = {}
    rmses = []
    for i in idx:
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        r = pred.predict(float(eg), float(fl), float(area), float(pf))
        preds[i] = r["z_db"]
        rmses.append(float(np.sqrt(np.mean((r["z_db"] - npz["full_z"][i]) ** 2))))
    rmses = np.array(rmses)

    # 2026-09-16 用户指令：剔除 700 µm² 面板，只放 4 张（2×2）
    keep = {k for k, i in enumerate(idx) if float(npz["x_struct"][i][3]) != 700.0}
    order = [o for o in np.argsort(rmses) if o in keep]
    qs = [0.10, 0.35, 0.70, 0.95]
    picks = [order[min(int(q * len(order)), len(order) - 1)] for q in qs]
    pick_idx = [idx[p] for p in picks]

    plt.rcParams.update(
        {
            "font.size": 7,
            "axes.linewidth": 0.6,
            "lines.linewidth": 0.9,
            "xtick.labelsize": 7,
            "ytick.labelsize": 7,
            "legend.fontsize": 6.5,
            "font.family": "sans-serif",
        }
    )
    fig, axes = plt.subplots(2, 2, figsize=(7.0, 2.6), sharex=True)
    for ax, i, q in zip(axes.flat, pick_idx, qs, strict=True):
        eg, fl, _ag, area, pf = npz["x_struct"][i]
        rmse_i = float(np.sqrt(np.mean((preds[i] - npz["full_z"][i]) ** 2)))
        ax.plot(freq, npz["full_z"][i], color="#2F5FA3", lw=1.0, label="Measured")
        ax.plot(freq, preds[i], color="#E64B35", lw=0.9, ls="--", label="Predicted")
        ax.set_title(
            f"EG={eg:g}, FL={fl:g}, A={int(area)} $\\mu$m$^2$, PF={int(pf)}",
            fontsize=7.0,
        )
        ax.text(
            0.03,
            0.05,
            f"RMSE {rmse_i:.2f} dB (q{int(q*100)})",
            transform=ax.transAxes,
            fontsize=6.5,
            color="0.25",
        )
        ax.grid(alpha=0.25, lw=0.4)
        ax.set_xlim(4.0, 7.0)
        ax.set_ylim(0, 70)
        ax.set_yticks([0, 20, 40, 60])
    for ax in axes[-1]:
        ax.set_xlabel("Frequency (GHz)")
    for ax in axes[:, 0]:
        ax.set_ylabel("$|Z_{11}|$ (dB)")
    axes[0, 1].legend(loc="upper right", frameon=False)
    fig.tight_layout(pad=0.4)
    PAPER_FIG_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(PAPER_FIG_DIR / "pred_vs_true.pdf")
    fig.savefig(PAPER_FIG_DIR / "pred_vs_true.png", dpi=600)
    print(f"picks RMSE: {[f'{rmses[p]:.2f}' for p in picks]}")
    print(f"saved to {PAPER_FIG_DIR}")


if __name__ == "__main__":
    main()
