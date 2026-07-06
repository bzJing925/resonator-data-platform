"""桌面模式环境初始化：目录、SQLite 建表、schema 版本。"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.models import Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


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


def _init_schema(engine) -> None:
    with engine.connect() as conn:
        current = _schema_version(conn)
        # schema 版本不一致时重建表（桌面开发模式数据可重新生成）
        if current is not None and current != SCHEMA_VERSION:
            logger.info(
                "桌面数据库 schema 版本 %s 与当前 %s 不一致，重建表", current, SCHEMA_VERSION
            )
            Base.metadata.drop_all(bind=engine)
            current = None

    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
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
        if current is None:
            conn.execute(
                text("INSERT INTO _aln_schema_version (version) VALUES (:v)"),
                {"v": SCHEMA_VERSION},
            )
        else:
            conn.execute(
                text(
                    "INSERT INTO _aln_schema_version (version) VALUES (:v) "
                    "ON CONFLICT(version) DO NOTHING"
                ),
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
