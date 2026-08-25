"""Streaming first-model download endpoint for the setup wizard (SSE).

``download_model`` is blocking, so we run it in a worker thread and forward the
progress snapshots it emits to the client as Server-Sent Events. The final event
carries the resulting local path (what vLLM serves).
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Any

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from ..downloader import DownloadProgress, ModelType, download_model
from .models import DownloadPayload

router = APIRouter()

_SENTINEL = object()


@router.post("/api/download")
async def post_download(payload: DownloadPayload) -> EventSourceResponse:
    try:
        model_type = ModelType(payload.model_type)
    except ValueError:
        model_type = ModelType.MODEL

    events: queue.Queue[Any] = queue.Queue()

    def on_progress(p: DownloadProgress) -> None:
        events.put(
            {
                "type": "progress",
                "downloaded_bytes": p.downloaded_bytes,
                "total_bytes": p.total_bytes,
                "speed_bps": p.speed_bps,
                "percent": round(p.percent, 2),
            }
        )

    def worker() -> None:
        try:
            result = download_model(
                payload.repo_id,
                model_type,
                revision=payload.revision,
                progress_callback=on_progress,
            )
            events.put(
                {
                    "type": "done",
                    "repo_id": result.repo_id,
                    "local_dir": str(result.local_dir),
                }
            )
        except Exception as exc:  # noqa: BLE001 - surface any error to the client
            events.put({"type": "error", "message": str(exc)})
        finally:
            events.put(_SENTINEL)

    async def event_stream():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        loop = asyncio.get_running_loop()
        while True:
            item = await loop.run_in_executor(None, events.get)
            if item is _SENTINEL:
                break
            yield {"data": json.dumps(item)}

    return EventSourceResponse(event_stream())
