from __future__ import annotations

import tempfile
import unittest
from datetime import date
from unittest.mock import patch

from app.analytics.earnings_release import analyze_earnings_sources, format_earnings_release_digest
from app.analytics.earnings_pipeline import (
    EARNINGS_DROP_KIND,
    FORMAL_REPORT_KIND,
    EarningsRadarSnapshot,
    EarningsRadarStore,
    ParsedEarningsFields,
    RecentEarningsRecord,
    build_recent_earnings_records,
    classify_earnings_filing_kind,
    filter_recent_earnings_records,
    filter_upcoming_earnings_records,
    is_likely_earnings_current_filing,
    parse_earnings_release_text,
)
from app.analytics.research_scoring import score_earnings_risk
from app.data.earnings_calendar import UpcomingEarningsRecord
from app.data.sec_edgar import SecCompany, SecCurrentFiling, SecEarningsReport, SecFiling, SecFilingDocument


NVDA_LIKE_10Q_FIXTURE = """
NVIDIA CORP Quarterly Report on Form 10-Q
Quarter ended April 27, 2026
Revenue was $44.1 billion, up 69% from a year ago.
Diluted earnings per share was $0.76. Net income was $18.8 billion.
Gross margin was 60.5%, and operating margin was 49.5%.
Data Center platform revenue was $39.1 billion as demand for accelerated computing remained strong.
Management's Discussion and Analysis says growth was driven by Blackwell platform shipments and cloud service provider demand.
Liquidity remained strong; net cash provided by operating activities was $27.4 billion for the first quarter.
The company returned capital through $14.1 billion of share repurchases and $244 million of cash dividends.
Risks include demand variability, supply constraints, export controls, customer concentration, tariffs, inventory levels, and margin pressure.
"""


def _filing(
    form: str = "8-K",
    *,
    accession: str = "0000000001-26-000001",
    cik: str = "1",
    company: str = "Acme Corp",
    filed_date: str = "2026-06-05",
    primary_document: str = "acme-20260605.htm",
) -> SecCurrentFiling:
    accession_no_dashes = accession.replace("-", "")
    return SecCurrentFiling(
        company_name=company,
        cik=cik,
        form=form,
        filing_date=filed_date,
        accession_number=accession,
        filing_url=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dashes}/{primary_document}",
        assigned_sic="7372",
        assigned_sic_description="Services-Prepackaged Software",
        acceptance_datetime=f"{filed_date}T16:01:00",
        primary_document=primary_document,
    )


def _recent_record(
    *,
    company: str = "Acme Corp",
    ticker: str = "ACME",
    form: str = "8-K",
    items: str = "2.02",
    filed_date: str = "2026-06-05",
    guidance: bool = True,
    risk_flags: tuple[str, ...] = ("Revenue decline",),
    exhibit_url: str | None = "https://example.test/ex99.htm",
) -> RecentEarningsRecord:
    return RecentEarningsRecord(
        cik="0000000001",
        company_name=company,
        ticker=ticker,
        form=form,
        items=items,
        filed_date=filed_date,
        acceptance_datetime=f"{filed_date}T16:01:00",
        report_date="2026-03-31",
        fiscal_period="First quarter 2026",
        sector="Technology",
        industry="Services-Prepackaged Software",
        sic="7372",
        exchange="Nasdaq",
        release_title=f"{company} Reports Results",
        revenue=123_400_000.0,
        revenue_growth=-4.0 if "Revenue decline" in risk_flags else 12.0,
        eps=0.45,
        net_income=20_000_000.0,
        guidance_flag=guidance,
        risk_flags=risk_flags,
        filing_url="https://example.test/filing.htm",
        exhibit_url=exhibit_url,
        accession_number="0000000001-26-000001",
        filing_type=EARNINGS_DROP_KIND,
    )


def _sec_10q_report(text: str = NVDA_LIKE_10Q_FIXTURE) -> SecEarningsReport:
    company = SecCompany(ticker="NVDA", cik="0001045810", title="NVIDIA CORP")
    filing = SecFiling(
        company=company,
        accession_number="0001045810-26-000091",
        filing_date="2026-05-28",
        report_date="2026-04-27",
        form="10-Q",
        primary_document="nvda-20260427.htm",
        description="10-Q",
    )
    document = SecFilingDocument(
        filing=filing,
        document="nvda-20260427.htm",
        description="Form 10-Q",
        type="10-Q",
        sequence="1",
    )
    return SecEarningsReport(company=company, filing=filing, document=document, text=text)


class _No8KFakeSecClient:
    def __init__(self, report: SecEarningsReport) -> None:
        self.report = report

    def recent_filings(self, symbol: str, *, forms: tuple[str, ...], limit: int) -> list[SecFiling]:
        return [self.report.filing]

    def latest_earnings_release(self, symbol: str) -> None:
        return None

    def latest_formal_earnings_report(self, symbol: str) -> SecEarningsReport:
        return self.report

    def get_companyfacts(self, symbol: str) -> tuple[SecCompany, dict]:
        raise RuntimeError("companyfacts offline in deterministic test")


