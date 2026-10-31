"""Supabase (Postgres + Storage) implementation. Uses the service_role key: server-side only."""

import logging
from typing import Any, Callable

import httpx
from postgrest.exceptions import APIError
from supabase import Client, ClientOptions, create_client

from ..models import CrossRef, Document, Figure, ManualTable, Node, NodeCondition, NodeSummary, RefStatus
from .keywords import keyword_terms
from .paradedb import ParadeDbKeywordSearch

log = logging.getLogger(__name__)

# PostgREST returns at most 1000 rows per request, so reads are paged.
PAGE = 1000
# Columns read back for Browse. The embedding is left out: it is large and never shown.
SUMMARY_COLS = "id,document_id,manual,chapter,section,title,parent_id,page_start,page_end,ordinal,classification"
NODE_COLS = SUMMARY_COLS + ",text,requirement_class,tags"

# Rows per write request. Nodes carry embeddings, so keep these batches modest.
NODE_BATCH = 100
REF_BATCH = 500


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


class SupabaseRepository:
    # Who answers the keyword search: "fts" (the SQL function of sql/004_hybrid_search.sql, in Supabase) or "paradedb"
    # (BM25 on a separate ParadeDB database).
    keyword_backend = "fts"
    keyword_search: ParadeDbKeywordSearch | None = None

    def __init__(
        self, url: str, service_role_key: str, bucket: str, keyword_backend: str = "fts", paradedb_url: str = ""
    ) -> None:
        if not url or not service_role_key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set (see .env.example).")
        # HTTP/1.1 with short keep-alive: pooled HTTP/2 connections get dropped by the server while idle, and the
        # next request then fails with "Server disconnected".
        http = httpx.Client(
            http2=False,
            timeout=120,
            limits=httpx.Limits(max_keepalive_connections=10, keepalive_expiry=5),
            transport=httpx.HTTPTransport(retries=2),
        )
        self.client: Client = create_client(url, service_role_key, options=ClientOptions(httpx_client=http))
        self.bucket = bucket
        self.keyword_backend = keyword_backend
        if keyword_backend == "paradedb":
            self.keyword_search = ParadeDbKeywordSearch(paradedb_url)

    def _table(self, name: str):
        return self.client.table(name)

    def _paged(self, build: Callable[[], Any]) -> list[dict]:
        rows: list[dict] = []
        start = 0
        while True:
            page = build().range(start, start + PAGE - 1).execute().data
            rows.extend(page)
            if len(page) < PAGE:
                return rows
            start += PAGE

    # --- source documents -------------------------------------------------
    def list_documents(self) -> list[Document]:
        rows = self._table("source_documents").select("*").order("uploaded_at", desc=True).execute().data
        return [Document(**r) for r in rows]

    def get_document(self, doc_id: str) -> Document | None:
        rows = self._table("source_documents").select("*").eq("id", doc_id).limit(1).execute().data
        return Document(**rows[0]) if rows else None

    def insert_document(self, doc: Document) -> None:
        self._table("source_documents").insert(doc.model_dump(mode="json")).execute()

    def update_document(self, doc_id: str, **fields: object) -> None:
        self._table("source_documents").update(fields).eq("id", doc_id).execute()

    def delete_document(self, doc_id: str) -> None:
        # nodes, figures and cross_refs go with it (ON DELETE CASCADE)
        self._table("source_documents").delete().eq("id", doc_id).execute()

    # --- PDF files --------------------------------------------------------
    def upload_file(self, storage_path: str, local_path: str) -> None:
        with open(local_path, "rb") as f:
            self.client.storage.from_(self.bucket).upload(
                storage_path, f, {"content-type": "application/pdf", "upsert": "true"}
            )

    def download_file(self, storage_path: str) -> bytes:
        return self.client.storage.from_(self.bucket).download(storage_path)

    def delete_file(self, storage_path: str) -> None:
        self.client.storage.from_(self.bucket).remove([storage_path])

    def upload_bytes(self, storage_path: str, data: bytes, content_type: str) -> None:
        self.client.storage.from_(self.bucket).upload(storage_path, data, {"content-type": content_type, "upsert": "true"})

    def signed_url(self, storage_path: str, seconds: int) -> str:
        return self.client.storage.from_(self.bucket).create_signed_url(storage_path, seconds)["signedURL"]

    def delete_files(self, storage_paths: list[str]) -> None:
        for batch in _chunks(storage_paths, REF_BATCH):
            self.client.storage.from_(self.bucket).remove(batch)

    # --- figures ----------------------------------------------------------
    def insert_figures(self, figures: list[Figure]) -> None:
        for batch in _chunks(figures, REF_BATCH):
            self._table("figures").insert([f.model_dump(mode="json") for f in batch]).execute()

    def list_figures(self, doc_id: str) -> list[Figure]:
        rows = self._paged(lambda: self._table("figures").select("*").eq("document_id", doc_id).order("page").order("label"))
        return [Figure(**r) for r in rows]

    def get_figure(self, figure_id: str) -> Figure | None:
        rows = self._table("figures").select("*").eq("id", figure_id).limit(1).execute().data
        return Figure(**rows[0]) if rows else None

    def delete_figures_for_document(self, doc_id: str) -> None:
        self._table("figures").delete().eq("document_id", doc_id).execute()

    def figure_index(self, doc_id: str) -> dict[str, str]:
        rows = self._paged(
            lambda: self._table("figures").select("id,number,page").eq("document_id", doc_id).order("page").order("id")
        )
        index: dict[str, str] = {}
        for r in rows:
            index.setdefault(r["number"], r["id"])
        return index

    # --- tables -----------------------------------------------------------
    def insert_tables(self, tables: list[ManualTable]) -> None:
        for batch in _chunks(tables, REF_BATCH):
            self._table("manual_tables").insert([t.model_dump(mode="json") for t in batch]).execute()

    def list_tables(self, doc_id: str) -> list[ManualTable]:
        rows = self._paged(
            lambda: self._table("manual_tables").select("*").eq("document_id", doc_id).order("page").order("label")
        )
        return [ManualTable(**r) for r in rows]

    def get_table(self, table_id: str) -> ManualTable | None:
        rows = self._table("manual_tables").select("*").eq("id", table_id).limit(1).execute().data
        return ManualTable(**rows[0]) if rows else None

    def delete_tables_for_document(self, doc_id: str) -> None:
        self._table("manual_tables").delete().eq("document_id", doc_id).execute()

    def table_index(self, doc_id: str) -> dict[str, str]:
        rows = self._paged(
            lambda: self._table("manual_tables").select("id,number,page").eq("document_id", doc_id).order("page").order("id")
        )
        index: dict[str, str] = {}
        for r in rows:
            index.setdefault(r["number"], r["id"])
        return index

    # --- nodes and cross-references --------------------------------------
    def delete_nodes_for_document(self, doc_id: str) -> None:
        # cross_refs of this document are removed with their nodes (ON DELETE CASCADE)
        self._table("nodes").delete().eq("document_id", doc_id).execute()

    def insert_nodes(self, nodes: list[Node]) -> None:
        for batch in _chunks(nodes, NODE_BATCH):
            self._table("nodes").insert([n.model_dump(mode="json") for n in batch]).execute()

    def insert_cross_refs(self, refs: list[CrossRef]) -> None:
        for batch in _chunks(refs, REF_BATCH):
            self._table("cross_refs").insert([r.model_dump(mode="json") for r in batch]).execute()

    def list_refs(self, doc_id: str, status: RefStatus | None = None) -> list[CrossRef]:
        def build():
            q = self._table("cross_refs").select("*").eq("document_id", doc_id)
            return (q.eq("status", status) if status else q).order("id")

        return [CrossRef(**r) for r in self._paged(build)]

    def save_refs(self, refs: list[CrossRef]) -> None:
        # Full rows, so the upsert satisfies NOT NULL columns and just updates by id.
        for batch in _chunks(refs, REF_BATCH):
            self._table("cross_refs").upsert([r.model_dump(mode="json") for r in batch], on_conflict="id").execute()

    def section_index(self, doc_id: str) -> dict[str, str]:
        rows = self._paged(
            lambda: self._table("nodes").select("id,section,ordinal").eq("document_id", doc_id).order("ordinal")
        )
        index: dict[str, str] = {}
        for r in rows:
            index.setdefault(r["section"], r["id"])
        return index

    # --- reading nodes back (Browse) --------------------------------------
    def list_node_summaries(self, doc_id: str) -> list[NodeSummary]:
        rows = self._paged(lambda: self._table("nodes").select(SUMMARY_COLS).eq("document_id", doc_id).order("ordinal"))
        return [NodeSummary(**r) for r in rows]

    def search_node_ids(self, doc_id: str, terms: list[str]) -> set[str]:
        def build():
            q = self._table("nodes").select("id").eq("document_id", doc_id)
            for t in terms:  # one `or` per term, so every term has to match somewhere
                q = q.or_(f"section.ilike.*{t}*,title.ilike.*{t}*,text.ilike.*{t}*")
            return q.order("ordinal")

        return {r["id"] for r in self._paged(build)}

    def nodes_missing_embedding(self, doc_id: str) -> list[tuple[str, str]]:
        rows = self._paged(
            lambda: self._table("nodes")
            .select("id,title,text")
            .eq("document_id", doc_id)
            .is_("embedding", "null")
            .order("ordinal")
        )
        return [(r["id"], f"{r['title']}\n{r['text']}".strip()) for r in rows]

    def save_embeddings(self, vectors: dict[str, list[float]]) -> None:
        # An upsert would need every NOT NULL column, so update one row at a time.
        for node_id, vec in vectors.items():
            self._table("nodes").update({"embedding": vec}).eq("id", node_id).execute()

    def get_node(self, node_id: str) -> Node | None:
        rows = self._table("nodes").select(NODE_COLS).eq("id", node_id).limit(1).execute().data
        return Node(**rows[0]) if rows else None

    def get_nodes(self, node_ids: list[str]) -> list[Node]:
        out: list[Node] = []
        for batch in _chunks(node_ids, NODE_BATCH):
            out.extend(Node(**r) for r in self._table("nodes").select(NODE_COLS).in_("id", batch).execute().data)
        return out

    def match_nodes(self, vector: list[float], k: int, manual: str | None = None) -> list[tuple[str, float]]:
        # The match_nodes SQL function (sql/003_match_nodes.sql) does the nearest-neighbour search in Postgres.
        rows = (
            self.client.rpc("match_nodes", {"query_embedding": vector, "match_count": k, "filter_manual": manual})
            .execute()
            .data
        )
        return [(r["id"], float(r["similarity"])) for r in rows]

    def _rpc_list(self, name: str, query: str, k: int, manual: str | None, column: str) -> list[tuple[str, float]]:
        try:
            rows = self.client.rpc(name, {"query_text": query, "match_count": k, "filter_manual": manual}).execute().data
        except APIError as exc:
            if exc.code == "PGRST202":  # its SQL has not been run: that search returns nothing, the others carry on
                log.error("%s is missing in the database; run sql/004_hybrid_search.sql", name)
                return []
            raise
        return [(r["id"], float(r[column])) for r in rows]

    def match_nodes_text(self, query: str, k: int, manual: str | None = None) -> list[tuple[str, float]]:
        if self.keyword_backend == "paradedb":
            terms = keyword_terms(query)  # the filler of a question would get a high weight: it is rare in the manuals
            if not terms or self.keyword_search is None:
                return []
            return self.keyword_search.match(" ".join(terms), k, manual)
        return self._rpc_list("match_nodes_text", query, k, manual, "rank")

    def match_nodes_title(self, query: str, k: int, manual: str | None = None) -> list[tuple[str, float]]:
        return self._rpc_list("match_nodes_title", query, k, manual, "score")

    def get_figures(self, figure_ids: list[str]) -> list[Figure]:
        out: list[Figure] = []
        for batch in _chunks(figure_ids, NODE_BATCH):
            out.extend(Figure(**r) for r in self._table("figures").select("*").in_("id", batch).execute().data)
        return out

    def figures_of_nodes(self, node_ids: list[str]) -> list[Figure]:
        out: list[Figure] = []
        for batch in _chunks(node_ids, NODE_BATCH):
            out.extend(Figure(**r) for r in self._table("figures").select("*").in_("node_id", batch).order("page").execute().data)
        return out

    def get_node_summaries(self, node_ids: list[str]) -> list[NodeSummary]:
        if not node_ids:
            return []
        rows = self._table("nodes").select(SUMMARY_COLS).in_("id", node_ids).execute().data
        return [NodeSummary(**r) for r in rows]

    def refs_from(self, node_id: str) -> list[CrossRef]:
        rows = self._table("cross_refs").select("*").eq("from_node_id", node_id).order("id").execute().data
        return [CrossRef(**r) for r in rows]

    def refs_to(self, node_id: str) -> list[CrossRef]:
        rows = self._table("cross_refs").select("*").eq("target_node_id", node_id).order("id").execute().data
        return [CrossRef(**r) for r in rows]

    def list_conditions(self, node_id: str) -> list[NodeCondition]:
        rows = self._table("node_conditions").select("*").eq("node_id", node_id).execute().data
        return [NodeCondition(**r) for r in rows]
