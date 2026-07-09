"""桌面模式环境初始化：目录、SQLite 建表、schema 版本。"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.models import Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3


def _ensure_dirs(root: Path) -> None:
    for sub in ("uploads", "files", "mappings", "exports", "logs", "watch"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def _schema_version(conn) -> int | None:
    try:
        return conn.execute(
            text("SELECT version FROM _aln_schema_version ORDER BY version DESC LIMIT 1")
        ).scalar()
    except Exception:
        return None


def _ensure_version_table(conn) -> None:
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS _aln_schema_version (
                version INTEGER PRIMARY KEY,
                applied_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )


def _migrate_v2_to_v3(engine) -> None:
    """为桌面版 SQLite 增加 upload_tasks.cancelled_at / kind 并更新约束。"""
    with engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE _upload_tasks_new (
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
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO _upload_tasks_new (
                    id, batch_no, status, progress_pct, stage, stage_progress_pct,
                    progress_msg, error_msg, started_at, finished_at, cancelled_at,
                    celery_task_id, kind
                )
                SELECT
                    id, batch_no, status, progress_pct, stage, stage_progress_pct,
                    progress_msg, error_msg, started_at, finished_at, NULL,
                    celery_task_id, 'upload'
                FROM upload_tasks
                """
            )
        )
        conn.execute(text("DROP TABLE upload_tasks"))
        conn.execute(text("ALTER TABLE _upload_tasks_new RENAME TO upload_tasks"))
        conn.commit()


def _init_schema(engine) -> None:
    with engine.connect() as conn:
        current = _schema_version(conn)

    if current is None:
        Base.metadata.create_all(bind=engine)
        with engine.connect() as conn:
            _ensure_version_table(conn)
            conn.execute(
                text("INSERT INTO _aln_schema_version (version) VALUES (:v)"),
                {"v": SCHEMA_VERSION},
            )
            conn.commit()
        return

    if current == SCHEMA_VERSION:
        return

    if current < SCHEMA_VERSION:
        logger.info("桌面数据库 schema 从 %s 升级到 %s", current, SCHEMA_VERSION)
        if current < 3:
            _migrate_v2_to_v3(engine)
        with engine.connect() as conn:
            _ensure_version_table(conn)
            conn.execute(
                text(
                    "INSERT INTO _aln_schema_version (version) VALUES (:v) "
                    "ON CONFLICT(version) DO NOTHING"
                ),
                {"v": SCHEMA_VERSION},
            )
            conn.commit()
        return

    # 当前版本高于代码版本时回退到重建（通常只在开发分支切换时出现）
    logger.info("桌面数据库 schema 版本 %s 高于当前 %s，重建表", current, SCHEMA_VERSION)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        _ensure_version_table(conn)
        conn.execute(
            text("INSERT INTO _aln_schema_version (version) VALUES (:v)"),
            {"v": SCHEMA_VERSION},
        )
        conn.commit()


def init_desktop_environment() -> None:
    settings = get_settings()
    if not settings.is_desktop:
        return

    root = settings.desktop_dir
    _ensure_dirs(root)

    engine = create_engine(
        settings.resolved_database_url,
        connect_args={"check_same_thread": False},
    )
    _init_schema(engine)
    engine.dispose()
    logger.info("桌面环境初始化完成: %s", root)
