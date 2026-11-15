-- Restored on 16.11.2026 from the server's migration history (the
-- `statements` column): it was applied through the dashboard/MCP in another
-- session and never had a file here. Kept verbatim so the history matches.

create or replace function public.admin_family_activity_since(p_since timestamptz)
returns table(family_name text, is_new_family boolean, logins bigint, new_transactions bigint)
language sql
stable
security definer
set search_path to 'public'
as $function$
  with ev as (
    select p.family_id, 'login'::text k from login_events l left join profiles p on p.id = l.user_id
      where l.event = 'login' and l.created_at > p_since
    union all
    select t.family_id, 'tx' from transactions t where t.created_at > p_since
    union all
    select f.id, 'fam' from families f where f.created_at > p_since
  )
  select coalesce(f.name, 'ללא משפחה')::text,
         coalesce(f.created_at > p_since, false),
         count(*) filter (where ev.k = 'login'),
         count(*) filter (where ev.k = 'tx')
  from ev left join families f on f.id = ev.family_id
  group by f.id, f.name, f.created_at
  order by count(*) desc;
$function$;

revoke execute on function public.admin_family_activity_since(timestamptz) from public, anon, authenticated;
