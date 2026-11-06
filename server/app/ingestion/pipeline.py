"""Ingests one uploaded PDF: read -> chunk -> capture cross-refs, figures and tables -> embed -> save -> link refs.

Figures and tables are captured from the same PDF (figures.py, tables.py), rendered to PNG in the bucket, and
saved in the figures and manual_tables tables so references to them can be linked.

Runs synchronously inside POST /api/documents/{id}/ingest. For a very large manual with
embeddings on, move this to a background job.
"""

import logging
from typing import Any

from ..config import get_settings
from ..models import Document
from ..repository.base import Repository
from .chunker import chunk_pdf
from .crossrefs import RefExtractor, extract_all
from .embeddings import Embedder
from .figures import capture_figures
from .planner import NodePlanner
from .resolution import cross_manual_pass, internal_pass
from .tables import capture_tables

log = logging.getLogger(__name__)


class IngestError(ValueError):
    """The file itself cannot be ingested (as opposed to a bug or an outage)."""


def current_docs(repo: Repository, include: Document | None = None) -> list[Document]:
    """The ingested document of each manual (the most recent upload wins), plus `include`."""
    by_manual: dict[str, Document] = {}
    for d in sorted(repo.list_documents(), key=lambda d: d.uploaded_at):
        if d.status == "ingested":
            by_manual[d.manual] = d
    if include:
        by_manual[include.manual] = include
    return list(by_manual.values())


def refresh_cross_refs(repo: Repository, include: Document | None = None) -> dict[str, int]:
    return cross_manual_pass(repo, current_docs(repo, include))


def _chunk_and_save(
    repo: Repository, embedder: Embedder | None, planner: NodePlanner, ref_extractor: RefExtractor, doc: Document
) -> dict[str, Any]:
    pdf = repo.download_file(doc.storage_path)
    result = chunk_pdf(pdf, doc.manual, doc.id, planner, get_settings().chunk_context_lines)
    if not result.nodes:
        raise IngestError(
            f"No text found in {doc.original_name}. Is it a text PDF (not a scan)?"
            if getattr(planner, "topics", False)
            else f"No headings found in {doc.original_name}. Is it a text PDF (not a scan) with numbered section headings?"
        )
    refs = extract_all(result.nodes, ref_extractor, get_settings().ref_extractor_workers)
    try:
        figures = capture_figures(pdf, doc.manual, doc.id, result.nodes, get_settings().figure_dpi)
    except Exception as exc:  # noqa: BLE001 - figures are an extra; the sections must still be saved
        log.exception("Capturing figures of %s failed", doc.id)
        figures = []
        figures_error = f"{type(exc).__name__}: {exc}"
    else:
        figures_error = None
    try:
        tables = capture_tables(pdf, doc.manual, doc.id, result.nodes, get_settings().table_dpi)
    except Exception as exc:  # noqa: BLE001 - tables are an extra too; their text is already in the sections
        log.exception("Capturing tables of %s failed", doc.id)
        tables = []
        tables_error = f"{type(exc).__name__}: {exc}"
    else:
        tables_error = None
    stats: dict[str, Any] = {
        "pages": result.pages,
        "nodes": len(result.nodes),
        "cross_refs": len(refs),
        "figures": len(figures),
        "tables": len(tables),
        "unassigned_lines": result.unassigned_lines,
    }

    if embedder:
        vectors = _embed_each(embedder, [(n.id, f"{n.title}\n{n.text}".strip()) for n in result.nodes])
        for node in result.nodes:
            node.embedding = vectors.get(node.id)  # None for a node that failed: POST /embed retries it later
        stats["vectors"] = len(vectors)
        if len(vectors) < len(result.nodes):
            stats["vectors_failed"] = len(result.nodes) - len(vectors)
    else:
        stats["vectors"] = "skipped (EMBEDDING_PROVIDER=none)"

    if figures_error:
        stats["figures_error"] = figures_error
    if tables_error:
        stats["tables_error"] = tables_error

    # A re-ingest replaces, never duplicates.
    repo.delete_files([f.image_path for f in repo.list_figures(doc.id)] + [t.image_path for t in repo.list_tables(doc.id)])
    repo.delete_figures_for_document(doc.id)
    repo.delete_tables_for_document(doc.id)
    repo.delete_nodes_for_document(doc.id)
    repo.insert_nodes(result.nodes)
    for captured in figures:
        repo.upload_bytes(captured.figure.image_path, captured.png, "image/png")
    repo.insert_figures([c.figure for c in figures])
    for captured_table in tables:
        repo.upload_bytes(captured_table.table.image_path, captured_table.png, "image/png")
    repo.insert_tables([c.table for c in tables])
    repo.insert_cross_refs(refs)
    stats["internal_pass"] = internal_pass(repo, doc)
    return stats


def _embed_each(embedder: Embedder, items: list[tuple[str, str]]) -> dict[str, list[float]]:
    """Vectors for (node id, text) pairs, as {id: vector}. A node that cannot be embedded is left out (and logged)
    instead of failing the rest: if the whole batch fails, each node is retried on its own."""
    if not items:
        return {}
    try:
        return dict(zip((i for i, _ in items), embedder.embed([t for _, t in items]), strict=True))
    except Exception:  # noqa: BLE001 - fall back to one node at a time to find the one that fails
        log.exception("Embedding %d nodes in one go failed; retrying one by one.", len(items))
    vectors: dict[str, list[float]] = {}
    for node_id, text in items:
        try:
            vectors[node_id] = embedder.embed([text])[0]
        except Exception:  # noqa: BLE001 - this node stays without a vector; the others still get theirs
            log.exception("Embedding node %s failed; it stays without a vector.", node_id)
    return vectors


def embed_missing(repo: Repository, embedder: Embedder, doc: Document) -> int:
    """Embeds the document's nodes that have no vector yet, without re-reading the PDF. Returns how many got one.

    Nodes that already have a vector are never touched. If a batch fails, each node is retried on its own, so one
    bad node stays without a vector (and is logged) instead of blocking the rest. Running this again retries it.
    """
    missing = repo.nodes_missing_embedding(doc.id)
    vectors = _embed_each(embedder, missing)
    if vectors:
        repo.save_embeddings(vectors)
    return len(vectors)


def ingest_document(
    repo: Repository, embedder: Embedder | None, planner: NodePlanner, ref_extractor: RefExtractor, doc: Document
) -> Document:
    repo.update_document(doc.id, status="processing", error=None)
    try:
        stats = _chunk_and_save(repo, embedder, planner, ref_extractor, doc)
        stats["cross_manual_pass"] = refresh_cross_refs(repo, include=doc)
        repo.update_document(doc.id, status="ingested", error=None, summary=stats)
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded on the document
        log.exception("Ingesting %s failed", doc.id)
        message = str(exc) if isinstance(exc, IngestError) else f"{type(exc).__name__}: {exc}"
        repo.update_document(doc.id, status="failed", error=message)
        raise
    updated = repo.get_document(doc.id)
    assert updated is not None
    return updated
