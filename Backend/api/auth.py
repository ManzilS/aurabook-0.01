"""API key authentication for the AuraBook Cloud Brain.

Supports two auth mechanisms:
  1. API key via ``Authorization: Bearer <key>`` or ``X-API-Key: <key>``
  2. HMAC signature (existing mechanism for local clients)

API keys are stored hashed (SHA-256) in the ``api_keys`` table.  The
raw key is shown once at creation time and never stored.

When ``require_api_key`` is False (default), all requests are allowed
through — this keeps backward compatibility with the open demo mode.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "ab_"
API_KEY_BYTE_LENGTH = 32

_API_KEYS_DDL = """\
CREATE TABLE IF NOT EXISTS api_keys (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  key_prefix TEXT NOT NULL,
  scopes TEXT[] NOT NULL DEFAULT '{}',
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  last_used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);
CREATE INDEX IF NOT EXISTS api_keys_hash_idx ON api_keys (key_hash) WHERE is_active;
"""


def generate_api_key() -> tuple[str, str]:
    """Return (raw_key, key_hash).  The raw key is shown once to the user."""
    raw = API_KEY_PREFIX + secrets.token_hex(API_KEY_BYTE_LENGTH)
    return raw, _hash_key(raw)


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def extract_api_key(request: Request) -> str | None:
    """Pull the API key from the request headers."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-API-Key", "").strip() or None


async def validate_api_key(
    request: Request,
    *,
    require: bool,
    pool_getter: Any = None,
) -> dict[str, Any] | None:
    """Validate the request's API key against the database.

    Returns the key record dict on success, None if auth is not required
    and no key was provided.  Raises 401/403 on invalid keys.
    """
    raw_key = extract_api_key(request)

    if not raw_key:
        if require:
            raise HTTPException(status_code=401, detail="API key required")
        return None

    if pool_getter is None:
        if require:
            raise HTTPException(status_code=401, detail="API key required but no database configured")
        return None

    key_hash = _hash_key(raw_key)
    pool = await pool_getter()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, scopes, is_active FROM api_keys WHERE key_hash = $1",
            key_hash,
        )

    if row is None or not row["is_active"]:
        raise HTTPException(status_code=403, detail="Invalid or revoked API key")

    # Touch last_used_at asynchronously (best-effort)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE api_keys SET last_used_at = $1 WHERE id = $2",
                datetime.now(UTC), row["id"],
            )
    except Exception:
        pass

    return {
        "key_id": str(row["id"]),
        "name": row["name"],
        "scopes": list(row["scopes"] or []),
    }


def api_keys_ddl() -> str:
    """Return the DDL for the api_keys table."""
    return _API_KEYS_DDL
