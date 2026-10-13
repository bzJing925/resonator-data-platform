"""Pipeline batch Celery task: streaming extraction + concurrent processing.

边解压边计算流水线（streaming pipeline）的 Celery 任务入口。
"""

from __future__ import annotations

import logging
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy import select

from app.config import get_settings
from app.core.filename import parse_filename
from app.core.mapping import load_mapping
from app.db import SessionLocal
from app.models import Batch, Mapping
from app.services.device_ingest import bulk_insert_devices
from app.workers.celery_app import celery_app
from app.workers.pipeline.calibration import CalibrationIndex
from app.workers.pipeline.extractor import StreamingExtractor
from app.workers.pipeline.processor import DutProcessor
from app.workers.progress import ProgressPublisher
from app.workers.run_execution import current_run, leased_stage

logger = logging.getLogger(__name__)

INSERT_CHUNK = 2000


def should_use_pipeline(zip_path: str, deembed: bool) -> bool:
    """判断当前批次是否应使用 pipeline（边解压边计算）链路。

    规则：
    1. 若未启用 deembed，直接返回 False（pipeline 目前只服务于需要
       边解压边去嵌的场景）。
    2. 若 settings.PIPELINE_ENABLED 为 False，返回 False。
    3. 否则检查 zip 内是否包含 OPEN/SHORT 校准件；有则 True，无则 False。
    """
    if not deembed:
        return False
    settings = get_settings()
    if not settings.PIPELINE_ENABLED:
        return False
    from app.workers.pipeline.extractor import zip_contains_calibration

    return zip_contains_calibration(zip_path)


def _looks_like_calibration(name: str) -> bool:
    """判断文件名是否像校准件（OPEN/SHORT/WO/WS）。"""
    upper = name.upper()
    keywords = ("OPEN", "SHORT", "WO", "WS")
    return any(kw in upper for kw in keywords)


def _path_to_item(p: Path, target_dir: Path) -> dict[str, Any]:
    """从文件路径生成待处理 item dict。"""
    ext = p.suffix.lower()
    item_type = "s2p" if ext == ".s2p" else "s1p"
    return {
        "type": item_type,
        "path": str(p),
        "s_param_relpath": str(p.relative_to(target_dir)),
    }


def _wafer_from_batch_no(batch_no: str) -> int | None:
    """从 batch_no 末尾提取 wafer 编号。"""
    m = re.search(r"\.(\d+)$", batch_no)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    return None


