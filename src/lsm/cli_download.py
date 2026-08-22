"""Console entrypoint ``lsm-download`` for fetching HuggingFace models.

Examples::

    lsm-download Qwen/Qwen2.5-7B-Instruct
    lsm-download BAAI/bge-m3 --type embedding
    lsm-download meta-llama/Llama-3.1-8B --token "$HF_TOKEN"

The printed path is what you pass to vLLM as ``--model <path>``.
"""

from __future__ import annotations

import argparse
import os
import sys

from .downloader import DownloadProgress, ModelType, download_model


def _format_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _make_progress_printer():
    """Return a callback that renders a single, updating aggregate progress line."""

    def _print(p: DownloadProgress) -> None:
        done = _format_bytes(p.downloaded_bytes)
        total = _format_bytes(p.total_bytes) if p.total_bytes else "?"
        speed = f"{_format_bytes(p.speed_bps)}/s" if p.speed_bps else "--"
        line = f"\r  {p.percent:5.1f}%  {done} / {total}  @ {speed}   "
        sys.stderr.write(line)
        sys.stderr.flush()

    return _print


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lsm-download",
        description="Download a HuggingFace model into a vLLM-ready local directory.",
    )
    parser.add_argument("repo_id", help="HuggingFace repo id, e.g. Qwen/Qwen2.5-7B-Instruct")
    parser.add_argument(
        "-t",
        "--type",
        dest="model_type",
        choices=[t.value for t in ModelType],
        default=ModelType.MODEL.value,
        help="Model kind; controls per-type file filtering (default: model).",
    )
    parser.add_argument(
        "-r",
        "--revision",
        default="main",
        help="Branch, tag or commit to download (default: main).",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="HuggingFace access token for gated/private repos.",
    )
    parser.add_argument(
        "--allow",
        action="append",
        metavar="PATTERN",
        help="Only download files matching this glob (repeatable).",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        metavar="PATTERN",
        help="Skip files matching this glob, on top of type defaults (repeatable).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if files are already cached.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress the progress display.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Silence HF's per-file bars in both modes: quiet suppresses all output, and
    # otherwise we render our own single aggregate line in their place.
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    progress_callback = None if args.quiet else _make_progress_printer()

    try:
        result = download_model(
            args.repo_id,
            ModelType(args.model_type),
            revision=args.revision,
            token=args.token,
            allow_patterns=args.allow,
            ignore_patterns=args.ignore,
            force=args.force,
            progress_callback=progress_callback,
        )
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001 - surface a clean CLI error
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if progress_callback is not None:
            sys.stderr.write("\n")

    print(f"Downloaded {result.repo_id} ({result.model_type.value}) to:")
    print(f"  {result.local_dir}")
    print("\nServe it with vLLM, e.g.:")
    print(f"  vllm serve {result.local_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
