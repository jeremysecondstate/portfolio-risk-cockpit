from __future__ import annotations

import csv
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

import requests

from app.analytics.technical_analysis import parse_quote_snapshot
MARKET_DATA_FILE_PATH_ENV = "MARKET_SCREENER_MARKET_DATA_PATH"
MARKET_DATA_SYMBOL_LIMIT_ENV = "MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT"
DEFAULT_MARKET_DATA_SYMBOL_LIMIT = 50
LOCAL_FILE_CACHE_TTL = timedelta(minutes=10)
FMP_API_KEY_ENV = "FMP_API_KEY"
FMP_BASE_URL_ENV = "FMP_BASE_URL"
FMP_MARKET_DATA_SYMBOL_LIMIT_ENV = "FMP_MARKET_DATA_SYMBOL_LIMIT"
FMP_CACHE_TTL_SECONDS_ENV = "FMP_CACHE_TTL_SECONDS"
DEFAULT_FMP_BASE_URL = "https://financialmodelingprep.com/stable"
DEFAULT_FMP_MARKET_DATA_SYMBOL_LIMIT = 20
DEFAULT_FMP_CACHE_TTL_SECONDS = 900
FMP_QUOTE_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/quote"
FMP_PROFILE_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/profile-symbol"
_SHARED_FMP_CACHE: dict[tuple[str, str, str], tuple[float, Mapping[str, Any]]] = {}


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MarketQuoteFundamentalsRecord":
        return cls(
            symbol=_normalize_symbol(_first_present(payload, "symbol", "ticker")),
            exchange=_optional_string(_first_present(payload, "exchange", "exchangeShortName", "exchange_short_name")),
            sector=_optional_string(_first_present(payload, "sector")),
            industry=_optional_string(_first_present(payload, "industry")),
            price=_optional_float(_first_present(payload, "price", "last", "last_price")),
            change_percent=_optional_float(_first_present(payload, "change_percent", "percent_change", "changesPercentage", "changePercentage", "changePercent", "change_percent")),
            volume=_optional_float(_first_present(payload, "volume", "total_volume", "totalVolume")),
            avg_volume=_optional_float(_first_present(payload, "avg_volume", "average_volume", "avgVolume", "averageVolume")),
            market_cap=_optional_float(_first_present(payload, "market_cap", "marketCap", "mktCap", "marketCapitalization", "MarketCapitalization")),
            pe_ratio=_optional_float(_first_present(payload, "pe_ratio", "pe", "peRatio", "PERatio", "priceEarningsRatio")),
            eps=_optional_float(_first_present(payload, "eps", "EPS", "earnings_per_share")),
            revenue_growth=_optional_float(_first_present(payload, "revenue_growth", "revenueGrowth", "revenueGrowthTTM", "QuarterlyRevenueGrowthYOY")),
            shares_float=_optional_float(_first_present(payload, "shares_float", "float", "sharesFloat", "floatShares")),
            shares_outstanding=_optional_float(_first_present(payload, "shares_outstanding", "sharesOutstanding", "shares_outstanding")),
            source=_optional_string(payload.get("source")) or "Market data provider",
            source_url=_optional_string(payload.get("source_url") or payload.get("url")),
            fetched_at=_optional_string(payload.get("fetched_at")) or _now(),
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
            )
        if not self.path.exists():
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Local market data file", "unavailable", fetched_at, f"Configured market data file does not exist: {self.path}"),),
            )

        try:
            rows = self._read_rows(force_refresh=force_refresh)
        except Exception as exc:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Local market data file", "error", fetched_at, f"Could not read local market data file: {exc}"),),
                errors=(str(exc),),
            )
        filtered = tuple(record for record in rows if not wanted or record.symbol in wanted)
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
            )
        records: list[MarketQuoteFundamentalsRecord] = []
        errors: list[str] = []
        requested = _limited_symbols(symbols, max_symbols)
        for symbol in requested:
            try:
                status_code, payload = get_quote(symbol)
                if int(status_code) != 200:
                    errors.append(f"{symbol}: Schwab quote returned HTTP {status_code}")
                    continue
                snapshot = parse_quote_snapshot(symbol, payload)
                price = snapshot.last or snapshot.mark
                if price is None and snapshot.total_volume is None:
                    errors.append(f"{symbol}: Schwab quote payload had no usable price or volume fields")
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
            )

        records_by_symbol: dict[str, MarketQuoteFundamentalsRecord] = {}
        warnings: list[str] = []
        cache_hits = 0

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
            existing = records_by_symbol.get(symbol)
            records_by_symbol[symbol] = profile_record if existing is None else _merge_quote_records(existing, profile_record)

        records = tuple(records_by_symbol.values())
        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        message = (
            f"FMP enrichment: {len(records)} rows updated; {skipped_limited} skipped/limited; cache used for {cache_hits}. "
            f"FMP cap is {self.symbol_limit} symbol(s) via {FMP_MARKET_DATA_SYMBOL_LIMIT_ENV}; page/selected-row enrichment keeps free-plan usage bounded."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)} Schwab/local providers remain available."

        return MarketQuoteFundamentalsSnapshot(
            records=records,
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("FMP quote/fundamentals", status, fetched_at, _redact_fmp_secret(message, self.api_key)),),
            errors=tuple(_redact_fmp_secret(warning, self.api_key) for warning in warnings[:4]),
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

    def _record_from_payload(
        self,
        symbol: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        source_url: str,
        fetched_at: str,
    ) -> MarketQuoteFundamentalsRecord:
        values = dict(payload)
        values.setdefault("symbol", symbol)
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
        for provider in self.providers:
            snapshot = provider.quote_fundamentals(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            statuses.extend(snapshot.statuses)
            errors.extend(snapshot.errors)
            for record in snapshot.records:
                existing = merged.get(record.symbol)
                merged[record.symbol] = record if existing is None else _merge_quote_records(existing, record)
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(merged.values()),
            fetched_at=fetched_at,
            statuses=tuple(statuses),
            errors=tuple(errors),
        )


