from __future__ import annotations

from app.data.databento_provider import DatabentoCmeContextProvider, DatabentoEquitiesProvider


class _FakeDatabentoClient:
    def __init__(self, rows_by_symbol: dict[str, dict]) -> None:
        self.rows_by_symbol = rows_by_symbol
        self.equity_calls: list[dict[str, object]] = []
        self.context_calls: list[dict[str, object]] = []

    def fetch_equity_rows(self, *, symbols, dataset, schema):
        self.equity_calls.append({"symbols": tuple(symbols), "dataset": dataset, "schema": schema})
        return {symbol: self.rows_by_symbol[symbol] for symbol in symbols if symbol in self.rows_by_symbol}

    def fetch_context_rows(self, *, symbols, dataset, schema):
        self.context_calls.append({"symbols": tuple(symbols), "dataset": dataset, "schema": schema})
        return {symbol: self.rows_by_symbol[symbol] for symbol in symbols if symbol in self.rows_by_symbol}


class _FailingDatabentoClient:
    def __init__(self, message: str) -> None:
        self.message = message

    def fetch_equity_rows(self, *, symbols, dataset, schema):
        raise RuntimeError(self.message)


class _ListDatabentoClient:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.equity_calls: list[dict[str, object]] = []

    def fetch_equity_rows(self, *, symbols, dataset, schema):
        requested = set(symbols)
        self.equity_calls.append({"symbols": tuple(symbols), "dataset": dataset, "schema": schema})
        return [row for row in self.rows if row.get("symbol") in requested]


