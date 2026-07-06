from __future__ import annotations

import pytest


def test_upload_task_accepts_deembed_stage(db):
    from app.models import UploadTask

    t = UploadTask(batch_no="T.01", stage="deembed", stage_progress_pct=50)
    db.add(t)
    db.commit()
    db.refresh(t)
    assert t.stage == "deembed"


def test_upload_task_rejects_invalid_stage(db):
    from sqlalchemy.exc import IntegrityError

    from app.models import UploadTask

    t = UploadTask(batch_no="T.02", stage="unknown")
    db.add(t)
    with pytest.raises(IntegrityError):
        db.commit()
