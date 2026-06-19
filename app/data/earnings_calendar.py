from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

import requests

from app.data.sec_edgar import DEFAULT_CACHE_DIR, DEFAULT_USER_AGENT


ALPHA_VANTAGE_EARNINGS_CALENDAR_URL = "https://www.alphavantage.co/query"
ALPHA_VANTAGE_DOCS_URL = "https://www.alphavantage.co/documentation/#earnings-calendar"
ALPHA_VANTAGE_HORIZONS = ("3month", "6month", "12month")
ALPHA_VANTAGE_CACHE_TTL = timedelta(hours=12)
MISSING_API_KEY_MESSAGE = "Upcoming calendar not configured. Set ALPHA_VANTAGE_API_KEY to enable."
FMP_API_KEY_ENV = "FMP_API_KEY"
FMP_BASE_URL_ENV = "FMP_BASE_URL"
DEFAULT_FMP_BASE_URL = "https://financialmodelingprep.com/stable"
FMP_EARNINGS_CALENDAR_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/earnings-calendar"
FMP_CACHE_TTL = timedelta(hours=12)
FMP_MISSING_API_KEY_MESSAGE = "FMP earnings calendar not configured. Set FMP_API_KEY to enable fallback earnings dates."


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


class FmpEarningsCalendarClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        cache_dir: str | Path | None = None,
        session: requests.Session | Any | None = None,
        timeout_seconds: int = 30,
        cache_ttl: timedelta = FMP_CACHE_TTL,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv(FMP_API_KEY_ENV, "")).strip()
        self.base_url = (base_url or os.getenv(FMP_BASE_URL_ENV, DEFAULT_FMP_BASE_URL) or DEFAULT_FMP_BASE_URL).rstrip("/")
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
            self.last_status = FMP_MISSING_API_KEY_MESSAGE
            return []

        request_symbol = symbol_filter[0] if len(symbol_filter) == 1 else None
        cache_path = self.cache_dir / _cache_filename_fmp(validated_horizon, request_symbol)
        payload = None if force_refresh else self._read_cache(cache_path)
        used_cache = payload is not None
        if payload is None:
            payload = self._fetch_calendar_json(validated_horizon, request_symbol)
            self._write_cache(cache_path, payload)

        records = parse_fmp_earnings_calendar_rows(payload, source_url=self._source_url(validated_horizon, request_symbol))
        if len(symbol_filter) > 1:
            allowed = set(symbol_filter)
            records = [record for record in records if record.symbol.upper() in allowed]

        source = "cache" if used_cache else "FMP"
        self.last_status = f"Loaded {len(records)} upcoming earnings rows from {source} stable earnings-calendar ({validated_horizon})."
        return records

    def _fetch_calendar_json(self, horizon: str, symbol: str | None) -> Any:
        response = self.session.get(
            f"{self.base_url}/earnings-calendar",
            params=self._params(horizon, symbol),
            headers={"apikey": self.api_key, "User-Agent": "portfolio-risk-cockpit/1.0"},
            timeout=self.timeout_seconds,
        )
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403}:
            raise RuntimeError(f"FMP earnings-calendar authentication was rejected (HTTP {status_code}); check {FMP_API_KEY_ENV}.")
        if status_code == 429:
            raise RuntimeError("FMP earnings-calendar rate or daily plan limit was reached (HTTP 429).")
        if status_code < 200 or status_code >= 300:
            raise RuntimeError(f"FMP earnings-calendar returned HTTP {status_code}.")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError("FMP earnings-calendar returned a non-JSON response.") from None
        warning = _detect_fmp_warning(payload)
        if warning:
            raise RuntimeError(_redact_fmp_secret(f"FMP earnings-calendar warning: {warning}", self.api_key))
        return payload

    def _params(self, horizon: str, symbol: str | None) -> dict[str, str]:
        start, end = _horizon_dates(horizon)
        params = {"from": start.isoformat(), "to": end.isoformat()}
        if symbol:
            params["symbol"] = symbol
        return params

    def _source_url(self, horizon: str, symbol: str | None) -> str:
        return f"{self.base_url}/earnings-calendar?{urllib.parse.urlencode(self._params(horizon, symbol))}"

    def _read_cache(self, path: Path) -> Any | None:
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > self.cache_ttl.total_seconds():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, path: Path, payload: Any) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
        temporary.replace(path)


