from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.data.market_data_provider import (
    DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    MarketDataFieldProvenance,
    MarketDataProviderStatus,
    MarketQuoteFundamentalsRecord,
    MarketQuoteFundamentalsSnapshot,
)


DATABENTO_API_KEY_ENV = "DATABENTO_API_KEY"
DATABENTO_EQUITIES_DATASET_ENV = "DATABENTO_EQUITIES_DATASET"
DATABENTO_EQUITIES_SCHEMA_ENV = "DATABENTO_EQUITIES_SCHEMA"
DATABENTO_EQUITIES_SYMBOL_LIMIT_ENV = "DATABENTO_EQUITIES_SYMBOL_LIMIT"
DATABENTO_CACHE_TTL_SECONDS_ENV = "DATABENTO_CACHE_TTL_SECONDS"
DATABENTO_CME_DATASET_ENV = "DATABENTO_CME_DATASET"
DATABENTO_CME_SCHEMA_ENV = "DATABENTO_CME_SCHEMA"
DATABENTO_CME_SYMBOLS_ENV = "DATABENTO_CME_SYMBOLS"
DATABENTO_CME_SYMBOL_LIMIT_ENV = "DATABENTO_CME_SYMBOL_LIMIT"
MARKET_SCREENER_ENABLE_DATABENTO_EQUITIES_ENV = "MARKET_SCREENER_ENABLE_DATABENTO_EQUITIES"
MARKET_SCREENER_ENABLE_DATABENTO_CME_CONTEXT_ENV = "MARKET_SCREENER_ENABLE_DATABENTO_CME_CONTEXT"

DEFAULT_DATABENTO_EQUITIES_SYMBOL_LIMIT = 100
DEFAULT_DATABENTO_CME_SYMBOL_LIMIT = 25
DEFAULT_DATABENTO_CACHE_TTL_SECONDS = 900
DATABENTO_DOC_URL = "https://databento.com/docs"
DATABENTO_SCHEMAS_DOC_URL = "https://databento.com/docs/schemas-and-data-formats"
DATABENTO_HISTORICAL_DOC_URL = "https://databento.com/docs/api-reference-historical"

_SHARED_DATABENTO_CACHE: dict[tuple[str, str, str, str, str], tuple[float, Mapping[str, Any]]] = {}
_PLACEHOLDER_SECRETS = {"", "THIS IS NOT A KEY", "NOT_A_KEY", "CHANGEME", "CHANGE_ME"}


class DatabentoProviderWarning(RuntimeError):
    """Nonblocking Databento provider warning safe to show in source/status UI."""


@dataclass(frozen=True)
class DatabentoCrossAssetContextRecord:
    symbol: str
    price: float | None = None
    volume: float | None = None
    timestamp: str = ""
    dataset: str = ""
    schema: str = ""
    source: str = "Databento CME context"
    source_url: str = DATABENTO_DOC_URL

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DatabentoCrossAssetContextSnapshot:
    records: tuple[DatabentoCrossAssetContextRecord, ...]
    fetched_at: str
    statuses: tuple[MarketDataProviderStatus, ...]
    errors: tuple[str, ...] = ()
    diagnostics: Mapping[str, int] = field(default_factory=dict)


