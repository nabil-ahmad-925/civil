"""The Lambda handlers (infra/lambdas/handlers.py), run with the in-memory repository and a fake model."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.models import CrossRef, Node
from app.repository.memory import MemoryRepository

LAMBDAS = Path(__file__).resolve().parents[2] / "infra" / "lambdas"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def h(monkeypatch):
    # In the Lambda the shared helper sits next to handler.py; here it is put on the path.
    monkeypatch.syspath_prepend(str(LAMBDAS / "shared"))
    monkeypatch.delitem(sys.modules, "lambda_common", raising=False)
    common = load(LAMBDAS / "shared" / "lambda_common.py", "lambda_common")
    sys.modules["lambda_common"] = common
    mod = SimpleNamespace(
        common=common,
        chat=load(LAMBDAS / "chat" / "handler.py", "chat_handler").handler,
        health=load(LAMBDAS / "health" / "handler.py", "health_handler").handler,
    )

    repo = MemoryRepository()
    node = lambda i, sec, text, emb: Node(id=i, document_id="d", manual="PPM", section=sec, title=f"{sec} T", page_start=1, page_end=1, ordinal=0, text=text, embedding=emb)  # noqa: E731
    repo.insert_nodes([node("a", "1.1", "Layout. See Section 2-1.1.", [1.0, 0.0, 0.0]), node("b", "2-1.1", "Title block rules.", [0.0, 1.0, 0.0])])
    repo.insert_cross_refs([CrossRef(id="r", document_id="d", from_node_id="a", mention="Section 2-1.1", target_manual="PPM", target_section="2-1.1", target_label="PPM 2-1.1", target_node_id="b", status="resolved", resolved_by="internal_pass")])

    class Embedder:
        def embed(self, texts):
            return [[1.0, 0.0, 0.0] for _ in texts]

    class LLM:
        model = "fake"
        seen: list = []

        def complete(self, system, messages):
            self.seen.append(messages)
            return "Layout [S1] points to the title block rules [S2]."

    llm = LLM()
    common.state["secrets"] = {"CHAT_API_KEY": "k3y"}
    common.state["deps"] = (repo, Embedder(), Settings(chat_top_k=1, chat_min_score=0.1))
    common.state["llm"] = llm
    mod.llm = llm
    return mod


def call(fn, body=None, key="k3y", qs=None):
    event = {"headers": {"x-api-key": key} if key is not None else {}, "body": json.dumps(body) if body is not None else None, "queryStringParameters": qs}
    res = fn(event, None)
    return res["statusCode"], json.loads(res["body"])


def test_chat_returns_the_answer_with_sources_and_the_linked_section(h):
    status, body = call(h.chat, {"message": "What do layout sheets show?"})
    assert status == 200
    assert body["covered"] and "[S2]" in body["answer"]
    assert [(s["ref"], s["section"], s["relation"], s["via"]) for s in body["sources"]] == [("S1", "1.1", "match", None), ("S2", "2-1.1", "mentions", "S1")]
    assert body["sources"][1]["excerpt"] == "Title block rules."


def test_requests_without_the_right_key_are_refused(h):
    assert call(h.chat, {"message": "x"}, key=None)[0] == 401
    assert call(h.chat, {"message": "x"}, key="nope")[0] == 401


@pytest.mark.parametrize(
    "body",
    [{}, {"message": "  "}, {"message": "x" * 2001}, {"message": "hi", "manual": "x" * 81}, {"message": "hi", "history": "no"}, {"message": "hi", "history": [{"role": "system", "content": "x"}]}],
)
def test_bad_chat_input_is_a_400(h, body):
    assert call(h.chat, body)[0] == 400


def test_chat_history_reaches_the_model(h):
    call(h.chat, {"message": "and for ramps?", "history": [{"role": "user", "content": "How wide is a lane?"}, {"role": "assistant", "content": "12 ft [S1]."}]})
    roles = [m["role"] for m in h.llm.seen[-1]]
    assert roles == ["user", "assistant", "user"]


def test_a_model_failure_is_a_502_without_details(h):
    from llm_client import LLMError

    def boom(system, messages):
        raise LLMError("OpenAI returned 500: secret details")

    h.llm.complete = boom
    status, body = call(h.chat, {"message": "layout sheets"})
    assert status == 502 and "secret" not in json.dumps(body)


def test_health_needs_no_key(h):
    assert h.health({}, None)["statusCode"] == 200


def test_the_paradedb_backend_without_its_connection_string_is_a_clear_500(h, monkeypatch):
    from app.config import get_settings

    for key in ("OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "PARADEDB_URL"):
        monkeypatch.setenv(key, "")  # put back afterwards: deps() writes the secret's values into the environment
    monkeypatch.setenv("KEYWORD_BACKEND", "paradedb")
    get_settings.cache_clear()
    h.common.state.pop("deps")
    h.common.state["secrets"] = {"CHAT_API_KEY": "k3y", "OPENAI_API_KEY": "o", "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "s"}
    try:
        status, body = call(h.chat, {"message": "hello there"})
    finally:
        get_settings.cache_clear()
    assert status == 500 and "PARADEDB_URL" in body["error"]


def test_the_optional_paradedb_url_is_kept_when_the_secret_is_read_and_unknown_keys_are_not(h, monkeypatch):
    secret = {"CHAT_API_KEY": "k3y", "OPENAI_API_KEY": "o", "PARADEDB_URL": "postgresql://u:p@h/db", "SOMETHING_ELSE": "x"}
    fake_boto3 = SimpleNamespace(client=lambda name: SimpleNamespace(get_secret_value=lambda SecretId: {"SecretString": json.dumps(secret)}))
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setenv("SECRET_ARN", "arn:test")
    h.common.state.pop("secrets")
    assert h.common.secrets() == {"CHAT_API_KEY": "k3y", "OPENAI_API_KEY": "o", "PARADEDB_URL": "postgresql://u:p@h/db"}
