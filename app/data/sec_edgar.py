from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_USER_AGENT = "PortfolioRiskCockpit jeremy@secondstate.art"
DEFAULT_CACHE_DIR = Path("data/sec_cache")
DEFAULT_CACHE_TTL = timedelta(hours=6)
TICKER_CACHE_TTL = timedelta(days=1)
MIN_SECONDS_BETWEEN_SEC_REQUESTS = 0.12


@dataclass(frozen=True)
class SecCompany:
    ticker: str
    cik: str
    title: str

    @property
    def cik_int(self) -> int:
        return int(self.cik)


@dataclass(frozen=True)
class SecFiling:
    company: SecCompany
    accession_number: str
    filing_date: str
    report_date: str
    form: str
    primary_document: str
    description: str

    @property
    def accession_no_dashes(self) -> str:
        return self.accession_number.replace("-", "")

    @property
    def filing_url(self) -> str:
        return (
            f"{SEC_ARCHIVES_BASE_URL}/{self.company.cik_int}/"
            f"{self.accession_no_dashes}/{self.primary_document}"
        )

    @property
    def filing_directory_url(self) -> str:
        return f"{SEC_ARCHIVES_BASE_URL}/{self.company.cik_int}/{self.accession_no_dashes}/"


class SecEdgarClient:
    """Small SEC EDGAR/data.sec.gov client with a local JSON cache.

    SEC endpoints are public and keyless, but the SEC asks automated clients to
    identify themselves and keep request rates reasonable. This client is tuned
    for one-click desktop use and caches responses under data/sec_cache by
    default so repeated clicks do not hammer SEC servers.
    """

    def __init__(
        self,
        *,
        cache_dir: Path | str | None = None,
        user_agent: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.cache_dir = Path(cache_dir or os.getenv("SEC_CACHE_DIR") or DEFAULT_CACHE_DIR)
        self.user_agent = (user_agent or os.getenv("SEC_USER_AGENT") or DEFAULT_USER_AGENT).strip()
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self._last_request_at = 0.0

    def company_for_ticker(self, ticker: str) -> SecCompany:
        normalized = normalize_ticker(ticker)
        ticker_payload = self._fetch_json(
            SEC_TICKER_URL,
            cache_name="company_tickers.json",
            ttl=TICKER_CACHE_TTL,
        )
        if not isinstance(ticker_payload, dict):
            raise RuntimeError("SEC company_tickers.json returned an unexpected response shape.")

        for raw_company in ticker_payload.values():
            if not isinstance(raw_company, dict):
                continue
            sec_ticker = str(raw_company.get("ticker", "")).upper()
            if sec_ticker != normalized:
                continue
            cik_str = str(raw_company.get("cik_str", "")).zfill(10)
            title = str(raw_company.get("title") or normalized)
            return SecCompany(ticker=sec_ticker, cik=cik_str, title=title)

        raise LookupError(f"Ticker {normalized} was not found in the SEC ticker/CIK map.")

    def get_submissions(self, ticker: str) -> tuple[SecCompany, dict[str, Any]]:
        company = self.company_for_ticker(ticker)
        payload = self._fetch_json(
            SEC_SUBMISSIONS_URL.format(cik=company.cik),
            cache_name=f"submissions_{company.cik}.json",
            ttl=DEFAULT_CACHE_TTL,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("SEC submissions API returned an unexpected response shape.")
        return company, payload

    def get_companyfacts(self, ticker: str) -> tuple[SecCompany, dict[str, Any]]:
        company = self.company_for_ticker(ticker)
        payload = self._fetch_json(
            SEC_COMPANYFACTS_URL.format(cik=company.cik),
            cache_name=f"companyfacts_{company.cik}.json",
            ttl=DEFAULT_CACHE_TTL,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("SEC companyfacts API returned an unexpected response shape.")
        return company, payload

    def recent_filings(
        self,
        ticker: str,
        *,
        forms: Iterable[str] | None = None,
        limit: int = 16,
    ) -> list[SecFiling]:
        company, submissions = self.get_submissions(ticker)
        recent = ((submissions.get("filings") or {}).get("recent") or {})
        if not isinstance(recent, dict):
            raise RuntimeError("SEC submissions payload did not include filings.recent.")

        form_filter = {form.upper() for form in forms} if forms else None
        forms_list = list(recent.get("form") or [])
        accessions = list(recent.get("accessionNumber") or [])
        filing_dates = list(recent.get("filingDate") or [])
        report_dates = list(recent.get("reportDate") or [])
        primary_docs = list(recent.get("primaryDocument") or [])
        descriptions = list(recent.get("primaryDocDescription") or [])

        filings: list[SecFiling] = []
        for index, form in enumerate(forms_list):
            form_text = str(form).upper()
            if form_filter and form_text not in form_filter:
                continue
            accession = _safe_list_get(accessions, index)
            primary_document = _safe_list_get(primary_docs, index)
            if not accession or not primary_document:
                continue
            filings.append(
                SecFiling(
                    company=company,
                    accession_number=accession,
                    filing_date=_safe_list_get(filing_dates, index),
                    report_date=_safe_list_get(report_dates, index),
                    form=form_text,
                    primary_document=primary_document,
                    description=_safe_list_get(descriptions, index),
                )
            )
            if len(filings) >= limit:
                break
        return filings

    def _fetch_json(self, url: str, *, cache_name: str, ttl: timedelta) -> Any:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / _safe_cache_filename(cache_name)
        cached = self._read_cache(cache_path, ttl)
        if cached is not None:
            return cached

        self._respect_rate_limit()
        response = self.session.get(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        self._write_cache(cache_path, payload)
        return payload

    def _read_cache(self, cache_path: Path, ttl: timedelta) -> Any | None:
        if not cache_path.exists():
            return None
        age_seconds = time.time() - cache_path.stat().st_mtime
        if age_seconds > ttl.total_seconds():
            return None
        try:
            with cache_path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, cache_path: Path, payload: Any) -> None:
        temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        temporary_path.replace(cache_path)

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_SECONDS_BETWEEN_SEC_REQUESTS:
            time.sleep(MIN_SECONDS_BETWEEN_SEC_REQUESTS - elapsed)
        self._last_request_at = time.monotonic()


def normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if normalized.startswith("HL:"):
        raise ValueError("SEC filings are only available for public-company stock tickers, not Hyperliquid rows.")
    normalized = normalized.replace("/", ".")
    if not normalized:
        raise ValueError("Enter a stock ticker first.")
    if not re.fullmatch(r"[A-Z0-9.\-]{1,12}", normalized):
        raise ValueError(f"{ticker!r} does not look like a stock ticker SEC can resolve.")
    return normalized


def _safe_cache_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _safe_list_get(values: list[Any], index: int) -> str:
    try:
        return str(values[index] or "")
    except IndexError:
        return ""


def cache_status_line() -> str:
    cache_dir = Path(os.getenv("SEC_CACHE_DIR") or DEFAULT_CACHE_DIR)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Generated: {generated_at} · Cache: {cache_dir}"
