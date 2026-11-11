"""P11 regressions: frozen grid, frontier math, dominance, ranking layers, risk flags."""

import pandas as pd

from app.ml.optimization_p11 import (
    GRID_A,
    GRID_B,
    build_candidates,
    dominance,
    measured_frontier,
    pareto_layer,
    risk_flags,
)


def test_frozen_grid_shape_and_branches():
    frame = build_candidates()
    assert len(frame) == len(GRID_A) + len(GRID_B) + 1
    assert frame.groupby("group").size().to_dict() == {"A": 4, "B": 6, "C": 1}
    assert frame.candidate_id.is_unique
    assert frame[frame.group.eq("C")].iloc[0].branch_id == "pf1_eg0_fl1"
    assert set(frame[frame.group.eq("A")].branch_id) == {"pf0_eg1_fl0", "pf0_eg1_fl1"}


def test_frontier_and_dominance_math():
    catalog = pd.DataFrame(
        dict(
            marks=["worse", "champ_qp", "champ_k2", "mid"],
            qp=[100.0, 200.0, 150.0, 160.0],
            k2eff_pct=[10.0, 12.0, 14.0, 13.0],
            eg_um=0.0, fl_um=0.0, area_um2=5500.0, has_pf=0,
        )
    )
    front = measured_frontier(catalog)
    assert front.marks.tolist() == ["champ_qp", "mid", "champ_k2"]
    wins, dominated = dominance(210.0, 13.0, front)
    assert wins == ["champ_qp", "mid"] and dominated == []  # champ_k2 keeps the k2 edge.
    wins, dominated = dominance(100.0, 11.0, front)
    assert wins == [] and dominated  # Everything dominates a dominated point.
    wins, _ = dominance(200.0, 12.0, front)
    assert wins == []  # Exact tie with a frontier point is not dominance.
    wins, _ = dominance(210.0, 11.0, front)
    assert wins == []  # Qp alone does not dominate without k2.


def test_pareto_layer_and_risk_flags():
    frame = pd.DataFrame(dict(q=[3.0, 2.0, 1.0], k=[1.0, 2.0, 3.0]))
    assert pareto_layer(frame, "q", "k") == [True, True, True]
    frame.loc[3] = [0.5, 0.5]
    assert pareto_layer(frame, "q", "k") == [True, True, True, False]
    flags = risk_flags(type("R", (), dict(eg_um=2.5, fl_um=0.0, group="A"))())
    assert any("EG 超出实测范围" in f for f in flags)
    flags = risk_flags(type("R", (), dict(eg_um=0.0, fl_um=2.5, group="C"))())
    assert any("不推荐" in f for f in flags) and any("FL 超出" in f for f in flags)
    assert not risk_flags(type("R", (), dict(eg_um=1.0, fl_um=1.5, group="B"))())
