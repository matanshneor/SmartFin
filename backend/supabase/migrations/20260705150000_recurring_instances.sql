-- The recurring transactions engine: every monthly/weekly occurrence created
-- automatically is linked to the source transaction. Deleting the source
-- transaction keeps the occurrences already created (the money already moved)
-- but stops future creation.

alter table public.transactions
  add column if not exists recurring_parent_id uuid references public.transactions(id) on delete set null;

create index if not exists idx_tx_recurring_parent
  on public.transactions(recurring_parent_id) where recurring_parent_id is not null;
