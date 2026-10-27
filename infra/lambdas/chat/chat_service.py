"""Answers a question from the manuals. Used only by the chat Lambda; it builds on the shared code in server/app/
(retrieval, repository, models).

retrieve (retrieval.py, next to this file: nearest sections plus what they cross-reference)
  -> number the sections as sources S1, S2, ... -> ask the model to answer ONLY from them, citing [S#]
  -> return the answer with the sources, so the client can show where each claim comes from.

If nothing in the manuals is close enough, the model is not asked: the answer says the manuals do not cover it.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from app.config import Settings
from app.ingestion.embeddings import Embedder
from app.models import Manual
from app.repository.base import Repository
from retrieval import Hit, is_relevant, search
from llm_client import ChatModel

NOT_COVERED = "The manuals loaded in the knowledge base do not cover this question."
MAX_SOURCES = 12
SHORT_FOLLOW_UP_WORDS = 6
# "where / which section / which article ..." asks for the places a topic appears, not for one explanation.
LOCATE_RE = re.compile(r"\b(where|which\s+(?:section|article|chapter|page|manual|part)s?|what\s+(?:section|article|chapter|page))\b", re.I)
LOCATE_NOTE = (
    "This question asks WHERE something appears. List every source above that actually mentions it, each with its "
    "section title, pages and citation, and say which one explains it best. Skip sources that do not mention it."
)

SYSTEM_PROMPT = """You answer questions about civil engineering standards (Caltrans manuals) using ONLY the numbered sources you are given.

