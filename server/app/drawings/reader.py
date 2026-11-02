"""The drawing reader: from a sheet image to proposed features, notes, links and typed facts.

For each sheet:
1. The whole sheet goes to the model (shrunk only if it is over the model's limit). It lists the features and the notes,
   and says which note belongs to which feature: only the whole sheet shows what is connected to what.
2. Optionally, overlapping tiles look for what that missed (Settings.drawing_tile_sweep). What they add is low confidence.
3. For each feature, a crop is cut from the full-size sheet around it and its notes, so a feature and its note are in one
   sharp picture wherever they sit. The model reads the printed values from it: never estimated, so a value that is not
   printed comes back as NOT_FOUND. Notes that apply to the whole sheet are read the same way, as sheet-level facts.
4. The same crop is transcribed in a separate call. A value whose printed text is not in that transcript is disputed. The
   transcript also shows whether the crop really holds what it was cut for: the model's positions can be off, and a crop
   cut in the wrong place must never turn into "not printed". Then it is reported as a failure instead of a NOT_FOUND.

A failed call never stops the run or disappears: it is written on the sheet, and shows up in the review list. Results
replace the previous run's only once the whole run has worked.
"""

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Sequence
from uuid import uuid4

from PIL import Image

from ..config import Settings
from ..models import (
    Confirmation,
    Drawing,
    DrawingFact,
    DrawingFeature,
    DrawingNote,
    DrawingSheet,
    FeatureRelation,
    FoundBy,
    NoteBinding,
)
from ..repository.base import Repository
from .images import (
    Box,
    SheetFile,
    center_inside,
    crop_png,
    from_model_box,
    long_side,
    overlaps,
    pad,
    picture_for_model,
    sheet_for_model,
    tile_boxes,
    to_rgb,
    union,
)
from .vision import CallLog, DrawingVision, ReadValue, SheetReading, VisionError

log = logging.getLogger(__name__)

# The model's boxes are approximate: a crop keeps this much room around them.
CROP_PADDING = 0.15  # of the box's size
CROP_MIN_PADDING_PX = 60
# Stands for "any value": a feature with no value printed for it says so, instead of having no facts at all.
ANY_VALUE_TERM = "printed_value"
# A crop cut for one note also shows pieces of its neighbours. Text is taken to come from the note when this much of it is
# in the note: enough to let a misread digit through to the second read, not enough for a neighbour's line.
SAME_NOTE_SHARE = 0.6
# A value this close to a crop's edge may be cut off by it, and both reads would then agree on the cut-off text.
EDGE_SHARE = 0.02  # of the crop's size
EDGE_MIN_PX = 10
NUMBER_RE = re.compile(r"[-+]?(\d+(\.\d*)?|\.\d+)")


class DrawingError(RuntimeError):
    """Nothing could be read from any sheet. The previous results, if any, are kept."""


@dataclass
class Crop:
    """One picture to read values from."""

    feature: DrawingFeature | None  # None: a note that applies to the whole sheet
    box: Box
    notes: list[DrawingNote]
    about: str
    only_notes: bool = False  # cut for these notes alone: a value from anything else in the picture is a neighbour's


@dataclass
class Link:
    note_id: str
    feature_id: str
    evidence: str
    confidence: str


