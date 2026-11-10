/* Managing family membership: removing a member, leaving, and replacing the
 * invite code.
 *
 * Until now whoever joined a family (by mistake, or with a code leaked on
 * WhatsApp) stayed in it forever. There was no route to remove them, no way to
 * leave yourself, and the code was created once and couldn't be changed. So a
 * leak of six characters meant permanent access to the family's finances, and
 * to every member's phone number and workplace.
 *
 * The model was completely flat: all members equal. Removal is the first
 * action that requires a distinction, so one single role was added: **the
 * family manager**. They can remove others; everyone else is a regular family
 * member who can leave by themselves. The manager can't be removed; otherwise
 * two members could remove each other until nobody was left. When they leave,
 * management passes to the longest-standing member who stays.
 */

-- ─── The family manager ─────────────────────────────────────────────────────

alter table public.families
  add column if not exists manager_id uuid references public.profiles(id) on delete set null;

-- For existing families: the longest-standing member. Not knowledge but the
-- best estimate that can be derived after the fact, and it is right for every
-- family opened through ensure_family.

update public.families f
   set manager_id = (select p.id from public.profiles p
                      where p.family_id = f.id order by p.created_at limit 1)
 where f.manager_id is null;

-- ─── Creating a family records ownership ────────────────────────────────────

create or replace function public.create_own_family(p_name text default 'המשפחה שלי')
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid      uuid := auth.uid();
    v_existing uuid;
    v_id       uuid;
begin
    if v_uid is null then
        raise exception 'not authenticated';
    end if;

    select family_id into v_existing from public.profiles where id = v_uid;
    if v_existing is not null then
        return v_existing;
    end if;

    v_id := gen_random_uuid();
    insert into public.families (id, name, manager_id)
        values (v_id, coalesce(nullif(btrim(p_name), ''), 'המשפחה שלי'), v_uid);
    update public.profiles set family_id = v_id where id = v_uid;
    return v_id;
end;
$$;

-- ─── Removing a member (owner only) ─────────────────────────────────────────

create or replace function public.remove_family_member(
    p_user_id uuid, p_keep_transactions boolean default true)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid    uuid := auth.uid();
    v_family uuid;
    v_manager uuid;
    v_new    uuid;
begin
    if v_uid is null then raise exception 'not authenticated'; end if;
    if v_uid = p_user_id then raise exception 'use leave_family to remove yourself'; end if;

    select family_id into v_family from public.profiles where id = v_uid;
    if v_family is null then raise exception 'no family'; end if;

    select manager_id into v_manager from public.families where id = v_family;
    if v_manager is distinct from v_uid then raise exception 'only the family manager can remove members'; end if;
    if p_user_id = v_manager then raise exception 'the family manager cannot be removed'; end if;

    if not exists (select 1 from public.profiles
                    where id = p_user_id and family_id = v_family) then
        raise exception 'not a member of your family';
    end if;

    if p_keep_transactions then
        -- The money left the shared budget, so the totals stay right. The
        -- attribution is dropped and the transaction is shown as "shared", a
        -- state the view already knows how to display.

        update public.transactions set user_id = null
         where family_id = v_family and user_id = p_user_id;
    else
        -- The trigger on transactions archives every row on the way out

        delete from public.transactions
         where family_id = v_family and user_id = p_user_id;
    end if;

    -- A new, empty family, so their account stays usable and isn't stuck
    -- without a family

    v_new := gen_random_uuid();
    insert into public.families (id, name, manager_id) values (v_new, 'המשפחה שלי', p_user_id);
    update public.profiles set family_id = v_new where id = p_user_id;
end;
$$;

-- ─── Leaving by yourself (anyone) ───────────────────────────────────────────

create or replace function public.leave_family(p_keep_transactions boolean default true)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid    uuid := auth.uid();
    v_family uuid;
    v_manager uuid;
    v_heir   uuid;
    v_new    uuid;
begin
    if v_uid is null then raise exception 'not authenticated'; end if;

    select family_id into v_family from public.profiles where id = v_uid;
    if v_family is null then raise exception 'no family'; end if;

    if p_keep_transactions then
        update public.transactions set user_id = null
         where family_id = v_family and user_id = v_uid;
    else
        delete from public.transactions
         where family_id = v_family and user_id = v_uid;
    end if;

    v_new := gen_random_uuid();
    insert into public.families (id, name, manager_id) values (v_new, 'המשפחה שלי', v_uid);
    update public.profiles set family_id = v_new where id = v_uid;

    select manager_id into v_manager from public.families where id = v_family;
    if v_manager = v_uid then
        -- Management passes to the longest-standing member who stays. Without
        -- this a family whose manager left is left with nobody who can manage it.

        select p.id into v_heir from public.profiles p
          where p.family_id = v_family order by p.created_at limit 1;
        update public.families set manager_id = v_heir where id = v_family;
    end if;

    -- A family left empty and without history is junk; with history it is
    -- kept, just as in join_family_by_code.

    if not exists (select 1 from public.profiles where family_id = v_family)
       and not exists (select 1 from public.transactions where family_id = v_family) then
        delete from public.families where id = v_family;
    end if;

    return v_new;
end;
$$;

-- ─── Replacing the invite code ──────────────────────────────────────────────

create or replace function public.rotate_invite_code()
returns text
language plpgsql
security definer
set search_path = public
as $$
declare
    v_uid    uuid := auth.uid();
    v_family uuid;
    v_code   text;
begin
    if v_uid is null then raise exception 'not authenticated'; end if;

    select family_id into v_family from public.profiles where id = v_uid;
    if v_family is null then raise exception 'no family'; end if;

    -- Any family member may, not just the manager: whoever discovers the code
    -- leaked needs to close it right away. The risk is symmetric: the old code
    -- stops working, and that is the whole point.

    v_code := public.gen_invite_code();
    update public.families set invite_code = v_code where id = v_family;
    return v_code;
end;
$$;

revoke all on function public.remove_family_member(uuid, boolean) from public, anon;
revoke all on function public.leave_family(boolean)               from public, anon;
revoke all on function public.rotate_invite_code()                from public, anon;
grant execute on function public.remove_family_member(uuid, boolean) to authenticated;
grant execute on function public.leave_family(boolean)               to authenticated;
grant execute on function public.rotate_invite_code()                to authenticated;
