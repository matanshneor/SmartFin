/* `get_my_family_id()` and the policy that relies on it, restored to files on
 * 22.9.2026.
 *
 * Both have existed in the database for a long time and were never written as
 * a migration; they were created through the dashboard. The result is that
 * building the database from scratch from these files **does not produce this
 * database**, in two separate ways:
 *
 * 1. `20260916110000` creates two `language sql` functions that call
 *    `get_my_family_id()`. Postgres validates a SQL function's body at
 *    creation, so that migration simply fails if the function doesn't exist.
 *
 * 2. `profiles_family_read` was created in the files in a **recursive**
 *    version: a SELECT policy on `profiles` that queries `profiles` itself. On
 *    the server it has long been phrased through the function (which is
 *    `security definer` and so doesn't trigger the policy again), but that fix
 *    stayed only there.
 *
 * Hence the date: before `20260916110000`, its first consumer.
 *
 * The definition is identical to the server's, including grants; verified in a
 * rollback against `pg_get_functiondef` and `proacl`. This is a documentation
 * migration, not a change.
 *
 * Note: the grant was also given to `anon`. For anonymous callers `auth.uid()`
 * is `NULL` and the function returns `NULL`, so there is no leak, but the grant
 * is unnecessary. It isn't touched here: `profiles_family_read` applies to
 * `public`, and revoking the grant could break reads the policy allows. Such a
 * change needs its own check.
 */
create or replace function public.get_my_family_id()
returns uuid
language sql
stable
security definer
set search_path to 'public'
as $$
  SELECT family_id FROM profiles WHERE id = auth.uid();
$$;

grant execute on function public.get_my_family_id() to authenticated, anon, service_role;

-- The non-recursive version, as it is on the server

drop policy if exists "profiles_family_read" on public.profiles;
create policy "profiles_family_read" on public.profiles
    for select using (
        family_id is not null
        and family_id = public.get_my_family_id()
    );
