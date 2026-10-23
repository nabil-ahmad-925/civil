"""Finds the figures of a manual and renders each one to a PNG.

A figure is a caption ("Figure 2-7 Title Block") plus the drawing next to it. Caltrans figures are mostly
vector drawings, which have no image file inside the PDF, so the drawing is found from the page's graphics
(images and vector paths) and the region is rendered, which works the same for both kinds.

A line that starts with "Figure N-N" is only taken as a caption when graphics sit right next to it and
the words after the number do not start in lower case. That keeps body sentences such as
"Figure 2-7 shows the layout" from becoming figures.
"""

import logging
import re
from dataclasses import dataclass
from uuid import uuid4

import pymupdf

from ..models import Figure, Manual, Node

log = logging.getLogger(__name__)

CAPTION_RE = re.compile(r"^\s*Figure\s+(?P<num>\d+(?:[-.]\d+)*[A-Za-z]?)\b[\s.:–—-]*(?P<title>.*)$", re.IGNORECASE | re.DOTALL)
# "Figure 2-7 shows ..." / "Figure 2-7 is ..." continues a sentence; a caption goes straight to its title.
SENTENCE_RE = re.compile(r"^[a-z]")
CAPTION_MAX_CHARS = 300
# Graphics closer together than this (points) belong to the same drawing.
MERGE_GAP = 14
# The drawing must start within this distance of the caption.
CAPTION_GAP = 60
# Drawings with no side longer than this are specks (bullets, dots), not figures...
MIN_FIGURE_SIZE = 40
# ...and thinner than this are rules and underlines (a heading's underline is not the drawing under it).
MIN_THICKNESS = 8
# Rendered region is grown by this much so line ends and labels are not clipped.
PADDING = 6


@dataclass
class CapturedFigure:
    figure: Figure
    png: bytes


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def _graphics(page: pymupdf.Page) -> list[pymupdf.Rect]:
    """Rectangles of the images and vector drawings on the page, without page borders."""
    page_area = page.rect.get_area()
    rects: list[pymupdf.Rect] = []
    for info in page.get_image_info():
        rects.append(pymupdf.Rect(info["bbox"]))
    for d in page.get_drawings():
        rects.append(pymupdf.Rect(d["rect"]))
    return [r for r in rects if r.get_area() < page_area * 0.9]  # a full-page frame is not a drawing


def _merge(rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """Joins rectangles that touch or sit within MERGE_GAP of each other, until none are left to join."""
    groups = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out: list[pymupdf.Rect] = []
        for r in groups:
            for i, o in enumerate(out):
                if (o + (-MERGE_GAP, -MERGE_GAP, MERGE_GAP, MERGE_GAP)).intersects(r):
                    out[i] = o | r
                    changed = True
                    break
            else:
                out.append(pymupdf.Rect(r))
        groups = out
    return groups


def _drawing_for(caption: pymupdf.Rect, drawings: list[pymupdf.Rect]) -> pymupdf.Rect | None:
    """The drawing just above or below the caption, overlapping it horizontally. Nearest wins."""
    best: tuple[float, pymupdf.Rect] | None = None
    for d in drawings:
        if max(d.width, d.height) < MIN_FIGURE_SIZE or min(d.width, d.height) < MIN_THICKNESS:
            continue
        if min(d.x1, caption.x1) - max(d.x0, caption.x0) <= 0:
            continue
        if d.y1 <= caption.y0 + 2:  # above the caption
            gap = caption.y0 - d.y1
        elif d.y0 >= caption.y1 - 2:  # below it
            gap = d.y0 - caption.y1
        else:
            continue
        if gap <= CAPTION_GAP and (best is None or gap < best[0]):
            best = (gap, d)
    return best[1] if best else None


def _captions(page: pymupdf.Page, found: dict[str, "CapturedFigure"]) -> list[tuple[pymupdf.Rect, str, str]]:
    """(bbox, number, text) of each caption line on the page.

    Works on lines, not blocks: a block can span the whole figure (its own labels are text too), which
    would put the caption's box on top of the drawing. A title on the next line is added only when it
    follows directly, with no blank line between.
    """
    out: list[tuple[pymupdf.Rect, str, str]] = []
    for block in page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        lines = [
            (pymupdf.Rect(ln["bbox"]), "".join(sp["text"] for sp in ln["spans"]).replace("\x00", "").strip())
            for ln in block["lines"]
        ]
        lines = [(r, t) for r, t in lines if t]
        for i, (rect, text) in enumerate(lines):
            m = CAPTION_RE.match(text)
            if not m or SENTENCE_RE.match(m["title"].strip()) or m["num"].upper() in found:
                continue
            bbox = pymupdf.Rect(rect)
            if not m["title"].strip() and i + 1 < len(lines):
                nxt, nxt_text = lines[i + 1]
                if nxt.y0 - rect.y1 < rect.height * 0.5 and not CAPTION_RE.match(nxt_text):
                    text, bbox = f"{text} {nxt_text}", bbox | nxt
            out.append((bbox, m["num"], " ".join(text.split())[:CAPTION_MAX_CHARS]))
    return out


def _owner(nodes: list[Node], page: int, caption: str) -> str | None:
    """The node the caption belongs to: among those covering the page, the one whose text holds the caption."""
    on_page = [n for n in nodes if n.page_start <= page <= n.page_end]
    if not on_page:
        return None
    head = " ".join(caption.split())[:40]
    for n in on_page:
        if head and head in " ".join(n.text.split()):
            return n.id
    return on_page[0].id


def capture_figures(pdf_bytes: bytes, manual: Manual, document_id: str, nodes: list[Node], dpi: int = 150) -> list[CapturedFigure]:
    """One CapturedFigure per distinct figure number, in page order. The first usable caption of a number wins."""
    found: dict[str, CapturedFigure] = {}
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            captions = _captions(page, found)
            if not captions:
                continue
            drawings = _merge(_graphics(page))
            for bbox, number, caption in captions:
                if number.upper() in found:
                    continue
                drawing = _drawing_for(bbox, drawings)
                if drawing is None:
                    continue  # no graphics next to it: a sentence, not a caption
                region = ((drawing | bbox) + (-PADDING, -PADDING, PADDING, PADDING)) & page.rect
                png = page.get_pixmap(clip=region, dpi=dpi).tobytes("png")
                label = f"Figure {number}"
                found[number.upper()] = CapturedFigure(
                    Figure(
                        id=str(uuid4()),
                        document_id=document_id,
                        manual=manual,
                        node_id=_owner(nodes, page.number + 1, caption),
                        label=label,
                        number=number,
                        caption=caption,
                        page=page.number + 1,
                        bbox=[round(v, 1) for v in region],
                        image_path=f"figures/{document_id}/{_slug(label)}.png",
                    ),
                    png,
                )
    log.info("capture_figures: %d figure(s): %s", len(found), [c.figure.label for c in found.values()])
    return list(found.values())
