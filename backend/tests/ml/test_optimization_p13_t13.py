from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from scripts.run_optimization_p13_t13 import (
    T13Error,
    audit_no_model_or_extremum_execution,
    build_shortlist,
    measured_overlaps,
    validate_geometry,
    validate_shortlist,
)


def candidates():
    rows = []
    for index in range(20):
        negative = index >= 17
        exact = index == 19
        eg = 0.0 if exact else 0.0 if index == 17 else 1.0 if index == 18 else 3 + index / 10
        fl = 1.5 if exact else 0.0 if index == 17 else 2.5
        area = 700.0 if exact else 5500.0
        pf = 1 if exact else 0
        branch = f"pf{pf}_eg{int(eg > 0)}_fl{int(fl > 0)}"
        rows.append(
            {
                "candidate_id": f"c_{index:02d}",
                "group": "C" if negative else "P12",
                "origin": "negative_control" if negative else "peak",
                "control_type": "negative_control" if negative else "nonnegative_control",
                "eg_um": str(eg),
                "fl_um": str(fl),
                "area_um2": str(area),
                "has_pf": str(pf),
                "branch_id": branch,
                "k2eff_pct_prediction": f"{14 + index / 100:.17f}",
                "risk_flags": "original exclusion reason" if negative else "existing risk",
                "recommendation": "不推荐（负面对照）" if negative else "P12 峰位候选",
                "new_prediction": "False",
            }
        )
    return pd.DataFrame(rows)


def measured_structures():
    rows = []
    for index in range(576):
        exact = index == 0
        eg = 0.0 if exact else 0.0
        fl = 1.5 if exact else 0.0
        area = 700.0 if exact else 701.0 + index
        pf = 1 if exact else 0
        rows.append(
            {
                "structure_id": "s_c454ac7ec8a2a7f8" if exact else f"s_{index:04d}",
                "eg_um": str(eg),
                "fl_um": str(fl),
                "area_um2": str(area),
                "has_pf": str(pf),
                "branch_id": f"pf{pf}_eg{int(eg > 0)}_fl{int(fl > 0)}",
            }
        )
    return pd.DataFrame(rows)


def bundle():
    return {
        "grid": candidates(),
        "measured": measured_structures(),
        "deduplication": {
            "eg_fl_atol_um": 1e-9,
            "area_atol_um2": 1e-9,
            "pf_must_match": True,
            "approximate_neighbor_exclusion": False,
        },
    }


def test_shortlist_preserves_17_failure_controls_and_three_exclusions():
    input_bundle = bundle()
    result = build_shortlist(input_bundle)
    negative = result.control_type.eq("negative_control")
    assert len(result) == 20
    assert result.loc[~negative, "included"].all()
    assert result.loc[~negative, "included_as_failure_control"].all()
    assert result.loc[~negative, "reason"].eq("no_eligible_models").all()
    assert not result.loc[negative, "included"].any()
    assert result.loc[negative, "reason"].eq("original exclusion reason").all()
    assert not result.recommendation_eligible.any()
    assert not result.new_candidate.any()
    assert not result.a_or_b_eligible.any()
    assert result.loc[~negative, "measured_match_count"].eq(0).all()
    exact = result[result.candidate_id.eq("c_19")].iloc[0]
    assert exact.measured_exact_match
    assert exact.measured_match_structure_ids == "s_c454ac7ec8a2a7f8"
    assert not exact.included


def test_nonnegative_measured_overlap_is_rejected():
    input_bundle = bundle()
    first = input_bundle["grid"].iloc[0]
    input_bundle["measured"].loc[1, ["eg_um", "fl_um", "area_um2", "has_pf"]] = [
        first.eg_um,
        first.fl_um,
        first.area_um2,
        first.has_pf,
    ]
    with pytest.raises(T13Error, match="NONNEGATIVE_CONTROL_MEASURED_OVERLAP"):
        build_shortlist(input_bundle)


