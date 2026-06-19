from __future__ import annotations

import os
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch

from app.data.earnings_calendar import (
    MISSING_API_KEY_MESSAGE,
    AlphaVantageEarningsCalendarClient,
    CompositeUpcomingEarningsProvider,
    FmpEarningsCalendarClient,
    UpcomingEarningsRecord,
    parse_alpha_vantage_earnings_calendar_csv,
    parse_fmp_earnings_calendar_rows,
    validate_horizon,
)


ALPHA_VANTAGE_CSV = """symbol,name,reportDate,fiscalDateEnding,estimate,currency
MSFT,Microsoft Corp,2026-07-21,2026-06-30,3.21,USD
TSM,Taiwan Semiconductor Manufacturing,2026-07-17,2026-06-30,,USD
"""


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class _FakeSession:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _FakeResponse:
        self.calls.append(url)
        return _FakeResponse(self.text)


class _FakeJsonResponse:
    def __init__(self, payload, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeJsonSession:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> _FakeJsonResponse:
        self.calls.append({"url": url, **kwargs})
        return _FakeJsonResponse(self.payload)


class _FakeUpcomingProvider:
    def __init__(self, rows: list[UpcomingEarningsRecord], last_status: str = "fake status") -> None:
        self.rows = rows
        self.last_status = last_status

    def upcoming_earnings(self, **_kwargs: object) -> list[UpcomingEarningsRecord]:
        return list(self.rows)


class _FailingUpcomingProvider:
    last_status = "failed"

    def __init__(self, message: str) -> None:
        self.message = message

    def upcoming_earnings(self, **_kwargs: object) -> list[UpcomingEarningsRecord]:
        raise RuntimeError(self.message)


class EarningsCalendarTests(unittest.TestCase):
    def test_alpha_vantage_csv_rows_parse_into_records(self) -> None:
        records = parse_alpha_vantage_earnings_calendar_csv(ALPHA_VANTAGE_CSV, source_url="https://example.test/source")

        self.assertEqual([record.symbol for record in records], ["MSFT", "TSM"])
        self.assertEqual(records[0].company_name, "Microsoft Corp")
        self.assertEqual(records[0].report_date, "2026-07-21")
        self.assertEqual(records[0].fiscal_date_ending, "2026-06-30")
        self.assertEqual(records[0].estimate, 3.21)
        self.assertEqual(records[0].currency, "USD")
        self.assertEqual(records[0].source, "Alpha Vantage")
        self.assertEqual(records[0].source_url, "https://example.test/source")
        self.assertIsNone(records[1].estimate)

    def test_fmp_json_rows_parse_into_records(self) -> None:
        records = parse_fmp_earnings_calendar_rows(
            [{"symbol": "MSFT", "companyName": "Microsoft Corp", "date": "2026-07-21T00:00:00", "epsEstimated": "3.21", "currency": "USD"}],
            source_url="https://example.test/fmp",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].symbol, "MSFT")
        self.assertEqual(records[0].report_date, "2026-07-21")
        self.assertEqual(records[0].estimate, 3.21)
        self.assertEqual(records[0].source, "FMP earnings calendar")

    def test_missing_api_key_returns_empty_result_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(os.environ, {"ALPHA_VANTAGE_API_KEY": ""}):
                client = AlphaVantageEarningsCalendarClient(api_key="", cache_dir=tmp_dir)

                self.assertEqual(client.upcoming_earnings(horizon="3month"), [])
                self.assertEqual(client.last_status, MISSING_API_KEY_MESSAGE)

    def test_horizon_validation_accepts_only_supported_values(self) -> None:
        self.assertEqual(validate_horizon("6month"), "6month")

        with self.assertRaises(ValueError):
            validate_horizon("1month")

    def test_provider_uses_ttl_cache_after_first_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = _FakeSession(ALPHA_VANTAGE_CSV)
            client = AlphaVantageEarningsCalendarClient(
                api_key="demo",
                cache_dir=tmp_dir,
                session=session,
                cache_ttl=timedelta(hours=1),
            )

            first = client.upcoming_earnings(horizon="3month")
            second = client.upcoming_earnings(horizon="3month")

        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertEqual(len(session.calls), 1)
        self.assertIn("cache", client.last_status)

    def test_provider_filters_multiple_symbols_client_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = _FakeSession(ALPHA_VANTAGE_CSV)
            client = AlphaVantageEarningsCalendarClient(api_key="demo", cache_dir=tmp_dir, session=session)

            records = client.upcoming_earnings(horizon="3month", symbols=["MSFT", "AAPL"])

        self.assertEqual([record.symbol for record in records], ["MSFT"])

    def test_fmp_provider_uses_stable_earnings_calendar_and_cache(self) -> None:
        payload = [{"symbol": "MSFT", "companyName": "Microsoft Corp", "date": "2026-07-21", "epsEstimated": 3.21, "currency": "USD"}]
        with tempfile.TemporaryDirectory() as tmp_dir:
            session = _FakeJsonSession(payload)
            client = FmpEarningsCalendarClient(api_key="fmp-secret-key", cache_dir=tmp_dir, session=session)

            first = client.upcoming_earnings(horizon="3month", symbols=["MSFT"])
            second = client.upcoming_earnings(horizon="3month", symbols=["MSFT"])

        self.assertEqual([record.symbol for record in first], ["MSFT"])
        self.assertEqual([record.symbol for record in second], ["MSFT"])
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(str(session.calls[0]["url"]).endswith("/earnings-calendar"))
        self.assertEqual(session.calls[0]["headers"]["apikey"], "fmp-secret-key")
        self.assertIn("cache", client.last_status)

    def test_composite_provider_falls_back_to_fmp_and_redacts_warning(self) -> None:
        fmp_row = UpcomingEarningsRecord("MSFT", "Microsoft Corp", "2026-07-21", None, 3.21, "USD", "FMP earnings calendar", "https://example.test/fmp")
        with patch.dict(os.environ, {"FMP_API_KEY": "fmp-secret-key"}):
            provider = CompositeUpcomingEarningsProvider(
                (
                    _FailingUpcomingProvider("Alpha failed with fmp-secret-key and apikey=fmp-secret-key"),
                    _FakeUpcomingProvider([fmp_row], "Loaded 1 FMP row."),
                )
            )

            records = provider.upcoming_earnings(horizon="3month", symbols=["MSFT"])

        self.assertEqual(records, [fmp_row])
        self.assertNotIn("fmp-secret-key", provider.last_status)
        self.assertIn("[REDACTED]", provider.last_status)


if __name__ == "__main__":
    unittest.main()
