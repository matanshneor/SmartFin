/* The guard at the door (Matan, 10.11.2026, security review, item 3).
 *
 * Supabase's default gives anon and authenticated everything on every table,
 * including TRUNCATE, which isn't subject to RLS at all. All protection relied
 * on RLS alone. RLS still locks every row; this is a second layer, so one RLS
 * rule written wrong in the future isn't enough to expose data.
 *
 *   · anon (not signed in) needs no table: sign-in, signup and the invite
 *     link go through SECURITY DEFINER functions. (Reading families stayed here
 *     for the health check, and was closed in the next migration, after the
 *     check moved to a function.)
 *   · authenticated keeps SELECT/INSERT/UPDATE/DELETE (and RLS restricts
 *     them); it loses only TRUNCATE, TRIGGER and REFERENCES, which the app
 *     doesn't use.
 *   · The default for new tables: the same, so this doesn't come back by itself.
 *
 * Tested in tests/test_table_grants.py.
 */
revoke all on all tables in schema public from anon;
revoke all on all sequences in schema public from anon;
grant select on public.families to anon;
revoke truncate, trigger, references on all tables in schema public from authenticated;
alter default privileges for role postgres in schema public revoke all on tables from anon;
alter default privileges for role postgres in schema public revoke all on sequences from anon;
alter default privileges for role postgres in schema public revoke truncate, trigger, references on tables from authenticated;
