"""Celery EAGER 模式下跑通 process_batch_task 完整管线。

需要本地 PostgreSQL（已在 localhost:15432）+ 真 Redis。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import SessionLocal
from app.models import Batch, Device, Mapping, UploadTask
from app.workers import celery_app
from app.workers.process_batch import process_batch_task
from tests.fixtures.platform_db import DATABASES, platform_database


@pytest.fixture(scope="module", autouse=True)
def _eager_mode():
    """让 .delay() 在调用线程内同步执行。"""
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


@pytest.fixture(params=DATABASES)
def clean_db(request, tmp_path, monkeypatch):
    """Dedicated SQLite file / PG schema; never truncate a configured database."""
    from app import db as database
    from app.workers import compute_batch, extract_batch

    with platform_database(request.param, tmp_path) as (db, batch):
        mapping = db.get(Mapping, batch.mapping_id)
        db.delete(batch)
        db.delete(mapping)
        db.commit()
        factory = sessionmaker(bind=db.bind)
        for module in (sys.modules[__name__], database, compute_batch, extract_batch):
            monkeypatch.setattr(module, "SessionLocal", factory)
        monkeypatch.setattr(database, "engine", db.bind)
        yield


@pytest.fixture
def staged_files(tmp_path: Path, monkeypatch):
    """Generated algorithm fixture in tmp; does not replace absent customer acceptance."""
    import pandas as pd

    from tests.fixtures.platform_cases import _algorithm_samples

    data_root = tmp_path / "aln-data"
    (data_root / "uploads").mkdir(parents=True)
    (data_root / "files").mkdir(parents=True)
    (data_root / "mappings").mkdir(parents=True)

    staged_zip = data_root / "uploads" / "T8901P.01.zip"
    samples = _algorithm_samples(tmp_path)
    with zipfile.ZipFile(staged_zip, "w") as archive:
        for index in range(20):
            archive.writestr(f"{index}_E6-1_X{index}Y0.s1p", samples["dut_s1p"])
    staged_mapping = data_root / "mappings" / "mapping_ELB003.xlsx"
    pd.DataFrame({0: ["E6-1"], 1: ["EG0 FL0 700&5500"]}).to_excel(
        staged_mapping,
        index=False,
        header=False,
    )

    # patch settings
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "DATA_ROOT", data_root, raising=False)
    monkeypatch.setattr("app.workers.cancel.SessionLocal", SessionLocal)
    return staged_zip, staged_mapping


def test_failure_path_marks_upload_task_failed(clean_db, staged_files):
    """触发 process_batch_task 跑失败：upload_tasks 必须落到 'failed' 而不是停在 'running'。

    历史 bug：异常处理里有 `try: publisher.fail() finally: pass`，publisher.fail
    自身再抛异常（比如 session 已经 invalid）时被静默吞掉，原始异常被替换，
    upload_tasks 永远停在 running。
    """
    _, staged_mapping = staged_files
    db = SessionLocal()
    try:
        # 真实 mapping（满足 batches.mapping_id NOT NULL）
        mapping_row = Mapping(
            name="mapping_for_failure_test",
            file_path=str(staged_mapping),
            entry_count=0,
        )
        db.add(mapping_row)
        db.flush()
        good_mapping_id = mapping_row.id

        batch_row = Batch(
            batch_no="nope.001",
            mapping_id=good_mapping_id,
            file_path="(pending)",
            device_count=0,
            deembedded=False,
            process_type="S1P",
            uploaded_by="test",
        )
        db.add(batch_row)
        task_row = UploadTask(batch_no="nope.001", status="pending", progress_pct=0)
        db.add(task_row)
        db.flush()
        upload_task_id = task_row.id
        db.commit()
    finally:
        db.close()

    # 任务参数里传一个不存在的 mapping_id，让 process_batch_task 第 2 步抛 RuntimeError。
    with pytest.raises(RuntimeError):
        process_batch_task.apply(
            kwargs=dict(
                upload_task_id=upload_task_id,
                zip_path="/tmp/does-not-exist.zip",
                batch_no="nope.001",
                mapping_id=999999,  # 故意不存在
                f_start_ghz=None,
                f_end_ghz=None,
                deembed_enabled=False,
                process_type="S1P",
            )
        ).get()

    db = SessionLocal()
    try:
        task = db.get(UploadTask, upload_task_id)
        assert task is not None
        assert task.status == "failed", f"upload_tasks 应落到 failed，实际 {task.status}"
        assert task.error_msg, "失败应当带 error_msg"
        assert task.finished_at is not None
    finally:
        db.close()


@pytest.mark.parametrize("fallback", [False, True])
def test_process_batch_full_pipeline(clean_db, staged_files, monkeypatch, fallback):
    """完整跑：插 mapping → 插 pending batch + upload_task → 触发 task → 校验入库。"""
    staged_zip, staged_mapping = staged_files
    batch_no = "T8901P.01"
    if fallback:
        from concurrent.futures import Future
        from types import SimpleNamespace

        class FailingPool:
            def __init__(self, **kwargs):
                self.submitted = 0

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def submit(self, fn, args):
                self.submitted += 1
                future = Future()
                if self.submitted == 1:
                    future.set_result(fn(args))
                else:
                    future.set_exception(RuntimeError("pool terminated after first result"))
                return future

        monkeypatch.setattr("concurrent.futures.ProcessPoolExecutor", FailingPool)
        monkeypatch.setattr("concurrent.futures.as_completed", lambda futures: iter(futures))
        monkeypatch.setattr("app.workers.compute_batch._PARALLEL_MIN_FILES", 1)
        monkeypatch.setattr(
            "app.workers.compute_batch.get_algorithm_config",
            lambda: SimpleNamespace(worker_extract_workers=2),
        )

    db = SessionLocal()
    try:
        # 准备 mapping 行
        mapping_row = Mapping(
            name="mapping_ELB003",
            file_path=str(staged_mapping),
            entry_count=0,
        )
        db.add(mapping_row)
        db.flush()
        mapping_id = mapping_row.id

        # 预占 batch（API 在生产中会做的事）
        batch_row = Batch(
            batch_no=batch_no,
            mapping_id=mapping_id,
            f_start_ghz=None,
            f_end_ghz=None,
            deembedded=False,
            process_type="S1P",
            file_path="(pending)",
            device_count=0,
            uploaded_by="test",
        )
        db.add(batch_row)

        # 预占 upload_task
        task_row = UploadTask(batch_no=batch_no, status="pending", progress_pct=0)
        db.add(task_row)
        db.flush()
        upload_task_id = task_row.id

        db.commit()
    finally:
        db.close()

    # 触发任务（EAGER → 同步执行）
    result = process_batch_task.apply(
        kwargs=dict(
            upload_task_id=upload_task_id,
            zip_path=str(staged_zip),
            batch_no=batch_no,
            mapping_id=mapping_id,
            f_start_ghz=None,
            f_end_ghz=None,
            deembed_enabled=False,
            process_type="S1P",
        )
    ).get()

    assert result["device_count"] >= 20, f"应至少入库 20 行，实际 {result['device_count']}"

    # 校验 DB 状态
    db = SessionLocal()
    try:
        # batches 应只有这 1 行
        batches = db.scalars(select(Batch)).all()
        assert len(batches) == 1
        assert batches[0].batch_no == batch_no
        assert batches[0].device_count >= 20

        # devices 至少 20 行
        device_count = db.scalar(select(Device).where(Device.batch_id == batches[0].id).limit(1))
        assert device_count is not None
        from sqlalchemy import func

        n = db.scalar(select(func.count(Device.id)).where(Device.batch_id == batches[0].id))
        assert n == 20  # Pool result + fallback must never double count the first device.

        # upload_tasks 最终态
        task = db.get(UploadTask, upload_task_id)
        assert task is not None
        assert task.status == "success"
        assert task.progress_pct == 100
        assert task.finished_at is not None
    finally:
        db.close()
