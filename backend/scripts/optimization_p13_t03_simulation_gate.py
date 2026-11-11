import argparse
import csv
import hashlib
import json
import os
import platform
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

BE = Path(__file__).resolve().parents[1]
SCRIPT_PATH = Path(__file__).resolve()
TEST_PATH = BE / "tests/ml/test_optimization_p13_t03_simulation_gate.py"
REQUIREMENT_KEYS = (
    "geometry_and_stack_correspondence_verified",
    "topology_and_manufacturing_domain_verified",
    "peak_and_mode_continuity_verified",
    "cross_branch_continuity_verified",
    "conditional_trend_validated_against_counterexamples",
    "simulation_to_measurement_transfer_verified",
)
CONTROL_FIELDS = (
    "Stage",
    "Template",
    "Variant",
    "EG_State",
    "Train_A_res_um2",
    "Train_L_FL_um",
    "Train_h_FL_nm",
    "Train_L_AG_um",
    "Train_h_AG_nm",
    "Train_h_EG_nm",
    "Train_PF_nm",
    "has_FL",
    "has_AG",
    "has_PF",
    "has_patterned_EG",
)
METADATA_FIELDS = (
    "Train_L_top_um",
    "Train_L_bot_um",
    "Train_L_air_um",
    "Train_h_FL_nm",
    "Train_L_FL_um",
    "Train_h_AG_nm",
    "Train_L_AG_um",
    "Train_h_EG_nm",
    "Train_L_EG_um",
    "Train_PF_nm",
    "MPH_Relative_Path",
    "Source_Model_File",
)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_csv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def number(value):
    return float(value) if value not in ("", None) else None


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_new(path, content):
    path = Path(path)
    with path.open("xb") as handle:
        handle.write(content)


def controls(row):
    values = json.loads(row["controls"])
    return {key: values.get(key) for key in CONTROL_FIELDS}


def p6a_eg_k2_negative_pairs(rows):
    return [
        row
        for row in rows
        if row["cohort"] == "all_audited"
        and row["axis"] == "Train_L_EG_um"
        and row["metric"] == "k2eff_pct"
        and number(row["delta"]) < 0
    ]


def p6b_robust_k2_counterexamples(rows):
    return [
        row
        for row in rows
        if row["metric"] == "k2eff_pct"
        and number(row["delta"]) < 0
        and number(row["refined_delta"]) < 0
        and row["half_bin_envelope_contains_zero"] == "False"
    ]


def fl_zero_to_positive_pairs(rows):
    return [
        row
        for row in rows
        if row["cohort"] == "all_audited"
        and row["axis"] == "Train_L_FL_um"
        and row["metric"] == "k2eff_pct"
        and number(row["low_axis"]) == 0
        and number(row["high_axis"]) > 0
    ]


def peak_continuity_mismatches(rows):
    return [
        row
        for row in rows
        if row["nearest_peak_continuity_mismatches"] not in ("", "0", "0.0")
    ]


def decision_for_requirements(requirements):
    if set(requirements) != set(REQUIREMENT_KEYS):
        raise ValueError("Requirement keys do not match the frozen T03 decision contract")
    eligible = all(requirements[key] is True for key in REQUIREMENT_KEYS)
    return {
        "gate": "ENABLED" if eligible else "DISABLED",
        "status": "PASS" if eligible else "REJECTED",
        "conditional_soft_constraint_eligible": eligible,
        "allowed_soft_constraints": ["simulation_derived_conditional_soft_constraint"]
        if eligible
        else [],
        "pure_measured_m1_m2_route": {
            "blocked": False,
            "reason": (
                "T03 rejects only simulation-derived soft constraints, not measured-only models"
            ),
        },
        "simulation_values_as_measured_labels": False,
        "simulation_values_as_pseudo_labels": False,
    }


def validate_decision(document):
    if document["conditional_soft_constraint_eligible"] is False:
        if document["gate"] != "DISABLED" or document["status"] != "REJECTED":
            raise ValueError("Ineligible soft constraints require DISABLED and scientific REJECTED")
        if document["allowed_soft_constraints"] != []:
            raise ValueError("Ineligible soft constraints must have an empty allow-list")
    if document["status"] == "FAILED" and document["gate"] == "DISABLED":
        raise ValueError("Scientific rejection must not be relabeled as software FAILED")
    if document["pure_measured_m1_m2_route"]["blocked"] is not False:
        raise ValueError("T03 must not block the measured-only M1/M2 route")
    if document["simulation_values_as_measured_labels"] is not False:
        raise ValueError("Simulation values cannot become measured labels")
    if document["simulation_values_as_pseudo_labels"] is not False:
        raise ValueError("Simulation values cannot become pseudo-labels")


def validate_p6b_v2_paths(inputs):
    for row in inputs:
        path = row["absolute_path"]
        if "/diagnostics/" in path:
            raise ValueError("Superseded P6B diagnostics v1 is not an allowed T03 input")
        if row["file_id"].startswith("P6B_V2_") and "/diagnostics_v2/" not in path:
            raise ValueError("P6B v2 inputs must resolve to diagnostics_v2")


