"""ARQ task functions that execute jobs against the model server."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from arq.worker import Retry

from .config import settings
from .job_store import JobStore
from .llm_client import LLMClient, LLMError
from .models import JobStatus, JobType, StreamEvent, StreamEventType


def _backoff_seconds(job_try: int) -> float:
    return settings.retry_backoff_base_s * (2 ** max(job_try - 1, 0))


async def _finalize_shielded(store: JobStore, job_id: str, **kwargs: Any) -> None:
    await asyncio.shield(store.finalize(job_id, **kwargs))


async def run_job(
    ctx: dict[str, Any], job_id: str, job_type: str, payload: dict[str, Any], stream: bool
) -> dict[str, Any] | None:
    """Execute a single job; the entry point registered with the ARQ worker."""
    store: JobStore = ctx["store"]
    llm: LLMClient = ctx["llm"]
    job_try: int = ctx.get("job_try", 1)

    record = await store.get(job_id)
    if record is None or record.is_terminal:
        return None
    if await store.is_cancel_requested(job_id):
        await store.finalize(job_id, JobStatus.CANCELLED)
        return None

    await store.update(
        job_id, status=JobStatus.RUNNING, started_at=time.time(), attempts=job_try
    )
    await store.publish_event(
        job_id, StreamEvent(type=StreamEventType.STATUS, status=JobStatus.RUNNING)
    )

    try:
        jtype = JobType(job_type)
        if jtype == JobType.EMBEDDINGS:
            result = await llm.embeddings(payload)
        elif stream:
            result = await _run_chat_stream(store, llm, job_id, payload)
        else:
            result = await llm.chat_completion(payload)

        if result is None:  # cancelled mid-stream
            await store.finalize(job_id, JobStatus.CANCELLED)
            return None
        await store.finalize(job_id, JobStatus.COMPLETED, result=result)
        return result
    except asyncio.CancelledError:
        await _finalize_shielded(store, job_id, status=JobStatus.CANCELLED)
        raise
    except LLMError as exc:
        if exc.transient and job_try <= settings.max_retries:
            await store.update(job_id, status=JobStatus.QUEUED, error=str(exc))
            raise Retry(defer=_backoff_seconds(job_try)) from exc
        await _finalize_shielded(store, job_id, status=JobStatus.FAILED, error=str(exc))
        return None
    except Exception as exc:  # noqa: BLE001 - convert to a failed job record
        await _finalize_shielded(
            store, job_id, status=JobStatus.FAILED, error=repr(exc)
        )
        return None


async def _run_chat_stream(
    store: JobStore, llm: LLMClient, job_id: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Stream a chat/vision completion, buffering deltas; return an assembled result.

    Returns ``None`` if the job was cancelled mid-stream.
    """
    parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None

    async for chunk in llm.stream_chat_completion(payload):
        if await store.is_cancel_requested(job_id):
            return None
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                parts.append(content)
                await store.append_token(job_id, content)
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
        if chunk.get("usage"):
            usage = chunk["usage"]

    return {
        "id": job_id,
        "object": "chat.completion",
        "model": payload.get("model"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "".join(parts)},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
    }
