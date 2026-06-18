from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.analytics.technical_analysis import (
    Candle,
    QuoteSnapshot,
    build_technical_command_center_report,
    format_technical_command_center_report,
)
from app.data.databento_provider import DatabentoCmeContextProvider, DatabentoEquitiesProvider
from app.data.market_data_provider import (
    FmpQuoteFundamentalsProvider,
    MarketDataProviderStatus,
    MarketQuoteFundamentalsRecord,
    MarketQuoteFundamentalsSnapshot,
)
from app.data.market_intelligence import ExternalMarketIntelligence, build_external_market_intelligence


class _FakeDatabentoClient:
    def __init__(self, rows_by_symbol: dict[str, object]) -> None:
        self.rows_by_symbol = rows_by_symbol
        self.equity_calls: list[dict[str, object]] = []
        self.context_calls: list[dict[str, object]] = []

    def fetch_equity_rows(self, *, symbols, dataset, schema):
        self.equity_calls.append({"symbols": tuple(symbols), "dataset": dataset, "schema": schema})
        return {symbol: self.rows_by_symbol[symbol] for symbol in symbols if symbol in self.rows_by_symbol}

    def fetch_context_rows(self, *, symbols, dataset, schema):
        self.context_calls.append({"symbols": tuple(symbols), "dataset": dataset, "schema": schema})
        return {symbol: self.rows_by_symbol[symbol] for symbol in symbols if symbol in self.rows_by_symbol}


def _candles(count: int, *, start: float = 100.0, step: float = 0.20) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        close = price + step
        rows.append(
            Candle(
                datetime_ms=1_700_000_000_000 + index * 60_000,
                open=price,
                high=max(price, close) + 0.30,
                low=min(price, close) - 0.30,
                close=close,
                volume=10_000 + index * 100,
            )
        )
        price = close
    return rows


