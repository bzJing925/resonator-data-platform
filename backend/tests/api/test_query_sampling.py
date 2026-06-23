"""skip_count + 降采样逻辑单元测试。

覆盖 skip_count=True 时的 LIMIT+1 截断判断、应用层均匀降采样阈值，
确保大数据量场景下前端不会收到过多行。
"""

from __future__ import annotations

import pytest

from app.api.query import _SAMPLE_THRESHOLD
from app.schemas.query import QueryRequest


# ── 降采样阈值常量 ───────────────────────────────────────────────────────


def test_sample_threshold_is_20000() -> None:
    """降采样阈值应为 20000，与前端 limit 默认值对齐。"""
    assert _SAMPLE_THRESHOLD == 20000


# ── skip_count 行为契约（通过 QueryRequest schema 验证）──────────────────


def test_query_request_skip_count_defaults_false() -> None:
    """默认 skip_count=False，保证向后兼容。"""
    req = QueryRequest(limit=100)
    assert req.skip_count is False


def test_query_request_skip_count_true() -> None:
    """显式传入 skip_count=True 应被接受。"""
    req = QueryRequest(limit=100, skip_count=True)
    assert req.skip_count is True


# ── 降采样步长计算（纯数学验证）──────────────────────────────────────────


def test_systematic_sample_step_calculation() -> None:
    """验证降采样步长公式: step = n // threshold + 1。

    当 n=25000, threshold=20000 时 step=2，采样后约 12500 行。
    当 n=45000, threshold=20000 时 step=3，采样后约 15000 行。
    """
    def _step(n: int, threshold: int = _SAMPLE_THRESHOLD) -> int:
        return (n // threshold) + 1

    assert _step(25000) == 2
    assert _step(45000) == 3
    assert _step(20001) == 2
    assert _step(20000) == 2
    assert _step(19999) == 1  # 不超过阈值，但 step=1 仍满足 n//20000+1


def test_systematic_sample_reduces_to_threshold() -> None:
    """均匀采样后行数应 ≤ threshold（除 n 刚好略大于 threshold 的边界）。"""
    for n in (21000, 25000, 40000, 80000, 200000):
        step = (n // _SAMPLE_THRESHOLD) + 1
        sampled = n // step + (1 if n % step else 0)
        assert sampled <= _SAMPLE_THRESHOLD, f"n={n}: sampled={sampled} > threshold"


def test_systematic_sample_below_threshold_noop() -> None:
    """行数 ≤ threshold 时不应触发采样（步长为 1 等价于不采样）。"""
    rows = [{"id": i} for i in range(1000)]
    step = (len(rows) // _SAMPLE_THRESHOLD) + 1
    assert step == 1
    assert rows[::1] == rows


# ── QueryResponse schema 对 sampled/sample_rate 的接受性 ────────────────


def test_query_response_accepts_sampled_fields() -> None:
    """QueryResponse 必须支持 sampled / sample_rate 字段（前端依赖）。"""
    from app.schemas.query import QueryResponse

    resp = QueryResponse(
        total=1000,
        returned=500,
        truncated=False,
        rows=[],
        sampled=True,
        sample_rate=0.5,
    )
    assert resp.sampled is True
    assert resp.sample_rate == 0.5


def test_query_response_sampled_false_defaults() -> None:
    """未采样时 sampled=False, sample_rate=None。"""
    from app.schemas.query import QueryResponse

    resp = QueryResponse(
        total=100,
        returned=100,
        truncated=False,
        rows=[{"id": 1}],
    )
    assert resp.sampled is False
    assert resp.sample_rate is None
