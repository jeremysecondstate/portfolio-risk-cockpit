from __future__ import annotations

import unittest
from unittest.mock import patch

from app.analytics.etf_analysis import build_etf_readout, build_etf_research_snapshot, detect_security_kind
from app.ui.schwab_research_workspace_extension import _fetch_etf_layers


class ETFAnalysisTests(unittest.TestCase):
    def test_etf_detection_prefers_quote_and_position_type(self) -> None:
        quote = {
            "assetMainType": "EQUITY",
            "assetSubType": "ETF",
            "quote": {"description": "Sample Space ETF"},
        }

        self.assertEqual(detect_security_kind("NASA", quote), "etf")
        self.assertEqual(detect_security_kind("ABC", None, "Mutual Fund"), "fund")
        self.assertEqual(detect_security_kind("GOOG", {"assetMainType": "EQUITY", "assetSubType": "COMMON_STOCK"}), "equity")

    def test_known_etf_symbol_fallback_does_not_require_hard_quote_type(self) -> None:
        self.assertEqual(detect_security_kind("SPY"), "etf")
        self.assertEqual(detect_security_kind("NASA"), "etf")

    def test_etf_readout_uses_etf_cards_instead_of_company_cards(self) -> None:
        snapshot = build_etf_research_snapshot("NASA", quote={"quote": {"description": "Space Thematic ETF"}}, security_kind="etf")
        readout = build_etf_readout(snapshot)
        document_titles = {card.title for card in readout.document_cards}
        structure_titles = {card.title for card in readout.structure_cards}

        self.assertIn("ETF Mode", document_titles)
        self.assertIn("Expense Ratio", structure_titles)
        self.assertIn("Top 10 Weight", structure_titles)
        self.assertIn("Liquidity", structure_titles)
        self.assertFalse({"Guidance Tone", "Revenue Trend", "Profitability", "Latest Earnings"} & document_titles)
        self.assertIn("company revenue, EPS, profitability, and guidance are not the right research questions", " ".join(readout.interpretation))

    def test_etf_source_links_include_document_types(self) -> None:
        snapshot = build_etf_research_snapshot("SPY", security_kind="etf")
        labels = " ".join(label for label, _date, _url in snapshot.source_links)

        self.assertIn("Official issuer fund page", labels)
        self.assertIn("Fund factsheet PDF", labels)
        self.assertIn("Prospectus", labels)
        self.assertIn("Holdings file", labels)
        self.assertIn("SEC fund filing search", labels)

    def test_etf_sec_lookup_failure_is_friendly_not_companyfacts_broken(self) -> None:
        with patch("app.ui.schwab_research_workspace_extension.SecEdgarClient", side_effect=RuntimeError("offline")):
            earnings_text, fundamentals_text, _filings_lines, statuses, snapshot = _fetch_etf_layers("NASA", None, "etf")

        self.assertIn("ETF companyfacts are not applicable", earnings_text)
        self.assertIn("ETF STRUCTURE / HOLDINGS", fundamentals_text)
        self.assertNotIn("SEC companyfacts unavailable/error", fundamentals_text)
        self.assertTrue(any(status.source == "SEC companyfacts" and status.status == "not-applicable" for status in statuses))
        self.assertFalse(any(status.status == "error" for status in statuses))
        self.assertTrue(snapshot.warnings)


if __name__ == "__main__":
    unittest.main()
