from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from app.data.sec_edgar import DEFAULT_CACHE_DIR, SEC_ARCHIVES_BASE_URL, SecCurrentFiling, SecEdgarClient, normalize_cik


IPO_FORMS = ("S-1", "S-1/A", "F-1", "F-1/A", "424B4", "EFFECT")
IPO_STATUSES = (
    "Filed",
    "Amended",
    "Effective",
    "Priced / Final Prospectus",
    "Trading Candidate",
)
REGISTRATION_FORMS = {"S-1", "F-1"}
AMENDMENT_FORMS = {"S-1/A", "F-1/A"}
FINAL_PROSPECTUS_FORMS = {"424B4"}
EFFECT_FORMS = {"EFFECT"}
NOT_EXTRACTED = "Not extracted yet"
NOT_DISCLOSED = "Not yet disclosed"
EMPTY_VALUE = "--"


@dataclass(frozen=True)
class ParsedIpoFields:
    proposed_ticker: str | None = None
    exchange: str | None = None
    offering_amount: float | None = None
    price_range_low: float | None = None
    price_range_high: float | None = None
    shares_offered: float | None = None
    implied_market_cap: float | None = None
    revenue: float | None = None
    revenue_growth: float | None = None
    net_income: float | None = None
    gross_margin: float | None = None
    cash: float | None = None
    debt: float | None = None
    use_of_proceeds: str | None = None
    underwriters: tuple[str, ...] = ()
    auditor: str | None = None
    risk_flags: tuple[str, ...] = ()
    is_foreign_issuer: bool | None = None


@dataclass(frozen=True)
class IpoPipelineRecord:
    cik: str
    company_name: str
    proposed_ticker: str | None
    form: str
    filed_date: str
    ipo_status: str
    sic: str | None
    sector: str | None
    industry: str | None
    exchange: str | None
    offering_amount: float | None = None
    price_range_low: float | None = None
    price_range_high: float | None = None
    shares_offered: float | None = None
    implied_market_cap: float | None = None
    revenue: float | None = None
    revenue_growth: float | None = None
    net_income: float | None = None
    gross_margin: float | None = None
    cash: float | None = None
    debt: float | None = None
    use_of_proceeds: str | None = None
    underwriters: tuple[str, ...] = ()
    auditor: str | None = None
    risk_flags: tuple[str, ...] = ()
    filing_url: str = ""
    accession_number: str = ""
    latest_filing_date: str | None = None
    amendment_count: int = 0
    is_foreign_issuer: bool = False
    has_final_prospectus: bool = False
    has_effect: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "IpoPipelineRecord":
        values = dict(payload)
        values["underwriters"] = tuple(values.get("underwriters") or ())
        values["risk_flags"] = tuple(values.get("risk_flags") or ())
        return cls(**values)


@dataclass(frozen=True)
class IpoPipelineSnapshot:
    records: tuple[IpoPipelineRecord, ...]
    fetched_at: str
    source: str
    used_cache: bool = False
    errors: tuple[str, ...] = ()


class IpoPipelineStore:
    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.path = self.cache_dir / "ipo_pipeline_records.json"

    def load(self, *, max_age: timedelta | None = None) -> IpoPipelineSnapshot | None:
        if not self.path.exists():
            return None
        if max_age is not None:
            age_seconds = datetime.now(timezone.utc).timestamp() - self.path.stat().st_mtime
            if age_seconds > max_age.total_seconds():
                return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            records = tuple(IpoPipelineRecord.from_dict(row) for row in payload.get("records", []))
            return IpoPipelineSnapshot(
                records=records,
                fetched_at=str(payload.get("fetched_at") or ""),
                source=str(payload.get("source") or "SEC EDGAR cached IPO pipeline"),
                used_cache=True,
                errors=tuple(payload.get("errors") or ()),
            )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def save(self, snapshot: IpoPipelineSnapshot) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "fetched_at": snapshot.fetched_at,
            "source": snapshot.source,
            "errors": list(snapshot.errors),
            "records": [record.to_dict() for record in snapshot.records],
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)


def is_ipo_form(form: str) -> bool:
    return form.strip().upper() in set(IPO_FORMS)


