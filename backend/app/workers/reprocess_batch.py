"""批次重处理任务：重新解压 / 重新去嵌 / 重新计算指定指标。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deembed import DeembedError, DeembedMethod, _run_deembed
from app.core.extract import ExtractError, extract_resonator_params
from app.core.filename import parse_filename
from app.core.mapping import load_mapping
from app.core.touchstone import split_s2p_to_s1p
from app.db import SessionLocal
from app.models import Batch, Device, Mapping, ProcessingRun
from app.workers.cancel import raise_if_cancelled
from app.workers.celery_app import celery_app
from app.workers.compute_batch import compute_batch_task
from app.workers.progress import ProgressPublisher
from app.workers.run_execution import current_run, leased_stage

logger = logging.getLogger(__name__)

_METRIC_COLUMNS: dict[str, list[str]] = {
    "qs": ["qs"],
    "qp": ["qp"],
    "kt2": ["k2eff_pct"],
    "qbode": [
        "qs_bodeq",
        "qp_bodeq",
        "dbqs",
        "dbqp",
        "bodeq_fitted",
        "bodeq_smooth",
        "bodeq_raw",
        "fbode_ghz",
    ],
}


def _validate_metrics(metrics: list[str]) -> list[str]:
    invalid = [m for m in metrics if m not in _METRIC_COLUMNS]
    if invalid:
        raise ValueError(f"不支持的指标: {invalid}，可选: {list(_METRIC_COLUMNS)}")
    return metrics


def _prepare_deembed_files(
    target_dir: Path,
) -> tuple[list[Path], dict[str, Path], dict[str, Path]]:
    s2p_files = sorted(p for p in target_dir.rglob("*.s2p") if p.is_file())
    dut_s2p: list[Path] = []
    cal_open: dict[str, Path] = {}
    cal_short: dict[str, Path] = {}
    for p in s2p_files:
        parsed = parse_filename(p.name)
        if parsed.is_open:
            cal_open[p.name] = p
        elif parsed.is_short:
            cal_short[p.name] = p
        else:
            dut_s2p.append(p)
    return dut_s2p, cal_open, cal_short


def _build_all_files_after_deembed(
    dut_s2p: list[Path],
    cal_open: dict[str, Path],
    cal_short: dict[str, Path],
    target_dir: Path,
    method: str,
    publisher: ProgressPublisher,
    db: Session,
    upload_task_id: int | None = None,
) -> list[dict[str, Any]]:
    all_files: list[dict[str, Any]] = []
    if not dut_s2p:
        return all_files

    raw_s11_dir = target_dir / "S11_raw"
    raw_s22_dir = target_dir / "S22_raw"
    raw_s11_dir.mkdir(parents=True, exist_ok=True)
    raw_s22_dir.mkdir(parents=True, exist_ok=True)
    s1p_pairs: list[tuple[Path, Path]] = []
    for s2p in dut_s2p:
        split = split_s2p_to_s1p(s2p, out_dir_s11=raw_s11_dir, out_dir_s22=raw_s22_dir)
        s1p_pairs.append((split.s11_path, split.s22_path))

    de_method = DeembedMethod(method) if method else DeembedMethod.DEFAULT

    def _deembed_cb(current: int, total: int) -> None:
        stage_pct = int(100 * current / total) if total else 0
        overall = 30 + int(15 * current / total) if total else 30
        publisher.stage_update(
            db,
            stage="deembed",
            stage_progress_pct=stage_pct,
            progress_pct=overall,
            progress_msg=f"去嵌中… {current}/{total} 对",
        )

    de_pairs = _run_deembed(
        s1p_pairs, cal_open, cal_short, target_dir, method=de_method, progress_callback=_deembed_cb
    )

    for original, (s11_de, s22_de) in zip(dut_s2p, de_pairs, strict=True):
        all_files.append(
            {
                "path": str(s11_de),
                "deembedded": True,
                "port": 0,
                "s_param_relpath": str(s11_de.relative_to(target_dir)),
                "source_member": str(original.relative_to(target_dir)),
                "source_port": "S11",
            }
        )
        all_files.append(
            {
                "path": str(s22_de),
                "deembedded": True,
                "port": 1,
                "s_param_relpath": str(s22_de.relative_to(target_dir)),
                "source_member": str(original.relative_to(target_dir)),
                "source_port": "S22",
            }
        )
        if upload_task_id is not None:
            raise_if_cancelled(upload_task_id)
    return all_files


@celery_app.task(bind=True, name="aln.redeembed_batch")
@leased_stage("extract")
def redeembed_batch_task(
    self: Task,
    upload_task_id: int,
    batch_no: str,
    run_id: str | None = None,
    lease_token: str | None = None,
) -> dict[str, Any]:
    publisher = ProgressPublisher(upload_task_id)
    db = SessionLocal()
    try:
        raise_if_cancelled(upload_task_id)
        publisher.start(db, "开始重新去嵌…")

        batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        if batch is None:
            raise RuntimeError(f"批次 {batch_no} 不存在")
        mapping_row = db.get(Mapping, batch.mapping_id)
        if mapping_row is None:
            raise RuntimeError(f"对照表 {batch.mapping_id} 不存在")

        target_dir = Path(batch.file_path) if batch.file_path else None
        if target_dir is None or not target_dir.exists():
            raise RuntimeError("批次解压目录不存在")
        ticket = current_run.get()
        if ticket:
            from app.services.result_publish import prepare_stage_directory

            target_dir = prepare_stage_directory(db, ticket, copy_from=target_dir)

        publisher.stage_update(
            db,
            stage="deembed",
            stage_progress_pct=0,
            progress_pct=30,
            progress_msg="扫描校准件与 DUT…",
        )

        dut_s2p, cal_open, cal_short = _prepare_deembed_files(target_dir)
        if not dut_s2p:
            raise DeembedError("未找到 .s2p DUT 文件")
        if not cal_open or not cal_short:
            raise DeembedError("缺少 OPEN/SHORT 校准件，无法重新去嵌")

        all_files = _build_all_files_after_deembed(
            dut_s2p,
            cal_open,
            cal_short,
            target_dir,
            batch.deembed_method,
            publisher,
            db,
            upload_task_id,
        )

        # 删除旧 devices，重新计算

        payload = {
            "upload_task_id": upload_task_id,
            "batch_id": batch.id,
            "mapping_id": batch.mapping_id,
            "wafer": None,
            "f_start_ghz": batch.f_start_ghz,
            "f_end_ghz": batch.f_end_ghz,
            "all_files": all_files,
            "target_dir": str(target_dir),
        }
        if current_run.get() is not None:
            return payload
        return compute_batch_task.apply(args=[payload]).get()

    except Exception as exc:
        logger.exception("redeembed_batch_task fatal")
        try:
            db.rollback()
        except Exception:
            pass
        try:
            publisher.fail(db, error_msg=str(exc))
        except Exception:
            pass
        raise
    finally:
        db.close()


@celery_app.task(bind=True, name="aln.recompute_batch")
@leased_stage("compute")
def recompute_batch_task(
    self: Task,
    upload_task_id: int,
    batch_no: str,
    metrics: list[str],
    run_id: str | None = None,
    lease_token: str | None = None,
) -> dict[str, Any]:
    publisher = ProgressPublisher(upload_task_id)
    db = SessionLocal()
    try:
        raise_if_cancelled(upload_task_id)
        metrics = _validate_metrics(metrics)
        publisher.start(db, "开始重新计算指标…")

        batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        if batch is None:
            raise RuntimeError(f"批次 {batch_no} 不存在")
        mapping_row = db.get(Mapping, batch.mapping_id)
        if mapping_row is None:
            raise RuntimeError(f"对照表 {batch.mapping_id} 不存在")
        mapping_dict = load_mapping(mapping_row.file_path)

        target_dir = Path(batch.file_path) if batch.file_path else None
        devices = db.scalars(
            select(Device).where(Device.batch_id == batch.id).order_by(Device.id)
        ).all()
        ticket = current_run.get()
        run = db.get(ProcessingRun, ticket.run_id)
        if run.retry_of_run_id:
            keys = {item["source_device_key"] for item in run.config_json["retry_items"]}
            devices = [device for device in devices if device.source_device_key in keys]
        if not devices:
            raise RuntimeError("批次下没有 devices 可供重新计算")

        total = len(devices)
        columns_to_update: set[str] = set()
        for m in metrics:
            columns_to_update.update(_METRIC_COLUMNS[m])

        updates: list[dict[str, Any]] = []
        skipped = 0
        failures: list[str] = []
        last_pct = -1
        ticket = current_run.get()
        evidences = []

        def flush_evidence():
            from app.services.processing_report import record_device_result
            from app.services.result_publish import stage_device_rows

            for entry in evidences:
                record_device_result(db, ticket.run_id, **entry)
            if updates:
                stage_device_rows(db, ticket.run_id, ticket.lease_token, updates)
            db.commit()
            updates.clear()
            evidences.clear()

        for i, device in enumerate(devices, start=1):

            def evidence(result=None, error_code=None, skipped=False, device=device):
                evidences.append(
                    {
                        "member": device.s_param_path or device.source_device_key,
                        "port": device.s_param_port,
                        "result": result,
                        "error_code": error_code,
                        "skipped": skipped,
                        "source_device_key": device.source_device_key,
                    }
                )
                if len(evidences) >= 1000:
                    flush_evidence()

            if not device.s_param_path:
                skipped += 1
                evidence(error_code="SOURCE_FILE_UNAVAILABLE", skipped=True)
                continue
            s1p_path = (
                (target_dir / device.s_param_path) if target_dir else Path(device.s_param_path)
            )
            try:
                port_number = 0 if device.s_param_port == "S11" else 1
                row = extract_resonator_params(
                    s1p_path,
                    mapping=mapping_dict,
                    wafer=device.wafer,
                    s_param_relpath=device.s_param_path,
                    deembedded=device.deembedded,
                    f_start_ghz=batch.f_start_ghz,
                    f_end_ghz=batch.f_end_ghz,
                    skip_validation=True,
                    port=port_number,
                )
            except (ExtractError, Exception) as exc:
                failures.append(f"device {device.id}: {exc}")
                evidence(error_code="METRIC_EXTRACTION_FAILED")
                continue

            upd: dict[str, Any] = {"id": device.id}
            if ticket:
                upd["source_device_key"] = device.source_device_key
            for col in columns_to_update:
                upd[col] = row.get(col)
            updates.append(upd)
            evidence(result=upd)

            stage_pct = int(100 * i / total)
            if stage_pct != last_pct and (stage_pct - last_pct >= 5 or i % 100 == 0 or i == total):
                raise_if_cancelled(upload_task_id)
                overall = 45 + int(55 * i / total)
                progress_msg = (
                    f"重新计算 {metrics} 中… {i}/{total}，失败 {len(failures)}，跳过 {skipped}"
                )
                publisher.stage_update(
                    db,
                    stage="metrics",
                    stage_progress_pct=stage_pct,
                    progress_pct=overall,
                    progress_msg=progress_msg,
                )
                last_pct = stage_pct

        if evidences:
            flush_evidence()

        device_count = (
            db.scalar(select(func.count(Device.id)).where(Device.batch_id == batch.id)) or 0
        )
        if ticket:
            from app.services.result_publish import publish_run

            db.rollback()
            publish_run(db.bind, ticket.run_id, ticket.lease_token)
            publisher.published(db, batch_id=batch.id, device_count=device_count)
        return {
            "batch_id": batch.id,
            "device_count": device_count,
            "skipped": skipped,
            "failures": len(failures),
            "failure_samples": failures[:5],
        }
    except Exception as exc:
        logger.exception("recompute_batch_task fatal")
        try:
            db.rollback()
        except Exception:
            pass
        try:
            publisher.fail(db, error_msg=str(exc))
        except Exception:
            pass
        raise
    finally:
        db.close()
