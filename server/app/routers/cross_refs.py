from fastapi import APIRouter, Depends

from ..deps import get_repo
from ..ingestion.pipeline import current_docs
from ..ingestion.resolution import cross_manual_pass, internal_pass
from ..repository.base import Repository
from ..schemas import CrossRefRowOut, FigureOut, NodeRefOut

BATCH = 100

router = APIRouter(prefix="/api/cross-refs", tags=["cross-references"])


@router.get("", response_model=list[CrossRefRowOut])
def list_cross_refs(repo: Repository = Depends(get_repo)):
    """Every cross-reference of every ingested document, with the node it comes from and the node it leads to."""
    refs = [r for d in repo.list_documents() for r in repo.list_refs(d.id)]
    wanted = {r.from_node_id for r in refs} | {r.target_node_id for r in refs if r.target_node_id}
    ids = sorted(wanted)
    nodes = {  # in batches: a long `in` filter would overflow the request URL
        n.id: NodeRefOut.from_summary(n)
        for i in range(0, len(ids), BATCH)
        for n in repo.get_node_summaries(ids[i : i + BATCH])
    }
    figures = {f.id: FigureOut.from_figure(f) for d in repo.list_documents() for f in repo.list_figures(d.id)}
    return [
        CrossRefRowOut(
            id=r.id,
            mention=r.mention,
            target_manual=r.target_manual,
            target_label=r.target_label,
            status=r.status,
            resolved_by=r.resolved_by,
            source=nodes[r.from_node_id],
            target=nodes.get(r.target_node_id) if r.target_node_id else None,
            target_figure=figures.get(r.target_figure_id) if r.target_figure_id else None,
        )
        for r in refs
        if r.from_node_id in nodes
    ]


@router.post("/refresh")
def refresh_cross_refs(repo: Repository = Depends(get_repo)):
    """Links the saved references again, without re-ingesting anything.

    Use it after the linking rules changed, or to check a reference that stayed unresolved. Same-manual references that
    are still unresolved are tried again, and every cross-manual reference is recomputed against the manuals now loaded.
    """
    docs = current_docs(repo)
    return {
        "internal_pass": {d.manual: internal_pass(repo, d) for d in docs},
        "cross_manual_pass": cross_manual_pass(repo, docs),
    }
