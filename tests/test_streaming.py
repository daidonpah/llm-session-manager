"""Tests for the SSE replay + reconnect and sync-wait helpers."""

from __future__ import annotations

import json
import time

from lsm.job_store import JobStore
from lsm.models import JobMode, JobRecord, JobStatus, JobType
from lsm.streaming import stream_job_events, wait_for_completion


async def _seed(store: JobStore, job_id: str, status: JobStatus) -> None:
    await store.create(
        JobRecord(
            id=job_id,
            type=JobType.CHAT,
            status=status,
            mode=JobMode.ASYNC,
            stream=True,
            model="m",
            created_at=time.time(),
        )
    )


async def _collect(agen) -> list[dict]:
    return [e async for e in agen]


async def test_stream_missing_job_emits_error(redis, store: JobStore) -> None:
    events = await _collect(stream_job_events(redis, store, "ghost"))
    assert events[0]["event"] == "error"


async def test_stream_replays_then_done_on_terminal(redis, store: JobStore) -> None:
    await _seed(store, "j", JobStatus.QUEUED)
    await store.append_token("j", "Hel")
    await store.append_token("j", "lo")
    await store.finalize("j", JobStatus.COMPLETED, result={"ok": 1})

    events = await _collect(stream_job_events(redis, store, "j"))
    tokens = [json.loads(e["data"])["delta"] for e in events if e["event"] == "token"]
    assert tokens == ["Hel", "lo"]
    assert events[-1]["event"] == "done"
    assert json.loads(events[-1]["data"])["result"] == {"ok": 1}


async def test_stream_reconnect_skips_seen_tokens(redis, store: JobStore) -> None:
    await _seed(store, "j", JobStatus.QUEUED)
    for part in ["a", "b", "c"]:
        await store.append_token("j", part)
    await store.finalize("j", JobStatus.COMPLETED, result=None)

    # Client already saw index 0 ("a"); resume from last_index=0.
    events = await _collect(stream_job_events(redis, store, "j", last_index=0))
    tokens = [json.loads(e["data"])["delta"] for e in events if e["event"] == "token"]
    assert tokens == ["b", "c"]


async def test_wait_for_completion_returns_terminal(redis, store: JobStore) -> None:
    await _seed(store, "j", JobStatus.QUEUED)
    await store.finalize("j", JobStatus.COMPLETED, result={"done": True})
    rec = await wait_for_completion(redis, store, "j", timeout=1.0)
    assert rec is not None
    assert rec.status == JobStatus.COMPLETED


async def test_wait_for_completion_times_out(redis, store: JobStore) -> None:
    await _seed(store, "j", JobStatus.RUNNING)
    rec = await wait_for_completion(redis, store, "j", timeout=0.2)
    assert rec is not None
    assert rec.status == JobStatus.RUNNING
