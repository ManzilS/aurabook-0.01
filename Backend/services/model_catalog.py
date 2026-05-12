"""Live-demo model discovery and API key validation."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any

import httpx


_CACHE_TTL_S = 300
_KEY_CACHE: dict[tuple[str, str], tuple[float, list[str]]] = {}
_PROVIDER_WARM_CACHE: dict[str, tuple[float, list[str]]] = {}


@dataclass(slots=True)
class ModelListResult:
    provider: str
    valid: bool
    models: list[str]
    default_model: str
    cache_status: str
    error: str | None = None


def detect_provider(api_key: str) -> str:
    key = api_key.strip()
    if key.startswith("sk-ant-"):
        return "claude"
    if key.startswith("AIza"):
        return "gemini"
    return "openai"


def warm_models(provider: str) -> ModelListResult:
    normalized = _normalize_provider(provider)
    cached = _fresh_provider_cache(normalized)
    models = cached or _fallback_models(normalized)
    return ModelListResult(
        provider=normalized,
        valid=False,
        models=models,
        default_model=models[0] if models else "",
        cache_status="warm" if cached else "fallback",
        error=None if cached else "Using fallback list until the API key is validated.",
    )


async def list_models_for_key(api_key: str, *, provider: str | None = None) -> ModelListResult:
    normalized = _normalize_provider(provider or detect_provider(api_key))
    key_hash = _key_hash(api_key)
    cache_key = (normalized, key_hash)
    cached = _fresh_key_cache(cache_key)
    if cached:
        return ModelListResult(
            provider=normalized,
            valid=True,
            models=cached,
            default_model=cached[0] if cached else "",
            cache_status="key-cache",
        )

    try:
        models = await _fetch_models(normalized, api_key)
    except httpx.HTTPStatusError as exc:
        return ModelListResult(
            provider=normalized,
            valid=False,
            models=_fresh_provider_cache(normalized) or _fallback_models(normalized),
            default_model="",
            cache_status="warm",
            error=_extract_error_message(exc.response),
        )
    except httpx.HTTPError as exc:
        return ModelListResult(
            provider=normalized,
            valid=False,
            models=_fresh_provider_cache(normalized) or _fallback_models(normalized),
            default_model="",
            cache_status="warm",
            error=str(exc),
        )

    now = time.time()
    _KEY_CACHE[cache_key] = (now, models)
    _PROVIDER_WARM_CACHE[normalized] = (now, models)
    return ModelListResult(
        provider=normalized,
        valid=True,
        models=models,
        default_model=models[0] if models else "",
        cache_status="live",
    )


async def _fetch_models(provider: str, api_key: str) -> list[str]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        if provider == "claude":
            response = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
            )
            response.raise_for_status()
            payload = response.json()
            return _coerce_anthropic_models(payload)

        if provider == "gemini":
            response = await client.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"key": api_key},
            )
            response.raise_for_status()
            payload = response.json()
            return _coerce_gemini_models(payload)

        response = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        return _coerce_openai_models(payload)


def _coerce_openai_models(payload: dict[str, Any]) -> list[str]:
    ids = [
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    allowed_prefixes = ("gpt-", "o1", "o3", "o4", "chatgpt-")
    excluded = (
        "audio",
        "embedding",
        "moderation",
        "realtime",
        "search",
        "speech",
        "transcribe",
        "tts",
        "whisper",
        "image",
        "dall-e",
    )
    return _sorted_models(
        model
        for model in ids
        if model.startswith(allowed_prefixes) and not any(part in model for part in excluded)
    )


def _coerce_anthropic_models(payload: dict[str, Any]) -> list[str]:
    ids = [
        item.get("id")
        for item in payload.get("data", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    return _sorted_models(model for model in ids if model.startswith("claude-"))


def _coerce_gemini_models(payload: dict[str, Any]) -> list[str]:
    models: list[str] = []
    for item in payload.get("models", []):
        if not isinstance(item, dict):
            continue
        methods = item.get("supportedGenerationMethods", [])
        if "generateContent" not in methods:
            continue
        name = item.get("name")
        if isinstance(name, str) and name.startswith("models/"):
            models.append(name.removeprefix("models/"))
    return _sorted_models(models)


def _sorted_models(models: Any) -> list[str]:
    unique = sorted({model for model in models if isinstance(model, str) and model.strip()})
    preferred = [
        model
        for model in unique
        if any(marker in model for marker in ("gpt-5", "gpt-4.1", "gpt-4o", "o4", "claude", "gemini-2.5"))
    ]
    remaining = [model for model in unique if model not in preferred]
    return preferred + remaining


def _normalize_provider(provider: str) -> str:
    cleaned = provider.strip().lower()
    aliases = {"anthropic": "claude"}
    return aliases.get(cleaned, cleaned or "openai")


def _fallback_models(provider: str) -> list[str]:
    if provider == "claude":
        return ["claude-sonnet-4-20250514", "claude-haiku-4-5-20251001"]
    if provider == "gemini":
        return ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash"]
    return ["gpt-4o-mini", "gpt-4o"]


def _fresh_key_cache(cache_key: tuple[str, str]) -> list[str] | None:
    cached = _KEY_CACHE.get(cache_key)
    if not cached:
        return None
    timestamp, models = cached
    if time.time() - timestamp > _CACHE_TTL_S:
        _KEY_CACHE.pop(cache_key, None)
        return None
    return models


def _fresh_provider_cache(provider: str) -> list[str] | None:
    cached = _PROVIDER_WARM_CACHE.get(provider)
    if not cached:
        return None
    timestamp, models = cached
    if time.time() - timestamp > _CACHE_TTL_S:
        _PROVIDER_WARM_CACHE.pop(provider, None)
        return None
    return models


def _key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.strip().encode("utf-8")).hexdigest()


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text or f"HTTP {response.status_code}"
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str):
            return error
        if isinstance(payload.get("message"), str):
            return payload["message"]
    return response.text or f"HTTP {response.status_code}"
