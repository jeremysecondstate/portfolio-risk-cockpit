from __future__ import annotations

from types import SimpleNamespace

from app.analytics.market_screener import (
    MARKET_SCREENER_REFRESH_FILINGS,
    MARKET_SCREENER_REFRESH_FUNDAMENTALS,
    MARKET_SCREENER_REFRESH_PROFILE,
    MARKET_SCREENER_REFRESH_QUOTE_TAPE,
    MarketScreenerBackfillConfig,
    MarketScreenerRecord,
    rebuild_market_screener_snapshot,
    refresh_market_screener_records,
)
from app.data.market_data_provider import (
    MarketDataProviderStatus,
    MarketQuoteFundamentalsRecord,
    MarketQuoteFundamentalsSnapshot,
)
from app.data.market_screener_parquet_store import (
    MARKET_SCREENER_USE_PARQUET_SNAPSHOT_ENV,
    MarketScreenerParquetStoreError,
)
from app.data.market_universe import MarketUniverseEntry, MarketUniverseSnapshot
from app.ui import earnings_radar_extension


class _FakeProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...], bool, int]] = []

    def quote_tape(self, symbols, *, force_refresh: bool = False, max_symbols: int = 100):
        requested = tuple(symbols)[:max_symbols]
        self.calls.append(("quote_tape", requested, force_refresh, max_symbols))
        return _snapshot(
            "Fake quote",
            (
                MarketQuoteFundamentalsRecord(
                    "ACME",
                    company_name="Quote Should Not Rename",
                    exchange="QUOTE",
                    sector="Quote Sector",
                    industry="Quote Industry",
                    price=21.5,
                    change_percent=1.2,
                    volume=120_000,
                    avg_volume=100_000,
                    market_cap=999_000_000,
                    pe_ratio=99.0,
                    eps=9.9,
                    revenue_growth=99.0,
                    shares_float=99,
                    shares_outstanding=100,
                    source="Fake quote",
                ),
            ),
        )

    def profile_classification(self, symbols, *, force_refresh: bool = False, max_symbols: int = 100):
        requested = tuple(symbols)[:max_symbols]
        self.calls.append(("profile_classification", requested, force_refresh, max_symbols))
        return _snapshot(
            "Fake profile",
            (
                MarketQuoteFundamentalsRecord(
                    "ACME",
                    company_name="Acme Updated",
                    exchange="NASDAQ",
                    sector="Technology",
                    industry="Software",
                    cik="1234567",
                    price=33.0,
                    market_cap=999_000_000,
                    source="Fake profile",
                ),
            ),
        )

    def fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 100):
        requested = tuple(symbols)[:max_symbols]
        self.calls.append(("fundamentals", requested, force_refresh, max_symbols))
        return _snapshot(
            "Fake fundamentals",
            (
                MarketQuoteFundamentalsRecord(
                    "ACME",
                    company_name="Fundamentals Should Not Rename",
                    price=44.0,
                    market_cap=2_500_000_000,
                    pe_ratio=18.5,
                    eps=2.1,
                    revenue_growth=12.5,
                    shares_float=40_000_000,
                    shares_outstanding=50_000_000,
                    market_cap_rank_value=2_500_000_000,
                    market_cap_rank_currency="USD",
                    market_cap_rank_trusted=True,
                    market_cap_rank_reason="provider supplied USD common-equity cap",
                    instrument_type="Common Stock",
                    country="United States",
                    is_adr=False,
                    is_etf=False,
                    is_fund=False,
                    is_otc=False,
                    source="Fake fundamentals",
                ),
            ),
        )

    def sec_filings(self, symbol: str, *, force_refresh: bool = False, limit: int = 1):
        self.calls.append(("sec_filings", (symbol,), force_refresh, limit))
        return (
            {
                "symbol": symbol,
                "companyName": "Filing Should Not Rename",
                "cik": "9999999",
                "form": "10-Q",
                "filingDate": "2026-06-18",
                "finalLink": "https://www.sec.gov/Archives/edgar/data/9999999/acme-10q.htm",
                "source": "FMP filing metadata",
            },
        )


