from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from app.analytics.earnings_pipeline import EARNINGS_DROP_KIND, RecentEarningsRecord
from app.analytics.market_screener import (
    MARKET_SCREENER_AI_SYSTEM_PROMPT,
    MARKET_SCREENER_SOURCE_MODE,
    MarketScreenerRecord,
    OpenAiMarketScreenerClient,
    build_market_screener_records,
    fetch_market_screener_snapshot,
    filter_market_screener_records,
    market_screener_ai_request_payload,
    sort_market_screener_records,
)
from app.data.earnings_calendar import MISSING_API_KEY_MESSAGE, UpcomingEarningsRecord
from app.data.market_data_provider import LocalMarketDataFileProvider, MarketQuoteFundamentalsRecord
from app.data.market_universe import MarketUniverseEntry
from app.ui import earnings_radar_extension


def _recent_record() -> RecentEarningsRecord:
    return RecentEarningsRecord(
        cik="0000000001",
        company_name="Acme Corp",
        ticker="ACME",
        form="8-K",
        items="2.02",
        filed_date="2026-06-05",
        acceptance_datetime="2026-06-05T16:01:00",
        report_date="2026-03-31",
        fiscal_period="First quarter 2026",
        sector="Technology",
        industry="Services-Prepackaged Software",
        sic="7372",
        exchange="Nasdaq",
        release_title="Acme Reports Results",
        revenue=123_400_000.0,
        revenue_growth=-4.0,
        eps=0.45,
        net_income=20_000_000.0,
        guidance_flag=True,
        risk_flags=("Revenue decline",),
        filing_url="https://example.test/acme-8k.htm",
        exhibit_url="https://example.test/acme-ex99.htm",
        accession_number="0000000001-26-000001",
        filing_type=EARNINGS_DROP_KIND,
        source_excerpt="Revenue fell because demand softened. API key sk-testsecret123456 should not leak.",
    )


class _FakeMissingUpcomingProvider:
    last_status = MISSING_API_KEY_MESSAGE

    def upcoming_earnings(self, **_kwargs):
        return []


class _FailingSecClient:
    def _fetch_json(self, *_args, **_kwargs):
        raise RuntimeError("SEC offline")


class _FakeResponses:
    def __init__(self, answer: str = "Selected screener row analysis only.") -> None:
        self.answer = answer
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.answer, id=f"screener_resp_{len(self.calls)}")


class _FakeOpenAiClient:
    def __init__(self, answer: str = "Selected screener row analysis only.") -> None:
        self.responses = _FakeResponses(answer)


def test_market_screener_merge_combines_universe_recent_and_upcoming_rows() -> None:
    records = build_market_screener_records(
        [
            MarketUniverseEntry("ACME", "Acme Corp", cik="0000000001", exchange="Nasdaq", sector="Technology"),
            MarketUniverseEntry("BETA", "Beta Inc", exchange="NYSE", sector="Industrials"),
        ],
        [_recent_record()],
        [UpcomingEarningsRecord("ACME", "Acme Corp", "2026-07-21", "2026-06-30", 1.25, "USD", "Alpha Vantage", "https://example.test/calendar")],
        supplemental_records=[MarketScreenerRecord("ACME", price=120.5, volume=2_000_000, avg_volume=1_000_000, change_percent=6.2, signals=("Schwab holding",), sources=("Local app holdings",))],
        fetched_at="2026-06-13T12:00:00+00:00",
    )

    acme = next(record for record in records if record.symbol == "ACME")
    beta = next(record for record in records if record.symbol == "BETA")

    assert acme.company_name == "Acme Corp"
    assert acme.exchange == "Nasdaq"
    assert acme.price == 120.5
    assert acme.next_earnings_date == "2026-07-21"
    assert acme.recent_filing_date == "2026-06-05"
    assert acme.eps == 0.45
    assert acme.revenue_growth == -4.0
    assert "Guidance mentioned" in acme.signals
    assert "Schwab holding" in acme.signals
    assert "Revenue decline" in acme.risk_flags
    assert "SEC EDGAR" in acme.sources
    assert "Alpha Vantage" in acme.sources
    assert beta.next_earnings_date is None


