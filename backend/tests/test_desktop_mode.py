from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import create_engine, text

from app.config import get_settings
from app.desktop_setup import SCHEMA_VERSION, init_desktop_environment
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


def test_init_desktop_environment_creates_dirs_and_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    get_settings.cache_clear()

    init_desktop_environment()

    assert (tmp_path / "aln-data.db").exists()
    assert (tmp_path / "uploads").exists()
    assert (tmp_path / "files").exists()
    assert (tmp_path / "mappings").exists()


def test_desktop_schema_migration_from_v2_preserves_data(monkeypatch, tmp_path):
    """桌面版 SQLite 数据库从 v2 迁移到 v3 时应保留数据并新增列。"""
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    get_settings.cache_clear()

    db_path = tmp_path / "aln-data.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    # 模拟旧的 v2 schema（缺少 cancelled_at / kind）
    with engine.connect() as conn:
        conn.execute(
            text("""
                CREATE TABLE upload_tasks (
                    id INTEGER PRIMARY KEY,
                    batch_no TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_pct INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    stage_progress_pct INTEGER NOT NULL,
                    progress_msg TEXT,
                    error_msg TEXT,
                    started_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    finished_at DATETIME,
                    celery_task_id TEXT
                )
            """)
        )
        conn.execute(
            text("""
                INSERT INTO upload_tasks
                    (id, batch_no, status, progress_pct, stage, stage_progress_pct)
                VALUES (1, 'BATCH001', 'success', 100, 'done', 100)
            """)
        )
        conn.execute(
            text("""
                CREATE TABLE _aln_schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
        )
        conn.execute(text("INSERT INTO _aln_schema_version (version) VALUES (2)"))
        conn.commit()

    init_desktop_environment()

    with engine.connect() as conn:
        inspector = sa.inspect(conn)
        columns = {c["name"] for c in inspector.get_columns("upload_tasks")}
        assert "cancelled_at" in columns
        assert "kind" in columns

        row = conn.execute(
            text("SELECT batch_no, status, kind FROM upload_tasks WHERE id = 1")
        ).one()
        assert row.batch_no == "BATCH001"
        assert row.status == "success"
        assert row.kind == "upload"

        version = conn.execute(
            text("SELECT version FROM _aln_schema_version ORDER BY version DESC LIMIT 1")
        ).scalar()
        assert version == SCHEMA_VERSION
