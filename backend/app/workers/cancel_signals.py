"""A queued Celery revoke acknowledgment proves this delivery never started."""

from celery.signals import task_revoked
from sqlalchemy import select
from sqlalchemy.orm import Session


@task_revoked.connect
def revoked_before_start(request=None, terminated=False, **kwargs):
    if terminated or request is None:
        return  # Forced termination is not sufficient evidence of a joined process.
    from app.db import engine
    from app.models import ProcessingRun
    from app.services.processing_cancel import acknowledge_cancelled_delivery

    delivery_id = getattr(request, "id", None)
    with Session(engine) as db:
        runs = [
            (run.id, run.lease_token)
            for run in db.scalars(
                select(ProcessingRun).where(ProcessingRun.status == "cancel_requested")
            )
            if delivery_id in run.executor_ids.values()
            and run.dispatch_phase in {"queued", "extract_handoff"}
        ]
    for run_id, token in runs:
        acknowledge_cancelled_delivery(engine, run_id, token)
