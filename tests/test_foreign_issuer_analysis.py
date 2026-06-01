from __future__ import annotations

import unittest

from app.analytics.etf_analysis import detect_security_kind
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


if __name__ == "__main__":
    unittest.main()
