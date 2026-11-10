-- A workplace per profile + full details (email, phone, workplace) in the
-- family member list on the settings page.

alter table public.profiles add column if not exists workplace text;

drop function if exists get_family_members(uuid);
create function get_family_members(p_family_id uuid)
returns table (id uuid, name text, avatar_initial text, email text, phone text, workplace text)
language sql stable security definer as $$
    select p.id, p.name, p.avatar_initial, u.email::text, p.phone, p.workplace
    from profiles p
    join auth.users u on u.id = p.id
    where p.family_id = p_family_id
    order by p.created_at;
$$;
