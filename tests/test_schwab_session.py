from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.brokers.schwab.session import (
    SchwabConfig,
    SchwabSession,
    SchwabTokenError,
    schwab_auth_error_requires_reauthorization,
)


class _TokenResponse:
    ok = True
    status_code = 200
    reason = "OK"
    text = ""

    def json(self) -> dict[str, object]:
        return {"access_token": "new-access-token", "expires_in": 1800}


class _ErrorResponse:
    ok = False

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.reason = "ERROR"
        self.text = text


class _OrderResponse:
    status_code = 201
    text = ""
    headers = {"Location": "https://api.schwabapi.com/trader/v1/accounts/acct/orders/67890"}

    def json(self) -> dict[str, object]:
        return {}


class SchwabSessionTests(unittest.TestCase):
    def test_expired_in_memory_access_token_refreshes_without_browser_reauth(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(minutes=29)
        cached_payload = {"access_token_expires_at": future.isoformat()}

        with (
            patch("app.brokers.schwab.session.load_token_payload", return_value=None),
            patch("app.brokers.schwab.session.requests.post", return_value=_TokenResponse()) as post,
            patch("app.brokers.schwab.session.save_token_payload", return_value=cached_payload),
        ):
            session = SchwabSession(SchwabConfig("client-id", "client-secret", "https://example.test/callback"))
            session.access_token = "expired-access-token"
            session.access_token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
            session.refresh_token = "saved-refresh-token"

            session.ensure_access_token()

        self.assertEqual(session.access_token, "new-access-token")
        self.assertEqual(session.refresh_token, "saved-refresh-token")
        self.assertEqual(post.call_count, 1)

    def test_temporary_token_endpoint_failure_does_not_require_reauthorization(self) -> None:
        exc = SchwabTokenError("Schwab refresh token exchange failed", _ErrorResponse(500, "server error"))

        self.assertFalse(schwab_auth_error_requires_reauthorization(exc))

    def test_invalid_grant_requires_reauthorization(self) -> None:
        exc = SchwabTokenError("Schwab refresh token exchange failed", _ErrorResponse(400, '{"error":"invalid_grant"}'))

        self.assertTrue(schwab_auth_error_requires_reauthorization(exc))

    def test_replace_order_uses_trader_api_put_order_endpoint(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(minutes=20)
        with (
            patch("app.brokers.schwab.session.load_token_payload", return_value=None),
            patch("app.brokers.schwab.session.requests.request", return_value=_OrderResponse()) as request,
        ):
            session = SchwabSession(SchwabConfig("client-id", "client-secret", "https://example.test/callback"))
            session.access_token = "access-token"
            session.access_token_expires_at = future
            session.account_hash = "acct"

            status_code, payload, location = session.replace_order("12345", {"orderType": "LIMIT"})

        self.assertEqual(status_code, 201)
        self.assertIsNone(payload)
        self.assertEqual(location, "https://api.schwabapi.com/trader/v1/accounts/acct/orders/67890")
        self.assertEqual(request.call_args.args[0], "PUT")
        self.assertEqual(
            request.call_args.args[1],
            "https://api.schwabapi.com/trader/v1/accounts/acct/orders/12345",
        )
        self.assertEqual(request.call_args.kwargs["json"], {"orderType": "LIMIT"})


if __name__ == "__main__":
    unittest.main()
