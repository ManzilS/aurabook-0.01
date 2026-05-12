"""Configuration settings for the AuraBook local client."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Environment variables use the ``AURABOOK_`` prefix.
    """

    host: str = "127.0.0.1"
    port: int = 8080
    log_level: str = "INFO"
    dev_mode: bool = False
    demo_mode: bool = False
    cors_origins: str = "*"

    default_adapter: str = "openai_proxy"
    max_loops: int = 1

    request_timeout: float = 120.0
    max_body_size: int = 10_485_760
    api_key: str = ""
    cloud_enabled: bool = True
    cloud_demo_mode: bool = False
    cloud_api_base_url: str = "http://127.0.0.1:8000"
    cloud_request_timeout: float = 10.0
    cloud_retry_attempts: int = 3
    cloud_retry_base_delay_s: float = 0.5
    cloud_shared_secret: str = ""
    cloud_circuit_breaker_threshold: int = 5
    cloud_circuit_breaker_reset_s: float = 60.0

    upstream_base_url: str = "https://api.openai.com/v1"
    upstream_api_key: str = ""
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

    watch_root: str = "."
    watch_extensions_csv: str = ".py,.js,.ts"
    watcher_debounce_ms: int = 200
    watcher_settle_time_ms: int = 300
    state_dir: str = str(Path.home() / ".aurabook")
    demo_store_path: str = ""
    cache_persist_enabled: bool = True
    cache_persist_path: str = ""
    prefetch_cache_ttl_s: int = 900
    search_cache_ttl_s: int = 180
    recent_hash_window_s: int = 900
    prefetch_history_max_entries: int = 100
    search_cache_max_entries: int = 128
    max_gradient: str = "full"
    upload_enabled: bool = True
    auto_suppress_negative_feedback: bool = True
    notes_similarity_threshold: float = 0.78
    guide_similarity_threshold: float = 0.88
    walkthrough_similarity_threshold: float = 0.94
    upload_retry_interval_s: float = 15.0
    upload_retry_batch_size: int = 10

    model_config = SettingsConfigDict(
        env_prefix="AURABOOK_",
        env_file=".env",
        extra="ignore",
    )

    def resolve_watch_root(self) -> Path:
        """Return the resolved root directory used by the file watcher."""
        return Path(self.watch_root).expanduser().resolve()

    def allowed_extensions(self) -> tuple[str, ...]:
        """Return the configured source file extensions to watch."""
        extensions: list[str] = []
        for raw_extension in self.watch_extensions_csv.split(","):
            cleaned = raw_extension.strip().lower()
            if not cleaned:
                continue
            if not cleaned.startswith("."):
                cleaned = f".{cleaned}"
            extensions.append(cleaned)
        return tuple(extensions)

    def upstream_chat_completions_url(self) -> str:
        """Return the upstream OpenAI-compatible chat completions URL."""
        return f"{self.upstream_base_url.rstrip('/')}/chat/completions"

    def provider_base_url(self, provider: str) -> str:
        """Return the configured base URL for a named adapter."""
        normalized = self._normalize_provider_name(provider)
        return {
            "openai": self.upstream_base_url,
            "ollama": self.ollama_base_url,
            "lmstudio": self.lmstudio_base_url,
            "qwen": self.qwen_base_url,
            "claude": self.anthropic_base_url,
            "gemini": self.gemini_base_url,
        }.get(normalized, self.upstream_base_url)

    def provider_api_key(self, provider: str) -> str:
        """Return the configured API key for a named adapter."""
        normalized = self._normalize_provider_name(provider)
        return {
            "openai": self.upstream_api_key,
            "ollama": self.ollama_api_key,
            "lmstudio": self.lmstudio_api_key,
            "qwen": self.qwen_api_key,
            "claude": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
        }.get(normalized, self.upstream_api_key)

    @staticmethod
    def _normalize_provider_name(value: str) -> str:
        """Normalize provider aliases into canonical adapter names."""
        cleaned = value.strip().lower()
        aliases = {
            "openai_proxy": "openai",
            "anthropic": "claude",
            "llmstudio": "lmstudio",
        }
        return aliases.get(cleaned, cleaned or "openai")

    def resolve_state_dir(self) -> Path:
        """Return the resolved directory for local AuraBook state."""
        return Path(self.state_dir).expanduser().resolve()

    def resolve_status_path(self) -> Path:
        """Return the persisted runtime status JSON file path."""
        return self.resolve_state_dir() / "runtime_status.json"

    def resolve_preferences_path(self) -> Path:
        """Return the persisted user preferences JSON file path."""
        return self.resolve_state_dir() / "preferences.json"

    def resolve_queue_dir(self) -> Path:
        """Return the persisted failed-upload queue directory."""
        return self.resolve_state_dir() / "upload_queue"

    def resolve_cache_path(self) -> Path:
        """Return the persisted on-disk cache file path."""
        if self.cache_persist_path.strip():
            return Path(self.cache_persist_path).expanduser().resolve()
        return self.resolve_state_dir() / "community_cache.json"

    def resolve_demo_store_path(self) -> Path:
        """Return the local JSON store used for offline demo mode."""
        if self.demo_store_path.strip():
            return Path(self.demo_store_path).expanduser().resolve()
        return self.resolve_state_dir() / "demo_memory.json"
