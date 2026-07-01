from pathlib import Path

from app.config import get_settings
from app.workers.dispatch import dispatch_batch_task


def test_desktop_mode_uses_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.is_desktop is True
    assert settings.DATABASE_URL.startswith("sqlite:///")
    assert Path(settings.files_dir).parent == tmp_path


def test_watch_dir_respects_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCH_DIR", str(tmp_path / "custom_watch"))
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.watch_dir == tmp_path / "custom_watch"


def test_watch_dir_defaults_under_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.watch_dir == tmp_path / "watch"


def test_dispatch_uses_local_queue_in_desktop_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    get_settings.cache_clear()

    task_id = 123
    dispatch_batch_task(
        task_id=task_id,
        zip_path=tmp_path / "test.zip",
        batch_no="TEST001",
        mapping_id=1,
    )

    from app.workers.local_queue import get_local_queue

    pending = get_local_queue().list_pending()
    assert any(item.task_id == task_id for item in pending)
