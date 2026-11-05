"""Domain models. Field names match the database columns in sql/schema.sql."""

from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_validator

# The three manuals with numbered headings. Any other name a user types at upload is a document without headings:
# it is cut into topics by the topic planner (ingestion/planner.py) instead.
CORE_MANUALS = ("PPM", "HDM", "CADD")
MAX_MANUAL_CHARS = 80
Manual = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MANUAL_CHARS)]


def normalize_manual(name: str, known: list[str] | tuple[str, ...] = ()) -> str:
    """The manual name as stored: spaces tidied, PPM/HDM/CADD in capitals, and a name that differs only in case from
    one already in `known` spelled as that one, so "drainage memo" and "Drainage Memo" are one manual."""
    name = " ".join(name.split())
    if not name:
        raise ValueError("The manual name is required.")
    if len(name) > MAX_MANUAL_CHARS:
        raise ValueError(f"The manual name is longer than {MAX_MANUAL_CHARS} characters.")
    if any(ord(c) < 32 for c in name):
        raise ValueError("The manual name has control characters.")
    for core in CORE_MANUALS:
        if name.casefold() == core.casefold():
            return core
    for other in known:
        if name.casefold() == other.casefold():
            return other
    return name


def is_core_manual(name: str) -> bool:
    return name in CORE_MANUALS

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


class ManualTable(BaseModel):
    """A table captured from the PDF: its caption, its cells, and a PNG of it as printed, stored in the bucket.

    The table's text also stays in the node that holds it; this record is what a reference can point at.
    """

    id: str
    document_id: str
    manual: Manual
    # The section the caption sits in. None if no node covers its page.
    node_id: str | None = None
    # As in the references: "Table 856.5" (label) and "856.5" (number).
    label: str
    number: str
    caption: str
    page: int  # 1-based
    bbox: list[float]  # x0, y0, x1, y1 in PDF points: the cropped region
    image_path: str  # in the storage bucket
    rows: list[list[str]]  # the cells, row by row; a merged or empty cell is ""


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
    # Set instead of target_node_id when the reference is to a figure or to a table.
    target_figure_id: str | None = None
    target_table_id: str | None = None
    status: RefStatus
    resolved_by: ResolvedBy | None = None


# --- drawings ------------------------------------------------------------------------------------
# What the drawing reader (app/drawings/) found. A model produced all of it, so everything stays "proposed" until an
# engineer verifies it. Boxes are x0, y0, x1, y1 in pixels of the stored sheet image; None when the model gave no usable one.

DrawingStatus = Literal["uploaded", "processing", "processed", "failed"]
ReviewState = Literal["proposed", "verified"]
Confidence = Literal["high", "medium", "low"]
FoundBy = Literal["sheet", "tile"]
# feature: belongs to a feature. sheet: applies to the whole sheet. unbound: no clear owner, a person has to decide.
NoteScope = Literal["feature", "sheet", "unbound"]
BindingEvidence = Literal["leader_line", "adjacent", "label"]
FactStatus = Literal["FOUND", "NOT_FOUND"]
# direct: printed on the drawing and read as it is. semantic: inferred or bound (nothing produces these yet).
FactKind = Literal["direct", "semantic"]
# Whether a second, independent read of the same crop has the same words.
Confirmation = Literal["confirmed", "disputed", "not_checked"]


class Drawing(BaseModel):
    """One drawing set: one or more sheets."""

    id: str
    name: str
    status: DrawingStatus
    error: str | None = None
    # Counts and model usage of the last run.
    summary: dict[str, Any] | None = None
    uploaded_at: datetime = Field(default_factory=utcnow)


class DrawingSheet(BaseModel):
    """One page of a drawing set: one stored image."""

    id: str
    drawing_id: str
    number: int  # 1-based, in upload order
    original_name: str
    image_path: str  # in the storage bucket
    content_type: str
    sha256: str
    size_bytes: int
    width: int
    height: int
    warnings: list[str] = Field(default_factory=list)  # e.g. too low a resolution; found nothing
    failures: list[str] = Field(default_factory=list)  # what could not be read in the last run


class FeatureRelation(BaseModel):
    feature_id: str
    relation: str  # as the model described it, e.g. "joins"


class DrawingFeature(BaseModel):
    """A thing on a sheet: a bridge, a U-turn, a ramp."""

    id: str
    drawing_id: str
    sheet_id: str
    type: str  # lower_snake_case, the model's word; not yet mapped to a standards term
    label: str = ""  # its name as printed, if any
    bbox: list[float] | None
    confidence: Confidence
    found_by: FoundBy
    related: list[FeatureRelation] = Field(default_factory=list)
    review_state: ReviewState = "proposed"


class DrawingNote(BaseModel):
    """A printed note, callout or dimension, as seen on the whole sheet. The values are read from a sharper crop (DrawingFact)."""

    id: str
    drawing_id: str
    sheet_id: str
    text: str
    bbox: list[float] | None
    confidence: Confidence
    scope: NoteScope
    found_by: FoundBy
    review_state: ReviewState = "proposed"


class NoteBinding(BaseModel):
    """A proposed link from a note to the feature it belongs to, with the crop that shows why."""

    id: str
    drawing_id: str
    note_id: str
    feature_id: str
    evidence: BindingEvidence
    confidence: Confidence
    image_path: str | None = None
    review_state: ReviewState = "proposed"


class DrawingFact(BaseModel):
    """One typed value read from a crop, or the explicit statement that it is not printed there.

    This is the only thing from a drawing that later stages may read.
    """

    id: str
    drawing_id: str
    sheet_id: str
    feature_id: str | None  # None: a sheet-level fact
    term: str  # lower_snake_case; the model's word until a term list is approved
    status: FactStatus
    kind: FactKind = "direct"
    value: float | str | None = None
    unit: str = ""
    verbatim_text: str = ""  # exactly as printed
    bbox: list[float] | None = None
    confidence: Confidence
    confirmation: Confirmation = "not_checked"
    image_path: str | None = None  # the crop it was read from
    produced_by: Literal["vision"] = "vision"
    review_state: ReviewState = "proposed"

    @model_validator(mode="after")
    def _found_has_evidence(self) -> "DrawingFact":
        if self.status == "FOUND" and (self.value in (None, "") or not self.verbatim_text.strip()):
            raise ValueError("A FOUND fact needs a value and its verbatim text.")
        if self.status == "NOT_FOUND" and (self.value is not None or self.verbatim_text):
            raise ValueError("A NOT_FOUND fact has no value and no verbatim text.")
        return self
