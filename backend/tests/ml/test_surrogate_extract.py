"""提取管线纯函数单测：两级清洗、每格抽样、物理组划分、归一化统计（v4）。"""

from pathlib import Path

import numpy as np
import pytest
import skrf

from app.config import AlgorithmConfig
from app.ml.surrogate.extract import (
    build_device_record,
    clean_records,
    compute_norm_stats,
    select_per_cell,
    split_groups,
)

N = 101


def _rec(eg, fl, area, pf=0, mark="M1", fs=4.8, fp=5.1, qs=500.0, qp=400.0):
    return {
        "device_id": 1, "eg": eg, "fl": fl, "ag": 1.0, "area_um2": area,
        "has_pf": pf, "mark": mark,
        "cell_key": f"{eg}|{fl}|{area}|{pf}",
        "params": np.array([fs, fp, 7.0, qs, qp, 450.0, 380.0]),
        "sample_pos": np.arange(32), "sample_z": np.full(32, 40.0),
        "sparse_base": np.full(32, 39.0),
        "full_z": np.full(N, 40.0), "phase": np.zeros(N),
    }


class TestCleanRecords:
    def test_physical_illegal_dropped(self):
        recs = [_rec(0.0, 0.0, 700), _rec(1.0, 0.5, 700, qs=0.0), _rec(1.0, 0.5, 700, fp=4.0)]
        kept, dropped = clean_records(recs, fs_tol_mhz=1e9, trend_sigma=1e9)
        assert len(kept) == 1
        assert len(dropped) == 2
        assert all(d["reason"] == "physical" for d in dropped)

    def test_cell_outlier_dropped(self):
        recs = [_rec(1.0, 0.5, 700, fs=4.80), _rec(1.0, 0.5, 700, fs=4.81),
                _rec(1.0, 0.5, 700, fs=4.79), _rec(1.0, 0.5, 700, fs=5.50)]
        kept, dropped = clean_records(recs, fs_tol_mhz=100.0, trend_sigma=1e9)
        assert len(kept) == 3
        assert len(dropped) == 1
        assert dropped[0]["reason"] == "cell_outlier"

    def test_trend_outlier_dropped(self):
        rng = np.random.default_rng(0)
        recs = []
        for eg in (0.0, 0.5, 1.0, 1.5, 2.0):
            for fl in (0.0, 0.5, 1.0):
                fs = 4.7 + 0.1 * eg
                if eg == 1.0 and fl == 0.5:
                    fs = 5.5
                for _ in range(4):
                    recs.append(_rec(eg, fl, 700, mark=f"M{eg}{fl}", fs=fs + rng.normal(0, 0.001)))
        kept, dropped = clean_records(recs, fs_tol_mhz=100.0, trend_sigma=3.0)
        dropped_cells = {d["cell_key"] for d in dropped if d["reason"] == "trend_outlier"}
        assert "1.0|0.5|700|0" in dropped_cells
        assert len(kept) == 60 - 4

    def test_pf_separates_cells(self):
        recs = [_rec(1.0, 0.5, 700, pf=0, fs=4.80), _rec(1.0, 0.5, 700, pf=0, fs=4.81),
                _rec(1.0, 0.5, 700, pf=1, mark="H1", fs=4.60),
                _rec(1.0, 0.5, 700, pf=1, mark="H1", fs=4.61)]
        kept, dropped = clean_records(recs, fs_tol_mhz=100.0, trend_sigma=1e9)
        assert len(kept) == 4

    def test_reason_priority_cell_over_physical(self):
        """同一记录同时违反 cell_outlier 与 physical（qs=0）时，优先归 cell_outlier。"""
        recs = [_rec(1.0, 0.5, 700, fs=4.80), _rec(1.0, 0.5, 700, fs=4.81),
                _rec(1.0, 0.5, 700, fs=4.79), _rec(1.0, 0.5, 700, fs=5.00, qs=0.0)]
        kept, dropped = clean_records(recs, fs_tol_mhz=100.0, trend_sigma=1e9)
        assert len(kept) == 3
        assert len(dropped) == 1
        assert dropped[0]["reason"] == "cell_outlier"

    def test_trend_outlier_small_n_detected_by_mad(self):
        """n=8 键（pf=1 组规模）：std 尺度被单离群格撑大（自掩蔽漏检），MAD 可检出。"""
        rng = np.random.default_rng(7)
        recs = []
        for eg in (0.0, 0.5, 1.0, 1.5):
            for fl in (0.0, 0.5):
                fs = 4.7 + 0.1 * eg + (0.3 if (eg, fl) == (1.0, 0.5) else 0.0)
                for _ in range(4):
                    recs.append(_rec(
                        eg, fl, 700, mark=f"M{eg}{fl}", fs=fs + rng.normal(0, 0.001), fp=5.6,
                    ))
        kept, dropped = clean_records(recs, fs_tol_mhz=100.0, trend_sigma=3.0)
        trend_dropped = {d["cell_key"] for d in dropped if d["reason"] == "trend_outlier"}
        assert trend_dropped == {"1.0|0.5|700|0"}
        assert len(kept) == 32 - 4


