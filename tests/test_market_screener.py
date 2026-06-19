from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

from app.analytics.earnings_pipeline import EARNINGS_DROP_KIND, RecentEarningsRecord
from app.analytics.market_screener import (
    ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT,
    ASK_SCREENER_PROVIDER_ACTION_ENRICH_THEN_EXECUTE,
    ASK_SCREENER_PROVIDER_ACTION_MISSING_CONFIG,
    AskScreenerPlanValidationError,
    AskScreenerPlannerError,
    AskScreenerProviderConfig,
    OpenAiAskScreenerPlannerClient,
    MARKET_SCREENER_AI_SYSTEM_PROMPT,
    MARKET_SCREENER_ENRICH_ALL_MARKET_DATA,
    MARKET_SCREENER_SOURCE_MODE,
    MarketScreenerBackfillConfig,
    MarketScreenerCoverageDiagnostics,
    MarketScreenerRecord,
    MarketScreenerSnapshot,
    MarketScreenerSourceStatus,
    OpenAiMarketScreenerClient,
    analyze_ask_screener_field_coverage,
    ask_screener_enrichment_decision,
    ask_screener_planner_request_payload,
    build_market_screener_records,
    execute_ask_screener_plan,
    execute_provider_aware_ask_screener_plan,
    enrich_market_screener_records,
    fetch_market_screener_snapshot,
    filter_market_screener_records,
    market_screener_ai_request_payload,
    market_screener_data_completeness,
    market_screener_data_completeness_score,
    market_screener_data_label,
    market_screener_diagnostics_summary,
    market_screener_is_my_holding,
    market_screener_major_cap_diagnostic_lines,
    market_screener_market_cap_rank,
    market_screener_record_has_quote_fields,
    merge_market_data_records_into_screener_records,
    parse_ask_screener_fallback,
    sort_market_screener_records,
    validate_ask_screener_plan,
)
from app.data.earnings_calendar import MISSING_API_KEY_MESSAGE, UpcomingEarningsRecord
from app.data.market_data_provider import CompositeMarketDataProvider, FmpQuoteFundamentalsProvider, LocalMarketDataFileProvider, MarketDataProviderStatus, MarketQuoteFundamentalsRecord, MarketQuoteFundamentalsSnapshot
from app.data.market_universe import MarketUniverseEntry, MarketUniverseSnapshot
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


class _FakeSecIdentityClient:
    cache_dir = "unused"

    def __init__(self, *, ticker_payload=None, submissions_by_cik=None) -> None:
        self.ticker_payload = ticker_payload if ticker_payload is not None else {}
        self.submissions_by_cik = submissions_by_cik or {}

    def _fetch_json(self, url, **_kwargs):
        if "company_tickers" in str(url):
            return self.ticker_payload
        return {}

    def get_submissions_by_cik(self, cik):
        return self.submissions_by_cik.get(str(cik).zfill(10), {})


class _DiagnosticMarketDataProvider:
    provider_name = "diagnostic_market_data"

    def __init__(self, snapshot: MarketQuoteFundamentalsSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[tuple[str, ...], int, bool]] = []

    def quote_fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
        self.calls.append((tuple(symbols), max_symbols, force_refresh))
        return self.snapshot

    def quote_fundamentals_by_cik(self, ciks, *, force_refresh: bool = False, max_symbols: int = 50):
        return MarketQuoteFundamentalsSnapshot(
            records=(),
            fetched_at=self.snapshot.fetched_at,
            statuses=(),
        )


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


class _FakeTkVar:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def set(self, value: object) -> None:
        self.value = value

    def get(self):
        return self.value


def _fake_screener_filter_app(
    records,
    *,
    sort_column: str | None = None,
    sort_desc: bool | None = None,
    page_size: str = "100",
) -> SimpleNamespace:
    return SimpleNamespace(
        market_screener_records=list(records),
        market_screener_filtered_records=[],
        market_screener_row_map={},
        market_screener_sort_column=sort_column or earnings_radar_extension.DEFAULT_MARKET_SCREENER_SORT_COLUMN,
        market_screener_sort_desc=earnings_radar_extension.DEFAULT_MARKET_SCREENER_SORT_DESC if sort_desc is None else sort_desc,
        market_screener_page=0,
        market_screener_search_var=_FakeTkVar(""),
        market_screener_sector_var=_FakeTkVar("All"),
        market_screener_exchange_var=_FakeTkVar("All"),
        market_screener_event_type_var=_FakeTkVar("All"),
        market_screener_risk_flag_var=_FakeTkVar("All"),
        market_screener_earnings_window_var=_FakeTkVar("All"),
        market_screener_data_completeness_var=_FakeTkVar("All"),
        market_screener_has_ai_signal_var=_FakeTkVar(False),
        market_screener_has_price_volume_data_var=_FakeTkVar(False),
        market_screener_page_size_var=_FakeTkVar(page_size),
        market_screener_ask_plan=None,
        market_screener_ask_result=None,
        market_screener_ask_summary_var=_FakeTkVar(),
        market_screener_empty_state_text="",
        market_screener_last_snapshot=None,
        market_screener_market_data_running_symbols=set(),
        market_screener_market_data_attempted_symbols=set(),
        market_screener_status_var=_FakeTkVar(),
    )


class _FakeTextWidget:
    def __init__(self) -> None:
        self.content = ""
        self.options: dict[str, object] = {}

    def configure(self, **kwargs) -> None:
        self.options.update(kwargs)

    def delete(self, *_args) -> None:
        self.content = ""

    def insert(self, _index, text: str) -> None:
        self.content += text


class _FakeButton:
    def __init__(self) -> None:
        self.options: dict[str, object] = {}

    def configure(self, **kwargs) -> None:
        self.options.update(kwargs)


class _FakeTree:
    def __init__(self, selection=()) -> None:
        self._selection = tuple(selection)

    def selection(self):
        return self._selection

    def select(self, *iids: str) -> None:
        self._selection = tuple(iids)


class _FakeCanvas:
    def __init__(self, width: int = 900) -> None:
        self.width = width
        self.calls: list[tuple[str, tuple, dict]] = []
        self.visible = True
        self.grid_calls: list[tuple[str, tuple, dict]] = []

    def delete(self, *args) -> None:
        self.calls.append(("delete", args, {}))

    def winfo_width(self) -> int:
        return self.width

    def grid(self, *args, **kwargs) -> None:
        self.visible = True
        self.grid_calls.append(("grid", args, kwargs))

    def grid_remove(self) -> None:
        self.visible = False
        self.grid_calls.append(("grid_remove", (), {}))

    def create_text(self, *args, **kwargs) -> int:
        self.calls.append(("text", args, kwargs))
        return len(self.calls)

    def create_rectangle(self, *args, **kwargs) -> int:
        self.calls.append(("rectangle", args, kwargs))
        return len(self.calls)


class _FakeMarketDataProvider:
    provider_name = "fake_market_data"

    def __init__(self, records_by_symbol: dict[str, MarketQuoteFundamentalsRecord]) -> None:
        self.records_by_symbol = records_by_symbol
        self.calls: list[tuple[tuple[str, ...], int, bool]] = []

    def quote_fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
        requested = tuple(symbols)[:max_symbols]
        self.calls.append((requested, max_symbols, force_refresh))
        records = tuple(self.records_by_symbol[symbol] for symbol in requested if symbol in self.records_by_symbol)
        return MarketQuoteFundamentalsSnapshot(
            records=records,
            fetched_at="2026-06-14T10:00:00+00:00",
            statuses=(
                MarketDataProviderStatus(
                    "Fake quote",
                    "available" if records else "empty",
                    "2026-06-14T10:00:00+00:00",
                    f"Loaded {len(records)} fake quote row(s).",
                ),
            ),
        )


class _FakeFilingMarketDataProvider(_FakeMarketDataProvider):
    def __init__(
        self,
        records_by_symbol: dict[str, MarketQuoteFundamentalsRecord],
        filings_by_symbol: dict[str, tuple[dict[str, object], ...]],
        filing_errors_by_symbol: dict[str, str] | None = None,
    ) -> None:
        super().__init__(records_by_symbol)
        self.filings_by_symbol = filings_by_symbol
        self.filing_errors_by_symbol = filing_errors_by_symbol or {}
        self.filing_calls: list[tuple[str, bool, int]] = []

    def sec_filings(self, symbol: str, *, force_refresh: bool = False, limit: int = 12):
        self.filing_calls.append((symbol, force_refresh, limit))
        error = self.filing_errors_by_symbol.get(symbol.upper())
        if error:
            raise RuntimeError(error)
        return self.filings_by_symbol.get(symbol.upper(), ())


class _FakeFmpResponse:
    def __init__(self, status_code: int, payload) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakeFmpSession:
    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls: list[dict[str, object]] = []

    def get(self, url, *, params=None, headers=None, timeout=None):
        call = {"url": url, "params": dict(params or {}), "headers": dict(headers or {}), "timeout": timeout}
        self.calls.append(call)
        return self.handler(url, call["params"])


def test_market_screener_default_sort_initializes_to_market_cap_desc(monkeypatch) -> None:
    monkeypatch.setattr(earnings_radar_extension.tk, "StringVar", _FakeTkVar)
    monkeypatch.setattr(earnings_radar_extension.tk, "BooleanVar", _FakeTkVar)
    app = SimpleNamespace()

    earnings_radar_extension._ensure_state(app)

    assert app.market_screener_sort_column == "market_cap"
    assert app.market_screener_sort_desc is True


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


def test_market_screener_initial_market_data_enrichment_respects_cap_and_reports_scope() -> None:
    universe = MarketUniverseSnapshot(
        records=tuple(MarketUniverseEntry(symbol, f"{symbol} Corp") for symbol in ("ACME", "BETA", "GAMMA", "DELTA")),
        fetched_at="2026-06-14T10:00:00+00:00",
        sources=("Fixture",),
        statuses=(),
    )
    provider = _FakeMarketDataProvider(
        {
            "ACME": MarketQuoteFundamentalsRecord("ACME", price=10.0, volume=1000, source="Fake quote"),
            "BETA": MarketQuoteFundamentalsRecord("BETA", price=20.0, volume=2000, source="Fake quote"),
            "GAMMA": MarketQuoteFundamentalsRecord("GAMMA", price=30.0, volume=3000, source="Fake quote"),
        }
    )

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=universe,
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=2,
    )

    assert provider.calls == [(("ACME", "BETA"), 2, False)]
    assert {record.symbol for record in snapshot.records if record.price is not None} == {"ACME", "BETA"}
    status = next(status for status in snapshot.statuses if status.source == "Market data enrichment")
    assert status.status == "partial"
    assert "Market data: enriched 2 of 4 rows via Fake quote" in status.message
    assert "MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT up to 100" in status.message


def test_market_screener_sec_filings_by_symbol_fallback_merges_metadata_and_completeness(monkeypatch) -> None:
    monkeypatch.setenv("FMP_API_KEY", "fmp-secret-filing-key")
    universe = MarketUniverseSnapshot(
        records=(
            MarketUniverseEntry("ACME", "Acme Corp", exchange="NASDAQ", sector="Technology", industry="Software"),
            MarketUniverseEntry("BETA", "Beta Corp", exchange="NYSE", sector="Industrials", industry="Machinery"),
        ),
        fetched_at="2026-06-14T10:00:00+00:00",
        sources=("Fixture",),
        statuses=(),
    )
    provider = _FakeFilingMarketDataProvider(
        {"ACME": MarketQuoteFundamentalsRecord("ACME", price=10.0, volume=1000, source="Fake quote")},
        {
            "ACME": (
                {
                    "symbol": "ACME",
                    "companyName": "Acme Corp",
                    "cik": "1234567",
                    "form": "10-Q",
                    "filingDate": "2026-06-12",
                    "finalLink": "https://www.sec.gov/Archives/edgar/data/1234567/acme-10q.htm",
                    "source": "FMP filing metadata",
                },
            )
        },
        {"BETA": "FMP filing error with fmp-secret-filing-key and apikey=fmp-secret-filing-key"},
    )

    snapshot = fetch_market_screener_snapshot(
        sec_client=_FakeSecIdentityClient(ticker_payload={}),
        universe_snapshot=universe,
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=2,
    )

    record = next(row for row in snapshot.records if row.symbol == "ACME")
    assert provider.filing_calls == [("ACME", False, 1), ("BETA", False, 1)]
    assert record.recent_filing_date == "2026-06-12"
    assert record.recent_filing_type == "10-Q"
    assert "FMP filing metadata" in record.sources
    assert snapshot.diagnostics.rows_enriched_by_fmp_sec_filings == 1
    assert any(status.source == "FMP SEC filings by symbol" and status.status == "available" for status in snapshot.statuses)
    provenance = {(row.field, row.source) for row in record.field_provenance}
    assert ("recent_filing_date", "FMP filing metadata") in provenance
    missing_families = market_screener_data_completeness(record)["missing_source_families"]
    assert "SEC/FMP filings" not in missing_families
    assert "fundamentals" in missing_families
    combined_errors = " ".join(snapshot.errors)
    assert "fmp-secret-filing-key" not in combined_errors
    assert "[REDACTED]" in combined_errors


def test_market_screener_startup_candidate_selection_uses_broad_universe_before_holdings() -> None:
    universe = MarketUniverseSnapshot(
        records=tuple(MarketUniverseEntry(symbol, f"{symbol} Corp") for symbol in ("ACME", "BETA", "GAMMA")),
        fetched_at="2026-06-14T10:00:00+00:00",
        sources=("Fixture",),
        statuses=(),
    )
    provider = _FakeMarketDataProvider(
        {
            "ACME": MarketQuoteFundamentalsRecord("ACME", market_cap=3_000_000_000, source="Fake quote"),
            "BETA": MarketQuoteFundamentalsRecord("BETA", market_cap=2_000_000_000, source="Fake quote"),
            "AAPL": MarketQuoteFundamentalsRecord("AAPL", market_cap=4_000_000_000_000, source="Fake quote"),
        }
    )

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=universe,
        recent_records=(),
        upcoming_records=(),
        supplemental_records=[
            MarketScreenerRecord(
                "AAPL",
                "Apple Inc.",
                signals=("Schwab holding",),
                sources=("Local app holdings",),
                portfolio_quantity=10,
            )
        ],
        market_data_provider=provider,
        market_data_symbol_limit=2,
    )

    assert provider.calls == [(("ACME", "BETA"), 2, False)]
    aapl = next(record for record in snapshot.records if record.symbol == "AAPL")
    assert market_screener_is_my_holding(aapl) is True
    assert aapl.market_cap is None


def test_market_screener_startup_major_cap_universe_rows_lead_global_candidates() -> None:
    universe = MarketUniverseSnapshot(
        records=tuple(MarketUniverseEntry(symbol, f"{symbol} Corp") for symbol in ("ACME", "AAPL", "MSFT", "ZZZZ")),
        fetched_at="2026-06-14T10:00:00+00:00",
        sources=("Fixture",),
        statuses=(),
    )
    provider = _FakeMarketDataProvider(
        {
            "AAPL": MarketQuoteFundamentalsRecord("AAPL", market_cap=3_000_000_000_000, source="Fake quote"),
            "MSFT": MarketQuoteFundamentalsRecord("MSFT", market_cap=3_100_000_000_000, source="Fake quote"),
        }
    )

    fetch_market_screener_snapshot(
        universe_snapshot=universe,
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=2,
    )

    assert provider.calls == [(("MSFT", "AAPL"), 2, False)]


