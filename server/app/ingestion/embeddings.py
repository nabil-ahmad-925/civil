"""Vectors for finding nodes. The stored text is never changed; the embedding only helps search find it."""

from typing import Protocol

import httpx

from ..config import Settings

# Keeps a single oversized section under the model's input limit. Only the vector input is cut.
MAX_INPUT_CHARS = 20_000
BATCH_SIZE = 64


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    def __init__(self, api_key: str, model: str, dim: int) -> None:
        self.model = model
        self.dim = dim
        self.client = httpx.Client(
            base_url="https://api.openai.com/v1",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = [t[:MAX_INPUT_CHARS] or " " for t in texts[i : i + BATCH_SIZE]]
            res = self.client.post("/embeddings", json={"model": self.model, "input": batch, "dimensions": self.dim})
            res.raise_for_status()
            data = sorted(res.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
        return out


def build_embedder(settings: Settings) -> Embedder | None:
    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=openai needs OPENAI_API_KEY.")
        return OpenAIEmbedder(settings.openai_api_key, settings.embedding_model, settings.embedding_dim)
    return None
