from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any, Iterable, Literal, Mapping

from app.data.sec_edgar import DEFAULT_CACHE_TTL, SecCompany, SecEdgarClient, SecFiling, normalize_ticker

CapitalPressureSeverity = Literal["low", "medium", "high"]
CapitalPressureRead = Literal["Low", "Moderate", "High", "Unknown"]
DilutionSensitivity = Literal["Low", "Moderate", "High", "Unknown"]

CAPITAL_STRUCTURE_FORMS: tuple[str, ...] = (
    "S-1",
    "S-1/A",
    "S-3",
    "S-3/A",
    "F-1",
    "F-1/A",
    "F-3",
    "F-3/A",
    "424B3",
    "424B4",
    "424B5",
    "424B7",
    "8-K",
    "6-K",
    "10-K",
    "10-Q",
    "20-F",
    "DEF 14A",
    "PRE 14A",
    "SC 13D",
    "SC 13G",
)
MAX_FILING_TEXT_CHARS = 450_000


@dataclass(frozen=True)
class CapitalStructureSignal:
    label: str
    severity: CapitalPressureSeverity
    score: int
    source_form: str
    source_date: str
    source_url: str
    excerpt: str
    explanation: str


@dataclass(frozen=True)
class CapitalStructureLevel:
    label: str
    price: float
    source: str
    explanation: str
    level_type: str


@dataclass(frozen=True)
class CapitalStructureCommonShareClass:
    instrument_type: str
    class_name: str | None
    shares: int | None
    voting_language: str | None
    conversion_language: str | None
    redemption_language: str | None
    resale_language: str | None
    source_form: str
    source_date: str
    source_url: str
    excerpt: str


@dataclass(frozen=True)
class CapitalStructurePreferredSeries:
    instrument_type: str
    series_name: str | None
    shares: int | None
    underlying_shares: int | None
    conversion_price: float | None
    conversion_rate: str | None
    liquidation_preference: float | None
    voting_language: str | None
    conversion_language: str | None
    redemption_language: str | None
    resale_language: str | None
    source_form: str
    source_date: str
    source_url: str
    excerpt: str


@dataclass(frozen=True)
class CapitalStructureWarrant:
    instrument_type: str
    series_name: str | None
    underlying_shares: int | None
    exercise_price: float | None
    expiration_date: str | None
    cashless_exercise_language: str | None
    redemption_language: str | None
    resale_language: str | None
    source_form: str
    source_date: str
    source_url: str
    excerpt: str


@dataclass(frozen=True)
class CapitalStructureConvertibleInstrument:
    instrument_type: str
    series_name: str | None
    principal_amount: float | None
    underlying_shares: int | None
    conversion_price: float | None
    conversion_rate: str | None
    maturity_date: str | None
    coupon_rate: str | None
    conversion_language: str | None
    redemption_language: str | None
    resale_language: str | None
    source_form: str
    source_date: str
    source_url: str
    excerpt: str


@dataclass(frozen=True)
class CapitalStructureOfferingProgram:
    instrument_type: str
    program_type: str
    program_name: str | None
    shares: int | None
    underlying_shares: int | None
    offering_price: float | None
    amount: float | None
    resale_language: str | None
    source_form: str
    source_date: str
    source_url: str
    excerpt: str


@dataclass(frozen=True)
class CapitalStructureAdsAdrStructure:
    instrument_type: str
    structure_name: str | None
    ratio: str | None
    ordinary_share_class: str | None
    voting_language: str | None
    conversion_language: str | None
    source_form: str
    source_date: str
    source_url: str
    excerpt: str


@dataclass(frozen=True)
class CapitalStructureTermsReport:
    common_share_classes: list[CapitalStructureCommonShareClass] = field(default_factory=list)
    preferred_series: list[CapitalStructurePreferredSeries] = field(default_factory=list)
    warrants: list[CapitalStructureWarrant] = field(default_factory=list)
    convertibles: list[CapitalStructureConvertibleInstrument] = field(default_factory=list)
    offering_programs: list[CapitalStructureOfferingProgram] = field(default_factory=list)
    ads_adr_structures: list[CapitalStructureAdsAdrStructure] = field(default_factory=list)
    technical_impact_lines: list[str] = field(default_factory=list)
    verification_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CapitalStructurePressureReport:
    symbol: str
    company_name: str
    filings_analyzed: int
    read: CapitalPressureRead
    supply_overhang_score: int
    dilution_sensitivity: DilutionSensitivity
    breakout_quality_adjustment: str
    confidence: Literal["Low", "Medium", "High"]
    signals: list[CapitalStructureSignal]
    warnings: list[str]
    explanation_lines: list[str]
    what_would_change: list[str]
    possible_supply_levels: list[CapitalStructureLevel] = field(default_factory=list)
    parsed_terms: CapitalStructureTermsReport = field(default_factory=CapitalStructureTermsReport)
    source_label: str = "SEC-only fallback"
    source_diagnostics: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapitalStructureFilingText:
    form: str
    filing_date: str
    source_url: str
    text: str


@dataclass(frozen=True)
class _SignalGroup:
    label: str
    severity: CapitalPressureSeverity
    score: int
    keywords: tuple[str, ...]
    explanation: str


SIGNAL_GROUPS: tuple[_SignalGroup, ...] = (
    _SignalGroup(
        label="Shelf / registration capacity",
        severity="high",
        score=30,
        keywords=(
            "shelf registration",
            "automatic shelf",
            "registration statement",
            "form s-3",
            "prospectus supplement",
            "securities offered",
            "primary offering",
            "resale prospectus",
            "selling stockholders",
            "selling shareholders",
            "at-the-market",
            "atm program",
            "equity distribution agreement",
            "sales agreement",
        ),
        explanation="Recent registration, shelf, resale, or ATM language can add supply overhang to technical breakouts.",
    ),
    _SignalGroup(
        label="Preferred stock",
        severity="medium",
        score=18,
        keywords=(
            "preferred stock",
            "series a preferred",
            "series b preferred",
            "series c preferred",
            "convertible preferred",
            "certificate of designation",
            "liquidation preference",
            "redemption",
            "conversion price",
        ),
        explanation="Preferred-stock terms can create conversion, redemption, or senior capital-structure pressure.",
    ),
    _SignalGroup(
        label="Warrants",
        severity="medium",
        score=22,
        keywords=(
            "warrant",
            "warrants to purchase",
            "exercise price",
            "common warrants",
            "pre-funded warrants",
            "placement agent warrants",
        ),
        explanation="Warrants can become non-chart supply near exercise or resale zones.",
    ),
    _SignalGroup(
        label="Convertibles / notes",
        severity="high",
        score=24,
        keywords=(
            "convertible notes",
            "convertible senior notes",
            "convertible debentures",
            "conversion price",
            "conversion rate",
            "exchangeable notes",
        ),
        explanation="Convertible instruments can make rallies more dilution-sensitive when conversion economics matter.",
    ),
    _SignalGroup(
        label="Share classes / voting control",
        severity="low",
        score=10,
        keywords=(
            "class a common stock",
            "class b common stock",
            "class c common stock",
            "dual class",
            "high vote",
            "voting power",
            "super voting",
            "non-voting common stock",
        ),
        explanation="Share-class or voting-control language can affect float quality, governance risk, and liquidity perception.",
    ),
    _SignalGroup(
        label="ADS / foreign issuer structure",
        severity="low",
        score=12,
        keywords=(
            "american depositary share",
            "american depositary shares",
            "american depositary receipts",
            "foreign private issuer",
            "ads represents",
            "adss",
            "adrs",
        ),
        explanation="ADS, ADR, or foreign-issuer language can require verification against ordinary-share and depositary terms.",
    ),
    _SignalGroup(
        label="Dilution warning",
        severity="medium",
        score=18,
        keywords=(
            "substantial dilution",
            "future dilution",
            "may issue additional shares",
            "additional shares of common stock",
            "anti-dilution",
            "beneficial ownership limitation",
            "fully diluted",
        ),
        explanation="Explicit dilution-risk language lowers the confidence of unconfirmed technical strength.",
    ),
    _SignalGroup(
        label="Reverse split / going concern / Nasdaq compliance",
        severity="high",
        score=22,
        keywords=(
            "reverse stock split",
            "going concern",
            "minimum bid price",
            "nasdaq compliance",
            "stockholders' equity requirement",
            "stockholders equity requirement",
        ),
        explanation="Going-concern, reverse-split, or listing-compliance language can increase rally-fade and gap-risk sensitivity.",
    ),
)

