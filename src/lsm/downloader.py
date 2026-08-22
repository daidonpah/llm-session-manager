"""Download HuggingFace models for local serving with vLLM.

Files land in two places under ``assets/`` (both configurable via ``LSM_``):

* ``hf_cache_dir`` -- the resumable, content-addressed HuggingFace cache.
* ``models_dir``   -- a clean, flat, vLLM-ready copy of each model, organized
  by :class:`ModelType` and repo id. Generative models sit at the root
  (``models/Qwen/Qwen2.5-7B-Instruct``); other kinds get a subdir
  (e.g. ``models/embeddings/BAAI/bge-m3``).

The path returned by :func:`download_model` is exactly what you hand to vLLM as
``--model <path>``.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .config import settings


class ModelType(str, Enum):
    """Kind of repo being downloaded; controls per-type file filtering."""

    MODEL = "model"
    EMBEDDING = "embedding"
    RERANKER = "reranker"
    TOKENIZER = "tokenizer"


# Weight shards and other large blobs we never need for a tokenizer-only pull.
_WEIGHT_PATTERNS = [
    "*.safetensors",
    "*.bin",
    "*.pt",
    "*.pth",
    "*.gguf",
    "*.onnx",
    "*.h5",
    "*.msgpack",
    "*.ot",
    "*.tflite",
]

# Duplicate/consolidated formats to skip when safetensors are present -- keeps
# the on-disk copy lean without losing anything vLLM needs.
_REDUNDANT_PATTERNS = ["*.pt", "*.pth", "*.h5", "*.msgpack", "consolidated*.pth"]

# Per-type ignore patterns applied on top of the caller's own ignore list.
_TYPE_IGNORE: dict[ModelType, list[str]] = {
    ModelType.MODEL: list(_REDUNDANT_PATTERNS),
    ModelType.EMBEDDING: list(_REDUNDANT_PATTERNS),
    ModelType.RERANKER: list(_REDUNDANT_PATTERNS),
    ModelType.TOKENIZER: list(_WEIGHT_PATTERNS),
}

# Subdirectory under models_dir per type. Generative models sit at the root of
# models_dir; other kinds get their own subdir so the tree stays organized.
_TYPE_SUBDIR: dict[ModelType, str] = {
    ModelType.MODEL: "",
    ModelType.EMBEDDING: "embeddings",
    ModelType.RERANKER: "rerankers",
    ModelType.TOKENIZER: "tokenizers",
}


@dataclass
class DownloadResult:
    """Outcome of a download: where the vLLM-ready copy lives."""

    repo_id: str
    model_type: ModelType
    local_dir: Path
    revision: str


@dataclass
class DownloadProgress:
    """A snapshot of aggregate download progress across all files in a repo."""

    downloaded_bytes: int
    total_bytes: int
    speed_bps: float

    @property
    def fraction(self) -> float:
        """Completion in ``[0, 1]``; ``0`` until the total size is known."""
        if self.total_bytes <= 0:
            return 0.0
        return min(self.downloaded_bytes / self.total_bytes, 1.0)

    @property
    def percent(self) -> float:
        return self.fraction * 100.0


ProgressCallback = Callable[[DownloadProgress], None]


def _make_progress_tqdm(callback: ProgressCallback) -> type:
    """Build a ``tqdm`` subclass that reports aggregate byte progress.

    ``snapshot_download`` spins up one bar per file (in worker threads); we sum
    their ``n``/``total`` and emit a single :class:`DownloadProgress` snapshot on
    every update. Only byte-scaled bars (``unit == "B"``) are aggregated so the
    outer "Fetching N files" counter bar is ignored.
    """
    from huggingface_hub.utils import tqdm as hf_tqdm

    lock = threading.Lock()
    # snapshot_download drives two byte bars: a "transfer" bar (network bytes,
    # whose total grows dynamically) and a "reconstruct" bar (bytes written to
    # disk, seeded with the true file total). We track the reconstruct bar for
    # completion since it maps 1:1 to the requested files. Bytes are accumulated
    # from each update()'s argument so progress is reported even when tqdm is
    # disabled (e.g. non-TTY), where reading self.n would return 0.
    accrued = {"done": 0, "start": 0.0}

    def _is_disk_bar(kwargs: dict[str, Any]) -> bool:
        return kwargs.get("unit") == "B" and "reconstruct" in str(
            kwargs.get("desc", "")
        ).lower()

    class _ProgressTqdm(hf_tqdm):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self._track = _is_disk_bar(kwargs)
            super().__init__(*args, **kwargs)

        def update(self, n: float | None = 1) -> bool | None:
            ret = super().update(n)
            if self._track:
                now = time.monotonic()
                with lock:
                    if not accrued["start"]:
                        accrued["start"] = now
                    accrued["done"] += int(n or 0)
                    done = accrued["done"]
                    elapsed = now - accrued["start"]
                # self.total is a plain attribute HF grows as bytes arrive; it is
                # readable regardless of the bar's disabled state. Speed is derived
                # from wall-clock so it works even when tqdm itself is disabled.
                total = int(self.total or 0)
                speed = done / elapsed if elapsed > 0 else 0.0
                callback(DownloadProgress(done, total, speed))
            return ret

    return _ProgressTqdm


def target_dir(repo_id: str, model_type: ModelType, models_root: Path) -> Path:
    """Compute the clean output directory for ``repo_id`` of ``model_type``."""
    subdir = _TYPE_SUBDIR[model_type]
    base = models_root / subdir if subdir else models_root
    return base / repo_id


def _configure_cache(cache_dir: Path) -> None:
    """Point the HF cache at the project-local dir before importing hub calls."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_dir))


def download_model(
    repo_id: str,
    model_type: ModelType = ModelType.MODEL,
    *,
    revision: str = "main",
    token: str | None = None,
    ignore_patterns: list[str] | None = None,
    allow_patterns: list[str] | None = None,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> DownloadResult:
    """Download ``repo_id`` from HuggingFace into a vLLM-ready local directory.

    ``model_type`` selects per-type file filtering (e.g. ``tokenizer`` skips
    weight shards). Extra ``allow_patterns`` / ``ignore_patterns`` are merged in.

    If ``progress_callback`` is given it receives :class:`DownloadProgress`
    snapshots (aggregate bytes/total/speed) as the download proceeds; otherwise
    the default HuggingFace terminal progress bars are shown.
    """
    cache_dir = Path(settings.hf_cache_dir).expanduser().resolve()
    models_root = Path(settings.models_dir).expanduser().resolve()
    _configure_cache(cache_dir)

    # Imported here so _configure_cache can set HF_HOME first.
    from huggingface_hub import snapshot_download

    merged_ignore = list(_TYPE_IGNORE.get(model_type, []))
    if ignore_patterns:
        merged_ignore.extend(ignore_patterns)

    out_dir = target_dir(repo_id, model_type, models_root)
    out_dir.mkdir(parents=True, exist_ok=True)

    extra: dict[str, Any] = {}
    if progress_callback is not None:
        extra["tqdm_class"] = _make_progress_tqdm(progress_callback)

    snapshot_download(
        repo_id=repo_id,
        repo_type="model",
        revision=revision,
        cache_dir=str(cache_dir / "hub"),
        local_dir=str(out_dir),
        token=token,
        allow_patterns=allow_patterns,
        ignore_patterns=merged_ignore or None,
        force_download=force,
        **extra,
    )

    return DownloadResult(
        repo_id=repo_id,
        model_type=model_type,
        local_dir=out_dir,
        revision=revision,
    )
