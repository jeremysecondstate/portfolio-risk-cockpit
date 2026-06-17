from __future__ import annotations

import csv
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

import requests

from app.analytics.technical_analysis import parse_quote_snapshot
MARKET_DATA_FILE_PATH_ENV = "MARKET_SCREENER_MARKET_DATA_PATH"
MARKET_DATA_SYMBOL_LIMIT_ENV = "MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT"
DEFAULT_MARKET_DATA_SYMBOL_LIMIT = 100
LOCAL_FILE_CACHE_TTL = timedelta(minutes=10)
FMP_API_KEY_ENV = "FMP_API_KEY"
FMP_BASE_URL_ENV = "FMP_BASE_URL"
FMP_MARKET_DATA_SYMBOL_LIMIT_ENV = "FMP_MARKET_DATA_SYMBOL_LIMIT"
FMP_CACHE_TTL_SECONDS_ENV = "FMP_CACHE_TTL_SECONDS"
MARKET_DATA_FALLBACK_PROVIDER_ENV = "MARKET_SCREENER_FALLBACK_PROVIDER"
MARKET_DATA_FALLBACK_SYMBOL_LIMIT_ENV = "MARKET_SCREENER_FALLBACK_SYMBOL_LIMIT"
ALPHA_VANTAGE_API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"
ALPHA_VANTAGE_BASE_URL_ENV = "ALPHA_VANTAGE_BASE_URL"
ALPHA_VANTAGE_CACHE_TTL_SECONDS_ENV = "ALPHA_VANTAGE_CACHE_TTL_SECONDS"
DEFAULT_FMP_BASE_URL = "https://financialmodelingprep.com/stable"
DEFAULT_FMP_MARKET_DATA_SYMBOL_LIMIT = 100
DEFAULT_FMP_CACHE_TTL_SECONDS = 900
DEFAULT_ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"
DEFAULT_ALPHA_VANTAGE_FALLBACK_SYMBOL_LIMIT = 25
DEFAULT_ALPHA_VANTAGE_CACHE_TTL_SECONDS = 900
FMP_QUOTE_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/quote"
FMP_PROFILE_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/profile-symbol"
FMP_PROFILE_BY_CIK_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/profile-cik"
FMP_KEY_METRICS_TTM_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/key-metrics-ttm"
FMP_RATIOS_TTM_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/ratios-ttm"
FMP_INCOME_GROWTH_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/income-statement-growth"
FMP_SHARES_FLOAT_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/shares-float"
ALPHA_VANTAGE_DOC_URL = "https://www.alphavantage.co/documentation/"
_SHARED_FMP_CACHE: dict[tuple[str, str, str], tuple[float, Mapping[str, Any]]] = {}
_SHARED_ALPHA_VANTAGE_CACHE: dict[tuple[str, str, str], tuple[float, Mapping[str, Any]]] = {}


@dataclass(frozen=True)
class MarketDataFieldProvenance:
    field: str
    source: str
    source_url: str | None = None
    source_detail: str = ""
    fetched_at: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MarketDataFieldProvenance":
        return cls(
            field=str(payload.get("field") or "").strip(),
            source=_optional_string(payload.get("source")) or "Market data provider",
            source_url=_optional_string(payload.get("source_url") or payload.get("url")),
            source_detail=_optional_string(payload.get("source_detail")) or "",
            fetched_at=_optional_string(payload.get("fetched_at")) or "",
        )


@dataclass(frozen=True)
class MarketQuoteFundamentalsRecord:
    symbol: str
    price: float | None = None
    change_percent: float | None = None
    volume: float | None = None
    avg_volume: float | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    eps: float | None = None
    revenue_growth: float | None = None
    source: str = "Market data provider"
    source_url: str | None = None
    fetched_at: str = ""
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    shares_float: float | None = None
    shares_outstanding: float | None = None
    field_provenance: tuple[MarketDataFieldProvenance, ...] = ()
    cik: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MarketQuoteFundamentalsRecord":
        source = _optional_string(payload.get("source")) or "Market data provider"
        source_url = _optional_string(payload.get("source_url") or payload.get("url"))
        fetched_at = _optional_string(payload.get("fetched_at")) or _now()
        values = {
            "symbol": _normalize_symbol(_first_present(payload, "symbol", "ticker")),
            "exchange": _optional_string(_first_present(payload, "exchange", "exchangeShortName", "exchange_short_name")),
            "sector": _optional_string(_first_present(payload, "sector")),
            "industry": _optional_string(_first_present(payload, "industry")),
            "price": _optional_float(_first_present(payload, "price", "last", "last_price")),
            "change_percent": _optional_float(_first_present(payload, "change_percent", "percent_change", "changesPercentage", "changePercentage", "changePercent", "change_percent")),
            "volume": _optional_float(_first_present(payload, "volume", "total_volume", "totalVolume")),
            "avg_volume": _optional_float(_first_present(payload, "avg_volume", "average_volume", "avgVolume", "averageVolume")),
            "market_cap": _optional_float(_first_present(payload, "market_cap", "marketCap", "marketCapTTM", "mktCap", "marketCapitalization", "MarketCapitalization")),
            "pe_ratio": _optional_float(_first_present(payload, "pe_ratio", "pe", "peRatio", "peRatioTTM", "PERatio", "priceEarningsRatio", "priceEarningsRatioTTM", "priceToEarningsRatioTTM")),
            "eps": _optional_float(_first_present(payload, "eps", "EPS", "epsTTM", "earnings_per_share", "earningsPerShareTTM", "netIncomePerShareTTM")),
            "revenue_growth": _optional_float(_first_present(payload, "revenue_growth", "revenueGrowth", "revenueGrowthTTM", "growthRevenue", "QuarterlyRevenueGrowthYOY")),
            "shares_float": _optional_float(_first_present(payload, "shares_float", "float", "sharesFloat", "floatShares", "freeFloat")),
            "shares_outstanding": _optional_float(_first_present(payload, "shares_outstanding", "sharesOutstanding", "outstandingShares", "shares_outstanding", "weightedAverageShsOut", "weightedAverageShsOutTTM")),
            "cik": _normalize_cik(_first_present(payload, "cik", "cik_str", "CIK")) or None,
        }
        provenance_payload = payload.get("field_provenance")
        field_provenance = _field_provenance_from_payload(provenance_payload)
        if not field_provenance:
            field_provenance = _provenance_for_values(values, source=source, source_url=source_url, fetched_at=fetched_at)
        return cls(
            **values,
            source=source,
            source_url=source_url,
            fetched_at=fetched_at,
            field_provenance=field_provenance,
        )