class DatabentoEquitiesProvider:
    provider_name = "databento_us_equities"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        dataset: str | None = None,
        schema: str | None = None,
        enabled: bool | None = None,
        client: Any | None = None,
        symbol_limit: int | None = None,
        cache_ttl_seconds: int | None = None,
        lookback_minutes: int = 60,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv(DATABENTO_API_KEY_ENV, "")
        self.dataset = str(dataset if dataset is not None else os.getenv(DATABENTO_EQUITIES_DATASET_ENV, "")).strip()
        self.schema = str(schema if schema is not None else os.getenv(DATABENTO_EQUITIES_SCHEMA_ENV, "")).strip()
        self.enabled = _env_flag(MARKET_SCREENER_ENABLE_DATABENTO_EQUITIES_ENV, False) if enabled is None else bool(enabled)
        self.client = client
        self.symbol_limit = (
            max(0, min(100, int(symbol_limit)))
            if symbol_limit is not None
            else _configured_int(DATABENTO_EQUITIES_SYMBOL_LIMIT_ENV, DEFAULT_DATABENTO_EQUITIES_SYMBOL_LIMIT, minimum=0, maximum=100)
        )
        self.cache_ttl_seconds = (
            max(0, int(cache_ttl_seconds))
            if cache_ttl_seconds is not None
            else _configured_int(DATABENTO_CACHE_TTL_SECONDS_ENV, DEFAULT_DATABENTO_CACHE_TTL_SECONDS, minimum=0, maximum=86_400)
        )
        self.lookback_minutes = max(1, int(lookback_minutes))
        self._cache = {} if client is not None else _SHARED_DATABENTO_CACHE

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        capped_input = _limited_symbols(symbols, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))

        disabled = self._disabled_snapshot(fetched_at, len(capped_input))
        if disabled is not None:
            return disabled
        if max_symbols <= 0 or self.symbol_limit <= 0:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Databento US Equities",
                        "disabled",
                        fetched_at,
                        f"Databento US Equities: 0 rows updated; {len(capped_input)} skipped/limited because the symbol cap is 0.",
                    ),
                ),
                diagnostics={"rows_skipped_by_configured_symbol_cap": len(capped_input)},
            )

        if _looks_like_cme_dataset(self.dataset):
            message = (
                f"Databento US Equities: configured dataset '{self.dataset}' looks like CME/futures coverage. "
                "Refusing to merge futures/options data into selected-equity quote fields; configure "
                f"{DATABENTO_EQUITIES_DATASET_ENV} for US equities or enable CME context separately."
            )
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Databento US Equities", "warning", fetched_at, message),),
                errors=(message,),
                diagnostics={
                    "databento_dataset_mismatch_warnings": 1,
                    "rows_provider_returned_no_usable_data": len(requested),
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                },
            )

        records: list[MarketQuoteFundamentalsRecord] = []
        cache_hits = 0
        warnings: list[str] = []
        try:
            rows_by_symbol, cache_hits = self._rows_by_symbol(requested, force_refresh=force_refresh)
        except DatabentoProviderWarning as exc:
            warnings.append(str(exc))
            rows_by_symbol = {}

        for symbol in requested:
            row = rows_by_symbol.get(symbol)
            if row is None:
                continue
            record = self._record_from_row(symbol, row, fetched_at=fetched_at)
            if _quote_record_has_any_tape_value(record):
                records.append(record)

        no_usable = max(0, len(requested) - len(records))
        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        message = (
            f"Databento US Equities: {len(records)} rows updated; tape fields only; cache used for {cache_hits}; "
            f"{skipped_limited} skipped/limited; {no_usable} no usable data. "
            f"Dataset={self.dataset or 'not configured'}; schema={self.schema or 'not configured'}. "
            "Databento fills equity price/volume fields only; FMP remains the fundamentals/profile source."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)}"

        return MarketQuoteFundamentalsSnapshot(
            records=tuple(records),
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("Databento US Equities", status, fetched_at, _redact_databento_secret(message, self.api_key)),),
            errors=tuple(_redact_databento_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "rows_enriched_by_databento_equities": len(records),
                "databento_equities_cache_hits": cache_hits,
                "rows_provider_returned_no_usable_data": no_usable,
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "provider_unavailable": 1 if warnings and not records else 0,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_provider_limit_warning(warning) for warning in warnings) else 0,
            },
        )

    def quote_fundamentals_by_cik(
        self,
        ciks: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        return MarketQuoteFundamentalsSnapshot(
            records=(),
            fetched_at=fetched_at,
            statuses=(
                MarketDataProviderStatus(
                    "Databento US Equities",
                    "disabled",
                    fetched_at,
                    "Databento US Equities does not provide CIK profile/fundamental lookup; FMP profile-by-CIK remains separate.",
                ),
            ),
        )

    def _disabled_snapshot(self, fetched_at: str, capped_count: int) -> MarketQuoteFundamentalsSnapshot | None:
        if not self.enabled:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Databento US Equities",
                        "disabled",
                        fetched_at,
                        f"Databento US Equities disabled; set {MARKET_SCREENER_ENABLE_DATABENTO_EQUITIES_ENV}=true with dataset/schema to add equity tape fields.",
                    ),
                ),
            )
        missing = []
        if not _secret_configured(self.api_key):
            missing.append(DATABENTO_API_KEY_ENV)
        if not self.dataset:
            missing.append(DATABENTO_EQUITIES_DATASET_ENV)
        if not self.schema:
            missing.append(DATABENTO_EQUITIES_SCHEMA_ENV)
        if missing:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Databento US Equities",
                        "unavailable",
                        fetched_at,
                        f"Databento US Equities enabled but missing {', '.join(missing)}; 0 of {capped_count} requested row(s) updated.",
                    ),
                ),
                diagnostics={"provider_unavailable": 1},
            )
        return None

    def _rows_by_symbol(self, symbols: tuple[str, ...], *, force_refresh: bool) -> tuple[dict[str, Mapping[str, Any]], int]:
        rows: dict[str, Mapping[str, Any]] = {}
        missing: list[str] = []
        cache_hits = 0
        for symbol in symbols:
            cached = self._cache_get("equities", symbol, force_refresh=force_refresh)
            if cached is None:
                missing.append(symbol)
            else:
                rows[symbol] = cached
                cache_hits += 1
        if missing:
            client = self.client or _build_default_databento_client(self.api_key)
            try:
                fetched_rows = _call_databento_rows(
                    client,
                    symbols=tuple(missing),
                    dataset=self.dataset,
                    schema=self.schema,
                    lookback_minutes=self.lookback_minutes,
                    context="equities",
                )
            except DatabentoProviderWarning:
                raise
            except Exception as exc:
                raise DatabentoProviderWarning(_redact_databento_secret(f"Databento US Equities fetch failed: {exc}", self.api_key)) from None
            for symbol, row in _coerce_rows_by_symbol(fetched_rows, tuple(missing)).items():
                rows[symbol] = row
                self._cache_set("equities", symbol, row)
        return rows, cache_hits

    def _record_from_row(self, symbol: str, row: Mapping[str, Any], *, fetched_at: str) -> MarketQuoteFundamentalsRecord:
        timestamp = _row_timestamp(row) or fetched_at
        values = {
            "symbol": _normalize_symbol(_first_present(row, "symbol", "raw_symbol", "ticker", "instrument", "instrument_id")) or symbol,
            "price": _price_from_row(row),
            "volume": _optional_float(_first_present(row, "volume", "size", "qty", "quantity")),
            "source": "Databento US Equities",
            "source_url": DATABENTO_SCHEMAS_DOC_URL,
            "fetched_at": timestamp,
        }
        field_provenance = _provenance_for_tape_values(values, source="Databento US Equities", fetched_at=timestamp)
        return MarketQuoteFundamentalsRecord.from_dict({**values, "field_provenance": field_provenance})

    def _cache_get(self, context: str, symbol: str, *, force_refresh: bool) -> Mapping[str, Any] | None:
        if force_refresh or self.cache_ttl_seconds <= 0:
            return None
        key = (context, self.dataset, self.schema, _normalize_symbol(symbol), "row")
        cached = self._cache.get(key)
        if cached is None:
            return None
        cached_at, payload = cached
        if time.time() - cached_at > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return payload

    def _cache_set(self, context: str, symbol: str, payload: Mapping[str, Any]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        key = (context, self.dataset, self.schema, _normalize_symbol(symbol), "row")
        self._cache[key] = (time.time(), dict(payload))


class DatabentoCmeContextProvider:
    provider_name = "databento_cme_context"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        dataset: str | None = None,
        schema: str | None = None,
        symbols: Iterable[str] | None = None,
        enabled: bool | None = None,
        client: Any | None = None,
        symbol_limit: int | None = None,
        cache_ttl_seconds: int | None = None,
        lookback_minutes: int = 60,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv(DATABENTO_API_KEY_ENV, "")
        self.dataset = str(dataset if dataset is not None else os.getenv(DATABENTO_CME_DATASET_ENV, "")).strip()
        self.schema = str(schema if schema is not None else os.getenv(DATABENTO_CME_SCHEMA_ENV, "")).strip()
        self.enabled = _env_flag(MARKET_SCREENER_ENABLE_DATABENTO_CME_CONTEXT_ENV, False) if enabled is None else bool(enabled)
        self.symbols = _limited_context_symbols(symbols if symbols is not None else _split_symbols(os.getenv(DATABENTO_CME_SYMBOLS_ENV, "")), 10_000)
        self.client = client
        self.symbol_limit = (
            max(0, min(100, int(symbol_limit)))
            if symbol_limit is not None
            else _configured_int(DATABENTO_CME_SYMBOL_LIMIT_ENV, DEFAULT_DATABENTO_CME_SYMBOL_LIMIT, minimum=0, maximum=100)
        )
        self.cache_ttl_seconds = (
            max(0, int(cache_ttl_seconds))
            if cache_ttl_seconds is not None
            else _configured_int(DATABENTO_CACHE_TTL_SECONDS_ENV, DEFAULT_DATABENTO_CACHE_TTL_SECONDS, minimum=0, maximum=86_400)
        )
        self.lookback_minutes = max(1, int(lookback_minutes))
        self._cache = {} if client is not None else _SHARED_DATABENTO_CACHE

    def context(self, *, force_refresh: bool = False, max_symbols: int | None = None) -> DatabentoCrossAssetContextSnapshot:
        fetched_at = _now()
        capped_input = self.symbols[: max(0, max_symbols if max_symbols is not None else self.symbol_limit)]
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(self.symbols) - len(requested))

        disabled = self._disabled_snapshot(fetched_at)
        if disabled is not None:
            return disabled
        if self.symbol_limit <= 0 or not requested:
            return DatabentoCrossAssetContextSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Databento CME context",
                        "disabled",
                        fetched_at,
                        "Databento CME context has no configured symbols or symbol cap is 0.",
                    ),
                ),
            )
        if not _looks_like_cme_dataset(self.dataset):
            message = (
                f"Databento CME context: dataset '{self.dataset}' does not look like CME/Globex coverage. "
                "Not using it as futures/options cross-asset context."
            )
            return DatabentoCrossAssetContextSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Databento CME context", "warning", fetched_at, message),),
                errors=(message,),
                diagnostics={"databento_dataset_mismatch_warnings": 1},
            )

        rows_by_symbol: dict[str, Mapping[str, Any]]
        cache_hits = 0
        warnings: list[str] = []
        try:
            rows_by_symbol, cache_hits = self._rows_by_symbol(tuple(requested), force_refresh=force_refresh)
        except DatabentoProviderWarning as exc:
            warnings.append(str(exc))
            rows_by_symbol = {}

        records = tuple(
            record
            for symbol in requested
            if (record := _context_record_from_row(symbol, rows_by_symbol.get(symbol), dataset=self.dataset, schema=self.schema, fetched_at=fetched_at)) is not None
        )
        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        message = (
            f"Databento CME context: {len(records)} context row(s) fetched; cache used for {cache_hits}; "
            f"{skipped_limited} skipped/limited. Dataset={self.dataset}; schema={self.schema}; "
            "kept separate from selected-equity quote/fundamental fields."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)}"
        return DatabentoCrossAssetContextSnapshot(
            records=records,
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("Databento CME context", status, fetched_at, _redact_databento_secret(message, self.api_key)),),
            errors=tuple(_redact_databento_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "databento_cme_context_rows": len(records),
                "databento_cme_cache_hits": cache_hits,
                "provider_unavailable": 1 if warnings and not records else 0,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_provider_limit_warning(warning) for warning in warnings) else 0,
            },
        )

    def _disabled_snapshot(self, fetched_at: str) -> DatabentoCrossAssetContextSnapshot | None:
        if not self.enabled:
            return DatabentoCrossAssetContextSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Databento CME context",
                        "disabled",
                        fetched_at,
                        f"Databento CME context disabled; set {MARKET_SCREENER_ENABLE_DATABENTO_CME_CONTEXT_ENV}=true to add futures/options cross-asset context.",
                    ),
                ),
            )
        missing = []
        if not _secret_configured(self.api_key):
            missing.append(DATABENTO_API_KEY_ENV)
        if not self.dataset:
            missing.append(DATABENTO_CME_DATASET_ENV)
        if not self.schema:
            missing.append(DATABENTO_CME_SCHEMA_ENV)
        if not self.symbols:
            missing.append(DATABENTO_CME_SYMBOLS_ENV)
        if missing:
            return DatabentoCrossAssetContextSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Databento CME context",
                        "unavailable",
                        fetched_at,
                        f"Databento CME context enabled but missing {', '.join(missing)}.",
                    ),
                ),
                diagnostics={"provider_unavailable": 1},
            )
        return None

    def _rows_by_symbol(self, symbols: tuple[str, ...], *, force_refresh: bool) -> tuple[dict[str, Mapping[str, Any]], int]:
        rows: dict[str, Mapping[str, Any]] = {}
        missing: list[str] = []
        cache_hits = 0
        for symbol in symbols:
            cached = self._cache_get("cme", symbol, force_refresh=force_refresh)
            if cached is None:
                missing.append(symbol)
            else:
                rows[symbol] = cached
                cache_hits += 1
        if missing:
            client = self.client or _build_default_databento_client(self.api_key)
            try:
                fetched_rows = _call_databento_rows(
                    client,
                    symbols=tuple(missing),
                    dataset=self.dataset,
                    schema=self.schema,
                    lookback_minutes=self.lookback_minutes,
                    context="cme",
                )
            except DatabentoProviderWarning:
                raise
            except Exception as exc:
                raise DatabentoProviderWarning(_redact_databento_secret(f"Databento CME context fetch failed: {exc}", self.api_key)) from None
            for symbol, row in _coerce_rows_by_symbol(fetched_rows, tuple(missing)).items():
                rows[symbol] = row
                self._cache_set("cme", symbol, row)
        return rows, cache_hits

    def _cache_get(self, context: str, symbol: str, *, force_refresh: bool) -> Mapping[str, Any] | None:
        if force_refresh or self.cache_ttl_seconds <= 0:
            return None
        key = (context, self.dataset, self.schema, _normalize_symbol(symbol), "row")
        cached = self._cache.get(key)
        if cached is None:
            return None
        cached_at, payload = cached
        if time.time() - cached_at > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return payload

    def _cache_set(self, context: str, symbol: str, payload: Mapping[str, Any]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        key = (context, self.dataset, self.schema, _normalize_symbol(symbol), "row")
        self._cache[key] = (time.time(), dict(payload))


def configured_databento_equities_provider() -> DatabentoEquitiesProvider:
    return DatabentoEquitiesProvider()


def configured_databento_cme_context_provider() -> DatabentoCmeContextProvider:
    return DatabentoCmeContextProvider()


def _build_default_databento_client(api_key: str) -> Any:
    try:
        import databento as db  # type: ignore[import-not-found]
    except Exception as exc:
        raise DatabentoProviderWarning(f"databento package is not installed or could not be imported: {exc}") from None
    historical = getattr(db, "Historical", None)
    if not callable(historical):
        raise DatabentoProviderWarning("databento package does not expose Historical client.")
    try:
        return historical(key=api_key)
    except Exception as exc:
        raise DatabentoProviderWarning(_redact_databento_secret(f"Databento client initialization failed: {exc}", api_key)) from None


def _call_databento_rows(
    client: Any,
    *,
    symbols: tuple[str, ...],
    dataset: str,
    schema: str,
    lookback_minutes: int,
    context: str,
) -> Any:
    custom_method = getattr(client, "fetch_equity_rows" if context == "equities" else "fetch_context_rows", None)
    if callable(custom_method):
        return custom_method(symbols=symbols, dataset=dataset, schema=schema)

    generic_method = getattr(client, "fetch_rows", None)
    if callable(generic_method):
        return generic_method(symbols=symbols, dataset=dataset, schema=schema, context=context)

    timeseries = getattr(client, "timeseries", None)
    get_range = getattr(timeseries, "get_range", None)
    if callable(get_range):
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=max(1, lookback_minutes))
        try:
            store = get_range(
                dataset=dataset,
                schema=schema,
                symbols=list(symbols),
                stype_in="raw_symbol",
                start=start.isoformat(),
                end=end.isoformat(),
                limit=max(1, len(symbols) * 10),
            )
        except TypeError:
            store = get_range(
                dataset=dataset,
                schema=schema,
                symbols=list(symbols),
                start=start.isoformat(),
                end=end.isoformat(),
                limit=max(1, len(symbols) * 10),
            )
        try:
            frame = store.to_df()
        except Exception as exc:
            raise DatabentoProviderWarning(f"Databento {dataset}/{schema} response could not be converted to rows: {exc}") from None
        if hasattr(frame, "to_dict"):
            return frame.to_dict(orient="records")
        return frame

    raise DatabentoProviderWarning("Databento client has no supported row fetch method.")


