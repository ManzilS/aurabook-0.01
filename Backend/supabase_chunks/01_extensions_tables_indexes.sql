create schema if not exists extensions;
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
  version integer not null default 1,
  helpful_count integer not null default 0,
  unhelpful_count integer not null default 0,
  suppress_count integer not null default 0,
  match_count integer not null default 0,
  last_matched_at timestamptz,
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
