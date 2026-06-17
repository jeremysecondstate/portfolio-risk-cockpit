from __future__ import annotations

from types import SimpleNamespace

from app.analytics.technical_analysis import (
    Candle,
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
                        shares_float=50_000_000,
                        source="FMP fundamentals",
                    ),
                ),
                fetched_at="2026-06-17T10:00:00+00:00",
                statuses=(MarketDataProviderStatus("FMP fundamentals", "available", "2026-06-17T10:00:00+00:00", "Loaded fundamentals."),),
            )

    intelligence = build_external_market_intelligence(
        "ACME",
        fmp_provider=_FakeFmpProvider(),
        databento_equities_provider=DatabentoEquitiesProvider(enabled=False, api_key="", dataset="", schema="", client=_FakeDatabentoClient({})),
        databento_cme_context_provider=DatabentoCmeContextProvider(enabled=False, api_key="", dataset="", schema="", symbols=(), client=_FakeDatabentoClient({})),
    )

    assert intelligence.fmp_profile["sector"] == "Technology"
    assert intelligence.fmp_quote["price"] == 12.25
    assert intelligence.fmp_fundamentals["market_cap"] == 1_000_000_000
    assert any(status.source == "FMP fundamentals" and status.status == "available" for status in intelligence.source_statuses)


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
    assert "FMP profile/classification" in text
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
