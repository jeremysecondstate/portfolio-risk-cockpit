from __future__ import annotations

"""Runtime installer for the organized SEC cache layout."""

import os
import re
import time
import urllib.parse
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import requests

from app.data import sec_edgar
from app.data.sec_cache_helpers import (
    DEFAULT_CACHE_DIR,
    cache_parts,
    cache_status_line,
    company_cache_name,
    configured_cache_dir,
    filing_cache_name,
    path_from_value,
    safe_filename,
    safe_part,
)


def install_sec_cache_layout() -> None:
    """Route SEC cache files into CIK/ticker folders without changing callers."""
    if getattr(sec_edgar.SecEdgarClient, "_organized_cache_installed", False):
        return

    sec_edgar.DEFAULT_CACHE_DIR = DEFAULT_CACHE_DIR
    sec_edgar.SecEdgarClient.__init__ = _init  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient.company_for_ticker = _company_for_ticker  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient.get_submissions = _get_submissions  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient.get_submissions_by_cik = _get_submissions_by_cik  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient.get_companyfacts = _get_companyfacts  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient.recent_current_filings = _recent_current_filings  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient.filing_index_items_for_accession = _filing_index_items_for_accession  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient.filing_documents = _filing_documents  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient.document_text = _document_text  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient._fetch_text = _fetch_text  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient._write_text_cache = _write_text_cache  # type: ignore[method-assign]
    sec_edgar.SecEdgarClient._organized_cache_installed = True  # type: ignore[attr-defined]
    sec_edgar.cache_status_line = cache_status_line


def _init(
    self: sec_edgar.SecEdgarClient,
    *,
    cache_dir: Path | str | None = None,
    user_agent: str | None = None,
    timeout_seconds: int = 30,
) -> None:
    self.cache_dir = path_from_value(cache_dir) or configured_cache_dir()
    self.user_agent = (user_agent or os.getenv("SEC_USER_AGENT") or sec_edgar.DEFAULT_USER_AGENT).strip()
    self.timeout_seconds = timeout_seconds
    self.session = requests.Session()
    self._last_request_at = 0.0


