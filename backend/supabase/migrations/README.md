# Migrations

**Current state (16 November 2026): the ledger matches.** Every version on the
server has a file here and every file is on the server; `supabase db push
--dry-run` reports "Remote database is up to date". Two kinds of drift were
fixed: seven files had been renamed after they were applied (the file now carries
the version the server recorded), and eight migrations applied from other
sessions (the old admin console's functions) had no file: they were restored
verbatim from the `statements` column of the history table.

The ledger had drifted once before, until 20 September 2026: the server knew only 5 of
the 34 migrations, and two early records (`20260527172248`, `20260527172300`) were
registered on the server with no local file: they were created through the
dashboard before the CLI came into use. That made `db push` dangerous: it would try
to replay every migration from May onwards on a database that already had
everything. The two orphan records were restored as real files from the
`statements` column of the history table, instead of being deleted.

## How to add a migration

1. Write the file here, named `<YYYYMMDDHHMMSS>_<description>.sql`.
2. **A dry run against the real data**: this isn't optional:

   ```
   supabase db query "begin; $(cat supabase/migrations/<file>.sql) rollback;" --linked
   ```

   `supabase db query` honours `rollback`, so a whole migration can be run on the
   production data to see what it does without changing anything. To test RLS or
   `SECURITY DEFINER` functions, impersonate an authenticated user inside the same
   transaction:

   ```sql
   select set_config('request.jwt.claims', json_build_object('sub','<uuid>')::text, true);
   set local role authenticated;
   ```

3. Only then `supabase db push --linked` (or `--dry-run` first).

## What not to do

`supabase migration repair --status reverted` **deletes** a row from the history
ledger. The CLI offers it when there is a record on the server with no local file:
don't do it before extracting its SQL from the `statements` column and saving it as
a file. That is the only record of that change.
