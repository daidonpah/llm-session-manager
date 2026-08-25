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
- An OpenAI-compatible model server reachable from the containers — or, on an
  NVIDIA DGX Spark, use the bundled `spark` profile to run vLLM in-stack (see
  [DGX Spark profile](#bundled-vllm-server--nvidia-dgx-spark-profile))
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

## First-run setup (web wizard)

Prefer not to hand-edit `.env`? The stack ships a **batteries-included setup
webapp** that configures everything from the browser — model/app settings, the
nginx hostnames, TLS certs (paste raw PEM), the admin account, and the first
model download (with live progress).

```bash
# Start just the setup service (it bind-mounts the repo and writes .env for you).
docker compose up -d --build setup
# Open the wizard (listens on 0.0.0.0:8989 so it's reachable on first run).
open http://localhost:8989          # or `uv run lsm-setup` without Docker
```

The wizard walks four steps: **Model & app → Reverse proxy → TLS certs → Admin →
First model**. What it writes:

- **`.env`** — merged in place, preserving your comments and ordering (only the
  allow-listed `LSM_*` / `NGINX_*` / `VLLM_*` / `HF_TOKEN` keys can be set).
- **`nginx/certs/`** — validated PEM cert/key pairs (keys written `0600`), either
  one shared cert (both hostnames via SAN) or a separate cert per vhost.
- **`assets/`** — the first model, downloaded via the same core as `lsm-download`.

The final step creates the admin account. This **seals the one-time wizard**: the
bcrypt hash is written to `.env` as `LSM_ADMIN_PASSWORD_HASH`, and from then on
the setup webapp requires that admin login (HTTP Basic) and no longer runs the
open wizard. After finishing, bring up the rest of the stack (`docker compose up
-d --build`) and restrict the setup port via `LSM_SETUP_BIND_HOST` if you like.

> **Developing the wizard UI?** The built SPA is committed (so the runtime image
> needs no Node), but you can hot-reload it with the Vite dev server:
> `docker compose --profile web-dev up setup web-dev` → edit under `web/setup/`,
> preview at `http://localhost:5173`. Rebuild the committed bundle with
> `npm run build` in `web/setup/` before committing UI changes.

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
| `LSM_ASSETS_DIR` | `assets` | Root dir for downloaded model assets (bind-mounted to `/app/assets` in Docker). |
| `LSM_HF_CACHE_DIR` | `assets/hf-cache` | Resumable HuggingFace cache used by `lsm-download`. |
| `LSM_MODELS_DIR` | `assets/models` | Clean, vLLM-ready model tree written by `lsm-download`. |
| `LSM_API_PORT` | `8080` | Published host port. |
| `LSM_BIND_HOST` | `127.0.0.1` | Host address the API port binds to. |
| `LSM_SETUP_PORT` | `8989` | Published host port for the setup wizard. |
| `LSM_SETUP_BIND_HOST` | `0.0.0.0` | Host address the setup port binds to (open on first run; restrict once configured). |
| `LSM_ADMIN_USER` | `admin` | Admin username for the setup webapp (post-setup login). |
| `LSM_ADMIN_PASSWORD_HASH` | (unset) | Bcrypt hash written by the wizard; its presence marks the stack "configured" and seals the wizard. Do not set by hand. |
| `NGINX_SM_SERVER_NAME` | `localhost` | Hostname for the session-manager vhost (must match the cert). |
| `NGINX_OPENAI_SERVER_NAME` | `openai.localhost` | Hostname for the raw OpenAI passthrough vhost (must match the cert). |
| `VLLM_IMAGE` | `ghcr.io/timothystewart6/vllm-gb10:latest` | vLLM image for the `spark` profile (GB10/sm_121a, ARM64). |
| `VLLM_MODEL_PATH` | `/app/assets/models/Pilcothink/Qwen3.8-27B-MixedInt4-AutoRound` | Local dir (or HF repo id) vLLM serves. |
| `VLLM_SERVED_MODEL_NAME` | `qwen3.8-27b` | Stable model id vLLM advertises (match `LSM_DEFAULT_MODEL`). |
| `VLLM_MAX_MODEL_LEN` | `262144` | Context window (the model supports up to `1010000`). |
| `NGINX_OPENAI_UPSTREAM` | `host.docker.internal:1234` | `host:port` of the model server the OpenAI vhost proxies to. |
| `NGINX_HTTP_PORT` | `80` | Host port nginx publishes for HTTP (redirects to HTTPS). |
| `NGINX_HTTPS_PORT` | `443` | Host port nginx publishes for HTTPS. |
| `NGINX_TLS_CERT_FILE` | `/etc/nginx/certs/server.crt` | Shared cert path *inside* the nginx container (mounted from `./nginx/certs`). |
| `NGINX_TLS_KEY_FILE` | `/etc/nginx/certs/server.key` | Shared key path *inside* the nginx container (mounted from `./nginx/certs`). |
| `NGINX_SM_TLS_CERT_FILE` | (shared) | Optional cert for the session-manager vhost; defaults to `NGINX_TLS_CERT_FILE`. |
| `NGINX_SM_TLS_KEY_FILE` | (shared) | Optional key for the session-manager vhost; defaults to `NGINX_TLS_KEY_FILE`. |
| `NGINX_OPENAI_TLS_CERT_FILE` | (shared) | Optional cert for the OpenAI vhost; defaults to `NGINX_TLS_CERT_FILE`. |
| `NGINX_OPENAI_TLS_KEY_FILE` | (shared) | Optional key for the OpenAI vhost; defaults to `NGINX_TLS_KEY_FILE`. |

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

## Downloading models (for vLLM)

Model downloading is a native part of this project — the same download core lives
inside the app (under `LSM_MODELS_DIR`, shared with the `api`/`worker` containers
via the `./assets` mount), so a future admin API / web UI can trigger downloads
and show progress. For now it's driven from the command line.

`lsm-download` fetches a HuggingFace repo into a clean, vLLM-ready directory
under `LSM_MODELS_DIR` (`assets/models` by default). The printed path is exactly
what you pass to vLLM as `--model`. A resumable HuggingFace cache is kept
separately in `LSM_HF_CACHE_DIR` so re-runs don't re-download.

```bash
uv sync
uv run lsm-download Qwen/Qwen2.5-7B-Instruct           # --type model (default)
uv run lsm-download BAAI/bge-m3 --type embedding
uv run lsm-download meta-llama/Llama-3.1-8B --token "$HF_TOKEN"   # gated repo
```

A live aggregate progress line (percent / bytes / speed) is shown while
downloading; pass `--quiet` to suppress it.

`--type` controls per-type file filtering: `model` / `embedding` / `reranker`
pull weights but skip redundant formats (`*.pth`, `*.h5`, `*.msgpack`), while
`tokenizer` skips weight shards entirely. Other flags: `--revision`, `--token`,
`--allow`/`--ignore` (repeatable globs), `--force`, and `--quiet`.

### Serving the result with vLLM

```bash
vllm serve ./assets/models/Qwen/Qwen2.5-7B-Instruct
```

Downloaded assets are large and git-ignored (`assets/`). The directory contains
a `.cache/huggingface/` metadata folder used for resumable downloads; vLLM
ignores it.

### Bundled vLLM server — NVIDIA DGX Spark profile

For a single **NVIDIA DGX Spark** (GB10 / `sm_121a`, ARM64) there's a ready-made
`spark` compose profile that runs vLLM alongside the API/worker, pre-tuned for
the flagship model `Pilcothink/Qwen3.8-27B-MixedInt4-AutoRound` (a 20.8 GB
mixed-int4 AutoRound build made to fit a single Spark).

**Prerequisites:** a DGX Spark host and the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
The bundled image (`ghcr.io/timothystewart6/vllm-gb10`) is ARM64/GB10-only.

```bash
# 1. Download the model into ./assets (vLLM serves this local dir).
uv run lsm-download Pilcothink/Qwen3.8-27B-MixedInt4-AutoRound

# 2. Point the app at the in-network vllm service (edit .env, uncomment):
#      LSM_MODEL_BASE_URL=http://vllm:8000/v1
#      LSM_DEFAULT_MODEL=qwen3.8-27b

# 3. Start the full stack including vLLM.
docker compose --profile spark up -d --build
```

vLLM serves the local dir and advertises a stable id via
`--served-model-name` (`VLLM_SERVED_MODEL_NAME`), so the on-disk path never
leaks into the API. First boot is slow (the container compiles/loads the model);
the healthcheck allows a 5-minute start period.

The single-Spark recipe (tensor-parallel 1, `--gpu-memory-utilization 0.9`,
`--kv-cache-dtype fp8`, `--enable-prefix-caching`, `qwen3` reasoning parser,
`qwen3_coder` tool-call parser, `--trust-remote-code`, auto tool choice) is baked
in as defaults. Every knob is overridable via the `VLLM_*` variables in `.env`
(image, model path, served name, context length, batching, etc.). Set
`VLLM_MODEL_PATH` to a HF repo id to have vLLM download into its own cache
instead of serving a pre-downloaded dir.

## Reverse proxy (recommended for remote access)

The API binds to loopback, so to reach it from other machines you need a TLS
terminating reverse proxy in front. **Streaming requires response buffering to be
disabled** — the configs below all handle that.

### Option A — bundled nginx service (default)

The compose stack ships an `nginx` service that terminates TLS and serves **two
name-based virtual hosts**, so you can expose both APIs on the same proxy:

- **`NGINX_SM_SERVER_NAME`** (default `localhost`) → the **session-manager API**
  (`api` container) — job persistence, retries, reconnectable streaming.
- **`NGINX_OPENAI_SERVER_NAME`** (default `openai.localhost`) → the **raw
  OpenAI-compatible model server** (`NGINX_OPENAI_UPSTREAM`, default the host's LM
  Studio) — a direct passthrough with no job semantics.

It publishes ports 80 (redirects to HTTPS) and 443.

1. Provide the TLS cert(s) in `./nginx/certs/`. You have two options:

   **One shared cert** (default) — a single SAN/wildcard cert covering both
   hostnames, at `server.crt` / `server.key`. For a quick local test:

   ```bash
   ./nginx/generate-self-signed-cert.sh localhost openai.localhost
   ```

   **A separate cert per vhost** — pass `--separate` to emit `sm.crt/sm.key` and
   `openai.crt/openai.key`, then point the per-vhost vars at them in `.env`:

   ```bash
   ./nginx/generate-self-signed-cert.sh --separate llm.example.com openai.example.com
   ```
   ```dotenv
   NGINX_SM_TLS_CERT_FILE=/etc/nginx/certs/sm.crt
   NGINX_SM_TLS_KEY_FILE=/etc/nginx/certs/sm.key
   NGINX_OPENAI_TLS_CERT_FILE=/etc/nginx/certs/openai.crt
   NGINX_OPENAI_TLS_KEY_FILE=/etc/nginx/certs/openai.key
   ```

   Each per-vhost var defaults to the shared `NGINX_TLS_CERT_FILE` /
   `NGINX_TLS_KEY_FILE`, so leave them unset to use one cert. For production, drop
   in your real cert(s) (e.g. Let's Encrypt `fullchain.pem` / `privkey.pem`,
   repointed via these vars).

2. Set the hostnames (must match the certificate) and, optionally, the OpenAI
   upstream and published ports in `.env`:

   ```dotenv
   NGINX_SM_SERVER_NAME=llm.example.com
   NGINX_OPENAI_SERVER_NAME=openai.example.com
   NGINX_OPENAI_UPSTREAM=host.docker.internal:1234
   NGINX_HTTP_PORT=80
   NGINX_HTTPS_PORT=443
   ```

3. `docker compose up -d`. The proxies are live on
   `https://<NGINX_SM_SERVER_NAME>/` and `https://<NGINX_OPENAI_SERVER_NAME>/`.
   Both hostnames must resolve to the host (DNS, or an `/etc/hosts` entry for
   local testing).

The `./nginx/certs` directory is git-ignored, so real keys are never committed.

> The raw OpenAI vhost has **no authentication and no session management**. Only
> expose it if the upstream model server is meant to be reachable directly; put
> auth at the proxy (see below) if it is not.

### Option B — your own nginx / Caddy / Traefik

Already running a proxy elsewhere? **Comment out the `nginx:` service** in
`docker-compose.yml` and point your proxy at `127.0.0.1:8080` instead.
`nginx/templates/lsm.conf.template` is a copy-paste starting point (replace the
`${...}` placeholders and the upstream). A minimal nginx config:

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
