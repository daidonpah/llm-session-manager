"""FastAPI application factory, lifespan and shared dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import Depends, FastAPI, Request

from .config import settings
from .job_store import JobStore
from .redis_client import create_redis


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open shared Redis + ARQ connections for the lifetime of the app."""
    app.state.redis = create_redis()
    app.state.store = JobStore(app.state.redis)
    app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        yield
    finally:
        await app.state.arq.aclose()
        await app.state.redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="llm-session-manager",
        version="0.1.0",
        summary="Job wrapper for OpenAI-compatible local models.",
        lifespan=lifespan,
    )
    from .routes import router  # imported here to avoid a circular import

    app.include_router(router)
    return app


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_arq(request: Request) -> ArqRedis:
    return request.app.state.arq


def get_redis(request: Request):
    return request.app.state.redis


StoreDep = Annotated[JobStore, Depends(get_store)]
ArqDep = Annotated[ArqRedis, Depends(get_arq)]
RedisDep = Annotated[object, Depends(get_redis)]
