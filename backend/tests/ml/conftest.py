"""合成小 npz 夹具：6 物理组 × 4 器件，N=101，K=32（v4：5 维输入 + groups）。"""

import json

import numpy as np
import pytest


@pytest.fixture()
def tiny_npz(tmp_path):
    rng = np.random.default_rng(0)
    n_freq, k = 101, 32
    groups = [
        (0.0, 0.0, 0, "A1-1", "train"), (0.0, 0.0, 1, "G1-1", "train"),
        (1.0, 0.5, 0, "B0", "train"), (1.0, 0.5, 1, "H0", "train"),
        (2.0, 1.0, 0, "C0", "val"), (0.75, 1.25, 0, "D0", "test"),
    ]
    freq = np.linspace(4.0, 7.0, n_freq)
    recs = {"x_struct": [], "y_params": [], "params_base": [], "sample_pos": [],
            "y_sparse": [], "sparse_base": [], "full_z": [], "phase": [], "group_idx": []}
    for gi, (eg, fl, pf, _mark, _split) in enumerate(groups):
        for _ in range(4):
            area = 700 + gi * 200
            z = 40 + 5 * np.sin(freq * 2) + rng.normal(0, 0.1, n_freq) + area / 1000
            pos = np.sort(rng.choice(n_freq, k, replace=False)).astype(np.int32)
            recs["x_struct"].append([eg, fl, 1.0, area, pf])  # [eg, fl, ag, area, pf]
            recs["y_params"].append([4.8 + eg * 0.01, 5.1 + fl * 0.01, 7.0, 500, 400, 450, 380])
            recs["params_base"].append([4.8, 5.1, 7.0, 500, 400, 450, 380])
            recs["sample_pos"].append(pos)
            recs["y_sparse"].append(z[pos])
            recs["sparse_base"].append(z[pos] - 0.5)
            recs["full_z"].append(z)
            recs["phase"].append(np.zeros(n_freq))
            recs["group_idx"].append(gi)
    np.savez_compressed(
        tmp_path / "dataset.npz",
        x_struct=np.array(recs["x_struct"], dtype=np.float32),
        y_params=np.array(recs["y_params"], dtype=np.float32),
        params_base=np.array(recs["params_base"], dtype=np.float32),
        sample_pos=np.array(recs["sample_pos"]),
        y_sparse=np.array(recs["y_sparse"], dtype=np.float32),
        sparse_base=np.array(recs["sparse_base"], dtype=np.float32),
        full_z=np.array(recs["full_z"], dtype=np.float32),
        phase=np.array(recs["phase"], dtype=np.float32),
        group_idx=np.array(recs["group_idx"], dtype=np.int32),
    )
    meta = {
        "groups": [
            {"eg": g[0], "fl": g[1], "has_pf": g[2], "mark": g[3], "split": g[4]}
            for g in groups
        ],
        "freq_ghz": freq.tolist(), "k": k, "n_freq": n_freq,
        "x_mean": [0.5] * 5, "x_std": [1.0] * 5,
        "params_mean": [4.85, 5.15, 7.0, 500, 400, 450, 380],
        "params_std": [0.02, 0.02, 0.5, 50, 40, 45, 38],
        "z_mean": [42.0], "z_std": [3.0],
    }
    (tmp_path / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return tmp_path
