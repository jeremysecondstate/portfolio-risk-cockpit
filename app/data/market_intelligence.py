from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.analytics.technical_analysis import Candle
from app.data.databento_provider import (
    DATABENTO_API_KEY_ENV,
    DatabentoCmeContextProvider,
    DatabentoEquitiesProvider,
    configured_databento_cme_context_provider,
    configured_databento_equities_provider,
)
from app.data.market_data_provider import (
    FMP_API_KEY_ENV,
    FmpQuoteFundamentalsProvider,
    MarketDataProviderStatus,
    MarketQuoteFundamentalsRecord,
    MarketQuoteFundamentalsSnapshot,
)


_DATABENTO_COMPONENT_ROWS_KEY = "__databento_component_rows"
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_ -]?key|token|secret|authorization|bearer)(\s*[=:]\s*)([A-Za-z0-9_\-./:+]{6,})"
)
_PLACEHOLDER_SECRETS = {"", "THIS IS NOT A KEY", "NOT_A_KEY", "CHANGEME", "CHANGE_ME"}


@dataclass(frozen=True)
class ExternalMarketIntelligence:
    symbol: str
    fmp_profile: dict[str, Any] = field(default_factory=dict)
    fmp_quote: dict[str, Any] = field(default_factory=dict)
    fmp_fundamentals: dict[str, Any] = field(default_factory=dict)
    databento_equity_tape: dict[str, Any] = field(default_factory=dict)
    databento_equity_candles: dict[str, tuple[Candle, ...]] = field(default_factory=dict)
    databento_futures_context: dict[str, Any] = field(default_factory=dict)
    source_statuses: tuple[MarketDataProviderStatus, ...] = ()
    warnings: tuple[str, ...] = ()
    provenance: dict[str, Any] = field(default_factory=dict)
    fetched_at: str = ""

    def source_status_lines(self) -> tuple[str, ...]:
        return tuple(_status_line(status) for status in self.source_statuses)


def build_external_market_intelligence(
    symbol: str,
    *,
    schwab_session: Any | None = None,
    force_refresh: bool = False,
    fmp_provider: Any | None = None,
    databento_equities_provider: Any | None = None,
    databento_cme_context_provider: Any | None = None,
) -> ExternalMarketIntelligence:
    clean_symbol = _normalize_symbol(symbol)
    fetched_at = _now()
    active_fmp_provider = fmp_provider if fmp_provider is not None else FmpQuoteFundamentalsProvider()
    active_equities_provider = (
        databento_equities_provider
        if databento_equities_provider is not None
        else configured_databento_equities_provider()
    )
    active_cme_provider = (
        databento_cme_context_provider
        if databento_cme_context_provider is not None
        else configured_databento_cme_context_provider()
    )
    secret_values = _provider_secret_values(active_fmp_provider, active_equities_provider, active_cme_provider)

    source_statuses: list[MarketDataProviderStatus] = []
    warnings: list[str] = []
    provenance: dict[str, Any] = {
        "built_at": fetched_at,
        "schwab_session_supplied": schwab_session is not None,
        "schwab_role": "Schwab quote/candle/account data remains the trusted broker-session input outside this external bundle.",
    }

    fmp_profile = _market_snapshot_payload(
        active_fmp_provider,
        "profile_classification",
        clean_symbol,
        force_refresh=force_refresh,
        statuses=source_statuses,
        warnings=warnings,
        secret_values=secret_values,
    )
    fmp_quote = _market_snapshot_payload(
        active_fmp_provider,
        "quote_tape",
        clean_symbol,
        force_refresh=force_refresh,
        statuses=source_statuses,
        warnings=warnings,
        secret_values=secret_values,
    )
    fmp_fundamentals = _market_snapshot_payload(
        active_fmp_provider,
        "fundamentals",
        clean_symbol,
        force_refresh=force_refresh,
        statuses=source_statuses,
        warnings=warnings,
        secret_values=secret_values,
    )

    databento_equity_tape, databento_equity_candles = _databento_equity_context(
        active_equities_provider,
        clean_symbol,
        force_refresh=force_refresh,
        statuses=source_statuses,
        warnings=warnings,
        secret_values=secret_values,
    )
    databento_futures_context = _databento_cme_context(
        active_cme_provider,
        force_refresh=force_refresh,
        statuses=source_statuses,
        warnings=warnings,
        secret_values=secret_values,
    )

    provenance.update(
        {
            "fmp_profile": _field_provenance(fmp_profile),
            "fmp_quote": _field_provenance(fmp_quote),
            "fmp_fundamentals": _field_provenance(fmp_fundamentals),
            "databento_equity_tape": _field_provenance(databento_equity_tape),
            "databento_equity_candles": {
                key: {"rows": len(value), "source": "Databento US Equities"}
                for key, value in databento_equity_candles.items()
            },
            "databento_futures_context": {
                key: _field_provenance(value if isinstance(value, Mapping) else {})
                for key, value in databento_futures_context.items()
            },
        }
    )

    return ExternalMarketIntelligence(
        symbol=clean_symbol,
        fmp_profile=fmp_profile,
        fmp_quote=fmp_quote,
        fmp_fundamentals=fmp_fundamentals,
        databento_equity_tape=databento_equity_tape,
        databento_equity_candles=databento_equity_candles,
        databento_futures_context=databento_futures_context,
        source_statuses=tuple(source_statuses),
        warnings=tuple(_dedupe(_redact_text(warning, secret_values) for warning in warnings if warning)),
        provenance=_sanitize_payload(provenance, secret_values),
        fetched_at=fetched_at,
    )


