"""eg=0/fl=0 基准表：按 (pf, area) 组织的均值性能参数与均值全谱（规格 §5.4 v4）。

基准表只在清洗后的基准单元记录上构建；查询时 pf 取最近档（0/1 语义），
面积不在档位上时对相邻面积档做 dB 域线性插值（z 已是 dB，params 直接线性）。
torch-free。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray


class BaselineTable:
    """按 (pf, area) 二维组织的基准表。

    Attributes:
        pfs: (P,) 升序 pf 档（[0., 1.]）。
        areas: (A,) 升序面积档（μm²）。
        params_base: (P, A, 7) 每档基准性能参数均值。
        z_base_full: (P, A, N) 每档基准全谱均值（dB）。
    """

    def __init__(
        self,
        pfs: NDArray[np.float64],
        areas: NDArray[np.float64],
        params_base: NDArray[np.float64],
        z_base_full: NDArray[np.float64],
    ) -> None:
        self.pfs = np.asarray(pfs, dtype=np.float64)
        self.areas = np.asarray(areas, dtype=np.float64)
        self.params_base = np.asarray(params_base, dtype=np.float64)
        self.z_base_full = np.asarray(z_base_full, dtype=np.float64)
        if not np.all(np.diff(self.areas) > 0):
            raise ValueError("areas 必须严格递增")
        expected = (len(self.pfs), len(self.areas))
        if self.params_base.shape[:2] != expected or self.z_base_full.shape[:2] != expected:
            raise ValueError("pfs/areas 与 params_base/z_base_full 形状不一致")

    @classmethod
    def build(cls, records: list[dict]) -> BaselineTable:
        """从清洗后的记录构建（只用 eg=0 且 fl=0 的基准单元，按 (has_pf, area) 分组）。"""
        base = [r for r in records if r["eg"] == 0.0 and r["fl"] == 0.0]
        if not base:
            raise ValueError("没有 eg=0/fl=0 的基准单元记录，无法构建基准表")
        groups: dict[tuple[int, int], list[dict]] = {}
        for r in base:
            groups.setdefault((int(r["has_pf"]), r["area_um2"]), []).append(r)
        pfs = sorted({k[0] for k in groups})
        areas = sorted({k[1] for k in groups})
        if len(groups) != len(pfs) * len(areas):
            raise ValueError("基准网格存在缺失的 (pf, area) 组合")
        params = np.zeros((len(pfs), len(areas), len(base[0]["params"])))
        z = np.zeros((len(pfs), len(areas), len(base[0]["full_z"])))
        for (pf, area), group in groups.items():
            pi, ai = pfs.index(pf), areas.index(area)
            params[pi, ai] = np.mean([r["params"] for r in group], axis=0)
            z[pi, ai] = np.mean([r["full_z"] for r in group], axis=0)
        return cls(
            np.asarray(pfs, dtype=np.float64),
            np.asarray(areas, dtype=np.float64),
            params,
            z,
        )

    def lookup(self, area: float, pf: float) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """按 (面积, pf) 查询 (params_base(7,), z_base_full(N,))。

        pf 取最近档（0/1 精确语义）；面积档间线性插值、越界取端点（返回副本）。
        """
        pi = int(np.argmin(np.abs(self.pfs - float(pf))))
        a = float(area)
        if a <= self.areas[0]:
            return self.params_base[pi, 0].copy(), self.z_base_full[pi, 0].copy()
        if a >= self.areas[-1]:
            return self.params_base[pi, -1].copy(), self.z_base_full[pi, -1].copy()
        hi = int(np.searchsorted(self.areas, a))
        lo = hi - 1
        w = (a - self.areas[lo]) / (self.areas[hi] - self.areas[lo])
        params = (1 - w) * self.params_base[pi, lo] + w * self.params_base[pi, hi]
        z = (1 - w) * self.z_base_full[pi, lo] + w * self.z_base_full[pi, hi]
        return params, z

    def save(self, path: str | Path) -> None:
        np.savez_compressed(
            path,
            pfs=self.pfs,
            areas=self.areas,
            params_base=self.params_base,
            z_base_full=self.z_base_full,
        )

    @classmethod
    def load(cls, path: str | Path) -> BaselineTable:
        with np.load(path) as data:
            return cls(data["pfs"], data["areas"], data["params_base"], data["z_base_full"])
