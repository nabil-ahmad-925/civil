-- Run once in the Supabase SQL editor.
-- The API uses the service_role key, which bypasses RLS. RLS is enabled with no policies
-- so the public anon key cannot read or write any of this.

-- Already ran an older version where sha256 was unique? Allow the same file to be uploaded again with:
--   alter table source_documents drop constraint if exists source_documents_sha256_key;
--
-- Already have the cross_refs table? Allow references to other documents with:
--   alter table cross_refs drop constraint if exists cross_refs_target_manual_check;
--   alter table cross_refs add constraint cross_refs_target_manual_check check (target_manual in ('PPM', 'HDM', 'CADD', 'OTHER'));
--   alter table cross_refs drop constraint if exists cross_refs_status_check;
--   alter table cross_refs add constraint cross_refs_status_check check (status in ('unresolved', 'resolved', 'awaiting_cross', 'external'));
--
-- Already have the tables? Add figures with sql/002_figures.sql, tables with sql/005_tables.sql and drawings with
-- sql/007_drawings.sql.
--
-- If you already ran an earlier version of this file (it had ingestion_jobs and batch_id), reset first:
--   drop table if exists cross_refs, node_conditions, nodes, ingestion_jobs, source_documents cascade;

create extension if not exists vector;

-- Private bucket holding the uploaded manual PDFs.
insert into storage.buckets (id, name, public)
values ('manuals', 'manuals', false)
on conflict (id) do nothing;

-- One row per uploaded PDF. sha256 + version are the "source hash/version" every node traces back to.
--   uploaded    in the bucket, not chunked yet
--   processing  being chunked right now
--   ingested    nodes + cross_refs saved (`summary` holds the counts)
--   failed      ingest raised; `error` says why. Run ingest again after fixing the cause.
create table if not exists source_documents (
  id            uuid primary key,
  manual        text not null,
  version       text not null,
  original_name text not null,
  size_bytes    bigint not null,
  sha256        text not null,
  storage_path  text not null,
  status        text not null check (status in ('uploaded', 'processing', 'ingested', 'failed')),
  error         text,
  summary       jsonb,
  uploaded_at   timestamptz not null default now()
);

-- Chunks of the manuals. `text` is the exact source text and is never rewritten.
-- `embedding` is only used to find the row; it is never shown.
create table if not exists nodes (
  id               uuid primary key,
  document_id      uuid not null references source_documents (id) on delete cascade,
  manual           text not null,
  chapter          text not null default '',
  section          text not null,
  title            text not null,
  parent_id        uuid references nodes (id) on delete set null,
  page_start       int not null,
  page_end         int not null,
  ordinal          int not null,
  text             text not null,
  embedding        vector(1536),   -- keep in step with EMBEDDING_DIM
  -- Filled by the tagging stage (not built yet).
  classification   text check (classification in ('requirement', 'guidance', 'non_governing')),
  requirement_class text check (requirement_class in ('boldface', 'underlined', 'approval_required', 'permissive')),
  tags             text[] not null default '{}',
  created_at       timestamptz not null default now()
);
create index if not exists nodes_document_idx on nodes (document_id, ordinal);
create index if not exists nodes_manual_section_idx on nodes (manual, section);
create index if not exists nodes_embedding_idx on nodes using hnsw (embedding vector_cosine_ops);

-- Applicability conditions and exceptions live apart from the requirement, linked to it.
-- Filled by the tagging stage (not built yet).
create table if not exists node_conditions (
  id              uuid primary key default gen_random_uuid(),
  node_id         uuid not null references nodes (id) on delete cascade,
  kind            text not null check (kind in ('applicability', 'exception')),
  text            text not null,
  input           text,
  required_inputs text[] not null default '{}'
);
create index if not exists node_conditions_node_idx on node_conditions (node_id);

