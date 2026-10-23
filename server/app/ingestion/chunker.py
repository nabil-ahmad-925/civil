"""Cuts a manual into nodes, one per heading, holding the text up to the next heading.

Page by page, a planner (planner.py) says which lines START a heading. This module does the cutting:
a node runs from its heading to the next heading, even across pages, and its text is the PDF's own
lines, untouched. Sections are never split, so tables stay whole inside the section that holds them.

RegexPlanner (below) finds headings by shape and formatting; it is free and offline, and is the
fallback when no OpenAI key is set. OpenAIPlanner (planner.py) asks a model instead.
"""

import logging
import re
from dataclasses import dataclass
from uuid import uuid4

from ..models import Manual, Node
from .pdf_reader import Line, dominant_size, read_lines, strip_running_lines
from .planner import NodePlanner, PageInput, Start, validate_starts

HEADING_RE = re.compile(
    r"^(?:(?P<kw>Chapter|Topic|Index|Section|Article)\s+)?"
    r"(?P<num>\d+(?:\.\d+)*)\s*[-–—:.]?\s+"
    r"(?P<title>[A-Z][^\n]*)$"
)
log = logging.getLogger(__name__)

# A whole manual can be tens of thousands of lines. Raise this to see more of them in the log.
LOG_LINES_LIMIT = 1000
# Each node's text is shown in the log up to this many characters. Raise it to see more, 0 for headings only.
NODE_TEXT_LOG_LIMIT = 1000

# Lines of the previous / next page shown to the planner with each page.
DEFAULT_CONTEXT_LINES = 6

MAX_HEADING_CHARS = 120
SIZE_MARGIN = 0.5  # points above body size that still counts as a heading


@dataclass
class ChunkResult:
    nodes: list[Node]
    pages: int
    # Lines before the first heading (cover, table of contents...). Not stored.
    unassigned_lines: int


def is_heading(line: Line, body_size: float) -> re.Match[str] | None:
    if len(line.text) > MAX_HEADING_CHARS:
        return None
    m = HEADING_RE.match(line.text)
    if not m or m["title"].rstrip().endswith((".", ",", ";")):  # a sentence, not a title
        return None
    return m if (line.bold or line.size >= body_size + SIZE_MARGIN) else None


def _log_file_info(manual: Manual, document_id: str, size_bytes: int, lines: list[Line], pages: int) -> None:
    """Logs what read_lines() produced, before any cutting happens."""
    log.info(
        "chunk_pdf input: manual=%s document_id=%s size=%d bytes pages=%d lines=%d",
        manual, document_id, size_bytes, pages, len(lines),
    )
    for i, ln in enumerate(lines[:LOG_LINES_LIMIT]):
        log.info("  [%d] page=%d size=%.1f bold=%s | %s", i, ln.page, ln.size, ln.bold, ln.text)
    if len(lines) > LOG_LINES_LIMIT:
        log.info("  ... %d more lines not logged (LOG_LINES_LIMIT=%d)", len(lines) - LOG_LINES_LIMIT, LOG_LINES_LIMIT)


def _log_nodes(nodes: list[Node]) -> None:
    """One block per node: where it sits, how big it is, and its text."""
    sections = {n.id: n.section for n in nodes}
    for n in nodes:
        parent = f"§{sections[n.parent_id]}" if n.parent_id else "none"
        head = (
            f"node {n.ordinal + 1}/{len(nodes)}  §{n.section}  {n.title!r}\n"
            f"    parent={parent}  chapter={n.chapter!r}  pages={n.page_start}-{n.page_end}  "
            f"{len(n.text)} chars, {n.text.count(chr(10)) + 1 if n.text else 0} lines"
        )
        body = n.text[:NODE_TEXT_LOG_LIMIT]
        shown = "\n".join(f"    | {line}" for line in body.split("\n")) if body else "    | (no text under this heading)"
        more = f"\n    | ... {len(n.text) - NODE_TEXT_LOG_LIMIT} more characters" if len(n.text) > NODE_TEXT_LOG_LIMIT else ""
        log.info("%s\n%s%s", head, shown if NODE_TEXT_LOG_LIMIT else "", more)


class RegexPlanner:
    """Finds headings by shape ("301.1 Lane Width") and formatting (bold or larger than body text)."""

    def plan_page(self, page: PageInput) -> list[Start]:
        starts: list[Start] = []
        for i, ln in enumerate(page.lines):
            m = is_heading(ln, page.body_size)
            if not m:
                continue
            section = m["num"]
            if m["kw"] == "Chapter":
                level = 1
            else:
                level = max(section.count(".") + 1, 2 if m["kw"] == "Topic" else 1)
            starts.append(Start(line=i, heading_lines=1, section=section, level=level))
        return starts


