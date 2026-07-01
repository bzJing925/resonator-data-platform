"""数据库引擎 + Session 工厂 + FastAPI Depends。"""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

_engine_kwargs = {"pool_pre_ping": True, "future": True}
if _settings.resolved_database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(
        pool_size=_settings.DB_POOL_SIZE,
        max_overflow=_settings.DB_MAX_OVERFLOW,
        pool_recycle=_settings.DB_POOL_RECYCLE,
        pool_timeout=_settings.DB_POOL_TIMEOUT,
    )

engine = create_engine(_settings.resolved_database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: 每请求一个 session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
