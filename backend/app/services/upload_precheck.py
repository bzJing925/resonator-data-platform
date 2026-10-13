"""Deterministic upload checks. Reads inputs, never computes metrics or writes Device rows."""

import hashlib
import json
import math
import shutil
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import skrf

from app.config import get_settings
from app.core.deembed import DeembedError, DeembedMethod, is_calibration_filename
from app.core.filename import parse_filename
from app.core.mapping import load_mapping
from app.core.touchstone import detect_snp_type, split_s2p_to_s1p
from app.services.archive_validation import ArchiveValidationError, validate_archive_members
from app.workers.pipeline.calibration import CalibrationIndex


class PrecheckError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def file_digest(path: Path, *, checkpoint=None, progress_callback=None) -> str:
    digest = hashlib.sha256()
    size, completed = path.stat().st_size, 0
    if progress_callback:
        progress_callback(0, size)
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(get_settings().PRECHECK_HASH_CHUNK_BYTES), b""):
            if checkpoint:
                checkpoint()
            digest.update(chunk)
            completed += len(chunk)
            if progress_callback:
                progress_callback(completed, size)
    return digest.hexdigest()


def validate_upload_options(options: dict) -> dict:
    result = {
        "process_type": "AUTO",
        "deembed": False,
        "deembed_method": "default",
        "f_start_ghz": None,
        "f_end_ghz": None,
        **options,
    }
    if result["process_type"] not in {"AUTO", "BOTH", "S1P", "S2P"}:
        raise PrecheckError("INVALID_PROCESS_TYPE", "文件类型选项无效")
    if not isinstance(result["deembed"], bool):
        raise PrecheckError("INVALID_DEEMBED_OPTION", "去嵌选项必须为布尔值")
    try:
        DeembedMethod(result["deembed_method"])
    except ValueError as exc:
        raise PrecheckError("INVALID_DEEMBED_METHOD", "去嵌方法无效") from exc
    for field in ("f_start_ghz", "f_end_ghz"):
        value = result[field]
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise PrecheckError("INVALID_FREQUENCY_RANGE", "频率必须是有限正数")
            if not math.isfinite(value) or value <= 0:
                raise PrecheckError("INVALID_FREQUENCY_RANGE", "频率必须是有限正数")
    start, end = result["f_start_ghz"], result["f_end_ghz"]
    if start is not None and end is not None and start >= end:
        raise PrecheckError("INVALID_FREQUENCY_RANGE", "起始频率必须小于结束频率")
    return result


def issue(code, severity, message, relpath=None):
    return {"code": code, "severity": severity, "message": message, "relpath": relpath}


