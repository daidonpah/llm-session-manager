"""Async HTTP client for any OpenAI-compatible model server."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from .config import settings


class LLMError(Exception):
    """Raised when the upstream model server errors.

    ``transient`` marks failures that are worth retrying (connection issues,
    timeouts, 5xx and 429 responses).
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, transient: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.transient = transient


def _is_transient_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


class LLMClient:
    """Thin wrapper over ``httpx.AsyncClient`` targeting the configured base URL."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (base_url or settings.model_base_url_clean).rstrip("/")
        self.api_key = api_key or settings.model_api_key
        timeout = httpx.Timeout(
            settings.request_timeout_s, connect=settings.connect_timeout_s
        )
        headers = {"Authorization": f"Bearer {self.api_key}"}
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, headers=headers
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> LLMClient:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Non-streaming chat/vision completion."""
        body = {**payload, "stream": False}
        return await self._post_json("/chat/completions", body)

    async def embeddings(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_json("/embeddings", payload)

    async def stream_chat_completion(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield parsed SSE chunks from a streaming chat/vision completion."""
        body = {**payload, "stream": True}
        try:
            async with self._client.stream("POST", "/chat/completions", json=body) as resp:
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")
                    raise LLMError(
                        f"upstream {resp.status_code}: {text[:500]}",
                        status_code=resp.status_code,
                        transient=_is_transient_status(resp.status_code),
                    )
                async for line in resp.aiter_lines():
                    chunk = _parse_sse_line(line)
                    if chunk is _DONE:
                        return
                    if chunk is not None:
                        yield chunk
        except httpx.HTTPError as exc:
            raise LLMError(f"stream request failed: {exc}", transient=True) from exc

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(path, json=body)
        except httpx.HTTPError as exc:
            raise LLMError(f"request to {path} failed: {exc}", transient=True) from exc
        if resp.status_code >= 400:
            raise LLMError(
                f"upstream {resp.status_code}: {resp.text[:500]}",
                status_code=resp.status_code,
                transient=_is_transient_status(resp.status_code),
            )
        return resp.json()


_DONE = object()


def _parse_sse_line(line: str) -> dict[str, Any] | object | None:
    """Parse a single SSE line; return a chunk dict, the DONE sentinel, or None."""
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    data = line[len("data:"):].strip()
    if data == "[DONE]":
        return _DONE
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None
