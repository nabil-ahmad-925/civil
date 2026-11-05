"""The drawing reader (app/drawings/): a fake model stands in for the vision calls, except in the tests of OpenAIVision."""

import base64
import io
import json

import httpx
import pytest
from PIL import Image, ImageDraw

from app.config import Settings, get_settings
from app.deps import get_drawing_vision
from app.drawings.images import (
    BadImage,
    data_url,
    fit_size,
    from_model_box,
    patches,
    read_upload,
    sheet_for_model,
    SheetFile,
    tile_boxes,
    to_rgb,
)
from app.drawings.reader import plan_crops, read_crop, SheetResult, Crop
from app.drawings.scoring import score_drawing
from app.drawings.vision import (
    CallLog,
    OpenAIVision,
    ReadValue,
    SeenFeature,
    SeenLink,
    SeenNote,
    SeenRelation,
    SheetReading,
    VisionError,
)
from app.main import app
from app.models import DrawingFact, DrawingFeature, DrawingNote, DrawingSheet


def png(width=4000, height=3000, mode="RGB", color="white") -> bytes:
    out = io.BytesIO()
    Image.new(mode, (width, height), color).save(out, "PNG")
    return out.getvalue()


def feature(ref="F1", kind="bridge", label="Br 12", box=(100, 100, 300, 300), confidence="high", related=()) -> SeenFeature:
    return SeenFeature(ref=ref, type=kind, label=label, box=list(box), confidence=confidence, related=list(related))


def note(text="R = 50 FT", box=(350, 100, 450, 150), scope="feature", links=("F1",), evidence="leader_line") -> SeenNote:
    return SeenNote(
        text=text, box=list(box), confidence="high", scope=scope,
        links=[SeenLink(feature=x, evidence=evidence, confidence="high") for x in links],
    )


def value(term="turning_radius", val="50", text="R = 50 FT", unit="ft", box=(100, 100, 500, 500), confidence="high") -> ReadValue:
    return ReadValue(term=term, value=val, unit=unit, text=text, box=list(box), confidence=confidence)


class FakeVision:
    """Answers from what a test sets. `pictures` keeps what each call was sent."""

    def __init__(self, sheet=None, values=None, transcript=("R = 50 FT",), tiles=None):
        self.sheet = sheet if sheet is not None else SheetReading(features=[feature()], notes=[note()])
        self.values = values if values is not None else [value()]
        self.transcript = transcript if isinstance(transcript, Exception) else list(transcript)
        self.tiles = tiles or []
        self.abouts: list[str] = []
        self.wanted: list[tuple] = []
        self.tile_calls = 0

    def read_sheet(self, picture, calls, *, tile=False):
        calls.add("tile" if tile else "sheet", {"prompt_tokens": 100, "completion_tokens": 10})
        if isinstance(self.sheet, Exception):
            raise self.sheet
        if tile:
            answer = self.tiles[self.tile_calls] if self.tile_calls < len(self.tiles) else SheetReading(features=[], notes=[])
            self.tile_calls += 1
            return answer
        return self.sheet

    def read_values(self, picture, about, wanted, calls):
        calls.add("values", {"prompt_tokens": 10, "completion_tokens": 10})
        self.abouts.append(about)
        self.wanted.append(tuple(wanted))
        if isinstance(self.values, Exception):
            raise self.values
        return self.values

    def transcribe(self, picture, calls):
        calls.add("confirm", {"prompt_tokens": 10, "completion_tokens": 10})
        if isinstance(self.transcript, Exception):
            raise self.transcript
        return self.transcript


@pytest.fixture
def run(client, repo):
    """Uploads a sheet and processes it with the given fake model. Returns the review."""

    def go(vision: FakeVision, data: bytes | None = None, **params):
        app.dependency_overrides[get_drawing_vision] = lambda: vision
        # One worker: the fake hands out its tile answers in call order.
        app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, drawing_workers=1)
        created = client.post("/api/drawings", params={"filename": "s.png", "name": "Set A"}, content=data or png())
        assert created.status_code == 201, created.text
        drawing_id = created.json()["id"]
        res = client.post(f"/api/drawings/{drawing_id}/process", params=params)
        assert res.status_code == 200, res.text
        return client.get(f"/api/drawings/{drawing_id}/review").json()

    return go


