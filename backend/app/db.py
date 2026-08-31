"""数据库引擎 + Session 工厂 + FastAPI Depends。"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _patch_psycopg_server_version() -> None:
    """兼容部分 psycopg 3.x 环境返回 bytes 版本字符串导致 SQLAlchemy 初始化失败的问题。

    SQLAlchemy 2.0.49 的 psycopg 方言期望 ``connection.pgconn.server_version`` 是字符串，
    但某些编译版本返回 int。这里直接读取 ``info.server_version`` 整数并转成元组。
    """
    try:
        from sqlalchemy.dialects.postgresql.base import PGDialect
        from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
    except Exception:
        return

    if getattr(PGDialect, "_aln_server_version_patched", False):
        return

    original = PGDialect._get_server_version_info

    def _patched(self, connection):
        try:
            raw_conn = connection.connection.dbapi_connection
            if raw_conn is not None:
                v = raw_conn.info.server_version
                if isinstance(v, int):
                    return (v // 10000, (v // 100) % 100, v % 100)
        except Exception:
            pass
        return original(self, connection)

    PGDialect._get_server_version_info = _patched
    PGDialect_psycopg._get_server_version_info = _patched
    PGDialect._aln_server_version_patched = True

    # 兼容未设置 client_encoding 时 psycopg 将 TEXT 列以 bytes 返回的环境。
    try:
        from sqlalchemy import event
        from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg as _PGPsycopg

        @event.listens_for(_PGPsycopg, "connect")
        def _set_client_encoding(dbapi_conn, _):
            try:
                with dbapi_conn.cursor() as cur:
                    cur.execute("SET client_encoding TO 'UTF8'")
            except Exception:
                pass
    except Exception:
        pass


_patch_psycopg_server_version()

_settings = get_settings()

# 当 PostgreSQL server_encoding 为 SQL_ASCII 时，psycopg 可能把 TEXT 列以 bytes
# 返回。通过连接参数强制 client_encoding=utf8，确保字符串列正常解码。
_connect_args: dict[str, Any] = {}
if _settings.DATABASE_URL.startswith("postgresql+psycopg://"):
    _connect_args["options"] = "-c client_encoding=utf8"

engine = create_engine(
    _settings.DATABASE_URL,
    pool_pre_ping=True,
    future=True,
    pool_size=_settings.DB_POOL_SIZE,
    max_overflow=_settings.DB_MAX_OVERFLOW,
    pool_recycle=_settings.DB_POOL_RECYCLE,
    pool_timeout=_settings.DB_POOL_TIMEOUT,
    connect_args=_connect_args,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: 每请求一个 session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
