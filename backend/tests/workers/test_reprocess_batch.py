from __future__ import annotations

import pytest

from app.models import Batch, Device, Mapping, UploadTask
from app.workers.cancel import TaskCancelledError
from app.workers.reprocess_batch import _validate_metrics, recompute_batch_task


def test_validate_metrics():
    assert _validate_metrics(["qs", "kt2"]) == ["qs", "kt2"]


def test_validate_metrics_rejects_unknown():
    import pytest

    with pytest.raises(ValueError):
        _validate_metrics(["qs", "foo"])


def test_recompute_cancellation_raises_in_loop(db, tmp_path, monkeypatch):
    """在重新计算循环中触发取消时应抛出 TaskCancelledError。"""
    # 最小对照表文件
    mapping_path = tmp_path / "mapping.csv"
    mapping_path.write_text("mark,description\nA1,EG0 FL0 700&5500\n")

    task = UploadTask(batch_no="B.01", kind="recompute")
    db.add(task)
    db.commit()
    db.refresh(task)

    mapping = Mapping(name="test", file_path=str(mapping_path))
    db.add(mapping)
    db.commit()
    db.refresh(mapping)

    batch = Batch(
        batch_no="B.01",
        mapping_id=mapping.id,
        file_path=str(tmp_path),
        deembed_method="default",
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)

    for i in range(200):
        db.add(
            Device(
                batch_id=batch.id,
                original_filename=f"dut{i}.s1p",
                s_param_path=f"dut{i}.s1p",
                s_param_port="S11",
                deembedded=False,
            )
        )
    db.commit()

    # 让测试中的 SessionLocal 返回同一个 db 会话
    monkeypatch.setattr("app.workers.reprocess_batch.SessionLocal", lambda: db)

    # 第一次调用（任务开始）不取消，第二次在循环内取消
    calls = 0

    def _raise_on_second(task_id: int) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise TaskCancelledError()

    monkeypatch.setattr("app.workers.reprocess_batch.raise_if_cancelled", _raise_on_second)
    monkeypatch.setattr(
        "app.workers.reprocess_batch.extract_resonator_params",
        lambda *args, **kwargs: {"qs": 1.0, "qp": 2.0, "k2eff_pct": 3.0},
    )

    with pytest.raises(TaskCancelledError):
        recompute_batch_task.apply(
            kwargs={
                "upload_task_id": task.id,
                "batch_no": batch.batch_no,
                "metrics": ["qs"],
            }
        ).get()
