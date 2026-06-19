from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.analytics.market_screener import (
    MarketScreenerFieldProvenance,
    MarketScreenerRecord,
    MarketScreenerSourceStatus,
    market_screener_snapshot_from_records,
)
from app.data.market_screener_parquet_store import (
    CURRENT_SNAPSHOT_RELATIVE_PATH,
    MARKET_SCREENER_PARQUET_ROOT_ENV,
    MARKET_SCREENER_USE_PARQUET_SNAPSHOT_ENV,
    MarketScreenerParquetStore,
    MarketScreenerParquetStoreError,
    parquet_engine_available,
)
from app.ui import earnings_radar_extension


class _FakeTkVar:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: object) -> None:
        self.value = str(value)

    def get(self) -> str:
        return self.value


PARQUET_AVAILABLE = parquet_engine_available()
requires_parquet = pytest.mark.skipif(not PARQUET_AVAILABLE, reason="pyarrow is not installed")


def test_market_screener_parquet_store_missing_snapshot_returns_empty(tmp_path) -> None:
    store = MarketScreenerParquetStore(tmp_path)

    assert store.current_exists() is False
    assert store.load_current() == []


@requires_parquet
def test_market_screener_parquet_store_save_load_round_trip(tmp_path) -> None:
    store = MarketScreenerParquetStore(tmp_path)
    record = MarketScreenerRecord(
        "ACME",
        "Acme Corp",
        exchange="NASDAQ",
        sector="Technology",
        industry="Software",
        price=12.5,
        change_percent=4.2,
        volume=1_200_000,
        avg_volume=900_000,
        market_cap=1_500_000_000,
        pe_ratio=21.4,
        eps=0.58,
        revenue_growth=14.2,
        shares_float=45_000_000,
        shares_outstanding=50_000_000,
        next_earnings_date="2026-07-21",
        recent_filing_date="2026-06-05",
        recent_filing_type="8-K",
        signals=("High volume", "Mover"),
        risk_flags=("Revenue decline",),
        sources=("FMP quote", "SEC EDGAR"),
        source_links=("https://example.test/quote", "https://example.test/filing"),
        fetched_at="2026-06-19T12:00:00+00:00",
        cik="0000000001",
        source_excerpt="Provider excerpt",
        portfolio_quantity=10,
        portfolio_average_cost=8.25,
        portfolio_market_value=125.0,
        portfolio_unrealized_pnl=42.5,
        portfolio_weight=0.02,
        field_provenance=(
            MarketScreenerFieldProvenance(
                "price",
                "FMP quote",
                "quote endpoint",
                "https://example.test/quote",
                "2026-06-19T12:00:00+00:00",
            ),
        ),
        market_cap_currency="USD",
        market_cap_rank_value=1_500_000_000,
        market_cap_rank_currency="USD",
        market_cap_rank_trusted=True,
        market_cap_rank_reason="US primary listing",
        instrument_type="Common Stock",
        country="United States",
        is_adr=False,
        is_etf=False,
        is_fund=False,
        is_otc=False,
    )

    store.save_current([record])
    loaded = store.load_current()

    assert store.current_exists() is True
    assert loaded == [record]


@requires_parquet
def test_market_screener_parquet_store_malformed_snapshot_raises(tmp_path) -> None:
    path = tmp_path / CURRENT_SNAPSHOT_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text("not a parquet file", encoding="utf-8")

    with pytest.raises(MarketScreenerParquetStoreError, match="Could not read Market Screener snapshot"):
        MarketScreenerParquetStore(tmp_path).load_current()


@requires_parquet
def test_market_screener_initial_load_uses_current_parquet_snapshot(monkeypatch, tmp_path) -> None:
    store = MarketScreenerParquetStore(tmp_path)
    store.save_current(
        [
            MarketScreenerRecord(
                "ACME",
                "Acme Corp",
                price=12.5,
                sources=("FMP quote",),
                fetched_at="2026-06-19T12:00:00+00:00",
            )
        ]
    )
    monkeypatch.setenv(MARKET_SCREENER_PARQUET_ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(MARKET_SCREENER_USE_PARQUET_SNAPSHOT_ENV, "true")
    loaded_snapshots = []
    status_lines: list[str] = []
    monkeypatch.setattr(earnings_radar_extension, "_load_screener_snapshot", lambda _app, snapshot: loaded_snapshots.append(snapshot))
    monkeypatch.setattr(earnings_radar_extension, "_append_screener_market_data_status", lambda _app, line: status_lines.append(line))
    app = SimpleNamespace(
        _market_screener_initial_parquet_checked=False,
        market_screener_status_var=_FakeTkVar(),
    )

    loaded = earnings_radar_extension._try_load_initial_screener_parquet_snapshot(app)

    assert loaded is True
    assert app._market_screener_initial_parquet_checked is True
    assert loaded_snapshots[0].records[0].symbol == "ACME"
    assert loaded_snapshots[0].records[0].price == 12.5
    assert "Loaded 1 screener rows from Parquet" in app.market_screener_status_var.value
    assert status_lines and "Parquet snapshot loaded" in status_lines[0]


@requires_parquet
def test_market_screener_provider_refresh_persists_parquet_snapshot(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(MARKET_SCREENER_PARQUET_ROOT_ENV, str(tmp_path))
    monkeypatch.setenv(MARKET_SCREENER_USE_PARQUET_SNAPSHOT_ENV, "true")
    monkeypatch.setattr(earnings_radar_extension, "_load_screener_snapshot", lambda app, snapshot: setattr(app, "market_screener_records", list(snapshot.records)))
    monkeypatch.setattr(earnings_radar_extension, "_run_pending_screener_refresh", lambda _app: None)
    app = SimpleNamespace(
        _market_screener_refreshing=True,
        _market_screener_refresh_pending=False,
        _market_screener_refresh_pending_force=False,
        _market_screener_parquet_persistence_enabled=True,
        market_screener_status_var=_FakeTkVar(),
        market_screener_market_data_status_lines=[],
    )
    snapshot = market_screener_snapshot_from_records(
        (MarketScreenerRecord("ACME", "Acme Corp", price=12.5, sources=("FMP quote",), fetched_at="2026-06-19T12:00:00+00:00"),),
        statuses=(
            MarketScreenerSourceStatus(
                "Market data enrichment",
                "available",
                "2026-06-19T12:00:00+00:00",
                "Loaded fixture provider row.",
            ),
        ),
    )

    earnings_radar_extension._finish_screener_success(app, snapshot)

    loaded = MarketScreenerParquetStore(tmp_path).load_current()
    assert [record.symbol for record in loaded] == ["ACME"]
    assert loaded[0].price == 12.5
    assert app._market_screener_refreshing is False
    assert "Loaded 1 screener rows" in app.market_screener_status_var.value
