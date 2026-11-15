-- Restored on 16.11.2026 from the server's migration history (the
-- `statements` column): it was applied through the dashboard/MCP in another
-- session and never had a file here. Kept verbatim so the history matches.

create table if not exists public.admin_check_log (
  id bigint generated always as identity primary key,
  checked_at timestamptz not null default now()
);
alter table public.admin_check_log enable row level security;
revoke all on table public.admin_check_log from anon, authenticated;

-- the last check before this rule was set: 2026-10-14 11:49 Israel time
insert into public.admin_check_log (checked_at) values ('2026-10-14 08:49:00+00');

create or replace function public.admin_activity_since_last_check(p_record boolean default true)
returns table (
  since timestamptz,
  window_minutes integer,
  new_users bigint,
  new_families bigint,
  logins bigint,
  new_transactions bigint,
  new_receipt_scans bigint,
  new_projects bigint
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_last timestamptz;
  v_min integer;
begin
  select max(checked_at) into v_last from public.admin_check_log;
  if v_last is null then v_last := now() - interval '30 minutes'; end if;
  v_min := greatest(1, ceil(extract(epoch from (now() - v_last)) / 60)::integer);
  if p_record then
    insert into public.admin_check_log (checked_at) values (now());
  end if;
  return query
    select v_last, v_min, s.new_users::bigint, s.new_families::bigint, s.logins::bigint,
           s.new_transactions::bigint, s.new_receipt_scans::bigint, s.new_projects::bigint
    from public.admin_activity_stats(v_min) s;
end;
$$;

revoke all on function public.admin_activity_since_last_check(boolean) from public, anon, authenticated;
