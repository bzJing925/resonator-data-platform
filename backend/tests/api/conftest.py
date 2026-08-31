"""API 测试共享配置。

覆盖 DATA_ROOT 到一个可写的临时目录，避免本地默认 /data3 只读导致上传失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """构造测试用 Settings 并替换 app.config.get_settings。"""
    from app.config import Settings

    test_settings = Settings(
        DATA_ROOT=tmp_path / "data",
        WATCH_ENABLED=False,
        ALN_DESKTOP_MODE=False,
    )
    monkeypatch.setattr("app.config.get_settings", lambda: test_settings)
    monkeypatch.setattr("app.api.tasks.get_settings", lambda: test_settings)
    monkeypatch.setattr("app.services.cleanup_service.get_settings", lambda: test_settings)
    return test_settings


@pytest.fixture
def engine(settings):
    """每个测试独立的内存 SQLite 引擎（StaticPool 支持多线程访问）。"""
    from app.models.base import Base

    test_engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db(engine) -> Session:
    """基于同一引擎的测试会话。"""
    session_local = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    session = session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(engine) -> TestClient:
    """FastAPI TestClient，每个请求从同一引擎新建会话。"""
    from app.db import get_db
    from app.main import app

    def _override_get_db():
        session_local = sessionmaker(
            bind=engine, autoflush=False, autocommit=False, future=True
        )
        session = session_local()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.clear()
