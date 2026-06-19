from __future__ import annotations

from app.analytics.earnings_pipeline import RecentEarningsRecord
from app.analytics.market_screener import (
    MAJOR_US_LARGE_CAP_SYMBOLS,
    MarketScreenerRecord,
    fetch_market_screener_snapshot,
    market_screener_major_cap_diagnostic_lines,
)
from app.data.market_data_provider import MarketDataProviderStatus, MarketQuoteFundamentalsRecord, MarketQuoteFundamentalsSnapshot
from app.data.market_universe import MarketUniverseEntry, MarketUniverseSnapshot


class _TopMarketCapProvider:
    provider_name = "top_market_cap_fixture"

    def __init__(
        self,
        records_by_symbol: dict[str, MarketQuoteFundamentalsRecord],
        *,
        status: MarketDataProviderStatus | None = None,
        diagnostics: dict[str, int] | None = None,
    ) -> None:
        self.records_by_symbol = records_by_symbol
        self.status = status
        self.diagnostics = diagnostics or {}
        self.ranking_calls: list[tuple[tuple[str, ...], int, bool]] = []
        self.quote_calls: list[tuple[tuple[str, ...], int, bool]] = []

    def market_cap_rankings(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
        requested = tuple(symbols)[:max_symbols]
        self.ranking_calls.append((requested, max_symbols, force_refresh))
        records = tuple(self.records_by_symbol[symbol] for symbol in requested if symbol in self.records_by_symbol)
        status = self.status or MarketDataProviderStatus(
            "FMP top-market-cap ranking",
            "available" if records else "empty",
            "2026-06-19T12:00:00+00:00",
            f"Loaded {len(records)} ranking row(s).",
        )
        return MarketQuoteFundamentalsSnapshot(
            records=records,
            fetched_at="2026-06-19T12:00:00+00:00",
            statuses=(status,),
            diagnostics=self.diagnostics,
        )

    def quote_fundamentals(self, symbols, *, force_refresh: bool = False, max_symbols: int = 50):
        requested = tuple(symbols)[:max_symbols]
        self.quote_calls.append((requested, max_symbols, force_refresh))
        return MarketQuoteFundamentalsSnapshot(records=(), fetched_at="2026-06-19T12:00:00+00:00", statuses=())

    def quote_fundamentals_by_cik(self, ciks, *, force_refresh: bool = False, max_symbols: int = 50):
        return MarketQuoteFundamentalsSnapshot(records=(), fetched_at="2026-06-19T12:00:00+00:00", statuses=())


def _snapshot(symbols: tuple[str, ...]) -> MarketUniverseSnapshot:
    return MarketUniverseSnapshot(
        records=tuple(MarketUniverseEntry(symbol, f"{symbol} Corp", exchange="NASDAQ", source="FMP stock-list") for symbol in symbols),
        fetched_at="2026-06-19T12:00:00+00:00",
        sources=("FMP stock-list",),
        statuses=(),
    )


def _cap(symbol: str, value: float | None, **kwargs) -> MarketQuoteFundamentalsRecord:
    return MarketQuoteFundamentalsRecord(
        symbol,
        market_cap=value,
        market_cap_currency=kwargs.pop("market_cap_currency", "USD") if value is not None else kwargs.pop("market_cap_currency", None),
        exchange=kwargs.pop("exchange", "NASDAQ"),
        source="FMP top-market-cap ranking",
        **kwargs,
    )


def test_top_market_cap_universe_orders_by_market_cap_before_truncation() -> None:
    provider = _TopMarketCapProvider(
        {
            "SMALL": _cap("SMALL", 5_000_000_000),
            "MEGA": _cap("MEGA", 500_000_000_000),
            "MID": _cap("MID", 50_000_000_000),
        }
    )

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=_snapshot(("SMALL", "MEGA", "MID")),
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=0,
        top_market_cap_universe_limit=2,
        top_market_cap_candidate_limit=3,
    )

    assert [record.symbol for record in snapshot.records] == ["MEGA", "MID"]
    assert snapshot.diagnostics.top_market_cap_universe_applied == 1
    assert snapshot.diagnostics.top_market_cap_selected_rows == 2