def _coerce_rows_by_symbol(payload: Any, requested: tuple[str, ...]) -> dict[str, Mapping[str, Any]]:
    rows_by_symbol: dict[str, Mapping[str, Any]] = {}
    if isinstance(payload, Mapping):
        if all(isinstance(value, Mapping) for value in payload.values()):
            for key, value in payload.items():
                symbol = _normalize_symbol(key)
                if symbol and symbol in requested:
                    rows_by_symbol[symbol] = dict(value)
            return rows_by_symbol
        rows = [payload]
    elif isinstance(payload, (list, tuple)):
        rows = [row for row in payload if isinstance(row, Mapping)]
    else:
        rows = []

    for row in rows:
        symbol = _normalize_symbol(_first_present(row, "symbol", "raw_symbol", "ticker", "instrument", "instrument_id"))
        if not symbol and len(requested) == 1:
            symbol = requested[0]
        if not symbol or symbol not in requested:
            continue
        rows_by_symbol[symbol] = dict(row)
    return rows_by_symbol


def _context_record_from_row(
    symbol: str,
    row: Mapping[str, Any] | None,
    *,
    dataset: str,
    schema: str,
    fetched_at: str,
) -> DatabentoCrossAssetContextRecord | None:
    if not row:
        return None
    timestamp = _row_timestamp(row) or fetched_at
    price = _price_from_row(row)
    volume = _optional_float(_first_present(row, "volume", "size", "qty", "quantity"))
    if price is None and volume is None:
        return None
    return DatabentoCrossAssetContextRecord(
        symbol=_normalize_symbol(_first_present(row, "symbol", "raw_symbol", "instrument", "instrument_id")) or symbol,
        price=price,
        volume=volume,
        timestamp=timestamp,
        dataset=dataset,
        schema=schema,
        source="Databento CME context",
        source_url=DATABENTO_SCHEMAS_DOC_URL,
    )


