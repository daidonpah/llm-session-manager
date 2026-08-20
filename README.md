# llm-session-manager

A small, self-hostable wrapper that puts **job persistence, retries, reconnectable
streaming, status queries, cancellation and bounded parallelism** in front of any
**OpenAI-compatible** local model server (vLLM, Ollama, llama.cpp, LM Studio, …).

Built with **FastAPI + Redis + [ARQ](https://arq-docs.helpmanual.io/)**. It supports
chat, vision (multimodal chat) and embeddings, in both synchronous (await the result)
and asynchronous (submit now, stream/poll later) modes.

## Why not Celery / RabbitMQ?

An LLM wrapper is a *small-N, GPU-bound, long-running, streaming* workload — not
web-scale task fan-out. The scaling knob is **bounded concurrency**, and the hard
parts are **streaming partial results** and **reconnecting to a running job**. Redis
alone covers the queue (via ARQ), the job state store, and pub/sub for live tokens,
without the extra moving parts of a broker + result backend + prefork workers. If you
already run a Celery/RabbitMQ estate or need complex routing, that calculus changes.

## Architecture

```
Client ──HTTP──▶ FastAPI (API)
                   │  create → persist to Redis + enqueue (ARQ)
                   │  status → read Redis hash
                   │  stream → SSE: replay buffered tokens, then tail pub/sub
                   ▼
                Redis  ── job hashes ── ARQ queue ── token buffer + pub/sub
                   ▲
                   │
              ARQ worker(s)  ── bounded concurrency (max_jobs)
                   │  httpx → OpenAI-compatible endpoint
                   │  retry w/ backoff, cooperative cancel
                   ▼
             Local model server (OpenAI API, configurable base_url)
```

## Requirements

- Docker + Docker Compose
- An OpenAI-compatible model server reachable from the containers
- (For local development without Docker) Python 3.12 and [uv](https://docs.astral.sh/uv/)

## Quick start

```bash
# 1. Configure. Copy the example and point it at your model server.
cp .env.example .env
#    Edit .env: set LSM_MODEL_BASE_URL and LSM_DEFAULT_MODEL.
#    If the model runs on the host (e.g. LM Studio), use host.docker.internal:
#      LSM_MODEL_BASE_URL=http://host.docker.internal:1234/v1

# 2. Start the stack (redis + api + worker), dev mode with live reload.
docker compose up --build

# 3. Check health (API is bound to 127.0.0.1 only).
curl http://127.0.0.1:8080/v1/health
# {"status":"ok"}
```

Interactive API docs are at `http://127.0.0.1:8080/docs`.

## Security: the API binds to loopback only

By default the API port is published on **`127.0.0.1`**, not `0.0.0.0`. This prevents
the service from being reachable on your LAN or leaking through the host firewall
(Docker publishes bypass many host firewalls when bound to `0.0.0.0`).

- Local access only: use the default. Nothing to do.
- Remote access: **put a reverse proxy in front** (see below). Do **not** widen the
  bind unless you fully understand the exposure. The bind host is controlled by
  `LSM_BIND_HOST` (default `127.0.0.1`).

## Configuration

All settings are environment variables prefixed with `LSM_` (see `.env.example`).

| Variable | Default | Description |
| --- | --- | --- |
| `LSM_REDIS_URL` | `redis://localhost:6379/0` | Redis connection (compose overrides to `redis://redis:6379/0`). |
| `LSM_MODEL_BASE_URL` | `http://localhost:8000/v1` | Upstream OpenAI-compatible base URL. |
| `LSM_MODEL_API_KEY` | `not-needed` | Bearer token for the upstream, if required. |
| `LSM_DEFAULT_MODEL` | `local-model` | Model id used when a request omits `model`. |
| `LSM_REQUEST_TIMEOUT_S` | `300` | Per-request upstream timeout. |
| `LSM_WORKER_MAX_JOBS` | `10` | Max concurrent jobs per worker (parallelism knob). |
| `LSM_MAX_RETRIES` | `3` | Retries for transient upstream failures. |
| `LSM_RETRY_BACKOFF_BASE_S` | `2.0` | Exponential backoff base. |
| `LSM_JOB_TTL_S` | `86400` | How long job records live in Redis. |
| `LSM_SYNC_WAIT_TIMEOUT_S` | `300` | Max wait for a synchronous request. |
| `LSM_API_PORT` | `8080` | Published host port. |
| `LSM_BIND_HOST` | `127.0.0.1` | Host address the API port binds to. |

## Scaling

- **Per-worker concurrency**: raise `LSM_WORKER_MAX_JOBS` (bounded so you don't
  overwhelm the GPU).
- **More workers**: `docker compose up --scale worker=3`, or use the `replicas`
  setting in `docker-compose.prod.yml`.

## API

Base path: `/v1`. Full interactive schema at `/docs`.

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/v1/health` | Liveness + Redis check. |
| `POST` | `/v1/jobs` | Submit a job (sync or async). |
| `GET` | `/v1/jobs` | List recent jobs (`?limit=`). |
| `GET` | `/v1/jobs/{id}` | Fetch a job record. |
| `POST` | `/v1/jobs/{id}/cancel` | Request cancellation. |
| `GET` | `/v1/jobs/{id}/stream` | SSE token stream (resumable). |

A job request has `type` (`chat` \| `vision` \| `embeddings`), `mode`
(`async` \| `sync`), optional `stream`, and a `payload` passed through to the
upstream endpoint (extra OpenAI fields are preserved).

### Async chat with streaming

```bash
# Submit
JOB=$(curl -s -X POST http://127.0.0.1:8080/v1/jobs \
  -H 'content-type: application/json' \
  -d '{"type":"chat","mode":"async","stream":true,
       "payload":{"messages":[{"role":"user","content":"Count to 5"}]}}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')

# Stream tokens (resume with -H "Last-Event-ID: <index>")
curl -N http://127.0.0.1:8080/v1/jobs/$JOB/stream
```

### Synchronous chat

```bash
curl -s -X POST http://127.0.0.1:8080/v1/jobs \
  -H 'content-type: application/json' \
  -d '{"type":"chat","mode":"sync",
       "payload":{"messages":[{"role":"user","content":"Say hi"}]}}'
```

### Vision (multimodal)

```bash
curl -s -X POST http://127.0.0.1:8080/v1/jobs \
  -H 'content-type: application/json' \
  -d '{"type":"vision","mode":"sync","payload":{"messages":[{"role":"user",
       "content":[{"type":"text","text":"Describe this"},
                  {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]}]}}'
