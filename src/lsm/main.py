"""Console entrypoint for the API server (``lsm-api``)."""

from __future__ import annotations

import uvicorn

from .app import create_app
from .config import settings

app = create_app()


def run() -> None:
    uvicorn.run(
        "lsm.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