@dataclass
class SheetResult:
    features: list[DrawingFeature] = field(default_factory=list)
    notes: list[DrawingNote] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    bindings: list[NoteBinding] = field(default_factory=list)
    facts: list[DrawingFact] = field(default_factory=list)
    crops: dict[str, bytes] = field(default_factory=dict)  # storage path -> PNG
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _normalized(text: str) -> str:
    """For comparing two reads of the same text: spacing, case and the kind of quote mark are not differences."""
    for curly, straight in (("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("′", "'"), ("″", '"')):
        text = text.replace(curly, straight)
    return re.sub(r"\s+", "", text).casefold()


def _term(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _typed_value(value: str) -> float | str:
    """50 for "50" and 1200 for "1,200". Anything else stays the text as printed ("12'-6\\"", "CLASS A")."""
    plain = value.strip().replace(",", "")
    return float(plain) if NUMBER_RE.fullmatch(plain) else value.strip()


def _confirmation(read: ReadValue, transcript: list[str] | None) -> Confirmation:
    if transcript is None:
        return "not_checked"
    text, value = _normalized(read.text), _normalized(read.value)
    if value.replace(",", "") not in text.replace(",", ""):  # the value does not come from the text it claims to
        return "disputed"
    return "confirmed" if text in _normalized("".join(transcript)) else "disputed"


def absorb(result: SheetResult, sheet: DrawingSheet, seen: SheetReading, region: Box, found_by: FoundBy) -> None:
    """Adds what one picture showed to the sheet's lists, in sheet coordinates. What a tile adds beyond the whole-sheet pass
    is low confidence, and a tile's view of something already listed is merged into it."""
    is_tile = found_by == "tile"
    by_id = {f.id: f for f in result.features}
    local: dict[str, str] = {}  # this answer's refs -> feature ids
    for f in seen.features:
        if f.ref in local:
            continue
        kind, box = _term(f.type) or "unknown", from_model_box(f.box, region)
        same = _same_feature(result.features, kind, box) if is_tile else None
        if same:
            local[f.ref] = same.id
            if same.found_by == "tile" and box and same.bbox:  # the parts of something only the tiles saw
                same.bbox = union([same.bbox, box])
            continue
        feature = DrawingFeature(
            id=str(uuid4()), drawing_id=sheet.drawing_id, sheet_id=sheet.id, type=kind, label=f.label.strip(), bbox=box,
            confidence="low" if is_tile or box is None else f.confidence, found_by=found_by,
        )
        result.features.append(feature)
        by_id[feature.id] = feature
        local[f.ref] = feature.id
    for f in seen.features:
        feature = by_id[local[f.ref]]
        for r in f.related:
            other = local.get(r.feature)
            if other and other != feature.id and all(x.feature_id != other for x in feature.related):
                feature.related.append(FeatureRelation(feature_id=other, relation=r.relation.strip()))
    for n in seen.notes:
        text = n.text.strip()
        if not text:
            continue
        box = from_model_box(n.box, region)
        links = [(local[x.feature], x) for x in n.links if x.feature in local] if n.scope == "feature" else []
        scope = "sheet" if n.scope == "sheet" else "feature" if links else "unbound"
        note = _same_note(result.notes, text, box) if is_tile else None
        if note:
            if len(text) > len(note.text) and note.found_by == "tile":  # a note cut by one tile's edge is whole in the next
                note.text, note.bbox = text, box or note.bbox
            if note.scope != "unbound":
                continue
            note.scope = scope
        else:
            note = DrawingNote(
                id=str(uuid4()), drawing_id=sheet.drawing_id, sheet_id=sheet.id, text=text, bbox=box,
                confidence="low" if is_tile or box is None else n.confidence, scope=scope, found_by=found_by,
            )
            result.notes.append(note)
        taken: set[str] = set()
        for feature_id, x in links:
            if feature_id not in taken:
                taken.add(feature_id)
                result.links.append(Link(note.id, feature_id, x.evidence, x.confidence))


def _same_feature(features: list[DrawingFeature], kind: str, box: Box | None) -> DrawingFeature | None:
    if box is None:
        return None
    for f in features:
        if f.type == kind and f.bbox and (center_inside(box, f.bbox) or center_inside(f.bbox, box)):
            return f
    return None


def _same_note(notes: list[DrawingNote], text: str, box: Box | None) -> DrawingNote | None:
    wanted = _normalized(text)
    for n in notes:
        have = _normalized(n.text)
        if (wanted in have or have in wanted) and (box is None or n.bbox is None or overlaps(box, n.bbox)):
            return n
    return None


def plan_crops(result: SheetResult, width: int, height: int, max_crop_px: int) -> list[Crop]:
    """What to read. A feature and its notes share one crop, wherever they sit. If that crop would be too big to read,
    the notes are read each on their own and stay bound to the feature."""

    def room(box: Box) -> Box:
        return pad(box, width, height, CROP_PADDING, CROP_MIN_PADDING_PX)

    notes = {n.id: n for n in result.notes}
    crops: list[Crop] = []
    for f in result.features:
        own = [notes[x.note_id] for x in result.links if x.feature_id == f.id]
        placed = [n for n in own if n.bbox]
        for n in own:
            if not n.bbox:
                result.failures.append(f"The note '{n.text[:80]}' has no usable position, so its values were not read.")
        if f.bbox is None:
            result.failures.append(f"The {f.type} '{f.label}' has no usable position, so its values were not read.")
            continue
        about = f.type.replace("_", " ") + (f" '{f.label}'" if f.label else "")
        whole = room(union([f.bbox] + [n.bbox for n in placed if n.bbox]))
        if not placed or long_side(whole) <= max_crop_px:
            crops.append(Crop(f, whole, placed, about))
            continue
        alone = room(f.bbox)
        if long_side(alone) <= max_crop_px:
            crops.append(Crop(f, alone, [], about))
        crops.extend(Crop(f, room(n.bbox), [n], f"{about}, in the note about it", only_notes=True) for n in placed if n.bbox)
    for n in result.notes:
        if n.scope != "sheet":
            continue
        if n.bbox:
            crops.append(Crop(None, room(n.bbox), [n], "the note in this picture, which applies to the whole sheet", only_notes=True))
        else:
            result.failures.append(f"The note '{n.text[:80]}' has no usable position, so its values were not read.")
    return crops


def _words_only(text: str) -> str:
    return re.sub(r"[\W_]+", "", text).casefold()


def _not_shown(crop: Crop, transcript: list[str]) -> list[str]:
    """What the crop was cut for but does not show. A note with no line of it in the transcript is not in the crop. A
    feature's label stands in when it has no notes. This asks where the crop is, not how well it reads, so punctuation
    does not count: "BRIDGE NO 54" is the note "BRIDGE NO. 54"."""
    have = _words_only("".join(transcript))
    anchors = [n.text for n in crop.notes] or ([crop.feature.label] if crop.feature and crop.feature.label else [])
    return [a for a in anchors if not any(_words_only(line) in have for line in a.splitlines() if _words_only(line))]


def _from_its_notes(text: str, crop: Crop) -> bool:
    wanted = _normalized(text)
    for n in crop.notes:
        matcher = SequenceMatcher(None, wanted, _normalized(n.text), autojunk=False)
        if sum(b.size for b in matcher.get_matching_blocks()) >= SAME_NOTE_SHARE * len(wanted):
            return True
    return False


def _at_cut_edge(box: Box, crop: Box, width: int, height: int) -> bool:
    """Whether a box touches a side of the crop that cuts through the sheet (not one at the sheet's own edge)."""
    dx = max((crop[2] - crop[0]) * EDGE_SHARE, EDGE_MIN_PX)
    dy = max((crop[3] - crop[1]) * EDGE_SHARE, EDGE_MIN_PX)
    return (
        (crop[0] > 0 and box[0] - crop[0] < dx)
        or (crop[1] > 0 and box[1] - crop[1] < dy)
        or (crop[2] < width and crop[2] - box[2] < dx)
        or (crop[3] < height and crop[3] - box[3] < dy)
    )


def _facts_from_crop(
    sheet: DrawingSheet,
    crop: Crop,
    values: list[ReadValue],
    transcript: list[str] | None,
    path: str,
    wanted: Sequence[str],
    may_say_not_found: bool,
) -> list[DrawingFact]:
    feature_id = crop.feature.id if crop.feature else None
    facts: list[DrawingFact] = []
    for v in values:
        value, text = v.value.strip(), v.text.strip()
        if not text or not value:  # no printed words to show for it: not a fact
            log.warning("Dropping a value with no printed text: %s", v)
            continue
        if crop.only_notes and not _from_its_notes(text, crop):
            log.info("Dropping '%s': it belongs to a neighbour of the note this crop was cut for", text)
            continue
        confirmation = _confirmation(v, transcript)
        box = from_model_box(v.box, crop.box)
        clear = confirmation == "confirmed" and box is not None and not _at_cut_edge(box, crop.box, sheet.width, sheet.height)
        facts.append(
            DrawingFact(
                id=str(uuid4()), drawing_id=sheet.drawing_id, sheet_id=sheet.id, feature_id=feature_id,
                term=_term(v.term) or ANY_VALUE_TERM, status="FOUND", value=_typed_value(value), unit=v.unit.strip(),
                verbatim_text=text, bbox=box, confidence=v.confidence if clear else "low",
                confirmation=confirmation, image_path=path,
            )
        )
    if not may_say_not_found:
        return facts
    found = {f.term for f in facts}
    missing = [_term(t) for t in wanted if _term(t) not in found]
    if crop.feature and not found and not wanted:
        missing = [ANY_VALUE_TERM]
    facts += [
        DrawingFact(
            id=str(uuid4()), drawing_id=sheet.drawing_id, sheet_id=sheet.id, feature_id=feature_id, term=term,
            status="NOT_FOUND", bbox=crop.box, confidence="medium", image_path=path,
        )
        for term in missing
    ]
    return facts


def read_crop(
    vision: DrawingVision, sheet: DrawingSheet, crop: Crop, png: bytes, calls: CallLog, max_patches: int, wanted: Sequence[str] = ()
) -> tuple[list[DrawingFact], str, str | None]:
    """The facts in one crop, its storage path, and what went wrong if anything. Also the targeted re-read for later stages: pass
    the terms a rule needs in `wanted`, and each one comes back FOUND or NOT_FOUND."""
    path = f"drawings/{sheet.drawing_id}/crops/{uuid4()}.png"
    picture = picture_for_model(png, max_patches)
    try:
        values = vision.read_values(picture, crop.about, wanted, calls)
    except VisionError as exc:
        calls.add_failure()
        return [], path, f"Could not read the values of {crop.about}: {exc}"
    anchored = bool(crop.notes) or bool(crop.feature and crop.feature.label)
    problem = None
    try:
        transcript = vision.transcribe(picture, calls) if values or anchored else []
    except VisionError as exc:
        calls.add_failure()
        transcript, problem = None, f"Could not confirm the values of {crop.about}: {exc}"
    # Without a transcript nothing shows the crop holds what it should, so nothing may be called not printed.
    not_shown = _not_shown(crop, transcript) if transcript is not None else []
    if not_shown:
        problem = f"The crop for {crop.about} does not show '{not_shown[0][:80]}', so its values may be incomplete."
    trusted = not not_shown and (transcript is not None or not anchored)
    return _facts_from_crop(sheet, crop, values, transcript, path, wanted, trusted), path, problem


def _dedupe(facts: list[DrawingFact]) -> list[DrawingFact]:
    """Crops of neighbouring things can show the same print: one fact per feature, term and printed text. A NOT_FOUND gives
    way when another crop of the same feature found that term."""
    seen: set[tuple] = set()
    found = {(f.feature_id, f.term) for f in facts if f.status == "FOUND"}
    any_found = {f.feature_id for f in facts if f.status == "FOUND"}
    out: list[DrawingFact] = []
    for f in facts:
        if f.status == "NOT_FOUND" and ((f.feature_id, f.term) in found or (f.term == ANY_VALUE_TERM and f.feature_id in any_found)):
            continue
        key = (f.sheet_id, f.feature_id, f.term, f.status, _normalized(f.verbatim_text))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def read_sheet(
    repo: Repository, vision: DrawingVision, sheet: DrawingSheet, settings: Settings, tile_sweep: bool, calls: CallLog
) -> SheetResult:
    result = SheetResult()
    if max(sheet.width, sheet.height) < settings.drawing_min_long_side_px:
        result.warnings.append(
            f"The image is only {sheet.width} x {sheet.height} pixels, so small text may not be readable."
        )
    data = repo.download_file(sheet.image_path)
    im = to_rgb(Image.open(io.BytesIO(data)))
    whole: Box = [0.0, 0.0, float(sheet.width), float(sheet.height)]
    max_patches = settings.drawing_max_patches
    workers = max(1, settings.drawing_workers)

    file = SheetFile(data, sheet.content_type, sheet.width, sheet.height)
    try:
        absorb(result, sheet, vision.read_sheet(sheet_for_model(file, im, max_patches), calls), whole, "sheet")
    except VisionError as exc:
        calls.add_failure()
        result.failures.append(f"Could not read the whole sheet: {exc}")

    if tile_sweep:
        tiles = tile_boxes(sheet.width, sheet.height, settings.drawing_tile_px, settings.drawing_tile_overlap)

        def read_tile(box: Box) -> SheetReading | str:
            try:
                return vision.read_sheet(picture_for_model(crop_png(im, box), max_patches), calls, tile=True)
            except VisionError as exc:
                calls.add_failure()
                return f"Could not read the tile at {[round(v) for v in box]}: {exc}"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for box, seen in zip(tiles, pool.map(read_tile, tiles)):
                if isinstance(seen, str):
                    result.failures.append(seen)
                else:
                    absorb(result, sheet, seen, box, "tile")

    if not result.features and not result.notes and not result.failures:
        result.warnings.append("The model found no features and no notes on this sheet.")

    crops = plan_crops(result, sheet.width, sheet.height, settings.drawing_max_crop_px)
    pictures = [crop_png(im, c.box) for c in crops]

    def read(index: int) -> tuple[list[DrawingFact], str, str | None]:
        return read_crop(vision, sheet, crops[index], pictures[index], calls, max_patches)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        answers = list(pool.map(read, range(len(crops))))

    crop_of_note: dict[str, str] = {}
    for crop, png, (facts, path, problem) in zip(crops, pictures, answers):
        if problem:
            result.failures.append(problem)
        result.facts.extend(facts)
        for n in crop.notes:
            crop_of_note.setdefault(n.id, path)
        if facts or crop.notes:  # the evidence for a fact or a link
            result.crops[path] = png
    result.facts = _dedupe(result.facts)

    result.bindings = [
        NoteBinding(
            id=str(uuid4()), drawing_id=sheet.drawing_id, note_id=x.note_id, feature_id=x.feature_id, evidence=x.evidence,
            confidence=x.confidence, image_path=crop_of_note.get(x.note_id),
        )
        for x in result.links
    ]
    kept = {f.image_path for f in result.facts} | {b.image_path for b in result.bindings}
    result.crops = {p: png for p, png in result.crops.items() if p in kept}
    result.failures = list(dict.fromkeys(result.failures))  # a note linked to two features is reported once
    return result


def _summary(results: list[SheetResult], calls: CallLog, settings: Settings, tile_sweep: bool) -> dict:
    facts = [f for r in results for f in r.facts]
    cost = (calls.input_tokens * settings.drawing_input_usd_per_m + calls.output_tokens * settings.drawing_output_usd_per_m) / 1_000_000
    return {
        "sheets": len(results),
        "features": sum(len(r.features) for r in results),
        "notes": sum(len(r.notes) for r in results),
        "unbound_notes": sum(1 for r in results for n in r.notes if n.scope == "unbound"),
        "bindings": sum(len(r.bindings) for r in results),
        "facts_found": sum(1 for f in facts if f.status == "FOUND"),
        "facts_not_found": sum(1 for f in facts if f.status == "NOT_FOUND"),
        "facts_disputed": sum(1 for f in facts if f.confirmation == "disputed"),
        "failures": sum(len(r.failures) for r in results),
        "tile_sweep": tile_sweep,
        "model": settings.drawing_vision_model,
        "calls": calls.calls,
        "failed_calls": calls.failed,
        "input_tokens": calls.input_tokens,
        "output_tokens": calls.output_tokens,
        "estimated_usd": round(cost, 4),
    }


def process_drawing(
    repo: Repository, vision: DrawingVision, drawing: Drawing, settings: Settings, tile_sweep: bool | None = None
) -> Drawing:
    """Reads every sheet of a drawing and saves what it found. Safe to run again: it replaces the previous results."""
    sweep = settings.drawing_tile_sweep if tile_sweep is None else tile_sweep
    repo.update_drawing(drawing.id, status="processing", error=None)
    try:
        sheets = repo.list_sheets(drawing.id)
        calls = CallLog()
        results = [read_sheet(repo, vision, s, settings, sweep, calls) for s in sheets]
        if results and all(r.failures and not r.features and not r.notes for r in results):
            raise DrawingError(results[0].failures[0])

        old = {f.image_path for f in repo.list_drawing_facts(drawing.id)} | {b.image_path for b in repo.list_note_bindings(drawing.id)}
        for r in results:
            for path, png in r.crops.items():
                repo.upload_bytes(path, png, "image/png")
        repo.delete_drawing_results(drawing.id)
        repo.save_drawing_results(
            [f for r in results for f in r.features],
            [n for r in results for n in r.notes],
            [b for r in results for b in r.bindings],
            [f for r in results for f in r.facts],
        )
        for s, r in zip(sheets, results):
            repo.update_sheet(s.id, warnings=r.warnings, failures=r.failures)
        repo.delete_files(sorted(p for p in old if p))
        repo.update_drawing(drawing.id, status="processed", error=None, summary=_summary(results, calls, settings, sweep))
    except Exception as exc:  # noqa: BLE001 - any failure must be recorded on the drawing
        log.exception("Processing drawing %s failed", drawing.id)
        message = str(exc) if isinstance(exc, DrawingError) else f"{type(exc).__name__}: {exc}"
        repo.update_drawing(drawing.id, status="failed", error=message)
        raise
    updated = repo.get_drawing(drawing.id)
    assert updated is not None
    return updated


def review_items(
    sheet: DrawingSheet, features: list[DrawingFeature], notes: list[DrawingNote], facts: list[DrawingFact]
) -> list[dict]:
    """What a person has to look at on this sheet, the most serious first."""
    items: list[dict] = [{"kind": "failed", "message": m, "target_id": None} for m in sheet.failures]
    items += [{"kind": "warning", "message": m, "target_id": None} for m in sheet.warnings]
    for f in facts:
        if f.status != "FOUND":
            continue
        if f.confirmation == "disputed":
            items.append({"kind": "disputed_reading", "message": f"A second read does not agree on '{f.verbatim_text}'.", "target_id": f.id})
        elif f.confirmation == "not_checked":
            items.append({"kind": "unconfirmed_reading", "message": f"'{f.verbatim_text}' could not be confirmed by a second read.", "target_id": f.id})
        elif f.confidence == "low":
            items.append({"kind": "low_confidence_reading", "message": f"Low confidence in '{f.verbatim_text}'.", "target_id": f.id})
    items += [
        {"kind": "unbound_note", "message": f"No clear owner for the note '{n.text[:120]}'.", "target_id": n.id}
        for n in notes
        if n.scope == "unbound"
    ]
    items += [
        {"kind": "low_confidence_feature", "message": f"Low confidence in the {f.type} '{f.label}'." if f.label else f"Low confidence in this {f.type}.", "target_id": f.id}
        for f in features
        if f.confidence == "low"
    ]
    return items
