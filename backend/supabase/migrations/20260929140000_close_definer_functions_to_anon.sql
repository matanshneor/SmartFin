/* SECURITY DEFINER functions that were open to anyone not signed in, for no
 * reason.
 *
 * Supabase's security advisor flagged six. None leaked: they all derive from
 * `auth.uid()`, which is `null` for anyone not signed in, so they did nothing.
 * But Postgres's default is `execute` for PUBLIC, and there is no reason for
 * the door to be open.
 *
 * · `delete_my_account`, `join_family_by_code`, `log_login_event`: called from
 *   the app only after sign-in (`log_login_event` after `set_auth_token`).
 *   They stay for `authenticated` only.
 * · `handle_new_user`, `archive_deleted_transaction`: trigger functions.
 *   Postgres checks `execute` on them only when creating the trigger, not when
 *   it fires, so closing them to everyone doesn't stop them, only the direct
 *   call through `/rest/v1/rpc`.
 *
 * `get_my_family_id` stays open **on purpose**: the `profiles_family_read`
 * policy (for the PUBLIC role) calls it. Closing it to anon would turn "you have
 * no access" (an empty list) into a permission error. It returns only the
 * caller's family, and `null` for anyone not signed in: nothing to close there.
 */
revoke all     on function public.delete_my_account()            from public, anon;
revoke all     on function public.join_family_by_code(text)      from public, anon;
revoke all     on function public.log_login_event(text)          from public, anon;
grant  execute on function public.delete_my_account()            to authenticated;
grant  execute on function public.join_family_by_code(text)      to authenticated;
grant  execute on function public.log_login_event(text)          to authenticated;

revoke all on function public.handle_new_user()              from public, anon, authenticated;
revoke all on function public.archive_deleted_transaction()  from public, anon, authenticated;

comment on function public.get_my_family_id() is
    'Open to anon on purpose: policy profiles_family_read (role PUBLIC) calls it; revoking would turn '
    'an empty result into a permission error. Returns null for anon. See 20260929140000.';