def load_inputs(run):
    o01_path = run / "audit/input_manifest.json"
    conflicts_path = run / "audit/version_conflicts.csv"
    o01_artifact_path = run / "audit/artifact_manifest.json"
    o01 = read_json(o01_path)
    o01_artifact = read_json(o01_artifact_path)
    output_hashes = {row["path"]: row["sha256"] for row in o01_artifact["outputs"]}
    registry = {row["file_id"]: dict(row) for row in o01["inputs"]}
    file_ids = list(o01["downstream_handoff"]["T03"]["file_ids"])
    file_ids.extend(["O00_PREFLIGHT", "O00_ARTIFACT_MANIFEST"])
    inputs = [registry[file_id] for file_id in file_ids]
    for file_id, path, relative in (
        ("O01_INPUT_MANIFEST", o01_path, "audit/input_manifest.json"),
        ("O01_VERSION_CONFLICTS", conflicts_path, "audit/version_conflicts.csv"),
        ("O01_ARTIFACT_MANIFEST", o01_artifact_path, None),
    ):
        inputs.append(
            {
                "file_id": file_id,
                "absolute_path": str(path),
                "sha256": output_hashes.get(relative) or sha256(path),
                "verification": "actual_file_read_sha256",
            }
        )
    if len(inputs) != 33 or len({row["file_id"] for row in inputs}) != 33:
        raise ValueError("Unexpected T03 input count or duplicate file_id")
    validate_p6b_v2_paths(inputs)
    return o01, inputs


def verify_input_hashes(inputs):
    receipts = []
    mismatches = []
    for row in inputs:
        path = Path(row["absolute_path"])
        actual = sha256(path)
        match = actual == row["sha256"]
        if not match:
            mismatches.append(row["file_id"])
        receipts.append(
            {
                "file_id": row["file_id"],
                "absolute_path": str(path),
                "bytes": path.stat().st_size,
                "sha256": actual,
                "expected_sha256": row["sha256"],
                "matches": match,
            }
        )
    if mismatches:
        raise ValueError(f"Frozen T03 input hash mismatch: {mismatches}")
    return receipts


def compact_pair(row, refined=False):
    result = {
        "series_id": row["series_id"],
        "low_id": row["low_id"],
        "high_id": row["high_id"],
        "low_eg_um": row.get("low_eg", row.get("low_axis")),
        "high_eg_um": row.get("high_eg", row.get("high_axis")),
        "delta_k2_percentage_points": row["delta"],
    }
    if refined:
        result.update(
            {
                "refined_delta_k2_percentage_points": row["refined_delta"],
                "half_bin_delta_low": row["half_bin_delta_low"],
                "half_bin_delta_high": row["half_bin_delta_high"],
                "half_bin_envelope_contains_zero": row["half_bin_envelope_contains_zero"],
            }
        )
    return result