def configured_market_data_provider(*, schwab_session: Any | None = None, local_path: str | Path | None = None) -> CompositeMarketDataProvider:
    providers: list[MarketQuoteFundamentalsProvider] = [LocalMarketDataFileProvider(local_path)]
    if schwab_session is not None:
        providers.append(SchwabQuoteFundamentalsProvider(schwab_session))
    providers.append(FmpQuoteFundamentalsProvider())
    return CompositeMarketDataProvider(providers)


def configured_market_data_symbol_limit(default: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT) -> int:
    return _configured_int(MARKET_DATA_SYMBOL_LIMIT_ENV, default, minimum=0, maximum=100)


def _merge_quote_records(left: MarketQuoteFundamentalsRecord, right: MarketQuoteFundamentalsRecord) -> MarketQuoteFundamentalsRecord:
    right_is_newer = _record_is_newer(right, left)
    return MarketQuoteFundamentalsRecord(
        symbol=left.symbol or right.symbol,
        exchange=_prefer_field(left.exchange, right.exchange, right_is_newer),
        sector=_prefer_field(left.sector, right.sector, right_is_newer),
        industry=_prefer_field(left.industry, right.industry, right_is_newer),
        price=_prefer_field(left.price, right.price, right_is_newer),
        change_percent=_prefer_field(left.change_percent, right.change_percent, right_is_newer),
        volume=_prefer_field(left.volume, right.volume, right_is_newer),
        avg_volume=_prefer_field(left.avg_volume, right.avg_volume, right_is_newer),
        market_cap=_prefer_field(left.market_cap, right.market_cap, right_is_newer),
        pe_ratio=_prefer_field(left.pe_ratio, right.pe_ratio, right_is_newer),
        eps=_prefer_field(left.eps, right.eps, right_is_newer),
        revenue_growth=_prefer_field(left.revenue_growth, right.revenue_growth, right_is_newer),
        shares_float=_prefer_field(left.shares_float, right.shares_float, right_is_newer),
        shares_outstanding=_prefer_field(left.shares_outstanding, right.shares_outstanding, right_is_newer),
        source=", ".join(dict.fromkeys([source for source in (left.source, right.source) if source])),
        source_url=right.source_url if right_is_newer and right.source_url else left.source_url or right.source_url,
        fetched_at=right.fetched_at if right_is_newer else left.fetched_at or right.fetched_at,
    )


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


def _prefer_field(left: Any, right: Any, right_is_newer: bool) -> Any:
    if right is None:
        return left
    if left is None or right_is_newer:
        return right
    return left


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


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper().replace("/", ".")
    return symbol if symbol and len(symbol) <= 16 else ""


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


def _is_fmp_limit_warning(message: str) -> bool:
    compact = str(message or "").lower()
    return any(term in compact for term in ("limit", "quota", "rate", "plan", "429", "403", "401"))


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
