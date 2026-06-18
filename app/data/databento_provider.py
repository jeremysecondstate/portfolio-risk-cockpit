from __future__ import annotations

import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.data.market_data_provider import (
    DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    DEFAULT_MARKET_SCREENER_BACKFILL_BATCH_SIZE,
    MarketDataFieldProvenance,
    MarketDataProviderStatus,
    MarketQuoteFundamentalsRecord,
    MarketQuoteFundamentalsSnapshot,
    MARKET_SCREENER_BACKFILL_BATCH_SIZE_ENV,
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
MAX_DATABENTO_EQUITIES_SYMBOL_LIMIT = 5000
DEFAULT_DATABENTO_EQUITIES_CHUNK_SIZE = 100
DEFAULT_DATABENTO_CME_SYMBOL_LIMIT = 25
DEFAULT_DATABENTO_CACHE_TTL_SECONDS = 900
DATABENTO_DOC_URL = "https://databento.com/docs"
DATABENTO_SCHEMAS_DOC_URL = "https://databento.com/docs/schemas-and-data-formats"
DATABENTO_HISTORICAL_DOC_URL = "https://databento.com/docs/api-reference-historical"
RECOMMENDED_DATABENTO_EQUITIES_DATASET = "EQUS.MINI"
RECOMMENDED_DATABENTO_EQUITIES_SCHEMA = "ohlcv-1m"
_DATABENTO_EQUITY_INTRADAY_TAPE_SCHEMAS = {"ohlcv-1m", "ohlcv-1s", "trades", "tbbo", "bbo", "mbp-1", "mbp-10"}
_DATABENTO_EQUITY_UNSUPPORTED_SCREENER_SCHEMAS = {"definition", "definitions", "statistics", "ohlcv-1d"}

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


@dataclass(frozen=True)
class DatabentoEquityRowsSnapshot:
    rows_by_symbol: Mapping[str, Mapping[str, Any]]
    fetched_at: str
    statuses: tuple[MarketDataProviderStatus, ...]
    errors: tuple[str, ...] = ()
    diagnostics: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class DatabentoTechnicalHistorySnapshot:
    rows_by_timeframe: Mapping[str, Mapping[str, Mapping[str, Any]]]
    fetched_at: str
    statuses: tuple[MarketDataProviderStatus, ...]
    errors: tuple[str, ...] = ()
    diagnostics: Mapping[str, Any] = field(default_factory=dict)
    timeframe_diagnostics: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


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
        batch_size: int | None = None,
        lookback_minutes: int = 60,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv(DATABENTO_API_KEY_ENV, "")
        self.dataset = str(dataset if dataset is not None else os.getenv(DATABENTO_EQUITIES_DATASET_ENV, "")).strip()
        self.schema = str(schema if schema is not None else os.getenv(DATABENTO_EQUITIES_SCHEMA_ENV, "")).strip()
        self.enabled = _env_flag(MARKET_SCREENER_ENABLE_DATABENTO_EQUITIES_ENV, False) if enabled is None else bool(enabled)
        self.client = client
        self.symbol_limit = (
            max(0, min(MAX_DATABENTO_EQUITIES_SYMBOL_LIMIT, int(symbol_limit)))
            if symbol_limit is not None
            else _configured_int(DATABENTO_EQUITIES_SYMBOL_LIMIT_ENV, DEFAULT_DATABENTO_EQUITIES_SYMBOL_LIMIT, minimum=0, maximum=MAX_DATABENTO_EQUITIES_SYMBOL_LIMIT)
        )
        self.cache_ttl_seconds = (
            max(0, int(cache_ttl_seconds))
            if cache_ttl_seconds is not None
            else _configured_int(DATABENTO_CACHE_TTL_SECONDS_ENV, DEFAULT_DATABENTO_CACHE_TTL_SECONDS, minimum=0, maximum=86_400)
        )
        self.batch_size = (
            max(1, int(batch_size))
            if batch_size is not None
            else _configured_int(MARKET_SCREENER_BACKFILL_BATCH_SIZE_ENV, DEFAULT_MARKET_SCREENER_BACKFILL_BATCH_SIZE, minimum=1, maximum=500)
        )
        self.lookback_minutes = max(1, int(lookback_minutes))
        self._cache = {} if client is not None else _SHARED_DATABENTO_CACHE

    def quote_tape(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        return self.quote_fundamentals(symbols, force_refresh=force_refresh, max_symbols=max_symbols)

    def row_context(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> DatabentoEquityRowsSnapshot:
        fetched_at = _now()
        capped_input = _limited_symbols(symbols, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))

        disabled = self._disabled_snapshot(fetched_at, len(capped_input))
        if disabled is not None:
            return DatabentoEquityRowsSnapshot(
                rows_by_symbol={},
                fetched_at=fetched_at,
                statuses=disabled.statuses,
                errors=disabled.errors,
                diagnostics=disabled.diagnostics,
            )
        if max_symbols <= 0 or self.symbol_limit <= 0:
            return DatabentoEquityRowsSnapshot(
                rows_by_symbol={},
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Databento US Equities",
                        "disabled",
                        fetched_at,
                        f"Databento US Equities row context: 0 rows updated; {len(capped_input)} skipped/limited because the symbol cap is 0.",
                    ),
                ),
                diagnostics={"rows_skipped_by_configured_symbol_cap": len(capped_input)},
            )

        if _looks_like_cme_dataset(self.dataset):
            message = (
                f"Databento US Equities row context: configured dataset '{self.dataset}' looks like CME/futures coverage. "
                "Refusing to merge futures/options data into selected-equity tape/candle fields; configure "
                f"{DATABENTO_EQUITIES_DATASET_ENV} for US equities or enable CME context separately."
            )
            return DatabentoEquityRowsSnapshot(
                rows_by_symbol={},
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Databento US Equities", "warning", fetched_at, message),),
                errors=(message,),
                diagnostics={
                    "databento_dataset_mismatch_warnings": 1,
                    "rows_provider_returned_no_usable_data": len(requested),
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                },
            )

        config_warnings = _databento_equities_config_warnings(self.dataset, self.schema)
        rows_by_symbol, cache_hits, chunks_attempted, fetch_warnings = self._rows_by_symbol(requested, force_refresh=force_refresh)
        warnings = [*config_warnings, *fetch_warnings]
        status = "available" if rows_by_symbol else "empty"
        if warnings:
            status = "partial" if rows_by_symbol else "warning"
        no_usable = max(0, len(requested) - len(rows_by_symbol))
        message = (
            f"Databento US Equities row context: {len(rows_by_symbol)} raw row set(s) updated; attempted {len(requested)} symbol(s) "
            f"in {chunks_attempted} chunk(s); cache used for {cache_hits}; {skipped_limited} skipped/limited; {no_usable} no usable data. "
            f"Dataset={self.dataset or 'not configured'}; schema={self.schema or 'not configured'}. "
            "Rows are selected-equity tape/candle context only; CME/futures context remains separate."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)}"
        return DatabentoEquityRowsSnapshot(
            rows_by_symbol=rows_by_symbol,
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("Databento US Equities", status, fetched_at, _redact_databento_secret(message, self.api_key)),),
            errors=tuple(_redact_databento_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "databento_equities_symbols_attempted": len(requested),
                "databento_equities_chunks_attempted": chunks_attempted,
                "databento_equities_cache_hits": cache_hits,
                "provider_rows_requested": len(requested),
                "provider_rows_returned": len(rows_by_symbol),
                "provider_rows_parsed": len(rows_by_symbol),
                "provider_rows_updated": len(rows_by_symbol),
                "provider_calls_attempted": chunks_attempted,
                "provider_cache_hits": cache_hits,
                "provider_warnings": len(warnings),
                "databento_dataset_mismatch_warnings": len(config_warnings),
                "rows_provider_returned_no_usable_data": no_usable,
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "provider_unavailable": 1 if fetch_warnings and not rows_by_symbol else 0,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_provider_limit_warning(warning) for warning in fetch_warnings) else 0,
            },
        )

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

        config_warnings = _databento_equities_config_warnings(self.dataset, self.schema)
        records: list[MarketQuoteFundamentalsRecord] = []
        cache_hits = 0
        chunks_attempted = 0
        warnings: list[str] = []
        rows_by_symbol, cache_hits, chunks_attempted, fetch_warnings = self._rows_by_symbol(requested, force_refresh=force_refresh)
        warnings.extend(fetch_warnings)

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
        elif config_warnings:
            status = "partial" if records else "warning"
        message = (
            f"Databento US Equities: {len(records)} rows updated; attempted {len(requested)} symbol(s) in {chunks_attempted} chunk(s); tape/computed tape fields only; cache used for {cache_hits}; "
            f"{skipped_limited} skipped/limited; {no_usable} no usable data. "
            f"Dataset={self.dataset or 'not configured'}; schema={self.schema or 'not configured'}. "
            "Path=historical timeseries backfill; live subscriptions and CME/futures context are not merged into equity screener tape fields. "
            f"Recommended equity tape config is {RECOMMENDED_DATABENTO_EQUITIES_DATASET} + {RECOMMENDED_DATABENTO_EQUITIES_SCHEMA}. "
            "Databento fills supported equity tape fields only; FMP remains the fundamentals/profile source."
        )
        if warnings and any(_is_provider_limit_warning(warning) for warning in warnings):
            message += " Entitlement/auth/rate issue detected; verify Databento API key, dataset entitlement, and rate limits."
        if config_warnings:
            message += f" Config warning: {_short_warning(config_warnings[0], self.api_key)}"
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)}"

        return MarketQuoteFundamentalsSnapshot(
            records=tuple(records),
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("Databento US Equities", status, fetched_at, _redact_databento_secret(message, self.api_key)),),
            errors=tuple(_redact_databento_secret(warning, self.api_key) for warning in (*config_warnings, *warnings)[:4]),
            diagnostics={
                "rows_enriched_by_databento_equities": len(records),
                "databento_equities_symbols_attempted": len(requested),
                "databento_equities_chunks_attempted": chunks_attempted,
                "databento_equities_cache_hits": cache_hits,
                "databento_equities_provider_warnings": len(warnings),
                "provider_rows_requested": len(requested),
                "provider_rows_returned": len(rows_by_symbol),
                "provider_rows_parsed": len(records),
                "provider_rows_updated": len(records),
                "provider_calls_attempted": chunks_attempted,
                "provider_cache_hits": cache_hits,
                "provider_warnings": len(warnings) + len(config_warnings),
                "databento_dataset_mismatch_warnings": len(config_warnings),
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

    def technical_history(
        self,
        symbols: Iterable[str],
        *,
        timeframes: Mapping[str, int] | None = None,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> DatabentoTechnicalHistorySnapshot:
        fetched_at = _now()
        capped_input = _limited_symbols(symbols, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))
        requested_timeframes = {
            str(key): max(1, int(value))
            for key, value in (timeframes or {}).items()
            if str(key or "").strip()
        }
        if not requested_timeframes:
            requested_timeframes = {
                "timing_1m": 24 * 60,
                "timing_5m": 14 * 24 * 60,
                "setup_30m": 14 * 24 * 60,
                "daily_1y": 370 * 24 * 60,
            }

        disabled = self._disabled_snapshot(fetched_at, len(capped_input))
        if disabled is not None:
            return DatabentoTechnicalHistorySnapshot(
                rows_by_timeframe={},
                fetched_at=fetched_at,
                statuses=disabled.statuses,
                errors=disabled.errors,
                diagnostics={
                    **dict(disabled.diagnostics),
                    "databento_technical_timeframes_requested": len(requested_timeframes),
                },
            )
        if max_symbols <= 0 or self.symbol_limit <= 0:
            return DatabentoTechnicalHistorySnapshot(
                rows_by_timeframe={},
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Databento technical history",
                        "disabled",
                        fetched_at,
                        f"Databento technical history: 0 rows updated; {len(capped_input)} skipped/limited because the symbol cap is 0.",
                    ),
                ),
                diagnostics={
                    "rows_skipped_by_configured_symbol_cap": len(capped_input),
                    "databento_technical_timeframes_requested": len(requested_timeframes),
                },
            )
        if _looks_like_cme_dataset(self.dataset):
            message = (
                f"Databento technical history: configured dataset '{self.dataset}' looks like CME/futures coverage. "
                "Refusing to merge futures/options data into selected-equity technical history; configure "
                f"{DATABENTO_EQUITIES_DATASET_ENV} for US equities or enable CME context separately."
            )
            return DatabentoTechnicalHistorySnapshot(
                rows_by_timeframe={},
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Databento technical history", "warning", fetched_at, message),),
                errors=(message,),
                diagnostics={
                    "databento_dataset_mismatch_warnings": 1,
                    "rows_provider_returned_no_usable_data": len(requested),
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                    "databento_technical_timeframes_requested": len(requested_timeframes),
                },
            )

        config_warnings = list(_databento_equities_config_warnings(self.dataset, self.schema))
        rows_by_timeframe: dict[str, Mapping[str, Mapping[str, Any]]] = {}
        timeframe_diagnostics: dict[str, Mapping[str, Any]] = {}
        errors: list[str] = []
        total_cache_hits = 0
        total_chunks_attempted = 0
        skipped_timeframes = 0
        for timeframe_key, lookback_minutes in requested_timeframes.items():
            if timeframe_key == "daily_1y" and not _databento_schema_supports_daily_history(self.schema):
                skipped_timeframes += 1
                timeframe_diagnostics[timeframe_key] = {
                    "status": "skipped",
                    "reason": "Configured Databento schema is not daily/history-capable for a 1y regime read.",
                    "requested_lookback_minutes": lookback_minutes,
                    "rows_returned": 0,
                }
                continue
            rows, cache_hits, chunks_attempted, warnings = self._rows_by_symbol_with_lookback(
                requested,
                force_refresh=force_refresh,
                context=f"equities_history_{timeframe_key}_{lookback_minutes}",
                lookback_minutes=lookback_minutes,
                timeframe_key=timeframe_key,
            )
            total_cache_hits += cache_hits
            total_chunks_attempted += chunks_attempted
            errors.extend(warnings)
            rows_by_timeframe[timeframe_key] = rows
            timeframe_diagnostics[timeframe_key] = {
                "status": "available" if rows else "empty",
                "requested_lookback_minutes": lookback_minutes,
                "rows_returned": len(rows),
                "provider_calls_attempted": chunks_attempted,
                "provider_cache_hits": cache_hits,
            }

        rows_returned = sum(len(rows) for rows in rows_by_timeframe.values())
        warnings = [*config_warnings, *errors]
        status = "available" if rows_returned else "empty"
        if warnings:
            status = "partial" if rows_returned else "warning"
        if skipped_timeframes and rows_returned:
            status = "partial"
        timeframes_text = ", ".join(
            f"{key}:{value}m" for key, value in requested_timeframes.items()
        )
        message = (
            f"Databento technical history: {rows_returned} timeframe row set(s) updated across {len(requested_timeframes)} requested timeframe(s); "
            f"attempted {len(requested)} symbol(s) in {total_chunks_attempted} chunk(s); cache used for {total_cache_hits}; "
            f"{skipped_limited} skipped/limited; {skipped_timeframes} timeframe(s) skipped. "
            f"Requested windows: {timeframes_text}. Dataset={self.dataset or 'not configured'}; schema={self.schema or 'not configured'}. "
            "Short row/tape context is not treated as full daily/regime history."
        )
        if config_warnings:
            message += f" Config warning: {_short_warning(config_warnings[0], self.api_key)}"
        if errors:
            message += f" Provider warning: {_short_warning(errors[0], self.api_key)}"
        return DatabentoTechnicalHistorySnapshot(
            rows_by_timeframe=rows_by_timeframe,
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("Databento technical history", status, fetched_at, _redact_databento_secret(message, self.api_key)),),
            errors=tuple(_redact_databento_secret(warning, self.api_key) for warning in warnings[:6]),
            diagnostics={
                "databento_technical_timeframes_requested": len(requested_timeframes),
                "databento_technical_timeframes_skipped": skipped_timeframes,
                "databento_technical_rows_returned": rows_returned,
                "databento_technical_cache_hits": total_cache_hits,
                "databento_technical_chunks_attempted": total_chunks_attempted,
                "provider_rows_requested": len(requested),
                "provider_rows_returned": rows_returned,
                "provider_calls_attempted": total_chunks_attempted,
                "provider_cache_hits": total_cache_hits,
                "provider_warnings": len(warnings),
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_provider_limit_warning(warning) for warning in errors) else 0,
            },
            timeframe_diagnostics=timeframe_diagnostics,
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
                diagnostics={"provider_rows_requested": capped_count},
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
                diagnostics={"provider_unavailable": 1, "provider_rows_requested": capped_count},
            )
        return None

    def _rows_by_symbol(self, symbols: tuple[str, ...], *, force_refresh: bool) -> tuple[dict[str, Mapping[str, Any]], int, int, list[str]]:
        return self._rows_by_symbol_with_lookback(
            symbols,
            force_refresh=force_refresh,
            context="equities",
            lookback_minutes=self.lookback_minutes,
            timeframe_key=None,
        )

    def _rows_by_symbol_with_lookback(
        self,
        symbols: tuple[str, ...],
        *,
        force_refresh: bool,
        context: str,
        lookback_minutes: int,
        timeframe_key: str | None,
    ) -> tuple[dict[str, Mapping[str, Any]], int, int, list[str]]:
        rows: dict[str, Mapping[str, Any]] = {}
        missing: list[str] = []
        cache_hits = 0
        chunks_attempted = 0
        warnings: list[str] = []
        for symbol in symbols:
            cached = self._cache_get(context, symbol, force_refresh=force_refresh)
            if cached is None:
                missing.append(symbol)
            else:
                rows[symbol] = cached
                cache_hits += 1
        if missing:
            try:
                client = self.client or _build_default_databento_client(self.api_key)
            except DatabentoProviderWarning as exc:
                warnings.append(_redact_databento_secret(str(exc), self.api_key))
                return rows, cache_hits, chunks_attempted, warnings
            for chunk in _chunk_symbols(tuple(missing), self.batch_size):
                chunks_attempted += 1
                try:
                    fetched_rows = _call_databento_rows(
                        client,
                        symbols=chunk,
                        dataset=self.dataset,
                        schema=self.schema,
                        lookback_minutes=lookback_minutes,
                        context=context,
                        timeframe_key=timeframe_key,
                    )
                except DatabentoProviderWarning as exc:
                    warnings.append(_redact_databento_secret(str(exc), self.api_key))
                    break
                except Exception as exc:
                    warnings.append(_redact_databento_secret(f"Databento US Equities fetch failed: {exc}", self.api_key))
                    break
                for symbol, row in _coerce_rows_by_symbol(fetched_rows, chunk).items():
                    rows[symbol] = row
                    self._cache_set(context, symbol, row)
        return rows, cache_hits, chunks_attempted, warnings

    def _record_from_row(self, symbol: str, row: Mapping[str, Any], *, fetched_at: str) -> MarketQuoteFundamentalsRecord:
        timestamp = _row_timestamp(row) or fetched_at
        price = _price_from_row(row)
        volume = _volume_from_row(row)
        change_percent = _change_percent_from_row(row)
        avg_volume = _avg_volume_from_row(row)
        values = {
            "symbol": _normalize_symbol(_first_present(row, "symbol", "raw_symbol", "ticker", "instrument", "instrument_id")) or symbol,
            "price": price,
            "volume": volume,
            "change_percent": change_percent,
            "avg_volume": avg_volume,
            "source": "Databento US Equities",
            "source_url": DATABENTO_SCHEMAS_DOC_URL,
            "fetched_at": timestamp,
        }
        field_provenance = _provenance_for_tape_values(row, values, source="Databento US Equities", fetched_at=timestamp)
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