def collect_evidence(registry):
    paths = {row["file_id"]: row["absolute_path"] for row in registry}
    p6a_series = read_csv(paths["P6A_SIM_TREND_SERIES"])
    p6a_pairs = read_csv(paths["P6A_SIM_TREND_PAIRS"])
    p6b_series = read_csv(paths["P6B_V2_EG_SERIES_DIAGNOSTICS"])
    p6b_pairs = read_csv(paths["P6B_V2_EG_PAIR_DIAGNOSTICS"])
    probes = read_csv(paths["P6B_V2_SIMULATION_PEAK_PROBES"])
    extraction = read_csv(paths["P6A_SIM_SIMULATION_EXTRACTION"])
    quarantine = read_csv(paths["P6A_SIM_QUARANTINE"])
    p6a_summary = read_json(paths["P6A_SIM_SUMMARY"])
    p6a_frozen = read_json(paths["P6A_SIM_FROZEN_INPUTS"])
    p6b_summary = read_json(paths["P6B_V2_SUMMARY"])
    correction = read_json(paths["P6B_DIAGNOSTIC_V1_CORRECTION"])
    candidates = read_csv(paths["P12_CANDIDATES"])
    peak_searches = read_csv(paths["P12_PEAK_SEARCHES"])

    eg_k2_series = [
        row
        for row in p6a_series
        if row["cohort"] == "all_audited"
        and row["axis"] == "Train_L_EG_um"
        and row["metric"] == "k2eff_pct"
    ]
    nonmonotonic = [row for row in eg_k2_series if row["direction"] == "nonmonotonic"]
    negative_pairs = p6a_eg_k2_negative_pairs(p6a_pairs)
    v2_negative = [
        row
        for row in p6b_pairs
        if row["metric"] == "k2eff_pct" and number(row["delta"]) < 0
    ]
    robust = p6b_robust_k2_counterexamples(p6b_pairs)
    fp_negative = [
        row
        for row in p6b_pairs
        if row["metric"] == "fp_ghz" and number(row["delta"]) < 0
    ]
    mismatches = peak_continuity_mismatches(p6b_series)
    switch_row_ids = sorted(
        {
            row_id
            for row in mismatches
            for row_id in row["row_ids"].split(";")
        }
    )
    cross_branch = fl_zero_to_positive_pairs(p6a_pairs)
    eligible = [row for row in extraction if row["trend_eligible"] == "True"]
    fl_k2_series = [
        row
        for row in p6a_series
        if row["cohort"] == "all_audited"
        and row["axis"] == "Train_L_FL_um"
        and row["metric"] == "k2eff_pct"
    ]
    direction_changes = Counter(
        (row["metric"], row["original_direction"], row["refined_direction"])
        for row in p6b_series
        if row["original_direction"] != row["refined_direction"]
    )
    p1203 = [
        row
        for row in candidates
        if row["candidate_id"].startswith("P1203_")
    ]
    if len(p1203) != 1:
        raise ValueError("P1203 record is missing or ambiguous")

    return {
        "p6a_eg_k2": {
            "series_count": len(eg_k2_series),
            "nonmonotonic_series_count": len(nonmonotonic),
            "negative_adjacent_pair_count": len(negative_pairs),
            "nonmonotonic_series": [
                {
                    "series_id": row["series_id"],
                    "row_ids": row["row_ids"].split(";"),
                    "controls": controls(row),
                    "transfer_status": row["transfer_status"],
                }
                for row in nonmonotonic
            ],
            "negative_adjacent_pairs": [compact_pair(row) for row in negative_pairs],
            "source_file_ids": ["P6A_SIM_TREND_SERIES", "P6A_SIM_TREND_PAIRS"],
        },
        "p6b_v2_eg_k2": {
            "raw_negative_pair_count": len(v2_negative),
            "refined_negative_pair_count": sum(
                number(row["refined_delta"]) < 0 for row in v2_negative
            ),
            "robust_negative_pair_count": len(robust),
            "robust_negative_series_count": len({row["series_id"] for row in robust}),
            "robust_counterexamples": [compact_pair(row, refined=True) for row in robust],
            "fp_counterexamples": [
                {
                    "series_id": row["series_id"],
                    "low_id": row["low_id"],
                    "high_id": row["high_id"],
                    "low_eg_um": row["low_eg"],
                    "high_eg_um": row["high_eg"],
                    "delta_fp_ghz": row["delta"],
                    "refined_delta_fp_ghz": row["refined_delta"],
                    "half_bin_envelope_contains_zero": row[
                        "half_bin_envelope_contains_zero"
                    ],
                }
                for row in fp_negative
            ],
            "direction_change_counts": [
                {
                    "metric": key[0],
                    "original_direction": key[1],
                    "refined_direction": key[2],
                    "count": value,
                }
                for key, value in sorted(direction_changes.items())
            ],
            "source_file_ids": [
                "P6B_V2_EG_SERIES_DIAGNOSTICS",
                "P6B_V2_EG_PAIR_DIAGNOSTICS",
            ],
        },
        "peak_switching": {
            "series_records": [
                {
                    "series_id": row["series_id"],
                    "metric": row["metric"],
                    "row_ids": row["row_ids"].split(";"),
                    "controls": controls(row),
                    "original_direction": row["original_direction"],
                    "refined_direction": row["refined_direction"],
                    "nearest_peak_continuity_mismatches": row[
                        "nearest_peak_continuity_mismatches"
                    ].split(";"),
                    "physical_mode_identity_verified": row[
                        "physical_mode_identity_verified"
                    ]
                    == "True",
                }
                for row in mismatches
            ],
            "probe_records": [
                {
                    "Training_Row_ID": row["Training_Row_ID"],
                    "source_sha256": row["source_sha256"],
                    "fp_hz": row["fp_hz"],
                    "fp_refined_hz": row["fp_refined_hz"],
                    "fp_competitor_gap_db": row["fp_competitor_gap_db"],
                    "fp_candidates_hz": json.loads(row["fp_candidates_hz"]),
                    "fp_step_hz": row["fp_step_hz"],
                }
                for row in probes
                if row["Training_Row_ID"] in switch_row_ids
            ],
            "physical_mode_identity_verified_values": dict(
                Counter(row["physical_mode_identity_verified"] for row in p6b_series)
            ),
            "source_file_ids": [
                "P6B_V2_EG_SERIES_DIAGNOSTICS",
                "P6B_V2_SIMULATION_PEAK_PROBES",
            ],
        },
        "cross_branch_continuity": {
            "controlled_fl_zero_to_positive_pair_count": len(cross_branch),
            "controlled_fl_zero_to_positive_pairs": cross_branch,
            "fl_k2_series_axis_min_values_um": sorted(
                {number(row["axis_min"]) for row in fl_k2_series}
            ),
            "fl_k2_series_has_fl_values": sorted(
                {str(controls(row)["has_FL"]) for row in fl_k2_series}
            ),
            "p12_branch_boundary_candidate": p1203[0],
            "p12_k2_p6c_peak_records": [
                row
                for row in peak_searches
                if row["target"] == "k2eff_pct" and row["model"] == "p6c"
            ],
            "interpretation": (
                "P12 documents separate FL=0 and FL>0 model branches and a model discontinuity; "
                "this is not physical continuity evidence. No controlled P6A FL=0 to FL>0 pair "
                "or P6B FL cross-branch diagnostic was registered for T03."
            ),
            "source_file_ids": [
                "P6A_SIM_TREND_SERIES",
                "P6A_SIM_TREND_PAIRS",
                "P6B_V2_SUMMARY",
                "P12_PROTOCOL",
                "P12_CANDIDATES",
                "P12_PEAK_SEARCHES",
            ],
        },
        "geometry_and_transfer_metadata": {
            "extraction_rows": len(extraction),
            "trend_eligible_rows": len(eligible),
            "quarantined_rows": len(quarantine),
            "quarantined_record_ids": [row["Training_Row_ID"] for row in quarantine],
            "quarantine_reasons": dict(Counter(row["exclusion_reason"] for row in quarantine)),
            "cross_source_status_all": dict(
                Counter(row["Cross_Source_Status"] for row in extraction)
            ),
            "cross_source_status_eligible": dict(
                Counter(row["Cross_Source_Status"] for row in eligible)
            ),
            "metadata_completeness_within_simulation": {
                field: {
                    "missing_eligible": sum(row[field] == "" for row in eligible),
                    "unique_eligible": len({row[field] for row in eligible}),
                }
                for field in METADATA_FIELDS
            },
            "transfer_status_counts": dict(
                Counter(row["transfer_status"] for row in p6a_series)
            ),
            "p6a_summary": p6a_summary,
            "p6a_frozen_flags": {
                "no_electrical_loss": p6a_frozen["no_electrical_loss"],
                "measured_labels_read": p6a_frozen["measured_labels_read"],
                "raw_de_mix": p6a_frozen["raw_de_mix"],
                "mode_tracking_verified": p6a_frozen["mode_tracking_verified"],
                "intended_use": p6a_frozen["intended_use"],
            },
            "p6b_summary": p6b_summary,
            "source_file_ids": [
                "P6A_SIM_FROZEN_INPUTS",
                "P6A_SIM_SUMMARY",
                "P6A_SIM_SIMULATION_EXTRACTION",
                "P6A_SIM_QUARANTINE",
                "P6A_SIM_TREND_SERIES",
                "P6B_V2_SUMMARY",
            ],
        },
        "vc06": {
            "correction": correction,
            "replacement_directory_used": "diagnostics_v2",
            "superseded_v1_used": False,
            "source_file_ids": [
                "P6B_DIAGNOSTIC_V1_CORRECTION",
                "P6B_DIAGNOSTICS_V2_OUTPUTS_MANIFEST",
            ],
        },
    }


