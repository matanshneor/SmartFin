/* The projects and comparison pages in one trip to the database each (Matan,
 * 13.10).
 *
 * Siblings of `get_month_page`, `get_home_page` and `get_settings_page`, with
 * the same rules: the transport here, the calculations stay in Python in the
 * same functions, and every part mimics the query it replaces.
 *
 * Projects: the active ones + totals, "recent activity", and again the closed
 * ones + totals: five queries in sequence, `270-590ms` in the logs.
 * Comparison: the archive, categories, family members, and two scans of the
 * same expenses (by category and by family member): `370-440ms`.
 *
 * SECURITY DEFINER with the `get_my_family_id()` check. Another family
 * member's personal project is filtered in Python, as in `get_projects`: here
 * the privacy is within the family, and RLS doesn't distinguish it anyway.
 */

create or replace function public.get_projects_page(p_family_id uuid)
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

        -- All projects, active and closed, newest first, like `get_projects`

        'projects', coalesce((
            select jsonb_agg(to_jsonb(p) order by p.created_at desc)
            from projects p
            where p.family_id = p_family_id
        ), '[]'::jsonb),

        -- Same as `project_totals(uuid)`

        'totals', coalesce((
            select jsonb_object_agg(x.project_id, jsonb_build_object(
                       'expense', x.expense, 'income', x.income, 'savings', x.savings))
            from (
                select t.project_id::text as project_id,
                       coalesce(sum(t.amount) filter (where t.type = 'expense'), 0) as expense,
                       coalesce(sum(t.amount) filter (where t.type = 'income'),  0) as income,
                       coalesce(sum(t.amount) filter (where t.type = 'savings'), 0) as savings
                from transactions t
                where t.family_id = p_family_id and t.project_id is not null
                group by t.project_id
            ) x
        ), '{}'::jsonb),

        -- Same as `project_last_activity`: the date of the latest transaction
        -- in each project

        'last_activity', coalesce((
            select jsonb_object_agg(x.project_id, x.last_date)
            from (
                select t.project_id::text as project_id, max(t.date) as last_date
                from transactions t
                where t.family_id = p_family_id and t.project_id is not null
                group by t.project_id
            ) x
        ), '{}'::jsonb)
    )
    where p_family_id = public.get_my_family_id();
$function$;

revoke all     on function public.get_projects_page(uuid) from public, anon;
grant  execute on function public.get_projects_page(uuid) to authenticated;


create or replace function public.get_months_page(
    p_family_id uuid,
    p_from      date,   -- start of the first month in the 12-month window
    p_to        date    -- exclusive: the start of the month after the current one
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

        -- Same as `_fetch_categories`

        'categories', coalesce((
            select jsonb_agg(to_jsonb(c) order by c.sort_order nulls last, c.name)
            from categories c
            where c.family_id is null or c.family_id = p_family_id
        ), '[]'::jsonb),

        -- Same as `get_months_archive(uuid)` and the `archive` part of
        -- `get_month_page`

        'archive', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'year',    a.year,    'month',   a.month,
                       'income',  a.income,  'expense', a.expense,
                       'savings', a.savings, 'balance', a.balance
                   ) order by a.year desc, a.month desc)
            from (
                select extract(year  from date)::int as year,
                       extract(month from date)::int as month,
                       coalesce(sum(amount) filter (where type = 'income'),  0) as income,
                       coalesce(sum(amount) filter (where type = 'expense'), 0) as expense,
                       coalesce(sum(amount) filter (where type = 'savings'), 0) as savings,
                       coalesce(sum(amount) filter (where type = 'income'),  0)
                           - coalesce(sum(amount) filter (where type = 'expense'), 0) as balance
                from transactions
                where family_id = p_family_id
                  and project_id is null
                group by 1, 2
            ) a
        ), '[]'::jsonb),

        -- The window's expenses, once, for `category_trend` and `member_trend` together

        'expense_rows', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'id',          t.id,
                       'amount',      t.amount,
                       'category_id', t.category_id,
                       'date',        t.date,
                       'description', t.description,
                       'user_id',     t.user_id,
                       'profiles', case when pr.id is null then null
                           else jsonb_build_object('name', pr.name) end
                   ) order by t.id)
            from transactions t
            left join profiles pr on pr.id = t.user_id
            where t.family_id = p_family_id
              and t.type = 'expense'
              and t.project_id is null
              and t.date >= p_from
              and t.date <  p_to
        ), '[]'::jsonb)
    )
    where p_family_id = public.get_my_family_id();
$function$;

revoke all     on function public.get_months_page(uuid, date, date) from public, anon;
grant  execute on function public.get_months_page(uuid, date, date) to authenticated;
