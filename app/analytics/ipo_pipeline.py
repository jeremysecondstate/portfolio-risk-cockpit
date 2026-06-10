from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from app.data.sec_edgar import (
    DEFAULT_CACHE_DIR,
    DEFAULT_CACHE_TTL,
    SEC_ARCHIVES_BASE_URL,
    SecCurrentFiling,
    SecEdgarClient,
    html_to_text,
    normalize_cik,
)


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
PROSPECTUS_SOURCE_FORMS = REGISTRATION_FORMS | AMENDMENT_FORMS | FINAL_PROSPECTUS_FORMS
NOT_EXTRACTED = "Not extracted yet"
NOT_DISCLOSED = "Not yet disclosed"
EMPTY_VALUE = "--"
PARSE_STATUS_NOT_PARSED = "Not parsed"
PARSE_STATUS_PARSED = "Parsed"
PARSE_STATUS_PARTIAL = "Partial"
PARSE_STATUS_NO_DOCUMENT = "No prospectus doc"
PARSE_STATUS_FAILED = "Parse failed"
PARSE_STATUS_CACHED = "Cached"


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
    dilution_per_share: float | None = None
    going_concern: bool | None = None
    customer_concentration: bool | None = None
    related_party_transactions: bool | None = None
    controlled_company: bool | None = None
    vie_or_china_risk: bool | None = None


@dataclass(frozen=True)
class IpoDocumentCandidate:
    cik: str
    company_name: str
    form: str
    filing_date: str
    accession_number: str
    name: str
    url: str
    document_type: str = ""
    description: str = ""
    sequence: str = ""
    size: int | None = None
    score: int = 0
    selection_reason: str = ""
    is_complete_submission: bool = False
    source: str = "index"

    @property
    def accession_no_dashes(self) -> str:
        return self.accession_number.replace("-", "")


@dataclass(frozen=True)
class IpoFetchedDocument:
    candidate: IpoDocumentCandidate
    text: str


@dataclass(frozen=True)
class IpoParseDiagnostics:
    status: str
    detail: str = ""
    source_form: str = ""
    source_accession_number: str = ""
    source_document: str = ""
    source_url: str = ""
    source_size: int | None = None
    source_reason: str = ""
    cached: bool = False


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
    parse_status: str = PARSE_STATUS_NOT_PARSED
    parse_diagnostics: str = ""
    parsed_source_form: str = ""
    parsed_source_accession_number: str = ""
    parsed_source_document: str = ""
    parsed_source_url: str = ""
    parsed_source_size: int | None = None
    parsed_source_reason: str = ""
    parsed_from_cache: bool = False

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


class IpoParsedFieldsStore:
    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE_DIR)
        self.path = self.cache_dir / "ipo_parsed_fields.json"
        self._payload: dict[str, Any] | None = None

    def load(self, candidate: IpoDocumentCandidate) -> ParsedIpoFields | None:
        entry = self._entries().get(ipo_parsed_field_cache_key(candidate))
        if not isinstance(entry, dict):
            return None
        try:
            return _parsed_fields_from_dict(entry.get("fields") or {})
        except (TypeError, ValueError):
            return None

    def load_any(self, candidates: Iterable[IpoDocumentCandidate]) -> tuple[IpoDocumentCandidate, ParsedIpoFields] | None:
        for candidate in candidates:
            fields = self.load(candidate)
            if fields is not None:
                return candidate, fields
        return None

    def save(self, candidate: IpoDocumentCandidate, fields: ParsedIpoFields) -> None:
        payload = self._load_payload()
        entries = payload.setdefault("entries", {})
        if not isinstance(entries, dict):
            entries = {}
            payload["entries"] = entries
        entries[ipo_parsed_field_cache_key(candidate)] = {
            "parsed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "metadata": {
                "cik": candidate.cik,
                "accession_number": candidate.accession_number,
                "document": candidate.name,
                "form": candidate.form,
                "url": candidate.url,
                "size": candidate.size,
                "selection_reason": candidate.selection_reason,
            },
            "fields": _parsed_fields_to_dict(fields),
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        self._payload = payload

    def _entries(self) -> dict[str, Any]:
        entries = self._load_payload().get("entries")
        return entries if isinstance(entries, dict) else {}

    def _load_payload(self) -> dict[str, Any]:
        if self._payload is not None:
            return self._payload
        if not self.path.exists():
            self._payload = {"version": 1, "entries": {}}
            return self._payload
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {"version": 1, "entries": {}}
        if not isinstance(payload, dict):
            payload = {"version": 1, "entries": {}}
        payload.setdefault("version", 1)
        payload.setdefault("entries", {})
        self._payload = payload
        return payload


