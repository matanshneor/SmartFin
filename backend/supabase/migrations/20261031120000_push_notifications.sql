/* Phone notifications: Web Push (Matan, 31.10).
 *
 * `push_subscriptions`: one device per row. Every user sees and manages only
 * their own; who gets what is decided by two security definer functions:
 *
 *   `push_family_targets`: at the moment of an action (a family member joined,
 *     a salary was recorded): only a family member can get their family's
 *     devices, and only theirs.
 *   `push_daily`: once a day, from `pg_cron` through the app. There is no
 *     signed-in user there, so the function is open to anon but requires a
 *     secret kept in the Vault (`push_cron_secret`), and without it returns
 *     nothing. The app gets the same secret from an environment variable
 *     (`PUSH_CRON_SECRET`). That way no service key that bypasses all RLS is
 *     added to the server.
 *
 * `push_log`: what was already sent (kind, family, period), so no notification
 * is sent twice, even if the job ran twice or two family members recorded a
 * salary in the same month.
 *
 * The secrets themselves aren't in this file (the repo is public). They are
 * created once by hand:
 *   select vault.create_secret('<secret>', 'push_cron_secret');
 *   select vault.create_secret('https://<site>/internal/push/daily', 'push_cron_url');
 */

-- ─── Subscriptions ──────────────────────────────────────────────────────────


create table if not exists public.push_subscriptions (
    endpoint    text primary key check (endpoint like 'https://%' and length(endpoint) <= 1000),
    user_id     uuid not null default auth.uid() references public.profiles(id) on delete cascade,
    p256dh      text not null check (length(p256dh) <= 200),
    auth        text not null check (length(auth) <= 100),
    created_at  timestamptz not null default now()
);
create index if not exists push_subscriptions_user_idx on public.push_subscriptions (user_id);

alter table public.push_subscriptions enable row level security;

drop policy if exists push_subscriptions_own on public.push_subscriptions;
create policy push_subscriptions_own on public.push_subscriptions
    for all to authenticated
    using (user_id = (select auth.uid()))
    with check (user_id = (select auth.uid()));

/* Registers this device for the signed-in user. security definer because the
 * same phone can move between two family members (sign out and sign in), and
 * then the row is already registered to the other one, and RLS wouldn't allow
 * updating it. Whoever holds the endpoint is the browser itself, so moving it
 * to the current user gives nobody anything they didn't already have. */