def test_market_screener_resolves_cik_only_filing_row_through_sec_mapping() -> None:
    recent = _recent_record()
    recent = RecentEarningsRecord(**{**recent.to_dict(), "ticker": None, "sector": None, "industry": None, "exchange": None})
    sec_client = _FakeSecIdentityClient(
        ticker_payload={"0": {"cik_str": 1, "ticker": "ACME", "title": "Acme Corp"}},
        submissions_by_cik={
            "0000000001": {
                "name": "Acme Corp",
                "tickers": ["ACME"],
                "exchanges": ["Nasdaq"],
                "sic": "7372",
                "sicDescription": "Services-Prepackaged Software",
            }
        },
    )

    snapshot = fetch_market_screener_snapshot(
        sec_client=sec_client,
        universe_snapshot=MarketUniverseSnapshot(records=(), fetched_at="2026-06-14T10:00:00+00:00", sources=(), statuses=()),
        recent_records=[recent],
        upcoming_records=(),
        market_data_records=(),
    )

    assert len(snapshot.records) == 1
    record = snapshot.records[0]
    assert record.symbol == "ACME"
    assert record.cik == "0000000001"
    assert record.exchange == "Nasdaq"
    provenance = {(row.field, row.source) for row in record.field_provenance}
    assert ("symbol", "SEC CIK/ticker identity") in provenance
    assert ("sector", "SEC submissions metadata") in provenance
    assert snapshot.diagnostics.rows_resolved_by_sec_cik_mapping == 1
    assert snapshot.diagnostics.rows_resolved_by_sec_submissions_metadata == 1


def test_market_screener_cik_only_row_can_use_fmp_profile_by_cik_without_symbol_guessing() -> None:
    recent = _recent_record()
    recent = RecentEarningsRecord(**{**recent.to_dict(), "ticker": None, "sector": None, "industry": None, "exchange": None})
    sec_client = _FakeSecIdentityClient(ticker_payload={}, submissions_by_cik={"0000000001": {}})

    class _FmpByCikOnlyProvider:
        provider_name = "fmp_by_cik_only"

        def quote_fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
            assert tuple(symbols) == ()
            return MarketQuoteFundamentalsSnapshot(records=(), fetched_at="2026-06-14T10:00:00+00:00", statuses=())

        def quote_fundamentals_by_cik(self, ciks, *, force_refresh: bool = False, max_symbols: int = 50):
            assert tuple(ciks) == ("0000000001",)
            return MarketQuoteFundamentalsSnapshot(
                records=(
                    MarketQuoteFundamentalsRecord(
                        "ACME",
                        price=25.0,
                        market_cap=2_000_000_000,
                        sector="Technology",
                        industry="Software",
                        source="FMP profile-by-CIK",
                        cik="0000000001",
                    ),
                ),
                fetched_at="2026-06-14T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP profile-by-CIK", "available", "2026-06-14T10:00:00+00:00", "Loaded 1."),),
                diagnostics={"rows_enriched_by_fmp_profile_by_cik": 1},
            )

    snapshot = fetch_market_screener_snapshot(
        sec_client=sec_client,
        universe_snapshot=MarketUniverseSnapshot(records=(), fetched_at="2026-06-14T10:00:00+00:00", sources=(), statuses=()),
        recent_records=[recent],
        upcoming_records=(),
        market_data_provider=_FmpByCikOnlyProvider(),
        market_data_symbol_limit=5,
    )

    record = snapshot.records[0]
    assert record.symbol == "ACME"
    assert record.price == 25.0
    assert record.market_cap == 2_000_000_000
    assert snapshot.diagnostics.rows_enriched_by_fmp_profile_by_cik == 1
    assert snapshot.diagnostics.unresolved_rows == 0


def test_market_screener_diagnostics_count_limits_empty_provider_and_unresolved_rows() -> None:
    provider = _DiagnosticMarketDataProvider(
        MarketQuoteFundamentalsSnapshot(
            records=(MarketQuoteFundamentalsRecord("ACME", price=10.0, source="Schwab quote"),),
            fetched_at="2026-06-14T10:00:00+00:00",
            statuses=(MarketDataProviderStatus("Diagnostic provider", "partial", "2026-06-14T10:00:00+00:00", "One empty and one auth-limited row."),),
            diagnostics={
                "rows_enriched_by_schwab_quote": 1,
                "rows_provider_returned_no_usable_data": 1,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1,
                "rows_skipped_by_configured_symbol_cap": 2,
            },
        )
    )
    snapshot = fetch_market_screener_snapshot(
        sec_client=_FakeSecIdentityClient(ticker_payload={}),
        universe_snapshot=MarketUniverseSnapshot(
            records=tuple(MarketUniverseEntry(symbol, f"{symbol} Corp") for symbol in ("ACME", "BETA", "GAMMA")),
            fetched_at="2026-06-14T10:00:00+00:00",
            sources=("Fixture",),
            statuses=(),
        ),
        recent_records=(),
        upcoming_records=(),
        supplemental_records=[MarketScreenerRecord("", "Mystery Filing", cik="0000009999", sources=("Manual filing",))],
        market_data_provider=provider,
        market_data_symbol_limit=1,
    )

    diagnostics = snapshot.diagnostics
    assert diagnostics.rows_enriched_by_schwab_quote == 1
    assert diagnostics.rows_provider_returned_no_usable_data == 1
    assert diagnostics.rows_blocked_by_provider_plan_rate_auth_limit == 1
    assert diagnostics.rows_skipped_by_configured_symbol_cap >= 2
    assert diagnostics.unresolved_rows == 1
    assert "unresolved" in market_screener_diagnostics_summary(diagnostics)


def test_market_screener_reports_incomplete_global_market_cap_coverage_when_only_holdings_have_caps() -> None:
    snapshot = fetch_market_screener_snapshot(
        sec_client=_FakeSecIdentityClient(ticker_payload={}),
        universe_snapshot=MarketUniverseSnapshot(
            records=(
                MarketUniverseEntry("ACME", "Acme Corp"),
                MarketUniverseEntry("BETA", "Beta Inc"),
            ),
            fetched_at="2026-06-14T10:00:00+00:00",
            sources=("Fixture",),
            statuses=(),
        ),
        recent_records=(),
        upcoming_records=(),
        supplemental_records=[
            MarketScreenerRecord(
                "HOLD",
                "Held Corp",
                signals=("Schwab holding",),
                sources=("Local app holdings",),
                portfolio_quantity=15,
            )
        ],
        market_data_records=(
            MarketQuoteFundamentalsRecord("HOLD", market_cap=1_000_000_000, source="FMP profile"),
        ),
        market_data_symbol_limit=3,
    )

    diagnostics = snapshot.diagnostics
    status = next(status for status in snapshot.statuses if status.source == "Market data enrichment")
    summary = earnings_radar_extension._screener_source_summary_text(snapshot)
    popout = earnings_radar_extension._screener_diagnostics_popout_text(snapshot)

    assert diagnostics.rows_with_market_cap == 1
    assert diagnostics.rows_missing_market_cap == 2
    assert diagnostics.market_cap_coverage_incomplete == 1
    assert "Global market-cap ranking coverage incomplete" in status.message
    assert "only on portfolio/holding rows" in status.message
    assert "market-cap ranking incomplete" in summary
    assert "Rows with market cap: 1/3" in popout
    assert "Global market-cap ranking coverage incomplete before page-1 ranking" in popout


def test_market_screener_does_not_guess_symbol_from_company_name_when_identity_untrusted() -> None:
    recent = _recent_record()
    recent = RecentEarningsRecord(**{**recent.to_dict(), "ticker": None, "cik": "0000009999"})
    sec_client = _FakeSecIdentityClient(
        ticker_payload={"0": {"cik_str": 1, "ticker": "ACME", "title": "Acme Corp"}},
        submissions_by_cik={"0000009999": {"name": "Acme Corp", "tickers": ["ACME", "ACMEB"]}},
    )

    snapshot = fetch_market_screener_snapshot(
        sec_client=sec_client,
        universe_snapshot=MarketUniverseSnapshot(records=(), fetched_at="2026-06-14T10:00:00+00:00", sources=(), statuses=()),
        recent_records=[recent],
        upcoming_records=(),
        market_data_records=(),
    )

    assert len(snapshot.records) == 1
    record = snapshot.records[0]
    assert record.company_name == "Acme Corp"
    assert record.symbol == ""
    assert snapshot.diagnostics.rows_missing_symbol == 1
    assert snapshot.diagnostics.unresolved_rows == 1


def test_market_screener_page_enrichment_merge_updates_existing_rows_without_duplicates() -> None:
    records = [
        MarketScreenerRecord("ACME", "Acme Corp"),
        MarketScreenerRecord("BETA", "Beta Inc"),
    ]

    merged = merge_market_data_records_into_screener_records(
        records,
        [
            MarketQuoteFundamentalsRecord("ACME", price=11.5, volume=1200, source="Fake quote"),
            MarketQuoteFundamentalsRecord("GAMMA", price=99.0, volume=9900, source="Fake quote"),
        ],
        fetched_at="2026-06-14T10:00:00+00:00",
    )

    assert len(merged) == 2
    assert [record.symbol for record in merged] == ["ACME", "BETA"]
    acme = next(record for record in merged if record.symbol == "ACME")
    beta = next(record for record in merged if record.symbol == "BETA")
    assert acme.price == 11.5
    assert acme.volume == 1200
    assert "Fake quote" in acme.sources
    assert beta.price is None


def test_market_screener_selected_row_enrichment_updates_one_symbol() -> None:
    records = [
        MarketScreenerRecord("ACME", "Acme Corp"),
        MarketScreenerRecord("BETA", "Beta Inc"),
    ]

    merged = merge_market_data_records_into_screener_records(
        records,
        [MarketQuoteFundamentalsRecord("BETA", price=22.25, volume=2200, source="Fake quote")],
        fetched_at="2026-06-14T10:00:00+00:00",
    )

    assert next(record for record in merged if record.symbol == "ACME").price is None
    beta = next(record for record in merged if record.symbol == "BETA")
    assert beta.price == 22.25
    assert beta.volume == 2200
    assert market_screener_record_has_quote_fields(beta) is True


def test_market_screener_selected_row_enrichment_finish_updates_snapshot_and_detail(monkeypatch) -> None:
    app = SimpleNamespace(
        market_screener_records=[MarketScreenerRecord("ACME", "Acme Corp"), MarketScreenerRecord("BETA", "Beta Inc")],
        market_screener_market_data_running_symbols={"BETA"},
        market_screener_market_data_attempted_symbols=set(),
        market_screener_status_var=_FakeTkVar(),
    )
    detail_updates: list[MarketScreenerRecord | None] = []
    monkeypatch.setattr(earnings_radar_extension, "_selected_screener_symbol", lambda _app: "BETA")
    monkeypatch.setattr(earnings_radar_extension, "_apply_screener_filters", lambda _app: None)
    monkeypatch.setattr(earnings_radar_extension, "_select_screener_symbol", lambda _app, _symbol: None)
    monkeypatch.setattr(earnings_radar_extension, "_append_screener_market_data_status", lambda _app, _line: None)
    monkeypatch.setattr(earnings_radar_extension, "_update_screener_detail_panel", lambda _app, record: detail_updates.append(record))
    snapshot = MarketQuoteFundamentalsSnapshot(
        records=(MarketQuoteFundamentalsRecord("BETA", price=22.25, volume=2200, source="Fake quote"),),
        fetched_at="2026-06-14T10:00:00+00:00",
        statuses=(MarketDataProviderStatus("Fake quote", "available", "2026-06-14T10:00:00+00:00", "Loaded 1 fake quote row."),),
    )

    earnings_radar_extension._finish_screener_market_data_enrichment(app, ("BETA",), "selected row", snapshot)

    acme = next(record for record in app.market_screener_records if record.symbol == "ACME")
    beta = next(record for record in app.market_screener_records if record.symbol == "BETA")
    assert acme.price is None
    assert beta.price == 22.25
    assert beta.volume == 2200
    assert app.market_screener_market_data_running_symbols == set()
    assert app.market_screener_market_data_attempted_symbols == {"BETA"}
    assert detail_updates[-1] == beta
    assert "selected row updated" in app.market_screener_status_var.value


def test_market_screener_enrichment_resorts_default_market_cap_desc(monkeypatch) -> None:
    app = _fake_screener_filter_app(
        [
            MarketScreenerRecord("ACME", "Acme Corp"),
            MarketScreenerRecord("MISS", "Missing Cap Corp"),
            MarketScreenerRecord("BETA", "Beta Inc"),
        ]
    )
    monkeypatch.setattr(earnings_radar_extension, "_populate_screener_table", lambda _app: None)
    monkeypatch.setattr(earnings_radar_extension, "_draw_screener_chart", lambda _app: None)
    monkeypatch.setattr(earnings_radar_extension, "_selected_screener_symbol", lambda _app: "")
    monkeypatch.setattr(earnings_radar_extension, "_append_screener_market_data_status", lambda _app, _line: None)
    snapshot = MarketQuoteFundamentalsSnapshot(
        records=(
            MarketQuoteFundamentalsRecord("ACME", market_cap=1_000_000_000, source="FMP fundamentals"),
            MarketQuoteFundamentalsRecord("BETA", market_cap=2_000_000_000, source="FMP fundamentals"),
        ),
        fetched_at="2026-06-14T10:00:00+00:00",
        statuses=(MarketDataProviderStatus("FMP fundamentals", "available", "2026-06-14T10:00:00+00:00", "Loaded fundamentals."),),
    )

    earnings_radar_extension._finish_screener_market_data_enrichment(app, ("ACME", "BETA"), "visible page", snapshot)

    assert app.market_screener_sort_column == "market_cap"
    assert app.market_screener_sort_desc is True
    assert [record.symbol for record in app.market_screener_filtered_records] == ["BETA", "ACME", "MISS"]


def test_market_screener_enrichment_preserves_manual_symbol_sort(monkeypatch) -> None:
    app = _fake_screener_filter_app(
        [MarketScreenerRecord("BETA", "Beta Inc"), MarketScreenerRecord("ACME", "Acme Corp")],
        sort_column="symbol",
        sort_desc=False,
    )
    monkeypatch.setattr(earnings_radar_extension, "_populate_screener_table", lambda _app: None)
    monkeypatch.setattr(earnings_radar_extension, "_draw_screener_chart", lambda _app: None)
    monkeypatch.setattr(earnings_radar_extension, "_selected_screener_symbol", lambda _app: "")
    monkeypatch.setattr(earnings_radar_extension, "_append_screener_market_data_status", lambda _app, _line: None)
    snapshot = MarketQuoteFundamentalsSnapshot(
        records=(
            MarketQuoteFundamentalsRecord("BETA", market_cap=2_000_000_000, source="FMP fundamentals"),
            MarketQuoteFundamentalsRecord("ACME", market_cap=1_000_000_000, source="FMP fundamentals"),
        ),
        fetched_at="2026-06-14T10:00:00+00:00",
        statuses=(MarketDataProviderStatus("FMP fundamentals", "available", "2026-06-14T10:00:00+00:00", "Loaded fundamentals."),),
    )

    earnings_radar_extension._finish_screener_market_data_enrichment(app, ("BETA", "ACME"), "visible page", snapshot)

    assert app.market_screener_sort_column == "symbol"
    assert app.market_screener_sort_desc is False
    assert [record.symbol for record in app.market_screener_filtered_records] == ["ACME", "BETA"]


