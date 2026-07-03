"""上传任务分发：服务器版走 Celery，桌面版走本地队列。"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings


def dispatch_batch_task(
    task_id: int,
    zip_path: Path,
    batch_no: str,
    mapping_id: int,
    *,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
) -> str | None:
    settings = get_settings()
    if settings.is_desktop:
        from app.workers.local_queue import LocalTask, get_local_queue

        get_local_queue().put(
            LocalTask(
                task_id=task_id,
                zip_path=Path(zip_path),
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=f_start_ghz,
                f_end_ghz=f_end_ghz,
                deembed=deembed,
                deembed_method=deembed_method,
                process_type=process_type,
            )
        )
        return f"local-{task_id}"

    from celery import chain

    from app.workers.compute_batch import compute_batch_task
    from app.workers.extract_batch import extract_batch_task

    result = chain(
        extract_batch_task.s(
            upload_task_id=task_id,
            zip_path=str(zip_path),
            batch_no=batch_no,
            mapping_id=mapping_id,
            f_start_ghz=f_start_ghz,
            f_end_ghz=f_end_ghz,
            deembed_enabled=bool(deembed),
            deembed_method=deembed_method if deembed else "default",
            process_type=process_type,
        ),
        compute_batch_task.s(),
    ).apply_async()
    return result.id
