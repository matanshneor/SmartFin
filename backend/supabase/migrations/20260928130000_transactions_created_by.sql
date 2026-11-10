/* Who recorded each transaction.
 *
 * Until now there was one field, `user_id`: "whose money" (the coloured tag).
 * It is filled only when the family turned on attribution to a family member,
 * and making a project shared resets it on every transaction in it. So nothing
 * knew who *recorded* a transaction.
 *
 * This is needed now for "make personal again" (Matan's decision, 28.9.2026):
 * the project goes back to belonging only to whoever opened it, and the
 * transactions other family members recorded in it stay, with a "נרשם ע״י אור"
 * mark, and with an upfront warning in the confirmation.
 *
 * `default auth.uid()`: every insert through the app runs with the user's JWT,
 * so the column fills without any code having to remember it. Recurring
 * occurrences copy it from the template explicitly (`materialize_recurring`);
 * otherwise they would be recorded under whoever happened to open the app that
 * day.
 *
 * Existing transactions stay `null`: there is no way to know who recorded
 * them, and "unknown" beats a guess. `on delete set null`: deleting an account
 * doesn't delete money that was recorded.
 */
alter table public.transactions
    add column if not exists created_by uuid
        references public.profiles(id) on delete set null
        default auth.uid();