def test_market_screener_selected_row_enrichment_requests_only_selected_symbol(monkeypatch) -> None:
    app = SimpleNamespace(
        market_screener_market_data_running_symbols=set(),
        market_screener_market_data_attempted_symbols=set(),
    )
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_request(_app, symbols, **kwargs) -> None:
        calls.append((tuple(symbols), kwargs))

    monkeypatch.setattr(earnings_radar_extension, "_request_market_data_enrichment", fake_request)

    earnings_radar_extension._request_selected_row_market_data_enrichment(app, MarketScreenerRecord("BETA", "Beta Inc"))

    assert calls == [(("BETA",), {"reason": "selected row", "force_refresh": False, "max_symbols": 1})]


def test_market_screener_visible_page_force_reenrichment_clears_attempted_symbols(monkeypatch) -> None:
    app = SimpleNamespace(
        market_screener_row_map={
            "row_beta": MarketScreenerRecord("BETA", "Beta Inc", price=22.0, volume=2_000, avg_volume=None, change_percent=None)
        },
        market_screener_market_data_running_symbols=set(),
        market_screener_market_data_attempted_symbols={"BETA"},
    )
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_request(_app, symbols, **kwargs) -> None:
        calls.append((tuple(symbols), kwargs))

    monkeypatch.setattr(earnings_radar_extension, "_request_market_data_enrichment", fake_request)

    earnings_radar_extension._request_visible_page_market_data_enrichment(app, force_refresh=False)
    assert calls == []

    earnings_radar_extension._request_visible_page_market_data_enrichment(app, force_refresh=True)

    assert calls == [(("BETA",), {"reason": "current page", "force_refresh": True, "max_symbols": earnings_radar_extension.MARKET_DATA_PAGE_ENRICHMENT_CAP})]
    assert app.market_screener_market_data_attempted_symbols == set()


def test_market_screener_visible_page_enrichment_uses_default_market_cap_sort(monkeypatch) -> None:
    app = _fake_screener_filter_app(
        [
            MarketScreenerRecord("AARD", "Alphabetical First Corp"),
            MarketScreenerRecord("MEGA", "Mega Cap Corp", market_cap=3_000_000_000_000),
            MarketScreenerRecord("MID", "Mid Cap Corp", market_cap=25_000_000_000),
            MarketScreenerRecord("ZZZZ", "Alphabetical Last Corp"),
        ],
        page_size="2",
    )
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_populate(target) -> None:
        target.market_screener_row_map = {
            f"row_{index}": record
            for index, record in enumerate(target.market_screener_filtered_records[:2])
        }

    monkeypatch.setattr(earnings_radar_extension, "_populate_screener_table", fake_populate)
    monkeypatch.setattr(earnings_radar_extension, "_draw_screener_chart", lambda _app: None)
    monkeypatch.setattr(earnings_radar_extension, "_request_market_data_enrichment", lambda _app, symbols, **kwargs: calls.append((tuple(symbols), kwargs)))

    earnings_radar_extension._apply_screener_filters(app)
    earnings_radar_extension._request_visible_page_market_data_enrichment(app, force_refresh=False)

    assert [record.symbol for record in app.market_screener_filtered_records[:4]] == ["MEGA", "MID", "AARD", "ZZZZ"]
    assert calls[0][0] == ("MEGA", "MID")
    assert calls[0][1]["reason"] == "current page"


def test_market_screener_visible_page_enrichment_retries_partial_mover_rows_once(monkeypatch) -> None:
    app = SimpleNamespace(
        market_screener_row_map={
            "row_beta": MarketScreenerRecord("BETA", "Beta Inc", price=22.0, volume=2_000, avg_volume=None, change_percent=None)
        },
        market_screener_market_data_running_symbols=set(),
        market_screener_market_data_attempted_symbols=set(),
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(earnings_radar_extension, "_request_market_data_enrichment", lambda _app, symbols, **_kwargs: calls.append(tuple(symbols)))

    earnings_radar_extension._request_visible_page_market_data_enrichment(app, force_refresh=False)

    assert calls == [("BETA",)]


def test_market_screener_visible_page_enrichment_keeps_price_market_cap_partial_rows_eligible(monkeypatch) -> None:
    partial = MarketScreenerRecord("ACME", "Acme Corp", price=22.0, market_cap=1_000_000_000)
    complete = MarketScreenerRecord(
        "DONE",
        "Done Corp",
        sector="Technology",
        industry="Software",
        price=10.0,
        change_percent=1.0,
        volume=1_000,
        avg_volume=900,
        market_cap=1_000_000_000,
        pe_ratio=20.0,
        eps=0.5,
        revenue_growth=5.0,
        shares_float=10_000_000,
        shares_outstanding=12_000_000,
    )
    assert earnings_radar_extension._market_data_symbols_from_records([partial, complete]) == ("DONE",)
    app = SimpleNamespace(
        market_screener_row_map={"row_acme": partial},
        market_screener_market_data_running_symbols=set(),
        market_screener_market_data_attempted_symbols=set(),
    )
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(earnings_radar_extension, "_request_market_data_enrichment", lambda _app, symbols, **_kwargs: calls.append(tuple(symbols)))

    earnings_radar_extension._request_visible_page_market_data_enrichment(app, force_refresh=False)

    assert calls == [("ACME",)]

    app.market_screener_market_data_attempted_symbols.add("ACME")
    earnings_radar_extension._request_visible_page_market_data_enrichment(app, force_refresh=False)

    assert calls == [("ACME",)]


def test_market_screener_visible_page_enrichment_uses_page_size_above_100(monkeypatch) -> None:
    records = {
        f"row_{index}": MarketScreenerRecord(f"SYM{index:03d}", f"Symbol {index}")
        for index in range(200)
    }
    app = SimpleNamespace(
        market_screener_row_map=records,
        market_screener_market_data_running_symbols=set(),
        market_screener_market_data_attempted_symbols=set(),
        market_screener_page_size_var=_FakeTkVar("200"),
    )
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    monkeypatch.setattr(earnings_radar_extension, "_request_market_data_enrichment", lambda _app, symbols, **kwargs: calls.append((tuple(symbols), kwargs)))

    earnings_radar_extension._request_visible_page_market_data_enrichment(app, force_refresh=False)

    assert len(calls) == 1
    symbols, kwargs = calls[0]
    assert len(symbols) == 200
    assert kwargs["max_symbols"] == 200


def test_market_screener_current_page_enrichment_logs_exact_symbol_snapshot(monkeypatch) -> None:
    app = SimpleNamespace(
        market_screener_row_map={
            "row_msft": MarketScreenerRecord("MSFT", "Microsoft Corp"),
            "row_aapl": MarketScreenerRecord("AAPL", "Apple Inc"),
            "row_sony": MarketScreenerRecord("SONY", "Sony ADR", market_cap=24_000_000_000_000, market_cap_currency="JPY", country="Japan", is_adr=True),
        },
        market_screener_market_data_running_symbols=set(),
        market_screener_market_data_attempted_symbols=set(),
        market_screener_page_size_var=_FakeTkVar("100"),
    )
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    lines: list[str] = []
    monkeypatch.setattr(earnings_radar_extension, "_request_market_data_enrichment", lambda _app, symbols, **kwargs: calls.append((tuple(symbols), kwargs)))
    monkeypatch.setattr(earnings_radar_extension, "_append_screener_market_data_status", lambda _app, line: lines.append(line))
    monkeypatch.setattr(earnings_radar_extension, "configured_market_data_symbol_limit", lambda default=100: 100)

    earnings_radar_extension._request_visible_page_market_data_enrichment(app, force_refresh=False)

    assert app.market_screener_last_enrichment_symbols == ("MSFT", "AAPL", "SONY")
    assert calls[0][0] == ("MSFT", "AAPL", "SONY")
    assert calls[0][1]["reason"] == "current page"
    assert "page_symbols=[MSFT, AAPL, SONY]" in lines[0]
    assert "requesting=[MSFT, AAPL, SONY]" in lines[0]
    assert "Rows may move after enrichment" in lines[0]


def test_market_screener_enrichment_status_reports_post_enrichment_resort(monkeypatch) -> None:
    app = _fake_screener_filter_app(
        [
            MarketScreenerRecord("ACME", "Acme Corp", market_cap=1_000_000_000, market_cap_currency="USD", exchange="NASDAQ"),
            MarketScreenerRecord("BETA", "Beta Inc"),
        ]
    )
    lines: list[str] = []
    monkeypatch.setattr(earnings_radar_extension, "_populate_screener_table", lambda _app: None)
    monkeypatch.setattr(earnings_radar_extension, "_draw_screener_chart", lambda _app: None)
    monkeypatch.setattr(earnings_radar_extension, "_selected_screener_symbol", lambda _app: "")
    monkeypatch.setattr(earnings_radar_extension, "_append_screener_market_data_status", lambda _app, line: lines.append(line))

    earnings_radar_extension._apply_screener_filters(app)
    snapshot = MarketQuoteFundamentalsSnapshot(
        records=(MarketQuoteFundamentalsRecord("BETA", market_cap=2_000_000_000, market_cap_currency="USD", exchange="NASDAQ", source="FMP fundamentals"),),
        fetched_at="2026-06-14T10:00:00+00:00",
        statuses=(MarketDataProviderStatus("FMP fundamentals", "available", "2026-06-14T10:00:00+00:00", "Loaded fundamentals."),),
    )

    earnings_radar_extension._finish_screener_market_data_enrichment(app, ("BETA",), "current page", snapshot)

    assert [record.symbol for record in app.market_screener_filtered_records] == ["BETA", "ACME"]
    assert "requested 1 symbol(s) [BETA]" in lines[-1]
    assert "Post-enrichment resort moved requested row(s): BETA 2->1" in lines[-1]


def test_market_screener_selection_change_refreshes_popout_context(monkeypatch) -> None:
    tree = _FakeTree()
    acme = MarketScreenerRecord("ACME", "Acme Corp", signals=("Upcoming earnings",), sources=("Alpha Vantage",))
    beta = MarketScreenerRecord("BETA", "Beta Inc", risk_flags=("Revenue decline",), sources=("SEC EDGAR",))
    app = SimpleNamespace(
        market_screener_table=tree,
        market_screener_row_map={"row_acme": acme, "row_beta": beta},
        market_screener_ai_status_var=_FakeTkVar(),
        market_screener_detail_text=_FakeTextWidget(),
        _market_screener_ai_running=False,
    )
    enrichment_requests: list[MarketScreenerRecord] = []
    monkeypatch.setattr(earnings_radar_extension, "_request_selected_row_market_data_enrichment", lambda _app, record: enrichment_requests.append(record))

    tree.select("row_acme")
    earnings_radar_extension._on_screener_selection_changed(app)

    assert app.market_screener_selected_record == acme
    assert app.market_screener_ai_status_var.value.startswith("Selected ACME")
    assert "ACME | Acme Corp" in app.market_screener_detail_text.content

    tree.select("row_beta")
    earnings_radar_extension._on_screener_selection_changed(app)

    assert app.market_screener_selected_record == beta
    assert app.market_screener_ai_status_var.value.startswith("Selected BETA")
    assert "BETA | Beta Inc" in app.market_screener_detail_text.content
    assert "ACME | Acme Corp" not in app.market_screener_detail_text.content
    assert enrichment_requests == [acme, beta]


def test_market_screener_row_action_states_disable_invalid_actions() -> None:
    source_button = _FakeButton()
    chat_button = _FakeButton()
    context_button = _FakeButton()
    app = SimpleNamespace(
        market_screener_open_source_button=source_button,
        market_screener_open_symbol_chat_button=chat_button,
        market_screener_context_button=context_button,
    )

    empty = earnings_radar_extension._update_screener_row_action_states(app, None)
    assert empty["open_source_enabled"] is False
    assert source_button.options["state"] == earnings_radar_extension.tk.DISABLED
    assert chat_button.options["state"] == earnings_radar_extension.tk.DISABLED
    assert context_button.options["state"] == earnings_radar_extension.tk.DISABLED

    no_source = MarketScreenerRecord("MSFT", "Microsoft Corp")
    no_source_state = earnings_radar_extension._update_screener_row_action_states(app, no_source)
    assert no_source_state["open_source_enabled"] is False
    assert no_source_state["open_symbol_chat_enabled"] is True
    assert no_source_state["context_enabled"] is True
    assert "source URL unavailable" in no_source_state["summary"]
    assert source_button.options["state"] == earnings_radar_extension.tk.DISABLED
    assert chat_button.options["state"] == earnings_radar_extension.tk.NORMAL
    assert context_button.options["state"] == earnings_radar_extension.tk.NORMAL

    complete = MarketScreenerRecord("MSFT", "Microsoft Corp", source_links=("https://example.test/msft",))
    complete_state = earnings_radar_extension._update_screener_row_action_states(app, complete)
    assert complete_state["summary"] == "Row actions available."
    assert source_button.options["state"] == earnings_radar_extension.tk.NORMAL
    assert chat_button.options["state"] == earnings_radar_extension.tk.NORMAL
    assert context_button.options["state"] == earnings_radar_extension.tk.NORMAL


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


def test_fmp_provider_maps_quote_and_profile_fields_without_key_in_status() -> None:
    api_key = "fmp-secret-key-123"

    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/batch-quote-short"):
            assert "apikey" not in params
            return _FakeFmpResponse(
                200,
                [
                    {
                        "symbol": "ACME",
                        "price": 12.34,
                        "changesPercentage": 3.21,
                        "volume": 1_250_000,
                        "avgVolume": 1_000_000,
                        "marketCap": 2_500_000_000,
                        "pe": 18.5,
                        "EPS": 0.67,
                        "revenueGrowth": 12.4,
                    }
                ],
            )
        if url.endswith("/profile"):
            return _FakeFmpResponse(
                200,
                [
                    {
                        "symbol": "ACME",
                        "sector": "Technology",
                        "industry": "Software",
                        "exchangeShortName": "NASDAQ",
                        "sharesFloat": 120_000_000,
                        "sharesOutstanding": 150_000_000,
                    }
                ],
            )
        raise AssertionError(url)

    session = _FakeFmpSession(handler)
    provider = FmpQuoteFundamentalsProvider(api_key=api_key, session=session, symbol_limit=5)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    assert len(snapshot.records) == 1
    record = snapshot.records[0]
    assert record.symbol == "ACME"
    assert record.price == 12.34
    assert record.change_percent == 3.21
    assert record.volume == 1_250_000
    assert record.avg_volume == 1_000_000
    assert record.market_cap == 2_500_000_000
    assert record.pe_ratio == 18.5
    assert record.eps == 0.67
    assert record.revenue_growth == 12.4
    assert record.sector == "Technology"
    assert record.industry == "Software"
    assert record.exchange == "NASDAQ"
    assert record.shares_float == 120_000_000
    assert record.shares_outstanding == 150_000_000
    assert record.source == "FMP quote, FMP profile"
    status_text = " ".join(status.message for status in snapshot.statuses) + " " + " ".join(snapshot.errors)
    assert "quote rows 1" in status_text
    assert "profile rows 1" in status_text
    assert snapshot.diagnostics["rows_enriched_by_fmp_quote"] == 1
    assert snapshot.diagnostics["rows_enriched_by_fmp_profile"] == 1
    assert snapshot.diagnostics["rows_provider_returned_no_usable_data"] == 0
    assert api_key not in status_text
    assert all(api_key not in str(call["url"]) for call in session.calls)
    assert all("apikey" not in call["params"] for call in session.calls)


def test_fmp_batch_quote_short_empty_response_is_nonblocking() -> None:
    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/batch-quote-short"):
            assert params["symbols"] == "ACME"
            return _FakeFmpResponse(200, [])
        if (
            url.endswith("/profile")
            or url.endswith("/market-capitalization-batch")
            or url.endswith("/market-capitalization")
            or url.endswith("/key-metrics-ttm")
            or url.endswith("/ratios-ttm")
            or url.endswith("/income-statement-growth")
            or url.endswith("/financial-growth")
            or url.endswith("/income-statement")
            or url.endswith("/shares-float")
        ):
            return _FakeFmpResponse(200, [])
        raise AssertionError(url)

    session = _FakeFmpSession(handler)
    provider = FmpQuoteFundamentalsProvider(api_key="secret", session=session, symbol_limit=5)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    assert snapshot.records == ()
    assert snapshot.statuses[0].status == "empty"
    assert snapshot.errors == ()
    assert snapshot.diagnostics["rows_provider_returned_no_usable_data"] == 1
    endpoints = [str(call["url"]).rsplit("/", 1)[-1] for call in session.calls]
    assert endpoints[0] == "batch-quote-short"
    assert "batch-quote" not in endpoints


def test_fmp_batch_quote_short_auth_rate_errors_are_redacted_nonblocking() -> None:
    api_key = "fmp-secret-auth"
    for status_code in (401, 403, 429):
        session = _FakeFmpSession(lambda _url, _params, code=status_code: _FakeFmpResponse(code, {"message": f"blocked {api_key}"}))
        provider = FmpQuoteFundamentalsProvider(api_key=api_key, session=session, symbol_limit=5)

        snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

        combined = " ".join(status.message for status in snapshot.statuses) + " " + " ".join(snapshot.errors)
        assert snapshot.records == ()
        assert snapshot.statuses[0].status == "warning"
        assert snapshot.diagnostics["rows_blocked_by_provider_plan_rate_auth_limit"] == 1
        assert len(session.calls) == 1
        assert api_key not in combined


def test_fmp_batch_quote_short_malformed_payload_falls_back_to_batch_quote() -> None:
    def handler(url: str, _params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/batch-quote-short"):
            return _FakeFmpResponse(200, {"data": {"unexpected": "shape"}})
        if url.endswith("/batch-quote"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "price": 12.34, "changesPercentage": 1.2, "volume": 1234}])
        if (
            url.endswith("/profile")
            or url.endswith("/market-capitalization-batch")
            or url.endswith("/market-capitalization")
            or url.endswith("/key-metrics-ttm")
            or url.endswith("/ratios-ttm")
            or url.endswith("/income-statement-growth")
            or url.endswith("/financial-growth")
            or url.endswith("/income-statement")
            or url.endswith("/shares-float")
        ):
            return _FakeFmpResponse(200, [])
        raise AssertionError(url)

    session = _FakeFmpSession(handler)
    provider = FmpQuoteFundamentalsProvider(api_key="secret", session=session, symbol_limit=5)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    record = snapshot.records[0]
    assert record.symbol == "ACME"
    assert record.price == 12.34
    assert record.change_percent == 1.2
    assert record.volume == 1234
    assert "quote endpoint batch-quote" in snapshot.statuses[0].message
    assert [str(call["url"]).rsplit("/", 1)[-1] for call in session.calls[:2]] == ["batch-quote-short", "batch-quote"]


