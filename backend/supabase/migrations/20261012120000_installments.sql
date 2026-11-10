-- Installment transactions (Matan, 12.10): "a ₪4,800 fridge in 12 installments".
--
-- Installments are a monthly recurring transaction (`monthly_same`) with an end
-- date: the existing engine (`materialize_recurring`) creates an installment
-- every month. These three columns only say it is an installment series:
--   installments       : how many installments in total (on the template and
--                        on every occurrence)
--   installment_no     : this installment's number (the template is 1; an
--                        occurrence by month)
--   installment_total  : the full amount; the regular installment is computed
--                        from it, and the rounding (agorot that don't divide)
--                        goes into the first installment

alter table public.transactions
    add column if not exists installments smallint
        check (installments is null or installments between 2 and 60),
    add column if not exists installment_no smallint
        check (installment_no is null or installment_no >= 1),
    add column if not exists installment_total numeric(12, 2)
        check (installment_total is null or installment_total > 0);
