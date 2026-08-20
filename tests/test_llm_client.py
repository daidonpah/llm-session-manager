"""Tests for the OpenAI-compatible LLM client (upstream mocked with respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from lsm.llm_client import LLMClient, LLMError

BASE = "http://model.test/v1"


@pytest.fixture
async def client() -> LLMClient:
    c = LLMClient(base_url=BASE, api_key="k")
    yield c
    await c.aclose()


@respx.mock
async def test_chat_completion_non_stream(client: LLMClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, json={"id": "x", "choices": []})
    )
    out = await client.chat_completion({"messages": [{"role": "user", "content": "hi"}]})
    assert out["id"] == "x"


@respx.mock
async def test_embeddings(client: LLMClient) -> None:
    respx.post(f"{BASE}/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})
    )
    out = await client.embeddings({"input": "hello"})
    assert out["data"][0]["embedding"] == [0.1, 0.2]


@respx.mock
async def test_5xx_is_transient(client: LLMClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(503))
    with pytest.raises(LLMError) as exc:
        await client.chat_completion({"messages": []})
    assert exc.value.transient is True
    assert exc.value.status_code == 503


@respx.mock
async def test_400_is_not_transient(client: LLMClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(400))
    with pytest.raises(LLMError) as exc:
        await client.chat_completion({"messages": []})
    assert exc.value.transient is False


@respx.mock
async def test_stream_yields_chunks_until_done(client: LLMClient) -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"He"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"llo"},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(f"{BASE}/chat/completions").mock(
        return_value=httpx.Response(200, text=body)
    )
    chunks = [c async for c in client.stream_chat_completion({"messages": []})]
    deltas = [c["choices"][0]["delta"].get("content") for c in chunks]
    assert deltas == ["He", "llo"]


@respx.mock
async def test_stream_error_status_raises(client: LLMClient) -> None:
    respx.post(f"{BASE}/chat/completions").mock(return_value=httpx.Response(500))
    with pytest.raises(LLMError) as exc:
        async for _ in client.stream_chat_completion({"messages": []}):
            pass
    assert exc.value.transient is True