def scientific_review():
    return [
        {
            "condition": "geometry_and_stack_correspondence_verified",
            "verified": False,
            "status": "REJECTED",
            "conclusion": (
                "Simulation geometry fields exist, but measured-device AG/stack correspondence and "
                "MPH settings were not independently verified. Recorded values cannot be promoted "
                "to measured geometry assumptions."
            ),
            "supporting_file_ids": [
                "DOC_2026_10_22_SURROGATE_P6A_PROTOCOL",
                "DOC_2026_10_22_SURROGATE_P6A_RESULTS",
                "P6A_SIM_SIMULATION_EXTRACTION",
                "P6A_SIM_FROZEN_INPUTS",
            ],
        },
        {
            "condition": "topology_and_manufacturing_domain_verified",
            "verified": False,
            "status": "REJECTED",
            "conclusion": (
                "Twelve rows were quarantined for incomplete physical/topology metadata; "
                "historical overrides were not independently validated. No manufacturing "
                "tolerance or layout step evidence was registered."
            ),
            "supporting_file_ids": [
                "P6A_SIM_QUARANTINE",
                "P6A_SIM_SIMULATION_EXTRACTION",
                "DOC_2026_10_27_SURROGATE_NO_FAB_K2_PLUS_ONE_PLAN",
            ],
        },
        {
            "condition": "peak_and_mode_continuity_verified",
            "verified": False,
            "status": "REJECTED",
            "conclusion": (
                "Series 80958e9d857c267b has nearest-peak continuity mismatches at TR-0378 and "
                "TR-0474 despite an originally increasing direction. All v2 series records retain "
                "physical_mode_identity_verified=False."
            ),
            "supporting_file_ids": [
                "P6B_V2_EG_SERIES_DIAGNOSTICS",
                "P6B_V2_SIMULATION_PEAK_PROBES",
                "DOC_2026_10_22_SURROGATE_P6B_RESULTS",
            ],
        },
        {
            "condition": "cross_branch_continuity_verified",
            "verified": False,
            "status": "REJECTED",
            "conclusion": (
                "No controlled FL=0 to FL>0 simulation pair was found. P12's branch discontinuity "
                "and P1203 boundary peak are model behavior, not evidence of physical continuity "
                "or of a required physical jump."
            ),
            "supporting_file_ids": [
                "P6A_SIM_TREND_PAIRS",
                "P12_PROTOCOL",
                "P12_CANDIDATES",
                "P12_PEAK_SEARCHES",
            ],
        },
        {
            "condition": "conditional_trend_validated_against_counterexamples",
            "verified": False,
            "status": "REJECTED",
            "conclusion": (
                "P6A retains 23 nonmonotonic EG-k2 series and 27 negative adjacent pairs. P6B v2 "
                "retains four robust negative pairs in three series after local-resolution and "
                "same-peak interpolation checks; counterexamples cannot be deleted to force a "
                "trend."
            ),
            "supporting_file_ids": [
                "P6A_SIM_TREND_SERIES",
                "P6A_SIM_TREND_PAIRS",
                "P6B_V2_EG_PAIR_DIAGNOSTICS",
                "DOC_2026_10_22_SURROGATE_P6B_RESULTS",
            ],
        },
        {
            "condition": "simulation_to_measurement_transfer_verified",
            "verified": False,
            "status": "REJECTED",
            "conclusion": (
                "All 2772 P6A trend-series rows are marked "
                "simulation_only_not_validated_on_measurement. No measured-transfer validation, "
                "mesh convergence, or loss-sensitivity evidence was registered. This alone "
                "prevents this round's simulation soft-constraint trial."
            ),
            "supporting_file_ids": [
                "P6A_SIM_TREND_SERIES",
                "P6A_SIM_SUMMARY",
                "P6B_V2_SUMMARY",
                "DOC_2026_10_22_SURROGATE_P6A_RESULTS",
                "DOC_2026_10_22_SURROGATE_P6B_RESULTS",
            ],
        },
    ]


