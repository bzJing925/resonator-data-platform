from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Real
from pathlib import Path

INTERFACE_VERSION = "optimization_p13.geometry_io.v1"
SCHEMA_VERSION = "optimization_p13.contract.v1"
O02_SHA256 = "bc0f16a4791d1968cac10afafd5fd14a32abfa7bd7d0329b2591ff825d88f05c"
O05_SPLITS_SHA256 = "fd000bd582f01286d6d862d1dddffa16199146e7acf07802ab2d8a27050a71a9"
O05_CAPACITY_SHA256 = "77cd9e9b664fa2e0e39a6ebd32c50579ee407adb13cd938d2450e6951896d60f"
P3_STRUCTURES_SHA256 = "426d844b4854c95593e3e9b621637d12f42eb4422f6ad75b8e6e67a86d837000"
P3_SOURCE_SHA256 = "c9d61cb4d63381cc6b5e849fb255a991c6d6f28e67fa29dcaf44e90c5b00c8b7"
BASELINE_DESIGN_GROUP_ID = "d_71c5a74d95544080"
GEOMETRY_FIELDS = ("eg_um", "fl_um", "area_um2", "has_pf")
KNOWN_BRANCHES = (
    "pf0_eg0_fl0",
    "pf0_eg0_fl1",
    "pf0_eg1_fl0",
    "pf0_eg1_fl1",
    "pf1_eg0_fl0",
    "pf1_eg0_fl1",
)
TARGET_UNITS = {
    "k2eff_pct": "percent_number",
    "k2eff_pct_native": "percent_number",
    "k2_delta": "percentage_points",
    "fs_ghz": "GHz",
    "fp_ghz": "GHz",
    "frequency_ratio": "dimensionless",
    "qs": "dimensionless",
    "qp": "dimensionless",
    "bodeq_fitted": "dimensionless",
}
MEASURED_FIELDS = {
    "actual",
    "actual_k2eff_pct",
    "measured_k2eff_pct",
    "k2eff_pct",
    "fs_ghz",
    "fp_ghz",
    "observed_fs_ghz",
    "observed_fp_ghz",
    "measured_fs_ghz",
    "measured_fp_ghz",
    "actual_fs_ghz",
    "actual_fp_ghz",
}
RAW_SIM_FIELDS = {
    "raw",
    "sim",
    "simulation",
    "raw_k2eff_pct",
    "sim_k2eff_pct",
    "simulation_k2eff_pct",
    "raw_fs_ghz",
    "raw_fp_ghz",
    "sim_fs_ghz",
    "sim_fp_ghz",
}
ALLOWED_RECORD_FIELDS = {
    "schema_version",
    "interface_version",
    "record_kind",
    "model_family",
    "structure_id",
    "candidate_id",
    "design_group_id",
    "design_group_null_reason",
    "scope",
    "fold",
    "unit_id",
    "family",
    "role",
    "branch_id",
    "model_id",
    "config_id",
    "target",
    "unit",
    "prediction",
    "actual",
    "valid_prediction",
    "invalid_reason",
    "null_reason",
    "support_label",
    "source_mode",
    "label_source_file_id",
    "source_file_id",
    "source_sha256",
    "source_split_file_id",
    "source_split_sha256",
    "source_structure_file_id",
    "source_structure_sha256",
    "protocol_sha256",
    "measured",
    "trusted_for_unmeasured_optimization",
    "aggregation_estimand_match",
    "qualified",
    "eg_um",
    "fl_um",
    "area_um2",
    "has_pf",
    "pred_a",
    "pred_z",
    "pred_fs_ghz",
    "pred_fp_ghz",
    "pred_frequency_ratio",
    "pred_k2eff_pct_native",
    "p3_comparable_prediction",
    "p3_comparable_null_reason",
    "frequency_valid",
    "native_k2_valid",
}
REQUIRED_RECORD_FIELDS = {
    "schema_version",
    "interface_version",
    "record_kind",
    "model_family",
    "scope",
    "fold",
    "unit_id",
    "family",
    "role",
    "branch_id",
    "model_id",
    "config_id",
    "target",
    "unit",
    "support_label",
    "source_file_id",
    "source_sha256",
    "protocol_sha256",
    "valid_prediction",
    "measured",
    "trusted_for_unmeasured_optimization",
    *GEOMETRY_FIELDS,
}
M1_INVALID_REASONS = {
    "missing_or_nonfinite_geometry",
    "negative_eg_or_fl",
    "nonpositive_area",
    "invalid_has_pf",
    "branch_mismatch",
    "unknown_or_empty_branch",
    "nonfinite_model_output",
    "non_physical_k2_prediction",
    "invalid_training_geometry",
    "invalid_training_target",
    "numerical_fit_failure",
}


class ContractError(ValueError):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class P6CControlSpec:
    recipe: str
    helper: str
    target: str
    fixed_details: str
    same_o05_rows_required: bool
    old_outer_prediction_substitute_allowed: bool
    recipe_reselection_allowed: bool


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_protocol(path: Path, expected_sha256: str = O02_SHA256) -> dict:
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ContractError("O02_HASH_MISMATCH", f"O02 hash mismatch: {actual}")
    protocol = json.loads(path.read_text())
    require_protocol(protocol)
    return protocol