def configured_databento_equities_provider(
    *,
    symbol_limit: int | None = None,
    cache_ttl_seconds: int | None = None,
    batch_size: int | None = None,
) -> DatabentoEquitiesProvider:
    return DatabentoEquitiesProvider(symbol_limit=symbol_limit, cache_ttl_seconds=cache_ttl_seconds, batch_size=batch_size)


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
    timeframe_key: str | None = None,
) -> Any:
    if timeframe_key:
        technical_method = getattr(client, "fetch_technical_history", None)
        if callable(technical_method):
            return technical_method(
                symbols=symbols,
                dataset=dataset,
                schema=schema,
                timeframe=timeframe_key,
                lookback_minutes=max(1, lookback_minutes),
            )

        generic_method = getattr(client, "fetch_rows", None)
        if callable(generic_method):
            try:
                return generic_method(
                    symbols=symbols,
                    dataset=dataset,
                    schema=schema,
                    context=context,
                    timeframe=timeframe_key,
                    lookback_minutes=max(1, lookback_minutes),
                )
            except TypeError:
                pass

        timeseries = getattr(client, "timeseries", None)
        get_range = getattr(timeseries, "get_range", None)
        if not callable(get_range):
            raise DatabentoProviderWarning(
                "Databento technical history requires fetch_technical_history, timeframe-aware fetch_rows, or timeseries.get_range; "
                "short row/tape clients are not reused for daily/regime history."
            )

    is_equity_context = context == "equities" or context.startswith("equities_history_")
    if not timeframe_key:
        custom_method = getattr(client, "fetch_equity_rows" if is_equity_context else "fetch_context_rows", None)
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
        row_limit = max(1, len(symbols) * min(max(lookback_minutes, 10), 10_000))
        try:
            store = get_range(
                dataset=dataset,
                schema=schema,
                symbols=list(symbols),
                stype_in="raw_symbol",
                start=start.isoformat(),
                end=end.isoformat(),
                limit=row_limit,
            )
        except TypeError:
            store = get_range(
                dataset=dataset,
                schema=schema,
                symbols=list(symbols),
                start=start.isoformat(),
                end=end.isoformat(),
                limit=row_limit,
            )
        try:
            frame = store.to_df()
        except Exception as exc:
            raise DatabentoProviderWarning(f"Databento {dataset}/{schema} response could not be converted to rows: {exc}") from None
        if hasattr(frame, "to_dict"):
            return frame.to_dict(orient="records")
        return frame

    raise DatabentoProviderWarning("Databento client has no supported row fetch method.")