Rules:
- Use only what the sources say. Never add requirements, numbers or section numbers from your own knowledge.
- Cite every claim with the source number in square brackets, like [S2]. Several sources: [S1][S3].
- Keep the manual's own wording for requirements ("shall", "must", "may") and for numbers and units.
- Some sources are marked as referenced from another source: they are the sections that the main sources point to ("see Section ..."). Use them to complete the answer and say which section the requirement comes from.
- If a source lists figures, mention the figure by its label (for example "Figure 2-7") when it helps. Do not describe what a figure looks like; you only have its caption.
- If the sources do not answer the question, or only partly, say exactly what is missing. Do not guess.
- Be concise: a short answer first, then the details that matter."""


@dataclass
class SourceFigure:
    id: str
    label: str
    caption: str
    page: int
    url: str  # temporary link to the picture


@dataclass
class Source:
    ref: str  # "S1"
    node_id: str
    manual: Manual
    section: str
    title: str
    page_start: int
    page_end: int
    score: float | None  # similarity for a direct match, None for a linked section
    relation: Literal["match", "mentions", "mentioned_by"]
    via: str | None  # ref of the source it is linked from
    mention: str | None
    text: str
    path: list[str] = field(default_factory=list)
    figures: list[SourceFigure] = field(default_factory=list)
    matched_by: list[str] = field(default_factory=list)  # for a direct match: meaning, keywords, heading


@dataclass
class ChatAnswer:
    answer: str
    covered: bool
    sources: list[Source]
    model: str | None = None


def asks_where(message: str) -> bool:
    return bool(LOCATE_RE.search(message))


def _search_query(message: str, history: list[dict[str, str]]) -> str:
    """A short follow-up ("and for ramps?") means nothing alone: put the previous question in front of it."""
    if len(message.split()) >= SHORT_FOLLOW_UP_WORDS:
        return message
    previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    return f"{previous} {message}".strip()


def _sources(repo: Repository, hits: list[Hit], url_seconds: int) -> list[Source]:
    out: list[Source] = []
    by_node: dict[str, Source] = {}

    def figures(h_figs) -> list[SourceFigure]:
        return [
            SourceFigure(lf.figure.id, lf.figure.label, lf.figure.caption, lf.figure.page, repo.signed_url(lf.figure.image_path, url_seconds))
            for lf in h_figs
        ]

    for h in hits:  # direct matches first, so they get the first numbers
        n = h.node
        s = Source(f"S{len(out) + 1}", n.id, n.manual, n.section, n.title, n.page_start, n.page_end, h.score, "match", None, None, n.text, h.path, figures(h.figures), h.matched_by)
        out.append(s)
        by_node[n.id] = s
    for h in hits:
        via = by_node[h.node.id].ref
        for ln in h.linked:
            if ln.node.id in by_node or len(out) >= MAX_SOURCES:
                continue
            n = ln.node
            s = Source(f"S{len(out) + 1}", n.id, n.manual, n.section, n.title, n.page_start, n.page_end, None, ln.relation, via, ln.mention, n.text)
            out.append(s)
            by_node[n.id] = s
    return out


_WORD_RE = re.compile(r"[a-z0-9]{3,}")
# Words of a question that say nothing about the topic.
_ASKING = frozenset(
    "the and are was were for from with what which where when who how why does did can could would should about into "
    "that this these those there their them then than have has had not you your want know tell show give need please "
    "explain describe article info information question answer section chapter page topic mention mentioned refer "
    "referred got".split()
)
BLOCK_CHARS = 500


def _excerpt(text: str, question: str, limit: int) -> str:
    """The text, or when it is longer than `limit` the start of it plus the passages that hold the question's words.

    Cutting a long section at its first characters loses a mention further down ("the Master Topographic file ..."
    in the middle of 14,000 characters), so the passages are chosen by how many of the question's words they contain.
    """
    if len(text) <= limit:
        return text
    words = {w for w in _WORD_RE.findall(question.lower()) if w not in _ASKING}
    blocks: list[str] = []  # whole lines grouped to about BLOCK_CHARS
    for line in text.split("\n"):
        if blocks and len(blocks[-1]) + len(line) < BLOCK_CHARS:
            blocks[-1] += "\n" + line
        else:
            blocks.append(line)
    scores = [len(words & set(_WORD_RE.findall(b.lower()))) for b in blocks]
    keep = {0}  # the start gives the context of the section
    used = len(blocks[0])
    for i in sorted(range(len(blocks)), key=lambda i: (-scores[i], i)):
        if scores[i] == 0 or i in keep:
            continue
        if used + len(blocks[i]) > limit:
            continue
        keep.add(i)
        used += len(blocks[i])
    out, last = [], -1
    for i in sorted(keep):
        out.append(("\n […]\n" if i != last + 1 else "\n") + blocks[i] if out else blocks[i])
        last = i
    return "".join(out).rstrip() + ("" if last == len(blocks) - 1 else "\n […]")


def _context(sources: list[Source], per_source: int, total: int, question: str = "") -> str:
    blocks: list[str] = []
    used = 0
    for s in sources:
        text = _excerpt(s.text.strip(), question, per_source)
        head = f"[{s.ref}] {s.manual} {s.title} (pp. {s.page_start}-{s.page_end})"
        if s.relation == "mentions":
            head += f" — referenced from {s.via}: \"{s.mention}\""
        elif s.relation == "mentioned_by":
            head += f" — mentions {s.via}: \"{s.mention}\""
        figs = "".join(f"\nFigures: {f.label} (p. {f.page}) {f.caption}" for f in s.figures[:1]) if s.figures else ""
        if len(s.figures) > 1:
            figs = "\nFigures: " + "; ".join(f"{f.label} (p. {f.page}) {f.caption}" for f in s.figures)
        block = f"{head}\n{text or '(no text under this heading)'}{figs}"
        if used + len(block) > total and blocks:
            break
        blocks.append(block)
        used += len(block)
    return "\n\n---\n\n".join(blocks)


def answer_question(
    repo: Repository,
    embedder: Embedder,
    llm: ChatModel,
    settings: Settings,
    message: str,
    history: list[dict[str, str]] | None = None,
    manual: Manual | None = None,
) -> ChatAnswer:
    history = [m for m in (history or []) if m.get("role") in ("user", "assistant") and m.get("content", "").strip()]
    history = history[-settings.chat_history_messages :]
    locate = asks_where(message)
    top_k = settings.chat_locate_top_k if locate else settings.chat_top_k
    hits = search(repo, embedder, _search_query(message, history), top_k, manual, expand=True)
    hits = [h for h in hits if is_relevant(h, settings.chat_min_score, settings.keyword_min_score)]
    if not hits:
        return ChatAnswer(NOT_COVERED, False, [])

    sources = _sources(repo, hits, settings.figure_url_seconds)
    context = _context(sources, settings.chat_source_chars, settings.chat_context_chars, message)
    user = f"Sources:\n\n{context}\n\n=====\nQuestion: {message}" + (f"\n\n{LOCATE_NOTE}" if locate else "")
    text = llm.complete(SYSTEM_PROMPT, [*history, {"role": "user", "content": user}])
    # Show the sources the answer cites. An answer that cites none shows the direct matches, so the reader sees what was used.
    cited = set(re.findall(r"\[(S\d+)\]", text))
    shown = [s for s in sources if s.ref in cited] or [s for s in sources if s.relation == "match"]
    return ChatAnswer(text, True, shown, getattr(llm, "model", None))
