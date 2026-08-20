"""Tests for the FastAPI routes using a fakeredis-backed app."""

from __future__ import annotations

CHAT_BODY = {
    "type": "chat",
    "payload": {"messages": [{"role": "user", "content": "hi"}]},
    "mode": "async",
}


def test_health_ok(client) -> None:
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_async_job_returns_202(client, stub_arq) -> None:
    resp = client.post("/v1/jobs", json=CHAT_BODY)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    job_id = body["job_id"]
    # The job was enqueued to ARQ with the right function + type.
    assert stub_arq.enqueued
    func, args, _kwargs = stub_arq.enqueued[0]
    assert func == "run_job"
    assert args[0] == job_id
    assert args[1] == "chat"


def test_create_defaults_model(client) -> None:
    from lsm.config import settings

    resp = client.post("/v1/jobs", json=CHAT_BODY)
    job_id = resp.json()["job_id"]
    rec = client.get(f"/v1/jobs/{job_id}").json()
    assert rec["model"] == settings.default_model


def test_get_job(client) -> None:
    job_id = client.post("/v1/jobs", json=CHAT_BODY).json()["job_id"]
    resp = client.get(f"/v1/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == job_id
    assert resp.json()["type"] == "chat"


def test_get_missing_job_404(client) -> None:
    assert client.get("/v1/jobs/nope").status_code == 404


def test_list_jobs(client) -> None:
    for _ in range(3):
        client.post("/v1/jobs", json=CHAT_BODY)
    resp = client.get("/v1/jobs")
    assert resp.status_code == 200
    assert resp.json()["count"] == 3


def test_cancel_job(client, monkeypatch) -> None:
    aborted: list[str] = []

    async def fake_abort(arq, job_id, timeout: float = 1.0):
        aborted.append(job_id)
        return True

    monkeypatch.setattr("lsm.routes.abort_job", fake_abort)
    job_id = client.post("/v1/jobs", json=CHAT_BODY).json()["job_id"]
    resp = client.post(f"/v1/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["cancel_requested"] is True
    assert job_id in aborted


def test_cancel_missing_job_404(client) -> None:
    assert client.post("/v1/jobs/nope/cancel").status_code == 404


def test_stream_missing_job_404(client) -> None:
    assert client.get("/v1/jobs/nope/stream").status_code == 404


def test_invalid_job_type_422(client) -> None:
    bad = {"type": "translate", "payload": {}, "mode": "async"}
    assert client.post("/v1/jobs", json=bad).status_code == 422