def _price_history_payload(count: int = 90) -> dict[str, object]:
    return {
        "candles": [
            {
                "datetime": candle.datetime_ms,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in _candles(count)
        ]
    }


def _fresh_minute_candles(count: int, *, start: float = 20.0, step: float = 0.05) -> list[Candle]:
    base_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - (count * 60_000)
    rows: list[Candle] = []
    price = start
    for index in range(count):
        close = price + step
        rows.append(Candle(base_ms + index * 60_000, price, max(price, close) + 0.10, min(price, close) - 0.10, close, 1_000 + index))
        price = close
    return rows


def test_external_market_intelligence_all_external_providers_disabled_or_missing_keys(monkeypatch) -> None:
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    client = _FakeDatabentoClient({"ACME": {"symbol": "ACME", "price": 12.0}})
    intelligence = build_external_market_intelligence(
        "ACME",
        fmp_provider=FmpQuoteFundamentalsProvider(api_key="", session=object()),
        databento_equities_provider=DatabentoEquitiesProvider(enabled=False, api_key="", dataset="", schema="", client=client),
        databento_cme_context_provider=DatabentoCmeContextProvider(enabled=False, api_key="", dataset="", schema="", symbols=(), client=client),
    )

    statuses = {(status.source, status.status) for status in intelligence.source_statuses}
    assert intelligence.fmp_profile == {}
    assert intelligence.fmp_quote == {}
    assert intelligence.fmp_fundamentals == {}
    assert intelligence.fmp_macro_context == {}
    assert intelligence.databento_equity_tape == {}
    assert intelligence.databento_futures_context == {}
    assert ("FMP profile/classification", "unavailable") in statuses
    assert ("Databento US Equities", "disabled") in statuses
    assert ("Databento CME context", "disabled") in statuses
    assert client.equity_calls == []
    assert client.context_calls == []


def test_external_market_intelligence_fmp_only_enrichment() -> None:
    class _FakeFmpProvider:
        api_key = "fmp-test-key"

        def profile_classification(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
            return MarketQuoteFundamentalsSnapshot(
                records=(
                    MarketQuoteFundamentalsRecord(
                        "ACME",
                        company_name="Acme Corp",
                        exchange="NASDAQ",
                        sector="Technology",
                        industry="Software",
                        source="FMP profile",
                        fetched_at="2026-06-17T10:00:00+00:00",
                    ),
                ),
                fetched_at="2026-06-17T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP profile/classification", "available", "2026-06-17T10:00:00+00:00", "Loaded profile."),),
            )

        def quote_tape(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
            return MarketQuoteFundamentalsSnapshot(
                records=(MarketQuoteFundamentalsRecord("ACME", price=12.25, volume=2500, change_percent=1.5, source="FMP quote"),),
                fetched_at="2026-06-17T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP quote/tape", "available", "2026-06-17T10:00:00+00:00", "Loaded quote."),),
            )

        def fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
            return MarketQuoteFundamentalsSnapshot(
                records=(
                    MarketQuoteFundamentalsRecord(
                        "ACME",
                        market_cap=1_000_000_000,
                        pe_ratio=22.0,
                        eps=1.25,
                        revenue_growth=14.5,
                        revenue=500_000_000,
                        net_income=75_000_000,
                        operating_income=90_000_000,
                        operating_income_yoy=18.0,
                        operating_cash_flow=110_000_000,
                        operating_cash_flow_yoy=12.0,
                        cash_and_equivalents=240_000_000,
                        total_liabilities=600_000_000,
                        cash_to_liabilities=40.0,
                        shares_float=50_000_000,
                        source="FMP fundamentals",
                    ),
                ),
                fetched_at="2026-06-17T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP fundamentals", "available", "2026-06-17T10:00:00+00:00", "Loaded fundamentals."),),
            )

        def filing_metadata(self, symbol, *, force_refresh: bool = False, limit: int = 12):
            return (
                {
                    "symbol": symbol,
                    "companyName": "Acme Corp",
                    "cik": "0001234567",
                    "form": "424B5",
                    "filingDate": "2026-06-12",
                    "accessionNumber": "0001234567-26-000001",
                    "primaryDocument": "acme-424b5.htm",
                    "finalLink": "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/acme-424b5.htm",
                    "description": "prefilter row fmp-test-key",
                },
            )

        def macro_context(self, symbol, *, force_refresh: bool = False, limit: int = 2):
            return {
                "CPI": {
                    "category": "Inflation",
                    "metric": "CPI economic indicator",
                    "value": 3.2,
                    "prior": 3.0,
                    "date": "2026-05",
                    "source": "FMP macro proxy fmp-test-key",
                }
            }

    intelligence = build_external_market_intelligence(
        "ACME",
        fmp_provider=_FakeFmpProvider(),
        databento_equities_provider=DatabentoEquitiesProvider(enabled=False, api_key="", dataset="", schema="", client=_FakeDatabentoClient({})),
        databento_cme_context_provider=DatabentoCmeContextProvider(enabled=False, api_key="", dataset="", schema="", symbols=(), client=_FakeDatabentoClient({})),
    )

    assert intelligence.fmp_profile["sector"] == "Technology"
    assert intelligence.fmp_quote["price"] == 12.25
    assert intelligence.fmp_fundamentals["market_cap"] == 1_000_000_000
    assert intelligence.fmp_fundamentals["operating_income_yoy"] == 18.0
    assert intelligence.fmp_fundamentals["cash_to_liabilities"] == 40.0
    assert intelligence.fmp_filing_metadata[0]["primaryDocument"] == "acme-424b5.htm"
    assert intelligence.fmp_filing_metadata[0]["description"] == "prefilter row [REDACTED]"
    assert intelligence.fmp_macro_context["CPI"]["value"] == 3.2
    assert intelligence.fmp_macro_context["CPI"]["source"] == "FMP macro proxy [REDACTED]"
    assert "fmp-test-key" not in str(intelligence.fmp_filing_metadata)
    assert "fmp-test-key" not in str(intelligence.fmp_macro_context)
    assert "fmp-test-key" not in str(intelligence.provenance)
    assert any(status.source == "FMP fundamentals" and status.status == "available" for status in intelligence.source_statuses)
    assert any(status.source == "FMP filing metadata" and status.status == "available" for status in intelligence.source_statuses)
    assert any(status.source == "FMP macro context" and status.status == "available" for status in intelligence.source_statuses)


