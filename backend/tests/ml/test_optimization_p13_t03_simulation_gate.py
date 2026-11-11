import pytest

from scripts import optimization_p13_t03_simulation_gate as gate


def requirements(value=False):
    return {key: value for key in gate.REQUIREMENT_KEYS}


def test_p6a_negative_pairs_keep_record_identity():
    rows = [
        {
            "cohort": "all_audited",
            "axis": "Train_L_EG_um",
            "metric": "k2eff_pct",
            "series_id": "series-a",
            "low_id": "TR-0001",
            "high_id": "TR-0002",
            "delta": "-0.01",
        },
        {
            "cohort": "all_audited",
            "axis": "Train_L_EG_um",
            "metric": "k2eff_pct",
            "series_id": "series-a",
            "low_id": "TR-0002",
            "high_id": "TR-0003",
            "delta": "0.02",
        },
        {
            "cohort": "consistent_only",
            "axis": "Train_L_EG_um",
            "metric": "k2eff_pct",
            "series_id": "series-b",
            "low_id": "TR-1001",
            "high_id": "TR-1002",
            "delta": "-0.03",
        },
    ]

    selected = gate.p6a_eg_k2_negative_pairs(rows)

    assert [(row["low_id"], row["high_id"]) for row in selected] == [("TR-0001", "TR-0002")]


def test_p6b_robust_counterexamples_require_v2_conditions():
    template = {
        "metric": "k2eff_pct",
        "series_id": "series-a",
        "low_id": "TR-0001",
        "high_id": "TR-0002",
        "delta": "-0.01",
        "refined_delta": "-0.005",
        "half_bin_envelope_contains_zero": "False",
    }
    rows = [
        template,
        {**template, "high_id": "TR-0003", "refined_delta": "0.001"},
        {**template, "high_id": "TR-0004", "half_bin_envelope_contains_zero": "True"},
        {**template, "high_id": "TR-0005", "delta": "0.01"},
        {**template, "high_id": "TR-0006", "metric": "fp_ghz"},
    ]

    selected = gate.p6b_robust_k2_counterexamples(rows)

    assert len(selected) == 1
    assert selected[0]["high_id"] == "TR-0002"


def test_cross_branch_pairs_require_zero_to_positive_fl():
    rows = [
        {
            "cohort": "all_audited",
            "axis": "Train_L_FL_um",
            "metric": "k2eff_pct",
            "low_axis": "0.0",
            "high_axis": "0.5",
        },
        {
            "cohort": "all_audited",
            "axis": "Train_L_FL_um",
            "metric": "k2eff_pct",
            "low_axis": "0.5",
            "high_axis": "1.0",
        },
    ]

    assert gate.fl_zero_to_positive_pairs(rows) == [rows[0]]


def test_decision_separates_scientific_rejection_from_software_pass():
    decision = gate.decision_for_requirements(requirements())
    document = {
        **decision,
        "software_validation": {"status": "PASS"},
    }

    gate.validate_decision(document)

    assert document["gate"] == "DISABLED"
    assert document["status"] == "REJECTED"
    assert document["software_validation"]["status"] == "PASS"
    assert document["pure_measured_m1_m2_route"]["blocked"] is False


def test_decision_does_not_relabel_scientific_rejection_as_failed():
    document = gate.decision_for_requirements(requirements())
    document["status"] = "FAILED"

    with pytest.raises(ValueError, match="DISABLED and scientific REJECTED"):
        gate.validate_decision(document)


def test_p6b_v1_path_is_rejected():
    rows = [
        {
            "file_id": "P6B_V2_EG_PAIR_DIAGNOSTICS",
            "absolute_path": "/tmp/optimization_p6b/diagnostics/eg_pair_diagnostics.csv",
        }
    ]

    with pytest.raises(ValueError, match="Superseded P6B diagnostics v1"):
        gate.validate_p6b_v2_paths(rows)
