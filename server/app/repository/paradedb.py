"""The BM25 keyword search, on a separate ParadeDB database (sql/paradedb/001_nodes_bm25.sql).

Supabase has no pg_search, so the sections' texts are copied to a ParadeDB database (logical replication, or
scripts/sync_paradedb.py) and asked there over a plain Postgres connection, which PostgREST cannot do.
The connection is opened on first use and kept while the Lambda container lives; a stale one is replaced once.
"""

import logging
import threading
from typing import Any, Callable

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_MS = 5000
# Explicit casts: the driver sends a small Python int as int2, and Postgres will not pick the function for it otherwise.
QUERY = "select id::text, score from match_nodes_bm25(%s::text, %s::int, %s::text)"


def _connect(dsn: str) -> Any:
    import psycopg  # only needed when this backend is on

    return psycopg.connect(dsn, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS, options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}")


class ParadeDbKeywordSearch:
    def __init__(self, dsn: str, connect: Callable[[], Any] | None = None) -> None:
        if not dsn:
            raise RuntimeError("KEYWORD_BACKEND=paradedb needs PARADEDB_URL (a Postgres connection string).")
        self._connect = connect or (lambda: _connect(dsn))
        self._conn: Any = None
        self._lock = threading.Lock()

    def _drop(self) -> None:
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:  # noqa: BLE001 - already broken; the next call opens a new one
            pass
        self._conn = None

    def match(self, query_text: str, k: int, manual: str | None = None) -> list[tuple[str, float]]:
        """(section id, BM25 score), best first. An unreachable database gives [] (logged): the other searches carry on."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self._conn is None or getattr(self._conn, "closed", False):
                        self._conn = self._connect()
                    with self._conn.cursor() as cur:
                        cur.execute(QUERY, (query_text, k, manual))
                        return [(row[0], float(row[1])) for row in cur.fetchall()]
                except Exception as exc:  # noqa: BLE001 - one search of three: never take the answer down with it
                    self._drop()  # a container kept warm for minutes may hold a connection the server has closed
                    if attempt == 2:
                        log.error("ParadeDB keyword search failed (%s: %s); answering without it", type(exc).__name__, exc)
        return []