def unverifiable_fields():
    return [
        {
            "fields": ["Train_h_AG_nm", "Train_L_AG_um", "measured AG"],
            "issue": (
                "Simulation values are recorded, but measured AG is unrecorded in O01; no "
                "correspondence validation was registered."
            ),
            "conclusion": (
                "Do not guess AG or use AG=1 as a measured-device alignment assumption."
            ),
            "file_ids": ["P6A_SIM_SIMULATION_EXTRACTION", "O01_INPUT_MANIFEST"],
        },
        {
            "fields": [
                "Train_h_FL_nm",
                "Train_h_EG_nm",
                "Train_PF_nm",
                "actual layer thicknesses",
            ],
            "issue": (
                "Simulation fields do not establish the real fabricated stack; twelve PF rows "
                "were quarantined for incomplete physical/topology metadata."
            ),
            "conclusion": (
                "Real layer thicknesses remain unverified; no thickness-derived soft constraint "
                "is eligible."
            ),
            "file_ids": ["P6A_SIM_SIMULATION_EXTRACTION", "P6A_SIM_QUARANTINE"],
        },
        {
            "fields": [
                "MPH_Relative_Path",
                "Source_Model_File",
                "Cross_Source_Status",
                "Diff_Fields",
            ],
            "issue": (
                "Paths and version labels are not MPH-content verification; 30 eligible rows "
                "lack MPH_Relative_Path and historical overrides were not independently validated."
            ),
            "conclusion": (
                "Do not infer verified topology, material settings, mesh, or loss configuration "
                "from these labels."
            ),
            "file_ids": [
                "P6A_SIM_SIMULATION_EXTRACTION",
                "DOC_2026_10_22_SURROGATE_P6A_RESULTS",
            ],
        },
        {
            "fields": ["Train_A_res_um2", "Train_L_top_um", "Train_L_bot_um", "Train_L_air_um"],
            "issue": (
                "Area variation is coupled with top/bottom/air geometry changes in the "
                "controlled simulation series."
            ),
            "conclusion": (
                "Area trends cannot be interpreted as pure area causality or transferred "
                "directly to measured candidates."
            ),
            "file_ids": ["P6A_SIM_TREND_SERIES", "DOC_2026_10_22_SURROGATE_P6A_RESULTS"],
        },
        {
            "fields": ["branch_id", "has_FL", "Train_L_FL_um", "Train_h_FL_nm"],
            "issue": (
                "No controlled FL=0 to FL>0 pair or physical branch-continuity validation "
                "was registered."
            ),
            "conclusion": (
                "Do not impose either continuity or a mandatory jump across measured-model "
                "FL branches."
            ),
            "file_ids": ["P6A_SIM_TREND_PAIRS", "P12_PROTOCOL", "P12_CANDIDATES"],
        },
        {
            "fields": [
                "fs_ghz",
                "fp_ghz",
                "fp_candidates_hz",
                "nearest_peak_continuity_mismatches",
            ],
            "issue": (
                "Nearest-frequency tracking and dominant-peak selection can diverge; peak "
                "identity is not physical eigenmode identity."
            ),
            "conclusion": (
                "Exclude peak-switching cases from any future conditional prior until mode "
                "continuity is independently verified."
            ),
            "file_ids": [
                "P6B_V2_EG_SERIES_DIAGNOSTICS",
                "P6B_V2_SIMULATION_PEAK_PROBES",
            ],
        },
        {
            "fields": [
                "frequency step",
                "mesh convergence",
                "material and electrical loss",
                "process correspondence",
            ],
            "issue": (
                "Local half-bin envelopes are not physical error bounds; mesh convergence, "
                "loss sensitivity, and process transfer were not verified. COMSOL lacks "
                "electrical loss."
            ),
            "conclusion": (
                "No Qp simulation prior is allowed, and residual k2 trends remain "
                "simulation-only hypotheses."
            ),
            "file_ids": ["P6A_SIM_FROZEN_INPUTS", "DOC_2026_10_22_SURROGATE_P6B_RESULTS"],
        },
        {
            "fields": ["manufacturing tolerance", "layout step", "fabrication range"],
            "issue": (
                "No manufacturing-tolerance or layout-step evidence was registered; the "
                "planned ±0.025 um perturbation is only numerical sensitivity."
            ),
            "conclusion": "Do not convert numerical grid offsets into fabrication claims.",
            "file_ids": [
                "DOC_2026_10_27_SURROGATE_NO_FAB_K2_PLUS_ONE_PLAN",
                "P12_CANDIDATES",
            ],
        },
        {
            "fields": ["transfer_status", "measured validation"],
            "issue": (
                "Every P6A trend-series row is simulation_only_not_validated_on_measurement."
            ),
            "conclusion": (
                "Simulation-to-measured transfer is unverified; gate=DISABLED and scientific "
                "status=REJECTED for this round."
            ),
            "file_ids": ["P6A_SIM_TREND_SERIES", "P6A_SIM_SUMMARY"],
        },
    ]


