/* Two SECURITY DEFINER functions take a family id and don't check who is
 * calling.
 *
 * SECURITY DEFINER means the function runs with the privileges of whoever
 * created it, not whoever calls it, i.e. RLS doesn't apply to it. That is
 * needed here (get_family_members reads from auth.users, which RLS blocks), but
 * it means the check must be inside the function. In these two it simply wasn't:
 *
 *   get_family_members(uuid)  → name, email, phone and workplace of every
 *                                member of any family, by id
 *   get_months_archive(uuid)  → the entire monthly financial history of any family
 *
 * And without a revoke, Postgres grants execute to PUBLIC by default, i.e. also
 * to anon, with no sign-in at all.
 *
 * What protects right now is that the PostgREST key is kept on the server and
 * not exposed to the client. That is real protection but the only one, and it
 * isn't what should protect here. The project's other functions already do it
 * right (join_family_by_code and delete_my_account derive from auth.uid()), and
 * these are two exceptions that were forgotten.
 *
 * The check uses the existing get_my_family_id(). Whoever asks for a family
 * they aren't a member of gets zero rows: the right answer to a question about
 * data that isn't theirs.
 *
 * SET search_path is added too: these two were the only ones without it, and
 * that is a known escalation vector in DEFINER functions.
 */

create or replace function public.get_family_members(p_family_id uuid)
returns table(id uuid, name text, avatar_initial text, email text, phone text, workplace text)
language sql
stable
security definer
set search_path = public
as $function$
    select p.id, p.name, p.avatar_initial, u.email::text, p.phone, p.workplace
    from profiles p
    join auth.users u on u.id = p.id
    where p.family_id = p_family_id
      and p_family_id = public.get_my_family_id()
    order by p.created_at;
$function$;

create or replace function public.get_months_archive(p_family_id uuid)
returns table(year integer, month integer, income numeric, expense numeric, savings numeric, balance numeric)
language sql
stable
security definer
set search_path = public
as $function$
    select
        extract(year  from date)::int  as year,
        extract(month from date)::int  as month,
        coalesce(sum(amount) filter (where type = 'income'),  0) as income,
        coalesce(sum(amount) filter (where type = 'expense'), 0) as expense,
        coalesce(sum(amount) filter (where type = 'savings'), 0) as savings,
        coalesce(sum(amount) filter (where type = 'income'),  0)
            - coalesce(sum(amount) filter (where type = 'expense'), 0) as balance
    from transactions
    where family_id = p_family_id
      and p_family_id = public.get_my_family_id()
      and project_id is null
    group by year, month
    order by year desc, month desc;
$function$;

revoke all     on function public.get_family_members(uuid) from public, anon;
revoke all     on function public.get_months_archive(uuid) from public, anon;
grant  execute on function public.get_family_members(uuid) to authenticated;
grant  execute on function public.get_months_archive(uuid) to authenticated;
