"""Baseline schema — trajectories, nodes, edges, notes.

Revision ID: 001
Revises: None
Create Date: 2026-05-12
"""
from __future__ import annotations

from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute("""
    CREATE TABLE IF NOT EXISTS trajectories (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      source TEXT NOT NULL DEFAULT 'local_client',
      client_id TEXT,
      cdc_hash TEXT UNIQUE,
      trajectory_fingerprint TEXT NOT NULL,
      original_goal TEXT,
      original_prompt TEXT NOT NULL,
      broken_code TEXT,
      error_log TEXT NOT NULL,
      ai_reflection TEXT,
      final_fix TEXT NOT NULL,
      history JSONB NOT NULL DEFAULT '[]'::JSONB,
      metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
      processed BOOLEAN NOT NULL DEFAULT FALSE,
      note_model TEXT,
      note_error TEXT,
      processed_at TIMESTAMPTZ,
      created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS trajectory_nodes (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      trajectory_id UUID NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
      node_type TEXT NOT NULL CHECK (node_type IN ('prompt','attempt','error','reflection','fix')),
      content TEXT NOT NULL,
      metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS trajectory_edges (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      trajectory_id UUID NOT NULL REFERENCES trajectories(id) ON DELETE CASCADE,
      source_node_id UUID NOT NULL REFERENCES trajectory_nodes(id) ON DELETE CASCADE,
      target_node_id UUID NOT NULL REFERENCES trajectory_nodes(id) ON DELETE CASCADE,
      edge_type TEXT NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS trajectory_notes (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      trajectory_id UUID NOT NULL UNIQUE REFERENCES trajectories(id) ON DELETE CASCADE,
      note_text TEXT NOT NULL,
      source_model TEXT NOT NULL,
      embedding VECTOR(1536) NOT NULL,
      version INTEGER NOT NULL DEFAULT 1,
      helpful_count INTEGER NOT NULL DEFAULT 0,
      unhelpful_count INTEGER NOT NULL DEFAULT 0,
      suppress_count INTEGER NOT NULL DEFAULT 0,
      match_count INTEGER NOT NULL DEFAULT 0,
      last_matched_at TIMESTAMPTZ,
      metadata JSONB NOT NULL DEFAULT '{}'::JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )
    """)

    op.execute("CREATE INDEX IF NOT EXISTS trajectories_processed_created_idx ON trajectories (processed, created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS trajectories_cdc_hash_idx ON trajectories (cdc_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS trajectories_fingerprint_idx ON trajectories (trajectory_fingerprint)")
    op.execute("CREATE INDEX IF NOT EXISTS trajectory_nodes_trajectory_idx ON trajectory_nodes (trajectory_id, node_type)")
    op.execute("CREATE INDEX IF NOT EXISTS trajectory_edges_trajectory_idx ON trajectory_edges (trajectory_id)")
    op.execute("CREATE INDEX IF NOT EXISTS trajectory_notes_embedding_hnsw_idx ON trajectory_notes USING hnsw (embedding vector_cosine_ops)")

    op.execute("""
    CREATE OR REPLACE FUNCTION set_updated_at()
    RETURNS TRIGGER LANGUAGE plpgsql AS $$
    BEGIN
      NEW.updated_at = NOW() AT TIME ZONE 'utc';
      RETURN NEW;
    END;
    $$
    """)

    op.execute("""
    DO $$ BEGIN
      CREATE TRIGGER trajectories_set_updated_at
        BEFORE UPDATE ON trajectories
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$
    """)

    op.execute("""
    DO $$ BEGIN
      CREATE TRIGGER trajectory_notes_set_updated_at
        BEFORE UPDATE ON trajectory_notes
        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
    EXCEPTION WHEN duplicate_object THEN NULL;
    END $$
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS trajectory_edges CASCADE")
    op.execute("DROP TABLE IF EXISTS trajectory_nodes CASCADE")
    op.execute("DROP TABLE IF EXISTS trajectory_notes CASCADE")
    op.execute("DROP TABLE IF EXISTS trajectories CASCADE")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
