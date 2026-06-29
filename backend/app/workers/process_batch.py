"""上传 zip → 解压 → 拆 S2P → 提参 → 入库 的 Celery 兼容任务。

当前实现已拆分为两个独立任务：
- aln.extract_batch（app/workers/extract_batch.py）
- aln.compute_batch（app/workers/compute_batch.py）

process_batch 保留为兼容入口：在 Celery EAGER 模式下串行调用 extract → compute，
让旧测试与旧上传入口无需修改即可工作。生产环境新上传请直接发 chain。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from celery import Task
from sqlalchemy.orm import Session

from app.core.extract import extract_resonator_params
from app.models import Device
from app.workers.celery_app import celery_app
from app.workers.progress import ProgressPublisher

logger = logging.getLogger(__name__)

# 增大 chunk，减少事务提交频率（原 500）。
INSERT_CHUNK = 2000

# 启用多进程提取的最小文件数（低于此阈值单线程更快，避免多进程启动开销）。
_PARALLEL_MIN_FILES = 50

# COPY 目标列（排除自增 id，与 devices 表定义顺序一致）。
# 保留在本模块是为了兼容现有单元测试的导入。
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


def _extract_single(args: tuple) -> dict[str, Any]:
    """子进程入口：提取单个 .s1p 文件的谐振参数。"""

    s1p_path, mapping, wafer, s_param_relpath, deembedded, f_start_ghz, f_end_ghz = args
    try:
        row = extract_resonator_params(
            s1p_path,
            mapping=mapping,
            wafer=wafer,
            s_param_relpath=s_param_relpath,
            deembedded=deembedded,
            f_start_ghz=f_start_ghz,
            f_end_ghz=f_end_ghz,
            skip_validation=True,
        )
        return {"ok": True, "row": row, "name": Path(s1p_path).name}
    except Exception as exc:
        return {"ok": False, "error": f"{Path(s1p_path).name}: {exc}", "name": Path(s1p_path).name}


def _bulk_insert_devices(db: Session, rows: list[dict[str, Any]]) -> None:
    """批量插入 Device。

    当行数 >= 3000 时尝试 PostgreSQL COPY 协议（比 ORM bulk insert 快 5–10 倍）。
    COPY 失败时静默降级到 SQLAlchemy bulk_insert_mappings。
    """
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


def _extract_parallel(
    worker_args: list[tuple],
    total: int,
    batch_id: int,
    publisher: ProgressPublisher,
    db: Session,
    max_workers: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """多进程并行提取参数。"""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    device_rows: list[dict[str, Any]] = []
    failures: list[str] = []
    processed = 0
    last_pct = 5

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

            pct = 5 + int(90 * processed / total)
            if pct != last_pct and (
                pct - last_pct >= 5 or processed % 200 == 0 or processed == total
            ):
                publisher.update(
                    db,
                    progress_pct=pct,
                    progress_msg=(
                        f"已处理 {processed}/{total}，失败 {len(failures)}"
                        f" (并行 {max_workers} workers)"
                    ),
                )
                last_pct = pct

            if len(device_rows) >= INSERT_CHUNK:
                _bulk_insert_devices(db, device_rows)
                device_rows = []

    if device_rows:
        _bulk_insert_devices(db, device_rows)

    return device_rows, failures


@celery_app.task(bind=True, name="aln.process_batch")
def process_batch_task(
    self: Task,
    upload_task_id: int,
    zip_path: str,
    batch_no: str,
    mapping_id: int,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed_enabled: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
) -> dict[str, Any]:
    """兼容入口：串行调用 aln.extract_batch → aln.compute_batch。

    在 Celery EAGER 模式下两条任务会同步执行，保持旧测试可用；
    生产环境建议直接投递 chain(extract_batch.s(...), compute_batch.s(...))。
    """
    from app.workers.compute_batch import compute_batch_task as _compute_task
    from app.workers.extract_batch import extract_batch_task as _extract_task

    extract_result = _extract_task.apply(
        kwargs={
            "upload_task_id": upload_task_id,
            "zip_path": zip_path,
            "batch_no": batch_no,
            "mapping_id": mapping_id,
            "f_start_ghz": f_start_ghz,
            "f_end_ghz": f_end_ghz,
            "deembed_enabled": deembed_enabled,
            "deembed_method": deembed_method,
            "process_type": process_type,
        }
    ).get()

    return _compute_task.apply(args=[extract_result]).get()