def test_fmp_batch_quote_short_and_fallback_malformed_status_is_redacted() -> None:
    api_key = "fmp-secret-malformed"

    def handler(url: str, _params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/batch-quote-short") or url.endswith("/batch-quote"):
            return _FakeFmpResponse(200, {"data": {"unexpected": api_key}})
        if (
            url.endswith("/profile")
            or url.endswith("/market-capitalization-batch")
            or url.endswith("/market-capitalization")
            or url.endswith("/key-metrics-ttm")
            or url.endswith("/ratios-ttm")
            or url.endswith("/income-statement-growth")
            or url.endswith("/financial-growth")
            or url.endswith("/income-statement")
            or url.endswith("/shares-float")
        ):
            return _FakeFmpResponse(200, [])
        raise AssertionError(url)

    session = _FakeFmpSession(handler)
    provider = FmpQuoteFundamentalsProvider(api_key=api_key, session=session, symbol_limit=5)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    combined = " ".join(status.message for status in snapshot.statuses) + " " + " ".join(snapshot.errors)
    assert snapshot.records == ()
    assert snapshot.statuses[0].status == "warning"
    assert "malformed payload" in combined
    assert api_key not in combined


def test_fmp_provider_deeper_endpoints_fill_remaining_fundamentals_and_cache() -> None:
    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/batch-quote-short"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "price": 12.34}])
        if url.endswith("/market-capitalization-batch"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "marketCap": 2_500_000_000}])
        symbol = params["symbol"]
        if url.endswith("/profile"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "sector": "Technology", "industry": "Software"}])
        if url.endswith("/key-metrics-ttm"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "marketCapTTM": 2_500_000_000, "epsTTM": 0.67}])
        if url.endswith("/ratios-ttm"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "priceEarningsRatioTTM": 18.5}])
        if url.endswith("/income-statement-growth"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "growthRevenue": 0.124}])
        if url.endswith("/shares-float"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "floatShares": 120_000_000, "outstandingShares": 150_000_000}])
        raise AssertionError(url)

    session = _FakeFmpSession(handler)
    provider = FmpQuoteFundamentalsProvider(api_key="secret", session=session, symbol_limit=5, cache_ttl_seconds=600)

    first = provider.quote_fundamentals(["ACME"], max_symbols=5)
    first_call_count = len(session.calls)
    second = provider.quote_fundamentals(["ACME"], max_symbols=5)
    third = provider.quote_fundamentals(["ACME"], max_symbols=5, force_refresh=True)

    assert first_call_count == 7
    assert len(session.calls) == first_call_count * 2
    record = first.records[0]
    assert record.market_cap == 2_500_000_000
    assert record.eps == 0.67
    assert record.pe_ratio == 18.5
    assert record.revenue_growth == 12.4
    assert record.shares_float == 120_000_000
    assert record.shares_outstanding == 150_000_000
    assert record.source == "FMP quote, FMP profile, FMP market cap, FMP key metrics TTM, FMP ratios TTM, FMP income growth, FMP shares float"
    assert first.diagnostics["rows_enriched_by_fmp_market_cap"] == 1
    assert first.diagnostics["rows_enriched_by_fmp_key_metrics"] == 1
    assert first.diagnostics["rows_enriched_by_fmp_ratios"] == 1
    assert first.diagnostics["rows_enriched_by_fmp_income_growth"] == 1
    assert first.diagnostics["rows_enriched_by_fmp_shares_float"] == 1
    assert second.diagnostics["fmp_cache_hits"] == 7
    assert third.diagnostics["fmp_cache_hits"] == 0


def test_fmp_visible_endpoint_fallbacks_fill_financial_growth_and_statement_eps() -> None:
    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/batch-quote-short"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "price": 12.34}])
        if url.endswith("/market-capitalization-batch"):
            return _FakeFmpResponse(200, [])
        if url.endswith("/market-capitalization"):
            return _FakeFmpResponse(200, [])
        symbol = params["symbol"]
        if url.endswith("/profile"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "sector": "Technology"}])
        if url.endswith("/key-metrics-ttm"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "marketCapTTM": 1_500_000_000}])
        if url.endswith("/ratios-ttm"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "priceEarningsRatioTTM": 21.0}])
        if url.endswith("/income-statement-growth"):
            return _FakeFmpResponse(200, [])
        if url.endswith("/financial-growth"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "growthRevenue": 0.085}])
        if url.endswith("/shares-float"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "floatShares": 50_000_000, "outstandingShares": 60_000_000}])
        if url.endswith("/income-statement"):
            return _FakeFmpResponse(200, [{"symbol": symbol, "epsdiluted": 1.23}])
        raise AssertionError(url)

    provider = FmpQuoteFundamentalsProvider(api_key="secret", session=_FakeFmpSession(handler), symbol_limit=5)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    record = snapshot.records[0]
    assert record.market_cap == 1_500_000_000
    assert record.pe_ratio == 21.0
    assert record.revenue_growth == 8.5
    assert record.eps == 1.23
    assert "FMP financial growth" in record.source
    assert "FMP income statement" in record.source
    assert snapshot.diagnostics["rows_enriched_by_fmp_financial_growth"] == 1
    assert snapshot.diagnostics["rows_enriched_by_fmp_income_statement"] == 1


def test_fmp_staged_family_methods_batch_profile_filter_fields_and_report_cache() -> None:
    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/profile"):
            assert params["symbol"] == "ACME,BETA"
            return _FakeFmpResponse(
                200,
                [
                    {
                        "symbol": "ACME",
                        "companyName": "Acme Corp",
                        "sector": "Technology",
                        "industry": "Software",
                        "exchangeShortName": "NASDAQ",
                        "cik": "1",
                        "price": 999.0,
                    },
                    {
                        "symbol": "BETA",
                        "companyName": "Beta Inc",
                        "sector": "Industrials",
                        "industry": "Machinery",
                        "exchangeShortName": "NYSE",
                        "cik": "2",
                    },
                ],
            )
        if url.endswith("/batch-quote-short"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "price": 12.5, "volume": 1000, "marketCap": 9_999}])
        if url.endswith("/market-capitalization-batch"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "marketCap": 2_500_000_000}])
        if url.endswith("/key-metrics-ttm"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "marketCapTTM": 2_500_000_000, "epsTTM": 0.67}])
        if url.endswith("/ratios-ttm"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "priceEarningsRatioTTM": 18.5}])
        if url.endswith("/income-statement-growth"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "growthRevenue": 0.124}])
        if url.endswith("/income-statement"):
            return _FakeFmpResponse(
                200,
                [{"symbol": "ACME", "revenue": 1_250_000_000, "netIncome": 120_000_000, "operatingIncome": 180_000_000, "epsdiluted": 0.82}],
            )
        if url.endswith("/balance-sheet-statement"):
            return _FakeFmpResponse(
                200,
                [{"symbol": "ACME", "cashAndCashEquivalents": 100_000_000, "totalAssets": 1_000_000_000, "totalLiabilities": 400_000_000, "shortTermDebt": 50_000_000, "longTermDebt": 150_000_000}],
            )
        if url.endswith("/cash-flow-statement"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "operatingCashFlow": 300_000_000, "freeCashFlow": 250_000_000}])
        if url.endswith("/cash-flow-statement-growth"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "growthOperatingCashFlow": 0.15, "growthFreeCashFlow": 0.12}])
        if url.endswith("/shares-float"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "floatShares": 120_000_000, "outstandingShares": 150_000_000}])
        raise AssertionError(url)

    session = _FakeFmpSession(handler)
    provider = FmpQuoteFundamentalsProvider(api_key="secret", session=session, symbol_limit=2000, cache_ttl_seconds=600, batch_size=100)

    profile = provider.profile_classification(["ACME", "BETA"], max_symbols=2000)
    profile_again = provider.profile_classification(["ACME", "BETA"], max_symbols=2000)
    quote = provider.quote_tape(["ACME"], max_symbols=2000)
    fundamentals = provider.fundamentals(["ACME"], max_symbols=2000)

    acme_profile = next(record for record in profile.records if record.symbol == "ACME")
    assert acme_profile.company_name == "Acme Corp"
    assert acme_profile.cik == "0000000001"
    assert acme_profile.price is None
    assert profile.diagnostics["provider_calls_attempted"] == 1
    assert profile.diagnostics["provider_rows_returned"] == 2
    assert profile_again.diagnostics["fmp_cache_hits"] == 2
    assert len([call for call in session.calls if str(call["url"]).endswith("/profile")]) == 1

    quote_record = quote.records[0]
    assert quote_record.price == 12.5
    assert quote_record.volume == 1000
    assert quote_record.market_cap is None
    assert quote.diagnostics["rows_enriched_by_fmp_quote"] == 1

    fundamentals_record = fundamentals.records[0]
    assert fundamentals_record.market_cap == 2_500_000_000
    assert fundamentals_record.pe_ratio == 18.5
    assert fundamentals_record.revenue_growth == 12.4
    assert fundamentals_record.revenue == 1_250_000_000
    assert fundamentals_record.net_income == 120_000_000
    assert fundamentals_record.operating_income == 180_000_000
    assert fundamentals_record.operating_cash_flow == 300_000_000
    assert fundamentals_record.free_cash_flow == 250_000_000
    assert fundamentals_record.operating_cash_flow_yoy == 15.0
    assert fundamentals_record.free_cash_flow_yoy == 12.0
    assert fundamentals_record.cash_and_equivalents == 100_000_000
    assert fundamentals_record.total_liabilities == 400_000_000
    assert fundamentals_record.total_debt == 200_000_000
    assert fundamentals_record.cash_to_liabilities == 25.0
    assert fundamentals_record.shares_float == 120_000_000
    assert fundamentals_record.price is None
    assert fundamentals.diagnostics["provider_calls_attempted"] == 9
    assert fundamentals.diagnostics["rows_enriched_by_fmp_market_cap"] == 1
    assert fundamentals.diagnostics["rows_enriched_by_fmp_income_statement"] == 1
    assert fundamentals.diagnostics["rows_enriched_by_fmp_balance_sheet"] == 1
    assert fundamentals.diagnostics["rows_enriched_by_fmp_cash_flow"] == 1
    assert fundamentals.diagnostics["rows_enriched_by_fmp_cash_flow_growth"] == 1


def test_fmp_missing_key_is_optional_and_does_not_call_network() -> None:
    session = _FakeFmpSession(lambda _url, _params: _FakeFmpResponse(200, []))
    provider = FmpQuoteFundamentalsProvider(api_key="", session=session)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    assert snapshot.records == ()
    assert session.calls == []
    assert snapshot.statuses[0].source == "FMP quote/fundamentals"
    assert snapshot.statuses[0].status == "unavailable"
    assert "No FMP_API_KEY is configured" in snapshot.statuses[0].message


