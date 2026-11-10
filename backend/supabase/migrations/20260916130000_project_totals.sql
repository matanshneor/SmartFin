/* Project totals, computed in the database instead of in Python.
 *
 * `_project_totals` pulled *every* project transaction of the family, ever, on
 * every load of the settings and projects pages, and summed them in Python. At
 * tens of rows that is instant; after a few years of trips and renovations it
 * is a scan that grows without limit, on a page that loads a lot.
 *
 * `security invoker` and not definer, on purpose: RLS still applies, so the
 * function sees exactly what the caller may see. There is no reason at all to
 * bypass it here.
 */
create or replace function public.project_totals(p_family_id uuid)
returns table(project_id uuid, expense numeric, income numeric, savings numeric)
language sql
stable
security invoker
set search_path = public
as $$
    select t.project_id,
           coalesce(sum(t.amount) filter (where t.type = 'expense'), 0),
           coalesce(sum(t.amount) filter (where t.type = 'income'),  0),
           coalesce(sum(t.amount) filter (where t.type = 'savings'), 0)
    from transactions t
    where t.family_id = p_family_id and t.project_id is not null
    group by t.project_id;
$$;

revoke all     on function public.project_totals(uuid) from public, anon;
grant  execute on function public.project_totals(uuid) to authenticated;
