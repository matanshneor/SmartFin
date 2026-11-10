-- Family settings: a flexible settings object per family (attribution to
-- family members by type, overage alert threshold, showing the workplace).
-- The defaults live in the server code: a family with an empty object gets the
-- site's historical behaviour.

alter table public.families
  add column if not exists settings jsonb not null default '{}'::jsonb;
