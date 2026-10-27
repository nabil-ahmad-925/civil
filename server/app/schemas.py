"""API response shapes. camelCase on the wire to match the Next.js client."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from .models import (
    Classification,
    CrossRef,
    Document,
    Figure,
    DocStatus,
    ManualTable,
    Manual,
    Node,
    NodeCondition,
    NodeSummary,
    RefStatus,
    RequirementClass,
    ResolvedBy,
    TargetManual,
)


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class DocumentOut(CamelModel):
    id: str
    manual: Manual
    version: str
    original_name: str
    size_bytes: int
    sha256: str
    uploaded_at: datetime
    status: DocStatus
    error: str | None = None
    summary: dict[str, Any] | None = None

    @classmethod
    def from_document(cls, d: Document) -> "DocumentOut":
        return cls(**d.model_dump(exclude={"storage_path"}))


class NodeSummaryOut(CamelModel):
    id: str
    document_id: str
    manual: Manual
    chapter: str
    section: str
    title: str
    parent_id: str | None
    page_start: int
    page_end: int
    ordinal: int
    classification: Classification | None
    depth: int  # 0 for a chapter, 1 for a section inside it, ...

    @classmethod
    def from_summary(cls, n: NodeSummary, depth: int) -> "NodeSummaryOut":
        return cls(**n.model_dump(), depth=depth)


class NodeRefOut(CamelModel):
    """A pointer to another node, enough to show it and jump to it."""

    id: str
    document_id: str
    manual: Manual
    section: str
    title: str

    @classmethod
    def from_summary(cls, n: NodeSummary) -> "NodeRefOut":
        return cls(**n.model_dump(include={"id", "document_id", "manual", "section", "title"}))


class FigureOut(CamelModel):
    """A captured figure. Its picture is at GET /api/figures/{id}/image."""

    id: str
    document_id: str
    manual: Manual
    node_id: str | None
    label: str
    caption: str
    page: int

    @classmethod
    def from_figure(cls, f: Figure) -> "FigureOut":
        return cls(**f.model_dump(include=set(cls.model_fields)))


class TableOut(CamelModel):
    """A captured table, without its cells. Its picture is at GET /api/tables/{id}/image."""

    id: str
    document_id: str
    manual: Manual
    node_id: str | None
    label: str
    caption: str
    page: int

    @classmethod
    def from_table(cls, t: ManualTable) -> "TableOut":
        return cls(**t.model_dump(include=set(cls.model_fields)))


class TableDetailOut(TableOut):
    """One table with its cells, row by row."""

    rows: list[list[str]]

    @classmethod
    def from_table(cls, t: ManualTable) -> "TableDetailOut":
        return cls(**t.model_dump(include=set(cls.model_fields)))


class RefOutgoingOut(CamelModel):
    """A mention in this node's text, and where it leads (node is null until it is linked)."""

    id: str
    mention: str
    target_manual: TargetManual
    target_label: str
    status: RefStatus
    resolved_by: ResolvedBy | None
    node: NodeRefOut | None
    figure: FigureOut | None = None
    table: TableOut | None = None


class RefIncomingOut(CamelModel):
    """Another node whose text mentions this one."""

    id: str
    mention: str
    status: RefStatus
    node: NodeRefOut


class ConditionOut(CamelModel):
    id: str
    kind: str
    text: str
    input: str | None
    required_inputs: list[str]

    @classmethod
    def from_condition(cls, c: NodeCondition) -> "ConditionOut":
        return cls(**c.model_dump(exclude={"node_id"}))


class SourceOut(CamelModel):
    id: str
    manual: Manual
    original_name: str
    version: str
    sha256: str
    uploaded_at: datetime


class NodeDetailOut(CamelModel):
    id: str
    document_id: str
    manual: Manual
    chapter: str
    section: str
    title: str
    page_start: int
    page_end: int
    ordinal: int
    text: str  # exactly as extracted from the PDF
    classification: Classification | None
    requirement_class: RequirementClass | None
    tags: list[str]
    source: SourceOut
    parent: NodeRefOut | None
    conditions: list[ConditionOut]
    refs_out: list[RefOutgoingOut]
    refs_in: list[RefIncomingOut]


class CrossRefRowOut(CamelModel):
    """One row of the Cross-References page: the mention, the node it is in, and where it leads."""

    id: str
    mention: str
    target_manual: TargetManual
    target_label: str
    status: RefStatus
    resolved_by: ResolvedBy | None
    source: NodeRefOut
    target: NodeRefOut | None
    target_figure: FigureOut | None = None
    target_table: TableOut | None = None
