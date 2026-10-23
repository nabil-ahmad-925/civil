"""PDF -> ordered text lines with the formatting needed to spot headings."""

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf

# Share of the page height, at the top and at the bottom, that counts as the margin.
EDGE_FRACTION = 0.12
PAGE_NUMBER_RE = re.compile(r"^(?:page\s+)?\d{1,4}(?:\s+of\s+\d{1,4})?$", re.IGNORECASE)


@dataclass(frozen=True)
class Line:
    page: int  # 1-based
    text: str
    size: float
    bold: bool
    # In the top or bottom margin, where running headers, footers and page numbers sit.
    edge: bool = True


def read_lines(pdf_bytes: bytes) -> tuple[list[Line], int]:
    """Returns (lines in reading order, page count)."""
    lines: list[Line] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = doc.page_count
        for page in doc:
            top, bottom = page.rect.height * EDGE_FRACTION, page.rect.height * (1 - EDGE_FRACTION)
            for block in page.get_text("dict")["blocks"]:
                if block["type"] != 0:  # text blocks only
                    continue
                for ln in block["lines"]:
                    # Postgres text columns reject NUL (\u0000), which some PDFs emit for unmapped glyphs.
                    for s in ln["spans"]:
                        s["text"] = s["text"].replace("\x00", "")
                    spans = [s for s in ln["spans"] if s["text"].strip()]
                    if not spans:
                        continue
                    text = "".join(s["text"] for s in ln["spans"]).strip()
                    bold = all(bool(s["flags"] & 16) or "bold" in s["font"].lower() for s in spans)
                    edge = ln["bbox"][3] <= top or ln["bbox"][1] >= bottom
                    lines.append(Line(page.number + 1, text, round(max(s["size"] for s in spans), 1), bold, edge))
    return lines, page_count


def _shape(text: str) -> str:
    return re.sub(r"\d+", "#", text.lower())


def strip_running_lines(lines: list[Line], page_count: int) -> list[Line]:
    """Drops page numbers and running headers/footers (same line, digits aside, on most pages, in the margin).

    Only margin lines count: a justified paragraph can leave a lone word such as "Manual." on a line of its
    own on several pages, and that is body text, not a header.
    """
    pages_seen: dict[str, set[int]] = {}
    for ln in lines:
        if not ln.edge:
            continue
        pages_seen.setdefault(_shape(ln.text), set()).add(ln.page)
    threshold = max(2, page_count / 2)
    running = {shape for shape, pages in pages_seen.items() if len(pages) >= threshold}
    return [ln for ln in lines if not (ln.edge and (PAGE_NUMBER_RE.match(ln.text) or _shape(ln.text) in running))]


def dominant_size(lines: list[Line]) -> float:
    """The body text size: the most common font size, weighted by characters."""
    weights: Counter[float] = Counter()
    for ln in lines:
        weights[ln.size] += len(ln.text)
    return weights.most_common(1)[0][0] if weights else 0.0