class CompositeUpcomingEarningsProvider:
    def __init__(self, providers: Iterable[UpcomingEarningsProvider]) -> None:
        self.providers = tuple(providers)
        self.last_status = "Ready."

    def upcoming_earnings(
        self,
        *,
        horizon: str = "3month",
        symbols: Iterable[str] | None = None,
        force_refresh: bool = False,
    ) -> list[UpcomingEarningsRecord]:
        merged: dict[tuple[str, str], UpcomingEarningsRecord] = {}
        statuses: list[str] = []
        warnings: list[str] = []
        for provider in self.providers:
            try:
                try:
                    rows = provider.upcoming_earnings(horizon=horizon, symbols=symbols, force_refresh=force_refresh)  # type: ignore[call-arg]
                except TypeError:
                    rows = provider.upcoming_earnings(horizon=horizon, symbols=symbols)
            except Exception as exc:
                warnings.append(_redact_fmp_secret(str(exc), os.getenv(FMP_API_KEY_ENV, "")))
                continue
            status = str(getattr(provider, "last_status", "") or "").strip()
            if status:
                statuses.append(status)
            for row in rows:
                key = (row.symbol.upper(), row.report_date)
                merged.setdefault(key, row)
        source_status = "; ".join(statuses) if statuses else "No earnings-calendar provider returned rows."
        warning_status = f" Warnings: {'; '.join(warnings[:2])}" if warnings else ""
        self.last_status = f"{source_status}{warning_status}"
        return sorted(merged.values(), key=lambda row: (row.report_date, row.symbol))


def configured_upcoming_earnings_provider() -> CompositeUpcomingEarningsProvider:
    return CompositeUpcomingEarningsProvider((AlphaVantageEarningsCalendarClient(), FmpEarningsCalendarClient()))


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


def parse_fmp_earnings_calendar_rows(
    payload: Any,
    *,
    source_url: str | None = FMP_EARNINGS_CALENDAR_DOC_URL,
) -> list[UpcomingEarningsRecord]:
    rows = payload if isinstance(payload, list) else payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    records: list[UpcomingEarningsRecord] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = _normalize_symbol(row.get("symbol") or row.get("ticker"))
        report_date = str(row.get("date") or row.get("reportDate") or row.get("report_date") or "").strip()
        if not symbol or not report_date:
            continue
        records.append(
            UpcomingEarningsRecord(
                symbol=symbol,
                company_name=_optional_string(row.get("companyName") or row.get("company_name") or row.get("name")),
                report_date=report_date[:10],
                fiscal_date_ending=_optional_string(row.get("fiscalDateEnding") or row.get("fiscal_date_ending") or row.get("period")),
                estimate=_optional_float(row.get("epsEstimated") or row.get("estimatedEps") or row.get("epsEstimate") or row.get("estimate")),
                currency=_optional_string(row.get("currency") or row.get("reportedCurrency")),
                source="FMP earnings calendar",
                source_url=source_url,
            )
        )
    return records


def _cache_filename(horizon: str, symbol: str | None) -> str:
    suffix = f"_{symbol}" if symbol else ""
    return f"upcoming_earnings_alpha_vantage_{horizon}{suffix}.csv"


def _cache_filename_fmp(horizon: str, symbol: str | None) -> str:
    suffix = f"_{symbol}" if symbol else ""
    return f"upcoming_earnings_fmp_{horizon}{suffix}.json"


def _horizon_dates(horizon: str) -> tuple[date, date]:
    months = {"3month": 3, "6month": 6, "12month": 12}[validate_horizon(horizon)]
    start = datetime.now(timezone.utc).date()
    return start, start + timedelta(days=months * 31)


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


def _detect_fmp_warning(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("Error Message", "Note", "Information", "message", "error"):
            value = payload.get(key)
            if value:
                return str(value)
    return None


def _redact_fmp_secret(message: str, api_key: str | None) -> str:
    text = str(message or "")
    clean_key = str(api_key or "").strip()
    if clean_key:
        text = text.replace(clean_key, "[REDACTED]")
    text = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(apikey['\"]?\s*[:=]\s*['\"]?)[^,'\"\s)}]+", r"\1[REDACTED]", text)
    return text