def require_protocol(protocol: Mapping) -> None:
    if protocol.get("schema") != "aln_optimization_p13_protocol_v1":
        raise ContractError("O02_SCHEMA_MISMATCH", "Unexpected O02 schema")
    if protocol.get("gate") != "G1" or protocol.get("status") != "PASS":
        raise ContractError("G1_NOT_FROZEN", "O02 must be frozen at G1")
    m2 = protocol["models"]["M2"]
    if m2["aggregation_estimand_match"] is not False:
        raise ContractError("M2_AGGREGATION_CONTRACT", "M2 aggregation flag changed")
    if m2["qualified_model_set_eligible"] is not False:
        raise ContractError("M2_QUALIFIED_CONTRACT", "M2 qualification override is forbidden")
    if m2["eligibility_override_allowed"] is not False:
        raise ContractError("M2_QUALIFIED_CONTRACT", "M2 eligibility override must stay false")
    p6c_control_spec(protocol)


def _geometry_reason(model_family: str, m1_reason: str) -> str:
    return m1_reason if model_family == "M1" else "INVALID_GEOMETRY"


def _finite_geometry_value(value, reason: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, Real):
        raise ContractError(reason, f"Invalid geometry value {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError(reason, f"Invalid geometry value {value!r}")
    return result


def validate_geometry(record: Mapping, model_family: str = "M1") -> dict:
    if not isinstance(record, Mapping):
        raise ContractError("INVALID_GEOMETRY", "Geometry must be a mapping")
    for field in set(record) & RAW_SIM_FIELDS:
        raise ContractError("RAW_OR_SIMULATION_FIELD_FORBIDDEN", f"Forbidden field {field}")
    missing = [field for field in GEOMETRY_FIELDS if field not in record]
    if missing:
        raise ContractError(
            _geometry_reason(model_family, "missing_or_nonfinite_geometry"),
            f"Missing geometry fields {missing}",
        )
    nonfinite_reason = _geometry_reason(model_family, "missing_or_nonfinite_geometry")
    eg = _finite_geometry_value(record["eg_um"], nonfinite_reason)
    fl = _finite_geometry_value(record["fl_um"], nonfinite_reason)
    area = _finite_geometry_value(record["area_um2"], nonfinite_reason)
    if eg < 0 or fl < 0:
        raise ContractError(
            _geometry_reason(model_family, "negative_eg_or_fl"), "EG and FL must be nonnegative"
        )
    if area <= 0:
        raise ContractError(
            _geometry_reason(model_family, "nonpositive_area"), "Area must be positive"
        )
    has_pf = _finite_geometry_value(record["has_pf"], nonfinite_reason)
    if has_pf not in (0.0, 1.0):
        raise ContractError(
            _geometry_reason(model_family, "invalid_has_pf"), "has_pf must be 0 or 1"
        )
    branch = f"pf{int(has_pf)}_eg{int(eg > 0)}_fl{int(fl > 0)}"
    if branch not in KNOWN_BRANCHES:
        raise ContractError(
            "unknown_or_empty_branch" if model_family == "M1" else "UNSUPPORTED_BRANCH",
            f"Unsupported branch {branch}",
        )
    supplied = record.get("branch_id")
    if supplied is not None and supplied != branch:
        reason = "branch_mismatch" if model_family == "M1" else "UNSUPPORTED_BRANCH"
        raise ContractError(reason, f"Expected {branch}, got {supplied}")
    return {
        "eg_um": eg,
        "fl_um": fl,
        "area_um2": area,
        "has_pf": int(has_pf),
        "branch_id": branch,
    }


def validate_inference_input(record: Mapping, model_family: str = "M1") -> dict:
    if not isinstance(record, Mapping):
        raise ContractError("INVALID_GEOMETRY", "Inference input must be a mapping")
    for field in set(record) & RAW_SIM_FIELDS:
        raise ContractError("RAW_OR_SIMULATION_FIELD_FORBIDDEN", f"Forbidden field {field}")
    for field in set(record) & MEASURED_FIELDS:
        raise ContractError("MEASURED_INPUT_FORBIDDEN", f"Measured inference input {field}")
    unknown = set(record) - {*GEOMETRY_FIELDS, "branch_id"}
    if unknown:
        fields = sorted(unknown)
        raise ContractError("UNKNOWN_INFERENCE_FIELD", f"Unknown inference fields {fields}")
    geometry = validate_geometry(record, model_family)
    return {field: geometry[field] for field in GEOMETRY_FIELDS}


def invoke_geometry_model(
    model,
    record: Mapping,
    *,
    interface_version: str = INTERFACE_VERSION,
) -> Mapping:
    if getattr(model, "interface_version", None) != interface_version:
        raise ContractError("INTERFACE_VERSION_MISMATCH", "Model interface version mismatch")
    if not getattr(model, "model_id", None) or not getattr(model, "config_id", None):
        raise ContractError("MODEL_ID_REQUIRED", "model_id and config_id are required")
    model_family = getattr(model, "model_family", "M1")
    geometry = validate_inference_input(record, model_family)
    output = model.predict(geometry)
    if not isinstance(output, Mapping):
        raise ContractError("INVALID_MODEL_OUTPUT", "Model output must be a mapping")
    for field in set(output) & (MEASURED_FIELDS | RAW_SIM_FIELDS):
        raise ContractError("MEASURED_INPUT_FORBIDDEN", f"Model returned measured field {field}")
    return output


def k2_from_frequency(fs_ghz, fp_ghz) -> float:
    fs = _finite_frequency(fs_ghz)
    fp = _finite_frequency(fp_ghz)
    if fs <= 0 or fp <= fs:
        raise ContractError("INVALID_FREQUENCY", "Require finite 0 < fs < fp")
    ratio = fs / fp
    if not math.isfinite(ratio) or not 0 < ratio < 1:
        raise ContractError("INVALID_FREQUENCY", "Frequency ratio must be in (0,1)")
    result = 25 * math.pi**2 * ratio * (1 - ratio)
    if not math.isfinite(result) or not 0 < result < 100:
        raise ContractError("INVALID_K2", "Native k2 must be finite and in (0,100)")
    return result


def _finite_frequency(value) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, Real):
        raise ContractError("INVALID_FREQUENCY", f"Invalid frequency {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError("INVALID_FREQUENCY", f"Invalid frequency {value!r}")
    return result


def frequency_supervision(fs_ghz, fp_ghz) -> dict:
    fs = _finite_frequency(fs_ghz)
    fp = _finite_frequency(fp_ghz)
    if fs <= 0 or fp <= fs:
        raise ContractError("INVALID_FREQUENCY", "Require finite 0 < fs < fp")
    k2_from_frequency(fs, fp)
    return {
        "a": math.log(fs),
        "z": math.log(fp / fs - 1),
        "fs_ghz": fs,
        "fp_ghz": fp,
    }


def validate_k2_label(fs_ghz, fp_ghz, label, consistency_atol: float = 0.01) -> float:
    expected = k2_from_frequency(fs_ghz, fp_ghz)
    if label is None or isinstance(label, bool) or not isinstance(label, Real):
        raise ContractError("INVALID_K2_LABEL", "k2 label must be finite and in (0,100)")
    actual = float(label)
    if not math.isfinite(actual) or not 0 < actual < 100:
        raise ContractError("INVALID_K2_LABEL", "k2 label must be finite and in (0,100)")
    if abs(actual - expected) > consistency_atol:
        raise ContractError(
            "K2_FORMULA_MISMATCH",
            f"k2 formula difference exceeds {consistency_atol} percentage points",
        )
    return actual


def _softplus(value: float) -> float:
    return max(value, 0.0) + math.log1p(math.exp(-abs(value)))


def _null_m2_outputs(reason: str, a=None, z=None) -> dict:
    return {
        "pred_a": a,
        "pred_z": z,
        "pred_fs_ghz": None,
        "pred_fp_ghz": None,
        "pred_frequency_ratio": None,
        "pred_k2eff_pct_native": None,
        "prediction": None,
        "valid_prediction": False,
        "invalid_reason": reason,
        "frequency_valid": False,
        "native_k2_valid": False,
        "aggregation_estimand_match": False,
        "qualified": False,
        "p3_comparable_prediction": None,
        "p3_comparable_null_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
    }


def m2_inverse_prediction(a, z) -> dict:
    try:
        latent_a = _finite_frequency(a)
        latent_z = _finite_frequency(z)
    except ContractError:
        return _null_m2_outputs("INVALID_FREQUENCY")
    try:
        spread = _softplus(latent_z)
        fs = math.exp(latent_a)
        fp = math.exp(latent_a + spread)
        ratio = math.exp(-spread)
        if not all(math.isfinite(value) for value in (spread, fs, fp, ratio)):
            return _null_m2_outputs("INVALID_FREQUENCY", latent_a, latent_z)
        if fs <= 0 or fp <= fs or not 0 < ratio < 1:
            return _null_m2_outputs("INVALID_FREQUENCY", latent_a, latent_z)
        native = k2_from_frequency(fs, fp)
    except (OverflowError, ContractError):
        return _null_m2_outputs("INVALID_FREQUENCY", latent_a, latent_z)
    return {
        "pred_a": latent_a,
        "pred_z": latent_z,
        "pred_fs_ghz": fs,
        "pred_fp_ghz": fp,
        "pred_frequency_ratio": ratio,
        "pred_k2eff_pct_native": native,
        "prediction": native,
        "valid_prediction": True,
        "invalid_reason": None,
        "frequency_valid": True,
        "native_k2_valid": True,
        "aggregation_estimand_match": False,
        "qualified": False,
        "p3_comparable_prediction": None,
        "p3_comparable_null_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
    }


def p6c_control_spec(protocol: Mapping) -> P6CControlSpec:
    control = protocol["models"]["P6C_control"]
    if control["recipe"] != "trend_gp_log_area":
        raise ContractError("P6C_RECIPE_CHANGED", "P6C recipe is frozen")
    if control["helper"] != "app.ml.optimization_ablation.AblationModel":
        raise ContractError("P6C_HELPER_CHANGED", "P6C helper is frozen")
    if control["target"] != "k2eff_pct":
        raise ContractError("P6C_TARGET_CHANGED", "P6C target is frozen")
    if not control["same_o05_rows_required"]:
        raise ContractError("P6C_ROWS_CHANGED", "P6C must use identical O05 rows")
    if control["old_outer_prediction_substitute_allowed"]:
        raise ContractError("P6C_OUTER_SUBSTITUTE_FORBIDDEN", "Old outer predictions are forbidden")
    if control["recipe_reselection_allowed"]:
        raise ContractError("P6C_RESELECTION_FORBIDDEN", "P6C recipe reselection is forbidden")
    return P6CControlSpec(
        recipe=control["recipe"],
        helper=control["helper"],
        target=control["target"],
        fixed_details=control["fixed_details"],
        same_o05_rows_required=control["same_o05_rows_required"],
        old_outer_prediction_substitute_allowed=control[
            "old_outer_prediction_substitute_allowed"
        ],
        recipe_reselection_allowed=control["recipe_reselection_allowed"],
    )


def instantiate_p6c_control(spec: P6CControlSpec, import_module=importlib.import_module):
    module_name, class_name = spec.helper.rsplit(".", 1)
    model_class = getattr(import_module(module_name), class_name)
    return model_class(spec.recipe, spec.target)


O02_FORMULA_CONSISTENCY_ATOL = 1e-12


def _m2_real_field(record: Mapping, field: str, reason: str) -> float:
    value = record.get(field)
    if value is None or isinstance(value, bool) or not isinstance(value, Real):
        raise ContractError(reason, f"{field} must be finite")
    result = float(value)
    if not math.isfinite(result):
        raise ContractError(reason, f"{field} must be finite")
    return result


def _validate_m2_frequency_record(record: Mapping, valid_prediction: bool) -> None:
    frequency_valid = record.get("frequency_valid")
    native_valid = record.get("native_k2_valid")
    if not isinstance(frequency_valid, bool) or not isinstance(native_valid, bool):
        raise ContractError("M2_VALIDITY_FLAG_CONTRADICTION", "M2 validity flags must be boolean")
    if valid_prediction and not (frequency_valid and native_valid):
        raise ContractError(
            "M2_VALIDITY_FLAG_CONTRADICTION",
            "valid_prediction requires valid frequency and native k2",
        )
    if not valid_prediction and native_valid:
        raise ContractError(
            "M2_VALIDITY_FLAG_CONTRADICTION",
            "Invalid prediction cannot have native_k2_valid=true",
        )
    if not valid_prediction and not frequency_valid:
        for field in (
            "pred_fs_ghz",
            "pred_fp_ghz",
            "pred_frequency_ratio",
            "pred_k2eff_pct_native",
        ):
            if record.get(field) is not None:
                raise ContractError("INVALID_FREQUENCY", "Invalid frequency outputs must be null")
        return
    fs = _m2_real_field(record, "pred_fs_ghz", "INVALID_FREQUENCY")
    fp = _m2_real_field(record, "pred_fp_ghz", "INVALID_FREQUENCY")
    ratio = _m2_real_field(record, "pred_frequency_ratio", "INVALID_FREQUENCY")
    if fs <= 0 or fp <= fs or not 0 < ratio < 1:
        raise ContractError("INVALID_FREQUENCY", "Require fs>0, fp>fs and 0<r<1")
    if not math.isclose(ratio, fs / fp, abs_tol=O02_FORMULA_CONSISTENCY_ATOL, rel_tol=0):
        raise ContractError("INVALID_FREQUENCY", "Frequency ratio is inconsistent with fs/fp")
    expected_native = k2_from_frequency(fs, fp)
    if not native_valid:
        if record.get("pred_k2eff_pct_native") is not None:
            raise ContractError("INVALID_K2", "Invalid native k2 must be null")
        return
    native = _m2_real_field(record, "pred_k2eff_pct_native", "INVALID_K2")
    if not 0 < native < 100:
        raise ContractError("INVALID_K2", "Native k2 must be in (0,100)")
    if not math.isclose(native, expected_native, abs_tol=O02_FORMULA_CONSISTENCY_ATOL, rel_tol=0):
        raise ContractError("INVALID_K2", "Native k2 is inconsistent with fs/fp")


def validate_prediction_record(record: Mapping, expected_o02_sha256: str = O02_SHA256) -> None:
    if not isinstance(record, Mapping):
        raise ContractError("INVALID_RECORD", "Prediction record must be a mapping")
    for field in set(record) & RAW_SIM_FIELDS:
        raise ContractError("RAW_OR_SIMULATION_FIELD_FORBIDDEN", f"Forbidden field {field}")
    unknown = set(record) - ALLOWED_RECORD_FIELDS
    if unknown:
        raise ContractError("UNKNOWN_FIELD", f"Unknown prediction fields {sorted(unknown)}")
    missing = sorted(REQUIRED_RECORD_FIELDS - set(record))
    if missing:
        raise ContractError("MISSING_FIELD", f"Missing prediction fields {missing}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise ContractError("SCHEMA_VERSION_MISMATCH", "Unexpected schema version")
    if record["interface_version"] != INTERFACE_VERSION:
        raise ContractError("INTERFACE_VERSION_MISMATCH", "Unexpected interface version")
    if record["protocol_sha256"] != expected_o02_sha256:
        raise ContractError("O02_HASH_MISMATCH", "Record references the wrong O02")
    for field in ("source_file_id", "model_id", "config_id", "support_label", "scope", "fold"):
        if not isinstance(record[field], str) or not record[field]:
            raise ContractError("MISSING_FIELD", f"{field} must be a nonempty string")
    if not re.fullmatch(r"[0-9a-f]{64}", record["source_sha256"]):
        raise ContractError("SOURCE_HASH_INVALID", "source_sha256 must be a SHA-256 hex digest")
    for field in ("source_split_sha256", "source_structure_sha256"):
        if field in record and not re.fullmatch(r"[0-9a-f]{64}", record[field]):
            raise ContractError("SOURCE_HASH_INVALID", f"{field} must be a SHA-256 hex digest")
    model_family = record["model_family"]
    if model_family not in {"M1", "M2", "P6C"}:
        raise ContractError("MODEL_FAMILY_UNKNOWN", f"Unknown model family {model_family}")
    geometry = validate_geometry(record, model_family)
    if record["branch_id"] != geometry["branch_id"]:
        raise ContractError("branch_mismatch", "Record branch does not match geometry")
    if record["target"] not in TARGET_UNITS or record["unit"] != TARGET_UNITS[record["target"]]:
        raise ContractError("UNIT_MISMATCH", "Target and unit do not match the contract")
    kind = record["record_kind"]
    if kind not in {"fit", "evaluation", "inference", "control", "baseline_difference"}:
        raise ContractError("RECORD_KIND_UNKNOWN", f"Unknown record kind {kind}")
    if kind != "inference":
        source_required = {
            "source_split_file_id",
            "source_structure_file_id",
            "source_split_sha256",
            "source_structure_sha256",
        }
        missing_source = sorted(source_required - set(record))
        if missing_source:
            raise ContractError("MISSING_FIELD", f"Missing source fields {missing_source}")
        if record["fold"] != record["unit_id"]:
            raise ContractError("FOLD_UNIT_MISMATCH", "fold must equal unit_id for O05 records")
    if kind != "inference" and record["measured"] is not True:
        raise ContractError("MEASUREMENT_FLAG_REQUIRED", "Measured records must set measured=true")
    if kind == "control" and model_family != "P6C":
        raise ContractError("CONTROL_MODEL_REQUIRED", "Control records require model_family=P6C")
    if kind in {"evaluation", "control", "baseline_difference"}:
        if kind == "baseline_difference":
            if record["role"] != "baseline_only":
                raise ContractError(
                    "BASELINE_ROLE_REQUIRED", "Baseline difference is baseline_only"
                )
            if record.get("design_group_id") != BASELINE_DESIGN_GROUP_ID:
                raise ContractError("BASELINE_GROUP_REQUIRED", "Unexpected baseline design group")
        elif record["role"] != "evaluation":
            raise ContractError("MEASURED_VALUE_FORBIDDEN", "Actual values require evaluation role")
        if not record.get("structure_id") or not record.get("design_group_id"):
            raise ContractError("IDENTITY_REQUIRED", "Evaluation identity is required")
        if record.get("actual") is None:
            raise ContractError("ACTUAL_REQUIRED", "Evaluation actual is required")
        if record.get("source_mode") != "deembedded":
            raise ContractError("RAW_OR_SIMULATION_SOURCE_FORBIDDEN", "Only deembedded labels")
        if record.get("label_source_file_id") != "P3_STRUCTURES":
            raise ContractError("LABEL_SOURCE_FORBIDDEN", "Only O01 P3 deembedded labels")
        actual = record["actual"]
        if isinstance(actual, bool) or not isinstance(actual, Real) or not math.isfinite(actual):
            raise ContractError("ACTUAL_INVALID", "Evaluation actual must be finite")
    else:
        if record.get("actual") is not None:
            raise ContractError("MEASURED_VALUE_FORBIDDEN", "Actual values are evaluation-only")
    if kind == "fit":
        if record.get("source_mode") != "deembedded":
            raise ContractError("RAW_OR_SIMULATION_SOURCE_FORBIDDEN", "Only deembedded labels")
        if record.get("label_source_file_id") != "P3_STRUCTURES":
            raise ContractError("LABEL_SOURCE_FORBIDDEN", "Only O01 P3 deembedded labels")
    if kind == "inference":
        if record.get("source_mode") not in {None, "unmeasured"}:
            raise ContractError(
                "RAW_OR_SIMULATION_SOURCE_FORBIDDEN",
                "Inference source_mode must be null or unmeasured",
            )
        if record["role"] != "inference" or record["measured"] is not False:
            raise ContractError("INFERENCE_MEASUREMENT_CONTRACT", "Inference must be unmeasured")
        if not record.get("candidate_id") and not record.get("structure_id"):
            raise ContractError("IDENTITY_REQUIRED", "candidate_id or structure_id is required")
        if record.get("design_group_id") is None and not record.get("design_group_null_reason"):
            raise ContractError("NULL_REASON_REQUIRED", "Null design group requires a reason")
    if record["trusted_for_unmeasured_optimization"] is not False:
        raise ContractError(
            "TRUST_FLAG_FORBIDDEN", "trusted_for_unmeasured_optimization must be false"
        )
    valid = record["valid_prediction"]
    if not isinstance(valid, bool):
        raise ContractError("VALID_FLAG_INVALID", "valid_prediction must be boolean")
    prediction = record.get("prediction")
    if valid:
        if prediction is None or isinstance(prediction, bool) or not isinstance(prediction, Real):
            raise ContractError("PREDICTION_REQUIRED", "A finite prediction is required")
        if not math.isfinite(prediction):
            raise ContractError("nonfinite_model_output", "Prediction must be finite")
        if record["target"] in {"k2eff_pct", "k2eff_pct_native"} and not 0 < prediction < 100:
            raise ContractError("non_physical_k2_prediction", "k2 prediction must be in (0,100)")
        if record.get("invalid_reason") is not None:
            raise ContractError("NULL_REASON_FORBIDDEN", "Valid prediction has no invalid reason")
    else:
        if prediction is not None:
            raise ContractError("NULL_REQUIRED", "Invalid prediction must be null, not zero")
        if not record.get("invalid_reason") and not record.get("null_reason"):
            raise ContractError("NULL_REASON_REQUIRED", "Null prediction requires a reason")
        if model_family == "M1" and record.get("invalid_reason") not in M1_INVALID_REASONS:
            raise ContractError(
                "INVALID_REASON_VOCABULARY", "M1 invalid_reason is outside the O02 vocabulary"
            )
    if model_family == "M2":
        if record.get("aggregation_estimand_match") is not False:
            raise ContractError("M2_AGGREGATION_CONTRACT", "M2 aggregation mismatch is mandatory")
        if record.get("qualified") is not False:
            raise ContractError("M2_QUALIFIED_CONTRACT", "M2 must remain unqualified")
        if record.get("p3_comparable_prediction") is not None:
            raise ContractError("M2_AGGREGATION_CONTRACT", "P3-comparable M2 must be null")
        if record.get("p3_comparable_null_reason") != "P3_AGGREGATION_ESTIMAND_MISMATCH":
            raise ContractError("NULL_REASON_REQUIRED", "M2 aggregation null reason is required")
        if prediction != record.get("pred_k2eff_pct_native"):
            raise ContractError("M2_OUTPUT_MISMATCH", "prediction must equal native k2")
        _validate_m2_frequency_record(record, valid)


def validate_schema_document(document: Mapping, protocol: Mapping) -> None:
    required = {
        "schema",
        "interface_version",
        "task_id",
        "o02_sha256",
        "fields",
        "record_types",
        "model_interfaces",
        "evaluation",
        "source_isolation",
        "numerical_reproduction",
        "artifact_contracts",
    }
    missing = sorted(required - set(document))
    if missing:
        raise ContractError("SCHEMA_INCOMPLETE", f"Missing schema sections {missing}")
    if document["schema"] != SCHEMA_VERSION:
        raise ContractError("SCHEMA_VERSION_MISMATCH", "Unexpected schema document version")
    if document["interface_version"] != INTERFACE_VERSION:
        raise ContractError("INTERFACE_VERSION_MISMATCH", "Unexpected schema interface")
    if document["task_id"] != "T06" or document["o02_sha256"] != O02_SHA256:
        raise ContractError("O02_HASH_MISMATCH", "Schema does not reference frozen O02")
    require_protocol(protocol)
    m2 = document["model_interfaces"]["M2"]
    if m2["aggregation_estimand_match"] is not False or m2["qualified"] is not False:
        raise ContractError("M2_QUALIFIED_CONTRACT", "Schema cannot qualify M2")
    evaluation = document["evaluation"]
    acceptance = protocol["evaluation"]["acceptance"]
    if evaluation["acceptance"] != acceptance:
        raise ContractError("THRESHOLD_CHANGED", "Schema acceptance rules differ from O02")
    if document["numerical_reproduction"] != protocol["numerical_reproduction"]:
        raise ContractError("TOLERANCE_CHANGED", "Schema tolerances differ from O02")


def build_schema_document(protocol: Mapping) -> dict:
    require_protocol(protocol)
    control = p6c_control_spec(protocol)
    m1 = protocol["models"]["M1"]
    m2 = protocol["models"]["M2"]["model_configuration"]
    common_fields = sorted(
        REQUIRED_RECORD_FIELDS
        | {
            "unit_id",
            "family",
            "candidate_id",
            "structure_id",
            "design_group_id",
            "actual",
            "prediction",
            "invalid_reason",
            "null_reason",
            "source_mode",
            "label_source_file_id",
            "source_split_file_id",
            "source_split_sha256",
            "source_structure_file_id",
            "source_structure_sha256",
        }
    )
    return {
        "schema": SCHEMA_VERSION,
        "interface_version": INTERFACE_VERSION,
        "task_id": "T06",
        "status": "PASS",
        "o02_sha256": O02_SHA256,
        "o05": {
            "splits_sha256": O05_SPLITS_SHA256,
            "capacity_sha256": O05_CAPACITY_SHA256,
            "valid_units": 53,
            "invalid_units": 59,
            "row_identity": [
                "unit_id",
                "structure_id",
                "design_group_id",
                "scope",
                "fold",
                "role",
                "branch_id",
            ],
            "baseline_design_group_id": BASELINE_DESIGN_GROUP_ID,
            "baseline_role": "baseline_only",
            "baseline_usage": "final_difference_only",
        },
        "record_types": {
            "training_supervision": {
                "purpose": "M1 k2 or M2 frequency supervision only; never an inference input",
                "allowed_measured_fields": ["k2eff_pct", "fs_ghz", "fp_ghz"],
                "source_mode": "deembedded",
                "label_source_file_id": "P3_STRUCTURES",
            },
            "fit": {
                "role": "fit",
                "actual_output_allowed": False,
                "source_mode": "deembedded",
                "label_source_file_id": "P3_STRUCTURES",
            },
            "evaluation": {
                "role": "evaluation",
                "actual_output_allowed": True,
                "source_mode": "deembedded",
                "label_source_file_id": "P3_STRUCTURES",
            },
            "control": {
                "role": "evaluation",
                "actual_output_allowed": True,
                "same_o05_rows_required": True,
            },
            "baseline_difference": {
                "role": "baseline_only",
                "actual_output_allowed": True,
                "design_group_id": BASELINE_DESIGN_GROUP_ID,
                "inner_fit_or_evaluation_allowed": False,
            },
            "inference": {
                "role": "inference",
                "actual_output_allowed": False,
                "measured": False,
                "source_mode_allowed": [None, "unmeasured"],
                "design_group_null_reason_required_when_null": True,
            },
        },
        "fields": {
            "structure_id": {
                "type": "string|null",
                "required_for": ["evaluation", "control", "baseline_difference"],
                "inference_rule": "Required when candidate_id is null",
            },
            "candidate_id": {
                "type": "string|null",
                "required_for": ["inference when structure_id is null"],
            },
            "design_group_id": {
                "type": "string|null",
                "required_for": ["fit", "evaluation", "control", "baseline_difference"],
                "null_reason_field": "design_group_null_reason",
            },
            "scope": {"type": "string", "nullable": False},
            "fold": {"type": "string", "nullable": False, "o05_rule": "fold == unit_id"},
            "unit_id": {"type": "string", "nullable": False},
            "family": {
                "type": "string",
                "allowed": [
                    "ordinary",
                    "eg_upper",
                    "fl_lower",
                    "joint_eg_upper_fl_lower",
                ],
            },
            "role": {
                "type": "string",
                "allowed": ["fit", "evaluation", "inference", "baseline_only"],
            },
            "branch_id": {"type": "string", "allowed": list(KNOWN_BRANCHES)},
            "model_id": {"type": "string", "nullable": False},
            "config_id": {"type": "string", "nullable": False},
            "target": {"type": "string", "allowed": list(TARGET_UNITS)},
            "unit": {"type": "string", "mapping": TARGET_UNITS},
            "prediction": {
                "type": "number|null",
                "null_rule": (
                    "Required when valid_prediction=false; zero is never a null substitute"
                ),
            },
            "actual": {
                "type": "number|null",
                "allowed_only_for": ["evaluation", "control", "baseline_difference"],
            },
            "valid_prediction": {"type": "boolean", "nullable": False},
            "invalid_reason": {
                "type": "string|null",
                "required_when": "valid_prediction=false",
            },
            "support_label": {"type": "string", "nullable": False},
            "source_file_id": {"type": "string", "nullable": False},
            "source_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "source_split_file_id": {
                "type": "string",
                "required_for": ["fit", "evaluation", "control", "baseline_difference"],
            },
            "source_structure_file_id": {
                "type": "string",
                "required_for": ["fit", "evaluation", "control", "baseline_difference"],
            },
            "source_split_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "required_for": ["fit", "evaluation", "control", "baseline_difference"],
            },
            "source_structure_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "required_for": ["fit", "evaluation", "control", "baseline_difference"],
            },
            "protocol_sha256": {"type": "string", "const": O02_SHA256},
            "measured": {"type": "boolean", "inference_const": False},
            "trusted_for_unmeasured_optimization": {"type": "boolean", "const": False},
            "eg_um": {"type": "number", "unit": "um", "minimum": 0},
            "fl_um": {"type": "number", "unit": "um", "minimum": 0},
            "area_um2": {"type": "number", "unit": "um^2", "exclusiveMinimum": 0},
            "has_pf": {"type": "integer", "allowed": [0, 1]},
            "pred_fs_ghz": {"type": "number|null", "unit": "GHz"},
            "pred_fp_ghz": {"type": "number|null", "unit": "GHz"},
            "pred_frequency_ratio": {"type": "number|null", "range": "(0,1)"},
            "pred_k2eff_pct_native": {
                "type": "number|null",
                "unit": "percent_number",
            },
            "p3_comparable_prediction": {
                "type": "null",
                "null_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
            },
        },
        "unknown_fields": "reject",
        "nonfinite_json_numbers_allowed": False,
        "null_policy": {
            "zero_is_not_null": True,
            "prediction_null_requires": "valid_prediction=false and invalid_reason or null_reason",
            "non_applicable_fields_use_null_and_reason": True,
        },
        "geometry_contract": {
            "fields": list(GEOMETRY_FIELDS),
            "branch_derivation": "pf{has_pf}_eg{int(eg_um>0)}_fl{int(fl_um>0)}",
            "known_branches": list(KNOWN_BRANCHES),
            "finite_required": True,
            "negative_eg_or_fl_allowed": False,
            "nonpositive_area_allowed": False,
            "has_pf_allowed": [0, 1],
            "inference_forbidden_measured_fields": sorted(MEASURED_FIELDS),
        },
        "source_isolation": {
            "authoritative_label": "O01 P3 deembedded structure aggregates only",
            "label_source_file_id": "P3_STRUCTURES",
            "p3_structures_sha256": P3_STRUCTURES_SHA256,
            "raw_or_simulation_labels_allowed": False,
            "raw_or_simulation_fields_allowed": False,
            "inference_measured_frequency_allowed": False,
            "m2_supervision_inference_decoupled": True,
        },
        "model_interfaces": {
            "common": {
                "interface_version": INTERFACE_VERSION,
                "method": "predict({eg_um, fl_um, area_um2, has_pf}) -> mapping",
                "model_id_and_config_id_required": True,
                "unknown_or_measured_input_fields": "reject",
            },
            "M1": {
                "owner": "A2",
                "inference_input": list(GEOMETRY_FIELDS),
                "target": "k2eff_pct",
                "unit": "percent_number",
                "prediction_transform": m1["algorithm"]["target_transform"],
                "configuration_ids": [row["config_id"] for row in m1["configurations"]],
                "invalid_behavior": protocol["data_contract"]["invalid_behavior"]["m1"],
            },
            "M2": {
                "owner": "A3",
                "training_supervision": "frequency_supervision(fs_ghz, fp_ghz) -> a,z; fit-only",
                "inference_input": list(GEOMETRY_FIELDS),
                "latent_outputs": ["pred_a", "pred_z"],
                "frequency_outputs": ["pred_fs_ghz", "pred_fp_ghz", "pred_frequency_ratio"],
                "native_output": "pred_k2eff_pct_native and prediction, percent_number",
                "inverse_transform": m2["output_transform"],
                "configuration_ids": [
                    row["config_id"] for row in protocol["models"]["M2"]["configurations"]
                ],
                "invalid_behavior": protocol["data_contract"]["invalid_behavior"]["m2"],
                "frequency_record_consistency": {
                    "required_when_valid": "fs>0, fp>fs, 0<r<1, native k2 in (0,100)",
                    "ratio_consistency_atol": O02_FORMULA_CONSISTENCY_ATOL,
                    "valid_prediction_requires_frequency_and_native_valid": True,
                    "contradictory_flags": "reject",
                },
                "aggregation_estimand_match": False,
                "aggregation_mismatch_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
                "qualified": False,
                "qualified_model_set_eligible": False,
                "eligibility_override_allowed": False,
                "p3_comparable_prediction": None,
                "p3_comparable_null_reason": "P3_AGGREGATION_ESTIMAND_MISMATCH",
            },
            "P6C_control": {
                "recipe": control.recipe,
                "config_id": control.recipe,
                "helper": control.helper,
                "target": control.target,
                "fixed_details": control.fixed_details,
                "same_o05_rows_required": True,
                "old_outer_prediction_substitute_allowed": False,
                "recipe_reselection_allowed": False,
            },
        },
        "evaluation": {
            "error_unit": "percentage_points",
            "row_identity": [
                "unit_id",
                "structure_id",
                "design_group_id",
                "scope",
                "fold",
                "role",
                "branch_id",
            ],
            "unit_mae": (
                "Mean absolute structure error within each design group, then "
                "equal-weight mean of design-group means within the unit"
            ),
            "ordinary_mae": protocol["evaluation"]["ordinary_mae"],
            "boundary_mae": protocol["evaluation"]["boundary_mae"],
            "worst_unit_mae": protocol["evaluation"]["worst_unit_mae"],
            "missing_prediction": (
                "Retain row; mark unit and affected configuration FAILED; "
                "never delete or average around it"
            ),
            "same_evaluation_rows_required": True,
            "same_actual_labels_required": True,
            "complete_o05_rows_required": True,
            "required_evaluation_row_count": 2882,
            "source_hash_coverage_required_on_both_sides": True,
            "m2_native_target": (
                "Descriptive evaluation only; eligibility remains "
                "P3_AGGREGATION_ESTIMAND_MISMATCH"
            ),
            "acceptance": protocol["evaluation"]["acceptance"],
            "threshold_critical_rule": protocol["numerical_reproduction"][
                "threshold_critical_rule"
            ],
        },
        "numerical_reproduction": protocol["numerical_reproduction"],
        "artifact_contracts": {
            "O07": {
                "path": "m1/inner_predictions.csv",
                "record_kind": "evaluation",
                "model_family": "M1",
                "required_fields": common_fields,
                "model_config": "m1/model_config.json",
            },
            "O08": {
                "path": "m2/inner_predictions.csv",
                "record_kind": "evaluation",
                "model_family": "M2",
                "required_fields": sorted(
                    set(common_fields)
                    | {
                        "pred_a",
                        "pred_z",
                        "pred_fs_ghz",
                        "pred_fp_ghz",
                        "pred_frequency_ratio",
                        "pred_k2eff_pct_native",
                        "p3_comparable_prediction",
                        "p3_comparable_null_reason",
                        "frequency_valid",
                        "native_k2_valid",
                        "aggregation_estimand_match",
                        "qualified",
                    }
                ),
                "aggregation_audit": "m2/aggregation_audit.csv",
                "qualified_model_set_eligible": False,
            },
        },
    }