def inspect_archive(
    path: Path, directory: Path, options: dict, mapping: dict, checkpoint=None,
    progress_callback=None,
) -> dict:
    from app.workers.extract_batch import _extract_zip

    problems, items, calibration, duts = [], [], [], []

    def progress(stage, done, total, unit):
        if checkpoint:
            checkpoint()
        if progress_callback:
            progress_callback(stage, done, total, unit)

    try:
        members = validate_archive_members(
            path, directory,
            progress_callback=lambda done, total: progress(
                "validate_archive", done, total, "steps"
            ),
        )
        directory.mkdir(parents=True, exist_ok=False)
        _extract_zip(
            path, directory, cancel_check=checkpoint,
            validation_progress=lambda done, total: progress(
                "validate_extract", done, total, "steps"
            ),
            progress_callback=lambda done, total: progress(
                "extract", int(100 * done / total) if total else 0, 100, "percent"
            ),
        )
    except InterruptedError:
        raise
    except ArchiveValidationError as exc:
        return {"items": [], "issues": [issue(exc.code, "fatal", str(exc), exc.member)]}
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError):
        return {"items": [], "issues": [issue("INVALID_ARCHIVE", "fatal", "压缩包无法读取或解压")]}
    files = [member for member in members if not member.is_dir()]
    for index, member in enumerate(files):
        progress("inspect_files", index, len(files), "files")
        if checkpoint:
            checkpoint()
        if member.is_dir():
            continue
        relpath = member.filename
        actual = directory / relpath
        suffix = actual.suffix.lower()
        item = {
            "relpath": relpath,
            "classification": "ignored",
            "status": "skipped",
            "ports": [],
            "deembedded": False,
            "deembedding_source": "unknown",
        }
        items.append(item)
        if suffix not in {".s1p", ".s2p", ".snp"}:
            problems.append(issue("UNRELATED_FILE", "warning", "无关文件将排除", relpath))
            continue
        parsed = parse_filename(actual.name)
        is_calibration = is_calibration_filename(
            actual.name, DeembedMethod(options["deembed_method"])
        )
        item["classification"] = "calibration" if is_calibration else "dut"
        try:
            source_type = detect_snp_type(actual) if suffix == ".snp" else suffix[1:].upper()
            network_path = actual
            if suffix == ".snp":
                network_path = (
                    directory.parent
                    / (directory.name + "-checks")
                    / (relpath + "." + source_type.lower())
                )
                network_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(actual, network_path)
            net = skrf.Network(str(network_path))
            frequency = np.asarray(net.f, dtype=float) / 1e9
            if (
                len(frequency) < 2
                or not np.all(np.isfinite(frequency))
                or np.any(np.diff(frequency) <= 0)
                or not np.all(np.isfinite(net.s))
            ):
                raise ValueError("invalid network samples")
            item["ports"] = [parsed.port or "S11"] if net.nports == 1 else ["S11", "S22"]
            item["source_type"] = source_type
            item["input_digest"] = file_digest(actual)
            item["frequency_ghz"] = [float(frequency[0]), float(frequency[-1])]
            item["status"] = "success"
        except Exception:
            item["status"] = "failed"
            problems.append(
                issue("BAD_TOUCHSTONE", "warning", "Touchstone无法读取，需排除", relpath)
            )
            continue
        if is_calibration:
            if net.nports == 2:
                calibration.append(network_path)
            continue
        if options["process_type"] not in {"AUTO", "BOTH", source_type}:
            item["status"] = "skipped"
            problems.append(
                issue("PROCESS_TYPE_EXCLUDED", "warning", "文件类型被选项排除", relpath)
            )
            continue
        if parsed.mark not in mapping:
            problems.append(issue(
                "MAPPING_NOT_FOUND", "warning", "器件未命中对照表：入库但不参与分析", relpath
            ))
        start, end = options["f_start_ghz"], options["f_end_ghz"]
        if (
            (start is not None and not frequency[0] <= start <= frequency[-1])
            or (end is not None and not frequency[0] <= end <= frequency[-1])
            or np.count_nonzero(
                (frequency >= (start or frequency[0])) & (frequency <= (end or frequency[-1]))
            )
            < 2
        ):
            problems.append(
                issue("FREQUENCY_OUTSIDE_INPUT", "fatal", "频段与实际采样不匹配", relpath)
            )
        if options["deembed"] and net.nports == 1:
            problems.append(
                issue("S1P_DEEMBED_UNVERIFIED", "warning", "S1P去嵌来源未验证", relpath)
            )
        duts.append((network_path, item))
    progress("inspect_files", len(files), len(files), "files")
    if not duts:
        problems.append(issue("NO_PROCESSABLE_INPUT", "fatal", "没有可处理器件输入"))
    if options["deembed"] and any(len(item["ports"]) == 2 for _, item in duts):
        progress("calibration", 0, None, "files")
        try:
            index = CalibrationIndex.build(
                directory.parent / (directory.name + "-checks"),
                calibration,
                options["deembed_method"],
            )
            for completed, (actual, item) in enumerate(duts):
                progress("calibration", completed, len(duts), "files")
                if len(item["ports"]) != 2:
                    continue
                split = split_s2p_to_s1p(
                    actual,
                    directory.parent / (directory.name + "-checks") / "dut_S11",
                    directory.parent / (directory.name + "-checks") / "dut_S22",
                )
                for port, dut in (("S11", split.s11_path), ("S22", split.s22_path)):
                    index.match(port, dut)
                item["deembedding_source"] = "calibration_checked_not_applied"
            progress("calibration", len(duts), len(duts), "files")
        except InterruptedError:
            raise
        except (DeembedError, ValueError, OSError):
            problems.append(issue("MISSING_CALIBRATION", "fatal", "缺少匹配的去嵌校准件"))
    return {"items": items, "issues": problems}


def inspect_table(path: Path, options: dict, mapping: dict, checkpoint=None,
                  progress_callback=None) -> dict:
    from app.share_pack import rows_from_xlsx

    if progress_callback:
        progress_callback("read_table", 0, None, "rows")
    try:
        rows, failures, stats, entries, columns = rows_from_xlsx(path, False)
    except SystemExit:
        return {"items": [], "issues": [issue("TABLE_REQUIRED_COLUMNS", "fatal", "表格列契约无效")]}
    except Exception:
        return {"items": [], "issues": [issue("INVALID_TABLE", "fatal", "表格无法读取")]}
    problems = [issue("TABLE_ROW_REJECTED", "warning", failure) for failure in failures]
    items = []
    for index, row in enumerate(rows, 1):
        if checkpoint:
            checkpoint()
        if progress_callback:
            progress_callback("inspect_rows", index - 1, len(rows), "rows")
        item = {
            "relpath": f"row/{index}",
            "original_filename": row["original_filename"],
            "classification": "dut",
            "status": "success",
            "ports": [row.get("s_param_port") or "S11"],
            "deembedded": False,
            "deembedding_source": "unknown",
        }
        items.append(item)
        if row.get("mark") not in mapping:
            problems.append(
                issue(
                    "MAPPING_NOT_FOUND", "warning",
                    "器件未命中对照表：入库但不参与分析", item["relpath"],
                )
            )
    if progress_callback:
        progress_callback("inspect_rows", len(rows), len(rows), "rows")
    if not rows:
        problems.append(issue("NO_PROCESSABLE_INPUT", "fatal", "没有可处理器件输入"))
    if options["deembed"]:
        problems.append(issue("TABLE_DEEMBED_UNVERIFIED", "warning", "表格去嵌来源未验证"))
    return {
        "items": items,
        "issues": problems,
        "row_failures": failures,
        "table_stats": stats,
        "column_report": columns,
        "_table_rows": rows,
    }


