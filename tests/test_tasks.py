"""Tests for the worker task dispatch, streaming, retry and cancel logic."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from arq.worker import Retry

from lsm.job_store import JobStore
from lsm.models import JobMode, JobRecord, JobStatus, JobType
from lsm.tasks import run_job


class FakeLLM:
    def __init__(self, *, chunks=None, result=None, embeddings=None, error=None) -> None:
        self.chunks = chunks or []
        self.result = result
        self._embeddings = embeddings
        self.error = error

    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.error:
            raise self.error
        return self.result

    async def embeddings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._embeddings

    async def stream_chat_completion(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        if self.error:
            raise self.error
        for c in self.chunks:
            yield c


async def _seed(store: JobStore, job_id: str, jtype: JobType, stream: bool) -> None:
    await store.create(
        JobRecord(
            id=job_id,
            type=jtype,
            status=JobStatus.QUEUED,
            mode=JobMode.ASYNC,
            stream=stream,
            model="m",
            created_at=time.time(),
        )
    )


async def test_non_stream_chat_completes(store: JobStore) -> None:
    await _seed(store, "j", JobType.CHAT, stream=False)
    ctx = {"store": store, "llm": FakeLLM(result={"id": "r", "choices": []}), "job_try": 1}
    out = await run_job(ctx, "j", "chat", {"messages": []}, False)
    assert out == {"id": "r", "choices": []}
    rec = await store.get("j")
    assert rec.status == JobStatus.COMPLETED


async def test_embeddings_completes(store: JobStore) -> None:
    await _seed(store, "e", JobType.EMBEDDINGS, stream=False)
    ctx = {"store": store, "llm": FakeLLM(embeddings={"data": [1]}), "job_try": 1}
    out = await run_job(ctx, "e", "embeddings", {"input": "x"}, False)
    assert out == {"data": [1]}
    assert (await store.get("e")).status == JobStatus.COMPLETED


async def test_stream_buffers_tokens_and_assembles(store: JobStore) -> None:
    await _seed(store, "s", JobType.CHAT, stream=True)
    chunks = [
        {"choices": [{"delta": {"content": "Hel"}}]},
        {"choices": [{"delta": {"content": "lo"}, "finish_reason": "stop"}]},
    ]
    ctx = {"store": store, "llm": FakeLLM(chunks=chunks), "job_try": 1}
    out = await run_job(ctx, "s", "chat", {"messages": []}, True)
    assert out["choices"][0]["message"]["content"] == "Hello"
    assert await store.get_tokens("s") == ["Hel", "lo"]
    assert (await store.get("s")).status == JobStatus.COMPLETED


async def test_transient_error_raises_retry(store: JobStore) -> None:
    from lsm.llm_client import LLMError

    await _seed(store, "t", JobType.CHAT, stream=False)
    ctx = {
        "store": store,
        "llm": FakeLLM(error=LLMError("boom", transient=True)),
        "job_try": 1,
    }
    with pytest.raises(Retry):
        await run_job(ctx, "t", "chat", {"messages": []}, False)
    assert (await store.get("t")).status == JobStatus.QUEUED


async def test_permanent_error_fails_job(store: JobStore) -> None:
    from lsm.llm_client import LLMError

    await _seed(store, "p", JobType.CHAT, stream=False)
    ctx = {
        "store": store,
        "llm": FakeLLM(error=LLMError("bad", transient=False)),
        "job_try": 1,
    }
    out = await run_job(ctx, "p", "chat", {"messages": []}, False)
    assert out is None
    rec = await store.get("p")
    assert rec.status == JobStatus.FAILED
    assert rec.error


async def test_cancel_before_start(store: JobStore) -> None:
    await _seed(store, "c", JobType.CHAT, stream=False)
    await store.request_cancel("c")
    ctx = {"store": store, "llm": FakeLLM(result={}), "job_try": 1}
    out = await run_job(ctx, "c", "chat", {"messages": []}, False)
    assert out is None
    assert (await store.get("c")).status == JobStatus.CANCELLED