def _company_for_ticker(self: sec_edgar.SecEdgarClient, ticker: str) -> sec_edgar.SecCompany:
    normalized = sec_edgar.normalize_ticker(ticker)
    payload = self._fetch_json(
        sec_edgar.SEC_TICKER_URL,
        cache_name="_global/company_tickers.json",
        ttl=sec_edgar.TICKER_CACHE_TTL,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("SEC company_tickers.json returned an unexpected response shape.")

    for raw_company in payload.values():
        if not isinstance(raw_company, dict):
            continue
        sec_ticker = str(raw_company.get("ticker", "")).upper()
        if sec_ticker != normalized:
            continue
        return sec_edgar.SecCompany(
            ticker=sec_ticker,
            cik=str(raw_company.get("cik_str", "")).zfill(10),
            title=str(raw_company.get("title") or normalized),
        )
    raise LookupError(f"Ticker {normalized} was not found in the SEC ticker/CIK map.")


def _get_submissions(self: sec_edgar.SecEdgarClient, ticker: str) -> tuple[sec_edgar.SecCompany, dict[str, Any]]:
    company = self.company_for_ticker(ticker)
    payload = self._fetch_json(
        sec_edgar.SEC_SUBMISSIONS_URL.format(cik=company.cik),
        cache_name=company_cache_name(company, "submissions.json"),
        ttl=sec_edgar.DEFAULT_CACHE_TTL,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("SEC submissions API returned an unexpected response shape.")
    return company, payload


def _get_submissions_by_cik(self: sec_edgar.SecEdgarClient, cik: str | int) -> dict[str, Any]:
    normalized_cik = sec_edgar.normalize_cik(cik)
    payload = self._fetch_json(
        sec_edgar.SEC_SUBMISSIONS_URL.format(cik=normalized_cik),
        cache_name=f"companies/{normalized_cik}/submissions.json",
        ttl=sec_edgar.DEFAULT_CACHE_TTL,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("SEC submissions API returned an unexpected response shape.")
    return payload


def _get_companyfacts(self: sec_edgar.SecEdgarClient, ticker: str) -> tuple[sec_edgar.SecCompany, dict[str, Any]]:
    company = self.company_for_ticker(ticker)
    payload = self._fetch_json(
        sec_edgar.SEC_COMPANYFACTS_URL.format(cik=company.cik),
        cache_name=company_cache_name(company, "companyfacts.json"),
        ttl=sec_edgar.DEFAULT_CACHE_TTL,
    )
    if not isinstance(payload, dict):
        raise RuntimeError("SEC companyfacts API returned an unexpected response shape.")
    return company, payload


def _recent_current_filings(
    self: sec_edgar.SecEdgarClient,
    form: str,
    *,
    limit: int = 100,
    start: int = 0,
) -> list[sec_edgar.SecCurrentFiling]:
    normalized_form = form.strip().upper()
    start = max(start, 0)
    limit = max(1, min(limit, 100))
    params = {
        "action": "getcurrent",
        "type": normalized_form,
        "owner": "exclude",
        "start": str(start),
        "count": str(limit),
        "output": "atom",
    }
    url = f"{sec_edgar.SEC_CURRENT_FILINGS_URL}?{urllib.parse.urlencode(params)}"
    raw = self._fetch_text(
        url,
        cache_name=f"current_filings/{safe_part(normalized_form)}/start_{start}_limit_{limit}.atom",
        ttl=sec_edgar.CURRENT_FILINGS_CACHE_TTL,
    )
    return sec_edgar.parse_current_filings_atom(raw, fallback_form=normalized_form)


def _filing_index_items_for_accession(
    self: sec_edgar.SecEdgarClient,
    cik: str | int,
    accession_number: str,
) -> list[dict[str, Any]]:
    normalized_cik = sec_edgar.normalize_cik(cik)
    accession = str(accession_number).replace("-", "")
    url = f"{sec_edgar.SEC_ARCHIVES_BASE_URL}/{int(normalized_cik)}/{accession}/index.json"
    payload = self._fetch_json(
        url,
        cache_name=f"companies/{normalized_cik}/filings/{accession}/index.json",
        ttl=sec_edgar.DEFAULT_CACHE_TTL,
    )
    directory = payload.get("directory") if isinstance(payload, dict) else {}
    raw_items = directory.get("item") if isinstance(directory, dict) else []
    return raw_items if isinstance(raw_items, list) else []


def _filing_documents(self: sec_edgar.SecEdgarClient, filing: sec_edgar.SecFiling) -> list[sec_edgar.SecFilingDocument]:
    payload = self._fetch_json(
        filing.filing_index_url,
        cache_name=filing_cache_name(filing, "index.json"),
        ttl=sec_edgar.DEFAULT_CACHE_TTL,
    )
    directory = payload.get("directory") if isinstance(payload, dict) else {}
    raw_items = directory.get("item") if isinstance(directory, dict) else []
    if not isinstance(raw_items, list):
        return []

    documents: list[sec_edgar.SecFilingDocument] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")
        if not name or name.endswith("/"):
            continue
        documents.append(
            sec_edgar.SecFilingDocument(
                filing=filing,
                document=name,
                description=str(raw.get("description") or ""),
                type=str(raw.get("type") or ""),
                sequence=str(raw.get("sequence") or ""),
                size=sec_edgar._to_int(raw.get("size")),
            )
        )
    return documents


def _document_text(self: sec_edgar.SecEdgarClient, document: sec_edgar.SecFilingDocument) -> str:
    raw = self._fetch_text(
        document.url,
        cache_name=filing_cache_name(document.filing, "documents", f"{document.document}.txt"),
        ttl=sec_edgar.DEFAULT_CACHE_TTL,
    )
    return sec_edgar.html_to_text(raw)


def _fetch_text(self: sec_edgar.SecEdgarClient, url: str, *, cache_name: str, ttl: timedelta) -> str:
    self.cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = self.cache_dir.joinpath(*cache_parts(cache_name))
    cached = self._read_text_cache(cache_path, ttl)
    if cached is not None:
        return cached

    for legacy_path in _legacy_paths(self.cache_dir, cache_name):
        if legacy_path == cache_path:
            continue
        cached = self._read_text_cache(legacy_path, ttl)
        if cached is not None:
            return cached

    last_error: Exception | None = None
    for attempt in range(sec_edgar.SEC_REQUEST_RETRIES):
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
                if attempt < sec_edgar.SEC_REQUEST_RETRIES - 1:
                    time.sleep(0.6 * (attempt + 1))
                    continue
            response.raise_for_status()
            text = response.text
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt >= sec_edgar.SEC_REQUEST_RETRIES - 1:
                raise
            time.sleep(0.6 * (attempt + 1))
    else:
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"SEC request failed for {url}")

    self._write_text_cache(cache_path, text)
    return text


def _write_text_cache(self: sec_edgar.SecEdgarClient, cache_path: Path, text: str) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = cache_path.with_name(f".{cache_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary_path.write_text(text, encoding="utf-8")
    temporary_path.replace(cache_path)


def _legacy_paths(cache_dir: Path, cache_name: str) -> list[Path]:
    paths = [cache_dir / safe_filename(cache_name)]
    parts = cache_parts(cache_name)
    if len(parts) >= 4 and parts[0] == "companies":
        cik = parts[1]
        tail = parts[3:] if parts[2] != "filings" else parts[2:]
        if len(tail) == 1 and tail[0] == "submissions.json":
            paths.append(cache_dir / f"submissions_{cik}.json")
        elif len(tail) == 1 and tail[0] == "companyfacts.json":
            paths.append(cache_dir / f"companyfacts_{cik}.json")
        elif len(tail) >= 3 and tail[0] == "filings":
            accession = tail[1]
            if tail[2] == "index.json":
                paths.append(cache_dir / f"filing_index_{cik}_{accession}.json")
            elif len(tail) >= 4 and tail[2] == "documents":
                paths.append(cache_dir / f"document_{cik}_{accession}_{tail[3]}")
            elif len(tail) >= 4 and tail[2] == "capital_structure":
                paths.append(cache_dir / f"capital_structure_{cik}_{accession}_{tail[3]}")

    if len(parts) == 3 and parts[0] == "current_filings":
        match = re.fullmatch(r"start_(\d+)_limit_(\d+)\.atom", parts[2])
        if match:
            paths.append(cache_dir / f"current_filings_{parts[1]}_{match.group(1)}_{match.group(2)}.atom")
    return paths
