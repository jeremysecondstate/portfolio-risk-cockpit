from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

from app.brokers.schwab.token_store import (
    access_token_is_fresh,
    cached_access_token_expires_at,
    clear_token_payload,
    load_token_payload,
    refresh_token_is_available,
    save_token_payload,
)

AUTH_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"
TRADER_BASE_URL = "https://api.schwabapi.com/trader/v1"
MARKETDATA_BASE_URL = "https://api.schwabapi.com/marketdata/v1"
TEMPORARY_AUTH_STATUS_CODES = {429, 500, 502, 503, 504}
REAUTH_AUTH_STATUS_CODES = {400, 401, 403}
REAUTH_ERROR_MARKERS = (
    "invalid_grant",
    "invalid_token",
    "invalid token",
    "token expired",
    "unauthorized",
    "forbidden",
)


class SchwabTokenError(RuntimeError):
    """Raised when Schwab rejects or cannot service an OAuth token request."""

    def __init__(self, label: str, response: requests.Response) -> None:
        self.status_code = response.status_code
        self.response_text = _trim_response_text(response)
        super().__init__(
            f"{label}: HTTP {response.status_code} {response.reason}. "
            f"Response body: {self.response_text}"
        )


def schwab_auth_error_requires_reauthorization(exc: Exception) -> bool:
    """Return True only when the saved OAuth grant appears rejected."""

    status_code = getattr(exc, "status_code", None)
    if status_code in TEMPORARY_AUTH_STATUS_CODES:
        return False

    response = getattr(exc, "response", None)
    if status_code is None and response is not None:
        status_code = getattr(response, "status_code", None)
        if status_code in TEMPORARY_AUTH_STATUS_CODES:
            return False

    text = str(exc).lower()
    if any(marker in text for marker in REAUTH_ERROR_MARKERS):
        return True

    return status_code in REAUTH_AUTH_STATUS_CODES


@dataclass(frozen=True)
class SchwabConfig:
    client_id: str
    client_secret: str
    redirect_uri: str

    @classmethod
    def from_env(cls) -> "SchwabConfig":
        load_dotenv()
        client_id = os.getenv("SCHWAB_CLIENT_ID", "").strip()
        client_secret = os.getenv("SCHWAB_CLIENT_SECRET", "").strip()
        redirect_uri = os.getenv("SCHWAB_REDIRECT_URI", "").strip()

        missing = [
            name
            for name, value in {
                "SCHWAB_CLIENT_ID": client_id,
                "SCHWAB_CLIENT_SECRET": client_secret,
                "SCHWAB_REDIRECT_URI": redirect_uri,
            }.items()
            if not value
        ]
        if missing:
            raise RuntimeError("Missing Schwab config: " + ", ".join(missing))

        return cls(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)