```

### Embeddings

```bash
curl -s -X POST http://127.0.0.1:8080/v1/jobs \
  -H 'content-type: application/json' \
  -d '{"type":"embeddings","mode":"sync",
       "payload":{"model":"your-embedding-model","input":"hello world"}}'
```

### Reconnecting to a stream

The SSE stream emits an `id:` (the token index) on every `token` event. On
reconnect, pass the last id you saw as the `Last-Event-ID` header; the server
replays only tokens after it, then tails live — no duplicates, no gaps.

## Reverse proxy (recommended for remote access)

The API binds to loopback, so to reach it from other machines terminate TLS and
proxy to `127.0.0.1:8080` with something like nginx, Caddy or Traefik. **Streaming
requires response buffering to be disabled.**

nginx:

```nginx
server {
    listen 443 ssl;
    server_name llm.example.com;
    # ssl_certificate / ssl_certificate_key ...

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Required for SSE streaming:
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
    }
}
```

Caddy (automatic HTTPS):

```caddy
llm.example.com {
    reverse_proxy 127.0.0.1:8080 {
        flush_interval -1   # stream responses immediately (SSE)
    }
}
```

Add authentication (mTLS, an auth proxy, or basic auth) at the proxy layer — the
service itself is unauthenticated by design.

## Development

```bash
# Without Docker
uv sync --extra dev
uv run lsm-api                          # API on 127.0.0.1:8080 (per .env)
uv run arq lsm.worker.WorkerSettings    # worker (needs a running Redis)

# Lint + tests
uv run ruff check src/ tests/
uv run pytest
```

`docker compose up` (dev) mounts `./src` and reloads both the API and worker on
change.

## Production

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

This disables source mounts/reload, runs multiple uvicorn workers, and scales the
worker service. The API stays bound to `127.0.0.1` — front it with your proxy.

## License

MIT.
