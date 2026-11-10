-- Self-service account deletion + an internal archive for the site owner +
-- sign-in records.
--
-- Principles:
-- 1. From the user's point of view the deletion is final: the account and all
--    data disappear, and the email/phone are freed for a new signup that
--    "remembers" nothing.
-- 2. From the site owner's point of view nothing disappears: deleted accounts,
--    deleted families and every deleted transaction (from any route) are kept
--    in owner_archive.
-- 3. The internal tables (owner_archive, login_events) have RLS on and no
--    policy at all: no client (anon/authenticated) can read or write them.
--    Writes happen only through SECURITY DEFINER functions/triggers, and reads
--    only directly from Supabase (the site owner).


-- ── Internal archive ───────────────────────────────────────────────────────

create table if not exists public.owner_archive (
    id          uuid primary key default gen_random_uuid(),
    kind        text not null,           -- 'transaction' | 'account' | 'family'
    payload     jsonb not null,
    archived_at timestamptz not null default now()
);
alter table public.owner_archive enable row level security;

-- ── Sign-in records ────────────────────────────────────────────────────────

create table if not exists public.login_events (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid,
    email      text,
    event      text not null default 'login',
    created_at timestamptz not null default now()
);
alter table public.login_events enable row level security;

create or replace function public.log_login_event(p_event text default 'login')
returns void
language plpgsql security definer set search_path = public as $$
begin
    insert into public.login_events (user_id, email, event)
    select u.id, u.email, p_event
    from auth.users u where u.id = auth.uid();
end;
$$;
grant execute on function public.log_login_event(text) to authenticated;

-- ── Archiving every transaction deletion (any route: manual, project delete,
-- family delete) ──────────────────────────────────────────────────────────

create or replace function public.archive_deleted_transaction()
returns trigger
language plpgsql security definer set search_path = public as $$
begin
    insert into public.owner_archive (kind, payload) values ('transaction', to_jsonb(old));
    return old;
end;
$$;

drop trigger if exists trg_archive_deleted_transaction on public.transactions;
create trigger trg_archive_deleted_transaction
    before delete on public.transactions
    for each row execute function public.archive_deleted_transaction();

-- ── Self-service account deletion ──────────────────────────────────────────
-- If other family members remain: only the account is deleted, the family's
-- data stays, and the user's personal attribution automatically becomes
-- "shared" (FK ON DELETE SET NULL).
-- If this is the last member: the whole family is deleted (transactions are
-- archived through the trigger).

create or replace function public.delete_my_account()
returns void
language plpgsql security definer set search_path = public as $$
declare
    v_uid    uuid := auth.uid();
    v_email  text;
    v_family uuid;
    v_others int  := 0;
begin
    if v_uid is null then
        raise exception 'not authenticated';
    end if;

    select email into v_email from auth.users where id = v_uid;
    select family_id into v_family from public.profiles where id = v_uid;

    if v_family is not null then
        select count(*) into v_others from public.profiles
        where family_id = v_family and id <> v_uid;
    end if;

    -- Archive the account (including the email) before deletion

    insert into public.owner_archive (kind, payload)
    select 'account',
           to_jsonb(p) || jsonb_build_object(
               'email', v_email,
               'was_last_member', (v_family is not null and v_others = 0))
    from public.profiles p where p.id = v_uid;

    -- The last member: archive the family (with its categories and projects)
    -- and delete it

    if v_family is not null and v_others = 0 then
        insert into public.owner_archive (kind, payload)
        select 'family', jsonb_build_object(
            'family',     to_jsonb(f),
            'categories', (select coalesce(jsonb_agg(to_jsonb(c)), '[]'::jsonb)
                           from public.categories c where c.family_id = f.id),
            'projects',   (select coalesce(jsonb_agg(to_jsonb(pr)), '[]'::jsonb)
                           from public.projects pr where pr.family_id = f.id))
        from public.families f where f.id = v_family;

        delete from public.families where id = v_family;
    end if;

    -- Deleting from auth cascades to the profile and frees the email + phone
    -- for a new signup

    delete from auth.users where id = v_uid;
end;
$$;
grant execute on function public.delete_my_account() to authenticated;
