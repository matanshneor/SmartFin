/* Family membership is set only through functions, not by direct writes.
 *
 * Background: profiles_own was defined FOR ALL USING (auth.uid() = id) with no
 * WITH CHECK. On UPDATE, Postgres also uses USING as the check on the new row,
 * i.e. the only condition on the row being written is that it is mine.
 * family_id wasn't restricted at all.
 *
 * I proved it against the real data (inside a rolled-back transaction): an
 * authenticated user runs one UPDATE on their own row, points it at another
 * family's id, and from that moment every policy in the other tables gives
 * them full access, because they are all derived from that column.
 * families_insert was WITH CHECK (true), so arbitrary families could be
 * created too.
 *
 * This wasn't exploitable through the app: no Flask route accepts a family_id
 * from the user, and direct access to PostgREST requires the anon key, which
 * is kept on the server only. But RLS is supposed to be the layer that catches
 * a future bug in the code, not a copy of the same check.
 *
 * The fix follows the pattern already here: join_family_by_code is a SECURITY
 * DEFINER derived from auth.uid(). Now creating a family works the same way,
 * and direct writes to the column are blocked at the grant level, not at the
 * policy level, because WITH CHECK can't compare to the row's previous value.
 */

-- ─── Creating a family and attaching to it, in one controlled step ──────────

create or replace function public.create_own_family(p_name text default 'המשפחה שלי')
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid      uuid := auth.uid();
    v_existing uuid;
    v_id       uuid;
begin
    if v_uid is null then
        raise exception 'not authenticated';
    end if;

    -- Someone who already has a family doesn't get a new one, and the existing
    -- one isn't replaced. That is what prevents using the function to move
    -- between families.

    select family_id into v_existing from public.profiles where id = v_uid;
    if v_existing is not null then
        return v_existing;
    end if;

    v_id := gen_random_uuid();
    insert into public.families (id, name)
        values (v_id, coalesce(nullif(btrim(p_name), ''), 'המשפחה שלי'));
    update public.profiles set family_id = v_id where id = v_uid;
    return v_id;
end;
$$;

revoke all     on function public.create_own_family(text) from public, anon;
grant  execute on function public.create_own_family(text) to authenticated;

-- ─── No more direct family creation ─────────────────────────────────────────

drop policy if exists "families_insert" on public.families;
revoke insert on public.families from authenticated, anon;

-- ─── No more direct membership changes ──────────────────────────────────────
-- A table-level UPDATE grant covers every column, so it is removed and only the
-- columns the user really edits in profile settings are granted back.

revoke update on public.profiles from authenticated, anon;
grant  update (name, avatar_initial, phone, workplace) on public.profiles to authenticated;

-- ─── Tightening the policy itself, for defence in depth ─────────────────────

alter policy "profiles_own" on public.profiles with check (auth.uid() = id);
