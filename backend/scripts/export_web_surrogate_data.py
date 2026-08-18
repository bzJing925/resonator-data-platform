"""代理模型静态网页数据导出：baseline.json / surr_meta.json / cell_means.parquet（规格 §3）。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np


def export_surrogate_webdata(surrogate_dir: Path, out_dir: Path) -> tuple[list[str], list[str]]:
    """返回 (written, missing)。任一源文件缺失 → 全部跳过并记入 missing。"""
    need = [surrogate_dir / "baseline.npz", surrogate_dir / "meta.json",
            surrogate_dir / "dataset.npz"]
    if not all(p.exists() for p in need):
        return [], ["baseline.json", "surr_meta.json", "cell_means.parquet"]

    with np.load(surrogate_dir / "baseline.npz") as b:
        baseline = {k: b[k].tolist() for k in ("pfs", "areas", "params_base", "z_base_full")}
    (out_dir / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")

    meta = json.loads((surrogate_dir / "meta.json").read_text(encoding="utf-8"))
    surr_meta = {k: meta[k] for k in
                 ("freq_ghz", "k", "x_mean", "x_std", "params_mean", "params_std",
                  "z_mean", "z_std")}
    (out_dir / "surr_meta.json").write_text(json.dumps(surr_meta), encoding="utf-8")

    import pandas as pd

    with np.load(surrogate_dir / "dataset.npz") as z:
        xs, full_z = z["x_struct"], z["full_z"]
    n_freq = full_z.shape[1]
    # 按 (eg,fl,area,pf) 格求均值谱 → 长表
    keys = {}
    for i in range(len(xs)):
        key = (float(xs[i, 0]), float(xs[i, 1]), int(xs[i, 3]), int(xs[i, 4]))
        keys.setdefault(key, []).append(i)
    recs = []
    for (eg, fl, area, pf), idxs in sorted(keys.items()):
        mean_z = full_z[idxs].mean(axis=0)
        for j in range(n_freq):
            recs.append((eg, fl, area, pf, j, float(mean_z[j])))
    df = pd.DataFrame(recs, columns=["eg", "fl", "area", "pf", "idx", "z"])
    df.to_parquet(out_dir / "cell_means.parquet", compression="zstd", index=False)

    return ["baseline.json", "surr_meta.json", "cell_means.parquet"], []
