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


def test_desktop_schema_migration_from_v3_allows_remap_kind(monkeypatch, tmp_path):
    """v3 桌面库升级到 v4 后，upload_tasks.kind 约束应接受 'remap' 且保留旧数据。"""
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    get_settings.cache_clear()

    db_path = tmp_path / "aln-data.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    # 模拟 v3 schema（kind 约束不含 remap，与 _migrate_v2_to_v3 的产物一致）
    with engine.connect() as conn:
        conn.execute(
            text("""
                CREATE TABLE upload_tasks (
                    id INTEGER NOT NULL PRIMARY KEY,
                    batch_no TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    progress_pct SMALLINT NOT NULL DEFAULT 0,
                    stage TEXT NOT NULL DEFAULT 'extract',
                    stage_progress_pct SMALLINT NOT NULL DEFAULT 0,
                    progress_msg TEXT,
                    error_msg TEXT,
                    started_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    finished_at DATETIME,
                    cancelled_at DATETIME,
                    celery_task_id TEXT,
                    kind TEXT NOT NULL DEFAULT 'upload',
                    CONSTRAINT ck_uptask_status
                        CHECK (status IN ('pending','running','success','failed','cancelled')),
                    CONSTRAINT ck_uptask_stage
                        CHECK (stage IN ('extract','deembed','metrics','done','failed')),
                    CONSTRAINT ck_uptask_kind
                        CHECK (kind IN ('upload','reextract','redeembed','recompute')),
                    CONSTRAINT ck_uptask_progress CHECK (progress_pct BETWEEN 0 AND 100),
                    CONSTRAINT ck_uptask_stage_progress
                        CHECK (stage_progress_pct BETWEEN 0 AND 100)
                )
            """)
        )
        conn.execute(
            text("""
                INSERT INTO upload_tasks
                    (id, batch_no, status, progress_pct, stage, stage_progress_pct, kind)
                VALUES (1, 'BATCH001', 'success', 100, 'done', 100, 'recompute')
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
        conn.execute(text("INSERT INTO _aln_schema_version (version) VALUES (3)"))
        conn.commit()

    init_desktop_environment()

    with engine.connect() as conn:
        # 迁移后应能插入 remap 任务（v3 约束下会 IntegrityError）
        conn.execute(
            text("""
                INSERT INTO upload_tasks
                    (id, batch_no, status, progress_pct, stage, stage_progress_pct, kind)
                VALUES (2, 'BATCH001', 'pending', 0, 'extract', 0, 'remap')
            """)
        )
        conn.commit()
        assert conn.execute(text("SELECT kind FROM upload_tasks WHERE id = 2")).scalar() == "remap"
        # 旧数据保留，kind 值不被改写
        row = conn.execute(
            text("SELECT batch_no, kind FROM upload_tasks WHERE id = 1")
        ).one()
        assert row.batch_no == "BATCH001"
        assert row.kind == "recompute"

        version = conn.execute(
            text("SELECT version FROM _aln_schema_version ORDER BY version DESC LIMIT 1")
        ).scalar()
        assert version == SCHEMA_VERSION


def test_desktop_fresh_db_allows_remap_kind(monkeypatch, tmp_path):
    """全新桌面库（create_all 路径）的 kind 约束也应接受 'remap'，与迁移产物保持一致。"""
    monkeypatch.setenv("ALN_DESKTOP_MODE", "true")
    monkeypatch.setenv("ALN_DESKTOP_DIR", str(tmp_path))
    get_settings.cache_clear()

    init_desktop_environment()

    engine = create_engine(
        f"sqlite:///{tmp_path / 'aln-data.db'}", connect_args={"check_same_thread": False}
    )
    with engine.connect() as conn:
        conn.execute(
            text("""
                INSERT INTO upload_tasks
                    (id, batch_no, status, progress_pct, stage, stage_progress_pct, kind)
                VALUES (1, 'BATCH001', 'pending', 0, 'extract', 0, 'remap')
            """)
        )
        conn.commit()
        assert conn.execute(text("SELECT kind FROM upload_tasks WHERE id = 1")).scalar() == "remap"


def test_sqlite_connect_sets_wal_pragmas(tmp_path):
    """SQLite 连接事件监听器应启用 WAL + synchronous=NORMAL（修桌面版并发锁死）。"""
    from sqlalchemy import event

    from app.db import _set_sqlite_pragmas

    engine = create_engine(f"sqlite:///{tmp_path / 'wal.db'}")
    event.listen(engine, "connect", _set_sqlite_pragmas)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        # synchronous: 0=OFF 1=NORMAL 2=FULL
        assert conn.exec_driver_sql("PRAGMA synchronous").scalar() == 1


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
