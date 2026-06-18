from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.analytics.earnings_ai import (
    EARNINGS_AI_SYSTEM_PROMPT,
    OpenAiEarningsRadarClient,
    earnings_ai_request_payload,
)
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
from app.ui import earnings_radar_extension


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
    source_excerpt: str | None = None,
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
        source_excerpt=source_excerpt,
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


class _FakeResponses:
    def __init__(self, answer: str = "Selected-row earnings analysis only.") -> None:
        self.answer = answer
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.answer, id=f"earnings_resp_{len(self.calls)}")


class _FakeOpenAiClient:
    def __init__(self, answer: str = "Selected-row earnings analysis only.") -> None:
        self.responses = _FakeResponses(answer)


class _FakeButton:
    def __init__(self) -> None:
        self.state = ""

    def configure(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]


class _FakeText:
    def __init__(self) -> None:
        self.value = ""
        self.state = ""

    def configure(self, **kwargs) -> None:
        if "state" in kwargs:
            self.state = kwargs["state"]

    def delete(self, *_args) -> None:
        self.value = ""

    def insert(self, _index, value: str) -> None:
        self.value += value


class _FakeTree:
    def __init__(self, selection=("row1",)) -> None:
        self._selection = selection

    def selection(self):
        return self._selection


class _FakeStatus:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


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

    def test_earnings_ai_payload_is_grounded_in_selected_record(self) -> None:
        record = _recent_record(source_excerpt="Revenue fell because demand softened. API key sk-testsecret123456 should not leak.")

        payload = earnings_ai_request_payload(record, "Summarize this row.", source_snippets=(record.source_excerpt or "",), timeout_seconds=33)
        selected = payload["selected_earnings_record"]

        self.assertEqual(payload["question"], "Summarize this row.")
        self.assertEqual(selected["company_name"], "Acme Corp")
        self.assertEqual(selected["ticker"], "ACME")
        self.assertEqual(selected["form"], "8-K")
        self.assertEqual(selected["filing_type"], EARNINGS_DROP_KIND)
        self.assertEqual(selected["parsed_metrics"]["revenue"], 123_400_000.0)
        self.assertEqual(selected["guidance"]["mentioned"], True)
        self.assertEqual(selected["risk_flags"], ["Revenue decline"])
        self.assertEqual(selected["source_label"], "SEC EDGAR")
        self.assertEqual(selected["accession_number"], "0000000001-26-000001")
        self.assertEqual(payload["request_budget"]["openai_timeout_seconds"], 33)
        self.assertIn("selected Earnings Radar record", " ".join(payload["grounding_rules"]))
        self.assertIn("sk-[REDACTED]", payload["source_snippets"][0]["text"])
        self.assertNotIn("sk-testsecret", json.dumps(payload))

    def test_earnings_ai_payload_marks_missing_data_explicitly(self) -> None:
        record = RecentEarningsRecord(
            cik="0000000099",
            company_name="Sparse Corp",
            ticker=None,
            form="10-Q",
            items="Formal report",
            filed_date="2026-06-01",
            acceptance_datetime="2026-06-01T18:00:00",
            report_date=None,
            fiscal_period=None,
            sector=None,
            industry=None,
            sic=None,
            exchange=None,
            release_title=None,
            revenue=None,
            revenue_growth=None,
            eps=None,
            net_income=None,
            guidance_flag=False,
            risk_flags=(),
            filing_url="https://example.test/sparse-10q.htm",
            exhibit_url=None,
            accession_number="0000000099-26-000001",
            filing_type=FORMAL_REPORT_KIND,
        )

        payload = earnings_ai_request_payload(record, "What changed?", timeout_seconds=120)
        selected = payload["selected_earnings_record"]

        self.assertEqual(selected["ticker"]["status"], "Not available in the selected Earnings Radar record.")
        self.assertEqual(selected["parsed_metrics"]["revenue"]["status"], "Not available in the selected Earnings Radar record.")
        self.assertEqual(selected["risk_flags"]["reason"], "No risk flags detected in parsed row.")
        self.assertEqual(payload["source_snippets"]["reason"], "No source text snippet is available in the selected Earnings Radar record.")
        self.assertIn("ticker", selected["missing_fields"])
        self.assertIn("source_excerpt", selected["missing_fields"])

    def test_earnings_ai_client_uses_responses_api_store_false_and_timeout(self) -> None:
        record = _recent_record(source_excerpt="Revenue declined 4%.")
        fake_openai = _FakeOpenAiClient(answer="Research-only selected-row answer.")
        client = OpenAiEarningsRadarClient(openai_client=fake_openai, model="gpt-test", timeout_seconds=22)

        response = client.analyze(record, "Guidance and risks?")

        call = fake_openai.responses.calls[0]
        request_payload = json.loads(call["input"][-1]["content"])
        self.assertEqual(response.response_id, "earnings_resp_1")
        self.assertEqual(response.source_mode, "earnings_radar_selected_record")
        self.assertEqual(call["model"], "gpt-test")
        self.assertEqual(call["store"], False)
        self.assertEqual(call["timeout"], 22)
        self.assertEqual(call["input"][0]["role"], "system")
        self.assertIn(EARNINGS_AI_SYSTEM_PROMPT, call["input"][0]["content"])
        self.assertEqual(request_payload["selected_earnings_record"]["ticker"], "ACME")
        self.assertIn("buy, sell, or hold", " ".join(request_payload["grounding_rules"]))

    def test_earnings_radar_selection_enables_selected_row_ai_actions(self) -> None:
        record = _recent_record()
        buttons = [_FakeButton(), _FakeButton(), _FakeButton(), _FakeButton()]
        detail = _FakeText()
        app = SimpleNamespace(
            earnings_recent_table=_FakeTree(),
            earnings_recent_row_map={"row1": record},
            earnings_ai_status_var=_FakeStatus(),
            earnings_recent_detail_text=detail,
            _earnings_ai_running=False,
            earnings_ai_analyze_button=buttons[0],
            earnings_ai_summarize_button=buttons[1],
            earnings_ai_symbol_chat_button=buttons[2],
            earnings_ai_quick_buttons=[buttons[3]],
        )

        earnings_radar_extension._on_recent_selection_changed(app)

        self.assertIs(app.earnings_recent_selected_record, record)
        self.assertIn("Selected ACME", app.earnings_ai_status_var.value)
        self.assertIn("Revenue", detail.value)
        self.assertTrue(all(button.state == "normal" for button in buttons))

        app.earnings_recent_table = _FakeTree(selection=())
        earnings_radar_extension._on_recent_selection_changed(app)

        self.assertIsNone(app.earnings_recent_selected_record)
        self.assertIn("Select a recent earnings row", app.earnings_ai_status_var.value)
        self.assertTrue(all(button.state == "disabled" for button in buttons))

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

    def test_schwab_fundamentals_use_fmp_cards_before_sec_companyfacts_when_sufficient(self) -> None:
        from app.analytics.research_workspace_insights import build_fundamental_metric_cards
        from app.analytics.stock_research import DataSourceStatus
        from app.data.market_data_provider import MarketDataProviderStatus, MarketQuoteFundamentalsRecord, MarketQuoteFundamentalsSnapshot
        from app.ui import schwab_research_workspace_extension as workspace

        class _FakeFmpProvider:
            def profile_classification(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
                return MarketQuoteFundamentalsSnapshot(
                    records=(MarketQuoteFundamentalsRecord("NVDA", company_name="NVIDIA Corp", exchange="NASDAQ", sector="Technology", industry="Semiconductors", cik="0001045810", source="FMP profile"),),
                    fetched_at="2026-06-17T10:00:00+00:00",
                    statuses=(MarketDataProviderStatus("FMP profile/classification", "available", "2026-06-17T10:00:00+00:00", "Loaded profile."),),
                )

            def fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
                return MarketQuoteFundamentalsSnapshot(
                    records=(
                        MarketQuoteFundamentalsRecord(
                            "NVDA",
                            market_cap=3_000_000_000_000,
                            pe_ratio=42.0,
                            eps=2.4,
                            revenue_growth=30.0,
                            revenue=44_100_000_000,
                            net_income=18_800_000_000,
                            operating_income=21_900_000_000,
                            net_income_yoy=22.0,
                            operating_income_yoy=18.0,
                            diluted_eps_yoy=12.0,
                            operating_cash_flow=20_500_000_000,
                            free_cash_flow=19_200_000_000,
                            operating_cash_flow_yoy=16.0,
                            free_cash_flow_yoy=14.0,
                            cash_and_equivalents=53_700_000_000,
                            total_assets=144_000_000_000,
                            total_liabilities=60_000_000_000,
                            total_debt=12_000_000_000,
                            cash_to_liabilities=89.5,
                            liabilities_to_assets=41.7,
                            enterprise_value=3_050_000_000_000,
                            ev_to_sales=19.5,
                            ev_to_ebitda=35.0,
                            price_to_sales=18.8,
                            shares_float=2_400_000_000,
                            shares_outstanding=2_450_000_000,
                            source="FMP fundamentals",
                        ),
                    ),
                    fetched_at="2026-06-17T10:00:00+00:00",
                    statuses=(MarketDataProviderStatus("FMP fundamentals", "available", "2026-06-17T10:00:00+00:00", "Loaded fundamentals."),),
                )

        report = _sec_10q_report()
        fake_client = _No8KFakeSecClient(report)
        calendar_status = DataSourceStatus("Upcoming earnings calendar", "no upcoming event", "2026-06-07 12:00 UTC", "No upcoming event in deterministic test.")

        with patch.object(workspace, "_upcoming_earnings_calendar_status", return_value=calendar_status), patch.object(workspace, "fetch_earnings_calendar_event", return_value=None):
            _earnings_text, fundamentals_text, _filings_lines, statuses = workspace._fetch_us_domestic_sec_layers("NVDA", fake_client, fmp_provider=_FakeFmpProvider())

        self.assertIn("Source: FMP profile/fundamentals", fundamentals_text)
        self.assertTrue(any(status.source == "SEC companyfacts" and status.status == "skipped" for status in statuses))
        cards = {card.title: card for card in build_fundamental_metric_cards(fundamentals_text)}
        self.assertEqual(cards["Revenue Trend"].label, "+30.0%")
        self.assertEqual(cards["Revenue Trend"].status, "good")
        self.assertEqual(cards["Operating Profit"].label, "+18.0%")
        self.assertEqual(cards["Operating Cash Flow"].label, "+16.0%")
        self.assertTrue(cards["Balance Sheet"].label.startswith("Cash 89.5%"))
        self.assertEqual(cards["Valuation"].label, "42.0x")
        self.assertIn("Net income: $18,800,000,000.00", fundamentals_text)
        self.assertIn("EV/EBITDA: 35", fundamentals_text)

    def test_incomplete_fmp_fundamentals_do_not_block_sec_companyfacts_fallback(self) -> None:
        from app.data.market_data_provider import MarketDataProviderStatus, MarketQuoteFundamentalsRecord, MarketQuoteFundamentalsSnapshot
        from app.ui import schwab_research_workspace_extension as workspace

        class _IncompleteFmpProvider:
            def profile_classification(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
                return MarketQuoteFundamentalsSnapshot(
                    records=(MarketQuoteFundamentalsRecord("NVDA", company_name="NVIDIA Corp", source="FMP profile"),),
                    fetched_at="2026-06-17T10:00:00+00:00",
                    statuses=(MarketDataProviderStatus("FMP profile/classification", "available", "2026-06-17T10:00:00+00:00", "Loaded profile."),),
                )

            def fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
                return MarketQuoteFundamentalsSnapshot(
                    records=(),
                    fetched_at="2026-06-17T10:00:00+00:00",
                    statuses=(MarketDataProviderStatus("FMP fundamentals", "empty", "2026-06-17T10:00:00+00:00", "No fundamentals."),),
                )

        text, statuses = workspace._fetch_fmp_fundamentals_text("NVDA", fmp_provider=_IncompleteFmpProvider())

        self.assertIsNone(text)
        self.assertTrue(any(status.source == "FMP profile/fundamentals" and status.status == "fallback" for status in statuses))

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
