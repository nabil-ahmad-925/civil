"""Finds the tables of a manual, keeps their cells and renders each one to a PNG.

A table is a caption ("Table 856.5 Design Speeds") plus the ruled grid next to it. The grid is found with
PyMuPDF's table finder, so it must have ruling lines: a table laid out with spaces only is not found. The cells
are saved as rows, and the region is rendered as printed. The table's text also stays in the node that holds it.

A line that starts with "Table N" is only taken as a caption when a grid sits right next to it and the words after
the number do not start in lower case. That keeps body sentences such as "Table 2.4 lists the limits" from becoming
tables. Only the part of a table on its caption's page is kept: a table that runs onto the next page is cut there.
"""

import logging
import re
from dataclasses import dataclass
from uuid import uuid4

import pymupdf

from ..models import Manual, ManualTable, Node
from .figures import CAPTION_GAP, PADDING, _captions, _owner, _slug

log = logging.getLogger(__name__)

CAPTION_RE = re.compile(r"^\s*Table\s+(?P<num>\d+(?:[-.]\d+)*[A-Za-z]?)\b[\s.:–—-]*(?P<title>.*)$", re.IGNORECASE | re.DOTALL)
# A grid with fewer rows than this is a ruled box or a callout, not a table.
MIN_ROWS = 2


@dataclass
class CapturedTable:
    table: ManualTable
    png: bytes


def _cells(rows: list[list[str | None]]) -> list[list[str]]:
    """Cell text with line breaks flattened; rows with nothing in them are dropped."""
    out = [[" ".join((c or "").replace("\x00", "").split()) for c in row] for row in rows]
    return [row for row in out if any(row)]


def _grid_for(caption: pymupdf.Rect, grids: list[tuple[pymupdf.Rect, list[list[str]]]]) -> tuple[pymupdf.Rect, list[list[str]]] | None:
    """The grid just above or below the caption, overlapping it horizontally. Nearest wins."""
    best: tuple[float, tuple[pymupdf.Rect, list[list[str]]]] | None = None
    for grid in grids:
        rect = grid[0]
        if min(rect.x1, caption.x1) - max(rect.x0, caption.x0) <= 0:
            continue
        if rect.y1 <= caption.y0 + 2:  # above the caption
            gap = caption.y0 - rect.y1
        elif rect.y0 >= caption.y1 - 2:  # below it
            gap = rect.y0 - caption.y1
        else:
            continue
        if gap <= CAPTION_GAP and (best is None or gap < best[0]):
            best = (gap, grid)
    return best[1] if best else None


def _grids(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, list[list[str]]]]:
    """(bbox, rows) of each ruled table on the page that has real content."""
    out: list[tuple[pymupdf.Rect, list[list[str]]]] = []
    for found in page.find_tables().tables:
        rows = _cells(found.extract())
        if len(rows) >= MIN_ROWS:
            out.append((pymupdf.Rect(found.bbox), rows))
    return out


def capture_tables(pdf_bytes: bytes, manual: Manual, document_id: str, nodes: list[Node], dpi: int = 150) -> list[CapturedTable]:
    """One CapturedTable per distinct table number, in page order. The first usable caption of a number wins."""
    found: dict[str, CapturedTable] = {}
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            captions = _captions(page, found, CAPTION_RE)
            if not captions:
                continue  # finding grids is slow: only pages that have a caption pay for it
            grids = _grids(page)
            for bbox, number, caption in captions:
                if number.upper() in found:
                    continue
                grid = _grid_for(bbox, grids)
                if grid is None:
                    continue  # no grid next to it: a sentence, not a caption
                rect, rows = grid
                region = ((rect | bbox) + (-PADDING, -PADDING, PADDING, PADDING)) & page.rect
                png = page.get_pixmap(clip=region, dpi=dpi).tobytes("png")
                label = f"Table {number}"
                found[number.upper()] = CapturedTable(
                    ManualTable(
                        id=str(uuid4()),
                        document_id=document_id,
                        manual=manual,
                        node_id=_owner(nodes, page.number + 1, caption),
                        label=label,
                        number=number,
                        caption=caption,
                        page=page.number + 1,
                        bbox=[round(v, 1) for v in region],
                        image_path=f"tables/{document_id}/{_slug(label)}.png",
                        rows=rows,
                    ),
                    png,
                )
    log.info("capture_tables: %d table(s): %s", len(found), [c.table.label for c in found.values()])
    return list(found.values())
