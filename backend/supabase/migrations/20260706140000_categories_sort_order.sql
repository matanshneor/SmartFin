-- A custom display order for categories (instead of a fixed alphabetical
-- order), set with the ▲▼ buttons in settings, and also affecting the category
-- grid in the add/edit transaction modal.

alter table public.categories add column if not exists sort_order integer;

-- Initial fill: alphabetical as today, but "אחר" always last in each
-- (family_id, type) group, matching what the modal already shows, so the
-- switch doesn't "jump".

with ranked as (
    select id,
           row_number() over (
               partition by family_id, type
               order by (name = 'אחר')::int, name
           ) as rn
    from public.categories
)
update public.categories c set sort_order = ranked.rn
from ranked where ranked.id = c.id;