_DATABENTO_COMPONENT_ROWS_KEY = "__databento_component_rows"


def _coerce_rows_by_symbol(payload: Any, requested: tuple[str, ...]) -> dict[str, Mapping[str, Any]]:
    rows_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    requested_set = set(requested)

    def add_row(symbol: str, row: Mapping[str, Any]) -> None:
        clean = _normalize_symbol(symbol)
        if not clean or clean not in requested_set:
            return
        rows_by_symbol.setdefault(clean, []).append(dict(row))

    if isinstance(payload, Mapping):
        if all(isinstance(value, (Mapping, list, tuple)) for value in payload.values()):
            for key, value in payload.items():
                symbol = _normalize_symbol(key)
                if isinstance(value, Mapping):
                    row = dict(value)
                    row.setdefault("symbol", symbol)
                    add_row(symbol, row)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        if isinstance(item, Mapping):
                            row = dict(item)
                            row.setdefault("symbol", symbol)
                            add_row(symbol, row)
            return {symbol: _merge_databento_component_rows(rows) for symbol, rows in rows_by_symbol.items()}
        rows = [payload]
    elif isinstance(payload, (list, tuple)):
        rows = [row for row in payload if isinstance(row, Mapping)]
    else:
        rows = []

    for row in rows:
        symbol = _normalize_symbol(_first_present(row, "symbol", "raw_symbol", "ticker", "instrument", "instrument_id"))
        if not symbol and len(requested) == 1:
            symbol = requested[0]
        add_row(symbol, row)
    return {symbol: _merge_databento_component_rows(rows) for symbol, rows in rows_by_symbol.items()}