-- Figures captured from the manuals: the caption and where it is. The PNG itself is in the bucket
-- (image_path = figures/<document id>/<label>.png).
create table if not exists figures (
  id          uuid primary key,
  document_id uuid not null references source_documents (id) on delete cascade,
  manual      text not null,
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

-- Tables captured from the manuals: the caption, the cells and where it is. The PNG itself is in the bucket
-- (image_path = tables/<document id>/<label>.png). The table's text also stays in its node.
create table if not exists manual_tables (
  id          uuid primary key,
  document_id uuid not null references source_documents (id) on delete cascade,
  manual      text not null,
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

-- Every "see Section X" style mention, saved verbatim while chunking.
--   unresolved      same-manual target not linked (yet, or never found)
--   awaiting_cross  points at another manual; waits for the cross-manual pass
--   resolved        linked to a real node
--   external        points at a document that is not one of the three manuals (named in target_label)
create table if not exists cross_refs (
  id             uuid primary key,
  document_id    uuid not null references source_documents (id) on delete cascade,
  from_node_id   uuid not null references nodes (id) on delete cascade,
  mention        text not null,
  target_manual  text not null check (target_manual in ('PPM', 'HDM', 'CADD', 'OTHER')),
  target_section text,
  target_label   text not null,
  target_node_id uuid references nodes (id) on delete set null,
  target_figure_id uuid references figures (id) on delete set null,   -- set instead of target_node_id for a figure
  target_table_id  uuid references manual_tables (id) on delete set null,   -- ... or for a table
  status         text not null check (status in ('unresolved', 'resolved', 'awaiting_cross', 'external')),
  resolved_by    text check (resolved_by in ('internal_pass', 'cross_manual_pass'))
);
create index if not exists cross_refs_document_status_idx on cross_refs (document_id, status);
create index if not exists cross_refs_from_node_idx on cross_refs (from_node_id);

alter table source_documents enable row level security;
alter table nodes            enable row level security;
alter table node_conditions  enable row level security;
alter table cross_refs       enable row level security;
alter table figures          enable row level security;
alter table manual_tables    enable row level security;

-- Drawings: what the drawing reader found on each sheet. The images are in the bucket under drawings/<drawing id>/.
-- Everything here was proposed by a model: review_state stays 'proposed' until an engineer verifies it.
create table if not exists drawings (
  id          uuid primary key,
  name        text not null,
  status      text not null check (status in ('uploaded', 'processing', 'processed', 'failed')),
  error       text,
  summary     jsonb,
  uploaded_at timestamptz not null default now()
);

create table if not exists drawing_sheets (
  id            uuid primary key,
  drawing_id    uuid not null references drawings (id) on delete cascade,
  number        int not null,
  original_name text not null,
  image_path    text not null,
  content_type  text not null,
  sha256        text not null,
  size_bytes    bigint not null,
  width         int not null,
  height        int not null,
  warnings      jsonb not null default '[]',
  failures      jsonb not null default '[]',
  unique (drawing_id, number)
);

create table if not exists drawing_features (
  id           uuid primary key,
  drawing_id   uuid not null references drawings (id) on delete cascade,
  sheet_id     uuid not null references drawing_sheets (id) on delete cascade,
  type         text not null,
  label        text not null default '',
  bbox         jsonb,
  confidence   text not null check (confidence in ('high', 'medium', 'low')),
  found_by     text not null check (found_by in ('sheet', 'tile')),
  related      jsonb not null default '[]',
  review_state text not null default 'proposed' check (review_state in ('proposed', 'verified'))
);
create index if not exists drawing_features_drawing_idx on drawing_features (drawing_id);

create table if not exists drawing_notes (
  id           uuid primary key,
  drawing_id   uuid not null references drawings (id) on delete cascade,
  sheet_id     uuid not null references drawing_sheets (id) on delete cascade,
  text         text not null,
  bbox         jsonb,
  confidence   text not null check (confidence in ('high', 'medium', 'low')),
  scope        text not null check (scope in ('feature', 'sheet', 'unbound')),
  found_by     text not null check (found_by in ('sheet', 'tile')),
  review_state text not null default 'proposed' check (review_state in ('proposed', 'verified'))
);
create index if not exists drawing_notes_drawing_idx on drawing_notes (drawing_id);

create table if not exists drawing_note_bindings (
  id           uuid primary key,
  drawing_id   uuid not null references drawings (id) on delete cascade,
  note_id      uuid not null references drawing_notes (id) on delete cascade,
  feature_id   uuid not null references drawing_features (id) on delete cascade,
  evidence     text not null check (evidence in ('leader_line', 'adjacent', 'label')),
  confidence   text not null check (confidence in ('high', 'medium', 'low')),
  image_path   text,
  review_state text not null default 'proposed' check (review_state in ('proposed', 'verified'))
);
create index if not exists drawing_note_bindings_drawing_idx on drawing_note_bindings (drawing_id);

create table if not exists drawing_facts (
  id            uuid primary key,
  drawing_id    uuid not null references drawings (id) on delete cascade,
  sheet_id      uuid not null references drawing_sheets (id) on delete cascade,
  feature_id    uuid references drawing_features (id) on delete cascade,
  term          text not null,
  status        text not null check (status in ('FOUND', 'NOT_FOUND')),
  kind          text not null check (kind in ('direct', 'semantic')),
  value         jsonb,
  unit          text not null default '',
  verbatim_text text not null default '',
  bbox          jsonb,
  confidence    text not null check (confidence in ('high', 'medium', 'low')),
  confirmation  text not null check (confirmation in ('confirmed', 'disputed', 'not_checked')),
  image_path    text,
  produced_by   text not null,
  review_state  text not null default 'proposed' check (review_state in ('proposed', 'verified'))
);
create index if not exists drawing_facts_drawing_idx on drawing_facts (drawing_id);

alter table drawings enable row level security;
alter table drawing_sheets enable row level security;
alter table drawing_features enable row level security;
alter table drawing_notes enable row level security;
alter table drawing_note_bindings enable row level security;
alter table drawing_facts enable row level security;
