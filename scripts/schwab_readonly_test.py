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

By default, printed output is redacted so account numbers and hash values are
not accidentally shared. Set SCHWAB_DEBUG_FULL_JSON=true in .env only when you
need the complete raw response locally.

Set SCHWAB_WRITE_SNAPSHOT=true in .env to write a local dashboard snapshot to
DATA/portfolio_snapshot.csv from the read-only account response.
"""

from __future__ import annotations

import base64
import copy
import csv
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
SENSITIVE_KEYS = {
    "accountNumber",
    "hashValue",
    "access_token",
    "refresh_token",
    "id_token",
    "token",
}


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "y"}


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def make_basic_auth_header(client_id: str, client_secret: str) -> str:
    raw = f"{client_id}:{client_secret}".encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"Basic {encoded}"


def mask_value(value: Any) -> Any:
    text = str(value)
    if len(text) <= 4:
        return "****"
    return f"***{text[-4:]}"


def redact(payload: Any) -> Any:
    data = copy.deepcopy(payload)
    if isinstance(data, dict):
        return {
            key: mask_value(value) if key in SENSITIVE_KEYS else redact(value)
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact(item) for item in data]
    return data


def pretty_print(title: str, payload: Any, *, full_json: bool = False) -> None:
    print(f"\n=== {title} ===")
    safe_payload = payload if full_json else redact(payload)
    print(json.dumps(safe_payload, indent=2, sort_keys=True))


def print_account_summary(accounts: Any) -> None:
    print("\n=== Account summary ===")
    if not isinstance(accounts, list):
        print("Unexpected accounts payload shape; see JSON output above.")
        return

    for idx, account_wrapper in enumerate(accounts, start=1):
        securities_account = account_wrapper.get("securitiesAccount", {})
        balances = securities_account.get("currentBalances", {})
        positions = securities_account.get("positions", []) or []
        account_type = securities_account.get("type", "UNKNOWN")
        print(f"Account {idx}: type={account_type}")
        print(f"  liquidationValue: {balances.get('liquidationValue')}")
        print(f"  cashBalance: {balances.get('cashBalance')}")
        print(f"  availableFunds: {balances.get('availableFunds')}")
        print("  positions:")
        for position in positions:
            instrument = position.get("instrument", {})
            symbol = instrument.get("symbol", "UNKNOWN")
            quantity = position.get("longQuantity", position.get("shortQuantity"))
            market_value = position.get("marketValue")
            print(f"    - {symbol}: quantity={quantity}, marketValue={market_value}")


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


def write_dashboard_snapshot(accounts: Any, repo_root: Path) -> Path:
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("Cannot write snapshot: accounts response was empty or unexpected.")

    securities_account = accounts[0].get("securitiesAccount", {})
    balances = securities_account.get("currentBalances", {})
    positions = securities_account.get("positions", []) or []
    cash = float(balances.get("cashBalance") or 0)

    output_path = repo_root / "data" / "portfolio_snapshot.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["type", "symbol", "quantity", "average_cost", "last_price", "notes"])
        writer.writerow(["cash", "CASH", "", "", f"{cash:.2f}", "Schwab read-only cash balance"])

        for position in positions:
            instrument = position.get("instrument", {})
            symbol = str(instrument.get("symbol") or "").strip().upper()
            quantity = float(position.get("longQuantity") or 0)
            if not symbol or quantity <= 0:
                continue

            average_cost = float(position.get("averagePrice") or position.get("averageLongPrice") or 0)
            market_value = float(position.get("marketValue") or 0)
            last_price = market_value / quantity if quantity else 0
            writer.writerow(
                [
                    "position",
                    symbol,
                    f"{quantity:g}",
                    f"{average_cost:.4f}",
                    f"{last_price:.4f}",
                    "Schwab read-only import",
                ]
            )

    return output_path


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")

    client_id = require_env("SCHWAB_CLIENT_ID")
    client_secret = require_env("SCHWAB_CLIENT_SECRET")
    redirect_uri = require_env("SCHWAB_REDIRECT_URI")
    full_json = env_flag("SCHWAB_DEBUG_FULL_JSON")
    write_snapshot = env_flag("SCHWAB_WRITE_SNAPSHOT")

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
    print("Do not paste your client secret, access token, refresh token, authorization code, or account number anywhere public.")

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
        pretty_print("Token response without access_token", tokens, full_json=full_json)
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
    pretty_print("GET /accounts/accountNumbers", account_numbers, full_json=full_json)

    accounts = request_json(
        "GET",
        f"{TRADER_BASE_URL}/accounts",
        headers=api_headers,
        params={"fields": "positions"},
    )
    pretty_print("GET /accounts?fields=positions", accounts, full_json=full_json)
    print_account_summary(accounts)

    if write_snapshot:
        output_path = write_dashboard_snapshot(accounts, repo_root)
        print(f"\nWrote local dashboard snapshot: {output_path}")
        print("Open the cockpit and click Reload Snapshot to view the Schwab read-only import.")
    else:
        print("\nSnapshot writing skipped. Set SCHWAB_WRITE_SNAPSHOT=true in .env to enable it.")

    print("\nRead-only Schwab API smoke test complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
