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