def build_document(attempt, run_id, input_receipts, evidence, pytest_exit, ruff_exit):
    requirements = {key: False for key in REQUIREMENT_KEYS}
    document = {
        "schema": "aln_optimization_p13_simulation_gate_v1",
        "task_id": "T03",
        "attempt_id": attempt,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "scope": (
            "Eligibility of simulation-derived conditional soft constraints for this P13 "
            "round only"
        ),
        **decision_for_requirements(requirements),
        "requirements": requirements,
        "scientific_review": scientific_review(),
        "evidence": evidence,
        "unverifiable_fields": unverifiable_fields(),
        "software_validation": {
            "status": "PASS",
            "scope": (
                "T03 input hashes, v2 record recomputation, JSON contracts, targeted "
                "pytest and Ruff"
            ),
            "registered_input_count": len(input_receipts),
            "input_hash_mismatches": [],
            "targeted_pytest_exit_code": pytest_exit,
            "targeted_ruff_exit_code": ruff_exit,
        },
        "input_receipts": input_receipts,
        "usage_boundaries": {
            "raw_comsol_sweeps_reread": False,
            "new_comsol_run": False,
            "m1_implemented": False,
            "model_training_performed": False,
            "raw_de_mixing": False,
            "simulation_k2_or_q_as_measured_label_or_pseudo_label": False,
            "simulation_qp_prior": "DISABLED",
            "ag_thickness_special_topology_manufacturing_tolerance_guessed": False,
            "scientific_rejection_meaning": (
                "The simulation-derived soft-constraint ablation is not eligible in this round. "
                "This is not a software failure and does not reject measured-only M1/M2 routes."
            ),
        },
        "supporting_documents": [
            "DOC_2026_10_27_SURROGATE_P13_AGENT_EXECUTION_ORDER",
            "DOC_2026_10_27_SURROGATE_NO_FAB_K2_PLUS_ONE_PLAN",
            "DOC_2026_10_22_SURROGATE_P6A_PROTOCOL",
            "DOC_2026_10_22_SURROGATE_P6A_RESULTS",
            "DOC_2026_10_22_SURROGATE_P6B_PROTOCOL",
            "DOC_2026_10_22_SURROGATE_P6B_RESULTS",
            "P12_PROTOCOL",
        ],
        "random_processes": [],
    }
    validate_decision(document)
    return document