def calculate_check_digest(
    input_digest: str, options: dict, mapping_digest: str, target_revision: dict | None
) -> str:
    payload = {
        "input": input_digest,
        "options": options,
        "mapping": mapping_digest,
        "target_revision": target_revision,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_precheck_report(
    path: Path,
    directory: Path,
    options: dict,
    mapping_path: Path,
    *,
    target_revision: dict | None = None,
    checkpoint=None,
    progress_callback=None,
) -> dict:
    if checkpoint:
        checkpoint()
    try:
        normalized = validate_upload_options(options)
    except PrecheckError as exc:
        return {
            "schema_version": 1,
            "items": [],
            "issues": [issue(exc.code, "fatal", str(exc))],
            "fatal_count": 1,
            "warning_codes": [],
            "check_digest": None,
            "quality_status": "invalid",
        }
    if path.stat().st_size > get_settings().UPLOAD_MAX_GB * 1024**3:
        return {
            "schema_version": 1,
            "items": [],
            "issues": [issue("UPLOAD_TOO_LARGE", "fatal", "上传超过容量限制")],
            "fatal_count": 1,
            "warning_codes": [],
            "check_digest": None,
            "quality_status": "invalid",
        }
    def hash_input(stage):
        return file_digest(
            path, checkpoint=checkpoint,
            progress_callback=(lambda done, total: progress_callback(stage, done, total, "bytes"))
            if progress_callback else None,
        )

    input_digest = hash_input("hash_input")
    mapping_digest = file_digest(mapping_path) if mapping_path.is_file() else "missing"
    try:
        mapping = load_mapping(mapping_path)
    except Exception:
        mapping = {}
    if path.stat().st_size > get_settings().UPLOAD_MAX_GB * 1024**3:
        inspection = {
            "items": [],
            "issues": [issue("UPLOAD_TOO_LARGE", "fatal", "上传超过容量限制")],
        }
    elif path.suffix.lower() == ".zip":
        inspection = inspect_archive(
            path, directory, normalized, mapping, checkpoint, progress_callback
        )
    elif path.suffix.lower() in {".xlsx", ".xls", ".csv"}:
        inspection = inspect_table(path, normalized, mapping, checkpoint, progress_callback)
        directory.mkdir(parents=True, exist_ok=False)
        rows = inspection.pop("_table_rows", [])
        (directory / "table-rows.json").write_text(
            json.dumps(rows, ensure_ascii=False, allow_nan=False), encoding="utf-8"
        )
    elif path.suffix.lower() in {".s1p", ".s2p", ".snp"}:
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory.parent) as temporary:
            archive = Path(temporary) / "single-input.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.write(path, arcname=path.name)
            inspection = inspect_archive(
                archive, directory, normalized, mapping, checkpoint, progress_callback
            )
    else:
        inspection = {"items": [], "issues": [issue("INVALID_FORMAT", "fatal", "上传格式不支持")]}
    if checkpoint:
        checkpoint()
    if (
        hash_input("verify_input") != input_digest
        or (file_digest(mapping_path) if mapping_path.is_file() else "missing") != mapping_digest
    ):
        inspection["issues"].append(issue("PRECHECK_STALE", "fatal", "检查期间输入或对照表变化"))
    fatal = sum(i["severity"] == "fatal" for i in inspection["issues"])
    warnings = sorted({i["code"] for i in inspection["issues"] if i["severity"] == "warning"})
    return {
        "schema_version": 1,
        **inspection,
        "input_name": path.name,
        "input_digest": input_digest,
        "mapping_digest": mapping_digest,
        "options": normalized,
        "target_revision": target_revision,
        "fatal_count": fatal,
        "warning_codes": warnings,
        "check_digest": calculate_check_digest(
            input_digest, normalized, mapping_digest, target_revision
        ),
        "quality_status": "invalid" if fatal else "partial" if warnings else "complete",
    }