def _merge_databento_component_rows(rows: list[Mapping[str, Any]]) -> Mapping[str, Any]:
    ordered = sorted((dict(row) for row in rows), key=_databento_row_sort_key)
    latest = dict(ordered[-1]) if ordered else {}
    if len(ordered) > 1:
        latest[_DATABENTO_COMPONENT_ROWS_KEY] = tuple(ordered)
    return latest


def _databento_row_sort_key(row: Mapping[str, Any]) -> tuple[int, str]:
    timestamp = _row_timestamp(row)
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return (0, timestamp)
    return (1, parsed.isoformat())


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


def _provenance_for_tape_values(
    row: Mapping[str, Any],
    values: Mapping[str, Any],
    *,
    source: str,
    fetched_at: str,
) -> tuple[MarketDataFieldProvenance, ...]:
    rows: list[MarketDataFieldProvenance] = []
    for field in ("price", "volume"):
        if values.get(field) is not None:
            rows.append(MarketDataFieldProvenance(field=field, source=source, source_url=DATABENTO_SCHEMAS_DOC_URL, fetched_at=fetched_at))
    for field, direct_check, detail in (
        ("change_percent", _row_has_direct_change_percent, "computed from Databento open/close or multi-row price history"),
        ("avg_volume", _row_has_direct_avg_volume, "computed from Databento multi-row volume history"),
    ):
        if values.get(field) is None:
            continue
        source_detail = "" if direct_check(row) else detail
        rows.append(
            MarketDataFieldProvenance(
                field=field,
                source=source,
                source_url=DATABENTO_SCHEMAS_DOC_URL,
                source_detail=source_detail,
                fetched_at=fetched_at,
            )
        )
    return tuple(rows)


