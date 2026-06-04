from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Iterable, Literal

from app.data.sec_edgar import DEFAULT_CACHE_TTL, SecEdgarClient, SecFiling, normalize_ticker

CapitalPressureSeverity = Literal["low", "medium", "high"]
CapitalPressureRead = Literal["Low", "Moderate", "High", "Unknown"]
DilutionSensitivity = Literal["Low", "Moderate", "High", "Unknown"]

CAPITAL_STRUCTURE_FORMS: tuple[str, ...] = (
    "S-1",
    "S-1/A",
    "S-3",
    "S-3/A",
    "424B3",
    "424B4",
    "424B5",
    "424B7",
    "8-K",
    "10-K",
    "10-Q",
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

_MONEY_PATTERN = r"\$([0-9]{1,5}(?:\.[0-9]{1,4})?)"
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
    filing_limit: int = 24,
    max_documents: int = 8,
    as_of: date | None = None,
) -> CapitalStructurePressureReport:
    clean_symbol = _clean_symbol(symbol)
    active_client = client or SecEdgarClient(timeout_seconds=10)
    warnings: list[str] = []

    try:
        filings = active_client.recent_filings(clean_symbol, forms=CAPITAL_STRUCTURE_FORMS, limit=filing_limit)
    except Exception as exc:
        return unknown_capital_structure_report(
            clean_symbol,
            warnings=[f"Capital structure overlay unavailable: SEC recent filings fetch failed: {exc}"],
        )

    if not filings:
        return unknown_capital_structure_report(
            clean_symbol,
            warnings=["Capital structure overlay unavailable: no recent capital-structure SEC filing forms were found."],
        )

    filing_texts: list[CapitalStructureFilingText] = []
    for filing in filings[: max(1, max_documents)]:
        try:
            text = fetch_primary_filing_text(active_client, filing)
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
        )

    return scan_capital_structure_filings(
        clean_symbol,
        company_name=company_name,
        filings=filing_texts,
        warnings=warnings,
        as_of=as_of,
    )


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
            warnings=list(warnings or []),
            explanation_lines=[
                "No material recent capital-structure overhang signal was detected in scanned filings.",
                "This is a filing-derived risk overlay, not a price prediction.",
            ],
            what_would_change=_what_would_change("Low"),
            possible_supply_levels=_dedupe_levels(possible_levels),
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
        warnings=list(warnings or []),
        explanation_lines=_explanation_lines(read, score, deduped_signals),
        what_would_change=_what_would_change(read),
        possible_supply_levels=_dedupe_levels(possible_levels),
    )


def unknown_capital_structure_report(
    symbol: str,
    *,
    company_name: str = "",
    warnings: Iterable[str] | None = None,
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

    if report.warnings:
        lines.extend(["", "Overlay data notes"])
        lines.extend(f"- {warning}" for warning in report.warnings)

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
) -> list[str]:
    top = sorted(signals, key=lambda item: item.score, reverse=True)[:3]
    labels = ", ".join(signal.label for signal in top)
    lines = [
        f"Capital structure pressure is {read.lower()} based on a {score}/100 transparent filing scan.",
        f"Top filing-derived signal groups: {labels}.",
    ]
    if any(signal.label == "Shelf / registration capacity" for signal in signals):
        lines.append("Active shelf, registration, resale, or ATM language was found in recent scanned filings.")
    if any(signal.label in {"Warrants", "Convertibles / notes", "Preferred stock"} for signal in signals):
        lines.append("Warrant, convertible, or preferred terms may create non-chart supply zones.")
    if any(signal.label == "Dilution warning" for signal in signals):
        lines.append("Explicit dilution-risk language was detected.")
    return lines


def _trading_impact_lines(report: CapitalStructurePressureReport) -> list[str]:
    if report.read == "Low":
        return [
            "No material recent overhang signal was detected in scanned filings.",
            "Bullish breakouts have cleaner follow-through conditions, subject to normal price/volume confirmation.",
        ]
    if report.read == "Moderate":
        return [
            "Breakouts may need stronger volume confirmation.",
            "Rallies may be more prone to fade if resale, conversion, or offering supply is active.",
            "Treat the overlay as a confidence modifier, not an override of the chart.",
        ]
    return [
        "Breakouts need stronger volume and VWAP confirmation.",
        "Rallies may fade near warrant, conversion, resale, or offering-related supply zones.",
        "Support failures may accelerate if dilution fear is active.",
        "Do not chase technical strength without confirming participation.",
    ]


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
