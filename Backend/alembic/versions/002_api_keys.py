"""Add api_keys table for authentication.

Revision ID: 002
Revises: 001
Create Date: 2026-05-12
"""
from __future__ import annotations

from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS api_keys (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      name TEXT NOT NULL,
      key_hash TEXT NOT NULL UNIQUE,
      key_prefix TEXT NOT NULL,
      scopes TEXT[] NOT NULL DEFAULT '{}',
      is_active BOOLEAN NOT NULL DEFAULT TRUE,
      last_used_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS api_keys_hash_idx ON api_keys (key_hash) WHERE is_active")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS api_keys CASCADE")
