-- Tapping a transaction in the "this week" card opens the edit sheet, as it
-- does everywhere else (Matan, 22.11): the week rows now carry the id and the
-- recurring fields the sheet needs. Built from
-- 20261121221505_week_rows_installments.sql (the live definition); only the
-- week_rows block changed.

CREATE OR REPLACE FUNCTION public.get_home_page(p_family_id uuid, p_year integer, p_month integer, p_recent_from date, p_recent_limit integer, p_week_from date, p_week_start date, p_week_to date)
 RETURNS jsonb
 LANGUAGE sql
 STABLE SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
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
        'categories', coalesce((
            select jsonb_agg(to_jsonb(c) order by c.sort_order nulls last, c.name)
            from categories c
            where c.family_id is null or c.family_id = p_family_id
        ), '[]'::jsonb),
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
        'week_rows', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'id',                  t.id,
                       'amount',              t.amount,
                       'date',                t.date,
                       'description',         t.description,
                       'category_id',         t.category_id,
                       'is_recurring',        t.is_recurring,
                       'recurring_parent_id', t.recurring_parent_id,
                       'recurring_frequency', t.recurring_frequency,
                       'recurring_end_date',  t.recurring_end_date,
                       'installments',        t.installments,
                       'installment_no',      t.installment_no,
                       'project_id',          t.project_id,
                       'user_id',             t.user_id,
                       'categories', case when c.id is null then null
                           else jsonb_build_object('name', c.name, 'icon', c.icon) end,
                       'profiles', case when pr.id is null then null
                           else jsonb_build_object('name', pr.name) end
                   ))
            from transactions t
            left join categories c on c.id = t.category_id
            left join profiles  pr on pr.id = t.user_id
            where t.family_id = p_family_id
              and t.type = 'expense'
              and t.project_id is null
              and t.date >= p_week_from
              and t.date <= p_week_to
        ), '[]'::jsonb),
        'week_has_older', exists (
            select 1 from transactions t
            where t.family_id = p_family_id
              and t.type = 'expense'
              and t.project_id is null
              and t.is_recurring = false
              and t.recurring_parent_id is null
              and t.date < p_week_start
        ),
        'has_transactions', exists (
            select 1 from transactions t where t.family_id = p_family_id
        )
    )
    where p_family_id = public.get_my_family_id();
$function$;

revoke all     on function public.get_home_page(uuid, int, int, date, int, date, date, date) from public, anon;
grant  execute on function public.get_home_page(uuid, int, int, date, int, date, date, date) to authenticated;
