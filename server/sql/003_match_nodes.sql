-- Run once in the Supabase SQL editor. Nearest-neighbour search over nodes.embedding (cosine), used by
-- GET /api/search. Read-only: nothing is changed or deleted.
-- Keep vector(1536) in step with EMBEDDING_DIM.

create or replace function match_nodes(
  query_embedding vector(1536),
  match_count int default 5,
  filter_manual text default null
)
returns table (id uuid, similarity float)
language sql stable
as $$
  select n.id, (1 - (n.embedding <=> query_embedding))::float as similarity
  from nodes n
  where n.embedding is not null
    and (filter_manual is null or n.manual = filter_manual)
  order by n.embedding <=> query_embedding
  limit match_count;
$$;
