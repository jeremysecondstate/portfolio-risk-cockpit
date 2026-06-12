from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from app.analytics.ipo_pipeline import sector_for_sic, unique_flags
from app.data.earnings_calendar import UpcomingEarningsRecord
from app.data.sec_edgar import DEFAULT_CACHE_DIR, SEC_ARCHIVES_BASE_URL, SecCurrentFiling, SecEdgarClient, normalize_cik


EARNINGS_FORMS = ("8-K", "6-K", "10-Q", "10-K", "20-F", "40-F")
FORMAL_REPORT_FORMS = {"10-Q", "10-K", "20-F", "40-F"}
PRESS_RELEASE_FORMS = {"8-K", "6-K"}
EARNINGS_DROP_KIND = "Earnings drop"
FORMAL_REPORT_KIND = "Formal report"
NOT_EXTRACTED = "Not extracted"
EMPTY_VALUE = "--"
GUIDANCE_TERMS = ("guidance", "outlook", "expects", "forecast", "raises", "lowers", "reaffirms")
EARNINGS_KEYWORDS = (
    "earnings",
    "financial results",
    "quarterly results",
    "annual results",
    "results of operations",
    "net sales",
    "revenue",
    "revenues",
    "net income",
    "net loss",
    "earnings per share",
    "diluted eps",
)
FOREIGN_RESULTS_KEYWORDS = (
    "financial results",
    "quarterly results",
    "annual results",
    "revenue",
    "net income",
    "earnings per share",
    "gross margin",
    "guidance",
    "outlook",
)


@dataclass(frozen=True)
class ParsedEarningsFields:
    release_title: str | None = None
    report_date: str | None = None
    fiscal_period: str | None = None
    revenue: float | None = None
    revenue_growth: float | None = None
    eps: float | None = None
    net_income: float | None = None
    guidance_flag: bool = False
    risk_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RecentEarningsRecord:
    cik: str
    company_name: str
    ticker: str | None
    form: str
    items: str
    filed_date: str
    acceptance_datetime: str
    report_date: str | None
    fiscal_period: str | None
    sector: str | None
    industry: str | None
    sic: str | None
    exchange: str | None
    release_title: str | None
    revenue: float | None
    revenue_growth: float | None
    eps: float | None
    net_income: float | None
    guidance_flag: bool
    risk_flags: tuple[str, ...]
    filing_url: str
    exhibit_url: str | None
    accession_number: str
    source: str = "SEC EDGAR"
    filing_type: str = EARNINGS_DROP_KIND
    source_excerpt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RecentEarningsRecord":
        values = dict(payload)
        values["risk_flags"] = tuple(values.get("risk_flags") or ())
        values.setdefault("source", "SEC EDGAR")
        values.setdefault("filing_type", EARNINGS_DROP_KIND)
        return cls(**values)


@dataclass(frozen=True)
class EarningsRadarSnapshot:
    recent: tuple[RecentEarningsRecord, ...]
    upcoming: tuple[UpcomingEarningsRecord, ...]
    fetched_at: str
    sources: tuple[str, ...]
    used_cache: bool = False
    errors: tuple[str, ...] = ()


