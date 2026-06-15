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
    market_screener_data_completeness_score,
    market_screener_data_label,
    market_screener_diagnostics_summary,
    market_screener_is_my_holding,
    market_screener_record_has_quote_fields,
    merge_market_data_records_into_screener_records,
    sort_market_screener_records,
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
    def __init__(self, value: str = "") -> None:
        self.value = value

    def set(self, value: str) -> None:
        self.value = value


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


class _FakeTree:
    def __init__(self, selection=()) -> None:
        self._selection = tuple(selection)

    def selection(self):
        return self._selection

    def select(self, *iids: str) -> None:
        self._selection = tuple(iids)


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
        if url.endswith("/batch-quote"):
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
    assert record.sector == "Technology"
    assert record.industry == "Software"
    assert record.exchange == "NASDAQ"
    assert record.shares_float == 120_000_000
    assert record.shares_outstanding == 150_000_000
    assert record.source == "FMP quote, FMP profile"
    status_text = " ".join(status.message for status in snapshot.statuses) + " " + " ".join(snapshot.errors)
    assert api_key not in status_text
    assert all(api_key not in str(call["url"]) for call in session.calls)
    assert all("apikey" not in call["params"] for call in session.calls)


def test_fmp_missing_key_is_optional_and_does_not_call_network() -> None:
    session = _FakeFmpSession(lambda _url, _params: _FakeFmpResponse(200, []))
    provider = FmpQuoteFundamentalsProvider(api_key="", session=session)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    assert snapshot.records == ()
    assert session.calls == []
    assert snapshot.statuses[0].source == "FMP quote/fundamentals"
    assert snapshot.statuses[0].status == "unavailable"
    assert "No FMP_API_KEY is configured" in snapshot.statuses[0].message


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
        if url.endswith("/batch-quote"):
            symbols = str(params["symbols"]).split(",")
            assert symbols == ["SYM000", "SYM001"]
            return _FakeFmpResponse(200, [{"symbol": symbol, "price": index + 1.0} for index, symbol in enumerate(symbols)])
        if url.endswith("/profile"):
            return _FakeFmpResponse(200, [{"symbol": params["symbol"], "sector": "Technology"}])
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
        if url.endswith("/batch-quote"):
            return _FakeFmpResponse(200, [{"symbol": "ACME", "marketCap": 5_000_000_000, "peRatio": 20.0}])
        if url.endswith("/profile"):
            return _FakeFmpResponse(200, [{"symbol": params["symbol"], "sector": "Technology", "industry": "Software"}])
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
    assert market_screener_data_label(acme) == "Quote + Filing + Earnings"
    assert market_screener_data_label(holding_only) == "Holding"
    assert market_screener_data_label(filing_only) == "Filing"
    assert market_screener_data_label(earnings_only) == "Earnings"
    assert market_screener_data_label(beta) == "Universe only"


def test_market_screener_ui_values_handle_missing_numeric_fields() -> None:
    values = earnings_radar_extension._screener_values(MarketScreenerRecord("ACME", "Acme Corp"))

    assert values[0] == "ACME"
    assert values[1] == "Universe only"
    assert values[2].endswith("low")
    assert values[3] == "Acme Corp"
    assert "--" in values
    assert "Not extracted" not in values


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
