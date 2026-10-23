import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.deps import get_embedder, get_planner, get_ref_extractor, get_repo
from app.ingestion.chunker import RegexPlanner
from app.main import app
from app.repository.memory import MemoryRepository

# The chat logic lives with the chat Lambda (infra/lambdas/chat/); its tests run here because they need this
# environment (the ingest pipeline builds the data they search).
_LAMBDAS = Path(__file__).resolve().parents[2] / "infra" / "lambdas"
for _dir in (_LAMBDAS / "chat", _LAMBDAS / "shared"):
    sys.path.insert(0, str(_dir))

from .pdfs import CADD_PAGES, HDM_PAGES, PPM_PAGES, make_pdf


class FakeEmbedder:
    """Deterministic 3-number vectors so the embedding path is exercised without any API."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.0] for t in texts]


class FakeRefExtractor:
    """Stands in for the model: finds 'Section N [of the <manual>]' / 'Table N' phrases with a plain pattern."""

    PATTERN = re.compile(
        r"(?:See\s+)?(?:(?P<manual0>CADD|HDM|PPM)\s+)?"
        r"(?P<kind>Section|Index|Table|Figure)\s+(?P<num>\d+(?:\.\d+)*[A-Z]?)"
        r"(?:\s+of\s+the\s+(?P<manual>CADD|HDM|PPM|Plans Preparation Manual)\b(?: Users Manual)?)?"
    )

    def find(self, node):
        out = []
        for m in self.PATTERN.finditer(node.text):
            kind = "section" if m["kind"] in ("Section", "Index") else m["kind"].lower()
            phrase = m.group(0).rstrip(".")
            out.append({"mention": phrase, "target_manual": {"Plans Preparation Manual": "PPM"}.get(m["manual"], m["manual"]) or m["manual0"] or "same", "target_kind": kind, "number": m["num"]})
        return out


@pytest.fixture
def repo() -> MemoryRepository:
    return MemoryRepository()


@pytest.fixture
def client(repo: MemoryRepository):
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    app.dependency_overrides[get_planner] = lambda: RegexPlanner()  # tests never call OpenAI
    app.dependency_overrides[get_ref_extractor] = lambda: FakeRefExtractor()
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def pdfs() -> dict[str, bytes]:
    return {"PPM": make_pdf(PPM_PAGES), "HDM": make_pdf(HDM_PAGES), "CADD": make_pdf(CADD_PAGES)}


def upload(client: TestClient, manual: str, data: bytes, name: str = "m.pdf", version: str = ""):
    return client.post(
        "/api/documents",
        params={"manual": manual, "version": version, "filename": name},
        content=data,
        headers={"Content-Type": "application/pdf"},
    )


def upload_and_ingest(client: TestClient, manual: str, data: bytes):
    doc = upload(client, manual, data, f"{manual}.pdf").json()
    res = client.post(f"/api/documents/{doc['id']}/ingest")
    assert res.status_code == 200, res.text
    return res.json()
