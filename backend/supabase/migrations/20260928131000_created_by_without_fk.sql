/* An immediate fix to 20260928130000: the foreign key from `created_by` to
 * `profiles` added a second relationship between `transactions` and `profiles`
 * (in addition to `user_id`). PostgREST doesn't know which one is meant in
 * `profiles(name, workplace)`, and returns PGRST201, and every transaction
 * query in the app uses that. The home page, the month page and projects went
 * down in production for a few minutes, from the moment it was applied.
 *
 * The column stays, without the foreign key. It doesn't need it: the name is
 * derived from the family member list, and whoever is no longer in it is shown
 * as "former family member". The lesson: any extra foreign key from
 * `transactions` to `profiles` breaks the existing embed.
 */
alter table public.transactions drop constraint if exists transactions_created_by_fkey;