def _provenance_for_tape_values(values: Mapping[str, Any], *, source: str, fetched_at: str) -> tuple[MarketDataFieldProvenance, ...]:
    rows = [
        MarketDataFieldProvenance(field=field, source=source, source_url=DATABENTO_SCHEMAS_DOC_URL, fetched_at=fetched_at)
        for field in ("price", "volume")
        if values.get(field) is not None
    ]
    return tuple(rows)


def _quote_record_has_any_tape_value(record: MarketQuoteFundamentalsRecord) -> bool:
    return record.price is not None or record.volume is not None or record.change_percent is not None


def _price_from_row(row: Mapping[str, Any]) -> float | None:
    value = _first_present(
        row,
        "price",
        "last",
        "last_price",
        "last_px",
        "close",
        "close_price",
        "px",
        "bid_px",
        "ask_px",
    )
    price = _optional_float(value)
    if price is None:
        return None
    if abs(price) >= 10_000_000:
        return price / 1_000_000_000
    return price


def _row_timestamp(row: Mapping[str, Any]) -> str:
    value = _first_present(row, "timestamp", "ts_event", "ts_recv", "time", "datetime")
    if value in (None, ""):
        return ""
    text = str(value).strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.isoformat()


def _limited_symbols(symbols: Iterable[str], max_symbols: int) -> tuple[str, ...]:
    if max_symbols <= 0:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        clean = _normalize_symbol(symbol)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if len(result) >= max_symbols:
            break
    return tuple(result)


