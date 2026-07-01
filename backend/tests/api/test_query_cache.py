"""Redis 查询缓存单元测试。

覆盖 _query_cache_key、_cache_get、_cache_set 的命中/降级/异常路径，
确保 Redis 故障时不抛异常、业务继续走 DB。
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.api.query import (
    _cache_get,
    _cache_set,
    _query_cache_key,
)
from app.schemas.query import AggregateRequest, QueryRequest


class _FakeRedis:
    """内存版 Redis，支持 get/setex。"""

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, int]] = {}
        self.call_log: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def get(self, key: str) -> str | None:
        self.call_log.append(("get", (key,), {}))
        val, _ttl = self._store.get(key, (None, 0))
        return val

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.call_log.append(("setex", (key, ttl, value), {}))
        self._store[key] = (value, ttl)


class _BrokenRedis:
    """模拟 Redis 连接异常。"""

    def get(self, _key: str) -> None:
        raise ConnectionError("Redis 挂了")

    def setex(self, *_args: Any, **_kwargs: Any) -> None:
        raise ConnectionError("Redis 挂了")


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    r = _FakeRedis()
    monkeypatch.setattr("app.api.query.get_redis_client", lambda: r)
    return r


@pytest.fixture
def broken_redis(monkeypatch: pytest.MonkeyPatch) -> _BrokenRedis:
    r = _BrokenRedis()
    monkeypatch.setattr("app.api.query.get_redis_client", lambda: r)
    return r


# ── cache key ────────────────────────────────────────────────────────────


def test_query_cache_key_stable() -> None:
    """相同请求参数 → 相同 key。"""
    req = QueryRequest(limit=10)
    k1 = _query_cache_key("devices", req)
    k2 = _query_cache_key("devices", req)
    assert k1 == k2
    assert k1.startswith("aln:query:devices:")


def test_query_cache_key_different_requests() -> None:
    """不同请求 → 不同 key。"""
    req1 = QueryRequest(limit=10)
    req2 = QueryRequest(limit=20)
    assert _query_cache_key("devices", req1) != _query_cache_key("devices", req2)


def test_distinct_cache_uses_separate_prefix() -> None:
    """distinct 请求的 key 前缀与其他查询不同。"""
    req = QueryRequest(limit=10)
    k = _query_cache_key("distinct", req)
    assert k.startswith("aln:query:distinct:")


def test_aggregate_cache_key_prefix() -> None:
    """聚合查询的 key 含 aggregate 前缀。"""
    req = AggregateRequest(
        metrics=[{"field": "fs_ghz", "agg": ["avg"]}],
        group_by=["batch_no"],
    )
    k = _query_cache_key("aggregate", req)
    assert k.startswith("aln:query:aggregate:")


# ── cache get ────────────────────────────────────────────────────────────


def test_cache_get_hit(fake_redis: _FakeRedis) -> None:
    """Redis 命中时正确反序列化 JSON。"""
    payload = {"total": 42, "rows": [{"id": 1}]}
    fake_redis._store["aln:query:devices:abc"] = (json.dumps(payload), 300)
    result = _cache_get("aln:query:devices:abc")
    assert result == payload


def test_cache_get_miss(fake_redis: _FakeRedis) -> None:
    """Redis 返回 None → 函数返回 None。"""
    assert _cache_get("aln:query:devices:not_exist") is None


def test_cache_get_redis_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 连接失败 → 静默降级返回 None。"""
    monkeypatch.setattr("app.api.query.get_redis_client", lambda: None)
    assert _cache_get("any_key") is None


def test_cache_get_broken_redis_returns_none(broken_redis: _BrokenRedis) -> None:
    """Redis 抛异常 → 不抛到上层，返回 None。"""
    assert _cache_get("aln:query:devices:x") is None


# ── cache set ────────────────────────────────────────────────────────────


def test_cache_set_success(fake_redis: _FakeRedis) -> None:
    """正常写入 Redis，TTL 正确。"""
    payload = {"total": 5, "rows": []}
    _cache_set("aln:query:devices:abc", payload, ttl=300)
    assert fake_redis.call_log == [
        ("setex", ("aln:query:devices:abc", 300, json.dumps(payload)), {})
    ]
    raw, ttl = fake_redis._store["aln:query:devices:abc"]
    assert json.loads(raw) == payload
    assert ttl == 300


def test_cache_set_redis_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 不可用 → 静默跳过，不抛异常。"""
    monkeypatch.setattr("app.api.query.get_redis_client", lambda: None)
    _cache_set("any_key", {"a": 1})  # 不应抛异常


def test_cache_set_broken_redis_silent(broken_redis: _BrokenRedis) -> None:
    """Redis 写入抛异常 → 静默吞掉，不中断业务。"""
    _cache_set("aln:query:devices:x", {"a": 1})  # 不应抛异常
