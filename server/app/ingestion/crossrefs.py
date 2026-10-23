"""Finds "see Section X" style mentions in node text with a model and saves them, unresolved, verbatim.

The model only POINTS at the text: it returns the phrase it found and what the phrase points at. Code then
checks that the phrase really is in the node's text, so a saved `mention` is always the source's own words.

Status at capture:
  unresolved      points into the same manual; the internal pass links it later
  awaiting_cross  points at another manual; only the cross-manual pass can link it
  external        points at a document that is not PPM, HDM or CADD; kept as text, never linked
Table/Figure mentions have no section number to look up, so they stay unresolved.
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol
from uuid import uuid4

import httpx

from ..models import CrossRef, Manual, Node, TargetManual

log = logging.getLogger(__name__)

MANUALS: tuple[Manual, ...] = ("PPM", "HDM", "CADD")
RETRY_STATUS = {429, 500, 502, 503, 504}
KIND_LABEL = {"table": "Table", "figure": "Figure"}
# A model skips references buried in a long paragraph, so each call gets a short piece. Pieces overlap so a
# reference cut by a boundary is whole in one of them.
CHUNK_CHARS = 1200
CHUNK_OVERLAP = 250


class RefExtractionError(RuntimeError):
    """The model could not produce an answer (API error, refusal, unreadable answer)."""


class RefExtractor(Protocol):
    def find(self, node: Node) -> list[dict[str, Any]]:
        """Raw items: {mention, target_manual, target_document, target_kind, number}. Not yet checked against the text."""
        ...


SYSTEM_PROMPT = """You find cross-references in one section of an engineering standards manual (Caltrans).
A cross-reference is a place where the text sends the reader to another section, index, topic, chapter, table or figure, in this manual or in another one (PPM = Plans Preparation Manual, HDM = Highway Design Manual, CADD = CADD Users Manual).

Return one item per thing referred to:
- "mention": the reference itself copied EXACTLY from the text, from the kind word ("Section", "Index", "Chapter", "Table"...) through the manual name if one is given, for example "Section 2.6 of the CADD Users Manual", "Index 301.5" or "Table 2.4A". Leave out introducing words such as "Refer to" or "see". Never rewrite, shorten or correct it.
- "target_manual": PPM, HDM or CADD when the phrase names that manual (by full name or abbreviation). "same" when it names no manual or says "this manual". "other" for any other document.
- "target_kind": "section" for a section, index, topic, chapter or appendix; "table"; or "figure".
- "target_document": only when target_manual is "other": the name of that document exactly as printed in the text (for example "Project Development Procedures Manual"). Otherwise an empty string.
- "number": the number exactly as printed, for example "2.6", "301.5", "2.4A". No trailing punctuation.

Examples (these are different from the text you will get):
- "as defined in Section 7.3 of the CADD User's Manual" -> mention "Section 7.3 of the CADD User's Manual", target_manual CADD, kind section, number 7.3
- "refer to Index 205.4" -> mention "Index 205.4", target_manual same, kind section, number 205.4
- "shown in Table 9.2B of this manual" -> target_manual same, kind table, number 9.2B
- "see Chapter 14 of the Project Development Procedures Manual" -> mention "Chapter 14 of the Project Development Procedures Manual", target_manual other, target_document "Project Development Procedures Manual", kind section, number 14

