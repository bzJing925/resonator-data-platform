"""Celery EAGER 模式下跑通 process_batch_task 完整管线。

需要本地 PostgreSQL（已在 localhost:15432）+ 真 Redis。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.db import SessionLocal, engine
from app.models import Batch, Device, Mapping, UploadTask
from app.workers import celery_app
from app.workers.process_batch import process_batch_task


@pytest.fixture(scope="module", autouse=True)
def _eager_mode():
    """让 .delay() 在调用线程内同步执行。"""
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


@pytest.fixture
def clean_db():
    """每个测试跑前清理 devices/batches/upload_tasks/mappings。"""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE devices, batches, upload_tasks, mapping_entries, mappings "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield


@pytest.fixture
def staged_files(tmp_path: Path, sample_zip: Path, sample_mapping: Path, monkeypatch):
    """把 fixtures zip / mapping 复制到隔离的 DATA_ROOT，并 monkeypatch settings。"""
    data_root = tmp_path / "aln-data"
    (data_root / "uploads").mkdir(parents=True)
    (data_root / "files").mkdir(parents=True)
    (data_root / "mappings").mkdir(parents=True)

    # 拷贝 zip
    staged_zip = data_root / "uploads" / "T8901P.01.zip"
    shutil.copy(sample_zip, staged_zip)
    # 拷贝 mapping
    staged_mapping = data_root / "mappings" / "mapping_ELB003.xlsx"
    shutil.copy(sample_mapping, staged_mapping)

    # patch settings
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "DATA_ROOT", data_root, raising=False)
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


def test_process_batch_full_pipeline(clean_db, staged_files):
    """完整跑：插 mapping → 插 pending batch + upload_task → 触发 task → 校验入库。"""
    staged_zip, staged_mapping = staged_files
    batch_no = "T8901P.01"

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
        assert n is not None and n >= 20

        # upload_tasks 最终态
        task = db.get(UploadTask, upload_task_id)
        assert task is not None
        assert task.status == "success"
        assert task.progress_pct == 100
        assert task.finished_at is not None
    finally:
        db.close()
