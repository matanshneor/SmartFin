-- An opening balance for a savings fund (relevant only when type='savings'),
-- so a fund's accumulated wealth also reflects money that existed before we
-- started tracking on the site.

alter table public.categories
    add column if not exists initial_balance numeric(10, 2);
