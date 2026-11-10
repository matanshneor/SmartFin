/* The nightly cleanup of orphan receipts: removed.
 *
 * `purge_orphan_receipts` (20260922170000) failed on every one of its seven
 * runs: Supabase blocks direct deletes from `storage.objects` ("Direct deletion
 * from storage tables is not allowed. Use the Storage API instead"). It never
 * deleted a single file, and failed every night without anyone seeing.
 *
 * Since 29.9.2026 the app deletes by itself, through the storage API: an
 * abandoned scan (`/api/receipts/discard`), and bulk deletions (project,
 * removal, leaving). What can still slip through (a browser closed mid-scan) is
 * rare, and Matan chose (29.9.2026) not to build a cleanup for it with a
 * service-role key.
 *
 * The two other nightly cleanups (`purge-login-events`, `purge-owner-archive`)
 * delete from ordinary tables and work (7 out of 7), and stay.
 */
select cron.unschedule('purge-orphan-receipts')
 where exists (select 1 from cron.job where jobname = 'purge-orphan-receipts');

drop function if exists public.purge_orphan_receipts();
