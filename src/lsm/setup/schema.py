"""Editable configuration surface for the setup webapp + filesystem helpers.

``EDITABLE_VARS`` is the allow-list of ``.env`` keys the webapp may write. Any
key posted that is not in this set is rejected, so the setup UI can never inject
arbitrary environment. Secrets (tokens, password hashes) are marked so the API
never echoes their values back to the browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VarSpec:
    key: str
    default: str = ""
    secret: bool = False
    group: str = "general"


# Allow-list of writable variables, grouped for the wizard UI.
EDITABLE_VARS: tuple[VarSpec, ...] = (
    # Upstream model server / app defaults.
    VarSpec("LSM_MODEL_BASE_URL", "http://host.docker.internal:1234/v1", group="model"),
    VarSpec("LSM_MODEL_API_KEY", "not-needed", secret=True, group="model"),
    VarSpec("LSM_DEFAULT_MODEL", "local-model", group="model"),
    VarSpec("LSM_API_PORT", "8080", group="app"),
    VarSpec("LSM_BIND_HOST", "127.0.0.1", group="app"),
    # nginx reverse proxy (hostnames + published ports + upstream).
    VarSpec("NGINX_SM_SERVER_NAME", "localhost", group="nginx"),
    VarSpec("NGINX_OPENAI_SERVER_NAME", "openai.localhost", group="nginx"),
    VarSpec("NGINX_OPENAI_UPSTREAM", "host.docker.internal:1234", group="nginx"),
    VarSpec("NGINX_HTTP_PORT", "80", group="nginx"),
    VarSpec("NGINX_HTTPS_PORT", "443", group="nginx"),
    # vLLM (DGX Spark profile).
    VarSpec("VLLM_IMAGE", "ghcr.io/timothystewart6/vllm-gb10:latest", group="vllm"),
    VarSpec("VLLM_MODEL_PATH", "", group="vllm"),
    VarSpec("VLLM_SERVED_MODEL_NAME", "qwen3.8-27b", group="vllm"),
    VarSpec("VLLM_MAX_MODEL_LEN", "262144", group="vllm"),
    VarSpec("VLLM_GPU_MEMORY_UTILIZATION", "0.9", group="vllm"),
    # HuggingFace token for gated repos (used by the downloader).
    VarSpec("HF_TOKEN", "", secret=True, group="model"),
)

EDITABLE_KEYS = frozenset(v.key for v in EDITABLE_VARS)
SECRET_KEYS = frozenset(v.key for v in EDITABLE_VARS if v.secret)

# Recognised cert/key slots -> filename under nginx/certs.
CERT_SLOTS: dict[str, tuple[str, str]] = {
    "shared": ("server.crt", "server.key"),
    "sm": ("sm.crt", "sm.key"),
    "openai": ("openai.crt", "openai.key"),
}


def repo_root() -> Path:
    """Directory that holds ``.env`` / ``nginx`` (override via ``LSM_REPO_ROOT``).

    In Docker the repo is bind-mounted at ``/app``; locally it is the project
    directory. ``LSM_REPO_ROOT`` lets tests point this at a temp dir.
    """
    env = os.environ.get("LSM_REPO_ROOT")
    if env:
        return Path(env)
    # src/lsm/setup/schema.py -> repo root is three parents up from lsm/.
    return Path(__file__).resolve().parents[3]


def env_path() -> Path:
    return repo_root() / ".env"


def certs_dir() -> Path:
    return repo_root() / "nginx" / "certs"


def write_cert(slot: str, cert_pem: str, key_pem: str) -> tuple[Path, Path]:
    """Validate + write a cert/key PEM pair for ``slot`` into ``nginx/certs``.

    Returns the (cert_path, key_path). Keys are written with ``0600`` perms.
    Raises ``ValueError`` on an unknown slot or non-PEM input.
    """
    if slot not in CERT_SLOTS:
        raise ValueError(f"unknown cert slot: {slot!r}")
    _require_pem(cert_pem, "CERTIFICATE")
    _require_pem(key_pem, "PRIVATE KEY")
    crt_name, key_name = CERT_SLOTS[slot]
    d = certs_dir()
    d.mkdir(parents=True, exist_ok=True)
    crt_path, key_path = d / crt_name, d / key_name
    crt_path.write_text(_normalize_pem(cert_pem), encoding="utf-8")
    key_path.write_text(_normalize_pem(key_pem), encoding="utf-8")
    os.chmod(key_path, 0o600)
    return crt_path, key_path


def _require_pem(text: str, kind: str) -> None:
    if "-----BEGIN" not in text or "-----END" not in text:
        raise ValueError(f"expected a PEM-encoded {kind.lower()}")


def _normalize_pem(text: str) -> str:
    return text.strip().replace("\r\n", "\n") + "\n"
