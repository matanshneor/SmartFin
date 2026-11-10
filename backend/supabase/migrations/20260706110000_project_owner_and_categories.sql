-- A personal project (owner_id set) versus a shared one (NULL). Tracking flags
-- per type: which transaction types (expense/income/savings) are relevant to
-- this project.

alter table public.projects
    add column if not exists owner_id      uuid references public.profiles(id) on delete set null,
    add column if not exists track_expense boolean not null default true,
    add column if not exists track_income  boolean not null default false,
    add column if not exists track_savings boolean not null default false;

-- Categories specific to a project, separate from the family's regular
-- categories, so editing here doesn't affect the rest of the site. Copied by
-- default from the family's categories at creation, and editable
-- independently from then on.

create table if not exists public.project_categories (
    id         uuid primary key default gen_random_uuid(),
    project_id uuid not null references public.projects(id) on delete cascade,
    family_id  uuid not null references public.families(id) on delete cascade,
    name       text not null,
    icon       text not null default '📦',
    type       text not null check (type in ('expense', 'income', 'savings')),
    created_at timestamptz not null default now()
);

alter table public.project_categories enable row level security;

create policy project_categories_family_select on public.project_categories
    for select using (
        family_id in (select family_id from public.profiles where id = auth.uid())
    );

create policy project_categories_family_insert on public.project_categories
    for insert with check (
        family_id in (select family_id from public.profiles where id = auth.uid())
    );

create policy project_categories_family_update on public.project_categories
    for update using (
        family_id in (select family_id from public.profiles where id = auth.uid())
    ) with check (
        family_id in (select family_id from public.profiles where id = auth.uid())
    );

create policy project_categories_family_delete on public.project_categories
    for delete using (
        family_id in (select family_id from public.profiles where id = auth.uid())
    );

-- Links a transaction to the project's own category. When a transaction is
-- attached to a project it uses this column instead of category_id (which
-- stays empty in that case).

alter table public.transactions
    add column if not exists project_category_id uuid references public.project_categories(id) on delete set null;
