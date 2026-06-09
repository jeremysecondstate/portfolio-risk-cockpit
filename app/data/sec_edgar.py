from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_CURRENT_FILINGS_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_ARCHIVES_BASE_URL = "https://www.sec.gov/Archives/edgar/data"
DEFAULT_USER_AGENT = "PortfolioRiskCockpit jeremy@secondstate.art"
DEFAULT_CACHE_DIR = Path("data/sec_cache")
DEFAULT_CACHE_DRIVE_DIR = Path("I:/My Drive/PRC/SEC_CACHE")
DEFAULT_CACHE_TTL = timedelta(hours=6)
TICKER_CACHE_TTL = timedelta(days=1)
CURRENT_FILINGS_CACHE_TTL = timedelta(minutes=15)
MIN_SECONDS_BETWEEN_SEC_REQUESTS = 0.12
SEC_REQUEST_RETRIES = 3


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
class SecCurrentFiling:
    company_name: str
    cik: str
    form: str
    filing_date: str
    accession_number: str
    filing_url: str
    assigned_sic: str = ""
    assigned_sic_description: str = ""
    acceptance_datetime: str = ""
    primary_document: str = ""

    @property
    def cik_int(self) -> int:
        return int(self.cik)

    @property
    def accession_no_dashes(self) -> str:
        return self.accession_number.replace("-", "")

    @property
    def filing_directory_url(self) -> str:
        return f"{SEC_ARCHIVES_BASE_URL}/{self.cik_int}/{self.accession_no_dashes}/"

    @property
    def filing_index_url(self) -> str:
        return f"{self.filing_directory_url}index.json"


@dataclass(frozen=True)
class SecEarningsRelease:
    company: SecCompany
    filing: SecFiling
    document: SecFilingDocument
    text: str

    @property
    def source_url(self) -> str:
        return self.document.url


@dataclass(frozen=True)
class SecEarningsReport:
    company: SecCompany
    filing: SecFiling
    document: SecFilingDocument
    text: str

    @property
    def source_url(self) -> str:
        return self.document.url

    @property
    def source_label(self) -> str:
        return f"SEC {self.filing.form} fallback"

    @property
    def analyzed_label(self) -> str:
        return f"SEC {self.filing.form} analyzed"

    @property
    def source_kind(self) -> str:
        return f"sec_{self.filing.form.lower().replace('-', '')}_fallback"


@dataclass(frozen=True)
class SecForeignIssuerRelease:
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
        self.cache_dir = Path(cache_dir or os.getenv("SEC_CACHE_DIR") or DEFAULT_CACHE_DRIVE_DIR)
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

    def get_submissions_by_cik(self, cik: str | int) -> dict[str, Any]:
        normalized_cik = normalize_cik(cik)
        payload = self._fetch_json(
            SEC_SUBMISSIONS_URL.format(cik=normalized_cik),
            cache_name=f"submissions_{normalized_cik}.json",
            ttl=DEFAULT_CACHE_TTL,
        )
        if not isinstance(payload, dict):
            raise RuntimeError("SEC submissions API returned an unexpected response shape.")
        return payload

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

    def recent_current_filings(
        self,
        form: str,
        *,
        limit: int = 100,
        start: int = 0,
    ) -> list[SecCurrentFiling]:
        normalized_form = form.strip().upper()
        params = {
            "action": "getcurrent",
            "type": normalized_form,
            "owner": "exclude",
            "start": str(max(start, 0)),
            "count": str(max(1, min(limit, 100))),
            "output": "atom",
        }
        url = f"{SEC_CURRENT_FILINGS_URL}?{urllib.parse.urlencode(params)}"
        raw = self._fetch_text(
            url,
            cache_name=f"current_filings_{normalized_form}_{start}_{limit}.atom",
            ttl=CURRENT_FILINGS_CACHE_TTL,
        )
        return parse_current_filings_atom(raw, fallback_form=normalized_form)

    def filing_index_items_for_accession(self, cik: str | int, accession_number: str) -> list[dict[str, Any]]:
        normalized_cik = normalize_cik(cik)
        accession_no_dashes = str(accession_number).replace("-", "")
        index_url = f"{SEC_ARCHIVES_BASE_URL}/{int(normalized_cik)}/{accession_no_dashes}/index.json"
        payload = self._fetch_json(
            index_url,
            cache_name=f"filing_index_{normalized_cik}_{accession_no_dashes}.json",
            ttl=DEFAULT_CACHE_TTL,
        )
        directory = payload.get("directory") if isinstance(payload, dict) else {}
        raw_items = directory.get("item") if isinstance(directory, dict) else []
        return raw_items if isinstance(raw_items, list) else []

    def document_text_url(self, url: str, *, cache_name: str | None = None, ttl: timedelta = DEFAULT_CACHE_TTL) -> str:
        raw = self._fetch_text(
            url,
            cache_name=cache_name or f"document_url_{_safe_cache_filename(url)}.txt",
            ttl=ttl,
        )
        return html_to_text(raw)

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

    def latest_formal_earnings_report(self, ticker: str) -> SecEarningsReport | None:
        filings = self.recent_filings(ticker, forms=("10-Q", "10-K"), limit=12)
        for filing in sorted(filings, key=_formal_report_filing_rank):
            documents = self.filing_documents(filing)
            document = choose_primary_filing_document(documents, filing)
            if document is None and filing.primary_document:
                document = SecFilingDocument(
                    filing=filing,
                    document=filing.primary_document,
                    description=filing.description,
                    type=filing.form,
                    sequence="",
                )
            if document is None:
                continue
            text = self.document_text(document)
            if not text.strip():
                continue
            return SecEarningsReport(company=filing.company, filing=filing, document=document, text=text)
        return None

    def latest_foreign_issuer_release(self, ticker: str) -> SecForeignIssuerRelease | None:
        filings = self.recent_filings(ticker, forms=("6-K", "20-F", "20-F/A", "40-F", "40-F/A"), limit=40)
        prioritized = sorted(filings, key=_foreign_results_filing_rank)
        for filing in prioritized:
            documents = self.filing_documents(filing)
            document = choose_foreign_results_document(documents, filing)
            if document is None:
                continue
            text = self.document_text(document)
            if not text.strip():
                continue
            if filing.form == "6-K" and not _text_has_foreign_results_keywords(text):
                continue
            return SecForeignIssuerRelease(company=filing.company, filing=filing, document=document, text=text)
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

        last_error: Exception | None = None
        for attempt in range(SEC_REQUEST_RETRIES):
            try:
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
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = requests.HTTPError(f"SEC request returned HTTP {response.status_code}", response=response)
                    if attempt < SEC_REQUEST_RETRIES - 1:
                        time.sleep(0.6 * (attempt + 1))
                        continue
                response.raise_for_status()
                text = response.text
                break
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= SEC_REQUEST_RETRIES - 1:
                    raise
                time.sleep(0.6 * (attempt + 1))
        else:
            if last_error is not None:
                raise last_error
            raise RuntimeError(f"SEC request failed for {url}")

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


