import re

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_repo
from ..models import CrossRef, NodeSummary
from ..repository.base import Repository
from ..schemas import (
    ConditionOut,
    FigureOut,
    NodeDetailOut,
    NodeRefOut,
    NodeSummaryOut,
    RefIncomingOut,
    RefOutgoingOut,
    SourceOut,
    TableOut,
)

router = APIRouter(prefix="/api/nodes", tags=["nodes"])

MAX_TERMS = 6
# Characters that mean something in a database filter. Dropped from search words so one cannot
# change what is being searched.
FILTER_CHARS = re.compile(r'[*%_\\",()]')


def search_terms(q: str) -> list[str]:
    return FILTER_CHARS.sub(" ", q).split()[:MAX_TERMS]


def _depths(nodes: list[NodeSummary]) -> dict[str, int]:
    parent = {n.id: n.parent_id for n in nodes}
    depth: dict[str, int] = {}
    for node_id in parent:
        chain, cur = [], node_id
        while cur is not None and cur in parent and cur not in depth and len(chain) < 100:
            chain.append(cur)
            cur = parent[cur]
        base = depth.get(cur, -1) if cur is not None else -1
        for n in reversed(chain):
            base += 1
            depth[n] = base
    return depth


@router.get("", response_model=list[NodeSummaryOut])
def list_nodes(
    document_id: str = Query(...),
    q: str = Query("", description="Words that must all appear in a node's section, title or text."),
    repo: Repository = Depends(get_repo),
):
    """The nodes of one document in reading order, without their text. Optionally only those matching `q`."""
    if not repo.get_document(document_id):
        raise HTTPException(404, "Document not found.")
    nodes = repo.list_node_summaries(document_id)
    depth = _depths(nodes)  # worked out on the whole document so a search keeps each node's real depth
    terms = search_terms(q)
    keep = repo.search_node_ids(document_id, terms) if terms else None
    return [NodeSummaryOut.from_summary(n, depth[n.id]) for n in nodes if keep is None or n.id in keep]


@router.get("/{node_id}", response_model=NodeDetailOut)
def get_node(node_id: str, repo: Repository = Depends(get_repo)):
    """One node with its exact text, its source file, and the cross-references in both directions."""
    node = repo.get_node(node_id)
    if not node:
        raise HTTPException(404, "Node not found.")
    doc = repo.get_document(node.document_id)
    if not doc:
        raise HTTPException(404, "The document this node belongs to was removed.")

    outgoing: list[CrossRef] = repo.refs_from(node_id)
    incoming: list[CrossRef] = repo.refs_to(node_id)
    wanted = {r.target_node_id for r in outgoing if r.target_node_id}
    wanted |= {r.from_node_id for r in incoming}
    if node.parent_id:
        wanted.add(node.parent_id)
    others = {n.id: n for n in repo.get_node_summaries(sorted(wanted))}

    def ref(node_id: str | None) -> NodeRefOut | None:
        return NodeRefOut.from_summary(others[node_id]) if node_id and node_id in others else None

    def figure_out(figure_id: str | None) -> FigureOut | None:
        f = repo.get_figure(figure_id) if figure_id else None
        return FigureOut.from_figure(f) if f else None

    def table_out(table_id: str | None) -> TableOut | None:
        t = repo.get_table(table_id) if table_id else None
        return TableOut.from_table(t) if t else None

    return NodeDetailOut(
        **node.model_dump(exclude={"embedding", "parent_id"}),
        source=SourceOut(**doc.model_dump(include={"id", "manual", "original_name", "version", "sha256", "uploaded_at"})),
        parent=ref(node.parent_id),
        conditions=[ConditionOut.from_condition(c) for c in repo.list_conditions(node_id)],
        refs_out=[
            RefOutgoingOut(
                id=r.id,
                mention=r.mention,
                target_manual=r.target_manual,
                target_label=r.target_label,
                status=r.status,
                resolved_by=r.resolved_by,
                node=ref(r.target_node_id),
                figure=figure_out(r.target_figure_id),
                table=table_out(r.target_table_id),
            )
            for r in outgoing
        ],
        refs_in=[
            RefIncomingOut(id=r.id, mention=r.mention, status=r.status, node=n)
            for r in incoming
            if (n := ref(r.from_node_id))
        ],
    )
