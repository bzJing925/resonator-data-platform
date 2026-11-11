import csv
import gzip
import json

import pytest

from app.ml.device_median_v1 import (
    aggregate_measurements,
    build_aggregation_differences,
    classify_source_value,
    join_primary_measurements,
    median,
    text_sha256,
)
from scripts.run_device_median_v1 import main, recheck_input_hashes, snapshot_inputs


def make_measurement(
    device_key,
    source_row_id,
    values,
    *,
    structure_key="structure",
    mode="deembedded",
    audit_flag="",
    invalid=(),
    coord="",
):
    return {
        "device_key": device_key,
        "structure_key": structure_key,
        "source_row_id": source_row_id,
        "mode": mode,
        "coord": coord,
        "audit_flag": audit_flag,
        "values": values,
        "validity": {
            parameter: {
                "valid": value is not None and parameter not in invalid,
                "reason": (
                    "missing"
                    if value is None
                    else "invalid_for_test"
                    if parameter in invalid
                    else ""
                ),
            }
            for parameter, value in values.items()
        },
    }


def target(rows, structure_key, parameter):
    return next(
        row
        for row in rows
        if row["structure_key"] == structure_key and row["parameter"] == parameter
    )


def test_median_odd_and_even_samples():
    assert median([1, 2, 100]) == 2
    assert median([1, 2, 3, 100]) == 2.5


def test_equal_weight_devices_not_uneven_coordinate_blocks():
    rows = [
        make_measurement(f"A{index}", f"row-{index}", {"qp": value}, coord="A")
        for index, value in enumerate([1, 2, 3], 1)
    ]
    rows.append(make_measurement("B1", "row-4", {"qp": 100}, coord="B"))

    result = aggregate_measurements(rows, ["qp"])
    qp = target(result["primary"], "structure", "qp")

    assert qp["median"] == 2.5
    assert qp["median"] != 51
    assert qp["n_total"] == qp["n_devices_total"] == 4
    assert qp["n_valid"] == 4
    assert qp["source_device_keys"] == ["A1", "A2", "A3", "B1"]


def test_missing_values_are_independent_per_parameter():
    rows = [
        make_measurement("A", "row-a", {"qp": 1, "qs": None}),
        make_measurement("B", "row-b", {"qp": 3, "qs": 5}),
        make_measurement("C", "row-c", {"qp": None, "qs": 7}),
    ]

    result = aggregate_measurements(rows, ["qp", "qs"])
    qp = target(result["primary"], "structure", "qp")
    qs = target(result["primary"], "structure", "qs")

    assert (qp["median"], qp["n_valid"], qp["n_missing"]) == (2, 2, 1)
    assert (qs["median"], qs["n_valid"], qs["n_missing"]) == (6, 2, 1)
    assert qp["n_total"] == qp["n_valid"] + qp["n_missing"] + qp["n_invalid"]
    assert qs["n_total"] == qs["n_valid"] + qs["n_missing"] + qs["n_invalid"]


def test_all_missing_parameter_is_null_without_dropping_other_parameters():
    rows = [
        make_measurement("A", "row-a", {"qp": None, "qs": 5}),
        make_measurement("B", "row-b", {"qp": None, "qs": 7}),
    ]

    result = aggregate_measurements(rows, ["qp", "qs"])
    qp = target(result["primary"], "structure", "qp")

    for field in ("median", "q25", "q75", "min", "max"):
        assert qp[field] is None
    assert qp["source_device_keys"] == []
    assert qp["n_valid"] == 0
    assert qp["n_missing"] == 2
    assert target(result["primary"], "structure", "qs")["median"] == 6


def test_valid_zero_is_preserved():
    result = aggregate_measurements(
        [make_measurement("A", "row-a", {"custom": 0})], ["custom"]
    )
    row = result["primary"][0]

    assert row["median"] == row["min"] == row["max"] == 0
    assert row["n_valid"] == 1
    assert row["n_missing"] == 0
    assert row["source_device_keys"] == ["A"]