class _TechnicalHistoryClient:
    def __init__(self) -> None:
        self.history_calls: list[dict[str, object]] = []

    def fetch_technical_history(self, *, symbols, dataset, schema, timeframe, lookback_minutes):
        self.history_calls.append(
            {
                "symbols": tuple(symbols),
                "dataset": dataset,
                "schema": schema,
                "timeframe": timeframe,
                "lookback_minutes": lookback_minutes,
            }
        )
        return {
            symbol: [
                {"symbol": symbol, "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.1, "volume": 100, "ts_event": "2026-06-16T14:30:00+00:00"},
                {"symbol": symbol, "open": 10.1, "high": 10.4, "low": 10.0, "close": 10.3, "volume": 150, "ts_event": "2026-06-16T14:31:00+00:00"},
            ]
            for symbol in symbols
        }


def test_databento_equities_disabled_returns_status_without_network() -> None:
    client = _FakeDatabentoClient({"ACME": {"symbol": "ACME", "price": 12.5}})
    provider = DatabentoEquitiesProvider(enabled=False, api_key="", dataset="", schema="", client=client)

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    assert snapshot.records == ()
    assert client.equity_calls == []
    assert snapshot.statuses[0].source == "Databento US Equities"
    assert snapshot.statuses[0].status == "disabled"


def test_databento_equities_missing_config_is_unavailable_and_redacts_secret() -> None:
    api_key = "db-secret-123"
    provider = DatabentoEquitiesProvider(enabled=True, api_key=api_key, dataset="", schema="", client=_FakeDatabentoClient({}))

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    combined = " ".join(status.message for status in snapshot.statuses) + " " + " ".join(snapshot.errors)
    assert snapshot.records == ()
    assert snapshot.statuses[0].status == "unavailable"
    assert "DATABENTO_EQUITIES_DATASET" in combined
    assert api_key not in combined
    assert snapshot.diagnostics["provider_unavailable"] == 1


def test_databento_equities_success_maps_tape_fields_and_uses_cache() -> None:
    client = _FakeDatabentoClient(
        {
            "ACME": {
                "symbol": "ACME",
                "price": 12.5,
                "volume": 2500,
                "ts_event": "2026-06-16T19:55:00+00:00",
            }
        }
    )
    provider = DatabentoEquitiesProvider(
        enabled=True,
        api_key="db-secret",
        dataset="XNAS.ITCH",
        schema="trades",
        client=client,
        cache_ttl_seconds=600,
    )

    first = provider.quote_fundamentals(["ACME"], max_symbols=5)
    second = provider.quote_fundamentals(["ACME"], max_symbols=5)

    assert len(first.records) == 1
    record = first.records[0]
    assert record.symbol == "ACME"
    assert record.price == 12.5
    assert record.volume == 2500
    assert record.source == "Databento US Equities"
    provenance = {row.field: row.source for row in record.field_provenance}
    assert provenance["price"] == "Databento US Equities"
    assert provenance["volume"] == "Databento US Equities"
    assert first.diagnostics["rows_enriched_by_databento_equities"] == 1
    assert second.diagnostics["databento_equities_cache_hits"] == 1
    assert len(client.equity_calls) == 1


def test_databento_equities_batches_above_100_and_reports_attempts() -> None:
    rows = {
        f"SYM{index:03d}": {"symbol": f"SYM{index:03d}", "price": float(index + 1), "volume": index + 100}
        for index in range(250)
    }
    client = _FakeDatabentoClient(rows)
    provider = DatabentoEquitiesProvider(
        enabled=True,
        api_key="db-secret",
        dataset="XNAS.ITCH",
        schema="trades",
        client=client,
        symbol_limit=250,
        cache_ttl_seconds=0,
    )

    snapshot = provider.quote_fundamentals(rows.keys(), max_symbols=250)

    assert len(snapshot.records) == 250
    assert [len(call["symbols"]) for call in client.equity_calls] == [100, 100, 50]
    assert snapshot.diagnostics["databento_equities_symbols_attempted"] == 250
    assert snapshot.diagnostics["databento_equities_chunks_attempted"] == 3
    assert snapshot.diagnostics["rows_skipped_by_configured_symbol_cap"] == 0
    assert "attempted 250 symbol(s) in 3 chunk(s)" in snapshot.statuses[0].message


def test_databento_equities_computes_tape_metrics_only_from_supported_rows() -> None:
    client = _ListDatabentoClient(
        [
            {"symbol": "ACME", "open": 10.0, "close": 11.0, "volume": 100, "ts_event": "2026-06-16T19:54:00+00:00"},
            {"symbol": "ACME", "open": 11.0, "close": 12.0, "volume": 300, "ts_event": "2026-06-16T19:55:00+00:00"},
            {"symbol": "BETA", "price": 20.0, "volume": 50, "ts_event": "2026-06-16T19:55:00+00:00"},
        ]
    )
    provider = DatabentoEquitiesProvider(
        enabled=True,
        api_key="db-secret",
        dataset="XNAS.ITCH",
        schema="ohlcv-1m",
        client=client,
        symbol_limit=5,
        cache_ttl_seconds=0,
    )

    snapshot = provider.quote_fundamentals(["ACME", "BETA"], max_symbols=5)

    by_symbol = {record.symbol: record for record in snapshot.records}
    assert by_symbol["ACME"].price == 12.0
    assert by_symbol["ACME"].volume == 300
    assert by_symbol["ACME"].change_percent == 20.0
    assert by_symbol["ACME"].avg_volume == 100
    provenance = {row.field: row for row in by_symbol["ACME"].field_provenance}
    assert provenance["change_percent"].source == "Databento US Equities"
    assert "computed from Databento" in provenance["change_percent"].source_detail
    assert "computed from Databento" in provenance["avg_volume"].source_detail
    assert by_symbol["BETA"].change_percent is None
    assert by_symbol["BETA"].avg_volume is None


def test_databento_equities_warning_redacts_api_key() -> None:
    api_key = "db-secret-redact"
    provider = DatabentoEquitiesProvider(
        enabled=True,
        api_key=api_key,
        dataset="XNAS.ITCH",
        schema="trades",
        client=_FailingDatabentoClient(f"entitlement failed for {api_key}"),
    )

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    combined = " ".join(status.message for status in snapshot.statuses) + " " + " ".join(snapshot.errors)
    assert snapshot.records == ()
    assert snapshot.statuses[0].status == "warning"
    assert api_key not in combined
    assert "[REDACTED]" in combined


def test_databento_equities_refuses_cme_dataset_for_equity_rows() -> None:
    client = _FakeDatabentoClient({"ES.FUT": {"symbol": "ES.FUT", "price": 5500.0}})
    provider = DatabentoEquitiesProvider(
        enabled=True,
        api_key="db-secret",
        dataset="GLBX.MDP3",
        schema="ohlcv-1m",
        client=client,
    )

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    assert snapshot.records == ()
    assert client.equity_calls == []
    assert snapshot.statuses[0].status == "warning"
    assert "Refusing to merge futures/options data into selected-equity quote fields" in snapshot.statuses[0].message
    assert snapshot.diagnostics["databento_dataset_mismatch_warnings"] == 1


def test_databento_equities_warns_for_non_intraday_equity_dataset_schema() -> None:
    provider = DatabentoEquitiesProvider(
        enabled=True,
        api_key="db-secret",
        dataset="EQUS.SUMMARY",
        schema="statistics",
        client=_FakeDatabentoClient({}),
    )

    snapshot = provider.quote_fundamentals(["ACME"], max_symbols=5)

    combined = " ".join(status.message for status in snapshot.statuses) + " " + " ".join(snapshot.errors)
    assert snapshot.records == ()
    assert snapshot.statuses[0].status == "warning"
    assert "EQUS.SUMMARY" in combined
    assert "cannot reasonably produce intraday price/volume/change/avg-volume" in combined
    assert "EQUS.MINI" in combined
    assert "ohlcv-1m" in combined
    assert snapshot.diagnostics["databento_dataset_mismatch_warnings"] == 2
    assert snapshot.diagnostics["rows_provider_returned_no_usable_data"] == 1


def test_databento_technical_history_uses_timeframe_lookbacks_and_skips_daily_for_intraday_schema() -> None:
    client = _TechnicalHistoryClient()
    provider = DatabentoEquitiesProvider(
        enabled=True,
        api_key="db-secret",
        dataset="XNAS.ITCH",
        schema="ohlcv-1m",
        client=client,
        cache_ttl_seconds=0,
    )

    snapshot = provider.technical_history(
        ["ACME"],
        timeframes={"timing_1m": 390, "daily_1y": 370 * 24 * 60},
        max_symbols=1,
    )

    assert [call["timeframe"] for call in client.history_calls] == ["timing_1m"]
    assert client.history_calls[0]["lookback_minutes"] == 390
    assert "timing_1m" in snapshot.rows_by_timeframe
    assert "daily_1y" not in snapshot.rows_by_timeframe
    assert snapshot.timeframe_diagnostics["daily_1y"]["status"] == "skipped"
    assert "Short row/tape context is not treated as full daily/regime history" in snapshot.statuses[0].message


def test_databento_cme_context_stays_separate_from_equity_rows() -> None:
    client = _FakeDatabentoClient(
        {
            "ES.FUT": {
                "symbol": "ES.FUT",
                "close": 5500.25,
                "volume": 12500,
                "ts_event": "2026-06-16T20:00:00+00:00",
            }
        }
    )
    provider = DatabentoCmeContextProvider(
        enabled=True,
        api_key="db-secret",
        dataset="GLBX.MDP3",
        schema="ohlcv-1m",
        symbols=("ES.FUT",),
        client=client,
    )

    snapshot = provider.context()

    assert len(snapshot.records) == 1
    record = snapshot.records[0]
    assert record.symbol == "ES.FUT"
    assert record.price == 5500.25
    assert record.volume == 12500
    assert record.source == "Databento CME context"
    assert snapshot.diagnostics["databento_cme_context_rows"] == 1
    assert snapshot.statuses[0].source == "Databento CME context"
    assert "kept separate from selected-equity quote/fundamental fields" in snapshot.statuses[0].message