def status_for_form(form: str) -> str:
    normalized = form.strip().upper()
    if normalized in AMENDMENT_FORMS:
        return "Amended"
    if normalized in EFFECT_FORMS:
        return "Effective"
    if normalized in FINAL_PROSPECTUS_FORMS:
        return "Priced / Final Prospectus"
    return "Filed"


def determine_ipo_status(forms: Iterable[str], *, proposed_ticker: str | None = None, exchange: str | None = None) -> str:
    normalized_forms = {form.strip().upper() for form in forms}
    has_final_or_effect = bool(normalized_forms & (FINAL_PROSPECTUS_FORMS | EFFECT_FORMS))
    if has_final_or_effect and _clean_text(proposed_ticker) and _clean_text(exchange):
        return "Trading Candidate"
    if normalized_forms & FINAL_PROSPECTUS_FORMS:
        return "Priced / Final Prospectus"
    if normalized_forms & EFFECT_FORMS:
        return "Effective"
    if normalized_forms & AMENDMENT_FORMS:
        return "Amended"
    return "Filed"


def build_ipo_pipeline_records(
    filings: Iterable[SecCurrentFiling],
    *,
    submissions_by_cik: dict[str, dict[str, Any]] | None = None,
    parsed_by_accession: dict[str, ParsedIpoFields] | None = None,
) -> list[IpoPipelineRecord]:
    submissions_by_cik = submissions_by_cik or {}
    parsed_by_accession = parsed_by_accession or {}
    grouped: dict[str, list[SecCurrentFiling]] = {}
    for filing in filings:
        if not is_ipo_form(filing.form):
            continue
        grouped.setdefault(normalize_cik(filing.cik), []).append(filing)

    records: list[IpoPipelineRecord] = []
    for cik, group in grouped.items():
        sorted_group = sorted(group, key=lambda item: (_date_sort_key(item.filing_date), item.acceptance_datetime), reverse=True)
        latest = sorted_group[0]
        forms = [filing.form for filing in sorted_group]
        submission = submissions_by_cik.get(cik) or {}
        parsed = _merge_parsed_fields([parsed_by_accession.get(filing.accession_number) for filing in sorted_group])

        ticker = parsed.proposed_ticker or _first_string(submission.get("tickers"))
        exchange = parsed.exchange or _first_string(submission.get("exchanges"))
        sic = _clean_text(latest.assigned_sic) or _clean_text(submission.get("sic"))
        industry = (
            _clean_text(latest.assigned_sic_description)
            or _clean_text(submission.get("sicDescription"))
            or None
        )
        sector = sector_for_sic(sic, industry)
        amendment_count = sum(1 for form in forms if form in AMENDMENT_FORMS)
        has_final = any(form in FINAL_PROSPECTUS_FORMS for form in forms)
        has_effect = any(form in EFFECT_FORMS for form in forms)
        is_foreign = bool(parsed.is_foreign_issuer) or any(form.startswith("F-1") for form in forms)
        status = determine_ipo_status(forms, proposed_ticker=ticker, exchange=exchange)

        record_without_flags = IpoPipelineRecord(
            cik=cik,
            company_name=latest.company_name,
            proposed_ticker=ticker,
            form=latest.form,
            filed_date=latest.filing_date,
            ipo_status=status,
            sic=sic,
            sector=sector,
            industry=industry,
            exchange=exchange,
            offering_amount=parsed.offering_amount,
            price_range_low=parsed.price_range_low,
            price_range_high=parsed.price_range_high,
            shares_offered=parsed.shares_offered,
            implied_market_cap=parsed.implied_market_cap,
            revenue=parsed.revenue,
            revenue_growth=parsed.revenue_growth,
            net_income=parsed.net_income,
            gross_margin=parsed.gross_margin,
            cash=parsed.cash,
            debt=parsed.debt,
            use_of_proceeds=parsed.use_of_proceeds,
            underwriters=parsed.underwriters,
            auditor=parsed.auditor,
            filing_url=latest.filing_url,
            accession_number=latest.accession_number,
            latest_filing_date=latest.filing_date,
            amendment_count=amendment_count,
            is_foreign_issuer=is_foreign,
            has_final_prospectus=has_final,
            has_effect=has_effect,
        )
        risk_flags = unique_flags(
            [
                *parsed.risk_flags,
                *analyze_ipo_risk_flags(
                    record_without_flags,
                    text="",
                    amendment_count=amendment_count,
                    is_foreign_issuer=is_foreign,
                ),
            ]
        )
        records.append(IpoPipelineRecord(**{**record_without_flags.to_dict(), "risk_flags": tuple(risk_flags)}))

    return sorted(records, key=lambda record: (_date_sort_key(record.filed_date), record.company_name), reverse=True)


