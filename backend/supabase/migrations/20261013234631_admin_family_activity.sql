-- Restored on 16.11.2026 from the server's migration history (the
-- `statements` column): it was applied through the dashboard/MCP in another
-- session and never had a file here. Kept verbatim so the history matches.

create or replace function public.admin_family_activity(p_minutes integer default 30)
returns table (
  family_name text,
  new_transactions bigint,
  is_new_family boolean
)
language sql
security definer
set search_path = public
as $$
  select f.name::text,
         count(t.id)::bigint,
         (f.created_at >= now() - make_interval(mins => p_minutes))
  from public.transactions t
  join public.families f on f.id = t.family_id
  where t.created_at >= now() - make_interval(mins => p_minutes)
  group by f.id, f.name, f.created_at
  order by count(t.id) desc;
$$;

revoke all on function public.admin_family_activity(integer) from public, anon, authenticated;