def choose_primary_filing_document(
    documents: list[SecFilingDocument],
    filing: SecFiling | None = None,
) -> SecFilingDocument | None:
    if not documents:
        return None
    if filing is not None and filing.primary_document:
        for document in documents:
            if document.document == filing.primary_document:
                return document

    candidates: list[tuple[int, SecFilingDocument]] = []
    filing_form = (filing.form if filing else "").upper()
    form_slug = filing_form.lower().replace("-", "")
    for document in documents:
        if not _looks_like_text_filing_document(document.document):
            continue
        haystack = f"{document.type} {document.document} {document.description}".lower()
        score = 5
        if filing_form and document.type.upper() == filing_form:
            score = 0
        elif form_slug and form_slug in haystack.replace("-", ""):
            score = 1
        elif "complete submission text" in haystack:
            score = 4
        candidates.append((score, document))
    if not candidates:
        return None
    return sorted(candidates, key=lambda row: (row[0], row[1].document))[0][1]


def choose_foreign_results_document(
    documents: list[SecFilingDocument],
    filing: SecFiling | None = None,
) -> SecFilingDocument | None:
    if not documents:
        return None
    primary_candidates = [document for document in documents if _looks_like_foreign_results_document(document)]
    if primary_candidates:
        return sorted(primary_candidates, key=_foreign_document_rank)[0]
    if filing is not None:
        for document in documents:
            if document.document == filing.primary_document:
                return document
    return documents[0]


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


def parse_current_filings_atom(raw: str, *, fallback_form: str = "") -> list[SecCurrentFiling]:
    if not raw.strip():
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    filings: list[SecCurrentFiling] = []
    for entry in root.iter():
        if _xml_local_name(entry.tag) != "entry":
            continue

        title = _xml_first_text(entry, "title")
        form = (_xml_first_text(entry, "filing-type") or _xml_entry_category_term(entry) or fallback_form).upper()
        cik = _xml_first_text(entry, "cik") or _parse_cik_from_text(title)
        accession = _xml_first_text(entry, "accession-number") or _parse_accession_from_text(title)
        filing_url = _xml_first_text(entry, "filing-href") or _xml_entry_link_href(entry)
        if not accession and filing_url:
            accession = _parse_accession_from_text(filing_url)

        if not form or not cik or not accession:
            continue

        company_name = (
            _xml_first_text(entry, "company-name")
            or _parse_company_from_title(title)
            or "Unknown company"
        )
        filing_date = _xml_first_text(entry, "filing-date") or _xml_first_text(entry, "updated")[:10]
        primary_document = _primary_document_from_filing_href(filing_url)
        filings.append(
            SecCurrentFiling(
                company_name=company_name.strip(),
                cik=normalize_cik(cik),
                form=form,
                filing_date=filing_date,
                accession_number=accession,
                filing_url=filing_url,
                assigned_sic=_xml_first_text(entry, "assigned-sic"),
                assigned_sic_description=_xml_first_text(entry, "assigned-sic-desc"),
                acceptance_datetime=_xml_first_text(entry, "acceptance-datetime"),
                primary_document=primary_document,
            )
        )
    return filings


