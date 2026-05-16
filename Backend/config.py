"""Shared configuration for the AuraBook Cloud Brain project."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"
    demo_mode: bool = False
    demo_store_path: str = ".demo/cloud_brain.json"

    db_backend: str = ""
    database_url: str = ""
    cors_allowed_origins: str = ""

    supabase_url: str = ""
    supabase_service_role_key: str = ""
    demo_supabase_url: str = ""
    demo_supabase_service_role_key: str = ""
    demo_runtime_environment: str = "standard"

    generation_provider: str = "openai"
    embedding_provider: str = "openai"
    generation_model: str = ""
    summary_model: str = ""
    fix_model: str = ""
    embedding_model: str = ""
    provider_request_timeout: float = 120.0
    provider_max_output_tokens: int = 2048

    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    openai_summary_model: str = "gpt-5.4-nano"
    openai_fix_model: str = "gpt-5.4-mini"

    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_api_key: str = "ollama"
    lmstudio_base_url: str = "http://127.0.0.1:1234/v1"
    lmstudio_api_key: str = "lm-studio"
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
    qwen_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: str = ""
    anthropic_version: str = "2023-06-01"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_api_key: str = ""

    openai_retry_attempts: int = 3
    openai_retry_base_delay_s: float = 1.0

    # With text-embedding-3-small, short-query vs long-document (full trajectory)
    # cosine similarity lands in the 0.35–0.55 range for relevant matches and
    # below 0.20 for unrelated content. 0.30 is the right production floor.
    search_match_threshold: float = 0.30
    # Demo mode uses deterministic token-hash embeddings that top out ~0.55;
    # a lower threshold compensates for the weaker signal.
    demo_search_match_threshold: float = 0.15
    search_match_count: int = 5
    search_candidate_multiplier: int = 3
    retrieval_vector_weight: float = 0.65
    retrieval_lexical_weight: float = 0.25
    retrieval_quality_weight: float = 0.10
    worker_batch_size: int = 10
    cloud_api_base_url: str = "http://127.0.0.1:8000"
    api_shared_secret: str = ""
    api_signature_max_age_s: int = 300
    idempotency_ttl_s: int = 86400
    retention_days: int = 30
    require_api_key: bool = False
    rate_limit_rpm: float = 120.0
    rate_limit_burst: float = 30.0
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = ""
    session_secret: str = ""
    frontend_url: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_plan_map: str = ""  # e.g. "price_abc:starter,price_def:team,price_ghi:enterprise"

    model_config = SettingsConfigDict(
        env_prefix="AURABOOK_CLOUD_",
        env_file=".env",
        extra="ignore",
    )

    def normalized_generation_provider(self) -> str:
        """Return the normalized provider used for summaries and fixes."""
        return self._normalize_provider_name(self.generation_provider)

    def normalized_embedding_provider(self) -> str:
        """Return the normalized provider used for embeddings."""
        return self._normalize_provider_name(self.embedding_provider)

    def generation_provider_ready(self) -> bool:
        """Return whether the configured generation provider has usable settings."""
        provider = self.normalized_generation_provider()
        if provider in {"ollama", "lmstudio"}:
            return bool(self.provider_base_url(provider))
        return bool(self.provider_api_key(provider))

    def embedding_provider_ready(self) -> bool:
        """Return whether the configured embedding provider has usable settings."""
        provider = self.normalized_embedding_provider()
        if provider == "claude":
            return False
        if provider in {"ollama", "lmstudio"}:
            return bool(self.provider_base_url(provider))
        return bool(self.provider_api_key(provider))

    def provider_api_key(self, provider: str) -> str:
        """Return the API key configured for a provider."""
        normalized = self._normalize_provider_name(provider)
        return {
            "openai": self.openai_api_key,
            "ollama": self.ollama_api_key,
            "lmstudio": self.lmstudio_api_key,
            "qwen": self.qwen_api_key,
            "claude": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
        }.get(normalized, "")

    def provider_base_url(self, provider: str) -> str:
        """Return the configured base URL for a provider."""
        normalized = self._normalize_provider_name(provider)
        return {
            "openai": self.openai_base_url,
            "ollama": self.ollama_base_url,
            "lmstudio": self.lmstudio_base_url,
            "qwen": self.qwen_base_url,
            "claude": self.anthropic_base_url,
            "gemini": self.gemini_base_url,
        }.get(normalized, "")

    def resolve_summary_model(self) -> str:
        """Return the model identifier used by the note summarizer."""
        return self._resolve_generation_model(
            purpose="summary",
            explicit=self.summary_model,
            openai_default=self.openai_summary_model,
        )

    def resolve_fix_model(self) -> str:
        """Return the model identifier used by the fix and swarm flows."""
        return self._resolve_generation_model(
            purpose="fix",
            explicit=self.fix_model,
            openai_default=self.openai_fix_model,
        )

    def resolve_embedding_model(self) -> str:
        """Return the model identifier used by the embedding provider."""
        if self.embedding_model.strip():
            return self.embedding_model.strip()

        provider = self.normalized_embedding_provider()
        if provider == "openai":
            return self.openai_embedding_model
        if provider == "gemini":
            return "gemini-embedding-001"
        if provider in {"ollama", "lmstudio"}:
            return "nomic-embed-text"
        if provider == "qwen":
            return "text-embedding-v4"
        if provider == "claude":
            raise ValueError(
                "Anthropic does not provide an embedding path for AuraBook. "
                "Use a separate embedding provider such as openai, gemini, ollama, lmstudio, or qwen."
            )
        raise ValueError(f"Unsupported embedding provider '{provider}'")

    def _resolve_generation_model(
        self,
        *,
        purpose: Literal["summary", "fix"],
        explicit: str,
        openai_default: str,
    ) -> str:
        """Resolve the generation model for the current provider and purpose."""
        if explicit.strip():
            return explicit.strip()
        if self.generation_model.strip():
            return self.generation_model.strip()

        provider = self.normalized_generation_provider()
        if provider == "openai":
            return openai_default
        if provider == "ollama":
            return "llama3.2"
        if provider == "lmstudio":
            return "local-model"
        if provider == "qwen":
            return "qwen-plus"
        if provider == "claude":
            return "claude-sonnet-4-20250514"
        if provider == "gemini":
            return "gemini-2.5-flash"
        raise ValueError(f"Unsupported generation provider '{provider}'")

    @staticmethod
    def _normalize_provider_name(value: str) -> str:
        """Normalize a provider alias into its canonical name."""
        cleaned = value.strip().lower()
        aliases = {
            "openai_proxy": "openai",
            "anthropic": "claude",
            "llmstudio": "lmstudio",
        }
        return aliases.get(cleaned, cleaned or "openai")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached settings instance."""
    return Settings()
