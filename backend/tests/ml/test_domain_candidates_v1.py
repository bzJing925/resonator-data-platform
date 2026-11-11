import json

import numpy as np
import pytest

from scripts.run_device_median_models_v1 import ResearchScalarBundle
from scripts.run_domain_candidates_v1 import (
    NEAR_ZERO_ABS_ACTUAL,
    DResearchInferenceAdapter,
    _read_csv_round_trip,
    build_pair_comparison,
    build_support_catalog,
    canonical_actual,
    classify_point,
    error_metrics,
    generate_geometry,
    make_research_point,
    performance_exclusion_reasons,
    quantize_geometry,
    refresh_point_identity,
    select_diagnostic_candidates,
    validate_fold_lineage,
)


def native_record(structure_key, actual, prediction, **overrides):
    record = {
        "structure_key": structure_key,
        "p3_structure_id": f"p3_{structure_key}",
        "p3_design_group_id": f"group_{structure_key}",
        "p3_branch_id": "pf0_eg1_fl1",
        "unit": "dimensionless",
        "label_sha256": "label-hash",
        "training_id": "train_test",
        "model_bundle_sha256": "bundle-hash",
        "actual": float(actual),
        "prediction": float(prediction),
    }
    record.update(overrides)
    return record


def test_csv_actuals_use_ieee_round_trip_parsing(tmp_path):
    path = tmp_path / "labels.csv"
    path.write_text("median\n13.337994259236929\n", encoding="utf-8")

    frame = _read_csv_round_trip(path)

    assert float(frame["median"].iloc[0]) == float("13.337994259236929")


def test_near_zero_actuals_keep_native_errors_but_relative_metrics_are_na():
    rows = [
        native_record("zero", 0.0, 2.0),
        native_record("threshold", NEAR_ZERO_ABS_ACTUAL, 0.0),
        native_record("positive", 2.0, 3.0),
        native_record("negative", -2.0, -3.0),
    ]

    metrics = error_metrics(rows)
    _, paired_rows = build_pair_comparison(
        population="primary",
        scope="normal_holdout",
        target="qp",
        comparison_id="a_vs_b",
        method_a="a",
        method_b="b",
        rows_a=rows,
        rows_b=rows,
    )

    assert metrics["n"] == 4
    assert metrics["n_relative"] == 2
    assert metrics["MAE"] == pytest.approx(1.0)
    assert metrics["mean_absolute_relative_error"] == 0.5
    assert metrics["mean_relative_bias"] == 0.0
    by_structure = {row["structure_key"]: row for row in paired_rows}
    assert by_structure["zero"]["sample_status"] == "COMMON"
    assert by_structure["zero"]["relative_status_a"] == "NEAR_ZERO_ACTUAL"
    assert by_structure["zero"]["absolute_relative_error_a"] is None
    assert by_structure["zero"]["absolute_error_a"] == 2.0
    assert by_structure["threshold"]["relative_status_a"] == "NEAR_ZERO_ACTUAL"
    assert by_structure["positive"]["absolute_relative_error_a"] == 0.5
    assert by_structure["negative"]["signed_relative_error_a"] == -0.5


def test_empty_relative_subset_is_na_without_dropping_native_samples():
    metrics = error_metrics([native_record("zero", 0.0, 1.0)])

    assert metrics["n"] == 1
    assert metrics["n_relative"] == 0
    assert metrics["MAE"] == 1.0
    assert metrics["mean_absolute_relative_error"] is None
    assert metrics["mean_relative_bias"] is None


def test_existing_actual_serialization_accepts_one_ulp_and_rejects_two():
    actual = 1.0
    one_ulp = float(np.nextafter(actual, 2.0))
    two_ulps = float(np.nextafter(one_ulp, 2.0))

    assert canonical_actual(actual, actual) == (actual, 0)
    assert canonical_actual(one_ulp, actual) == (actual, 1)
    with pytest.raises(RuntimeError, match="exceeds 1 ULP"):
        canonical_actual(two_ulps, actual)