def test_fmp_filing_metadata_normalizes_sec_prefilter_rows_without_key_leakage() -> None:
    api_key = "fmp-secret-filing-key"

    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        assert "apikey" not in params
        if url.endswith("/sec-filings-search/symbol"):
            assert params["symbol"] == "ACME"
            assert params["limit"] == "2"
            return _FakeFmpResponse(
                200,
                [
                    {
                        "symbol": "ACME",
                        "companyName": "Acme Corp",
                        "cik": "1234567",
                        "form": "424B5",
                        "filingDate": "2026-06-12",
                        "finalLink": "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000123/acme-424b5.htm",
                    }
                ],
            )
        raise AssertionError(url)

    session = _FakeFmpSession(handler)
    provider = FmpQuoteFundamentalsProvider(api_key=api_key, session=session, cache_ttl_seconds=600)

    rows = provider.filing_metadata("ACME", limit=2)
    cached = provider.filing_metadata("ACME", limit=2)

    assert len(rows) == 1
    assert rows == cached
    assert rows[0]["accessionNumber"] == "0001234567-26-000123"
    assert rows[0]["primaryDocument"] == "acme-424b5.htm"
    assert rows[0]["source"] == "FMP filing metadata"
    assert api_key not in str(rows)
    assert len(session.calls) == 1


def test_fmp_macro_context_builds_labeled_proxy_rows_and_caches() -> None:
    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        assert "apikey" not in params
        if url.endswith("/economic-indicators"):
            name = str(params["name"])
            return _FakeFmpResponse(
                200,
                [
                    {"date": "2026-05", "value": 3.2 if name == "CPI" else 2.0},
                    {"date": "2026-04", "value": 3.0 if name == "CPI" else 1.8},
                ],
            )
        if url.endswith("/quote"):
            symbol = str(params["symbol"])
            return _FakeFmpResponse(200, [{"symbol": symbol, "price": 75.0, "previousClose": 73.5}])
        raise AssertionError(url)

    session = _FakeFmpSession(handler)
    provider = FmpQuoteFundamentalsProvider(api_key="secret", session=session, cache_ttl_seconds=600)

    context = provider.macro_context(limit=2)
    cached = provider.macro_context(limit=2)

    assert context == cached
    assert context["CPI"]["category"] == "Inflation"
    assert context["CPI"]["value"] == 3.2
    assert context["CPI"]["prior"] == 3.0
    assert context["CLUSD"]["category"] == "Energy"
    assert context["CLUSD"]["price"] == 75.0
    assert len(session.calls) == 7


def test_fmp_missing_fields_remain_none_and_screener_renders_dashes() -> None:
    record = MarketQuoteFundamentalsRecord.from_dict({"symbol": "MISS", "price": "", "volume": None, "marketCap": "", "source": "FMP quote"})
    rows = build_market_screener_records(
        [MarketUniverseEntry("MISS", "Missing Fields Inc")],
        market_data_records=[record],
        fetched_at="2026-06-14T10:00:00+00:00",
    )

    row = rows[0]
    assert row.price is None
    assert row.volume is None
    assert row.market_cap is None
    values = earnings_radar_extension._screener_values(row)
    assert values[7] == "--"
    assert values[9] == "--"
    assert values[11] == "--"
    assert values[15] == "--"


def test_fmp_cap_and_cache_prevent_full_universe_calls() -> None:
    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/batch-quote-short"):
            symbols = str(params["symbols"]).split(",")
            assert symbols == ["SYM000", "SYM001"]
            return _FakeFmpResponse(200, [{"symbol": symbol, "price": index + 1.0} for index, symbol in enumerate(symbols)])
        if url.endswith("/profile"):
            return _FakeFmpResponse(
                200,
                [
                    {
                        "symbol": params["symbol"],
                        "sector": "Technology",
                        "marketCap": 1_000_000,
                        "peRatio": 12.0,
                        "EPS": 0.5,
                        "revenueGrowth": 10.0,
                        "sharesFloat": 100_000,
                        "sharesOutstanding": 120_000,
                    }
                ],
            )
        raise AssertionError(url)

    session = _FakeFmpSession(handler)
    provider = FmpQuoteFundamentalsProvider(api_key="secret", session=session, symbol_limit=2, cache_ttl_seconds=600)
    symbols = [f"SYM{index:03d}" for index in range(819)]

    first = provider.quote_fundamentals(symbols, max_symbols=819)
    first_call_count = len(session.calls)
    second = provider.quote_fundamentals(symbols, max_symbols=819)

    assert first_call_count == 3
    assert len(session.calls) == first_call_count
    assert len(first.records) == 2
    assert len(second.records) == 2
    assert "817 skipped/limited" in first.statuses[0].message
    assert "cache used for 4" in second.statuses[0].message


def test_fmp_plan_limit_warning_redacts_api_key() -> None:
    api_key = "fmp-redact-me"

    def handler(_url: str, _params: dict[str, object]) -> _FakeFmpResponse:
        return _FakeFmpResponse(200, {"Error Message": f"Daily plan limit reached for {api_key}. Please upgrade."})

    provider = FmpQuoteFundamentalsProvider(api_key=api_key, session=_FakeFmpSession(handler), symbol_limit=2)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=2)

    combined = " ".join(status.message for status in snapshot.statuses) + " " + " ".join(snapshot.errors)
    assert snapshot.records == ()
    assert snapshot.statuses[0].status == "warning"
    assert api_key not in combined
    assert "[REDACTED]" in combined


def test_composite_market_data_provider_merges_local_schwab_and_fmp_without_duplicate_symbols() -> None:
    local_provider = _FakeMarketDataProvider({"ACME": MarketQuoteFundamentalsRecord("ACME", price=10.0, volume=1000, source="Schwab quote")})

    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/batch-quote-short"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "marketCap": 5_000_000_000, "peRatio": 20.0}])
        if url.endswith("/profile"):
            return _FakeFmpResponse(
                200,
                [
                    {
                        "symbol": params["symbol"],
                        "sector": "Technology",
                        "industry": "Software",
                        "EPS": 1.2,
                        "revenueGrowth": 8.5,
                        "sharesFloat": 100_000_000,
                        "sharesOutstanding": 120_000_000,
                    }
                ],
            )
        raise AssertionError(url)

    fmp_provider = FmpQuoteFundamentalsProvider(api_key="secret", session=_FakeFmpSession(handler), symbol_limit=5)
    composite = CompositeMarketDataProvider([local_provider, fmp_provider])

    snapshot = composite.quote_fundamentals(["ACME"], max_symbols=5)

    assert len(snapshot.records) == 1
    record = snapshot.records[0]
    assert record.symbol == "ACME"
    assert record.price == 10.0
    assert record.volume == 1000
    assert record.market_cap == 5_000_000_000
    assert record.pe_ratio == 20.0
    assert record.sector == "Technology"
    assert record.source == "Schwab quote, FMP quote, FMP profile"


def test_market_data_source_ladder_preserves_precedence_and_field_provenance() -> None:
    local_provider = _FakeMarketDataProvider(
        {"ACME": MarketQuoteFundamentalsRecord("ACME", price=10.0, source="Local market data file")}
    )
    schwab_provider = _FakeMarketDataProvider(
        {"ACME": MarketQuoteFundamentalsRecord("ACME", price=11.0, volume=1_000, source="Schwab quote")}
    )
    fmp_provider = _FakeMarketDataProvider(
        {
            "ACME": MarketQuoteFundamentalsRecord(
                "ACME",
                price=12.0,
                market_cap=5_000_000_000,
                sector="Technology",
                source="FMP quote, FMP profile",
            )
        }
    )
    composite = CompositeMarketDataProvider([local_provider, schwab_provider, fmp_provider])

    snapshot = composite.quote_fundamentals(["ACME"], max_symbols=5)

    record = snapshot.records[0]
    assert record.price == 10.0
    assert record.volume == 1_000
    assert record.market_cap == 5_000_000_000
    provenance = {row.field: row.source for row in record.field_provenance}
    assert provenance["price"] == "Local market data file"
    assert provenance["volume"] == "Schwab quote"
    assert provenance["market_cap"] == "FMP quote, FMP profile"


def test_market_data_source_ladder_places_databento_before_fmp_for_tape_fields() -> None:
    local_provider = _FakeMarketDataProvider(
        {"ACME": MarketQuoteFundamentalsRecord("ACME", price=10.0, source="Local market data file")}
    )
    schwab_provider = _FakeMarketDataProvider({})
    databento_provider = _FakeMarketDataProvider(
        {"ACME": MarketQuoteFundamentalsRecord("ACME", price=12.0, volume=4_000, source="Databento US Equities")}
    )
    fmp_provider = _FakeMarketDataProvider(
        {
            "ACME": MarketQuoteFundamentalsRecord(
                "ACME",
                price=13.0,
                volume=5_000,
                market_cap=5_000_000_000,
                sector="Technology",
                source="FMP quote, FMP profile",
            )
        }
    )
    composite = CompositeMarketDataProvider([local_provider, schwab_provider, databento_provider, fmp_provider])

    snapshot = composite.quote_fundamentals(["ACME"], max_symbols=5)

    record = snapshot.records[0]
    assert record.price == 10.0
    assert record.volume == 4_000
    assert record.market_cap == 5_000_000_000
    provenance = {row.field: row.source for row in record.field_provenance}
    assert provenance["price"] == "Local market data file"
    assert provenance["volume"] == "Databento US Equities"
    assert provenance["market_cap"] == "FMP quote, FMP profile"


def test_fmp_provider_profile_by_cik_maps_profile_fields_and_cik() -> None:
    def handler(url: str, params: dict[str, object]) -> _FakeFmpResponse:
        if url.endswith("/profile-cik"):
            assert params["cik"] == "320193"
            return _FakeFmpResponse(
                200,
                [
                    {
                        "symbol": "AAPL",
                        "cik": "0000320193",
                        "price": 195.0,
                        "marketCap": 3_000_000_000_000,
                        "sector": "Technology",
                        "industry": "Consumer Electronics",
                        "exchangeShortName": "NASDAQ",
                    }
                ],
            )
        raise AssertionError(url)

    provider = FmpQuoteFundamentalsProvider(api_key="secret", session=_FakeFmpSession(handler), symbol_limit=5)

    snapshot = provider.quote_fundamentals_by_cik(["0000320193"], max_symbols=5)

    assert len(snapshot.records) == 1
    record = snapshot.records[0]
    assert record.symbol == "AAPL"
    assert record.cik == "0000320193"
    assert record.price == 195.0
    assert record.market_cap == 3_000_000_000_000
    assert record.source == "FMP profile-by-CIK"
    assert snapshot.diagnostics["rows_enriched_by_fmp_profile_by_cik"] == 1


def test_market_screener_holding_quote_fmp_row_has_label_completeness_and_provenance() -> None:
    records = build_market_screener_records(
        [MarketUniverseEntry("ACME", "Acme Corp", cik="0000000001", exchange="Nasdaq", sector="Technology", industry="Software")],
        supplemental_records=[
            MarketScreenerRecord(
                "ACME",
                price=119.0,
                signals=("Schwab holding",),
                sources=("Local app holdings",),
                portfolio_quantity=10,
                portfolio_average_cost=100.0,
                portfolio_market_value=1190.0,
                portfolio_unrealized_pnl=190.0,
                portfolio_weight=0.02,
            )
        ],
        market_data_records=[
            MarketQuoteFundamentalsRecord("ACME", price=121.25, volume=2_500_000, source="Schwab quote", fetched_at="2026-06-14T10:00:00+00:00"),
            MarketQuoteFundamentalsRecord(
                "ACME",
                avg_volume=1_000_000,
                change_percent=5.6,
                market_cap=12_000_000_000,
                pe_ratio=31.5,
                eps=1.22,
                revenue_growth=14.4,
                shares_float=120_000_000,
                shares_outstanding=150_000_000,
                source="FMP quote, FMP profile",
                source_url="https://example.test/fmp",
                fetched_at="2026-06-14T10:01:00+00:00",
            ),
        ],
        fetched_at="2026-06-14T10:00:00+00:00",
    )

    acme = records[0]
    assert market_screener_is_my_holding(acme) is True
    assert market_screener_data_label(acme) == "Holding + Quote + FMP"
    assert market_screener_data_completeness_score(acme) >= 75
    assert filter_market_screener_records(records, event_type="My Holdings") == [acme]
    assert filter_market_screener_records(records, data_completeness="High completeness (>=75%)") == [acme]
    assert sort_market_screener_records([MarketScreenerRecord("SPRS"), acme], "data_completeness", descending=True)[0] == acme

    provenance = {(row.field, row.source) for row in acme.field_provenance}
    assert ("portfolio_quantity", "Local app holdings") in provenance
    assert ("price", "Schwab quote") in provenance
    assert ("market_cap", "FMP quote, FMP profile") in provenance
    detail = earnings_radar_extension._screener_detail_text(acme)
    assert "Portfolio:" in detail
    assert "Field provenance:" in detail
    assert "market_cap: FMP quote, FMP profile" in detail
    payload = market_screener_ai_request_payload(acme, "Explain the selected row.", timeout_seconds=30)
    selected = payload["selected_market_screener_record"]
    assert selected["data_label"] == "Holding + Quote + FMP"
    assert selected["portfolio_context"]["is_my_holding"] is True
    assert selected["portfolio_context"]["quantity"] == 10
    assert any(row["field"] == "market_cap" and row["source"] == "FMP quote, FMP profile" for row in selected["field_provenance"])


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
    filing_only = MarketScreenerRecord("FILI", "Filing Inc", recent_filing_date="2026-06-07", sources=("SEC EDGAR",))
    earnings_only = MarketScreenerRecord("EARN", "Earnings Inc", next_earnings_date="2026-06-21", sources=("Alpha Vantage",))
    holding_only = MarketScreenerRecord("HOLD", "Holding Inc", signals=("Schwab holding",), sources=("Local app holdings",))

    assert filter_market_screener_records([acme, beta], search="acme") == [acme]
    assert filter_market_screener_records([acme, beta], sector="Technology") == [acme]
    assert filter_market_screener_records([acme, beta], event_type="Upcoming earnings") == [acme]
    assert filter_market_screener_records([acme, beta], event_type="Quote-enriched") == [acme]
    assert filter_market_screener_records([acme, beta], event_type="Fundamentals available") == []
    assert filter_market_screener_records([acme, beta], event_type="Guidance mentioned") == [acme]
    assert filter_market_screener_records([acme, beta], event_type="High volume / mover") == [acme]
    assert filter_market_screener_records([acme, beta], risk_flag="Any risk flag") == [acme]
    assert filter_market_screener_records([acme, beta], earnings_date_window="Next 7 days", today=date(2026, 6, 13)) == [acme]
    assert filter_market_screener_records([acme, beta], has_ai_signal=True) == [acme]
    assert filter_market_screener_records([acme, beta], has_price_volume_data=True) == [acme]
    assert filter_market_screener_records([holding_only, beta], event_type="My Holdings") == [holding_only]
    assert sort_market_screener_records([beta, acme], "change_percent", descending=True) == [acme, beta]
    assert sort_market_screener_records(
        [beta, MarketScreenerRecord("LOW", price=8.0), MarketScreenerRecord("HIGH", price=30.0)],
        "price",
        descending=False,
    ) == [MarketScreenerRecord("LOW", price=8.0), MarketScreenerRecord("HIGH", price=30.0), beta]
    assert sort_market_screener_records(
        [beta, MarketScreenerRecord("LOW", price=8.0), MarketScreenerRecord("HIGH", price=30.0)],
        "price",
        descending=True,
    ) == [MarketScreenerRecord("HIGH", price=30.0), MarketScreenerRecord("LOW", price=8.0), beta]
    rich_missing_cap = MarketScreenerRecord(
        "RICH",
        "Rich Missing Cap",
        exchange="Nasdaq",
        sector="Technology",
        industry="Software",
        price=120.0,
        volume=2_000_000,
        avg_volume=1_500_000,
        change_percent=2.5,
        pe_ratio=31.0,
        eps=1.2,
        revenue_growth=14.0,
        signals=("Schwab holding",),
        sources=("Local app holdings",),
    )
    sorted_by_market_cap = sort_market_screener_records(
        [
            MarketScreenerRecord("MISS", "Missing Cap"),
            rich_missing_cap,
            MarketScreenerRecord("MEGA", "Mega Cap", market_cap=3_000_000_000_000),
            MarketScreenerRecord("ZERO", "Zero Cap", market_cap=0),
            MarketScreenerRecord("MID", "Mid Cap", market_cap=10_000_000_000),
        ],
        "market_cap",
        descending=True,
    )
    assert [record.symbol for record in sorted_by_market_cap] == ["MEGA", "MID", "ZERO", "RICH", "MISS"]
    assert market_screener_data_label(acme) == "Quote + Filing + Earnings"
    assert market_screener_data_label(holding_only) == "Holding"
    assert market_screener_data_label(filing_only) == "Filing"
    assert market_screener_data_label(earnings_only) == "Earnings"
    assert market_screener_data_label(beta) == "Universe only"


