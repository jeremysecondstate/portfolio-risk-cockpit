from __future__ import annotations

import os
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URLS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidClient:
    """Requests-based Plaid client for read-only Investments data."""

    def __init__(self) -> None:
        self.client_id = os.getenv("PLAID_CLIENT_ID", "").strip()
        self.api_secret = os.getenv("PLAID_" + "SECRET", "").strip()
        self.env = os.getenv("PLAID_ENV", "sandbox").strip().lower() or "sandbox"
        self.products = [item.strip() for item in os.getenv("PLAID_PRODUCTS", "investments").split(",") if item.strip()] or ["investments"]
        self.country_codes = [item.strip() for item in os.getenv("PLAID_COUNTRY_CODES", "US").split(",") if item.strip()] or ["US"]

        if not self.client_id or not self.api_secret:
            raise RuntimeError("Missing Plaid client id or secret in .env")
        if self.env not in BASE_URLS:
            raise RuntimeError("PLAID_ENV must be sandbox, development, or production")

        self.base_url = BASE_URLS[self.env]

    def create_link_token(self, *, user_id: str = "portfolio-risk-cockpit-user") -> dict[str, Any]:
        return self._post("/link/token/create", {
            "client_id": self.client_id,
            "secret": self.api_secret,
            "client_name": "Portfolio Risk Cockpit",
            "user": {"client_user_id": user_id},
            "products": self.products,
            "country_codes": self.country_codes,
            "language": "en",
        })

    def create_hosted_link_token(self, *, user_id: str = "portfolio-risk-cockpit-user") -> dict[str, Any]:
        return self._post("/link/token/create", {
            "client_id": self.client_id,
            "secret": self.api_secret,
            "client_name": "Portfolio Risk Cockpit",
            "user": {"client_user_id": user_id},
            "products": self.products,
            "country_codes": self.country_codes,
            "language": "en",
            "hosted_link": {"url_lifetime_seconds": 1800},
        })

    def get_link_token(self, link_token: str) -> dict[str, Any]:
        return self._post("/link/token/get", {
            "client_id": self.client_id,
            "secret": self.api_secret,
            "link_token": link_token,
        })

    def create_sandbox_public_token(self, *, institution_id: str = "ins_109508") -> dict[str, Any]:
        if self.env != "sandbox":
            raise RuntimeError("Sandbox public token creation requires PLAID_ENV=sandbox")
        return self._post("/sandbox/public_token/create", {
            "client_id": self.client_id,
            "secret": self.api_secret,
            "institution_id": institution_id,
            "initial_products": self.products,
        })

    def exchange_public_token(self, public_token: str) -> dict[str, Any]:
        return self._post("/item/public_token/exchange", {
            "client_id": self.client_id,
            "secret": self.api_secret,
            "public_token": public_token.strip(),
        })

    def get_investment_holdings(self, token: str) -> dict[str, Any]:
        return self._post("/investments/holdings/get", {
            "client_id": self.client_id,
            "secret": self.api_secret,
            "access_token": token,
        })

    def _post(self, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = requests.post(f"{self.base_url}{endpoint}", json=payload, timeout=30)
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Plaid {endpoint} returned non-JSON HTTP {response.status_code}") from exc

        if response.status_code >= 400:
            code = body.get("error_code")
            message = body.get("error_message") or body
            raise RuntimeError(f"Plaid {endpoint} failed HTTP {response.status_code}: {code} {message}")

        return body
