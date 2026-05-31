from __future__ import annotations

import unittest
from unittest.mock import patch

from app.analytics.earnings_release import EarningsReleaseDigest, format_earnings_release_digest
from app.ui.schwab_output_popout_extension import _iter_url_spans, _open_external_url


class EarningsReleaseFormattingTests(unittest.TestCase):
    def test_digest_uses_numbered_readable_snippet_blocks(self) -> None:
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
        )

        output = format_earnings_release_digest(digest)

        self.assertIn("Headline / Result Snippets:", output)
        self.assertIn("Guidance / Outlook Snippets:", output)
        self.assertIn("Margin / Cash Flow Snippets:", output)
        self.assertIn("Snippet 1:\nRevenue of $5.7 billion", output)
        self.assertIn("Snippet 2:\nOperating margin", output)
        self.assertIn(digest.source_url, output)

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