@dataclass(frozen=True)
class MarketDataProviderStatus:
    source: str
    status: str
    fetched_at: str
    message: str


@dataclass(frozen=True)
class MarketQuoteFundamentalsSnapshot:
    records: tuple[MarketQuoteFundamentalsRecord, ...]
    fetched_at: str
    statuses: tuple[MarketDataProviderStatus, ...]
    errors: tuple[str, ...] = ()
    diagnostics: Mapping[str, int] = field(default_factory=dict)


class MarketQuoteFundamentalsProvider(Protocol):
    provider_name: str

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        ...

    def quote_fundamentals_by_cik(
        self,
        ciks: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        ...


class LocalMarketDataFileProvider:
    provider_name = "local_market_data_file"

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        cache_ttl: timedelta = LOCAL_FILE_CACHE_TTL,
    ) -> None:
        self.path = Path(path or os.getenv(MARKET_DATA_FILE_PATH_ENV, "") or "") if (path or os.getenv(MARKET_DATA_FILE_PATH_ENV, "")) else None
        self.cache_ttl = cache_ttl
        self._cached_rows: tuple[MarketQuoteFundamentalsRecord, ...] | None = None
        self._cached_at = 0.0

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        wanted = set(_limited_symbols(symbols, max_symbols))
        if self.path is None:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Local market data file", "unavailable", fetched_at, f"No {MARKET_DATA_FILE_PATH_ENV} file is configured."),),
                diagnostics={"provider_unavailable": 1},
            )
        if not self.path.exists():
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Local market data file", "unavailable", fetched_at, f"Configured market data file does not exist: {self.path}"),),
                diagnostics={"provider_unavailable": 1},
            )

        try:
            rows = self._read_rows(force_refresh=force_refresh)
        except Exception as exc:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Local market data file", "error", fetched_at, f"Could not read local market data file: {exc}"),),
                errors=(str(exc),),
                diagnostics={"provider_unavailable": 1},
            )
        filtered = tuple(record for record in rows if not wanted or record.symbol in wanted)
        enriched = sum(1 for record in filtered if _quote_record_has_any_value(record))
        return MarketQuoteFundamentalsSnapshot(
            records=filtered,
            fetched_at=fetched_at,
            statuses=(
                MarketDataProviderStatus(
                    "Local market data file",
                    "available" if filtered else "empty",
                    fetched_at,
                    f"Loaded {len(filtered)} quote/fundamental row(s) from {self.path}.",
                ),
            ),
            diagnostics={
                "rows_enriched_by_local_file": enriched,
                "rows_provider_returned_no_usable_data": max(0, len(wanted) - enriched) if wanted else 0,
            },
        )

    def _read_rows(self, *, force_refresh: bool) -> tuple[MarketQuoteFundamentalsRecord, ...]:
        now = time.time()
        if self._cached_rows is not None and not force_refresh and now - self._cached_at <= self.cache_ttl.total_seconds():
            return self._cached_rows
        assert self.path is not None
        if self.path.suffix.lower() == ".json":
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            raw_rows = payload.get("records", payload) if isinstance(payload, dict) else payload
            if not isinstance(raw_rows, list):
                raise ValueError("JSON market data file must contain a list or a {'records': [...]} object.")
            rows = tuple(record for record in (MarketQuoteFundamentalsRecord.from_dict(row) for row in raw_rows if isinstance(row, Mapping)) if record.symbol)
        elif self.path.suffix.lower() in {".csv", ".tsv"}:
            delimiter = "\t" if self.path.suffix.lower() == ".tsv" else ","
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                rows = tuple(record for record in (MarketQuoteFundamentalsRecord.from_dict(row) for row in csv.DictReader(handle, delimiter=delimiter)) if record.symbol)
        else:
            raise ValueError("Local market data file must be .json, .csv, or .tsv.")
        self._cached_rows = rows
        self._cached_at = now
        return rows


class SchwabQuoteFundamentalsProvider:
    provider_name = "schwab_quote"

    def __init__(self, schwab_session: Any | None) -> None:
        self.schwab_session = schwab_session

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        get_quote = getattr(self.schwab_session, "get_quote", None)
        if not callable(get_quote):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Schwab quote", "unavailable", fetched_at, "No authenticated Schwab market-data session is available."),),
                diagnostics={"provider_unavailable": 1},
            )
        records: list[MarketQuoteFundamentalsRecord] = []
        errors: list[str] = []
        blocked = 0
        no_usable = 0
        requested = _limited_symbols(symbols, max_symbols)
        for symbol in requested:
            try:
                status_code, payload = get_quote(symbol)
                if int(status_code) != 200:
                    errors.append(f"{symbol}: Schwab quote returned HTTP {status_code}")
                    if int(status_code) in {401, 403, 429}:
                        blocked += 1
                    continue
                snapshot = parse_quote_snapshot(symbol, payload)
                price = snapshot.last or snapshot.mark
                if price is None and snapshot.total_volume is None:
                    errors.append(f"{symbol}: Schwab quote payload had no usable price or volume fields")
                    no_usable += 1
                    continue
                records.append(
                    MarketQuoteFundamentalsRecord(
                        symbol=symbol,
                        price=price,
                        volume=snapshot.total_volume,
                        source="Schwab quote",
                        fetched_at=fetched_at,
                    )
                )
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
        status = "available" if records else "empty"
        message = f"Loaded {len(records)} Schwab quote row(s) for {len(requested)} capped screener symbol(s)."
        if errors:
            status = "partial" if records else "error"
            message += f" {len(errors)} quote lookup(s) failed."
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(records),
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("Schwab quote", status, fetched_at, message),),
            errors=tuple(errors),
            diagnostics={
                "rows_enriched_by_schwab_quote": len(records),
                "rows_blocked_by_provider_plan_rate_auth_limit": blocked,
                "rows_provider_returned_no_usable_data": no_usable,
            },
        )


class FmpProviderWarning(RuntimeError):
    """Nonblocking FMP provider warning safe to show in source/status UI."""


