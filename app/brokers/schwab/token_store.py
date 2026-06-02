from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TOKEN_CACHE_PATH = PROJECT_ROOT / "data" / "schwab_tokens.json"
ACCESS_TOKEN_EXPIRY_SAFETY_SECONDS = 60


def load_token_payload(path: str | Path = TOKEN_CACHE_PATH) -> dict[str, Any] | None:
    """Load cached Schwab OAuth tokens from local app data."""
    token_path = Path(path)
    if not token_path.exists():
        return None

    try:
        with token_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    return payload if isinstance(payload, dict) else None


def save_token_payload(
    payload: dict[str, Any],
    *,
    previous_refresh_token: str | None = None,
    path: str | Path = TOKEN_CACHE_PATH,
) -> dict[str, Any]:
    """Persist the token fields needed for silent Schwab re-authorization.

    Schwab may return a refresh token on the initial code exchange and may or may
    not rotate it during refresh. If a refresh response omits `refresh_token`, we
    retain the previous one.
    """
    token_path = Path(path)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    expires_in = _int_value(payload.get("expires_in"), default=1800)
    access_token_expires_at = now + timedelta(seconds=max(expires_in - ACCESS_TOKEN_EXPIRY_SAFETY_SECONDS, 1))

    refresh_token = str(payload.get("refresh_token") or previous_refresh_token or "").strip()
    cached_payload = {
        "access_token": payload.get("access_token"),
        "refresh_token": refresh_token,
        "token_type": payload.get("token_type"),
        "scope": payload.get("scope"),
        "access_token_expires_at": access_token_expires_at.isoformat(),
        "saved_at": now.isoformat(),
    }

    refresh_expires_in = _optional_int_value(payload.get("refresh_token_expires_in"))
    if refresh_expires_in is not None:
        cached_payload["refresh_token_expires_at"] = (now + timedelta(seconds=refresh_expires_in)).isoformat()

    with token_path.open("w", encoding="utf-8") as handle:
        json.dump(cached_payload, handle, indent=2)

    return cached_payload


def clear_token_payload(path: str | Path = TOKEN_CACHE_PATH) -> None:
    """Remove cached Schwab OAuth tokens."""
    token_path = Path(path)
    try:
        token_path.unlink()
    except FileNotFoundError:
        return


def access_token_is_fresh(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False

    access_token = payload.get("access_token")
    expires_at = _parse_datetime(payload.get("access_token_expires_at"))
    if not access_token or expires_at is None:
        return False

    return expires_at > datetime.now(timezone.utc)


def cached_access_token_expires_at(payload: dict[str, Any] | None) -> datetime | None:
    if not payload:
        return None
    return _parse_datetime(payload.get("access_token_expires_at"))


def refresh_token_is_available(payload: dict[str, Any] | None) -> bool:
    if not payload or not payload.get("refresh_token"):
        return False

    expires_at = _parse_datetime(payload.get("refresh_token_expires_at"))
    if expires_at is None:
        return True

    return expires_at > datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _int_value(value: Any, *, default: int) -> int:
    parsed = _optional_int_value(value)
    return parsed if parsed is not None else default


def _optional_int_value(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