def test_pair_union_reports_native_losses_and_per_structure_differences():
    rows_a = [native_record("a", 2.0, 3.0), native_record("b", 4.0, 5.0)]
    rows_b = [
        native_record("b", 4.0, 4.5, training_id="train_b"),
        native_record("c", 8.0, 7.0, training_id="train_b"),
    ]

    comparison, paired = build_pair_comparison(
        population="primary",
        scope="normal_holdout",
        target="qp",
        comparison_id="a_vs_b",
        method_a="a",
        method_b="b",
        rows_a=rows_a,
        rows_b=rows_b,
    )

    assert comparison["n_native_a"] == 2
    assert comparison["n_native_b"] == 2
    assert comparison["n_common"] == 1
    assert comparison["n_lost_a"] == 1
    assert comparison["n_lost_b"] == 1
    assert json.loads(comparison["lost_structure_ids_a_json"]) == ["a"]
    assert json.loads(comparison["lost_structure_ids_b_json"]) == ["c"]
    assert comparison["better_a_count"] == 0
    assert comparison["better_b_count"] == 1
    assert comparison["better_b_fraction"] == 1.0
    assert [row["sample_status"] for row in paired] == ["ONLY_A", "COMMON", "ONLY_B"]
    assert paired[0]["better_method"] == "N/A"
    assert paired[0]["prediction_b"] is None
    common = paired[1]
    assert common["actual_a"] == common["actual_b"] == 4.0
    assert common["absolute_error_difference_a_minus_b"] == 0.5
    assert common["better_method"] == "b"
    assert paired[2]["absolute_error_b"] == 1.0


def test_fold_lineage_rejects_outer_test_membership_and_training_id_mismatch():
    fit = {
        "training_id": "train_expected",
        "training_structure_ids": ["train_1", "train_2"],
        "training_design_group_ids": ["group_1"],
        "outer_test_used": False,
    }

    validate_fold_lineage(
        fit=fit,
        expected_training_id="train_expected",
        eligible_training_structure_ids=["train_1", "train_2"],
        eligible_training_design_group_ids=["group_1"],
        outer_test_structure_ids=["test_1"],
        outer_test_design_group_ids=["group_2"],
    )
    with pytest.raises(RuntimeError, match="structure overlap"):
        validate_fold_lineage(
            fit=fit,
            expected_training_id="train_expected",
            eligible_training_structure_ids=["train_1", "train_2"],
            eligible_training_design_group_ids=["group_1"],
            outer_test_structure_ids=["train_2"],
            outer_test_design_group_ids=["group_2"],
        )
    with pytest.raises(RuntimeError, match="training_id mismatch"):
        validate_fold_lineage(
            fit=fit,
            expected_training_id="train_other",
            eligible_training_structure_ids=["train_1", "train_2"],
            eligible_training_design_group_ids=["group_1"],
            outer_test_structure_ids=["test_1"],
            outer_test_design_group_ids=["group_2"],
        )


def d05_material(eg, fl=1.0, area=100.0, *, swap_ports=False):
    areas = ("5500", "700") if swap_ports else ("700", "5500")

    def normalize(value):
        return str(value).removesuffix(".0")

    return {
        "schema": "b01_structure_association_v1",
        "geometry": {
            "eg_um": normalize(eg),
            "fl_um": normalize(fl),
            "area_um2": normalize(area),
            "has_pf": False,
        },
        "ag": {"status": "unrecorded", "normalized_ag_um": None},
        "topology": {
            "status": "standard_mapping",
            "value": "EG_FL_PORT_AREA_PF",
            "unknown_topology_merge_allowed": False,
        },
        "cohort": "standard_eg_fl_unrecorded_ag",
        "mapping_semantics": {
            "normalized_area_s11": areas[0],
            "normalized_area_s22": areas[1],
            "has_pf": False,
            "port_area_rule": "first_area=S11;second_area=S22",
        },
    }


def d05_point(eg, fl=1.0, area=100.0, *, port="S11", **kwargs):
    point = make_research_point(
        d05_material(eg, fl, area, **kwargs),
        p3_branch_id="pf0_eg1_fl1",
        business_scope="synthetic-test",
    )
    point["port"] = port
    return point


