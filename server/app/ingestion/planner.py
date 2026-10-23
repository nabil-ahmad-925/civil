"""Decides where nodes START on a page. It never touches the text.

A planner looks at the numbered lines of ONE page and answers: which line numbers begin a new
heading? The chunker then cuts the original lines at those numbers, so every node's text is the PDF's
own text, never something a model wrote. A node ends where the next one starts, even on a later page,
so the planner only has to mark starts.

Each page comes with a little of its neighbours, so a heading cut by a page break can be read whole:
  before  the last lines of the previous page: context only, not numbered, cannot be chosen
  lines   the page being planned: numbered, the only place a heading may START
  after   the first lines of the next page: numbered on from `lines`, only there so a heading that
          starts at the bottom of this page can continue into it
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field, replace
from typing import Protocol, Sequence

import httpx

from .pdf_reader import Line

log = logging.getLogger(__name__)

MAX_LEVEL = 8
MAX_HEADING_LINES = 4  # a heading wraps over at most this many lines
SIZE_TOLERANCE = 0.6  # points; lines of one heading are set in the same size
MAX_LINE_CHARS = 300  # longer lines are shortened in the prompt only, never in the stored text


class PlannerError(RuntimeError):
    """The planner could not produce a plan (API error, refusal, unreadable answer)."""


@dataclass(frozen=True)
class PageInput:
    page: int
    lines: list[Line]  # this page's lines, page numbers and running headers already removed
    body_size: float  # the document's normal text size (used by the regex planner)
    context: str  # where we are in the manual so far
    before: list[Line] = field(default_factory=list)  # end of the previous page (context only)
    after: list[Line] = field(default_factory=list)  # start of the next page (a heading may run into it)


@dataclass(frozen=True)
class Start:
    line: int  # index into PageInput.lines of the heading's first line
    heading_lines: int  # how many consecutive lines the heading occupies (may run into `after`)
    section: str  # the number exactly as printed in the heading, e.g. "1-1.2" or "301.1"
    level: int  # 1 = chapter, 2 = section, 3 = subsection ...


class NodePlanner(Protocol):
    def plan_page(self, page: PageInput) -> list[Start]: ...


# --- helpers shared by every planner -------------------------------------------------------------


def _section_pattern(section: str) -> re.Pattern[str]:
    # The number as its own token: "1" must not match inside "1-1.2", "1-1" not inside "1-1.2".
    return re.compile(r"(?<![\w.\-])" + re.escape(section) + r"(?![\w\-]|\.\d)")


def _same_style(a: Line, b: Line) -> bool:
    return a.bold == b.bold and abs(a.size - b.size) <= SIZE_TOLERANCE


def validate_starts(starts: list[Start], lines: list[Line], after: Sequence[Line] = ()) -> list[Start]:
    """Keeps only starts the code can safely cut at. Anything doubtful is dropped (or trimmed) and logged.

    A heading must START on a line of the page. It may run on into `after` (the next page's preview),
    but only over lines set in the same style as its first line, and at most MAX_HEADING_LINES long.
    """
    everything = list(lines) + list(after)
    good: list[Start] = []
    next_free = 0  # first line not yet used by a previous heading
    for s in sorted(starts, key=lambda s: s.line):
        problem = None
        if not 0 <= s.line < len(lines):
            problem = "line number is outside the page (a heading can only start on the page itself)"
        elif s.heading_lines < 1:
            problem = "heading_lines must be at least 1"
        elif s.line + s.heading_lines > len(everything):
            problem = "heading_lines runs past the end of the page and its preview"
        elif s.line < next_free:
            problem = "overlaps the previous heading"
        elif not 1 <= s.level <= MAX_LEVEL:
            problem = "level out of range"
        elif not s.section.strip():
            problem = "empty section number"
        if problem:
            log.warning("Dropping planned start %s: %s", s, problem)
            continue

        # Trim a heading that swallows lines that do not look like part of it.
        span = everything[s.line : s.line + min(s.heading_lines, MAX_HEADING_LINES)]
        keep = 1
        while keep < len(span) and _same_style(span[0], span[keep]):
            keep += 1
        if keep < s.heading_lines:
            log.warning(
                "Trimming planned heading %s from %d to %d line(s): the rest is not set like the first line or is too long",
                s, s.heading_lines, keep,
            )
            s = replace(s, heading_lines=keep)

        heading = " ".join(ln.text for ln in everything[s.line : s.line + s.heading_lines])
        if not _section_pattern(s.section).search(heading):
            log.warning("Dropping planned start %s: section %r is not in the heading text %r", s, s.section, heading)
            continue
        good.append(s)
        next_free = s.line + s.heading_lines
    return good


# --- OpenAI --------------------------------------------------------------------------------------

SYSTEM_PROMPT = """You split pages of an engineering standards manual (Caltrans) into sections.
You get the numbered lines of ONE page (the current page), plus a little of the pages around it. Return every line of the current page where a NEW numbered heading starts.

Rules:
- A heading is a section title with its number as printed, for example "1-1.2 Application of Plan Preparation Standards", "301.1 Lane Width", "SECTION 1-1 GENERAL", or "CHAPTER 1" followed by the chapter title.
- A heading can wrap over several lines. Return its first line in "line" and how many consecutive lines it occupies in "heading_lines". All lines of one heading are set in the same style.
- A heading may only START on a line of the current page. Never return a line from the "previous page" or "next page" blocks as a start.
- If a heading starts at the bottom of the current page and its title continues on the next page, count the continuation lines from the "next page" block in "heading_lines". Those lines are numbered on from the current page.
- "section" is the heading's number exactly as printed, for example "1-1.2", "301.1", "1-1" or "1". Copy it; never reformat it.
- "level" is 1 for a chapter, 2 for a section, 3 for a subsection, and so on. Use the context to stay consistent with earlier pages.
- Do NOT return: numbered list items such as "1. Necessity", bullet points, table rows, figure or table captions, ordinary sentences, or repeated page headers.
- Text at the top of the current page before its first heading belongs to a heading from an earlier page. Do not return it. Use the "previous page" block to tell whether such text continues a list or a table.
- If no heading starts on the current page, return an empty list.
- You only choose line numbers. Never rewrite, shorten or invent text."""

RESPONSE_SCHEMA = {
    "name": "page_plan",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["starts"],
        "properties": {
            "starts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["line", "heading_lines", "section", "level"],
                    "properties": {
                        "line": {"type": "integer"},
                        "heading_lines": {"type": "integer"},
                        "section": {"type": "string"},
                        "level": {"type": "integer"},
                    },
                },
            }
        },
    },
}

RETRY_STATUS = {429, 500, 502, 503, 504}


def build_user_message(page: PageInput) -> str:
    def clip(text: str) -> str:
        return text[:MAX_LINE_CHARS]

    parts = [f"Context from earlier pages (not part of this page):\n{page.context}"]
    if page.before:
        tail = "\n".join(f"  {clip(ln.text)}" for ln in page.before)
        parts.append(f"End of the previous page (context only, do NOT return these lines):\n{tail}")
    numbered = "\n".join(f"[{i}] {clip(ln.text)}" for i, ln in enumerate(page.lines))
    parts.append(f"Current page {page.page} (return heading starts from THESE lines only):\n{numbered}")
    if page.after:
        first = len(page.lines)
        preview = "\n".join(f"[{first + i}] {clip(ln.text)}" for i, ln in enumerate(page.after))
        parts.append(
            "Start of the next page (preview only: a heading that starts on the current page may continue into "
            f"these lines, but never start a heading here):\n{preview}"
        )
    return "\n\n".join(parts)


class OpenAIPlanner:
    def __init__(
        self,
        api_key: str,
        model: str,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 2.0,
    ) -> None:
        self.model = model
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.client = client or httpx.Client(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=90,
        )

    def _request_body(self, page: PageInput) -> dict:
        body: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(page)},
            ],
            "response_format": {"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
        }
        if self.model.startswith(("gpt-4", "gpt-3")):  # newer reasoning models reject a temperature
            body["temperature"] = 0
        return body

    def _post(self, body: dict) -> dict:
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            try:
                res = self.client.post("/chat/completions", json=body)
            except httpx.TransportError as exc:
                if last:
                    raise PlannerError(f"Could not reach OpenAI: {exc}") from exc
                time.sleep(self.backoff_seconds * 2**attempt)
                continue
            if res.status_code in RETRY_STATUS and not last:
                time.sleep(self.backoff_seconds * 2**attempt)
                continue
            if res.status_code != 200:
                raise PlannerError(f"OpenAI returned {res.status_code}: {res.text[:300]}")
            return res.json()
        raise PlannerError("OpenAI request failed")  # unreachable; keeps type checkers happy

    def plan_page(self, page: PageInput) -> list[Start]:
        if not page.lines:
            return []
        message = self._post(self._request_body(page))["choices"][0]["message"]
        if message.get("refusal"):
            raise PlannerError(f"OpenAI refused page {page.page}: {message['refusal']}")
        try:
            starts = json.loads(message["content"])["starts"]
            return [
                Start(line=int(s["line"]), heading_lines=int(s["heading_lines"]), section=str(s["section"]).strip(), level=int(s["level"]))
                for s in starts
            ]
        except (KeyError, TypeError, ValueError) as exc:
            raise PlannerError(f"Could not read OpenAI's answer for page {page.page}: {exc}") from exc
