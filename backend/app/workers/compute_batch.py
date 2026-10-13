"""指标计算 Celery 任务：从 aln.extract_batch 的结果中读取 .s1p 列表，
批量提参并写入 devices 表。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy.orm import Session

from app.config import get_algorithm_config
from app.core.extract import extract_resonator_params
from app.core.mapping import load_mapping
from app.db import SessionLocal
from app.models import Batch, Mapping, ProcessingRun
from app.services.device_ingest import bulk_insert_devices
from app.services.processing_report import record_device_result, summarize_run
from app.workers.cancel import TaskCancelledError, raise_if_cancelled
from app.workers.celery_app import celery_app
from app.workers.progress import ProgressPublisher
from app.workers.run_execution import current_run, leased_stage

logger = logging.getLogger(__name__)

INSERT_CHUNK = 2000
_PARALLEL_MIN_FILES = 50


class ParallelExtractionError(RuntimeError):
    """Pool failed before any durable writes; safe to retry serially."""


def _extract_single(args: tuple) -> dict[str, Any]:
    """子进程入口：提取单个文件（s1p 或 s2p 的某一端口）的谐振参数。"""
    from pathlib import Path

    s1p_path, mapping, wafer, s_param_relpath, deembedded, f_start_ghz, f_end_ghz, port = args[:8]
    member = args[8] if len(args) > 8 else s_param_relpath
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
        return {
            "ok": True,
            "row": row,
            "member": member,
            "port": row.get("s_param_port") or ("S11" if port == 0 else "S22"),
            "name": f"{Path(s1p_path).name}#{row.get('s_param_port')}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{Path(s1p_path).name}: {exc}",
            "member": member,
            "port": "S11" if port == 0 else "S22",
            "code": "METRIC_EXTRACTION_FAILED",
            "name": Path(s1p_path).name,
        }


@celery_app.task(bind=True, name="aln.compute_batch")
@leased_stage("compute")
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
        ticket = current_run.get()
        if ticket is None:
            raise RuntimeError("指标计算必须持有处理租约")

        run = db.get(ProcessingRun, ticket.run_id)
        if run.retry_of_run_id:
            selected = {(item["member"], item["port"]) for item in run.config_json["retry_items"]}
            all_files = [item for item in all_files if (
                item.get("source_member") or item.get("s_param_relpath"),
                item.get("source_port") or ("S22" if item.get("port") == 1 else "S11"),
            ) in selected]

        target_path = extract_result.get("target_dir") or batch.file_path
        target_dir = Path(target_path) if target_path else None

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
                item.get("source_member") or item.get("s_param_relpath"),
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
            except TaskCancelledError:
                raise
            except ParallelExtractionError:
                logger.exception("多进程提参失败，回退到单线程")
                use_parallel = False

        if not use_parallel:
            import json
            import tempfile

            with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+t") as results:
                for i, args in enumerate(worker_args, 1):
                    # CPU work holds no report/staging write transaction.
                    result = _extract_single(args)
                    results.write(json.dumps(result, default=str) + "\n")
                    stage_pct = int(100 * i / total)
                    if stage_pct != last_pct and (
                        stage_pct - last_pct >= 5 or i % 100 == 0 or i == total
                    ):
                        raise_if_cancelled(upload_task_id)
                        publisher.stage_update(
                            db,
                            stage="metrics",
                            stage_progress_pct=stage_pct,
                            progress_pct=45 + int(55 * i / total),
                            progress_msg=f"已计算 {i}/{total}",
                        )
                        last_pct = stage_pct
                results.seek(0)
                device_rows, failures = _persist_results(
                    (json.loads(line) for line in results),
                    batch.id,
                    db,
                )

        if ticket:
            from app.services.result_publish import publish_run

            db.commit()
            result = publish_run(db.bind, ticket.run_id, ticket.lease_token)
            db.expire_all()
            device_count = db.get(Batch, batch_id).device_count
            publisher.published(db, batch_id=batch_id, device_count=device_count)
            return {
                "batch_id": batch_id,
                "device_count": device_count,
                "published_count": result["published_count"],
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

    processed = 0
    last_pct = 0
    import json
    import tempfile

    # Spool structured subprocess results to temporary storage, bounded in RAM.
    # Nothing reaches Device or report tables until the entire pool is successful.
    with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+t") as results:
        try:
            with ProcessPoolExecutor(max_workers=max_workers) as exe:
                futures = {exe.submit(_extract_single, args): args for args in worker_args}
                for future in as_completed(futures):
                    result = future.result()
                    results.write(json.dumps(result, default=str) + "\n")
                    processed += 1
                    stage_pct = int(100 * processed / total)
                    if stage_pct != last_pct and (
                        stage_pct - last_pct >= 5 or processed % 200 == 0 or processed == total
                    ):
                        raise_if_cancelled(upload_task_id)
                        publisher.stage_update(
                            db,
                            stage="metrics",
                            stage_progress_pct=stage_pct,
                            progress_pct=45 + int(55 * stage_pct / 100),
                            progress_msg=f"已计算 {processed}/{total} (并行 {max_workers} workers)",
                        )
                        last_pct = stage_pct
        except TaskCancelledError:
            raise
        except Exception as exc:
            raise ParallelExtractionError("指标计算进程失败") from exc

        results.seek(0)
        return _persist_results((json.loads(line) for line in results), batch_id, db)


def _persist_results(results, batch_id, db):
    from itertools import batched

    from app.config import get_settings

    ticket = current_run.get()
    failures = []
    for chunk in batched(results, get_settings().STAGE_INSERT_CHUNK):
        rows = []
        for result in chunk:
            evidence = record_device_result(
                db,
                ticket.run_id,
                result["member"],
                result["port"],
                result=result.get("row"),
                error_code=result.get("code"),
            )
            if result["ok"]:
                row = result["row"]
                row["batch_id"] = batch_id
                if evidence.source_device_key:
                    row["source_device_key"] = evidence.source_device_key
                rows.append(row)
            else:
                failures.append(result["error"])
        if rows:
            bulk_insert_devices(db, rows)
        db.commit()
    summarize_run(db, ticket.run_id)
    db.commit()
    return [], failures
