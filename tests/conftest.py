"""Shared pytest fixtures: fakeredis, job store, and a TestClient app."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import fakeredis.aioredis as fakeredis_aio
import pytest
from starlette.testclient import TestClient

import lsm.app as appmod
from lsm.job_store import JobStore


class StubArq:
    """Minimal stand-in for the ARQ pool used by the API in tests."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[Any, ...]] = []
        self.aborted: list[str] = []

    async def enqueue_job(self, func: str, *args: Any, **kwargs: Any) -> object:
        self.enqueued.append((func, args, kwargs))
        return object()

    async def abort_job(self, job_id: str) -> bool:
        self.aborted.append(job_id)
        return True

    async def aclose(self) -> None:  # pragma: no cover - nothing to close
        pass


@pytest.fixture
def redis() -> fakeredis_aio.FakeRedis:
    return fakeredis_aio.FakeRedis(decode_responses=True)


@pytest.fixture
def store(redis: fakeredis_aio.FakeRedis) -> JobStore:
    return JobStore(redis)


@pytest.fixture
def stub_arq() -> StubArq:
    return StubArq()


@pytest.fixture
def client(
    redis: fakeredis_aio.FakeRedis, store: JobStore, stub_arq: StubArq
) -> TestClient:
    @asynccontextmanager
    async def patched_lifespan(app):  # type: ignore[no-untyped-def]
        app.state.redis = redis
        app.state.store = store
        app.state.arq = stub_arq
        yield

    original = appmod.lifespan
    appmod.lifespan = patched_lifespan
    try:
        app = appmod.create_app()
        with TestClient(app) as test_client:
            yield test_client
    finally:
        appmod.lifespan = original