def d05_protocol():
    return {
        "geometry_constraints": {
            "allowed_research_branches": ["pf0_eg1_fl1"],
        },
        "extrapolation_rule": {
            "increments_delta": [0.5, 1.0],
            "minimum_distinct_measured_levels_on_slice": 3,
        },
        "geometry_budget_and_order": {"maximum_points": 50000},
    }


def d05_catalog(*, physical_limit=1.0):
    measured = [
        d05_point(eg, fl, area)
        for eg in (1.0, 2.0, 3.0)
        for fl in (1.0, 2.0)
        for area in (100.0, 200.0, 300.0)
    ]
    domain = {
        "domain_id": "domain_synthetic_eg_upper",
        "population": "primary",
        "scope": "eg_upper",
        "axis": "eg_um",
        "direction": "UPPER_EXTRAPOLATION",
        "p3_branch_id": "pf0_eg1_fl1",
        "fixed_other_geometry": {"fl_um": 1.0, "area_um2": 100.0, "has_pf": False},
        "held_out_level": 3.0,
        "physical_extrapolation_distance": physical_limit,
        "nearest_two_training_level_spacing": 1.0,
        "distance_support_status": "SUPPORTED",
        "policy_status": "DIAGNOSTIC_ONLY",
        "search_policy": None,
        "historical_error_margin_k2_pp": 0.2,
        "historical_error_margin_qp": 10.0,
    }
    interpolation = {
        **domain,
        "domain_id": "domain_synthetic_interpolation",
        "scope": "normal_holdout",
        "direction": "INTERPOLATION",
        "held_out_level": 2.0,
        "physical_extrapolation_distance": 0.0,
        "interpolation_bracket": [1.0, 3.0],
        "historical_error_margin_k2_pp": 0.25,
        "historical_error_margin_qp": 12.0,
    }
    return measured, build_support_catalog(measured, [domain, interpolation])