class FmpQuoteFundamentalsProvider:
    provider_name = "fmp_quote_fundamentals"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        session: requests.Session | Any | None = None,
        timeout_seconds: float = 8.0,
        symbol_limit: int | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv(FMP_API_KEY_ENV, "")
        self.base_url = (base_url or os.getenv(FMP_BASE_URL_ENV, DEFAULT_FMP_BASE_URL) or DEFAULT_FMP_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.symbol_limit = (
            max(0, min(100, int(symbol_limit)))
            if symbol_limit is not None
            else _configured_int(FMP_MARKET_DATA_SYMBOL_LIMIT_ENV, DEFAULT_FMP_MARKET_DATA_SYMBOL_LIMIT, minimum=0, maximum=100)
        )
        self.cache_ttl_seconds = (
            max(0, int(cache_ttl_seconds))
            if cache_ttl_seconds is not None
            else _configured_int(FMP_CACHE_TTL_SECONDS_ENV, DEFAULT_FMP_CACHE_TTL_SECONDS, minimum=0, maximum=86_400)
        )
        self._cache: dict[tuple[str, str, str], tuple[float, Mapping[str, Any]]] = {} if session is not None else _SHARED_FMP_CACHE

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

        if not _optional_string(self.api_key):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP quote/fundamentals",
                        "unavailable",
                        fetched_at,
                        f"FMP enrichment: 0 rows updated; {skipped_limited} skipped/limited; cache used for 0. No {FMP_API_KEY_ENV} is configured; Schwab/local providers remain available.",
                    ),
                ),
                diagnostics={
                    "provider_unavailable": 1,
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                },
            )

        if max_symbols <= 0 or self.symbol_limit <= 0:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP quote/fundamentals",
                        "disabled",
                        fetched_at,
                        f"FMP enrichment: 0 rows updated; {len(capped_input)} skipped/limited; cache used for 0. FMP enrichment is disabled because the symbol cap is 0.",
                    ),
                ),
                diagnostics={"rows_skipped_by_configured_symbol_cap": len(capped_input)},
            )

        records_by_symbol: dict[str, MarketQuoteFundamentalsRecord] = {}
        warnings: list[str] = []
        cache_hits = 0
        quote_rows = 0
        profile_rows = 0
        key_metrics_rows = 0
        ratios_rows = 0
        growth_rows = 0
        shares_float_rows = 0

        quote_payloads: dict[str, Mapping[str, Any]] = {}
        quote_blocked = False
        try:
            quote_payloads, quote_cache_hits = self._quote_payloads(requested, force_refresh=force_refresh)
            cache_hits += quote_cache_hits
        except FmpProviderWarning as exc:
            warnings.append(str(exc))
            quote_blocked = _is_fmp_limit_warning(str(exc))

        for symbol, payload in quote_payloads.items():
            record = self._record_from_payload(symbol, payload, source="FMP quote", source_url=FMP_QUOTE_DOC_URL, fetched_at=fetched_at)
            if _quote_record_has_any_value(record):
                records_by_symbol[symbol] = record
                quote_rows += 1

        profile_symbols = () if quote_blocked else requested
        for symbol in profile_symbols:
            try:
                profile_payload, profile_cache_hit = self._profile_payload(symbol, force_refresh=force_refresh)
                cache_hits += int(profile_cache_hit)
            except FmpProviderWarning as exc:
                warnings.append(str(exc))
                if _is_fmp_limit_warning(str(exc)):
                    break
                continue
            if not profile_payload:
                continue
            profile_record = self._record_from_payload(symbol, profile_payload, source="FMP profile", source_url=FMP_PROFILE_DOC_URL, fetched_at=fetched_at)
            if not _quote_record_has_any_value(profile_record):
                continue
            profile_rows += 1
            existing = records_by_symbol.get(symbol)
            records_by_symbol[symbol] = profile_record if existing is None else _merge_quote_records(existing, profile_record)
        fmp_blocked = quote_blocked or any(_is_fmp_limit_warning(warning) for warning in warnings)
        if not fmp_blocked:
            for symbol in requested:
                existing = records_by_symbol.get(symbol)
                if existing is not None and not _record_needs_deeper_fmp_fields(existing):
                    continue
                for endpoint, source, source_url in _FMP_DEEP_ENDPOINTS:
                    existing = records_by_symbol.get(symbol)
                    if existing is not None and not _record_needs_fmp_endpoint(existing, endpoint):
                        continue
                    try:
                        payload, endpoint_cache_hit = self._single_symbol_payload(endpoint, symbol, force_refresh=force_refresh)
                        cache_hits += int(endpoint_cache_hit)
                    except FmpProviderWarning as exc:
                        warnings.append(str(exc))
                        if _is_fmp_limit_warning(str(exc)):
                            break
                        continue
                    if not payload:
                        continue
                    endpoint_record = self._record_from_payload(symbol, payload, source=source, source_url=source_url, fetched_at=fetched_at)
                    if not _quote_record_has_any_value(endpoint_record):
                        continue
                    if endpoint == "key-metrics-ttm":
                        key_metrics_rows += 1
                    elif endpoint == "ratios-ttm":
                        ratios_rows += 1
                    elif endpoint == "income-statement-growth":
                        growth_rows += 1
                    elif endpoint == "shares-float":
                        shares_float_rows += 1
                    existing = records_by_symbol.get(symbol)
                    records_by_symbol[symbol] = endpoint_record if existing is None else _merge_quote_records(existing, endpoint_record)
                if warnings and _is_fmp_limit_warning(warnings[-1]):
                    break

        records = tuple(records_by_symbol.values())
        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        no_usable_rows = max(0, len(requested) - len(records))
        paid_mode_text = (
            "paid/high-cap FMP mode is active"
            if self.symbol_limit >= DEFAULT_FMP_MARKET_DATA_SYMBOL_LIMIT
            else "bounded FMP mode is active"
        )
        message = (
            f"FMP enrichment: {len(records)} rows updated; quote rows {quote_rows}; profile rows {profile_rows}; "
            f"key metrics {key_metrics_rows}; ratios {ratios_rows}; growth {growth_rows}; shares-float {shares_float_rows}; "
            f"profile-by-CIK rows 0; cache used for {cache_hits}; {skipped_limited} skipped/limited; {no_usable_rows} no usable data. "
            f"FMP cap is {self.symbol_limit} symbol(s) via {FMP_MARKET_DATA_SYMBOL_LIMIT_ENV}; {paid_mode_text}."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)} Schwab/local providers remain available."

        return MarketQuoteFundamentalsSnapshot(
            records=records,
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("FMP quote/fundamentals", status, fetched_at, _redact_fmp_secret(message, self.api_key)),),
            errors=tuple(_redact_fmp_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "rows_enriched_by_fmp_quote": quote_rows,
                "rows_enriched_by_fmp_profile": profile_rows,
                "rows_enriched_by_fmp_key_metrics": key_metrics_rows,
                "rows_enriched_by_fmp_ratios": ratios_rows,
                "rows_enriched_by_fmp_income_growth": growth_rows,
                "rows_enriched_by_fmp_shares_float": shares_float_rows,
                "fmp_cache_hits": cache_hits,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_fmp_limit_warning(warning) for warning in warnings) else 0,
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "rows_provider_returned_no_usable_data": no_usable_rows,
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
        capped_input = _limited_ciks(ciks, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))

        if not _optional_string(self.api_key):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP profile-by-CIK",
                        "unavailable",
                        fetched_at,
                        f"FMP profile-by-CIK: 0 rows updated; {skipped_limited} skipped/limited. No {FMP_API_KEY_ENV} is configured.",
                    ),
                ),
                diagnostics={
                    "provider_unavailable": 1,
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                },
            )

        if max_symbols <= 0 or self.symbol_limit <= 0:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP profile-by-CIK",
                        "disabled",
                        fetched_at,
                        f"FMP profile-by-CIK: 0 rows updated; {len(capped_input)} skipped/limited because the symbol cap is 0.",
                    ),
                ),
                diagnostics={"rows_skipped_by_configured_symbol_cap": len(capped_input)},
            )

        records: list[MarketQuoteFundamentalsRecord] = []
        warnings: list[str] = []
        cache_hits = 0
        for cik in requested:
            try:
                payload, cache_hit = self._profile_by_cik_payload(cik, force_refresh=force_refresh)
                cache_hits += int(cache_hit)
            except FmpProviderWarning as exc:
                warnings.append(str(exc))
                if _is_fmp_limit_warning(str(exc)):
                    break
                continue
            if not payload:
                continue
            symbol = _normalize_symbol(_first_present(payload, "symbol", "ticker"))
            record = self._record_from_payload(
                symbol,
                payload,
                source="FMP profile-by-CIK",
                source_url=FMP_PROFILE_BY_CIK_DOC_URL,
                fetched_at=fetched_at,
                cik=cik,
            )
            if _quote_record_has_any_value(record) or record.symbol:
                records.append(record)

        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        no_usable_rows = max(0, len(requested) - len(records))
        message = (
            f"FMP profile-by-CIK: {len(records)} rows updated; cache used for {cache_hits}; {skipped_limited} skipped/limited; {no_usable_rows} no usable data. "
            "CIK lookups are only requested for capped filing rows that still need trusted identity/profile data."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)}"

        return MarketQuoteFundamentalsSnapshot(
            records=tuple(records),
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("FMP profile-by-CIK", status, fetched_at, _redact_fmp_secret(message, self.api_key)),),
            errors=tuple(_redact_fmp_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "rows_enriched_by_fmp_profile_by_cik": len(records),
                "fmp_cache_hits": cache_hits,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_fmp_limit_warning(warning) for warning in warnings) else 0,
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "rows_provider_returned_no_usable_data": no_usable_rows,
            },
        )

    def _quote_payloads(self, symbols: tuple[str, ...], *, force_refresh: bool) -> tuple[dict[str, Mapping[str, Any]], int]:
        payloads: dict[str, Mapping[str, Any]] = {}
        missing: list[str] = []
        cache_hits = 0
        for symbol in symbols:
            cached = self._cache_get("quote", symbol, force_refresh=force_refresh)
            if cached is None:
                missing.append(symbol)
            else:
                payloads[symbol] = cached
                cache_hits += 1
        if not missing:
            return payloads, cache_hits

        payload = self._get_json("batch-quote", {"symbols": ",".join(missing)})
        rows = _coerce_fmp_rows(payload)
        for row in rows:
            symbol = _normalize_symbol(_first_present(row, "symbol", "ticker"))
            if not symbol and len(missing) == 1:
                symbol = missing[0]
            if not symbol or symbol not in missing:
                continue
            payloads[symbol] = row
            self._cache_set("quote", symbol, row)
        return payloads, cache_hits

    def _profile_payload(self, symbol: str, *, force_refresh: bool) -> tuple[Mapping[str, Any] | None, bool]:
        cached = self._cache_get("profile", symbol, force_refresh=force_refresh)
        if cached is not None:
            return cached, True
        payload = self._get_json("profile", {"symbol": symbol})
        rows = _coerce_fmp_rows(payload)
        selected = next((row for row in rows if _normalize_symbol(_first_present(row, "symbol", "ticker")) == symbol), rows[0] if rows else None)
        if selected is not None:
            self._cache_set("profile", symbol, selected)
        return selected, False

    def _profile_by_cik_payload(self, cik: str, *, force_refresh: bool) -> tuple[Mapping[str, Any] | None, bool]:
        normalized_cik = _normalize_cik(cik)
        if not normalized_cik:
            return None, False
        cached = self._cache_get("profile-cik", normalized_cik, force_refresh=force_refresh)
        if cached is not None:
            return cached, True
        payload = self._get_json("profile-cik", {"cik": _fmp_cik_param(normalized_cik)})
        rows = _coerce_fmp_rows(payload)
        selected = rows[0] if rows else None
        if selected is not None:
            self._cache_set("profile-cik", normalized_cik, selected)
        return selected, False

    def _single_symbol_payload(self, endpoint: str, symbol: str, *, force_refresh: bool) -> tuple[Mapping[str, Any] | None, bool]:
        cached = self._cache_get(endpoint, symbol, force_refresh=force_refresh)
        if cached is not None:
            return cached, True
        payload = self._get_json(endpoint, {"symbol": symbol})
        rows = _coerce_fmp_rows(payload)
        selected = next((row for row in rows if _normalize_symbol(_first_present(row, "symbol", "ticker")) == symbol), rows[0] if rows else None)
        if selected is not None:
            self._cache_set(endpoint, symbol, selected)
        return selected, False

    def _record_from_payload(
        self,
        symbol: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        source_url: str,
        fetched_at: str,
        cik: str | None = None,
    ) -> MarketQuoteFundamentalsRecord:
        values = dict(payload)
        values.setdefault("symbol", symbol)
        if cik:
            values.setdefault("cik", cik)
        values.update(_normalized_fmp_payload_fields(values))
        values["source"] = source
        values["source_url"] = source_url
        values["fetched_at"] = fetched_at
        return MarketQuoteFundamentalsRecord.from_dict(values)

    def _get_json(self, endpoint: str, params: Mapping[str, str]) -> Any:
        url = f"{self.base_url}/{endpoint.strip('/')}"
        try:
            response = self.session.get(
                url,
                params=dict(params),
                headers={"apikey": str(self.api_key), "User-Agent": "portfolio-risk-cockpit/1.0"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise FmpProviderWarning(_redact_fmp_secret(f"FMP {endpoint} request failed: {exc}", self.api_key)) from None

        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403}:
            raise FmpProviderWarning(f"FMP {endpoint} authentication was rejected (HTTP {status_code}); check {FMP_API_KEY_ENV}.")
        if status_code == 429:
            raise FmpProviderWarning(f"FMP {endpoint} rate or daily plan limit was reached (HTTP 429).")
        if status_code < 200 or status_code >= 300:
            raise FmpProviderWarning(f"FMP {endpoint} returned HTTP {status_code}.")
        try:
            payload = response.json()
        except ValueError:
            raise FmpProviderWarning(f"FMP {endpoint} returned a non-JSON response.") from None
        plan_limit = _detect_fmp_plan_limit(payload)
        if plan_limit:
            raise FmpProviderWarning(f"FMP {endpoint} plan limit response: {plan_limit}")
        return payload

    def _cache_get(self, endpoint: str, symbol: str, *, force_refresh: bool) -> Mapping[str, Any] | None:
        if force_refresh or self.cache_ttl_seconds <= 0:
            return None
        key = (self.base_url, endpoint, symbol)
        cached = self._cache.get(key)
        if cached is None:
            return None
        cached_at, payload = cached
        if time.time() - cached_at > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return payload

    def _cache_set(self, endpoint: str, symbol: str, payload: Mapping[str, Any]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[(self.base_url, endpoint, symbol)] = (time.time(), dict(payload))


class AlphaVantageFallbackProvider:
    provider_name = "alpha_vantage_fallback"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        session: requests.Session | Any | None = None,
        timeout_seconds: float = 8.0,
        symbol_limit: int | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv(ALPHA_VANTAGE_API_KEY_ENV, "")
        self.base_url = (base_url or os.getenv(ALPHA_VANTAGE_BASE_URL_ENV, DEFAULT_ALPHA_VANTAGE_BASE_URL) or DEFAULT_ALPHA_VANTAGE_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.symbol_limit = (
            max(0, min(100, int(symbol_limit)))
            if symbol_limit is not None
            else _configured_int(MARKET_DATA_FALLBACK_SYMBOL_LIMIT_ENV, DEFAULT_ALPHA_VANTAGE_FALLBACK_SYMBOL_LIMIT, minimum=0, maximum=100)
        )
        self.cache_ttl_seconds = (
            max(0, int(cache_ttl_seconds))
            if cache_ttl_seconds is not None
            else _configured_int(ALPHA_VANTAGE_CACHE_TTL_SECONDS_ENV, DEFAULT_ALPHA_VANTAGE_CACHE_TTL_SECONDS, minimum=0, maximum=86_400)
        )
        self._cache: dict[tuple[str, str, str], tuple[float, Mapping[str, Any]]] = {} if session is not None else _SHARED_ALPHA_VANTAGE_CACHE

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

        if not _optional_string(self.api_key):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Fallback Alpha Vantage",
                        "unavailable",
                        fetched_at,
                        f"Fallback provider requested but no {ALPHA_VANTAGE_API_KEY_ENV} is configured.",
                    ),
                ),
                diagnostics={
                    "provider_unavailable": 1,
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                },
            )

        if max_symbols <= 0 or self.symbol_limit <= 0:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Fallback Alpha Vantage",
                        "disabled",
                        fetched_at,
                        f"Fallback Alpha Vantage: 0 rows updated; {len(capped_input)} skipped/limited because the fallback symbol cap is 0.",
                    ),
                ),
                diagnostics={"rows_skipped_by_configured_symbol_cap": len(capped_input)},
            )

        records_by_symbol: dict[str, MarketQuoteFundamentalsRecord] = {}
        warnings: list[str] = []
        cache_hits = 0
        quote_rows = 0
        overview_rows = 0
        for symbol in requested:
            try:
                quote_payload, quote_cache_hit = self._payload("GLOBAL_QUOTE", symbol, force_refresh=force_refresh)
                cache_hits += int(quote_cache_hit)
                quote_record = self._quote_record(symbol, quote_payload, fetched_at=fetched_at)
                if _quote_record_has_any_value(quote_record):
                    records_by_symbol[symbol] = quote_record
                    quote_rows += 1
            except RuntimeError as exc:
                warnings.append(str(exc))
                if _is_alpha_vantage_limit_warning(str(exc)):
                    break
            try:
                overview_payload, overview_cache_hit = self._payload("OVERVIEW", symbol, force_refresh=force_refresh)
                cache_hits += int(overview_cache_hit)
                overview_record = self._overview_record(symbol, overview_payload, fetched_at=fetched_at)
                if _quote_record_has_any_value(overview_record):
                    existing = records_by_symbol.get(symbol)
                    records_by_symbol[symbol] = overview_record if existing is None else _merge_quote_records(existing, overview_record)
                    overview_rows += 1
            except RuntimeError as exc:
                warnings.append(str(exc))
                if _is_alpha_vantage_limit_warning(str(exc)):
                    break

        records = tuple(records_by_symbol.values())
        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        message = (
            f"Fallback Alpha Vantage: {len(records)} rows updated; {skipped_limited} skipped/limited; cache used for {cache_hits}. "
            "Fallback is only attached to visible-page or selected-row enrichment when explicitly configured."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)}"
        return MarketQuoteFundamentalsSnapshot(
            records=records,
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("Fallback Alpha Vantage", status, fetched_at, _redact_fmp_secret(message, self.api_key)),),
            errors=tuple(_redact_fmp_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "rows_enriched_by_fallback_provider": len(records),
                "rows_enriched_by_fallback_quote": quote_rows,
                "rows_enriched_by_fallback_profile": overview_rows,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_alpha_vantage_limit_warning(warning) for warning in warnings) else 0,
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "rows_provider_returned_no_usable_data": max(0, len(requested) - len(records)),
            },
        )

    def _payload(self, function: str, symbol: str, *, force_refresh: bool) -> tuple[Mapping[str, Any], bool]:
        cached = self._cache_get(function, symbol, force_refresh=force_refresh)
        if cached is not None:
            return cached, True
        try:
            response = self.session.get(
                self.base_url,
                params={"function": function, "symbol": symbol, "apikey": self.api_key},
                headers={"User-Agent": "portfolio-risk-cockpit/1.0"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(_redact_fmp_secret(f"Alpha Vantage {function} request failed: {exc}", self.api_key)) from None
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403, 429}:
            raise RuntimeError(f"Alpha Vantage {function} authentication/rate limit returned HTTP {status_code}.")
        if status_code < 200 or status_code >= 300:
            raise RuntimeError(f"Alpha Vantage {function} returned HTTP {status_code}.")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(f"Alpha Vantage {function} returned a non-JSON response.") from None
        warning = _detect_alpha_vantage_warning(payload)
        if warning:
            raise RuntimeError(warning)
        if not isinstance(payload, Mapping):
            return {}, False
        self._cache_set(function, symbol, payload)
        return payload, False

    def _quote_record(self, symbol: str, payload: Mapping[str, Any], *, fetched_at: str) -> MarketQuoteFundamentalsRecord:
        quote = payload.get("Global Quote") if isinstance(payload.get("Global Quote"), Mapping) else payload
        values = {
            "symbol": symbol,
            "price": _first_present(quote, "05. price", "price"),
            "volume": _first_present(quote, "06. volume", "volume"),
            "change_percent": _first_present(quote, "10. change percent", "change_percent"),
            "source": "Fallback Alpha Vantage quote",
            "source_url": ALPHA_VANTAGE_DOC_URL,
            "fetched_at": fetched_at,
        }
        return MarketQuoteFundamentalsRecord.from_dict(values)

    def _overview_record(self, symbol: str, payload: Mapping[str, Any], *, fetched_at: str) -> MarketQuoteFundamentalsRecord:
        values = {
            "symbol": symbol,
            "exchange": _first_present(payload, "Exchange", "exchange"),
            "sector": _first_present(payload, "Sector", "sector"),
            "industry": _first_present(payload, "Industry", "industry"),
            "market_cap": _first_present(payload, "MarketCapitalization", "marketCap"),
            "pe_ratio": _first_present(payload, "PERatio", "peRatio"),
            "eps": _first_present(payload, "EPS", "eps"),
            "revenue_growth": _first_present(payload, "QuarterlyRevenueGrowthYOY", "revenueGrowth"),
            "shares_outstanding": _first_present(payload, "SharesOutstanding", "sharesOutstanding"),
            "source": "Fallback Alpha Vantage profile",
            "source_url": ALPHA_VANTAGE_DOC_URL,
            "fetched_at": fetched_at,
        }
        return MarketQuoteFundamentalsRecord.from_dict(values)

    def _cache_get(self, endpoint: str, symbol: str, *, force_refresh: bool) -> Mapping[str, Any] | None:
        if force_refresh or self.cache_ttl_seconds <= 0:
            return None
        key = (self.base_url, endpoint, symbol)
        cached = self._cache.get(key)
        if cached is None:
            return None
        cached_at, payload = cached
        if time.time() - cached_at > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return payload

    def _cache_set(self, endpoint: str, symbol: str, payload: Mapping[str, Any]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[(self.base_url, endpoint, symbol)] = (time.time(), dict(payload))


_FMP_DEEP_ENDPOINTS = (
    ("key-metrics-ttm", "FMP key metrics TTM", FMP_KEY_METRICS_TTM_DOC_URL),
    ("ratios-ttm", "FMP ratios TTM", FMP_RATIOS_TTM_DOC_URL),
    ("income-statement-growth", "FMP income growth", FMP_INCOME_GROWTH_DOC_URL),
    ("shares-float", "FMP shares float", FMP_SHARES_FLOAT_DOC_URL),
)


def _record_needs_deeper_fmp_fields(record: MarketQuoteFundamentalsRecord) -> bool:
    return any(
        value is None
        for value in (
            record.market_cap,
            record.pe_ratio,
            record.eps,
            record.revenue_growth,
            record.shares_float,
            record.shares_outstanding,
        )
    )


def _record_needs_fmp_endpoint(record: MarketQuoteFundamentalsRecord, endpoint: str) -> bool:
    if endpoint == "key-metrics-ttm":
        return record.market_cap is None or record.pe_ratio is None or record.eps is None
    if endpoint == "ratios-ttm":
        return record.pe_ratio is None
    if endpoint == "income-statement-growth":
        return record.revenue_growth is None
    if endpoint == "shares-float":
        return record.shares_float is None or record.shares_outstanding is None
    return _record_needs_deeper_fmp_fields(record)


def _normalized_fmp_payload_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for target, keys in (
        ("market_cap", ("marketCap", "marketCapTTM", "mktCap")),
        ("pe_ratio", ("pe", "peRatio", "peRatioTTM", "PERatio", "priceEarningsRatio", "priceEarningsRatioTTM", "priceToEarningsRatioTTM")),
        ("eps", ("eps", "EPS", "epsTTM", "earningsPerShareTTM", "netIncomePerShareTTM")),
        ("shares_float", ("sharesFloat", "floatShares", "freeFloat", "float")),
        ("shares_outstanding", ("sharesOutstanding", "outstandingShares", "weightedAverageShsOut", "weightedAverageShsOutTTM")),
        ("exchange", ("exchangeShortName", "exchange")),
    ):
        value = _first_present(payload, *keys)
        if value not in (None, ""):
            normalized[target] = value
    growth = _fmp_percent_value(_first_present(payload, "revenueGrowth", "revenueGrowthTTM", "growthRevenue", "QuarterlyRevenueGrowthYOY"))
    if growth is not None:
        normalized["revenue_growth"] = growth
    return normalized


def _fmp_percent_value(value: Any) -> float | None:
    parsed = _optional_float(value)
    if parsed is None:
        return None
    if abs(parsed) <= 1:
        return parsed * 100
    return parsed


class CompositeMarketDataProvider:
    provider_name = "composite_market_data"

    def __init__(self, providers: Iterable[MarketQuoteFundamentalsProvider]) -> None:
        self.providers = tuple(providers)

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        requested = _limited_symbols(symbols, max_symbols)
        if not self.providers:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Market quote/fundamental provider", "unavailable", fetched_at, "No market quote/fundamental provider is configured."),),
            )

        merged: dict[str, MarketQuoteFundamentalsRecord] = {}
        statuses: list[MarketDataProviderStatus] = []
        errors: list[str] = []
        diagnostics: dict[str, int] = {}
        for provider in self.providers:
            snapshot = provider.quote_fundamentals(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            statuses.extend(snapshot.statuses)
            errors.extend(snapshot.errors)
            _merge_diagnostics(diagnostics, snapshot.diagnostics)
            for record in snapshot.records:
                existing = merged.get(record.symbol)
                merged[record.symbol] = record if existing is None else _merge_quote_records(existing, record)
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(merged.values()),
            fetched_at=fetched_at,
            statuses=tuple(statuses),
            errors=tuple(errors),
            diagnostics=diagnostics,
        )

    def quote_fundamentals_by_cik(
        self,
        ciks: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        requested = _limited_ciks(ciks, max_symbols)
        if not self.providers:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Market quote/fundamental provider", "unavailable", fetched_at, "No market quote/fundamental provider is configured."),),
                diagnostics={"provider_unavailable": 1},
            )

        merged: dict[str, MarketQuoteFundamentalsRecord] = {}
        statuses: list[MarketDataProviderStatus] = []
        errors: list[str] = []
        diagnostics: dict[str, int] = {}
        for provider in self.providers:
            by_cik = getattr(provider, "quote_fundamentals_by_cik", None)
            if not callable(by_cik) or provider is self:
                continue
            snapshot = by_cik(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            statuses.extend(snapshot.statuses)
            errors.extend(snapshot.errors)
            _merge_diagnostics(diagnostics, snapshot.diagnostics)
            for record in snapshot.records:
                key = _normalize_symbol(record.symbol) or _normalize_cik(record.cik)
                if not key:
                    continue
                existing = merged.get(key)
                merged[key] = record if existing is None else _merge_quote_records(existing, record)
        if not statuses:
            statuses.append(
                MarketDataProviderStatus(
                    "FMP profile-by-CIK",
                    "unavailable",
                    fetched_at,
                    "No configured market-data provider supports CIK-based quote/profile lookup.",
                )
            )
            diagnostics["provider_unavailable"] = diagnostics.get("provider_unavailable", 0) + 1
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(merged.values()),
            fetched_at=fetched_at,
            statuses=tuple(statuses),
            errors=tuple(errors),
            diagnostics=diagnostics,
        )


def configured_market_data_provider(
    *,
    schwab_session: Any | None = None,
    local_path: str | Path | None = None,
    include_fallback_provider: bool = False,
) -> CompositeMarketDataProvider:
    providers: list[MarketQuoteFundamentalsProvider] = [LocalMarketDataFileProvider(local_path)]
    if schwab_session is not None:
        providers.append(SchwabQuoteFundamentalsProvider(schwab_session))
    from app.data.databento_provider import configured_databento_equities_provider

    providers.append(configured_databento_equities_provider())
    providers.append(FmpQuoteFundamentalsProvider())
    if include_fallback_provider:
        fallback_provider = configured_fallback_market_data_provider()
        if fallback_provider is not None:
            providers.append(fallback_provider)
    return CompositeMarketDataProvider(providers)


def configured_fallback_market_data_provider() -> MarketQuoteFundamentalsProvider | None:
    provider_name = str(os.getenv(MARKET_DATA_FALLBACK_PROVIDER_ENV, "") or "").strip().lower().replace("-", "_")
    if provider_name not in {"alpha_vantage", "alphavantage"}:
        return None
    return AlphaVantageFallbackProvider()


def configured_market_data_symbol_limit(default: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT) -> int:
    return _configured_int(MARKET_DATA_SYMBOL_LIMIT_ENV, default, minimum=0, maximum=1000)


def _merge_quote_records(left: MarketQuoteFundamentalsRecord, right: MarketQuoteFundamentalsRecord) -> MarketQuoteFundamentalsRecord:
    field_provenance = _merge_quote_field_provenance(left, right)
    return MarketQuoteFundamentalsRecord(
        symbol=left.symbol or right.symbol,
        exchange=_prefer_ladder_field(left.exchange, right.exchange),
        sector=_prefer_ladder_field(left.sector, right.sector),
        industry=_prefer_ladder_field(left.industry, right.industry),
        price=_prefer_ladder_field(left.price, right.price),
        change_percent=_prefer_ladder_field(left.change_percent, right.change_percent),
        volume=_prefer_ladder_field(left.volume, right.volume),
        avg_volume=_prefer_ladder_field(left.avg_volume, right.avg_volume),
        market_cap=_prefer_ladder_field(left.market_cap, right.market_cap),
        pe_ratio=_prefer_ladder_field(left.pe_ratio, right.pe_ratio),
        eps=_prefer_ladder_field(left.eps, right.eps),
        revenue_growth=_prefer_ladder_field(left.revenue_growth, right.revenue_growth),
        shares_float=_prefer_ladder_field(left.shares_float, right.shares_float),
        shares_outstanding=_prefer_ladder_field(left.shares_outstanding, right.shares_outstanding),
        source=", ".join(dict.fromkeys([source for source in (left.source, right.source) if source])),
        source_url=left.source_url or right.source_url,
        fetched_at=left.fetched_at or right.fetched_at,
        field_provenance=field_provenance,
        cik=left.cik or right.cik,
    )


def _merge_quote_field_provenance(
    left: MarketQuoteFundamentalsRecord,
    right: MarketQuoteFundamentalsRecord,
) -> tuple[MarketDataFieldProvenance, ...]:
    left_by_field = _quote_provenance_by_field(left)
    right_by_field = _quote_provenance_by_field(right)
    merged: list[MarketDataFieldProvenance] = []
    for field in _QUOTE_VALUE_FIELDS:
        selected = right_by_field.get(field) if _field_was_selected_from_right(left, right, field) else left_by_field.get(field)
        if selected is None:
            selected = left_by_field.get(field) or right_by_field.get(field)
        if selected is not None:
            merged.append(selected)
    return _dedupe_field_provenance(merged)


def _quote_record_has_any_value(record: MarketQuoteFundamentalsRecord) -> bool:
    return any(
        value is not None
        for value in (
            record.exchange,
            record.sector,
            record.industry,
            record.price,
            record.market_cap,
            record.volume,
            record.avg_volume,
            record.change_percent,
            record.pe_ratio,
            record.eps,
            record.revenue_growth,
            record.shares_float,
            record.shares_outstanding,
        )
    )


_QUOTE_VALUE_FIELDS = (
    "exchange",
    "sector",
    "industry",
    "price",
    "market_cap",
    "volume",
    "avg_volume",
    "change_percent",
    "pe_ratio",
    "eps",
    "revenue_growth",
    "shares_float",
    "shares_outstanding",
)


def _field_was_selected_from_right(
    left: MarketQuoteFundamentalsRecord,
    right: MarketQuoteFundamentalsRecord,
    field: str,
) -> bool:
    right_value = getattr(right, field)
    left_value = getattr(left, field)
    return right_value is not None and left_value is None


def _quote_provenance_by_field(record: MarketQuoteFundamentalsRecord) -> dict[str, MarketDataFieldProvenance]:
    rows = _field_provenance_from_payload(getattr(record, "field_provenance", ()))
    if not rows:
        rows = _provenance_for_values(
            {field: getattr(record, field) for field in _QUOTE_VALUE_FIELDS},
            source=record.source,
            source_url=record.source_url,
            fetched_at=record.fetched_at,
        )
    return {row.field: row for row in rows if row.field}


def _field_provenance_from_payload(payload: Any) -> tuple[MarketDataFieldProvenance, ...]:
    if not payload:
        return ()
    rows: list[MarketDataFieldProvenance] = []
    for item in payload if isinstance(payload, (list, tuple)) else ():
        if isinstance(item, MarketDataFieldProvenance):
            row = item
        elif isinstance(item, Mapping):
            row = MarketDataFieldProvenance.from_dict(item)
        else:
            continue
        if row.field:
            rows.append(row)
    return _dedupe_field_provenance(rows)


def _provenance_for_values(
    values: Mapping[str, Any],
    *,
    source: str,
    source_url: str | None,
    fetched_at: str,
    source_detail: str = "",
) -> tuple[MarketDataFieldProvenance, ...]:
    rows = [
        MarketDataFieldProvenance(field=field, source=source, source_url=source_url, source_detail=source_detail, fetched_at=fetched_at)
        for field, value in values.items()
        if value is not None and field in _QUOTE_VALUE_FIELDS
    ]
    return _dedupe_field_provenance(rows)


def _dedupe_field_provenance(rows: Iterable[MarketDataFieldProvenance]) -> tuple[MarketDataFieldProvenance, ...]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[MarketDataFieldProvenance] = []
    for row in rows:
        key = (row.field, row.source, row.source_url or "", row.source_detail, row.fetched_at)
        if not row.field or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return tuple(result)


def _prefer_ladder_field(left: Any, right: Any) -> Any:
    return left if left is not None else right


def _record_is_newer(incoming: MarketQuoteFundamentalsRecord, existing: MarketQuoteFundamentalsRecord) -> bool:
    incoming_time = _parse_timestamp(incoming.fetched_at)
    existing_time = _parse_timestamp(existing.fetched_at)
    if incoming_time is None:
        return existing_time is None and bool(incoming.fetched_at and not existing.fetched_at)
    if existing_time is None:
        return True
    return incoming_time > existing_time


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


def _limited_ciks(ciks: Iterable[str], max_symbols: int) -> tuple[str, ...]:
    if max_symbols <= 0:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for cik in ciks:
        clean = _normalize_cik(cik)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if len(result) >= max_symbols:
            break
    return tuple(result)


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper().replace("/", ".")
    return symbol if symbol and len(symbol) <= 16 else ""


def _normalize_cik(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(10) if digits else ""


def _fmp_cik_param(value: Any) -> str:
    normalized = _normalize_cik(value)
    return normalized.lstrip("0") or normalized


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


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


def _coerce_fmp_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    if isinstance(payload, Mapping):
        rows = payload.get("data") or payload.get("results") or payload.get("records")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, Mapping)]
        return [payload]
    return []


