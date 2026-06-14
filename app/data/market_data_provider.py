from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from app.analytics.technical_analysis import parse_quote_snapshot
MARKET_DATA_FILE_PATH_ENV = "MARKET_SCREENER_MARKET_DATA_PATH"
MARKET_DATA_SYMBOL_LIMIT_ENV = "MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT"
DEFAULT_MARKET_DATA_SYMBOL_LIMIT = 50
LOCAL_FILE_CACHE_TTL = timedelta(minutes=10)


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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MarketQuoteFundamentalsRecord":
        return cls(
            symbol=_normalize_symbol(payload.get("symbol") or payload.get("ticker")),
            price=_optional_float(payload.get("price") or payload.get("last") or payload.get("last_price")),
            change_percent=_optional_float(payload.get("change_percent") or payload.get("percent_change") or payload.get("changePercent")),
            volume=_optional_float(payload.get("volume") or payload.get("total_volume") or payload.get("totalVolume")),
            avg_volume=_optional_float(payload.get("avg_volume") or payload.get("average_volume") or payload.get("averageVolume")),
            market_cap=_optional_float(payload.get("market_cap") or payload.get("marketCapitalization") or payload.get("MarketCapitalization")),
            pe_ratio=_optional_float(payload.get("pe_ratio") or payload.get("peRatio") or payload.get("PERatio")),
            eps=_optional_float(payload.get("eps") or payload.get("EPS") or payload.get("earnings_per_share")),
            revenue_growth=_optional_float(payload.get("revenue_growth") or payload.get("revenueGrowth") or payload.get("QuarterlyRevenueGrowthYOY")),
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
    return CompositeMarketDataProvider(providers)


def configured_market_data_symbol_limit(default: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT) -> int:
    try:
        value = int(os.getenv(MARKET_DATA_SYMBOL_LIMIT_ENV, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(0, min(100, value))


def _merge_quote_records(left: MarketQuoteFundamentalsRecord, right: MarketQuoteFundamentalsRecord) -> MarketQuoteFundamentalsRecord:
    return MarketQuoteFundamentalsRecord(
        symbol=left.symbol or right.symbol,
        price=right.price if right.price is not None else left.price,
        change_percent=right.change_percent if right.change_percent is not None else left.change_percent,
        volume=right.volume if right.volume is not None else left.volume,
        avg_volume=right.avg_volume if right.avg_volume is not None else left.avg_volume,
        market_cap=right.market_cap if right.market_cap is not None else left.market_cap,
        pe_ratio=right.pe_ratio if right.pe_ratio is not None else left.pe_ratio,
        eps=right.eps if right.eps is not None else left.eps,
        revenue_growth=right.revenue_growth if right.revenue_growth is not None else left.revenue_growth,
        source=", ".join(dict.fromkeys([source for source in (left.source, right.source) if source])),
        source_url=right.source_url or left.source_url,
        fetched_at=right.fetched_at or left.fetched_at,
    )


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
