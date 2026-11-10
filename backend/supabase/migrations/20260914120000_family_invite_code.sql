-- A short invite code per family + a join that works.
--
-- Background: until now the invite code was the family's UUID, and
-- join_family_by_code on the Python side checked the family exists with a
-- plain SELECT on families. But the RLS policy families_member_read allows
-- reading a family only to someone already a member, and a new user signing
-- up with a code isn't a member of any family yet. So the SELECT always came
-- back empty and the join failed silently, every time since the feature was
-- written. The user got "signed up successfully" and then ensure_family opened
-- a new family of their own.
--
-- The fix: an RPC function with security definer, running with the owner's
-- privileges and so seeing the whole table: the same pattern as
-- email_for_phone and delete_my_account.
-- In addition the code moves from a 36-character UUID to a 6-character code
-- that can be read out over the phone.


-- ─── Invite code ────────────────────────────────────────────────────────────


alter table public.families add column if not exists invite_code text;

-- An alphabet without confusable characters: no I/1 and no O/0, so reading it
-- out over the phone doesn't fail.

create or replace function public.gen_invite_code()
returns text
language plpgsql
set search_path = public
as $$
declare
    v_alphabet constant text := 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    v_code text;
    v_i    int;
begin
    loop
        v_code := '';
        for v_i in 1..6 loop
            v_code := v_code || substr(
                v_alphabet, 1 + floor(random() * length(v_alphabet))::int, 1);
        end loop;
        exit when not exists (
            select 1 from public.families where invite_code = v_code);
    end loop;
    return v_code;
end;
$$;

-- Backfilling row by row and not in one UPDATE: within a single statement the
-- subquery checking uniqueness doesn't see rows the same statement updated, so
-- two families could get the same code. A loop with a separate statement per
-- row prevents that.

do $$
declare
    r record;
begin
    for r in select id from public.families where invite_code is null loop
        update public.families
           set invite_code = public.gen_invite_code()
         where id = r.id;
    end loop;
end $$;

create unique index if not exists families_invite_code_unique_idx
    on public.families (invite_code);

alter table public.families alter column invite_code set not null;
alter table public.families alter column invite_code set default public.gen_invite_code();

-- ─── Joining a family ───────────────────────────────────────────────────────


-- Returns the family_id on success, and NULL when the code doesn't exist, so
-- the layer above can tell "joined" from "wrong code" instead of swallowing both.

create or replace function public.join_family_by_code(p_code text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid    uuid := auth.uid();
    v_code   text;
    v_family uuid;
    v_old    uuid;
begin
    if v_uid is null then
        raise exception 'not authenticated';
    end if;

    -- Normalise the input: the code travels over WhatsApp and is read out over
    -- the phone, so strip everything that isn't a letter or digit (spaces,
    -- dashes, direction marks) and upper-case it.

    v_code := upper(regexp_replace(coalesce(p_code, ''), '[^A-Za-z0-9]', '', 'g'));
    if v_code = '' then
        return null;
    end if;

    select id into v_family from public.families where invite_code = v_code;
    if v_family is null then
        return null;
    end if;

    select family_id into v_old from public.profiles where id = v_uid;
    if v_old = v_family then
        return v_family;  -- already a member there, nothing to do
    end if;

    update public.profiles set family_id = v_family where id = v_uid;

    -- Clean up the abandoned family. On first sign-in ensure_family opens a
    -- family for every user, and without this cleanup every join would leave
    -- behind an empty family named "המשפחה שלי": exactly the junk that piled up
    -- in the table until now. Delete only when it has no other member and no
    -- transaction, so real history is never lost.

    if v_old is not null
       and not exists (select 1 from public.profiles where family_id = v_old)
       and not exists (select 1 from public.transactions where family_id = v_old)
    then
        delete from public.families where id = v_old;
    end if;

    return v_family;
end;
$$;

grant execute on function public.join_family_by_code(text) to authenticated;

-- A preview before confirming ("מצטרפים למשפחת כהן?"). Returns only the name,
-- and reveals nothing else about the family.

create or replace function public.family_name_for_code(p_code text)
returns text
language sql
security definer
set search_path = public
stable
as $$
    select name
    from public.families
    where invite_code = upper(regexp_replace(coalesce(p_code, ''), '[^A-Za-z0-9]', '', 'g'))
    limit 1;
$$;

grant execute on function public.family_name_for_code(text) to anon, authenticated;
