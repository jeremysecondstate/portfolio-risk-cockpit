"""Read-only Schwab Trader API smoke test.

This script performs the OAuth authorization-code exchange locally, then calls
safe read-only Schwab Trader API endpoints:

- GET /accounts/accountNumbers
- GET /accounts

It does not place, preview, replace, or cancel orders.

Usage:
    1. Copy .env.example to .env and fill in SCHWAB_CLIENT_ID,
       SCHWAB_CLIENT_SECRET, and SCHWAB_REDIRECT_URI.
    2. Run: python scripts/schwab_readonly_test.py
    3. Open the printed authorization URL.
    4. After Schwab redirects to your callback page, copy only the code value.
    5. Paste the code into this script.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
TRADER_BASE_URL = "https://api.schwabapi.com/trader/v1"


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def make_basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"Basic {encoded}"


def pretty_print(title: str, payload: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, sort_keys=True))


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    response = requests.request(method, url, timeout=30, **kwargs)
    print(f"{method.upper()} {url} -> {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        payload = response.text
    if response.status_code >= 400:
        pretty_print("Error response", payload)
        response.raise_for_status()
    return payload


def exchange_code_for_token(
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    authorization_code: str,
) -> dict[str, Any]:
    headers = {
        "Authorization": make_basic_auth_header(client_id, client_secret),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "redirect_uri": redirect_uri,
    }
    return request_json("POST", TOKEN_URL, headers=headers, data=data)


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")

    client_id = require_env("SCHWAB_CLIENT_ID")
    client_secret = require_env("SCHWAB_CLIENT_SECRET")
    redirect_uri = require_env("SCHWAB_REDIRECT_URI")

    state = secrets.token_urlsafe(24)
    auth_params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "readonly",
        "state": state,
    }
    authorization_url = f"{AUTH_URL}?{urlencode(auth_params)}"

    print("\nOpen this URL in your browser to connect Schwab:")
    print(authorization_url)
    print("\nAfter Schwab redirects to your callback page, copy the authorization code.")
    print("Do not paste your client secret, access token, or refresh token anywhere public.")

    authorization_code = input("\nPaste Schwab authorization code here: ").strip()
    if not authorization_code:
        print("No authorization code provided. Exiting.", file=sys.stderr)
        return 1

    tokens = exchange_code_for_token(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        authorization_code=authorization_code,
    )

    access_token = tokens.get("access_token")
    if not access_token:
        pretty_print("Token response without access_token", tokens)
        return 1

    print("\nToken exchange succeeded. Access token received.")
    if tokens.get("refresh_token"):
        print("Refresh token received. This script will not print or store it.")

    api_headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    account_numbers = request_json(
        "GET",
        f"{TRADER_BASE_URL}/accounts/accountNumbers",
        headers=api_headers,
    )
    pretty_print("GET /accounts/accountNumbers", account_numbers)

    accounts = request_json(
        "GET",
        f"{TRADER_BASE_URL}/accounts",
        headers=api_headers,
        params={"fields": "positions"},
    )
    pretty_print("GET /accounts?fields=positions", accounts)

    print("\nRead-only Schwab API smoke test complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
