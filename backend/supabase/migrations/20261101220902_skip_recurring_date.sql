/* Skipping a future month in a recurring series (Matan, 1.11).
 *
 * A future month shows the projected occurrences, and tapping one offers "skip
 * this month". The occurrence doesn't exist as a row yet (there is nothing to
 * delete), so the date is recorded in the template's `recurring_skips`, and
 * exactly as with deleting an occurrence, the engine won't create it.
 * `p_skip = false` removes the skip ("undo" in the toast).
 *
 * One atomic update, not read-modify-write from the app: two family members
 * skipping two months in the same second lost one (see
 * `delete_recurring_occurrence`). Future dates only: what was already charged
 * is a real transaction, and is deleted like one.
 */
create or replace function public.skip_recurring_date(
    p_template_id uuid,
    p_family_id   uuid,
    p_date        date,
    p_skip        boolean default true
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_updated integer;
begin
    if p_date <= (now() at time zone 'Asia/Jerusalem')::date then
        return 0;
    end if;

    -- `security definer` bypasses RLS: access derives from the signed-in user,
    -- explicitly

    update public.transactions
       set recurring_skips = case when p_skip then (
               select array_agg(distinct d order by d)
               from unnest(coalesce(recurring_skips, '{}'::date[]) || p_date) as d
           ) else array_remove(coalesce(recurring_skips, '{}'::date[]), p_date) end
     where id = p_template_id
       and is_recurring
       and family_id = p_family_id
       and family_id = (select family_id from public.profiles where id = auth.uid());
    get diagnostics v_updated = row_count;
    return v_updated;
end;
$$;

revoke all on function public.skip_recurring_date(uuid, uuid, date, boolean) from public, anon;
grant execute on function public.skip_recurring_date(uuid, uuid, date, boolean) to authenticated;
