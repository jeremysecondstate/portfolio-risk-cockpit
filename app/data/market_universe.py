from __future__ import annotations

import csv
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from app.data.sec_edgar import SEC_TICKER_URL, TICKER_CACHE_TTL, SecEdgarClient


MARKET_UNIVERSE_SEED_PATH_ENV = "MARKET_UNIVERSE_SEED_PATH"
MARKET_UNIVERSE_PROVIDER_ENV = "MARKET_UNIVERSE_PROVIDER"
DEFAULT_MARKET_UNIVERSE_PROVIDER = "fmp"
FMP_API_KEY_ENV = "FMP_API_KEY"
FMP_BASE_URL_ENV = "FMP_BASE_URL"
DEFAULT_FMP_BASE_URL = "https://financialmodelingprep.com/stable"
FMP_STOCK_LIST_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/stock-list"
DEFAULT_MARKET_UNIVERSE_LIMIT = 750
_SYMBOL_ALIASES = {
    "BRK-B": "BRK.B",
    "BRK/B": "BRK.B",
    "BRK B": "BRK.B",
    "BF-B": "BF.B",
    "BF/B": "BF.B",
    "BF B": "BF.B",
}


@dataclass(frozen=True)
class MarketUniverseEntry:
    symbol: str
    company_name: str | None = None
    cik: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    source: str = "Provider market universe"
    source_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MarketUniverseEntry":
        return cls(
            symbol=_normalize_symbol(payload.get("symbol") or payload.get("ticker")),
            company_name=_optional_string(payload.get("company_name") or payload.get("name") or payload.get("title")),
            cik=_optional_string(payload.get("cik") or payload.get("cik_str")),
            exchange=_optional_string(payload.get("exchange")),
            sector=_optional_string(payload.get("sector")),
            industry=_optional_string(payload.get("industry")),
            source=_optional_string(payload.get("source")) or "Local market universe seed",
            source_url=_optional_string(payload.get("source_url") or payload.get("url")),
        )


@dataclass(frozen=True)
class MarketUniverseSourceStatus:
    source: str
    status: str
    fetched_at: str
    message: str


@dataclass(frozen=True)
class MarketUniverseSnapshot:
    records: tuple[MarketUniverseEntry, ...]
    fetched_at: str
    sources: tuple[str, ...]
    statuses: tuple[MarketUniverseSourceStatus, ...]
    errors: tuple[str, ...] = ()
    used_fallback: bool = False


