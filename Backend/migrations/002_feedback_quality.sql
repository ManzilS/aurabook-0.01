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

grant execute on function public.match_notes(text, float, integer) to service_role, authenticated;

notify pgrst, 'reload schema';