def _quote_record_has_any_tape_value(record: MarketQuoteFundamentalsRecord) -> bool:
    return (
        record.price is not None
        or record.volume is not None
        or record.change_percent is not None
        or record.avg_volume is not None
    )


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


def _open_price_from_row(row: Mapping[str, Any]) -> float | None:
    return _scaled_price(_first_present(row, "open", "open_price", "open_px"))


def _close_price_from_row(row: Mapping[str, Any]) -> float | None:
    return _scaled_price(_first_present(row, "close", "close_price", "close_px", "price", "last", "last_price", "last_px", "px"))


def _scaled_price(value: Any) -> float | None:
    price = _optional_float(value)
    if price is None:
        return None
    if abs(price) >= 10_000_000:
        return price / 1_000_000_000
    return price


def _volume_from_row(row: Mapping[str, Any]) -> float | None:
    return _optional_float(_first_present(row, "volume", "size", "qty", "quantity"))


def _avg_volume_from_row(row: Mapping[str, Any]) -> float | None:
    direct = _optional_float(_first_present(row, "avg_volume", "average_volume", "avgVolume", "averageVolume"))
    if direct is not None:
        return direct
    component_rows = _databento_component_rows(row)
    if len(component_rows) < 2:
        return None
    previous_volumes = [volume for component in component_rows[:-1] if (volume := _volume_from_row(component)) is not None]
    if not previous_volumes:
        return None
    return sum(previous_volumes) / len(previous_volumes)


