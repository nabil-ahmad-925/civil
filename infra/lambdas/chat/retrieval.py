"""Finds the sections that answer a question, then brings along what they link to. Used by the chat Lambda.

1. Three searches run at the same time, each over every section (none narrows the others), and their lists are merged
   (reciprocal rank fusion), so a question does not have to be phrased in any particular way:
     meaning    the question is embedded; nearest sections by cosine similarity (finds paraphrases)
     keywords   the words of the question in the section text, rare words counting more (finds a name such as
                "Master Topographic File" that meaning-search ranks low). Either full-text rank with an exact-phrase
                bonus, or BM25 (KEYWORD_BACKEND); the scores of the two are on different scales
     heading    a section number ("1-1.8") or a heading typed in full or in part (finds "§1-1.8 Content and
                Development of Electronic Files" exactly)
   The merged top sections are the HITS; each says which searches found it (`matched_by`).
2. Each hit is expanded through the cross-reference graph, so an answer does not stop at "see Section 2-1.1":
     mentions       sections and figures the hit's own text points to (resolved references only)
     in_section     figures captioned inside the hit
Only references that were resolved to a real node or figure are followed. A linked section that is also a
hit is marked `also_hit` so the caller does not show it twice.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Literal

from app.ingestion.embeddings import Embedder
from app.models import Figure, Manual, Node
from app.repository.base import Repository

Relation = Literal["mentions", "mentioned_by"]
FigureRelation = Literal["in_section", "mentioned"]
MAX_K = 20
# Reciprocal rank fusion: a section scores sum(weight / (RRF_K + rank)) over the lists it is in.
RRF_K = 60
WEIGHTS = {"meaning": 1.0, "keywords": 1.0, "heading": 2.0}
# An exact heading or section number is what the person named: it goes first.
EXACT_BONUS = 1.0
# A keyword rank from this up means a meaningful word of the question is in the section. A question about something
# the manuals never mention matches no section at all, so any match is real evidence; the model still judges the answer.
# This is the value for the full-text rank (0..2); Settings.keyword_min_score gives the one that fits the backend in use.
KEYWORD_RELEVANT = 0.3
# Of the keyword matches only those near the best one count: the OR-search also returns sections sharing a single
# common word, and those must not push out the sections that hold the rare words.
KEYWORD_KEEP = 0.5  # of the best keyword rank
# Only the sections a hit's own text points to are brought along (not the ones that point to it). All of them are
# returned with it; the cap only guards against a runaway list.
MAX_MENTIONS = 100


@dataclass
class LinkedNode:
    relation: Relation
    mention: str  # the words in the text that make the reference
    node: Node
    also_hit: bool = False


@dataclass
class LinkedFigure:
    relation: FigureRelation
    figure: Figure
    mention: str | None = None


@dataclass
class Hit:
    node: Node
    score: float | None  # cosine similarity; None when the meaning-search did not return this section
    path: list[str]  # titles from the chapter down to the parent
    matched_by: list[str] = field(default_factory=list)  # "meaning", "keywords", "heading"
    keyword_rank: float | None = None
    heading_score: float | None = None
    linked: list[LinkedNode] = field(default_factory=list)
    figures: list[LinkedFigure] = field(default_factory=list)


def _paths(repo: Repository, nodes: list[Node]) -> dict[str, list[str]]:
    """Titles of each node's ancestors, chapter first. One query per level of depth, not per node."""
    parent = {n.id: n.parent_id for n in nodes}
    titles: dict[str, str] = {}
    frontier = {p for p in parent.values() if p}
    while frontier:
        found = repo.get_node_summaries(sorted(frontier))
        for s in found:
            titles[s.id] = s.title
            parent[s.id] = s.parent_id
        frontier = {s.parent_id for s in found if s.parent_id and s.parent_id not in titles}
    out: dict[str, list[str]] = {}
    for n in nodes:
        chain: list[str] = []
        cur, guard = n.parent_id, 0
        while cur and cur in titles and guard < 50:
            chain.insert(0, titles[cur])
            cur, guard = parent.get(cur), guard + 1
        out[n.id] = chain
    return out


def is_relevant(hit: Hit, min_score: float, keyword_min: float = KEYWORD_RELEVANT) -> bool:
    """Whether a hit is worth answering from: close in meaning, or its heading was named, or its text has the words."""
    return (
        (hit.score is not None and hit.score >= min_score)
        or (hit.heading_score is not None and hit.heading_score >= 0.8)
        or (hit.keyword_rank is not None and hit.keyword_rank >= keyword_min)
    )


