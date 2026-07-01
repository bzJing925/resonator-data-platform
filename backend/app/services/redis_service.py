"""Redis 客户端工厂。

提供单一入口创建 Redis 连接；连接失败时返回 None，让业务层降级走 DB。
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings


def get_redis_client() -> Any | None:
    """惰性获取 Redis 连接；Redis 故障时返回 None。"""
    try:
        from redis import Redis

        return Redis.from_url(
            get_settings().REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    except Exception:
        return None
