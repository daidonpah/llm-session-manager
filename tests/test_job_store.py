"""Tests for the Redis-backed JobStore."""

from __future__ import annotations

import time

from lsm.job_store import JobStore
from lsm.models import JobMode, JobRecord, JobStatus, JobType


def _record(job_id: str = "job1", **overrides) -> JobRecord:
    base = dict(
        id=job_id,
        type=JobType.CHAT,
        status=JobStatus.QUEUED,
        mode=JobMode.ASYNC,
        stream=True,
        model="local-model",
        created_at=time.time(),
    )
    base.update(overrides)
    return JobRecord(**base)


async def test_create_and_get_roundtrip(store: JobStore) -> None:
    await store.create(_record())
    got = await store.get("job1")
    assert got is not None
    assert got.id == "job1"
    assert got.type == JobType.CHAT
    assert got.status == JobStatus.QUEUED
    assert got.stream is True


async def test_get_missing_returns_none(store: JobStore) -> None:
    assert await store.get("nope") is None


async def test_update_fields_and_clear(store: JobStore) -> None:
    await store.create(_record())
    await store.update("job1", status=JobStatus.RUNNING, attempts=2)
    got = await store.get("job1")
    assert got.status == JobStatus.RUNNING
    assert got.attempts == 2


async def test_finalize_completed_sets_result(store: JobStore) -> None:
    await store.create(_record())
    await store.finalize("job1", JobStatus.COMPLETED, result={"ok": True})
    got = await store.get("job1")
    assert got.status == JobStatus.COMPLETED
    assert got.result == {"ok": True}
    assert got.is_terminal


async def test_cancel_flag(store: JobStore) -> None:
    await store.create(_record())
    assert await store.is_cancel_requested("job1") is False
    assert await store.request_cancel("job1") is True
    assert await store.is_cancel_requested("job1") is True


async def test_request_cancel_missing_job(store: JobStore) -> None:
    assert await store.request_cancel("ghost") is False


async def test_token_buffer_append_and_read(store: JobStore) -> None:
    await store.create(_record())
    assert await store.append_token("job1", "he") == 0
    assert await store.append_token("job1", "llo") == 1
    assert await store.get_tokens("job1") == ["he", "llo"]


async def test_list_orders_newest_first(store: JobStore) -> None:
    await store.create(_record("a", created_at=1.0))
    await store.create(_record("b", created_at=2.0))
    await store.create(_record("c", created_at=3.0))
    ids = [r.id for r in await store.list(limit=10)]
    assert ids == ["c", "b", "a"]


async def test_list_respects_limit(store: JobStore) -> None:
    for i in range(5):
        await store.create(_record(f"j{i}", created_at=float(i)))
    assert len(await store.list(limit=3)) == 3