def test_market_screener_market_cap_sort_uses_trusted_rank_descending() -> None:
    msft = MarketScreenerRecord("MSFT", "Microsoft Corp", exchange="NASDAQ", country="United States", market_cap=2_900_000_000_000, market_cap_currency="USD")
    sony = MarketScreenerRecord("SONY", "Sony Group Corp ADR", exchange="NYSE", country="Japan", market_cap=24_000_000_000_000, market_cap_currency="JPY", is_adr=True)
    honda = MarketScreenerRecord("HMC", "Honda Motor Co ADR", exchange="NYSE", country="Japan", market_cap=10_000_000_000_000, market_cap_currency="USD", is_adr=True)
    fund = MarketScreenerRecord("SPY", "SPDR S&P 500 ETF Trust", exchange="NYSE ARCA", market_cap=70_000_000_000_000, market_cap_currency="USD", is_etf=True)

    ranked = sort_market_screener_records([sony, fund, honda, msft], "market_cap", descending=True)

    assert [record.symbol for record in ranked] == ["SPY", "HMC", "MSFT", "SONY"]
    assert market_screener_market_cap_rank(msft).category == "us_primary_common"
    assert market_screener_market_cap_rank(sony).trusted is False
    assert "JPY" in market_screener_market_cap_rank(sony).reason


def test_market_screener_market_cap_sort_is_strict_numeric_ascending_with_missing_bottom() -> None:
    rows = [
        MarketScreenerRecord("MISS", "Missing Cap"),
        MarketScreenerRecord("MID", "Mid Cap", market_cap=10_000_000_000),
        MarketScreenerRecord("ZERO", "Zero Cap", market_cap=0),
        MarketScreenerRecord("MEGA", "Mega Cap", market_cap=3_000_000_000_000),
    ]

    ranked = sort_market_screener_records(rows, "market_cap", descending=False)

    assert [record.symbol for record in ranked] == ["ZERO", "MID", "MEGA", "MISS"]


def test_market_screener_market_cap_sort_honors_provider_rank_metadata() -> None:
    msft = MarketScreenerRecord("MSFT", "Microsoft Corp", exchange="NASDAQ", country="United States", market_cap=2_900_000_000_000, market_cap_currency="USD")
    foreign = MarketScreenerRecord(
        "FORE",
        "Foreign Common",
        exchange="NYSE",
        country="Canada",
        market_cap=5_000_000_000_000,
        market_cap_currency="CAD",
        market_cap_rank_value=40_000_000_000,
        market_cap_rank_currency="USD",
        market_cap_rank_trusted=True,
    )

    ranked = sort_market_screener_records([foreign, msft], "market_cap", descending=True)

    assert [record.symbol for record in ranked] == ["MSFT", "FORE"]
    rank = market_screener_market_cap_rank(foreign)
    assert rank.trusted is True
    assert rank.ranking_market_cap == 40_000_000_000
    assert rank.category == "trusted_non_primary"


def test_market_screener_market_cap_sort_demotes_ambiguous_non_primary_rows() -> None:
    holding = MarketScreenerRecord(
        "HOLD",
        "Held Small Cap",
        market_cap=1_000_000_000,
        signals=("Schwab holding",),
        sources=("Local app holdings",),
        portfolio_quantity=25,
    )
    etf = MarketScreenerRecord("VXUS", "Vanguard Total International Stock ETF", market_cap=655_200_000_000, is_etf=True)
    foreign = MarketScreenerRecord("STX", "Seagate Technology", market_cap=239_900_000_000, country="Ireland")
    primary = MarketScreenerRecord("ACN", "Accenture plc", market_cap=80_300_000_000, exchange="NYSE", market_cap_currency="USD")
    missing = MarketScreenerRecord("MISS", "Missing Cap", signals=("Schwab holding",), portfolio_quantity=10)

    ranked = sort_market_screener_records([holding, primary, missing, foreign, etf], "market_cap", descending=True)

    assert [record.symbol for record in ranked] == ["ACN", "VXUS", "STX", "HOLD", "MISS"]


def test_market_screener_position_rows_are_labels_only_for_market_cap_ordering() -> None:
    holding = MarketScreenerRecord(
        "HOLD",
        "Held Small Cap",
        market_cap=1_000_000_000,
        signals=("Schwab holding",),
        sources=("Local app holdings",),
        portfolio_quantity=25,
    )
    larger_non_position = MarketScreenerRecord("BETA", "Beta Large Cap", market_cap=20_000_000_000)

    ranked = sort_market_screener_records([holding, larger_non_position], "market_cap", descending=True)

    assert [record.symbol for record in ranked] == ["BETA", "HOLD"]
    assert filter_market_screener_records([holding, larger_non_position], event_type="My Holdings") == [holding]
    assert market_screener_data_label(holding).startswith("Holding")


def test_market_quote_record_marks_foreign_local_currency_cap_untrusted_for_screener_rank() -> None:
    quote = MarketQuoteFundamentalsRecord.from_dict(
        {
            "symbol": "SONY",
            "companyName": "Sony Group Corp ADR",
            "exchangeShortName": "NYSE",
            "marketCap": 24_000_000_000_000,
            "currency": "JPY",
            "country": "Japan",
            "isAdr": True,
            "source": "FMP profile",
        }
    )
    record = build_market_screener_records(
        [MarketUniverseEntry("SONY", "Sony Group Corp ADR", exchange="NYSE")],
        market_data_records=[quote],
        fetched_at="2026-06-18T12:00:00+00:00",
    )[0]

    rank = market_screener_market_cap_rank(record)

    assert record.market_cap_currency == "JPY"
    assert record.country == "Japan"
    assert rank.trusted is False
    assert rank.category == "untrusted_non_usd"
    assert "market cap currency=JPY" in next(row.source_detail for row in record.field_provenance if row.field == "market_cap")


def test_market_screener_major_cap_diagnostics_explain_msft_absence() -> None:
    diagnostics = MarketScreenerCoverageDiagnostics(total_rows=1, rows_skipped_by_configured_symbol_cap=7)
    lines = market_screener_major_cap_diagnostic_lines(
        [MarketScreenerRecord("AAPL", "Apple Inc", exchange="NASDAQ", market_cap=3_000_000_000_000, market_cap_currency="USD")],
        diagnostics,
    )

    msft_line = next(line for line in lines if line.startswith("MSFT absent:"))
    assert "no row with this symbol is present" in msft_line
    assert "7 row(s) were skipped by configured provider caps" in msft_line


def test_ask_screener_plan_validation_sanitizes_and_rejects_unsupported_fields() -> None:
    plan = validate_ask_screener_plan(
        {
            "intent": "Tech names above $10",
            "filters": [
                {"field": "sector", "operator": "contains", "value": "tech"},
                {"field": "price", "operator": ">", "value": "$10"},
                {"field": "holding", "operator": "true"},
            ],
            "sort": {"field": "price", "direction": "desc"},
            "limit": "25",
        }
    )

    assert plan.intent == "Tech names above $10"
    assert [row.field for row in plan.filters] == ["sector", "price", "is_my_holding"]
    assert [row.operator for row in plan.filters] == ["contains", "gt", "is_true"]
    assert plan.filters[1].value == 10.0
    assert plan.sort is not None
    assert plan.sort.field == "price"
    assert plan.sort.descending is True
    assert plan.limit == 25

    try:
        validate_ask_screener_plan({"filters": [{"field": "api_key", "operator": "exists"}]})
    except AskScreenerPlanValidationError as exc:
        assert "Unsupported Ask Screener field" in str(exc)
    else:
        raise AssertionError("unsupported field should fail validation")


def test_ask_screener_executor_handles_numeric_text_boolean_filters() -> None:
    acme = MarketScreenerRecord(
        "ACME",
        "Acme Corp",
        sector="Technology",
        price=18.0,
        revenue_growth=12.0,
        eps=1.2,
        signals=("Schwab holding",),
        sources=("Local app holdings",),
    )
    beta = MarketScreenerRecord("BETA", "Beta Inc", sector="Technology", price=8.0, revenue_growth=20.0, eps=-0.4)
    gamma = MarketScreenerRecord("GAMMA", "Gamma Inc", sector="Industrials", price=30.0, revenue_growth=None, eps=0.8)

    result = execute_ask_screener_plan(
        [acme, beta, gamma],
        {
            "intent": "Held tech above 10",
            "filters": [
                {"field": "sector", "operator": "eq", "value": "Technology"},
                {"field": "price", "operator": "gt", "value": 10},
                {"field": "is_my_holding", "operator": "is_true"},
            ],
        },
    )

    assert result.records == (acme,)
    assert result.total_input_rows == 3
    assert result.total_matched_rows == 1
    assert "missing values were not inferred" in result.summary


def test_ask_screener_executor_sorts_limits_and_summarizes_results() -> None:
    records = [
        MarketScreenerRecord("LOW", "Low Volume", volume=100_000),
        MarketScreenerRecord("HIGH", "High Volume", volume=2_000_000),
        MarketScreenerRecord("MID", "Mid Volume", volume=500_000),
    ]

    result = execute_ask_screener_plan(
        records,
        {
            "intent": "Top volume",
            "filters": [{"field": "volume", "operator": "exists"}],
            "sort": {"field": "volume", "descending": True},
            "limit": 2,
        },
    )

    assert [record.symbol for record in result.records] == ["HIGH", "MID"]
    assert result.limited is True
    assert "matched 3 of 3 row(s)" in result.summary
    assert "showing first 2" in result.summary
    assert "sorted by volume descending" in result.summary


def test_market_screener_ask_sort_overrides_default_market_cap_sort(monkeypatch) -> None:
    app = _fake_screener_filter_app(
        [
            MarketScreenerRecord("MEGA", "Mega Cap", market_cap=3_000_000_000_000, volume=100_000),
            MarketScreenerRecord("SMALL", "Small Cap", market_cap=500_000_000, volume=2_000_000),
            MarketScreenerRecord("MISS", "Missing Volume", market_cap=1_000_000_000),
        ]
    )
    monkeypatch.setattr(earnings_radar_extension, "_populate_screener_table", lambda _app: None)
    monkeypatch.setattr(earnings_radar_extension, "_draw_screener_chart", lambda _app: None)
    plan = validate_ask_screener_plan(
        {
            "intent": "Top volume",
            "sort": {"field": "volume", "descending": True},
            "limit": 10,
        }
    )

    earnings_radar_extension._apply_ask_screener_plan_local(app, plan, source_label="local")

    assert app.market_screener_sort_column == "volume"
    assert app.market_screener_sort_desc is True
    assert [record.symbol for record in app.market_screener_filtered_records] == ["SMALL", "MEGA", "MISS"]
    assert "sorted by volume descending" in app.market_screener_ask_summary_var.value


def test_ask_screener_fallback_parser_covers_core_phrases_and_metadata_filters() -> None:
    today = date(2026, 6, 16)
    acme = MarketScreenerRecord(
        "ACME",
        "Acme Corp",
        exchange="Nasdaq",
        sector="Technology",
        price=25.0,
        next_earnings_date="2026-06-25",
        recent_filing_date="2026-06-10",
        volume=2_000_000,
        avg_volume=1_000_000,
        revenue_growth=15.0,
        signals=("Schwab holding",),
        sources=("Local app holdings",),
    )
    beta = MarketScreenerRecord("BETA", "Beta Inc", exchange="NYSE", sector="Industrials", price=None)
    gamma = MarketScreenerRecord("GAMM", "Gamma Inc", exchange="NYSE", sector="Healthcare", price=3.0, eps=-0.2)
    rows = [acme, beta, gamma]

    cases = (
        ("show my holdings", ["ACME"]),
        ("quote enriched", ["ACME", "GAMM"]),
        ("recent filings", ["ACME"]),
        ("earnings soon", ["ACME"]),
        ("high volume", ["ACME"]),
        ("positive revenue growth", ["ACME"]),
        ("negative EPS", ["GAMM"]),
        ("technology sector on nasdaq", ["ACME"]),
        ("missing price data", ["BETA"]),
    )
    for query, expected_symbols in cases:
        plan = parse_ask_screener_fallback(query, records=rows)
        assert plan is not None, query
        result = execute_ask_screener_plan(rows, plan, today=today)
        assert [record.symbol for record in result.records] == expected_symbols

    clear_plan = parse_ask_screener_fallback("clear filters", records=rows)
    assert clear_plan is not None
    assert clear_plan.clear_filters is True


def test_ask_screener_llm_malformed_json_fails_gracefully() -> None:
    fake_openai = _FakeOpenAiClient(answer="not json")
    client = OpenAiAskScreenerPlannerClient(openai_client=fake_openai, model="gpt-test", timeout_seconds=11)

    try:
        client.plan("Find tech rows", [MarketScreenerRecord("ACME", "Acme Corp", sector="Technology")])
    except AskScreenerPlannerError as exc:
        assert "could not use the model plan" in str(exc)
        assert "valid JSON" in str(exc)
    else:
        raise AssertionError("malformed JSON should fail gracefully")

    call = fake_openai.responses.calls[0]
    request_payload = json.loads(call["input"][-1]["content"])
    assert call["model"] == "gpt-test"
    assert call["store"] is False
    assert call["timeout"] == 11
    assert request_payload["snapshot_metadata"]["total_rows"] == 1
    assert "omitted_row_data" in request_payload["snapshot_metadata"]


def test_ask_screener_llm_unsupported_plan_fails_validation() -> None:
    fake_openai = _FakeOpenAiClient(answer=json.dumps({"filters": [{"field": "secret_token", "operator": "exists"}]}))
    client = OpenAiAskScreenerPlannerClient(openai_client=fake_openai, model="gpt-test", timeout_seconds=11)

    try:
        client.plan("Find rows with secret token", [MarketScreenerRecord("ACME", "Acme Corp")])
    except AskScreenerPlannerError as exc:
        assert "Unsupported Ask Screener field" in str(exc)
    else:
        raise AssertionError("unsupported model plan should fail validation")