class TestSelectPerCell:
    def test_caps_at_max_and_prefers_median(self):
        fss = [4.80, 4.81, 4.79, 4.82, 4.78, 4.90, 4.70, 4.85]
        recs = [_rec(1.0, 0.5, 700, fs=f) for f in fss]
        for i, r in enumerate(recs):
            r["device_id"] = i
        selected = select_per_cell(recs, max_per_cell=5)
        assert len(selected) == 5
        kept_fs = sorted(r["params"][0] for r in selected)
        assert kept_fs == [4.78, 4.79, 4.80, 4.81, 4.82]

    def test_fewer_than_max_keeps_all(self):
        recs = [_rec(1.0, 0.5, 700, fs=4.8), _rec(1.0, 0.5, 700, fs=4.9)]
        assert len(select_per_cell(recs, max_per_cell=5)) == 2

    def test_pf_cells_selected_independently(self):
        recs = [_rec(1.0, 0.5, 700, pf=0, fs=4.8 + i * 0.01) for i in range(7)]
        recs += [_rec(1.0, 0.5, 700, pf=1, mark="H1", fs=4.6 + i * 0.01) for i in range(3)]
        selected = select_per_cell(recs, max_per_cell=5)
        assert len(selected) == 5 + 3

    def test_select_tiebreak_at_cutoff(self):
        """|fs-中位数| 并列且骑跨 cutoff 时，device_id 小者入选。"""
        fss = [4.80, 4.80, 4.80, 4.90, 4.70]
        ids = [10, 5, 7, 1, 2]
        recs = []
        for dev_id, f in zip(ids, fss, strict=True):
            r = _rec(1.0, 0.5, 700, fs=f)
            r["device_id"] = dev_id
            recs.append(r)
        selected = select_per_cell(recs, max_per_cell=2)
        assert sorted(r["device_id"] for r in selected) == [5, 7]


class TestSplitGroups:
    def _groups(self):
        groups = [(0.0, 0.0, 0, "A1-1"), (0.0, 0.0, 1, "G1-1")]
        groups += [(1.0, 0.5, 0, f"B{i}") for i in range(4)]
        groups += [(1.0, 0.5, 1, f"H{i}") for i in range(2)]
        return groups

    def test_baseline_groups_stay_in_train(self):
        splits = split_groups(self._groups(), val_n=2, test_n=2, seed=42)
        assert splits[(0.0, 0.0, 0, "A1-1")] == "train"
        assert splits[(0.0, 0.0, 1, "G1-1")] == "train"
        assert sum(v == "val" for v in splits.values()) == 2
        assert sum(v == "test" for v in splits.values()) == 2

    def test_deterministic_with_seed(self):
        s1 = split_groups(self._groups(), val_n=2, test_n=2, seed=42)
        s2 = split_groups(self._groups(), val_n=2, test_n=2, seed=42)
        assert s1 == s2