class SchwabSession:
    """Small Schwab API session helper for the desktop cockpit.

    This helper centralizes OAuth/token/account-hash plumbing so UI handlers do
    not each duplicate the same request code. It intentionally exposes only
    read/preview actions plus guarded live actions that the UI explicitly gates.
    """

    def __init__(self, config: SchwabConfig | None = None) -> None:
        self.config = config or SchwabConfig.from_env()
        self.access_token: str | None = None
        self.access_token_expires_at: datetime | None = None
        self.refresh_token: str | None = None
        self.account_hash: str | None = None
        self._hydrate_from_cache()

    def _hydrate_from_cache(self) -> None:
        cached_payload = load_token_payload()
        if not cached_payload:
            return

        if access_token_is_fresh(cached_payload):
            self.access_token = cached_payload.get("access_token")
            self.access_token_expires_at = cached_access_token_expires_at(cached_payload)

        if refresh_token_is_available(cached_payload):
            self.refresh_token = cached_payload.get("refresh_token")

    def has_cached_authorization(self) -> bool:
        """Return whether this session can likely authorize without browser login."""
        return bool(self.access_token or self.refresh_token)

    def ensure_access_token(self) -> None:
        """Use a cached refresh token when possible instead of prompting the user."""
        if self._access_token_is_current():
            return
        self.access_token = None
        self.access_token_expires_at = None
        if self.refresh_token:
            self.refresh_access_token()
            return
        raise RuntimeError("Schwab access token is not available yet.")

    def _access_token_is_current(self) -> bool:
        if not self.access_token:
            return False
        if self.access_token_expires_at is None:
            return self.refresh_token is None
        return self.access_token_expires_at > datetime.now(timezone.utc)

    def build_authorization_url(self) -> tuple[str, str]:
        state = secrets.token_urlsafe(24)
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": "readonly",
            "state": state,
        }
        return f"{AUTH_URL}?{urlencode(params)}", state

    def exchange_authorization_code(self, authorization_code: str) -> None:
        response = requests.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": authorization_code.strip(),
                "redirect_uri": self.config.redirect_uri,
            },
            auth=(self.config.client_id, self.config.client_secret),
            timeout=30,
        )
        if not response.ok:
            raise SchwabTokenError("Schwab authorization code exchange failed", response)
        payload = response.json()
        self._store_token_payload(payload, previous_refresh_token=self.refresh_token)

    def refresh_access_token(self) -> None:
        if not self.refresh_token:
            raise RuntimeError("Schwab refresh token is not available.")

        response = requests.post(
            TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            auth=(self.config.client_id, self.config.client_secret),
            timeout=30,
        )
        if not response.ok:
            raise SchwabTokenError("Schwab refresh token exchange failed", response)
        payload = response.json()
        self._store_token_payload(payload, previous_refresh_token=self.refresh_token)

    def _store_token_payload(self, payload: dict[str, Any], *, previous_refresh_token: str | None) -> None:
        cached_payload = save_token_payload(payload, previous_refresh_token=previous_refresh_token)
        self.access_token = payload["access_token"]
        self.access_token_expires_at = cached_access_token_expires_at(cached_payload)
        self.refresh_token = payload.get("refresh_token") or previous_refresh_token

    def clear_cached_authorization(self) -> None:
        self.access_token = None
        self.access_token_expires_at = None
        self.refresh_token = None
        self.account_hash = None
        clear_token_payload()

    def _headers(self) -> dict[str, str]:
        self.ensure_access_token()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    def _request(self, method: str, url: str, *, retry_on_unauthorized: bool = True, **kwargs: Any) -> requests.Response:
        response = requests.request(method, url, **kwargs)
        if response.status_code != 401 or not retry_on_unauthorized or not self.refresh_token:
            return response

        self.access_token = None
        self.access_token_expires_at = None
        self.refresh_access_token()

        headers = dict(kwargs.get("headers") or {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        kwargs["headers"] = headers
        return requests.request(method, url, **kwargs)

    def get_account_hash(self) -> str:
        if self.account_hash:
            return self.account_hash

        response = self._request(
            "GET",
            f"{TRADER_BASE_URL}/accounts/accountNumbers",
            headers=self._headers(),
            timeout=30,
        )
        response.raise_for_status()
        accounts = response.json()
        if not accounts:
            raise RuntimeError("No Schwab accounts returned.")

        account_hash = accounts[0].get("hashValue")
        if not account_hash:
            raise RuntimeError("Schwab account hashValue was missing.")

        self.account_hash = account_hash
        return account_hash

    def get_account(self, *, fields: str = "positions") -> tuple[int, Any]:
        """Fetch the selected Schwab account, including positions by default."""
        account_hash = self.get_account_hash()
        response = self._request(
            "GET",
            f"{TRADER_BASE_URL}/accounts/{account_hash}",
            headers=self._headers(),
            params={"fields": fields} if fields else None,
            timeout=30,
        )
        return response.status_code, response.json()

    def preview_order(self, order_payload: dict[str, Any]) -> tuple[int, Any]:
        account_hash = self.get_account_hash()
        response = self._request(
            "POST",
            f"{TRADER_BASE_URL}/accounts/{account_hash}/previewOrder",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=order_payload,
            timeout=30,
        )
        return response.status_code, response.json()

    def get_orders(self, *, from_entered_time: datetime, to_entered_time: datetime) -> tuple[int, Any]:
        account_hash = self.get_account_hash()
        response = self._request(
            "GET",
            f"{TRADER_BASE_URL}/accounts/{account_hash}/orders",
            headers=self._headers(),
            params={
                "fromEnteredTime": from_entered_time.astimezone(timezone.utc).isoformat(timespec="seconds"),
                "toEnteredTime": to_entered_time.astimezone(timezone.utc).isoformat(timespec="seconds"),
            },
            timeout=30,
        )
        return response.status_code, response.json()

    def get_quote(self, symbol: str) -> tuple[int, Any]:
        """Fetch a Schwab market-data quote for a stock, ETF, or option symbol."""
        cleaned_symbol = symbol.strip().upper()
        if not cleaned_symbol:
            raise ValueError("Symbol is required for Schwab quote lookup.")

        response = self._request(
            "GET",
            f"{MARKETDATA_BASE_URL}/quotes",
            headers=self._headers(),
            params={"symbols": cleaned_symbol, "fields": "quote"},
            timeout=30,
        )
        return response.status_code, response.json()

    def get_price_history(
        self,
        symbol: str,
        *,
        period_type: str = "day",
        period: int = 10,
        frequency_type: str = "minute",
        frequency: int = 5,
        need_extended_hours_data: bool = False,
    ) -> tuple[int, Any]:
        """Fetch Schwab market-data candles for a symbol."""
        cleaned_symbol = symbol.strip().upper()
        if not cleaned_symbol:
            raise ValueError("Symbol is required for price history.")

        response = self._request(
            "GET",
            f"{MARKETDATA_BASE_URL}/pricehistory",
            headers=self._headers(),
            params={
                "symbol": cleaned_symbol,
                "periodType": period_type,
                "period": period,
                "frequencyType": frequency_type,
                "frequency": frequency,
                "needExtendedHoursData": str(need_extended_hours_data).lower(),
            },
            timeout=30,
        )
        return response.status_code, response.json()

    def cancel_order(self, order_id: str) -> tuple[int, object]:
        """Cancel a Schwab order by ID.

        Schwab may return an empty body on successful cancel.
        """
        cleaned_order_id = str(order_id).strip()
        if not cleaned_order_id:
            raise ValueError("Order ID is required for cancel.")

        account_hash = self.get_account_hash()

        response = self._request(
            "DELETE",
            f"{TRADER_BASE_URL}/accounts/{account_hash}/orders/{cleaned_order_id}",
            headers=self._headers(),
            timeout=30,
        )

        if not response.text:
            payload = None
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text

        return response.status_code, payload

    def submit_live_order(self, order_payload: dict[str, Any]) -> tuple[int, object, str | None]:
        """Submit a live Schwab order.

        Schwab may return an empty body on success and put the order URL/ID
        in the Location header.
        """
        account_hash = self.get_account_hash()

        response = self._request(
            "POST",
            f"{TRADER_BASE_URL}/accounts/{account_hash}/orders",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=order_payload,
            timeout=30,
        )

        location = response.headers.get("Location")

        if not response.text:
            payload = None
        else:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text

        return response.status_code, payload, location


def _trim_response_text(response: requests.Response) -> str:
    response_text = response.text.strip()
    if len(response_text) > 800:
        response_text = response_text[:800] + "..."
    return response_text or "<empty response body>"
