"""基准表构建/查询/插值/存取单测。"""

import numpy as np

from app.ml.surrogate.baseline import BaselineTable


def _record(eg, fl, area, params, z_full):
    return {
        "eg": eg,
        "fl": fl,
        "ag": 0.0,
        "area_um2": area,
        "params": np.asarray(params, dtype=np.float64),
        "full_z": np.asarray(z_full, dtype=np.float64),
    }


def _make_records():
    """3 个面积档 × 基准(eg=0,fl=0) 各 2 条 + 非基准 1 条。"""
    recs = []
    for area in (700, 1500, 3000):
        for i in range(2):
            recs.append(
                _record(
                    0.0,
                    0.0,
                    area,
                    params=[4.8, 5.1, 7.0, 510 + i * 10, 400, 450, 380],
                    z_full=np.full(101, 30.0 + area / 1000 + i),
                )
            )
    recs.append(
        _record(
            1.0,
            0.5,
            1500,
            params=[4.9, 5.2, 7.2, 600, 500, 550, 480],
            z_full=np.full(101, 50.0),
        )
    )
    return recs


class TestBaselineTable:
    def test_build_uses_only_baseline_cells(self):
        table = BaselineTable.build(_make_records())
        assert table.areas.tolist() == [700, 1500, 3000]
        # params_base 是基准格两条记录的均值（qs = (510+520)/2 = 515）
        row = table.params_base[1]
        assert row[3] == 515.0
        # z_base_full 均值（area=1500 → 30+1.5+i → mean = 32.0）
        np.testing.assert_allclose(table.z_base_full[1], 32.0)

    def test_lookup_exact_area(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(700)
        np.testing.assert_allclose(params, table.params_base[0])
        np.testing.assert_allclose(z, table.z_base_full[0])

    def test_lookup_interpolates_in_db_domain(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(1100)  # 700 与 1500 之间
        w = (1100 - 700) / (1500 - 700)
        expected_z = (1 - w) * table.z_base_full[0] + w * table.z_base_full[1]
        expected_p = (1 - w) * table.params_base[0] + w * table.params_base[1]
        np.testing.assert_allclose(z, expected_z)
        np.testing.assert_allclose(params, expected_p)

    def test_lookup_out_of_range_clamps(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(100)  # 小于最小档 → 取最小档
        np.testing.assert_allclose(z, table.z_base_full[0])
        params, z = table.lookup(9999)
        np.testing.assert_allclose(z, table.z_base_full[-1])

    def test_save_load_roundtrip(self, tmp_path):
        table = BaselineTable.build(_make_records())
        path = tmp_path / "baseline.npz"
        table.save(path)
        loaded = BaselineTable.load(path)
        np.testing.assert_allclose(loaded.areas, table.areas)
        np.testing.assert_allclose(loaded.params_base, table.params_base)
        np.testing.assert_allclose(loaded.z_base_full, table.z_base_full)

    def test_build_without_baseline_records_raises(self):
        import pytest

        with pytest.raises(ValueError, match="基准单元"):
            BaselineTable.build([_record(1.0, 1.0, 700, [4.8] * 7, [30.0] * 101)])
