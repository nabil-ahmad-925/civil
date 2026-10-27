import pymupdf

from app.ingestion.resolution import cross_manual_pass, internal_pass
from app.ingestion.tables import capture_tables
from app.models import CrossRef, Document, Node
from app.repository.memory import MemoryRepository

from .conftest import upload_and_ingest
from .pdfs import make_pdf


def _grid(page, x: int, y: int, rows: list[list[str]], col_w: int = 100, row_h: int = 24) -> None:
    """A ruled table: every cell is a drawn box with its text inside."""
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            cell = pymupdf.Rect(x + c * col_w, y + r * row_h, x + (c + 1) * col_w, y + (r + 1) * row_h)
            page.draw_rect(cell)
            page.insert_text((cell.x0 + 4, cell.y1 - 8), text, fontsize=10, fontname="helv")


def table_pdf() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 120), "2.2 Tables", fontsize=14, fontname="hebo")
    page.insert_text((50, 145), "Table 2.9 lists the limits for every sheet in the set.", fontsize=10, fontname="helv")
    # A caption above a ruled table.
    page.insert_text((100, 200), "Table 2.7 Design Speeds", fontsize=10, fontname="helv")
    _grid(page, 100, 210, [["Class", "Speed"], ["A", "65"], ["B", "55"]])
    # A caption-looking line with no grid near it.
    page.insert_text((100, 600), "Table 2.8 Missing Grid", fontsize=10, fontname="helv")
    data = doc.tobytes()
    doc.close()
    return data


def test_a_caption_with_a_grid_next_to_it_is_captured_with_its_cells_and_a_png():
    [cap] = capture_tables(table_pdf(), "PPM", "d1", [], dpi=72)
    t = cap.table
    assert (t.label, t.number, t.page) == ("Table 2.7", "2.7", 1)
    assert t.caption == "Table 2.7 Design Speeds"
    assert t.rows == [["Class", "Speed"], ["A", "65"], ["B", "55"]]
    assert t.image_path == "tables/d1/table-2-7.png"
    assert cap.png.startswith(b"\x89PNG")
    x0, y0, x1, y1 = t.bbox
    assert x0 <= 100 and y0 <= 200 and x1 >= 300 and y1 >= 282  # caption and grid are both inside


def test_a_body_sentence_or_a_caption_without_a_grid_is_not_a_table():
    labels = [c.table.label for c in capture_tables(table_pdf(), "PPM", "d1", [])]
    assert "Table 2.9" not in labels and "Table 2.8" not in labels


def test_a_page_without_any_caption_is_not_searched_for_grids():
    assert capture_tables(make_pdf([[("h", "2.1 General"), ("b", "Nothing tabular here.")]]), "PPM", "d1", []) == []


def test_the_table_belongs_to_the_node_that_holds_its_caption():
    nodes = [
        Node(id="a", document_id="d1", manual="PPM", section="2.0", title="2.0 Other", page_start=1, page_end=1, ordinal=0, text="nothing"),
        Node(id="b", document_id="d1", manual="PPM", section="2.2", title="2.2 Tables", page_start=1, page_end=1, ordinal=1, text="x\nTable 2.7 Design Speeds"),
    ]
    [cap] = capture_tables(table_pdf(), "PPM", "d1", nodes)
    assert cap.table.node_id == "b"


def _ref(doc: Document, label: str, manual: str = "PPM", status: str = "unresolved") -> CrossRef:
    return CrossRef(
        id=f"r-{label}-{manual}", document_id=doc.id, from_node_id="n", mention=label, target_manual=manual,
        target_label=f"{manual} {label}", status=status,
    )


def _doc(i: str, manual: str) -> Document:
    return Document(id=i, manual=manual, version="v", original_name="x.pdf", size_bytes=1, sha256="s", storage_path="p", status="ingested")


def test_internal_pass_links_a_table_reference_to_the_table():
    repo = MemoryRepository()
    doc = _doc("d1", "PPM")
    [cap] = capture_tables(table_pdf(), "PPM", "d1", [])
    repo.insert_tables([cap.table])
    repo.insert_cross_refs([_ref(doc, "Table 2.7"), _ref(doc, "Table 2.99")])
    assert internal_pass(repo, doc) == {"resolved": 1, "still_unresolved": 1}
    refs = {r.target_label: r for r in repo.list_refs("d1")}
    assert refs["PPM Table 2.7"].status == "resolved"
    assert refs["PPM Table 2.7"].target_table_id == cap.table.id
    assert refs["PPM Table 2.99"].status == "unresolved"


def test_a_table_and_a_figure_with_the_same_number_do_not_get_mixed_up():
    repo = MemoryRepository()
    doc = _doc("d1", "PPM")
    [cap] = capture_tables(table_pdf(), "PPM", "d1", [])
    repo.insert_tables([cap.table])
    repo.insert_cross_refs([_ref(doc, "Figure 2.7")])  # only the table 2.7 exists
    internal_pass(repo, doc)
    [ref] = repo.list_refs("d1")
    assert (ref.status, ref.target_table_id, ref.target_figure_id) == ("unresolved", None, None)


def test_cross_manual_pass_links_a_table_in_another_manual_and_lets_go_when_it_is_deleted():
    repo = MemoryRepository()
    ppm, hdm = _doc("d1", "PPM"), _doc("d2", "HDM")
    [cap] = capture_tables(table_pdf(), "PPM", "d1", [])
    repo.insert_tables([cap.table])
    repo.insert_cross_refs([_ref(hdm, "Table 2.7", manual="PPM", status="awaiting_cross")])
    cross_manual_pass(repo, [ppm, hdm])
    [ref] = repo.list_refs("d2")
    assert (ref.status, ref.target_table_id) == ("resolved", cap.table.id)
    repo.delete_tables_for_document("d1")
    cross_manual_pass(repo, [ppm, hdm])
    [ref] = repo.list_refs("d2")
    assert (ref.status, ref.target_table_id) == ("unresolved", None)


def test_ingest_saves_tables_serves_them_and_links_the_reference(client, repo):
    text_pdf = make_pdf([[("h", "2.1 General"), ("b", "The speeds are in Table 2.7 of this manual.")]])
    both = pymupdf.open(stream=text_pdf, filetype="pdf")
    both.insert_pdf(pymupdf.open(stream=table_pdf(), filetype="pdf"))
    doc = upload_and_ingest(client, "PPM", both.tobytes())
    assert doc["summary"]["tables"] == 1

    [tab] = client.get("/api/tables", params={"document_id": doc["id"]}).json()
    assert tab["label"] == "Table 2.7" and tab["page"] == 2 and "rows" not in tab
    detail = client.get(f"/api/tables/{tab['id']}").json()
    assert detail["rows"] == [["Class", "Speed"], ["A", "65"], ["B", "55"]]
    img = client.get(f"/api/tables/{tab['id']}/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content.startswith(b"\x89PNG")
    assert client.get("/api/tables/nope").status_code == 404

    rows = client.get("/api/cross-refs").json()
    row = next(r for r in rows if r["targetLabel"] == "PPM Table 2.7")
    assert row["status"] == "resolved" and row["targetTable"]["id"] == tab["id"]

    # Ingesting again replaces the table and its image instead of adding a second one.
    client.post(f"/api/documents/{doc['id']}/ingest")
    assert len(client.get("/api/tables", params={"document_id": doc["id"]}).json()) == 1
    assert len([p for p in repo.files if p.startswith("tables/")]) == 1

    client.delete(f"/api/documents/{doc['id']}")
    assert not [p for p in repo.files if p.startswith("tables/")]
    assert repo.tables == {}
