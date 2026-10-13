"""Temporary real inputs verify deterministic precheck without metric computation."""

import json
import stat
import zipfile
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import func, select

from app.models import Device
from app.services.archive_validation import ArchiveValidationError
from app.services.upload_precheck import (
    PrecheckError,
    build_precheck_report,
    validate_upload_options,
)
from tests.fixtures.platform_cases import _algorithm_samples, _write_archive, make_platform_archives
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture
def mapping_file(tmp_path):
    path = tmp_path / "mapping.csv"
    path.write_text("E6-1,EG0 FL0 700&5500\nE6-2,EG0 FL0 700&5500\nE6-3,EG0 FL0 700&5500\n")
    return path


@pytest.fixture(autouse=True)
def no_metrics(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Precheck must not compute resonator metrics")

    monkeypatch.setattr("app.core.extract.extract_resonator_params", forbidden)
    monkeypatch.setattr("app.workers.extract_batch._find_7z", lambda: None)


def codes(report):
    return {i["code"] for i in report["issues"]}


def check(path, tmp_path, mapping_file, options=None, **kwargs):
    return build_precheck_report(
        path, tmp_path / "inspection", options or {}, mapping_file, **kwargs
    )


def test_complete_archive_separates_file_and_port_identity(tmp_path, mapping_file):
    archive = make_platform_archives(tmp_path)["complete"]
    report = check(archive, tmp_path, mapping_file)
    assert report["fatal_count"] == 0
    duts = [item for item in report["items"] if item["classification"] == "dut"]
    assert len(duts) == 3 and sum(len(item["ports"]) for item in duts) == 5
    assert "UNRELATED_FILE" in codes(report)
    assert all(item["deembedded"] is False for item in duts)
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.parametrize(
    "case,code",
    [
        ("empty", "NO_PROCESSABLE_INPUT"),
        ("duplicate_member", "DUPLICATE_ARCHIVE_MEMBER"),
        ("corrupt_touchstone", "BAD_TOUCHSTONE"),
        ("missing_calibration", "MISSING_CALIBRATION"),
        ("partial_failure", "BAD_TOUCHSTONE"),
    ],
)
def test_archive_problem_cases(case, code, tmp_path, mapping_file):
    archives = make_platform_archives(tmp_path)
    if case == "empty":
        archive = _write_archive(tmp_path / "empty.zip", [])
    else:
        archive = archives[case]
    report = check(archive, tmp_path, mapping_file, {"deembed": case == "missing_calibration"})
    assert code in codes(report)
    if case == "partial_failure":
        assert report["fatal_count"] == 0 and report["quality_status"] == "partial"


@pytest.mark.parametrize(
    "name", ["../escape.s1p", "/absolute.s1p", "a\\escape.s1p", "a/../escape.s1p", "C:/escape.s1p"]
)
def test_unsafe_members_are_rejected_before_extraction(name, tmp_path, mapping_file):
    archive = _write_archive(tmp_path / "unsafe.zip", [(name, b"bad")])
    report = check(archive, tmp_path, mapping_file)
    assert "UNSAFE_ARCHIVE_PATH" in codes(report)
    assert not (tmp_path / "inspection").exists()
    from app.workers.extract_batch import _extract_zip

    with pytest.raises(ArchiveValidationError):
        _extract_zip(archive, tmp_path / "old-worker")
    assert not (tmp_path / "old-worker").exists()


def test_archive_symlink_and_expanded_capacity(tmp_path, mapping_file, monkeypatch):
    archive = tmp_path / "symlink.zip"
    link = zipfile.ZipInfo("link.s1p")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr(link, "../outside")
    assert "UNSAFE_ARCHIVE_PATH" in codes(check(archive, tmp_path, mapping_file))
    archive = _write_archive(tmp_path / "large.zip", [("dut.s1p", b"x" * 10000)])
    monkeypatch.setattr(
        "app.services.archive_validation.get_settings",
        lambda: SimpleNamespace(UPLOAD_MAX_GB=0.000001),
    )
    assert "EXTRACT_TOO_LARGE" in codes(check(archive, tmp_path, mapping_file))


@pytest.mark.parametrize(
    "options",
    [
        {"f_start_ghz": float("nan")},
        {"f_end_ghz": float("inf")},
        {"f_start_ghz": 3, "f_end_ghz": 2},
        {"f_start_ghz": -1},
        {"process_type": "wrong"},
        {"deembed_method": "wrong"},
        {"deembed": "true"},
    ],
)
def test_invalid_options_are_fatal(options):
    with pytest.raises(PrecheckError):
        validate_upload_options(options)


def test_real_frequency_mapping_and_s1p_provenance(tmp_path, mapping_file):
    samples = _algorithm_samples(tmp_path)
    source = tmp_path / "17_E6-1_X0Y0.s1p"
    source.write_bytes(samples["dut_s1p"])
    report = check(source, tmp_path, mapping_file, {"deembed": True, "f_end_ghz": 1000})
    assert {"S1P_DEEMBED_UNVERIFIED", "FREQUENCY_OUTSIDE_INPUT"} <= codes(report)
    assert report["items"][0]["deembedded"] is False
    mapping_file.write_text("OTHER,EG0 FL0 1&2\n")
    report = build_precheck_report(source, tmp_path / "inspection2", {}, mapping_file)
    assert "MAPPING_NOT_FOUND" in codes(report)


def test_existing_calibration_matcher_and_digest_changes(tmp_path, mapping_file):
    archive = make_platform_archives(tmp_path)["complete"]
    first = check(archive, tmp_path, mapping_file, {"deembed": True})
    assert "MISSING_CALIBRATION" not in codes(first) and first["fatal_count"] == 0
    second = build_precheck_report(
        archive, tmp_path / "inspection2", {"deembed": True}, mapping_file
    )
    assert first["check_digest"] == second["check_digest"]
    mapping_file.write_text(mapping_file.read_text() + "E6-4,EG1 FL1 1&2\n")
    third = build_precheck_report(
        archive, tmp_path / "inspection3", {"deembed": True}, mapping_file
    )
    assert first["check_digest"] != third["check_digest"]
    fourth = build_precheck_report(
        archive,
        tmp_path / "inspection4",
        {"deembed": True},
        mapping_file,
        target_revision={"source_revision": 2, "result_revision": 1},
    )
    assert third["check_digest"] != fourth["check_digest"]


def table_frame():
    row = {
        "original_filename": "17_E6-1_X0Y0.s1p",
        "display_name": "DUT",
        "coord": "X0Y0",
        "EG": 0,
        "FL": 0,
        "AG": 0,
        "PF": "N",
        "Area(um2)": 700,
        "fs(GHz)": 1,
        "fp(GHz)": 2,
        "Zs(Ω)": 3,
        "Zp(Ω)": 4,
        "Qs": 5,
        "Qp": 6,
        "Qs_BodeQ": 7,
        "Qp_BodeQ": 8,
        "k2eff(%)": 9,
    }
    return pd.DataFrame([row, {**row, "original_filename": None}])


def test_table_partial_failures_and_required_columns(tmp_path, mapping_file):
    table = tmp_path / "table.xlsx"
    table_frame().to_excel(table, index=False)
    report = check(table, tmp_path, mapping_file)
    assert report["fatal_count"] == 0 and report["quality_status"] == "partial"
    assert len(report["row_failures"]) == 1 and "TABLE_ROW_REJECTED" in codes(report)
    table_frame().drop(columns=["Qs"]).to_excel(table, index=False)
    assert "TABLE_REQUIRED_COLUMNS" in codes(
        build_precheck_report(table, tmp_path / "table-inspection2", {}, mapping_file)
    )


@pytest.mark.parametrize("kind", DATABASES)
def test_precheck_never_writes_active_devices(kind, tmp_path, mapping_file):
    with platform_database(kind, tmp_path) as (db, _batch):
        archive = make_platform_archives(tmp_path)["complete"]
        before = db.scalar(select(func.count(Device.id)))
        check(archive, tmp_path, mapping_file)
        db.expire_all()
        assert db.scalar(select(func.count(Device.id))) == before == 0


def test_streaming_extractor_checks_paths_before_spawning(tmp_path):
    from app.workers.pipeline.extractor import StreamingExtractor

    archive = _write_archive(tmp_path / "unsafe.zip", [("../outside", b"bad")])
    extractor = StreamingExtractor(archive, tmp_path / "streaming")
    with pytest.raises(ArchiveValidationError):
        list(extractor.extract())
    assert extractor._proc is None and not (tmp_path / "streaming").exists()


def test_basic_uses_existing_wo_ws_classifier(tmp_path, mapping_file):
    samples = _algorithm_samples(tmp_path)
    archive = _write_archive(
        tmp_path / "basic.zip",
        [
            ("17_E6-1_X0Y0.s2p", samples["dut_s2p_a"]),
            ("WO.s2p", samples["cal_open"]),
            ("WS.s2p", samples["cal_short"]),
        ],
    )
    report = check(archive, tmp_path, mapping_file, {"deembed_method": "basic"})
    assert [item["classification"] for item in report["items"]] == [
        "dut",
        "calibration",
        "calibration",
    ]
    assert "MAPPING_NOT_FOUND" not in codes(report)


def test_hash_reports_actual_bytes_and_can_stop_between_chunks(tmp_path, monkeypatch):
    import hashlib

    from app.config import get_settings
    from app.services.upload_precheck import file_digest

    monkeypatch.setattr(get_settings(), "PRECHECK_HASH_CHUNK_BYTES", 1024)
    path = tmp_path / "large.bin"
    payload = b"a" * 5120 + b"b" * 11
    path.write_bytes(payload)
    events = []
    result = file_digest(path, progress_callback=lambda done, total: events.append((done, total)))
    assert result == hashlib.sha256(payload).hexdigest()
    assert events[:3] == [(0, len(payload)), (1024, len(payload)), (2048, len(payload))]
    assert events[-1] == (len(payload), len(payload))
    events.clear()

    def stop():
        if len(events) >= 3:
            raise InterruptedError("stop")

    with pytest.raises(InterruptedError):
        file_digest(path, checkpoint=stop,
                    progress_callback=lambda done, total: events.append((done, total)))
    assert events[-1][0] == 2048


def test_real_archive_progress_covers_phases_without_changing_report(tmp_path, mapping_file):
    archive = make_platform_archives(tmp_path)["complete"]
    events = []
    report = check(archive, tmp_path, mapping_file,
                   progress_callback=lambda *event: events.append(event))
    stages = list(dict.fromkeys(event[0] for event in events))
    assert stages == ["hash_input", "validate_archive", "validate_extract", "extract",
                      "inspect_files", "verify_input"]
    files = [event for event in events if event[0] == "inspect_files"]
    assert files[0][1] == 0 and files[-1][1] == files[-1][2]
    assert files[-1][1] == len(report["items"])
    without = build_precheck_report(archive, tmp_path / "another", {}, mapping_file)
    assert report == without


def test_cancel_during_archive_validation_does_not_extract(tmp_path, mapping_file):
    archive = make_platform_archives(tmp_path)["complete"]

    def progress(stage, done, total, unit):
        if stage == "validate_archive" and done > 0:
            raise InterruptedError("stop before extraction")

    with pytest.raises(InterruptedError):
        check(archive, tmp_path, mapping_file, progress_callback=progress)
    assert not (tmp_path / "inspection").exists()


def test_unmatched_mapping_is_warning_not_fatal(tmp_path, mapping_file):
    samples = _algorithm_samples(tmp_path)
    source = tmp_path / "17_E9-9_X0Y0.s1p"
    source.write_bytes(samples["dut_s1p"])
    report = check(source, tmp_path, mapping_file)
    unmatched = [i for i in report["issues"] if i["code"] == "MAPPING_NOT_FOUND"]
    assert unmatched and all(i["severity"] == "warning" for i in unmatched)
    assert report["fatal_count"] == 0 and report["quality_status"] == "partial"


def test_table_unmatched_mapping_is_warning_not_fatal(tmp_path, mapping_file):
    table = tmp_path / "table.xlsx"
    frame = table_frame()
    frame.loc[0, "original_filename"] = "17_E9-9_X0Y0.s1p"
    frame.to_excel(table, index=False)
    report = check(table, tmp_path, mapping_file)
    unmatched = [i for i in report["issues"] if i["code"] == "MAPPING_NOT_FOUND"]
    assert unmatched and all(i["severity"] == "warning" for i in unmatched)
    assert report["fatal_count"] == 0