def test_d05_identity_boundaries_distance_quantization_and_old_rejection():
    measured, catalog = d05_catalog()
    protocol = d05_protocol()

    control = classify_point(measured[0], catalog, protocol)
    assert control["category"] == "MEASURED_CONTROL"
    assert not control["diagnostic_candidate"]

    other_port = d05_point(1.0, swap_ports=True)
    other_port = refresh_point_identity(other_port)
    assert other_port["structure_key"] != measured[0]["structure_key"]
    assert classify_point(other_port, catalog, protocol)["category"] == (
        "UNMEASURED_SPARSE_SUPPORT"
    )
    sparse = d05_point(2.0, swap_ports=True)
    sparse["generation_axis"] = "eg_um"
    sparse["generation_bracket"] = [1.0, 3.0]
    sparse_without_margin = classify_point(sparse, catalog, protocol)
    assert sparse_without_margin["category"] == "UNMEASURED_SPARSE_SUPPORT"
    assert sparse_without_margin["domain_id"] is None
    assert sparse_without_margin["interpolation_bracket"] is None
    assert sparse_without_margin["historical_error_margin_k2_pp"] is None

    single_axis = d05_point(4.0)
    extrapolated = classify_point(single_axis, catalog, protocol)
    assert extrapolated["category"] == "UNMEASURED_EXTRAPOLATION"
    assert extrapolated["axis"] == "eg_um"
    assert extrapolated["physical_distance"] == 1.0
    assert extrapolated["d03_diagnostic_authorized"]

    multiaxis = classify_point(d05_point(4.0, 3.0), catalog, protocol)
    assert multiaxis["category"] == "UNSUPPORTED"
    assert "MULTIAXIS_OUT_OF_BOUNDS" in multiaxis["rejection_reasons"]

    negative = classify_point(d05_point(-1.0), catalog, protocol)
    assert negative["category"] == "UNSUPPORTED"
    assert "ILLEGAL_OR_NONFINITE_GEOMETRY" in negative["rejection_reasons"]
    zero_crossing = classify_point(d05_point(0.0), catalog, protocol)
    assert "BRANCH_GEOMETRY_MISMATCH" in zero_crossing["rejection_reasons"]

    unknown = d05_point(1.0)
    unknown["p3_branch_id"] = "pf9_eg1_fl1"
    unknown = refresh_point_identity(unknown)
    assert "UNKNOWN_BRANCH" in classify_point(unknown, catalog, protocol)["rejection_reasons"]

    _, limited_catalog = d05_catalog(physical_limit=0.5)
    excessive = classify_point(d05_point(4.0), limited_catalog, protocol)
    assert excessive["category"] == "UNSUPPORTED"
    assert "EXCESSIVE_DISTANCE" in excessive["rejection_reasons"]

    midpoint = d05_point(1.5)
    assert classify_point(midpoint, catalog, protocol)["category"] == (
        "UNMEASURED_INTERPOLATION"
    )
    quantized = quantize_geometry(midpoint, {"eg_um": "1"})
    assert quantized["material"]["geometry"]["eg_um"] == "2"
    assert classify_point(quantized, catalog, protocol)["category"] == "MEASURED_CONTROL"

    class FakeModel:
        model_name = "p6c_trend_gp_log_area_scalar"

        def __init__(self):
            self.calls = 0

        def predict(self, frame):
            self.calls += 1
            return np.full(len(frame), 7.0)

    fake = FakeModel()
    training = {
        "k2eff_pct": {
            "branches": {
                "pf0_eg1_fl1": {
                    "eg_um": {"min": 1.0, "max": 3.0},
                    "fl_um": {"min": 1.0, "max": 2.0},
                    "area_um2": {"min": 100.0, "max": 300.0},
                }
            }
        }
    }
    old = ResearchScalarBundle({"k2eff_pct": fake}, training, {"k2eff_pct": "pp"})
    frame = __import__("pandas").DataFrame(
        [
            {
                "structure_key": single_axis["structure_key"],
                "p3_branch_id": "pf0_eg1_fl1",
                "eg_um": 4.0,
                "fl_um": 1.0,
                "area_um2": 100.0,
                "has_pf": False,
            }
        ]
    )
    assert old.predict_records("k2eff_pct", frame)[0]["reason"] == (
        "outside_training_range"
    )
    assert fake.calls == 0
    single_axis["point_id"] = "single-axis"
    single_axis["support"] = extrapolated
    adapter = DResearchInferenceAdapter(old)
    assert adapter.predict_points([single_axis])[("single-axis", "k2eff_pct")] == 7.0
    assert fake.calls == 1


def test_d05_geometry_order_and_budget_are_frozen_before_inference():
    measured, catalog = d05_catalog()
    points, report = generate_geometry(measured, catalog, d05_protocol())
    keys = [point["full_structure_key_json"] for point in points]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys)
    assert report["status"] == "READY_FOR_DIAGNOSTIC_INFERENCE"
    assert report["counts"]["MEASURED_CONTROL"] == len(measured)

    _, stopped = generate_geometry(
        measured,
        catalog,
        {
            **d05_protocol(),
            "geometry_budget_and_order": {"maximum_points": 1},
        },
    )
    assert stopped["status"] == "STOPPED_GEOMETRY_BUDGET_EXCEEDED"
    assert stopped["generated_post_deduplication"] > 1
    assert stopped["prediction_allowed"] is False


def d05_interpolation_domain(lower, upper):
    return {
        "domain_id": "domain_identity_bracket",
        "population": "primary",
        "scope": "normal_holdout",
        "axis": "area_um2",
        "direction": "INTERPOLATION",
        "p3_branch_id": "pf0_eg1_fl1",
        "fixed_other_geometry": {"eg_um": 1.0, "fl_um": 1.0, "has_pf": False},
        "held_out_level": (lower + upper) / 2,
        "physical_extrapolation_distance": 0.0,
        "nearest_two_training_level_spacing": upper - lower,
        "distance_support_status": "SUPPORTED",
        "policy_status": "DIAGNOSTIC_ONLY",
        "search_policy": None,
        "historical_error_margin_k2_pp": 0.25,
        "historical_error_margin_qp": 12.0,
        "interpolation_bracket": [lower, upper],
    }