def fetch_ipo_pipeline_snapshot(
    client: SecEdgarClient | None = None,
    *,
    forms: Iterable[str] = IPO_FORMS,
    per_form_limit: int = 60,
    force_refresh: bool = False,
    parse_documents: bool = True,
    parse_limit: int = 25,
    enrich_limit: int = 120,
    cache_max_age: timedelta = timedelta(minutes=30),
) -> IpoPipelineSnapshot:
    client = client or SecEdgarClient()
    store = IpoPipelineStore(client.cache_dir)
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
            return IpoPipelineSnapshot(
                records=stale.records,
                fetched_at=stale.fetched_at,
                source="SEC EDGAR cached IPO pipeline",
                used_cache=True,
                errors=tuple(errors),
            )
        if errors:
            raise RuntimeError("Could not fetch SEC IPO filings: " + "; ".join(errors))

    filings = _dedupe_filings(filings)
    grouped = _group_filings_by_cik(filings)
    submissions_by_cik: dict[str, dict[str, Any]] = {}
    for cik in _latest_ciks(grouped)[:enrich_limit]:
        try:
            submissions_by_cik[cik] = client.get_submissions_by_cik(cik)
        except Exception as exc:
            errors.append(f"{cik} submissions: {exc}")

    parsed_by_accession: dict[str, ParsedIpoFields] = {}
    if parse_documents:
        parse_candidates = _document_parse_candidates(grouped)[:parse_limit]
        for filing in parse_candidates:
            try:
                text = fetch_primary_ipo_document_text(client, filing)
                parsed_by_accession[filing.accession_number] = parse_ipo_filing_text(text, form=filing.form)
            except Exception as exc:
                errors.append(f"{filing.accession_number} parse: {exc}")

    records = build_ipo_pipeline_records(
        filings,
        submissions_by_cik=submissions_by_cik,
        parsed_by_accession=parsed_by_accession,
    )
    snapshot = IpoPipelineSnapshot(
        records=tuple(records),
        fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        source="SEC EDGAR current filings and data.sec.gov submissions",
        used_cache=False,
        errors=tuple(errors),
    )
    store.save(snapshot)
    return snapshot


def fetch_primary_ipo_document_text(client: SecEdgarClient, filing: SecCurrentFiling) -> str:
    url = primary_ipo_document_url(client, filing)
    return client.document_text_url(
        url,
        cache_name=f"ipo_document_{filing.cik}_{filing.accession_no_dashes}_{url.rsplit('/', 1)[-1]}.txt",
    )


