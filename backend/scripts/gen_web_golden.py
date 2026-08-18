"""生成 TS 移植逻辑的黄金值夹具（规格 §8）。

用法：uv run python scripts/gen_web_golden.py   # 在 backend/ 下跑，输出到 ../frontend/src/ml/__fixtures__/
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import AlgorithmConfig  # noqa: E402
from app.core.sparse_sampler import baseline_interpolate, deterministic_sample_positions  # noqa: E402
from app.ml.surrogate.baseline import BaselineTable  # noqa: E402

OUT = PROJECT_ROOT.parent / "frontend/src/ml/__fixtures__"
FREQ = np.linspace(4.0, 7.0, 1001)  # 与真实数据同轴


def gen_sampling() -> dict:
    cases = []
    for fs, fp in [(4.8, 5.1), (4.05, 4.2), (6.7, 6.95), (5.0, 5.0), (-1.0, 5.0),
                   (4.0, 7.0), (5.5, 5.62)]:
        pos = deterministic_sample_positions(FREQ, fs, fp)
        cases.append({"fs": fs, "fp": fp, "k": int(AlgorithmConfig.surr_k),
                      "positions": pos.tolist()})
    return {"freq_ghz": FREQ.tolist(), "cases": cases}


def gen_baseline() -> dict:
    rng = np.random.default_rng(7)
    pfs = np.array([0.0, 1.0])
    areas = np.array([700.0, 1200.0, 2000.0])
    params = rng.normal(0, 1, (2, 3, 7))
    z = rng.normal(40, 5, (2, 3, 21))  # 小 N 即可
    bt = BaselineTable(pfs, areas, params, z)
    cases = []
    for area, pf in [(700.0, 0.0), (950.0, 1.0), (2000.0, 1.0), (3000.0, 0.0),
                     (500.0, 0.0), (1600.0, 0.7)]:
        p, zz = bt.lookup(area, pf)
        cases.append({"area": area, "pf": pf, "params": p.tolist(), "z": zz.tolist()})
    return {"pfs": pfs.tolist(), "areas": areas.tolist(),
            "params_base": params.tolist(), "z_base_full": z.tolist(), "cases": cases}


def gen_spline() -> dict:
    rng = np.random.default_rng(11)
    cases = []
    for n_pts in (8, 32, 256):
        xs = np.sort(rng.uniform(4.0, 7.0, n_pts))
        ys = np.sin(xs * 3) * 10 + rng.normal(0, 0.5, n_pts) + 40
        out = baseline_interpolate(xs, ys, FREQ)
        cases.append({"xs": xs.tolist(), "ys": ys.tolist(), "out": out.tolist()})
    return {"freq_ghz": FREQ.tolist(), "cases": cases}


def gen_surrogate() -> dict:
    """端到端黄金：5 组格内输入的完整 Python 推理输出（供 TS 编排对拍）。"""
    from app.ml.surrogate.infer import SurrogatePredictor

    data, ckpt = PROJECT_ROOT / "surrogate_data", PROJECT_ROOT / "checkpoints_surrogate"
    if not (data / "meta.json").exists():
        return {"cases": [], "note": "surrogate_data 缺失，跳过（e2e 兜底）"}
    stage2 = ckpt / "stage2_ft.pt"
    if not stage2.exists():
        stage2 = ckpt / "stage2.pt"
    pred = SurrogatePredictor(ckpt / "stage1.pt", stage2, ckpt / "stage2_config.json",
                              data / "baseline.npz", data / "meta.json")
    cases = []
    for eg, fl, area, pf in [(0.0, 0.0, 700.0, 0.0), (1.0, 0.5, 1500.0, 0.0),
                             (2.0, 2.0, 5500.0, 1.0), (0.75, 1.25, 3000.0, 0.0),
                             (1.5, 0.25, 4500.0, 1.0)]:
        r = pred.predict(eg, fl, area, pf)
        cases.append({"eg": eg, "fl": fl, "area": area, "pf": pf,
                      "params": r["params"], "sample_freq": r["sample_freq"].tolist(),
                      "sample_z": r["sample_z"].tolist(), "z_db": r["z_db"].tolist()})
    return {"cases": cases}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, gen in [("sampling", gen_sampling), ("baseline", gen_baseline),
                      ("spline", gen_spline), ("surrogate", gen_surrogate)]:
        (OUT / f"{name}.json").write_text(json.dumps(gen()), encoding="utf-8")
        print(f"written {name}.json")


if __name__ == "__main__":
    main()
