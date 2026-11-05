"""API response shapes. camelCase on the wire to match the Next.js client."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from .models import (
    Classification,
    CrossRef,
    Document,
    Drawing,
    DrawingFact,
    DrawingFeature,
    DrawingNote,
    DrawingSheet,
    DrawingStatus,
    Figure,
    DocStatus,
    ManualTable,
    Manual,
    Node,
    NodeCondition,
    NodeSummary,
    NoteBinding,
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


class DrawingSheetOut(CamelModel):
    """One sheet. Its picture is at GET /api/drawings/sheets/{id}/image."""

    id: str
    drawing_id: str
    number: int
    original_name: str
    width: int
    height: int
    warnings: list[str]
    failures: list[str]

    @classmethod
    def from_sheet(cls, s: DrawingSheet) -> "DrawingSheetOut":
        return cls(**s.model_dump(include=set(cls.model_fields)))


class DrawingOut(CamelModel):
    id: str
    name: str
    status: DrawingStatus
    error: str | None = None
    summary: dict[str, Any] | None = None
    uploaded_at: datetime
    sheets: list[DrawingSheetOut]

    @classmethod
    def from_drawing(cls, d: Drawing, sheets: list[DrawingSheet]) -> "DrawingOut":
        return cls(**d.model_dump(), sheets=[DrawingSheetOut.from_sheet(s) for s in sheets])


class FeatureRelationOut(CamelModel):
    feature_id: str
    relation: str


class DrawingFeatureOut(CamelModel):
    id: str
    type: str
    label: str
    bbox: list[float] | None
    confidence: str
    found_by: str
    related: list[FeatureRelationOut]
    review_state: str

    @classmethod
    def from_feature(cls, f: DrawingFeature) -> "DrawingFeatureOut":
        return cls(**f.model_dump(include=set(cls.model_fields)))


class DrawingNoteOut(CamelModel):
    id: str
    text: str
    bbox: list[float] | None
    confidence: str
    scope: str
    found_by: str
    review_state: str

    @classmethod
    def from_note(cls, n: DrawingNote) -> "DrawingNoteOut":
        return cls(**n.model_dump(include=set(cls.model_fields)))


class NoteBindingOut(CamelModel):
    """A proposed link. The crop that shows why is at GET /api/drawings/bindings/{id}/evidence, when has_evidence."""

    id: str
    note_id: str
    feature_id: str
    evidence: str
    confidence: str
    has_evidence: bool
    review_state: str

    @classmethod
    def from_binding(cls, b: NoteBinding) -> "NoteBindingOut":
        return cls(**b.model_dump(exclude={"drawing_id", "image_path"}), has_evidence=bool(b.image_path))


class DrawingFactOut(CamelModel):
    """A value read from a crop. The crop is at GET /api/drawings/facts/{id}/evidence, when has_evidence."""

    id: str
    feature_id: str | None
    term: str
    status: str
    kind: str
    value: float | str | None
    unit: str
    verbatim_text: str
    bbox: list[float] | None
    confidence: str
    confirmation: str
    has_evidence: bool
    review_state: str

    @classmethod
    def from_fact(cls, f: DrawingFact) -> "DrawingFactOut":
        return cls(**f.model_dump(exclude={"drawing_id", "sheet_id", "image_path", "produced_by"}), has_evidence=bool(f.image_path))


class ReviewItemOut(CamelModel):
    kind: str
    message: str
    target_id: str | None


class SheetReviewOut(CamelModel):
    sheet: DrawingSheetOut
    features: list[DrawingFeatureOut]
    notes: list[DrawingNoteOut]
    bindings: list[NoteBindingOut]
    facts: list[DrawingFactOut]
    needs_review: list[ReviewItemOut]


class DrawingReviewOut(CamelModel):
    drawing: DrawingOut
    sheets: list[SheetReviewOut]
