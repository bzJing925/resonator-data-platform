"""指标计算 Celery 任务：从 aln.extract_batch 的结果中读取 .s1p 列表，
批量提参并写入 devices 表。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.config import get_algorithm_config
from app.core.extract import ExtractError, extract_resonator_params
from app.core.mapping import load_mapping
from app.db import SessionLocal
from app.models import Batch, Device, Mapping
from app.services.device_ingest import bulk_insert_devices
from app.workers.cancel import raise_if_cancelled
from app.workers.celery_app import celery_app
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)

INSERT_CHUNK = 2000
_PARALLEL_MIN_FILES = 50


def _extract_single(args: tuple) -> dict[str, Any]:
    """子进程入口：提取单个文件（s1p 或 s2p 的某一端口）的谐振参数。"""
    from pathlib import Path

    s1p_path, mapping, wafer, s_param_relpath, deembedded, f_start_ghz, f_end_ghz, port = args
    try:
        row = extract_resonator_params(
            Path(s1p_path),
            mapping=mapping,
            wafer=wafer,
            s_param_relpath=s_param_relpath,
            deembedded=deembedded,
            f_start_ghz=f_start_ghz,
            f_end_ghz=f_end_ghz,
            skip_validation=True,
            port=port,
        )
        return {"ok": True, "row": row, "name": f"{Path(s1p_path).name}#{row.get('s_param_port')}"}
    except Exception as exc:
        return {"ok": False, "error": f"{Path(s1p_path).name}: {exc}", "name": Path(s1p_path).name}


@celery_app.task(bind=True, name="aln.compute_batch")
def compute_batch_task(self: Task, extract_result: dict[str, Any]) -> dict[str, Any]:
    """根据 extract_batch 的输出执行指标计算并入库。"""
    upload_task_id = extract_result["upload_task_id"]
    batch_id = extract_result["batch_id"]
    mapping_id = extract_result["mapping_id"]
    wafer = extract_result.get("wafer")
    f_start_ghz = extract_result.get("f_start_ghz")
    f_end_ghz = extract_result.get("f_end_ghz")
    all_files = extract_result["all_files"]

    publisher = ProgressPublisher(upload_task_id)
    db = SessionLocal()

    try:
        raise_if_cancelled(upload_task_id)
        publisher.stage_update(
            db,
            stage="metrics",
            stage_progress_pct=0,
            progress_pct=45,
            progress_msg="开始指标计算…",
        )

        batch = db.get(Batch, batch_id)
        if batch is None:
            raise RuntimeError(f"batches 表无 id={batch_id}")

        mapping_row = db.get(Mapping, mapping_id)
        if mapping_row is None:
            raise RuntimeError(f"mappings 表无 id={mapping_id}")
        mapping_dict = load_mapping(mapping_row.file_path)

        # 保证重复执行同一批次不会产生重复 device 行
        db.execute(delete(Device).where(Device.batch_id == batch.id))
        db.commit()

        target_dir = Path(batch.file_path) if batch.file_path else None

        total = len(all_files)
        if total == 0:
            raise RuntimeError("无可计算的待处理文件")

        device_rows: list[dict[str, Any]] = []
        failures: list[str] = []
        last_pct = 0

        worker_args = [
            (
                item["path"],
                mapping_dict,
                wafer,
                item.get("s_param_relpath")
                or (
                    str(Path(item["path"]).relative_to(target_dir)) if target_dir else item["path"]
                ),
                item["deembedded"],
                f_start_ghz,
                f_end_ghz,
                item.get("port", 0),
            )
            for item in all_files
        ]

        algo_cfg = get_algorithm_config()
        parallel_workers = algo_cfg.worker_extract_workers
        use_parallel = parallel_workers > 1 and total >= _PARALLEL_MIN_FILES and os.name != "nt"

        if use_parallel:
            try:
                device_rows, failures = _extract_parallel(
                    worker_args=worker_args,
                    total=total,
                    batch_id=batch.id,
                    publisher=publisher,
                    db=db,
                    max_workers=parallel_workers,
                    upload_task_id=upload_task_id,
                )
            except Exception:
                logger.exception("多进程提参失败，回退到单线程")
                use_parallel = False

        if not use_parallel:
            for i, item in enumerate(all_files, start=1):
                s1p_path = item["path"]
                try:
                    row = extract_resonator_params(
                        Path(s1p_path),
                        mapping=mapping_dict,
                        wafer=wafer,
                        s_param_relpath=item.get("s_param_relpath")
                        or (
                            str(Path(s1p_path).relative_to(target_dir)) if target_dir else s1p_path
                        ),
                        deembedded=item["deembedded"],
                        f_start_ghz=f_start_ghz,
                        f_end_ghz=f_end_ghz,
                        skip_validation=True,
                        port=item.get("port", 0),
                    )
                    row["batch_id"] = batch.id
                    device_rows.append(row)
                except (ExtractError, Exception) as exc:
                    failures.append(f"{Path(s1p_path).name}: {exc}")
                    logger.warning("提参失败 %s: %s", Path(s1p_path).name, exc)

                stage_pct = int(100 * i / total)
                if stage_pct != last_pct and (
                    stage_pct - last_pct >= 5 or i % 100 == 0 or i == total
                ):
                    raise_if_cancelled(upload_task_id)
                    overall = 45 + int(55 * i / total)
                    publisher.stage_update(
                        db,
                        stage="metrics",
                        stage_progress_pct=stage_pct,
                        progress_pct=overall,
                        progress_msg=f"已处理 {i}/{total}，失败 {len(failures)}",
                    )
                    last_pct = stage_pct

                if len(device_rows) >= INSERT_CHUNK:
                    bulk_insert_devices(db, device_rows)
                    device_rows = []

            if device_rows:
                bulk_insert_devices(db, device_rows)

        device_count = (
            db.scalar(select(func.count(Device.id)).where(Device.batch_id == batch.id)) or 0
        )

        db.execute(
            update(Batch)
            .where(Batch.id == batch.id)
            .values(
                device_count=device_count,
                f_start_ghz=f_start_ghz,
                f_end_ghz=f_end_ghz,
                task_id=upload_task_id,
            )
        )
        db.commit()

        try:
            db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_batch_stats"))
            db.commit()
        except Exception:
            logger.exception("刷新物化视图 mv_batch_stats 失败（非致命）")

        publisher.done(db, batch_id=batch.id, device_count=device_count)
        return {
            "batch_id": batch.id,
            "device_count": device_count,
            "failures": len(failures),
            "failure_samples": failures[:5],
        }

    except Exception as exc:
        logger.exception("compute_batch_task fatal")
        try:
            db.rollback()
        except Exception:
            pass
        try:
            publisher.fail(db, error_msg=str(exc))
        except Exception:
            logger.exception("publisher.fail itself raised")
        raise
    finally:
        db.close()


def _extract_parallel(
    worker_args: list[tuple],
    total: int,
    batch_id: int,
    publisher: ProgressPublisher,
    db: Session,
    max_workers: int,
    upload_task_id: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """多进程并行提取参数。"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    device_rows: list[dict[str, Any]] = []
    failures: list[str] = []
    processed = 0
    last_pct = 0

    with ProcessPoolExecutor(max_workers=max_workers) as exe:
        futures = {exe.submit(_extract_single, args): args for args in worker_args}
        for future in as_completed(futures):
            result = future.result()
            processed += 1
            if result["ok"]:
                row = result["row"]
                row["batch_id"] = batch_id
                device_rows.append(row)
            else:
                failures.append(result["error"])
                logger.warning("提参失败 %s", result["error"])

            stage_pct = int(100 * processed / total)
            if stage_pct != last_pct and (
                stage_pct - last_pct >= 5 or processed % 200 == 0 or processed == total
            ):
                raise_if_cancelled(upload_task_id)
                overall = 45 + int(55 * stage_pct / 100)
                publisher.stage_update(
                    db,
                    stage="metrics",
                    stage_progress_pct=stage_pct,
                    progress_pct=overall,
                    progress_msg=(
                        f"已处理 {processed}/{total}，"
                        f"失败 {len(failures)} (并行 {max_workers} workers)"
                    ),
                )
                last_pct = stage_pct

            if len(device_rows) >= INSERT_CHUNK:
                bulk_insert_devices(db, device_rows)
                device_rows = []

    if device_rows:
        bulk_insert_devices(db, device_rows)

    return device_rows, failures
