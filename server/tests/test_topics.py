"""Documents without numbered headings: uploaded under a typed manual name, cut into topics by a planner."""

from app.deps import get_topic_planner
from app.ingestion.planner import PageInput, Start

from .conftest import upload, upload_and_ingest
from .pdfs import make_pdf

MEMO_PAGES = [
    [("b", "Intro: culverts carry water under the road."), ("b", "They are sized for the design flood.")],
    [("b", "Next: ditches run beside the road."), ("b", "Ditches are lined where the slope is steep.")],
    [("b", "Lining is described in the standard drawings."), ("b", "See Section 2.1.3 of the PPM for the title block.")],
]


class FakeTopicPlanner:
    """Stands in for the model: a topic starts on each line that begins with one of the given words."""

    topics = True

    def __init__(self, starts: dict[str, str]) -> None:
        self.starts = starts
        self.seen: list[PageInput] = []

    def plan_page(self, page: PageInput) -> list[Start]:
        self.seen.append(page)
        return [
            Start(line=i, heading_lines=0, section="", level=1, title=title)
            for i, ln in enumerate(page.lines)
            for word, title in self.starts.items()
            if ln.text.startswith(word)
        ]


def use_planner(client, planner):
    from app.main import app

    app.dependency_overrides[get_topic_planner] = lambda: planner


def test_a_document_without_headings_is_cut_into_topics_at_the_planned_lines(client, repo):
    use_planner(client, FakeTopicPlanner({"Intro": "Culverts", "Next": "Ditches"}))
    doc = upload_and_ingest(client, "Drainage Memo", make_pdf(MEMO_PAGES))
    assert doc["manual"] == "Drainage Memo" and doc["status"] == "ingested"

    nodes = sorted(repo.nodes.values(), key=lambda n: n.ordinal)
    assert [(n.section, n.title, n.parent_id) for n in nodes] == [("1", "Culverts", None), ("2", "Ditches", None)]
    assert all(n.manual == "Drainage Memo" for n in nodes)
    # the first line of a topic is its text; the document's own words are untouched
    assert nodes[0].text == "Intro: culverts carry water under the road.\nThey are sized for the design flood."
    assert nodes[1].text.startswith("Next: ditches run beside the road.") and nodes[1].text.endswith("title block.")
    assert (nodes[0].page_start, nodes[0].page_end) == (1, 1) and (nodes[1].page_start, nodes[1].page_end) == (2, 3)
    assert all(n.embedding for n in nodes)


def test_text_before_the_first_planned_topic_is_not_lost(client, repo):
    use_planner(client, FakeTopicPlanner({"Next": "Ditches"}))
    upload_and_ingest(client, "Drainage Memo", make_pdf(MEMO_PAGES))
    nodes = sorted(repo.nodes.values(), key=lambda n: n.ordinal)
    assert len(nodes) == 2
    assert nodes[0].text.startswith("Intro: culverts") and nodes[0].title.startswith("Intro: culverts")  # titled from its text
    assert nodes[1].title == "Ditches"


def test_the_planner_is_told_which_topic_is_open(client, repo):
    planner = FakeTopicPlanner({"Intro": "Culverts", "Next": "Ditches"})
    use_planner(client, planner)
    upload_and_ingest(client, "Drainage Memo", make_pdf(MEMO_PAGES))
    assert [p.context for p in planner.seen] == [
        "none yet (start of the document)", "Current topic: Culverts", "Current topic: Ditches",
    ]
    assert planner.seen[1].before and planner.seen[1].after  # the previous and next page come with it


def test_a_typed_name_is_tidied_and_spelled_like_the_first_upload(client, repo):
    pdf = make_pdf(MEMO_PAGES)
    assert upload(client, "  Drainage   Memo ", pdf).json()["manual"] == "Drainage Memo"
    assert upload(client, "drainage memo", pdf).json()["manual"] == "Drainage Memo"
    assert upload(client, "ppm", pdf).json()["manual"] == "PPM"
    assert all(path.startswith(("other/", "PPM/")) for path in repo.files)  # a typed name is never a storage folder


def test_a_document_without_headings_needs_the_model(client, repo):
    use_planner(client, None)
    doc = upload(client, "Drainage Memo", make_pdf(MEMO_PAGES)).json()
    res = client.post(f"/api/documents/{doc['id']}/ingest")
    assert res.status_code == 409 and "OPENAI_API_KEY" in res.json()["error"]
    assert repo.nodes == {}


def test_references_to_a_manual_are_followed_and_own_numbering_is_not(client, repo, pdfs):
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    use_planner(client, FakeTopicPlanner({"Intro": "Culverts"}))
    memo = upload_and_ingest(client, "Drainage Memo", make_pdf([*MEMO_PAGES, [("b", "Intro again. See Section 4 for details.")]]))
    refs = [r for r in repo.refs.values() if r.document_id == memo["id"]]
    # "Section 4" names no manual, so it would mean the memo itself: its topics are numbered by the code, not printed
    assert [(r.target_manual, r.target_label, r.status) for r in refs] == [("PPM", "PPM 2.1.3", "resolved")]
    assert repo.nodes[refs[0].target_node_id].section == "2.1.3"


def test_the_openai_topic_planner_sends_the_page_and_reads_line_numbers_and_titles():
    import json

    import httpx

    from app.ingestion.pdf_reader import Line
    from app.ingestion.planner import OpenAITopicPlanner, build_topic_message

    sent = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        content = json.dumps({"starts": [{"line": 1, "title": "Ditches"}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(answer))
    planner = OpenAITopicPlanner("key", "gpt-4.1-mini", client=client)
    line = lambda text: Line(page=2, text=text, size=10.0, bold=False)  # noqa: E731
    page = PageInput(
        page=2, lines=[line("one"), line("two")], body_size=10.0, context="Current topic: Culverts",
        before=[line("end of page 1")], after=[line("start of page 3")],
    )
    assert planner.topics is True
    assert planner.plan_page(page) == [Start(line=1, heading_lines=0, section="", level=1, title="Ditches")]
    message = sent[0]["messages"][1]["content"]
    assert message == build_topic_message(page)
    assert "[0] one" in message and "[1] two" in message and "[2] start of page 3" in message and "end of page 1" in message
    assert sent[0]["response_format"]["json_schema"]["name"] == "page_topics"
