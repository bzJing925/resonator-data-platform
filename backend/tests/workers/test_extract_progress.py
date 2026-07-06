import zipfile

import pytest

from app.workers.cancel import TaskCancelledError
from app.workers.extract_batch import _extract_zip


def test_zipfile_progress_callback(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workers.extract_batch._find_7z", lambda: None)
    zip_path = tmp_path / "sample.zip"
    target_dir = tmp_path / "out"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.s1p", "dummy" * 100)
        zf.writestr("b.s1p", "dummy" * 100)
    progress = []
    _extract_zip(zip_path, target_dir, progress_callback=lambda c, t: progress.append((c, t)))
    assert progress[-1] == (2, 2)
    assert len(progress) >= 2


def test_zipfile_cancellation_aborts_extraction(tmp_path, monkeypatch):
    monkeypatch.setattr("app.workers.extract_batch._find_7z", lambda: None)
    zip_path = tmp_path / "sample.zip"
    target_dir = tmp_path / "out"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for i in range(5):
            zf.writestr(f"{i}.s1p", "dummy" * 100)

    calls = 0

    def _raise_after_two(task_id):
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise TaskCancelledError()

    monkeypatch.setattr("app.workers.extract_batch.raise_if_cancelled", _raise_after_two)
    with pytest.raises(TaskCancelledError):
        _extract_zip(zip_path, target_dir, upload_task_id=1)
    # 只应完成部分解压（zipfile 在 extract 成员前检查取消）
    assert len(list(target_dir.rglob("*.s1p"))) < 5