# --- images ---------------------------------------------------------------------------------------


def test_a_scan_over_the_limit_is_shrunk_to_fit_and_keeps_its_shape():
    # A 24 x 36 inch sheet at 300 dpi is rejected by OpenAI as it is: the limit is enforced, not shrunk for us.
    assert patches(10800, 7200) > 30000
    width, height = fit_size(10800, 7200, 30000)
    assert patches(width, height) <= 30000
    assert abs(width / height - 1.5) < 0.01
    assert width > 5000  # as large as the limit allows, not needlessly small


def test_a_sheet_that_fits_goes_to_the_model_as_it_is():
    data = png(1000, 800)
    file = SheetFile(data, "image/png", 1000, 800)
    assert sheet_for_model(file, Image.open(io.BytesIO(data)), 30000) == data_url(data, "image/png")


def test_a_sheet_over_the_limit_goes_shrunk_and_thin_lines_stay_visible():
    im = Image.new("RGB", (6400, 6400), "white")
    ImageDraw.Draw(im).line((0, 3200, 6400, 3200), fill="black", width=3)
    out = io.BytesIO()
    im.save(out, "PNG")
    url = sheet_for_model(SheetFile(out.getvalue(), "image/png", 6400, 6400), im, 900)
    sent = Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("L")
    assert patches(*sent.size) <= 900
    middle = [sent.getpixel((sent.width // 2, y)) for y in range(sent.height)]
    assert min(middle) < 200


def test_tiles_overlap_and_cover_the_whole_sheet():
    tiles = tile_boxes(5000, 3000, 2000, 0.15)
    assert max(t[2] for t in tiles) == 5000 and max(t[3] for t in tiles) == 3000
    columns = sorted({t[0] for t in tiles})
    assert all(b < a + 2000 for a, b in zip(columns, columns[1:]))  # each starts before the previous one ends
    assert all(t[2] - t[0] == 2000 and t[3] - t[1] == 2000 for t in tiles)
    assert tile_boxes(1000, 800, 2000, 0.15) == [[0.0, 0.0, 1000.0, 800.0]]


def test_model_positions_map_back_to_the_sheet_from_any_picture():
    assert from_model_box([250, 500, 500, 1000], [0, 0, 4000, 3000]) == [1000, 1500, 2000, 3000]
    assert from_model_box([0, 0, 500, 500], [1000, 2000, 1400, 2200]) == [1000, 2000, 1200, 2100]  # inside a crop
    assert from_model_box([], [0, 0, 100, 100]) is None
    assert from_model_box([500, 500, 400, 600], [0, 0, 100, 100]) is None
    assert from_model_box([0, 0, 2000, 1000], [0, 0, 100, 100]) == [0, 0, 100, 100]  # clamped to the picture


def test_each_page_of_a_tiff_is_a_sheet():
    out = io.BytesIO()
    Image.new("L", (300, 200), 255).save(out, "TIFF", save_all=True, append_images=[Image.new("L", (400, 500), 255)])
    sheets = read_upload(out.getvalue())
    assert [(s.width, s.height, s.content_type) for s in sheets] == [(300, 200, "image/png"), (400, 500, "image/png")]


def test_a_photo_turned_by_exif_is_stored_upright():
    exif = Image.Exif()
    exif[274] = 6  # rotated 90 degrees
    out = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(out, "JPEG", exif=exif)
    [sheet] = read_upload(out.getvalue())
    assert (sheet.width, sheet.height, sheet.content_type) == (200, 300, "image/png")


def test_cmyk_is_converted_and_sixteen_bit_scans_keep_their_tones():
    out = io.BytesIO()
    Image.new("CMYK", (100, 100)).save(out, "JPEG")
    assert read_upload(out.getvalue())[0].content_type == "image/png"
    grey = Image.new("I;16", (10, 10), 32768)
    assert to_rgb(grey).getpixel((0, 0))[0] == pytest.approx(128, abs=1)


@pytest.mark.parametrize("data", [b"not an image", png()[:200]])
def test_a_bad_or_truncated_file_is_rejected(data):
    with pytest.raises(BadImage):
        read_upload(data)


def test_upload_rejects_what_is_not_a_drawing_image(client):
    assert client.post("/api/drawings", params={"filename": "x.pdf"}, content=b"%PDF-1.7").status_code == 400
    assert client.post("/api/drawings", params={"filename": "x.png"}, content=b"").status_code == 400
    assert client.post("/api/drawings", params={"filename": "  "}, content=png(10, 10)).status_code == 400


# --- reading --------------------------------------------------------------------------------------


def test_a_clean_read_gives_a_confirmed_proposed_fact_with_its_evidence(run, client, repo):
    review = run(FakeVision())
    sheet = review["sheets"][0]
    assert review["drawing"]["status"] == "processed"
    [f] = sheet["features"]
    assert f["type"] == "bridge" and f["bbox"] == [400, 300, 1200, 900] and f["reviewState"] == "proposed"
    [fact] = sheet["facts"]
    assert fact["status"] == "FOUND" and fact["value"] == 50 and fact["unit"] == "ft"
    assert fact["verbatimText"] == "R = 50 FT" and fact["confirmation"] == "confirmed" and fact["kind"] == "direct"
    assert fact["confidence"] == "high" and fact["reviewState"] == "proposed" and fact["featureId"] == f["id"]
    [link] = sheet["bindings"]
    assert link["evidence"] == "leader_line" and link["hasEvidence"] and link["reviewState"] == "proposed"
    assert sheet["needsReview"] == []
    evidence = client.get(f"/api/drawings/facts/{fact['id']}/evidence")
    assert evidence.status_code == 200 and evidence.headers["content-type"] == "image/png"
    assert client.get(f"/api/drawings/bindings/{link['id']}/evidence").status_code == 200
    assert client.get(f"/api/drawings/sheets/{sheet['sheet']['id']}/image").status_code == 200


def test_the_crop_holds_the_feature_and_its_note_even_far_apart(run, client):
    """The bridge is on one side of the sheet and its note on the other: they must be read from one picture."""
    review = run(FakeVision(sheet=SheetReading(features=[feature(box=(100, 100, 200, 200))], notes=[note(box=(400, 100, 500, 150))])))
    fact = review["sheets"][0]["facts"][0]
    crop = Image.open(io.BytesIO(client.get(f"/api/drawings/facts/{fact['id']}/evidence").content))
    # Sheet 4000 wide: the feature starts at x=400, the note ends at x=2000.
    assert crop.width >= 1600


def test_a_value_the_second_read_does_not_have_is_disputed_and_flagged(run):
    review = run(FakeVision(transcript=["R = 80 FT"]))
    sheet = review["sheets"][0]
    assert sheet["facts"][0]["confirmation"] == "disputed" and sheet["facts"][0]["confidence"] == "low"
    disputed = next(i for i in sheet["needsReview"] if i["kind"] == "disputed_reading")
    assert disputed["targetId"] == sheet["facts"][0]["id"]


def test_a_value_that_is_not_in_its_own_printed_text_is_disputed(run):
    review = run(FakeVision(values=[value(val="80", text="R = 50 FT")]))
    assert review["sheets"][0]["facts"][0]["confirmation"] == "disputed"


def test_spacing_and_case_do_not_dispute_a_reading(run):
    review = run(FakeVision(values=[value(text="R=50 ft")], transcript=["r = 50", "FT"]))
    assert review["sheets"][0]["facts"][0]["confirmation"] == "confirmed"


def test_when_the_second_read_fails_the_fact_is_kept_unconfirmed_and_flagged(run):
    review = run(FakeVision(transcript=VisionError("boom")))
    sheet = review["sheets"][0]
    assert sheet["facts"][0]["confirmation"] == "not_checked" and sheet["facts"][0]["confidence"] == "low"
    assert {i["kind"] for i in sheet["needsReview"]} == {"failed", "unconfirmed_reading"}


def test_a_feature_with_nothing_printed_says_not_found(run):
    review = run(FakeVision(values=[]))
    [fact] = review["sheets"][0]["facts"]
    assert fact["status"] == "NOT_FOUND" and fact["value"] is None and fact["verbatimText"] == "" and fact["hasEvidence"]


def test_a_crop_cut_in_the_wrong_place_is_a_failure_never_not_found(run):
    """The note is on the sheet, but the crop does not show it: the model's position was off."""
    sheet = run(FakeVision(values=[], transcript=["SOMETHING ELSE"]))["sheets"][0]
    assert sheet["facts"] == []
    assert any("does not show 'R = 50 FT'" in m for m in sheet["sheet"]["failures"])
    assert "failed" in {i["kind"] for i in sheet["needsReview"]}


def test_a_crop_that_shows_only_part_of_its_note_still_counts(run):
    reading = SheetReading(features=[feature()], notes=[note(text="SPAN = 120 FT\nCONC. CLASS A")])
    sheet = run(FakeVision(sheet=reading, values=[], transcript=["SPAN = 120 FT"]))["sheets"][0]
    assert sheet["sheet"]["failures"] == [] and sheet["facts"][0]["status"] == "NOT_FOUND"


def test_a_feature_with_only_a_label_is_checked_by_its_label(run):
    reading = SheetReading(features=[feature(label="Br 12")], notes=[])
    wrong = run(FakeVision(sheet=reading, values=[], transcript=["OTHER"]))["sheets"][0]
    assert wrong["facts"] == [] and any("Br 12" in m for m in wrong["sheet"]["failures"])
    right = run(FakeVision(sheet=reading, values=[], transcript=["BR 12"]))["sheets"][0]
    assert right["facts"][0]["status"] == "NOT_FOUND"


def test_without_a_second_read_nothing_is_called_not_printed(run):
    sheet = run(FakeVision(values=[], transcript=VisionError("boom")))["sheets"][0]
    assert sheet["facts"] == [] and any("boom" in m for m in sheet["sheet"]["failures"])


def test_punctuation_does_not_make_a_crop_look_misplaced(run):
    reading = SheetReading(features=[feature()], notes=[note(text="BRIDGE NO. 54-1234")])
    sheet = run(FakeVision(sheet=reading, values=[], transcript=["BRIDGE NO 54 1234"]))["sheets"][0]
    assert sheet["sheet"]["failures"] == [] and sheet["facts"][0]["status"] == "NOT_FOUND"


def test_a_value_cut_off_by_the_crop_edge_is_not_trusted(run):
    """Both reads see the same cut-off text, so they agree: being at the crop's edge is what gives it away."""
    sheet = run(FakeVision(values=[value(box=(100, 100, 1000, 500))]))["sheets"][0]
    assert sheet["facts"][0]["confirmation"] == "confirmed" and sheet["facts"][0]["confidence"] == "low"
    assert [i["kind"] for i in sheet["needsReview"]] == ["low_confidence_reading"]


def test_a_value_at_the_sheets_own_edge_is_not_cut_off(run):
    reading = SheetReading(features=[feature(box=(0, 0, 300, 300))], notes=[note(box=(0, 310, 100, 350))])
    sheet = run(FakeVision(sheet=reading, values=[value(box=(0, 100, 500, 500))]))["sheets"][0]
    assert sheet["facts"][0]["confidence"] == "high"


def test_a_note_crop_drops_the_cut_off_line_of_its_neighbour_but_keeps_a_misread(run):
    """The real case: a crop cut for note 1 also showed the start of note 2, and read 'PPM 2.1' instead of 'PPM 2.1.3'."""
    reading = SheetReading(features=[], notes=[note(text="1. ALL DIMENSIONS IN FEET.", scope="sheet", links=())])
    neighbour = value(term="standard_reference", val="PPM 2.1", text="PLANS PREPARED PER PPM 2.1", unit="")
    own_misread = value(term="dimension_unit", val="FEEF", text="1. ALL DIMENSIONS IN FEEF.", unit="")
    vision = FakeVision(sheet=reading, values=[neighbour, own_misread], transcript=["1. ALL DIMENSIONS IN FEET.", "PLANS PREPARED PER PPM 2.1"])
    sheet = run(vision)["sheets"][0]
    assert [f["verbatimText"] for f in sheet["facts"]] == ["1. ALL DIMENSIONS IN FEEF."]
    assert sheet["facts"][0]["confirmation"] == "disputed"


def test_a_targeted_read_returns_each_wanted_term_found_or_not_found():
    sheet = DrawingSheet(
        id="s", drawing_id="d", number=1, original_name="a", image_path="p", content_type="image/png",
        sha256="x", size_bytes=1, width=100, height=100,
    )
    vision = FakeVision(values=[value()])
    crop = Crop(None, [0.0, 0.0, 100.0, 100.0], [], "a thing")
    facts, _, problem = read_crop(vision, sheet, crop, png(100, 100), CallLog(), 30000, wanted=["turning_radius", "design_speed"])
    assert problem is None and vision.wanted == [("turning_radius", "design_speed")]
    assert {(f.term, f.status) for f in facts} == {("turning_radius", "FOUND"), ("design_speed", "NOT_FOUND")}


def test_a_note_with_no_clear_owner_is_left_unbound_and_flagged(run):
    review = run(FakeVision(sheet=SheetReading(features=[feature()], notes=[note(scope="unclear", links=())])))
    sheet = review["sheets"][0]
    assert sheet["notes"][0]["scope"] == "unbound" and sheet["bindings"] == []
    assert "unbound_note" in {i["kind"] for i in sheet["needsReview"]}


def test_a_link_to_a_feature_that_does_not_exist_is_not_a_link(run):
    review = run(FakeVision(sheet=SheetReading(features=[feature()], notes=[note(links=("F9",))])))
    assert review["sheets"][0]["notes"][0]["scope"] == "unbound" and review["sheets"][0]["bindings"] == []


def test_a_sheet_wide_note_becomes_a_sheet_level_fact(run):
    vision = FakeVision(
        sheet=SheetReading(features=[], notes=[note(text="ALL DIMENSIONS IN FEET", scope="sheet", links=())]),
        values=[value(term="dimension_unit", val="FEET", text="ALL DIMENSIONS IN FEET", unit="")],
        transcript=["ALL DIMENSIONS IN FEET"],
    )
    fact = run(vision)["sheets"][0]["facts"][0]
    assert fact["featureId"] is None and fact["status"] == "FOUND" and fact["value"] == "FEET"


def test_a_note_too_far_from_its_large_feature_is_read_on_its_own():
    sheet_w, sheet_h = 10000, 10000
    f = DrawingFeature(id="f", drawing_id="d", sheet_id="s", type="bridge", bbox=[0, 0, 500, 500], confidence="high", found_by="sheet")
    n = DrawingNote(id="n", drawing_id="d", sheet_id="s", text="R", bbox=[9000, 9000, 9500, 9100], confidence="high", scope="feature", found_by="sheet")
    from app.drawings.reader import Link

    result = SheetResult(features=[f], notes=[n], links=[Link("n", "f", "leader_line", "high")])
    crops = plan_crops(result, sheet_w, sheet_h, 2400)
    assert len(crops) == 2 and all(c.feature is f for c in crops)
    assert crops[0].notes == [] and crops[1].notes == [n]
    assert all(c.box[2] - c.box[0] <= 2400 for c in crops)


def test_positions_the_model_could_not_give_are_flagged_not_dropped(run):
    review = run(FakeVision(sheet=SheetReading(features=[feature(box=())], notes=[note(box=(), links=("F1",))])))
    sheet = review["sheets"][0]
    assert sheet["features"][0]["bbox"] is None and sheet["features"][0]["confidence"] == "low"
    assert sheet["facts"] == []
    assert "failed" in {i["kind"] for i in sheet["needsReview"]}


def test_a_failed_crop_is_on_the_sheet_and_the_rest_still_read(run):
    vision = FakeVision(values=VisionError("too busy"))
    sheet = run(vision)["sheets"][0]
    assert sheet["facts"] == [] and sheet["features"]  # not read is not the same as not printed
    assert any("too busy" in m for m in sheet["sheet"]["failures"])
    assert sheet["bindings"][0]["hasEvidence"]


def test_low_resolution_gets_a_warning(run):
    sheet = run(FakeVision(), data=png(800, 600))["sheets"][0]
    assert any("800 x 600" in w for w in sheet["sheet"]["warnings"])
    assert "warning" in {i["kind"] for i in sheet["needsReview"]}


def test_nothing_found_is_said_out_loud(run):
    sheet = run(FakeVision(sheet=SheetReading(features=[], notes=[])))["sheets"][0]
    assert any("no features" in w for w in sheet["sheet"]["warnings"])


def test_relations_between_features_are_kept(run):
    reading = SheetReading(
        features=[feature(related=[SeenRelation(feature="F2", relation="joins")]), feature(ref="F2", kind="alignment", label="", box=(0, 500, 1000, 600))],
        notes=[],
    )
    sheet = run(FakeVision(sheet=reading))["sheets"][0]
    by_type = {f["type"]: f for f in sheet["features"]}
    assert by_type["bridge"]["related"] == [{"featureId": by_type["alignment"]["id"], "relation": "joins"}]


# --- tile sweep -----------------------------------------------------------------------------------


def test_no_tile_calls_unless_asked(run):
    vision = FakeVision()
    assert run(vision)["drawing"]["summary"]["tile_sweep"] is False
    assert vision.tile_calls == 0


def test_a_tile_adds_what_the_sheet_pass_missed_as_low_confidence_and_merges_duplicates(run):
    # 4000 x 3000 with 2000 px tiles: the first tile is the top left, 500 x 666 of the 0..1000 scale of the sheet.
    tile = SheetReading(
        features=[
            feature(ref="F1", box=(200, 200, 600, 600)),  # the same bridge as the sheet pass (400..1200 px)
            feature(ref="F2", kind="culvert", label="", box=(800, 800, 900, 900), confidence="high"),  # new
        ],
        notes=[
            note(box=(700, 150, 900, 225)),  # the same note (1400..1800 px)
            note(text="SEE DETAIL 4", box=(800, 700, 900, 750), links=("F2",)),  # new
        ],
    )
    vision = FakeVision(tiles=[tile])
    sheet = run(vision, tile_sweep="true")["sheets"][0]
    assert vision.tile_calls > 1
    types = sorted(f["type"] for f in sheet["features"])
    assert types == ["bridge", "culvert"]
    culvert = next(f for f in sheet["features"] if f["type"] == "culvert")
    assert culvert["confidence"] == "low" and culvert["foundBy"] == "tile"
    assert sorted(n["text"] for n in sheet["notes"]) == ["R = 50 FT", "SEE DETAIL 4"]
    new_note = next(n for n in sheet["notes"] if n["text"] == "SEE DETAIL 4")
    link = next(b for b in sheet["bindings"] if b["noteId"] == new_note["id"])
    assert link["featureId"] == culvert["id"]
    bridge = next(f for f in sheet["features"] if f["type"] == "bridge")
    assert bridge["foundBy"] == "sheet" and bridge["confidence"] == "high"
    assert "low_confidence_feature" in {i["kind"] for i in sheet["needsReview"]}


def test_a_note_cut_by_a_tile_edge_is_one_note_with_its_whole_text(run):
    first = SheetReading(features=[], notes=[note(text="CONC. CL", box=(800, 100, 1000, 200), scope="sheet", links=())])
    second = SheetReading(features=[], notes=[note(text="CONC. CLASS A", box=(0, 100, 300, 200), scope="sheet", links=())])
    vision = FakeVision(sheet=SheetReading(features=[], notes=[]), tiles=[first, second], values=[])
    sheet = run(vision, data=png(3400, 2000), tile_sweep="true")["sheets"][0]
    assert [n["text"] for n in sheet["notes"]] == ["CONC. CLASS A"]


# --- running, replacing, deleting -----------------------------------------------------------------


def test_running_again_replaces_the_results_and_their_crops(client, repo, run):
    vision = FakeVision()
    review = run(vision)
    drawing_id = review["drawing"]["id"]
    first_crops = {p for p in repo.files if "/crops/" in p}
    assert len(first_crops) == 1
    assert client.post(f"/api/drawings/{drawing_id}/process").status_code == 200
    assert len(repo.drawing_facts) == 1 and len(repo.drawing_features) == 1
    second_crops = {p for p in repo.files if "/crops/" in p}
    assert len(second_crops) == 1 and second_crops != first_crops


def test_when_nothing_can_be_read_the_run_fails_and_keeps_the_previous_results(client, repo, run):
    vision = FakeVision()
    drawing_id = run(vision)["drawing"]["id"]
    vision.sheet = VisionError("OpenAI returned 400")
    res = client.post(f"/api/drawings/{drawing_id}/process")
    assert res.status_code == 422 and "400" in res.json()["error"]
    review = client.get(f"/api/drawings/{drawing_id}/review").json()
    assert review["drawing"]["status"] == "failed" and "400" in review["drawing"]["error"]
    assert len(review["sheets"][0]["features"]) == 1 and len(review["sheets"][0]["facts"]) == 1


def test_one_bad_sheet_does_not_fail_the_set(client, repo, run):
    vision = FakeVision()
    drawing_id = run(vision)["drawing"]["id"]
    assert client.post(f"/api/drawings/{drawing_id}/sheets", params={"filename": "b.png"}, content=png()).status_code == 201

    good = vision.read_sheet
    seen = {"n": 0}

    def flaky(picture, calls, *, tile=False):
        seen["n"] += 1
        if seen["n"] == 2:
            raise VisionError("second sheet broke")
        return good(picture, calls, tile=tile)

    vision.read_sheet = flaky
    res = client.post(f"/api/drawings/{drawing_id}/process")
    assert res.status_code == 200 and res.json()["status"] == "processed"
    review = client.get(f"/api/drawings/{drawing_id}/review").json()
    assert [s["sheet"]["number"] for s in review["sheets"]] == [1, 2]
    assert review["sheets"][0]["features"] and any("second sheet" in m for m in review["sheets"][1]["sheet"]["failures"])


def test_adding_a_sheet_marks_the_set_as_needing_a_new_run(client, run):
    drawing_id = run(FakeVision())["drawing"]["id"]
    res = client.post(f"/api/drawings/{drawing_id}/sheets", params={"filename": "b.png"}, content=png())
    assert res.json()["status"] == "uploaded" and [s["number"] for s in res.json()["sheets"]] == [1, 2]


def test_delete_removes_everything(client, repo, run):
    drawing_id = run(FakeVision())["drawing"]["id"]
    assert client.delete(f"/api/drawings/{drawing_id}").status_code == 204
    assert not repo.files and not repo.sheets and not repo.drawing_facts and not repo.drawing_features
    assert not repo.drawing_notes and not repo.note_bindings
    assert client.get(f"/api/drawings/{drawing_id}/review").status_code == 404


def test_process_without_a_key_says_what_to_do(client):
    app.dependency_overrides[get_drawing_vision] = lambda: None
    drawing_id = client.post("/api/drawings", params={"filename": "s.png"}, content=png()).json()["id"]
    res = client.post(f"/api/drawings/{drawing_id}/process")
    assert res.status_code == 409 and "OPENAI_API_KEY" in res.json()["error"]


def test_the_run_reports_calls_tokens_and_cost(run):
    summary = run(FakeVision())["drawing"]["summary"]
    assert summary["calls"] == {"sheet": 1, "values": 1, "confirm": 1}
    assert summary["input_tokens"] == 120 and summary["output_tokens"] == 30
    assert summary["estimated_usd"] == round((120 * 10 + 30 * 50) / 1_000_000, 4)


def test_a_fact_cannot_break_the_found_rule():
    common = dict(id="x", drawing_id="d", sheet_id="s", feature_id=None, term="t", confidence="high")
    with pytest.raises(ValueError):
        DrawingFact(**common, status="FOUND", value=5, verbatim_text="")
    with pytest.raises(ValueError):
        DrawingFact(**common, status="FOUND", verbatim_text="5")
    with pytest.raises(ValueError):
        DrawingFact(**common, status="NOT_FOUND", value=5)
    assert DrawingFact(**common, status="FOUND", value=0.0, verbatim_text="0").value == 0.0


# --- scoring --------------------------------------------------------------------------------------


def test_scoring_separates_correct_missed_and_wrong_but_confident():
    sheet = DrawingSheet(
        id="s", drawing_id="d", number=1, original_name="a", image_path="p", content_type="image/png",
        sha256="x", size_bytes=1, width=100, height=100,
    )
    features = [
        DrawingFeature(id="f1", drawing_id="d", sheet_id="s", type="bridge", bbox=[0, 0, 1, 1], confidence="high", found_by="sheet"),
        DrawingFeature(id="f2", drawing_id="d", sheet_id="s", type="ramp", bbox=[0, 0, 1, 1], confidence="high", found_by="sheet"),
    ]
    common = dict(drawing_id="d", sheet_id="s", feature_id="f1", confidence="high", status="FOUND", confirmation="confirmed")
    facts = [
        DrawingFact(id="1", term="span", value=120, verbatim_text="SPAN 120 FT", **common),
        DrawingFact(id="2", term="radius", value=80, verbatim_text="R = 80 FT", **common),
    ]
    expected = {
        "sheets": {
            "1": {
                "features": [{"type": "bridge"}, {"type": "u_turn"}],
                "values": [
                    {"feature": "bridge", "text": "span 120 ft"},
                    {"feature": "bridge", "term": "radius", "text": "R = 50 FT"},
                    {"text": "CLASS A"},
                ],
            }
        }
    }
    score = score_drawing(expected, [sheet], features, facts)
    assert (score["features_found"], score["features_missed"], score["features_extra"]) == (1, 1, 1)
    assert (score["values_correct"], score["values_wrong_confident"], score["values_missed"]) == (1, 1, 1)
    assert "sheet 1: u_turn" in score["missed"]


# --- the OpenAI client ----------------------------------------------------------------------------


def answer(content: dict | str, usage=None, refusal=None) -> httpx.Response:
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"choices": [{"message": {"content": text, "refusal": refusal}}], "usage": usage or {}})


def client_with(handler) -> OpenAIVision:
    http = httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler))
    return OpenAIVision("key", "gpt-6-astra", client=http, backoff_seconds=0)


