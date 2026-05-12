# AuraBook Cloud Migrations

Use this when provisioning a fresh Supabase project or repairing a partial migration.

## Fresh Project

Run one of these options in the Supabase SQL editor:

1. Preferred: run [`supabase.sql`](/E:/Codex/AIBook/aurabook-cloud/supabase.sql)
2. Split migration path:
   - [`migrations/001_base.sql`](/E:/Codex/AIBook/aurabook-cloud/migrations/001_base.sql)
   - [`migrations/002_feedback_quality.sql`](/E:/Codex/AIBook/aurabook-cloud/migrations/002_feedback_quality.sql)

## Important Migration Notes

- `public.match_notes` must be dropped and recreated when its return columns change.
- `public.match_notes` should accept `text` for `query_embedding`.
  The cloud app serializes embeddings to a pgvector literal string before calling the RPC.
- `ivfflat` can miss good matches on small or changing datasets.
  Fresh schema now uses an `hnsw` index, and `match_notes` forces `ivfflat.probes = 100`
  so existing projects with the older index still return stable results.
- The function input parameter is `result_limit`, not `match_count`.
  This avoids a Postgres conflict with the returned `match_count` column.
- The scripts end with `notify pgrst, 'reload schema';` so PostgREST sees the new schema immediately.

## Recovery For A Partial Migration

If `001` succeeded but `002` failed, run this block:

```sql
alter table public.trajectory_notes
  add column if not exists version integer not null default 1,
  add column if not exists helpful_count integer not null default 0,
  add column if not exists unhelpful_count integer not null default 0,
  add column if not exists suppress_count integer not null default 0,
  add column if not exists match_count integer not null default 0,
  add column if not exists last_matched_at timestamptz;

drop function if exists public.match_notes(extensions.vector, float, integer);
drop function if exists public.match_notes(jsonb, float, integer);
drop function if exists public.match_notes(text, float, integer);

create function public.match_notes(
  query_embedding text,
  match_threshold float,
  result_limit integer
)
returns table (
  note_id uuid,
  trajectory_id uuid,
  note_text text,
  source_model text,
  metadata jsonb,
  similarity float,
  created_at timestamptz,
  original_prompt text,
  final_fix text,
  ai_reflection text,
  version integer,
  helpful_count integer,
  unhelpful_count integer,
  suppress_count integer,
  match_count integer,
  last_matched_at timestamptz
)
language plpgsql
stable
as $$
begin
  perform set_config('ivfflat.probes', '100', true);

  return query
  with query_input as (
    select query_embedding::extensions.vector(1536) as embedding
  )
  select
    n.id as note_id,
    n.trajectory_id,
    n.note_text,
    n.source_model,
    n.metadata,
    1 - (n.embedding <=> q.embedding) as similarity,
    n.created_at,
    t.original_prompt,
    t.final_fix,
    t.ai_reflection,
    n.version,
    n.helpful_count,
    n.unhelpful_count,
    n.suppress_count,
    n.match_count,
    n.last_matched_at
  from query_input as q
  join public.trajectory_notes as n on true
  join public.trajectories as t on t.id = n.trajectory_id
  where 1 - (n.embedding <=> q.embedding) >= match_threshold
  order by n.embedding <=> q.embedding
  limit result_limit;
end;
$$;

grant execute on function public.match_notes(text, float, integer)
to service_role, authenticated;

notify pgrst, 'reload schema';
```

## Verification Queries

Verify the core tables exist:

```sql
select table_name
from information_schema.tables
where table_schema = 'public'
  and table_name in ('trajectories', 'trajectory_nodes', 'trajectory_edges', 'trajectory_notes')
order by table_name;
```

Verify the note quality columns exist:

```sql
select column_name
from information_schema.columns
where table_schema = 'public'
  and table_name = 'trajectory_notes'
order by ordinal_position;
```

Verify the search RPC exists:

```sql
select routine_name
from information_schema.routines
where routine_schema = 'public'
  and routine_name = 'match_notes';
```

Verify the RPC can self-match a stored note embedding:

```sql
select *
from public.match_notes(
  (
    select embedding::text
    from public.trajectory_notes
    order by created_at desc
    limit 1
  ),
  0.0,
  3
);
```

For existing projects created before this change, also replace the older IVFFlat index:

```sql
drop index if exists public.trajectory_notes_embedding_idx;
create index if not exists trajectory_notes_embedding_hnsw_idx
  on public.trajectory_notes
  using hnsw (embedding extensions.vector_cosine_ops);
```
