from __future__ import annotations

import unittest

from app.analytics.ipo_pipeline import (
    IpoPipelineRecord,
    analyze_ipo_risk_flags,
    build_ipo_pipeline_records,
    determine_ipo_status,
    display_price_range,
    is_ipo_form,
    parse_ipo_filing_text,
    status_for_form,
)
from app.data.sec_edgar import SecCurrentFiling


def _filing(company: str, cik: str, form: str, date: str, accession: str) -> SecCurrentFiling:
    return SecCurrentFiling(
        company_name=company,
        cik=cik,
        form=form,
        filing_date=date,
        accession_number=accession,
        filing_url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm",
        assigned_sic="7372",
        assigned_sic_description="Services-Prepackaged Software",
    )


class IpoPipelineTests(unittest.TestCase):
    def test_form_detection_and_status_mapping(self) -> None:
        self.assertTrue(is_ipo_form("S-1"))
        self.assertTrue(is_ipo_form("F-1/A"))
        self.assertTrue(is_ipo_form("424B4"))
        self.assertTrue(is_ipo_form("EFFECT"))
        self.assertFalse(is_ipo_form("10-Q"))

        self.assertEqual(status_for_form("S-1"), "Filed")
        self.assertEqual(status_for_form("S-1/A"), "Amended")
        self.assertEqual(status_for_form("F-1/A"), "Amended")
        self.assertEqual(status_for_form("EFFECT"), "Effective")
        self.assertEqual(status_for_form("424B4"), "Priced / Final Prospectus")

    def test_status_does_not_call_s1_trading_without_final_or_effective_event(self) -> None:
        self.assertEqual(
            determine_ipo_status(["S-1"], proposed_ticker="ACME", exchange="Nasdaq"),
            "Filed",
        )
        self.assertEqual(determine_ipo_status(["S-1", "S-1/A"]), "Amended")
        self.assertEqual(determine_ipo_status(["F-1", "EFFECT"]), "Effective")
        self.assertEqual(determine_ipo_status(["S-1", "424B4"]), "Priced / Final Prospectus")
        self.assertEqual(
            determine_ipo_status(["S-1", "EFFECT"], proposed_ticker="ACME", exchange="Nasdaq"),
            "Trading Candidate",
        )

    def test_grouped_records_use_latest_form_and_submission_metadata(self) -> None:
        records = build_ipo_pipeline_records(
            [
                _filing("Acme Software Inc.", "0001234567", "S-1", "2026-05-01", "0001234567-26-000001"),
                _filing("Acme Software Inc.", "0001234567", "S-1/A", "2026-05-14", "0001234567-26-000002"),
            ],
            submissions_by_cik={
                "0001234567": {
                    "tickers": ["ACME"],
                    "exchanges": ["Nasdaq"],
                    "sic": "7372",
                    "sicDescription": "Services-Prepackaged Software",
                }
            },
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].form, "S-1/A")
        self.assertEqual(records[0].ipo_status, "Amended")
        self.assertEqual(records[0].proposed_ticker, "ACME")
        self.assertEqual(records[0].exchange, "Nasdaq")
        self.assertEqual(records[0].sector, "Technology")
        self.assertEqual(records[0].amendment_count, 1)

    def test_parser_extracts_optional_fields_without_requiring_them(self) -> None:
        empty = parse_ipo_filing_text("", form="S-1")

        self.assertIsNone(empty.proposed_ticker)
        self.assertIsNone(empty.price_range_low)
        self.assertFalse(empty.risk_flags)

        parsed = parse_ipo_filing_text(
            "We expect to list our common stock on the Nasdaq Global Market under the symbol ACME. "
            "The initial public offering price is expected to be between $18.00 and $20.00 per share. "
            "We are offering 10 million shares. Revenue was $0 and net loss was $12 million. "
            "Gross margin was -4.5%. Cash and cash equivalents were $20 million. Total debt was $55 million. "
            "Use of proceeds We intend to use the net proceeds for working capital and growth investments. "
            "The representatives of the underwriters are Goldman Sachs & Co. LLC and Morgan Stanley & Co. LLC. "
            "Our independent registered public accounting firm is Deloitte & Touche LLP. "
            "Risk factors include going concern language and related party transaction disclosure.",
            form="S-1",
        )

        self.assertEqual(parsed.proposed_ticker, "ACME")
        self.assertEqual(parsed.exchange, "Nasdaq Global Market")
        self.assertEqual(parsed.price_range_low, 18.0)
        self.assertEqual(parsed.price_range_high, 20.0)
        self.assertEqual(parsed.shares_offered, 10_000_000)
        self.assertEqual(parsed.revenue, 0)
        self.assertLess(parsed.net_income or 0, 0)
        self.assertEqual(parsed.gross_margin, -4.5)
        self.assertIn("Goldman Sachs", " ".join(parsed.underwriters))
        self.assertIn("Deloitte", parsed.auditor or "")
        self.assertIn("Going concern language", parsed.risk_flags)

    def test_missing_price_range_displays_not_yet_disclosed(self) -> None:
        record = IpoPipelineRecord(
            cik="0001234567",
            company_name="Acme Software Inc.",
            proposed_ticker=None,
            form="S-1",
            filed_date="2026-05-01",
            ipo_status="Filed",
            sic=None,
            sector=None,
            industry=None,
            exchange=None,
            filing_url="https://sec.example",
            accession_number="0001234567-26-000001",
        )

        self.assertEqual(display_price_range(record), "Not yet disclosed")

    def test_risk_flags_combine_financials_keywords_and_missing_terms(self) -> None:
        record = IpoPipelineRecord(
            cik="0001234567",
            company_name="Acme Software Inc.",
            proposed_ticker=None,
            form="F-1/A",
            filed_date="2026-05-01",
            ipo_status="Amended",
            sic=None,
            sector=None,
            industry=None,
            exchange=None,
            revenue=0,
            revenue_growth=-12.0,
            net_income=-30_000_000,
            gross_margin=-2.0,
            cash=10_000_000,
            debt=45_000_000,
            filing_url="https://sec.example",
            accession_number="0001234567-26-000001",
            amendment_count=4,
            is_foreign_issuer=True,
        )

        flags = analyze_ipo_risk_flags(
            record,
            text="The filing describes a controlled company, customer concentration, China-based VIE, and auditor change.",
        )

        self.assertIn("No revenue", flags)
        self.assertIn("Revenue declining", flags)
        self.assertIn("Unprofitable", flags)
        self.assertIn("High debt", flags)
        self.assertIn("Negative gross margin", flags)
        self.assertIn("Foreign issuer", flags)
        self.assertIn("Repeated amendments", flags)
        self.assertIn("Price range missing", flags)
        self.assertIn("Controlled company", flags)
        self.assertIn("Customer concentration", flags)
        self.assertIn("China/VIE structure", flags)
        self.assertIn("Auditor change", flags)


if __name__ == "__main__":
    unittest.main()
