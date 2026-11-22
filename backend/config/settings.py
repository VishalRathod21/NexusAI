"""Central configuration.

Every process (backend, agents, MCP servers, UI) reads its settings from here,
so there is one place that knows about environment variables and one place
that holds secrets. Secrets are `SecretStr` so they do not leak through repr()
or logs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "NexusAI"
    log_level: str = "INFO"

    # --- LLM ---
    llm_provider: Literal["offline", "groq", "gemini", "openai"] = "offline"
    llm_model: str = ""
    llm_temperature: float = 0.2
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    groq_api_key: SecretStr | None = None
    google_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    llm_price_input_per_1m: float = 0.0
    llm_price_output_per_1m: float = 0.0

    # --- Orchestration ---
    orchestration_mode: Literal["a2a", "local"] = "a2a"
    market_agent_url: str = "http://127.0.0.1:9001"
    competitor_agent_url: str = "http://127.0.0.1:9002"
    customer_agent_url: str = "http://127.0.0.1:9003"
    strategy_agent_url: str = "http://127.0.0.1:9004"
    agent_timeout_seconds: float = 180.0
    tool_timeout_seconds: float = 25.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5

    # --- MCP ---
    mcp_web_url: str = "http://127.0.0.1:9101/mcp"
    mcp_business_url: str = "http://127.0.0.1:9102/mcp"
    mcp_calculator_url: str = "http://127.0.0.1:9103/mcp"
    web_search_backend: Literal["ddgs", "fixture"] = "ddgs"

    # --- Persistence ---
    database_url: str = "sqlite+aiosqlite:///./nexusai.db"
    redis_url: str = ""
    cache_ttl_seconds: int = 3600

    # --- Observability ---
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
    audit_log_path: str = "logs/security_events.jsonl"

    # --- Accounts and access ---
    allow_signup: bool = True
    session_days: int = 7
    # Set to true when the site is served over HTTPS, so the session cookie
    # is never sent over plain HTTP.
    cookie_secure: bool = False
    # Shared secret between the backend, the agents and the MCP servers. When
    # set, agents and MCP servers reject calls that do not present it.
    internal_api_token: SecretStr | None = None

    # --- Frontend ---
    api_base_url: str = "http://127.0.0.1:8000"
    # Browser origins allowed to call the API directly. The React dev server
    # proxies /api, so this only matters if the UI is served from elsewhere.
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # ------------------------------------------------------------ helpers
    @property
    def resolved_model(self) -> str:
        return self.llm_model or DEFAULT_MODELS.get(self.llm_provider, "offline")

    def provider_api_key(self) -> str | None:
        key = {
            "groq": self.groq_api_key,
            "gemini": self.google_api_key,
            "openai": self.openai_api_key,
        }.get(self.llm_provider)
        value = key.get_secret_value().strip() if key else ""
        return value or None

    def agent_urls(self) -> list[str]:
        """Seed list for A2A discovery. Which agent does what is learned from
        the Agent Cards served at these URLs, not from this list."""
        return [
            self.market_agent_url,
            self.competitor_agent_url,
            self.customer_agent_url,
            self.strategy_agent_url,
        ]

    def mcp_servers(self) -> dict[str, str]:
        return {
            "web_research": self.mcp_web_url,
            "business_data": self.mcp_business_url,
            "market_calculator": self.mcp_calculator_url,
        }

    def internal_token(self) -> str | None:
        value = self.internal_api_token.get_secret_value().strip() if self.internal_api_token else ""
        return value or None

    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def secret_values(self) -> list[str]:
        """Actual secret strings, used by output validation to catch leaks."""
        secrets = [
            self.groq_api_key,
            self.google_api_key,
            self.openai_api_key,
            self.langfuse_secret_key,
            self.internal_api_token,
        ]
        values = [s.get_secret_value().strip() for s in secrets if s]
        return [v for v in values if len(v) >= 8]


@lru_cache
def get_settings() -> Settings:
    return Settings()
