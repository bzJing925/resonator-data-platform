"""SurrogateDataset：加载 extract_surrogate_dataset 产出的 npz + meta.json。

归一化在 __getitem__ 内完成（统计来自 meta.json，提取时已只用 train 组计算）。
基准（params_base / sparse_base）用同一组统计归一化，保证"基准 + 残差"在同一空间。
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import Dataset


class SurrogateDataset(Dataset):
    """按物理器件组 split 过滤的代理模型数据集。"""

    def __init__(self, data_dir: str | Path, split: str = "train") -> None:
        super().__init__()
        data_dir = Path(data_dir)
        meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
        self.meta = meta
        self.freq_ghz = torch.tensor(meta["freq_ghz"], dtype=torch.float32)
        self.k: int = meta["k"]
        self.n_freq: int = meta["n_freq"]

        data = np.load(data_dir / "dataset.npz")
        keep_groups = {i for i, g in enumerate(meta["groups"]) if g["split"] == split}
        if not keep_groups:
            raise ValueError(f"meta['groups'] 中没有 split={split!r} 的组（检查 split 拼写）")
        with data:
            mask = np.array([ci in keep_groups for ci in data["group_idx"]])

            self.x = torch.from_numpy(data["x_struct"][mask]).float()
            self.y_params = torch.from_numpy(data["y_params"][mask]).float()
            self.params_base = torch.from_numpy(data["params_base"][mask]).float()
            self.y_sparse = torch.from_numpy(data["y_sparse"][mask]).float()
            self.sparse_base = torch.from_numpy(data["sparse_base"][mask]).float()
            self.full_z = torch.from_numpy(data["full_z"][mask]).float()
            self.sample_pos = torch.from_numpy(data["sample_pos"][mask]).long()
            self.group_idx = torch.from_numpy(data["group_idx"][mask]).long()

        self.x_mean = torch.tensor(meta["x_mean"], dtype=torch.float32)
        self.x_std = torch.tensor(meta["x_std"], dtype=torch.float32)
        self.p_mean = torch.tensor(meta["params_mean"], dtype=torch.float32)
        self.p_std = torch.tensor(meta["params_std"], dtype=torch.float32)
        self.z_mean = float(meta["z_mean"][0])
        self.z_std = float(meta["z_std"][0])

    def __len__(self) -> int:
        return len(self.x)

    def norm_z(self, z: torch.Tensor) -> torch.Tensor:
        return (z - self.z_mean) / self.z_std

    def denorm_z(self, z: torch.Tensor) -> torch.Tensor:
        return z * self.z_std + self.z_mean

    def denorm_params(self, p: torch.Tensor) -> torch.Tensor:
        return p * self.p_std + self.p_mean

    def __getitem__(self, idx: int) -> dict[str, torch.Tensor]:
        return {
            "x": (self.x[idx] - self.x_mean) / self.x_std,
            "y_params": (self.y_params[idx] - self.p_mean) / self.p_std,
            "params_base": (self.params_base[idx] - self.p_mean) / self.p_std,
            "y_sparse": self.norm_z(self.y_sparse[idx]),
            "sparse_base": self.norm_z(self.sparse_base[idx]),
            "group_idx": self.group_idx[idx],
        }
