"""P12 regressions: fine grid, catalog exclusion, bounded refine, pareto dedup, view block."""

import numpy as np
import pandas as pd
import pytest

from app.ml.optimization_data import sha256, write_json
from app.ml.optimization_p12 import (
    fine_grid,
    mark_catalog_matches,
    pareto_representatives,
    refine_peak_gp,
)
from app.ml.optimization_p12_view import make_p12_candidates


def test_fine_grid_definition_and_catalog_exclusion():
    grid = fine_grid()
    assert len(grid) == 9801 and grid.eg_um.min() == 0 and grid.eg_um.max() == 3.0
    assert grid.fl_um.max() == 2.0 and set(grid.has_pf) == {0}
    catalog = pd.DataFrame(
        dict(eg_um=[1.0], fl_um=[1.5], area_um2=[5500.0], has_pf=[0])
    )
    mask = mark_catalog_matches(grid, catalog)
    assert mask.sum() == 1 and grid.loc[mask, "eg_um"].iloc[0] == 1.0


def test_refine_peak_gp_stays_in_bounds_and_finds_synthetic_peak():
    def predict_log(frame):
        x = np.exp(-((frame.eg_um - 1.8) ** 2 + (frame.fl_um - 0.9) ** 2) * 20)
        return np.log(100 + 50 * x).to_numpy(), np.full(len(frame), 0.01)

    eg, fl, value = refine_peak_gp(predict_log, (1.6, 0.85))
    assert 0.0 <= eg <= 3.0 and 0.0 <= fl <= 2.0
    assert abs(eg - 1.8) < 0.05 and abs(fl - 0.9) < 0.05
    assert np.isfinite(value) and value > np.log(140)
    eg, fl, value = refine_peak_gp(predict_log, (3.0, 2.0))
    assert np.isfinite(value)  # Penalty keeps the optimizer finite even at the corner.


def test_pareto_representatives_dedup_identical_predictions():
    rows = [
        dict(eg_um=eg, fl_um=fl, qp=qp, k2eff_pct=k2)
        for eg, fl, qp, k2 in [
            (1.0, 0.5, 1300.0, 14.0), (1.1, 0.5, 1300.0, 14.0),
            (1.2, 0.5, 1200.0, 14.4), (0.9, 0.5, 900.0, 13.0),
        ]
    ]
    grid = pd.DataFrame(rows)
    frontier = pd.DataFrame(dict(qp=[1395.0], k2eff_pct=[14.6], marks=["C6-1"]))
    reps = pareto_representatives(grid, "qp", "k2eff_pct", frontier, 1395.957083, 14.674470)
    assert len(reps) == 2  # Two distinct non-dominated value pairs.
    assert reps.iloc[0].eg_um == 1.0  # The identical plateau pair dedupes to its first point.
    assert 1.1 not in reps.eg_um.tolist()


def p12_tree(root):
    root.mkdir(parents=True)
    rows = []
    for i, group in enumerate(["P12"] * 6 + ["P11"] * 10):
        rows.append(
            dict(
                candidate_id=f"cand{i:02d}", group=group,
                origin="peak_qp_p6c" if group == "P12" else "P11",
                eg_um=2.5, fl_um=0.25 * (i % 4), area_um2=5500.0, has_pf=0,
                branch_id="pf0_eg1_fl1", qp_prediction=1200.0 + i, qp_lower=1100.0 + i,
                qp_upper=1300.0 + i, k2eff_pct_prediction=14.5, k2eff_pct_lower=14.1,
                k2eff_pct_upper=14.9, qp_p6c=1190.0 + i, k2eff_pct_p6c=14.4,
                p3_support="outside_training_bounds", extrapolation_axis="eg_um",
                extrapolation_distance=0.5, qp_delta_vs_qp_baseline=-100.0,
                k2_delta_vs_k2_baseline=-0.1, risk_flags="演示", recommendation="待实测验证候选",
            )
        )
    for j in range(3):
        rows.append(
            dict(rows[0], candidate_id=f"ctrl{j}", group="C", origin="negative_control",
                 recommendation="不推荐（负面对照）")
        )
    frame = pd.DataFrame(rows)
    frame.to_csv(root / "candidates.csv", index=False)
    write_json(root / "verification.json", {"all_passed": True})
    write_json(
        root / "outputs_manifest.json",
        {
            str(p.relative_to(root)): sha256(p)
            for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "outputs_manifest.json"
        },
    )
    return root


def test_p12_view_block_extracts_and_excludes_controls(tmp_path):
    block = make_p12_candidates(p12_tree(tmp_path / "p12"), {"existing_structure"})
    assert block["schema"] == "aln_optimization_p12_candidates_v1"
    assert block["n_entries"] == 16 == len(block["entries"])
    assert "ctrl0" not in {e["candidate_id"] for e in block["entries"]}
    assert block["entries"][0]["origin"] == "peak_qp_p6c"
    with pytest.raises(ValueError, match="collides"):
        make_p12_candidates(p12_tree(tmp_path / "p12b"), {"cand00"})
