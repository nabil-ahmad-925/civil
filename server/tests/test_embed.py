import math

import httpx
import pytest

from app.ingestion import embeddings
from app.ingestion.embeddings import OpenAIEmbedder, count_tokens, pool, split_text
from app.ingestion.pipeline import embed_missing
from app.models import Document, Node
from app.repository.memory import MemoryRepository


class FakeEmbedder:
    def embed(self, texts):
        return [[float(len(t))] for t in texts]


def test_embed_missing_fills_only_nodes_without_vectors():
    repo = MemoryRepository()
    doc = Document(id="d", manual="PPM", version="1", original_name="a.pdf", size_bytes=1, sha256="x", storage_path="p", status="ingested")
    base = dict(document_id="d", manual="PPM", page_start=1, page_end=1)
    repo.insert_nodes([
        Node(id="a", section="1", title="T", ordinal=0, text="body", **base),
        Node(id="b", section="2", title="U", ordinal=1, text="x", embedding=[9.0], **base),
    ])
    assert embed_missing(repo, FakeEmbedder(), doc) == 1
    assert repo.nodes["a"].embedding == [float(len("T\nbody"))]
    assert repo.nodes["b"].embedding == [9.0]
    assert embed_missing(repo, FakeEmbedder(), doc) == 0


def test_embed_missing_one_failing_node_does_not_block_the_others():
    class Flaky:
        def embed(self, texts):
            if any("BAD" in t for t in texts):
                raise RuntimeError("boom")
            return [[1.0] for _ in texts]

    repo = MemoryRepository()
    doc = Document(id="d", manual="PPM", version="1", original_name="a.pdf", size_bytes=1, sha256="x", storage_path="p", status="ingested")
    base = dict(document_id="d", manual="PPM", page_start=1, page_end=1)
    repo.insert_nodes([
        Node(id="a", section="1", title="T", ordinal=0, text="fine", **base),
        Node(id="b", section="2", title="U", ordinal=1, text="BAD", **base),
        Node(id="c", section="3", title="V", ordinal=2, text="fine too", **base),
    ])
    assert embed_missing(repo, Flaky(), doc) == 2
    assert repo.nodes["a"].embedding == [1.0]
    assert repo.nodes["b"].embedding is None  # stays missing, so a re-run retries it
    assert repo.nodes["c"].embedding == [1.0]


# --- splitting ---------------------------------------------------------------------------------------------------


def _long_text(paragraphs: int = 120) -> str:
    return "\n".join(f"Paragraph {i}. " + "The lane width shall be twelve feet on all state highways. " * 8 for i in range(paragraphs))


def test_short_text_is_not_split():
    assert split_text("short text") == ["short text"]


def test_long_text_is_split_into_pieces_within_the_chunk_size():
    text = _long_text()
    assert count_tokens(text) > embeddings.SPLIT_THRESHOLD
    chunks = split_text(text)
    assert len(chunks) > 1
    assert all(count_tokens(c) <= embeddings.CHUNK_TOKENS for c in chunks)


def test_split_loses_no_text_and_pieces_overlap():
    text = _long_text()
    chunks = split_text(text)
    joined = "\n".join(chunks)
    for i in range(120):
        assert f"Paragraph {i}." in joined  # every paragraph is in at least one piece
    # Overlap: the last line of a piece starts the next one.
    assert chunks[0].split("\n")[-1] in chunks[1]


def test_one_unbroken_run_longer_than_a_piece_is_still_cut():
    text = "x" * 250_000  # no spaces, lines or sentences (about 30k tokens)
    assert count_tokens(text) > embeddings.SPLIT_THRESHOLD
    chunks = split_text(text)
    assert len(chunks) > 1
    assert all(count_tokens(c) <= embeddings.CHUNK_TOKENS for c in chunks)


# --- pooling -----------------------------------------------------------------------------------------------------


def test_pool_of_one_vector_is_that_vector():
    assert pool([[0.6, 0.8]], [5]) == [0.6, 0.8]


def test_pool_is_weighted_and_unit_length():
    v = pool([[1.0, 0.0], [0.0, 1.0]], [3, 1])
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0)
    assert v[0] > v[1]  # the bigger piece pulls harder


# --- the OpenAI embedder (no network: the HTTP call is faked) -----------------------------------------------------


def _embedder(handler) -> OpenAIEmbedder:
    e = OpenAIEmbedder("key", "text-embedding-3-small", 4)
    e.client = httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler))
    return e


def _ok(request: httpx.Request) -> httpx.Response:
    import json

    texts = json.loads(request.content)["input"]
    # Each text's vector is [1, 0, 0, 0] unless it mentions "other", so pooled results can be told apart.
    return httpx.Response(200, json={"data": [{"index": i, "embedding": [0.0, 1.0, 0.0, 0.0] if "other" in t else [1.0, 0.0, 0.0, 0.0]} for i, t in enumerate(texts)]})


def test_embedder_returns_one_vector_per_text_in_order_mixing_short_and_long():
    calls: list[int] = []

    def handler(request):
        import json

        calls.append(len(json.loads(request.content)["input"]))
        return _ok(request)

    out = _embedder(handler).embed(["short other", _long_text(), "short"])
    assert len(out) == 3
    assert out[0] == [0.0, 1.0, 0.0, 0.0]  # a short text keeps the model's vector as is
    assert out[2] == [1.0, 0.0, 0.0, 0.0]
    assert math.isclose(math.sqrt(sum(x * x for x in out[1])), 1.0)  # the long one was pooled
    assert sum(calls) > 3  # the long text went out in several pieces


def test_embedder_sends_an_empty_text_as_a_space():
    seen: list[list[str]] = []

    def handler(request):
        import json

        seen.append(json.loads(request.content)["input"])
        return _ok(request)

    _embedder(handler).embed([""])
    assert seen == [[" "]]


def test_embedder_retries_rate_limits_then_succeeds(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(429) if attempts["n"] < 3 else _ok(request)

    assert _embedder(handler).embed(["a"]) == [[1.0, 0.0, 0.0, 0.0]]
    assert attempts["n"] == 3


def test_embedder_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="failed after"):
        _embedder(lambda request: httpx.Response(503)).embed(["a"])


def test_embedder_does_not_retry_a_400(monkeypatch):
    monkeypatch.setattr(embeddings.time, "sleep", lambda s: None)
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        return httpx.Response(400)

    with pytest.raises(httpx.HTTPStatusError):
        _embedder(handler).embed(["a"])
    assert attempts["n"] == 1


def test_ingest_still_succeeds_when_one_node_cannot_be_embedded(client, repo, pdfs):
    from app.deps import get_embedder
    from app.main import app

    class FailsOnSecondNode:
        def __init__(self):
            self.singles = 0

        def embed(self, texts):
            if len(texts) > 1:  # the whole batch fails, so the pipeline must fall back to one node at a time
                raise RuntimeError("batch failed")
            self.singles += 1
            if self.singles == 2:
                raise RuntimeError("this node failed")
            return [[1.0, 0.0, 0.0]]

    app.dependency_overrides[get_embedder] = lambda: FailsOnSecondNode()
    from .conftest import upload_and_ingest

    doc = upload_and_ingest(client, "PPM", pdfs["PPM"])
    assert doc["status"] == "ingested"
    nodes = list(repo.nodes.values())
    assert len(nodes) > 2
    assert sum(n.embedding is None for n in nodes) == 1
    assert doc["summary"]["vectors_failed"] == 1
    assert doc["summary"]["vectors"] == len(nodes) - 1