def _change_percent_from_row(row: Mapping[str, Any]) -> float | None:
    direct = _optional_float(
        _first_present(row, "change_percent", "percent_change", "changesPercentage", "changePercentage", "changePercent")
    )
    if direct is not None:
        return direct
    component_rows = _databento_component_rows(row)
    if len(component_rows) >= 2:
        start = _open_price_from_row(component_rows[0]) or _close_price_from_row(component_rows[0])
        end = _close_price_from_row(component_rows[-1]) or _price_from_row(component_rows[-1])
        return _percent_change(start, end)
    previous_close = _scaled_price(_first_present(row, "previous_close", "prev_close", "prior_close"))
    latest = _close_price_from_row(row) or _price_from_row(row)
    if previous_close is not None:
        return _percent_change(previous_close, latest)
    return _percent_change(_open_price_from_row(row), latest)


def _percent_change(start: float | None, end: float | None) -> float | None:
    if start in (None, 0) or end is None:
        return None
    return ((end - start) / abs(start)) * 100


def _databento_component_rows(row: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = row.get(_DATABENTO_COMPONENT_ROWS_KEY)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(component for component in raw if isinstance(component, Mapping))


def _row_has_direct_change_percent(row: Mapping[str, Any]) -> bool:
    return _first_present(row, "change_percent", "percent_change", "changesPercentage", "changePercentage", "changePercent") is not None


def _row_has_direct_avg_volume(row: Mapping[str, Any]) -> bool:
    return _first_present(row, "avg_volume", "average_volume", "avgVolume", "averageVolume") is not None


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


def _parse_timestamp(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _chunk_symbols(symbols: tuple[str, ...], chunk_size: int) -> tuple[tuple[str, ...], ...]:
    size = max(1, int(chunk_size))
    return tuple(tuple(symbols[index : index + size]) for index in range(0, len(symbols), size))


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


def _looks_like_equities_dataset(dataset: str) -> bool:
    text = str(dataset or "").strip().upper()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "EQUS",
            "XNAS",
            "XNYS",
            "XASE",
            "ARCX",
            "BATS",
            "IEXG",
            "MEMX",
            "FINN",
            "FINY",
            "EDGA",
            "EDGX",
            "XCHI",
        )
    )