def test_external_market_intelligence_databento_equities_ohlcv_tape_and_candles() -> None:
    client = _FakeDatabentoClient(
        {
            "ACME": [
                {"symbol": "ACME", "open": 10.0, "high": 10.5, "low": 9.8, "close": 10.25, "volume": 100, "ts_event": "2026-06-17T14:30:00+00:00"},
                {"symbol": "ACME", "open": 10.25, "high": 11.1, "low": 10.2, "close": 11.0, "volume": 250, "ts_event": "2026-06-17T14:31:00+00:00"},
            ]
        }
    )
    provider = DatabentoEquitiesProvider(
        enabled=True,
        api_key="db-test-key",
        dataset="XNAS.ITCH",
        schema="ohlcv-1m",
        client=client,
        cache_ttl_seconds=600,
    )

    intelligence = build_external_market_intelligence(
        "ACME",
        fmp_provider=FmpQuoteFundamentalsProvider(api_key="", session=object()),
        databento_equities_provider=provider,
        databento_cme_context_provider=DatabentoCmeContextProvider(enabled=False, api_key="", dataset="", schema="", symbols=(), client=client),
    )

    assert intelligence.databento_equity_tape["price"] == 11.0
    assert intelligence.databento_equity_tape["volume"] == 250
    assert len(intelligence.databento_equity_candles["ACME"]) == 2
    assert intelligence.databento_equity_candles["ACME"][-1].close == 11.0
    assert client.equity_calls == [{"symbols": ("ACME",), "dataset": "XNAS.ITCH", "schema": "ohlcv-1m"}]


def test_external_market_intelligence_databento_cme_context_stays_separate() -> None:
    client = _FakeDatabentoClient(
        {
            "ES.FUT": {"symbol": "ES.FUT", "close": 5500.25, "volume": 12500, "ts_event": "2026-06-17T15:00:00+00:00"},
        }
    )

    intelligence = build_external_market_intelligence(
        "ACME",
        fmp_provider=FmpQuoteFundamentalsProvider(api_key="", session=object()),
        databento_equities_provider=DatabentoEquitiesProvider(enabled=False, api_key="", dataset="", schema="", client=client),
        databento_cme_context_provider=DatabentoCmeContextProvider(
            enabled=True,
            api_key="db-test-key",
            dataset="GLBX.MDP3",
            schema="ohlcv-1m",
            symbols=("ES.FUT",),
            client=client,
        ),
    )

    assert intelligence.databento_equity_tape == {}
    assert intelligence.databento_futures_context["ES.FUT"]["price"] == 5500.25
    assert "ES.FUT" not in intelligence.fmp_quote
    assert client.context_calls == [{"symbols": ("ES.FUT",), "dataset": "GLBX.MDP3", "schema": "ohlcv-1m"}]


def test_command_center_report_surfaces_external_intelligence_statuses_and_context() -> None:
    intelligence = ExternalMarketIntelligence(
        symbol="ACME",
        fmp_profile={"company_name": "Acme Corp", "sector": "Technology", "industry": "Software", "exchange": "NASDAQ"},
        fmp_fundamentals={"market_cap": 1_000_000_000, "pe_ratio": 22.0},
        databento_equity_tape={"price": 11.0, "volume": 250, "fetched_at": "2026-06-17T14:31:00+00:00"},
        databento_futures_context={"ES.FUT": {"price": 5500.25, "volume": 12500, "timestamp": "2026-06-17T15:00:00+00:00"}},
        source_statuses=(MarketDataProviderStatus("FMP fundamentals", "available", "2026-06-17T10:00:00+00:00", "Loaded fundamentals."),),
        warnings=("External source warning.",),
    )

    report = build_technical_command_center_report(
        "ACME",
        {"daily_1y": _candles(90), "timing_5m": _candles(80, start=110.0, step=0.05)},
        external_intelligence=intelligence,
    )
    text = format_technical_command_center_report(report)

    assert "External source warning." in report.warnings
    assert "MARKET INTELLIGENCE SOURCES" in text
    assert "SOURCE ROUTING PLAN" in text
    assert "FMP profile/classification" in text
    assert "fundamentals: FMP fundamentals" in text
    assert "Databento CME/futures cross-asset context is kept separate" in text
    assert "ES.FUT" in text


