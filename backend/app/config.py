"""Centralized configuration.

Every runtime knob is a Settings field, populated from environment variables
with the SF_ prefix (or a local .env file). This keeps the service
deployable on any container host and testable in-process by overriding
fields directly.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def app_root() -> Path:
    """Project root (parent of backend/)."""
    return Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- app ---
    environment: str = "dev"
    log_level: str = "INFO"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    api_key: str | None = None  # when set, all /v1 routes require X-API-Key

    # --- storage / persistence ---
    database_url: str | None = None  # default: sqlite under .data/
    storage_dir: Path | None = None  # default: .data/storage under app root

    # --- audio / mastering (stream-ready targets) ---
    target_lufs: float = -14.0      # Spotify/YouTube loudness target
    true_peak_db: float = -1.0      # ITU-R BS.1770 true-peak ceiling
    sample_rate: int = 44100

    # --- pipeline / SLO ---
    preview_seconds: int = 30       # hook preview length pushed to user
    preview_slo_seconds: float = 25.0
    default_duration_secs: int = 190
    min_duration_secs: int = 60
    max_duration_secs: int = 300
    max_stage_attempts: int = 3
    stage_retry_base_delay_s: float = 1.0
    stage_retry_max_delay_s: float = 8.0

    # --- cost control (USD) ---
    job_budget_usd: float = 2.00        # per-job hard cap
    daily_cost_cap_usd: float = 500.0   # fleet-wide rolling-day cap
    pricing_preview_usd: float = 0.02   # billed when hook/preview stage succeeds
    pricing_full_usd: float = 0.15      # billed when full-compose stage succeeds
    pricing_mix_master_usd: float = 0.01

    # --- embedded worker ---
    enable_worker: bool = True
    worker_concurrency: int = 2
    worker_poll_interval_s: float = 0.5

    # --- music provider: simulated | http ---
    provider: str = "simulated"
    provider_base_url: str | None = None
    provider_api_key: str | None = None
    provider_timeout_s: float = 240.0

    # --- LLM (optional OpenAI-compatible gateway for lyrics/intent) ---
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "gpt-4o-mini"
    llm_timeout_s: float = 30.0

    # --- observability ---
    otel_endpoint: str | None = None  # OTLP/HTTP traces endpoint (optional)

    # --- inputs ---
    max_upload_bytes: int = 25 * 1024 * 1024
    min_voice_secs: float = 3.0
    max_voice_secs: float = 30.0

    # ------------------------------------------------------------------
    @property
    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        root = app_root() / ".data"
        root.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{(root / 'songforge.db').as_posix()}"

    @property
    def effective_storage_dir(self) -> Path:
        d = self.storage_dir or (app_root() / ".data" / "storage")
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key)

    def daily_cost_cap_for_test(self) -> float:
        return self.daily_cost_cap_usd


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Used by tests to re-read env / tmp paths."""
    get_settings.cache_clear()