class _SecFundamentalsProvider:
    def fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 100):
        return _snapshot(
            "SEC companyfacts",
            (
                MarketQuoteFundamentalsRecord(
                    "ACME",
                    market_cap=9_999_999_999,
                    pe_ratio=99.0,
                    eps=9.9,
                    revenue_growth=99.0,
                    shares_float=99,
                    shares_outstanding=100,
                    source="SEC companyfacts",
                ),
            ),
        )


class _FakeTkVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: object) -> None:
        self.value = str(value)


def _snapshot(source: str, records: tuple[MarketQuoteFundamentalsRecord, ...]) -> MarketQuoteFundamentalsSnapshot:
    fetched_at = "2026-06-19T12:00:00+00:00"
    return MarketQuoteFundamentalsSnapshot(
        records=records,
        fetched_at=fetched_at,
        statuses=(MarketDataProviderStatus(source, "available" if records else "empty", fetched_at, f"Loaded {len(records)} row(s)."),),
    )


def _base_record() -> MarketScreenerRecord:
    return MarketScreenerRecord(
        "ACME",
        "Acme Old",
        exchange="NYSE",
        sector="Industrials",
        industry="Machinery",
        price=10.0,
        change_percent=8.0,
        volume=200_000,
        avg_volume=50_000,
        market_cap=1_000_000_000,
        pe_ratio=30.0,
        eps=1.0,
        revenue_growth=3.0,
        shares_float=10_000_000,
        shares_outstanding=12_000_000,
        recent_filing_date="2026-05-01",
        recent_filing_type="8-K",
        signals=("High volume", "Mover", "Recent SEC filing", "Watchlist"),
        sources=("Seed",),
        source_links=("https://example.test/old-filing.htm",),
        fetched_at="2026-06-18T12:00:00+00:00",
        cik="0000000001",
    )


def _config() -> MarketScreenerBackfillConfig:
    return MarketScreenerBackfillConfig(
        enabled=True,
        profile_limit=10,
        quote_limit=10,
        fundamental_limit=10,
        databento_limit=10,
    )


def test_quote_tape_refresh_updates_only_quote_fields_and_quote_signals() -> None:
    result = refresh_market_screener_records([_base_record()], MARKET_SCREENER_REFRESH_QUOTE_TAPE, provider=_FakeProvider(), config=_config(), force_refresh=True)

    row = result.snapshot.records[0]
    assert row.price == 21.5
    assert row.change_percent == 1.2
    assert row.volume == 120_000
    assert row.avg_volume == 100_000
    assert row.company_name == "Acme Old"
    assert row.exchange == "NYSE"
    assert row.market_cap == 1_000_000_000
    assert row.recent_filing_date == "2026-05-01"
    assert "High volume" not in row.signals
    assert "Mover" not in row.signals
    assert "Recent SEC filing" in row.signals
    assert "Watchlist" in row.signals


def test_profile_refresh_updates_only_profile_classification_fields() -> None:
    result = refresh_market_screener_records([_base_record()], MARKET_SCREENER_REFRESH_PROFILE, provider=_FakeProvider(), config=_config(), force_refresh=True)

    row = result.snapshot.records[0]
    assert row.company_name == "Acme Updated"
    assert row.exchange == "NASDAQ"
    assert row.sector == "Technology"
    assert row.industry == "Software"
    assert row.cik == "0001234567"
    assert row.price == 10.0
    assert row.market_cap == 1_000_000_000
    assert row.recent_filing_date == "2026-05-01"


