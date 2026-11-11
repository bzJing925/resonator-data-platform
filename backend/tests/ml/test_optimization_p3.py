"""Consequential regression checks: geometry identity, leakage and interval semantics."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.filename import parse_filename
from app.core.mapping import load_mapping, parse_description
from app.ml.optimization_baselines import (
    ScalarBaseline,
    boundary_manifests,
    conformal_radius,
    make_split_manifest,
    predict_bundle,
)
from app.ml.optimization_data import (
    STANDARD_COHORT,
    assign_design_groups,
    canonical_device,
    cohort,
)


def test_real_numbering_and_spaced_tokens():
    assert parse_filename("S11_2_K-10_X-1Y-1N27_Fail_de.s1p").mark == "K-10"
    entry = parse_description("U4-1", "FL1 AG 1.25 2500 & 3500")
    assert (entry.fl, entry.ag, entry.area_s11, entry.area_s22) == (1, 1.25, 2500, 3500)
    assert parse_description("K-10", "Airgap 1.5").ag == 1.5
    assert canonical_device("S11_2_K-10_X0Y0N1_Fail_de.s1p") == "S11_2_K-10_X0Y0N1_Fail.s1p"


def test_header_excluded_and_duplicate_rejected(tmp_path):
    path = tmp_path / "mapping.csv"
    path.write_text("Mark,Description\nK-10,Airgap 1.5\n")
    assert list(load_mapping(path)) == ["K-10"]
    path.write_text("K-10,Airgap 1.5\nK-10,Airgap 2\n")
    with pytest.raises(ValueError, match="重复"):
        load_mapping(path)


def test_ambiguous_topology_is_not_merged_with_numeric_standard():
    assert cohort("EG0.75 FL1 700&5500") == STANDARD_COHORT
    assert cohort("EG0+PF FL0.5 700&5500") == STANDARD_COHORT
    for desc in (
        "EG0.75 No AG FL1 700&5500",
        "EG0.75 C>B FL1 700&5500",
        "BASIC+EG 700&5500",
        "EG0 FL0 200",
        "EG FL0 0.75",
    ):
        assert cohort(desc) != STANDARD_COHORT


def test_connected_components_bind_both_ports_and_repeated_structures():
    records = pd.DataFrame(
        [
            [True, "s1", "A1", "p1"],
            [True, "s2", "A1", "p1"],
            [True, "s2", "A2", "p2"],
            [True, "s3", "A2", "p2"],
            [True, "s4", "B1", "p3"],
        ],
        columns=["geometry_eligible", "structure_id", "mark", "physical_id"],
    )
    result = assign_design_groups(records)
    assert result.iloc[:4].design_group_id.nunique() == 1
    assert result.iloc[4].design_group_id != result.iloc[0].design_group_id


def _structures():
    rows = []
    for design in range(20):
        for port in range(2):
            rows.append(
                {
                    "structure_id": f"s{design}_{port}",
                    "design_group_id": f"d{design}",
                    "branch_id": "pf0_eg1_fl1",
                    "has_pf": 0,
                    "eg_um": 1.0,
                    "fl_um": (design % 5 + 1) * 0.5,
                    "area_um2": 700 + design * 20 + port * 10,
                    "qs": 100.0 + design,
                }
            )
    return pd.DataFrame(rows)


def test_split_and_boundary_embargo_are_group_disjoint():
    data = _structures()
    main = make_split_manifest(data)
    assert set(main.role) == {"train", "validation", "calibration", "test"}
    assert main.groupby("design_group_id").role.nunique().max() == 1
    pd.testing.assert_frame_equal(main, make_split_manifest(data))
    for m in boundary_manifests(data).values():
        test_groups = set(m.loc[m.role.eq("test"), "design_group_id"])
        assert not test_groups & set(
            m.loc[m.role.isin(["train", "calibration"]), "design_group_id"]
        )


def test_conformal_uses_design_maxima_and_finite_sample_rank():
    cal = pd.DataFrame(
        {"design_group_id": np.repeat(np.arange(10), 2), "qs": np.exp(np.arange(20) / 10)}
    )
    radius = conformal_radius(cal, np.ones(20), "qs")
    assert radius["n_calibration_design_groups"] == 10
    assert radius["rank"] == 10
    assert radius["log_radius"] == pytest.approx(1.9)
    assert conformal_radius(cal.iloc[:2], np.ones(2), "qs")["log_radius"] is None


def test_model_does_not_learn_test_scaling_and_refuses_unsupported_inputs():
    data = _structures()
    model = ScalarBaseline("nearest_measured", "qs").fit(data.iloc[:20])
    future = data.iloc[30:].copy()
    support = model.support(future)
    assert set(support.support) == {"outside_training_bounds"}
    assert model.branches["pf0_eg1_fl1"]["high"][2] == data.iloc[:20].area_um2.max()
    bundle = {
        "models": {"qs": model},
        "metadata": {"schema_version": "test", "calibration": {"qs": {"log_radius": 0.1}}},
    }
    result = predict_bundle(bundle, data.iloc[:2])
    assert not result.trusted_for_unmeasured_optimization.any()
    with pytest.raises(ValueError, match="discrete"):
        predict_bundle(bundle, data.iloc[:2].assign(has_pf=0.3))
    with pytest.raises(ValueError, match="AG"):
        predict_bundle(bundle, data.iloc[:2].assign(ag_um=1.0))
