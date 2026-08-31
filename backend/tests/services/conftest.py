"""Services 测试共享配置。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.base import Base


@pytest.fixture
def cleanup_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """将 cleanup_service 使用的 files_dir 指向临时目录，避免写入真实 DATA_ROOT。"""
    settings = Settings(DATA_ROOT=tmp_path / "data")
    monkeypatch.setattr("app.services.cleanup_service.get_settings", lambda: settings)
    return settings


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Session:
    """基于内存 SQLite 的会话，每个测试独立。"""
    settings = Settings(DATA_ROOT=tmp_path / "data")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_local()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