def d05_midpoint(lower, upper, value, *, upper_port="S11", upper_swap_ports=False):
    lower_point = d05_point(1.0, area=lower)
    upper_point = d05_point(
        1.0,
        area=upper,
        port=upper_port,
        swap_ports=upper_swap_ports,
    )
    midpoint = d05_point(1.0, area=value)
    midpoint["generation_axis"] = "area_um2"
    midpoint["generation_bracket"] = [lower, upper]
    midpoint["source_structure_keys"] = sorted(
        [lower_point["structure_key"], upper_point["structure_key"]]
    )
    return lower_point, upper_point, midpoint


def test_d05_interpolation_and_sparse_provenance_require_b01_identity():
    protocol = d05_protocol()
    lower, upper, midpoint = d05_midpoint(100.0, 300.0, 200.0)
    catalog = build_support_catalog(
        [lower, upper], [d05_interpolation_domain(100.0, 300.0)]
    )
    same_port = classify_point(midpoint, catalog, protocol)
    assert same_port["category"] == "UNMEASURED_INTERPOLATION"
    assert same_port["interpolation_bracket"] == [100.0, 300.0]
    assert same_port["historical_error_margin_qp"] == 12.0

    for mixed_upper in (
        d05_midpoint(100.0, 300.0, 200.0, upper_port="S22"),
        d05_midpoint(100.0, 300.0, 200.0, upper_swap_ports=True),
    ):
        lower, upper, midpoint = mixed_upper
        catalog = build_support_catalog(
            [lower, upper], [d05_interpolation_domain(100.0, 300.0)]
        )
        support = classify_point(midpoint, catalog, protocol)
        assert support["category"] == "UNMEASURED_SPARSE_SUPPORT"
        assert support["axis"] is None
        assert support["interpolation_bracket"] is None
        assert support["domain_id"] is None
        assert support["historical_error_margin_qp"] is None

    lower, upper, midpoint = d05_midpoint(100.0, 400.0, 250.0)
    same_identity_neighbor = d05_point(1.0, area=200.0)
    catalog = build_support_catalog(
        [lower, same_identity_neighbor, upper],
        [d05_interpolation_domain(100.0, 400.0)],
    )
    same_identity_sparse = classify_point(midpoint, catalog, protocol)
    assert same_identity_sparse["category"] == "UNMEASURED_SPARSE_SUPPORT"
    assert same_identity_sparse["interpolation_bracket"] == [100.0, 400.0]
    assert same_identity_sparse["historical_error_margin_qp"] == 12.0

    for mixed_upper in (
        d05_midpoint(100.0, 400.0, 250.0, upper_port="S22"),
        d05_midpoint(100.0, 400.0, 250.0, upper_swap_ports=True),
    ):
        lower, upper, midpoint = mixed_upper
        same_identity_neighbor = d05_point(1.0, area=200.0)
        catalog = build_support_catalog(
            [lower, same_identity_neighbor, upper],
            [d05_interpolation_domain(100.0, 400.0)],
        )
        support = classify_point(midpoint, catalog, protocol)
        assert support["category"] == "UNMEASURED_SPARSE_SUPPORT"
        assert support["interpolation_bracket"] is None
        assert support["domain_id"] is None
        assert support["historical_error_margin_qp"] is None


def test_d05_extrapolation_slice_requires_b01_identity():
    protocol = d05_protocol()
    domain = {
        "domain_id": "domain_identity_extrapolation",
        "population": "primary",
        "scope": "eg_upper",
        "axis": "eg_um",
        "direction": "UPPER_EXTRAPOLATION",
        "p3_branch_id": "pf0_eg1_fl1",
        "fixed_other_geometry": {"fl_um": 1.0, "area_um2": 100.0, "has_pf": False},
        "held_out_level": 3.0,
        "physical_extrapolation_distance": 1.0,
        "nearest_two_training_level_spacing": 1.0,
        "distance_support_status": "SUPPORTED",
        "policy_status": "DIAGNOSTIC_ONLY",
        "search_policy": None,
        "historical_error_margin_k2_pp": 0.2,
        "historical_error_margin_qp": 10.0,
    }
    for port, swap_ports in (("S11", False), ("S22", False), ("S11", True)):
        measured = [
            d05_point(1.0),
            d05_point(2.0),
            d05_point(3.0, port=port, swap_ports=swap_ports),
        ]
        support = classify_point(
            d05_point(4.0), build_support_catalog(measured, [domain]), protocol
        )
        if port == "S11" and not swap_ports:
            assert support["category"] == "UNMEASURED_EXTRAPOLATION"
        else:
            assert support["category"] == "UNSUPPORTED"
            assert support["rejection_reasons"] == ["INSUFFICIENT_MEASURED_LEVELS"]


