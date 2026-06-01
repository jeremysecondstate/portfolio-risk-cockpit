from __future__ import annotations

import unittest

from app.analytics.etf_analysis import detect_security_kind
from app.analytics.earnings_release import format_earnings_release_digest
from app.analytics.etf_analysis import build_etf_research_snapshot, format_etf_documents_text
from app.analytics.foreign_issuer_analysis import (
    REPORTING_PROFILE_ETF_OR_FUND,
    REPORTING_PROFILE_FOREIGN_ISSUER,
    REPORTING_PROFILE_US_DOMESTIC_EQUITY,
    build_foreign_issuer_snapshot,
    detect_reporting_profile,
    foreign_issuer_earnings_cards,
    foreign_issuer_fundamental_cards,
    foreign_issuer_source_links,
    format_foreign_issuer_earnings_text,
    format_foreign_issuer_fundamentals_text,
    format_foreign_issuer_results_explanation,
)


class ForeignIssuerAnalysisTests(unittest.TestCase):
    def test_asml_like_forms_detect_foreign_issuer_not_etf(self) -> None:
        quote = {"assetMainType": "EQUITY", "assetSubType": "COMMON_STOCK", "quote": {"description": "ASML Holding N.V."}}

        self.assertEqual(detect_security_kind("ASML", quote), "equity")
        self.assertEqual(
            detect_reporting_profile("ASML", quote, sec_forms=["6-K", "20-F"], company_title="ASML HOLDING NV"),
            REPORTING_PROFILE_FOREIGN_ISSUER,
        )

    def test_us_domestic_and_etf_profiles_remain_separate(self) -> None:
        self.assertEqual(
            detect_reporting_profile("NVDA", {"assetMainType": "EQUITY", "assetSubType": "COMMON_STOCK"}, sec_forms=["10-Q", "8-K"]),
            REPORTING_PROFILE_US_DOMESTIC_EQUITY,
        )
        self.assertEqual(
            detect_reporting_profile("SPY", {"assetMainType": "EQUITY", "assetSubType": "ETF"}),
            REPORTING_PROFILE_ETF_OR_FUND,
        )

    def test_foreign_issuer_fallback_does_not_show_missing_earnings_exhibit(self) -> None:
        snapshot = build_foreign_issuer_snapshot(
            "ASML",
            company_name="ASML HOLDING NV",
            filings_lines=[
                "6-K filed 2026-04-15 period 2026-03-31: https://www.sec.gov/Archives/edgar/data/937966/example6k.htm",
                "20-F filed 2026-02-11 period 2025-12-31: https://www.sec.gov/Archives/edgar/data/937966/example20f.htm",
            ],
            sec_text=(
                "ASML reports Q1 2026 total net sales increased to EUR 8.8 billion. "
                "Net income was EUR 2.8 billion and gross margin was 54.0%. "
                "Order intake and bookings remained strong. Outlook expects growth."
            ),
            companyfacts_available=False,
        )

        earnings_text = format_foreign_issuer_earnings_text(snapshot)
        fundamentals_text = format_foreign_issuer_fundamentals_text(snapshot)

        self.assertIn("Foreign issuer source mode", earnings_text)
        self.assertIn("SEC companyfacts/XBRL not available or limited", earnings_text)
        self.assertNotIn("No earnings exhibit found", earnings_text)
        self.assertIn("20-F / 40-F", fundamentals_text)
        self.assertNotIn("SEC companyfacts unavailable/error", fundamentals_text)

    def test_source_links_include_ir_6k_and_20f_categories(self) -> None:
        links = foreign_issuer_source_links(
            "ASML",
            "ASML HOLDING NV",
            [
                "6-K filed 2026-04-15 period 2026-03-31: https://www.sec.gov/Archives/edgar/data/937966/example6k.htm",
                "20-F filed 2026-02-11 period 2025-12-31: https://www.sec.gov/Archives/edgar/data/937966/example20f.htm",
            ],
        )
        labels = " ".join(label for label, _date, _url in links)
        urls = " ".join(url for _label, _date, url in links)

        self.assertIn("Official investor relations financial results page", labels)
        self.assertIn("Company annual report page", labels)
        self.assertIn("SEC 6-K filing", labels)
        self.assertIn("SEC 20-F filing", labels)
        self.assertIn("https://www.asml.com/en/investors/financial-results", urls)

    def test_foreign_issuer_cards_replace_domestic_earnings_cards(self) -> None:
        snapshot = build_foreign_issuer_snapshot("ASML", sec_text="Revenue increased. Net income improved. Guidance reaffirmed.")
        earnings_titles = {title for title, _label, _status, _why in foreign_issuer_earnings_cards(snapshot)}
        fundamental_titles = {title for title, _label, _status, _why in foreign_issuer_fundamental_cards(snapshot)}

        self.assertIn("Foreign Issuer Mode", earnings_titles)
        self.assertIn("Latest Results", earnings_titles)
        self.assertIn("Orders / Bookings", earnings_titles)
        self.assertIn("Foreign Issuer Fundamentals", fundamental_titles)
        self.assertFalse({"Latest Earnings"} & earnings_titles)

    def test_foreign_issuer_explanation_is_analysis_not_nav_dump(self) -> None:
        snapshot = build_foreign_issuer_snapshot(
            "ASML",
            company_name="ASML HOLDING NV",
            filings_lines=["6-K filed 2026-04-15 period 2026-03-31: https://www.sec.gov/Archives/edgar/data/937966/example6k.htm"],
            official_text=(
                "SupplierNet CustomerNet Search Search Home Investors Annual reports 2025 full-year results "
                "EUR 32.7bn Total net sales 52.8% Gross margin EUR 24.73 Earnings per share basic. "
            ),
            sec_text=(
                "ASML reports EUR 8.8 billion total net sales and EUR 2.8 billion net income in Q1 2026. "
                "ASML now expects 2026 total net sales to be between EUR 36 billion and EUR 40 billion, "
                "with a gross margin between 51% and 53%."
            ),
            source_links=[
                ("Official investor relations financial results page", "--", "https://www.asml.com/en/investors/financial-results"),
                ("Latest quarterly result page or PDF", "--", "https://www.asml.com/en/investors/financial-results/q1-2026"),
                ("Latest quarterly result page or PDF", "--", "https://www.asml.com/en/investors/financial-results/q4-2025"),
                ("Company annual report page", "--", "https://www.asml.com/investors/annual-report"),
                ("SEC 6-K filing", "2026-04-15", "https://www.sec.gov/Archives/edgar/data/937966/example6k.htm"),
                ("SEC 20-F filing", "2026-02-11", "https://www.sec.gov/Archives/edgar/data/937966/example20f.htm"),
                ("SEC companyfacts / XBRL", "latest loaded", "https://data.sec.gov/api/xbrl/companyfacts/"),
            ],
            companyfacts_available=True,
        )

        output = format_foreign_issuer_results_explanation(snapshot)

        self.assertIn("1. Bottom Line", output)
        self.assertIn("3. Latest Results Snapshot", output)
        self.assertIn("4. Good / Bad / Watch", output)
        self.assertIn("5. Results History Read", output)
        self.assertIn("6. Result Verdict", output)
        self.assertIn("Official company sources:", output)
        self.assertIn("SEC foreign issuer filings:", output)
        self.assertIn("Supplemental:", output)
        self.assertIn("EUR 8.8 billion", output)
        self.assertIn("EUR 2.8 billion", output)
        self.assertIn("EUR 36 billion to EUR 40 billion", output)
        self.assertNotIn("SupplierNet CustomerNet Search Search Home", output)

    def test_missing_clean_values_use_not_cleanly_extracted(self) -> None:
        snapshot = build_foreign_issuer_snapshot(
            "ASML",
            sec_text="Revenue and outlook were discussed, but this paragraph has no clean value pair.",
            source_links=[
                ("Latest quarterly result page or PDF", "--", "https://www.asml.com/en/investors/financial-results/q1-2026"),
            ],
        )

        output = format_foreign_issuer_results_explanation(snapshot)

        self.assertIn("Not cleanly extracted yet", output)
        self.assertIn("values not extracted yet", output)

    def test_raw_extracts_are_bottom_section_and_truncated(self) -> None:
        snapshot = build_foreign_issuer_snapshot(
            "ASML",
            sec_text=(
                "ASML reports EUR 8.8 billion total net sales and EUR 2.8 billion net income in Q1 2026. "
                "This extra sentence is intentionally long so the raw extract section must truncate the loaded text before it becomes a source dump."
            ),
            source_links=[("SEC 6-K filing", "2026-04-15", "https://www.sec.gov/Archives/edgar/data/937966/example6k.htm")],
        )

        output = format_foreign_issuer_results_explanation(snapshot)
        raw_index = output.index("9. Source Details / Raw Extracts")
        source_index = output.index("7. Source Links")

        self.assertGreater(raw_index, source_index)
        raw_lines = output[raw_index:].splitlines()[1:]
        self.assertTrue(all(len(line) <= 175 for line in raw_lines if line.startswith("- ")))

    def test_domestic_and_etf_explanations_remain_unchanged(self) -> None:
        domestic = format_earnings_release_digest(None)
        etf = format_etf_documents_text(build_etf_research_snapshot("SPY", security_kind="etf"))

        self.assertIn("FAST EARNINGS RELEASE LAYER", domestic)
        self.assertIn("No recent 8-K earnings-release exhibit", domestic)
        self.assertNotIn("Bottom Line", domestic)
        self.assertIn("ETF DOCUMENTS / UPDATES", etf)
        self.assertNotIn("Foreign Issuer Results Explanation", etf)


if __name__ == "__main__":
    unittest.main()