_MONEY_PATTERN = r"\$([0-9][0-9,]{0,8}(?:\.[0-9]{1,4})?)"
_MONEY_AMOUNT_PATTERN = r"\$([0-9][0-9,]*(?:\.[0-9]+)?)(?:\s*(million|billion))?"
_QUANTITY_PATTERN = r"([0-9][0-9,]*(?:\.[0-9]+)?)(?:\s*(million|billion))?"
_DATE_TEXT_PATTERN = (
    r"("
    r"\d{4}-\d{2}-\d{2}|"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)"
    r"\s+\d{1,2},\s+\d{4}|"
    r"\d{1,2}/\d{1,2}/\d{2,4}"
    r")"
)
_LEVEL_PATTERNS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "Possible warrant strike",
        "warrant_strike",
        rf"(?:exercise price|exercisable at|warrants?[^.{{}}]{{0,140}}?exercise price)[^.{{}}]{{0,80}}?{_MONEY_PATTERN}",
        ("warrant", "warrants", "exercise price", "exercisable"),
    ),
    (
        "Possible conversion price",
        "conversion_price",
        rf"conversion price[^.{{}}]{{0,80}}?{_MONEY_PATTERN}",
        ("convert", "preferred", "note", "debenture"),
    ),
    (
        "Possible offering price",
        "offering_price",
        rf"(?:offering price|public offering price)[^.{{}}]{{0,80}}?{_MONEY_PATTERN}",
        ("offering", "prospectus", "securities"),
    ),
    (
        "Possible purchase/resale price",
        "resale_price",
        rf"(?:purchase price|resale price)[^.{{}}]{{0,80}}?{_MONEY_PATTERN}",
        ("purchase price", "resale", "selling stockholder", "selling shareholder"),
    ),
)


def analyze_capital_structure_pressure(
    symbol: str,
    *,
    client: SecEdgarClient | None = None,
    fmp_provider: Any | None = None,
    fmp_profile: Mapping[str, Any] | None = None,
    fmp_filing_metadata: Iterable[Mapping[str, Any]] | None = None,
    filing_limit: int = 24,
    max_documents: int = 8,
    as_of: date | None = None,
) -> CapitalStructurePressureReport:
    clean_symbol = _clean_symbol(symbol)
    active_client = client or SecEdgarClient(timeout_seconds=10)
    warnings: list[str] = []
    source_label = "SEC-only fallback"
    source_diagnostics: dict[str, Any] = {
        "sec_recent_filings_requested": 0,
        "sec_filing_text_documents_fetched": 0,
        "fmp_metadata_rows": 0,
        "fmp_metadata_used": False,
    }

    fmp_filings = _fmp_prefilter_filings(
        clean_symbol,
        fmp_profile=fmp_profile,
        fmp_filing_metadata=fmp_filing_metadata,
        fmp_provider=fmp_provider,
        limit=filing_limit,
    )
    source_diagnostics["fmp_metadata_rows"] = len(fmp_filings)
    if fmp_filings:
        filings = fmp_filings
        source_label = "FMP metadata + SEC source text"
        source_diagnostics["fmp_metadata_used"] = True
        source_diagnostics["sec_recent_filings_skipped"] = True
    else:
        try:
            source_diagnostics["sec_recent_filings_requested"] = 1
            filings = active_client.recent_filings(clean_symbol, forms=CAPITAL_STRUCTURE_FORMS, limit=filing_limit)
        except Exception as exc:
            return unknown_capital_structure_report(
                clean_symbol,
                warnings=[f"Capital structure overlay unavailable: SEC recent filings fetch failed: {exc}"],
                source_label=source_label,
                source_diagnostics=source_diagnostics,
            )

    if not filings:
        return unknown_capital_structure_report(
            clean_symbol,
            warnings=["Capital structure overlay unavailable: no recent capital-structure SEC filing forms were found."],
            source_label=source_label,
            source_diagnostics=source_diagnostics,
        )

    filing_texts: list[CapitalStructureFilingText] = []
    for filing in filings[: max(1, max_documents)]:
        try:
            text = fetch_primary_filing_text(active_client, filing)
            source_diagnostics["sec_filing_text_documents_fetched"] += 1
        except Exception as exc:
            warnings.append(f"{filing.form} filed {filing.filing_date or '--'} text fetch failed: {exc}")
            continue
        if not text.strip():
            warnings.append(f"{filing.form} filed {filing.filing_date or '--'} had no readable filing text.")
            continue
        filing_texts.append(
            CapitalStructureFilingText(
                form=filing.form,
                filing_date=filing.filing_date,
                source_url=filing.filing_url,
                text=text[:MAX_FILING_TEXT_CHARS],
            )
        )

    company_name = filings[0].company.title if filings else clean_symbol
    if not filing_texts:
        return unknown_capital_structure_report(
            clean_symbol,
            company_name=company_name,
            warnings=warnings or ["Capital structure overlay unavailable: filing text could not be loaded."],
            source_label=source_label,
            source_diagnostics=source_diagnostics,
        )

    report = scan_capital_structure_filings(
        clean_symbol,
        company_name=company_name,
        filings=filing_texts,
        warnings=warnings,
        as_of=as_of,
    )
    return replace(report, source_label=source_label, source_diagnostics=source_diagnostics)


def _fmp_prefilter_filings(
    symbol: str,
    *,
    fmp_profile: Mapping[str, Any] | None,
    fmp_filing_metadata: Iterable[Mapping[str, Any]] | None,
    fmp_provider: Any | None,
    limit: int,
) -> list[SecFiling]:
    metadata_rows = list(fmp_filing_metadata or ())
    if not metadata_rows and fmp_provider is not None:
        metadata_rows = _fmp_provider_filing_metadata(symbol, fmp_provider, limit=limit)
    company = _sec_company_from_fmp(symbol, fmp_profile, metadata_rows)
    if company is None:
        return []
    filings: list[SecFiling] = []
    seen: set[tuple[str, str]] = set()
    for row in metadata_rows:
        if not isinstance(row, Mapping):
            continue
        filing = _sec_filing_from_fmp_metadata(company, row)
        if filing is None:
            continue
        if filing.form not in CAPITAL_STRUCTURE_FORMS:
            continue
        key = (filing.accession_number, filing.primary_document)
        if key in seen:
            continue
        seen.add(key)
        filings.append(filing)
        if len(filings) >= max(1, limit):
            break
    return filings


def _fmp_provider_filing_metadata(symbol: str, provider: Any, *, limit: int) -> list[Mapping[str, Any]]:
    for method_name in ("filing_metadata", "sec_filings", "filings"):
        method = getattr(provider, method_name, None)
        if not callable(method):
            continue
        try:
            payload = method(symbol, limit=limit)
        except TypeError:
            try:
                payload = method([symbol], max_symbols=1)
            except TypeError:
                payload = method(symbol)
        except Exception:
            return []
        return _coerce_metadata_rows(payload)
    return []


def _coerce_metadata_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, Mapping):
        for key in ("filings", "records", "data", "results"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, Mapping)]
        return [payload]
    if isinstance(payload, (list, tuple)):
        return [row for row in payload if isinstance(row, Mapping)]
    return []


def _sec_company_from_fmp(
    symbol: str,
    profile: Mapping[str, Any] | None,
    metadata_rows: list[Mapping[str, Any]],
) -> SecCompany | None:
    profile = profile or {}
    cik = _normalize_cik_value(_first_metadata_value(profile, "cik", "CIK", "cik_str"))
    company_name = str(_first_metadata_value(profile, "company_name", "companyName", "name") or "").strip()
    for row in metadata_rows:
        cik = cik or _normalize_cik_value(_first_metadata_value(row, "cik", "CIK", "cik_str"))
        company_name = company_name or str(_first_metadata_value(row, "companyName", "company_name", "name") or "").strip()
        if cik and company_name:
            break
    if not cik:
        return None
    return SecCompany(ticker=_clean_symbol(symbol), cik=cik, title=company_name or _clean_symbol(symbol))


def _sec_filing_from_fmp_metadata(company: SecCompany, row: Mapping[str, Any]) -> SecFiling | None:
    form = str(_first_metadata_value(row, "form", "type", "filingType") or "").strip().upper()
    if not form:
        return None
    filing_url = str(_first_metadata_value(row, "filing_url", "finalLink", "link", "url", "reportUrl") or "").strip()
    accession = str(_first_metadata_value(row, "accession_number", "accessionNumber", "accessionNo", "accession") or "").strip()
    if not accession:
        accession = _parse_accession_from_url(filing_url)
    accession = _format_accession_number(accession)
    primary_document = str(_first_metadata_value(row, "primary_document", "primaryDocument", "document") or "").strip()
    if not primary_document:
        primary_document = filing_url.rstrip("/").rsplit("/", 1)[-1] if filing_url else ""
    if not accession or not primary_document:
        return None
    return SecFiling(
        company=company,
        accession_number=accession,
        filing_date=str(_first_metadata_value(row, "filing_date", "fillingDate", "filedDate", "date", "acceptedDate") or ""),
        report_date=str(_first_metadata_value(row, "report_date", "reportDate", "periodOfReport") or ""),
        form=form,
        primary_document=primary_document,
        description=str(_first_metadata_value(row, "description", "title") or "FMP filing metadata prefilter"),
        items=str(_first_metadata_value(row, "items", "item") or ""),
    )


