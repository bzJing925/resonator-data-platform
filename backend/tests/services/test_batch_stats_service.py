"""批次统计服务单元测试。"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from app.services.batch_stats_service import get_batch_stats, refresh_mv_batch_stats


def test_refresh_mv_batch_stats_success() -> None:
    """刷新物化视图成功时提交。"""
    db = MagicMock(spec=Session)
    refresh_mv_batch_stats(db)
    db.execute.assert_called_once()
    assert "REFRESH MATERIALIZED VIEW" in str(db.execute.call_args[0][0])
    db.commit.assert_called_once()


def test_refresh_mv_batch_stats_failure_silent() -> None:
    """刷新物化视图失败时不抛异常。"""
    db = MagicMock(spec=Session)
    db.execute.side_effect = RuntimeError("mv 不存在")
    refresh_mv_batch_stats(db)  # 不应抛异常


def _make_mock_session(mv_result: dict[str, Any] | None = None) -> MagicMock:
    db = MagicMock(spec=Session)
    if mv_result is not None:
        db.execute.return_value.mappings.return_value.all.return_value = [mv_result]
    else:
        db.execute.return_value.mappings.return_value.all.return_value = []
    return db


def test_get_batch_stats_from_mv() -> None:
    """优先从物化视图读取统计。"""
    db = _make_mock_session({"pass_count": 8, "fs_mean": 1.85, "fs_median": 1.86})
    stats = get_batch_stats(db, batch_id=1, total_dev=10)
    assert stats["fs_ghz_mean"] == 1.85
    assert stats["fs_ghz_median"] == 1.86
    assert stats["pass_rate"] == 0.8


def test_get_batch_stats_zero_devices() -> None:
    """device 数为 0 时 pass_rate 为 None。"""
    db = _make_mock_session({"pass_count": 0, "fs_mean": None, "fs_median": None})
    stats = get_batch_stats(db, batch_id=1, total_dev=0)
    assert stats["fs_ghz_mean"] is None
    assert stats["pass_rate"] is None
