"""Helpers for the Lambda handlers: secrets, credentials check, request parsing, responses.

The build copies this file next to a handler.py that needs it, so the handler does `import lambda_common as common`.
Every route except /health needs the header `x-api-key`. The key and the OpenAI / Supabase credentials live in one
Secrets Manager secret (SECRET_ARN), read once per container. The browser must not hold the key: the Next.js server
calls this API.
"""

import base64
import hmac
import json
import logging
import os
from typing import Any

log = logging.getLogger()
log.setLevel(logging.INFO)

MAX_MESSAGE_CHARS = 2000
MAX_HISTORY = 20
MAX_HISTORY_CHARS = 4000
MANUALS = ("PPM", "HDM", "CADD")
SECRET_KEYS = ("OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "CHAT_API_KEY")
OPTIONAL_SECRET_KEYS = ("PARADEDB_URL",)  # only needed when KEYWORD_BACKEND=paradedb

state: dict[str, Any] = {}


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def secrets() -> dict[str, str]:
    if "secrets" not in state:
        import boto3  # in the Lambda runtime already

        raw = boto3.client("secretsmanager").get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"]
        state["secrets"] = {k: v for k, v in json.loads(raw).items() if k in (*SECRET_KEYS, *OPTIONAL_SECRET_KEYS) and v}
    return state["secrets"]


def deps():
    """(repository, embedder, settings), built once per container."""
    if "deps" not in state:
        found = secrets()
        missing = [k for k in SECRET_KEYS if k not in found]
        if missing:
            raise HttpError(500, f"The secret is missing: {', '.join(missing)}. Run infra/scripts/set_secrets.py.")
        os.environ.update({k: v for k, v in found.items() if k != "CHAT_API_KEY"})
        from app.config import get_settings
        from app.ingestion.embeddings import OpenAIEmbedder
        from app.repository.supabase_repo import SupabaseRepository

        s = get_settings()
        if s.keyword_backend == "paradedb" and not s.paradedb_url:
            raise HttpError(500, "KEYWORD_BACKEND is paradedb but the secret has no PARADEDB_URL. Run infra/scripts/set_secrets.py.")
        state["deps"] = (
            SupabaseRepository(s.supabase_url, s.supabase_service_role_key, s.storage_bucket, s.keyword_backend, s.paradedb_url),
            OpenAIEmbedder(s.openai_api_key, s.embedding_model, s.embedding_dim),
            s,
        )
    return state["deps"]


def check_key(event: dict) -> None:
    expected = secrets().get("CHAT_API_KEY", "")
    given = (event.get("headers") or {}).get("x-api-key", "")
    if not expected:
        raise HttpError(500, "The secret has no CHAT_API_KEY.")
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HttpError(401, "Missing or wrong x-api-key.")


def respond(status: int, body: dict) -> dict:
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


def json_body(event: dict) -> dict:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise HttpError(400, "The body must be JSON.") from exc
    if not isinstance(data, dict):
        raise HttpError(400, "The body must be a JSON object.")
    return data


def parse_manual(value: Any) -> str | None:
    if value in (None, ""):
        return None
    # PPM, HDM, CADD, or the name typed for a document without headings.
    if not isinstance(value, str) or not value.strip() or len(value) > 80:
        raise HttpError(400, "manual must be a name of up to 80 characters.")
    return " ".join(value.split())


def node_json(n) -> dict:  # noqa: ANN001
    return {
        "id": n.id, "documentId": n.document_id, "manual": n.manual, "section": n.section, "title": n.title,
        "pageStart": n.page_start, "pageEnd": n.page_end,
    }


def handle(event: dict, action) -> dict:  # noqa: ANN001
    """Checks the key, runs `action(event)` and turns its result or error into an HTTP response."""
    try:
        check_key(event)
        return respond(200, action(event))
    except HttpError as exc:
        return respond(exc.status, {"error": exc.message})
    except Exception:  # noqa: BLE001
        log.exception("Unhandled error")
        return respond(500, {"error": "Something went wrong."})