def _detect_fmp_plan_limit(payload: Any) -> str | None:
    fragments: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if isinstance(value, (str, int, float)):
                fragments.append(f"{key}: {value}")
    elif isinstance(payload, list):
        for row in payload[:3]:
            if isinstance(row, Mapping):
                for key, value in row.items():
                    if isinstance(value, (str, int, float)):
                        fragments.append(f"{key}: {value}")
    text = " ".join(str(fragment) for fragment in fragments)
    compact = text.lower()
    if not compact:
        return None
    limit_terms = ("limit", "rate", "quota", "plan", "upgrade", "premium", "not available")
    error_terms = ("error", "message", "reach", "exceeded", "forbidden", "unauthorized")
    if any(term in compact for term in limit_terms) and any(term in compact for term in error_terms):
        return _short_warning(text, None)
    return None


def _detect_alpha_vantage_warning(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("Error Message", "Note", "Information"):
        value = payload.get(key)
        if value:
            return f"Alpha Vantage provider warning: {value}"
    compact = " ".join(str(value) for value in payload.values() if isinstance(value, (str, int, float))).lower()
    if compact and any(term in compact for term in ("limit", "premium", "rate", "apikey", "invalid api")):
        return f"Alpha Vantage provider warning: {_short_warning(compact, None)}"
    return None


def _is_fmp_limit_warning(message: str) -> bool:
    compact = str(message or "").lower()
    return any(term in compact for term in ("limit", "quota", "rate", "plan", "429", "403", "401"))


def _is_alpha_vantage_limit_warning(message: str) -> bool:
    compact = str(message or "").lower()
    return any(term in compact for term in ("limit", "premium", "rate", "apikey", "invalid api", "429", "403", "401"))


def _merge_diagnostics(target: dict[str, int], source: Mapping[str, int] | None) -> None:
    for key, value in (source or {}).items():
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if amount:
            target[str(key)] = target.get(str(key), 0) + amount


def _short_warning(message: str, api_key: str | None) -> str:
    clean = " ".join(_redact_fmp_secret(str(message or ""), api_key).split())
    return clean[:240] + ("..." if len(clean) > 240 else "")


def _redact_fmp_secret(message: str, api_key: str | None) -> str:
    text = str(message or "")
    clean_key = str(api_key or "").strip()
    if clean_key:
        text = text.replace(clean_key, "[REDACTED]")
    text = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(apikey['\"]?\s*[:=]\s*['\"]?)[^,'\"\s)}]+", r"\1[REDACTED]", text)
    return text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
