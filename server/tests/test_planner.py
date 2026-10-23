import json
import logging

import httpx
import pytest

from app.ingestion.chunker import chunk_pdf
from app.ingestion.pdf_reader import Line
from app.ingestion.planner import (
    MAX_HEADING_LINES,
    OpenAIPlanner,
    PageInput,
    PlannerError,
    Start,
    validate_starts,
)

from .pdfs import make_pdf


def L(text: str, page: int = 1) -> Line:
    return Line(page=page, text=text, size=12.0, bold=False)


# ---- validation --------------------------------------------------------------------------------


def test_validate_keeps_good_starts_and_drops_doubtful_ones(caplog):
    lines = [L("1 Scope"), L("text"), L("1.1 Purpose"), L("more"), L("not a number")]
    planned = [
        Start(2, 1, "1.1", 2),   # fine
        Start(0, 1, "1", 1),     # fine (given out of order on purpose)
        Start(9, 1, "2", 1),     # line outside the page
        Start(3, 1, "7", 2),     # model's section is not in the line's text
        Start(1, 0, "1", 2),     # zero-line heading
        Start(4, 3, "1", 2),     # runs past the end of the page
        Start(2, 1, "1.1", 99),  # level out of range (and overlaps the kept start)
    ]
    kept = validate_starts(planned, lines)
    assert [(s.line, s.section) for s in kept] == [(0, "1"), (2, "1.1")]
    assert "outside the page" in caplog.text and "not in the heading text" in caplog.text


def test_overlapping_headings_are_dropped():
    lines = [L("1 Big"), L("Title wraps"), L("1.1 Next")]
    kept = validate_starts([Start(0, 2, "1", 1), Start(1, 1, "1.1", 2)], lines)  # second starts inside the first
    assert [s.line for s in kept] == [0]


# ---- cutting, with a scripted planner ----------------------------------------------------------


class Scripted:
    """Answers from a fixed plan and records what it was shown."""

    def __init__(self, plan: dict[int, list[Start]]):
        self.plan, self.seen = plan, []

    def plan_page(self, page: PageInput) -> list[Start]:
        self.seen.append(page)
        return self.plan.get(page.page, [])


PAGES = [
    [("b", "CHAPTER 1"), ("b", "INTRODUCTION TO THE"), ("b", "MANUAL"), ("b", "This chapter introduces the manual."),
     ("b", "SECTION 1-1 GENERAL"), ("b", "General text on page one.")],
    [("b", "General text continues on page two."), ("b", "1-1.1 Purpose"), ("b", "Purpose text.")],
    [("b", "1-1.2 Application of Plan"), ("b", "Standards"), ("b", "Standards text."), ("b", "1. A list item stays in the body.")],
]
PLAN = {
    1: [Start(0, 3, "1", 1), Start(4, 1, "1-1", 2)],
    2: [Start(1, 1, "1-1.1", 3)],
    3: [Start(0, 2, "1-1.2", 3)],
}


def test_nodes_run_from_start_to_next_start_across_pages():
    nodes = {n.section: n for n in chunk_pdf(make_pdf(PAGES), "PPM", "doc", Scripted(PLAN)).nodes}
    assert list(nodes) == ["1", "1-1", "1-1.1", "1-1.2"]

    assert nodes["1"].title == "CHAPTER 1 INTRODUCTION TO THE MANUAL"  # the heading as printed; wrapped lines joined
    assert nodes["1"].text == "This chapter introduces the manual."  # and is not part of the body
    # a node continues onto the next page until the next start
    assert nodes["1-1"].text == "General text on page one.\nGeneral text continues on page two."
    assert (nodes["1-1"].page_start, nodes["1-1"].page_end) == (1, 2)
    assert nodes["1-1"].title == "SECTION 1-1 GENERAL"
    assert nodes["1-1.2"].title == "1-1.2 Application of Plan Standards"
    assert nodes["1-1.2"].text == "Standards text.\n1. A list item stays in the body."
    assert nodes["1-1.2"].page_start == 3


