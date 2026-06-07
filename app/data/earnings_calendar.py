from __future__ import annotations

import csv
import io
import os
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Protocol

import requests

from app.data.sec_edgar import DEFAULT_CACHE_DIR, DEFAULT_USER_AGENT


ALPHA_VANTAGE_EARNINGS_CALENDAR_URL = "https://www.alphavantage.co/query"
ALPHA_VANTAGE_DOCS_URL = "https://www.alphavantage.co/documentation/#earnings-calendar"
ALPHA_VANTAGE_HORIZONS = ("3month", "6month", "12month")
ALPHA_VANTAGE_CACHE_TTL = timedelta(hours=12)
MISSING_API_KEY_MESSAGE = "Upcoming calendar not configured. Set ALPHA_VANTAGE_API_KEY to enable."


@dataclass(frozen=True)
class UpcomingEarningsRecord:
    symbol: str
    company_name: str | None
    report_date: str
    fiscal_date_ending: str | None
    estimate: float | None
    currency: str | None
    source: str
    source_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "UpcomingEarningsRecord":
        return cls(
            symbol=str(payload.get("symbol") or "").upper(),
            company_name=_optional_string(payload.get("company_name")),
            report_date=str(payload.get("report_date") or ""),
            fiscal_date_ending=_optional_string(payload.get("fiscal_date_ending")),
            estimate=_optional_float(payload.get("estimate")),
            currency=_optional_string(payload.get("currency")),
            source=str(payload.get("source") or "Alpha Vantage"),
            source_url=_optional_string(payload.get("source_url")),
        )


class UpcomingEarningsProvider(Protocol):
    def upcoming_earnings(
        self,
        *,
        horizon: str = "3month",
        symbols: Iterable[str] | None = None,
    ) -> list[UpcomingEarningsRecord]:
        ...


class AlphaVantageEarningsCalendarClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        cache_dir: str | Path | None = None,
        session: requests.Session | Any | None = None,
        timeout_seconds: int = 30,
        cache_ttl: timedelta = ALPHA_VANTAGE_CACHE_TTL,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("ALPHA_VANTAGE_API_KEY", "")).strip()
        self.cache_dir = Path(cache_dir or os.getenv("SEC_CACHE_DIR") or DEFAULT_CACHE_DIR)
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.cache_ttl = cache_ttl
        self.last_status = "Ready."

    def upcoming_earnings(
        self,
        *,
        horizon: str = "3month",
        symbols: Iterable[str] | None = None,
        force_refresh: bool = False,
    ) -> list[UpcomingEarningsRecord]:
        validated_horizon = validate_horizon(horizon)
        symbol_filter = tuple(_normalize_symbol(symbol) for symbol in (symbols or ()) if _normalize_symbol(symbol))
        if not self.api_key:
            self.last_status = MISSING_API_KEY_MESSAGE
            return []

        request_symbol = symbol_filter[0] if len(symbol_filter) == 1 else None
        cache_path = self.cache_dir / _cache_filename(validated_horizon, request_symbol)
        text = None if force_refresh else self._read_cache(cache_path)
        used_cache = text is not None
        if text is None:
            text = self._fetch_calendar_csv(validated_horizon, request_symbol)
            self._write_cache(cache_path, text)

        records = parse_alpha_vantage_earnings_calendar_csv(text, source_url=self._source_url(validated_horizon, request_symbol))
        if len(symbol_filter) > 1:
            allowed = set(symbol_filter)
            records = [record for record in records if record.symbol.upper() in allowed]

        source = "cache" if used_cache else "Alpha Vantage"
        self.last_status = f"Loaded {len(records)} upcoming earnings rows from {source} ({validated_horizon})."
        return records

    def _fetch_calendar_csv(self, horizon: str, symbol: str | None) -> str:
        url = self._source_url(horizon, symbol, include_api_key=True)
        response = self.session.get(
            url,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/csv,text/plain,*/*"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        text = str(response.text or "")
        if text.lstrip().startswith("{"):
            raise RuntimeError("Alpha Vantage returned a JSON error instead of an earnings-calendar CSV.")
        return text

    def _source_url(self, horizon: str, symbol: str | None, *, include_api_key: bool = False) -> str:
        params = {
            "function": "EARNINGS_CALENDAR",
            "horizon": horizon,
        }
        if symbol:
            params["symbol"] = symbol
        if include_api_key:
            params["apikey"] = self.api_key
        return f"{ALPHA_VANTAGE_EARNINGS_CALENDAR_URL}?{urllib.parse.urlencode(params)}"

    def _read_cache(self, path: Path) -> str | None:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl.total_seconds():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _write_cache(self, path: Path, text: str) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)


def validate_horizon(horizon: str) -> str:
    normalized = str(horizon or "").strip().lower()
    if normalized not in ALPHA_VANTAGE_HORIZONS:
        allowed = ", ".join(ALPHA_VANTAGE_HORIZONS)
        raise ValueError(f"Unsupported earnings-calendar horizon {horizon!r}. Use one of: {allowed}.")
    return normalized


def parse_alpha_vantage_earnings_calendar_csv(
    text: str,
    *,
    source_url: str | None = ALPHA_VANTAGE_DOCS_URL,
) -> list[UpcomingEarningsRecord]:
    if not (text or "").strip():
        return []

    reader = csv.DictReader(io.StringIO(text))
    records: list[UpcomingEarningsRecord] = []
    for row in reader:
        symbol = _normalize_symbol(row.get("symbol"))
        report_date = str(row.get("reportDate") or row.get("report_date") or "").strip()
        if not symbol or not report_date:
            continue
        records.append(
            UpcomingEarningsRecord(
                symbol=symbol,
                company_name=_optional_string(row.get("name") or row.get("companyName") or row.get("company_name")),
                report_date=report_date,
                fiscal_date_ending=_optional_string(row.get("fiscalDateEnding") or row.get("fiscal_date_ending")),
                estimate=_optional_float(row.get("estimate")),
                currency=_optional_string(row.get("currency")),
                source="Alpha Vantage",
                source_url=source_url,
            )
        )
    return records


def _cache_filename(horizon: str, symbol: str | None) -> str:
    suffix = f"_{symbol}" if symbol else ""
    return f"upcoming_earnings_alpha_vantage_{horizon}{suffix}.csv"


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    return symbol if re.fullmatch(r"[A-Z0-9.\-]{1,16}", symbol) else ""


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
