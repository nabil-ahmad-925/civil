"""Vectors for finding nodes. The stored text is never changed; the embedding only helps search find it.

The embedding model accepts at most 8,191 tokens per input. A node can be longer than that, so:
  - text up to SPLIT_THRESHOLD tokens is embedded whole (same vector as before);
  - longer text is cut into CHUNK_TOKENS-sized pieces (with a little overlap), each piece is embedded, and the
    piece vectors are averaged (weighted by size) into ONE vector. The result is still EMBEDDING_DIM numbers, so
    the database column and the search code do not change.
Nothing is silently cut off any more.
"""

import logging
import math
import re
import time
from functools import lru_cache
from typing import Protocol

import httpx

from ..config import Settings

log = logging.getLogger(__name__)

# The model's hard limit is 8,191 tokens. Splitting starts a little below it as a safety margin.
SPLIT_THRESHOLD = 8_000
CHUNK_TOKENS = 1_000  # size of each piece of a long text; small pieces make a sharper average
OVERLAP_TOKENS = 100  # how much a piece repeats from the end of the previous one
BATCH_SIZE = 64  # texts per request
MAX_REQUEST_TOKENS = 250_000  # tokens per request (OpenAI allows about 300k)
MAX_ATTEMPTS = 5

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


# --- tokens ------------------------------------------------------------------------------------------------------


class _CharCounter:
    """Fallback when the tokenizer cannot be loaded: assumes ~2 characters per token, which over-counts on purpose
    (numbers and tables are the densest text), so a text is never judged shorter than it is."""

    def encode(self, text: str, disallowed_special=()) -> list[int]:  # noqa: ARG002 - same signature as tiktoken
        return [0] * math.ceil(len(text) / 2)


@lru_cache(maxsize=1)
def _tokenizer():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")  # the tokenizer of text-embedding-3-*
    except Exception:  # noqa: BLE001 - e.g. the vocabulary file cannot be downloaded
        log.warning("tiktoken is unavailable; counting tokens by characters (over-estimate).")
        return _CharCounter()


def count_tokens(text: str) -> int:
    # disallowed_special=() makes text like "<|endoftext|>" count as ordinary text instead of raising.
    return len(_tokenizer().encode(text, disallowed_special=()))


# --- splitting ---------------------------------------------------------------------------------------------------


def _hard_cut(text: str) -> list[str]:
    """Last resort for one unbroken run longer than a piece: cut it by tokens (or characters in the fallback)."""
    enc = _tokenizer()
    if isinstance(enc, _CharCounter):
        size, step = CHUNK_TOKENS * 2, (CHUNK_TOKENS - OVERLAP_TOKENS) * 2
        return [text[i : i + size] for i in range(0, len(text), step)]
    tokens = enc.encode(text, disallowed_special=())
    step = CHUNK_TOKENS - OVERLAP_TOKENS
    return [enc.decode(tokens[i : i + CHUNK_TOKENS]) for i in range(0, len(tokens), step)]


def _units(text: str) -> list[str]:
    """Pieces no longer than CHUNK_TOKENS, cut at the most natural place: lines first (paragraphs, table rows),
    then sentences, then (only if needed) by raw tokens."""
    out: list[str] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        if count_tokens(line) <= CHUNK_TOKENS:
            out.append(line)
            continue
        for sentence in _SENTENCE_END.split(line):
            if count_tokens(sentence) <= CHUNK_TOKENS:
                out.append(sentence)
            else:
                out.extend(_hard_cut(sentence))
    return out


