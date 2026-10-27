-- Run once on the ParadeDB database (NOT on Supabase: Supabase has no pg_search). See server/docs/INGESTION_FLOW.md, "ParadeDB".
-- Keeps a copy of the sections' searchable columns and answers the BM25 keyword search. The vectors, the section tree and
-- everything else stay in Supabase; only id, document, manual, title and text are copied here.
--
-- How the copy is kept: logical replication from Supabase (002_replication.sql), or `python scripts/sync_paradedb.py`.
-- Written for and tested on pg_search 0.26 (CREATE INDEX ... USING bm25, the ||| match operator, pdb.score).

create extension if not exists pg_search;

-- Same name and columns as in Supabase (replication needs them to match). No foreign keys: documents are not copied.
create table if not exists nodes (
  id          uuid primary key,
  document_id uuid not null,
  manual      text not null,
  title       text not null,
  text        text not null
);

-- One BM25 index per table. id is the key field (first, unique, not tokenized; before 0.26 add `with (key_field = 'id')`). Title and text are lower-cased and stemmed
-- in English ("standards" finds "standard"); manual is listed so it can be filtered.
create index if not exists nodes_bm25_idx on nodes
using bm25 (
  id,
  (title::pdb.simple('stemmer=english')),
  (text::pdb.simple('stemmer=english')),
  manual
);

-- (id, score), best first, optionally one manual. The Python side removes the filler words of the question first
-- (app/repository/keywords.py) and calls this with what is left, e.g. 'dental standards cleaning'.
-- A section matches if it has ANY of the words (|||); BM25 weighs rare words more, saturates repeats and favours shorter
-- sections. A word in the heading counts twice as much as one in the text. The score is averaged over the words of the
-- question, so a longer question does not score higher just for having more of them: that is the scale
-- Settings.keyword_min_score_bm25 is calibrated on.
create or replace function match_nodes_bm25(
  query_text text,
  match_count int default 10,
  filter_manual text default null
)
returns table (id uuid, score float)
language sql stable
as $$
  with in_text as (
    select n.id, pdb.score(n.id) as s
    from nodes n
    where n.text ||| query_text
      and (filter_manual is null or n.manual = filter_manual)
    order by pdb.score(n.id) desc, n.id
    limit match_count * 3
  ),
  in_title as (
    select n.id, pdb.score(n.id) as s
    from nodes n
    where n.title ||| query_text
      and (filter_manual is null or n.manual = filter_manual)
    order by pdb.score(n.id) desc, n.id
    limit match_count * 3
  )
  select coalesce(x.id, t.id),
         ((coalesce(x.s, 0) + 2.0 * coalesce(t.s, 0)) / greatest(cardinality(string_to_array(trim(query_text), ' ')), 1))::float
  from in_text x
  full join in_title t on t.id = x.id
  order by 2 desc, 1
  limit match_count;
$$;
