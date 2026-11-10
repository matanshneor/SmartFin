/* Every family member sees a valid invite code in settings (Matan's request,
 * 29.9.2026).
 *
 * `rotate_invite_code` is manager-only, and rightly so: it also replaces a
 * code still valid, i.e. cancels invitations already sent. But renewing a code
 * **that already expired** cancels nothing, and there is no reason for a
 * family member opening settings to see "the code expired" and wait for the
 * manager.
 *
 * Hence a separate function, any member may run, that can do only this: if the
 * code is missing or expired, a new code for a week; otherwise nothing. It
 * can't be used to cancel a valid code, so the manager's permission isn't
 * weakened.
 *
 * One update with the condition inside it, not "check then update": two family
 * members opening settings in the same second would produce two codes, and one
 * of them would see a code already replaced. This way the second finds nothing
 * to update and gets the code the first one produced.
 *
 * Returns the code valid now, new or existing.
 */
create or replace function public.renew_expired_invite_code()
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

    update public.families
       set invite_code            = public.gen_invite_code(),
           invite_code_expires_at = now() + interval '7 days'
     where id = v_family
       and (invite_code is null or invite_code_expires_at <= now());

    select invite_code into v_code from public.families where id = v_family;
    return v_code;
end;
$$;

revoke all     on function public.renew_expired_invite_code() from public, anon;
grant  execute on function public.renew_expired_invite_code() to authenticated;