def _market_snapshot_payload(
    provider: Any,
    method_name: str,
    symbol: str,
    *,
    force_refresh: bool,
    statuses: list[MarketDataProviderStatus],
    warnings: list[str],
    secret_values: tuple[str, ...],
) -> dict[str, Any]:
    method = getattr(provider, method_name, None)
    if not callable(method):
        fetched_at = _now()
        status = MarketDataProviderStatus(
            f"Market intelligence {method_name.replace('_', ' ')}",
            "unavailable",
            fetched_at,
            f"Configured provider does not expose {method_name.replace('_', ' ')} enrichment.",
        )
        statuses.append(_sanitize_status(status, secret_values))
        return {}
    try:
        snapshot = _call_symbol_snapshot(method, symbol, force_refresh=force_refresh)
    except Exception as exc:
        fetched_at = _now()
        message = _redact_text(f"{method_name.replace('_', ' ').title()} enrichment failed: {exc}", secret_values)
        statuses.append(
            MarketDataProviderStatus(
                f"Market intelligence {method_name.replace('_', ' ')}",
                "error",
                fetched_at,
                message,
            )
        )
        warnings.append(message)
        return {}
    _append_snapshot_status(snapshot, statuses, warnings, secret_values)
    return _record_payload_from_snapshot(snapshot, symbol, secret_values)


def _call_symbol_snapshot(method: Any, symbol: str, *, force_refresh: bool) -> MarketQuoteFundamentalsSnapshot:
    try:
        return method([symbol], force_refresh=force_refresh, max_symbols=1)
    except TypeError:
        return method([symbol], force_refresh=force_refresh)


def _databento_equity_context(
    provider: Any,
    symbol: str,
    *,
    force_refresh: bool,
    statuses: list[MarketDataProviderStatus],
    warnings: list[str],
    secret_values: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, tuple[Candle, ...]]]:
    row_context = getattr(provider, "row_context", None)
    if callable(row_context):
        try:
            row_snapshot = _call_symbol_snapshot(row_context, symbol, force_refresh=force_refresh)
        except Exception as exc:
            fetched_at = _now()
            message = _redact_text(f"Databento US Equities row context failed: {exc}", secret_values)
            statuses.append(MarketDataProviderStatus("Databento US Equities", "error", fetched_at, message))
            warnings.append(message)
            return {}, {}
        _append_snapshot_status(row_snapshot, statuses, warnings, secret_values)
        row = _row_for_symbol(getattr(row_snapshot, "rows_by_symbol", {}) or {}, symbol)
        tape_payload = _databento_tape_payload_from_row(provider, symbol, row, getattr(row_snapshot, "fetched_at", "") or _now(), secret_values)
        candles = _candles_from_databento_row(row)
        return tape_payload, ({symbol: tuple(candles)} if candles else {})

    return _databento_equity_snapshot_fallback(
        provider,
        symbol,
        force_refresh=force_refresh,
        statuses=statuses,
        warnings=warnings,
        secret_values=secret_values,
    )


def _databento_equity_snapshot_fallback(
    provider: Any,
    symbol: str,
    *,
    force_refresh: bool,
    statuses: list[MarketDataProviderStatus],
    warnings: list[str],
    secret_values: tuple[str, ...],
) -> tuple[dict[str, Any], dict[str, tuple[Candle, ...]]]:
    method = getattr(provider, "quote_tape", None) or getattr(provider, "quote_fundamentals", None)
    if not callable(method):
        fetched_at = _now()
        statuses.append(
            MarketDataProviderStatus(
                "Databento US Equities",
                "unavailable",
                fetched_at,
                "Configured Databento equities provider does not expose tape enrichment.",
            )
        )
        return {}, {}
    try:
        snapshot = _call_symbol_snapshot(method, symbol, force_refresh=force_refresh)
    except Exception as exc:
        fetched_at = _now()
        message = _redact_text(f"Databento US Equities tape enrichment failed: {exc}", secret_values)
        statuses.append(MarketDataProviderStatus("Databento US Equities", "error", fetched_at, message))
        warnings.append(message)
        return {}, {}
    _append_snapshot_status(snapshot, statuses, warnings, secret_values)
    return _record_payload_from_snapshot(snapshot, symbol, secret_values), {}


