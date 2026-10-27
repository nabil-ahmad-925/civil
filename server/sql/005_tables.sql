-- Run once in the Supabase SQL editor, on a database that already has the figures table (sql/002_figures.sql).
-- Adds manual_tables (one row per table captured from a manual; the PNG is in the bucket under
-- image_path = tables/<document id>/<label>.png) and lets a reference point at one.
-- Re-ingest each document afterwards so its tables are captured and the references to them are linked.

create table if not exists manual_tables (
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
  rows        jsonb not null,
  created_at  timestamptz not null default now()
);
create index if not exists manual_tables_document_idx on manual_tables (document_id, page);
create index if not exists manual_tables_number_idx on manual_tables (document_id, number);
create index if not exists manual_tables_node_idx on manual_tables (node_id);

alter table manual_tables enable row level security;

alter table cross_refs add column if not exists target_table_id uuid references manual_tables (id) on delete set null;
