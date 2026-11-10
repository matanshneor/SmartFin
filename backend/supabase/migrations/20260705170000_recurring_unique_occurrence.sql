-- Prevents creating a duplicate occurrence of a recurring transaction (e.g.
-- when two family members open the site at the same time and the materializer
-- runs twice in parallel).

create unique index if not exists uq_tx_recurring_occurrence
  on public.transactions(recurring_parent_id, date)
  where recurring_parent_id is not null;
