"""稀疏采样重建数据集。

流程: S1P → Z11(dB) → 分区 → 采样 → 训练对
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import skrf as rf
import torch
from numpy.typing import NDArray
from torch.utils.data import Dataset

from app.core.extract import extract_resonator_params
from app.ml.sparse.region_partition import partition_regions

log = logging.getLogger("aln")


def s1p_to_z11_db(
    path: str | Path, target_n_freq: int = 1001
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """读取 S1P/S2P，返回插值后的 (freq_ghz, z_db)。

    使用 skrf 读取阻抗参数 Z，转为 dB，再插值到统一频点数。
    对 S2P 默认取 Z11（端口 0）。
    """
    net = rf.Network(str(path))
    freq_ghz = net.f / 1e9
    z = net.z[:, 0, 0]
    z_mag = np.abs(z)
    z_db = 20.0 * np.log10(np.maximum(z_mag, 1e-12))

    # 插值到统一频点数
    if len(freq_ghz) != target_n_freq:
        freq_new = np.linspace(freq_ghz[0], freq_ghz[-1], target_n_freq)
        z_db_new = np.interp(freq_new, freq_ghz, z_db)
        return freq_new.astype(np.float64), z_db_new.astype(np.float64)

    return freq_ghz.astype(np.float64), z_db.astype(np.float64)


def s2p_to_z_samples(
    path: str | Path, target_n_freq: int = 1001
) -> list[tuple[NDArray[np.float64], NDArray[np.float64], str]]:
    """读取 S2P，返回两个样本的 (freq_ghz, z_db, port_name)。

    port 0 -> S11 (Z11), port 1 -> S22 (Z22)。
    """
    net = rf.Network(str(path))
    if net.nports != 2:
        raise ValueError(f"期望 2-port S2P，得到 {net.nports}-port: {path}")

    freq_ghz = net.f / 1e9
    results = []
    for port, name in [(0, "S11"), (1, "S22")]:
        z = net.z[:, port, port]
        z_mag = np.abs(z)
        z_db = 20.0 * np.log10(np.maximum(z_mag, 1e-12))

        if len(freq_ghz) != target_n_freq:
            freq_new = np.linspace(freq_ghz[0], freq_ghz[-1], target_n_freq)
            z_db_new = np.interp(freq_new, freq_ghz, z_db)
            results.append((freq_new.astype(np.float64), z_db_new.astype(np.float64), name))
        else:
            results.append((freq_ghz.astype(np.float64), z_db.astype(np.float64), name))

    return results


def extract_five_params(s1p_path: str | Path, port: int = 0) -> dict[str, float]:
    """从 S1P/S2P 提取 5 个关键性能指标: fs, fp, Qs, Qp, kt2。

    复用现有的 extract_resonator_params，只取需要的字段。
    对 S2P 可通过 port 参数选择端口 (0=S11, 1=S22)。
    """
    row = extract_resonator_params(str(s1p_path), port=port)
    return {
        "fs": row.fs_ghz or 0.0,
        "fp": row.fp_ghz or 0.0,
        "Qs": row.qs or 0.0,
        "Qp": row.qp or 0.0,
        "kt2": row.k2eff_pct or 0.0,
    }


def uniform_sample_from_mask(
    freq: NDArray[np.float64],
    z_db: NDArray[np.float64],
    mask: NDArray[np.bool_],
    n_samples: int,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """在 mask 指定的区域内均匀采样 n_samples 个点。

    返回: (sampled_freq, sampled_z_db)
    """
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return np.array([]), np.array([])
    if n_samples >= len(idx):
        return freq[idx], z_db[idx]

    # 均匀间隔取点
    step = len(idx) / n_samples
    selected = [idx[int(i * step)] for i in range(n_samples)]
    return freq[selected], z_db[selected]


def _weighted_sample_from_mask(
    freq: NDArray[np.float64],
    z_db: NDArray[np.float64],
    mask: NDArray[np.bool_],
    n_samples: int,
    weights: NDArray[np.float64] | None = None,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """在 mask 指定的区域内按权重采样 n_samples 个点。

    weights 为 None 时退化为均匀采样。
    """
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return np.array([]), np.array([])
    if n_samples >= len(idx):
        return freq[idx], z_db[idx]

    if n_samples == 0 or len(idx) == 0:
        return np.array([], dtype=freq.dtype), np.array([], dtype=z_db.dtype)

    n_samples = min(n_samples, len(idx))

    if weights is not None:
        w = weights[idx]
        w = np.clip(w, 1e-8, None)
        w = w / w.sum()
        selected = np.random.choice(idx, size=n_samples, replace=False, p=w)
    else:
        step = len(idx) / n_samples
        selected = np.array([idx[int(i * step)] for i in range(n_samples)])

    return freq[selected], z_db[selected]


def fixed_rule_sample(
    freq: NDArray[np.float64],
    z_db: NDArray[np.float64],
    region_mask: dict[str, NDArray[np.bool_]],
    target_k: int = 300,
    fs: float = 0.0,
    fp: float = 0.0,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """固定规则采样：按区域配额分配采样点数，主模区内 fs/fp 附近更密。

    配额比例:
        主模区: 50%  (~target_k * 0.5)
        杂模区: 30%  (~target_k * 0.3)
        平滑区: 20%  (~target_k * 0.2)

    主模区内高斯加权：fs/fp 附近采样密度最高，向两侧指数衰减。
    """
    quotas = {
        "main": int(target_k * 0.5),
        "spurious": int(target_k * 0.3),
        "smooth": target_k - int(target_k * 0.5) - int(target_k * 0.3),
    }

    # 动态调整配额：空区域把配额转移给其他区域
    actual = {}
    for region in ["main", "spurious", "smooth"]:
        actual[region] = min(quotas[region], int(region_mask[region].sum()))

    leftover = target_k - sum(actual.values())
    # 把剩余配额按主模区:杂模区:平滑区 = 5:3:2 分配
    while leftover > 0:
        for region, quota in [("main", 5), ("spurious", 3), ("smooth", 2)]:
            if leftover <= 0:
                break
            available = int(region_mask[region].sum()) - actual[region]
            if available > 0:
                add = min(leftover, max(1, quota))
                add = min(add, available)
                actual[region] += add
                leftover -= add

    # 主模区：fs/fp 高斯加权
    main_weights = None
    n_total = len(freq)
    if fs > 0 and fp > fs:
        bw = fp - fs
        d_fs = np.abs(freq - fs) / (bw * 0.3)
        d_fp = np.abs(freq - fp) / (bw * 0.3)
        main_weights = np.zeros(n_total, dtype=np.float64)
        gauss = np.exp(-np.minimum(d_fs, d_fp) ** 2)
        main_weights[region_mask["main"]] = gauss[region_mask["main"]]

    f_main, z_main = _weighted_sample_from_mask(
        freq, z_db, region_mask["main"], actual["main"], main_weights
    )
    f_spur, z_spur = _weighted_sample_from_mask(
        freq, z_db, region_mask["spurious"], actual["spurious"]
    )
    f_smooth, z_smooth = _weighted_sample_from_mask(
        freq, z_db, region_mask["smooth"], actual["smooth"]
    )

    f_all = np.concatenate([f_main, f_spur, f_smooth])
    z_all = np.concatenate([z_main, z_spur, z_smooth])

    order = np.argsort(f_all)
    return f_all[order], z_all[order]


def baseline_interpolate(
    sample_freq: NDArray[np.float64],
    sample_z: NDArray[np.float64],
    target_freq: NDArray[np.float64],
) -> NDArray[np.float64]:
    """用 cubic spline 将稀疏采样点插值到完整频率轴。

    作为重建网络的基线（残差结构）。
    """
    if len(sample_freq) < 4:
        # 点太少，退化为线性插值
        return np.interp(target_freq, sample_freq, sample_z)
    from scipy.interpolate import CubicSpline
    cs = CubicSpline(sample_freq, sample_z, extrapolate=True)
    return cs(target_freq).astype(np.float64)


def _load_file_worker(args: tuple[str, int]) -> list[dict] | None:
    """多进程 worker：加载单个文件（S1P 或 S2P）返回样本列表。"""
    fpath, target_k = args
    f = Path(fpath)
    try:
        if f.suffix.lower() == ".s1p":
            freq, z_db = s1p_to_z11_db(f)
            params = extract_five_params(f)
            region_mask = partition_regions(z_db, freq)
            region_ids = np.zeros(len(freq), dtype=np.int64)
            region_ids[region_mask["main"]] = 0
            region_ids[region_mask["spurious"]] = 1
            region_ids[region_mask["smooth"]] = 2
            sf, sz = fixed_rule_sample(
                freq, z_db, region_mask, target_k, fs=params["fs"], fp=params["fp"]
            )
            z_baseline = baseline_interpolate(sf, sz, freq)
            return [{
                "cond": np.array([params["fs"], params["fp"], params["Qs"],
                                  params["Qp"], params["kt2"]], dtype=np.float32),
                "sample_freq": sf.astype(np.float32),
                "sample_z": sz.astype(np.float32),
                "target_freq": freq.astype(np.float32),
                "target_z": z_db.astype(np.float32),
                "z_baseline": z_baseline.astype(np.float32),
                "region_ids": region_ids,
                "filename": f.name,
            }]
        else:
            z_samples = s2p_to_z_samples(f)
            samples = []
            for freq, z_db, port_name in z_samples:
                port = 0 if port_name == "S11" else 1
                params = extract_five_params(f, port=port)
                region_mask = partition_regions(z_db, freq)
                region_ids = np.zeros(len(freq), dtype=np.int64)
                region_ids[region_mask["main"]] = 0
                region_ids[region_mask["spurious"]] = 1
                region_ids[region_mask["smooth"]] = 2
                sf, sz = fixed_rule_sample(
                    freq, z_db, region_mask, target_k, fs=params["fs"], fp=params["fp"]
                )
                z_baseline = baseline_interpolate(sf, sz, freq)
                samples.append({
                    "cond": np.array([params["fs"], params["fp"], params["Qs"],
                                      params["Qp"], params["kt2"]], dtype=np.float32),
                    "sample_freq": sf.astype(np.float32),
                    "sample_z": sz.astype(np.float32),
                    "target_freq": freq.astype(np.float32),
                    "target_z": z_db.astype(np.float32),
                    "z_baseline": z_baseline.astype(np.float32),
                    "region_ids": region_ids,
                    "filename": f"{f.stem}_{port_name}.s1p",
                })
            return samples
    except Exception as exc:
        log.warning("[跳过] %s: %s", f.name, exc)
        return None


class SparseReconDataset(Dataset):
    """稀疏采样重建数据集。

    每个样本:
        - cond: [fs, fp, Qs, Qp, kt2] (5,)
        - samples: [(freq_j, z_j)] (K, 2) — 稀疏采样点
        - target_freq: (N,) — 全频段频率
        - target_z: (N,) — 全频段 Z11(dB) 真值
        - region_ids: (N,) — 每点区域类别 0/1/2
    """

    def __init__(
        self,
        s1p_dir: str | Path | list[str | Path],
        target_k: int = 300,
        noise_std: float = 0.0,
        augment_shift: float = 0.0,
        num_workers: int = 8,
    ):
        self.target_k = target_k
        self.noise_std = noise_std
        self.augment_shift = augment_shift
        self.samples: list[dict] = []

        dirs = [s1p_dir] if isinstance(s1p_dir, (str, Path)) else s1p_dir

        all_files: list[Path] = []
        for d in dirs:
            d = Path(d).resolve()
            if not d.exists():
                print(f"[警告] 目录不存在: {d}")
                continue
            all_files.extend(sorted(d.glob("*.s1p")))
            all_files.extend(sorted(d.glob("*.s2p")))

        if not all_files:
            print("SparseReconDataset: 未找到任何 S1P/S2P 文件")
            return

        print(
            f"SparseReconDataset: 发现 {len(all_files)} 个文件，"
            f"使用 {num_workers} 进程并行加载..."
        )

        from concurrent.futures import ProcessPoolExecutor, as_completed

        args_list = [(str(f), target_k) for f in all_files]
        done = 0
        with ProcessPoolExecutor(max_workers=num_workers) as exe:
            futures = {exe.submit(_load_file_worker, a): a for a in args_list}
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    self.samples.extend(result)
                done += 1
                if done % 1000 == 0:
                    print(f"  进度: {done}/{len(all_files)} 文件 -> {len(self.samples)} 样本")

        print(f"SparseReconDataset: 成功加载 {len(self.samples)} 个样本")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        d = self.samples[idx]

        # 数据增强
        target_z = d["target_z"].copy()
        sample_z = d["sample_z"].copy()

        if self.noise_std > 0.0:
            noise = np.random.normal(0.0, self.noise_std, size=target_z.shape)
            target_z = target_z + noise
            sample_z = sample_z + np.random.normal(0.0, self.noise_std, size=sample_z.shape)

        if self.augment_shift > 0.0:
            shift = np.random.uniform(-self.augment_shift, self.augment_shift)
            target_z = target_z + shift
            sample_z = sample_z + shift

        # 构造稀疏点序列 (K, 2)
        samples = np.stack([d["sample_freq"], sample_z], axis=1)  # (K, 2)

        return {
            "cond": torch.from_numpy(d["cond"]).float(),           # (5,)
            "samples": torch.from_numpy(samples).float(),           # (K, 2)
            "target_freq": torch.from_numpy(d["target_freq"]).float(),  # (N,)
            "target_z": torch.from_numpy(target_z).float(),         # (N,)
            "z_baseline": torch.from_numpy(d["z_baseline"]).float(),    # (N,)
            "region_ids": torch.from_numpy(d["region_ids"]).long(),    # (N,)
            "filename": d["filename"],
        }


def collate_fn(batch: list[dict]) -> dict[str, torch.Tensor]:
    """处理变长序列的 batching（稀疏点数量 K 可能不同）。"""
    cond = torch.stack([b["cond"] for b in batch])  # (B, 5)
    target_freq = torch.stack([b["target_freq"] for b in batch])  # (B, N)
    target_z = torch.stack([b["target_z"] for b in batch])  # (B, N)
    region_ids = torch.stack([b["region_ids"] for b in batch])  # (B, N)

    # padding 稀疏点序列
    max_k = max(b["samples"].shape[0] for b in batch)
    b = len(batch)
    d_model = batch[0]["samples"].shape[1]  # 2

    padded_samples = torch.zeros(b, max_k, d_model)
    sample_mask = torch.ones(b, max_k, dtype=torch.bool)  # True = padding

    for i, b in enumerate(batch):
        k = b["samples"].shape[0]
        padded_samples[i, :k] = b["samples"]
        sample_mask[i, :k] = False

    # z_baseline
    z_baseline = None
    if "z_baseline" in batch[0]:
        z_baseline = torch.stack([b["z_baseline"] for b in batch])

    result = {
        "cond": cond,
        "samples": padded_samples,
        "sample_mask": sample_mask,
        "target_freq": target_freq,
        "target_z": target_z,
        "region_ids": region_ids,
    }
    if z_baseline is not None:
        result["z_baseline"] = z_baseline
    return result
