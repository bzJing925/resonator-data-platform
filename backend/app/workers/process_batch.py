"""上传 zip → 解压 → 拆 S2P → 提参 → 入库 的 Celery 兼容任务。

当前实现已拆分为两个独立任务：
- aln.extract_batch（app/workers/extract_batch.py）
- aln.compute_batch（app/workers/compute_batch.py）

process_batch 保留为兼容入口：在 Celery EAGER 模式下串行调用 extract → compute，
让旧测试与旧上传入口无需修改即可工作。生产环境新上传请直接发 chain。
"""

from __future__ import annotations

from typing import Any

from celery import Task

from app.workers.celery_app import celery_app


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
