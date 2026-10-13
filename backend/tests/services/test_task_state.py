"""C02 task/run status transitions, leases, and cancellation race behavior."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.services.task_state import can_transition, transition_task


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", future=True)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE upload_tasks (
                id INTEGER PRIMARY KEY,
                batch_no TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN (
                    'pending','running','success','failed','cancel_requested','cancelled'
                )),
                progress_pct SMALLINT NOT NULL DEFAULT 0,
                stage TEXT NOT NULL DEFAULT 'extract',
                stage_progress_pct SMALLINT NOT NULL DEFAULT 0,
                progress_msg TEXT,
                error_msg TEXT,
                started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME,
                cancelled_at DATETIME,
                celery_task_id TEXT,
                kind TEXT NOT NULL DEFAULT 'upload'
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE processing_runs (
                id TEXT PRIMARY KEY,
                task_id INTEGER NOT NULL UNIQUE,
                status TEXT NOT NULL CHECK (status IN (
                    'pending','running','success','failed','cancel_requested','cancelled'
                )),
                lease_token TEXT NOT NULL
            )
            """
        )

    session = Session(engine, autoflush=False, future=True)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed_pair(
    session: Session, task_status: str, run_status: str | None = None
) -> tuple[int, str, str]:
    task_id = 1
    run_id = str(uuid4())
    lease_token = str(uuid4())
    session.execute(
        text("INSERT INTO upload_tasks(id,batch_no,status) VALUES (:id,:batch_no,:status)"),
        {"id": task_id, "batch_no": "LOT-001", "status": task_status},
    )
    session.execute(
        text(
            "INSERT INTO processing_runs(id,task_id,status,lease_token) "
            "VALUES (:id,:task_id,:status,:lease_token)"
        ),
        {
            "id": run_id,
            "task_id": task_id,
            "status": run_status or task_status,
            "lease_token": lease_token,
        },
    )
    session.commit()
    return task_id, run_id, lease_token


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("pending", "running"),
        ("pending", "cancel_requested"),
        ("running", "success"),
        ("running", "failed"),
        ("running", "cancel_requested"),
        ("cancel_requested", "cancelled"),
    ],
)
def test_allowed_transition_updates_task_and_run(db_session: Session, old: str, new: str) -> None:
    task_id, run_id, token = _seed_pair(db_session, old)

    result = transition_task(db_session, task_id, run_id, token, old, new)
    db_session.commit()

    assert result == "changed"
    assert db_session.execute(
        text("SELECT status FROM upload_tasks WHERE id=:id"), {"id": task_id}
    ).scalar_one() == new
    assert db_session.execute(
        text("SELECT status FROM processing_runs WHERE id=:id"), {"id": run_id}
    ).scalar_one() == new


@pytest.mark.parametrize("terminal", ["success", "failed", "cancelled"])
def test_terminal_status_transition_is_idempotent(db_session: Session, terminal: str) -> None:
    task_id, run_id, token = _seed_pair(db_session, terminal)

    result = transition_task(db_session, task_id, run_id, token, terminal, terminal)

    assert result == "noop"
    assert db_session.execute(
        text("SELECT status FROM upload_tasks WHERE id=:id"), {"id": task_id}
    ).scalar_one() == terminal
    assert db_session.execute(
        text("SELECT status FROM processing_runs WHERE id=:id"), {"id": run_id}
    ).scalar_one() == terminal


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("pending", "success"),
        ("success", "running"),
        ("failed", "success"),
        ("cancel_requested", "running"),
        ("cancelled", "success"),
        ("unknown", "running"),
    ],
)
def test_forbidden_transition_is_rejected(old: str, new: str) -> None:
    assert not can_transition(old, new)


def test_wrong_lease_token_leaves_both_records_unchanged(db_session: Session) -> None:
    task_id, run_id, _token = _seed_pair(db_session, "pending")

    result = transition_task(db_session, task_id, run_id, str(uuid4()), "pending", "running")

    assert result == "conflict"
    assert db_session.execute(
        text("SELECT status FROM upload_tasks WHERE id=:id"), {"id": task_id}
    ).scalar_one() == "pending"
    assert db_session.execute(
        text("SELECT status FROM processing_runs WHERE id=:id"), {"id": run_id}
    ).scalar_one() == "pending"


def test_late_running_worker_cannot_overwrite_cancelled_task(db_session: Session) -> None:
    task_id, run_id, token = _seed_pair(db_session, "cancelled")

    result = transition_task(db_session, task_id, run_id, token, "running", "success")

    assert result == "conflict"
    assert db_session.execute(
        text("SELECT status FROM upload_tasks WHERE id=:id"), {"id": task_id}
    ).scalar_one() == "cancelled"
    assert db_session.execute(
        text("SELECT status FROM processing_runs WHERE id=:id"), {"id": run_id}
    ).scalar_one() == "cancelled"


def test_task_and_run_must_match_before_transition(db_session: Session) -> None:
    task_id, run_id, token = _seed_pair(db_session, "pending", run_status="running")

    result = transition_task(db_session, task_id, run_id, token, "pending", "running")

    assert result == "conflict"
    assert db_session.execute(
        text("SELECT status FROM upload_tasks WHERE id=:id"), {"id": task_id}
    ).scalar_one() == "pending"
    assert db_session.execute(
        text("SELECT status FROM processing_runs WHERE id=:id"), {"id": run_id}
    ).scalar_one() == "running"
