"""HTTP routes: job submission (sync/async), status, streaming, cancel, list."""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from .app import ArqDep, RedisDep, StoreDep
from .config import settings
from .models import (
    JobCreatedResponse,
    JobCreateRequest,
    JobListResponse,
    JobMode,
    JobRecord,
    JobStatus,
)
from .streaming import stream_job_events, wait_for_completion
from .worker import abort_job

router = APIRouter(prefix="/v1")


@router.get("/health", tags=["ops"])
async def health(redis: RedisDep) -> dict[str, str]:
    try:
        await redis.ping()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc
    return {"status": "ok"}


async def _enqueue(store: StoreDep, arq: ArqDep, req: JobCreateRequest) -> JobRecord:
    job_id = uuid.uuid4().hex
    model = req.payload.get("model") or settings.default_model
    req.payload.setdefault("model", model)
    record = JobRecord(
        id=job_id,
        type=req.type,
        status=JobStatus.QUEUED,
        mode=req.mode,
        stream=req.stream,
        model=model,
        created_at=time.time(),
    )
    await store.create(record)
    await arq.enqueue_job(
        "run_job", job_id, req.type.value, req.payload, req.stream, _job_id=job_id
    )
    return record


@router.post(
    "/jobs",
    response_model=None,
    status_code=202,
    responses={
        202: {"model": JobCreatedResponse, "description": "Job accepted (async mode)"},
        200: {"model": JobRecord, "description": "Completed job (sync mode)"},
    },
    tags=["jobs"],
)
async def create_job(
    req: JobCreateRequest, store: StoreDep, arq: ArqDep, redis: RedisDep
) -> JSONResponse:
    """Submit a job. In ``async`` mode returns immediately with the job id.

    In ``sync`` mode blocks until the job finishes (or the wait times out).
    """
    record = await _enqueue(store, arq, req)
    if req.mode == JobMode.SYNC:
        final = await wait_for_completion(
            redis, store, record.id, settings.sync_wait_timeout_s
        )
        if final is None or not final.is_terminal:
            raise HTTPException(status_code=504, detail="job did not finish in time")
        return JSONResponse(status_code=200, content=jsonable_encoder(final))
    payload = JobCreatedResponse(job_id=record.id, status=record.status)
    return JSONResponse(status_code=202, content=jsonable_encoder(payload))


@router.get("/jobs", response_model=JobListResponse, tags=["jobs"])
async def list_jobs(
    store: StoreDep, limit: int = Query(50, ge=1, le=500)
) -> JobListResponse:
    jobs = await store.list(limit=limit)
    return JobListResponse(jobs=jobs, count=len(jobs))


@router.get("/jobs/{job_id}", response_model=JobRecord, tags=["jobs"])
async def get_job(job_id: str, store: StoreDep) -> JobRecord:
    record = await store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    return record


@router.post("/jobs/{job_id}/cancel", response_model=JobRecord, tags=["jobs"])
async def cancel_job(job_id: str, store: StoreDep, arq: ArqDep) -> JobRecord:
    record = await store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    if record.is_terminal:
        return record
    await store.request_cancel(job_id)
    await abort_job(arq, job_id)
    refreshed = await store.get(job_id)
    return refreshed or record


@router.get("/jobs/{job_id}/stream", tags=["jobs"])
async def stream_job(
    job_id: str,
    store: StoreDep,
    redis: RedisDep,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """Server-Sent Events stream of tokens + status, resumable via ``Last-Event-ID``."""
    record = await store.get(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail="job not found")
    last_index = _parse_last_index(last_event_id)
    return EventSourceResponse(
        stream_job_events(redis, store, job_id, last_index=last_index)
    )


def _parse_last_index(last_event_id: str | None) -> int:
    if not last_event_id:
        return -1
    try:
        return int(last_event_id)
    except ValueError:
        return -1
