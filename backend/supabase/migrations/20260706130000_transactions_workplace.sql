-- The workplace is recorded ("frozen") on an income transaction when it is
-- created, so a future workplace change doesn't accidentally rewrite what is
-- already shown on existing history. NULL = an old transaction from before this
-- feature, which keeps falling back to a live lookup from the profile as today.

alter table public.transactions add column if not exists workplace text;