def test_ask_screener_planner_payload_uses_metadata_and_redacts_secrets() -> None:
    secret = "sk-testsecret123456"
    rows = [
        MarketScreenerRecord(
            "SECR",
            "Secret Corp",
            sector=f"Technology {secret}",
            exchange="Nasdaq",
            source_links=(f"https://example.test/?apikey={secret}",),
            source_excerpt=f"Do not leak {secret}",
        )
        for _index in range(5)
    ]

    payload = ask_screener_planner_request_payload("find technology", rows, timeout_seconds=33)
    text = json.dumps(payload)

    assert payload["snapshot_metadata"]["total_rows"] == 5
    assert "Full screener rows" in payload["snapshot_metadata"]["omitted_row_data"]
    assert "SECR" not in text
    assert "Secret Corp" not in text
    assert "source_excerpt" not in text
    assert "source_links" not in text
    assert secret not in text
    assert "sk-[REDACTED]" in text
    assert "No buy/sell/hold recommendations" in " ".join(payload["safety_rules"])
    assert "field_coverage" in payload["snapshot_metadata"]


def test_ask_screener_v2_field_coverage_analysis_counts_required_fields() -> None:
    rows = [
        MarketScreenerRecord("ACME", "Acme Corp", sector="Technology", industry="Software", volume=1000),
        MarketScreenerRecord("BETA", "Beta Inc", sector=None, industry="", volume=None),
    ]

    coverage = {row.field: row for row in analyze_ask_screener_field_coverage(rows)}

    assert coverage["sector"].available_count == 1
    assert coverage["sector"].missing_count == 1
    assert coverage["industry"].coverage_ratio == 0.5
    assert coverage["volume"].available_count == 1
    assert coverage["market_cap"].missing_count == 2


def test_ask_screener_v2_enrichment_decision_logic_handles_caps_confirm_and_missing_config() -> None:
    rows = [MarketScreenerRecord(f"SYM{index}", f"Symbol {index}") for index in range(3)]
    plan = validate_ask_screener_plan(
        {
            "intent": "Technology electronics",
            "filters": [{"field": "classification", "operator": "contains_any", "value": ["technology", "electronic"]}],
            "limit": 100,
        }
    )

    missing = ask_screener_enrichment_decision(
        rows,
        plan,
        config=AskScreenerProviderConfig(fmp_configured=False, databento_equities_configured=False),
    )
    assert missing.action == ASK_SCREENER_PROVIDER_ACTION_MISSING_CONFIG
    assert "FMP_API_KEY" in " ".join(missing.missing_provider_config)

    enrich = ask_screener_enrichment_decision(
        rows,
        plan,
        config=AskScreenerProviderConfig(fmp_configured=True, profile_enrich_limit=2, require_confirm_above=100),
    )
    assert enrich.action == ASK_SCREENER_PROVIDER_ACTION_ENRICH_THEN_EXECUTE
    assert enrich.symbols_to_enrich == ("SYM0", "SYM1")
    assert enrich.candidate_symbol_count == 3

    confirm = ask_screener_enrichment_decision(
        rows,
        plan,
        config=AskScreenerProviderConfig(fmp_configured=True, profile_enrich_limit=2, require_confirm_above=1),
    )
    assert confirm.action == ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT


def test_ask_screener_v2_capped_auto_enrichment_then_filters_locally() -> None:
    rows = [MarketScreenerRecord(symbol, f"{symbol} Corp") for symbol in ("ACME", "BETA", "GAMMA", "DELTA")]
    provider = _FakeMarketDataProvider(
        {
            "ACME": MarketQuoteFundamentalsRecord("ACME", sector="Technology", industry="Software", source="FMP profile"),
            "BETA": MarketQuoteFundamentalsRecord("BETA", sector="Consumer Cyclical", industry="Electronic Components", source="FMP profile"),
            "GAMMA": MarketQuoteFundamentalsRecord("GAMMA", sector="Healthcare", industry="Devices", source="FMP profile"),
            "DELTA": MarketQuoteFundamentalsRecord("DELTA", sector="Healthcare", industry="Devices", source="FMP profile"),
        }
    )
    plan = parse_ask_screener_fallback("Can you filter for up to 100 symbols in the Technology / Electronics sectors?", records=rows)

    assert plan is not None
    result = execute_provider_aware_ask_screener_plan(
        rows,
        plan,
        provider=provider,
        config=AskScreenerProviderConfig(fmp_configured=True, profile_enrich_limit=3, require_confirm_above=100),
    )

    assert provider.calls == [(("ACME", "BETA", "DELTA"), 3, False)]
    assert [record.symbol for record in result.records] == ["ACME", "BETA"]
    assert result.enrichment_status == "provider-enriched"
    assert result.symbols_requested == 3
    assert result.rows_updated == 3
    assert result.total_matched_rows == 2
    assert result.more_enrichment_may_help is True
    assert "status: provider-enriched" in result.summary
    assert "symbols requested: 3" in result.summary
    assert "rows updated: 3" in result.summary
    assert "matches found: 2" in result.summary


def test_market_screener_staged_enrichment_preserves_good_values_zeroes_and_provenance() -> None:
    class _StagedProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[str, ...], int]] = []

        def profile_classification(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
            requested = tuple(symbols)
            self.calls.append(("profile_classification", requested, max_symbols))
            return MarketQuoteFundamentalsSnapshot(
                records=(
                    MarketQuoteFundamentalsRecord("ACME", company_name="", sector="Technology", industry="Software", exchange="NASDAQ", source="FMP profile"),
                    MarketQuoteFundamentalsRecord("BETA", company_name="Beta Inc", sector="Industrials", industry="Machinery", exchange="NYSE", source="FMP profile"),
                ),
                fetched_at="2026-06-14T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP profile/classification", "available", "2026-06-14T10:00:00+00:00", "Loaded profiles."),),
                diagnostics={"provider_calls_attempted": 1, "provider_rows_requested": len(requested), "provider_rows_returned": 2, "provider_rows_parsed": 2},
            )

        def quote_tape(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
            requested = tuple(symbols)
            self.calls.append(("quote_tape", requested, max_symbols))
            return MarketQuoteFundamentalsSnapshot(
                records=(
                    MarketQuoteFundamentalsRecord("ACME", price=None, volume=0, avg_volume=0, change_percent=0, source="Databento US Equities"),
                    MarketQuoteFundamentalsRecord("BETA", price=5.0, volume=100, avg_volume=0, source="Databento US Equities"),
                ),
                fetched_at="2026-06-14T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("Databento US Equities", "available", "2026-06-14T10:00:00+00:00", "Loaded tape."),),
                diagnostics={"provider_calls_attempted": 1, "provider_rows_requested": len(requested), "provider_rows_returned": 2, "provider_rows_parsed": 2},
            )

        def fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
            requested = tuple(symbols)
            self.calls.append(("fundamentals", requested, max_symbols))
            return MarketQuoteFundamentalsSnapshot(
                records=(
                    MarketQuoteFundamentalsRecord("BETA", market_cap=0, pe_ratio=0, eps=0, revenue_growth=0, shares_outstanding=0, source="FMP key metrics TTM"),
                ),
                fetched_at="2026-06-14T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP fundamentals", "available", "2026-06-14T10:00:00+00:00", "Loaded fundamentals."),),
                diagnostics={"provider_calls_attempted": 1, "provider_rows_requested": len(requested), "provider_rows_returned": 1, "provider_rows_parsed": 1},
            )

    rows = [
        MarketScreenerRecord("ACME", "Acme Corp", price=10.0),
        MarketScreenerRecord("BETA"),
    ]

    result = enrich_market_screener_records(
        rows,
        MARKET_SCREENER_ENRICH_ALL_MARKET_DATA,
        provider=_StagedProvider(),
        config=MarketScreenerBackfillConfig(profile_limit=10, quote_limit=10, fundamental_limit=10, databento_limit=10),
    )

    acme = next(record for record in result.records if record.symbol == "ACME")
    beta = next(record for record in result.records if record.symbol == "BETA")
    assert acme.company_name == "Acme Corp"
    assert acme.price == 10.0
    assert acme.volume == 0
    assert beta.company_name == "Beta Inc"
    assert beta.market_cap == 0
    assert beta.revenue_growth == 0
    assert beta.shares_outstanding == 0
    assert result.report.rows_with_volume == 2
    assert result.report.rows_with_revenue_growth == 1
    assert result.report.rows_with_float_or_shares == 1
    assert result.report.provider_calls_attempted == 3
    assert result.report.rows_updated == 2
    assert ("volume", "Databento US Equities") in {(row.field, row.source) for row in acme.field_provenance}


def test_ask_screener_candidate_set_uses_staged_profile_enrichment_only() -> None:
    class _ProfileOnlyProvider:
        def __init__(self) -> None:
            self.profile_calls: list[tuple[str, ...]] = []
            self.quote_calls: list[tuple[str, ...]] = []

        def profile_classification(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
            requested = tuple(symbols)
            self.profile_calls.append(requested)
            return MarketQuoteFundamentalsSnapshot(
                records=(
                    MarketQuoteFundamentalsRecord("ACME", sector="Technology", industry="Software", exchange="NASDAQ", source="FMP profile"),
                    MarketQuoteFundamentalsRecord("BETA", sector="Healthcare", industry="Devices", exchange="NYSE", source="FMP profile"),
                ),
                fetched_at="2026-06-14T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP profile/classification", "available", "2026-06-14T10:00:00+00:00", "Loaded profiles."),),
                diagnostics={"provider_calls_attempted": 1, "provider_rows_requested": len(requested), "provider_rows_returned": 2, "provider_rows_parsed": 2},
            )

        def quote_fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
            self.quote_calls.append(tuple(symbols))
            raise AssertionError("classification candidate-set enrichment should not call quote_fundamentals when profile_classification is available")

    provider = _ProfileOnlyProvider()
    rows = [MarketScreenerRecord("ACME", "Acme Corp"), MarketScreenerRecord("BETA", "Beta Inc")]
    plan = validate_ask_screener_plan(
        {
            "filters": [{"field": "classification", "operator": "contains", "value": "technology"}],
            "limit": 10,
        }
    )

    result = execute_provider_aware_ask_screener_plan(
        rows,
        plan,
        provider=provider,
        config=AskScreenerProviderConfig(fmp_configured=True, profile_enrich_limit=10),
    )

    assert provider.profile_calls == [("ACME", "BETA")]
    assert provider.quote_calls == []
    assert [record.symbol for record in result.records] == ["ACME"]
    assert result.enrichment_status == "provider-enriched"
    assert "Coverage after ask_screener_candidate_set" in " ".join(result.notes)


def test_ask_screener_v2_parser_interprets_requested_phrases() -> None:
    rows = [
        MarketScreenerRecord(
            "ACME",
            "Acme Corp",
            sector="Technology",
            industry="Software",
            price=12.0,
            change_percent=4.5,
            volume=2_000,
            avg_volume=1_000,
            recent_filing_date="2026-06-10",
            signals=("Guidance mentioned",),
        ),
        MarketScreenerRecord(
            "ELEC",
            "Electronics Inc",
            sector="Consumer Cyclical",
            industry="Electronic Components",
            price=4.0,
            change_percent=-1.0,
            volume=3_000,
            avg_volume=4_000,
        ),
        MarketScreenerRecord("MISS", "Missing Data Inc"),
    ]

    tech_plan = parse_ask_screener_fallback("up to 100 Technology / Electronics sectors", records=rows)
    assert tech_plan is not None
    assert tech_plan.limit == 100
    assert tech_plan.filters[0].field == "classification"
    assert set(tech_plan.filters[0].value) >= {"technology", "electronic"}
    assert [record.symbol for record in execute_ask_screener_plan(rows, tech_plan).records] == ["ACME", "ELEC"]

    volume_plan = parse_ask_screener_fallback("highest volume today", records=rows)
    assert volume_plan is not None
    assert volume_plan.sort is not None
    assert volume_plan.sort.field == "volume"
    assert volume_plan.sort.descending is True
    assert {"price", "volume"}.issubset(set(volume_plan.required_fields))

    momentum_plan = parse_ask_screener_fallback("show momentum stocks", records=rows)
    assert momentum_plan is not None
    assert momentum_plan.filters[0].field == "momentum_proxy"
    assert [record.symbol for record in execute_ask_screener_plan(rows, momentum_plan).records] == ["ACME"]

    catalyst_plan = parse_ask_screener_fallback("recent catalysts", records=rows)
    assert catalyst_plan is not None
    assert catalyst_plan.filters[0].field == "recent_catalyst"
    assert [record.symbol for record in execute_ask_screener_plan(rows, catalyst_plan).records] == ["ACME"]

    missing_plan = parse_ask_screener_fallback("missing data", records=rows)
    assert missing_plan is not None
    assert missing_plan.filters[0].field == "has_missing_data"
    assert "MISS" in [record.symbol for record in execute_ask_screener_plan(rows, missing_plan).records]


def test_ask_screener_v2_missing_provider_config_returns_diagnostic_summary() -> None:
    rows = [MarketScreenerRecord("ACME", "Acme Corp")]
    plan = parse_ask_screener_fallback("Technology / Electronics sectors", records=rows)

    assert plan is not None
    result = execute_provider_aware_ask_screener_plan(
        rows,
        plan,
        config=AskScreenerProviderConfig(fmp_configured=False, databento_equities_configured=False),
    )

    assert result.enrichment_status == "missing-provider-config"
    assert result.rows_updated == 0
    assert result.enrichment_decision is not None
    assert result.enrichment_decision.action == ASK_SCREENER_PROVIDER_ACTION_MISSING_CONFIG
    assert "Missing provider config" in " ".join(result.notes)
    assert "status: missing-provider-config" in result.summary
    assert "more enrichment may help: no" in result.summary


def test_ask_screener_v2_provider_errors_are_nonblocking_and_redacted() -> None:
    class _FailingProvider:
        def quote_fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
            raise RuntimeError("provider failed with sk-testsecret123456")

    rows = [MarketScreenerRecord("ACME", "Acme Corp")]
    plan = parse_ask_screener_fallback("highest volume today", records=rows)

    assert plan is not None
    result = execute_provider_aware_ask_screener_plan(
        rows,
        plan,
        provider=_FailingProvider(),
        config=AskScreenerProviderConfig(databento_equities_configured=True, fmp_configured=False),
        provider_configured=True,
    )

    note_text = " ".join(result.notes)
    assert result.enrichment_status == "provider-attempted"
    assert result.records == ()
    assert "sk-testsecret" not in note_text
    assert "sk-[REDACTED]" in note_text
    assert "sk-testsecret" not in result.summary


def test_market_screener_ui_values_handle_missing_numeric_fields() -> None:
    values = earnings_radar_extension._screener_values(MarketScreenerRecord("ACME", "Acme Corp"))

    assert values[0] == "ACME"
    assert values[1] == "Universe only"
    assert values[2].endswith("low")
    assert values[3] == "Acme Corp"
    assert "--" in values
    assert "Not extracted" not in values


