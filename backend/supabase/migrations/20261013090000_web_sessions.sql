-- Staying signed in even when a response gets lost (Matan, 13.10: "sometimes it
-- asks me to sign in and sometimes not").
--
-- Supabase's refresh token rotates on every refresh, and the old one is
-- invalidated. Until now the new token was stored only in the cookie, i.e. only
-- if the response reached the phone. On iPhone that doesn't always happen: the
-- app goes to the background mid-request (`499` in Railway's logs), the server
-- has already rotated the token, and the cookie is left with the old one. On
-- the next visit: "Invalid Refresh Token: Already Used", and a sign-out. 11
-- times in two days (11–12.10).
--
-- The fix: a copy of the latest tokens in a table, keyed by a random session id
-- kept in the cookie (`sid`, 256 bits). A request with a stale cookie finds here
-- the token that replaced its own.
--
-- Security: the server sends only the `sha256` of the id; the id itself isn't
-- stored in the database. Whoever has the id already holds the cookie, which
-- today carries the refresh token itself, so there is no access here that
-- didn't exist before. No direct access to the table (RLS with no policy);
-- only through the functions, each of which requires the hash.

create table if not exists public.web_sessions (
    sid_hash      text primary key,
    user_id       uuid not null references auth.users (id) on delete cascade,
    access_token  text not null,
    refresh_token text not null,
    expires_at    bigint not null,
    updated_at    timestamptz not null default now()
);
alter table public.web_sessions enable row level security;
create index if not exists web_sessions_user_id_idx on public.web_sessions (user_id);

-- On sign-in: a new row only, never overwriting an existing one

create or replace function public.web_session_open(
    p_sid_hash text, p_user_id uuid, p_access text, p_refresh text, p_expires bigint)
returns void
language sql
security definer
set search_path = ''
as $$
    delete from public.web_sessions where updated_at < now() - interval '120 days';
    insert into public.web_sessions (sid_hash, user_id, access_token, refresh_token, expires_at)
    values (p_sid_hash, p_user_id, p_access, p_refresh, p_expires)
    on conflict (sid_hash) do nothing;
$$;

create or replace function public.web_session_get(p_sid_hash text)
returns table (access_token text, refresh_token text, expires_at bigint)
language sql
security definer
set search_path = ''
as $$
    select s.access_token, s.refresh_token, s.expires_at
    from public.web_sessions s where s.sid_hash = p_sid_hash;
$$;

-- After a refresh: only if the token in the database is the one that was
-- refreshed (two workers don't overwrite each other)

create or replace function public.web_session_rotate(
    p_sid_hash text, p_old_refresh text, p_access text, p_refresh text, p_expires bigint)
returns boolean
language sql
security definer
set search_path = ''
as $$
    with u as (
        update public.web_sessions
        set access_token = p_access, refresh_token = p_refresh,
            expires_at = p_expires, updated_at = now()
        where sid_hash = p_sid_hash and refresh_token = p_old_refresh
        returning 1)
    select exists (select 1 from u);
$$;

create or replace function public.web_session_close(p_sid_hash text)
returns void
language sql
security definer
set search_path = ''
as $$
    delete from public.web_sessions where sid_hash = p_sid_hash;
$$;

revoke all on function public.web_session_open(text, uuid, text, text, bigint) from public;
revoke all on function public.web_session_get(text) from public;
revoke all on function public.web_session_rotate(text, text, text, text, bigint) from public;
revoke all on function public.web_session_close(text) from public;
grant execute on function public.web_session_open(text, uuid, text, text, bigint) to anon, authenticated;
grant execute on function public.web_session_get(text) to anon, authenticated;
grant execute on function public.web_session_rotate(text, text, text, text, bigint) to anon, authenticated;
grant execute on function public.web_session_close(text) to anon, authenticated;
