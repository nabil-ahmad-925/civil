#!/usr/bin/env python3
"""Copies the sections' searchable columns (id, document, manual, title, text) from Supabase to the ParadeDB database.

For when logical replication (sql/paradedb/002_replication.sql) is not set up or has to be checked. Reads Supabase
only; writes ParadeDB only. Run from server/ after sql/paradedb/001_nodes_bm25.sql.

    python scripts/sync_paradedb.py           # insert or update every section, remove the ones gone from Supabase
    python scripts/sync_paradedb.py --check   # only compare the counts per manual

Needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY and PARADEDB_URL, in the environment or in server/.env.
"""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.repository.supabase_repo import SupabaseRepository  # noqa: E402

UPSERT = (
    "insert into nodes (id, document_id, manual, title, text) values (%s, %s, %s, %s, %s) "
    "on conflict (id) do update set document_id = excluded.document_id, manual = excluded.manual, "
    "title = excluded.title, text = excluded.text"
)


def read_supabase(repo: SupabaseRepository) -> list[tuple[str, str, str, str, str]]:
    rows = []
    for doc in repo.list_documents():
        ids = [s.id for s in repo.list_node_summaries(doc.id)]
        rows += [(n.id, n.document_id, n.manual, n.title, n.text) for n in repo.get_nodes(ids)]
    return rows


def main() -> None:
    settings = get_settings()
    if not settings.paradedb_url:
        sys.exit("PARADEDB_URL is not set (environment or server/.env).")
    repo = SupabaseRepository(settings.supabase_url, settings.supabase_service_role_key, settings.storage_bucket)
    rows = read_supabase(repo)
    print(f"Supabase: {len(rows)} sections {dict(Counter(r[2] for r in rows))}")
    with psycopg.connect(settings.paradedb_url) as conn, conn.cursor() as cur:
        if "--check" in sys.argv:
            cur.execute("select manual, count(*) from nodes group by manual")
            there = dict(cur.fetchall())
            print(f"ParadeDB: {sum(there.values())} sections {there}")
            print("In sync." if there == dict(Counter(r[2] for r in rows)) else "NOT in sync: run without --check.")
            return
        cur.executemany(UPSERT, rows)
        cur.execute("delete from nodes where id <> all(%s::uuid[])", ([r[0] for r in rows],))
        removed = cur.rowcount
        cur.execute("select count(*) from nodes")
        print(f"ParadeDB: {cur.fetchone()[0]} sections now ({len(rows)} written, {removed} removed).")


if __name__ == "__main__":
    main()
