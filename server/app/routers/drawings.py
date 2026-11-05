import hashlib
import threading
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from ..config import Settings, get_settings
from ..deps import get_drawing_vision, get_repo
from ..drawings.images import BadImage, SheetFile, read_upload
from ..drawings.reader import DrawingError, process_drawing, review_items
from ..drawings.vision import DrawingVision
from ..models import Drawing, DrawingSheet
from ..repository.base import Repository
from ..schemas import (
    DrawingFactOut,
    DrawingFeatureOut,
    DrawingNoteOut,
    DrawingOut,
    DrawingReviewOut,
    NoteBindingOut,
    ReviewItemOut,
    SheetReviewOut,
)

router = APIRouter(prefix="/api/drawings", tags=["drawings"])

EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}

# Drawings being processed by this process right now.
_active: set[str] = set()
_active_lock = threading.Lock()


async def _read_body(request: Request, settings: Settings) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > settings.max_upload_bytes:
            raise HTTPException(413, f"File exceeds the {settings.max_upload_mb} MB limit.")
    if not body:
        raise HTTPException(400, "File is empty.")
    return bytes(body)


def _parse(data: bytes) -> list[SheetFile]:
    try:
        return read_upload(data)
    except BadImage as exc:
        raise HTTPException(400, str(exc)) from exc


def _store_sheets(repo: Repository, drawing_id: str, files: list[SheetFile], filename: str, first_number: int) -> None:
    for number, f in enumerate(files, start=first_number):
        sheet_id = str(uuid4())
        path = f"drawings/{drawing_id}/sheets/{sheet_id}.{EXTENSIONS[f.content_type]}"
        repo.upload_bytes(path, f.data, f.content_type)
        repo.insert_sheet(
            DrawingSheet(
                id=sheet_id,
                drawing_id=drawing_id,
                number=number,
                original_name=filename,
                image_path=path,
                content_type=f.content_type,
                sha256=hashlib.sha256(f.data).hexdigest(),
                size_bytes=len(f.data),
                width=f.width,
                height=f.height,
            )
        )


def _create(repo: Repository, name: str, filename: str, files: list[SheetFile]) -> DrawingOut:
    drawing = Drawing(id=str(uuid4()), name=name, status="uploaded")
    repo.insert_drawing(drawing)
    _store_sheets(repo, drawing.id, files, filename, 1)
    return DrawingOut.from_drawing(drawing, repo.list_sheets(drawing.id))


def _add(repo: Repository, drawing: Drawing, filename: str, files: list[SheetFile]) -> DrawingOut:
    sheets = repo.list_sheets(drawing.id)
    _store_sheets(repo, drawing.id, files, filename, (sheets[-1].number if sheets else 0) + 1)
    repo.update_drawing(drawing.id, status="uploaded", error=None)  # the new sheets have not been read
    updated = repo.get_drawing(drawing.id)
    assert updated is not None
    return DrawingOut.from_drawing(updated, repo.list_sheets(drawing.id))


def _require(repo: Repository, drawing_id: str) -> Drawing:
    drawing = repo.get_drawing(drawing_id)
    if not drawing:
        raise HTTPException(404, "Drawing not found.")
    return drawing


def _busy(drawing_id: str) -> bool:
    with _active_lock:
        return drawing_id in _active


@router.get("", response_model=list[DrawingOut])
def list_drawings(repo: Repository = Depends(get_repo)):
    return [DrawingOut.from_drawing(d, repo.list_sheets(d.id)) for d in repo.list_drawings()]


@router.post("", response_model=DrawingOut, status_code=201)
async def upload_drawing(
    request: Request,
    filename: str = Query(...),
    name: str = Query(""),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
):
    """Step 1. The raw image as the body. One sheet per image, or one per page of a TIFF. Nothing is read yet."""
    filename = filename.strip()
    if not filename:
        raise HTTPException(400, "Filename is required.")
    data = await _read_body(request, settings)
    files = await run_in_threadpool(_parse, data)
    return await run_in_threadpool(_create, repo, name.strip() or filename, filename, files)


@router.post("/{drawing_id}/sheets", response_model=DrawingOut, status_code=201)
async def add_sheets(
    drawing_id: str,
    request: Request,
    filename: str = Query(...),
    repo: Repository = Depends(get_repo),
    settings: Settings = Depends(get_settings),
):
    """Adds another image to the set. Run process again afterwards."""
    filename = filename.strip()
    if not filename:
        raise HTTPException(400, "Filename is required.")
    drawing = _require(repo, drawing_id)
    if _busy(drawing_id):
        raise HTTPException(409, "This drawing is being processed.")
    data = await _read_body(request, settings)
    files = await run_in_threadpool(_parse, data)
    return await run_in_threadpool(_add, repo, drawing, filename, files)


