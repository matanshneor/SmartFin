-- A private bucket for receipt images. Every file's path is
-- {family_id}/{uuid}.{ext}; the access policy compares the root folder in the
-- path to the signed-in user's family, so each family sees and uploads only to
-- its own folder.

insert into storage.buckets (id, name, public)
values ('receipts', 'receipts', false)
on conflict (id) do nothing;

create policy receipts_family_select on storage.objects
    for select to authenticated using (
        bucket_id = 'receipts'
        and (storage.foldername(name))[1] = (
            select family_id::text from public.profiles where id = auth.uid()
        )
    );

create policy receipts_family_insert on storage.objects
    for insert to authenticated with check (
        bucket_id = 'receipts'
        and (storage.foldername(name))[1] = (
            select family_id::text from public.profiles where id = auth.uid()
        )
    );

create policy receipts_family_delete on storage.objects
    for delete to authenticated using (
        bucket_id = 'receipts'
        and (storage.foldername(name))[1] = (
            select family_id::text from public.profiles where id = auth.uid()
        )
    );
