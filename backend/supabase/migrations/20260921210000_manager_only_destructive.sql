/* The family manager: enforcement, not just the tag.
 *
 * The role was added in 20260916120000 and since then applied to one action
 * only: removing a member. Every other destructive action stayed open to anyone
 * who joined with a six-character code, above all the transaction reset, which
 * deletes **the whole** family's history and requires only the caller's own
 * password.
 *
 * On top of that, a gap that paralysed the role itself: delete_my_account was
 * written two months before manager_id was added, and never updated. A manager
 * who deleted their account left `manager_id = NULL` (through the FK), and from
 * that moment remove_family_member failed forever: `NULL is distinct from <any
 * uuid>` is always true, and no code anywhere appoints a manager again. The
 * family could never remove a member.
 * leave_family already transfers management; here it is exactly the same.
 */

-- ─── Who is the manager ─────────────────────────────────────────────────────
-- `security definer` so it reads families regardless of the read policy, and
-- returns a boolean only, without exposing the manager's id to anyone who
-- shouldn't see it.

create or replace function public.is_family_manager()
returns boolean
language sql
security definer
stable
set search_path = public
as $$
    select exists (
        select 1 from public.families f
         join public.profiles p on p.family_id = f.id
        where p.id = auth.uid() and f.manager_id = auth.uid()
    );
$$;

revoke all on function public.is_family_manager() from public, anon;
grant execute on function public.is_family_manager() to authenticated;


-- ─── Deleting an account transfers management ───────────────────────────────

create or replace function public.delete_my_account()
returns void
language plpgsql security definer set search_path = public as $$
declare
    v_uid    uuid := auth.uid();
    v_email  text;
    v_family uuid;
    v_others int  := 0;
    v_heir   uuid;
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

    -- Others remain, and the one leaving is the manager: management passes to
    -- the longest-standing member who stays. Without these lines the family is
    -- stuck with no manager and forever unable to remove a member.

    elsif v_family is not null then
        if exists (select 1 from public.families
                    where id = v_family and manager_id = v_uid) then
            select p.id into v_heir from public.profiles p
              where p.family_id = v_family and p.id <> v_uid
              order by p.created_at limit 1;
            update public.families set manager_id = v_heir where id = v_family;
        end if;
    end if;

    -- Deleting from auth cascades to the profile and frees the email + phone
    -- for a new signup

    delete from auth.users where id = v_uid;
end;
$$;
