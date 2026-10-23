"""The chat model. One call: system prompt + conversation in, text out."""

from typing import Protocol

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    """The model could not produce an answer (API error, empty answer)."""


class ChatModel(Protocol):
    def complete(self, system: str, messages: list[dict[str, str]]) -> str: ...


class OpenAIChatModel:
    def __init__(self, api_key: str, model: str, client: httpx.Client | None = None, max_retries: int = 2) -> None:
        self.model = model
        self.max_retries = max_retries
        self.client = client or httpx.Client(
            base_url="https://api.openai.com/v1", headers={"Authorization": f"Bearer {api_key}"}, timeout=45
        )

    def complete(self, system: str, messages: list[dict[str, str]]) -> str:
        body: dict = {"model": self.model, "messages": [{"role": "system", "content": system}, *messages]}
        if self.model.startswith(("gpt-4", "gpt-3")):  # newer reasoning models reject a temperature
            body["temperature"] = 0.1
        for attempt in range(self.max_retries + 1):
            last = attempt == self.max_retries
            try:
                res = self.client.post("/chat/completions", json=body)
            except httpx.TransportError as exc:
                if last:
                    raise LLMError(f"Could not reach OpenAI: {exc}") from exc
                continue
            if res.status_code in RETRY_STATUS and not last:
                continue
            if res.status_code != 200:
                raise LLMError(f"OpenAI returned {res.status_code}: {res.text[:300]}")
            text = (res.json()["choices"][0]["message"].get("content") or "").strip()
            if not text:
                raise LLMError("OpenAI returned an empty answer.")
            return text
        raise LLMError("OpenAI request failed")  # unreachable
