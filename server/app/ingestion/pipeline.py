"""Ingests one uploaded PDF: read -> chunk -> capture cross-refs and figures -> embed -> save -> link refs.

Figures are captured from the same PDF (figures.py), rendered to PNG in the bucket, and saved
in the figures table so references to them can be linked.

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
            f"No headings found in {doc.original_name}. "
            "Is it a text PDF (not a scan) with numbered section headings?"
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
    stats: dict[str, Any] = {
        "pages": result.pages,
        "nodes": len(result.nodes),
        "cross_refs": len(refs),
        "figures": len(figures),
        "unassigned_lines": result.unassigned_lines,
    }

    if embedder:
        vectors = embedder.embed([f"{n.title}\n{n.text}".strip() for n in result.nodes])
        for node, vec in zip(result.nodes, vectors, strict=True):
            node.embedding = vec
        stats["vectors"] = len(vectors)
    else:
        stats["vectors"] = "skipped (EMBEDDING_PROVIDER=none)"

    if figures_error:
        stats["figures_error"] = figures_error

    # A re-ingest replaces, never duplicates.
    repo.delete_files([f.image_path for f in repo.list_figures(doc.id)])
    repo.delete_figures_for_document(doc.id)
    repo.delete_nodes_for_document(doc.id)
    repo.insert_nodes(result.nodes)
    for captured in figures:
        repo.upload_bytes(captured.figure.image_path, captured.png, "image/png")
    repo.insert_figures([c.figure for c in figures])
    repo.insert_cross_refs(refs)
    stats["internal_pass"] = internal_pass(repo, doc)
    return stats


def embed_missing(repo: Repository, embedder: Embedder, doc: Document) -> int:
    """Embeds the document's nodes that have no vector yet, without re-reading the PDF. Returns how many."""
    missing = repo.nodes_missing_embedding(doc.id)
    if missing:
        repo.save_embeddings(dict(zip((i for i, _ in missing), embedder.embed([t for _, t in missing]), strict=True)))
    return len(missing)


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
