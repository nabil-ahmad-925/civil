"""POST /api/chat: this server answers exactly like the chat Lambda."""

import json
import sys

import pytest

from app.config import Settings, get_settings
from app.deps import get_embedder
from app.main import app
from app.models import Node
from app.routers.chat import MAX_HISTORY, MAX_HISTORY_CHARS, _logic, get_chat_model

from .conftest import FakeEmbedder, upload_and_ingest
from .test_chat import FakeLLM
from .test_lambda_handlers import LAMBDAS, load

QUESTION = "What do layout sheets show?"
SOURCE_KEYS = {"ref", "id", "manual", "section", "title", "pageStart", "pageEnd", "score", "relation", "matchedBy", "via", "mention", "path", "excerpt", "figures"}


@pytest.fixture
def chat(client, repo, pdfs):
    """The test client with a fake model and a PPM manual loaded. Returns (post, llm)."""
    upload_and_ingest(client, "PPM", pdfs["PPM"])
    # A section longer than the excerpt limit, closest in meaning to the question, so it comes first and has to be cut.
    repo.insert_nodes([Node(id="long", document_id="d", manual="PPM", section="9.9", title="9.9 Layout sheets", page_start=1, page_end=1, ordinal=99, text="Layout sheets show the plan. " * 60, embedding=FakeEmbedder().embed([QUESTION])[0])])
    llm = FakeLLM("Layout sheets point to the title block section [S1].")
    app.dependency_overrides[get_chat_model] = lambda: llm
    app.dependency_overrides[get_settings] = lambda: Settings(chat_top_k=3, chat_min_score=-1.0)

    def post(body=None, **headers):
        return client.post("/api/chat", json=body if body is not None else {"message": QUESTION}, headers=headers)

    return post, llm


def test_it_answers_with_numbered_sources_in_the_shape_the_frontend_expects(chat):
    post, llm = chat
    res = post()
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"answer", "covered", "model", "sources"}
    assert body["covered"] is True and body["model"] == "fake" and "[S1]" in body["answer"]
    assert body["sources"] and set(body["sources"][0]) == SOURCE_KEYS
    assert body["sources"][0]["ref"] == "S1" and body["sources"][0]["relation"] == "match"
    first = body["sources"][0]
    assert first["id"] == "long" and len(first["excerpt"]) == 400  # a long section is cut to the excerpt length
    assert first["score"] == round(first["score"], 4) and 0 < first["score"] <= 1
    assert "Question: " + QUESTION in llm.calls[0][1][-1]["content"]


def test_it_gives_the_same_answer_as_the_lambda_handler(chat, repo, monkeypatch):
    post, llm = chat
    # The Lambda, run for real: its handler, with the same repository, embedder, settings and model.
    common = load(LAMBDAS / "shared" / "lambda_common.py", "lambda_common_parity")
    monkeypatch.setitem(sys.modules, "lambda_common", common)
    handler = load(LAMBDAS / "chat" / "handler.py", "chat_handler_parity").handler
    common.state["secrets"] = {"CHAT_API_KEY": "k"}
    common.state["deps"] = (repo, FakeEmbedder(), Settings(chat_top_k=3, chat_min_score=-1.0))
    common.state["llm"] = llm

    for body in ({"message": QUESTION}, {"message": QUESTION, "manual": "PPM"}, {"message": "Where is the title block described?", "history": [{"role": "user", "content": "hi"}]}):
        lam = handler({"headers": {"x-api-key": "k"}, "body": json.dumps(body)}, None)
        assert lam["statusCode"] == 200
        assert post(body).json() == json.loads(lam["body"]), body


def test_history_is_trimmed_like_the_lambda_does(chat):
    post, llm = chat
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{i}" + "x" * (MAX_HISTORY_CHARS + 50)} for i in range(MAX_HISTORY + 5)]
    assert post({"message": QUESTION, "history": history}).status_code == 200
    seen = llm.calls[0][1]
    assert len(seen) <= MAX_HISTORY + 1  # the last 20 turns + the new question
    assert all(len(m["content"]) <= MAX_HISTORY_CHARS or m["content"].startswith("Sources:") for m in seen)


@pytest.mark.parametrize(
    "body",
    [{}, {"message": ""}, {"message": "   "}, {"message": "x" * 2001}, {"message": "hi", "manual": "XYZ"}, {"message": "hi", "history": [{"role": "system", "content": "x"}]}, {"message": "hi", "history": "no"}],
)
def test_a_malformed_request_is_refused(chat, body):
    post, _ = chat
    assert post(body).status_code == 422


def test_an_api_key_is_required_only_when_one_is_configured(chat):
    post, _ = chat
    assert post().status_code == 200  # none configured: open
    app.dependency_overrides[get_settings] = lambda: Settings(chat_top_k=3, chat_min_score=-1.0, chat_api_key="s3cret")
    assert post().status_code == 401 and post(**{"x-api-key": "wrong"}).status_code == 401
    assert post(**{"x-api-key": "s3cret"}).status_code == 200


def test_a_model_failure_is_a_502_without_details(chat):
    post, llm = chat
    from_lambda = _logic()[1]

    class Down:
        model = "x"

        def complete(self, system, messages):
            raise from_lambda.LLMError("401 Unauthorized: sk-secret")

    app.dependency_overrides[get_chat_model] = lambda: Down()
    res = post()
    assert res.status_code == 502 and "sk-secret" not in res.text and res.json() == {"error": "The answer service is unavailable. Try again."}


def test_a_question_the_manuals_do_not_cover_says_so_without_asking_the_model(client, repo, pdfs):
    llm = FakeLLM("never used")
    app.dependency_overrides[get_chat_model] = lambda: llm
    app.dependency_overrides[get_settings] = lambda: Settings(chat_min_score=0.99)
    body = client.post("/api/chat", json={"message": "How do I bake sourdough bread?"}).json()  # nothing is loaded at all
    assert body["covered"] is False and body["sources"] == [] and llm.calls == []


def test_without_an_embedder_it_says_what_to_set(chat):
    post, _ = chat
    app.dependency_overrides[get_embedder] = lambda: None
    res = post()
    assert res.status_code == 503 and "EMBEDDING_PROVIDER" in res.json()["error"]