class _NodeBuilder:
    """Turns a stream of pages plus planned starts into nodes."""

    def __init__(self, manual: Manual, document_id: str) -> None:
        self.manual = manual
        self.document_id = document_id
        self.nodes: list[Node] = []
        # Every line ends up in exactly one of these three, which chunk_pdf checks at the end.
        self.heading_lines = 0
        self.body_lines = 0
        self.unassigned = 0  # before the first heading (cover, contents...)
        self._chapter = ""
        self._stack: list[tuple[int, str, str]] = []  # (level, node id, label) of the open headings
        self._open: dict | None = None
        self._body: list[Line] = []

    def context(self) -> str:
        path = " > ".join(label for _, _, label in self._stack) or "none yet (start of the document)"
        return f"Current position: {path}"

    def add_page(self, page: int, lines: list[Line], starts: list[Start], after: list[Line]) -> int:
        """Adds one page. Returns how many lines at the top of the NEXT page a heading took from it."""
        by_line = {s.line: s for s in starts}
        everything = lines + after
        i = 0
        while i < len(lines):
            start = by_line.get(i)
            if start:
                self._flush()
                self._open_node(start, everything[i : i + start.heading_lines], page)
                self.heading_lines += start.heading_lines
                i += start.heading_lines
            else:
                if self._open is not None:
                    self._body.append(lines[i])
                    self.body_lines += 1
                else:
                    self.unassigned += 1
                i += 1
        return max(0, i - len(lines))

    def finish(self) -> list[Node]:
        self._flush()
        return self.nodes

    def _open_node(self, start: Start, heading: list[Line], page: int) -> None:
        # The heading exactly as printed ("SECTION 1-1 GENERAL"). A title that wraps over several lines
        # is joined with single spaces; nothing is removed.
        title = " ".join(ln.text for ln in heading)
        node_id = str(uuid4())
        while self._stack and self._stack[-1][0] >= start.level:
            self._stack.pop()
        parent_id = self._stack[-1][1] if self._stack else None
        label = title
        self._stack.append((start.level, node_id, label))
        if start.level == 1:
            self._chapter = label
        self._open = {
            "id": node_id,
            "parent_id": parent_id,
            "section": start.section,
            "title": title,
            "page": page,
            "end_page": heading[-1].page,  # a heading can run onto the next page
            "chapter": self._chapter,
        }
        self._body = []

    def _flush(self) -> None:
        cur = self._open
        if cur is None:
            return
        self.nodes.append(
            Node(
                id=cur["id"],
                document_id=self.document_id,
                manual=self.manual,
                chapter=cur["chapter"],
                section=cur["section"],
                title=cur["title"],
                parent_id=cur["parent_id"],
                page_start=cur["page"],
                page_end=self._body[-1].page if self._body else cur["end_page"],
                ordinal=len(self.nodes),
                text="\n".join(ln.text for ln in self._body),
            )
        )
        self._open = None
        self._body = []


def chunk_pdf(
    pdf_bytes: bytes,
    manual: Manual,
    document_id: str,
    planner: NodePlanner | None = None,
    context_lines: int = DEFAULT_CONTEXT_LINES,
) -> ChunkResult:
    """Cuts the PDF into nodes.

    context_lines: how many lines of the previous page (context) and of the next page (preview) the
    planner is shown with each page. 0 shows only the page itself.
    """
    planner = planner or RegexPlanner()
    lines, pages = read_lines(pdf_bytes)
    _log_file_info(manual, document_id, len(pdf_bytes), lines, pages)
    lines = strip_running_lines(lines, pages)
    body_size = dominant_size(lines)

    by_page: dict[int, list[Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.page, []).append(ln)
    page_numbers = list(by_page)
    window = max(0, context_lines)

    builder = _NodeBuilder(manual, document_id)
    taken = 0  # lines at the top of this page already used by a heading that began on the previous page
    for n, page_no in enumerate(page_numbers):
        page_lines = by_page[page_no][taken:]
        taken = 0
        if not page_lines:
            continue
        before = by_page[page_numbers[n - 1]][-window:] if window and n > 0 else []
        after = by_page[page_numbers[n + 1]][:window] if window and n + 1 < len(page_numbers) else []

        page_input = PageInput(
            page=page_no, lines=page_lines, body_size=body_size, context=builder.context(), before=before, after=after,
        )
        starts = validate_starts(planner.plan_page(page_input), page_lines, after)
        log.info(
            "chunk_pdf page %d (%d of %d with text): %d heading(s) %s",
            page_no, n + 1, len(page_numbers), len(starts), [(s.line, s.section, s.level) for s in starts],
        )
        taken = builder.add_page(page_no, page_lines, starts, after)
        if taken:
            log.info("chunk_pdf page %d: a heading continues onto page %d (%d line(s))", page_no, page_numbers[n + 1], taken)
    nodes = builder.finish()

    accounted = builder.heading_lines + builder.body_lines + builder.unassigned
    if accounted != len(lines):  # a line went missing or was counted twice: never save a damaged result
        raise RuntimeError(f"Chunking lost track of lines: {accounted} accounted for, {len(lines)} read.")
    _log_nodes(nodes)
    log.info(
        "chunk_pdf done: %d nodes, %d heading lines, %d body lines, %d before the first heading",
        len(nodes), builder.heading_lines, builder.body_lines, builder.unassigned,
    )
    return ChunkResult(nodes=nodes, pages=pages, unassigned_lines=builder.unassigned)
