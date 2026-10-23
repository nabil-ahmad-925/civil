from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..deps import get_repo
from ..repository.base import Repository
from ..schemas import FigureOut

router = APIRouter(prefix="/api/figures", tags=["figures"])


@router.get("", response_model=list[FigureOut])
def list_figures(document_id: str = Query(...), repo: Repository = Depends(get_repo)):
    """The figures captured from one document, in page order."""
    if not repo.get_document(document_id):
        raise HTTPException(404, "Document not found.")
    return [FigureOut.from_figure(f) for f in repo.list_figures(document_id)]


@router.get("/{figure_id}/image")
def figure_image(figure_id: str, repo: Repository = Depends(get_repo)):
    """The figure as a PNG."""
    figure = repo.get_figure(figure_id)
    if not figure:
        raise HTTPException(404, "Figure not found.")
    try:
        data = repo.download_file(figure.image_path)
    except Exception as exc:  # noqa: BLE001 - the storage client raises its own error types
        raise HTTPException(404, "The image file is missing.") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
