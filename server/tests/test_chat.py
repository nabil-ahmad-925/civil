from app.config import Settings
from chat_service import NOT_COVERED, answer_question

from .conftest import FakeEmbedder, upload_and_ingest


class FakeLLM:
    model = "fake"

    def __init__(self, reply="Use the title block [S1]."):
        self.reply = reply
        self.calls: list[tuple[str, list[dict]]] = []

    def complete(self, system, messages):
        self.calls.append((system, messages))
        return self.reply


def settings(**kw):
    return Settings(chat_min_score=kw.pop("chat_min_score", -1.0), **kw)


def test_the_prompt_holds_numbered_sources_with_linked_sections(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    llm = FakeLLM("Layout sheets point to the title block section [S1][S9].")
    res = answer_question(repo, FakeEmbedder(), llm, settings(chat_top_k=20), "What do layout sheets show?")
    system, messages = llm.calls[0]
    assert "ONLY the numbered sources" in system
    user = messages[-1]["content"]
    assert user.startswith("Sources:") and "Question: What do layout sheets show?" in user
    assert "[S1]" in user and "Layout sheets show the alignment" in user
    assert res.covered and res.answer.startswith("Layout sheets")
    assert all(s.ref.startswith("S") for s in res.sources)


def test_a_section_the_hits_link_to_is_added_as_a_source_with_its_origin():
    from app.models import CrossRef, Node
    from app.repository.memory import MemoryRepository

    repo = MemoryRepository()
    mk = lambda i, sec, text, emb: Node(id=i, document_id="d", manual="PPM", section=sec, title=f"{sec} T", page_start=1, page_end=1, ordinal=0, text=text, embedding=emb)  # noqa: E731
    repo.insert_nodes([mk("a", "1.1", "Layout. See Section 2-1.1.", [1.0, 0.0, 0.0]), mk("b", "2-1.1", "Title block rules.", [0.0, 1.0, 0.0])])
    repo.insert_cross_refs([CrossRef(id="r", document_id="d", from_node_id="a", mention="Section 2-1.1", target_manual="PPM", target_section="2-1.1", target_label="PPM 2-1.1", target_node_id="b", status="resolved", resolved_by="internal_pass")])

    class Query:
        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    llm = FakeLLM("Layout [S1] points to the title block rules [S2].")
    res = answer_question(repo, Query(), llm, settings(chat_top_k=1), "layout sheets")
    assert [(s.ref, s.section, s.relation, s.via) for s in res.sources] == [("S1", "1.1", "match", None), ("S2", "2-1.1", "mentions", "S1")]
    user = llm.calls[0][1][-1]["content"]
    assert "[S2] PPM 2-1.1 T" in user and 'referenced from S1: "Section 2-1.1"' in user and "Title block rules." in user


def test_nothing_close_enough_is_not_covered_and_the_model_is_not_called(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    llm = FakeLLM()
    res = answer_question(repo, FakeEmbedder(), llm, settings(chat_min_score=2.0), "anything")
    assert res.answer == NOT_COVERED and not res.covered and res.sources == [] and llm.calls == []


def test_only_cited_sources_are_returned_and_uncited_answers_show_the_matches(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    cited = answer_question(repo, FakeEmbedder(), FakeLLM("Only this one [S2]."), settings(chat_top_k=3), "alignment layout")
    assert [s.ref for s in cited.sources] == ["S2"]
    none = answer_question(repo, FakeEmbedder(), FakeLLM("No citations here."), settings(chat_top_k=2), "alignment layout")
    assert none.sources and {s.relation for s in none.sources} == {"match", "mentions"}
    # a returned section always comes with the sections its own text mentions, cited or not
    shown = {s.ref for s in none.sources}
    assert all(s.via in shown for s in none.sources if s.relation == "mentions")
    assert any(s.relation == "mentions" and s.section == "2.1.3" for s in none.sources)


def test_a_short_follow_up_is_searched_with_the_previous_question(client, repo, pdfs):
    from chat_service import _search_query

    history = [{"role": "user", "content": "How wide is a lane?"}, {"role": "assistant", "content": "12 feet [S1]."}]
    assert _search_query("and for ramps?", history) == "How wide is a lane? and for ramps?"
    assert _search_query("What is the minimum shoulder width on freeways?", history) == "What is the minimum shoulder width on freeways?"


def test_figures_of_a_source_come_with_a_link(client, repo):
    import pymupdf

    from .pdfs import make_pdf
    from .test_figures import figure_pdf

    both = pymupdf.open(stream=make_pdf([[("h", "2.1 General"), ("b", "The block is shown in Figure 2.7 of this manual.")]]), filetype="pdf")
    both.insert_pdf(pymupdf.open(stream=figure_pdf(), filetype="pdf"))
    upload_and_ingest(client, "PPM", both.tobytes())
    res = answer_question(repo, FakeEmbedder(), FakeLLM(), settings(chat_top_k=20), "block")
    figs = [f for s in res.sources for f in s.figures]
    assert figs and figs[0].label == "Figure 2.7" and figs[0].url.startswith("memory://figures/")


def test_a_long_section_is_cut_to_the_passages_that_hold_the_question_words():
    from chat_service import _excerpt

    filler = "\n".join(f"Line {i} about something else entirely, repeated to make the section long." for i in range(120))
    text = "Section start.\n" + filler + "\nThe Master Topographic file (bb file) is kept by Surveys.\n" + filler
    out = _excerpt(text, "which section mentions the Master Topographic File?", 1500)
    assert len(out) <= 1500 + 100
    assert out.startswith("Section start.") and "Master Topographic file (bb file)" in out and "[…]" in out
    assert _excerpt("short", "anything", 1500) == "short"
    # With no usable question words it falls back to the start of the section.
    assert "Master Topographic" not in _excerpt(text, "what is this?", 1500)
