-- Who originally created the project. Especially important for a project that
-- was personal and became shared: owner_id is then reset to NULL, and only
-- created_by keeps who may make it personal again.

alter table public.projects
    add column if not exists created_by uuid references public.profiles(id) on delete set null;

update public.projects set created_by = owner_id where owner_id is not null and created_by is null;