def test_show_technical_analysis_still_succeeds_when_enrichment_returns_warnings_only(monkeypatch) -> None:
    from app.ui import trading_cockpit

    class _Var:
        def __init__(self, value: str = "") -> None:
            self.value = value

        def get(self) -> str:
            return self.value

        def set(self, value: str) -> None:
            self.value = value

    class _FakeSession:
        def get_price_history(self, symbol, **kwargs):
            return 200, _price_history_payload()

        def get_quote(self, symbol):
            return 200, {symbol: {"quote": {"bidPrice": 109.95, "askPrice": 110.05, "lastPrice": 110.0, "totalVolume": 5000}}}

    captured: list[str] = []
    app = SimpleNamespace(
        symbol_var=_Var("ACME"),
        schwab_status_var=_Var(),
        _authorize_schwab_session=lambda: _FakeSession(),
        _set_preview_text=lambda text: captured.append(text),
        format_technical_analysis_report=lambda report: format_technical_command_center_report(report),
    )
    monkeypatch.setattr(
        trading_cockpit,
        "build_external_market_intelligence",
        lambda *args, **kwargs: ExternalMarketIntelligence(
            "ACME",
            source_statuses=(MarketDataProviderStatus("External provider", "warning", "2026-06-17T10:00:00+00:00", "Warning-only enrichment."),),
            warnings=("External warning only.",),
        ),
    )
    monkeypatch.setattr(trading_cockpit, "analyze_capital_structure_pressure", lambda symbol: trading_cockpit.unknown_capital_structure_report(symbol))
    monkeypatch.setattr(trading_cockpit.messagebox, "showerror", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError(args)))

    trading_cockpit.SchwabTradingCockpitApp.show_technical_analysis(app)

    assert app.schwab_status_var.get() == "Schwab: connected"
    assert captured
    assert "MARKET INTELLIGENCE SOURCES" in captured[0]
    assert "External warning only." in captured[0]


def test_command_center_uses_databento_candles_when_schwab_timeframe_missing() -> None:
    intelligence = ExternalMarketIntelligence(
        symbol="ACME",
        databento_equity_tape={"price": 22.0, "volume": 2_000, "avg_volume": 1_000, "fetched_at": datetime.now(timezone.utc).isoformat()},
        databento_equity_candles={"ACME": tuple(_fresh_minute_candles(60, start=20.0, step=0.04))},
    )

    report = build_technical_command_center_report(
        "ACME",
        {"daily_1y": _candles(90), "timing_5m": []},
        external_intelligence=intelligence,
    )
    text = format_technical_command_center_report(report)

    timing_source = report.timeframe_source_labels["timing_5m"]
    assert timing_source.source == "Databento"
    assert report.snapshots["timing_5m"].candle_count > 0
    assert report.snapshots["timing_5m"].source == "Databento"
    assert "TIMEFRAME DATA SOURCES" in text
    assert "Databento selected-equity candles influenced VWAP" in text


def test_command_center_does_not_use_short_databento_tape_as_daily_regime_history() -> None:
    intelligence = ExternalMarketIntelligence(
        symbol="ACME",
        databento_equity_tape={"price": 22.0, "volume": 2_000, "fetched_at": datetime.now(timezone.utc).isoformat()},
        databento_equity_candles={"ACME": tuple(_fresh_minute_candles(60, start=20.0, step=0.04))},
    )

    report = build_technical_command_center_report(
        "ACME",
        {"daily_1y": []},
        external_intelligence=intelligence,
    )
    text = format_technical_command_center_report(report)

    daily_source = report.timeframe_source_labels["daily_1y"]
    assert daily_source.source == "unavailable"
    assert report.snapshots["daily_1y"].candle_count == 0
    assert "short tape context was skipped" in daily_source.reason
    assert "short tape context was skipped" in text


