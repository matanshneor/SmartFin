-- Restored on 16.11.2026 from the server's migration history (the
-- `statements` column): it was applied through the dashboard/MCP in another
-- session and never had a file here. Kept verbatim so the history matches.

create or replace function public.admin_new_families_since(p_since timestamptz)
returns table(family_name text, created_at timestamptz)
language sql
security definer
set search_path to 'public'
as $$
  select f.name::text, f.created_at
  from families f
  where f.created_at > p_since
  order by f.created_at;
$$;
revoke execute on function public.admin_new_families_since(timestamptz) from public, anon, authenticated;
