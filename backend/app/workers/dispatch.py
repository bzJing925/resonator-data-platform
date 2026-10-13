"""上传任务分发：服务器版走 Celery，桌面版走本地队列。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import uuid4

from app.config import get_settings


class ConfirmedDispatchError(RuntimeError):
    """Explicit evidence that no queue accepted this delivery; not a timeout."""


def _reserve_executor_ids(run_id, lease_token, stages):
    ids = {stage: str(uuid4()) for stage in stages}
    if run_id:
        from app.db import engine
        from app.services.processing_run import _locked_session, _owned_run

        with _locked_session(engine) as db:
            run, _batch = _owned_run(db, run_id, lease_token)
            run.executor_ids = {**run.executor_ids, **ids}
    return ids


def _send_chain(extract, compute, run_id, lease_token):
    from celery import chain

    ids = _reserve_executor_ids(run_id, lease_token, ("extract", "compute"))
    return chain(
        extract.set(task_id=ids["extract"]), compute.set(task_id=ids["compute"])
    ).apply_async()


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
    run_id: str | None = None,
    lease_token: str | None = None,
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
                run_id=run_id,
                lease_token=lease_token,
            )
        )
        return f"local-{task_id}"

    from app.workers.compute_batch import compute_batch_task
    from app.workers.extract_batch import extract_batch_task

    result = _send_chain(
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
            run_id=run_id,
            lease_token=lease_token,
        ),
        compute_batch_task.s(),
        run_id,
        lease_token,
    )
    return result.id


def dispatch_reprocess_task(
    task_id: int,
    batch_no: str,
    mapping_id: int,
    kind: Literal["reextract", "redeembed", "recompute"],
    *,
    zip_path: Path | None = None,
    f_start_ghz: float | None = None,
    f_end_ghz: float | None = None,
    deembed: bool = False,
    deembed_method: str = "default",
    process_type: str = "AUTO",
    metrics: list[str] | None = None,
    run_id: str | None = None,
    lease_token: str | None = None,
) -> str | None:
    settings = get_settings()
    if settings.is_desktop:
        from app.workers.local_queue import LocalTask, get_local_queue

        get_local_queue().put(
            LocalTask(
                task_id=task_id,
                zip_path=Path(zip_path) if zip_path else None,
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=f_start_ghz,
                f_end_ghz=f_end_ghz,
                deembed=deembed,
                deembed_method=deembed_method,
                process_type=process_type,
                kind=kind,
                metrics=metrics,
                run_id=run_id,
                lease_token=lease_token,
            )
        )
        return f"local-{task_id}"

    from app.workers.compute_batch import compute_batch_task
    from app.workers.extract_batch import extract_batch_task
    from app.workers.reprocess_batch import recompute_batch_task, redeembed_batch_task

    if kind == "reextract":
        result = _send_chain(
            extract_batch_task.s(
                upload_task_id=task_id,
                zip_path=str(zip_path),
                batch_no=batch_no,
                mapping_id=mapping_id,
                f_start_ghz=f_start_ghz,
                f_end_ghz=f_end_ghz,
                deembed_enabled=deembed,
                deembed_method=deembed_method,
                process_type=process_type,
                run_id=run_id,
                lease_token=lease_token,
            ),
            compute_batch_task.s(),
            run_id,
            lease_token,
        )
        return result.id
    if kind == "redeembed":
        result = _send_chain(
            redeembed_batch_task.s(
                upload_task_id=task_id, batch_no=batch_no, run_id=run_id, lease_token=lease_token
            ),
            compute_batch_task.s(),
            run_id,
            lease_token,
        )
        return result.id
    if kind == "recompute":
        ids = _reserve_executor_ids(run_id, lease_token, ("compute",))
        result = recompute_batch_task.apply_async(
            task_id=ids["compute"],
            kwargs={
                "upload_task_id": task_id,
                "batch_no": batch_no,
                "metrics": metrics or [],
                "run_id": run_id,
                "lease_token": lease_token,
            },
        )
        return result.id
    return None


def dispatch_precheck_job(draft_id: str) -> str:
    settings = get_settings()
    if settings.is_desktop:
        from app.workers.local_queue import LocalPrecheckTask, get_local_queue

        get_local_queue().put(LocalPrecheckTask(draft_id=draft_id))
        return f"local-precheck-{draft_id}"
    from app.workers.precheck import precheck_upload_task

    return precheck_upload_task.apply_async(args=[draft_id]).id


def dispatch_prechecked_table(task_id: int, run_id: str, lease_token: str) -> str:
    if get_settings().is_desktop:
        from app.workers.local_queue import LocalTask, get_local_queue

        get_local_queue().put(
            LocalTask(task_id=task_id, run_id=run_id, lease_token=lease_token, kind="table_import")
        )
        return f"local-table-{task_id}"
    from app.workers.precheck import import_prechecked_table_task

    ids = _reserve_executor_ids(run_id, lease_token, ("compute",))
    return import_prechecked_table_task.apply_async(
        args=[task_id, run_id, lease_token],
        task_id=ids["compute"],
    ).id


def dispatch_export_job(job_id: str, lease_token: str) -> str:
    import os

    from app.db import engine
    from app.services.export_job_service import mark_dispatched

    if get_settings().is_desktop:
        from app.workers.local_queue import LocalExportTask, get_local_queue
        executor_id = f"native-queue-{os.getpid()}"
        mark_dispatched(engine, job_id, lease_token, executor_id)
        get_local_queue().put(LocalExportTask(job_id, lease_token))
        return executor_id
    from app.workers.export_job import export_job_task
    executor_id = str(uuid4())
    mark_dispatched(engine, job_id, lease_token, executor_id)
    return export_job_task.apply_async(args=[job_id, lease_token], task_id=executor_id).id
