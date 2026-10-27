"""In-memory repository for tests and for trying the API with no Supabase."""

import math
import re
import threading
from collections import Counter
from pathlib import Path

from ..models import CrossRef, Document, Figure, ManualTable, Node, NodeCondition, NodeSummary, RefStatus
from .keywords import STOP_WORDS, keyword_terms

# BM25 constants (the usual ones) and how much more a word in the heading counts than one in the text. The SQL in
# sql/paradedb/001_nodes_bm25.sql uses the same title weight.
BM25_K1 = 1.2
BM25_B = 0.75
TITLE_BOOST = 2.0


def _stem(word: str) -> str:
    """A crude stand-in for the English stemmer of the real index ("standards" and "standard" match)."""
    return word.rstrip("s") if len(word) > 3 else word


class MemoryRepository:
    def __init__(self, keyword_backend: str = "fts") -> None:
        # "fts" mimics sql/004_hybrid_search.sql, "paradedb" mimics the BM25 of sql/paradedb/001_nodes_bm25.sql.
        self.keyword_backend = keyword_backend
        self._lock = threading.RLock()
        self.documents: dict[str, Document] = {}
        self.files: dict[str, bytes] = {}
        self.nodes: dict[str, Node] = {}
        self.refs: dict[str, CrossRef] = {}
        self.figures: dict[str, Figure] = {}
        self.tables: dict[str, ManualTable] = {}

    # --- source documents -------------------------------------------------
    def list_documents(self) -> list[Document]:
        with self._lock:
            return sorted(self.documents.values(), key=lambda d: d.uploaded_at, reverse=True)

    def get_document(self, doc_id: str) -> Document | None:
        return self.documents.get(doc_id)

    def insert_document(self, doc: Document) -> None:
        with self._lock:
            self.documents[doc.id] = doc

    def update_document(self, doc_id: str, **fields: object) -> None:
        with self._lock:
            self.documents[doc_id] = self.documents[doc_id].model_copy(update=fields)

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            self.documents.pop(doc_id, None)
            # Mirrors ON DELETE CASCADE.
            self.delete_nodes_for_document(doc_id)
            self.delete_figures_for_document(doc_id)
            self.delete_tables_for_document(doc_id)

    # --- PDF files --------------------------------------------------------
    def upload_file(self, storage_path: str, local_path: str) -> None:
        self.files[storage_path] = Path(local_path).read_bytes()

    def download_file(self, storage_path: str) -> bytes:
        return self.files[storage_path]

    def delete_file(self, storage_path: str) -> None:
        self.files.pop(storage_path, None)

    def upload_bytes(self, storage_path: str, data: bytes, content_type: str) -> None:
        self.files[storage_path] = data

    def signed_url(self, storage_path: str, seconds: int) -> str:
        return f"memory://{storage_path}?expires={seconds}"

    def delete_files(self, storage_paths: list[str]) -> None:
        for path in storage_paths:
            self.files.pop(path, None)

    # --- figures ----------------------------------------------------------
    def insert_figures(self, figures: list[Figure]) -> None:
        with self._lock:
            for f in figures:
                self.figures[f.id] = f

    def list_figures(self, doc_id: str) -> list[Figure]:
        return sorted((f for f in self.figures.values() if f.document_id == doc_id), key=lambda f: (f.page, f.label))

    def get_figure(self, figure_id: str) -> Figure | None:
        return self.figures.get(figure_id)

    def delete_figures_for_document(self, doc_id: str) -> None:
        with self._lock:
            gone = {k for k, f in self.figures.items() if f.document_id == doc_id}
            self.figures = {k: f for k, f in self.figures.items() if k not in gone}
            # Mirrors ON DELETE SET NULL on cross_refs.target_figure_id.
            for r in self.refs.values():
                if r.target_figure_id in gone:
                    r.target_figure_id = None

    def figure_index(self, doc_id: str) -> dict[str, str]:
        index: dict[str, str] = {}
        for f in self.list_figures(doc_id):
            index.setdefault(f.number, f.id)
        return index

    # --- tables -----------------------------------------------------------
    def insert_tables(self, tables: list[ManualTable]) -> None:
        with self._lock:
            for t in tables:
                self.tables[t.id] = t

    def list_tables(self, doc_id: str) -> list[ManualTable]:
        return sorted((t for t in self.tables.values() if t.document_id == doc_id), key=lambda t: (t.page, t.label))

    def get_table(self, table_id: str) -> ManualTable | None:
        return self.tables.get(table_id)

    def delete_tables_for_document(self, doc_id: str) -> None:
        with self._lock:
            gone = {k for k, t in self.tables.items() if t.document_id == doc_id}
            self.tables = {k: t for k, t in self.tables.items() if k not in gone}
            # Mirrors ON DELETE SET NULL on cross_refs.target_table_id.
            for r in self.refs.values():
                if r.target_table_id in gone:
                    r.target_table_id = None

    def table_index(self, doc_id: str) -> dict[str, str]:
        index: dict[str, str] = {}
        for t in self.list_tables(doc_id):
            index.setdefault(t.number, t.id)
        return index

    # --- nodes and cross-references --------------------------------------
    def delete_nodes_for_document(self, doc_id: str) -> None:
        with self._lock:
            gone = {k for k, n in self.nodes.items() if n.document_id == doc_id}
            self.nodes = {k: n for k, n in self.nodes.items() if k not in gone}
            self.refs = {k: r for k, r in self.refs.items() if r.document_id != doc_id}
            # Mirrors ON DELETE SET NULL on cross_refs.target_node_id.
            for r in self.refs.values():
                if r.target_node_id in gone:
                    r.target_node_id = None

    def insert_nodes(self, nodes: list[Node]) -> None:
        for n in nodes:
            self.nodes[n.id] = n

    def insert_cross_refs(self, refs: list[CrossRef]) -> None:
        for r in refs:
            self.refs[r.id] = r

    def list_refs(self, doc_id: str, status: RefStatus | None = None) -> list[CrossRef]:
        return [
            r.model_copy()
            for r in self.refs.values()
            if r.document_id == doc_id and (status is None or r.status == status)
        ]

    def save_refs(self, refs: list[CrossRef]) -> None:
        for r in refs:
            self.refs[r.id] = r

    def section_index(self, doc_id: str) -> dict[str, str]:
        index: dict[str, str] = {}
        for n in sorted((n for n in self.nodes.values() if n.document_id == doc_id), key=lambda n: n.ordinal):
            index.setdefault(n.section, n.id)
        return index

    # --- reading nodes back (Browse) --------------------------------------
    @staticmethod
    def _summary(n: Node) -> NodeSummary:
        return NodeSummary(**n.model_dump(include=set(NodeSummary.model_fields)))

    def list_node_summaries(self, doc_id: str) -> list[NodeSummary]:
        nodes = sorted((n for n in self.nodes.values() if n.document_id == doc_id), key=lambda n: n.ordinal)
        return [self._summary(n) for n in nodes]

    def search_node_ids(self, doc_id: str, terms: list[str]) -> set[str]:
        found = set()
        for n in self.nodes.values():
            if n.document_id != doc_id:
                continue
            haystack = f"{n.section}\n{n.title}\n{n.text}".lower()
            if all(t.lower() in haystack for t in terms):
                found.add(n.id)
        return found

    def nodes_missing_embedding(self, doc_id: str) -> list[tuple[str, str]]:
        with self._lock:
            nodes = sorted((n for n in self.nodes.values() if n.document_id == doc_id and n.embedding is None), key=lambda n: n.ordinal)
            return [(n.id, f"{n.title}\n{n.text}".strip()) for n in nodes]

    def save_embeddings(self, vectors: dict[str, list[float]]) -> None:
        with self._lock:
            for node_id, vec in vectors.items():
                if node_id in self.nodes:
                    self.nodes[node_id].embedding = vec

    def get_node(self, node_id: str) -> Node | None:
        n = self.nodes.get(node_id)
        return n.model_copy(update={"embedding": None}) if n else None

    def get_nodes(self, node_ids: list[str]) -> list[Node]:
        return [self.nodes[i].model_copy(update={"embedding": None}) for i in node_ids if i in self.nodes]

    def match_nodes(self, vector: list[float], k: int, manual: str | None = None) -> list[tuple[str, float]]:
        def norm(v: list[float]) -> float:
            return sum(x * x for x in v) ** 0.5 or 1.0

        q = norm(vector)
        scored = [
            (n.id, sum(a * b for a, b in zip(vector, n.embedding, strict=True)) / (q * norm(n.embedding)))
            for n in self.nodes.values()
            if n.embedding is not None and (manual is None or n.manual == manual)
        ]
        return sorted(scored, key=lambda s: s[1], reverse=True)[:k]

    # Stand-ins for the SQL functions in sql/004_hybrid_search.sql and sql/paradedb/001_nodes_bm25.sql: same idea, plain Python.
    _STOP = STOP_WORDS

    @staticmethod
    def _words(text: str) -> list[str]:
        return re.findall(r"[a-z0-9]+", text.lower())

    def _bm25_field(self, tokens: dict[str, list[str]], terms: set[str]) -> dict[str, float]:
        """BM25 of one field (title or text) for every node that has a term in it. Statistics cover all nodes."""
        counts = {i: Counter(t) for i, t in tokens.items()}
        average = (sum(len(t) for t in tokens.values()) / len(tokens)) or 1.0
        scores: dict[str, float] = {}
        for term in terms:
            holders = [i for i, c in counts.items() if term in c]
            if not holders:
                continue
            idf = math.log(1 + (len(tokens) - len(holders) + 0.5) / (len(holders) + 0.5))
            for i in holders:
                tf = counts[i][term]
                norm = BM25_K1 * (1 - BM25_B + BM25_B * len(tokens[i]) / average)
                scores[i] = scores.get(i, 0.0) + idf * tf * (BM25_K1 + 1) / (tf + norm)
        return scores

    def _match_nodes_bm25(self, query: str, k: int, manual: str | None) -> list[tuple[str, float]]:
        terms = {_stem(w) for w in keyword_terms(query)}
        if not terms:
            return []
        nodes = list(self.nodes.values())
        in_text = self._bm25_field({n.id: [_stem(w) for w in self._words(n.text)] for n in nodes}, terms)
        in_title = self._bm25_field({n.id: [_stem(w) for w in self._words(n.title)] for n in nodes}, terms)
        wanted = {n.id for n in nodes if manual is None or n.manual == manual}
        # Averaged over the question's words, so a longer question does not score higher just for having more of them.
        scored = {
            i: (in_text.get(i, 0.0) + TITLE_BOOST * in_title.get(i, 0.0)) / len(terms)
            for i in wanted & (in_text.keys() | in_title.keys())
        }
        return sorted(scored.items(), key=lambda s: (-s[1], s[0]))[:k]

    def match_nodes_text(self, query: str, k: int, manual: str | None = None) -> list[tuple[str, float]]:
        if self.keyword_backend == "paradedb":
            return self._match_nodes_bm25(query, k, manual)
        terms = {w.rstrip("s") for w in self._words(query) if w not in self._STOP}
        phrase = " ".join(self._words(query))
        scored = []
        for n in self.nodes.values():
            if manual is not None and n.manual != manual:
                continue
            words = self._words(f"{n.title} {n.text}")
            have = {w.rstrip("s") for w in words}
            hit = terms & have
            if not hit:
                continue
            bonus = 1.0 if len(terms) > 0 and phrase and phrase in " ".join(words) else 0.0
            scored.append((n.id, len(hit) / len(terms) + bonus))
        return sorted(scored, key=lambda s: s[1], reverse=True)[:k]

    def match_nodes_title(self, query: str, k: int, manual: str | None = None) -> list[tuple[str, float]]:
        cleaned = " ".join(self._words(query))
        numbers = set(re.findall(r"\d+(?:[-.]\d+)+[A-Za-z]?", query))
        scored = []
        for n in sorted(self.nodes.values(), key=lambda n: n.ordinal):
            if manual is not None and n.manual != manual:
                continue
            title = " ".join(self._words(n.title))
            if n.section in numbers or (len(n.title) >= 15 and title in cleaned) or (len(cleaned) >= 4 and cleaned in title):
                scored.append((n.id, 1.0))
        return scored[:k]

    def get_figures(self, figure_ids: list[str]) -> list[Figure]:
        return [self.figures[i] for i in figure_ids if i in self.figures]

    def figures_of_nodes(self, node_ids: list[str]) -> list[Figure]:
        wanted = set(node_ids)
        return sorted((f for f in self.figures.values() if f.node_id in wanted), key=lambda f: (f.page, f.label))

    def get_node_summaries(self, node_ids: list[str]) -> list[NodeSummary]:
        return [self._summary(self.nodes[i]) for i in node_ids if i in self.nodes]

    def refs_from(self, node_id: str) -> list[CrossRef]:
        return [r.model_copy() for r in self.refs.values() if r.from_node_id == node_id]

    def refs_to(self, node_id: str) -> list[CrossRef]:
        return [r.model_copy() for r in self.refs.values() if r.target_node_id == node_id]

    def list_conditions(self, node_id: str) -> list[NodeCondition]:
        return []  # nothing writes conditions until the tagging stage exists
