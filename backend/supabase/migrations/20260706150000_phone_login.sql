-- Allows signing in with a phone number too (next to email): a unique index on
-- phone (applied only when it isn't NULL; old accounts without a phone are
-- unaffected), and a lookup function letting anon (before sign-in) find the
-- email associated with a given phone.
-- The phone is stored normalised (digits only) by the backend before writing,
-- so the comparison here is simple and reliable.

create unique index if not exists profiles_phone_unique_idx
    on public.profiles (phone) where phone is not null;

create or replace function public.email_for_phone(p_phone text)
returns text
language sql
security definer
set search_path = public
stable
as $$
    select u.email
    from public.profiles p
    join auth.users u on u.id = p.id
    where p.phone = p_phone
    limit 1;
$$;

grant execute on function public.email_for_phone(text) to anon, authenticated;
