"""The two passes that link saved cross-references to real nodes.

internal_pass       same-manual references; runs when one document is ingested.
cross_manual_pass   references into another manual; re-checked every time a document is
                    ingested or removed, so it settles as soon as both manuals are in.
"""

import re

from ..models import CrossRef, Document, Manual
from ..repository.base import Repository

FIGURE_LABEL_RE = re.compile(r"\bFigure\s+(\S+)$")


def figure_number(ref: CrossRef) -> str | None:
    """"2-7" for a reference labelled "PPM Figure 2-7"; None for anything else."""
    m = FIGURE_LABEL_RE.search(ref.target_label) if not ref.target_section else None
    return m[1].upper() if m else None


# How a heading can carry its kind word: the section "Topic 103" is stored as printed, while a reference to it
# ("Highway Design Manual Topic 103") yields the number 103.
SECTION_WORDS = ("Topic", "Chapter", "Section", "Index", "Article")
_SWAP_SEPARATORS = str.maketrans(".-", "-.")
_WORD_RE = re.compile(r"\b(" + "|".join(SECTION_WORDS) + r")\b", re.IGNORECASE)


def find_section(index: dict[str, str], ref: CrossRef) -> str | None:
    """Node id of the section a reference points at, or None.

    Tries the number as saved, then with a kind word in front ("Topic 103"; the word the mention uses goes first), and,
    for a paragraph reference such as "210.2(3)(c)", the section that holds it ("210.2"). Last, the number with "." and
    "-" swapped, because a mention may write "Section 2-6" for what the manual numbers "2.6".
    """
    if not ref.target_section:
        return None
    number = ref.target_section.strip()
    candidates = [number]
    whole = re.sub(r"\(.*$", "", number).strip()  # "210.2(3)(c" -> "210.2"
    if whole and whole != number:
        candidates.append(whole)
    # The same section is written "2.6" and "2-6" in different places ("Section 2-6" / "Section 2.6 of the CADD ...").
    candidates += [swapped for c in list(candidates) if (swapped := c.translate(_SWAP_SEPARATORS)) != c]
    mentioned = [w.capitalize() for w in _WORD_RE.findall(ref.mention)]
    words = [*mentioned, *(w for w in SECTION_WORDS if w not in mentioned)]
    for c in candidates:
        for key in (c, *(f"{w} {c}" for w in words)):
            if key in index:
                return index[key]
    return None


def _figures(repo: Repository, doc_id: str) -> dict[str, str]:
    return {number.upper(): fid for number, fid in repo.figure_index(doc_id).items()}


def internal_pass(repo: Repository, doc: Document) -> dict[str, int]:
    index = repo.section_index(doc.id)
    figures = _figures(repo, doc.id)
    pending = repo.list_refs(doc.id, "unresolved")
    changed = []
    for ref in pending:
        number = figure_number(ref)
        if number and ref.target_manual == doc.manual:
            if number in figures:
                ref.status = "resolved"
                ref.target_figure_id = figures[number]
                ref.resolved_by = "internal_pass"
                changed.append(ref)
            continue
        node_id = find_section(index, ref)
        if node_id and ref.target_manual == doc.manual:
            ref.status = "resolved"
            ref.target_node_id = node_id
            ref.resolved_by = "internal_pass"
            changed.append(ref)
    repo.save_refs(changed)
    # Left as unresolved: tables, figures that were not captured, and sections the chunker did not find.
    return {"resolved": len(changed), "still_unresolved": len(pending) - len(changed)}


def cross_manual_pass(repo: Repository, docs: list[Document]) -> dict[str, int]:
    """docs: the current document of each manual that is ingested (at most one per manual).

    Recomputes every cross-manual reference of those documents from scratch, so it also fixes
    references left dangling when a target document was replaced or deleted:
      target manual not ingested yet        -> awaiting_cross
      target manual ingested, section or figure found -> resolved
      target manual ingested, no such section or figure -> unresolved (final until that manual changes)
    """
    by_manual: dict[Manual, Document] = {d.manual: d for d in docs}
    indexes = {m: repo.section_index(d.id) for m, d in by_manual.items()}
    figure_indexes = {m: _figures(repo, d.id) for m, d in by_manual.items()}
    counts = {"resolved": 0, "unresolved": 0, "waiting_for_other_manual": 0}

    for doc in docs:
        changed = []
        for ref in repo.list_refs(doc.id):
            if ref.target_manual == doc.manual or ref.target_manual == "OTHER":
                continue  # same-manual refs belong to the internal pass; other documents are never linked
            before = (ref.status, ref.target_node_id, ref.target_figure_id)
            node_id = figure_id = None
            number = figure_number(ref)
            if ref.target_manual not in by_manual:
                ref.status, ref.resolved_by = "awaiting_cross", None
                counts["waiting_for_other_manual"] += 1
            else:
                if number:
                    figure_id = figure_indexes[ref.target_manual].get(number)
                elif ref.target_section:
                    node_id = find_section(indexes[ref.target_manual], ref)
                if node_id or figure_id:
                    ref.status, ref.resolved_by = "resolved", "cross_manual_pass"
                    counts["resolved"] += 1
                else:
                    ref.status, ref.resolved_by = "unresolved", None
                    counts["unresolved"] += 1
            ref.target_node_id, ref.target_figure_id = node_id, figure_id
            if (ref.status, ref.target_node_id, ref.target_figure_id) != before:
                changed.append(ref)
        repo.save_refs(changed)
    return counts
