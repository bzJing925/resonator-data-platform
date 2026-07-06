"""Workers 测试共享配置。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base


@pytest.fixture
def db(monkeypatch) -> Session:
    """基于内存 SQLite 的会话，并替换 cancel 模块使用的 SessionLocal。"""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("app.workers.cancel.SessionLocal", session_local)
    session = session_local()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
