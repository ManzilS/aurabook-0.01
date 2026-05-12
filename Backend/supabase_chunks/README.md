# Supabase Schema Chunks

Run these files in the Supabase SQL editor in numeric order:

1. `00_reset_incompatible_schema.sql`
2. `01_extensions_tables_indexes.sql`
3. `02_triggers_match_notes_function.sql`
4. `03_rls_policies_grants_reload.sql`

Each SQL file is under 100 lines and keeps SQL statements intact.

Use `00_reset_incompatible_schema.sql` only when you want to clear the current incompatible schema first.
