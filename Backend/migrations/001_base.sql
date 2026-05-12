create extension if not exists vector with schema extensions;
create extension if not exists pgcrypto with schema extensions;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = timezone('utc', now());
  return new;
end;
$$;

create table if not exists public.trajectories (
  id uuid primary key default gen_random_uuid(),
  source text not null default 'local_client',
  client_id text,
  cdc_hash text unique,
  trajectory_fingerprint text not null unique,
  original_goal text,
  original_prompt text not null,
  broken_code text,
  error_log text not null,
  ai_reflection text,
  final_fix text not null,
  history jsonb not null default '[]'::jsonb,
  metadata jsonb not null default '{}'::jsonb,
  processed boolean not null default false,
  note_model text,
  note_error text,
  processed_at timestamptz,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.trajectory_nodes (
  id uuid primary key default gen_random_uuid(),
  trajectory_id uuid not null references public.trajectories(id) on delete cascade,
  node_type text not null check (node_type in ('prompt', 'attempt', 'error', 'reflection', 'fix')),
  content text not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.trajectory_edges (
  id uuid primary key default gen_random_uuid(),
  trajectory_id uuid not null references public.trajectories(id) on delete cascade,
  source_node_id uuid not null references public.trajectory_nodes(id) on delete cascade,
  target_node_id uuid not null references public.trajectory_nodes(id) on delete cascade,
  edge_type text not null,
  created_at timestamptz not null default timezone('utc', now())
);

create table if not exists public.trajectory_notes (
  id uuid primary key default gen_random_uuid(),
  trajectory_id uuid not null unique references public.trajectories(id) on delete cascade,
  note_text text not null,
  source_model text not null,
  embedding extensions.vector(1536) not null,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default timezone('utc', now()),
  updated_at timestamptz not null default timezone('utc', now())
);

create index if not exists trajectories_processed_created_idx
  on public.trajectories (processed, created_at);

create index if not exists trajectories_cdc_hash_idx
  on public.trajectories (cdc_hash);

create index if not exists trajectories_fingerprint_idx
  on public.trajectories (trajectory_fingerprint);

create index if not exists trajectory_nodes_trajectory_idx
  on public.trajectory_nodes (trajectory_id, node_type);

create index if not exists trajectory_edges_trajectory_idx
  on public.trajectory_edges (trajectory_id);

drop index if exists public.trajectory_notes_embedding_idx;
create index if not exists trajectory_notes_embedding_hnsw_idx
  on public.trajectory_notes
  using hnsw (embedding extensions.vector_cosine_ops);

drop trigger if exists trajectories_set_updated_at on public.trajectories;
create trigger trajectories_set_updated_at
before update on public.trajectories
for each row
execute function public.set_updated_at();

drop trigger if exists trajectory_notes_set_updated_at on public.trajectory_notes;
create trigger trajectory_notes_set_updated_at
before update on public.trajectory_notes
for each row
execute function public.set_updated_at();

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
  ai_reflection text
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
    t.ai_reflection
  from query_input as q
  join public.trajectory_notes as n on true
  join public.trajectories as t on t.id = n.trajectory_id
  where 1 - (n.embedding <=> q.embedding) >= match_threshold
  order by n.embedding <=> q.embedding
  limit result_limit;
end;
$$;

alter table public.trajectories enable row level security;
alter table public.trajectory_nodes enable row level security;
alter table public.trajectory_edges enable row level security;
alter table public.trajectory_notes enable row level security;

create policy "service role full access trajectories"
on public.trajectories
for all
to service_role
using (true)
with check (true);

create policy "service role full access trajectory nodes"
on public.trajectory_nodes
for all
to service_role
using (true)
with check (true);

create policy "service role full access trajectory edges"
on public.trajectory_edges
for all
to service_role
using (true)
with check (true);

create policy "service role full access trajectory notes"
on public.trajectory_notes
for all
to service_role
using (true)
with check (true);

create policy "authenticated can read notes"
on public.trajectory_notes
for select
to authenticated
using (true);

grant execute on function public.match_notes(text, float, integer) to service_role, authenticated;