def ipo_parsed_field_cache_key(candidate: IpoDocumentCandidate) -> str:
    raw = "|".join(
        (
            normalize_cik(candidate.cik),
            candidate.accession_number,
            candidate.name,
            str(candidate.size or ""),
            candidate.url,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _parsed_fields_to_dict(fields: ParsedIpoFields) -> dict[str, Any]:
    payload = asdict(fields)
    payload["underwriters"] = list(fields.underwriters)
    payload["risk_flags"] = list(fields.risk_flags)
    return payload


def _parsed_fields_from_dict(payload: dict[str, Any]) -> ParsedIpoFields:
    values = dict(payload)
    values["underwriters"] = tuple(values.get("underwriters") or ())
    values["risk_flags"] = tuple(values.get("risk_flags") or ())
    allowed = {field_name for field_name in ParsedIpoFields.__dataclass_fields__}
    return ParsedIpoFields(**{key: value for key, value in values.items() if key in allowed})


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
    parse_diagnostics_by_cik: dict[str, IpoParseDiagnostics] | None = None,
) -> list[IpoPipelineRecord]:
    submissions_by_cik = submissions_by_cik or {}
    parsed_by_accession = parsed_by_accession or {}
    parse_diagnostics_by_cik = parse_diagnostics_by_cik or {}
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
        diagnostics = parse_diagnostics_by_cik.get(cik) or _default_parse_diagnostics(parsed_by_accession, sorted_group)

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
            parse_status=diagnostics.status,
            parse_diagnostics=diagnostics.detail,
            parsed_source_form=diagnostics.source_form,
            parsed_source_accession_number=diagnostics.source_accession_number,
            parsed_source_document=diagnostics.source_document,
            parsed_source_url=diagnostics.source_url,
            parsed_source_size=diagnostics.source_size,
            parsed_source_reason=diagnostics.source_reason,
            parsed_from_cache=diagnostics.cached,
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
    parse_limit: int | None = None,
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

    submission_filings: list[SecCurrentFiling] = []
    current_company_names = _company_names_by_cik(filings)
    for cik, submission in submissions_by_cik.items():
        submission_filings.extend(
            sec_current_filings_from_submission(
                submission,
                fallback_cik=cik,
                fallback_company_name=current_company_names.get(cik, ""),
            )
        )

    if submission_filings:
        filings = _dedupe_filings([*filings, *submission_filings])
        grouped = _group_filings_by_cik(filings)

    parsed_by_accession: dict[str, ParsedIpoFields] = {}
    parse_diagnostics_by_cik: dict[str, IpoParseDiagnostics] = {}
    if parse_documents:
        parsed_store = IpoParsedFieldsStore(client.cache_dir)
        parse_candidates = _document_parse_candidates(grouped)
        if parse_limit is not None:
            parse_candidates = parse_candidates[: max(0, parse_limit)]
        for filing in parse_candidates:
            cik = normalize_cik(filing.cik)
            try:
                document_candidates = ipo_document_candidates_for_filing(client, filing)
                if not document_candidates:
                    parse_diagnostics_by_cik[cik] = IpoParseDiagnostics(
                        status=PARSE_STATUS_NO_DOCUMENT,
                        detail=f"No prospectus-like document found in {filing.accession_number}.",
                        source_form=filing.form,
                        source_accession_number=filing.accession_number,
                    )
                    continue

                cached = parsed_store.load_any(document_candidates)
                if cached is not None:
                    candidate, parsed = cached
                    parsed_by_accession[candidate.accession_number] = parsed
                    parse_diagnostics_by_cik[cik] = _parse_diagnostics_for_candidate(
                        candidate,
                        parsed,
                        status=PARSE_STATUS_CACHED,
                        cached=True,
                    )
                    continue

                fetched = fetch_ipo_document_text_with_source(client, filing, candidates=document_candidates)
                parsed = parse_ipo_filing_text(fetched.text, form=fetched.candidate.form)
                parsed_store.save(fetched.candidate, parsed)
                parsed_by_accession[fetched.candidate.accession_number] = parsed
                parse_diagnostics_by_cik[cik] = _parse_diagnostics_for_candidate(fetched.candidate, parsed)
            except Exception as exc:
                parse_diagnostics_by_cik[cik] = IpoParseDiagnostics(
                    status=PARSE_STATUS_FAILED,
                    detail=str(exc),
                    source_form=filing.form,
                    source_accession_number=filing.accession_number,
                )
                errors.append(f"{filing.accession_number} parse: {exc}")

    records = build_ipo_pipeline_records(
        filings,
        submissions_by_cik=submissions_by_cik,
        parsed_by_accession=parsed_by_accession,
        parse_diagnostics_by_cik=parse_diagnostics_by_cik,
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


def sec_current_filings_from_submission(
    submission: dict[str, Any],
    *,
    fallback_cik: str = "",
    fallback_company_name: str = "",
) -> list[SecCurrentFiling]:
    recent = ((submission.get("filings") or {}).get("recent") or {})
    if not isinstance(recent, dict):
        return []

    cik = normalize_cik(submission.get("cik") or fallback_cik)
    company_name = (
        _clean_text(submission.get("name"))
        or _clean_text(submission.get("entityName"))
        or _clean_text(fallback_company_name)
        or "Unknown company"
    )
    sic = _clean_text(submission.get("sic"))
    sic_description = _clean_text(submission.get("sicDescription"))

    forms = list(recent.get("form") or [])
    accessions = list(recent.get("accessionNumber") or [])
    filing_dates = list(recent.get("filingDate") or [])
    report_dates = list(recent.get("reportDate") or [])
    primary_docs = list(recent.get("primaryDocument") or [])
    acceptance_datetimes = list(recent.get("acceptanceDateTime") or [])

    filings: list[SecCurrentFiling] = []
    for index, raw_form in enumerate(forms):
        form = str(raw_form or "").strip().upper()
        if not is_ipo_form(form):
            continue
        accession = _safe_list_get(accessions, index)
        if not accession:
            continue
        primary_document = _safe_list_get(primary_docs, index)
        accession_no_dashes = accession.replace("-", "")
        document_name = primary_document or f"{accession}-index.htm"
        filing_url = f"{SEC_ARCHIVES_BASE_URL}/{int(cik)}/{accession_no_dashes}/{document_name}"
        filings.append(
            SecCurrentFiling(
                company_name=company_name,
                cik=cik,
                form=form,
                filing_date=_safe_list_get(filing_dates, index) or _safe_list_get(report_dates, index),
                accession_number=accession,
                filing_url=filing_url,
                assigned_sic=sic,
                assigned_sic_description=sic_description,
                acceptance_datetime=_safe_list_get(acceptance_datetimes, index),
                primary_document=primary_document,
            )
        )
    return filings


def related_prospectus_filing_for_report(client: SecEdgarClient, filing: SecCurrentFiling) -> SecCurrentFiling:
    normalized_form = filing.form.strip().upper()
    if normalized_form in PROSPECTUS_SOURCE_FORMS:
        return filing

    submission = client.get_submissions_by_cik(filing.cik)
    related = sec_current_filings_from_submission(
        submission,
        fallback_cik=filing.cik,
        fallback_company_name=filing.company_name,
    )
    selected = _best_prospectus_filing(related)
    if selected is None:
        raise RuntimeError(f"No related S-1/F-1/424B4 prospectus found for {filing.company_name} ({filing.cik}).")
    return selected


def fetch_primary_ipo_document_text(client: SecEdgarClient, filing: SecCurrentFiling) -> str:
    return fetch_ipo_document_text_with_source(client, filing).text


def fetch_ipo_document_text_with_source(
    client: SecEdgarClient,
    filing: SecCurrentFiling,
    *,
    candidates: tuple[IpoDocumentCandidate, ...] | None = None,
) -> IpoFetchedDocument:
    candidates = candidates or ipo_document_candidates_for_filing(client, filing)
    if not candidates:
        raise RuntimeError(f"No primary document URL found for {filing.accession_number}.")

    primary = candidates[0]
    text = _fetch_candidate_text(client, primary)
    if primary.is_complete_submission or not _prospectus_text_is_too_thin(text):
        return IpoFetchedDocument(candidate=primary, text=text)

    complete = next((candidate for candidate in candidates if candidate.is_complete_submission), None)
    if complete is None:
        return IpoFetchedDocument(candidate=primary, text=text)

    fallback_text = _fetch_candidate_text(client, complete)
    if len(_collapse_text(fallback_text)) <= len(_collapse_text(text)):
        return IpoFetchedDocument(candidate=primary, text=text)

    fallback = IpoDocumentCandidate(
        **{
            **asdict(complete),
            "selection_reason": f"Complete-submission fallback; primary candidate {primary.name} was too thin.",
        }
    )
    return IpoFetchedDocument(candidate=fallback, text=fallback_text)


def primary_ipo_document_url(client: SecEdgarClient, filing: SecCurrentFiling) -> str:
    candidate = select_ipo_document_candidate(client, filing)
    if candidate is not None:
        return candidate.url
    if filing.filing_url:
        return filing.filing_url
    raise RuntimeError(f"No primary document URL found for {filing.accession_number}.")


def select_ipo_document_candidate(client: SecEdgarClient, filing: SecCurrentFiling) -> IpoDocumentCandidate | None:
    candidates = ipo_document_candidates_for_filing(client, filing)
    return candidates[0] if candidates else None


def ipo_document_candidates_for_filing(client: SecEdgarClient, filing: SecCurrentFiling) -> tuple[IpoDocumentCandidate, ...]:
    candidates_by_url: dict[str, IpoDocumentCandidate] = {}

    primary_name = filing.primary_document.strip() if filing.primary_document else ""
    if primary_name and _looks_like_text_filing_document(primary_name):
        primary_candidate = _document_candidate_from_parts(
            filing,
            name=primary_name,
            document_type=filing.form,
            description="SEC primary document",
            sequence="",
            size=None,
            source="primary",
        )
        candidates_by_url[primary_candidate.url] = primary_candidate

    try:
        items = client.filing_index_items_for_accession(filing.cik, filing.accession_number)
    except Exception:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name or name.endswith("/"):
            continue
        candidate = _document_candidate_from_parts(
            filing,
            name=name,
            document_type=str(item.get("type") or ""),
            description=str(item.get("description") or ""),
            sequence=str(item.get("sequence") or ""),
            size=_to_int(item.get("size")),
            source="index",
        )
        if candidate is None:
            continue
        previous = candidates_by_url.get(candidate.url)
        if previous is None or _document_candidate_sort_key(candidate) < _document_candidate_sort_key(previous):
            candidates_by_url[candidate.url] = candidate

    if not any(candidate.is_complete_submission for candidate in candidates_by_url.values()):
        complete = _document_candidate_from_parts(
            filing,
            name=f"{filing.accession_number}.txt",
            document_type="",
            description="Complete submission text file",
            sequence="",
            size=None,
            source="synthetic",
        )
        if complete is not None:
            candidates_by_url.setdefault(complete.url, complete)

    return tuple(sorted(candidates_by_url.values(), key=_document_candidate_sort_key))


def _document_candidate_from_parts(
    filing: SecCurrentFiling,
    *,
    name: str,
    document_type: str,
    description: str,
    sequence: str,
    size: int | None,
    source: str,
) -> IpoDocumentCandidate | None:
    if not _looks_like_text_filing_document(name):
        return None

    cik = normalize_cik(filing.cik)
    accession_no_dashes = filing.accession_number.replace("-", "")
    url = f"{SEC_ARCHIVES_BASE_URL}/{int(cik)}/{accession_no_dashes}/{name}"
    haystack = f"{document_type} {name} {description}".lower()
    is_complete = _is_complete_submission_document(name, description)
    score = _score_ipo_document_candidate(filing.form, name, document_type, description, is_complete=is_complete)
    if score is None:
        return None
    return IpoDocumentCandidate(
        cik=cik,
        company_name=filing.company_name,
        form=filing.form.strip().upper(),
        filing_date=filing.filing_date,
        accession_number=filing.accession_number,
        name=name,
        url=url,
        document_type=document_type.strip().upper(),
        description=description,
        sequence=sequence,
        size=size,
        score=score,
        selection_reason=_document_selection_reason(filing.form, name, document_type, description, is_complete=is_complete, haystack=haystack),
        is_complete_submission=is_complete,
        source=source,
    )


def _score_ipo_document_candidate(
    filing_form: str,
    name: str,
    document_type: str,
    description: str,
    *,
    is_complete: bool,
) -> int | None:
    lower_name = name.lower()
    lower_type = document_type.strip().upper()
    haystack = f"{document_type} {name} {description}".lower()
    if _is_non_prospectus_document(lower_name, haystack):
        return None
    if is_complete:
        return 80

    form = filing_form.strip().upper()
    form_slug = _form_slug(form)
    normalized_haystack = haystack.replace("-", "").replace("_", "")
    extension_bonus = 0 if lower_name.endswith((".htm", ".html")) else 8
    size_bonus = -2 if "prospectus" in haystack else 0

    if lower_type == form:
        return 0 + extension_bonus + size_bonus
    if form == "424B4" and ("424b4" in normalized_haystack or "final prospectus" in haystack):
        return 1 + extension_bonus + size_bonus
    if form_slug and form_slug in normalized_haystack:
        return 2 + extension_bonus + size_bonus
    if "prospectus" in haystack:
        return 5 + extension_bonus
    if lower_name.endswith((".htm", ".html")) and not _looks_like_exhibit(haystack):
        return 20
    if lower_name.endswith(".txt"):
        return 50
    return None


def _document_selection_reason(
    filing_form: str,
    name: str,
    document_type: str,
    description: str,
    *,
    is_complete: bool,
    haystack: str,
) -> str:
    form = filing_form.strip().upper()
    if is_complete:
        return "Complete submission text fallback."
    if document_type.strip().upper() == form:
        return f"Document type matches selected SEC form {form}."
    if form == "424B4" and ("424b4" in haystack or "final prospectus" in haystack):
        return "Final prospectus document matched 424B4/prospectus terms."
    if _form_slug(form) in haystack.replace("-", "").replace("_", ""):
        return f"Filename or description matches {form} prospectus form."
    if "prospectus" in haystack:
        return "Prospectus term found in filename or description."
    return "Best available HTML filing document."


def _document_candidate_sort_key(candidate: IpoDocumentCandidate) -> tuple[int, int, str]:
    size_rank = -(candidate.size or 0)
    return candidate.score, size_rank, candidate.name.lower()


def _fetch_candidate_text(client: SecEdgarClient, candidate: IpoDocumentCandidate) -> str:
    cache_name = (
        f"companies/{normalize_cik(candidate.cik)}/filings/{candidate.accession_no_dashes}/"
        f"ipo_documents/{_safe_cache_part(candidate.name)}.txt"
    )
    if candidate.is_complete_submission and hasattr(client, "_fetch_text"):
        fetch_text = getattr(client, "_fetch_text")
        if callable(fetch_text):
            raw = fetch_text(candidate.url, cache_name=cache_name, ttl=DEFAULT_CACHE_TTL)
            return html_to_text(_prospectus_document_from_complete_submission(raw, fallback_form=candidate.form))

    text = client.document_text_url(candidate.url, cache_name=cache_name)
    if candidate.is_complete_submission:
        return html_to_text(_prospectus_document_from_complete_submission(text, fallback_form=candidate.form))
    return text


def _prospectus_document_from_complete_submission(raw: str, *, fallback_form: str = "") -> str:
    blocks = _complete_submission_document_blocks(raw)
    if not blocks:
        return raw
    ranked = sorted(blocks, key=lambda block: _complete_submission_block_rank(block, fallback_form=fallback_form))
    return ranked[0].get("text") or ranked[0].get("raw") or raw


def _complete_submission_document_blocks(raw: str) -> list[dict[str, str]]:
    blocks: list[dict[str, str]] = []
    for match in re.finditer(r"(?is)<DOCUMENT>(.*?)</DOCUMENT>", raw or ""):
        block = match.group(1)
        text_match = re.search(r"(?is)<TEXT>(.*?)(?:</TEXT>|$)", block)
        blocks.append(
            {
                "raw": block,
                "type": _complete_submission_tag(block, "TYPE"),
                "filename": _complete_submission_tag(block, "FILENAME"),
                "description": _complete_submission_tag(block, "DESCRIPTION"),
                "text": text_match.group(1) if text_match else block,
            }
        )
    return blocks


def _complete_submission_tag(block: str, tag: str) -> str:
    match = re.search(rf"(?im)^\s*<{tag}>\s*([^\r\n<]+)", block)
    return match.group(1).strip() if match else ""


def _complete_submission_block_rank(block: dict[str, str], *, fallback_form: str = "") -> tuple[int, int, str]:
    document_type = block.get("type", "").strip().upper()
    filename = block.get("filename", "").lower()
    description = block.get("description", "").lower()
    haystack = f"{document_type} {filename} {description}".lower()
    fallback = fallback_form.strip().upper()
    score = 50
    if document_type == fallback and fallback:
        score = 0
    elif document_type in PROSPECTUS_SOURCE_FORMS:
        score = 1
    elif "prospectus" in haystack:
        score = 2
    elif any(slug in filename.replace("-", "") for slug in ("forms1", "formf1", "s1", "f1", "424b4")):
        score = 3
    elif document_type.startswith("EX-") or "exhibit" in haystack:
        score = 80
    return score, -len(block.get("text") or ""), filename


def _prospectus_text_is_too_thin(text: str) -> bool:
    clean = _collapse_text(text)
    if len(clean) < 2500:
        return True
    lower = clean[:12000].lower()
    return not any(term in lower for term in ("prospectus", "the offering", "risk factors", "use of proceeds"))


def _is_complete_submission_document(name: str, description: str) -> bool:
    lower = f"{name} {description}".lower()
    return "complete submission" in lower or bool(re.fullmatch(r"\d{10}-\d{2}-\d{6}\.txt", name.lower()))


def _is_non_prospectus_document(lower_name: str, haystack: str) -> bool:
    if lower_name.endswith("-index.htm") or lower_name in {"index.htm", "index.html", "index.json"}:
        return True
    if lower_name.endswith((".xml", ".xsd", ".jpg", ".jpeg", ".png", ".gif", ".css", ".js", ".json")):
        return True
    if any(term in lower_name for term in ("filingsummary", "metadata", "calculation", "schema")):
        return True
    if _looks_like_exhibit(haystack):
        return True
    return False


def _looks_like_exhibit(haystack: str) -> bool:
    exhibit_terms = (
        "ex-",
        "exhibit",
        "graphic",
        "logo",
        "opinion",
        "consent",
        "power of attorney",
        "bylaws",
        "certificate",
        "xbrl",
        "taxonomy",
        "cover page interactive data",
    )
    if "complete submission" in haystack:
        return False
    if "prospectus" in haystack and not any(term in haystack for term in ("ex-99", "exhibit 99")):
        return False
    return any(term in haystack for term in exhibit_terms)


def _parse_diagnostics_for_candidate(
    candidate: IpoDocumentCandidate,
    parsed: ParsedIpoFields,
    *,
    status: str | None = None,
    cached: bool = False,
) -> IpoParseDiagnostics:
    field_count = _parsed_field_count(parsed)
    resolved_status = status or (PARSE_STATUS_PARSED if field_count >= 3 else PARSE_STATUS_PARTIAL)
    detail = f"{resolved_status}: {field_count} field(s) extracted from {candidate.name}."
    if candidate.selection_reason:
        detail = f"{detail} {candidate.selection_reason}"
    return IpoParseDiagnostics(
        status=resolved_status,
        detail=detail,
        source_form=candidate.form,
        source_accession_number=candidate.accession_number,
        source_document=candidate.name,
        source_url=candidate.url,
        source_size=candidate.size,
        source_reason=candidate.selection_reason,
        cached=cached,
    )


def _default_parse_diagnostics(
    parsed_by_accession: dict[str, ParsedIpoFields],
    filings: Iterable[SecCurrentFiling],
) -> IpoParseDiagnostics:
    for filing in filings:
        parsed = parsed_by_accession.get(filing.accession_number)
        if parsed is not None:
            return IpoParseDiagnostics(status=PARSE_STATUS_PARSED if _parsed_field_count(parsed) >= 3 else PARSE_STATUS_PARTIAL)
    return IpoParseDiagnostics(status=PARSE_STATUS_NOT_PARSED)


def _parsed_field_count(parsed: ParsedIpoFields) -> int:
    count = 0
    for key, value in asdict(parsed).items():
        if key in {"underwriters", "risk_flags"}:
            count += 1 if value else 0
        elif value not in (None, "", False):
            count += 1
    return count


def parse_ipo_filing_text(text: str, *, form: str = "") -> ParsedIpoFields:
    clean_text = _collapse_text(text)
    if not clean_text:
        return ParsedIpoFields(is_foreign_issuer=form.upper().startswith("F-1") if form else None)

    offering_text = _offering_section_text(clean_text)
    price_low, price_high = _extract_price_range(offering_text)
    proposed_ticker = _extract_ticker(clean_text)
    exchange = _extract_exchange(clean_text)
    table_multiplier = _table_amount_multiplier(clean_text)
    revenue = (
        _extract_financial_row_value(clean_text, (r"Revenue", r"Revenues", r"Net sales"), table_multiplier)
        or _extract_money_after_terms(clean_text, ("revenue", "revenues", "net sales"))
    )
    gross_profit = _extract_financial_row_value(clean_text, (r"Gross profit",), table_multiplier)
    net_income = (
        _extract_financial_row_value(clean_text, (r"Net income \(loss\)", r"Net loss", r"Net income"), table_multiplier)
        or _extract_money_after_terms(clean_text, ("net income", "net loss"))
    )
    if net_income is not None and _near_term(clean_text, "net loss", net_income):
        net_income = -abs(net_income)
    gross_margin = _extract_percent_after_terms(clean_text, ("gross margin", "gross profit margin"))
    if gross_margin is None and revenue not in (None, 0) and gross_profit is not None:
        gross_margin = gross_profit / revenue * 100
    cash = (
        _extract_financial_row_value(clean_text, (r"Cash and cash equivalents", r"Cash equivalents"), table_multiplier)
        or _extract_money_after_terms(clean_text, ("cash and cash equivalents", "cash equivalents"))
    )
    debt = (
        _extract_financial_row_value(clean_text, (r"Total debt", r"Indebtedness"), table_multiplier)
        or _extract_money_after_terms(clean_text, ("total debt", "indebtedness"))
    )
    risk_flags = analyze_text_risk_flags(clean_text)
    is_foreign = form.upper().startswith("F-1") or "foreign private issuer" in clean_text.lower()
    going_concern = _has_pattern(clean_text, (r"\bgoing concern\b", r"\bsubstantial doubt about our ability to continue\b"))
    customer_concentration = _has_pattern(clean_text, (r"\bcustomer concentration\b", r"\bmajor customers?\b", r"\bsignificant customers?\b"))
    related_party = _has_pattern(clean_text, (r"\brelated[- ]party\b", r"\btransactions with related parties\b"))
    controlled_company = _has_pattern(clean_text, (r"\bcontrolled company\b",))
    vie_or_china = _has_pattern(clean_text, (r"\bvariable interest entity\b", r"\bVIEs?\b", r"\bPRC\b", r"\bchina[- ]based\b"))

    return ParsedIpoFields(
        proposed_ticker=proposed_ticker,
        exchange=exchange,
        offering_amount=_extract_money_after_terms(clean_text, ("offering amount", "aggregate offering price", "maximum aggregate offering price")),
        price_range_low=price_low,
        price_range_high=price_high,
        shares_offered=_extract_shares_offered(offering_text),
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
        dilution_per_share=_extract_per_share_value_near(clean_text, ("immediate dilution", "dilution per share", "dilution to new investors")),
        going_concern=going_concern,
        customer_concentration=customer_concentration,
        related_party_transactions=related_party,
        controlled_company=controlled_company,
        vie_or_china_risk=vie_or_china,
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
    checks = [
        ("Going concern language", (r"\bgoing concern\b", r"\bsubstantial doubt about our ability to continue\b")),
        ("Related-party transactions", (r"\brelated[- ]party transactions?\b", r"\btransactions with related parties\b")),
        ("Customer concentration", (r"\bcustomer concentration\b", r"\bmajor customers?\b", r"\bsignificant customers?\b")),
        ("Controlled company", (r"\bcontrolled company\b",)),
        ("China/VIE structure", (r"\bvariable interest entity\b", r"\bVIEs?\b", r"\bPRC\b", r"\bchina[- ]based\b")),
        ("SPAC-related", (r"\bspecial purpose acquisition company\b", r"\bSPAC\b")),
        ("Auditor change", (r"\bauditor change\b", r"\bchange in auditor\b", r"\bchanged auditors\b", r"\bauditor resignation\b")),
    ]
    flags: list[str] = []
    for flag, patterns in checks:
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            flags.append(flag)
    return unique_flags(flags)


def _has_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text or "", flags=re.IGNORECASE) for pattern in patterns)


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
        dilution_per_share=merged.get("dilution_per_share"),
        going_concern=merged.get("going_concern"),
        customer_concentration=merged.get("customer_concentration"),
        related_party_transactions=merged.get("related_party_transactions"),
        controlled_company=merged.get("controlled_company"),
        vie_or_china_risk=merged.get("vie_or_china_risk"),
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
        selected = _best_prospectus_filing(filings)
        if selected is not None:
            candidates.append(selected)
    return sorted(candidates, key=lambda filing: _date_sort_key(filing.filing_date), reverse=True)


def _best_prospectus_filing(
    filings: Iterable[SecCurrentFiling],
    *,
    reference_date: str | None = None,
) -> SecCurrentFiling | None:
    parseable = [filing for filing in filings if filing.form.strip().upper() in PROSPECTUS_SOURCE_FORMS]
    if reference_date:
        dated = [filing for filing in parseable if _date_sort_key(filing.filing_date) <= _date_sort_key(reference_date)]
        if dated:
            parseable = dated
    if not parseable:
        return None
    return sorted(parseable, key=_prospectus_filing_rank)[0]


def _prospectus_filing_rank(filing: SecCurrentFiling) -> tuple[int, str, str]:
    form = filing.form.strip().upper()
    if form in FINAL_PROSPECTUS_FORMS:
        form_score = 0
    elif form in AMENDMENT_FORMS:
        form_score = 1
    elif form in REGISTRATION_FORMS:
        form_score = 2
    else:
        form_score = 9
    return form_score, _reverse_date_sort_key(filing.filing_date), filing.accession_number


def _company_names_by_cik(filings: Iterable[SecCurrentFiling]) -> dict[str, str]:
    names: dict[str, str] = {}
    for filing in filings:
        names.setdefault(normalize_cik(filing.cik), filing.company_name)
    return names


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


def _safe_cache_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(value).strip())
    return safe.strip("._") or "_"


