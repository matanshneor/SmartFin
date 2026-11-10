/* Any family member could appoint themselves manager.
 *
 * `families_member_update` is `for update using (id in (...))` with no column
 * restriction at all, and unlike `profiles` (see 20260916100000) `families` had
 * no narrowed `grant update (...)`. So at the RLS level `UPDATE families SET
 * manager_id = auth.uid()` was allowed for every member, and so were `SET
 * invite_code = 'AAAAAA'` and `SET settings = '{}'`.
 *
 * Not reachable through Flask: `update_family_name` sends `name` only, and the
 * settings go through `merge_family_settings`. But **every** `_require_manager`
 * check in the app relies on a column any member can write, and that is exactly
 * the argument the `profiles` migration made itself: "RLS is supposed to catch
 * a future bug in the code, not be a copy of the same check".
 *
 * `invite_code` is excluded too: replacing it is a manager action
 * (`rotate_invite_code`, which is `security definer` and checks for itself),
 * and a direct write to it bypasses that.
 */
revoke update on public.families from authenticated;
grant  update (name, settings) on public.families to authenticated;
