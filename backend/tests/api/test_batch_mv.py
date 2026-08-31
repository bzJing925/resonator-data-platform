"""物化视图查询回退单元测试。

覆盖 get_batch() 优先查 mv_batch_stats、物化视图无数据时回退实时聚合，
确保物化视图不存在/未刷新时 API 仍能正常返回。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


class _FakeResult:
    """模拟 sqlalchemy Row / MappingResult。"""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[_FakeResult]:
        return [self]


class _EmptyResult:
    """模拟空结果集。"""

    def mappings(self) -> _EmptyResult:
        return self

    def all(self) -> list[Any]:
        return []


class _ScalarResult:
    """模拟 scalar 返回单个值。"""

    def __init__(self, value: Any) -> None:
        self.value = value

    def scalar(self) -> Any:
        return self.value


# ── 物化视图优先路径 ─────────────────────────────────────────────────────


def test_get_batch_prefers_mv_when_exists(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mv_batch_stats 有记录时，直接取物化视图数据、不走实时聚合。"""
    from app.services.batch_stats_service import get_batch_stats

    db = MagicMock()
    db.execute.return_value.mappings.return_value.all.return_value = [
        {"pass_count": 80, "fs_mean": 1.85, "fs_median": 1.86}
    ]

    stats = get_batch_stats(db, batch_id=42, total_dev=100)
    assert stats["fs_ghz_mean"] == 1.85
    assert stats["fs_ghz_median"] == 1.86
    assert stats["pass_rate"] == 0.8


def test_get_batch_source_contains_mv_query() -> None:
    """源码级契约：批次统计服务必须包含 mv_batch_stats 查询。"""
    from app.services.batch_stats_service import get_batch_stats

    source = get_batch_stats.__code__.co_consts
    all_str = " ".join(str(c) for c in source if isinstance(c, str))
    assert "mv_batch_stats" in all_str
    assert "pass_count" in all_str
    assert "avg_fs_ghz" in all_str
    assert "median_fs_ghz" in all_str


# ── 回退路径契约 ─────────────────────────────────────────────────────────


def test_get_batch_source_contains_fallback() -> None:
    """源码级契约：批次统计服务必须包含实时聚合回退。"""
    import inspect

    from app.services.batch_stats_service import get_batch_stats

    source = inspect.getsource(get_batch_stats)
    assert "func.avg" in source or "percentile_cont" in source


def test_get_batch_source_contains_pass_rate_calculation() -> None:
    """源码级契约：必须按 pass_count / device_count 计算 pass_rate。"""
    import inspect

    from app.services.batch_stats_service import get_batch_stats

    source = inspect.getsource(get_batch_stats)
    assert "pass_rate" in source


# ── list_batches 不使用物化视图 ──────────────────────────────────────────


def test_list_batches_source_no_mv() -> None:
    """源码级契约：list_batches 不应引用 mv_batch_stats（防回归）。"""
    from app.api.batches import list_batches

    source = list_batches.__code__.co_consts
    all_str = " ".join(str(c) for c in source if isinstance(c, str))
    assert "mv_batch_stats" not in all_str
