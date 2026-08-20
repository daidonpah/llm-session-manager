# syntax=docker/dockerfile:1

# Single image used by both the API and the worker; the compose command selects
# which process to run. Built with uv for fast, reproducible installs.
FROM python:3.12-slim-bookworm

# Install uv via pip (portable; avoids depending on an external registry image).
RUN pip install --no-cache-dir uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install dependencies first (cached unless the lock/manifest change).
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

# Install the project itself.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev

EXPOSE 8080

# Default to the API; the worker service overrides this command in compose.
CMD ["uvicorn", "lsm.main:app", "--host", "0.0.0.0", "--port", "8080"]