def test_true_repeats_take_device_median_before_structure_median():
    rows = [
        make_measurement("A", "row-a1", {"qp": 1}),
        make_measurement("A", "row-a2", {"qp": 100}),
        make_measurement("B", "row-b", {"qp": 3}),
    ]

    result = aggregate_measurements(rows, ["qp"])
    qp = result["primary"][0]
    a_rows = [row for row in result["device_ledger"] if row["device_key"] == "A"]

    assert qp["median"] == 26.75  # median([50.5, 3])
    assert qp["n_total"] == 2
    assert all(row["device_value"] == 50.5 for row in a_rows)
    assert all(row["n_measurements"] == 2 for row in a_rows)


def test_low_support_and_invalid_counts_are_per_parameter():
    rows = [
        make_measurement("A", "row-a", {"qp": -1}, invalid=("qp",)),
        make_measurement("B", "row-b", {"qp": None}),
        make_measurement("C", "row-c", {"qp": 3}),
    ]
    row = aggregate_measurements(rows, ["qp"])["primary"][0]

    assert row["n_total"] == 3
    assert (row["n_valid"], row["n_missing"], row["n_invalid"]) == (1, 1, 1)
    assert row["low_support"] is True
    assert row["source_device_keys"] == ["C"]


def source_record(row_id, *, qp=1, qs=1, mode="deembedded", port="S11"):
    return {
        "row_id": row_id,
        "mode": mode,
        "original_filename": f"{port}_DUT_Fail.s1p",
        "device_id": f"{port}_DUT_Fail.s1p",
        "source_path": "workbook.xlsx",
        "source_sheet": "Sheet1",
        "excel_row": row_id.split(":")[-1],
        "port": port,
        "coord": "X0Y0",
        "filename_has_fail": "True",
        "frequency_valid": "True",
        "qp": str(qp) if qp is not None else "",
        "qs": str(qs) if qs is not None else "",
        "valid_qp": str(qp is not None and qp > 0),
        "valid_qs": str(qs is not None and qs > 0),
    }


def identity_record(
    record,
    *,
    device_key=None,
    structure_key="structure",
    canonical=True,
    eligible=True,
    alias_of=None,
    flagged=False,
    ag_status="unrecorded",
    topology_status="standard_mapping",
):
    row_id = record["row_id"]
    return {
        "source": {"row_id": row_id, "source_row_sha256": text_sha256(record)},
        "device_key": device_key or f"device-{row_id}",
        "physical_device_key": device_key or f"device-{row_id}",
        "structure_key": structure_key,
        "mode": record["mode"],
        "batch": "#2",
        "port": record["port"],
        "is_canonical_measurement": canonical,
        "canonical_source_row_id": alias_of or row_id,
        "alias_of_source_row_id": alias_of,
        "fl0_review": {"flagged": flagged},
        "structure_association": {
            "p3_structure_id": f"p3-{structure_key}",
            "p3_branch_id": "branch",
            "p3_design_group_id": "group",
            "geometry": {"eg_um": 0, "fl_um": 1, "area_um2": 700, "has_pf": False},
            "ag": {"status": ag_status},
            "topology": {"status": topology_status},
            "cohort": {
                "geometry_eligible": eligible,
                "id": "standard_eg_fl_unrecorded_ag" if eligible else "unresolved",
                "exclusion_reasons": [] if eligible else ["unresolved_cohort"],
            },
        },
    }


def test_exact_duplicate_alias_is_counted_once_and_preserved_in_exclusions():
    first = source_record("deembedded:1", qp=2)
    alias = source_record("deembedded:2", qp=2)
    identity = [
        identity_record(first, device_key="A"),
        identity_record(
            alias, device_key="A", canonical=False, alias_of="deembedded:1"
        ),
    ]

    joined = join_primary_measurements([first, alias], identity, [], ["qp"])
    result = aggregate_measurements(joined["measurements"], ["qp"])

    assert len(joined["measurements"]) == 1
    assert result["primary"][0]["n_total"] == 1
    assert result["primary"][0]["median"] == 2
    assert joined["exclusions"][0]["source_row_id"] == "deembedded:2"
    assert "exact_duplicate_import" in joined["exclusions"][0]["reasons"]
    assert joined["exclusions"][0]["alias_of_source_row_id"] == "deembedded:1"


