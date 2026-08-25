"""Request models for the setup webapp API."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field


class ConfigPayload(BaseModel):
    """A map of ``.env`` KEY -> VALUE to write (filtered against the allow-list)."""

    config: dict[str, str] = Field(default_factory=dict)

    def filtered(self, allowed: Iterable[str]) -> dict[str, str]:
        allowed = set(allowed)
        return {k: v for k, v in self.config.items() if k in allowed}


class CertPayload(BaseModel):
    """Raw PEM text for one cert slot (``shared`` / ``sm`` / ``openai``)."""

    slot: str = "shared"
    cert_pem: str
    key_pem: str


class CompletePayload(BaseModel):
    """Finalize setup: admin username + password (stored hashed)."""

    username: str = Field(default="admin", min_length=1)
    password: str = Field(min_length=8)


class DownloadPayload(BaseModel):
    """First-model download request (streamed progress via SSE)."""

    repo_id: str = Field(min_length=1)
    model_type: str = "model"
    revision: str = "main"
