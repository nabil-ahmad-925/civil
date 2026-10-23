-- Run once in the Supabase SQL editor (after 003). Two more ways to find sections, next to the vector search:
--   match_nodes_text   keywords: the words of the question, rare ones counting more, plus a bonus for the exact phrase
--   match_nodes_title  headings: a section number ("1-1.8") or a heading typed in full or in part
-- GET /api/search and the chat merge the three lists, so an exact name or section number is found even when the
-- meaning-based search ranks it low. Read-only functions; the only change to data is a generated search column.

create extension if not exists pg_trgm;

-- Search text of each node: the heading counts more than the body. Kept up to date by Postgres itself.
alter table nodes add column if not exists fts tsvector generated always as (
  setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('english', coalesce(text, '')), 'B')
) stored;
create index if not exists nodes_fts_idx on nodes using gin (fts);

create or replace function match_nodes_text(
  query_text text,
  match_count int default 10,
  filter_manual text default null
)
returns table (id uuid, rank float)
language sql stable
as $$
  with words as (
    -- every meaningful word of the question, stemmed (stop words fall away by themselves), and in how many
    -- sections it occurs
    select lex,
           (select count(*) from nodes d where d.fts @@ to_tsquery('simple', quote_literal(lex))) as df
    from unnest(tsvector_to_array(to_tsvector('english', query_text))) as lex
    -- words people use to ASK ("I want to know the article where ... info ...") say nothing about the topic
    where lex <> all (array[
      'want', 'know', 'tell', 'show', 'give', 'get', 'got', 'need', 'look', 'find', 'pleas', 'explain', 'describ',
      'descript', 'articl', 'info', 'inform', 'question', 'answer', 'section', 'chapter', 'page', 'topic', 'mention',
      'refer', 'locat', 'say', 'said'
    ])
  ),
  weighted as (
    -- a rare word ("topographic") says more than a common one ("file"): weight = how rare it is, with a small floor
    select lex, ln(((select count(*) from nodes)::float + 1) / (df + 1)) + 0.1 as w
    from words
    where df > 0
  ),
  total as (select sum(w) as s from weighted),
  phrase as (select phraseto_tsquery('english', query_text) as q)
  -- rank: the share of the question's weight that the section contains (0..1), plus 1 when the whole question is
  -- in the section as an exact phrase (a short question such as a name or a heading)
  select n.id,
         (sum(weighted.w) / total.s
          + case when numnode(phrase.q) > 0 and n.fts @@ phrase.q then 1.0 else 0.0 end)::float as rank
  from nodes n
  join weighted on n.fts @@ to_tsquery('simple', quote_literal(weighted.lex))
  cross join total
  cross join phrase
  where filter_manual is null or n.manual = filter_manual
  group by n.id, total.s, phrase.q
  order by rank desc, n.id
  limit match_count;
$$;

create or replace function match_nodes_title(
  query_text text,
  match_count int default 10,
  filter_manual text default null
)
returns table (id uuid, score float)
language sql stable
as $$
  with q as (
    select
      lower(regexp_replace(query_text, '^[\s§#]+', '')) as cleaned,
      coalesce((select array_agg(distinct m[1]) from regexp_matches(query_text, '(\d+(?:[-.]\d+)+[A-Za-z]?)', 'g') as m), '{}'::text[]) as numbers
  ),
  scored as (
    select n.id, n.ordinal, length(n.title) as title_length, q.cleaned,
           (n.section = any(q.numbers)) as same_number,
           word_similarity(lower(n.title), q.cleaned) as title_in_query,   -- the whole heading appears in the question
           word_similarity(q.cleaned, lower(n.title)) as query_in_title    -- the question is part of a heading
    from nodes n, q
    where filter_manual is null or n.manual = filter_manual
  )
  select id, greatest(case when same_number then 1.0 else 0.0 end, title_in_query, query_in_title)::float as score
  from scored
  -- A short heading such as "Details" appears inside many questions without being what is asked for, so the
  -- "heading inside the question" test needs a heading of some length; the other way round needs a real query.
  where same_number
     or (title_in_query >= 0.8 and title_length >= 15)
     or (query_in_title >= 0.8 and length(cleaned) >= 4)
  order by score desc, ordinal
  limit match_count;
$$;