class EarningsPipelineTests(unittest.TestCase):
    def test_8k_item_202_is_classified_as_recent_earnings(self) -> None:
        filing = _filing("8-K")

        self.assertTrue(is_likely_earnings_current_filing(filing, items="2.02"))
        self.assertEqual(classify_earnings_filing_kind(filing, items="2.02"), EARNINGS_DROP_KIND)

    def test_8k_ex99_with_earnings_keywords_is_classified(self) -> None:
        filing = _filing("8-K")

        self.assertTrue(
            is_likely_earnings_current_filing(
                filing,
                items="9.01",
                text_hint="EX-99.1 press release announcing quarterly earnings and financial results",
            )
        )

    def test_non_earnings_8k_is_ignored(self) -> None:
        filing = _filing("8-K")

        self.assertFalse(is_likely_earnings_current_filing(filing, items="1.01", text_hint="material definitive agreement"))

    def test_6k_with_quarterly_results_keywords_is_included(self) -> None:
        filing = _filing("6-K")

        self.assertTrue(is_likely_earnings_current_filing(filing, text_hint="foreign issuer quarterly results revenue net income"))

    def test_formal_reports_are_included_but_distinguished(self) -> None:
        filing = _filing("10-Q")

        self.assertEqual(classify_earnings_filing_kind(filing), FORMAL_REPORT_KIND)

    def test_parsing_extracts_financial_fields_and_guidance(self) -> None:
        parsed = parse_earnings_release_text(
            """
            Acme Reports First Quarter 2026 Results
            Revenue increased 12% to $123.4 million for the quarter ended March 31, 2026.
            Diluted EPS was $0.45. Net income was $20.0 million.
            The company expects full-year guidance to improve.
            """
        )

        self.assertEqual(parsed.release_title, "Acme Reports First Quarter 2026 Results")
        self.assertEqual(parsed.report_date, "2026-03-31")
        self.assertEqual(parsed.fiscal_period, "Quarter ended March 31, 2026")
        self.assertEqual(parsed.revenue, 123_400_000.0)
        self.assertEqual(parsed.revenue_growth, 12.0)
        self.assertEqual(parsed.eps, 0.45)
        self.assertEqual(parsed.net_income, 20_000_000.0)
        self.assertTrue(parsed.guidance_flag)

    def test_parsing_extracts_nvda_like_10q_financial_fields(self) -> None:
        parsed = parse_earnings_release_text(NVDA_LIKE_10Q_FIXTURE)

        self.assertEqual(parsed.report_date, "2026-04-27")
        self.assertEqual(parsed.fiscal_period, "Quarter ended April 27, 2026")
        self.assertEqual(parsed.revenue, 44_100_000_000.0)
        self.assertEqual(parsed.revenue_growth, 69.0)
        self.assertEqual(parsed.eps, 0.76)
        self.assertEqual(parsed.net_income, 18_800_000_000.0)

    def test_parsing_sets_risk_flags_for_declines_and_losses(self) -> None:
        parsed = parse_earnings_release_text(
            """
            Acme Reports Quarterly Results
            Revenue decreased 8% to $90 million. Loss per share was $0.15.
            Net loss was $5 million. Management lowers guidance.
            """
        )

        self.assertEqual(parsed.revenue_growth, -8.0)
        self.assertEqual(parsed.eps, -0.15)
        self.assertEqual(parsed.net_income, -5_000_000.0)
        self.assertIn("Revenue decline", parsed.risk_flags)
        self.assertIn("Negative EPS", parsed.risk_flags)
        self.assertIn("Net loss", parsed.risk_flags)
        self.assertIn("Guidance cut", parsed.risk_flags)

    def test_build_records_uses_submissions_metadata_and_parsed_fields(self) -> None:
        filing = _filing("8-K")
        submissions = {
            "0000000001": {
                "tickers": ["ACME"],
                "exchanges": ["Nasdaq"],
                "sic": "7372",
                "sicDescription": "Services-Prepackaged Software",
                "filings": {
                    "recent": {
                        "accessionNumber": [filing.accession_number],
                        "items": ["2.02,9.01"],
                        "reportDate": ["2026-03-31"],
                        "primaryDocument": ["acme-20260605.htm"],
                        "primaryDocDescription": ["Earnings Release"],
                    }
                },
            }
        }

        records = build_recent_earnings_records(
            [filing],
            submissions_by_cik=submissions,
            parsed_by_accession={filing.accession_number: ParsedEarningsFields(revenue=10_000_000.0, guidance_flag=True)},
            exhibit_url_by_accession={filing.accession_number: "https://example.test/ex99.htm"},
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].ticker, "ACME")
        self.assertEqual(records[0].items, "2.02,9.01")
        self.assertEqual(records[0].sector, "Technology")
        self.assertEqual(records[0].release_title, "Earnings Release")
        self.assertEqual(records[0].revenue, 10_000_000.0)
        self.assertTrue(records[0].guidance_flag)

    def test_no_8k_uses_10q_fallback_digest_with_metrics(self) -> None:
        report = _sec_10q_report()

        digest = analyze_earnings_sources(
            "NVDA",
            None,
            sec_report=report,
            company_name="NVIDIA CORP",
            latest_sec_filing_date="2026-05-28",
            today=date(2026, 6, 7),
        )
        rendered = format_earnings_release_digest(digest)

        self.assertIsNotNone(digest)
        self.assertEqual(digest.source_label, "SEC 10-Q fallback")  # type: ignore[union-attr]
        self.assertEqual(digest.source_kind, "sec_10q_fallback")  # type: ignore[union-attr]
        self.assertEqual(digest.freshness_card_label, "SEC 10-Q analyzed")  # type: ignore[union-attr]
        self.assertIn("Loaded source: SEC 10-Q fallback", rendered)
        self.assertIn("No 8-K earnings-release exhibit found; using recent SEC 10-Q financial statements and MD&A as earnings context", rendered)
        self.assertIn("$44.1 billion", rendered)
        self.assertIn("Diluted earnings per share was $0.76", rendered)
        self.assertIn("Gross margin was 60.5%", rendered)
        self.assertIn("Net income was $18.8 billion", rendered)
        self.assertIn("Data Center platform revenue", rendered)
        self.assertIn("growth was driven by", rendered)
        self.assertIn("net cash provided by operating activities", rendered)
        self.assertIn("share repurchases", rendered)
        self.assertIn("export controls", rendered)
        self.assertNotIn("earnings source unavailable", rendered.lower())
        self.assertLess(score_earnings_risk(rendered), 50.0)

    def test_schwab_refresh_path_uses_10q_when_no_8k_exists(self) -> None:
        from app.analytics.stock_research import DataSourceStatus
        from app.ui import schwab_research_workspace_extension as workspace

        report = _sec_10q_report()
        fake_client = _No8KFakeSecClient(report)
        calendar_status = DataSourceStatus("Upcoming earnings calendar", "no upcoming event", "2026-06-07 12:00 UTC", "No upcoming event in deterministic test.")

        with patch.object(workspace, "_upcoming_earnings_calendar_status", return_value=calendar_status), patch.object(workspace, "fetch_earnings_calendar_event", return_value=None):
            earnings_text, _fundamentals_text, filings_lines, statuses = workspace._fetch_us_domestic_sec_layers("NVDA", fake_client)

        self.assertIn("Loaded source: SEC 10-Q fallback", earnings_text)
        self.assertIn("using recent SEC 10-Q financial statements and MD&A as earnings context", earnings_text)
        self.assertTrue(any(line.startswith("10-Q filed 2026-05-28") for line in filings_lines))
        self.assertTrue(any(status.source == "Recent EDGAR earnings" and status.status == "fallback" for status in statuses))

    def test_cache_read_write_round_trip(self) -> None:
        recent = _recent_record()
        upcoming = UpcomingEarningsRecord("ACME", "Acme Corp", "2026-07-21", "2026-06-30", 1.25, "USD", "Alpha Vantage")
        snapshot = EarningsRadarSnapshot(
            recent=(recent,),
            upcoming=(upcoming,),
            fetched_at="2026-06-06 12:00 UTC",
            sources=("SEC EDGAR", "Alpha Vantage"),
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            store = EarningsRadarStore(tmp_dir)
            store.save(snapshot)
            loaded = store.load()

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.recent[0], recent)  # type: ignore[union-attr]
        self.assertEqual(loaded.upcoming[0], upcoming)  # type: ignore[union-attr]
        self.assertTrue(loaded.used_cache)  # type: ignore[union-attr]

    def test_filter_recent_records(self) -> None:
        acme = _recent_record(company="Acme Corp", ticker="ACME", guidance=True, risk_flags=("Revenue decline",))
        beta = _recent_record(company="Beta Inc", ticker="BETA", filed_date="2026-06-01", guidance=False, risk_flags=(), exhibit_url=None)

        self.assertEqual(filter_recent_earnings_records([acme, beta], search="acme"), [acme])
        self.assertEqual(filter_recent_earnings_records([acme, beta], guidance=True), [acme])
        self.assertEqual(filter_recent_earnings_records([acme, beta], risk_flag="Revenue decline"), [acme])
        self.assertEqual(filter_recent_earnings_records([acme, beta], has_exhibit=True), [acme])
        self.assertEqual(filter_recent_earnings_records([acme, beta], date_from="2026-06-03"), [acme])

    def test_filter_upcoming_records(self) -> None:
        acme = UpcomingEarningsRecord("ACME", "Acme Corp", "2026-07-21", "2026-06-30", 1.25, "USD", "Alpha Vantage")
        beta = UpcomingEarningsRecord("BETA", "Beta Inc", "2026-08-05", "2026-06-30", None, "USD", "Alpha Vantage")

        self.assertEqual(filter_upcoming_earnings_records([acme, beta], search="beta"), [beta])
        self.assertEqual(filter_upcoming_earnings_records([acme, beta], symbols=["ACME"]), [acme])
        self.assertEqual(filter_upcoming_earnings_records([acme, beta], has_estimate=True), [acme])
        self.assertEqual(filter_upcoming_earnings_records([acme, beta], date_to="2026-07-31"), [acme])


if __name__ == "__main__":
    unittest.main()