def test_market_screener_quote_fundamental_records_merge_correctly() -> None:
    records = build_market_screener_records(
        [MarketUniverseEntry("ACME", "Acme Corp", exchange="Nasdaq")],
        market_data_records=[
            MarketQuoteFundamentalsRecord(
                "ACME",
                price=121.25,
                change_percent=5.6,
                volume=2_500_000,
                avg_volume=1_000_000,
                market_cap=12_000_000_000,
                pe_ratio=31.5,
                eps=1.22,
                revenue_growth=14.4,
                source="Local market data file",
                source_url="https://example.test/acme",
                fetched_at="2026-06-14T10:00:00+00:00",
            )
        ],
        fetched_at="2026-06-14T10:00:00+00:00",
    )

    acme = records[0]
    assert acme.price == 121.25
    assert acme.change_percent == 5.6
    assert acme.volume == 2_500_000
    assert acme.avg_volume == 1_000_000
    assert acme.market_cap == 12_000_000_000
    assert acme.pe_ratio == 31.5
    assert acme.eps == 1.22
    assert acme.revenue_growth == 14.4
    assert "High volume" in acme.signals
    assert "Mover" in acme.signals
    assert "Local market data file" in acme.sources
    assert "https://example.test/acme" in acme.source_links


def test_local_market_data_file_provider_loads_capped_requested_symbols(tmp_path) -> None:
    path = tmp_path / "market-data.csv"
    path.write_text(
        "symbol,price,change_percent,volume,avg_volume,market_cap,pe_ratio,eps,revenue_growth,source,source_url\n"
        "ACME,121.25,5.6,2500000,1000000,12000000000,31.5,1.22,14.4,Fixture,https://example.test/acme\n"
        "BETA,8.5,0.4,1000,2000,,,,,Fixture,https://example.test/beta\n",
        encoding="utf-8",
    )
    provider = LocalMarketDataFileProvider(path)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=1)

    assert len(snapshot.records) == 1
    assert snapshot.records[0].symbol == "ACME"
    assert snapshot.records[0].price == 121.25
    assert snapshot.records[0].market_cap == 12_000_000_000
    assert snapshot.statuses[0].status == "available"


def test_market_screener_filters_and_sorting_cover_events_risks_windows_and_movers() -> None:
    acme = MarketScreenerRecord(
        "ACME",
        "Acme Corp",
        exchange="Nasdaq",
        sector="Technology",
        volume=2_000_000,
        avg_volume=1_000_000,
        change_percent=6.2,
        next_earnings_date="2026-06-20",
        recent_filing_date="2026-06-05",
        signals=("Upcoming earnings", "Guidance mentioned"),
        risk_flags=("Revenue decline",),
        sources=("SEC EDGAR",),
    )
    beta = MarketScreenerRecord("BETA", "Beta Inc", exchange="NYSE", sector="Industrials", sources=("SEC company_tickers.json",))

    assert filter_market_screener_records([acme, beta], search="acme") == [acme]
    assert filter_market_screener_records([acme, beta], sector="Technology") == [acme]
    assert filter_market_screener_records([acme, beta], event_type="Upcoming earnings") == [acme]
    assert filter_market_screener_records([acme, beta], event_type="Guidance mentioned") == [acme]
    assert filter_market_screener_records([acme, beta], event_type="High volume / mover") == [acme]
    assert filter_market_screener_records([acme, beta], risk_flag="Any risk flag") == [acme]
    assert filter_market_screener_records([acme, beta], earnings_date_window="Next 7 days", today=date(2026, 6, 13)) == [acme]
    assert filter_market_screener_records([acme, beta], has_ai_signal=True) == [acme]
    assert sort_market_screener_records([beta, acme], "change_percent", descending=True) == [acme, beta]


def test_market_screener_ui_values_handle_missing_numeric_fields() -> None:
    values = earnings_radar_extension._screener_values(MarketScreenerRecord("ACME", "Acme Corp"))

    assert values[0] == "ACME"
    assert values[1] == "Acme Corp"
    assert "--" in values
    assert "Not extracted" not in values


