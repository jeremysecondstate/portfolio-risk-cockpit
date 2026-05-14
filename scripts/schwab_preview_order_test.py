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


def basic_auth(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def request_json(method: str, url: str, **kwargs: Any) -> Any:
    response = requests.request(method, url, timeout=30, **kwargs)
    print(f"{method.upper()} {url} -> {response.status_code}")

    try:
        payload = response.json()
    except ValueError:
        payload = response.text

    if response.status_code >= 400:
        print(json.dumps(payload, indent=2) if isinstance(payload, dict) else payload)
        response.raise_for_status()

    return payload


def exchange_code_for_token(
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    authorization_code: str,
) -> dict[str, Any]:
    headers = {
        "Authorization": basic_auth(client_id, client_secret),
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {
        "grant_type": "authorization_code",
        "code": authorization_code,
        "redirect_uri": redirect_uri,
    }
    return request_json("POST", TOKEN_URL, headers=headers, data=data)


def build_limit_order(symbol: str, side: str, quantity: float, limit_price: float) -> dict[str, Any]:
    return {
        "orderType": "LIMIT",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "price": f"{limit_price:.2f}",
        "orderLegCollection": [
            {
                "instruction": side,
                "quantity": quantity,
                "instrument": {
                    "symbol": symbol,
                    "assetType": "EQUITY",
                },
            }
        ],
    }


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

    auth_url = f"{AUTH_URL}?{urlencode(auth_params)}"

    print("\nThis script only calls Schwab previewOrder.")
    print("It does NOT place a live order.\n")
    print("Open this URL:")
    print(auth_url)

    code = input("\nPaste Schwab authorization code here: ").strip()
    if not code:
        print("No code provided.")
        return 1

    tokens = exchange_code_for_token(client_id, client_secret, redirect_uri, code)
    access_token = tokens.get("access_token")

    if not access_token:
        print("No access token returned.")
        return 1

    print("\nToken exchange succeeded.")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }

    account_numbers = request_json(
        "GET",
        f"{TRADER_BASE_URL}/accounts/accountNumbers",
        headers=headers,
    )

    if not account_numbers:
        raise RuntimeError("No Schwab accounts returned.")

    account_hash = account_numbers[0]["hashValue"]

    print("\nBuild a preview-only equity LIMIT order.")
    print("Nothing will be placed.")

    symbol = input("Symbol: ").strip().upper()
    side = input("Side [BUY/SELL]: ").strip().upper()
    quantity = float(input("Quantity: ").strip())
    limit_price = float(input("Limit price: ").strip())

    if side not in {"BUY", "SELL"}:
        raise ValueError("Side must be BUY or SELL.")
    if not symbol:
        raise ValueError("Symbol is required.")
    if quantity <= 0:
        raise ValueError("Quantity must be positive.")
    if limit_price <= 0:
        raise ValueError("Limit price must be positive.")

    order = build_limit_order(symbol, side, quantity, limit_price)

    print("\nOrder JSON that will be previewed:")
    print(json.dumps(order, indent=2))

    confirm = input("\nType PREVIEW to call Schwab previewOrder: ").strip().upper()
    if confirm != "PREVIEW":
        print("Cancelled.")
        return 1

    preview_headers = {
        **headers,
        "Content-Type": "application/json",
    }

    preview = request_json(
        "POST",
        f"{TRADER_BASE_URL}/accounts/{account_hash}/previewOrder",
        headers=preview_headers,
        json=order,
    )

    print("\n=== Schwab previewOrder response ===")
    print(json.dumps(preview, indent=2))

    print("\nDone. No live order was placed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())