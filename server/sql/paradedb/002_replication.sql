-- Keeps the ParadeDB copy of `nodes` up to date by logical replication, so ingesting in Supabase is all it takes.
-- The alternative is `python scripts/sync_paradedb.py` (run it after each ingest); use it too to check that the two agree.
-- Each block says which database it runs on. Run 001_nodes_bm25.sql on ParadeDB FIRST: the subscriber needs the table.
--
-- Tested: a plain PostgreSQL 17 publisher (standing in for Supabase) -> ParadeDB 0.26. The initial copy, inserts, edits,
-- deletes and a re-ingest-style delete-and-insert all reached the table and were searchable at once; edits to the columns
-- that are not published (embedding, tree, ordinal) cause no traffic and no error.
-- NOT tested against Supabase itself. Supabase's own guide (supabase.com/partners/integrations/paradedb) says to use the
-- DIRECT connection string (not the pooler) and that the ParadeDB host must reach Supabase over IPv6 unless you buy
-- Supabase's IPv4 add-on. If that is a problem on your network, use sync_paradedb.py instead.

-- ===== 1. On SUPABASE (SQL editor) ==========================================================================================
-- Only the five columns the search needs are published (a column list, PostgreSQL 15+), so the vectors are not copied.
create publication paradedb_pub;
alter publication paradedb_pub add table public.nodes (id, document_id, manual, title, text);
select pg_create_logical_replication_slot('paradedb_slot', 'pgoutput');

-- ===== 2. On PARADEDB (after 001_nodes_bm25.sql) ============================================================================
-- Fill in Supabase's DIRECT connection details (Project settings -> Database). The existing rows are copied at once.
--   create subscription paradedb_sub
--     connection 'host=db.YOUR-PROJECT.supabase.co port=5432 user=postgres password=YOUR-PASSWORD dbname=postgres sslmode=require'
--     publication paradedb_pub
--     with (copy_data = true, create_slot = false, slot_name = paradedb_slot);

-- ===== Check ================================================================================================================
-- On PARADEDB:  select subname, pid is not null as running from pg_stat_subscription;
--               select count(*) from nodes;               -- the same as in Supabase
-- Or:           python scripts/sync_paradedb.py --check

-- ===== Take it down (when you stop using it: an idle slot makes Supabase keep WAL files) =====================================
-- On PARADEDB:  alter subscription paradedb_sub disable;  alter subscription paradedb_sub set (slot_name = none);  drop subscription paradedb_sub;
-- On SUPABASE:  select pg_drop_replication_slot('paradedb_slot');  drop publication paradedb_pub;
