-- Run once in the Supabase SQL editor, on a database that already has the schema.
-- Adds the figures table (one row per figure captured from a manual; the PNG is in the bucket under
-- figures/<document id>/) and lets a cross-reference point at a figure. Nothing is changed or deleted.

create table if not exists figures (
  id          uuid primary key,
  document_id uuid not null references source_documents (id) on delete cascade,
  manual      text not null check (manual in ('PPM', 'HDM', 'CADD')),
  node_id     uuid references nodes (id) on delete set null,
  label       text not null,
  number      text not null,
  caption     text not null,
  page        int not null,
  bbox        jsonb not null,
  image_path  text not null,
  created_at  timestamptz not null default now()
);
create index if not exists figures_document_idx on figures (document_id, page);
create index if not exists figures_number_idx on figures (document_id, number);
create index if not exists figures_node_idx on figures (node_id);
alter table figures enable row level security;

alter table cross_refs add column if not exists target_figure_id uuid references figures (id) on delete set null;
create index if not exists cross_refs_target_figure_idx on cross_refs (target_figure_id);