class TestComputeNormStats:
    def test_stats_only_from_train_groups(self):
        recs = [_rec(0.0, 0.0, 700, mark="A1-1"), _rec(1.0, 0.5, 700, mark="B0")]
        splits = {(0.0, 0.0, 0, "A1-1"): "train", (1.0, 0.5, 0, "B0"): "test"}
        stats = compute_norm_stats(recs, splits)
        assert stats["x_mean"][0] == pytest.approx(0.0)
        assert stats["x_mean"][4] == pytest.approx(0.0)
        assert set(stats.keys()) == {
            "x_mean", "x_std", "params_mean", "params_std", "z_mean", "z_std",
        }


def _make_2port_s2p(path: Path, n: int = 301) -> None:
    """合成 S11/S22 不同的 2-port S2P（4–7 GHz，仿 tests/core/test_sparse_sampler.py）。"""
    freq = np.linspace(4.0, 7.0, n)
    f0 = 4.9
    z1 = 50.0 + 200.0 * ((freq - f0) ** 2 - 0.05) / ((freq - f0) ** 2 + 0.05)
    z2 = 50.0 + 150.0 * ((freq - (f0 + 0.2)) ** 2 - 0.03) / ((freq - (f0 + 0.2)) ** 2 + 0.03)
    s11 = (z1 - 50.0) / (z1 + 50.0)
    s22 = (z2 - 50.0) / (z2 + 50.0)
    s = np.zeros((n, 2, 2), dtype=complex)
    s[:, 0, 0] = s11
    s[:, 1, 1] = s22
    z0 = np.full((n, 2), 50.0)
    net = skrf.Network(f=freq * 1e9, s=s, z0=z0)
    net.write_touchstone(str(path), form="ma")


class TestBuildDeviceRecord:
    def _row(self, name="dev.s2p", port="S11"):
        return {"id": 1, "eg": 0.0, "fl": 0.0, "area_um2": 700, "has_pf": 0,
                "mark": "A1-1", "fs_ghz": 4.85, "fp_ghz": 5.15, "k2eff_pct": 7.0,
                "qs": 500.0, "qp": 400.0, "qs_bodeq": 450.0, "qp_bodeq": 380.0,
                "s_param_path": name, "s_param_port": port, "batch_no": "T1"}

    def test_build_with_s2p(self, tmp_path):
        files = tmp_path / "files" / "T1"
        files.mkdir(parents=True)
        _make_2port_s2p(files / "dev.s2p")
        freq_axis = np.linspace(4.0, 7.0, 1001)
        rec = build_device_record(self._row(), tmp_path / "files", freq_axis, k=32)
        assert rec is not None
        assert rec["full_z"].shape == (1001,)
        assert rec["sample_pos"].shape == (32,)
        assert rec["phase"].shape == (1001,)
        assert rec["ag"] == AlgorithmConfig.surr_default_ag
        assert rec["cell_key"] == "0.0|0.0|700|0"
        assert np.all(np.isfinite(rec["full_z"]))

    def test_missing_file_returns_none(self, tmp_path):
        freq_axis = np.linspace(4.0, 7.0, 1001)
        rec = build_device_record(self._row("nope.s2p"), tmp_path / "files", freq_axis, k=32)
        assert rec is None

    def test_port_s22_selects_second_port(self, tmp_path):
        files = tmp_path / "files" / "T1"
        files.mkdir(parents=True)
        _make_2port_s2p(files / "dev.s2p")
        freq_axis = np.linspace(4.0, 7.0, 1001)
        r11 = build_device_record(self._row(port="S11"), tmp_path / "files", freq_axis, k=32)
        r22 = build_device_record(self._row(port="S22"), tmp_path / "files", freq_axis, k=32)
        assert not np.allclose(r11["full_z"], r22["full_z"])
