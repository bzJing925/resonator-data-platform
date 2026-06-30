"""Pipeline batch Celery task: streaming extraction + concurrent processing.

边解压边计算流水线（streaming pipeline）的 Celery 任务入口。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.filename import parse_filename
from app.core.mapping import load_mapping
from app.db import SessionLocal
from app.models import Batch, Device, Mapping
from app.services.file_tree_service import build_file_tree_from_disk
from app.workers.celery_app import celery_app
from app.workers.pipeline.calibration import CalibrationIndex
from app.workers.pipeline.extractor import StreamingExtractor
from app.workers.pipeline.processor import DutProcessor
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)

INSERT_CHUNK = 2000

# COPY 目标列（排除自增 id，与 devices 表定义顺序一致）
_COPY_COLUMNS = [
    "batch_id",
    "original_filename",
    "display_name",
    "mark",
    "wafer",
    "folder_name",
    "coord",
    "x",
    "y",
    "eg",
    "fl",
    "ag",
    "pf",
    "area_n",
    "area_um2",
    "fs_ghz",
    "fp_ghz",
    "zs_ohm",
    "zp_ohm",
    "qs",
    "qp",
    "qs_bodeq",
    "qp_bodeq",
    "dbqs",
    "dbqp",
    "bodeq_fitted",
    "bodeq_smooth",
    "bodeq_raw",
    "fbode_ghz",
    "k2eff_pct",
    "fp2_ghz",
    "fs2_ghz",
    "zp2_ohm",
    "zs2_ohm",
    "deembedded",
    "s_param_path",
    "s_param_port",
]


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


def _bulk_insert_devices(db: Session, rows: list[dict[str, Any]]) -> None:
    """批量插入 Device；大行数走 PostgreSQL COPY。"""
    if not rows:
        return

    copy_threshold = 3000
    if len(rows) >= copy_threshold:
        try:
            _copy_insert_devices(db, rows)
            return
        except Exception:
            logger.exception("COPY 批量插入失败，降级到 bulk_insert")

    db.bulk_insert_mappings(Device, rows)
    db.commit()


def _copy_insert_devices(db: Session, rows: list[dict[str, Any]]) -> None:
    """用 PostgreSQL COPY FROM 批量插入。"""
    raw_conn = db.connection().connection
    cols_sql = ", ".join(_COPY_COLUMNS)
    copy_sql = f"COPY devices ({cols_sql}) FROM STDIN"

    with raw_conn.cursor() as cur:
        with cur.copy(copy_sql) as copy:
            for r in rows:
                copy.write_row(tuple(r.get(c) for c in _COPY_COLUMNS))

    db.commit()


@celery_app.task(bind=True, name="aln.pipeline_batch")
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

        target_dir = settings.files_dir / batch_no
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

        # 清理该 batch 旧 devices，防止重复入库
        db.execute(delete(Device).where(Device.batch_id == batch.id))
        db.commit()

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
            for p in extractor.extract(progress_callback=_extract_progress):
                extracted_paths.append(p)
        except Exception:
            logger.exception("解压失败")
            raise

        # 初始化虚拟文件树
        try:
            build_file_tree_from_disk(db, batch)
        except Exception:
            logger.exception("初始化虚拟文件树失败（非致命）")

        # ── 2. 文件分类与校准件优先 ───────────────────────────────────
        cal_s2p_files: list[Path] = []
        pending_duts: list[dict[str, Any]] = []
        cal_index: CalibrationIndex | None = None
        device_rows: list[dict[str, Any]] = []
        failures: list[str] = []
        total_processed = 0

        # 扫描所有已提取文件进行分类
        for p in extracted_paths:
            if not p.is_file():
                continue
            ext = p.suffix.lower()
            if ext not in (".s1p", ".s2p"):
                continue

            parsed = parse_filename(p.name)
            if parsed.is_calibration or _looks_like_calibration(p.name):
                if ext == ".s2p":
                    cal_s2p_files.append(p)
                continue

            item = _path_to_item(p, target_dir)
            pending_duts.append(item)

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
        # macOS spawn 模式下多进程有 pickle 开销，小批次直接单线程
        if total_duts <= 4 or os.name == "nt":
            max_workers = 1

        processor = DutProcessor(
            compress_raw=settings.PIPELINE_COMPRESS_RAW,
            keep_deembed_temp=settings.PIPELINE_KEEP_DEEMBED_TEMP,
        )

        last_pct = 0

        if max_workers == 1:
            # 单线程模式（小批次或 Windows）
            for item in all_duts:
                result = processor.process(item, mapping_dict, wafer, cal_index, target_dir)
                total_processed += 1
                if result["ok"]:
                    for row in result["rows"]:
                        row["batch_id"] = batch.id
                        device_rows.append(row)
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
                    _bulk_insert_devices(db, device_rows)
                    device_rows = []
        else:
            with ProcessPoolExecutor(max_workers=max_workers) as exe:
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

                    if result["ok"]:
                        for row in result["rows"]:
                            row["batch_id"] = batch.id
                            device_rows.append(row)
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
                        _bulk_insert_devices(db, device_rows)
                        device_rows = []

        if device_rows:
            _bulk_insert_devices(db, device_rows)

        # ── 4. 收尾 ─────────────────────────────────────────────────
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

        # 根据 KEEP_RAW_ZIP 删除原始 zip
        if not settings.KEEP_RAW_ZIP:
            try:
                raw_zip = Path(zip_path)
                if raw_zip.exists():
                    raw_zip.unlink()
                    batch.raw_zip_path = None
                    db.commit()
            except Exception:
                logger.exception("删除原 zip 失败: %s", zip_path)

        publisher.done(db, batch_id=batch.id, device_count=device_count)
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
