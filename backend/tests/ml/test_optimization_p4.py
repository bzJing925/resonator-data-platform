"""Search acceptance tests independent of P3 predictive accuracy."""

import itertools

import numpy as np
import pytest

from app.ml.optimization_baselines import ScalarBaseline
from app.ml.optimization_data import GEOMETRY, STANDARD_COHORT, TARGETS
from app.ml.optimization_search import (
    enumerate_grid,
    local_cell_support,
    pareto_mask,
    search_catalog,
    validate_config,
)


def catalog():
    frame = enumerate_grid([dict(zip(GEOMETRY, [[1, 2], [1, 2], [700, 900], [0]], strict=True))])
    for i, target in enumerate(TARGETS):
        frame[target] = np.arange(1, len(frame) + 1) if i % 2 == 0 else np.arange(len(frame), 0, -1)
        frame[target + "_n_coords"] = 12
    frame["fs_ghz"], frame["fp_ghz"] = 4.5, 4.7
    frame["design_group_id"] = frame.structure_id
    return frame


def test_local_cells_fixed_axis_holes_and_boundary():
    state = {
        "x": np.array([[0, 1, 700], [0, 1, 900], [0, 2, 700], [0, 2, 900]]),
        "structure_ids": ["a", "b", "c", "d"],
    }
    middle = local_cell_support(state, np.array([0, 1.5, 800]))
    assert middle["local_support"] == "complete_local_cell"
    assert middle["present_corners"] == middle["required_corners"] == 4
    assert local_cell_support(state, state["x"][0])["local_support"] == "exact_fitted_structure"
    assert local_cell_support(state, np.array([0.1, 1.5, 800]))["local_support"] == (
        "outside_training_bounds"
    )
    missing = {"x": state["x"][:-1], "structure_ids": state["structure_ids"][:-1]}
    assert local_cell_support(missing, np.array([0, 1.5, 800]))["present_corners"] == 3
    assert local_cell_support(missing, np.array([0, 1.5, 800]))["local_support"] == (
        "missing_local_corners"
    )
    assert local_cell_support(None, np.array([0, 1, 700]))["local_support"] == "unknown_branch"


def test_pareto_matches_independent_brute_force_and_keeps_ties():
    values = np.array(list(itertools.product(range(3), repeat=4)) + [(2, 2, 2, 2)])
    expected = [
        not any(
            all(a >= b for a, b in zip(other, row, strict=True))
            and any(a > b for a, b in zip(other, row, strict=True))
            for other in values
        )
        for row in values
    ]
    assert pareto_mask(values).tolist() == expected
    assert sum(expected) == 2
    with pytest.raises(ValueError, match="finite"):
        pareto_mask(np.array([[1, np.nan]]))


def test_measured_constraints_and_vectors_never_splice():
    frame = catalog()
    config = {"minimums": {"qs": 3}, "bounds": {"fs_ghz": [4.4, 4.6]}, "top_n": 2}
    result = search_catalog(frame, config)
    expected = frame[frame.qs.ge(3)]
    assert set(result["feasible"].structure_id) == set(expected.structure_id)
    for target, rows in result["ranks"].items():
        assert rows[target].tolist() == sorted(expected[target], reverse=True)[:2]
        for row in rows.itertuples():
            original = frame[frame.structure_id.eq(row.structure_id)].iloc[0]
            assert all(getattr(row, t) == original[t] for t in TARGETS)
    assert not result["feasible"].trusted_for_unmeasured_optimization.any()
    assert result["summary"]["n_pareto"] == len(expected)


def test_no_solution_and_missing_metrics_are_explicit():
    frame = catalog()
    result = search_catalog(frame, {"minimums": {"qp": 1000}})
    assert result["summary"]["status"] == "no_feasible_candidates"
    assert result["pareto"].empty
    assert all(df.empty for df in result["ranks"].values())
    frame.loc[0, "qs"] = np.nan
    frame.loc[1, "qp_n_coords"] = 2
    result = search_catalog(frame, {})
    assert not result["candidates"].iloc[:2].eligible.any()
    assert result["summary"]["n_feasible"] == 6


def test_exploratory_support_is_not_trust_or_observation():
    frame = catalog()
    fit = frame.iloc[:-1]
    bundle = {
        "models": {t: ScalarBaseline("nearest_measured", t).fit(fit) for t in TARGETS},
        "metadata": {
            "schema_version": "test",
            "calibration": {t: {"log_radius": 0.1} for t in TARGETS},
        },
    }
    config = {
        "mode": "exploratory",
        "grid": [
            dict(zip(GEOMETRY, [[1, 1.5, 2, 3], [1, 1.5, 2], [700, 800, 900], [0, 1]], strict=True))
        ],
    }
    result = search_catalog(frame, config, bundle)
    assert not result["candidates"].trusted_for_unmeasured_optimization.any()
    assert result["feasible"].has_pf.eq(0).all()
    assert result["feasible"].eg_um.le(2).all()
    assert result["candidates"].value_source.eq("frozen_p3_surrogate_exploratory").all()
    hole = result["candidates"].query("eg_um == 1.5 and fl_um == 1.5 and area_um2 == 800")
    assert not hole.eligible.any()
    assert result["summary"]["n_rejected"] > 0
    assert result["summary"]["n_feasible"] > 0


@pytest.mark.parametrize(
    "config",
    [
        {"mode": "trusted"},
        {"ag_um": 1},
        {"minimums": {"qp_bodeq": 100}},
        {"bounds": {"fs_ghz": [4, float("nan")]}},
        {"bounds": {"area_um2": [900, 700]}},
        {"min_coordinates": 9},
        {"top_n": True},
        {"grid": []},
        {"mode": "exploratory", "bounds": {"fs_ghz": [4, 5]}},
    ],
)
def test_invalid_config(config):
    with pytest.raises(ValueError):
        validate_config(config)


@pytest.mark.parametrize(
    "grid",
    [
        {"eg_um": [1], "fl_um": [1], "area_um2": [700], "has_pf": [2]},
        {"eg_um": [1], "fl_um": [1], "area_um2": [0], "has_pf": [0]},
        {"eg_um": [float("inf")], "fl_um": [1], "area_um2": [700], "has_pf": [0]},
        {"eg_um": [1], "fl_um": [1], "area_um2": [700], "has_pf": [0], "ag_um": [1]},
        {"eg_um": list(range(101)), "fl_um": list(range(101)), "area_um2": [700], "has_pf": [0]},
    ],
)
def test_invalid_grid(grid):
    with pytest.raises(ValueError):
        enumerate_grid([grid])


def test_no_unknown_cohort():
    frame = catalog()
    assert frame.cohort.eq(STANDARD_COHORT).all()
    frame.loc[0, "cohort"] = "AG1"
    with pytest.raises(ValueError, match="cohort"):
        search_catalog(frame, {})
