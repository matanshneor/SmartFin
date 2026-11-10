/* The settings page in one trip to the database (Matan, 13.10: "as fast as
 * possible").
 *
 * Until now: categories, the monthly average per category, members, the
 * family, the recurring transactions, two queries for "₪5,000 ← ₪5,500", and
 * the transaction count, one after another, `300-530ms` in the logs. A sibling
 * of `get_month_page` and `get_home_page`, with the same rules: the transport
 * here, the calculations stay in Python in the same functions.
 *
 * (The latest email comes from Supabase's auth service and not the database,
 * so it stays a separate query.)
 *
 * SECURITY DEFINER with the `get_my_family_id()` check: whoever asks for a
 * family they aren't a member of gets `null`.
 */

create or replace function public.get_settings_page(
    p_family_id uuid,
    p_avg_from  date,   -- the last three whole months (`category_monthly_averages`)
    p_avg_to    date    -- exclusive: the start of the current month
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

        -- Same as `_fetch_categories`

        'categories', coalesce((
            select jsonb_agg(to_jsonb(c) order by c.sort_order nulls last, c.name)
            from categories c
            where c.family_id is null or c.family_id = p_family_id
        ), '[]'::jsonb),

        -- The total per category in the window; division and rounding happen in
        -- Python (`category_averages_from_totals`), as in `category_monthly_averages`

        'category_totals', coalesce((
            select jsonb_object_agg(x.category_id, x.total)
            from (
                select t.category_id::text as category_id, sum(t.amount) as total
                from transactions t
                where t.family_id = p_family_id
                  and t.project_id is null
                  and t.category_id is not null
                  and t.date >= p_avg_from
                  and t.date <  p_avg_to
                group by t.category_id
            ) x
        ), '{}'::jsonb),

        -- Same as `get_recurring_transactions`: all templates, largest first

        'recurring', coalesce((
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
                       ) order by t.amount desc
                   )
            from transactions t
            left join categories         c  on c.id  = t.category_id
            left join project_categories pc on pc.id = t.project_category_id
            left join profiles           pr on pr.id = t.user_id
            left join projects           pj on pj.id = t.project_id
            where t.family_id = p_family_id
              and t.is_recurring = true
        ), '[]'::jsonb),

        -- For `recurring_price_changes`: templates that ended...

        'ended', coalesce((
            select jsonb_agg(jsonb_build_object(
                       'id', t.id, 'amount', t.amount, 'type', t.type,
                       'category_id', t.category_id, 'description', t.description,
                       'recurring_end_date', t.recurring_end_date))
            from transactions t
            where t.family_id = p_family_id
              and t.is_recurring = true
              and t.recurring_end_date is not null
        ), '[]'::jsonb),

        -- ...and the amount of the last occurrence of each of them

        'ended_last', coalesce((
            select jsonb_object_agg(x.parent, x.amount)
            from (
                select distinct on (i.recurring_parent_id)
                       i.recurring_parent_id::text as parent, i.amount
                from transactions i
                join transactions t on t.id = i.recurring_parent_id
                where i.family_id = p_family_id
                  and t.family_id = p_family_id
                  and t.is_recurring = true
                  and t.recurring_end_date is not null
                order by i.recurring_parent_id, i.date desc
            ) x
        ), '{}'::jsonb),

        -- Same as `family_transaction_count`: including projects and templates

        'transaction_count', (
            select count(*) from transactions t where t.family_id = p_family_id
        )
    )
    where p_family_id = public.get_my_family_id();
$function$;

revoke all     on function public.get_settings_page(uuid, date, date) from public, anon;
grant  execute on function public.get_settings_page(uuid, date, date) to authenticated;
