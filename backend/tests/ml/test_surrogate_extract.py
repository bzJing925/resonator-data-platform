"""提取管线纯函数单测：两级清洗、每格抽样、物理组划分、归一化统计（v4）。"""

import numpy as np
import pytest

from app.ml.surrogate.extract import (
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
