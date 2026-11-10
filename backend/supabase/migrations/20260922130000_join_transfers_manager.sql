/* Joining another family locked the family that was left, forever.
 *
 * `join_family_by_code` updated `profiles.family_id` and didn't touch
 * `manager_id`. Whoever opened a family and then joined another left
 * `families.manager_id` pointing at them while no longer a member.
 * `is_family_manager()` requires both membership and a match, so it returned
 * `false` **for everyone**:
 *
 *   - the family's transactions can't be reset
 *   - a category can't be deleted
 *   - a leaked invite code can't be replaced
 *   - a member can't be removed
 *
 * And no screen in the app appoints a new manager, so it is irreversible
 * without direct database access.
 *
 * `leave_family` and `delete_my_account` both already hand the role to the
 * longest-standing member who stays. This is exactly the same handover, in the
 * third route that was missed.
 */
create or replace function public.join_family_by_code(p_code text)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
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

    -- Management passes to the longest-standing member who stays in the old
    -- family. Without this it is left with a manager who isn't a member, i.e.
    -- with nobody who can manage it, forever.

    if v_old is not null then
        select manager_id into v_manager from public.families where id = v_old;
        if v_manager = v_uid then
            select p.id into v_heir from public.profiles p
              where p.family_id = v_old order by p.created_at limit 1;
            update public.families set manager_id = v_heir where id = v_old;
        end if;
    end if;

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

/* And a fix for families already stuck this way. There is no other way to
 * give them management back. */
update public.families f
   set manager_id = (select p.id from public.profiles p
                      where p.family_id = f.id order by p.created_at limit 1)
 where f.manager_id is null
    or not exists (select 1 from public.profiles p
                    where p.id = f.manager_id and p.family_id = f.id);
