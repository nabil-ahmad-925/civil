import pymupdf

from app.ingestion.figures import capture_figures
from app.ingestion.resolution import cross_manual_pass, internal_pass
from app.models import CrossRef, Document, Node
from app.repository.memory import MemoryRepository

from .conftest import upload_and_ingest
from .pdfs import make_pdf


def figure_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 120), "2.2 Figures", fontsize=14, fontname="hebo")
    page.insert_text((50, 145), "Figure 2.9 shows the layout of every sheet in the set.", fontsize=10, fontname="helv")
    # A drawing (vector, no embedded image) with its caption underneath.
    page.draw_rect(pymupdf.Rect(100, 200, 400, 400))
    page.draw_line((100, 200), (400, 400))
    page.insert_text((100, 420), "Figure 2.7 Title Block", fontsize=10, fontname="helv")
    # A caption-looking line with nothing drawn near it.
    page.insert_text((100, 600), "Figure 2.8 Missing Drawing", fontsize=10, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def test_a_caption_with_a_drawing_next_to_it_is_captured_as_png():
    [cap] = capture_figures(figure_pdf(), "PPM", "d1", [], dpi=72)
    assert cap.figure.label == "Figure 2.7"
    assert cap.figure.number == "2.7"
    assert cap.figure.caption == "Figure 2.7 Title Block"
    assert cap.figure.page == 1
    assert cap.figure.image_path == "figures/d1/figure-2-7.png"
    assert cap.png.startswith(b"\x89PNG")
    x0, y0, x1, y1 = cap.figure.bbox
    assert x0 <= 100 and y0 <= 200 and x1 >= 400 and y1 >= 420  # drawing and caption are both inside


def test_a_body_sentence_or_a_caption_without_a_drawing_is_not_a_figure():
    labels = [c.figure.label for c in capture_figures(figure_pdf(), "PPM", "d1", [])]
    assert "Figure 2.9" not in labels and "Figure 2.8" not in labels


def test_the_figure_belongs_to_the_node_that_holds_its_caption():
    nodes = [
        Node(id="a", document_id="d1", manual="PPM", section="2.0", title="2.0 Other", page_start=1, page_end=1, ordinal=0, text="nothing"),
        Node(id="b", document_id="d1", manual="PPM", section="2.2", title="2.2 Figures", page_start=1, page_end=1, ordinal=1, text="x\nFigure 2.7 Title Block"),
    ]
    [cap] = capture_figures(figure_pdf(), "PPM", "d1", nodes)
    assert cap.figure.node_id == "b"


def _ref(doc: Document, label: str, manual: str = "PPM", status: str = "unresolved") -> CrossRef:
    return CrossRef(
        id=f"r-{label}-{manual}", document_id=doc.id, from_node_id="n", mention=label, target_manual=manual,
        target_label=f"{manual} {label}", status=status,
    )


def _doc(i: str, manual: str) -> Document:
    return Document(id=i, manual=manual, version="v", original_name="x.pdf", size_bytes=1, sha256="s", storage_path="p", status="ingested")


def test_internal_pass_links_a_figure_reference_to_the_figure():
    repo = MemoryRepository()
    doc = _doc("d1", "PPM")
    [cap] = capture_figures(figure_pdf(), "PPM", "d1", [])
    repo.insert_figures([cap.figure])
    repo.insert_cross_refs([_ref(doc, "Figure 2.7"), _ref(doc, "Figure 2.99")])
    result = internal_pass(repo, doc)
    assert result == {"resolved": 1, "still_unresolved": 1}
    refs = {r.target_label: r for r in repo.list_refs("d1")}
    assert refs["PPM Figure 2.7"].status == "resolved"
    assert refs["PPM Figure 2.7"].target_figure_id == cap.figure.id
    assert refs["PPM Figure 2.99"].status == "unresolved"


def test_cross_manual_pass_links_a_figure_in_another_manual():
    repo = MemoryRepository()
    ppm, hdm = _doc("d1", "PPM"), _doc("d2", "HDM")
    [cap] = capture_figures(figure_pdf(), "PPM", "d1", [])
    repo.insert_figures([cap.figure])
    repo.insert_cross_refs([_ref(hdm, "Figure 2.7", manual="PPM", status="awaiting_cross")])
    cross_manual_pass(repo, [ppm, hdm])
    [ref] = repo.list_refs("d2")
    assert (ref.status, ref.target_figure_id) == ("resolved", cap.figure.id)
    # The figures go away -> the reference is recomputed and no longer points at them.
    repo.delete_figures_for_document("d1")
    cross_manual_pass(repo, [ppm, hdm])
    [ref] = repo.list_refs("d2")
    assert (ref.status, ref.target_figure_id) == ("unresolved", None)


def test_ingest_saves_figures_serves_the_image_and_links_the_reference(client, repo):
    pdf = figure_pdf()
    # A page with a section that mentions the figure, then the figure page.
    text_pdf = make_pdf([[("h", "2.1 General"), ("b", "The block is shown in Figure 2.7 of this manual.")]])
    both = pymupdf.open(stream=text_pdf, filetype="pdf")
    both.insert_pdf(pymupdf.open(stream=pdf, filetype="pdf"))
    doc = upload_and_ingest(client, "PPM", both.tobytes())
    assert doc["summary"]["figures"] == 1

    [fig] = client.get("/api/figures", params={"document_id": doc["id"]}).json()
    assert fig["label"] == "Figure 2.7" and fig["page"] == 2
    img = client.get(f"/api/figures/{fig['id']}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content.startswith(b"\x89PNG")

    rows = client.get("/api/cross-refs").json()
    row = next(r for r in rows if r["targetLabel"] == "PPM Figure 2.7")
    assert row["status"] == "resolved" and row["targetFigure"]["id"] == fig["id"]

    # Ingesting again replaces the figure and its image instead of adding a second one.
    client.post(f"/api/documents/{doc['id']}/ingest")
    assert len(client.get("/api/figures", params={"document_id": doc["id"]}).json()) == 1
    assert len([p for p in repo.files if p.startswith("figures/")]) == 1

    client.delete(f"/api/documents/{doc['id']}")
    assert not [p for p in repo.files if p.startswith("figures/")]
    assert repo.figures == {}


def test_an_underlined_heading_above_the_caption_is_not_taken_for_the_drawing():
    # Like PPM Figure 2-11: an underlined heading, the caption, then a wide, short picture under it.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((200, 140), "Projects Prepared by Consultants", fontsize=12, fontname="hebo")
    page.draw_line((200, 144), (420, 144))  # the underline
    page.insert_text((250, 175), "FIGURE 2-11", fontsize=14, fontname="hebo")
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 500, 34), False)
    pix.clear_with(200)
    page.insert_image(pymupdf.Rect(56, 200, 556, 234), pixmap=pix)
    data = doc.tobytes()
    doc.close()

    [cap] = capture_figures(data, "PPM", "d1", [])
    x0, y0, x1, y1 = cap.figure.bbox
    assert x0 <= 56 and x1 >= 556  # the whole picture, not just the underline's width
    assert y1 >= 234
    assert y0 > 144  # the heading above the caption is left out