def test_top_market_cap_universe_respects_configured_top_n_and_candidate_caps() -> None:
    provider = _TopMarketCapProvider(
        {
            "SMALL": _cap("SMALL", 5_000_000_000),
            "MID": _cap("MID", 50_000_000_000),
            "MEGA": _cap("MEGA", 500_000_000_000),
            "GIANT": _cap("GIANT", 5_000_000_000_000),
        }
    )

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=_snapshot(("SMALL", "MID", "MEGA", "GIANT")),
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=0,
        top_market_cap_universe_limit=2,
        top_market_cap_candidate_limit=3,
    )

    assert provider.ranking_calls == [(("SMALL", "MID", "MEGA"), 3, False)]
    assert [record.symbol for record in snapshot.records] == ["MEGA", "MID"]
    assert "GIANT" not in {record.symbol for record in snapshot.records}
    assert snapshot.diagnostics.top_market_cap_candidate_limit == 3
    assert snapshot.diagnostics.top_market_cap_universe_limit == 2
    assert snapshot.diagnostics.top_market_cap_candidate_rows == 3


def test_top_market_cap_universe_normalizes_and_merges_berkshire_class_b_aliases() -> None:
    provider = _TopMarketCapProvider({"BRK.B": _cap("BRK/B", 900_000_000_000, exchange="NYSE")})
    universe = MarketUniverseSnapshot(
        records=(
            MarketUniverseEntry("BRK/B", "Berkshire slash", exchange="NYSE", source="FMP stock-list"),
            MarketUniverseEntry("BRK-B", "Berkshire dash", exchange="NYSE", source="FMP stock-list"),
            MarketUniverseEntry("BRK.B", "Berkshire dot", exchange="NYSE", source="FMP stock-list"),
            MarketUniverseEntry("AAPL", "Apple Inc", exchange="NASDAQ", source="FMP stock-list"),
        ),
        fetched_at="2026-06-19T12:00:00+00:00",
        sources=("FMP stock-list",),
        statuses=(),
    )

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=universe,
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=0,
        top_market_cap_universe_limit=5,
        top_market_cap_candidate_limit=5,
    )

    assert provider.ranking_calls == [(("BRK.B", "AAPL"), 2, False)]
    assert [record.symbol for record in snapshot.records] == ["BRK.B"]
    assert snapshot.records[0].market_cap == 900_000_000_000


def test_top_market_cap_universe_excludes_missing_market_cap_rows_when_rankable_rows_exist() -> None:
    provider = _TopMarketCapProvider(
        {
            "CAP1": _cap("CAP1", 10_000_000_000),
            "CAP2": _cap("CAP2", 20_000_000_000),
            "MISS": _cap("MISS", None),
        }
    )

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=_snapshot(("CAP1", "MISS", "CAP2")),
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=0,
        top_market_cap_universe_limit=3,
        top_market_cap_candidate_limit=3,
    )

    assert [record.symbol for record in snapshot.records] == ["CAP2", "CAP1"]
    assert snapshot.diagnostics.top_market_cap_missing_market_cap_rows == 1
    assert snapshot.diagnostics.top_market_cap_excluded_rows == 1


def test_top_market_cap_universe_demotes_non_primary_non_usd_and_ambiguous_rows() -> None:
    universe = MarketUniverseSnapshot(
        records=(
            MarketUniverseEntry("PRIMARY", "Primary Corp", exchange="NASDAQ", source="FMP stock-list"),
            MarketUniverseEntry("ADR", "ADR Corp", exchange="NYSE", source="FMP stock-list"),
            MarketUniverseEntry("ETF", "Example ETF", exchange="NYSE ARCA", source="FMP stock-list"),
            MarketUniverseEntry("FUND", "Example Fund", exchange="NYSE", source="FMP stock-list"),
            MarketUniverseEntry("OTC", "OTC Corp", exchange="OTC", source="FMP stock-list"),
            MarketUniverseEntry("CAD", "Canadian Corp", exchange="NASDAQ", source="FMP stock-list"),
            MarketUniverseEntry("AMBIG", "Ambiguous Corp", exchange=None, source="FMP stock-list"),
        ),
        fetched_at="2026-06-19T12:00:00+00:00",
        sources=("FMP stock-list",),
        statuses=(),
    )
    provider = _TopMarketCapProvider(
        {
            "PRIMARY": _cap("PRIMARY", 100_000_000_000),
            "ADR": _cap("ADR", 500_000_000_000, is_adr=True, exchange="NYSE"),
            "ETF": _cap("ETF", 700_000_000_000, is_etf=True, exchange="NYSE ARCA"),
            "FUND": _cap("FUND", 600_000_000_000, is_fund=True, exchange="NYSE"),
            "OTC": _cap("OTC", 800_000_000_000, is_otc=True, exchange="OTC"),
            "CAD": _cap("CAD", 900_000_000_000, market_cap_currency="CAD"),
            "AMBIG": _cap("AMBIG", 1_000_000_000_000, market_cap_currency=None, exchange=None),
        }
    )

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=universe,
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=0,
        top_market_cap_universe_limit=3,
        top_market_cap_candidate_limit=7,
    )

    assert [record.symbol for record in snapshot.records] == ["OTC", "ETF", "PRIMARY"]
    assert "CAD" not in {record.symbol for record in snapshot.records}
    assert "AMBIG" not in {record.symbol for record in snapshot.records}
    assert snapshot.diagnostics.top_market_cap_demoted_non_primary_rows >= 6
    assert snapshot.diagnostics.top_market_cap_untrusted_rows >= 2


