/* A personal project of whoever leaves the family vanished for everyone.
 *
 * Removal, leaving and moving by invite code reset `transactions.user_id`, but
 * didn't touch `projects.owner_id`, which kept pointing at whoever left (their
 * profile exists, so the foreign key isn't reset). The project is "someone
 * else's personal project" to everyone who stays, and so hidden; the money in
 * it is excluded from household expenses because it is in a project, and from
 * every list and export because it is hidden; and whoever left doesn't see it
 * because they are in another family. No way back from the UI.
 *
 * Now whoever decides is asked (Matan's suggestion): "delete them" or "keep as
 * shared".
 *   · `share`  : the project becomes shared, and the whole family sees it.
 *   · `delete` : the project is deleted **with its transactions**. Without them
 *     the transactions would land in household expenses as "uncategorised"
 *     (`on delete set null`).
 *   · `ask`    : what the app sends when it hasn't asked yet: if there are
 *     personal projects, an exception `needs_project_choice:N` and the screen
 *     asks; if not, carry on.
 * The default is `share`, so the code running in production now (which doesn't
 * send the parameter yet) keeps working in the gap between migration and
 * deploy, and the safe way.
 *
 * Whoever chose to delete the transactions isn't asked: the projects are
 * deleted with them. "Deleting the transactions" so far deleted only what is
 * recorded under their name, and a personal project can also hold transactions
 * recorded as shared, and they would stay in an orphan project.
 *
 * Moving by invite code asks nothing today either (not even about the
 * transactions), so there it is `share`.
 *
 * The signatures of both functions change, so `drop` before `create`;
 * otherwise a second overload is created and calls become ambiguous. The
 * grants are set again at the end.
 */

create or replace function public._release_personal_projects(
    p_family uuid,
    p_user   uuid,
    p_mode   text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    v_count integer;
begin
    if p_mode not in ('share', 'delete', 'ask') then
        raise exception 'unknown project choice: %', p_mode;
    end if;

    select count(*) into v_count from public.projects
     where family_id = p_family and owner_id = p_user;
    if v_count = 0 then
        return;
    end if;

    if p_mode = 'ask' then
        raise exception 'needs_project_choice:%', v_count;
    end if;

    if p_mode = 'share' then
        update public.projects set owner_id = null
         where family_id = p_family and owner_id = p_user;
        return;
    end if;

    -- delete: first the transactions (the trigger archives each on the way
    -- out), then the project

    delete from public.transactions
     where family_id = p_family
       and project_id in (select id from public.projects
                           where family_id = p_family and owner_id = p_user);
    delete from public.projects
     where family_id = p_family and owner_id = p_user;
end;
$$;

-- Internal only: called from the three functions below, not from the app

revoke all on function public._release_personal_projects(uuid, uuid, text)
    from public, anon, authenticated;

drop function if exists public.remove_family_member(uuid, boolean);
drop function if exists public.leave_family(boolean);

CREATE OR REPLACE FUNCTION public.remove_family_member(p_user_id uuid, p_keep_transactions boolean DEFAULT true, p_projects text DEFAULT 'share')
 RETURNS void
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
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

    perform public._release_personal_projects(
        v_family, p_user_id, case when p_keep_transactions then p_projects else 'delete' end);

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
$function$;

CREATE OR REPLACE FUNCTION public.leave_family(p_keep_transactions boolean DEFAULT true, p_projects text DEFAULT 'share')
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
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

    perform public._release_personal_projects(
        v_family, v_uid, case when p_keep_transactions then p_projects else 'delete' end);

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
$function$;

CREATE OR REPLACE FUNCTION public.join_family_by_code(p_code text)
 RETURNS uuid
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public'
AS $function$
declare
    v_uid     uuid := auth.uid();
    v_code    text;
    v_family  uuid;
    v_old     uuid;
    v_manager uuid;
    v_heir    uuid;
begin
    if v_uid is null then
        raise exception 'not authenticated';
    end if;

    v_code := upper(regexp_replace(coalesce(p_code, ''), '[^A-Za-z0-9]', '', 'g'));
    if v_code = '' then
        return null;
    end if;

    select id into v_family from public.families
     where invite_code = v_code
       and (invite_code_expires_at is null or invite_code_expires_at > now());
    if v_family is null then
        return null;
    end if;

    select family_id into v_old from public.profiles where id = v_uid;
    if v_old = v_family then
        return v_family;
    end if;

    if v_old is not null then
        perform public._release_personal_projects(v_old, v_uid, 'share');
    end if;

    update public.profiles set family_id = v_family where id = v_uid;

    -- Management passes to the longest-standing member who stays in the old
    -- family (see 20260922130000).

    if v_old is not null then
        select manager_id into v_manager from public.families where id = v_old;
        if v_manager = v_uid then
            select p.id into v_heir from public.profiles p
              where p.family_id = v_old order by p.created_at limit 1;
            update public.families set manager_id = v_heir where id = v_old;
        end if;
    end if;

    if v_old is not null
       and not exists (select 1 from public.profiles where family_id = v_old)
       and not exists (select 1 from public.transactions where family_id = v_old)
    then
        delete from public.families where id = v_old;
    end if;

    return v_family;
end;
$function$;


revoke all     on function public.remove_family_member(uuid, boolean, text) from public, anon;
revoke all     on function public.leave_family(boolean, text)               from public, anon;
grant  execute on function public.remove_family_member(uuid, boolean, text) to authenticated;
grant  execute on function public.leave_family(boolean, text)               to authenticated;
