"""提取链路冒烟：2 个合成 s2p + 构造行 → 清洗/抽样/基准/落地。"""

from pathlib import Path

import numpy as np
import skrf


def _make_2port_s2p(path: Path, n: int = 301) -> None:
    """合成 S11/S22 不同的 2-port S2P（4–7 GHz）。"""
    freq = np.linspace(4.0, 7.0, n)
    f0 = 4.9
    z1 = 50.0 + 200.0 * ((freq - f0) ** 2 - 0.05) / ((freq - f0) ** 2 + 0.05)
    z2 = 50.0 + 150.0 * ((freq - (f0 + 0.2)) ** 2 - 0.03) / ((freq - (f0 + 0.2)) ** 2 + 0.03)
    s = np.zeros((n, 2, 2), dtype=complex)
    s[:, 0, 0] = (z1 - 50.0) / (z1 + 50.0)
    s[:, 1, 1] = (z2 - 50.0) / (z2 + 50.0)
    net = skrf.Network(f=freq * 1e9, s=s, z0=np.full((n, 2), 50.0))
    net.write_touchstone(str(path), form="ma")


def _row(i, mark, pf, path_name, fs=4.8, fp=5.1):
    return {"id": i, "eg": 0.0 if pf == 0 else 1.0, "fl": 0.0, "area_um2": 700,
            "has_pf": pf, "mark": mark,
            "fs_ghz": fs, "fp_ghz": fp, "k2eff_pct": 7.0, "qs": 500.0, "qp": 400.0,
            "qs_bodeq": 450.0, "qp_bodeq": 380.0,
            "s_param_path": path_name, "s_param_port": "S11", "batch_no": "T1"}


def test_extract_end_to_end(tmp_path):
    from app.ml.surrogate.baseline import BaselineTable
    from app.ml.surrogate.extract import (
        _group_tuple,
        assemble_npz,
        build_device_record,
        clean_records,
        compute_norm_stats,
        fill_sparse_base,
        select_per_cell,
        split_groups,
    )

    files_dir = tmp_path / "files" / "T1"
    files_dir.mkdir(parents=True)
    _make_2port_s2p(files_dir / "a.s2p")
    _make_2port_s2p(files_dir / "b.s2p")

    rows = [_row(1, "A1-1", 0, "a.s2p", fs=4.8, fp=5.1),
            _row(2, "H1-1", 1, "b.s2p", fs=4.9, fp=5.2)]
    freq_axis = np.linspace(4.0, 7.0, 1001)

    recs = [build_device_record(r, tmp_path / "files", freq_axis, k=32) for r in rows]
    assert all(r is not None for r in recs)
    assert recs[0]["full_z"].shape == (1001,)
    assert recs[0]["sample_pos"].shape == (32,)
    assert recs[0]["has_pf"] == 0 and recs[1]["has_pf"] == 1
    assert recs[0]["ag"] == 1.0  # surr_default_ag 占位

    kept, dropped = clean_records(recs, fs_tol_mhz=1e9, trend_sigma=1e9)
    assert len(kept) == 2
    table = BaselineTable.build(kept)  # eg=0/fl=0 的只有 a（pf=0）
    for r in kept:
        r["params_base"], _ = table.lookup(r["area_um2"], r["has_pf"])
    fill_sparse_base(kept, table)

    selected = select_per_cell(kept, max_per_cell=5)
    assert len(selected) == 2

    groups = sorted({_group_tuple(r) for r in kept})
    splits = split_groups(groups, val_n=0, test_n=1, seed=42)
    stats = compute_norm_stats(kept, splits)
    assemble_npz(selected, groups, splits, stats, dropped, tmp_path / "out", freq_axis, 32)
    data = np.load(tmp_path / "out" / "dataset.npz")
    assert data["full_z"].shape == (2, 1001)
    assert data["x_struct"].shape == (2, 5)
    assert np.all(np.isfinite(data["full_z"]))
