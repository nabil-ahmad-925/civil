"""Retrieval (infra/lambdas/chat/retrieval.py): the sections found, and what their cross-references bring along."""

import pymupdf

from retrieval import search

from .conftest import FakeEmbedder, upload_and_ingest
from .pdfs import make_pdf
from .test_figures import figure_pdf


def test_hits_come_with_text_and_the_path_of_parent_sections(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    hits = search(repo, FakeEmbedder(), "title block", k=20)
    assert len(hits) == 5 and all("meaning" in h.matched_by for h in hits)
    h = next(h for h in hits if h.node.section == "2.1.3")
    assert h.path == ["2 Project Plans", "2.1 General"] and "district and county" in h.node.text


def test_a_hit_brings_along_only_the_sections_it_mentions(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    hits = search(repo, FakeEmbedder(), "alignment", k=20)
    layout = next(h for h in hits if h.node.section == "2.2")
    # "See Section 2.1.3 for the title block." in 2.2 is resolved by the internal pass.
    assert [(ln.node.section, ln.relation) for ln in layout.linked if ln.relation == "mentions"] == [("2.1.3", "mentions")]
    assert "title block" in layout.linked[0].node.text
    title_block = next(h for h in hits if h.node.section == "2.1.3")
    assert title_block.linked == []  # 2.2 points to 2.1.3, but sections that merely point to a hit are not brought along


def test_a_linked_section_that_is_also_a_hit_is_flagged(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    layout = next(h for h in search(repo, FakeEmbedder(), "alignment", k=20) if h.node.section == "2.2")
    assert layout.linked[0].also_hit is True


def test_expand_false_returns_hits_only(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    hits = search(repo, FakeEmbedder(), "alignment", k=20, expand=False)
    assert all(h.linked == [] and h.figures == [] for h in hits)


def test_search_can_be_limited_to_one_manual(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    upload_and_ingest(client, "HDM", pdfs["HDM"])
    hits = search(repo, FakeEmbedder(), "lane width", k=20, manual="HDM")
    assert hits and {h.node.manual for h in hits} == {"HDM"}


def test_figures_in_the_hit_and_mentioned_ones_come_back(client, repo):
    both = pymupdf.open(stream=make_pdf([[("h", "2.1 General"), ("b", "The block is shown in Figure 2.7 of this manual.")]]), filetype="pdf")
    both.insert_pdf(pymupdf.open(stream=figure_pdf(), filetype="pdf"))
    upload_and_ingest(client, "PPM", both.tobytes())
    hits = search(repo, FakeEmbedder(), "block", k=20)
    general = next(h for h in hits if h.node.section == "2.1")
    assert [(f.figure.label, f.relation) for f in general.figures] == [("Figure 2.7", "mentioned")]
    figures = next(h for h in hits if h.node.section == "2.2")
    assert [(f.figure.label, f.relation) for f in figures.figures] == [("Figure 2.7", "in_section")]
