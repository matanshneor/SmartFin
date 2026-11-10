/* Closes the bypass of the family membership lock
 * (20260916100000_lock_family_membership).
 *
 * That lock removed the UPDATE grant on profiles.family_id, but left an
 * authenticated user DELETE on the whole table and INSERT on family_id. So the
 * same attack in two steps instead of one:
 *
 *   1. DELETE my own profile row (profiles_own is FOR ALL).
 *   2. INSERT a new row with id = auth.uid() and another family's family_id.
 *      The WITH CHECK only checks that id = auth.uid(), so it passes.
 *
 * From that moment every policy in the other tables gives full access to the
 * foreign family, because they are all derived from profiles.family_id.
 *
 * Not exploitable through the app (no Flask route deletes or creates a
 * profile, and the anon key is kept on the server), and the family's UUID must
 * be known too. But RLS is the layer that should stop this even when the other
 * layers fail.
 *
 * No legitimate route uses these grants:
 *   - A profile is created at signup by the handle_new_user trigger (SECURITY DEFINER).
 *   - A profile is deleted on account deletion by delete_my_account (SECURITY DEFINER).
 *   - Membership changes only through create_own_family / join_family_by_code /
 *     leave_family / remove_family_member (all SECURITY DEFINER).
 */

revoke insert, delete on public.profiles from authenticated, anon;
