/* The receipts bucket's limits, and cleaning up files nobody points at.
 *
 * **The limits already exist on the server** (8MB and a list of types), but
 * they were set through the dashboard and never written as a migration. So a
 * build from scratch from the repo produces a bucket open to any size and any
 * file type, whose only protection is a check in Flask. This is exactly the
 * same class as `get_my_family_id` and the nine duplicated policies: the ledger
 * is complete, the content isn't.
 *
 * 6MB and not 8: that is what `/api/receipts/scan` actually enforces (app.py).
 * Two different numbers for the same limit means one of them was never tested.
 *
 * **And orphans.** `upload_receipt` uploads **before** a transaction exists,
 * and the path is deleted only in `delete_transaction`. Every abandoned scan
 * modal leaves a file no row points at and no code will delete, on a 1GB
 * bucket on the free plan. Already now, one of the two files in the bucket is
 * an orphan.
 */
update storage.buckets
   set file_size_limit   = 6 * 1024 * 1024,
       allowed_mime_types = array['image/jpeg', 'image/png', 'image/webp']
 where id = 'receipts';

create or replace function public.purge_orphan_receipts()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_deleted integer;
begin
    -- A 24-hour window before a file counts as an orphan: a scan just uploaded
    -- is still waiting for the user to tap "save", and the transaction that
    -- will point at it doesn't exist yet.

    delete from storage.objects o
     where o.bucket_id = 'receipts'
       and o.created_at < now() - interval '24 hours'
       and not exists (select 1 from public.transactions t
                        where t.receipt_path = o.name);
    get diagnostics v_deleted = row_count;
    return v_deleted;
end;
$$;

revoke all on function public.purge_orphan_receipts() from public, anon, authenticated;

select cron.unschedule('purge-orphan-receipts')
 where exists (select 1 from cron.job where jobname = 'purge-orphan-receipts');

select cron.schedule('purge-orphan-receipts', '57 3 * * *',
                     $cron$select public.purge_orphan_receipts()$cron$);
