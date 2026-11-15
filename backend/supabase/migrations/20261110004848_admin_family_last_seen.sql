-- Restored on 16.11.2026 from the server's migration history (the
-- `statements` column): it was applied through the dashboard/MCP in another
-- session and never had a file here. Kept verbatim so the history matches.

create or replace function public.admin_family_last_seen()
returns table(family_name text, last_seen timestamptz)
language sql
security definer
set search_path to ''
as $$
  with s as (
    select user_id, greatest(coalesce(refreshed_at, updated_at), updated_at) t from auth.sessions
    union all
    select user_id, updated_at from public.web_sessions
  )
  select coalesce(f.name, 'ללא משפחה')::text, max(s.t)
  from s
  left join public.profiles p on p.id = s.user_id
  left join public.families f on f.id = p.family_id
  group by f.id, f.name
  order by 2 desc;
$$;
revoke execute on function public.admin_family_last_seen() from public, anon, authenticated;
