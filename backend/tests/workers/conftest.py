"""Workers 测试共享配置。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base


@pytest.fixture
def db(monkeypatch, tmp_path) -> Session:
    """Temporary file SQLite supports the worker's fresh lease sessions."""
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.sqlite'}", future=True)
    Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    monkeypatch.setattr("app.workers.cancel.SessionLocal", session_local)
    monkeypatch.setattr("app.db.SessionLocal", session_local)
    monkeypatch.setattr("app.db.engine", engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
