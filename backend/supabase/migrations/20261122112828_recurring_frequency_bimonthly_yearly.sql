-- Two more recurring frequencies (Matan, 22.11): every two months (arnona,
-- water, electricity) and once a year (insurance, licence fees), on the
-- transaction's own day of the month. Widens the CHECK constraint; existing
-- rows are untouched.
alter table public.transactions
    drop constraint if exists transactions_recurring_frequency_check;

alter table public.transactions
    add constraint transactions_recurring_frequency_check
    check (recurring_frequency in ('monthly_same', 'monthly_1', 'monthly_15', 'weekly', 'biweekly',
                                   'bimonthly', 'yearly'));
