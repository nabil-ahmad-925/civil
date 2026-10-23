import hashlib
import os
import tempfile
import threading

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from ..deps import get_embedder, get_planner, get_ref_extractor, get_repo
from ..ingestion.crossrefs import RefExtractor
from ..ingestion.embeddings import Embedder
from ..ingestion.planner import NodePlanner
from ..ingestion.pipeline import IngestError, embed_missing, ingest_document, refresh_cross_refs
from ..models import Manual
from ..repository.base import Repository
from ..schemas import DocumentOut
from ..services.uploads import finalize_upload

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Documents being ingested by this process right now.
_active: set[str] = set()
_active_lock = threading.Lock()


@router.get("", response_model=list[DocumentOut])
def list_documents(repo: Repository = Depends(get_repo)):
    return [DocumentOut.from_document(d) for d in repo.list_documents()]


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    request: Request,
    manual: Manual = Query(...),
    filename: str = Query(...),
    version: str = Query(""),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
):
    """Step 1. Raw PDF as the body; metadata in the query string. Saved to the Supabase bucket, not chunked."""
    filename = filename.strip()
    if not filename:
        raise HTTPException(400, "Filename is required.")

    hasher = hashlib.sha256()
    size = 0
    head = b""
    fd, tmp_path = tempfile.mkstemp(suffix=".part")
    try:
        with os.fdopen(fd, "wb") as out:
            async for chunk in request.stream():
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB limit.")
                if len(head) < 5:
                    head += chunk[: 5 - len(head)]
                if len(head) >= 5 and not head.startswith(b"%PDF-"):
                    raise HTTPException(400, "File is not a PDF.")
                hasher.update(chunk)
                out.write(chunk)
        if size == 0:
            raise HTTPException(400, "File is empty.")
        if not head.startswith(b"%PDF-"):
            raise HTTPException(400, "File is not a PDF.")

        doc = await run_in_threadpool(
            finalize_upload,
            repo,
            tmp_path,
            manual=manual,
            version=version.strip() or "Not specified",
            filename=filename,
            sha256=hasher.hexdigest(),
            size=size,
        )
        return DocumentOut.from_document(doc)
    finally:
        os.unlink(tmp_path)


@router.post("/{doc_id}/ingest", response_model=DocumentOut)
def ingest(
    doc_id: str,
    repo: Repository = Depends(get_repo),
    embedder: Embedder | None = Depends(get_embedder),
    planner: NodePlanner = Depends(get_planner),
    ref_extractor: RefExtractor = Depends(get_ref_extractor),
):
    """Step 2. Chunks the stored PDF and saves nodes, cross-references and vectors. Safe to run again."""
    doc = repo.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    with _active_lock:
        if doc_id in _active:
            raise HTTPException(409, "This document is already being ingested.")
        _active.add(doc_id)
    try:
        return DocumentOut.from_document(ingest_document(repo, embedder, planner, ref_extractor, doc))
    except IngestError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # the failure is already saved on the document
        raise HTTPException(500, f"Ingestion failed: {exc}") from exc
    finally:
        with _active_lock:
            _active.discard(doc_id)


@router.post("/{doc_id}/embed")
def embed(
    doc_id: str,
    repo: Repository = Depends(get_repo),
    embedder: Embedder | None = Depends(get_embedder),
):
    """Adds vectors to an ingested document's nodes that have none, without re-ingesting the PDF."""
    doc = repo.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    if embedder is None:
        raise HTTPException(409, "Vectors are off. Set EMBEDDING_PROVIDER=openai in server/.env and restart.")
    return {"embedded": embed_missing(repo, embedder, doc)}


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: str, repo: Repository = Depends(get_repo)):
    doc = repo.get_document(doc_id)
    if not doc:
        raise HTTPException(404, "Document not found.")
    with _active_lock:
        if doc_id in _active:
            raise HTTPException(409, "This document is being ingested.")
    images = [f.image_path for f in repo.list_figures(doc_id)]
    repo.delete_document(doc_id)  # also removes its nodes, figures and cross-references
    repo.delete_file(doc.storage_path)
    repo.delete_files(images)
    # References from other manuals that pointed into this one go back to waiting.
    refresh_cross_refs(repo)