def test_geometry_and_unique_id_are_enforced():
    with pytest.raises(T13Error, match="DUPLICATE_CANDIDATE_ID"):
        build_shortlist({**bundle(), "grid": pd.concat([candidates(), candidates().iloc[:1]])})
    invalid = candidates()
    invalid.loc[0, "eg_um"] = "-0.1"
    with pytest.raises(T13Error, match="NEGATIVE_GEOMETRY"):
        validate_geometry(invalid)
    mismatch = candidates()
    mismatch.loc[0, "branch_id"] = "pf1_eg0_fl0"
    with pytest.raises(T13Error, match="GEOMETRY_BRANCH_MISMATCH"):
        validate_geometry(mismatch)


def test_overlap_uses_o02_tolerances_and_exact_pf():
    candidate = candidates().iloc[19:20].copy()
    measured = pd.DataFrame(
        [
            {
                "structure_id": "within_tolerance",
                "eg_um": str(5e-10),
                "fl_um": str(1.5 - 5e-10),
                "area_um2": str(700 + 5e-10),
                "has_pf": "1",
            },
            {
                "structure_id": "wrong_pf",
                "eg_um": "0",
                "fl_um": "1.5",
                "area_um2": "700",
                "has_pf": "0",
            },
            {
                "structure_id": "outside_area_tolerance",
                "eg_um": "0",
                "fl_um": "1.5",
                "area_um2": str(700 + 2e-9),
                "has_pf": "1",
            },
        ]
    )
    result = measured_overlaps(candidate, measured, bundle()["deduplication"])[0]
    assert result["measured_match_count"] == 1
    assert result["measured_match_structure_ids"] == "within_tolerance"


def write_shortlist(tmp_path, input_bundle=None):
    input_bundle = input_bundle or bundle()
    result = build_shortlist(input_bundle)
    result.to_csv(tmp_path, index=False)
    return input_bundle


def test_shortlist_validation_preserves_all_o11_values(tmp_path):
    path = tmp_path / "shortlist.csv"
    input_bundle = write_shortlist(path)
    result = validate_shortlist(path, input_bundle)
    assert result["failure_control_count"] == 17
    assert result["negative_control_excluded_count"] == 3
    assert result["nonnegative_measured_match_row_count"] == 0
    assert result["negative_measured_match_row_count"] == 1
    assert result["geometry_invalid_row_count"] == 0
    assert result["new_candidate_count"] == 0


def test_shortlist_validation_rejects_original_value_or_exclusion_change(tmp_path):
    path = tmp_path / "shortlist.csv"
    input_bundle = write_shortlist(path)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.loc[0, "k2eff_pct_prediction"] = "999"
    frame.to_csv(path, index=False)
    with pytest.raises(T13Error, match="O11_VALUES_OR_ORDER_CHANGED"):
        validate_shortlist(path, input_bundle)
    input_bundle = write_shortlist(path)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    frame.loc[19, "included"] = "True"
    frame.to_csv(path, index=False)
    with pytest.raises(T13Error, match="NEGATIVE_CONTROL_INCLUDED"):
        validate_shortlist(path, input_bundle)


def test_static_audit_rejects_model_and_argmax_calls(tmp_path):
    source = tmp_path / "forbidden.py"
    source.write_text("values.argmax()\n")
    with pytest.raises(T13Error, match="MODEL_OR_EXTREMUM_CALL_DETECTED"):
        audit_no_model_or_extremum_execution(source)
    source.write_text("model.predict(rows)\n")
    with pytest.raises(T13Error, match="MODEL_OR_EXTREMUM_CALL_DETECTED"):
        audit_no_model_or_extremum_execution(source)
    actual = audit_no_model_or_extremum_execution(
        Path(__file__).resolve().parents[2] / "scripts/run_optimization_p13_t13.py"
    )
    assert actual["fit_call_count"] == 0
    assert actual["deployment_prediction_call_count"] == 0
    assert actual["argmax_call_count"] == 0