def _first_metadata_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_cik_value(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(10) if digits else ""


def _parse_accession_from_url(url: str) -> str:
    match = re.search(r"(\d{10})[-/]?(\d{2})[-/]?(\d{6})", str(url or ""))
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _format_accession_number(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 18:
        return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"
    return str(value or "").strip()


def fetch_primary_filing_text(client: SecEdgarClient, filing: SecFiling) -> str:
    document_name = filing.primary_document or "primary-document"
    return client.document_text_url(
        filing.filing_url,
        cache_name=f"capital_structure_{filing.company.cik}_{filing.accession_no_dashes}_{document_name}.txt",
        ttl=DEFAULT_CACHE_TTL,
    )


def scan_capital_structure_filings(
    symbol: str,
    *,
    company_name: str = "",
    filings: Iterable[CapitalStructureFilingText],
    warnings: Iterable[str] | None = None,
    as_of: date | None = None,
) -> CapitalStructurePressureReport:
    clean_symbol = _clean_symbol(symbol)
    filings_list = list(filings)
    if not filings_list:
        return unknown_capital_structure_report(
            clean_symbol,
            company_name=company_name or clean_symbol,
            warnings=list(warnings or []) or ["Capital structure overlay unavailable: no filing text was available."],
        )

    effective_as_of = as_of or datetime.now(timezone.utc).date()
    signals: list[CapitalStructureSignal] = []
    possible_levels: list[CapitalStructureLevel] = []
    for filing in filings_list:
        collapsed = _collapse_text(filing.text[:MAX_FILING_TEXT_CHARS])
        if not collapsed:
            continue
        normalized = _normalise_for_scan(collapsed)
        for group in SIGNAL_GROUPS:
            matched = _matched_keywords(normalized, group.keywords)
            if not matched:
                continue
            score = _recency_adjusted_score(group.score, filing.filing_date, effective_as_of)
            severity = _severity_for_signal_score(score, group.severity)
            first_keyword = matched[0]
            signals.append(
                CapitalStructureSignal(
                    label=group.label,
                    severity=severity,
                    score=score,
                    source_form=filing.form,
                    source_date=filing.filing_date,
                    source_url=filing.source_url,
                    excerpt=_excerpt_around(collapsed, first_keyword),
                    explanation=f"{group.explanation} Matched phrase: {first_keyword}.",
                )
            )
        possible_levels.extend(_extract_price_levels(collapsed, filing))

    parsed_terms = parse_capital_structure_terms(filings_list)
    possible_levels.extend(_levels_from_parsed_terms(parsed_terms))
    report_warnings = _dedupe_text(list(warnings or []) + parsed_terms.verification_warnings)
    deduped_signals = _dedupe_signals(signals)
    score = _aggregate_supply_score(deduped_signals)
    if not deduped_signals:
        return CapitalStructurePressureReport(
            symbol=clean_symbol,
            company_name=company_name or clean_symbol,
            filings_analyzed=len(filings_list),
            read="Low",
            supply_overhang_score=0,
            dilution_sensitivity="Low",
            breakout_quality_adjustment="Clean",
            confidence=_confidence_for_scan(len(filings_list), has_signals=False),
            signals=[],
            warnings=report_warnings,
            explanation_lines=[
                "No material recent capital-structure overhang signal was detected in scanned filings.",
                "This is a filing-derived risk overlay, not a price prediction.",
            ],
            what_would_change=_what_would_change("Low"),
            possible_supply_levels=_dedupe_levels(possible_levels),
            parsed_terms=parsed_terms,
        )

    read = classify_capital_pressure_score(score)
    return CapitalStructurePressureReport(
        symbol=clean_symbol,
        company_name=company_name or clean_symbol,
        filings_analyzed=len(filings_list),
        read=read,
        supply_overhang_score=score,
        dilution_sensitivity=_dilution_sensitivity(read),
        breakout_quality_adjustment=_breakout_adjustment(score),
        confidence=_confidence_for_scan(len(filings_list), has_signals=True),
        signals=deduped_signals,
        warnings=report_warnings,
        explanation_lines=_explanation_lines(read, score, deduped_signals, parsed_terms=parsed_terms),
        what_would_change=_what_would_change(read),
        possible_supply_levels=_dedupe_levels(possible_levels),
        parsed_terms=parsed_terms,
    )


def unknown_capital_structure_report(
    symbol: str,
    *,
    company_name: str = "",
    warnings: Iterable[str] | None = None,
    source_label: str = "SEC-only fallback",
    source_diagnostics: Mapping[str, Any] | None = None,
) -> CapitalStructurePressureReport:
    clean_symbol = _clean_symbol(symbol)
    report_warnings = list(warnings or [])
    return CapitalStructurePressureReport(
        symbol=clean_symbol,
        company_name=company_name or clean_symbol,
        filings_analyzed=0,
        read="Unknown",
        supply_overhang_score=0,
        dilution_sensitivity="Unknown",
        breakout_quality_adjustment="Needs confirmation",
        confidence="Low",
        signals=[],
        warnings=report_warnings,
        explanation_lines=[
            "Capital structure overlay unavailable; technical analysis can still be used without this filing-derived modifier.",
            "This is a filing-derived risk overlay, not a price prediction.",
        ],
        what_would_change=[
            "SEC recent filings and primary filing text load successfully.",
            "A later scan finds recent registration, warrant, convertible, preferred, or dilution language.",
        ],
        source_label=source_label,
        source_diagnostics=dict(source_diagnostics or {}),
    )


def classify_capital_pressure_score(score: int | float) -> CapitalPressureRead:
    bounded = _clamp_int(score)
    if bounded <= 24:
        return "Low"
    if bounded <= 54:
        return "Moderate"
    return "High"


def capital_structure_technical_modifier(technical_read: str, report: CapitalStructurePressureReport | None) -> str:
    if report is None or report.read == "Unknown":
        return "Capital structure overlay unavailable; do not adjust technical confidence from filings."
    read = (technical_read or "").strip().lower()
    if "bullish" in read:
        if report.read == "Low":
            return "Bullish technical read has cleaner follow-through conditions."
        if report.read == "High":
            return "Bullish technical read is supply-fragile; require stronger volume/VWAP confirmation."
        return "Bullish technical read needs confirmation because filing-derived supply risk is present."
    if "bearish" in read:
        if report.read == "High":
            return "Bearish technical read is reinforced by capital-structure risk."
        return "Bearish technical read is not materially changed by the filing overlay."
    if report.read == "High":
        return "Mixed chart plus overhang favors waiting for confirmation."
    if report.read == "Low":
        return "Mixed technical read has no material recent filing overhang detected."
    return "Mixed technical read needs confirmation because filing-derived supply risk is present."


def format_capital_structure_pressure_section(
    report: CapitalStructurePressureReport,
    *,
    technical_read: str = "",
) -> list[str]:
    lines = [
        "",
        "CAPITAL STRUCTURE PRESSURE",
        "--------------------------",
        f"- Read: {report.read}.",
        f"- Supply overhang score: {report.supply_overhang_score}/100.",
        f"- Dilution sensitivity: {report.dilution_sensitivity}.",
        f"- Breakout quality adjustment: {report.breakout_quality_adjustment}.",
        f"- Confidence: {report.confidence}.",
        f"- Filings analyzed: {report.filings_analyzed}.",
        f"- Source route: {report.source_label}.",
        f"- Technical confidence modifier: {capital_structure_technical_modifier(technical_read, report)}",
        "- This is a filing-derived risk overlay, not a price prediction.",
    ]
    if report.read == "Unknown":
        reason = "; ".join(report.warnings) if report.warnings else "SEC filing text was unavailable."
        lines.append(f"- Capital structure overlay unavailable: {reason}")
        return lines

    lines.extend(["", "Why"])
    lines.extend(f"- {line}" for line in report.explanation_lines)

    lines.extend(["", "Key signals"])
    if report.signals:
        for signal in sorted(report.signals, key=lambda item: item.score, reverse=True)[:8]:
            lines.append(
                f"- {signal.label}: {signal.severity} ({signal.score}/100) from {signal.source_form} "
                f"filed {signal.source_date or '--'}; {signal.explanation}"
            )
    else:
        lines.append("- No material recent overhang signal was detected in scanned filings.")

    impact_lines = _trading_impact_lines(report)
    lines.extend(["", "Trading impact"])
    lines.extend(f"- {line}" for line in impact_lines)

    lines.extend(["", "Possible supply levels"])
    if report.possible_supply_levels:
        for level in report.possible_supply_levels[:8]:
            lines.append(f"- {level.label}: ${level.price:,.4g} from {level.source}; {level.explanation}")
    else:
        lines.append("- No conservative warrant, conversion, offering, or resale price level was extracted.")

    lines.extend(["", "Parsed filing terms"])
    parsed_rows = _format_parsed_terms_summary_rows(report.parsed_terms)
    if parsed_rows:
        lines.extend(f"- {row}" for row in parsed_rows[:10])
    else:
        lines.append("- No source-backed security terms were parsed from scanned filings.")

    if report.warnings:
        lines.extend(["", "Overlay data notes"])
        lines.extend(f"- {warning}" for warning in report.warnings)
    if report.source_diagnostics:
        lines.extend(["", "Source diagnostics"])
        for key, value in report.source_diagnostics.items():
            if value in (None, "", (), [], {}):
                continue
            lines.append(f"- {key}: {value}")

    lines.extend(["", "What would change"])
    lines.extend(f"- {line}" for line in report.what_would_change)
    return lines


def _matched_keywords(normalized_text: str, keywords: tuple[str, ...]) -> list[str]:
    matches: list[str] = []
    for keyword in keywords:
        if re.search(_keyword_pattern(keyword), normalized_text):
            matches.append(keyword)
    return matches


def _keyword_pattern(keyword: str) -> str:
    escaped = re.escape(_normalise_for_scan(keyword)).replace(r"\ ", r"\s+")
    return rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"


def _excerpt_around(collapsed_text: str, keyword: str, *, radius: int = 150) -> str:
    normalized = _normalise_for_scan(collapsed_text)
    match = re.search(_keyword_pattern(keyword), normalized)
    if not match:
        return collapsed_text[: radius * 2].strip()
    start = max(0, match.start() - radius)
    end = min(len(collapsed_text), match.end() + radius)
    return collapsed_text[start:end].strip()


def _extract_price_levels(text: str, filing: CapitalStructureFilingText) -> list[CapitalStructureLevel]:
    collapsed = _collapse_text(text)
    normalized = _normalise_for_scan(collapsed)
    levels: list[CapitalStructureLevel] = []
    for label, level_type, pattern, context_terms in _LEVEL_PATTERNS:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            price = _parse_price(match.group(1))
            if price is None:
                continue
            context = normalized[max(0, match.start() - 180) : min(len(normalized), match.end() + 180)]
            if not any(term in context for term in context_terms):
                continue
            levels.append(
                CapitalStructureLevel(
                    label=label,
                    price=price,
                    source=f"{filing.form} filed {filing.filing_date or '--'}",
                    explanation="Extracted from a nearby filing phrase; treat as a possible non-chart supply/resistance zone.",
                    level_type=level_type,
                )
            )
    return levels


def parse_capital_structure_terms(filings: Iterable[CapitalStructureFilingText]) -> CapitalStructureTermsReport:
    common_share_classes: list[CapitalStructureCommonShareClass] = []
    preferred_series: list[CapitalStructurePreferredSeries] = []
    warrants: list[CapitalStructureWarrant] = []
    convertibles: list[CapitalStructureConvertibleInstrument] = []
    offering_programs: list[CapitalStructureOfferingProgram] = []
    ads_adr_structures: list[CapitalStructureAdsAdrStructure] = []

    for filing in filings:
        collapsed = _collapse_text(filing.text[:MAX_FILING_TEXT_CHARS])
        if not collapsed:
            continue
        common_share_classes.extend(_parse_common_share_classes(collapsed, filing))
        preferred_series.extend(_parse_preferred_series(collapsed, filing))
        warrants.extend(_parse_warrants(collapsed, filing))
        convertibles.extend(_parse_convertibles(collapsed, filing))
        offering_programs.extend(_parse_offering_programs(collapsed, filing))
        ads_adr_structures.extend(_parse_ads_adr_structures(collapsed, filing))

    common_share_classes = _dedupe_by(
        common_share_classes,
        lambda item: (item.class_name or "", item.source_url, item.shares, item.voting_language or ""),
    )
    preferred_series = _dedupe_by(
        preferred_series,
        lambda item: (
            item.series_name or "",
            item.source_url,
            item.shares,
            item.underlying_shares,
            _rounded_optional(item.conversion_price),
            _rounded_optional(item.liquidation_preference),
        ),
    )
    warrants = _dedupe_by(
        warrants,
        lambda item: (
            item.instrument_type,
            item.series_name or "",
            item.source_url,
            item.underlying_shares,
            _rounded_optional(item.exercise_price),
            item.expiration_date or "",
        ),
    )
    convertibles = _dedupe_by(
        convertibles,
        lambda item: (
            item.instrument_type,
            item.series_name or "",
            item.source_url,
            _rounded_optional(item.principal_amount),
            _rounded_optional(item.conversion_price),
            item.conversion_rate or "",
            item.maturity_date or "",
        ),
    )
    offering_programs = _dedupe_by(
        offering_programs,
        lambda item: (
            item.program_type,
            item.program_name or "",
            item.source_url,
            item.shares,
            _rounded_optional(item.offering_price),
            _rounded_optional(item.amount),
        ),
    )
    ads_adr_structures = _dedupe_by(
        ads_adr_structures,
        lambda item: (item.instrument_type, item.structure_name or "", item.source_url, item.ratio or ""),
    )

    base_report = CapitalStructureTermsReport(
        common_share_classes=common_share_classes,
        preferred_series=preferred_series,
        warrants=warrants,
        convertibles=convertibles,
        offering_programs=offering_programs,
        ads_adr_structures=ads_adr_structures,
    )
    return CapitalStructureTermsReport(
        common_share_classes=base_report.common_share_classes,
        preferred_series=base_report.preferred_series,
        warrants=base_report.warrants,
        convertibles=base_report.convertibles,
        offering_programs=base_report.offering_programs,
        ads_adr_structures=base_report.ads_adr_structures,
        technical_impact_lines=_technical_impact_lines_from_terms(base_report),
        verification_warnings=_verification_warnings_from_terms(base_report),
    )


def _parse_common_share_classes(text: str, filing: CapitalStructureFilingText) -> list[CapitalStructureCommonShareClass]:
    normalized = _normalise_for_scan(text)
    patterns = (
        r"\bclass\s+[a-z]\s+common stock\b",
        r"\bnon-voting common stock\b",
        r"\bhigh[- ]vote common stock\b",
        r"\bsuper[- ]voting common stock\b",
    )
    terms: list[CapitalStructureCommonShareClass] = []
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            if _is_conversion_target_reference(normalized, match.start()):
                continue
            raw_name = _clean_label(match.group(0))
            excerpt = _source_excerpt(_bounded_window_from_span(text, match.start(), match.end(), radius=650))
            terms.append(
                CapitalStructureCommonShareClass(
                    instrument_type="common share class",
                    class_name=raw_name,
                    shares=_extract_labeled_share_count(excerpt, raw_name),
                    voting_language=_extract_language_excerpt(
                        excerpt,
                        ("vote", "voting", "non-voting", "high vote", "high-vote", "super voting", "super-voting"),
                    ),
                    conversion_language=_extract_language_excerpt(excerpt, ("convertible", "convert", "conversion", "exchangeable", "exchange")),
                    redemption_language=_extract_language_excerpt(excerpt, ("redeem", "redemption")),
                    resale_language=_extract_language_excerpt(excerpt, ("resale", "selling stockholder", "selling shareholder")),
                    source_form=filing.form,
                    source_date=filing.filing_date,
                    source_url=filing.source_url,
                    excerpt=excerpt,
                )
            )
    return _merge_common_share_classes(terms)


def _parse_preferred_series(text: str, filing: CapitalStructureFilingText) -> list[CapitalStructurePreferredSeries]:
    normalized = _normalise_for_scan(text)
    pattern = r"\bseries\s+[a-z0-9][a-z0-9 -]{0,35}\s+(?:convertible\s+)?preferred stock\b"
    terms: list[CapitalStructurePreferredSeries] = []
    for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
        series_name = _clean_label(match.group(0))
        excerpt = _source_excerpt(_window_from_span(text, match.start(), match.end(), radius=850))
        conversion_price = _extract_money_field(
            excerpt,
            (
                rf"conversion price[^.;]{{0,120}}?{_MONEY_PATTERN}",
                rf"{_MONEY_PATTERN}[^.;]{{0,80}}?conversion price",
            ),
        )
        conversion_rate = _extract_text_field(
            excerpt,
            (
                r"conversion rate(?:\s+(?:of|equal to|is))?\s+([0-9][0-9,.]*\s+shares?[^.;]{0,140})",
                r"convertible into\s+([^.;]{1,140}?shares? of common stock)",
            ),
        )
        terms.append(
            CapitalStructurePreferredSeries(
                instrument_type="convertible preferred stock" if conversion_price is not None or conversion_rate or "convertible" in _normalise_for_scan(excerpt) else "preferred stock",
                series_name=series_name,
                shares=_extract_labeled_share_count(excerpt, series_name),
                underlying_shares=_extract_underlying_share_count(excerpt),
                conversion_price=conversion_price,
                conversion_rate=conversion_rate,
                liquidation_preference=_extract_money_field(
                    excerpt,
                    (
                        rf"liquidation preference[^.;]{{0,120}}?{_MONEY_PATTERN}",
                        rf"{_MONEY_PATTERN}[^.;]{{0,80}}?liquidation preference",
                    ),
                ),
                voting_language=_extract_language_excerpt(excerpt, ("vote", "voting", "voting rights", "consent")),
                conversion_language=_extract_language_excerpt(excerpt, ("convert", "conversion", "conversion price", "conversion rate")),
                redemption_language=_extract_language_excerpt(excerpt, ("redeem", "redemption", "redeemable")),
                resale_language=_extract_language_excerpt(excerpt, ("resale", "selling stockholder", "selling shareholder", "registration rights")),
                source_form=filing.form,
                source_date=filing.filing_date,
                source_url=filing.source_url,
                excerpt=excerpt,
            )
        )
    return terms


def _parse_warrants(text: str, filing: CapitalStructureFilingText) -> list[CapitalStructureWarrant]:
    normalized = _normalise_for_scan(text)
    patterns = (
        r"\bpre-funded warrants?\b",
        r"\bcommon warrants?\b",
        r"\bplacement agent warrants?\b",
        r"\bwarrants? to purchase\b",
    )
    terms: list[CapitalStructureWarrant] = []
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            window = _bounded_window_from_span(text, match.start(), match.end(), radius=800)
            excerpt = _source_excerpt(window)
            instrument_type, series_name = _warrant_type_and_name(match.group(0), excerpt)
            terms.append(
                CapitalStructureWarrant(
                    instrument_type=instrument_type,
                    series_name=series_name,
                    underlying_shares=_extract_underlying_share_count(excerpt),
                    exercise_price=_extract_money_field(
                        excerpt,
                        (
                            rf"exercise price[^.;]{{0,120}}?{_MONEY_PATTERN}",
                            rf"exercisable (?:at|for)[^.;]{{0,80}}?{_MONEY_PATTERN}",
                            rf"nominal exercise price[^.;]{{0,120}}?{_MONEY_PATTERN}",
                        ),
                    ),
                    expiration_date=_extract_date_field(
                        excerpt,
                        (
                            rf"(?:expire|expires|expiration date|will expire)[^.;]{{0,120}}?{_DATE_TEXT_PATTERN}",
                            rf"{_DATE_TEXT_PATTERN}[^.;]{{0,80}}?(?:expiration|expire|expires)",
                        ),
                    ),
                    cashless_exercise_language=_extract_language_excerpt(excerpt, ("cashless exercise", "net exercise")),
                    redemption_language=_extract_language_excerpt(excerpt, ("redeem", "redemption", "call the warrants")),
                    resale_language=_extract_language_excerpt(excerpt, ("resale", "selling stockholder", "selling shareholder", "registered for resale")),
                    source_form=filing.form,
                    source_date=filing.filing_date,
                    source_url=filing.source_url,
                    excerpt=excerpt,
                )
            )
    return terms


def _parse_convertibles(text: str, filing: CapitalStructureFilingText) -> list[CapitalStructureConvertibleInstrument]:
    normalized = _normalise_for_scan(text)
    pattern = (
        r"\b(?:[0-9]+(?:\.[0-9]+)?%\s+)?(?:convertible|exchangeable)"
        r"(?:\s+senior|\s+subordinated)?\s+(?:notes?|debentures?)(?:\s+due\s+\d{4})?\b"
    )
    terms: list[CapitalStructureConvertibleInstrument] = []
    for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
        series_name = _clean_label(match.group(0))
        excerpt = _source_excerpt(_window_from_span(text, match.start(), match.end(), radius=900))
        terms.append(
            CapitalStructureConvertibleInstrument(
                instrument_type="convertible debenture" if "debenture" in match.group(0) else "convertible note",
                series_name=series_name,
                principal_amount=_extract_money_amount_field(
                    excerpt,
                    (
                        rf"(?:aggregate\s+)?principal amount\s+(?:of|equal to)\s+{_MONEY_AMOUNT_PATTERN}",
                        rf"{_MONEY_AMOUNT_PATTERN}[^.;]{{0,80}}?aggregate principal amount",
                    ),
                ),
                underlying_shares=_extract_underlying_share_count(excerpt),
                conversion_price=_extract_money_field(
                    excerpt,
                    (
                        rf"conversion price[^.;]{{0,120}}?{_MONEY_PATTERN}",
                        rf"{_MONEY_PATTERN}[^.;]{{0,80}}?conversion price",
                    ),
                ),
                conversion_rate=_extract_text_field(
                    excerpt,
                    (
                        r"conversion rate(?:\s+(?:of|equal to|is))?\s+([0-9][0-9,.]*\s+shares?[^.;]{0,160})",
                        r"convertible into\s+([^.;]{1,160}?shares? of common stock)",
                    ),
                ),
                maturity_date=_extract_date_field(
                    excerpt,
                    (
                        rf"(?:mature|matures|maturity date)[^.;]{{0,120}}?{_DATE_TEXT_PATTERN}",
                        rf"{_DATE_TEXT_PATTERN}[^.;]{{0,80}}?(?:maturity|mature|matures)",
                    ),
                ),
                coupon_rate=_extract_coupon_rate(excerpt, series_name),
                conversion_language=_extract_language_excerpt(excerpt, ("convert", "conversion", "conversion price", "conversion rate", "make-whole")),
                redemption_language=_extract_language_excerpt(excerpt, ("redeem", "redemption", "repurchase")),
                resale_language=_extract_language_excerpt(excerpt, ("resale", "selling stockholder", "selling shareholder", "registration rights")),
                source_form=filing.form,
                source_date=filing.filing_date,
                source_url=filing.source_url,
                excerpt=excerpt,
            )
        )
    return terms


def _parse_offering_programs(text: str, filing: CapitalStructureFilingText) -> list[CapitalStructureOfferingProgram]:
    specs = (
        ("ATM program", "at-the-market offering program", ("at-the-market", "atm program", "equity distribution agreement", "sales agreement")),
        ("Resale prospectus", "resale prospectus", ("resale prospectus", "selling stockholders", "selling shareholders")),
        ("Shelf registration", "shelf registration statement", ("shelf registration", "automatic shelf", "form s-3", "form f-3")),
        ("Offering", "registered offering", ("public offering price", "prospectus supplement", "securities offered", "primary offering")),
    )
    programs: list[CapitalStructureOfferingProgram] = []
    for program_type, program_name, keywords in specs:
        for window in _windows_for_keywords(text, keywords, radius=900):
            excerpt = _source_excerpt(window)
            programs.append(
                CapitalStructureOfferingProgram(
                    instrument_type=_offering_instrument_type(excerpt),
                    program_type=program_type,
                    program_name=_clean_label(program_name),
                    shares=_extract_share_count(
                        excerpt,
                        (
                            rf"(?:up to|maximum of|resale of|sell|offer(?:ing|ed|s)?(?:\s+and\s+sell)?|offering of)\s+{_QUANTITY_PATTERN}\s+shares",
                            rf"selling (?:stockholders|shareholders)[^.;]{{0,120}}?{_QUANTITY_PATTERN}\s+shares",
                            rf"{_QUANTITY_PATTERN}\s+shares[^.;]{{0,120}}?(?:resale|offered|selling stockholders|selling shareholders)",
                        ),
                    ),
                    underlying_shares=_extract_underlying_share_count(excerpt),
                    offering_price=_extract_money_field(
                        excerpt,
                        (
                            rf"(?:public offering price|offering price|purchase price|resale price)[^.;]{{0,120}}?{_MONEY_PATTERN}",
                            rf"{_MONEY_PATTERN}[^.;]{{0,80}}?(?:public offering price|offering price|purchase price|resale price)",
                        ),
                    ),
                    amount=_extract_money_amount_field(
                        excerpt,
                        (
                            rf"(?:up to|maximum of|aggregate offering amount of|sales having an aggregate offering price of)[^.;]{{0,120}}?{_MONEY_AMOUNT_PATTERN}",
                            rf"{_MONEY_AMOUNT_PATTERN}[^.;]{{0,100}}?(?:of common stock|aggregate offering price|aggregate amount)",
                        ),
                    ),
                    resale_language=_extract_language_excerpt(
                        excerpt,
                        ("resale", "selling stockholder", "selling shareholder", "at-the-market", "equity distribution agreement", "sales agreement"),
                    ),
                    source_form=filing.form,
                    source_date=filing.filing_date,
                    source_url=filing.source_url,
                    excerpt=excerpt,
                )
            )
            break
    return programs


def _parse_ads_adr_structures(text: str, filing: CapitalStructureFilingText) -> list[CapitalStructureAdsAdrStructure]:
    terms: list[CapitalStructureAdsAdrStructure] = []
    keywords = (
        "american depositary share",
        "american depositary shares",
        "american depositary receipts",
        "foreign private issuer",
        "ads represents",
        "adss",
        "adrs",
    )
    for window in _windows_for_keywords(text, keywords, radius=850):
        normalized = _normalise_for_scan(window)
        if not any(term in normalized for term in ("american depositary", "foreign private issuer", "ordinary share", "ordinary shares", "adr", "adrs")):
            continue
        excerpt = _source_excerpt(window)
        structure_name = "Foreign Private Issuer" if "foreign private issuer" in normalized else "ADS / ADR Structure"
        terms.append(
            CapitalStructureAdsAdrStructure(
                instrument_type="foreign issuer ADS/ADR structure",
                structure_name=structure_name,
                ratio=_extract_ads_ratio(excerpt),
                ordinary_share_class=_extract_ordinary_share_class(excerpt),
                voting_language=_extract_language_excerpt(excerpt, ("vote", "voting", "depositary", "ordinary shares")),
                conversion_language=_extract_language_excerpt(excerpt, ("represents", "convert", "exchange", "ordinary shares")),
                source_form=filing.form,
                source_date=filing.filing_date,
                source_url=filing.source_url,
                excerpt=excerpt,
            )
        )
        break
    return terms


def _levels_from_parsed_terms(parsed_terms: CapitalStructureTermsReport) -> list[CapitalStructureLevel]:
    levels: list[CapitalStructureLevel] = []
    for warrant in parsed_terms.warrants:
        if warrant.exercise_price is None:
            continue
        levels.append(
            CapitalStructureLevel(
                label="Parsed warrant exercise price",
                price=warrant.exercise_price,
                source=_source_label(warrant.source_form, warrant.source_date),
                explanation="Source-backed warrant exercise price; treat as possible non-chart supply context, not a prediction.",
                level_type="warrant_strike",
            )
        )
    for preferred in parsed_terms.preferred_series:
        if preferred.conversion_price is None:
            continue
        levels.append(
            CapitalStructureLevel(
                label="Parsed preferred conversion price",
                price=preferred.conversion_price,
                source=_source_label(preferred.source_form, preferred.source_date),
                explanation="Source-backed preferred conversion price; treat as possible non-chart supply context, not a prediction.",
                level_type="conversion_price",
            )
        )
    for convertible in parsed_terms.convertibles:
        if convertible.conversion_price is None:
            continue
        levels.append(
            CapitalStructureLevel(
                label="Parsed convertible conversion price",
                price=convertible.conversion_price,
                source=_source_label(convertible.source_form, convertible.source_date),
                explanation="Source-backed convertible conversion price; treat as possible non-chart supply context, not a prediction.",
                level_type="conversion_price",
            )
        )
    for offering in parsed_terms.offering_programs:
        if offering.offering_price is None:
            continue
        levels.append(
            CapitalStructureLevel(
                label="Parsed offering/resale price",
                price=offering.offering_price,
                source=_source_label(offering.source_form, offering.source_date),
                explanation="Source-backed offering or resale price; treat as a filing reference level, not guaranteed support or resistance.",
                level_type="offering_price",
            )
        )
    return levels


def _technical_impact_lines_from_terms(parsed_terms: CapitalStructureTermsReport) -> list[str]:
    lines: list[str] = []
    for warrant in parsed_terms.warrants[:4]:
        if warrant.exercise_price is not None and warrant.underlying_shares is not None:
            lines.append(
                f"Possible warrant supply: {_format_int(warrant.underlying_shares)} underlying shares near ${warrant.exercise_price:,.4g} from {warrant.series_name or warrant.instrument_type}."
            )
        elif warrant.exercise_price is not None:
            lines.append(f"Possible warrant supply level near ${warrant.exercise_price:,.4g} from {warrant.series_name or warrant.instrument_type}.")
        elif warrant.underlying_shares is not None:
            lines.append(f"Possible warrant supply: {_format_int(warrant.underlying_shares)} underlying shares disclosed; no exercise price was parsed.")

    for preferred in parsed_terms.preferred_series[:3]:
        if preferred.conversion_price is not None:
            lines.append(f"Preferred conversion overhang: {preferred.series_name or 'preferred stock'} conversion price parsed near ${preferred.conversion_price:,.4g}.")
        elif preferred.conversion_rate:
            lines.append(f"Preferred conversion overhang: {preferred.series_name or 'preferred stock'} conversion-rate language was parsed.")
        if preferred.liquidation_preference is not None:
            lines.append(f"Preferred seniority: {preferred.series_name or 'preferred stock'} liquidation preference parsed at ${preferred.liquidation_preference:,.4g}.")

    for convertible in parsed_terms.convertibles[:3]:
        if convertible.conversion_price is not None:
            lines.append(f"Convertible supply reference: {convertible.series_name or 'convertible instrument'} conversion price parsed near ${convertible.conversion_price:,.4g}.")
        elif convertible.conversion_rate:
            lines.append(f"Convertible supply reference: {convertible.series_name or 'convertible instrument'} conversion-rate language was parsed.")
        if convertible.maturity_date:
            lines.append(f"Convertible maturity check: {convertible.series_name or 'convertible instrument'} maturity parsed as {convertible.maturity_date}.")

    for offering in parsed_terms.offering_programs[:4]:
        if offering.program_type == "ATM program":
            amount = f" up to ${offering.amount:,.0f}" if offering.amount is not None else ""
            lines.append(f"ATM overhang warning:{amount} at-the-market/equity-distribution language was parsed.")
        elif offering.program_type in {"Shelf registration", "Resale prospectus"}:
            shares = f" {_format_int(offering.shares)} shares" if offering.shares is not None else ""
            lines.append(f"{offering.program_type} overhang warning:{shares} source-backed offering/resale language was parsed.")
        elif offering.offering_price is not None:
            lines.append(f"Offering price reference parsed near ${offering.offering_price:,.4g}; treat it as filing context only.")

    if parsed_terms.common_share_classes:
        classes = ", ".join(item.class_name or item.instrument_type for item in parsed_terms.common_share_classes[:4])
        lines.append(f"Share-class verification warning: parsed {classes}; float/voting quality may differ by class.")
    if parsed_terms.ads_adr_structures:
        lines.append("ADS/ADR or foreign-issuer verification warning: verify ADS ratio, ordinary-share terms, and issuer documents before relying on U.S. filing text alone.")
    if any((parsed_terms.warrants, parsed_terms.convertibles, parsed_terms.preferred_series, parsed_terms.offering_programs)):
        lines.append("Breakout/chase-risk caveat: filing-derived terms are a confidence modifier and do not predict price direction.")
    return _dedupe_text(lines)


def _verification_warnings_from_terms(parsed_terms: CapitalStructureTermsReport) -> list[str]:
    warnings: list[str] = []
    if parsed_terms.common_share_classes:
        warnings.append("Share-class terms were parsed; verify class-specific float, voting rights, and conversion mechanics.")
    if parsed_terms.ads_adr_structures:
        warnings.append("ADS/ADR or foreign private issuer structure detected; verify ADS ratio and ordinary-share terms from issuer/depositary sources.")
    return warnings


def _windows_for_keywords(text: str, keywords: tuple[str, ...], *, radius: int) -> list[str]:
    collapsed = _collapse_text(text)
    normalized = _normalise_for_scan(collapsed)
    windows: list[str] = []
    seen: set[tuple[int, int]] = set()
    for keyword in keywords:
        for match in re.finditer(_keyword_pattern(keyword), normalized, flags=re.IGNORECASE):
            start = max(0, match.start() - radius)
            end = min(len(collapsed), match.end() + radius)
            key = (start // 80, end // 80)
            if key in seen:
                continue
            seen.add(key)
            windows.append(collapsed[start:end].strip())
    return windows


def _window_from_span(text: str, start: int, end: int, *, radius: int) -> str:
    collapsed = _collapse_text(text)
    return collapsed[max(0, start - radius) : min(len(collapsed), end + radius)].strip()


def _bounded_window_from_span(text: str, start: int, end: int, *, radius: int) -> str:
    collapsed = _collapse_text(text)
    left = max(0, start - radius)
    right = min(len(collapsed), end + radius)
    left_boundary = max(collapsed.rfind(". ", left, start), collapsed.rfind("; ", left, start))
    if left_boundary >= 0:
        left = left_boundary + 2
    right_candidates = [index for index in (collapsed.find(". ", end, right), collapsed.find("; ", end, right)) if index >= 0]
    if right_candidates:
        right = min(right_candidates) + 1
    return collapsed[left:right].strip()


def _source_excerpt(text: str, *, max_chars: int = 520) -> str:
    collapsed = _collapse_text(text)
    if len(collapsed) <= max_chars:
        return collapsed
    return f"{collapsed[: max_chars - 3].rstrip()}..."


def _clean_label(value: str) -> str:
    label = _collapse_text(value).strip(" .,:;")
    label = label.title()
    replacements = {
        "Ads": "ADS",
        "Adrs": "ADRs",
        "Adr": "ADR",
        "Atm": "ATM",
        "S-3": "S-3",
        "F-3": "F-3",
    }
    for source, replacement in replacements.items():
        label = label.replace(source, replacement)
    return label


def _source_label(source_form: str, source_date: str) -> str:
    return f"{source_form} filed {source_date or '--'}"


def _format_int(value: int) -> str:
    return f"{value:,}"


def _rounded_optional(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _dedupe_by(items: list, key_func) -> list:
    seen: set[tuple] = set()
    result: list = []
    for item in items:
        key = key_func(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _dedupe_text(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = _collapse_text(value)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _merge_common_share_classes(items: list[CapitalStructureCommonShareClass]) -> list[CapitalStructureCommonShareClass]:
    grouped: dict[tuple[str, str, str, str], list[CapitalStructureCommonShareClass]] = {}
    for item in items:
        key = (item.class_name or "", item.source_form, item.source_date, item.source_url)
        grouped.setdefault(key, []).append(item)

    merged: list[CapitalStructureCommonShareClass] = []
    for group in grouped.values():
        first = group[0]
        merged.append(
            CapitalStructureCommonShareClass(
                instrument_type=first.instrument_type,
                class_name=first.class_name,
                shares=_first_present(item.shares for item in group),
                voting_language=_first_present(item.voting_language for item in group),
                conversion_language=_first_present(item.conversion_language for item in group),
                redemption_language=_first_present(item.redemption_language for item in group),
                resale_language=_first_present(item.resale_language for item in group),
                source_form=first.source_form,
                source_date=first.source_date,
                source_url=first.source_url,
                excerpt=_combine_source_excerpts(item.excerpt for item in group),
            )
        )
    return merged


def _first_present(values: Iterable) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None


def _combine_source_excerpts(values: Iterable[str], *, max_chars: int = 720) -> str:
    excerpts = _dedupe_text(values)
    return _source_excerpt(" / ".join(excerpts), max_chars=max_chars)


def _parsed_term_count(parsed_terms: CapitalStructureTermsReport | None) -> int:
    if parsed_terms is None:
        return 0
    return (
        len(parsed_terms.common_share_classes)
        + len(parsed_terms.preferred_series)
        + len(parsed_terms.warrants)
        + len(parsed_terms.convertibles)
        + len(parsed_terms.offering_programs)
        + len(parsed_terms.ads_adr_structures)
    )


def _extract_labeled_share_count(text: str, label: str) -> int | None:
    escaped = re.escape(_normalise_for_scan(label)).replace(r"\ ", r"\s+")
    return _extract_share_count(
        text,
        (
            rf"{_QUANTITY_PATTERN}\s+shares\s+of\s+{escaped}",
            rf"{escaped}[^.;]{{0,140}}?{_QUANTITY_PATTERN}\s+shares",
        ),
    )


def _is_conversion_target_reference(normalized_text: str, start: int) -> bool:
    prefix = normalized_text[max(0, start - 32) : start]
    return bool(re.search(r"(?:convertible|conversion|converted|exchangeable|exchanged)\s+into\s+$", prefix))


def _extract_underlying_share_count(text: str) -> int | None:
    return _extract_share_count(
        text,
        (
            rf"(?:underlying|issuable upon exercise of|issuable upon conversion of)[^.;]{{0,160}}?{_QUANTITY_PATTERN}\s+shares",
            rf"(?:to purchase|exercisable for|convertible into)[^.;]{{0,160}}?{_QUANTITY_PATTERN}\s+shares",
            rf"{_QUANTITY_PATTERN}\s+shares[^.;]{{0,120}}?(?:underlying|issuable upon exercise|issuable upon conversion)",
        ),
    )


def _extract_share_count(text: str, patterns: tuple[str, ...]) -> int | None:
    normalized = _normalise_for_scan(text)
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            context = normalized[max(0, match.start() - 80) : min(len(normalized), match.end() + 80)]
            if _ambiguous_value_context(context):
                continue
            count = _parse_scaled_number(match.group(1), match.group(2) if match.lastindex and match.lastindex >= 2 else None)
            if count is None:
                continue
            as_int = int(round(count))
            if 0 < as_int <= 20_000_000_000:
                return as_int
    return None


def _extract_money_field(text: str, patterns: tuple[str, ...]) -> float | None:
    normalized = _normalise_for_scan(text)
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            context = normalized[max(0, match.start() - 80) : min(len(normalized), match.end() + 80)]
            if _ambiguous_value_context(context):
                continue
            price = _parse_price(match.group(1))
            if price is not None:
                return price
    return None


def _extract_money_amount_field(text: str, patterns: tuple[str, ...]) -> float | None:
    normalized = _normalise_for_scan(text)
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            context = normalized[max(0, match.start() - 80) : min(len(normalized), match.end() + 80)]
            if _ambiguous_value_context(context):
                continue
            amount = _parse_scaled_number(match.group(1), match.group(2) if match.lastindex and match.lastindex >= 2 else None)
            if amount is not None and amount > 0:
                return amount
    return None


def _extract_date_field(text: str, patterns: tuple[str, ...]) -> str | None:
    normalized = _normalise_for_scan(text)
    for pattern in patterns:
        for match in re.finditer(pattern, normalized, flags=re.IGNORECASE):
            context = normalized[max(0, match.start() - 80) : min(len(normalized), match.end() + 80)]
            if _ambiguous_value_context(context):
                continue
            value = _collapse_text(match.group(1)).strip(" .,;:")
            if value:
                return value
    return None


def _extract_text_field(text: str, patterns: tuple[str, ...]) -> str | None:
    normalized = _normalise_for_scan(text)
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if not match:
            continue
        context = normalized[max(0, match.start() - 80) : min(len(normalized), match.end() + 80)]
        if _ambiguous_value_context(context):
            continue
        value = _collapse_text(match.group(1)).strip(" .,;:")
        if value and len(value) <= 180:
            return value
    return None


def _extract_language_excerpt(text: str, keywords: tuple[str, ...], *, radius: int = 95, max_chars: int = 260) -> str | None:
    collapsed = _collapse_text(text)
    normalized = _normalise_for_scan(collapsed)
    for keyword in keywords:
        match = re.search(_keyword_pattern(keyword), normalized, flags=re.IGNORECASE)
        if not match:
            continue
        start = max(0, match.start() - radius)
        end = min(len(collapsed), match.end() + radius)
        return _source_excerpt(collapsed[start:end].strip(), max_chars=max_chars)
    return None


def _ambiguous_value_context(context: str) -> bool:
    ambiguous_phrases = (
        "not determined",
        "not yet determined",
        "to be determined",
        "will be determined",
        "not currently determinable",
        "has not been determined",
        "no exercise price",
        "without an exercise price",
        "no conversion price",
        "without a conversion price",
    )
    return any(phrase in context for phrase in ambiguous_phrases)


def _parse_scaled_number(value: str, scale: str | None = None) -> float | None:
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    scale_clean = (scale or "").strip().lower()
    if scale_clean == "million":
        parsed *= 1_000_000
    elif scale_clean == "billion":
        parsed *= 1_000_000_000
    return parsed


def _warrant_type_and_name(raw: str, excerpt: str) -> tuple[str, str]:
    normalized_raw = _normalise_for_scan(raw)
    normalized_excerpt = _normalise_for_scan(excerpt)
    if "pre-funded" in normalized_raw:
        return "pre-funded warrant", "Pre-Funded Warrants"
    if "placement agent" in normalized_raw:
        return "placement agent warrant", "Placement Agent Warrants"
    if "common warrant" in normalized_raw:
        return "common warrant", "Common Warrants"
    if "pre-funded" in normalized_excerpt:
        return "pre-funded warrant", "Pre-Funded Warrants"
    if "placement agent" in normalized_excerpt:
        return "placement agent warrant", "Placement Agent Warrants"
    if "common warrant" in normalized_excerpt:
        return "common warrant", "Common Warrants"
    return "warrant", "Warrants to Purchase Common Stock"


def _extract_coupon_rate(excerpt: str, series_name: str | None) -> str | None:
    for value in (series_name or "", excerpt):
        match = re.search(r"(?<![0-9])([0-9]+(?:\.[0-9]+)?%)", value)
        if match:
            return match.group(1)
    return None


def _offering_instrument_type(excerpt: str) -> str:
    normalized = _normalise_for_scan(excerpt)
    if "common stock" in normalized:
        return "common stock offering program"
    if "ordinary shares" in normalized:
        return "ordinary share offering program"
    if "warrant" in normalized:
        return "warrant offering program"
    return "securities offering program"


def _extract_ads_ratio(excerpt: str) -> str | None:
    normalized = _normalise_for_scan(excerpt)
    patterns = (
        r"(?:each|one)\s+(?:american depositary share|ads)\s+represents?\s+([^.;]{1,120}?(?:ordinary shares?|class [a-z] ordinary shares?))",
        r"([0-9]+)\s+(?:american depositary shares|adss?)\s+represent\s+([0-9]+)\s+ordinary shares",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            return _collapse_text(" ".join(group for group in match.groups() if group)).strip(" .,;:")
    return None


def _extract_ordinary_share_class(excerpt: str) -> str | None:
    normalized = _normalise_for_scan(excerpt)
    match = re.search(r"\bclass\s+[a-z]\s+ordinary shares?\b", normalized, flags=re.IGNORECASE)
    if match:
        return _clean_label(match.group(0))
    if "ordinary shares" in normalized:
        return "Ordinary Shares"
    return None


def _parse_price(value: str) -> float | None:
    try:
        price = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    if price <= 0 or price > 100_000:
        return None
    return price


def _dedupe_signals(signals: list[CapitalStructureSignal]) -> list[CapitalStructureSignal]:
    seen: set[tuple[str, str, str]] = set()
    result: list[CapitalStructureSignal] = []
    for signal in sorted(signals, key=lambda item: item.score, reverse=True):
        key = (signal.label, signal.source_form, signal.source_url)
        if key in seen:
            continue
        seen.add(key)
        result.append(signal)
    return result


def _dedupe_levels(levels: list[CapitalStructureLevel]) -> list[CapitalStructureLevel]:
    seen: set[tuple[str, float, str]] = set()
    result: list[CapitalStructureLevel] = []
    for level in levels:
        key = (level.level_type, round(level.price, 4), level.source)
        if key in seen:
            continue
        seen.add(key)
        result.append(level)
    return result[:12]


def _aggregate_supply_score(signals: list[CapitalStructureSignal]) -> int:
    by_label: dict[str, list[int]] = {}
    for signal in signals:
        by_label.setdefault(signal.label, []).append(signal.score)
    total = 0.0
    for scores in by_label.values():
        ordered = sorted(scores, reverse=True)
        total += ordered[0]
        for extra in ordered[1:]:
            total += min(extra * 0.35, 10)
    return _clamp_int(total)


def _recency_adjusted_score(base_score: int, filing_date: str, as_of: date) -> int:
    parsed = _parse_date(filing_date)
    if parsed is None:
        return base_score
    days = max(0, (as_of - parsed).days)
    if days <= 90:
        multiplier = 1.15
    elif days <= 365:
        multiplier = 1.0
    elif days <= 730:
        multiplier = 0.7
    else:
        multiplier = 0.5
    return _clamp_int(round(base_score * multiplier))


def _severity_for_signal_score(score: int, fallback: CapitalPressureSeverity) -> CapitalPressureSeverity:
    if score >= 24:
        return "high"
    if score >= 12:
        return "medium"
    return fallback


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _confidence_for_scan(filings_analyzed: int, *, has_signals: bool) -> Literal["Low", "Medium", "High"]:
    if filings_analyzed >= 6 and has_signals:
        return "High"
    if filings_analyzed >= 2:
        return "Medium"
    return "Low"


def _dilution_sensitivity(read: CapitalPressureRead) -> DilutionSensitivity:
    if read == "High":
        return "High"
    if read == "Moderate":
        return "Moderate"
    if read == "Low":
        return "Low"
    return "Unknown"


def _breakout_adjustment(score: int) -> str:
    if score >= 75:
        return "Avoid chase"
    if score >= 55:
        return "Supply-capped"
    if score >= 25:
        return "Needs confirmation"
    return "Clean"


def _explanation_lines(
    read: CapitalPressureRead,
    score: int,
    signals: list[CapitalStructureSignal],
    *,
    parsed_terms: CapitalStructureTermsReport | None = None,
) -> list[str]:
    top = sorted(signals, key=lambda item: item.score, reverse=True)[:3]
    labels = ", ".join(signal.label for signal in top)
    lines = [
        f"Capital structure pressure is {read.lower()} based on a {score}/100 transparent filing scan.",
        f"Top filing-derived signal groups: {labels}.",
    ]
    parsed_count = _parsed_term_count(parsed_terms)
    if parsed_count:
        lines.append(f"Parsed {parsed_count} source-backed capital-structure term(s) for future Filing Terms panel hooks.")
    if any(signal.label == "Shelf / registration capacity" for signal in signals):
        lines.append("Active shelf, registration, resale, or ATM language was found in recent scanned filings.")
    if any(signal.label in {"Warrants", "Convertibles / notes", "Preferred stock"} for signal in signals):
        lines.append("Warrant, convertible, or preferred terms may create non-chart supply zones.")
    if any(signal.label in {"Share classes / voting control", "ADS / foreign issuer structure"} for signal in signals):
        lines.append("Share-class, ADS/ADR, or foreign-issuer terms may require float and voting-structure verification.")
    if any(signal.label == "Dilution warning" for signal in signals):
        lines.append("Explicit dilution-risk language was detected.")
    return lines


def _trading_impact_lines(report: CapitalStructurePressureReport) -> list[str]:
    if report.read == "Low":
        base = [
            "No material recent overhang signal was detected in scanned filings.",
            "Bullish breakouts have cleaner follow-through conditions, subject to normal price/volume confirmation.",
        ]
    elif report.read == "Moderate":
        base = [
            "Breakouts may need stronger volume confirmation.",
            "Rallies may be more prone to fade if resale, conversion, or offering supply is active.",
            "Treat the overlay as a confidence modifier, not an override of the chart.",
        ]
    else:
        base = [
            "Breakouts need stronger volume and VWAP confirmation.",
            "Rallies may fade near warrant, conversion, resale, or offering-related supply zones.",
            "Support failures may accelerate if dilution fear is active.",
            "Do not chase technical strength without confirming participation.",
        ]
    return _dedupe_text([*base, *report.parsed_terms.technical_impact_lines])


def _format_parsed_terms_summary_rows(parsed_terms: CapitalStructureTermsReport) -> list[str]:
    rows: list[str] = []
    for item in parsed_terms.common_share_classes[:3]:
        details = [item.class_name or item.instrument_type]
        if item.shares is not None:
            details.append(f"{_format_int(item.shares)} shares")
        if item.voting_language:
            details.append("voting language parsed")
        rows.append(f"Share class: {', '.join(details)} ({_source_label(item.source_form, item.source_date)}).")
    for item in parsed_terms.preferred_series[:3]:
        details = [item.series_name or item.instrument_type]
        if item.conversion_price is not None:
            details.append(f"conversion ${item.conversion_price:,.4g}")
        if item.liquidation_preference is not None:
            details.append(f"liquidation preference ${item.liquidation_preference:,.4g}")
        rows.append(f"Preferred series: {', '.join(details)} ({_source_label(item.source_form, item.source_date)}).")
    for item in parsed_terms.warrants[:3]:
        details = [item.series_name or item.instrument_type]
        if item.underlying_shares is not None:
            details.append(f"{_format_int(item.underlying_shares)} underlying shares")
        if item.exercise_price is not None:
            details.append(f"exercise ${item.exercise_price:,.4g}")
        if item.expiration_date:
            details.append(f"expires {item.expiration_date}")
        rows.append(f"Warrant: {', '.join(details)} ({_source_label(item.source_form, item.source_date)}).")
    for item in parsed_terms.convertibles[:3]:
        details = [item.series_name or item.instrument_type]
        if item.conversion_price is not None:
            details.append(f"conversion ${item.conversion_price:,.4g}")
        elif item.conversion_rate:
            details.append("conversion rate parsed")
        if item.maturity_date:
            details.append(f"matures {item.maturity_date}")
        rows.append(f"Convertible: {', '.join(details)} ({_source_label(item.source_form, item.source_date)}).")
    for item in parsed_terms.offering_programs[:3]:
        details = [item.program_type]
        if item.shares is not None:
            details.append(f"{_format_int(item.shares)} shares")
        if item.amount is not None:
            details.append(f"${item.amount:,.0f}")
        if item.offering_price is not None:
            details.append(f"price ${item.offering_price:,.4g}")
        rows.append(f"Offering program: {', '.join(details)} ({_source_label(item.source_form, item.source_date)}).")
    for item in parsed_terms.ads_adr_structures[:2]:
        details = [item.structure_name or item.instrument_type]
        if item.ratio:
            details.append(f"ratio {item.ratio}")
        if item.ordinary_share_class:
            details.append(item.ordinary_share_class)
        rows.append(f"ADS/ADR: {', '.join(details)} ({_source_label(item.source_form, item.source_date)}).")
    return rows


def _what_would_change(read: CapitalPressureRead) -> list[str]:
    if read == "High":
        return [
            "A later filing terminates or materially reduces shelf, ATM, resale, warrant, convertible, or preferred-stock pressure.",
            "Volume confirms above key technical resistance despite the filing-derived overhang.",
            "Updated filings clarify that extracted levels are stale, redeemed, exercised, or no longer outstanding.",
        ]
    if read == "Moderate":
        return [
            "A fresh filing adds, removes, or updates shelf, resale, warrant, convertible, or preferred-stock terms.",
            "Price breaks out on unusually strong volume and holds above VWAP/resistance.",
            "A later SEC scan finds no active offering or resale capacity in the recent filing stack.",
        ]
    return [
        "A new S-1, S-3, 424B, 8-K, 10-Q, or proxy filing adds offering, resale, warrant, convertible, or dilution language.",
        "Price-volume behavior confirms or rejects the chart setup independently of filing context.",
    ]


def _collapse_text(text: str) -> str:
    text = re.sub(r"[\t\r\f\v]+", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalise_for_scan(text: str) -> str:
    normalized = (text or "").lower()
    normalized = normalized.replace("\u2019", "'").replace("\u2018", "'")
    normalized = normalized.replace("\u201c", '"').replace("\u201d", '"')
    normalized = normalized.replace("\u2013", "-").replace("\u2014", "-")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _clean_symbol(symbol: str) -> str:
    try:
        return normalize_ticker(symbol)
    except Exception:
        return str(symbol or "").strip().upper()


def _clamp_int(score: int | float) -> int:
    return int(max(0, min(100, round(float(score)))))
