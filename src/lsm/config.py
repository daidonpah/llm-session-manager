"""Application configuration loaded from environment (prefix ``LSM_``)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the API and the ARQ worker."""

    model_config = SettingsConfigDict(
        env_prefix="LSM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Upstream OpenAI-compatible model server
    model_base_url: str = "http://localhost:8000/v1"
    model_api_key: str = "not-needed"
    default_model: str = "local-model"

    # HTTP client timeouts (seconds)
    connect_timeout_s: float = 10.0
    request_timeout_s: float = 300.0

    # Worker concurrency + retries
    worker_max_jobs: int = 10
    worker_job_timeout_s: int = 600
    max_retries: int = 3
    retry_backoff_base_s: float = 2.0

    # Data lifetimes (seconds)
    job_ttl_s: int = 86_400
    token_buffer_ttl_s: int = 3_600

    # Synchronous request behaviour
    sync_wait_timeout_s: float = 300.0

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    log_level: str = "INFO"

    # Model assets (HuggingFace downloads for use with vLLM)
    # `hf_cache_dir` is the resumable/deduplicated HF cache (HF_HOME/hub);
    # `models_dir` holds clean, flat, vLLM-ready copies of each downloaded model.
    assets_dir: str = "assets"
    hf_cache_dir: str = "assets/hf-cache"
    models_dir: str = "assets/models"

    @property
    def model_base_url_clean(self) -> str:
        """Base URL without a trailing slash."""
        return self.model_base_url.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()


settings = get_settings()
