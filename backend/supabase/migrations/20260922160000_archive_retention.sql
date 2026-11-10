/* "Permanently delete the account" didn't delete anything.
 *
 * `delete_my_account` archives the profile (name, email, phone, workplace), the
 * family, the categories and the projects; and the trigger
 * `archive_deleted_transaction` copies **every** transaction ever deleted. All
 * into `owner_archive`, with no code anywhere in the repo cleaning it. Forever.
 *
 * The privacy policy promised "deleting the account... removes the profile and
 * the data associated with it" and "data is kept as long as the account is
 * active". Neither claim was true. In an app hosted in the EU that processes
 * financial data, that is a direct failure of Article 17 (right to erasure) and
 * 5(1)(e) (storage limitation), and worse, because the promise was written
 * explicitly.
 *
 * At the same time it is also an amplifier: no rate limit prevents
 * "add-delete-repeat", and every such cycle added a permanent row to a 500MB
 * database.
 *
 * 30 days is the window: enough to help someone who deleted by mistake and got
 * in touch, and short enough that the promise in the policy is true. The policy
 * was updated to say so explicitly.
 */
create or replace function public.purge_expired_archive()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_deleted integer;
begin
    delete from public.owner_archive
     where archived_at < now() - interval '30 days';
    get diagnostics v_deleted = row_count;
    return v_deleted;
end;
$$;

revoke all on function public.purge_expired_archive() from public, anon, authenticated;

-- `login_events` on the same logic. The policy declares "recording sign-ins...
-- for security and abuse detection", without saying for how long, and with no
-- cleanup at all. 90 days is enough for an investigation and doesn't turn the
-- table into a permanent movement log of every user.

create or replace function public.purge_expired_login_events()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_deleted integer;
begin
    delete from public.login_events
     where created_at < now() - interval '90 days';
    get diagnostics v_deleted = row_count;
    return v_deleted;
end;
$$;

revoke all on function public.purge_expired_login_events() from public, anon, authenticated;

create index if not exists owner_archive_archived_at_idx
    on public.owner_archive (archived_at);

/* The schedule. `pg_cron` and not cleanup from inside the app: a cleanup that
 * depends on someone signing in is exactly what stops working when nobody
 * signs in, and the case where the table most needs cleaning is an account
 * that was deleted and nobody comes back to. */
create extension if not exists pg_cron;

select cron.unschedule('purge-owner-archive')
 where exists (select 1 from cron.job where jobname = 'purge-owner-archive');
select cron.unschedule('purge-login-events')
 where exists (select 1 from cron.job where jobname = 'purge-login-events');

select cron.schedule('purge-owner-archive', '17 3 * * *',
                     $cron$select public.purge_expired_archive()$cron$);
select cron.schedule('purge-login-events',  '37 3 * * *',
                     $cron$select public.purge_expired_login_events()$cron$);