def normalize_cik(cik: str | int) -> str:
    raw = str(cik).strip().upper().replace("CIK", "")
    digits = re.sub(r"\D", "", raw)
    if not digits:
        raise ValueError(f"{cik!r} does not contain a valid SEC CIK.")
    return digits.zfill(10)


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


def _xml_local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _xml_first_text(node: ET.Element, local_name: str) -> str:
    target = local_name.lower()
    for child in node.iter():
        if _xml_local_name(child.tag) == target and child.text:
            return child.text.strip()
    return ""


def _xml_entry_category_term(entry: ET.Element) -> str:
    for child in entry.iter():
        if _xml_local_name(child.tag) != "category":
            continue
        term = str(child.attrib.get("term") or "").strip()
        if term:
            return term
    return ""


def _xml_entry_link_href(entry: ET.Element) -> str:
    for child in entry.iter():
        if _xml_local_name(child.tag) != "link":
            continue
        href = str(child.attrib.get("href") or "").strip()
        if href:
            return href
    return ""


def _parse_cik_from_text(text: str) -> str:
    match = re.search(r"\b(\d{10})\b", text or "")
    return match.group(1) if match else ""


def _parse_accession_from_text(text: str) -> str:
    match = re.search(r"\b(\d{10}-\d{2}-\d{6})\b", text or "")
    return match.group(1) if match else ""


def _parse_company_from_title(title: str) -> str:
    if " - " in title:
        tail = title.split(" - ", 1)[1]
        return re.sub(r"\s*\(\d{10}\).*$", "", tail).strip()
    return ""


def _primary_document_from_filing_href(filing_url: str) -> str:
    if not filing_url:
        return ""
    name = filing_url.rstrip("/").rsplit("/", 1)[-1]
    return "" if name.endswith("-index.htm") else name


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


def _looks_like_foreign_results_document(document: SecFilingDocument) -> bool:
    haystack = f"{document.type} {document.document} {document.description}".lower()
    return any(
        term in haystack
        for term in (
            "ex-99",
            "exhibit 99",
            "earnings",
            "results",
            "press release",
            "quarter",
            "annual report",
            "financial statements",
            "presentation",
        )
    )


def _foreign_results_filing_rank(filing: SecFiling) -> tuple[int, str]:
    haystack = f"{filing.form} {filing.description} {filing.primary_document}".lower()
    if filing.form == "6-K" and any(term in haystack for term in ("results", "earnings", "quarter", "press release")):
        score = 0
    elif filing.form == "6-K":
        score = 1
    elif filing.form in {"20-F", "20-F/A"}:
        score = 2
    elif filing.form in {"40-F", "40-F/A"}:
        score = 3
    else:
        score = 9
    return score, _reverse_date_sort_key(filing.filing_date)


def _formal_report_filing_rank(filing: SecFiling) -> tuple[str, int]:
    form = filing.form.upper()
    form_score = 0 if form == "10-Q" else 1 if form == "10-K" else 9
    return _reverse_date_sort_key(filing.filing_date), form_score


def _foreign_document_rank(document: SecFilingDocument) -> tuple[int, str]:
    haystack = f"{document.type} {document.document} {document.description}".lower()
    score = 10
    if "ex-99.1" in haystack or "exhibit 99.1" in haystack:
        score = 0
    elif "press release" in haystack or "results" in haystack:
        score = 1
    elif "quarter" in haystack or "earnings" in haystack:
        score = 2
    elif "presentation" in haystack:
        score = 3
    elif "annual report" in haystack or "20-f" in haystack or "40-f" in haystack:
        score = 4
    return score, document.document


def _text_has_foreign_results_keywords(text: str) -> bool:
    lower = text[:12000].lower()
    return any(
        term in lower
        for term in (
            "financial results",
            "quarterly results",
            "net sales",
            "revenue",
            "net income",
            "earnings per share",
            "gross margin",
            "guidance",
            "outlook",
            "orders",
            "bookings",
            "backlog",
        )
    )


def _reverse_date_sort_key(value: str) -> str:
    try:
        date = datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return "9999-99-99"
    return f"{9999 - date.year:04d}-{12 - date.month:02d}-{31 - date.day:02d}"


def _looks_like_text_filing_document(name: str) -> bool:
    lower = name.lower()
    return lower.endswith((".htm", ".html", ".txt")) and not lower.endswith("-index.htm")


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
    cache_dir = Path(os.getenv("SEC_CACHE_DIR") or DEFAULT_CACHE_DRIVE_DIR)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Generated: {generated_at} · Cache: {cache_dir}"