def test_high_volume_mover_filter_requires_actual_market_data() -> None:
    no_market_data = MarketScreenerRecord("ACME", "Acme Corp")
    changed_without_volume = MarketScreenerRecord("BETA", "Beta Inc", change_percent=None, volume=None, avg_volume=None)
    mover = MarketScreenerRecord("GAMMA", "Gamma Inc", change_percent=-5.1)
    high_volume = MarketScreenerRecord("DELTA", "Delta Inc", volume=2_000_000, avg_volume=1_000_000)

    assert filter_market_screener_records(
        [no_market_data, changed_without_volume, mover, high_volume],
        event_type="High volume / mover",
    ) == [mover, high_volume]


def test_market_screener_missing_providers_degrade_to_fallback_and_status_messages() -> None:
    snapshot = fetch_market_screener_snapshot(
        sec_client=_FailingSecClient(),
        recent_records=(),
        upcoming_provider=_FakeMissingUpcomingProvider(),
        universe_limit=4,
        include_fallback_universe=True,
    )

    assert snapshot.records
    assert any(status.source == "SEC company_tickers.json" and status.status == "unavailable" for status in snapshot.statuses)
    assert any(status.source == "Built-in fallback universe" and status.status == "fallback" for status in snapshot.statuses)
    assert any(status.source == "Upcoming earnings calendar" and status.status == "unavailable" for status in snapshot.statuses)
    assert any(status.source == "Market quote/fundamental metrics" and status.status == "unavailable" for status in snapshot.statuses)


def test_market_screener_ai_payload_is_grounded_in_selected_row_and_marks_missing_metrics() -> None:
    record = MarketScreenerRecord(
        "ACME",
        "Acme Corp",
        sector="Technology",
        next_earnings_date="2026-07-21",
        signals=("Upcoming earnings",),
        sources=("Alpha Vantage",),
        source_links=("https://example.test/calendar",),
        source_excerpt="Upcoming report date from provider. Secret sk-testsecret123456 should be redacted.",
    )

    payload = market_screener_ai_request_payload(record, "Why is this interesting?", timeout_seconds=44)
    selected = payload["selected_market_screener_record"]

    assert payload["question"] == "Why is this interesting?"
    assert selected["symbol"] == "ACME"
    assert selected["event_fields"]["next_earnings_date"] == "2026-07-21"
    assert selected["market_data"]["price"]["status"] == "Not available in the selected Market Intelligence Screener row."
    assert selected["fundamental_fields"]["eps"]["status"] == "Not available in the selected Market Intelligence Screener row."
    assert "price" in selected["missing_fields"]
    assert "buy, sell, or hold" in " ".join(payload["grounding_rules"])
    assert "sk-[REDACTED]" in payload["source_snippets"][0]["text"]
    assert "sk-testsecret" not in json.dumps(payload)


def test_market_screener_ai_client_uses_responses_api_store_false_and_timeout() -> None:
    record = MarketScreenerRecord("ACME", "Acme Corp", signals=("Recent SEC filing",), sources=("SEC EDGAR",))
    fake_openai = _FakeOpenAiClient(answer="Research-only selected-row answer.")
    client = OpenAiMarketScreenerClient(openai_client=fake_openai, model="gpt-test", timeout_seconds=22)

    response = client.analyze(record, "Explain the row.")

    call = fake_openai.responses.calls[0]
    request_payload = json.loads(call["input"][-1]["content"])
    assert response.response_id == "screener_resp_1"
    assert response.source_mode == MARKET_SCREENER_SOURCE_MODE
    assert call["model"] == "gpt-test"
    assert call["store"] is False
    assert call["timeout"] == 22
    assert call["input"][0]["role"] == "system"
    assert MARKET_SCREENER_AI_SYSTEM_PROMPT in call["input"][0]["content"]
    assert request_payload["selected_market_screener_record"]["symbol"] == "ACME"
    assert "buy, sell, or hold" in " ".join(request_payload["grounding_rules"])
