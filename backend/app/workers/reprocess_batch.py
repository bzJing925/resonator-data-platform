"""批次重处理任务：重新解压 / 重新去嵌 / 重新计算指定指标。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.deembed import DeembedError, DeembedMethod, _run_deembed
from app.core.extract import ExtractError, extract_resonator_params
from app.core.filename import parse_filename
from app.core.mapping import load_mapping
from app.core.touchstone import split_s2p_to_s1p
from app.db import SessionLocal
from app.models import Batch, Device, Mapping, UploadTask
from app.workers.cancel import raise_if_cancelled
from app.workers.celery_app import celery_app
from app.workers.compute_batch import compute_batch_task
from app.workers.progress import ProgressPublisher

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


def _reset_task(db: Session, task_id: int) -> UploadTask:
    task = db.get(UploadTask, task_id)
    if task is None:
        raise RuntimeError(f"任务 {task_id} 不存在")
    task.status = "pending"
    task.stage = "extract"
    task.progress_pct = 0
    task.stage_progress_pct = 0
    task.progress_msg = "排队中"
    task.error_msg = None
    task.finished_at = None
    db.commit()
    return task


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

    for s11_de, s22_de in de_pairs:
        all_files.append(
            {
                "path": str(s11_de),
                "deembedded": True,
                "port": 0,
                "s_param_relpath": str(s11_de.relative_to(target_dir)),
            }
        )
        all_files.append(
            {
                "path": str(s22_de),
                "deembedded": True,
                "port": 1,
                "s_param_relpath": str(s22_de.relative_to(target_dir)),
            }
        )
        if upload_task_id is not None:
            raise_if_cancelled(upload_task_id)
    return all_files


@celery_app.task(bind=True, name="aln.redeembed_batch")
def redeembed_batch_task(
    self: Task,
    upload_task_id: int,
    batch_no: str,
) -> dict[str, Any]:
    publisher = ProgressPublisher(upload_task_id)
    db = SessionLocal()
    try:
        raise_if_cancelled(upload_task_id)
        _reset_task(db, upload_task_id)
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
        db.execute(delete(Device).where(Device.batch_id == batch.id))
        db.commit()

        return compute_batch_task.apply(
            args=[
                {
                    "upload_task_id": upload_task_id,
                    "batch_id": batch.id,
                    "mapping_id": batch.mapping_id,
                    "wafer": None,
                    "f_start_ghz": batch.f_start_ghz,
                    "f_end_ghz": batch.f_end_ghz,
                    "all_files": all_files,
                }
            ]
        ).get()
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
def recompute_batch_task(
    self: Task,
    upload_task_id: int,
    batch_no: str,
    metrics: list[str],
) -> dict[str, Any]:
    publisher = ProgressPublisher(upload_task_id)
    db = SessionLocal()
    try:
        raise_if_cancelled(upload_task_id)
        metrics = _validate_metrics(metrics)
        _reset_task(db, upload_task_id)
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

        for i, device in enumerate(devices, start=1):
            if not device.s_param_path:
                skipped += 1
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
                continue

            upd: dict[str, Any] = {"id": device.id}
            for col in columns_to_update:
                upd[col] = row.get(col)
            updates.append(upd)

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

            if len(updates) >= 1000:
                db.bulk_update_mappings(Device, updates)
                db.commit()
                updates = []

        if updates:
            db.bulk_update_mappings(Device, updates)
            db.commit()

        device_count = (
            db.scalar(select(func.count(Device.id)).where(Device.batch_id == batch.id)) or 0
        )
        publisher.done(db, batch_id=batch.id, device_count=device_count)
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
