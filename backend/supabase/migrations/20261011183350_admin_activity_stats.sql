-- Restored on 16.11.2026 from the server's migration history (the
-- `statements` column): it was applied through the dashboard/MCP in another
-- session and never had a file here. Kept verbatim so the history matches.

create or replace function public.admin_activity_stats(minutes integer default 60)
returns table (
  window_minutes integer,
  new_users bigint,
  new_families bigint,
  logins bigint,
  new_transactions bigint,
  new_receipt_scans bigint,
  new_projects bigint
)
language sql
stable
security definer
set search_path = public
as $$
  select
    minutes,
    (select count(*) from profiles      where created_at > now() - make_interval(mins => minutes)),
    (select count(*) from families      where created_at > now() - make_interval(mins => minutes)),
    (select count(*) from login_events  where event = 'login' and created_at > now() - make_interval(mins => minutes)),
    (select count(*) from transactions  where created_at > now() - make_interval(mins => minutes)),
    (select count(*) from receipt_scans where created_at > now() - make_interval(mins => minutes)),
    (select count(*) from projects      where created_at > now() - make_interval(mins => minutes));
$$;

comment on function public.admin_activity_stats(integer) is
  'Aggregate activity counts only (no row content). Not callable by app users.';

revoke all on function public.admin_activity_stats(integer) from public, anon, authenticated;