create or replace function public.push_subscribe(p_endpoint text, p_p256dh text, p_auth text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    if auth.uid() is null then
        raise exception 'not signed in';
    end if;
    insert into public.push_subscriptions (endpoint, user_id, p256dh, auth)
    values (p_endpoint, auth.uid(), p_p256dh, p_auth)
    on conflict (endpoint) do update
        set user_id = excluded.user_id, p256dh = excluded.p256dh, auth = excluded.auth,
            created_at = now();
end;
$$;

revoke all on function public.push_subscribe(text, text, text) from public, anon;
grant execute on function public.push_subscribe(text, text, text) to authenticated;

-- ─── What was sent ──────────────────────────────────────────────────────────


create table if not exists public.push_log (
    kind       text not null,
    family_id  uuid not null references public.families(id) on delete cascade,
    period     text not null,
    sent_at    timestamptz not null default now(),
    primary key (kind, family_id, period)
);
-- No policy at all: only the functions below touch it

alter table public.push_log enable row level security;

-- ─── At the moment of an action ─────────────────────────────────────────────


/* The family's devices, except whoever performed the action. `p_kind/p_period`:
 * if sent, this is a once-per-period notification (salary: once a month), and
 * the function records it; if already recorded, it returns no device. */
create or replace function public.push_family_targets(p_kind text default null, p_period text default null)
returns table (endpoint text, p256dh text, auth text)
language plpgsql
security definer
set search_path = public
as $$
#variable_conflict use_column
declare
    v_family uuid := public.get_my_family_id();
    v_rows   int;
begin
    if v_family is null then
        return;
    end if;
    if p_kind is not null then
        insert into public.push_log (kind, family_id, period)
        values (p_kind, v_family, coalesce(p_period, ''))
        on conflict do nothing;
        get diagnostics v_rows = row_count;
        if v_rows = 0 then
            return;
        end if;
    end if;
    return query
        select s.endpoint, s.p256dh, s.auth
        from public.push_subscriptions s
        join public.profiles p on p.id = s.user_id
        where p.family_id = v_family and s.user_id <> auth.uid();
end;
$$;

revoke all on function public.push_family_targets(text, text) from public, anon;
grant execute on function public.push_family_targets(text, text) to authenticated;

-- ─── Once a day ─────────────────────────────────────────────────────────────


create or replace function public._push_secret_ok(p_secret text)
returns boolean
language sql
security definer
stable
set search_path = public
as $$
    select coalesce(p_secret, '') <> '' and exists (
        select 1 from vault.decrypted_secrets
        where name = 'push_cron_secret' and decrypted_secret = p_secret
    );
$$;
revoke all on function public._push_secret_ok(text) from public, anon, authenticated;

/* Everything to send today: a row per (notification, device). `data`: the
 * numbers for the wording; the Hebrew wording itself is built in the app
 * (`backend/push.py`). Every notification is recorded in `push_log` here, so a
 * second run on the same day doesn't return it again.
 *
 * `p_today` comes from the app (Israel time, `backend/clock.py`); the server
 * here is in UTC. */
create or replace function public.push_daily(p_secret text, p_today date)
returns table (kind text, data jsonb, endpoint text, p256dh text, auth text)
language plpgsql
security definer
set search_path = public
as $$
#variable_conflict use_column
declare
    v_prev date := (date_trunc('month', p_today) - interval '1 month')::date;
begin
    if not public._push_secret_ok(p_secret) then
        return;
    end if;

    drop table if exists _due;
    create temporary table _due (kind text, family_id uuid, period text, data jsonb) on commit drop;

    -- Month closed: on the 1st, for every family that had transactions (no projects)

    if extract(day from p_today) = 1 then
        insert into _due
        select 'month_closed', t.family_id, to_char(v_prev, 'YYYY-MM'),
               jsonb_build_object(
                   'month',   extract(month from v_prev)::int,
                   'income',  coalesce(sum(t.amount) filter (where t.type = 'income'), 0),
                   'expense', coalesce(sum(t.amount) filter (where t.type = 'expense'), 0),
                   'savings', coalesce(sum(t.amount) filter (where t.type = 'savings'), 0))
        from public.transactions t
        where t.project_id is null
          and t.date >= v_prev and t.date < date_trunc('month', p_today)::date
        group by t.family_id;
    end if;

    -- Last installment: an installment series ending today (the template
    -- carries the series)

    insert into _due
    select 'last_installment', t.family_id, t.id::text,
           jsonb_build_object('description', coalesce(t.description, ''),
                              'amount', floor(t.installment_total * 100 / t.installments) / 100)
    from public.transactions t
    where t.installments is not null and t.is_recurring
      and t.recurring_end_date = p_today and t.project_id is null;

    -- A week with no new transaction: after 7, 14 and 21 days, then quiet

    insert into _due
    select 'quiet_week', q.family_id, 'w' || q.weeks || '-' || to_char(q.last_at, 'YYYY-MM-DD'),
           jsonb_build_object('weeks', q.weeks)
    from (
        select t.family_id, max(t.created_at) as last_at,
               floor(extract(epoch from (now() - max(t.created_at))) / 604800)::int as weeks
        from public.transactions t
        group by t.family_id
    ) q
    where q.weeks between 1 and 3;

    -- Only what hasn't been sent yet, recorded in the same statement

    return query
        with fresh as (
            insert into public.push_log (kind, family_id, period)
            select d.kind, d.family_id, d.period from _due d
            on conflict do nothing
            returning push_log.kind, push_log.family_id, push_log.period
        )
        select d.kind, d.data, s.endpoint, s.p256dh, s.auth
        from fresh f
        join _due d on d.kind = f.kind and d.family_id = f.family_id and d.period = f.period
        join public.profiles p on p.family_id = d.family_id
        join public.push_subscriptions s on s.user_id = p.id;
end;
$$;

revoke all on function public.push_daily(text, date) from public;
grant execute on function public.push_daily(text, date) to anon, authenticated;

/* A device the service said "no longer exists" (404/410) is deleted. The
 * secret here too. */
create or replace function public.push_forget(p_secret text, p_endpoints text[])
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    if public._push_secret_ok(p_secret) then
        delete from public.push_subscriptions where endpoint = any(p_endpoints);
    end if;
end;
$$;

revoke all on function public.push_forget(text, text[]) from public;
grant execute on function public.push_forget(text, text[]) to anon, authenticated;

-- ─── The schedule ───────────────────────────────────────────────────────────


create extension if not exists pg_net with schema extensions;

/* Every day at 07:05 UTC: 09:05 in winter and 10:05 in summer in Israel.
 * pg_cron calls the app, and the app sends. Without both secrets in the Vault
 * the call simply doesn't go out. */
select cron.schedule('push-daily', '5 7 * * *', $cron$
    select net.http_post(
        url     := (select decrypted_secret from vault.decrypted_secrets where name = 'push_cron_url'),
        headers := jsonb_build_object(
            'Content-Type', 'application/json',
            'X-Cron-Secret', (select decrypted_secret from vault.decrypted_secrets where name = 'push_cron_secret')),
        body    := '{}'::jsonb)
    where exists (select 1 from vault.decrypted_secrets where name = 'push_cron_url');
$cron$);
