/* The home page in one trip to the database (Matan, 13.10: "as fast as
 * possible").
 *
 * Until now the home page sent nine questions, one after another (see
 * `_run_queries` in app.py on why in sequence): settings, the month summary,
 * categories, members, "new family?", recent transactions, this week, "is
 * there a previous week?", and the month's rows for alerts. In the logs:
 * `300-400ms` normally. The month page moved to one function (`get_month_page`)
 * and takes `40-90ms`.
 *
 * As there: only the transport moves here. The calculations (summary, this
 * week, alerts, transaction formatting) stay in Python, in exactly the same
 * functions, so there aren't two sources of truth. Every part here mimics the
 * query it replaces, including `null` and not `{}` when there is no linked row,
 * because the templates check exactly that.
 *
 * The dates are computed in Python and sent (`clock.today()`, Israel time), not
 * the database's `current_date`, which runs in UTC: between midnight and 3am
 * there were two different "todays".
 *
 * SECURITY DEFINER with the same check as `get_month_page`:
 * `get_my_family_id()`. Whoever asks for a family they aren't a member of gets
 * `null`.
 */

create or replace function public.get_home_page(
    p_family_id     uuid,
    p_year          int,
    p_month         int,
    p_recent_from   date,   -- start of the month: transactions from a recurring series only from here
-- (`_recent_series_filter`)
    p_recent_limit  int,
    p_week_from     date,   -- Sunday of the previous week
    p_week_start    date,   -- Sunday of the shown week
    p_week_to       date    -- Saturday of the shown week
)
returns jsonb
language sql
stable
security definer
set search_path = public
as $function$
    select jsonb_build_object(

        'family', (
            select to_jsonb(f) from families f where f.id = p_family_id
        ),

        -- Same as `get_month_page` / `get_family_members(uuid)`, including the order

        'members', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'id',             p.id,
                       'name',           p.name,
                       'avatar_initial', p.avatar_initial,
                       'email',          u.email::text,
                       'phone',          p.phone,
                       'workplace',      p.workplace
                   ) order by p.created_at)
            from profiles p
            join auth.users u on u.id = p.id
            where p.family_id = p_family_id
        ), '[]'::jsonb),

        -- Same as `_fetch_categories`: `sort_order` (empty ones last), then name

        'categories', coalesce((
            select jsonb_agg(to_jsonb(c) order by c.sort_order nulls last, c.name)
            from categories c
            where c.family_id is null or c.family_id = p_family_id
        ), '[]'::jsonb),

        -- Same as `fetch_month_rows`: all the month's rows, including projects.
        -- Both the month summary (without projects) and the budget alerts are
        -- derived from it.

        'rows', coalesce((
            select jsonb_agg(
                       to_jsonb(t) || jsonb_build_object(
                           'categories', case when c.id is null then null
                               else jsonb_build_object('name', c.name, 'icon', c.icon) end,
                           'project_categories', case when pc.id is null then null
                               else jsonb_build_object('name', pc.name, 'icon', pc.icon) end,
                           'profiles', case when pr.id is null then null
                               else jsonb_build_object('name', pr.name, 'workplace', pr.workplace) end,
                           'projects', case when pj.id is null then null
                               else jsonb_build_object('owner_id', pj.owner_id,
                                                       'name', pj.name, 'icon', pj.icon) end
                       ) order by t.date desc
                   )
            from transactions t
            left join categories         c  on c.id  = t.category_id
            left join project_categories pc on pc.id = t.project_category_id
            left join profiles           pr on pr.id = t.user_id
            left join projects           pj on pj.id = t.project_id
            where t.family_id = p_family_id
              and t.date >= make_date(p_year, p_month, 1)
              and t.date <  make_date(p_year, p_month, 1) + interval '1 month'
        ), '[]'::jsonb),

        -- Same as `get_recent_transactions`: no projects, in entry order, and
        -- transactions from a recurring series only from the start of the month

        'recent', coalesce((
            select jsonb_agg(r.j order by r.created_at desc)
            from (
                select t.created_at,
                       to_jsonb(t) || jsonb_build_object(
                           'categories', case when c.id is null then null
                               else jsonb_build_object('name', c.name, 'icon', c.icon) end,
                           'project_categories', case when pc.id is null then null
                               else jsonb_build_object('name', pc.name, 'icon', pc.icon) end,
                           'profiles', case when pr.id is null then null
                               else jsonb_build_object('name', pr.name, 'workplace', pr.workplace) end,
                           'projects', null
                       ) as j
                from transactions t
                left join categories         c  on c.id  = t.category_id
                left join project_categories pc on pc.id = t.project_category_id
                left join profiles           pr on pr.id = t.user_id
                where t.family_id = p_family_id
                  and t.project_id is null
                  and ((t.is_recurring is not true and t.recurring_parent_id is null)
                       or t.date >= p_recent_from)
                order by t.created_at desc
                limit p_recent_limit
            ) r
        ), '[]'::jsonb),

        -- Same as `week_spending`: household expenses of the previous and
        -- current week

        'week_rows', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'amount',              t.amount,
                       'date',                t.date,
                       'description',         t.description,
                       'category_id',         t.category_id,
                       'is_recurring',        t.is_recurring,
                       'recurring_parent_id', t.recurring_parent_id,
                       'project_id',          t.project_id,
                       'categories', case when c.id is null then null
                           else jsonb_build_object('name', c.name, 'icon', c.icon) end
                   ))
            from transactions t
            left join categories c on c.id = t.category_id
            where t.family_id = p_family_id
              and t.type = 'expense'
              and t.project_id is null
              and t.date >= p_week_from
              and t.date <= p_week_to
        ), '[]'::jsonb),

        -- "Is there anywhere to page back to": `is_recurring = false` exactly
        -- like `.eq(False)` there (a row with `NULL` isn't counted)

        'week_has_older', exists (
            select 1 from transactions t
            where t.family_id = p_family_id
              and t.type = 'expense'
              and t.project_id is null
              and t.is_recurring = false
              and t.recurring_parent_id is null
              and t.date < p_week_start
        ),

        -- "New family": no transaction ever, including projects

        'has_transactions', exists (
            select 1 from transactions t where t.family_id = p_family_id
        )
    )
    where p_family_id = public.get_my_family_id();
$function$;

revoke all     on function public.get_home_page(uuid, int, int, date, int, date, date, date) from public, anon;
grant  execute on function public.get_home_page(uuid, int, int, date, int, date, date, date) to authenticated;