def test_parents_and_chapter_follow_the_planned_levels_not_the_numbering():
    nodes = {n.section: n for n in chunk_pdf(make_pdf(PAGES), "PPM", "doc", Scripted(PLAN)).nodes}
    assert nodes["1"].parent_id is None
    assert nodes["1-1"].parent_id == nodes["1"].id
    assert nodes["1-1.1"].parent_id == nodes["1-1"].id
    assert nodes["1-1.2"].parent_id == nodes["1-1"].id  # a level-3 sibling pops 1-1.1 and lands under 1-1
    assert {n.chapter for n in nodes.values()} == {"CHAPTER 1 INTRODUCTION TO THE MANUAL"}


def test_every_line_is_accounted_for_and_text_before_the_first_heading_is_kept_out():
    pages = [[("b", "Cover text"), ("b", "1 Scope"), ("b", "Body")], [("b", "More body")]]
    result = chunk_pdf(make_pdf(pages), "PPM", "d", Scripted({1: [Start(1, 1, "1", 1)]}))
    assert result.unassigned_lines == 1
    assert [n.text for n in result.nodes] == ["Body\nMore body"]


def texts(lines):
    return [ln.text for ln in lines]


def test_the_planner_is_shown_one_page_at_a_time_with_its_neighbours():
    planner = Scripted(PLAN)
    chunk_pdf(make_pdf(PAGES), "PPM", "doc", planner)
    assert [p.page for p in planner.seen] == [1, 2, 3]
    assert "none yet" in planner.seen[0].context
    assert "CHAPTER 1 INTRODUCTION TO THE MANUAL > SECTION 1-1 GENERAL" in planner.seen[1].context
    assert planner.seen[2].context.startswith(
        "Current position: CHAPTER 1 INTRODUCTION TO THE MANUAL > SECTION 1-1 GENERAL > 1-1.1 Purpose"
    )
    # the first page has nothing before it, the last has nothing after it
    assert planner.seen[0].before == [] and planner.seen[2].after == []
    # page 2 sees how page 1 ended and how page 3 begins
    assert texts(planner.seen[1].before)[-2:] == ["SECTION 1-1 GENERAL", "General text on page one."]
    assert texts(planner.seen[1].after)[:2] == ["1-1.2 Application of Plan", "Standards"]


def test_the_size_of_the_window_is_a_setting():
    for size, expected in ((2, 2), (0, 0)):
        planner = Scripted(PLAN)
        chunk_pdf(make_pdf(PAGES), "PPM", "doc", planner, context_lines=size)
        assert len(planner.seen[1].before) == expected and len(planner.seen[1].after) == expected


# ---- a heading cut by a page break ---------------------------------------------------------------

SPAN_PAGES = [
    [("b", "Intro text."), ("h", "1-1.2 Application of Plan Preparation")],
    [("h", "Standards"), ("b", "Body starts here."), ("b", "More body.")],
    [("h", "1-1.3 Next heading"), ("b", "Last body.")],
]
SPAN_PLAN = {1: [Start(1, 2, "1-1.2", 3)], 3: [Start(0, 1, "1-1.3", 3)]}


def test_a_heading_cut_by_a_page_break_is_read_whole(caplog):
    caplog.set_level(logging.INFO, logger="app.ingestion.chunker")
    planner = Scripted(SPAN_PLAN)
    result = chunk_pdf(make_pdf(SPAN_PAGES), "PPM", "doc", planner)

    first = result.nodes[0]
    assert first.title == "1-1.2 Application of Plan Preparation Standards"
    assert first.text == "Body starts here.\nMore body."  # the continuation is not body text
    assert (first.page_start, first.page_end) == (1, 2)
    assert result.nodes[1].section == "1-1.3"
    assert result.unassigned_lines == 1  # "Intro text."
    # the line the heading took is gone from page 2, so it cannot be planned again
    assert texts(planner.seen[1].lines) == ["Body starts here.", "More body."]
    # but the model on page 1 was shown it as a preview, numbered on from the page
    assert texts(planner.seen[0].after)[:1] == ["Standards"]
    assert "a heading continues onto page 2 (1 line(s))" in caplog.text


