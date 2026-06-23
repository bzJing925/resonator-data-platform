"""PINN 性能测试与误差分析。

测试项：
1. 数据处理速度：s1p 解析 + 插值
2. 推理速度：单条 / 批量 PINN 重建
3. 全量重建误差：RMSE / MAE 分布
4. 绘图速度：matplotlib 渲染对比图

用法:
    python scripts/benchmark_pinn.py \
        --checkpoint-dir ./pinn_real_run \
        --s1p-dir /Users/jingbozuo/Desktop/aln-data-master/#3 \
        --latent-dim 8 \
        --device mps
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.ml.dataset import RealS1PBatchDataset
from app.ml.models import ResidualNet, SpectralVAE


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PINN 性能测试")
    p.add_argument("--checkpoint-dir", type=str, required=True)
    p.add_argument("--s1p-dir", type=str, required=True)
    p.add_argument("--latent-dim", type=int, default=8)
    p.add_argument("--n-freq", type=int, default=1001)
    p.add_argument("--device", type=str, default="auto")
    return p.parse_args()


def get_device(pref: str) -> torch.device:
    if pref == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        elif torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    return torch.device(pref)


def main() -> None:
    args = parse_args()
    device = get_device(args.device)
    ckpt = Path(args.checkpoint_dir)

    print(f"设备: {device}")
    print(f"检查点: {ckpt}")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. 数据处理速度
    # ------------------------------------------------------------------
    print("\n【1. 数据处理速度】")
    t0 = time.perf_counter()
    dataset = RealS1PBatchDataset(
        s1p_dir=args.s1p_dir,
        n_freq=args.n_freq,
        latent_dim=args.latent_dim,
    )
    dt = time.perf_counter() - t0
    print(f"  加载 {len(dataset)} 个 s1p 文件: {dt*1000:.1f} ms")
    print(f"  平均每文件: {dt/len(dataset)*1000:.2f} ms")

    # ------------------------------------------------------------------
    # 2. 加载模型
    # ------------------------------------------------------------------
    vae = SpectralVAE(n_freq=args.n_freq, latent_dim=args.latent_dim).to(device)
    residual_net = ResidualNet(latent_dim=args.latent_dim).to(device)
    vae.load_state_dict(torch.load(ckpt / "vae.pt", map_location=device))
    residual_net.load_state_dict(torch.load(ckpt / "residual_net.pt", map_location=device))
    vae.eval()
    residual_net.eval()

    with open(ckpt / "params_norm.json", "r") as f:
        norm = json.load(f)
    params_mean = torch.tensor(norm["mean"], dtype=torch.float32, device=device)
    params_std = torch.tensor(norm["std"], dtype=torch.float32, device=device)

    with open(ckpt / "base_latents.json", "r") as f:
        base_meta = json.load(f)
    # 收集所有 batch 的 z_base
    z_bases = {}
    for bid, meta in base_meta.items():
        z_bases[int(bid)] = torch.tensor(meta["z_base"], dtype=torch.float32, device=device)

    # ------------------------------------------------------------------
    # 3. 推理速度（单条）
    # ------------------------------------------------------------------
    print("\n【2. 推理速度（单条）】")
    n_warmup = 10
    n_test = 100

    item = dataset[0]
    spec = item["spectrum"].unsqueeze(0).to(device)
    params = item["params"].unsqueeze(0).to(device)
    bid = int(item["batch_id"].item())

    # warmup
    with torch.no_grad():
        for _ in range(n_warmup):
            dz = residual_net(params)
            _ = vae.decode(z_bases[bid] + dz)

    # 测试
    t0 = time.perf_counter()
    with torch.no_grad():
        for _ in range(n_test):
            dz = residual_net(params)
            _ = vae.decode(z_bases[bid] + dz)
    dt = time.perf_counter() - t0
    print(f"  {n_test} 次单条推理: {dt*1000:.1f} ms")
    print(f"  单次平均: {dt/n_test*1000:.3f} ms")
    print(f"  吞吐量: {n_test/dt:.0f} spectra/s")

    # ------------------------------------------------------------------
    # 4. 推理速度（批量）
    # ------------------------------------------------------------------
    print("\n【3. 推理速度（批量）】")
    batch_sizes = [16, 32, 64, 128]
    for bs in batch_sizes:
        if bs > len(dataset):
            continue
        specs = torch.stack([dataset[i]["spectrum"] for i in range(bs)]).to(device)
        params_batch = torch.stack([dataset[i]["params"] for i in range(bs)]).to(device)
        bids = [int(dataset[i]["batch_id"].item()) for i in range(bs)]
        z_base_batch = torch.stack([z_bases[b] for b in bids])

        # warmup
        with torch.no_grad():
            dz = residual_net(params_batch)
            _ = vae.decode(z_base_batch + dz)

        t0 = time.perf_counter()
        with torch.no_grad():
            for _ in range(20):
                dz = residual_net(params_batch)
                _ = vae.decode(z_base_batch + dz)
        dt = time.perf_counter() - t0
        print(f"  batch={bs}: {dt/20*1000:.2f} ms/batch, {bs/(dt/20):.0f} spectra/s")

    # ------------------------------------------------------------------
    # 5. 全量重建误差分布
    # ------------------------------------------------------------------
    print("\n【4. 全量重建误差】")
    all_rmse = []
    all_mae = []
    area_rmse: dict[str, list[float]] = {}

    with torch.no_grad():
        for idx in range(len(dataset)):
            item = dataset[idx]
            spec = item["spectrum"].unsqueeze(0).to(device)
            params = item["params"].unsqueeze(0).to(device)
            bid = int(item["batch_id"].item())

            dz = residual_net(params)
            z = z_bases[bid] + dz
            recon = vae.decode(z)

            orig = spec.squeeze().cpu().numpy()
            rec = recon.squeeze().cpu().numpy()
            residual = orig - rec

            rmse = float(np.sqrt(np.mean(residual**2)))
            mae = float(np.mean(np.abs(residual)))
            all_rmse.append(rmse)
            all_mae.append(mae)

            # 按 area 分组统计
            fname = dataset.devices[idx]["filename"]
            area = fname[6] if len(fname) > 6 else "?"  # S22_3_X...
            area_rmse.setdefault(area, []).append(rmse)

    all_rmse = np.array(all_rmse)
    all_mae = np.array(all_mae)

    print(f"  RMSE 均值: {all_rmse.mean():.4f} dB")
    print(f"  RMSE 中位数: {np.median(all_rmse):.4f} dB")
    print(f"  RMSE 标准差: {all_rmse.std():.4f} dB")
    print(f"  RMSE 最小: {all_rmse.min():.4f} dB")
    print(f"  RMSE 最大: {all_rmse.max():.4f} dB")
    print(f"  MAE 均值: {all_mae.mean():.4f} dB")

    # 按 area 统计
    print(f"\n  按 Area 分组 RMSE:")
    for area in sorted(area_rmse.keys()):
        vals = np.array(area_rmse[area])
        print(f"    Area {area}: mean={vals.mean():.4f}, max={vals.max():.4f}, n={len(vals)}")

    # 找出最差样本
    worst_idx = int(np.argmax(all_rmse))
    print(f"\n  最差样本: {dataset.devices[worst_idx]['filename']}")
    print(f"    RMSE={all_rmse[worst_idx]:.4f} dB")

    # ------------------------------------------------------------------
    # 6. 绘图速度
    # ------------------------------------------------------------------
    print("\n【5. 绘图速度（matplotlib）】")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        freq = np.linspace(4.0, 7.0, args.n_freq)
        t0 = time.perf_counter()
        fig, axes = plt.subplots(2, 2, figsize=(12, 8))
        for ax, idx in zip(axes.flat, [0, 1, 2, 3]):
            item = dataset[idx]
            spec = item["spectrum"].squeeze().numpy()
            params = item["params"].unsqueeze(0).to(device)
            bid = int(item["batch_id"].item())
            with torch.no_grad():
                dz = residual_net(params)
                z = z_bases[bid] + dz
                recon = vae.decode(z).squeeze().cpu().numpy()
            ax.plot(freq, spec, label="orig")
            ax.plot(freq, recon, label="recon")
        plt.savefig("/tmp/bench_plot.png")
        plt.close()
        dt = time.perf_counter() - t0
        print(f"  4 张对比图渲染: {dt*1000:.1f} ms")
        print(f"  单图平均: {dt/4*1000:.1f} ms")
    except ImportError:
        print("  matplotlib 未安装，跳过绘图测试")

    print("\n" + "=" * 60)
    print("测试完成。")


if __name__ == "__main__":
    main()
