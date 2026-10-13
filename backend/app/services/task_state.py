"""单一任务/run 状态转换规则及乐观并发更新。"""

from __future__ import annotations

from typing import Literal

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.models.task import UploadTask
from app.schemas.processing import ProcessingStatus

TransitionResult = Literal["changed", "noop", "conflict"]

_TERMINAL = frozenset({"success", "failed", "cancelled"})
_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"running", "cancel_requested"}),
    "running": frozenset({"success", "failed", "cancel_requested"}),
    "cancel_requested": frozenset({"cancelled"}),
    "success": frozenset({"success"}),
    "failed": frozenset({"failed"}),
    "cancelled": frozenset({"cancelled"}),
}


def can_transition(old: str, new: str) -> bool:
    """Return whether a status change is part of the shared C02 state graph."""
    return new in _ALLOWED_TRANSITIONS.get(old, ())


class _ConcurrentTransitionError(Exception):
    """Roll back the savepoint when either task or run loses the compare-and-set."""


def transition_task(
    session: Session,
    task_id: int,
    run_id: str,
    lease_token: str,
    expected_status: ProcessingStatus,
    new_status: ProcessingStatus,
    *,
    confirmed_dispatch_failure: bool = False,
) -> TransitionResult:
    """Atomically transition an UploadTask and its ProcessingRun using one lease.

    ``processing_runs`` is introduced by B02. Keeping the association lookup here
    by its C02 table contract lets the state rule land first without changing the
    existing task model or migration chain.
    """
    dispatch_exception = (
        confirmed_dispatch_failure and expected_status == "pending" and new_status == "failed"
    )
    if (
        not lease_token
        or not run_id
        or (not can_transition(expected_status, new_status) and not dispatch_exception)
    ):
        return "conflict"

    try:
        with session.begin_nested():
            task = session.get(UploadTask, task_id)
            if task is None:
                return "conflict"

            run = (
                session.execute(
                    text(
                        "SELECT task_id, status, lease_token FROM processing_runs "
                        "WHERE id = :run_id"
                    ),
                    {"run_id": run_id},
                )
                .mappings()
                .first()
            )
            if (
                run is None
                or run["task_id"] != task_id
                or run["lease_token"] != lease_token
                or run["status"] != task.status
            ):
                return "conflict"

            if task.status != expected_status or run["status"] != expected_status:
                return "conflict"
            if dispatch_exception:
                phase = session.scalar(
                    text("SELECT dispatch_phase FROM processing_runs WHERE id=:id"), {"id": run_id}
                )
                if phase != "queued":
                    return "conflict"
            if expected_status == new_status:
                return "noop" if expected_status in _TERMINAL else "conflict"

            task_result = session.execute(
                update(UploadTask)
                .where(UploadTask.id == task_id, UploadTask.status == expected_status)
                .values(status=new_status)
            )
            if task_result.rowcount != 1:
                raise _ConcurrentTransitionError

            run_result = session.execute(
                text(
                    "UPDATE processing_runs SET status = :new_status "
                    "WHERE id = :run_id AND task_id = :task_id "
                    "AND status = :expected_status AND lease_token = :lease_token"
                    + (" AND dispatch_phase = 'queued'" if dispatch_exception else "")
                ),
                {
                    "new_status": new_status,
                    "run_id": run_id,
                    "task_id": task_id,
                    "expected_status": expected_status,
                    "lease_token": lease_token,
                },
            )
            if run_result.rowcount != 1:
                raise _ConcurrentTransitionError
    except _ConcurrentTransitionError:
        return "conflict"

    return "changed"
