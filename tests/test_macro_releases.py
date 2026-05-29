from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.macro.analysis import format_macro_report, market_impact_lines
from app.macro.models import MacroRelease, MacroSnapshot, MacroSourceStatus
from app.macro.sources import (
    collect_macro_snapshot,
    parse_bea_response,
    parse_bls_response,
    parse_treasury_rates_response,
    save_macro_cache,
)
from app.ui.unified_trade_thesis_next_checks_extension import _macro_report_or_error


FETCHED_AT = "2026-05-29T12:00:00+00:00"


class MacroReleaseTests(unittest.TestCase):
    def test_parses_bls_response(self) -> None:
        payload = {
            "Results": {
                "series": [
                    {
                        "seriesID": "CUUR0000SA0",
                        "data": [
                            {"year": "2026", "periodName": "April", "value": "321.500", "calculations": {"pct_changes": {"1": "0.3"}}},
                            {"year": "2026", "periodName": "March", "value": "320.500"},
                        ],
                    }
                ]
            }
        }

        releases = parse_bls_response(payload, fetched_at=FETCHED_AT)

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0].source, "BLS.gov")
        self.assertEqual(releases[0].metric, "CPI")
        self.assertEqual(releases[0].period, "2026 April")
        self.assertAlmostEqual(releases[0].actual or 0, 321.5)
        self.assertAlmostEqual(releases[0].prior or 0, 320.5)

    def test_parses_bea_response(self) -> None:
        payload = {
            "BEAAPI": {
                "Results": {
                    "Data": [
                        {"TimePeriod": "2026Q1", "DataValue": "1.6", "LineDescription": "Gross domestic product"},
                        {"TimePeriod": "2025Q4", "DataValue": "3.4", "LineDescription": "Gross domestic product"},
                    ]
                }
            }
        }

        releases = parse_bea_response(payload, metric="Real GDP", category="growth", unit="annualized percent", fetched_at=FETCHED_AT)

        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0].source, "BEA.gov")
        self.assertEqual(releases[0].metric, "Real GDP")
        self.assertAlmostEqual(releases[0].actual or 0, 1.6)
        self.assertAlmostEqual(releases[0].prior or 0, 3.4)

    def test_parses_treasury_rates_response(self) -> None:
        payload = {
            "data": [
                {"record_date": "2026-05-28", "security_desc_2_yr": "4.12", "security_desc_10_yr": "4.48", "security_desc_30_yr": "4.96"},
                {"record_date": "2026-05-27", "security_desc_2_yr": "4.05", "security_desc_10_yr": "4.40", "security_desc_30_yr": "4.90"},
            ]
        }

        releases = parse_treasury_rates_response(payload, fetched_at=FETCHED_AT)

        self.assertEqual([release.metric for release in releases], ["2-year Treasury yield", "10-year Treasury yield", "30-year Treasury yield"])
        self.assertEqual(releases[0].source, "Treasury.gov")
        self.assertAlmostEqual(releases[1].actual or 0, 4.48)
        self.assertAlmostEqual(releases[1].prior or 0, 4.40)

    def test_cache_fallback_when_source_fails(self) -> None:
        cached_release = MacroRelease(
            category="inflation",
            metric="CPI",
            source="BLS.gov",
            period="2026 April",
            release_timestamp="2026 April",
            actual=321.5,
            prior=320.5,
            revision=None,
            forecast=None,
            unit="index",
            raw_source="BLS",
            freshness_status="fresh",
            fetch_timestamp=FETCHED_AT,
        )
        cached_snapshot = MacroSnapshot(
            fetched_at=FETCHED_AT,
            releases=[cached_release],
            source_statuses=[MacroSourceStatus("BLS.gov", "fresh", FETCHED_AT, "BLS")],
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "macro_cache.json"
            save_macro_cache(cached_snapshot, cache_path)
            with patch("app.macro.sources.CACHE_PATH", cache_path), patch("app.macro.sources.fetch_bls_releases", side_effect=RuntimeError("BLS offline")), patch(
                "app.macro.sources.fetch_bea_releases", return_value=([], MacroSourceStatus("BEA.gov", "fresh", FETCHED_AT, "BEA"))
            ), patch("app.macro.sources.fetch_treasury_rates", return_value=([], MacroSourceStatus("Treasury.gov", "fresh", FETCHED_AT, "Treasury"))):
                snapshot = collect_macro_snapshot()

        fallback = [release for release in snapshot.releases if release.source == "BLS.gov"][0]
        self.assertEqual(fallback.freshness_status, "cached")
        self.assertIn("BLS offline", fallback.notes)
        self.assertTrue(any(status.source == "BLS.gov" and status.cached_fallback for status in snapshot.source_statuses))

    def test_macro_interpretation_hotter_and_cooler_inflation(self) -> None:
        hotter = MacroRelease(
            "inflation", "CPI", "BLS.gov", "2026 April", "2026 April", 321.5, 320.5, None, None, "index", "BLS", "fresh", FETCHED_AT
        )
        cooler = MacroRelease(
            "inflation", "CPI", "BLS.gov", "2026 May", "2026 May", 319.5, 320.5, None, None, "index", "BLS", "fresh", FETCHED_AT
        )

        self.assertIn("hotter", "\n".join(market_impact_lines([hotter])))
        self.assertIn("cooler", "\n".join(market_impact_lines([cooler])))

    def test_output_contains_sources_and_timestamps(self) -> None:
        release = MacroRelease(
            "treasury", "10-year Treasury yield", "Treasury.gov", "2026-05-28", "2026-05-28", 4.48, 4.40, None, None, "percent", "Treasury", "fresh", FETCHED_AT
        )
        snapshot = MacroSnapshot(
            fetched_at=FETCHED_AT,
            releases=[release],
            source_statuses=[MacroSourceStatus("Treasury.gov", "fresh", FETCHED_AT, "Treasury")],
        )

        report = format_macro_report(snapshot)

        self.assertIn("Official Macro Snapshot", report)
        self.assertIn("Treasury.gov", report)
        self.assertIn(FETCHED_AT, report)

    def test_schwab_technical_analysis_macro_failure_stays_readable(self) -> None:
        with patch("app.ui.unified_trade_thesis_next_checks_extension.build_macro_report", side_effect=RuntimeError("macro fetch failed")):
            report = _macro_report_or_error()

        self.assertIn("Official Macro Snapshot", report)
        self.assertIn("unavailable/error", report)
        self.assertIn("macro fetch failed", report)


if __name__ == "__main__":
    unittest.main()