def test_the_request_sends_the_picture_at_original_detail_with_a_strict_schema():
    bodies: list[dict] = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return answer({"features": [], "notes": []}, {"prompt_tokens": 7, "completion_tokens": 3})

    calls = CallLog()
    assert client_with(handler).read_sheet("data:image/png;base64,AA", calls) == SheetReading(features=[], notes=[])
    body = bodies[0]
    assert body["model"] == "gpt-6-astra" and "temperature" not in body
    assert body["messages"][1]["content"] == [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA", "detail": "original"}}]
    assert body["response_format"]["json_schema"]["strict"] is True
    assert calls.calls == {"sheet": 1} and calls.input_tokens == 7 and calls.output_tokens == 3


def _objects(schema):
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            yield schema
        for v in schema.values():
            yield from _objects(v)
    elif isinstance(schema, list):
        for v in schema:
            yield from _objects(v)


def test_every_object_in_the_schemas_is_valid_for_strict_mode():
    from app.drawings.vision import SHEET_SCHEMA, TRANSCRIPT_SCHEMA, VALUES_SCHEMA

    for schema in (SHEET_SCHEMA, VALUES_SCHEMA, TRANSCRIPT_SCHEMA):
        for obj in _objects(schema["schema"]):
            assert obj["additionalProperties"] is False and set(obj["required"]) == set(obj["properties"])


def test_a_bad_answer_is_asked_once_more_then_fails():
    tries = {"n": 0}

    def bad_then_good(request):
        tries["n"] += 1
        return answer("{not json") if tries["n"] == 1 else answer({"texts": ["A"]})

    assert client_with(bad_then_good).transcribe("x", CallLog()) == ["A"]
    assert tries["n"] == 2

    tries["n"] = 0

    def always_bad(request):
        tries["n"] += 1
        return answer({"wrong": True})

    with pytest.raises(VisionError):
        client_with(always_bad).transcribe("x", CallLog())
    assert tries["n"] == 2


def test_a_refusal_and_an_api_error_are_vision_errors():
    with pytest.raises(VisionError, match="refused"):
        client_with(lambda r: answer("", refusal="no")).transcribe("x", CallLog())
    with pytest.raises(VisionError, match="400"):
        client_with(lambda r: httpx.Response(400, text="too many patches")).transcribe("x", CallLog())


def test_settings_default_to_the_chosen_model_and_no_sweep():
    assert Settings(_env_file=None).drawing_max_patches <= 25000  # measured: positions go wrong near OpenAI's 30,000
    assert Settings(_env_file=None).drawing_vision_model == "gpt-6-astra"
    assert Settings(_env_file=None).drawing_tile_sweep is False