@celery_app.task(bind=True, name="aln.pipeline_batch")
@leased_stage("compute")
def pipeline_batch_task(
    self: Task,
    upload_task_id: int,
    zip_path: str,
    batch_no: str,
    mapping_id: int,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed_method: str = "default",
    process_type: str = "AUTO",
    run_id: str | None = None,
    lease_token: str | None = None,
) -> dict[str, Any]:
    """Pipeline batch task: stream-extract + process + bulk-insert.

    参数与 legacy extract_batch / compute_batch 保持一致，
    方便上层 upload API 做统一调度。
    """
    publisher = ProgressPublisher(upload_task_id)
    settings = get_settings()
    db = SessionLocal()

    try:
        publisher.start(db, msg="开始 pipeline 处理…")

        batch = db.scalar(select(Batch).where(Batch.batch_no == batch_no))
        if batch is None:
            raise RuntimeError(f"batches 表无 batch_no={batch_no} 的预占行")

        mapping_row = db.get(Mapping, mapping_id)
        if mapping_row is None:
            raise RuntimeError(f"mappings 表无 id={mapping_id}")
        mapping_dict = load_mapping(mapping_row.file_path)

        from app.models import ProcessingRun
        from app.services.result_publish import prepare_stage_directory

        ticket = current_run.get()
        run_config = db.get(ProcessingRun, ticket.run_id).config_json
        prepared_dir = run_config.get("prepared_dir")
        target_dir = prepare_stage_directory(db, ticket, copy_from=prepared_dir)

        # ── 1. 解压（流式产出文件路径）────────────────────────────────
        extracted_paths: list[Path] = []

        def _extract_progress(count: int) -> None:
            # 0-30% 映射给 extract 阶段
            pct = min(30, int(30 * count / max(count, 1)))
            publisher.stage_update(
                db,
                stage="extract",
                stage_progress_pct=pct,
                progress_pct=pct,
                progress_msg=f"解压中… 已发现 {count} 个文件",
            )

        extractor = StreamingExtractor(
            zip_path,
            target_dir,
            scan_interval=settings.PIPELINE_SCAN_INTERVAL,
        )
        try:
            paths = (
                sorted(target_dir.rglob("*"))
                if prepared_dir
                else extractor.extract(progress_callback=_extract_progress)
            )
            for p in paths:
                extracted_paths.append(p)
        except Exception:
            logger.exception("解压失败")
            raise

        # ── 2. 文件分类与校准件优先 ───────────────────────────────────
        cal_s2p_files: list[Path] = []
        pending_duts: list[dict[str, Any]] = []
        cal_index: CalibrationIndex | None = None
        device_rows: list[dict[str, Any]] = []
        failures: list[str] = []
        total_processed = 0

        # Preserve source-member identity before generating derived curves.
        from app.core.deembed import DeembedMethod, is_calibration_filename
        from app.services.processing_report import record_item_result
        from app.services.source_identity import register_source_input
        from app.services.upload_precheck import file_digest

        approved = set(run_config.get("prepared_file_names", [])) if prepared_dir else None
        for p in extracted_paths:
            if not p.is_file():
                continue
            member = str(p.relative_to(target_dir))
            ext = p.suffix.lower()
            calibration = is_calibration_filename(p.name, DeembedMethod(deembed_method))
            if ext not in (".s1p", ".s2p"):
                if not run_config.get("precheck_report"):
                    record_item_result(db, ticket.run_id, "file", member, "skipped")
                continue
            if calibration:
                if not run_config.get("precheck_report"):
                    record_item_result(db, ticket.run_id, "file", member, "success")
                if ext == ".s2p":
                    cal_s2p_files.append(p)
                continue
            if approved is not None and member not in approved:
                continue
            record_item_result(db, ticket.run_id, "file", member, "pending")
            ports = ["S11", "S22"] if ext == ".s2p" else [parse_filename(p.name).port or "S11"]
            for port in ports:
                source = register_source_input(
                    db,
                    batch.id,
                    member,
                    port,
                    input_digest=file_digest(p),
                    current_relpath=None,
                    original_filename=p.name,
                )
                record_item_result(
                    db,
                    ticket.run_id,
                    "device",
                    member,
                    "pending",
                    port=port,
                    source_device_key=source.source_device_key,
                )
            pending_duts.append(_path_to_item(p, target_dir))
        db.commit()

        # 建立校准索引
        if cal_s2p_files:
            cal_index = CalibrationIndex.build(target_dir, cal_s2p_files, method=deembed_method)

        # 所有 DUT 都进入待处理队列
        all_duts = pending_duts

        publisher.stage_update(
            db,
            stage="metrics",
            stage_progress_pct=0,
            progress_pct=35,
            progress_msg=f"解压完成，发现 {len(all_duts)} 个 DUT，{len(cal_s2p_files)} 个校准件",
        )

        if not all_duts:
            raise RuntimeError("ZIP 解压后未发现可处理的 DUT 文件（.s1p 或 .s2p）")

        # ── 3. 消费者池 ─────────────────────────────────────────────
        wafer = _wafer_from_batch_no(batch_no)
        total_duts = len(all_duts)
        max_workers = settings.PIPELINE_WORKERS or os.cpu_count() or 1
        # macOS 下 Celery worker 子进程是 daemonic，无法再通过 ProcessPoolExecutor
        # 创建子进程，因此使用 ThreadPoolExecutor；小批次或 Windows 直接单线程。
        use_threads = sys.platform == "darwin"
        if total_duts <= 4 or os.name == "nt":
            max_workers = 1
            use_threads = False

        processor = DutProcessor(
            compress_raw=False,
            keep_deembed_temp=True,
        )

        def persist_result(item, result):
            from app.services.processing_report import record_device_result

            member = item["s_param_relpath"]
            succeeded = set()
            for row in result.get("rows", []):
                port = row.get("s_param_port", "S11")
                evidence = record_device_result(db, ticket.run_id, member, port, result=row)
                row["source_device_key"] = evidence.source_device_key
                row["batch_id"] = batch.id
                device_rows.append(row)
                succeeded.add(port)
            ports = (
                ["S11", "S22"]
                if item["type"] == "s2p"
                else [parse_filename(Path(item["path"]).name).port or "S11"]
            )
            for port in ports:
                if port not in succeeded:
                    record_device_result(
                        db, ticket.run_id, member, port, error_code="METRIC_EXTRACTION_FAILED"
                    )

        last_pct = 0

        if max_workers == 1:
            # 单线程模式（小批次或 Windows）
            for item in all_duts:
                result = processor.process(item, mapping_dict, wafer, cal_index, target_dir)
                total_processed += 1
                persist_result(item, result)
                for f in result.get("failures", []):
                    failures.append(f)

                stage_pct = int(100 * total_processed / total_duts)
                if stage_pct != last_pct and (
                    stage_pct - last_pct >= 5
                    or total_processed % 100 == 0
                    or total_processed == total_duts
                ):
                    overall = 35 + int(60 * stage_pct / 100)
                    publisher.stage_update(
                        db,
                        stage="metrics",
                        stage_progress_pct=stage_pct,
                        progress_pct=overall,
                        progress_msg=f"已处理 {total_processed}/{total_duts}，失败 {len(failures)}",
                    )
                    last_pct = stage_pct

                if len(device_rows) >= INSERT_CHUNK:
                    bulk_insert_devices(db, device_rows, commit=True)
                    device_rows = []
        else:
            executor_cls = ThreadPoolExecutor if use_threads else ProcessPoolExecutor
            with executor_cls(max_workers=max_workers) as exe:
                futures = {
                    exe.submit(
                        processor.process,
                        item,
                        mapping_dict,
                        wafer,
                        cal_index,
                        target_dir,
                    ): item
                    for item in all_duts
                }

                for future in as_completed(futures):
                    result = future.result()
                    total_processed += 1

                    persist_result(futures[future], result)
                    for f in result.get("failures", []):
                        failures.append(f)

                    stage_pct = int(100 * total_processed / total_duts)
                    if stage_pct != last_pct and (
                        stage_pct - last_pct >= 5
                        or total_processed % 100 == 0
                        or total_processed == total_duts
                    ):
                        overall = 35 + int(60 * stage_pct / 100)
                        publisher.stage_update(
                            db,
                            stage="metrics",
                            stage_progress_pct=stage_pct,
                            progress_pct=overall,
                            progress_msg=f"已处理 {total_processed}/{total_duts}，"
                            f"失败 {len(failures)}",
                        )
                        last_pct = stage_pct

                    if len(device_rows) >= INSERT_CHUNK:
                        bulk_insert_devices(db, device_rows, commit=True)
                        device_rows = []

        if device_rows:
            bulk_insert_devices(db, device_rows, commit=True)

        # Only publication changes the active dataset and its file tree.
        from app.services.result_publish import publish_run

        db.commit()
        result = publish_run(db.bind, ticket.run_id, ticket.lease_token)
        db.expire_all()
        device_count = db.get(Batch, batch.id).device_count
        publisher.published(db, batch_id=batch.id, device_count=device_count)
        return {
            "batch_id": batch.id,
            "device_count": device_count,
            "failures": len(failures),
            "failure_samples": failures[:5],
        }

    except Exception as exc:
        logger.exception("pipeline_batch_task fatal")
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
