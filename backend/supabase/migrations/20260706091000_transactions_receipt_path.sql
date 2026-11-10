-- The storage path of the receipt image attached to a transaction (private
-- "receipts" bucket).

alter table public.transactions
    add column if not exists receipt_path text;