class EarningsRadarStore:
    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.path = self.cache_dir / "earnings_radar_records.json"

    def load(self, *, max_age: timedelta | None = None) -> EarningsRadarSnapshot | None:
        if not self.path.exists():
            return None
        if max_age is not None:
            age_seconds = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
            if age_seconds > max_age.total_seconds():
                return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return EarningsRadarSnapshot(
                recent=tuple(RecentEarningsRecord.from_dict(row) for row in payload.get("recent", [])),
                upcoming=tuple(UpcomingEarningsRecord.from_dict(row) for row in payload.get("upcoming", [])),
                fetched_at=str(payload.get("fetched_at") or ""),
                sources=tuple(payload.get("sources") or ("SEC EDGAR cached earnings radar",)),
                used_cache=True,
                errors=tuple(payload.get("errors") or ()),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def save(self, snapshot: EarningsRadarSnapshot) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": snapshot.fetched_at,
            "sources": list(snapshot.sources),
            "errors": list(snapshot.errors),
            "recent": [record.to_dict() for record in snapshot.recent],
            "upcoming": [record.to_dict() for record in snapshot.upcoming],
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def fetch_recent_earnings_snapshot(
    client: SecEdgarClient | None = None,
    *,
    forms: Iterable[str] = EARNINGS_FORMS,
    per_form_limit: int = 100,
    force_refresh: bool = False,
    parse_documents: bool = True,
    parse_limit: int = 40,
    enrich_limit: int = 160,
    cache_max_age: timedelta = timedelta(minutes=30),
) -> EarningsRadarSnapshot:
    client = client or SecEdgarClient()
    store = EarningsRadarStore(client.cache_dir)
    cached = store.load(max_age=None if force_refresh else cache_max_age)
    if cached is not None and not force_refresh:
        return cached

    errors: list[str] = []
    filings: list[SecCurrentFiling] = []
    for form in forms:
        try:
            filings.extend(client.recent_current_filings(form, limit=per_form_limit))
        except Exception as exc:
            errors.append(f"{form}: {exc}")

    if not filings:
        stale = store.load(max_age=None)
        if stale is not None:
            return EarningsRadarSnapshot(
                recent=stale.recent,
                upcoming=stale.upcoming,
                fetched_at=stale.fetched_at,
                sources=("SEC EDGAR cached earnings radar",),
                used_cache=True,
                errors=tuple(errors),
            )
        if errors:
            raise RuntimeError("Could not fetch SEC earnings filings: " + "; ".join(errors))

    filings = _dedupe_filings(filings)
    grouped = _group_filings_by_cik(filings)
    submissions_by_cik: dict[str, dict[str, Any]] = {}
    for cik in _latest_ciks(grouped)[:enrich_limit]:
        try:
            submissions_by_cik[cik] = client.get_submissions_by_cik(cik)
        except Exception as exc:
            errors.append(f"{cik} submissions: {exc}")

    text_hint_by_accession: dict[str, str] = {}
    exhibit_url_by_accession: dict[str, str] = {}
    for filing in _sorted_filings(filings)[:enrich_limit]:
        if filing.form.strip().upper() not in PRESS_RELEASE_FORMS:
            continue
        try:
            index_items = client.filing_index_items_for_accession(filing.cik, filing.accession_number)
        except Exception as exc:
            errors.append(f"{filing.accession_number} index: {exc}")
            continue
        hint = _index_items_text(index_items)
        if hint:
            text_hint_by_accession[filing.accession_number] = hint
        exhibit_url = _choose_current_earnings_exhibit_url(filing, index_items)
        if exhibit_url:
            exhibit_url_by_accession[filing.accession_number] = exhibit_url

    parsed_by_accession: dict[str, ParsedEarningsFields] = {}
    if parse_documents:
        candidates = _document_parse_candidates(
            filings,
            submissions_by_cik=submissions_by_cik,
            text_hint_by_accession=text_hint_by_accession,
        )[:parse_limit]
        for filing in candidates:
            try:
                url = exhibit_url_by_accession.get(filing.accession_number) or primary_earnings_document_url(client, filing)
                text = client.document_text_url(
                    url,
                    cache_name=f"earnings_document_{filing.cik}_{filing.accession_no_dashes}_{url.rsplit('/', 1)[-1]}.txt",
                )
                parsed_by_accession[filing.accession_number] = parse_earnings_release_text(text)
                text_hint_by_accession[filing.accession_number] = " ".join(
                    [
                        text_hint_by_accession.get(filing.accession_number, ""),
                        text[:12000],
                    ]
                )
            except Exception as exc:
                errors.append(f"{filing.accession_number} parse: {exc}")

    records = build_recent_earnings_records(
        filings,
        submissions_by_cik=submissions_by_cik,
        parsed_by_accession=parsed_by_accession,
        exhibit_url_by_accession=exhibit_url_by_accession,
        text_hint_by_accession=text_hint_by_accession,
    )
    snapshot = EarningsRadarSnapshot(
        recent=tuple(records),
        upcoming=(),
        fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        sources=("SEC EDGAR current filings",),
        used_cache=False,
        errors=tuple(errors),
    )
    store.save(snapshot)
    return snapshot


def build_recent_earnings_records(
    filings: Iterable[SecCurrentFiling],
    *,
    submissions_by_cik: dict[str, dict[str, Any]] | None = None,
    parsed_by_accession: dict[str, ParsedEarningsFields] | None = None,
    exhibit_url_by_accession: dict[str, str] | None = None,
    text_hint_by_accession: dict[str, str] | None = None,
) -> list[RecentEarningsRecord]:
    submissions_by_cik = submissions_by_cik or {}
    parsed_by_accession = parsed_by_accession or {}
    exhibit_url_by_accession = exhibit_url_by_accession or {}
    text_hint_by_accession = text_hint_by_accession or {}
    records: list[RecentEarningsRecord] = []

    for filing in filings:
        cik = normalize_cik(filing.cik)
        submission = submissions_by_cik.get(cik) or {}
        metadata = _submission_metadata_for_accession(submission, filing.accession_number)
        items = str(metadata.get("items") or "")
        text_hint = " ".join(
            [
                filing.company_name,
                filing.primary_document,
                filing.filing_url,
                str(metadata.get("primary_doc_description") or ""),
                items,
                text_hint_by_accession.get(filing.accession_number, ""),
            ]
        )
        filing_type = classify_earnings_filing_kind(filing, items=items, text_hint=text_hint)
        if filing_type is None:
            continue

        parsed = parsed_by_accession.get(filing.accession_number) or ParsedEarningsFields()
        ticker = _first_string(submission.get("tickers"))
        exchange = _first_string(submission.get("exchanges"))
        sic = _clean_text(filing.assigned_sic) or _clean_text(submission.get("sic")) or None
        industry = _clean_text(filing.assigned_sic_description) or _clean_text(submission.get("sicDescription")) or None
        sector = sector_for_sic(sic, industry)
        risk_flags = unique_flags(parsed.risk_flags)
        records.append(
            RecentEarningsRecord(
                cik=cik,
                company_name=filing.company_name,
                ticker=ticker,
                form=filing.form.strip().upper(),
                items=_items_label(items, filing.form, filing_type),
                filed_date=filing.filing_date,
                acceptance_datetime=filing.acceptance_datetime,
                report_date=parsed.report_date or _optional_string(metadata.get("report_date")),
                fiscal_period=parsed.fiscal_period,
                sector=sector,
                industry=industry,
                sic=sic,
                exchange=exchange,
                release_title=parsed.release_title or _optional_string(metadata.get("primary_doc_description")),
                revenue=parsed.revenue,
                revenue_growth=parsed.revenue_growth,
                eps=parsed.eps,
                net_income=parsed.net_income,
                guidance_flag=parsed.guidance_flag,
                risk_flags=tuple(risk_flags),
                filing_url=filing.filing_url,
                exhibit_url=exhibit_url_by_accession.get(filing.accession_number),
                accession_number=filing.accession_number,
                filing_type=filing_type,
                source_excerpt=_source_excerpt(text_hint_by_accession.get(filing.accession_number, "")),
            )
        )

    return sorted(records, key=lambda record: (_date_sort_key(record.filed_date), record.acceptance_datetime, record.company_name), reverse=True)


def is_likely_earnings_current_filing(filing: SecCurrentFiling, *, items: str = "", text_hint: str = "") -> bool:
    return classify_earnings_filing_kind(filing, items=items, text_hint=text_hint) is not None


def classify_earnings_filing_kind(filing: SecCurrentFiling, *, items: str = "", text_hint: str = "") -> str | None:
    form = filing.form.strip().upper()
    haystack = f"{items} {text_hint} {filing.company_name} {filing.primary_document} {filing.filing_url}".lower()
    if form in FORMAL_REPORT_FORMS:
        return FORMAL_REPORT_KIND
    if form == "8-K":
        if "2.02" in items:
            return EARNINGS_DROP_KIND
        if "7.01" in items and _has_earnings_keywords(haystack):
            return EARNINGS_DROP_KIND
        if ("9.01" in items or "ex-99" in haystack or "exhibit 99" in haystack) and _has_earnings_keywords(haystack):
            return EARNINGS_DROP_KIND
    if form == "6-K" and _has_foreign_results_keywords(haystack):
        return EARNINGS_DROP_KIND
    return None


def parse_earnings_release_text(text: str) -> ParsedEarningsFields:
    clean_text = _collapse_text(text)
    if not clean_text:
        return ParsedEarningsFields()

    revenue = _extract_money_after_terms(clean_text, ("revenue", "revenues", "net sales"))
    revenue_growth = _extract_revenue_growth(clean_text)
    eps = _extract_eps(clean_text)
    net_income = _extract_money_after_terms(clean_text, ("net income", "net loss"))
    if net_income is not None and _term_near_value(clean_text, "net loss"):
        net_income = -abs(net_income)

    lower = clean_text.lower()
    guidance_flag = any(term in lower for term in GUIDANCE_TERMS)
    risk_flags = analyze_earnings_risk_flags(
        clean_text,
        revenue_growth=revenue_growth,
        eps=eps,
        net_income=net_income,
    )
    fiscal_period, report_date = _extract_fiscal_period(clean_text)
    return ParsedEarningsFields(
        release_title=_extract_release_title(text),
        report_date=report_date,
        fiscal_period=fiscal_period,
        revenue=revenue,
        revenue_growth=revenue_growth,
        eps=eps,
        net_income=net_income,
        guidance_flag=guidance_flag,
        risk_flags=tuple(risk_flags),
    )


def analyze_earnings_risk_flags(
    text: str,
    *,
    revenue_growth: float | None = None,
    eps: float | None = None,
    net_income: float | None = None,
) -> list[str]:
    lower = text.lower()
    flags: list[str] = []
    if eps is not None and eps < 0:
        flags.append("Negative EPS")
    if net_income is not None and net_income < 0:
        flags.append("Net loss")
    if revenue_growth is not None and revenue_growth < 0:
        flags.append("Revenue decline")
    if any(term in lower for term in ("lowers guidance", "lowered guidance", "cuts guidance", "cut guidance", "reduces guidance", "reduced guidance")):
        flags.append("Guidance cut")
    checks = [
        ("Going concern language", ("going concern", "substantial doubt about our ability to continue")),
        ("Delayed filing", ("delayed filing", "late filing", "unable to file")),
        ("Auditor change", ("auditor resignation", "change in auditor", "changed auditors")),
        ("Restatement / non-reliance", ("restatement", "non-reliance", "should no longer be relied upon")),
    ]
    for flag, terms in checks:
        if any(term in lower for term in terms):
            flags.append(flag)
    return unique_flags(flags)


def primary_earnings_document_url(client: SecEdgarClient, filing: SecCurrentFiling) -> str:
    if filing.primary_document and not filing.primary_document.endswith("-index.htm"):
        return filing.filing_url

    items = client.filing_index_items_for_accession(filing.cik, filing.accession_number)
    candidates: list[tuple[int, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        document_type = str(item.get("type") or "").upper()
        if not _looks_like_text_document(name):
            continue
        score = 5
        if document_type == filing.form.upper():
            score = 0
        elif _form_slug(filing.form) in name.lower():
            score = 1
        elif name.lower().endswith((".htm", ".html")):
            score = 2
        candidates.append((score, name))

    if candidates:
        name = sorted(candidates)[0][1]
        return f"{SEC_ARCHIVES_BASE_URL}/{int(normalize_cik(filing.cik))}/{filing.accession_no_dashes}/{name}"
    if filing.filing_url:
        return filing.filing_url
    raise RuntimeError(f"No primary document URL found for {filing.accession_number}.")


def filter_recent_earnings_records(
    records: Iterable[RecentEarningsRecord],
    *,
    search: str = "",
    form: str = "All",
    item: str = "All",
    sector: str = "All",
    exchange: str = "All",
    risk_flag: str = "All",
    date_from: str = "",
    date_to: str = "",
    has_exhibit: bool = False,
    guidance: bool | None = None,
) -> list[RecentEarningsRecord]:
    search_text = search.strip().lower()
    start_date = _parse_date(date_from)
    end_date = _parse_date(date_to)
    filtered: list[RecentEarningsRecord] = []
    for record in records:
        if search_text and search_text not in _record_search_text(record):
            continue
        if form != "All" and record.form != form:
            continue
        if item != "All" and item.lower() not in record.items.lower():
            continue
        if sector != "All" and (record.sector or EMPTY_VALUE) != sector:
            continue
        if exchange != "All" and (record.exchange or EMPTY_VALUE) != exchange:
            continue
        if risk_flag == "Any risk flag" and not record.risk_flags:
            continue
        if risk_flag not in {"All", "Any risk flag"} and risk_flag not in record.risk_flags:
            continue
        if has_exhibit and not record.exhibit_url:
            continue
        if guidance is not None and record.guidance_flag is not guidance:
            continue
        record_date = _parse_date(record.filed_date)
        if start_date is not None and record_date is not None and record_date < start_date:
            continue
        if end_date is not None and record_date is not None and record_date > end_date:
            continue
        filtered.append(record)
    return filtered


def filter_upcoming_earnings_records(
    records: Iterable[UpcomingEarningsRecord],
    *,
    search: str = "",
    date_from: str = "",
    date_to: str = "",
    has_estimate: bool = False,
    symbols: Iterable[str] | None = None,
) -> list[UpcomingEarningsRecord]:
    search_text = search.strip().lower()
    start_date = _parse_date(date_from)
    end_date = _parse_date(date_to)
    symbol_filter = {str(symbol).strip().upper() for symbol in (symbols or ()) if str(symbol).strip()}
    filtered: list[UpcomingEarningsRecord] = []
    for record in records:
        if symbol_filter and record.symbol.upper() not in symbol_filter:
            continue
        if search_text and search_text not in f"{record.symbol} {record.company_name or ''}".lower():
            continue
        if has_estimate and record.estimate is None:
            continue
        record_date = _parse_date(record.report_date)
        if start_date is not None and record_date is not None and record_date < start_date:
            continue
        if end_date is not None and record_date is not None and record_date > end_date:
            continue
        filtered.append(record)
    return filtered


def display_money(value: float | None) -> str:
    if value is None:
        return NOT_EXTRACTED
    return f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}"


def display_percent(value: float | None) -> str:
    return NOT_EXTRACTED if value is None else f"{value:.1f}%"


def display_optional_text(value: str | None, *, missing: str = EMPTY_VALUE) -> str:
    clean = _clean_text(value)
    return clean or missing


def _document_parse_candidates(
    filings: list[SecCurrentFiling],
    *,
    submissions_by_cik: dict[str, dict[str, Any]],
    text_hint_by_accession: dict[str, str],
) -> list[SecCurrentFiling]:
    def _rank(filing: SecCurrentFiling) -> tuple[int, str]:
        submission = submissions_by_cik.get(normalize_cik(filing.cik)) or {}
        metadata = _submission_metadata_for_accession(submission, filing.accession_number)
        items = str(metadata.get("items") or "")
        hint = text_hint_by_accession.get(filing.accession_number, "")
        kind = classify_earnings_filing_kind(filing, items=items, text_hint=hint)
        if filing.form == "8-K" and "2.02" in items:
            score = 0
        elif kind == EARNINGS_DROP_KIND:
            score = 1
        elif kind == FORMAL_REPORT_KIND:
            score = 2
        else:
            score = 9
        return score, _reverse_date_sort_key(filing.filing_date)

    return sorted([filing for filing in filings if filing.form.strip().upper() in set(EARNINGS_FORMS)], key=_rank)


def _submission_metadata_for_accession(submission: dict[str, Any], accession: str) -> dict[str, str]:
    recent = ((submission.get("filings") or {}).get("recent") or {})
    if not isinstance(recent, dict):
        return {}
    accessions = list(recent.get("accessionNumber") or [])
    try:
        index = accessions.index(accession)
    except ValueError:
        return {}
    return {
        "items": _safe_list_get(list(recent.get("items") or []), index),
        "report_date": _safe_list_get(list(recent.get("reportDate") or []), index),
        "primary_document": _safe_list_get(list(recent.get("primaryDocument") or []), index),
        "primary_doc_description": _safe_list_get(list(recent.get("primaryDocDescription") or []), index),
    }


def _choose_current_earnings_exhibit_url(filing: SecCurrentFiling, index_items: list[dict[str, Any]]) -> str | None:
    candidates: list[tuple[int, str]] = []
    for item in index_items:
        name = str(item.get("name") or "")
        if not _looks_like_text_document(name):
            continue
        haystack = f"{item.get('type') or ''} {name} {item.get('description') or ''}".lower()
        if not _looks_like_earnings_document(haystack):
            continue
        candidates.append((_document_rank(haystack), name))
    if not candidates:
        return None
    name = sorted(candidates)[0][1]
    return f"{SEC_ARCHIVES_BASE_URL}/{int(normalize_cik(filing.cik))}/{filing.accession_no_dashes}/{name}"


def _index_items_text(index_items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in index_items:
        parts.extend([str(item.get("type") or ""), str(item.get("name") or ""), str(item.get("description") or "")])
    return " ".join(parts)


def _looks_like_earnings_document(haystack: str) -> bool:
    return any(term in haystack for term in ("ex-99", "exhibit 99", "earnings", "press release", "results"))


def _document_rank(haystack: str) -> int:
    if "ex-99.1" in haystack or "exhibit 99.1" in haystack:
        return 0
    if "ex-99" in haystack or "exhibit 99" in haystack:
        return 1
    if "earnings" in haystack:
        return 2
    if "press release" in haystack:
        return 3
    if "results" in haystack:
        return 4
    return 10


def _has_earnings_keywords(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in EARNINGS_KEYWORDS)


def _has_foreign_results_keywords(text: str) -> bool:
    lower = text.lower()
    return any(term in lower for term in FOREIGN_RESULTS_KEYWORDS)


def _extract_release_title(text: str) -> str | None:
    for raw_line in (text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip(" -")
        if not line or len(line) < 8 or len(line) > 180:
            continue
        lower = line.lower()
        if any(term in lower for term in ("table of contents", "exhibit", "united states securities")):
            continue
        if any(term in lower for term in ("results", "earnings", "quarter", "fiscal", "reports", "announces")):
            return line
    return None


def _extract_fiscal_period(text: str) -> tuple[str | None, str | None]:
    date_pattern = r"([A-Z][a-z]+ \d{1,2}, \d{4})"
    match = re.search(rf"((?:quarter|year|fiscal year)\s+ended\s+{date_pattern})", text, flags=re.IGNORECASE)
    if match:
        return _title_period(match.group(1)), _normalize_report_date(match.group(2))
    match = re.search(r"\b(first|second|third|fourth)\s+quarter\s+(?:of\s+)?(fiscal\s+)?(\d{4})\b", text, flags=re.IGNORECASE)
    if match:
        fiscal = "fiscal " if match.group(2) else ""
        return f"{match.group(1).title()} quarter {fiscal}{match.group(3)}".replace(" fiscal", " fiscal"), None
    match = re.search(r"\bfiscal\s+(year|quarter)\s+(\d{4})\b", text, flags=re.IGNORECASE)
    if match:
        return f"Fiscal {match.group(1).lower()} {match.group(2)}", None
    return None, None


def _normalize_report_date(value: str) -> str | None:
    try:
        return datetime.strptime(value, "%B %d, %Y").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _title_period(value: str) -> str:
    return value[:1].upper() + value[1:]


def _extract_money_after_terms(text: str, terms: tuple[str, ...]) -> float | None:
    lower = text.lower()
    for term in terms:
        start = lower.find(term)
        if start < 0:
            continue
        value = _first_money_value(text[start : start + 360])
        if value is not None:
            return value
    return None


def _first_money_value(window: str) -> float | None:
    pattern = re.compile(
        r"(?P<paren>\()?\s*(?P<dollar>\$)?\s*(?P<num>-?[0-9][0-9,]*(?:\.[0-9]+)?)\s*(?P<scale>billion|million|thousand|bn|mm|m)?(?!\s*%)\s*(?P<close>\))?",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(window):
        if not match.group("dollar") and not match.group("scale"):
            continue
        value = float(match.group("num").replace(",", ""))
        scale = (match.group("scale") or "").lower()
        if scale in {"billion", "bn"}:
            value *= 1_000_000_000
        elif scale in {"million", "mm", "m"}:
            value *= 1_000_000
        elif scale == "thousand":
            value *= 1_000
        if match.group("paren") and match.group("close"):
            value = -abs(value)
        return value
    return None


def _extract_revenue_growth(text: str) -> float | None:
    lower = text.lower()
    starts = [index for term in ("revenue", "revenues", "net sales") if (index := lower.find(term)) >= 0]
    starts.append(lower.find("revenue growth"))
    for start in [index for index in starts if index >= 0]:
        window = text[start : start + 300]
        match = re.search(r"(-?[0-9]+(?:\.[0-9]+)?)\s*%", window)
        if not match:
            continue
        value = float(match.group(1))
        if re.search(r"\b(decreased|declined|down|lower|fell|drop)\b", window, flags=re.IGNORECASE):
            value = -abs(value)
        return value
    return None


def _extract_eps(text: str) -> float | None:
    lower = text.lower()
    terms = ("diluted eps", "adjusted eps", "eps", "earnings per share", "loss per share")
    for term in terms:
        start = lower.find(term)
        if start < 0:
            continue
        window = text[start : start + 220]
        match = re.search(r"(?P<paren>\()?\s*\$?\s*(?P<num>-?[0-9]+(?:\.[0-9]+)?)\s*(?P<close>\))?", window)
        if not match:
            continue
        value = float(match.group("num"))
        if "loss per share" in term or (match.group("paren") and match.group("close")):
            value = -abs(value)
        return value
    return None


def _term_near_value(text: str, term: str) -> bool:
    return term.lower() in text[:20000].lower()


def _record_search_text(record: RecentEarningsRecord) -> str:
    return " ".join(
        [
            record.company_name,
            record.ticker or "",
            record.cik,
            record.form,
            record.items,
            record.filing_type,
            record.industry or "",
            record.release_title or "",
            " ".join(record.risk_flags),
        ]
    ).lower()


def _items_label(items: str, form: str, filing_type: str) -> str:
    clean = _clean_text(items)
    if clean:
        return clean
    if filing_type == FORMAL_REPORT_KIND:
        return "Formal report"
    if form.strip().upper() == "6-K":
        return "Foreign issuer results"
    return "Earnings-looking exhibit"


def _dedupe_filings(filings: Iterable[SecCurrentFiling]) -> list[SecCurrentFiling]:
    by_accession: dict[str, SecCurrentFiling] = {}
    for filing in filings:
        by_accession[filing.accession_number] = filing
    return list(by_accession.values())


def _group_filings_by_cik(filings: Iterable[SecCurrentFiling]) -> dict[str, list[SecCurrentFiling]]:
    grouped: dict[str, list[SecCurrentFiling]] = {}
    for filing in filings:
        grouped.setdefault(normalize_cik(filing.cik), []).append(filing)
    return grouped


def _latest_ciks(grouped: dict[str, list[SecCurrentFiling]]) -> list[str]:
    return sorted(grouped, key=lambda cik: max(_date_sort_key(filing.filing_date) for filing in grouped[cik]), reverse=True)


def _sorted_filings(filings: Iterable[SecCurrentFiling]) -> list[SecCurrentFiling]:
    return sorted(filings, key=lambda filing: (_date_sort_key(filing.filing_date), filing.acceptance_datetime), reverse=True)


def _date_sort_key(value: str) -> str:
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "0000-00-00"


def _reverse_date_sort_key(value: str) -> str:
    try:
        date = datetime.strptime(value[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return "9999-99-99"
    return f"{9999 - date.year:04d}-{12 - date.month:02d}-{31 - date.day:02d}"


def _parse_date(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _looks_like_text_document(name: str) -> bool:
    lower = name.lower()
    if not lower.endswith((".htm", ".html", ".txt")):
        return False
    return not any(skip in lower for skip in ("index", "filingsummary", ".xml", ".xsd", ".jpg", ".png"))


def _form_slug(form: str) -> str:
    return form.lower().replace("/", "").replace("-", "")


def _collapse_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _source_excerpt(text: str) -> str | None:
    clean = _collapse_text(text)
    if not clean:
        return None
    return clean[:4000]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _optional_string(value: Any) -> str | None:
    clean = _clean_text(value)
    return clean or None


def _first_string(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            clean = _clean_text(item)
            if clean:
                return clean
        return None
    clean = _clean_text(value)
    return clean or None


def _safe_list_get(values: list[Any], index: int) -> str:
    try:
        return str(values[index] or "")
    except IndexError:
        return ""
