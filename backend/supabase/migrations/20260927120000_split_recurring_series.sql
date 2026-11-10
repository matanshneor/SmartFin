/* "Update from now on" rewrote the series' first month.
 *
 * You edit September's rent to ₪5,500 and answer "yes" to "also update the
 * template so the following months use the new amount?". The app updated the
 * template row, but the template **is** January's rent, and it is counted in
 * January's summary. January silently became ₪5,500, along with the
 * description and category. The docstring promised "history isn't rewritten".
 *
 * The fix is not to touch the template but to split the series at the edited
 * occurrence:
 *   · The old series ends the day before it: exactly what "this and all
 *     following" does (`delete_occurrences_from`), so history stays a
 *     recognised series.
 *   · The edited occurrence (which already carries the new amount, because
 *     the edit route saved it a moment earlier) becomes the template of a new
 *     series. The same "the successor becomes the template" as in
 *     `delete_recurring_occurrence`.
 *   · Later occurrences already created move to the new series without their
 *     amount changing: they are history. Without the move, the new series
 *     wouldn't see them and would create them again: duplicates.
 *   · Skips (`recurring_skips`) are split by date.
 *
 * All in one function: four writes from the app would, on a failure midway,
 * leave two live series producing the same money.
 *
 * Returns the new template's id, or `null` when there is nothing to split: the
 * occurrence doesn't exist, doesn't belong to this template, isn't the
 * caller's family's, or the series already ended before it.
 */
create or replace function public.split_recurring_series(
    p_template_id uuid,
    p_instance_id uuid,
    p_family_id   uuid
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    v_template public.transactions%rowtype;
    v_instance public.transactions%rowtype;
begin
    -- `security definer` bypasses RLS, so ownership is checked here,
    -- explicitly, against the signed-in user and not the parameter.

    if p_family_id is distinct from public.get_my_family_id() then
        return null;
    end if;

    select * into v_template
    from public.transactions
    where id = p_template_id
      and family_id = p_family_id
      and is_recurring
    for update;
    if not found then
        return null;
    end if;

    select * into v_instance
    from public.transactions
    where id = p_instance_id
      and family_id = p_family_id
      and recurring_parent_id = p_template_id
      and not is_recurring
    for update;
    if not found then
        return null;
    end if;

    -- An occurrence is always created **after** the template, and only before
    -- the end. Anything else is broken data, and splitting it would violate the
    -- CHECK on the end date.

    if v_instance.date <= v_template.date
       or (v_template.recurring_end_date is not null
           and v_instance.date > v_template.recurring_end_date) then
        return null;
    end if;

    -- 1. The occurrence becomes the template and takes the series definition
    --    from it, and the skips from its date on.

    update public.transactions
       set is_recurring        = true,
           recurring_parent_id = null,
           recurring_frequency = v_template.recurring_frequency,
           recurring_end_date  = v_template.recurring_end_date,
           recurring_skips     = (
               select array_agg(d order by d)
               from unnest(coalesce(v_template.recurring_skips, '{}'::date[])) as d
               where d >= v_instance.date
           )
     where id = v_instance.id;

    -- 2. The later occurrences move to it.

    update public.transactions
       set recurring_parent_id = v_instance.id
     where recurring_parent_id = v_template.id
       and family_id = p_family_id
       and date > v_instance.date;

    -- 3. The old series ends the day before, and keeps only its own skips.

    update public.transactions
       set recurring_end_date = v_instance.date - 1,
           recurring_skips    = (
               select array_agg(d order by d)
               from unnest(coalesce(v_template.recurring_skips, '{}'::date[])) as d
               where d < v_instance.date
           )
     where id = v_template.id;

    return v_instance.id;
end;
$$;

revoke all     on function public.split_recurring_series(uuid, uuid, uuid) from public, anon;
grant  execute on function public.split_recurring_series(uuid, uuid, uuid) to authenticated;