def validate_document(document):
    validate_decision(document)
    software = document["software_validation"]
    if software["status"] != "PASS":
        raise ValueError("Formal T03 output requires passing software validation")
    if software["targeted_pytest_exit_code"] != 0 or software["targeted_ruff_exit_code"] != 0:
        raise ValueError("Targeted pytest and Ruff must pass before formalization")
    if software["input_hash_mismatches"] or len(document["input_receipts"]) != 33:
        raise ValueError("T03 input receipt validation failed")
    if not all(row["matches"] for row in document["input_receipts"]):
        raise ValueError("A T03 input receipt does not match its frozen hash")
    p6a = document["evidence"]["p6a_eg_k2"]
    if (p6a["series_count"], p6a["nonmonotonic_series_count"]) != (98, 23):
        raise ValueError("Unexpected P6A EG-k2 series accounting")
    if p6a["negative_adjacent_pair_count"] != len(p6a["negative_adjacent_pairs"]):
        raise ValueError("P6A negative-pair count does not match record IDs")
    if len(p6a["nonmonotonic_series"]) != p6a["nonmonotonic_series_count"]:
        raise ValueError("P6A nonmonotonic-series count does not match record IDs")
    p6b = document["evidence"]["p6b_v2_eg_k2"]
    if p6b["robust_negative_pair_count"] != len(p6b["robust_counterexamples"]):
        raise ValueError("P6B robust-pair count does not match record IDs")
    if (p6b["raw_negative_pair_count"], p6b["robust_negative_pair_count"]) != (27, 4):
        raise ValueError("Unexpected P6B v2 negative-pair accounting")
    if p6b["robust_negative_series_count"] != 3:
        raise ValueError("Unexpected P6B v2 robust-series accounting")
    cross = document["evidence"]["cross_branch_continuity"]
    if cross["controlled_fl_zero_to_positive_pair_count"] != 0:
        raise ValueError("Cross-branch evidence changed; a new scientific review is required")
    transfer = document["evidence"]["geometry_and_transfer_metadata"]["transfer_status_counts"]
    if transfer != {"simulation_only_not_validated_on_measurement": 2772}:
        raise ValueError("Unexpected simulation-transfer status accounting")
    if document["evidence"]["vc06"]["superseded_v1_used"] is not False:
        raise ValueError("VC06 forbids superseded P6B v1 diagnostics")
    boundaries = document["usage_boundaries"]
    for key in (
        "raw_comsol_sweeps_reread",
        "new_comsol_run",
        "m1_implemented",
        "model_training_performed",
        "raw_de_mixing",
        "simulation_k2_or_q_as_measured_label_or_pseudo_label",
        "ag_thickness_special_topology_manufacturing_tolerance_guessed",
    ):
        if boundaries[key] is not False:
            raise ValueError(f"T03 usage boundary violated: {key}")


