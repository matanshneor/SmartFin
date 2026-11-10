/* anon's last read (families, for the health check) is closed.
 *
 * It didn't work anyway: the RLS policy on families reads profiles, and anon no
 * longer had a grant on it, so `/health/db` returned 401. `ping` now goes
 * through `rpc/get_my_family_id` (SECURITY DEFINER), so anon has no grant on
 * any table.
 */
revoke select on public.families from anon;
