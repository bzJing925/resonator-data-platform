"""PostgreSQL COPY 批量入库单元测试。

覆盖 _bulk_insert_devices 的阈值判断、COPY 调用、异常降级，
以及 _COPY_COLUMNS 与 Device 表定义的一致性。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.models import Device
from app.workers.process_batch import (
    _COPY_COLUMNS,
    _bulk_insert_devices,
    _copy_insert_devices,
)


def _make_rows(n: int) -> list[dict[str, Any]]:
    """构造 n 行符合 _COPY_COLUMNS 的测试数据。"""
    base = {
        "batch_id": 1,
        "original_filename": "test.s1p",
        "display_name": "test",
        "mark": "A",
        "wafer": 1,
        "folder_name": "S11",
        "coord": "A1",
        "x": 0.0,
        "y": 0.0,
        "eg": None,
        "fl": None,
        "ag": None,
        "pf": "Y",
        "area_n": None,
        "area_um2": None,
        "fs_ghz": 1.8,
        "fp_ghz": 2.0,
        "zs_ohm": 10.0,
        "zp_ohm": 1000.0,
        "qs": 100.0,
        "qp": 50.0,
        "qs_bodeq": 95.0,
        "qp_bodeq": 48.0,
        "dbqs": 20.0,
        "dbqp": 30.0,
        "bodeq_fitted": None,
        "bodeq_smooth": None,
        "bodeq_raw": None,
        "fbode_ghz": None,
        "k2eff_pct": 1.0,
        "fp2_ghz": None,
        "fs2_ghz": None,
        "zp2_ohm": None,
        "zs2_ohm": None,
        "deembedded": False,
        "s_param_path": "",
    }
    return [{**base, "original_filename": f"test_{i}.s1p"} for i in range(n)]


# ── _COPY_COLUMNS 一致性 ─────────────────────────────────────────────────


def test_copy_columns_match_device_table() -> None:
    """_COPY_COLUMNS 的顺序应与 Device 表列一致（排除自增 id）。"""
    device_cols = [c.name for c in Device.__table__.columns]
    assert "id" in device_cols
    assert device_cols[0] == "id"
    non_id_cols = device_cols[1:]
    assert _COPY_COLUMNS == non_id_cols


def test_copy_columns_no_id() -> None:
    """_COPY_COLUMNS 不应包含自增主键 id。"""
    assert "id" not in _COPY_COLUMNS


# ── 阈值判断 ─────────────────────────────────────────────────────────────


def test_bulk_insert_calls_copy_above_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """行数 >= 3000 时优先调用 _copy_insert_devices。"""
    copy_called = False

    def _fake_copy(_db: Session, rows: list[dict[str, Any]]) -> None:
        nonlocal copy_called
        copy_called = True

    monkeypatch.setattr(
        "app.workers.process_batch._copy_insert_devices", _fake_copy
    )
    db = MagicMock(spec=Session)
    rows = _make_rows(3000)
    _bulk_insert_devices(db, rows)
    assert copy_called is True


def test_bulk_insert_calls_orm_below_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """行数 < 3000 时直接 bulk_insert_mappings，不走 COPY。"""
    copy_called = False

    def _fake_copy(_db: Session, _rows: list[dict[str, Any]]) -> None:
        nonlocal copy_called
        copy_called = True

    monkeypatch.setattr(
        "app.workers.process_batch._copy_insert_devices", _fake_copy
    )
    db = MagicMock(spec=Session)
    rows = _make_rows(2999)
    _bulk_insert_devices(db, rows)
    assert copy_called is False
    db.bulk_insert_mappings.assert_called_once_with(Device, rows)
    db.commit.assert_called_once()


# ── 空输入 ───────────────────────────────────────────────────────────────


def test_bulk_insert_empty_rows_noop() -> None:
    """空列表 → 什么都不做、不抛异常。"""
    db = MagicMock(spec=Session)
    _bulk_insert_devices(db, [])
    db.bulk_insert_mappings.assert_not_called()
    db.commit.assert_not_called()


# ── COPY 异常降级 ────────────────────────────────────────────────────────


def test_copy_fallback_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """COPY 抛异常 → 降级到 bulk_insert_mappings，不中断。"""

    def _exploding_copy(_db: Session, _rows: list[dict[str, Any]]) -> None:
        raise RuntimeError("COPY 协议失败")

    monkeypatch.setattr(
        "app.workers.process_batch._copy_insert_devices", _exploding_copy
    )
    db = MagicMock(spec=Session)
    rows = _make_rows(3000)
    _bulk_insert_devices(db, rows)
    # 降级后调用 ORM
    db.bulk_insert_mappings.assert_called_once_with(Device, rows)
    db.commit.assert_called_once()


# ── _copy_insert_devices 直接测试（mock psycopg cursor）──────────────────


def test_copy_insert_writes_all_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    """_copy_insert_devices 应逐行写入 COPY 缓冲区。"""
    written_rows: list[tuple[Any, ...]] = []

    class FakeCopy:
        def write_row(self, row: tuple[Any, ...]) -> None:
            written_rows.append(row)

    class FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def copy(self, sql: str) -> FakeCopy:
            assert "COPY devices" in sql
            return FakeCopy()

    fake_conn = MagicMock()
    fake_conn.cursor.return_value = FakeCursor()

    fake_raw_conn = MagicMock()
    fake_raw_conn.cursor.return_value = FakeCursor()

    db = MagicMock(spec=Session)
    db.connection.return_value.connection = fake_raw_conn

    rows = _make_rows(5)
    _copy_insert_devices(db, rows)
    assert len(written_rows) == 5
    # 第一行应与 _COPY_COLUMNS 顺序一致
    first = written_rows[0]
    assert len(first) == len(_COPY_COLUMNS)