def run_logged(command, log_path, env):
    with Path(log_path).open("x", encoding="utf-8") as handle:
        handle.write("COMMAND: " + " ".join(command) + "\n")
        handle.flush()
        result = subprocess.run(
            command,
            cwd=BE,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        handle.write(f"\nEXIT_CODE: {result.returncode}\n")
    return result.returncode


def environment():
    packages = {
        name: version(name)
        for name in ("numpy", "scipy", "pandas", "scikit-learn", "pytest", "ruff")
    }
    uv = subprocess.run(
        ["uv", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    git = subprocess.run(
        ["/opt/homebrew/bin/git", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "packages": packages,
        "uv": uv,
        "git": git,
        "cpu_count": os.cpu_count(),
        "numeric_threads": {
            key: os.environ.get(key, "1")
            for key in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "NUMEXPR_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
        "python_dont_write_bytecode": os.environ.get("PYTHONDONTWRITEBYTECODE", "1"),
    }


def output_receipt(run, path):
    path = Path(path)
    return {
        "path": str(path.relative_to(run)),
        "kind": "file",
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
    }


def produce(run, attempt):
    run = run.resolve()
    gate_path = run / "audit/simulation_gate.json"
    manifest_path = run / "audit/simulation_gate_artifact_manifest.json"
    if gate_path.exists() or manifest_path.exists():
        raise FileExistsError("Formal T03 outputs already exist; use a new reviewed attempt")
    work = run / "audit/simulation_gate_work" / attempt
    work.mkdir(parents=True, exist_ok=False)
    log_dir = run / "audit/logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    pytest_log = log_dir / f"simulation_gate_pytest_{attempt}.log"
    ruff_log = log_dir / f"simulation_gate_ruff_{attempt}.log"
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in (
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    ):
        env[key] = "1"
    pytest_command = [
        "uv",
        "run",
        "--no-sync",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "--basetemp",
        str(work / "pytest_tmp"),
        "tests/ml/test_optimization_p13_t03_simulation_gate.py",
    ]
    ruff_command = [
        "uv",
        "run",
        "--no-sync",
        "ruff",
        "check",
        "--no-cache",
        "scripts/optimization_p13_t03_simulation_gate.py",
        "tests/ml/test_optimization_p13_t03_simulation_gate.py",
    ]
    ruff_exit = run_logged(ruff_command, ruff_log, env)
    pytest_exit = run_logged(pytest_command, pytest_log, env)
    if ruff_exit != 0 or pytest_exit != 0:
        raise RuntimeError(f"Targeted checks failed: Ruff={ruff_exit}, pytest={pytest_exit}")

    source_hashes_before = {
        str(SCRIPT_PATH): sha256(SCRIPT_PATH),
        str(TEST_PATH): sha256(TEST_PATH),
    }
    o01, inputs = load_inputs(run)
    input_receipts = verify_input_hashes(inputs)
    evidence = collect_evidence(inputs)
    document = build_document(
        attempt,
        o01["run_id"],
        input_receipts,
        evidence,
        pytest_exit,
        ruff_exit,
    )
    validate_document(document)
    temp_gate = work / "simulation_gate.json"
    write_new(temp_gate, json_bytes(document))
    temp_document = read_json(temp_gate)
    validate_document(temp_document)
    temp_validation = {
        "schema": "aln_optimization_p13_simulation_gate_temp_validation_v1",
        "task_id": "T03",
        "attempt_id": attempt,
        "status": "PASS",
        "temporary_output": str(temp_gate.relative_to(run)),
        "temporary_output_sha256": sha256(temp_gate),
        "checks": {
            "json_roundtrip": True,
            "decision_contract": True,
            "input_hashes": True,
            "p6a_counterexample_accounting": True,
            "p6b_v2_counterexample_accounting": True,
            "cross_branch_accounting": True,
            "transfer_status_accounting": True,
            "usage_boundaries": True,
        },
    }
    temp_validation_path = work / "simulation_gate_validation.json"
    write_new(temp_validation_path, json_bytes(temp_validation))
    if sha256(SCRIPT_PATH) != source_hashes_before[str(SCRIPT_PATH)]:
        raise RuntimeError("T03 script changed during execution")
    if sha256(TEST_PATH) != source_hashes_before[str(TEST_PATH)]:
        raise RuntimeError("T03 tests changed during execution")

    write_new(gate_path, temp_gate.read_bytes())
    if sha256(gate_path) != temp_validation["temporary_output_sha256"]:
        raise RuntimeError("Formal gate differs from the validated temporary output")
    formal_document = read_json(gate_path)
    validate_document(formal_document)
    main_command = (
        "uv run --no-sync python scripts/optimization_p13_t03_simulation_gate.py "
        f"--run {run} --attempt {attempt}"
    )
    manifest = {
        "schema": "aln_optimization_p13_artifact_manifest_v1",
        "task_id": "T03",
        "attempt_id": attempt,
        "run_id": o01["run_id"],
        "status": "REJECTED",
        "software_status": "PASS",
        "gate": "DISABLED",
        "inputs": input_receipts,
        "outputs": [
            output_receipt(run, path)
            for path in (gate_path, temp_gate, temp_validation_path, pytest_log, ruff_log)
        ],
        "source_sha256": source_hashes_before,
        "command": main_command,
        "commands": [
            {"command": main_command, "cwd": str(BE), "exit_code": 0},
            {
                "command": " ".join(pytest_command),
                "cwd": str(BE),
                "exit_code": pytest_exit,
                "log": str(pytest_log),
            },
            {
                "command": " ".join(ruff_command),
                "cwd": str(BE),
                "exit_code": ruff_exit,
                "log": str(ruff_log),
            },
        ],
        "exit_code": 0,
        "environment": environment(),
        "validation": temp_validation,
        "random_processes": [],
        "created_at": datetime.now(UTC).isoformat(),
        "notes": [
            "Scientific REJECTED applies only to simulation-derived conditional soft constraints.",
            (
                "Software validation PASS does not imply simulation-to-measured transfer "
                "or model eligibility."
            ),
            "Pure measured M1/M2 routes are not blocked by O04.",
            (
                "No v1 diagnostics, raw COMSOL sweep reread, new COMSOL run, M1 "
                "implementation, training, dependency change, commit, or deployment was used."
            ),
            (
                "The temporary gate was validated before the formal gate and this manifest "
                "were created."
            ),
        ],
    }
    write_new(manifest_path, json_bytes(manifest))
    reloaded_manifest = read_json(manifest_path)
    for row in reloaded_manifest["outputs"]:
        path = run / row["path"]
        if sha256(path) != row["sha256"]:
            raise RuntimeError(f"Manifest output hash mismatch: {row['path']}")
    print(
        json.dumps(
            {
                "task_id": "T03",
                "status": "REJECTED",
                "software_status": "PASS",
                "gate": "DISABLED",
                "gate_output": str(gate_path),
                "manifest": str(manifest_path),
                "gate_sha256": sha256(gate_path),
                "manifest_sha256": sha256(manifest_path),
                "pytest_exit_code": pytest_exit,
                "ruff_exit_code": ruff_exit,
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--attempt", required=True)
    args = parser.parse_args()
    produce(args.run, args.attempt)


if __name__ == "__main__":
    main()