def test_command_center_uses_explicit_databento_timeframe_history_for_missing_daily() -> None:
    intelligence = ExternalMarketIntelligence(
        symbol="ACME",
        databento_technical_candles={"daily_1y": tuple(_candles(90, start=50.0, step=0.10))},
        provenance={
            "databento_technical_history": {
                "daily_1y": {
                    "status": "available",
                    "requested_lookback_minutes": 370 * 24 * 60,
                    "rows_returned": 1,
                }
            }
        },
    )

    report = build_technical_command_center_report(
        "ACME",
        {"daily_1y": []},
        external_intelligence=intelligence,
    )

    daily_source = report.timeframe_source_labels["daily_1y"]
    assert daily_source.source == "Databento"
    assert report.snapshots["daily_1y"].candle_count == 90
    assert "Requested Databento lookback" in daily_source.reason
    assert report.data_plan is not None
    assert any(decision.domain == "technical_history:daily_1y" and decision.selected_source == "Databento" for decision in report.data_plan.decisions)


def test_command_center_demotes_confidence_on_external_quote_conflict() -> None:
    intelligence = ExternalMarketIntelligence(
        symbol="ACME",
        fmp_profile={"company_name": "Acme Corp", "sector": "Technology", "industry": "Software"},
        fmp_quote={"price": 105.0, "volume": 10_000},
        fmp_fundamentals={"market_cap": 1_000_000_000, "revenue_growth": 12.0, "shares_float": 50_000_000},
        databento_equity_tape={"price": 99.75, "volume": 10_000, "avg_volume": 7_000, "fetched_at": datetime.now(timezone.utc).isoformat()},
    )

    report = build_technical_command_center_report(
        "ACME",
        {"daily_1y": _candles(90), "setup_30m": _candles(90), "timing_5m": _candles(90)},
        quote_snapshot=QuoteSnapshot("ACME", bid=99.95, ask=100.05, last=100.0, mark=100.0),
        external_intelligence=intelligence,
    )
    text = format_technical_command_center_report(report)

    assert any("Source conflict" in warning for warning in report.warnings)
    assert report.best_action == "Wait for source confirmation"
    assert report.confidence in {"Medium", "Low"}
    assert "DECISION-WEIGHTED READ" in text
    assert "Market Intelligence" in report.scores


def test_external_market_intelligence_redacts_provider_secrets_from_statuses_and_warnings() -> None:
    secret = "secret-token-12345"

    class _SecretFmpProvider:
        api_key = secret

        def profile_classification(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at="2026-06-17T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP profile/classification", "warning", "2026-06-17T10:00:00+00:00", f"profile failed with {secret}"),),
                errors=(f"profile error {secret}",),
            )

        def quote_tape(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at="2026-06-17T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP quote/tape", "warning", "2026-06-17T10:00:00+00:00", f"quote failed with api_key={secret}"),),
                errors=(f"quote error {secret}",),
            )

        def fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 1):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at="2026-06-17T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP fundamentals", "warning", "2026-06-17T10:00:00+00:00", f"fundamentals failed with {secret}"),),
                errors=(f"fundamentals error {secret}",),
            )

    intelligence = build_external_market_intelligence(
        "ACME",
        fmp_provider=_SecretFmpProvider(),
        databento_equities_provider=DatabentoEquitiesProvider(enabled=False, api_key="", dataset="", schema="", client=_FakeDatabentoClient({})),
        databento_cme_context_provider=DatabentoCmeContextProvider(enabled=False, api_key="", dataset="", schema="", symbols=(), client=_FakeDatabentoClient({})),
    )

    combined = " ".join([*intelligence.source_status_lines(), *intelligence.warnings])
    assert secret not in combined
    assert "[REDACTED]" in combined
