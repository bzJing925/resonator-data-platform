"""Stage 2 Step B 微调：用 Stage 1 预测稀疏点微调重建器（规格 §7）。

Step A 用真值稀疏点/cond 训练；推理时输入是 Stage 1 的预测点（分布不同）。
本脚本按推理链路（两遍前向 + 预测 fs/fp 定采样位置）为 train/val 记录生成
预测稀疏点与 cond，以此微调 stage2.pt，闭合训练/推理分布差。

用法：
    uv run python scripts/finetune_stage2_surrogate.py --data ./surrogate_data \
        --ckpt ./checkpoints_surrogate --from-scratch --epochs 400 --lr 5e-3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

# 把 backend 目录加入路径，以便 import app（同 train_sparse_reconstructor.py）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.sparse_sampler import (  # noqa: E402
    baseline_interpolate,
    deterministic_sample_positions,
)
from app.ml.sparse.reconstructor import SparseReconstructor  # noqa: E402
from app.ml.surrogate.model import BaselineResidualMLP  # noqa: E402

_COND_IDX = [0, 1, 3, 4, 2]  # y_params 中 [fs, fp, Qs, Qp, kt2] 的列索引


@torch.no_grad()
def _stage1_predict_all(
    stage1: BaselineResidualMLP, data_dir: Path, split: str
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """对 split 全部记录跑两遍前向推理，返回 (cond_pred, sample_freq, sample_z, full_z)。

    与 SurrogatePredictor.predict 同链路：第一遍出 params（sparse_base 置零），
    预测 fs/fp 定采样位置并查 sparse_base，第二遍出稀疏点。
    """
    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    freq = np.asarray(meta["freq_ghz"], dtype=np.float64)
    x_mean = np.asarray(meta["x_mean"], dtype=np.float32)
    x_std = np.asarray(meta["x_std"], dtype=np.float32)
    p_mean = np.asarray(meta["params_mean"], dtype=np.float32)
    p_std = np.asarray(meta["params_std"], dtype=np.float32)
    z_mean = float(meta["z_mean"][0])
    z_std = float(meta["z_std"][0])

    from app.ml.surrogate.baseline import BaselineTable

    baseline = BaselineTable.load(data_dir / "baseline.npz")

    with np.load(data_dir / "dataset.npz") as z:
        keep = {i for i, g in enumerate(meta["groups"]) if g["split"] == split}
        mask = np.array([ci in keep for ci in z["group_idx"]])
        x_all = z["x_struct"][mask]
        full_z = z["full_z"][mask]

    x_n = torch.from_numpy((x_all - x_mean) / x_std).float()
    k: int = meta["k"]
    conds, sfreqs, szs = [], [], []
    for i in range(len(x_all)):
        eg, fl, _ag, area, pf = x_all[i]
        params_base, z_base_full = baseline.lookup(float(area), float(pf))
        pb = torch.from_numpy((params_base - p_mean) / p_std).float().unsqueeze(0)
        xi = x_n[i : i + 1]
        out1 = stage1(xi, pb, torch.zeros(1, k))
        params = (out1["params"][0].numpy() * p_std + p_mean).astype(np.float64)
        pos = deterministic_sample_positions(freq, float(params[0]), float(params[1]), k=k)
        sb_db = (
            z_base_full[pos]
            if len(z_base_full) == len(freq)
            else np.interp(pos / (len(freq) - 1), np.linspace(0, 1, len(z_base_full)), z_base_full)
        )
        sb = torch.from_numpy((sb_db - z_mean) / z_std).float().unsqueeze(0)
        out2 = stage1(xi, pb, sb)
        sparse_z = (out2["sparse"][0].numpy() * z_std + z_mean).astype(np.float64)
        conds.append(params[_COND_IDX])
        sfreqs.append(freq[pos])
        szs.append(sparse_z)
    return (
        np.stack(conds).astype(np.float32),
        np.stack(sfreqs).astype(np.float32),
        np.stack(szs).astype(np.float32),
        full_z,
    )


class StepBDataset(Dataset):
    """Stage 1 预测稀疏点 + 真值全谱 的微调对。

    cond 用 params 统计归一化、z 用 z_mean/z_std 归一化（O(1) 激活 —
    原始 dB/百位 Q 值会把 LayerNorm 压垮，位置信息被淹没）。
    """

    def __init__(self, cond, sfreq, sz, full_z, freq, cond_mean, cond_std, z_mean, z_std) -> None:
        self.cond = torch.from_numpy((cond - cond_mean) / cond_std)
        self.samples = torch.from_numpy(np.stack([sfreq, (sz - z_mean) / z_std], axis=-1))
        self.target = torch.from_numpy((full_z - z_mean) / z_std).float()
        self.freq = torch.from_numpy(np.asarray(freq, dtype=np.float32))
        self.z_baseline = torch.from_numpy(
            np.stack(
                [
                    baseline_interpolate(s[:, 0], s[:, 1], np.asarray(freq, dtype=np.float64))
                    for s in self.samples.numpy()
                ]
            )
        ).float()

    def __len__(self) -> int:
        return len(self.cond)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        return {
            "cond": self.cond[idx],
            "samples": self.samples[idx],
            "freq_target": self.freq,
            "z_baseline": self.z_baseline[idx],
            "target": self.target[idx],
        }


def train(
    data_dir: str,
    ckpt_dir: str,
    epochs: int = 400,
    batch_size: int = 256,
    lr: float = 5e-3,
    patience: int = 20,
    scale_init: float = 1.0,
    from_scratch: bool = False,
    smoke: bool = False,
    device: str = "auto",
    n_target_sub: int = 256,
    w_peak: float = 3.0,
) -> dict:
    if device == "auto":
        dev = (
            torch.device("mps")
            if torch.backends.mps.is_available()
            else torch.device("cuda")
            if torch.cuda.is_available()
            else torch.device("cpu")
        )
    else:
        dev = torch.device(device)
    print(f"device={dev}（注意：本模型 1001 查询点注意力在 MPS 上实测比 CPU 慢约 7 倍）")
    data_dir_p, ckpt = Path(data_dir), Path(ckpt_dir)
    meta = json.loads((data_dir_p / "meta.json").read_text(encoding="utf-8"))
    freq = np.asarray(meta["freq_ghz"], dtype=np.float64)

    # Stage 1 推理生成微调对
    c1 = torch.load(ckpt / "stage1.pt", map_location="cpu", weights_only=True)
    stage1 = BaselineResidualMLP(
        in_dim=c1["in_dim"],
        hidden=c1["hidden"],
        n_res_blocks=c1["n_res_blocks"],
        n_params=c1["n_params"],
        k=c1["k"],
    )
    stage1.load_state_dict(c1["model_state"])
    stage1.eval()

    cond_idx = np.asarray(_COND_IDX)
    cond_mean = np.asarray(meta["params_mean"], dtype=np.float32)[cond_idx]
    cond_std = np.asarray(meta["params_std"], dtype=np.float32)[cond_idx]
    z_mean = float(meta["z_mean"][0])
    z_std = float(meta["z_std"][0])
    datasets = {}
    for split in ("train", "val"):
        cond, sfreq, sz, full_z = _stage1_predict_all(stage1, data_dir_p, split)
        datasets[split] = StepBDataset(
            cond, sfreq, sz, full_z, freq, cond_mean, cond_std, z_mean, z_std
        )

    train_dl = DataLoader(datasets["train"], batch_size=batch_size, shuffle=True)
    val_dl = DataLoader(datasets["val"], batch_size=batch_size)
    if smoke:
        epochs = min(epochs, 2)

    cfg = json.loads((ckpt / "stage2_config.json").read_text(encoding="utf-8"))
    cfg["max_freq"] = 0.1  # 频率标尺：4–7 GHz 窗口映射到 40–70，正弦位置编码才可分辨
    model = SparseReconstructor(**cfg).to(dev)
    if not from_scratch:
        model.load_state_dict(torch.load(ckpt / "stage2.pt", map_location=dev, weights_only=True))
    # Step A 的 residual_scale 收敛到 ≈0（样条基线已够好 → 门控残差通路梯度饿死）。
    # Step B 需要残差通路纠正 Stage 1 预测误差，重新打开：scale 重置为 scale_init。
    if scale_init > 0:
        with torch.no_grad():
            model.residual_scale.fill_(scale_init)
        print(f"residual_scale 重置为 {scale_init}（Step A 收敛值 ≈ 0）")
    opt = torch.optim.AdamW(model.parameters(), lr=lr)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=epochs)

    def run_epoch(dl, train_mode: bool) -> tuple[float, float]:
        """返回 (普通 MSE, 峰加权 MSE)；训练态加权入损失，val 两种都统计。"""
        model.train(train_mode)
        total, total_w, n = 0.0, 0.0, 0
        with torch.set_grad_enabled(train_mode):
            for b in dl:
                cond = b["cond"].to(dev)
                samples = b["samples"].to(dev)
                freq_t = b["freq_target"].to(dev)
                zb = b["z_baseline"].to(dev)
                target = b["target"].to(dev)
                # 训练态子采样目标频率点：查询点相互独立（仅对样本做 cross-attn），
                # 子采样的 MSE 是全量 MSE 的无偏估计；注意力开销 ∝ 查询点数，约 4× 加速
                if train_mode and 0 < n_target_sub < freq_t.shape[1]:
                    idx = torch.randperm(freq_t.shape[1], device=dev)[:n_target_sub]
                    freq_t, zb, target = freq_t[:, idx], zb[:, idx], target[:, idx]
                pred = model(cond, samples, freq_t, z_baseline=zb)
                # 峰区加权：全谱 1001 点的 MSE 对 ~10 个峰点几乎无梯度（Zp 欠预测主因）。
                # w = 1 + w_peak·relu(target_n−1)：峰区（≳+1σ）点权重 1+w_peak 倍以上
                w = 1.0 + w_peak * torch.relu(target - 1.0)
                loss_w = ((pred - target) ** 2 * w).sum() / w.sum().clamp_min(1e-12)
                loss = torch.nn.functional.mse_loss(pred, target)
                if train_mode:
                    opt.zero_grad()
                    (loss_w if w_peak > 0 else loss).backward()
                    opt.step()
                total += loss.item() * len(cond)
                total_w += loss_w.item() * len(cond)
                n += len(cond)
        return total / max(n, 1), total_w / max(n, 1)

    best_val, best_state, bad = float("inf"), None, 0
    history = []
    for epoch in range(1, epochs + 1):
        tr, tr_w = run_epoch(train_dl, True)
        va, va_w = run_epoch(val_dl, False)
        sched.step()
        history.append({"epoch": epoch, "train": tr, "val": va, "val_w": va_w})
        # 模型选择与早停用峰加权 val（与训练目标一致；普通 MSE 仅记录参考）
        crit = va_w if w_peak > 0 else va
        if crit < best_val - 1e-6:
            best_val, bad = crit, 0
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
        if epoch % 5 == 0 or epoch == 1:
            print(
                f"epoch {epoch}: train={tr:.4f} val={va:.4f} val_w={va_w:.4f} best={best_val:.4f}"
            )
        if bad >= patience:
            print(f"early stop at epoch {epoch}")
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    torch.save(model.state_dict(), ckpt / "stage2_ft.pt")
    (ckpt / "stage2_config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    (ckpt / "stage2_ft_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    print(f"saved: {ckpt / 'stage2_ft.pt'} (best val={best_val:.4f})")
    return {"best_val": best_val, "epochs_run": len(history)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="./surrogate_data")
    ap.add_argument("--ckpt", default="./checkpoints_surrogate")
    ap.add_argument("--epochs", type=int, default=400)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=5e-3)
    ap.add_argument("--patience", type=int, default=20)
    ap.add_argument(
        "--scale-init",
        type=float,
        default=1.0,
        help="重开残差通路的 residual_scale 初值（0=不重置）",
    )
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument(
        "--device",
        default="auto",
        help="auto/cpu/mps/cuda；本模型 1001 查询点注意力在 MPS 实测比 CPU 慢约 7 倍，建议 cpu",
    )
    ap.add_argument(
        "--n-target-sub",
        type=int,
        default=256,
        help="训练态每步子采样的目标频率点数（0=全量；子采样 MSE 无偏，约 4× 加速）",
    )
    ap.add_argument(
        "--w-peak",
        type=float,
        default=3.0,
        help="峰区损失权重（0=普通 MSE）；峰点少、普通 MSE 对 Zp 欠预测梯度太弱",
    )
    ap.add_argument(
        "--from-scratch",
        action="store_true",
        help="不加载 stage2.pt，从零训练（归一化语义变更后使用）",
    )
    args = ap.parse_args()
    train(
        args.data,
        args.ckpt,
        args.epochs,
        args.batch_size,
        args.lr,
        args.patience,
        args.scale_init,
        args.from_scratch,
        args.smoke,
        args.device,
        args.n_target_sub,
        args.w_peak,
    )


if __name__ == "__main__":
    main()
