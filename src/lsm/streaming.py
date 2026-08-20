"""SSE fan-out: replay buffered tokens then tail live events, with reconnect."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as aioredis

from . import redis_keys as keys
from .job_store import JobStore
from .models import JobRecord, StreamEvent, StreamEventType


def _sse(event: str, data: Any, event_id: str | None = None) -> dict[str, str]:
    msg = {"event": event, "data": json.dumps(data)}
    if event_id is not None:
        msg["id"] = event_id
    return msg


def _token_event(index: int, delta: str | None) -> dict[str, str]:
    return _sse("token", {"index": index, "delta": delta}, event_id=str(index))


def _terminal_event(record: JobRecord) -> dict[str, str]:
    if record.status.value == "failed":
        return _sse("error", {"status": record.status.value, "error": record.error})
    return _sse("done", {"status": record.status.value, "result": record.result})


async def stream_job_events(
    redis: aioredis.Redis, store: JobStore, job_id: str, last_index: int = -1
) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events for a job: replay tokens after ``last_index`` then tail live.

    Passing ``last_index`` (e.g. from a ``Last-Event-ID`` header) lets a client
    reconnect without receiving duplicate tokens.
    """
    record = await store.get(job_id)
    if record is None:
        yield _sse("error", {"error": "job not found", "job_id": job_id})
        return

    pubsub = redis.pubsub()
    await pubsub.subscribe(keys.job_events_channel(job_id))
    try:
        # Subscribe first, then replay the buffer so nothing is lost in the gap.
        for index, delta in enumerate(await store.get_tokens(job_id)):
            if index > last_index:
                yield _token_event(index, delta)
                last_index = index

        record = await store.get(job_id)
        if record is not None and record.is_terminal:
            for index, delta in enumerate(await store.get_tokens(job_id)):
                if index > last_index:
                    yield _token_event(index, delta)
                    last_index = index
            yield _terminal_event(record)
            return

        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            event = StreamEvent.model_validate_json(message["data"])
            if event.type == StreamEventType.TOKEN:
                if event.index is not None and event.index <= last_index:
                    continue
                if event.index is not None:
                    last_index = event.index
                yield _token_event(last_index, event.delta)
            elif event.type == StreamEventType.STATUS:
                status = event.status.value if event.status else None
                yield _sse("status", {"status": status})
            elif event.type in (StreamEventType.DONE, StreamEventType.ERROR):
                final = await store.get(job_id)
                yield _terminal_event(final) if final else _sse(event.type.value, event.data)
                return
    finally:
        await pubsub.unsubscribe(keys.job_events_channel(job_id))
        await pubsub.aclose()


async def wait_for_completion(
    redis: aioredis.Redis, store: JobStore, job_id: str, timeout: float
) -> JobRecord | None:
    """Block until the job reaches a terminal state or ``timeout`` elapses."""
    record = await store.get(job_id)
    if record is None:
        return None
    if record.is_terminal:
        return record

    pubsub = redis.pubsub()
    await pubsub.subscribe(keys.job_events_channel(job_id))
    try:
        record = await store.get(job_id)
        if record is not None and record.is_terminal:
            return record
        deadline = time.monotonic() + timeout
        while (remaining := deadline - time.monotonic()) > 0:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=remaining
            )
            if message is None:
                continue
            event = StreamEvent.model_validate_json(message["data"])
            if event.type in (StreamEventType.DONE, StreamEventType.ERROR):
                return await store.get(job_id)
        return await store.get(job_id)
    finally:
        await pubsub.unsubscribe(keys.job_events_channel(job_id))
        await pubsub.aclose()
