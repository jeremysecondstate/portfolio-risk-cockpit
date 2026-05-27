from __future__ import annotations

import html
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
    items: str = ""

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

    @property
    def filing_index_url(self) -> str:
        return f"{self.filing_directory_url}index.json"

    @property
    def likely_earnings_release_8k(self) -> bool:
        return self.form == "8-K" and ("2.02" in self.items or "9.01" in self.items)


@dataclass(frozen=True)
class SecFilingDocument:
    filing: SecFiling
    document: str
    description: str
    type: str
    sequence: str
    size: int | None = None

    @property
    def url(self) -> str:
        return f"{self.filing.filing_directory_url}{self.document}"

    @property
    def looks_like_earnings_exhibit(self) -> bool:
        haystack = f"{self.type} {self.document} {self.description}".lower()
        return (
            "ex-99" in haystack
            or "exhibit 99" in haystack
            or "earnings" in haystack
            or "press release" in haystack
            or "results" in haystack
        )


@dataclass(frozen=True)
class SecEarningsRelease:
    company: SecCompany
    filing: SecFiling
    document: SecFilingDocument
    text: str

    @property
    def source_url(self) -> str:
        return self.document.url


class SecEdgarClient:
    """Small SEC EDGAR/data.sec.gov client with a local JSON/text cache.

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
        items = list(recent.get("items") or [])

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
                    items=_safe_list_get(items, index),
                )
            )
            if len(filings) >= limit:
                break
        return filings

    def filing_documents(self, filing: SecFiling) -> list[SecFilingDocument]:
        payload = self._fetch_json(
            filing.filing_index_url,
            cache_name=f"filing_index_{filing.company.cik}_{filing.accession_no_dashes}.json",
            ttl=DEFAULT_CACHE_TTL,
        )
        directory = payload.get("directory") if isinstance(payload, dict) else {}
        raw_items = directory.get("item") if isinstance(directory, dict) else []
        if not isinstance(raw_items, list):
            return []

        documents: list[SecFilingDocument] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("name") or "")
            if not name or name.endswith("/"):
                continue
            documents.append(
                SecFilingDocument(
                    filing=filing,
                    document=name,
                    description=str(raw.get("description") or ""),
                    type=str(raw.get("type") or ""),
                    sequence=str(raw.get("sequence") or ""),
                    size=_to_int(raw.get("size")),
                )
            )
        return documents

    def document_text(self, document: SecFilingDocument) -> str:
        raw = self._fetch_text(
            document.url,
            cache_name=f"document_{document.filing.company.cik}_{document.filing.accession_no_dashes}_{document.document}.txt",
            ttl=DEFAULT_CACHE_TTL,
        )
        return html_to_text(raw)

    def latest_earnings_release(self, ticker: str) -> SecEarningsRelease | None:
        filings = self.recent_filings(ticker, forms=("8-K",), limit=30)
        prioritized = sorted(filings, key=lambda filing: filing.likely_earnings_release_8k, reverse=True)
        for filing in prioritized:
            if filing.items and not filing.likely_earnings_release_8k:
                continue
            documents = self.filing_documents(filing)
            document = choose_earnings_exhibit(documents)
            if document is None:
                continue
            text = self.document_text(document)
            if not text.strip():
                continue
            return SecEarningsRelease(company=filing.company, filing=filing, document=document, text=text)
        return None

    def _fetch_json(self, url: str, *, cache_name: str, ttl: timedelta) -> Any:
        text = self._fetch_text(url, cache_name=cache_name, ttl=ttl)
        return json.loads(text)

    def _fetch_text(self, url: str, *, cache_name: str, ttl: timedelta) -> str:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / _safe_cache_filename(cache_name)
        cached = self._read_text_cache(cache_path, ttl)
        if cached is not None:
            return cached

        self._respect_rate_limit()
        response = self.session.get(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json,text/html,text/plain,*/*",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        text = response.text
        self._write_text_cache(cache_path, text)
        return text

    def _read_text_cache(self, cache_path: Path, ttl: timedelta) -> str | None:
        if not cache_path.exists():
            return None
        age_seconds = time.time() - cache_path.stat().st_mtime
        if age_seconds > ttl.total_seconds():
            return None
        try:
            return cache_path.read_text(encoding="utf-8")
        except OSError:
            return None

    def _write_text_cache(self, cache_path: Path, text: str) -> None:
        temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        temporary_path.write_text(text, encoding="utf-8")
        temporary_path.replace(cache_path)

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < MIN_SECONDS_BETWEEN_SEC_REQUESTS:
            time.sleep(MIN_SECONDS_BETWEEN_SEC_REQUESTS - elapsed)
        self._last_request_at = time.monotonic()


def choose_earnings_exhibit(documents: list[SecFilingDocument]) -> SecFilingDocument | None:
    if not documents:
        return None
    primary_candidates = [document for document in documents if document.looks_like_earnings_exhibit]
    if primary_candidates:
        return sorted(primary_candidates, key=_document_rank)[0]
    return None


def html_to_text(raw: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?</style>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>|</div>|</tr>|</h[1-6]>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[\t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


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


def _document_rank(document: SecFilingDocument) -> tuple[int, str]:
    haystack = f"{document.type} {document.document} {document.description}".lower()
    score = 10
    if "ex-99.1" in haystack or "exhibit 99.1" in haystack:
        score = 0
    elif "ex-99" in haystack or "exhibit 99" in haystack:
        score = 1
    elif "earnings" in haystack:
        score = 2
    elif "press release" in haystack:
        score = 3
    elif "results" in haystack:
        score = 4
    return score, document.document


def _safe_cache_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def _safe_list_get(values: list[Any], index: int) -> str:
    try:
        return str(values[index] or "")
    except IndexError:
        return ""


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def cache_status_line() -> str:
    cache_dir = Path(os.getenv("SEC_CACHE_DIR") or DEFAULT_CACHE_DIR)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Generated: {generated_at} · Cache: {cache_dir}"