def split_text(text: str) -> list[str]:
    """[text] if it fits; otherwise pieces of about CHUNK_TOKENS tokens that overlap by about OVERLAP_TOKENS."""
    if count_tokens(text) <= SPLIT_THRESHOLD:
        return [text]
    chunks: list[str] = []
    current: list[tuple[str, int]] = []  # (unit, tokens)
    size = 0
    for unit in _units(text):
        n = count_tokens(unit)
        if current and size + n > CHUNK_TOKENS:
            chunks.append("\n".join(u for u, _ in current))
            # Start the next piece with the last few units of this one, up to OVERLAP_TOKENS. If even the last unit
            # alone is bigger than that (long paragraphs), still repeat it, as long as it is at most half a piece.
            tail: list[tuple[str, int]] = []
            tail_size = 0
            for u, t in reversed(current):
                if tail_size + t > OVERLAP_TOKENS and not (not tail and t <= CHUNK_TOKENS // 2):
                    break
                tail.insert(0, (u, t))
                tail_size += t
            current, size = tail, tail_size
        current.append((unit, n))
        size += n
    if current:
        chunks.append("\n".join(u for u, _ in current))
    return chunks


# --- pooling -----------------------------------------------------------------------------------------------------


def pool(vectors: list[list[float]], weights: list[int]) -> list[float]:
    """One vector from many: the average weighted by piece size, scaled to length 1 (search uses cosine)."""
    if len(vectors) == 1:
        return vectors[0]  # nothing to merge; keep the model's own vector untouched
    total = sum(weights)
    dim = len(vectors[0])
    mean = [sum(w * v[i] for v, w in zip(vectors, weights, strict=True)) / total for i in range(dim)]
    norm = math.sqrt(sum(x * x for x in mean)) or 1.0
    return [x / norm for x in mean]


# --- the embedder ------------------------------------------------------------------------------------------------


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
        """One vector per text, in order. Long texts are split, embedded in pieces and pooled."""
        pieces: list[str] = []  # every piece of every text, flat
        owner: list[int] = []  # which text each piece belongs to
        sizes: list[int] = []  # tokens in each piece (the pooling weight)
        for i, text in enumerate(texts):
            parts = split_text(text)
            if len(parts) > 1:
                log.info("Text %d is long: split into %d pieces for embedding.", i, len(parts))
            for part in parts:
                pieces.append(part if part.strip() else " ")
                owner.append(i)
                sizes.append(max(count_tokens(part), 1))

        piece_vectors = self._embed_pieces(pieces, sizes)

        grouped: dict[int, list[int]] = {}
        for p, i in enumerate(owner):
            grouped.setdefault(i, []).append(p)
        return [pool([piece_vectors[p] for p in grouped[i]], [sizes[p] for p in grouped[i]]) for i in range(len(texts))]

    def _embed_pieces(self, pieces: list[str], sizes: list[int]) -> list[list[float]]:
        out: list[list[float]] = []
        start = 0
        while start < len(pieces):
            end, tokens = start, 0
            while end < len(pieces) and end - start < BATCH_SIZE and (end == start or tokens + sizes[end] <= MAX_REQUEST_TOKENS):
                tokens += sizes[end]
                end += 1
            out.extend(self._request(pieces[start:end]))
            start = end
        return out

    def _request(self, batch: list[str]) -> list[list[float]]:
        """One API call. Retries rate limits (429), server errors (5xx) and network errors with a growing pause."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                res = self.client.post("/embeddings", json={"model": self.model, "input": batch, "dimensions": self.dim})
            except httpx.TransportError as exc:  # timeout, dropped connection, ...
                problem = str(exc) or type(exc).__name__
            else:
                if res.status_code == 429 or res.status_code >= 500:
                    problem = f"HTTP {res.status_code}"
                else:
                    res.raise_for_status()  # any other 4xx (e.g. 400) is a bug on our side: do not retry
                    data = sorted(res.json()["data"], key=lambda d: d["index"])
                    return [d["embedding"] for d in data]
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"Embedding request failed after {MAX_ATTEMPTS} attempts: {problem}")
            pause = 2 ** (attempt - 1)
            log.warning("Embedding request failed (%s); retrying in %ss (attempt %d/%d).", problem, pause, attempt, MAX_ATTEMPTS)
            time.sleep(pause)
        raise AssertionError("unreachable")


def build_embedder(settings: Settings) -> Embedder | None:
    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=openai needs OPENAI_API_KEY.")
        return OpenAIEmbedder(settings.openai_api_key, settings.embedding_model, settings.embedding_dim)
    return None
