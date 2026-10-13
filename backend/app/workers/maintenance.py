"""Shared periodic maintenance for Celery and native startup/background execution."""
from app.workers.celery_app import celery_app


@celery_app.task(name='app.workers.maintenance.purge_artifacts')
def purge_artifacts():
    from app.db import engine
    from app.services.artifact_lifecycle import purge_due

    return purge_due(engine)
