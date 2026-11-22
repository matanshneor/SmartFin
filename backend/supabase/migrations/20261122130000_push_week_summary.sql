-- A weekly summary notification (Matan, 22.11): on Sunday morning, what the
-- family spent in the week that ended (Sunday to Saturday) against the week
-- before it. Running expenses only, as in the home page's week card: no
-- projects and no recurring transactions. Sent once per family and week
-- (push_log), and only to a family that spent something.
-- Built from the live definition of push_daily; only the week_summary block
-- is new.

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

    -- Last installment: an installment series ending today (the template carries the series)
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

    -- The week's summary: on Sunday, the week that ended against the one before it
    if extract(dow from p_today) = 0 then
        insert into _due
        select 'week_summary', w.family_id, 'wk-' || to_char(p_today - 7, 'YYYY-MM-DD'),
               jsonb_build_object('total', w.total, 'previous', w.previous)
        from (
            select t.family_id,
                   coalesce(sum(t.amount) filter (where t.date >= p_today - 7), 0)  as total,
                   coalesce(sum(t.amount) filter (where t.date <  p_today - 7), 0)  as previous
            from public.transactions t
            where t.type = 'expense' and t.project_id is null
              and t.is_recurring is not true and t.recurring_parent_id is null
              and t.date >= p_today - 14 and t.date < p_today
            group by t.family_id
        ) w
        where w.total > 0;
    end if;

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