def d06_inputs(**overrides):
    support = {
        "category": "UNMEASURED_EXTRAPOLATION",
        "d03_policy_status": "ALLOWED_SEARCH",
        "rejection_reasons_json": "[]",
        "exclusion_reasons_json": "[]",
    }
    predictions = {
        "k2eff_pct": {
            "prediction": 16.0,
            "historical_error_margin": 0.2,
            "k2_improvement_pp": 1.2,
            "stress_gain_k2": 1.0,
        },
        "qp": {
            "prediction": 1220.0,
            "historical_error_margin": 10.0,
            "qp_ratio": 1.01,
            "stress_qp_ratio": 1.0,
        },
    }
    goals = {
        "k2_improvement_min_percentage_points": 1.0,
        "stress_gain_k2_min_percentage_points": 1.0,
        "qp_ratio_min": 1.0,
        "stress_qp_ratio_min": 1.0,
    }
    for key, value in overrides.items():
        if key in support:
            support[key] = value
        else:
            target, field = key.split("__")
            predictions[target][field] = value
    return support, predictions, goals


def test_d06_performance_gates_require_domain_margins_and_unmeasured_identity():
    assert performance_exclusion_reasons(*d06_inputs()) == []
    assert "D03_RANGE_NOT_ALLOWED_FOR_SEARCH" in performance_exclusion_reasons(
        *d06_inputs(d03_policy_status="DIAGNOSTIC_ONLY")
    )
    assert "MEASURED_OR_UNSUPPORTED" in performance_exclusion_reasons(
        *d06_inputs(category="MEASURED_CONTROL")
    )
    reasons = performance_exclusion_reasons(
        *d06_inputs(
            k2eff_pct__historical_error_margin=None,
            k2eff_pct__stress_gain_k2=None,
            qp__historical_error_margin=None,
            qp__stress_qp_ratio=None,
        )
    )
    assert "MISSING_K2_MARGIN" in reasons
    assert "MISSING_QP_MARGIN" in reasons
    assert "STRESS_K2_BELOW_MINIMUM" in reasons
    assert "STRESS_QP_RATIO_BELOW_MINIMUM" in reasons


def test_d06_diagnostics_are_separate_ordered_and_cover_axes():
    def row(point_id, axis, improvement, delta=0.5, category="UNMEASURED_EXTRAPOLATION"):
        return {
            "point_id": point_id,
            "axis": axis,
            "category": category,
            "relative_distance_delta": delta,
            "k2_prediction": 15.0,
            "qp_prediction": 1200.0,
            "k2_improvement_pp": improvement,
            "full_structure_key_json": point_id,
            "diagnostic_selected": False,
            "diagnostic_rank": None,
            "diagnostic_selection_basis": None,
        }

    selected = select_diagnostic_candidates([
        row("eg-low", "eg_um", 0.1),
        row("fl-best", "fl_um", 0.2),
        row("eg-best", "eg_um", 0.3),
        row("interior", "eg_um", 9.0, category="UNMEASURED_INTERPOLATION"),
    ])
    assert [row["point_id"] for row in selected] == ["eg-best", "fl-best"]
    assert [row["diagnostic_rank"] for row in selected] == [1, 2]
    assert all(row["diagnostic_selected"] for row in selected)
    assert all(
        "MODEL_DISAGREEMENT_UNAVAILABLE" in row["diagnostic_selection_basis"]
        for row in selected
    )
