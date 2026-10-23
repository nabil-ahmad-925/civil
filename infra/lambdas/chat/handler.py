"""POST /chat  {message, history?, manual?}  ->  {answer, covered, model, sources[]}

Retrieval (nearest sections + what they cross-reference) and one model call; the logic is in
chat_service.py (next to this file). Sources come numbered S1, S2, ... as cited in the answer text.
"""

from typing import Any

import lambda_common as common

EXCERPT_CHARS = 400


def _history(value: Any) -> list[dict[str, str]]:
    if value in (None, []):
        return []
    if not isinstance(value, list):
        raise common.HttpError(400, "history must be a list of {role, content}.")
    out = []
    for m in value[-common.MAX_HISTORY :]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant") or not isinstance(m.get("content"), str):
            raise common.HttpError(400, "Each history item needs role (user or assistant) and content (text).")
        out.append({"role": m["role"], "content": m["content"][: common.MAX_HISTORY_CHARS]})
    return out


def _llm(settings):  # noqa: ANN001
    if "llm" not in common.state:
        from llm_client import OpenAIChatModel

        common.state["llm"] = OpenAIChatModel(settings.openai_api_key, settings.chat_model)
    return common.state["llm"]


def _chat(event: dict) -> dict:
    data = common.json_body(event)
    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        raise common.HttpError(400, "message is required.")
    if len(message) > common.MAX_MESSAGE_CHARS:
        raise common.HttpError(400, f"message is longer than {common.MAX_MESSAGE_CHARS} characters.")
    manual, history = common.parse_manual(data.get("manual")), _history(data.get("history"))

    from chat_service import answer_question
    from llm_client import LLMError

    repo, embedder, settings = common.deps()
    try:
        res = answer_question(repo, embedder, _llm(settings), settings, message.strip(), history, manual)  # type: ignore[arg-type]
    except LLMError as exc:
        common.log.error("Model call failed: %s", exc)
        raise common.HttpError(502, "The answer service is unavailable. Try again.") from exc
    return {
        "answer": res.answer,
        "covered": res.covered,
        "model": res.model,
        "sources": [
            {
                "ref": s.ref, "id": s.node_id, "manual": s.manual, "section": s.section, "title": s.title,
                "pageStart": s.page_start, "pageEnd": s.page_end, "score": None if s.score is None else round(s.score, 4),
                "relation": s.relation, "matchedBy": s.matched_by, "via": s.via, "mention": s.mention, "path": s.path,
                "excerpt": s.text.strip()[:EXCERPT_CHARS],
                "figures": [{"id": f.id, "label": f.label, "caption": f.caption, "page": f.page, "url": f.url} for f in s.figures],
            }
            for s in res.sources
        ],
    }


def handler(event: dict, context: Any) -> dict:
    return common.handle(event, _chat)
