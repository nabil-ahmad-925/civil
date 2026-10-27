"""POST /api/chat: the same answer as the chat Lambda (infra/lambdas/chat), computed by this server.

The chat logic is not copied. It is loaded from the Lambda's own folder, so this endpoint and the deployed Lambda run the
very same code and cannot drift apart (tests/test_chat_endpoint.py checks that they answer identically). The request and
the response have the Lambda's shape, so the Next.js route works with either: for this server set CHAT_API_URL to
http://localhost:8000/api in client/.env.local (the route adds /chat) and CHAT_API_KEY to any value, or to CHAT_API_KEY of
server/.env when that is set.

Differences from the Lambda: a malformed request is a 422 here (the Lambda says 400), and the API key is optional.
"""

import hmac
import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..config import Settings, get_settings
from ..deps import get_embedder, get_repo
from ..ingestion.embeddings import Embedder
from ..models import Manual
from ..repository.base import Repository
from ..schemas import CamelModel

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# Where the chat logic is: next to the chat Lambda.
CHAT_DIR = Path(__file__).resolve().parents[3] / "infra" / "lambdas" / "chat"
# The Lambda's limits (infra/lambdas/shared/lambda_common.py and chat/handler.py).
MAX_MESSAGE_CHARS = 2000
MAX_HISTORY = 20
MAX_HISTORY_CHARS = 4000
EXCERPT_CHARS = 400


class ChatTurnIn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatIn(CamelModel):
    message: str = Field(max_length=MAX_MESSAGE_CHARS)
    history: list[ChatTurnIn] = Field(default_factory=list)
    manual: Manual | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message is required.")
        return value.strip()


class ChatFigureOut(CamelModel):
    id: str
    label: str
    caption: str
    page: int
    url: str  # temporary link to the picture


class ChatSourceOut(CamelModel):
    ref: str
    id: str
    manual: Manual
    section: str
    title: str
    page_start: int
    page_end: int
    score: float | None
    relation: Literal["match", "mentions", "mentioned_by"]
    matched_by: list[str]
    via: str | None
    mention: str | None
    path: list[str]
    excerpt: str
    figures: list[ChatFigureOut]


class ChatOut(CamelModel):
    answer: str
    covered: bool
    model: str | None
    sources: list[ChatSourceOut]


@lru_cache
def _logic() -> tuple[Any, Any]:
    """(chat_service, llm_client): the Lambda's own modules."""
    if not CHAT_DIR.is_dir():
        raise HTTPException(503, f"The chat logic was not found at {CHAT_DIR}. The infra/lambdas/chat folder must be next to server/.")
    if str(CHAT_DIR) not in sys.path:
        sys.path.insert(0, str(CHAT_DIR))
    import chat_service
    import llm_client

    return chat_service, llm_client


@lru_cache
def get_chat_model() -> Any:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(503, "Chat needs OPENAI_API_KEY in server/.env.")
    return _logic()[1].OpenAIChatModel(settings.openai_api_key, settings.chat_model)


def check_key(x_api_key: str | None = Header(None), settings: Settings = Depends(get_settings)) -> None:
    if settings.chat_api_key and not hmac.compare_digest((x_api_key or "").encode(), settings.chat_api_key.encode()):
        raise HTTPException(401, "Missing or wrong x-api-key.")


@router.post("", response_model=ChatOut, dependencies=[Depends(check_key)])
def chat(
    body: ChatIn,
    repo: Repository = Depends(get_repo),
    embedder: Embedder | None = Depends(get_embedder),
    llm: Any = Depends(get_chat_model),
    settings: Settings = Depends(get_settings),
):
    """Answers a question from the loaded manuals, citing numbered sources."""
    if embedder is None:
        raise HTTPException(503, "Chat searches by meaning too: set EMBEDDING_PROVIDER=openai in server/.env.")
    chat_service, llm_client = _logic()
    history = [{"role": t.role, "content": t.content[:MAX_HISTORY_CHARS]} for t in body.history[-MAX_HISTORY:]]
    try:
        res = chat_service.answer_question(repo, embedder, llm, settings, body.message, history, body.manual)
    except llm_client.LLMError as exc:
        log.error("Model call failed: %s", exc)
        raise HTTPException(502, "The answer service is unavailable. Try again.") from exc
    return ChatOut(
        answer=res.answer,
        covered=res.covered,
        model=res.model,
        sources=[
            ChatSourceOut(
                ref=s.ref, id=s.node_id, manual=s.manual, section=s.section, title=s.title, page_start=s.page_start,
                page_end=s.page_end, score=None if s.score is None else round(s.score, 4), relation=s.relation,
                matched_by=s.matched_by, via=s.via, mention=s.mention, path=s.path, excerpt=s.text.strip()[:EXCERPT_CHARS],
                figures=[ChatFigureOut(id=f.id, label=f.label, caption=f.caption, page=f.page, url=f.url) for f in s.figures],
            )
            for s in res.sources
        ],
    )
