-- Updates the automatic profile creation so it also reads a phone number from
-- signup, next to the name it already read.

create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
    insert into public.profiles (id, name, phone)
    values (
        new.id,
        coalesce(new.raw_user_meta_data->>'name', 'משתמש'),
        new.raw_user_meta_data->>'phone'
    );
    return new;
end;
$$;
