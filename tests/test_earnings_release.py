from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.analytics.earnings_release import (
    CompanyEarningsRelease,
    EarningsCalendarEvent,
    EarningsReleaseDigest,
    build_earnings_freshness,
    format_earnings_release_digest,
    source_matches_symbol,
)
from app.ui.schwab_output_popout_extension import _iter_url_spans, _open_external_url


class EarningsReleaseFormattingTests(unittest.TestCase):
    def test_digest_uses_clean_sections_and_source_details(self) -> None:
        digest = EarningsReleaseDigest(
            title="L3Harris Technologies Reports Strong First Quarter 2026 Results",
            filing_date="2026-04-30",
            filing_items="2.02, 9.01",
            exhibit_type="EX-99.1",
            source_url="https://www.sec.gov/Archives/edgar/data/202058/000020205826000032/exhibit991q1cy26earnings.htm",
            headline_snippets=[
                "Revenue of $5.7 billion, up 12%, and 15% organically.",
                "Operating margin of 11.4%, up 120 bps.",
            ],
            guidance_snippets=["Updates 2026 EPS guidance."],
            margin_cashflow_snippets=["Cash used in operations was $(95) million."],
            symbol="LHX",
            source_date="2026-04-30",
            latest_sec_filing_date="2026-04-30",
            metric_snippets={
                "revenue": ["Revenue of $5.7 billion, up 12%."],
                "eps": ["Updates 2026 EPS guidance."],
                "margins": ["Operating margin of 11.4%, up 120 bps."],
            },
            source_details=[("SEC 8-K earnings exhibit", "2026-04-30", "https://www.sec.gov/Archives/edgar/data/202058/000020205826000032/exhibit991q1cy26earnings.htm")],
        )

        output = format_earnings_release_digest(digest)

        self.assertIn("Bottom Line", output)
        self.assertIn("Freshness Check", output)
        self.assertIn("Latest Quarter Snapshot", output)
        self.assertIn("Source Details", output)
        self.assertIn("Raw excerpts:", output)
        self.assertNotIn("Snippet 1:", output)
        self.assertLess(output.index("Bottom Line"), output.index("Source Details"))
        self.assertLess(output.index("Source Details"), output.index("Raw excerpts:"))
        self.assertIn(digest.source_url, output)

    def test_hpe_hpq_source_disambiguation(self) -> None:
        hpe_text = "HPE (NYSE: HPE) today announced results for Hewlett Packard Enterprise Company."
        hpq_text = "HP Inc. (NYSE: HPQ) today announced quarterly earnings."

        self.assertTrue(source_matches_symbol("HPE", "Hewlett Packard Enterprise Company", hpe_text, "https://investors.hpe.com/news"))
        self.assertFalse(source_matches_symbol("HPE", "Hewlett Packard Enterprise Company", hpq_text, "https://investor.hp.com/news"))
        self.assertTrue(source_matches_symbol("HPQ", "HP Inc.", hpq_text, "https://investor.hp.com/news"))
        self.assertFalse(source_matches_symbol("HPQ", "HP Inc.", hpe_text, "https://investors.hpe.com/news"))

    def test_earnings_today_with_old_sec_filing_warns_stale(self) -> None:
        event = EarningsCalendarEvent("HPE", "Hewlett Packard Enterprise Company", "2026-06-01", "time-after-hours")
        old_sec_release = SimpleNamespace(filing=SimpleNamespace(filing_date="2026-05-14"))

        freshness = build_earnings_freshness("HPE", calendar_event=event, sec_release=old_sec_release, today=date(2026, 6, 1))

        self.assertEqual(freshness.status, "stale")
        self.assertEqual(freshness.card_label, "Potentially Stale")
        self.assertIn("Potentially stale", freshness.verdict)
        self.assertIn("2026-05-14", freshness.verdict)

    def test_earnings_today_company_ir_release_is_fresh(self) -> None:
        event = EarningsCalendarEvent("HPE", "Hewlett Packard Enterprise Company", "2026-06-01", "time-after-hours")
        company_release = CompanyEarningsRelease(
            "HPE Reports Fiscal 2026 Second Quarter Results",
            "2026-06-01",
            "https://investors.hpe.com/news",
            "HPE Reports Fiscal 2026 Second Quarter Results. Revenue and EPS improved.",
            "company_ir",
        )

        freshness = build_earnings_freshness("HPE", calendar_event=event, company_release=company_release, today=date(2026, 6, 1))

        self.assertEqual(freshness.status, "fresh")
        self.assertEqual(freshness.card_label, "Fresh Release")
        self.assertIn("Fresh earnings release found", freshness.verdict)

    def test_url_span_detection_trims_sentence_punctuation(self) -> None:
        content = "Source: https://www.sec.gov/Archives/edgar/data/202058/example.htm. Next sentence."

        spans = _iter_url_spans(content)

        self.assertEqual(len(spans), 1)
        start, end, url = spans[0]
        self.assertEqual(url, "https://www.sec.gov/Archives/edgar/data/202058/example.htm")
        self.assertEqual(content[start:end], url)

    def test_external_url_handler_opens_browser_tab(self) -> None:
        url = "https://www.sec.gov/Archives/edgar/data/202058/example.htm"

        with patch("app.ui.schwab_output_popout_extension.webbrowser.open_new_tab") as open_new_tab:
            result = _open_external_url(url)

        self.assertEqual(result, "break")
        open_new_tab.assert_called_once_with(url)


if __name__ == "__main__":
    unittest.main()
