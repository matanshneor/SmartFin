/* Bounds for the transaction date.
 *
 * `date` passed from the request body to the database with no check at all:
 * not in Python and not in the column, which is `DATE NOT NULL DEFAULT
 * CURRENT_DATE` and nothing more. Postgres accepts any date between year 1 and
 * year 9999.
 *
 * That didn't stay theoretical because of the recurring engine: a template with
 * a start date in the year 1000 makes it create an occurrence for every month
 * since (up to the code's cap of 500 rows) in one call. 500 real transactions,
 * going into the summaries, the month strip and the comparison, forever.
 *
 * The range here is wide on purpose and meant to catch disasters, not to be
 * product policy: the tight check sits on the server (`_parse_date`), where it
 * can compare to "today". A CHECK constraint must be immutable, so it can't
 * know what today is.
 *
 * `recurring_end_date < date` is a plain contradiction: a "recurring
 * transaction" that ended before it started will never produce an occurrence,
 * and the user believes it is active.
 *
 * Verified before adding: the actual range in the database is 2026-07-01 to
 * 2026-09-20, zero rows outside the bounds and zero end-before-start.
 */
alter table public.transactions
  add constraint transactions_date_sane
  check (date >= date '2000-01-01' and date <= date '2100-01-01');

alter table public.transactions
  add constraint transactions_recurring_end_after_start
  check (recurring_end_date is null or recurring_end_date >= date);
