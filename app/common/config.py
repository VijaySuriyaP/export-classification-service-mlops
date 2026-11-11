"""Runtime configuration (D4).

Every environment-specific value - endpoints, credentials, model identifiers, limits - is read
from environment variables injected at runtime (see ``.env.example``). Nothing environment
specific has a default here: endpoints default to empty, which disables the dependency.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # --- service ---
    service_name: str = "export-classification"
    log_level: str = "INFO"

    # --- infrastructure (required at runtime) ---
    redis_url: str = ""

    # --- run lifecycle ---
    run_ttl_seconds: int = 7 * 24 * 3600
    run_max_attempts: int = Field(default=2, ge=1, le=5)
    run_wall_clock_timeout_s: float = Field(default=120.0, gt=0)
    run_max_steps: int = Field(default=12, ge=1)
    run_max_tool_calls: int = Field(default=20, ge=1)
    run_max_llm_calls: int = Field(default=10, ge=1)
    tool_timeout_s: float = Field(default=10.0, gt=0)

    # --- API ---
    max_request_bytes: int = 16_384
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = Field(default=30, ge=1)
    cors_allow_origins: str = ""

    # --- caching (D7) ---
    run_cache_ttl_seconds: int = Field(default=0, ge=0, description="0 disables whole-run caching")

    # --- tracing ---
    otel_exporter_otlp_endpoint: str = ""

    # --- worker ---
    worker_metrics_port: int = 9100
    worker_heartbeat_ttl_s: int = 30
    agent_runner: str = "app.agents.stub_runner:StubAgentRunner"

    # --- LLM provider (primary + optional fallback), behind our own interface ---
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_price_prompt_per_1k: float = 0.0
    llm_price_completion_per_1k: float = 0.0
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""
    llm_fallback_price_prompt_per_1k: float = 0.0
    llm_fallback_price_completion_per_1k: float = 0.0

    # --- resilience (A5) ---
    llm_timeout_s: float = Field(default=30.0, gt=0)
    llm_max_attempts: int = Field(default=3, ge=1, le=6)
    llm_backoff_base_s: float = Field(default=0.5, ge=0)
    llm_backoff_max_s: float = Field(default=8.0, ge=0)
    cb_failure_threshold: int = Field(default=5, ge=1)
    cb_reset_timeout_s: float = Field(default=30.0, gt=0)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
