-- Run once in the Supabase SQL editor, on a database that already has the rest of the schema.
-- Adds what the drawing reader stores (app/drawings/). The images are in the bucket under drawings/<drawing id>/.
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
