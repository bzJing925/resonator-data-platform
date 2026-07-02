"""桌面模式环境初始化：目录、SQLite 建表、schema 版本。"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import get_settings
from app.models import Base

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


def _ensure_dirs(root: Path) -> None:
    for sub in ("uploads", "files", "mappings", "exports", "logs", "watch"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def _init_schema(engine) -> None:
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
