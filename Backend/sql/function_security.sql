-- Applied through Supabase migration harden_function_execution_context.
alter function public.set_updated_at() set search_path = pg_catalog;
alter function public.match_notes(text, double precision, integer) set search_path = pg_catalog, extensions;
revoke execute on function public.rls_auto_enable() from public, anon, authenticated;
notify pgrst, 'reload schema';
