"""单文件计算服务单元测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from app.core.extract import ExtractError
from app.models import Batch, Device
from app.services.compute_service import (
    _upsert_device,
    _wafer_from_batch_no,
    compute_single_device,
    sync_batch_device_count,
)


def test_wafer_from_batch_no() -> None:
    """从 batch_no 末尾提取 wafer 编号。"""
    assert _wafer_from_batch_no("T8901P.01") == 1
    assert _wafer_from_batch_no("T8901P.12") == 12
    assert _wafer_from_batch_no("no_wafer") is None


def test_upsert_device_insert() -> None:
    """不存在 Device 时插入新行。"""
    db = MagicMock(spec=Session)
    db.scalar.return_value = None

    batch = MagicMock(spec=Batch)
    batch.id = 7

    row = {
        "batch_id": 7,
        "original_filename": "test.s1p",
        "s_param_path": "test.s1p",
        "s_param_port": "S11",
        "fs_ghz": 1.8,
    }

    _upsert_device(db, batch, "test.s1p", row)
    db.add.assert_called_once()
    db.commit.assert_called_once()
    db.refresh.assert_called_once()


def test_upsert_device_update() -> None:
    """存在 Device 时更新现有行。"""
    db = MagicMock(spec=Session)
    existing = MagicMock(spec=Device)
    existing.id = 42
    db.scalar.return_value = existing

    batch = MagicMock(spec=Batch)
    batch.id = 7

    row = {
        "batch_id": 7,
        "original_filename": "test.s1p",
        "s_param_path": "test.s1p",
        "s_param_port": "S11",
        "fs_ghz": 1.9,
    }

    _upsert_device(db, batch, "test.s1p", row)
    assert existing.fs_ghz == 1.9
    db.add.assert_not_called()
    db.commit.assert_called_once()


def test_compute_single_device_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """正常提参并入库。"""
    batch = MagicMock(spec=Batch)
    batch.id = 7
    batch.batch_no = "T8901P.01"
    batch.mapping_id = 3

    mapping = MagicMock()
    mapping.file_path = "/tmp/mapping.xlsx"

    db = MagicMock(spec=Session)
    db.get.return_value = mapping
    db.scalar.return_value = None  # 无 existing device

    def _fake_extract(*_args, **_kwargs):
        return {
            "original_filename": "test.s1p",
            "s_param_path": "test.s1p",
            "s_param_port": "S11",
            "fs_ghz": 1.8,
        }

    monkeypatch.setattr("app.services.compute_service.extract_resonator_params", _fake_extract)
    monkeypatch.setattr("app.services.compute_service.load_mapping", lambda _path: {})

    device = compute_single_device(
        db,
        batch=batch,
        relpath="test.s1p",
        target_path=Path("/tmp/test.s1p"),
    )
    assert device is not None
    db.commit.assert_called()


def test_compute_single_device_extract_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """extract_resonator_params 抛 ExtractError 时直接向上传播。"""
    batch = MagicMock(spec=Batch)
    batch.id = 7
    batch.batch_no = "T8901P.01"
    batch.mapping_id = None

    db = MagicMock(spec=Session)

    def _fake_extract(*_args, **_kwargs):
        raise ExtractError("拟合失败")

    monkeypatch.setattr("app.services.compute_service.extract_resonator_params", _fake_extract)

    with pytest.raises(ExtractError):
        compute_single_device(
            db,
            batch=batch,
            relpath="test.s1p",
            target_path=Path("/tmp/test.s1p"),
        )


def test_sync_batch_device_count() -> None:
    """重新统计并更新批次 device 数。"""
    db = MagicMock(spec=Session)
    db.scalar.return_value = 15
    count = sync_batch_device_count(db, batch_id=7)
    assert count == 15
    db.commit.assert_called_once()
