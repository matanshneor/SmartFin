-- An optional link between a transaction and a project. Deleting a project
-- doesn't delete its transactions, only detaches them from it (they go back to
-- being counted under their regular category).

alter table public.transactions
    add column if not exists project_id uuid references public.projects(id) on delete set null;
