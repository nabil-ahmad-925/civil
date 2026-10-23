#!/usr/bin/env python3
"""Copies OPENAI_API_KEY, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY from server/.env into the Secrets Manager secret,
keeping the CHAT_API_KEY that CDK generated. Run after `cdk deploy`, with your AWS credentials active.

    python scripts/set_secrets.py            # prints which keys it set, never their values
    python scripts/set_secrets.py --show-api-key   # also prints CHAT_API_KEY (for the client's .env.local)
"""

import json
import sys
from pathlib import Path

import boto3

SECRET_NAME = "civil-eng/chat"
KEYS = ("OPENAI_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
ENV_FILE = Path(__file__).resolve().parents[2] / "server" / ".env"


def read_env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> None:
    env = read_env(ENV_FILE)
    missing = [k for k in KEYS if not env.get(k)]
    if missing:
        sys.exit(f"{ENV_FILE} has no value for: {', '.join(missing)}")
    sm = boto3.client("secretsmanager")
    current = json.loads(sm.get_secret_value(SecretId=SECRET_NAME)["SecretString"])
    current.update({k: env[k] for k in KEYS})
    sm.put_secret_value(SecretId=SECRET_NAME, SecretString=json.dumps(current))
    print(f"Updated {SECRET_NAME}: {', '.join(KEYS)} (CHAT_API_KEY kept).")
    if "--show-api-key" in sys.argv:
        print(f"CHAT_API_KEY={current['CHAT_API_KEY']}")


if __name__ == "__main__":
    main()
