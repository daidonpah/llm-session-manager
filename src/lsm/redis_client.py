"""Helpers for creating the string-decoded Redis client used by the store.

ARQ manages its own (pickle-based) connection; this client is dedicated to the
application's own job state, token buffers and pub/sub channels.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from .config import settings


def create_redis(url: str | None = None) -> aioredis.Redis:
    """Create a decoded-responses Redis client backed by a connection pool."""
    return aioredis.Redis.from_url(
        url or settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