def _fuse(lists: dict[str, list[tuple[str, float]]]) -> list[tuple[str, float]]:
    """Merged ranking of the three result lists, best first."""
    fused: dict[str, float] = {}
    for name, items in lists.items():
        rank, previous = 0, None
        for position, (node_id, value) in enumerate(sorted(items, key=lambda i: i[1], reverse=True), start=1):
            if previous is None or round(value, 3) != previous:  # equal scores share a rank (1, 2, 2, 2, 5): five
                rank, previous = position, round(value, 3)       # sections holding the same two rare words are all "first"
            fused[node_id] = fused.get(node_id, 0.0) + WEIGHTS[name] / (RRF_K + rank)
            if name == "heading" and value >= 0.99:
                fused[node_id] += EXACT_BONUS
    return sorted(fused.items(), key=lambda s: s[1], reverse=True)


def _run_searches(
    repo: Repository, embedder: Embedder, query: str, pool: int, manual: Manual | None
) -> tuple[list[tuple[str, float]], list[tuple[str, float]], list[tuple[str, float]]]:
    """(keywords, meaning, heading) at the same time: each is a network round trip, and none needs another's answer."""
    with ThreadPoolExecutor(max_workers=3) as executor:
        keywords = executor.submit(repo.match_nodes_text, query, pool, manual)
        meaning = executor.submit(lambda: repo.match_nodes(embedder.embed([query])[0], pool, manual))
        heading = executor.submit(repo.match_nodes_title, query, pool, manual)
        return keywords.result(), meaning.result(), heading.result()


def search(
    repo: Repository, embedder: Embedder, query: str, k: int = 5, manual: Manual | None = None, expand: bool = True
) -> list[Hit]:
    k = max(1, min(k, MAX_K))
    pool = max(k * 3, 15)  # candidates taken from each search before merging
    keywords, meaning, heading = _run_searches(repo, embedder, query, pool, manual)
    best = max((v for _, v in keywords), default=0.0)
    lists = {
        "meaning": meaning,
        "keywords": [(i, v) for i, v in keywords if v >= best * KEYWORD_KEEP],
        "heading": heading,
    }
    ranked = _fuse(lists)[:k]
    if not ranked:
        return []
    cosine, keyword, heading = dict(lists["meaning"]), dict(lists["keywords"]), dict(lists["heading"])
    score = {i: cosine.get(i) for i, _ in ranked}
    nodes = {n.id: n for n in repo.get_nodes(list(score))}
    hits = [
        Hit(
            node=nodes[i], score=cosine.get(i), path=[], keyword_rank=keyword.get(i), heading_score=heading.get(i),
            matched_by=[n for n, found in (("meaning", i in cosine), ("keywords", i in keyword), ("heading", i in heading)) if found],
        )
        for i, _ in ranked
        if i in nodes
    ]
    paths = _paths(repo, [h.node for h in hits])
    for h in hits:
        h.path = paths[h.node.id]
    if not expand:
        return hits

    out_refs = {h.node.id: [r for r in repo.refs_from(h.node.id) if r.status == "resolved"] for h in hits}
    own_figures = repo.figures_of_nodes(list(score))

    wanted_nodes = {r.target_node_id for rs in out_refs.values() for r in rs[:MAX_MENTIONS] if r.target_node_id}
    wanted_figures = {r.target_figure_id for rs in out_refs.values() for r in rs if r.target_figure_id}
    linked_nodes = {n.id: n for n in repo.get_nodes(sorted(wanted_nodes - set(nodes)))} | nodes
    linked_figures = {f.id: f for f in repo.get_figures(sorted(wanted_figures))}

    for h in hits:
        # Figures captioned inside the hit come first; a mention of one of them (its own caption) adds nothing.
        h.figures = [LinkedFigure("in_section", f) for f in own_figures if f.node_id == h.node.id]
        seen: set[str] = set()
        for r in out_refs[h.node.id]:
            if r.target_node_id and r.target_node_id in linked_nodes and r.target_node_id not in seen and r.target_node_id != h.node.id:
                if sum(1 for ln in h.linked if ln.relation == "mentions") < MAX_MENTIONS:
                    seen.add(r.target_node_id)
                    h.linked.append(LinkedNode("mentions", r.mention, linked_nodes[r.target_node_id], r.target_node_id in score))
            elif r.target_figure_id and r.target_figure_id in linked_figures:
                if all(lf.figure.id != r.target_figure_id for lf in h.figures):
                    h.figures.append(LinkedFigure("mentioned", linked_figures[r.target_figure_id], r.mention))
    return hits