FALLBACK_MARKET_UNIVERSE: tuple[MarketUniverseEntry, ...] = (
    MarketUniverseEntry("AAPL", "Apple Inc.", exchange="Nasdaq", sector="Technology", industry="Consumer Electronics", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("MSFT", "Microsoft Corporation", exchange="Nasdaq", sector="Technology", industry="Software", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("NVDA", "NVIDIA Corporation", exchange="Nasdaq", sector="Technology", industry="Semiconductors", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("AMZN", "Amazon.com, Inc.", exchange="Nasdaq", sector="Consumer Cyclical", industry="Internet Retail", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("GOOGL", "Alphabet Inc.", exchange="Nasdaq", sector="Communication Services", industry="Internet Content", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("META", "Meta Platforms, Inc.", exchange="Nasdaq", sector="Communication Services", industry="Internet Content", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("TSLA", "Tesla, Inc.", exchange="Nasdaq", sector="Consumer Cyclical", industry="Auto Manufacturers", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("AVGO", "Broadcom Inc.", exchange="Nasdaq", sector="Technology", industry="Semiconductors", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("JPM", "JPMorgan Chase & Co.", exchange="NYSE", sector="Financial Services", industry="Banks", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("UNH", "UnitedHealth Group Incorporated", exchange="NYSE", sector="Healthcare", industry="Healthcare Plans", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("LLY", "Eli Lilly and Company", exchange="NYSE", sector="Healthcare", industry="Drug Manufacturers", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("XOM", "Exxon Mobil Corporation", exchange="NYSE", sector="Energy", industry="Oil & Gas Integrated", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("WMT", "Walmart Inc.", exchange="NYSE", sector="Consumer Defensive", industry="Discount Stores", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("MA", "Mastercard Incorporated", exchange="NYSE", sector="Financial Services", industry="Credit Services", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("AMD", "Advanced Micro Devices, Inc.", exchange="Nasdaq", sector="Technology", industry="Semiconductors", source="Built-in fallback universe", source_url=None),
    MarketUniverseEntry("NFLX", "Netflix, Inc.", exchange="Nasdaq", sector="Communication Services", industry="Entertainment", source="Built-in fallback universe", source_url=None),
)


def fetch_market_universe_snapshot(
    client: SecEdgarClient | None = None,
    *,
    limit: int = DEFAULT_MARKET_UNIVERSE_LIMIT,
    include_fallback: bool = True,
    seed_path: str | Path | None = None,
    provider: str | None = None,
) -> MarketUniverseSnapshot:
    fetched_at = _now()
    statuses: list[MarketUniverseSourceStatus] = []
    errors: list[str] = []
    rows: list[MarketUniverseEntry] = []
    provider_name = (
        provider
        or os.getenv(MARKET_UNIVERSE_PROVIDER_ENV, DEFAULT_MARKET_UNIVERSE_PROVIDER)
        or DEFAULT_MARKET_UNIVERSE_PROVIDER
    ).strip().lower()

    local_seed = _load_local_seed(seed_path or os.getenv(MARKET_UNIVERSE_SEED_PATH_ENV, ""))
    if local_seed:
        rows.extend(local_seed)
        statuses.append(
            MarketUniverseSourceStatus(
                "Local market universe seed",
                "available",
                fetched_at,
                f"Loaded {len(local_seed)} locally configured universe row(s).",
            )
        )

    if provider_name in {"fmp", "provider", "market_data"}:
        try:
            fmp_rows = _load_fmp_stock_universe(limit=limit)
            rows.extend(fmp_rows)
            statuses.append(
                MarketUniverseSourceStatus(
                    "FMP stock-list",
                    "available",
                    fetched_at,
                    f"Loaded {len(fmp_rows)} provider-owned symbol row(s). SEC was not used for screener symbol discovery.",
                )
            )
        except Exception as exc:
            clean_error = _redact_provider_secret(str(exc), os.getenv(FMP_API_KEY_ENV, ""))
            errors.append(f"FMP stock-list: {clean_error}")
            statuses.append(
                MarketUniverseSourceStatus(
                    "FMP stock-list",
                    "unavailable",
                    fetched_at,
                    f"FMP symbol universe unavailable: {clean_error}",
                )
            )
    elif provider_name == "sec":
        try:
            sec_rows = _load_sec_company_tickers(client or SecEdgarClient(timeout_seconds=12), limit=limit)
            rows.extend(sec_rows)
            statuses.append(
                MarketUniverseSourceStatus(
                    "SEC company_tickers.json",
                    "available",
                    fetched_at,
                    f"Loaded {len(sec_rows)} public-company ticker row(s) from SEC cache/API via legacy provider override.",
                )
            )
        except Exception as exc:
            errors.append(f"SEC company_tickers.json: {exc}")
            statuses.append(
                MarketUniverseSourceStatus(
                    "SEC company_tickers.json",
                    "unavailable",
                    fetched_at,
                    f"SEC ticker universe unavailable: {exc}",
                )
            )
    else:
        errors.append(f"Unsupported market universe provider: {provider_name}")
        statuses.append(
            MarketUniverseSourceStatus(
                "Market universe provider",
                "unavailable",
                fetched_at,
                f"Unsupported {MARKET_UNIVERSE_PROVIDER_ENV} value: {provider_name}.",
            )
        )

    used_fallback = False
    if include_fallback and not rows:
        fallback = FALLBACK_MARKET_UNIVERSE[: max(1, min(limit, len(FALLBACK_MARKET_UNIVERSE)))]
        rows.extend(fallback)
        used_fallback = True
        statuses.append(
            MarketUniverseSourceStatus(
                "Built-in fallback universe",
                "fallback",
                fetched_at,
                f"Loaded {len(fallback)} static large-cap rows because provider universe data was unavailable.",
            )
        )

    records = _dedupe_entries(rows)[: max(1, limit)]
    sources = tuple(sorted({entry.source for entry in records if entry.source}))
    return MarketUniverseSnapshot(
        records=tuple(records),
        fetched_at=fetched_at,
        sources=sources,
        statuses=tuple(statuses),
        errors=tuple(errors),
        used_fallback=used_fallback,
    )


def _load_fmp_stock_universe(*, limit: int) -> list[MarketUniverseEntry]:
    api_key = os.getenv(FMP_API_KEY_ENV, "").strip()
    if not api_key:
        raise RuntimeError(f"No {FMP_API_KEY_ENV} is configured.")
    base_url = (os.getenv(FMP_BASE_URL_ENV, DEFAULT_FMP_BASE_URL) or DEFAULT_FMP_BASE_URL).rstrip("/")
    try:
        response = requests.get(
            f"{base_url}/stock-list",
            headers={"apikey": api_key, "User-Agent": "portfolio-risk-cockpit/1.0"},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise RuntimeError(_redact_provider_secret(f"FMP stock-list request failed: {exc}", api_key)) from None

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code in {401, 403}:
        raise RuntimeError(f"FMP stock-list authentication was rejected (HTTP {status_code}); check {FMP_API_KEY_ENV}.")
    if status_code == 429:
        raise RuntimeError("FMP stock-list rate or daily plan limit was reached (HTTP 429).")
    if status_code < 200 or status_code >= 300:
        raise RuntimeError(f"FMP stock-list returned HTTP {status_code}.")
    try:
        payload = response.json()
    except ValueError:
        raise RuntimeError("FMP stock-list returned a non-JSON response.") from None
    if not isinstance(payload, list):
        raise RuntimeError("FMP stock-list returned an unexpected response shape.")

    rows: list[MarketUniverseEntry] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        symbol = _normalize_symbol(item.get("symbol") or item.get("ticker"))
        if not symbol:
            continue
        rows.append(
            MarketUniverseEntry(
                symbol=symbol,
                company_name=_optional_string(item.get("name") or item.get("companyName") or item.get("company_name")),
                exchange=_optional_string(item.get("exchangeShortName") or item.get("exchange")),
                source="FMP stock-list",
                source_url=FMP_STOCK_LIST_DOC_URL,
            )
        )
        if len(rows) >= max(1, limit):
            break
    return rows


def _load_sec_company_tickers(client: SecEdgarClient, *, limit: int) -> list[MarketUniverseEntry]:
    payload = client._fetch_json(  # Uses the same cache/rate-limit path as other SEC features.
        SEC_TICKER_URL,
        cache_name="company_tickers.json",
        ttl=TICKER_CACHE_TTL,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("SEC company_tickers.json returned an unexpected response shape.")

    rows: list[MarketUniverseEntry] = []
    for item in payload.values():
        if not isinstance(item, dict):
            continue
        symbol = _normalize_symbol(item.get("ticker"))
        if not symbol:
            continue
        company_name = _optional_string(item.get("title"))
        cik = _optional_string(str(item.get("cik_str") or "").zfill(10))
        rows.append(
            MarketUniverseEntry(
                symbol=symbol,
                company_name=company_name,
                cik=cik,
                source="SEC company_tickers.json",
                source_url=SEC_TICKER_URL,
            )
        )
        if len(rows) >= max(1, limit):
            break
    return rows


def _load_local_seed(seed_path: str | Path | None) -> list[MarketUniverseEntry]:
    if not seed_path:
        return []
    path = Path(seed_path)
    if not path.exists():
        return []
    try:
        if path.suffix.lower() == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("records", payload) if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                return []
            return [entry for entry in (MarketUniverseEntry.from_dict(row) for row in rows if isinstance(row, dict)) if entry.symbol]
        if path.suffix.lower() in {".csv", ".tsv"}:
            delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
            with path.open("r", encoding="utf-8", newline="") as handle:
                return [
                    entry
                    for entry in (MarketUniverseEntry.from_dict(row) for row in csv.DictReader(handle, delimiter=delimiter))
                    if entry.symbol
                ]
    except (OSError, json.JSONDecodeError, csv.Error):
        return []
    return []


def _dedupe_entries(entries: Iterable[MarketUniverseEntry]) -> list[MarketUniverseEntry]:
    by_symbol: dict[str, MarketUniverseEntry] = {}
    for entry in entries:
        symbol = _normalize_symbol(entry.symbol)
        if not symbol:
            continue
        if symbol not in by_symbol:
            by_symbol[symbol] = entry
            continue
        existing = by_symbol[symbol]
        by_symbol[symbol] = MarketUniverseEntry(
            symbol=symbol,
            company_name=existing.company_name or entry.company_name,
            cik=existing.cik or entry.cik,
            exchange=existing.exchange or entry.exchange,
            sector=existing.sector or entry.sector,
            industry=existing.industry or entry.industry,
            source=existing.source or entry.source,
            source_url=existing.source_url or entry.source_url,
        )
    return sorted(by_symbol.values(), key=lambda row: row.symbol)


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    symbol = _SYMBOL_ALIASES.get(symbol, symbol)
    symbol = symbol.replace("/", ".")
    symbol = _SYMBOL_ALIASES.get(symbol, symbol)
    return symbol if symbol and len(symbol) <= 16 else ""


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _redact_provider_secret(value: str, secret: str | None) -> str:
    text = str(value or "")
    clean_secret = str(secret or "").strip()
    if clean_secret:
        text = text.replace(clean_secret, "[REDACTED]")
    text = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(apikey['\"]?\s*[:=]\s*['\"]?)[^,'\"\s)}]+", r"\1[REDACTED]", text)
    return text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
