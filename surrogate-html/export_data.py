"""代理模型离线单文件数据导出：baseline.json / surr_meta.json / ui_meta.json。

（--with-truth 时追加 cell_means.json 格均值真值谱）

改编自 backend/scripts/export_web_surrogate_data.py，差异：
  - 输出到 surrogate-html/assets/；自动把仓库 backend/ 插入 sys.path；
  - cell_means 不写 Arrow，改写紧凑 JSON（--with-truth 时才生成，约 10MB 级）；
  - 新增 ui_meta.json：滑杆范围、面积档、仿真外推档、训练网格集合（原静态版由
    DuckDB 查 cell_means 得到，离线单文件版直接导出）。

用法：
    backend/.venv/bin/python surrogate-html/export_data.py [--with-truth]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
BACKEND = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def _r4(v: float) -> float:
    """float32 派生值收敛到 4 位小数（浏览器侧以 1e-6 容差匹配）。"""
    return round(float(v), 4)


def export_all(surrogate_dir: Path, out_dir: Path, with_truth: bool) -> list[str]:
    need = [
        surrogate_dir / "baseline.npz",
        surrogate_dir / "meta.json",
        surrogate_dir / "dataset.npz",
    ]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        raise FileNotFoundError(f"代理模型数据缺失: {missing}")
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    with np.load(surrogate_dir / "baseline.npz") as b:
        baseline = {k: b[k].tolist() for k in ("pfs", "areas", "params_base", "z_base_full")}
    (out_dir / "baseline.json").write_text(json.dumps(baseline), encoding="utf-8")
    written.append("baseline.json")

    meta = json.loads((surrogate_dir / "meta.json").read_text(encoding="utf-8"))
    surr_meta = {
        k: meta[k]
        for k in (
            "freq_ghz",
            "k",
            "x_mean",
            "x_std",
            "params_mean",
            "params_std",
            "z_mean",
            "z_std",
        )
    }
    from app.services.model_metadata import describe
    metadata = describe(surrogate_dir, BACKEND / 'checkpoints_surrogate', BACKEND / 'eval_out')
    onnx_metadata = out_dir / 'model_metadata.json'
    if onnx_metadata.is_file():
        from app.schemas.model_metadata import ModelMetadata
        try:
            published = ModelMetadata.model_validate_json(onnx_metadata.read_text())
            if published.source_model_digest == metadata.model_digest:
                metadata = published
            else:
                metadata.source_model_digest, metadata.model_digest = metadata.model_digest, None
        except ValueError:
            metadata.source_model_digest, metadata.model_digest = metadata.model_digest, None
    else:
        metadata.source_model_digest, metadata.model_digest = metadata.model_digest, None
    surr_meta['metadata'] = metadata.model_dump(mode='json')
    (out_dir / "surr_meta.json").write_text(json.dumps(surr_meta), encoding="utf-8")
    written.append("surr_meta.json")

    # ---- ui_meta.json：页面控件选项 + 训练网格（供「网格外」提示） ----
    with np.load(surrogate_dir / "dataset.npz") as z:
        xs = z["x_struct"]
        full_z = z["full_z"]
    sim_areas: list[int] = []
    ext_path = surrogate_dir / "sim_extension.npz"
    if ext_path.exists():
        with np.load(ext_path) as e:
            sim_areas = sorted({int(a) for a in e["cells"][:, 2]})
    cells: dict[tuple[float, float, int, int], list[int]] = {}
    for i in range(len(xs)):
        key = (_r4(xs[i, 0]), _r4(xs[i, 1]), int(xs[i, 3]), int(xs[i, 4]))
        cells.setdefault(key, []).append(i)
    ui_meta = {
        "eg_min": float(xs[:, 0].min()),
        "eg_max": float(xs[:, 0].max()),
        "fl_min": float(xs[:, 1].min()),
        "fl_max": float(xs[:, 1].max()),
        "areas": sorted({int(a) for a in xs[:, 3]} | set(sim_areas)),
        "sim_only_areas": sim_areas,
        "grid_cells": sorted([list(k) for k in cells]),
    }
    (out_dir / "ui_meta.json").write_text(json.dumps(ui_meta), encoding="utf-8")
    written.append("ui_meta.json")

    # ---- cell_means.json（可选）：格均值真值谱叠加 ----
    if with_truth:
        n_freq = full_z.shape[1]
        cm_cells = []
        for (eg, fl, area, pf), idxs in sorted(cells.items()):
            mean_z = full_z[idxs].mean(axis=0)
            cm_cells.append(
                [eg, fl, area, pf, [round(float(v), 3) for v in mean_z]]
            )
        payload = {"n_freq": int(n_freq), "cells": cm_cells}
        (out_dir / "cell_means.json").write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
        written.append("cell_means.json")

    return written


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(BACKEND / "surrogate_data"))
    ap.add_argument("--out", default=str(HERE / "assets"))
    ap.add_argument("--with-truth", action="store_true",
                    help="同时导出 cell_means.json（格均值真值叠加数据，数 MB）")
    args = ap.parse_args()
    written = export_all(Path(args.data), Path(args.out), args.with_truth)
    for name in written:
        size = (Path(args.out) / name).stat().st_size
        print(f"已导出 {name}（{size / 1024:.0f} KiB）")


if __name__ == "__main__":
    main()
