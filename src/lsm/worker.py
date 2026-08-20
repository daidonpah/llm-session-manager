"""ARQ worker wiring: lifecycle hooks and ``WorkerSettings``.

Run with::

    arq lsm.worker.WorkerSettings
"""

from __future__ import annotations

from typing import Any

from arq.connections import ArqRedis, RedisSettings
from arq.constants import default_queue_name
from arq.jobs import Job

from .config import settings
from .job_store import JobStore
from .llm_client import LLMClient
from .redis_client import create_redis
from .tasks import run_job


async def abort_job(arq: ArqRedis, job_id: str, timeout: float = 1.0) -> bool:
    """Best-effort ARQ abort for a job by id.

    Returns whether the abort request was accepted. The cooperative cancel flag
    in :class:`~lsm.job_store.JobStore` is the primary stop mechanism; this is a
    secondary hard-stop for jobs that are already executing.
    """
    job = Job(job_id, redis=arq, _queue_name=default_queue_name)
    try:
        return await job.abort(timeout=timeout)
    except TimeoutError:
        return False


async def startup(ctx: dict[str, Any]) -> None:
    ctx["store_redis"] = create_redis()
    ctx["store"] = JobStore(ctx["store_redis"])
    ctx["llm"] = LLMClient()


async def shutdown(ctx: dict[str, Any]) -> None:
    llm: LLMClient = ctx.get("llm")
    if llm is not None:
        await llm.aclose()
    store_redis = ctx.get("store_redis")
    if store_redis is not None:
        await store_redis.aclose()


class WorkerSettings:
    """Settings object consumed by the ``arq`` CLI."""

    functions = [run_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.worker_max_jobs
    job_timeout = settings.worker_job_timeout_s
    max_tries = settings.max_retries + 1
    allow_abort_jobs = True
    keep_result = settings.job_ttl_s