def test_ports_raw_de_ag_and_topology_never_implicitly_merge():
    s11 = source_record("deembedded:1", qp=2, port="S11")
    s22 = source_record("deembedded:2", qp=100, port="S22")
    raw = source_record("raw:1", qp=1000, mode="raw", port="S11")
    records = [s11, s22, raw]
    identity = [
        identity_record(s11, device_key="same-dut-S11", structure_key="de-ag-unknown"),
        identity_record(
            s22,
            device_key="same-dut-S22",
            structure_key="de-ag-explicit-topology-other",
            ag_status="mapping_explicit",
            topology_status="other_known_topology",
        ),
        identity_record(raw, device_key="same-dut-S11-raw", structure_key="de-ag-unknown"),
    ]

    joined = join_primary_measurements(records, identity, [], ["qp"])
    result = aggregate_measurements(joined["measurements"], ["qp"])

    assert len(joined["measurements"]) == 2
    assert len(result["primary"]) == 2
    assert target(result["primary"], "de-ag-unknown", "qp")["median"] == 2
    assert target(result["primary"], "de-ag-explicit-topology-other", "qp")["median"] == 100
    assert joined["exclusions"][0]["source_row_id"] == "raw:1"
    assert "raw_control_only" in joined["exclusions"][0]["reasons"]


def test_shuffle_preserves_labels_and_source_membership():
    records = [source_record(f"deembedded:{index}", qp=index) for index in range(1, 5)]
    identity = [
        identity_record(record, device_key=f"D{index}")
        for index, record in enumerate(records, 1)
    ]

    first = aggregate_measurements(
        join_primary_measurements(records, identity, [], ["qp"])["measurements"], ["qp"]
    )
    shuffled = aggregate_measurements(
        join_primary_measurements(
            list(reversed(records)), list(reversed(identity)), [], ["qp"]
        )["measurements"],
        ["qp"],
    )

    assert first == shuffled


def test_all_59_fl0_review_rows_are_primary_and_only_qp_is_sensitivity_masked():
    records = [source_record(f"deembedded:{index}", qp=index, qs=index) for index in range(1, 60)]
    identity = [
        identity_record(record, device_key=f"D{index}", flagged=True)
        for index, record in enumerate(records, 1)
    ]
    audit = [
        {
            **record,
            "decision": "review",
            "reasons": "phase_derivative_unstable",
        }
        for record in records
    ]

    joined = join_primary_measurements(records, identity, audit, ["qp", "qs"])
    result = aggregate_measurements(joined["measurements"], ["qp", "qs"])
    primary_qp = target(result["primary"], "structure", "qp")
    sensitivity_qp = target(result["sensitivity"], "structure", "qp")

    assert len(joined["measurements"]) == 59
    assert primary_qp["median"] == 30
    assert sensitivity_qp["median"] is None
    assert sensitivity_qp["n_missing"] == 59
    assert target(result["primary"], "structure", "qs") == target(
        result["sensitivity"], "structure", "qs"
    )
    qp_ledger = [row for row in result["device_ledger"] if row["parameter"] == "qp"]
    assert len(qp_ledger) == 59
    assert all(row["valid"] and row["audit_flag"] == "FL0_QP_REVIEW" for row in qp_ledger)
    assert all(
        not row["sensitivity_valid"]
        and row["sensitivity_reason"] == "fl0_qp_review_masked"
        for row in qp_ledger
    )
    assert all(
        row["audit_flag"] == "FL0_QP_REVIEW" and row["sensitivity_valid"]
        for row in result["device_ledger"]
        if row["parameter"] == "qs"
    )


def test_p3_source_validity_rejects_nonpositive_qp_without_truthiness_drops():
    zero = classify_source_value(source_record("deembedded:1", qp=0), "qp")
    missing = classify_source_value(source_record("deembedded:1", qp=None), "qp")

    assert zero["value"] == 0
    assert zero["valid"] is False
    assert "non_positive" in zero["reason"]
    assert missing == {"value": None, "valid": False, "reason": "missing"}


