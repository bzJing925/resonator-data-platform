"""基准表构建/查询/插值/存取单测（v4：PF 维）。"""

import numpy as np
import pytest

from app.ml.surrogate.baseline import BaselineTable


def _record(eg, fl, area, params, z_full, has_pf=0):
    return {
        "eg": eg, "fl": fl, "ag": 0.0, "area_um2": area, "has_pf": has_pf,
        "params": np.asarray(params, dtype=np.float64),
        "full_z": np.asarray(z_full, dtype=np.float64),
    }


def _make_records():
    """2 个面积档 × 2 个 PF × 基准各 2 条 + 非基准 2 条。"""
    recs = []
    for pf in (0, 1):
        for area in (700, 1500):
            for i in range(2):
                recs.append(_record(
                    0.0, 0.0, area,
                    params=[4.8, 5.1, 7.0, 510 + i * 10 + pf * 100, 400, 450, 380],
                    z_full=np.full(101, 30.0 + area / 1000 + i + pf * 10),
                    has_pf=pf,
                ))
    # 非基准：eg≠0、以及 eg=0 但 fl≠0（也不应进基准表）
    recs.append(_record(1.0, 0.5, 1500, [4.9, 5.2, 7.2, 600, 500, 550, 480],
                        np.full(101, 50.0)))
    recs.append(_record(0.0, 1.0, 1500, [4.9, 5.2, 7.2, 600, 500, 550, 480],
                        np.full(101, 50.0)))
    return recs


class TestBaselineTable:
    def test_build_groups_by_pf_and_area(self):
        table = BaselineTable.build(_make_records())
        assert table.areas.tolist() == [700, 1500]
        assert table.pfs.tolist() == [0.0, 1.0]
        # pf=0, area=1500: qs 均值 = (510+520)/2 = 515
        assert table.params_base[0, 1, 3] == 515.0
        # pf=1, area=1500: qs 均值 = (610+620)/2 = 615
        assert table.params_base[1, 1, 3] == 615.0
        # z_base_full pf=1, area=700: 30+0.7+10+i → mean 41.2
        np.testing.assert_allclose(table.z_base_full[1, 0], 41.2)

    def test_lookup_exact(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(700, 0)
        np.testing.assert_allclose(params, table.params_base[0, 0])
        np.testing.assert_allclose(z, table.z_base_full[0, 0])
        _, z1 = table.lookup(700, 1)
        np.testing.assert_allclose(z1, table.z_base_full[1, 0])

    def test_lookup_interpolates_area_in_db_domain(self):
        table = BaselineTable.build(_make_records())
        params, z = table.lookup(900, 0)  # 700/1500 之间，w=0.25
        w = (900 - 700) / (1500 - 700)
        np.testing.assert_allclose(
            z, (1 - w) * table.z_base_full[0, 0] + w * table.z_base_full[0, 1]
        )
        np.testing.assert_allclose(
            params, (1 - w) * table.params_base[0, 0] + w * table.params_base[0, 1]
        )

    def test_lookup_out_of_range_clamps(self):
        table = BaselineTable.build(_make_records())
        _, z = table.lookup(100, 0)
        np.testing.assert_allclose(z, table.z_base_full[0, 0])
        _, z = table.lookup(9999, 1)
        np.testing.assert_allclose(z, table.z_base_full[1, -1])

    def test_lookup_returns_copies(self):
        """越界 clamp 分支返回副本，原地修改不污染表。"""
        table = BaselineTable.build(_make_records())
        _, z = table.lookup(100, 0)
        z[:] = -999
        assert table.z_base_full[0, 0, 0] != -999

    def test_save_load_roundtrip(self, tmp_path):
        table = BaselineTable.build(_make_records())
        path = tmp_path / "baseline.npz"
        table.save(path)
        loaded = BaselineTable.load(path)
        np.testing.assert_allclose(loaded.areas, table.areas)
        np.testing.assert_allclose(loaded.pfs, table.pfs)
        np.testing.assert_allclose(loaded.params_base, table.params_base)
        np.testing.assert_allclose(loaded.z_base_full, table.z_base_full)

    def test_build_without_baseline_records_raises(self):
        with pytest.raises(ValueError, match="基准单元"):
            BaselineTable.build([_record(1.0, 1.0, 700, [4.8] * 7, [30.0] * 101)])