def test_market_screener_summary_strip_is_compact_and_keeps_counts_visible() -> None:
    records = [
        MarketScreenerRecord(
            "HOLD",
            "Held Corp",
            exchange="Nasdaq",
            sector="Technology",
            industry="Software",
            price=120.0,
            market_cap=1_000_000_000,
            volume=2_000_000,
            avg_volume=1_500_000,
            change_percent=3.2,
            pe_ratio=22.0,
            eps=1.25,
            revenue_growth=12.0,
            shares_float=50_000_000,
            next_earnings_date="2026-07-21",
            signals=("Schwab holding",),
            sources=("Local app holdings", "FMP quote"),
            source_links=("https://example.test/hold",),
            portfolio_quantity=10,
        ),
        MarketScreenerRecord("QUOT", "Quote Corp", price=14.0, volume=100_000, sources=("Schwab quote",)),
        MarketScreenerRecord("FILI", "Filing Corp", recent_filing_date="2026-06-12", sources=("SEC EDGAR",)),
        MarketScreenerRecord("BASE", "Base Corp"),
    ]

    groups = earnings_radar_extension._screener_summary_groups(records)
    canvas = _FakeCanvas()
    earnings_radar_extension._draw_screener_summary_strip(canvas, groups)
    text_values = [kwargs["text"] for call, _args, kwargs in canvas.calls if call == "text"]

    assert earnings_radar_extension.MARKET_SCREENER_SUMMARY_STRIP_HEIGHT < earnings_radar_extension.EARNINGS_RADAR_CHART_HEIGHT
    assert earnings_radar_extension.MARKET_SCREENER_TABLE_ROWHEIGHT == 38
    assert groups[0][1] == {"My Holdings": 1, "Not held": 3}
    assert "Portfolio" in text_values
    assert "Data" in text_values
    assert "Completeness" in text_values
    assert "My Holdings" in text_values
    assert "Not held" in text_values
    assert "Universe only" in text_values
    assert "1" in text_values
    assert "3" in text_values
    assert any(call == "rectangle" for call, _args, _kwargs in canvas.calls)


def test_market_screener_summary_toggle_collapses_canvas_and_restores_redraw(monkeypatch) -> None:
    canvas = _FakeCanvas()
    app = SimpleNamespace(
        market_screener_summary_visible_var=_FakeTkVar(True),
        market_screener_summary_toggle_var=_FakeTkVar("Hide Summary"),
        market_screener_chart=canvas,
        market_screener_filtered_records=[MarketScreenerRecord("ACME", "Acme Corp")],
    )
    redraws: list[list[MarketScreenerRecord]] = []
    monkeypatch.setattr(earnings_radar_extension, "_draw_screener_chart", lambda target: redraws.append(list(target.market_screener_filtered_records)))

    earnings_radar_extension._toggle_screener_summary(app)

    assert app.market_screener_summary_visible_var.get() is False
    assert app.market_screener_summary_toggle_var.get() == "Show Summary"
    assert canvas.visible is False
    assert canvas.grid_calls[-1][0] == "grid_remove"
    assert redraws == []

    app.market_screener_filtered_records = [MarketScreenerRecord("BETA", "Beta Inc")]
    earnings_radar_extension._toggle_screener_summary(app)

    assert app.market_screener_summary_visible_var.get() is True
    assert app.market_screener_summary_toggle_var.get() == "Hide Summary"
    assert canvas.visible is True
    assert canvas.grid_calls[-1][0] == "grid"
    assert [[record.symbol for record in rows] for rows in redraws] == [["BETA"]]


def test_high_volume_mover_filter_requires_actual_market_data() -> None:
    no_market_data = MarketScreenerRecord("ACME", "Acme Corp")
    changed_without_volume = MarketScreenerRecord("BETA", "Beta Inc", change_percent=None, volume=None, avg_volume=None)
    mover = MarketScreenerRecord("GAMMA", "Gamma Inc", change_percent=-5.1)
    high_volume = MarketScreenerRecord("DELTA", "Delta Inc", volume=2_000_000, avg_volume=1_000_000)
    schwab_quote_only = build_market_screener_records(
        [MarketUniverseEntry("EPSI", "Epsilon Inc")],
        market_data_records=[MarketQuoteFundamentalsRecord("EPSI", price=12.5, volume=100_000, source="Schwab quote")],
        fetched_at="2026-06-14T10:00:00+00:00",
    )[0]

    assert filter_market_screener_records(
        [no_market_data, changed_without_volume, mover, high_volume, schwab_quote_only],
        event_type="High volume / mover",
    ) == [mover, high_volume]
    values = earnings_radar_extension._screener_values(schwab_quote_only)
    assert values[1] == "Quote"
    assert values[7] == "$12.50"
    assert values[8] == "--"
    assert values[10] == "--"
    assert "High volume" not in schwab_quote_only.signals
    assert "Mover" not in schwab_quote_only.signals


def test_high_volume_mover_empty_state_explains_missing_fields() -> None:
    text = earnings_radar_extension._high_volume_mover_empty_state_text(
        [
            MarketScreenerRecord("ACME", "Acme Corp"),
            MarketScreenerRecord("BETA", "Beta Inc", volume=100_000),
        ]
    )

    assert "High Volume / Mover returned 0 rows because mover fields are sparse" in text
    assert "missing change %" in text
    assert "missing avg volume" in text
    assert "Missing values are not treated as zero" in text
    assert "Enrich Current Page" in text
    assert "Re-enrich Current Page" in text

    threshold_text = earnings_radar_extension._high_volume_mover_empty_state_text(
        [MarketScreenerRecord("GAMMA", "Gamma Inc", change_percent=1.2, volume=100_000, avg_volume=100_000)]
    )

    assert "no change % reached +/-5%" in threshold_text
    assert "no volume reached 1.5x average volume" in threshold_text

    cap_text = earnings_radar_extension._high_volume_mover_empty_state_text(
        [MarketScreenerRecord("ZETA", "Zeta Inc")],
        diagnostics=SimpleNamespace(rows_skipped_by_configured_symbol_cap=7),
    )
    assert "7 row(s) skipped by cap" in cap_text
    assert "FMP_MARKET_DATA_SYMBOL_LIMIT up to 100" in cap_text


def test_screener_source_diagnostics_popout_shows_full_counters_and_company_tickers_limits() -> None:
    record = MarketScreenerRecord("ACME", "Acme Corp", sources=("SEC company_tickers.json",))
    snapshot = MarketScreenerSnapshot(
        records=(record,),
        fetched_at="2026-06-14T10:00:00+00:00",
        sources=("SEC company_tickers.json",),
        statuses=(
            MarketScreenerSourceStatus(
                "FMP profile",
                "unavailable",
                "2026-06-14T10:00:00+00:00",
                "Plan limit reached for profile endpoint.",
            ),
        ),
        diagnostics=MarketScreenerCoverageDiagnostics(
            total_rows=1,
            rows_with_symbol=1,
            rows_missing_cik=1,
            rows_enriched_by_fmp_profile_by_cik=0,
            rows_blocked_by_provider_plan_rate_auth_limit=1,
            rows_skipped_by_configured_symbol_cap=2,
            provider_unavailable=1,
            rows_still_missing_price_volume=1,
            rows_still_missing_fundamentals=1,
        ),
    )

    text = earnings_radar_extension._screener_diagnostics_popout_text(snapshot, selected_record=record)

    assert "SOURCE DIAGNOSTICS / WHY BLANKS?" in text
    assert "SEC company_tickers supplies symbol, company name, and CIK only" in text
    assert "Rows skipped by configured symbol cap: 2" in text
    assert "Rows blocked by provider plan/rate/auth limit: 1" in text
    assert "FMP quote/profile/profile-by-CIK" in text
    assert "Would paid FMP help?" in text
    assert "Selected row why blanks?" in text
    assert "fallback disabled/not attempted" in text


def test_screener_diagnostics_popout_shows_provider_and_market_cap_rank_monitor_output() -> None:
    record = MarketScreenerRecord(
        "SONY",
        "Sony Group Corp ADR",
        exchange="NYSE",
        country="Japan",
        market_cap=24_000_000_000_000,
        market_cap_currency="JPY",
        is_adr=True,
        sources=("FMP profile",),
    )
    snapshot = MarketScreenerSnapshot(
        records=(record,),
        fetched_at="2026-06-18T12:00:00+00:00",
        sources=("FMP profile",),
        statuses=(
            MarketScreenerSourceStatus(
                "FMP quote/fundamentals",
                "partial",
                "2026-06-18T12:00:00+00:00",
                "FMP enrichment: 1 rows updated; provider calls attempted 2; cache used for 3; 4 skipped/limited; 0 no usable data.",
            ),
        ),
        diagnostics=MarketScreenerCoverageDiagnostics(
            total_rows=1,
            rows_with_symbol=1,
            rows_with_untrusted_market_cap=1,
            rows_with_non_usd_market_cap=1,
            rows_missing_market_cap=0,
            rows_skipped_by_configured_symbol_cap=4,
            provider_cache_hits=3,
            provider_warnings=2,
            provider_rows_requested=5,
            provider_rows_returned=1,
            provider_rows_updated=1,
            major_us_large_caps_absent=13,
        ),
        errors=("rate warning",),
    )

    text = earnings_radar_extension._screener_diagnostics_popout_text(
        snapshot,
        selected_record=record,
        session_lines=("Market data current page snapshot: page_symbols=[MSFT, SONY]; requesting=[MSFT, SONY].",),
    )

    assert "Provider config/caps" in text
    assert "MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT" in text
    assert "Market cap ranking diagnostics" in text
    assert "Untrusted market caps: 1" in text
    assert "Non-USD market caps: 1" in text
    assert "Major-cap diagnostics" in text
    assert "MSFT absent:" in text
    assert "Provider cache hits: 3" in text
    assert "Provider warnings: 2" in text
    assert "Rows skipped by configured symbol cap: 4" in text
    assert "Market data current page snapshot: page_symbols=[MSFT, SONY]" in text
    assert "rate warning" in text


def test_screener_detail_text_includes_snapshot_provider_reasons_for_blanks() -> None:
    record = MarketScreenerRecord("ACME", "Acme Corp", sources=("SEC company_tickers.json",))
    snapshot = MarketScreenerSnapshot(
        records=(record,),
        fetched_at="2026-06-14T10:00:00+00:00",
        sources=("SEC company_tickers.json",),
        statuses=(
            MarketScreenerSourceStatus(
                "Market data enrichment",
                "empty",
                "2026-06-14T10:00:00+00:00",
                "Provider returned 0 usable quote/profile row(s).",
            ),
            MarketScreenerSourceStatus(
                "FMP quote/profile",
                "unavailable",
                "2026-06-14T10:00:00+00:00",
                "API key plan limit reached.",
            ),
        ),
        diagnostics=MarketScreenerCoverageDiagnostics(
            total_rows=1,
            rows_with_symbol=1,
            rows_missing_cik=1,
            rows_blocked_by_provider_plan_rate_auth_limit=1,
            rows_skipped_by_configured_symbol_cap=4,
            rows_provider_returned_no_usable_data=1,
            provider_unavailable=1,
            rows_still_missing_price_volume=1,
            rows_still_missing_fundamentals=1,
        ),
    )

    detail = earnings_radar_extension._screener_detail_text(
        record,
        snapshot=snapshot,
        session_lines=("Market data selected row: enriched 0 of 1 requested symbol(s). Provider empty.",),
    )

    assert "Source-aware missing reasons:" in detail
    assert "missing CIK" in detail
    assert "skipped by cap: 4 row(s)" in detail
    assert "provider unavailable: 1 provider status row(s)" in detail
    assert "provider returned no usable data: 1 requested row(s)" in detail
    assert "provider auth/plan/rate limit: 1 provider attempt(s)" in detail
    assert "fallback disabled/not attempted" in detail
    assert "session enrichment detail: Market data selected row: enriched 0 of 1 requested symbol(s)." in detail


def test_screener_diagnostics_selected_row_groups_blank_reasons_by_source_family() -> None:
    record = MarketScreenerRecord("ACME", "Acme Corp", price=12.0, market_cap=1_000_000_000, sources=("SEC company_tickers.json",))
    snapshot = MarketScreenerSnapshot(
        records=(record,),
        fetched_at="2026-06-14T10:00:00+00:00",
        sources=("SEC company_tickers.json",),
        statuses=(
            MarketScreenerSourceStatus(
                "Databento US Equities",
                "disabled",
                "2026-06-14T10:00:00+00:00",
                "Databento US Equities disabled; set MARKET_SCREENER_ENABLE_DATABENTO_EQUITIES=true with dataset/schema to add equity tape fields.",
            ),
            MarketScreenerSourceStatus(
                "Databento US Equities",
                "warning",
                "2026-06-14T10:00:00+00:00",
                "Config warning: schema 'statistics' is unsupported for intraday screener tape fields.",
            ),
            MarketScreenerSourceStatus(
                "FMP quote/fundamentals",
                "empty",
                "2026-06-14T10:00:00+00:00",
                "FMP enrichment: 0 rows updated; 1 no usable data.",
            ),
        ),
        diagnostics=MarketScreenerCoverageDiagnostics(
            total_rows=1,
            rows_with_symbol=1,
            rows_missing_cik=1,
            rows_provider_returned_no_usable_data=1,
            rows_still_missing_price_volume=1,
            rows_still_missing_fundamentals=1,
        ),
    )

    text = earnings_radar_extension._screener_diagnostics_popout_text(snapshot, selected_record=record)

    assert "missing quote/tape fields: volume, change_percent, avg_volume" in text
    assert "missing profile fields: exchange, sector, industry" in text
    assert "missing FMP/profile fundamental fields: pe_ratio, eps, revenue_growth, shares_float, shares_outstanding" in text
    assert "provider disabled/missing config detail: Databento US Equities disabled" in text
    assert "unsupported source field detail: Databento US Equities warning" in text
    assert "provider returned no usable data detail: FMP quote/fundamentals empty" in text


def test_market_screener_missing_providers_degrade_to_fallback_and_status_messages(monkeypatch) -> None:
    monkeypatch.delenv("FMP_API_KEY", raising=False)
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
    assert any(status.source == "Market data enrichment" and status.status == "unavailable" for status in snapshot.statuses)


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


def test_market_screener_ai_payload_keeps_cross_asset_context_separate() -> None:
    record = MarketScreenerRecord("ACME", "Acme Corp", price=12.5, volume=1_000, sources=("Databento US Equities",))

    payload = market_screener_ai_request_payload(
        record,
        "Explain the row with macro context.",
        cross_asset_context={
            "records": [
                {
                    "symbol": "ES.FUT",
                    "price": 5500.25,
                    "volume": 12500,
                    "dataset": "GLBX.MDP3",
                    "schema": "ohlcv-1m",
                    "source": "Databento CME context",
                }
            ]
        },
        timeout_seconds=44,
    )

    assert payload["selected_market_screener_record"]["symbol"] == "ACME"
    assert payload["selected_market_screener_record"]["market_data"]["price"] == 12.5
    assert payload["cross_asset_context"]["records"][0]["symbol"] == "ES.FUT"
    assert "ES.FUT" not in json.dumps(payload["selected_market_screener_record"])
    assert "cross_asset_context only as separate CME/futures/options" in " ".join(payload["grounding_rules"])


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
