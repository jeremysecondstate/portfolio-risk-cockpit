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
