/* A global cap on receipt scans.
 *
 * `/api/receipts/scan` is the only route in the app that costs real money: it
 * calls OpenAI. The existing quota is 100 scans **per family**, and signup is
 * open: every new account gets a family, and with it a fresh quota. So there
 * was no cap at all on the total.
 *
 * The count must cross families, and RLS on receipt_scans limits every user to
 * their own family, hence a function and not a query from the app. It returns
 * one number only: how many scans were made in total since a date. It contains
 * no data of any family.
 *
 * `p_since` comes from the app and isn't computed here, so it is exactly the
 * same month boundary as the per-family count: there it is derived from Israel
 * time (backend/clock.py), and `now()` on the server is UTC. Without this the
 * two counts would reset at different hours.
 */
create or replace function public.receipt_scans_global_since(p_since timestamptz)
returns integer
language sql
security definer
stable
set search_path = public
as $$
    select count(*)::int from public.receipt_scans where created_at >= p_since;
$$;

revoke all on function public.receipt_scans_global_since(timestamptz) from public, anon;
grant execute on function public.receipt_scans_global_since(timestamptz) to authenticated;

-- The count runs before every scan. With 53 rows it doesn't matter; with
-- 50,000 it does.

create index if not exists receipt_scans_created_at_idx on public.receipt_scans (created_at);
