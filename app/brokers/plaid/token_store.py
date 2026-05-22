from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLAID_TOKEN_CACHE_PATH = PROJECT_ROOT / "data" / "secrets" / "plaid_access_token.json"
PLAID_PENDING_LINK_PATH = PROJECT_ROOT / "data" / "secrets" / "plaid_pending_link.json"


def load_plaid_token(path: str | Path = PLAID_TOKEN_CACHE_PATH) -> dict[str, Any] | None:
    token_path = Path(path)
    if not token_path.exists():
        return None

    try:
        with token_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def save_plaid_token(payload: dict[str, Any], path: str | Path = PLAID_TOKEN_CACHE_PATH) -> None:
    token_path = Path(path)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    cached_payload = {
        "access_token": payload.get("access_token"),
        "item_id": payload.get("item_id"),
        "request_id": payload.get("request_id"),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with token_path.open("w", encoding="utf-8") as handle:
        json.dump(cached_payload, handle, indent=2)


def clear_plaid_token(path: str | Path = PLAID_TOKEN_CACHE_PATH) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return


def save_pending_link(payload: dict[str, Any], path: str | Path = PLAID_PENDING_LINK_PATH) -> None:
    pending_path = Path(path)
    pending_path.parent.mkdir(parents=True, exist_ok=True)

    cached_payload = {
        "link_token": payload.get("link_token"),
        "hosted_link_url": payload.get("hosted_link_url"),
        "expiration": payload.get("expiration"),
        "request_id": payload.get("request_id"),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    with pending_path.open("w", encoding="utf-8") as handle:
        json.dump(cached_payload, handle, indent=2)


def load_pending_link(path: str | Path = PLAID_PENDING_LINK_PATH) -> dict[str, Any] | None:
    pending_path = Path(path)
    if not pending_path.exists():
        return None

    try:
        with pending_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def clear_pending_link(path: str | Path = PLAID_PENDING_LINK_PATH) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return
