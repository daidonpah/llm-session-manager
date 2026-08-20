"""Redis-backed persistence for jobs, token buffers and stream events."""

from __future__ import annotations

import json
import time
from enum import Enum
from typing import Any

import redis.asyncio as aioredis

from . import redis_keys as keys
from .config import settings
from .models import JobRecord, JobStatus, StreamEvent, StreamEventType

_JSON_FIELDS = {"result"}
_BOOL_FIELDS = {"stream", "cancel_requested"}


def _encode(field: str, value: Any) -> str:
    if field in _JSON_FIELDS:
        return json.dumps(value)
    if field in _BOOL_FIELDS:
        return "1" if value else "0"
    if isinstance(value, Enum):
        return value.value
    return str(value)


def _record_from_hash(h: dict[str, str]) -> JobRecord:
    return JobRecord(
        id=h["id"],
        type=h["type"],
        status=h["status"],
        mode=h["mode"],
        stream=h.get("stream") == "1",
        model=h.get("model") or None,
        created_at=float(h["created_at"]),
        started_at=float(h["started_at"]) if h.get("started_at") else None,
        finished_at=float(h["finished_at"]) if h.get("finished_at") else None,
        attempts=int(h.get("attempts", 0)),
        error=h.get("error") or None,
        result=json.loads(h["result"]) if h.get("result") else None,
        cancel_requested=h.get("cancel_requested") == "1",
    )


class JobStore:
    """Thin async facade over the Redis data structures backing a job."""

    def __init__(self, redis: aioredis.Redis) -> None:
        self.redis = redis

    async def create(self, record: JobRecord) -> None:
        mapping = {
            k: _encode(k, v)
            for k, v in record.model_dump().items()
            if v is not None
        }
        key = keys.job_key(record.id)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, settings.job_ttl_s)
            pipe.zadd(keys.JOBS_INDEX, {record.id: record.created_at})
            await pipe.execute()

    async def get(self, job_id: str) -> JobRecord | None:
        h = await self.redis.hgetall(keys.job_key(job_id))
        return _record_from_hash(h) if h else None

    async def update(self, job_id: str, **fields: Any) -> None:
        mapping = {k: _encode(k, v) for k, v in fields.items() if v is not None}
        drop = [k for k, v in fields.items() if v is None]
        key = keys.job_key(job_id)
        if mapping:
            await self.redis.hset(key, mapping=mapping)
        if drop:
            await self.redis.hdel(key, *drop)

    async def list(self, limit: int = 50) -> list[JobRecord]:
        ids = await self.redis.zrevrange(keys.JOBS_INDEX, 0, max(limit - 1, 0))
        records: list[JobRecord] = []
        for job_id in ids:
            record = await self.get(job_id)
            if record is not None:
                records.append(record)
        return records

    async def request_cancel(self, job_id: str) -> bool:
        if not await self.redis.exists(keys.job_key(job_id)):
            return False
        await self.update(job_id, cancel_requested=True)
        await self.publish_event(
            job_id, StreamEvent(type=StreamEventType.STATUS, status=JobStatus.CANCELLED)
        )
        return True

    async def is_cancel_requested(self, job_id: str) -> bool:
        val = await self.redis.hget(keys.job_key(job_id), "cancel_requested")
        return val == "1"

    async def append_token(self, job_id: str, delta: str) -> int:
        tokens_key = keys.job_tokens_key(job_id)
        index = await self.redis.rpush(tokens_key, delta) - 1
        await self.redis.expire(tokens_key, settings.token_buffer_ttl_s)
        await self.publish_event(
            job_id,
            StreamEvent(type=StreamEventType.TOKEN, index=index, delta=delta),
        )
        return index

    async def get_tokens(self, job_id: str) -> list[str]:
        return await self.redis.lrange(keys.job_tokens_key(job_id), 0, -1)

    async def publish_event(self, job_id: str, event: StreamEvent) -> None:
        await self.redis.publish(keys.job_events_channel(job_id), event.model_dump_json())

    async def finalize(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: Any | None = None,
        error: str | None = None,
    ) -> None:
        await self.update(
            job_id, status=status, finished_at=time.time(), result=result, error=error
        )
        key = keys.job_key(job_id)
        await self.redis.expire(key, settings.job_ttl_s)
        await self.redis.expire(keys.job_tokens_key(job_id), settings.token_buffer_ttl_s)
        if status == JobStatus.FAILED:
            event = StreamEvent(type=StreamEventType.ERROR, status=status, data=error)
        else:
            event = StreamEvent(type=StreamEventType.DONE, status=status, data=result)
        await self.publish_event(job_id, event)