def test_a_page_that_is_only_the_end_of_a_heading_is_skipped():
    pages = [
        [("b", "Intro"), ("h", "CHAPTER 1")],
        [("h", "INTRODUCTION")],
        [("h", "1-1 Section"), ("b", "Text.")],
    ]
    planner = Scripted({1: [Start(1, 2, "1", 1)], 3: [Start(0, 1, "1-1", 2)]})
    result = chunk_pdf(make_pdf(pages), "PPM", "doc", planner)
    assert [p.page for p in planner.seen] == [1, 3]  # nothing left on page 2 to plan
    first = result.nodes[0]
    assert (first.title, first.text) == ("CHAPTER 1 INTRODUCTION", "")
    assert (first.page_start, first.page_end) == (1, 2)


def test_lines_that_do_not_look_like_the_heading_are_not_taken_from_the_next_page():
    pages = [
        [("b", "Intro"), ("h", "1-1.2 Application of Plan Preparation")],
        [("b", "Standards"), ("b", "Body.")],  # plain text: not part of the heading
    ]
    planner = Scripted({1: [Start(1, 2, "1-1.2", 3)]})
    first = chunk_pdf(make_pdf(pages), "PPM", "doc", planner).nodes[0]
    assert first.title == "1-1.2 Application of Plan Preparation"
    assert first.text == "Standards\nBody."
    assert texts(planner.seen[1].lines) == ["Standards", "Body."]


def test_a_heading_may_run_into_the_preview_but_never_start_in_it():
    page = [L("text"), L("1.1 Long heading")]
    preview = [L("continues"), L("body")]
    assert validate_starts([Start(1, 2, "1.1", 2)], page, preview) == [Start(1, 2, "1.1", 2)]
    assert validate_starts([Start(2, 1, "1.2", 2)], page, preview) == []  # starts in the preview
    assert validate_starts([Start(1, 4, "1.1", 2)], page, preview) == []  # needs more lines than exist


def test_a_heading_is_trimmed_to_the_lines_set_like_its_first_line(caplog):
    lines = [Line(1, "1.1 Title", 14.0, True), Line(1, "continues", 14.0, True), Line(1, "body text", 10.0, False)]
    assert validate_starts([Start(0, 3, "1.1", 2)], lines) == [Start(0, 2, "1.1", 2)]
    assert "Trimming planned heading" in caplog.text


def test_a_heading_is_never_longer_than_the_cap():
    lines = [Line(1, "1.1 a", 14.0, True)] + [Line(1, f"w{i}", 14.0, True) for i in range(6)]
    assert validate_starts([Start(0, 7, "1.1", 2)], lines)[0].heading_lines == MAX_HEADING_LINES


def test_a_bad_plan_from_the_model_cannot_corrupt_the_nodes():
    bad = Scripted({1: [Start(0, 3, "1", 1), Start(99, 1, "2", 1), Start(4, 1, "wrong", 2)]})
    nodes = chunk_pdf(make_pdf(PAGES), "PPM", "doc", bad).nodes
    assert [n.section for n in nodes] == ["1"]  # the two bad starts were dropped
    assert "General text on page one." in nodes[0].text  # so the rest just stays in the open node


def test_progress_is_logged_per_page(caplog):
    caplog.set_level(logging.INFO, logger="app.ingestion.chunker")
    chunk_pdf(make_pdf(PAGES), "PPM", "doc", Scripted(PLAN))
    assert "page 2 (2 of 3 with text): 1 heading(s)" in caplog.text
    assert "chunk_pdf done: 4 nodes" in caplog.text


# ---- OpenAIPlanner against a fake OpenAI -------------------------------------------------------


def fake_openai(handler) -> httpx.Client:
    return httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler))


def reply(starts: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"starts": starts})}}]})


PAGE = PageInput(
    page=4,
    lines=[L("text before"), L("1-1.2 Application of Plan"), L("Standards"), L("body")],
    body_size=12.0,
    context="Current position: 1 > 1-1",
)


def test_openai_planner_sends_numbered_lines_and_a_strict_schema_and_parses_the_answer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content), path=request.url.path)
        return reply([{"line": 1, "heading_lines": 2, "section": "1-1.2", "level": 3}])

    starts = OpenAIPlanner("k", "gpt-4.1-mini", client=fake_openai(handler)).plan_page(PAGE)

    assert starts == [Start(line=1, heading_lines=2, section="1-1.2", level=3)]
    assert seen["path"].endswith("/chat/completions") and seen["model"] == "gpt-4.1-mini"
    assert seen["temperature"] == 0
    assert seen["response_format"]["json_schema"]["strict"] is True
    user = seen["messages"][1]["content"]
    assert "[0] text before" in user and "[1] 1-1.2 Application of Plan" in user and "[3] body" in user
    assert "Current position: 1 > 1-1" in user and "Current page 4" in user


