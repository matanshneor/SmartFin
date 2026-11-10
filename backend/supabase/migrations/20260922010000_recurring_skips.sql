/* Dates deliberately skipped in a recurring series.
 *
 * The recurring engine fills in missing months, and it decides what is missing
 * by what exists. It has no way to tell "I haven't created this yet" from "this
 * was deleted on purpose": in both cases it sees a gap and fills it.
 *
 * The result: deleting a single occurrence undid itself on the next run.
 * Whoever cancelled Netflix for one month deleted September, and the next day
 * it was back. Until now this didn't surface because there was no way to delete
 * a single occurrence on purpose; once the dialog offers "only this one", it
 * becomes a feature that doesn't work.
 *
 * An array on the template row and not a separate table: the data belongs to
 * the series, is always read with it, and is never queried on its own. A table
 * would add a join to every engine run without adding any capability.
 */
alter table public.transactions
  add column if not exists recurring_skips date[];

comment on column public.transactions.recurring_skips is
  'Occurrence dates the user deleted on purpose; the engine will not recreate them';
