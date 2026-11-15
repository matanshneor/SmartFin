-- Restored on 16.11.2026 from the server's migration history (the
-- `statements` column): it was applied through the dashboard/MCP in another
-- session and never had a file here. Kept verbatim so the history matches.

create or replace function public.admin_family_activity_since_last_check(p_record boolean default true)
returns table(since timestamptz, family_name text, is_new_family boolean, new_users bigint, logins bigint,
              new_transactions bigint, new_receipt_scans bigint, new_projects bigint)
language plpgsql
security definer
set search_path to 'public'
as $function$
declare
  v_last timestamptz;
begin
  select max(checked_at) into v_last from public.admin_check_log;
  if v_last is null then v_last := now() - interval '30 minutes'; end if;
  if p_record then
    insert into public.admin_check_log (checked_at) values (now());
  end if;
  return query
  with ev as (
    select p.family_id, 'user'::text k from profiles p where p.created_at > v_last
    union all
    select p.family_id, 'login' from login_events l left join profiles p on p.id = l.user_id
      where l.event = 'login' and l.created_at > v_last
    union all
    select t.family_id, 'tx' from transactions t where t.created_at > v_last
    union all
    select r.family_id, 'scan' from receipt_scans r where r.created_at > v_last
    union all
    select pr.family_id, 'proj' from projects pr where pr.created_at > v_last
    union all
    select f.id, 'fam' from families f where f.created_at > v_last
  )
  select v_last,
         coalesce(f.name, 'ללא משפחה')::text,
         coalesce(f.created_at > v_last, false),
         count(*) filter (where ev.k = 'user'),
         count(*) filter (where ev.k = 'login'),
         count(*) filter (where ev.k = 'tx'),
         count(*) filter (where ev.k = 'scan'),
         count(*) filter (where ev.k = 'proj')
  from ev left join families f on f.id = ev.family_id
  group by f.id, f.name, f.created_at
  order by count(*) desc;
end;
$function$;

revoke execute on function public.admin_family_activity_since_last_check(boolean) from public, anon, authenticated;