def test_newer_models_get_no_temperature():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return reply([])

    OpenAIPlanner("k", "gpt-5.4-mini", client=fake_openai(handler)).plan_page(PAGE)
    assert "temperature" not in seen


def test_an_empty_page_makes_no_api_call():
    def handler(request):
        raise AssertionError("must not be called")

    assert OpenAIPlanner("k", "m", client=fake_openai(handler)).plan_page(PageInput(1, [], 12.0, "")) == []


def test_errors_become_planner_errors():
    refusing = lambda r: httpx.Response(200, json={"choices": [{"message": {"content": None, "refusal": "no"}}]})
    garbage = lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
    missing = lambda r: reply([{"line": 1}])
    denied = lambda r: httpx.Response(401, json={"error": {"message": "bad key"}})
    for handler, text in ((refusing, "refused"), (garbage, "Could not read"), (missing, "Could not read"), (denied, "401")):
        with pytest.raises(PlannerError, match=text):
            OpenAIPlanner("k", "m", client=fake_openai(handler), max_retries=0).plan_page(PAGE)


def test_rate_limits_are_retried_then_succeed():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429) if len(calls) < 3 else reply([])

    planner = OpenAIPlanner("k", "m", client=fake_openai(handler), max_retries=3, backoff_seconds=0)
    assert planner.plan_page(PAGE) == [] and len(calls) == 3


def test_the_whole_path_with_a_fake_openai():
    def handler(request):
        page = json.loads(request.content)["messages"][1]["content"]
        if "Current page 1 " in page:
            return reply([{"line": 0, "heading_lines": 3, "section": "1", "level": 1}, {"line": 4, "heading_lines": 1, "section": "1-1", "level": 2}])
        if "Current page 2 " in page:
            return reply([{"line": 1, "heading_lines": 1, "section": "1-1.1", "level": 3}])
        return reply([{"line": 0, "heading_lines": 2, "section": "1-1.2", "level": 3}])

    result = chunk_pdf(make_pdf(PAGES), "PPM", "doc", OpenAIPlanner("k", "m", client=fake_openai(handler)))
    assert [n.section for n in result.nodes] == ["1", "1-1", "1-1.1", "1-1.2"]


def test_every_node_is_logged_with_its_place_and_text(caplog):
    caplog.set_level(logging.INFO, logger="app.ingestion.chunker")
    chunk_pdf(make_pdf(PAGES), "PPM", "doc", Scripted(PLAN))
    text = caplog.text
    assert "node 1/4  §1  'CHAPTER 1 INTRODUCTION TO THE MANUAL'" in text
    assert "parent=none" in text and "parent=§1-1" in text
    assert "pages=1-2" in text  # §1-1 runs over a page break
    assert "| General text on page one." in text and "| General text continues on page two." in text
    assert "(no text under this heading)" not in text.split("node 3/4")[1].split("node 4/4")[0]


def test_the_message_has_three_blocks_in_order_with_the_right_numbering():
    page = PageInput(
        page=5,
        lines=[L("a"), L("b"), L("c")],
        body_size=12.0,
        context="Current position: 1",
        before=[L("prev one"), L("prev two")],
        after=[L("next one"), L("next two")],
    )
    from app.ingestion.planner import build_user_message

    message = build_user_message(page)
    assert message.index("End of the previous page") < message.index("Current page 5") < message.index("Start of the next page")
    assert "  prev one\n  prev two" in message and "[0] prev one" not in message  # context: not numbered
    assert "[0] a\n[1] b\n[2] c" in message
    assert "[3] next one\n[4] next two" in message  # the preview carries on from the page
    # no neighbours, no blocks
    bare = build_user_message(PageInput(1, [L("only")], 12.0, "ctx"))
    assert "previous page" not in bare and "next page" not in bare