def _databento_cme_context(
    provider: Any,
    *,
    force_refresh: bool,
    statuses: list[MarketDataProviderStatus],
    warnings: list[str],
    secret_values: tuple[str, ...],
) -> dict[str, Any]:
    method = getattr(provider, "context", None)
    if not callable(method):
        fetched_at = _now()
        statuses.append(
            MarketDataProviderStatus(
                "Databento CME context",
                "unavailable",
                fetched_at,
                "Configured Databento CME provider does not expose cross-asset context.",
            )
        )
        return {}
    try:
        snapshot = method(force_refresh=force_refresh)
    except TypeError:
        snapshot = method()
    except Exception as exc:
        fetched_at = _now()
        message = _redact_text(f"Databento CME context failed: {exc}", secret_values)
        statuses.append(MarketDataProviderStatus("Databento CME context", "error", fetched_at, message))
        warnings.append(message)
        return {}
    _append_snapshot_status(snapshot, statuses, warnings, secret_values)
    records = getattr(snapshot, "records", ()) or ()
    context: dict[str, Any] = {}
    for record in records:
        symbol = _normalize_symbol(getattr(record, "symbol", ""))
        if not symbol:
            continue
        payload = record.to_dict() if hasattr(record, "to_dict") else _object_payload(record)
        context[symbol] = _sanitize_payload(payload, secret_values)
    return context


def _databento_tape_payload_from_row(
    provider: Any,
    symbol: str,
    row: Mapping[str, Any] | None,
    fetched_at: str,
    secret_values: tuple[str, ...],
) -> dict[str, Any]:
    if not row:
        return {}
    builder = getattr(provider, "_record_from_row", None)
    if callable(builder):
        try:
            record = builder(symbol, row, fetched_at=fetched_at)
            if isinstance(record, MarketQuoteFundamentalsRecord) or hasattr(record, "to_dict"):
                payload = record.to_dict() if hasattr(record, "to_dict") else _object_payload(record)
                return _sanitize_payload(payload, secret_values)
        except Exception:
            pass
    latest = _latest_databento_row(row)
    payload = {
        "symbol": symbol,
        "price": _optional_float(_first_present(latest, "price", "last", "close", "settle")),
        "volume": _optional_float(_first_present(latest, "volume", "size", "qty", "quantity")),
        "fetched_at": _timestamp_from_row(latest) or fetched_at,
        "source": "Databento US Equities",
    }
    return _sanitize_payload(_drop_empty(payload), secret_values)


def _append_snapshot_status(
    snapshot: Any,
    statuses: list[MarketDataProviderStatus],
    warnings: list[str],
    secret_values: tuple[str, ...],
) -> None:
    for status in getattr(snapshot, "statuses", ()) or ():
        clean_status = _sanitize_status(status, secret_values)
        statuses.append(clean_status)
        if str(clean_status.status).strip().lower() in {"warning", "error"}:
            warnings.append(clean_status.message)
    for error in getattr(snapshot, "errors", ()) or ():
        warning = _redact_text(str(error), secret_values)
        if warning:
            warnings.append(warning)


def _record_payload_from_snapshot(snapshot: Any, symbol: str, secret_values: tuple[str, ...]) -> dict[str, Any]:
    for record in getattr(snapshot, "records", ()) or ():
        if _normalize_symbol(getattr(record, "symbol", "")) != symbol:
            continue
        payload = record.to_dict() if hasattr(record, "to_dict") else _object_payload(record)
        return _sanitize_payload(_drop_empty(payload), secret_values)
    return {}


def _row_for_symbol(rows_by_symbol: Mapping[str, Mapping[str, Any]], symbol: str) -> Mapping[str, Any] | None:
    clean_symbol = _normalize_symbol(symbol)
    for key, row in rows_by_symbol.items():
        if _normalize_symbol(key) == clean_symbol:
            return row
    return None