Rules:
- Read the WHOLE text, including long paragraphs, before answering. A reference buried in the middle of a long paragraph counts as much as one at the start. Missing a reference is worse than returning an extra one.
- Only items with a number: a mention such as "refer to the CADD Users Manual" with no section, table or figure number is not returned.
- "other" is for a manual or document that is not PPM, HDM or CADD (for example the Project Development Procedures Manual, a law, or a standard from another body). Still return it, with target_document set.
- A phrase that points at several targets, such as "Sections 2.1 and 2.3 of the CADD", gives one item per target; each repeats the full phrase as its "mention".
- Ignore numbers that are not references: dimensions, units, dates, list numbering, equation numbers, and the section's own heading number.
- If the text has no cross-references, return an empty list."""

RESPONSE_SCHEMA = {
    "name": "cross_refs",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["refs"],
        "properties": {
            "refs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["mention", "target_manual", "target_document", "target_kind", "number"],
                    "properties": {
                        "mention": {"type": "string"},
                        "target_manual": {"type": "string", "enum": [*MANUALS, "same", "other"]},
                        "target_document": {"type": "string"},
                        "target_kind": {"type": "string", "enum": ["section", "table", "figure"]},
                        "number": {"type": "string"},
                    },
                },
            }
        },
    },
}


class OpenAIRefExtractor:
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

    def _body(self, node: Node, text: str) -> dict:
        user = f"This section belongs to the {node.manual}. Its number is {node.section} ({node.title}).\n\nText:\n{text}"
        body: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
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
                    raise RefExtractionError(f"Could not reach OpenAI: {exc}") from exc
                time.sleep(self.backoff_seconds * 2**attempt)
                continue
            if res.status_code in RETRY_STATUS and not last:
                time.sleep(self.backoff_seconds * 2**attempt)
                continue
            if res.status_code != 200:
                raise RefExtractionError(f"OpenAI returned {res.status_code}: {res.text[:300]}")
            return res.json()
        raise RefExtractionError("OpenAI request failed")  # unreachable; keeps type checkers happy

    def find(self, node: Node) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for piece in split_text(node.text):
            message = self._post(self._body(node, piece))["choices"][0]["message"]
            if message.get("refusal"):
                raise RefExtractionError(f"OpenAI refused section {node.section}: {message['refusal']}")
            try:
                found.extend(json.loads(message["content"])["refs"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RefExtractionError(f"Could not read OpenAI's answer for section {node.section}: {exc}") from exc
        return found  # the same reference seen in two overlapping pieces is merged by extract_refs


def split_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Overlapping pieces of at most `size` characters, cut at whitespace. Short text stays one piece."""
    if len(text) <= size:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind(" ", start + size // 2, end)
            cut = text.rfind("\n", start + size // 2, end) if cut < 0 else max(cut, text.rfind("\n", start + size // 2, end))
            end = cut if cut > start else end
        pieces.append(text[start:end])
        if end >= len(text):
            break
        nxt = end - overlap
        space = text.find(" ", nxt, end)  # start the next piece on a word
        start = space + 1 if space >= 0 else end
    return pieces


def _squash(text: str) -> str:
    """One space between words, so a phrase broken over PDF lines still compares equal."""
    return " ".join(text.split())


def _has_number(mention: str, number: str) -> bool:
    """The number as its own token: "2.6" must not match inside "12.6" or "2.6.1"."""
    return bool(re.search(r"(?<![\w.\-])" + re.escape(number) + r"(?![\w\-]|\.\d)", mention))


def extract_refs(node: Node, extractor: RefExtractor) -> list[CrossRef]:
    # Nothing to find without a number, so skip the call.
    if not re.search(r"\d", node.text):
        return []
    text = _squash(node.text)
    refs: list[CrossRef] = []
    seen: dict[tuple[TargetManual, str], int] = {}  # target -> index in refs
    for item in extractor.find(node):
        mention = _squash(str(item.get("mention", "")))
        number = str(item.get("number", "")).strip().rstrip(".,;:)")
        kind = item.get("target_kind")
        manual = item.get("target_manual")
        document = _squash(str(item.get("target_document", "")))
        if manual == "same":
            manual = node.manual
        if manual == "other":
            if not document or document not in text:
                log.warning("Dropping reference %r in %s %s: other document %r is not in the text", mention, node.manual, node.section, document)
                continue
            manual = "OTHER"
        if manual not in (*MANUALS, "OTHER") or kind not in ("section", *KIND_LABEL) or not number:
            log.warning("Dropping reference %r in %s %s: unusable fields", item, node.manual, node.section)
            continue
        if not mention or mention not in text:
            log.warning("Dropping reference %r in %s %s: the phrase is not in the text", mention, node.manual, node.section)
            continue

        if not _has_number(mention, number):
            log.warning("Dropping reference %r in %s %s: the phrase does not contain %r", mention, node.manual, node.section, number)
            continue

        target: TargetManual = manual
        section = number if kind == "section" else None
        if target == node.manual and section == node.section:
            continue  # a section naming itself
        name = document if target == "OTHER" else target
        label = f"{name} {number}" if section else f"{name} {KIND_LABEL[kind]} {number}"
        if (target, label) in seen:
            # Same target named twice: keep the fuller wording ("Section 2.6 of the CADD Users Manual").
            kept = refs[seen[(target, label)]]
            if len(mention) > len(kept.mention):
                kept.mention = mention
            continue
        seen[(target, label)] = len(refs)

        refs.append(
            CrossRef(
                id=str(uuid4()),
                document_id=node.document_id,
                from_node_id=node.id,
                mention=mention,
                target_manual=target,
                target_section=section,
                target_label=label,
                status="external" if target == "OTHER" else "unresolved" if target == node.manual else "awaiting_cross",
            )
        )
    return refs


def extract_all(nodes: list[Node], extractor: RefExtractor, workers: int = 8) -> list[CrossRef]:
    """References of every node, in node order. Nodes are sent to the model a few at a time."""
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        per_node = pool.map(lambda n: extract_refs(n, extractor), nodes)
        return [r for refs in per_node for r in refs]
