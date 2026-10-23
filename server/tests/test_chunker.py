import logging

from app.ingestion.chunker import chunk_pdf

from .pdfs import CADD_PAGES, HDM_PAGES, PPM_PAGES, make_pdf


def test_cuts_one_node_per_numbered_heading():
    result = chunk_pdf(make_pdf(PPM_PAGES), "PPM", "doc-1")
    assert [n.section for n in result.nodes] == ["2", "2.1", "2.1.3", "2.2", "2.3"]
    assert [n.title for n in result.nodes][2] == "2.1.3 Title Block"
    assert result.pages == 3


def test_node_text_is_exact_source_text_up_to_next_heading():
    nodes = {n.section: n for n in chunk_pdf(make_pdf(PPM_PAGES), "PPM", "doc-1").nodes}
    assert nodes["2.1.3"].text == "The title block shall show the district and county."
    assert nodes["2.2"].text.splitlines() == [
        "Layout sheets show the alignment. See Section 2.1.3 for the title block.",
        "Line weights follow Section 2.6 of the CADD Users Manual.",
        "Levels are listed in Table 2.4A.",
    ]


def test_running_header_and_page_numbers_are_not_in_the_text():
    result = chunk_pdf(make_pdf(PPM_PAGES), "PPM", "doc-1")
    everything = "\n".join(n.text for n in result.nodes)
    assert "Caltrans Manual 2024" not in everything
    assert "Page " not in everything


def test_pages_and_parents():
    nodes = {n.section: n for n in chunk_pdf(make_pdf(PPM_PAGES), "PPM", "doc-1").nodes}
    assert nodes["2.1.3"].page_start == 1
    assert nodes["2.2"].page_start == 2 and nodes["2.2"].page_end == 2
    assert nodes["2.1.3"].parent_id == nodes["2.1"].id
    assert nodes["2.1"].parent_id == nodes["2"].id
    assert nodes["2"].parent_id is None


def test_keyword_headings_and_chapter_are_recognised():
    nodes = chunk_pdf(make_pdf(HDM_PAGES), "HDM", "doc-2").nodes
    assert [n.section for n in nodes] == ["300", "301.1", "301.5"]
    assert nodes[1].chapter == "Chapter 300 Geometric Cross Section"
    assert nodes[1].title == "Index 301.1 Lane Width"


def test_body_sentences_starting_with_numbers_are_not_headings():
    pages = [[("h", "1 Scope"), ("b", "12 feet is the minimum. It applies everywhere.")]]
    nodes = chunk_pdf(make_pdf(pages), "PPM", "d").nodes
    assert [n.section for n in nodes] == ["1"]


def test_pdf_without_headings_yields_no_nodes():
    pages = [[("b", "Just a paragraph with no headings at all.")]]
    result = chunk_pdf(make_pdf(pages), "CADD", "d")
    assert result.nodes == [] and result.unassigned_lines > 0


def test_cadd_fixture_chunks():
    nodes = chunk_pdf(make_pdf(CADD_PAGES), "CADD", "d").nodes
    assert [n.section for n in nodes] == ["2", "2.6"]
    assert "Text height is set per level." in nodes[1].text


def test_logs_the_file_info_before_cutting(caplog):
    caplog.set_level(logging.INFO, logger="app.ingestion.chunker")
    pdf = make_pdf(PPM_PAGES)
    result = chunk_pdf(pdf, "PPM", "doc-1")
    text = caplog.text
    assert f"manual=PPM document_id=doc-1 size={len(pdf)} bytes pages=3 lines=" in text
    assert "page=1 size=14.0 bold=True | 2 Project Plans" in text
    assert result.pages == 3  # the normal result is still returned after logging
