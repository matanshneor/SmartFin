/* Deleting a single occurrence from a recurring series, without destroying the
 * series.
 *
 * "Only this one" on the first occurrence deleted the template row itself.
 * `recurring_parent_id` is `on delete set null`, so every other occurrence lost
 * its link at once: the series stopped producing new occurrences, and
 * everything already created vanished from "recurring transactions" and from
 * the panel on the month page. The UI, at that same moment, announced "the rest
 * of the series was kept".
 *
 * The distinction between "template" and "occurrence" is entirely internal.
 * Whoever deletes March's rent shouldn't have to care that March happens to be
 * the month the series was opened, so deleting the template hands the role to
 * the next occurrence instead of killing the series.
 *
 * All in one function and not three calls from the app, because this is also
 * where `recurring_skips` was read-modify-write: two family members deleting two
 * occurrences in the same second lost one skip, and the transaction came back
 * the next day. Here it is one atomic update.
 */
create or replace function public.delete_recurring_occurrence(
    p_tx_id     uuid,
    p_family_id uuid
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_row       public.transactions%rowtype;
    v_template  uuid;
    v_heir      public.transactions%rowtype;
    v_deleted   integer;
begin
    -- Access derives from the signed-in user, not the parameter: `security
    -- definer` bypasses RLS, so the check must be here, explicitly.

    select * into v_row
    from public.transactions
    where id = p_tx_id
      and family_id = p_family_id
      and family_id = (select family_id from public.profiles where id = auth.uid());

    if not found then
        return 0;
    end if;

    v_template := case when v_row.is_recurring then v_row.id
                       else v_row.recurring_parent_id end;

    if v_template is null then
        delete from public.transactions where id = p_tx_id;
        return 1;
    end if;

    -- The skip is recorded on the template before any deletion, so the engine
    -- doesn't recreate this date tomorrow.

    update public.transactions
       set recurring_skips = (
           select array_agg(distinct d)
           from unnest(coalesce(recurring_skips, '{}'::date[]) || v_row.date) as d
       )
     where id = v_template;

    -- A regular occurrence: delete and finish.

    if p_tx_id <> v_template then
        delete from public.transactions where id = p_tx_id;
        get diagnostics v_deleted = row_count;
        return v_deleted;
    end if;

    -- From here: deleting the **template**. The successor is the earliest
    -- remaining occurrence.

    select * into v_heir
    from public.transactions
    where recurring_parent_id = v_template
      and family_id = p_family_id
    order by date, created_at
    limit 1;

    if found then
        -- The successor becomes the template and carries everything that
        -- defined the series.

        update public.transactions
           set is_recurring        = true,
               recurring_frequency = v_row.recurring_frequency,
               recurring_end_date  = v_row.recurring_end_date,
               recurring_skips     = (
                   select array_agg(distinct d)
                   from unnest(coalesce((select recurring_skips from public.transactions
                                          where id = v_template), '{}'::date[])) as d
               ),
               recurring_parent_id = null
         where id = v_heir.id;

        -- And the remaining siblings point at it. Without this they would be
        -- orphaned the moment the old template is deleted, and that is the
        -- whole bug.

        update public.transactions
           set recurring_parent_id = v_heir.id
         where recurring_parent_id = v_template
           and id <> v_heir.id
           and family_id = p_family_id;
    end if;

    delete from public.transactions where id = v_template;
    get diagnostics v_deleted = row_count;
    return v_deleted;
end;
$$;

revoke all on function public.delete_recurring_occurrence(uuid, uuid) from public, anon;
grant execute on function public.delete_recurring_occurrence(uuid, uuid) to authenticated;