def test_old_coordinate_labels_are_comparison_only():
    measurements = [
        make_measurement(f"A{index}", f"row-{index}", {"qp": value}, coord="A")
        for index, value in enumerate([1, 2, 3], 1)
    ]
    measurements.append(make_measurement("B1", "row-4", {"qp": 100}, coord="B"))
    for row in measurements:
        row["p3_structure_id"] = "old-structure"
    primary = aggregate_measurements(measurements, ["qp"])["primary"]
    original = [dict(row) for row in primary]

    differences = build_aggregation_differences(
        primary,
        primary,
        [{"structure_id": "old-structure", "qp": "51", "qp_n": "4", "qp_n_coords": "2"}],
    )
    old_comparison = next(
        row for row in differences if row["comparison"] == "primary_vs_old_coordinate"
    )

    assert primary == original
    assert old_comparison["reference_value"] == 51
    assert old_comparison["candidate_value"] == 2.5
    assert old_comparison["delta"] == -48.5
    assert old_comparison["changed"] is True


def write_csv(path, rows, fieldnames=None):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def test_cli_build_and_nonempty_output_guard(tmp_path):
    records = [
        source_record("deembedded:1", qp=1, qs=2),
        source_record("deembedded:2", qp=3, qs=4),
    ]
    records_path = tmp_path / "records.csv.gz"
    with gzip.open(records_path, "wt", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    identity = {
        "identity_status": "RESOLVED",
        "counts": {"unresolved_identity_conflicts": 0},
        "record_count": 2,
        "records": [
            identity_record(record, device_key=f"D{index}")
            for index, record in enumerate(records, 1)
        ],
    }
    identity_path = tmp_path / "identity.json"
    identity_path.write_text(json.dumps(identity), encoding="utf-8")
    audit_path = tmp_path / "record_audit.csv"
    write_csv(audit_path, [{"row_id": "unused", "decision": "curve_supported", "reasons": ""}])
    write_csv(
        tmp_path / "structures.csv",
        [
            {
                "structure_id": "p3-structure",
                "qp": 51,
                "qs": 51,
                "qp_n": 2,
                "qs_n": 2,
                "qp_n_coords": 1,
                "qs_n_coords": 1,
            }
        ],
    )
    protocol_path = tmp_path / "data_protocol.json"
    protocol_path.write_text(
        json.dumps({"parameters": ["qp", "qs"], "low_support_threshold": 5}),
        encoding="utf-8",
    )
    out = tmp_path / "data"

    exit_code = main(
        [
            "build",
            "--records",
            str(records_path),
            "--identity",
            str(identity_path),
            "--audit",
            str(audit_path),
            "--protocol",
            str(protocol_path),
            "--out",
            str(out),
        ]
    )

    assert exit_code == 0
    with (out / "structure_targets_primary.csv").open(newline="") as handle:
        targets = list(csv.DictReader(handle))
    qp = next(row for row in targets if row["parameter"] == "qp")
    assert qp["median"] == "2.0"
    assert qp["n_total"] == "2"
    assert qp["low_support"] == "True"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["counts"]["primary_devices"] == 2
    assert manifest["structures_csv_used_as_device_source"] is False
    assert manifest["inputs_unchanged_before_after"] is True
    assert len(manifest["outputs"]) == 6

    with pytest.raises(FileExistsError, match="non-empty"):
        main(
            [
                "build",
                "--records",
                str(records_path),
                "--identity",
                str(identity_path),
                "--audit",
                str(audit_path),
                "--protocol",
                str(protocol_path),
                "--out",
                str(out),
            ]
        )


def test_input_hash_recheck_detects_mutation(tmp_path):
    path = tmp_path / "records.csv.gz"
    path.write_bytes(b"before")
    before = snapshot_inputs({"records": path})
    path.write_bytes(b"after")

    with pytest.raises(RuntimeError, match="Input hash changed"):
        recheck_input_hashes(before)