def test_top_market_cap_universe_populates_major_large_cap_diagnostics() -> None:
    provider = _TopMarketCapProvider({"AAPL": _cap("AAPL", 3_000_000_000_000)})

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=_snapshot(("AAPL",)),
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=0,
        top_market_cap_universe_limit=1,
        top_market_cap_candidate_limit=1,
    )

    assert snapshot.diagnostics.major_us_large_caps_present == 1
    assert snapshot.diagnostics.major_us_large_caps_absent == len(MAJOR_US_LARGE_CAP_SYMBOLS) - 1
    lines = market_screener_major_cap_diagnostic_lines(snapshot.records, snapshot.diagnostics)
    assert any(line.startswith("MSFT absent:") for line in lines)


def test_top_market_cap_universe_reports_provider_auth_plan_rate_limits() -> None:
    provider = _TopMarketCapProvider(
        {},
        status=MarketDataProviderStatus(
            "FMP top-market-cap ranking",
            "warning",
            "2026-06-19T12:00:00+00:00",
            "FMP market-cap endpoint rate or daily plan limit was reached (HTTP 429).",
        ),
        diagnostics={"rows_blocked_by_provider_plan_rate_auth_limit": 1, "provider_warnings": 1},
    )

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=_snapshot(("AAPL", "MSFT")),
        recent_records=(),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=0,
        top_market_cap_universe_limit=2,
        top_market_cap_candidate_limit=2,
    )

    assert snapshot.diagnostics.top_market_cap_universe_applied == 0
    assert snapshot.diagnostics.rows_blocked_by_provider_plan_rate_auth_limit == 1
    assert snapshot.diagnostics.provider_warnings >= 1
    assert any(status.source == "Top market-cap universe" and status.status == "warning" for status in snapshot.statuses)


def test_top_market_cap_universe_does_not_use_sec_for_non_filing_fields() -> None:
    provider = _TopMarketCapProvider({"ACME": _cap("ACME", 12_000_000_000)})
    recent = RecentEarningsRecord(
        cik="0000000001",
        company_name="SEC Name",
        ticker="ACME",
        form="8-K",
        items="2.02",
        filed_date="2026-06-18",
        acceptance_datetime="2026-06-18T12:00:00",
        report_date="2026-03-31",
        fiscal_period="Q1",
        sector="SEC Sector",
        industry="SEC Industry",
        sic="7372",
        exchange="SEC Exchange",
        release_title="ACME reports",
        revenue=None,
        revenue_growth=25.0,
        eps=1.23,
        net_income=None,
        guidance_flag=False,
        risk_flags=(),
        filing_url="https://www.sec.gov/Archives/edgar/data/1/acme.htm",
        exhibit_url=None,
        accession_number="0000000001-26-000001",
        source="SEC EDGAR",
    )

    snapshot = fetch_market_screener_snapshot(
        universe_snapshot=MarketUniverseSnapshot(
            records=(MarketUniverseEntry("ACME", "Provider Name", exchange="NASDAQ", sector="Technology", source="FMP stock-list"),),
            fetched_at="2026-06-19T12:00:00+00:00",
            sources=("FMP stock-list",),
            statuses=(),
        ),
        recent_records=(recent,),
        upcoming_records=(),
        market_data_provider=provider,
        market_data_symbol_limit=0,
        top_market_cap_universe_limit=1,
        top_market_cap_candidate_limit=1,
    )

    record = snapshot.records[0]
    blocked_fields = {
        "symbol",
        "company_name",
        "exchange",
        "sector",
        "industry",
        "price",
        "change_percent",
        "volume",
        "avg_volume",
        "market_cap",
        "pe_ratio",
        "eps",
        "revenue_growth",
        "shares_float",
        "shares_outstanding",
    }
    assert record.company_name == "Provider Name"
    assert record.exchange == "NASDAQ"
    assert record.sector == "Technology"
    assert record.eps is None
    assert record.revenue_growth is None
    assert record.recent_filing_date == "2026-06-18"
    assert not any(row.field in blocked_fields and "SEC" in row.source.upper() for row in record.field_provenance)
    assert any(row.field == "recent_filing_date" and "SEC" in row.source.upper() for row in record.field_provenance)