def _collapse_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _offering_section_text(text: str) -> str:
    section = _section_between_headings(
        text,
        ("The Offering", "Offering", "Initial Public Offering"),
        (
            "Risk Factors",
            "Use of Proceeds",
            "Dividend Policy",
            "Capitalization",
            "Dilution",
            "Management",
            "Underwriting",
            "Plan of Distribution",
            "Business",
        ),
    )
    if _looks_like_real_offering_section(section):
        return section

    match = re.search(
        r"\bwe\s+are\s+offering\b.{0,900}?\b(?:initial\s+public\s+offering\s+price|public\s+offering\s+price|price\s+range|between\s+\$)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        start = max(0, match.start() - 120)
        return text[start : match.end() + 600]
    return ""


def _section_between_headings(text: str, headings: tuple[str, ...], stop_headings: tuple[str, ...]) -> str:
    lower = text.lower()
    starts: list[tuple[int, int]] = []
    for heading in headings:
        for match in re.finditer(rf"\b{re.escape(heading.lower())}\b", lower):
            starts.append((match.start(), match.end()))
    if not starts:
        return ""
    for start, content_start in sorted(starts, key=lambda item: item[0]):
        if _looks_like_toc_window(text[max(0, start - 120) : start + 240]):
            continue
        stop_index = len(text)
        for stop in stop_headings:
            stop_match = re.search(rf"\b{re.escape(stop.lower())}\b", lower[content_start + 120 :])
            if stop_match:
                stop_index = min(stop_index, content_start + 120 + stop_match.start())
        section = text[content_start:stop_index].strip()
        if section:
            return section[:8000]
    return ""


def _looks_like_real_offering_section(section: str) -> bool:
    lower = section.lower()
    return bool(section) and any(term in lower for term in ("we are offering", "shares offered", "offering price", "price range"))


def _looks_like_toc_window(value: str) -> bool:
    lower = value.lower()
    return "table of contents" in lower or len(re.findall(r"\b[A-Z][A-Z ]{3,}\s+\d{1,3}\b", value)) >= 3


def _looks_like_table_or_boilerplate(value: str) -> bool:
    lower = value.lower()
    if "table of contents" in lower or "indicate by check mark" in lower:
        return True
    if len(re.findall(r"\b[A-Z][A-Z '&/-]{3,}\s+\d{1,3}\b", value)) >= 3:
        return True
    words = re.findall(r"[A-Za-z]+", value)
    return bool(words) and sum(1 for word in words if len(word) <= 2) / max(len(words), 1) > 0.45


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
    range_patterns = (
        r"between\s+\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s+and\s+\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
        r"price\s+range\s+(?:of\s+)?\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:-|to|and)\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
        r"estimated\s+(?:initial\s+)?public\s+offering\s+price\s+(?:range\s+)?(?:of\s+)?\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:-|to|and)\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)",
    )
    for pattern in range_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            low = float(match.group(1).replace(",", ""))
            high = float(match.group(2).replace(",", ""))
            return (low, high) if _reasonable_ipo_price_range(low, high) else (None, None)
    match = re.search(r"(?:public offering price|initial public offering price)\s+(?:of|is)\s+\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)", text, flags=re.IGNORECASE)
    if match:
        value = float(match.group(1).replace(",", ""))
        return (value, value) if _reasonable_ipo_price_range(value, value) else (None, None)
    return None, None


def _reasonable_ipo_price_range(low: float, high: float) -> bool:
    if low <= 0 or high <= 0 or high < low:
        return False
    if high > 500:
        return False
    if low < 0.01:
        return False
    if high / low > 5:
        return False
    return True


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


def _extract_financial_row_value(text: str, labels: tuple[str, ...], default_multiplier: float) -> float | None:
    for label in labels:
        match = re.search(
            rf"(?<![A-Za-z0-9]){label}(?![A-Za-z0-9])"
            rf"[^$\d(]{{0,80}}"
            rf"(\(?\$?\s*-?[0-9][0-9,.]*(?:\s*(?:billion|million|thousand|B|M|K))?\)?)"
            rf"(?:\s+\(?\$?\s*-?[0-9][0-9,.]*(?:\s*(?:billion|million|thousand|B|M|K))?\)?)?",
            text,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        value = _parse_money_amount(match.group(1), default_multiplier=default_multiplier)
        if value is None:
            continue
        if "loss" in label.lower() or (label.lower() == "net income" and _near_term(text, "net loss", value)):
            value = -abs(value)
        return value
    return None


def _parse_money_amount(value: str | None, *, default_multiplier: float = 1) -> float | None:
    if value is None:
        return None
    raw = value.strip()
    negative = raw.startswith("-") or (raw.startswith("(") and raw.endswith(")"))
    clean = raw.strip("()").replace("$", "").replace(",", "").replace(" ", "")
    multiplier = default_multiplier
    lower = clean.lower()
    for suffix, scale in (("billion", 1_000_000_000), ("million", 1_000_000), ("thousand", 1_000), ("b", 1_000_000_000), ("m", 1_000_000), ("k", 1_000)):
        if lower.endswith(suffix):
            multiplier = scale
            clean = clean[: -len(suffix)]
            break
    try:
        parsed = float(clean) * multiplier
    except ValueError:
        return None
    return -abs(parsed) if negative else parsed


def _table_amount_multiplier(text: str) -> float:
    head = text[:12000].lower()
    if "in thousands" in head or "$ in thousands" in head:
        return 1_000
    if "in millions" in head or "$ in millions" in head:
        return 1_000_000
    return 1


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
    match = re.search(
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|thousand)?\s+(?:ordinary\s+shares|common\s+stock|shares)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    shares = float(match.group(1).replace(",", ""))
    scale = (match.group(2) or "").lower()
    if scale == "million":
        shares *= 1_000_000
    elif scale == "thousand":
        shares *= 1_000
    if shares <= 0 or shares > 1_000_000_000:
        return None
    return shares


def _extract_per_share_value_near(text: str, terms: tuple[str, ...]) -> float | None:
    lower = text.lower()
    for term in terms:
        index = lower.find(term)
        if index < 0:
            continue
        window = text[index : index + 520]
        dollar_values = [float(value) for value in re.findall(r"\$\s*([0-9]+(?:\.[0-9]+)?)", window) if 0 <= float(value) < 200]
        if dollar_values and "dilution" in term:
            return dollar_values[0]
        values = [float(value) for value in re.findall(r"\$?\s*([0-9]+(?:\.[0-9]+)?)", window) if 0 <= float(value) < 200]
        if values:
            return values[-1] if "dilution" in term else values[0]
    return None


def _extract_section_snippet(text: str, heading: str) -> str | None:
    section = _section_between_headings(
        text,
        (heading,),
        (
            "Dividend Policy",
            "Capitalization",
            "Dilution",
            "Management",
            "Underwriting",
            "Plan of Distribution",
            "Risk Factors",
            "Business",
        ),
    )
    if not section or _looks_like_table_or_boilerplate(section):
        return None
    snippet = f"{heading.title()} {section[:420]}".strip()
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
