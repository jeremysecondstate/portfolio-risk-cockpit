from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from app.data import sec_edgar

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_DIR = PROJECT_ROOT / "app" / "data" / "sec_cache"
DEFAULT_CACHE_DRIVE_DIR = Path("I:/My Drive/PRC/SEC_CACHE")



def configured_cache_dir() -> Path:
    return path_from_value(os.getenv("SEC_CACHE_DIR")) or DEFAULT_CACHE_DRIVE_DIR


def path_from_value(value: Path | str | None) -> Path | None:
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    return Path(text) if text else None


def cache_status_line() -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"Generated: {generated_at} · Cache: {configured_cache_dir()}"


def company_cache_name(company: sec_edgar.SecCompany, *parts: str) -> str:
    return "/".join(("companies", company.cik, safe_part(company.ticker), *parts))


def filing_cache_name(filing: sec_edgar.SecFiling, *parts: str) -> str:
    return "/".join((company_cache_name(filing.company), "filings", filing.accession_no_dashes, *parts))


def cache_parts(cache_name: str) -> tuple[str, ...]:
    normalized = str(cache_name).replace("\\", "/").strip("/")
    if "/" in normalized:
        return tuple(safe_part(part) for part in normalized.split("/") if part and part not in {".", ".."})

    legacy_name = safe_filename(normalized)
    legacy_parts = _legacy_parts(legacy_name)
    return legacy_parts or ("_misc", legacy_name)


def _legacy_parts(name: str) -> tuple[str, ...] | None:
    if name == "company_tickers.json":
        return ("_global", "company_tickers.json")

    patterns = [
        (r"submissions_(\d{10})\.json", lambda m: ("companies", m.group(1), "submissions.json")),
        (r"companyfacts_(\d{10})\.json", lambda m: ("companies", m.group(1), "companyfacts.json")),
        (r"filing_index_(\d{10})_(\d+)\.json", lambda m: ("companies", m.group(1), "filings", m.group(2), "index.json")),
        (r"document_(\d{10})_(\d+)_(.+)\.txt", lambda m: ("companies", m.group(1), "filings", m.group(2), "documents", f"{safe_filename(m.group(3))}.txt")),
        (r"capital_structure_(\d{10})_(\d+)_(.+)\.txt", lambda m: ("companies", m.group(1), "filings", m.group(2), "capital_structure", f"{safe_filename(m.group(3))}.txt")),
        (r"current_filings_([A-Za-z0-9_.-]+)_(\d+)_(\d+)\.atom", lambda m: ("current_filings", safe_part(m.group(1)), f"start_{m.group(2)}_limit_{m.group(3)}.atom")),
        (r"document_url_(.+)\.txt", lambda m: ("_global", "documents", f"{safe_filename(m.group(1))}.txt")),
    ]
    for pattern, builder in patterns:
        match = re.fullmatch(pattern, name)
        if match:
            return builder(match)
    return None


def safe_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value).strip())
    return safe.strip("._") or "_"


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value))
