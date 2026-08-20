"""Pydantic schemas and enums shared by the API and worker."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobType(str, Enum):
    CHAT = "chat"
    VISION = "vision"
    EMBEDDINGS = "embeddings"


class JobMode(str, Enum):
    SYNC = "sync"
    ASYNC = "async"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
)

# chat + vision share the same upstream endpoint (/chat/completions).
CHAT_LIKE_TYPES = frozenset({JobType.CHAT, JobType.VISION})


class _PassthroughModel(BaseModel):
    """Base that preserves unknown fields so we stay OpenAI-compatible."""

    model_config = ConfigDict(extra="allow")


class ChatPayload(_PassthroughModel):
    """An OpenAI-style chat/vision completion request (extra fields allowed)."""

    model: str | None = None
    messages: list[dict[str, Any]]
    stream: bool = False


class EmbeddingsPayload(_PassthroughModel):
    """An OpenAI-style embeddings request (extra fields allowed)."""

    model: str | None = None
    input: str | list[str] | list[int] | list[list[int]]


class JobCreateRequest(BaseModel):
    """Generic job submission accepted by ``POST /v1/jobs``."""

    type: JobType
    payload: dict[str, Any]
    mode: JobMode = JobMode.ASYNC
    stream: bool = False


class JobRecord(BaseModel):
    """The persisted state of a job (source of truth, stored in a Redis hash)."""

    id: str
    type: JobType
    status: JobStatus
    mode: JobMode
    stream: bool = False
    model: str | None = None
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    attempts: int = 0
    error: str | None = None
    result: Any | None = None
    cancel_requested: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class JobCreatedResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobListResponse(BaseModel):
    jobs: list[JobRecord]
    count: int = Field(..., description="Number of jobs returned")


class StreamEventType(str, Enum):
    """Types emitted on the job event channel / SSE stream."""

    TOKEN = "token"
    DONE = "done"
    ERROR = "error"
    STATUS = "status"


class StreamEvent(BaseModel):
    type: StreamEventType
    index: int | None = None
    delta: str | None = None
    status: JobStatus | None = None
    data: Any | None = None