def primary_ipo_document_url(client: SecEdgarClient, filing: SecCurrentFiling) -> str:
    if filing.primary_document and not filing.primary_document.endswith("-index.htm"):
        return filing.filing_url

    items = client.filing_index_items_for_accession(filing.cik, filing.accession_number)
    candidates: list[tuple[int, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        document_type = str(item.get("type") or "").upper()
        if not _looks_like_text_filing_document(name):
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


def parse_ipo_filing_text(text: str, *, form: str = "") -> ParsedIpoFields:
    clean_text = _collapse_text(text)
    if not clean_text:
        return ParsedIpoFields(is_foreign_issuer=form.upper().startswith("F-1") if form else None)

    price_low, price_high = _extract_price_range(clean_text)
    proposed_ticker = _extract_ticker(clean_text)
    exchange = _extract_exchange(clean_text)
    revenue = _extract_money_after_terms(clean_text, ("revenue", "revenues", "net sales"))
    net_income = _extract_money_after_terms(clean_text, ("net income", "net loss"))
    if net_income is not None and _near_term(clean_text, "net loss", net_income):
        net_income = -abs(net_income)
    gross_margin = _extract_percent_after_terms(clean_text, ("gross margin", "gross profit margin"))
    cash = _extract_money_after_terms(clean_text, ("cash and cash equivalents", "cash equivalents"))
    debt = _extract_money_after_terms(clean_text, ("total debt", "indebtedness"))
    risk_flags = analyze_text_risk_flags(clean_text)
    is_foreign = form.upper().startswith("F-1") or "foreign private issuer" in clean_text.lower()

    return ParsedIpoFields(
        proposed_ticker=proposed_ticker,
        exchange=exchange,
        offering_amount=_extract_money_after_terms(clean_text, ("offering amount", "aggregate offering price", "maximum aggregate offering price")),
        price_range_low=price_low,
        price_range_high=price_high,
        shares_offered=_extract_shares_offered(clean_text),
        implied_market_cap=_extract_money_after_terms(clean_text, ("implied market capitalization", "market capitalization")),
        revenue=revenue,
        revenue_growth=_extract_percent_after_terms(clean_text, ("revenue growth", "revenues increased")),
        net_income=net_income,
        gross_margin=gross_margin,
        cash=cash,
        debt=debt,
        use_of_proceeds=_extract_section_snippet(clean_text, "use of proceeds"),
        underwriters=tuple(_extract_underwriters(clean_text)),
        auditor=_extract_auditor(clean_text),
        risk_flags=tuple(risk_flags),
        is_foreign_issuer=is_foreign,
    )


def analyze_ipo_risk_flags(
    record: IpoPipelineRecord,
    *,
    text: str = "",
    amendment_count: int | None = None,
    is_foreign_issuer: bool | None = None,
) -> list[str]:
    flags: list[str] = []
    if record.revenue is not None and record.revenue <= 0:
        flags.append("No revenue")
    if record.revenue_growth is not None and record.revenue_growth < 0:
        flags.append("Revenue declining")
    if record.net_income is not None and record.net_income < 0:
        flags.append("Unprofitable")
    if record.gross_margin is not None and record.gross_margin < 0:
        flags.append("Negative gross margin")
    if record.debt is not None and record.debt > 0 and record.cash is not None and record.debt > record.cash:
        flags.append("High debt")
    if is_foreign_issuer is True or record.is_foreign_issuer:
        flags.append("Foreign issuer")
    if (amendment_count if amendment_count is not None else record.amendment_count) >= 3:
        flags.append("Repeated amendments")
    if record.price_range_low is None or record.price_range_high is None:
        flags.append("Price range missing")
    if record.offering_amount is None and record.shares_offered is None:
        flags.append("Offering terms incomplete")
    if text:
        flags.extend(analyze_text_risk_flags(text))
    return unique_flags(flags)


def analyze_text_risk_flags(text: str) -> list[str]:
    lower = text.lower()
    checks = [
        ("Going concern language", ("going concern", "substantial doubt about our ability to continue")),
        ("Related-party transactions", ("related party transaction", "related-party transaction", "transactions with related parties")),
        ("Customer concentration", ("customer concentration", "major customer", "significant customer")),
        ("Controlled company", ("controlled company",)),
        ("China/VIE structure", ("variable interest entity", " vie ", "prc", "china-based")),
        ("SPAC-related", ("special purpose acquisition company", " spac ")),
        ("Auditor change", ("auditor change", "change in auditor", "changed auditors", "auditor resignation")),
    ]
    flags: list[str] = []
    for flag, terms in checks:
        if any(term in lower for term in terms):
            flags.append(flag)
    return unique_flags(flags)


def sector_for_sic(sic: str | None, industry: str | None = None) -> str | None:
    industry_lower = (industry or "").lower()
    if any(term in industry_lower for term in ("biotechnology", "pharmaceutical", "medical", "health")):
        return "Health Care"
    if any(term in industry_lower for term in ("software", "semiconductor", "computer", "internet")):
        return "Technology"
    if any(term in industry_lower for term in ("bank", "insurance", "investment", "finance")):
        return "Financials"

    try:
        code = int(str(sic or "").strip()[:4])
    except ValueError:
        return None
    if 100 <= code <= 999:
        return "Agriculture"
    if 1000 <= code <= 1499:
        return "Energy / Materials"
    if 1500 <= code <= 1799:
        return "Industrials"
    if 2000 <= code <= 3999:
        if 2830 <= code <= 2839 or 3840 <= code <= 3859:
            return "Health Care"
        if 3570 <= code <= 3579 or 3670 <= code <= 3679:
            return "Technology"
        return "Manufacturing"
    if 4000 <= code <= 4999:
        return "Transportation / Utilities"
    if 5000 <= code <= 5999:
        return "Consumer / Retail"
    if 6000 <= code <= 6799:
        return "Financials"
    if 7000 <= code <= 8999:
        if 7370 <= code <= 7379:
            return "Technology"
        return "Services"
    return "Other"


def unique_flags(flags: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for flag in flags:
        clean = flag.strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        output.append(clean)
    return output


def display_money(value: float | None) -> str:
    if value is None:
        return NOT_EXTRACTED
    return f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}"


def display_percent(value: float | None) -> str:
    return NOT_EXTRACTED if value is None else f"{value:.1f}%"


def display_price_range(record: IpoPipelineRecord) -> str:
    if record.price_range_low is None or record.price_range_high is None:
        return NOT_DISCLOSED
    if record.price_range_low == record.price_range_high:
        return f"${record.price_range_low:,.2f}"
    return f"${record.price_range_low:,.2f} - ${record.price_range_high:,.2f}"


def display_optional_text(value: str | None, *, missing: str = EMPTY_VALUE) -> str:
    clean = _clean_text(value)
    return clean or missing


def _merge_parsed_fields(values: Iterable[ParsedIpoFields | None]) -> ParsedIpoFields:
    merged: dict[str, Any] = {}
    underwriters: list[str] = []
    risk_flags: list[str] = []
    for parsed in values:
        if parsed is None:
            continue
        for key, value in asdict(parsed).items():
            if key in {"underwriters", "risk_flags"}:
                continue
            if merged.get(key) in (None, "") and value not in (None, ""):
                merged[key] = value
        underwriters.extend(parsed.underwriters)
        risk_flags.extend(parsed.risk_flags)
    return ParsedIpoFields(
        proposed_ticker=merged.get("proposed_ticker"),
        exchange=merged.get("exchange"),
        offering_amount=merged.get("offering_amount"),
        price_range_low=merged.get("price_range_low"),
        price_range_high=merged.get("price_range_high"),
        shares_offered=merged.get("shares_offered"),
        implied_market_cap=merged.get("implied_market_cap"),
        revenue=merged.get("revenue"),
        revenue_growth=merged.get("revenue_growth"),
        net_income=merged.get("net_income"),
        gross_margin=merged.get("gross_margin"),
        cash=merged.get("cash"),
        debt=merged.get("debt"),
        use_of_proceeds=merged.get("use_of_proceeds"),
        underwriters=tuple(unique_flags(underwriters)),
        auditor=merged.get("auditor"),
        risk_flags=tuple(unique_flags(risk_flags)),
        is_foreign_issuer=merged.get("is_foreign_issuer"),
    )


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


def _document_parse_candidates(grouped: dict[str, list[SecCurrentFiling]]) -> list[SecCurrentFiling]:
    candidates: list[SecCurrentFiling] = []
    for filings in grouped.values():
        parseable = [filing for filing in filings if filing.form in {"S-1", "S-1/A", "F-1", "F-1/A", "424B4"}]
        if parseable:
            candidates.append(sorted(parseable, key=lambda item: _date_sort_key(item.filing_date), reverse=True)[0])
    return sorted(candidates, key=lambda filing: _date_sort_key(filing.filing_date), reverse=True)


def _date_sort_key(value: str) -> str:
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return "0000-00-00"


def _looks_like_text_filing_document(name: str) -> bool:
    lower = name.lower()
    if not lower.endswith((".htm", ".html", ".txt")):
        return False
    return not any(skip in lower for skip in ("index", "filingsummary", ".xml", ".xsd", ".jpg", ".png"))


def _form_slug(form: str) -> str:
    return form.lower().replace("/", "").replace("-", "")


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_string(value: Any) -> str | None:
    if isinstance(value, list):
        for item in value:
            clean = _clean_text(item)
            if clean:
                return clean
        return None
    clean = _clean_text(value)
    return clean or None


def _collapse_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_ticker(text: str) -> str | None:
    patterns = (
        r"(?:proposed|expected)\s+(?:ticker\s+)?symbol\s+(?:is|will be)?\s*[\"']?([A-Z][A-Z0-9.]{0,5})[\"']?",
        r"under the symbol\s+[\"']?([A-Z][A-Z0-9.]{0,5})[\"']?",
        r"ticker symbol\s+[\"']?([A-Z][A-Z0-9.]{0,5})[\"']?",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            candidate = match.group(1).strip(" .,\"'").upper()
            if candidate not in {"THE", "OUR", "AND", "IPO"}:
                return candidate
    return None


def _extract_exchange(text: str) -> str | None:
    exchanges = (
        "Nasdaq Global Select Market",
        "Nasdaq Global Market",
        "Nasdaq Capital Market",
        "New York Stock Exchange",
        "NYSE American",
        "NYSE",
        "Nasdaq",
    )
    lower = text.lower()
    for exchange in exchanges:
        if exchange.lower() in lower:
            return exchange
    return None


def _extract_price_range(text: str) -> tuple[float | None, float | None]:
    match = re.search(r"between\s+\$?\s*([0-9]+(?:\.[0-9]+)?)\s+and\s+\$?\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    if match:
        return float(match.group(1)), float(match.group(2))
    match = re.search(r"(?:public offering price|initial public offering price)\s+(?:of|is)\s+\$?\s*([0-9]+(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    if match:
        value = float(match.group(1))
        return value, value
    return None, None


def _extract_money_after_terms(text: str, terms: tuple[str, ...]) -> float | None:
    lower = text.lower()
    for term in terms:
        start = lower.find(term)
        if start < 0:
            continue
        value = _first_money_value(text[start : start + 320])
        if value is not None:
            return value
    return None


def _first_money_value(window: str) -> float | None:
    match = re.search(r"\(?\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(billion|million|thousand)?\)?", window, flags=re.IGNORECASE)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    scale = (match.group(2) or "").lower()
    if scale == "billion":
        value *= 1_000_000_000
    elif scale == "million":
        value *= 1_000_000
    elif scale == "thousand":
        value *= 1_000
    if "(" in match.group(0) and ")" in match.group(0):
        value = -abs(value)
    return value


def _near_term(text: str, term: str, _value: float) -> bool:
    lower = text.lower()
    index = lower.find(term)
    return index >= 0


def _extract_percent_after_terms(text: str, terms: tuple[str, ...]) -> float | None:
    lower = text.lower()
    for term in terms:
        start = lower.find(term)
        if start < 0:
            continue
        match = re.search(r"(-?[0-9]+(?:\.[0-9]+)?)\s*%", text[start : start + 260])
        if match:
            return float(match.group(1))
    return None


def _extract_shares_offered(text: str) -> float | None:
    match = re.search(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|thousand)?\s+shares", text, flags=re.IGNORECASE)
    if not match:
        return None
    shares = float(match.group(1).replace(",", ""))
    scale = (match.group(2) or "").lower()
    if scale == "million":
        shares *= 1_000_000
    elif scale == "thousand":
        shares *= 1_000
    return shares


def _extract_section_snippet(text: str, heading: str) -> str | None:
    lower = text.lower()
    index = lower.find(heading.lower())
    if index < 0:
        return None
    snippet = text[index : index + 420].strip()
    snippet = re.sub(r"\s+", " ", snippet)
    return snippet[:260].rstrip(" ,.;") if snippet else None


def _extract_underwriters(text: str) -> list[str]:
    match = re.search(r"(?:representatives of the underwriters|underwriters)\s+(?:are|include|:)\s+([^.;]{5,180})", text, flags=re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1)
    parts = re.split(r",| and ", raw)
    return [part.strip(" .") for part in parts if len(part.strip(" .")) >= 3][:8]


def _extract_auditor(text: str) -> str | None:
    patterns = (
        r"independent registered public accounting firm(?:\s+is|\s*,)?\s+([A-Z][A-Za-z0-9&., '\-]{3,90})",
        r"audited by\s+([A-Z][A-Za-z0-9&., '\-]{3,90})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip(" .")
    return None
