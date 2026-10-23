"""GET /health: no key, no dependencies."""

import json
from typing import Any


def handler(event: dict, context: Any) -> dict:
    return {"statusCode": 200, "headers": {"content-type": "application/json"}, "body": json.dumps({"status": "ok"})}
