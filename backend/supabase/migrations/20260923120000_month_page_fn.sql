/* The month page made five trips to the database to build one screen.
 *
 * `_run_queries` (app.py) runs them in sequence on purpose: it was parallel
 * once, and was moved back to sequential after "Server disconnected" from a
 * shared Supabase client silently returned zeros. So the five pay one after
 * another: ~30ms each, ~150ms in total, before the template started building.
 *
 * The fix here isn't to run them faster but not to run five. The function
 * returns one object with the five parts, and the assembly stays in Python
 * exactly as it was (`_merge_settings`, name shortening, `first_name`), so the
 * change is in transport only and not in semantics.
 *
 * The shape of `rows` mimics what PostgREST returned for
 *   select("*, categories(name, icon), project_categories(name, icon),
 *           profiles(name, workplace), projects(owner_id, name, icon)")
 * including `null` (not `{}`) when there is no linked row: the templates check
 * exactly that.
 *
 * SECURITY DEFINER like its siblings, and with the same check:
 * `get_my_family_id()`. Without it this is a function that hands over the full
 * financial picture of any family by id. Whoever asks for a family they aren't
 * a member of gets `null`, and Python treats that as a failed query.
 */

create or replace function public.get_month_page(
    p_family_id uuid,
    p_year      int,
    p_month     int
)
returns jsonb
language sql
stable
security definer
set search_path = public
as $function$
    select jsonb_build_object(

        -- The full family row: `settings` merges into the defaults in Python,
        -- and the rest feeds the same request cache `get_family` used to fill.

        'family', (
            select to_jsonb(f) from families f where f.id = p_family_id
        ),

        -- Same as get_family_members(uuid), including the join order: the
        -- member order decides the tag colours (`_member_colors`), so it isn't
        -- cosmetic.

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

        -- `family_id is null` are the global categories seeded for everyone;
        -- without them the month page shows only categories the family added
        -- itself.

        'categories', coalesce((
            select jsonb_agg(to_jsonb(c) order by c.sort_order nulls last, c.name)
            from categories c
            where c.family_id is null or c.family_id = p_family_id
        ), '[]'::jsonb),

        -- Includes project transactions. The exclusion happens upstream
        -- (`_household_rows`), and this query is the source for both pictures.

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

        -- Same as get_months_archive(uuid): the month strip at the top of the page.

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
        ), '[]'::jsonb)
    )
    -- Zero rows → the function returns null. That is the right answer to a
    -- question about a family that isn't yours, and it is also what Python
    -- recognises as a failure rather than "an empty family".

    where p_family_id = public.get_my_family_id();
$function$;

revoke all     on function public.get_month_page(uuid, int, int) from public, anon;
grant  execute on function public.get_month_page(uuid, int, int) to authenticated;
