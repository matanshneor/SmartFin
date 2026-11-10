-- App opens (Matan, 10.11.2026).
--
-- login_events records only a sign-in with a password, and since the session is
-- kept, most people almost never type one. This records every time a signed-in
-- user opens the app or comes back to it, so the owner can see who really uses
-- it and how often.
--
-- No user reads or writes the table directly (RLS on, no policies, no grants);
-- the only way in is log_app_open(), which records the caller and nobody else.
-- Kept for 90 days: each call deletes what is older.

create table if not exists public.app_opens (
  id bigint generated always as identity primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  opened_at timestamptz not null default now()
);
create index if not exists app_opens_user_time on public.app_opens (user_id, opened_at desc);
alter table public.app_opens enable row level security;
-- No policies: no user reads or writes the table directly

-- And no grants either: RLS already blocks every row, this is the second layer
-- (the same as every other table since 20261110003245)
revoke all on table public.app_opens from anon, authenticated;
revoke all on sequence public.app_opens_id_seq from anon, authenticated;

create or replace function public.log_app_open()
returns void
language plpgsql
security definer
set search_path to ''
as $$
begin
  if auth.uid() is null then return; end if;
  -- Don't record again if the same user was recorded in the last 5 minutes
  if exists (select 1 from public.app_opens
             where user_id = auth.uid() and opened_at > now() - interval '5 minutes') then
    return;
  end if;
  insert into public.app_opens (user_id) values (auth.uid());
  -- Clean up records older than 90 days
  delete from public.app_opens where opened_at < now() - interval '90 days';
end;
$$;
revoke execute on function public.log_app_open() from public, anon;
grant execute on function public.log_app_open() to authenticated;
