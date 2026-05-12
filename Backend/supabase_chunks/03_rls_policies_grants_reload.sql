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

notify pgrst, 'reload schema';
