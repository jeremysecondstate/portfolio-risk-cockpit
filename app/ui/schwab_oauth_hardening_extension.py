from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import requests

from app.brokers.schwab.session import SchwabSession, TOKEN_URL
from app.brokers.schwab.token_store import cached_access_token_expires_at, save_token_payload


def install_schwab_oauth_hardening_extension() -> None:
    """Make the manual Schwab callback-code handoff more tolerant and debuggable."""

    SchwabSession.exchange_authorization_code = _exchange_authorization_code_hardened  # type: ignore[method-assign]


def normalize_schwab_authorization_code(value: str) -> str:
    """Accept a raw Schwab code or a full callback URL and return only the code."""

    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError("Schwab authorization code is empty.")

    if "code=" in cleaned:
        parsed = urlparse(cleaned)
        query = parsed.query or cleaned.partition("?")[2]
        code_values = parse_qs(query, keep_blank_values=True).get("code")
        if code_values:
            cleaned = code_values[0]

    cleaned = unquote(cleaned).strip()
    cleaned = re.sub(r"\s+", "", cleaned)
    if not cleaned:
        raise ValueError("Schwab authorization code is empty after cleanup.")
    return cleaned


def _exchange_authorization_code_hardened(self: SchwabSession, authorization_code: str) -> None:
    code = normalize_schwab_authorization_code(authorization_code)
    response = requests.post(
        TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
        },
        auth=(self.config.client_id, self.config.client_secret),
        timeout=30,
    )
    _raise_for_schwab_token_error(response, "Schwab authorization code exchange failed")
    payload: dict[str, Any] = response.json()
    previous_refresh_token = self.refresh_token
    self.access_token = payload["access_token"]
    self.refresh_token = payload.get("refresh_token") or previous_refresh_token
    cached_payload = save_token_payload(payload, previous_refresh_token=previous_refresh_token)
    self.access_token_expires_at = cached_access_token_expires_at(cached_payload)


def _raise_for_schwab_token_error(response: requests.Response, label: str) -> None:
    if response.ok:
        return

    response_text = response.text.strip()
    if len(response_text) > 800:
        response_text = response_text[:800] + "..."
    if not response_text:
        response_text = "<empty response body>"

    raise RuntimeError(
        f"{label}: HTTP {response.status_code} {response.reason}. "
        f"Response body: {response_text}"
    )