def test_fundamentals_refresh_updates_only_fundamental_and_market_cap_rank_fields() -> None:
    result = refresh_market_screener_records([_base_record()], MARKET_SCREENER_REFRESH_FUNDAMENTALS, provider=_FakeProvider(), config=_config(), force_refresh=True)

    row = result.snapshot.records[0]
    assert row.market_cap == 2_500_000_000
    assert row.pe_ratio == 18.5
    assert row.eps == 2.1
    assert row.revenue_growth == 12.5
    assert row.shares_float == 40_000_000
    assert row.shares_outstanding == 50_000_000
    assert row.market_cap_rank_value == 2_500_000_000
    assert row.market_cap_rank_trusted is True
    assert row.company_name == "Acme Old"
    assert row.price == 10.0
    assert row.recent_filing_date == "2026-05-01"


def test_filing_refresh_updates_only_filing_fields_links_and_signals() -> None:
    result = refresh_market_screener_records([_base_record()], MARKET_SCREENER_REFRESH_FILINGS, provider=_FakeProvider(), config=_config(), force_refresh=True)

    row = result.snapshot.records[0]
    assert row.recent_filing_date == "2026-06-18"
    assert row.recent_filing_type == "10-Q"
    assert "https://www.sec.gov/Archives/edgar/data/9999999/acme-10q.htm" in row.source_links
    assert "Recent SEC filing" in row.signals
    assert row.company_name == "Acme Old"
    assert row.cik == "0000000001"
    assert row.price == 10.0
    assert row.market_cap == 1_000_000_000


def test_sec_owned_fundamental_provider_cannot_update_non_filing_fields() -> None:
    result = refresh_market_screener_records(
        [_base_record()],
        MARKET_SCREENER_REFRESH_FUNDAMENTALS,
        provider=_SecFundamentalsProvider(),
        config=_config(),
        force_refresh=True,
    )

    row = result.snapshot.records[0]
    assert row.market_cap == 1_000_000_000
    assert row.pe_ratio == 30.0
    assert row.eps == 1.0
    assert row.revenue_growth == 3.0
    assert not any(item.source == "SEC companyfacts" and item.field in {"market_cap", "pe_ratio", "eps", "revenue_growth"} for item in row.field_provenance)


def test_full_rebuild_job_reconstructs_snapshot_from_fetch_inputs() -> None:
    result = rebuild_market_screener_snapshot(
        universe_snapshot=MarketUniverseSnapshot(
            records=(MarketUniverseEntry("ACME", "Acme Corp", exchange="NASDAQ"),),
            fetched_at="2026-06-19T12:00:00+00:00",
            sources=("Fixture",),
            statuses=(),
        ),
        recent_records=(),
        upcoming_records=(),
        market_data_records=(),
    )

    assert result.job == "full_rebuild"
    assert result.rows_updated == 1
    assert result.snapshot.records[0].symbol == "ACME"
    assert any(status.source == "Full Rebuild" for status in result.snapshot.statuses)


def test_parquet_save_failure_warns_and_keeps_provider_result_in_memory(monkeypatch) -> None:
    class _FailingStore:
        current_path = "memory://fmpsec_filings_parquet"

        def save_current(self, _records) -> None:
            raise MarketScreenerParquetStoreError("disk full")

    monkeypatch.setenv(MARKET_SCREENER_USE_PARQUET_SNAPSHOT_ENV, "true")
    monkeypatch.setattr(earnings_radar_extension, "MarketScreenerParquetStore", _FailingStore)
    app = SimpleNamespace(
        _market_screener_parquet_persistence_enabled=True,
        market_screener_market_data_status_lines=[],
        market_screener_source_summary_var=_FakeTkVar(),
    )

    warning = earnings_radar_extension._persist_current_screener_parquet_snapshot(app, [_base_record()], reason="Quote/Tape Refresh")

    assert warning is not None
    assert "kept in memory" in warning
    assert app.market_screener_market_data_status_lines
    assert "Parquet snapshot save failed" in app.market_screener_market_data_status_lines[-1]