def _limited_context_symbols(symbols: Iterable[str], max_symbols: int) -> tuple[str, ...]:
    return _limited_symbols(symbols, max_symbols)


def _split_symbols(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in re.split(r"[,\s]+", str(value or "")) if part.strip())


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper().replace("/", ".")
    return symbol if symbol and len(symbol) <= 32 else ""


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _first_present(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _configured_int(env_name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(env_name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _env_flag(env_name: str, default: bool) -> bool:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _secret_configured(value: str | None) -> bool:
    return str(value or "").strip().upper() not in _PLACEHOLDER_SECRETS


def _looks_like_cme_dataset(dataset: str) -> bool:
    text = str(dataset or "").strip().upper()
    return any(token in text for token in ("GLBX", "CME", "CBOT", "NYMEX", "COMEX", "MDP3"))


def _is_provider_limit_warning(message: str) -> bool:
    text = str(message or "").lower()
    return any(term in text for term in ("limit", "quota", "rate", "plan", "entitlement", "permission", "401", "403", "429"))


def _short_warning(message: str, api_key: str | None) -> str:
    clean = " ".join(_redact_databento_secret(str(message or ""), api_key).split())
    return clean[:240] + ("..." if len(clean) > 240 else "")


def _redact_databento_secret(message: str, api_key: str | None) -> str:
    text = str(message or "")
    clean_key = str(api_key or "").strip()
    if clean_key:
        text = text.replace(clean_key, "[REDACTED]")
    text = re.sub(r"(?i)(DATABENTO_API_KEY\s*[:=]\s*['\"]?)[^,'\"\s)}]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(key=)[^&\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^,'\"\s)}]+", r"\1[REDACTED]", text)
    return text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
