"""Add organizations, users, memberships, and leaderboard stats.

Revision ID: 003
Revises: 002
Create Date: 2026-05-13
"""
from __future__ import annotations

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS organizations (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      name TEXT NOT NULL,
      slug TEXT NOT NULL UNIQUE,
      plan TEXT NOT NULL DEFAULT 'free',
      max_members INTEGER NOT NULL DEFAULT 5,
      max_api_keys INTEGER NOT NULL DEFAULT 3,
      max_trajectories INTEGER NOT NULL DEFAULT 1000,
      metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS users (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      email TEXT NOT NULL UNIQUE,
      display_name TEXT NOT NULL DEFAULT '',
      avatar_url TEXT,
      auth_provider TEXT NOT NULL DEFAULT 'local',
      auth_provider_id TEXT,
      is_active BOOLEAN NOT NULL DEFAULT TRUE,
      metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS org_memberships (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
      user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','admin','member','viewer')),
      created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
      UNIQUE (org_id, user_id)
    )
    """)

    op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL")
    op.execute("ALTER TABLE trajectories ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE CASCADE")
    op.execute("ALTER TABLE trajectory_notes ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE CASCADE")

    op.execute("""
    CREATE TABLE IF NOT EXISTS user_stats (
      user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
      org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
      trajectories_contributed INTEGER NOT NULL DEFAULT 0,
      notes_generated INTEGER NOT NULL DEFAULT 0,
      helpful_votes_received INTEGER NOT NULL DEFAULT 0,
      tokens_saved_estimate INTEGER NOT NULL DEFAULT 0,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS trajectories_org_idx ON trajectories (org_id) WHERE org_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS trajectory_notes_org_idx ON trajectory_notes (org_id) WHERE org_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS org_memberships_user_idx ON org_memberships (user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS org_memberships_org_idx ON org_memberships (org_id)")
    op.execute("CREATE INDEX IF NOT EXISTS api_keys_org_idx ON api_keys (org_id) WHERE org_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS user_stats_org_idx ON user_stats (org_id) WHERE org_id IS NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS user_stats_leaderboard_idx ON user_stats (org_id, helpful_votes_received DESC)")

    op.execute("""
    DO $$ BEGIN
      CREATE TRIGGER organizations_set_updated_at
        BEFORE UPDATE ON organizations
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$
    """)

    op.execute("""
    DO $$ BEGIN
      CREATE TRIGGER users_set_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE trajectory_notes DROP COLUMN IF EXISTS org_id")
    op.execute("ALTER TABLE trajectories DROP COLUMN IF EXISTS org_id")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS user_id")
    op.execute("ALTER TABLE api_keys DROP COLUMN IF EXISTS org_id")
    op.execute("DROP TABLE IF EXISTS user_stats CASCADE")
    op.execute("DROP TABLE IF EXISTS org_memberships CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP TABLE IF EXISTS organizations CASCADE")
