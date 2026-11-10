-- Project budgets (e.g. "a trip to Japan"): spanning months, with an optional
-- budget target.

create table if not exists public.projects (
    id            uuid primary key default gen_random_uuid(),
    family_id     uuid not null references public.families(id) on delete cascade,
    name          text not null,
    budget_target numeric(10, 2),
    archived      boolean not null default false,
    created_at    timestamptz not null default now()
);

alter table public.projects enable row level security;

create policy projects_family_select on public.projects
    for select using (
        family_id in (select family_id from public.profiles where id = auth.uid())
    );

create policy projects_family_insert on public.projects
    for insert with check (
        family_id in (select family_id from public.profiles where id = auth.uid())
    );

create policy projects_family_update on public.projects
    for update using (
        family_id in (select family_id from public.profiles where id = auth.uid())
    ) with check (
        family_id in (select family_id from public.profiles where id = auth.uid())
    );

create policy projects_family_delete on public.projects
    for delete using (
        family_id in (select family_id from public.profiles where id = auth.uid())
    );
