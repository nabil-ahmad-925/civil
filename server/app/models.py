"""Domain models. Field names match the database columns in sql/schema.sql."""

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

Manual = Literal["PPM", "HDM", "CADD"]
# OTHER: the reference points at a document that is not one of the three manuals (named in target_label).
TargetManual = Literal["PPM", "HDM", "CADD", "OTHER"]

# uploaded: PDF is in the bucket, not chunked yet. failed: ingest raised; `error` says why.
DocStatus = Literal["uploaded", "processing", "ingested", "failed"]
# external: points outside the three manuals. Kept as text; there is no node to link it to.
RefStatus = Literal["unresolved", "resolved", "awaiting_cross", "external"]
ResolvedBy = Literal["internal_pass", "cross_manual_pass"]
Classification = Literal["requirement", "guidance", "non_governing"]
RequirementClass = Literal["boldface", "underlined", "approval_required", "permissive"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(BaseModel):
    id: str
    manual: Manual
    version: str
    original_name: str
    size_bytes: int
    sha256: str
    storage_path: str
    status: DocStatus
    error: str | None = None
    # Counts from the last successful ingest (nodes, cross_refs, ...).
    summary: dict[str, Any] | None = None
    uploaded_at: datetime = Field(default_factory=utcnow)


class Node(BaseModel):
    id: str
    document_id: str
    manual: Manual
    chapter: str = ""
    section: str
    title: str
    parent_id: str | None = None
    page_start: int
    page_end: int
    ordinal: int
    # Exact source text. Never rewritten.
    text: str
    embedding: list[float] | None = None
    # Filled by the tagging stage (not built yet).
    classification: Classification | None = None
    requirement_class: RequirementClass | None = None
    tags: list[str] = Field(default_factory=list)


class Figure(BaseModel):
    """A figure captured from the PDF: its caption and a PNG of the drawing, stored in the bucket."""

    id: str
    document_id: str
    manual: Manual
    # The section the caption sits in. None if no node covers its page.
    node_id: str | None = None
    # As in the references: "Figure 2-7" (label) and "2-7" (number).
    label: str
    number: str
    caption: str
    page: int  # 1-based
    bbox: list[float]  # x0, y0, x1, y1 in PDF points: the cropped region
    image_path: str  # in the storage bucket


class NodeSummary(BaseModel):
    """A node without its text or vector: enough to list and place it."""

    id: str
    document_id: str
    manual: Manual
    chapter: str = ""
    section: str
    title: str
    parent_id: str | None = None
    page_start: int
    page_end: int
    ordinal: int
    classification: Classification | None = None


class NodeCondition(BaseModel):
    """An applicability condition or exception, kept apart from the requirement it belongs to."""

    id: str
    node_id: str
    kind: Literal["applicability", "exception"]
    text: str
    input: str | None = None
    required_inputs: list[str] = Field(default_factory=list)


class CrossRef(BaseModel):
    id: str
    document_id: str
    from_node_id: str
    # Verbatim mention as it appears in the source text.
    mention: str
    target_manual: TargetManual
    target_section: str | None = None
    target_label: str
    target_node_id: str | None = None
    # Set instead of target_node_id when the reference is to a figure.
    target_figure_id: str | None = None
    status: RefStatus
    resolved_by: ResolvedBy | None = None
