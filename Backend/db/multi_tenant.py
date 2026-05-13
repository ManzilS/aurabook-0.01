"""Multi-tenancy models and DDL for team/org support.

Data model:
  - Organizations own trajectories and notes
  - Users belong to organizations via memberships (with roles)
  - API keys are scoped to an organization
  - Trajectories and notes carry an org_id for row-level filtering
  - A NULL org_id means "global / community" (shared across all orgs)

Roles:
  - owner:  full admin, can manage members and API keys
  - admin:  can manage members, read all data
  - member: can read/write trajectories and notes
  - viewer: read-only access
"""

from __future__ import annotations

MULTI_TENANT_DDL = """\
-- Organizations
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
);

-- Users
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
);

-- Org membership
CREATE TABLE IF NOT EXISTS org_memberships (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner','admin','member','viewer')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
  UNIQUE (org_id, user_id)
);

-- Scope API keys to orgs
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;

-- Scope trajectories to orgs
ALTER TABLE trajectories ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE CASCADE;

-- Scope notes to orgs (via trajectory relationship, but also directly for queries)
ALTER TABLE trajectory_notes ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE CASCADE;

-- Leaderboard stats
CREATE TABLE IF NOT EXISTS user_stats (
  user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
  trajectories_contributed INTEGER NOT NULL DEFAULT 0,
  notes_generated INTEGER NOT NULL DEFAULT 0,
  helpful_votes_received INTEGER NOT NULL DEFAULT 0,
  tokens_saved_estimate INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

-- Indexes for multi-tenant queries
CREATE INDEX IF NOT EXISTS trajectories_org_idx ON trajectories (org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS trajectory_notes_org_idx ON trajectory_notes (org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS org_memberships_user_idx ON org_memberships (user_id);
CREATE INDEX IF NOT EXISTS org_memberships_org_idx ON org_memberships (org_id);
CREATE INDEX IF NOT EXISTS api_keys_org_idx ON api_keys (org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS user_stats_org_idx ON user_stats (org_id) WHERE org_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS user_stats_leaderboard_idx ON user_stats (org_id, helpful_votes_received DESC);

-- Updated-at triggers
DO $$ BEGIN
  CREATE TRIGGER organizations_set_updated_at
    BEFORE UPDATE ON organizations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  CREATE TRIGGER users_set_updated_at
    BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
"""


PLAN_LIMITS: dict[str, dict[str, int]] = {
    "free": {"max_members": 5, "max_api_keys": 3, "max_trajectories": 1000},
    "starter": {"max_members": 25, "max_api_keys": 10, "max_trajectories": 10000},
    "team": {"max_members": 100, "max_api_keys": 50, "max_trajectories": 100000},
    "enterprise": {"max_members": 10000, "max_api_keys": 500, "max_trajectories": 10000000},
}
