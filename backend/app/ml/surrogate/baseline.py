"""eg=0/fl=0 基准表：每面积档的均值性能参数与均值全谱（规格 §5.4）。

基准表只在清洗后的基准单元记录上构建；查询时面积不在档位上，
对相邻面积档做 dB 域线性插值（z 已是 dB，params 直接线性）。
torch-free。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray


class BaselineTable:
    """按面积档组织的基准表。

    Attributes:
        areas: (A,) 升序面积档（μm²）。
        params_base: (A, 7) 每档基准性能参数均值。
        z_base_full: (A, N) 每档基准全谱均值（dB）。
    """

    def __init__(
        self,
        areas: NDArray[np.float64],
        params_base: NDArray[np.float64],
        z_base_full: NDArray[np.float64],
    ) -> None:
        self.areas = np.asarray(areas, dtype=np.float64)
        self.params_base = np.asarray(params_base, dtype=np.float64)
        self.z_base_full = np.asarray(z_base_full, dtype=np.float64)
        if not (len(self.areas) == len(self.params_base) == len(self.z_base_full)):
            raise ValueError("areas/params_base/z_base_full 行数不一致")

    @classmethod
    def build(cls, records: list[dict]) -> BaselineTable:
        """从清洗后的记录构建（只用 eg=0 且 fl=0 的基准单元）。"""
        base = [r for r in records if r["eg"] == 0.0 and r["fl"] == 0.0]
        if not base:
            raise ValueError("没有 eg=0/fl=0 的基准单元记录，无法构建基准表")
        areas = sorted({r["area_um2"] for r in base})
        params_rows, z_rows = [], []
        for area in areas:
            group = [r for r in base if r["area_um2"] == area]
            params_rows.append(np.mean([r["params"] for r in group], axis=0))
            z_rows.append(np.mean([r["full_z"] for r in group], axis=0))
        return cls(
            np.asarray(areas, dtype=np.float64),
            np.stack(params_rows),
            np.stack(z_rows),
        )

    def lookup(self, area: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """按面积查询 (params_base(7,), z_base_full(N,))；档间线性插值、越界取端点。"""
        a = float(area)
        if a <= self.areas[0]:
            return self.params_base[0], self.z_base_full[0]
        if a >= self.areas[-1]:
            return self.params_base[-1], self.z_base_full[-1]
        hi = int(np.searchsorted(self.areas, a))
        lo = hi - 1
        w = (a - self.areas[lo]) / (self.areas[hi] - self.areas[lo])
        params = (1 - w) * self.params_base[lo] + w * self.params_base[hi]
        z = (1 - w) * self.z_base_full[lo] + w * self.z_base_full[hi]
        return params, z

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            path, areas=self.areas, params_base=self.params_base, z_base_full=self.z_base_full
        )

    @classmethod
    def load(cls, path: str | Path) -> BaselineTable:
        data = np.load(path)
        return cls(data["areas"], data["params_base"], data["z_base_full"])