def _databento_equities_config_warnings(dataset: str, schema: str) -> tuple[str, ...]:
    dataset_text = str(dataset or "").strip().upper()
    schema_text = str(schema or "").strip().lower()
    warnings: list[str] = []
    recommended = f"{DATABENTO_EQUITIES_DATASET_ENV}={RECOMMENDED_DATABENTO_EQUITIES_DATASET} and {DATABENTO_EQUITIES_SCHEMA_ENV}={RECOMMENDED_DATABENTO_EQUITIES_SCHEMA}"
    if dataset_text == "EQUS.SUMMARY":
        warnings.append(
            "Databento US Equities config: EQUS.SUMMARY is a summary/end-of-day-style equities dataset and may leave intraday screener tape fields blank; "
            f"use {recommended} for Market Screener equity tape enrichment."
        )
    elif dataset_text and not _looks_like_equities_dataset(dataset_text):
        warnings.append(
            f"Databento US Equities config: dataset '{dataset}' is not recognized as a US equities tape dataset; "
            f"use {recommended} unless this is a custom equities-compatible dataset."
        )
    if schema_text in _DATABENTO_EQUITY_UNSUPPORTED_SCREENER_SCHEMAS:
        warnings.append(
            f"Databento US Equities config: schema '{schema}' cannot reasonably produce intraday price/volume/change/avg-volume screener fields; "
            f"use {recommended}."
        )
    elif schema_text and schema_text not in _DATABENTO_EQUITY_INTRADAY_TAPE_SCHEMAS:
        warnings.append(
            f"Databento US Equities config: schema '{schema}' is not a supported intraday tape schema for screener enrichment; "
            f"use {recommended}."
        )
    return tuple(warnings)


def _databento_schema_supports_daily_history(schema: str) -> bool:
    return str(schema or "").strip().lower() in {"ohlcv-1d", "ohlcv-1day", "ohlcv-daily", "daily", "eod"}


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