@router.post("/{drawing_id}/process", response_model=DrawingOut)
def process(
    drawing_id: str,
    tile_sweep: bool | None = Query(None, description="Overrides DRAWING_TILE_SWEEP for this run."),
    repo: Repository = Depends(get_repo),
    vision: DrawingVision | None = Depends(get_drawing_vision),
    settings: Settings = Depends(get_settings),
):
    """Step 2. Reads every sheet and saves what it found. Safe to run again: it replaces the previous results."""
    drawing = _require(repo, drawing_id)
    if vision is None:
        raise HTTPException(409, "A vision model reads the drawings: set OPENAI_API_KEY in server/.env.")
    with _active_lock:
        if drawing_id in _active:
            raise HTTPException(409, "This drawing is already being processed.")
        _active.add(drawing_id)
    try:
        updated = process_drawing(repo, vision, drawing, settings, tile_sweep)
        return DrawingOut.from_drawing(updated, repo.list_sheets(drawing_id))
    except DrawingError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # the failure is already saved on the drawing
        raise HTTPException(500, f"Processing failed: {exc}") from exc
    finally:
        with _active_lock:
            _active.discard(drawing_id)


@router.get("/{drawing_id}/review", response_model=DrawingReviewOut)
def review(drawing_id: str, repo: Repository = Depends(get_repo)):
    """Everything found on each sheet, and what a person has to look at. All of it is proposed until an engineer verifies it."""
    drawing = _require(repo, drawing_id)
    sheets = repo.list_sheets(drawing_id)
    features = repo.list_drawing_features(drawing_id)
    notes = repo.list_drawing_notes(drawing_id)
    bindings = repo.list_note_bindings(drawing_id)
    facts = repo.list_drawing_facts(drawing_id)
    out: list[SheetReviewOut] = []
    for s in sheets:
        own_features = [f for f in features if f.sheet_id == s.id]
        own_notes = [n for n in notes if n.sheet_id == s.id]
        own_facts = [f for f in facts if f.sheet_id == s.id]
        note_ids = {n.id for n in own_notes}
        out.append(
            SheetReviewOut(
                sheet=DrawingOut.from_drawing(drawing, [s]).sheets[0],
                features=[DrawingFeatureOut.from_feature(f) for f in own_features],
                notes=[DrawingNoteOut.from_note(n) for n in own_notes],
                bindings=[NoteBindingOut.from_binding(b) for b in bindings if b.note_id in note_ids],
                facts=[DrawingFactOut.from_fact(f) for f in own_facts],
                needs_review=[ReviewItemOut(**i) for i in review_items(s, own_features, own_notes, own_facts)],
            )
        )
    return DrawingReviewOut(drawing=DrawingOut.from_drawing(drawing, sheets), sheets=out)


def _image(repo: Repository, path: str | None, media_type: str) -> Response:
    if not path:
        raise HTTPException(404, "There is no image for this.")
    try:
        data = repo.download_file(path)
    except Exception as exc:  # noqa: BLE001 - the storage client raises its own error types
        raise HTTPException(404, "The image file is missing.") from exc
    return Response(data, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


@router.get("/sheets/{sheet_id}/image")
def sheet_image(sheet_id: str, repo: Repository = Depends(get_repo)):
    sheet = repo.get_sheet(sheet_id)
    if not sheet:
        raise HTTPException(404, "Sheet not found.")
    return _image(repo, sheet.image_path, sheet.content_type)


@router.get("/facts/{fact_id}/evidence")
def fact_evidence(fact_id: str, repo: Repository = Depends(get_repo)):
    """The crop a fact was read from."""
    fact = repo.get_drawing_fact(fact_id)
    if not fact:
        raise HTTPException(404, "Fact not found.")
    return _image(repo, fact.image_path, "image/png")


@router.get("/bindings/{binding_id}/evidence")
def binding_evidence(binding_id: str, repo: Repository = Depends(get_repo)):
    """The crop that shows a note and the feature it is linked to."""
    binding = repo.get_note_binding(binding_id)
    if not binding:
        raise HTTPException(404, "Link not found.")
    return _image(repo, binding.image_path, "image/png")


@router.delete("/{drawing_id}", status_code=204)
def delete_drawing(drawing_id: str, repo: Repository = Depends(get_repo)):
    _require(repo, drawing_id)
    if _busy(drawing_id):
        raise HTTPException(409, "This drawing is being processed.")
    images = {s.image_path for s in repo.list_sheets(drawing_id)}
    images |= {f.image_path for f in repo.list_drawing_facts(drawing_id) if f.image_path}
    images |= {b.image_path for b in repo.list_note_bindings(drawing_id) if b.image_path}
    repo.delete_drawing(drawing_id)
    repo.delete_files(sorted(images))