def _candles_from_databento_row(row: Mapping[str, Any] | None) -> list[Candle]:
    if not row:
        return []
    rows = _databento_component_rows(row)
    candles: list[Candle] = []
    for index, item in enumerate(rows):
        if not isinstance(item, Mapping):
            continue
        close = _optional_float(_first_present(item, "close", "price", "last", "settle"))
        if close is None:
            continue
        open_price = _optional_float(_first_present(item, "open", "open_price", "openPrice")) or close
        high = _optional_float(_first_present(item, "high", "high_price", "highPrice")) or max(open_price, close)
        low = _optional_float(_first_present(item, "low", "low_price", "lowPrice")) or min(open_price, close)
        volume = _optional_float(_first_present(item, "volume", "size", "qty", "quantity")) or 0.0
        datetime_ms = _datetime_ms_from_row(item)
        if datetime_ms == 0:
            datetime_ms = index
        candles.append(Candle(datetime_ms=datetime_ms, open=open_price, high=high, low=low, close=close, volume=volume))
    return sorted(candles, key=lambda candle: candle.datetime_ms)


def _databento_component_rows(row: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    component_rows = row.get(_DATABENTO_COMPONENT_ROWS_KEY)
    if isinstance(component_rows, (list, tuple)):
        rows = tuple(item for item in component_rows if isinstance(item, Mapping))
        if rows:
            return rows
    return (row,)


def _latest_databento_row(row: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = _databento_component_rows(row)
    return rows[-1] if rows else row


def _datetime_ms_from_row(row: Mapping[str, Any]) -> int:
    value = _first_present(row, "datetime", "datetime_ms", "timestamp_ms", "ts_event", "ts_recv", "timestamp", "time")
    if isinstance(value, (int, float)):
        number = int(value)
        return number if number > 10_000_000_000 else number * 1000
    if isinstance(value, str):
        parsed = _parse_datetime(value)
        if parsed is not None:
            return int(parsed.timestamp() * 1000)
    return 0


def _timestamp_from_row(row: Mapping[str, Any]) -> str:
    value = _first_present(row, "ts_event", "ts_recv", "timestamp", "time", "datetime")
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        number = float(value)
        seconds = number / 1000.0 if number > 10_000_000_000 else number
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    return str(value)


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _field_provenance(payload: Mapping[str, Any]) -> Any:
    provenance = payload.get("field_provenance")
    if provenance:
        return provenance
    return {
        "source": payload.get("source", ""),
        "source_url": payload.get("source_url", ""),
        "fetched_at": payload.get("fetched_at", ""),
    }


def _status_line(status: MarketDataProviderStatus) -> str:
    fetched = status.fetched_at or "--"
    return f"{status.source}: {status.status}; fetched {fetched}; {status.message}"


def _sanitize_status(status: Any, secret_values: tuple[str, ...]) -> MarketDataProviderStatus:
    return MarketDataProviderStatus(
        source=_redact_text(str(getattr(status, "source", "") or "Market intelligence source"), secret_values),
        status=_redact_text(str(getattr(status, "status", "") or "unknown"), secret_values),
        fetched_at=_redact_text(str(getattr(status, "fetched_at", "") or ""), secret_values),
        message=_redact_text(str(getattr(status, "message", "") or ""), secret_values),
    )


def _provider_secret_values(*providers: Any) -> tuple[str, ...]:
    values: list[str] = []
    for env_name in (FMP_API_KEY_ENV, DATABENTO_API_KEY_ENV, "ALPHA_VANTAGE_API_KEY"):
        values.append(os.getenv(env_name, "") or "")
    for provider in providers:
        for attr in ("api_key", "token", "secret"):
            values.append(str(getattr(provider, attr, "") or ""))
    return tuple(_dedupe(value for value in values if _looks_like_secret(value)))


def _looks_like_secret(value: str) -> bool:
    clean = str(value or "").strip()
    return len(clean) >= 4 and clean.upper() not in _PLACEHOLDER_SECRETS


def _redact_text(text: str, secret_values: Iterable[str]) -> str:
    redacted = str(text or "")
    for secret in secret_values:
        if secret:
            redacted = redacted.replace(secret, "[REDACTED]")
    return _SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", redacted)


def _sanitize_payload(value: Any, secret_values: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return _redact_text(value, secret_values)
    if isinstance(value, Mapping):
        return {str(key): _sanitize_payload(item, secret_values) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_sanitize_payload(item, secret_values) for item in value)
    if isinstance(value, list):
        return [_sanitize_payload(item, secret_values) for item in value]
    if is_dataclass(value):
        return _sanitize_payload(asdict(value), secret_values)
    return value


def _object_payload(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    payload: dict[str, Any] = {}
    for name in ("symbol", "source", "source_url", "fetched_at", "price", "volume", "timestamp", "dataset", "schema"):
        item = getattr(value, name, None)
        if item not in (None, ""):
            payload[name] = item
    return payload


def _drop_empty(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != "" and value != [] and value != () and value != {}
    }


def _first_present(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _optional_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    rows: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        rows.append(value)
    return rows
