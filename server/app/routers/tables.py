from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..deps import get_repo
from ..repository.base import Repository
from ..schemas import TableDetailOut, TableOut

router = APIRouter(prefix="/api/tables", tags=["tables"])


@router.get("", response_model=list[TableOut])
def list_tables(document_id: str = Query(...), repo: Repository = Depends(get_repo)):
    """The tables captured from one document, in page order. Cells are left out; GET /{id} has them."""
    if not repo.get_document(document_id):
        raise HTTPException(404, "Document not found.")
    return [TableOut.from_table(t) for t in repo.list_tables(document_id)]


@router.get("/{table_id}", response_model=TableDetailOut)
def get_table(table_id: str, repo: Repository = Depends(get_repo)):
    """One table with its cells, row by row."""
    table = repo.get_table(table_id)
    if not table:
        raise HTTPException(404, "Table not found.")
    return TableDetailOut.from_table(table)


@router.get("/{table_id}/image")
def table_image(table_id: str, repo: Repository = Depends(get_repo)):
    """The table as printed, as a PNG."""
    table = repo.get_table(table_id)
    if not table:
        raise HTTPException(404, "Table not found.")
    try:
        data = repo.download_file(table.image_path)
    except Exception as exc:  # noqa: BLE001 - the storage client raises its own error types
        raise HTTPException(404, "The image file is missing.") from exc
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})
