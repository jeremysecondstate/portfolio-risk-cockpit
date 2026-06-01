from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus

import requests

from app.analytics.etf_analysis import detect_security_kind
from app.data.sec_edgar import html_to_text

REPORTING_PROFILE_US_DOMESTIC_EQUITY = "us_domestic_equity"
REPORTING_PROFILE_FOREIGN_ISSUER = "foreign_issuer"
REPORTING_PROFILE_ETF_OR_FUND = "etf_or_fund"
REPORTING_PROFILE_UNKNOWN = "unknown"

FOREIGN_ISSUER_SEC_FORMS = (
    "6-K",
    "20-F",
    "20-F/A",
    "40-F",
    "40-F/A",
    "F-1",
    "F-1/A",
    "F-3",
    "F-3/A",
    "F-4",
    "F-4/A",
    "F-6",
    "F-6/A",
)
FOREIGN_RESULTS_FORMS = ("6-K", "20-F", "20-F/A", "40-F", "40-F/A")
US_DOMESTIC_REPORT_FORMS = ("10-Q", "10-K", "8-K", "10-Q/A", "10-K/A", "S-1", "S-3", "S-4")
ALL_COMPANY_REPORT_FORMS = tuple(dict.fromkeys((*US_DOMESTIC_REPORT_FORMS, *FOREIGN_ISSUER_SEC_FORMS)))

FOREIGN_METRIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "revenue": ("total net sales", "net sales", "revenue", "sales"),
    "net_income": ("net income", "profit for the period", "profit attributable", "income from operations"),
    "eps": ("earnings per share", "basic eps", "diluted eps", "eps"),
    "gross_margin": ("gross margin", "gross profit"),
    "orders_bookings": ("order intake", "orders", "net bookings", "bookings", "backlog"),
    "guidance_outlook": ("guidance", "outlook", "expects", "expect", "forecast", "full year"),
    "dividend_buyback": ("dividend", "share buyback", "repurchase"),
    "currency_basis": ("us gaap", "ifrs", "euro", "eur", "reporting currency", "based on"),
    "rd_capex": ("r&d", "research and development", "capex", "capital expenditure"),
    "balance_sheet_cash": ("cash and cash equivalents", "cash", "liquidity", "balance sheet"),
    "geography_customer": ("geography", "geographic", "customer concentration", "customers", "china", "taiwan"),
}

NOT_CLEANLY_EXTRACTED = "Not cleanly extracted yet"
VALUES_NOT_EXTRACTED = "values not extracted yet"
MONEY_RE = r"(?:(?:EUR|USD)\s?|\u20ac|\$)\s?[\d,.]+(?:\s?(?:billion|bn|million|m))?"
PERCENT_RE = r"\d+(?:\.\d+)?%"

KNOWN_FOREIGN_IR_SOURCES: dict[str, list[tuple[str, str, str]]] = {
    "ASML": [
        ("Official investor relations financial results page", "--", "https://www.asml.com/en/investors/financial-results"),
        ("Company annual report page", "--", "https://www.asml.com/investors/annual-report"),
        ("Official company press releases / announcements", "--", "https://www.asml.com/en/news/press-releases"),
        ("Company investor calendar", "--", "https://www.asml.com/investors/financial-calendar"),
    ],
}

KNOWN_FOREIGN_ISSUER_SYMBOLS = {
    "ASML",
    "ARM",
    "BABA",
    "BP",
    "NVO",
    "SAP",
    "SHEL",
    "SHOP",
    "SONY",
    "TSM",
}


@dataclass(frozen=True)
class ForeignIssuerSnapshot:
    symbol: str
    company_name: str
    reporting_profile: str
    source_links: list[tuple[str, str, str]]
    latest_source_label: str
    latest_source_date: str
    metric_snippets: dict[str, list[str]]
    revenue_trend: str
    profitability_trend: str
    guidance_tone: str
    orders_bookings_label: str
    reporting_basis_label: str
    source_freshness: str
    companyfacts_note: str
    warnings: list[str]
    filings_lines: list[str]


def detect_reporting_profile(
    symbol: str,
    quote: dict[str, Any] | None = None,
    position_asset_type: str | None = None,
    *,
    sec_forms: list[str] | tuple[str, ...] | None = None,
    company_title: str = "",
) -> str:
    security_kind = detect_security_kind(symbol, quote, position_asset_type)
    if security_kind in {"etf", "fund"}:
        return REPORTING_PROFILE_ETF_OR_FUND

    normalized = _normalize_symbol(symbol)
    forms = {str(form).upper() for form in (sec_forms or ()) if str(form).strip()}
    if forms & set(FOREIGN_ISSUER_SEC_FORMS):
        return REPORTING_PROFILE_FOREIGN_ISSUER
    if forms & set(US_DOMESTIC_REPORT_FORMS):
        return REPORTING_PROFILE_US_DOMESTIC_EQUITY

    metadata = " ".join(_profile_strings(quote, position_asset_type, company_title)).upper()
    if _metadata_indicates_foreign_issuer(metadata):
        return REPORTING_PROFILE_FOREIGN_ISSUER
    if any(term in metadata for term in ("COMMON_STOCK", "COMMON STOCK", "EQUITY", "STOCK")):
        return REPORTING_PROFILE_US_DOMESTIC_EQUITY
    if normalized in KNOWN_FOREIGN_ISSUER_SYMBOLS:
        return REPORTING_PROFILE_FOREIGN_ISSUER
    return REPORTING_PROFILE_UNKNOWN


def build_foreign_issuer_snapshot(
    symbol: str,
    *,
    company_name: str = "",
    filings_lines: list[str] | None = None,
    sec_text: str = "",
    official_text: str = "",
    companyfacts_text: str = "",
    companyfacts_available: bool = False,
    source_links: list[tuple[str, str, str]] | None = None,
    warnings: list[str] | None = None,
) -> ForeignIssuerSnapshot:
    normalized = _normalize_symbol(symbol)
    company = company_name or normalized
    filings = filings_lines or []
    links = source_links or foreign_issuer_source_links(normalized, company, filings)
    combined_text = "\n\n".join(part for part in (official_text, sec_text, companyfacts_text) if part.strip())
    metric_snippets = extract_foreign_issuer_metrics(combined_text)
    latest_label, latest_date = _latest_source(links, filings)
    revenue = _trend_from_snippets(metric_snippets.get("revenue", []))
    profitability = _profitability_from_snippets(metric_snippets)
    guidance = _guidance_from_snippets(metric_snippets.get("guidance_outlook", []))
    companyfacts_note = (
        "SEC companyfacts/XBRL loaded as supplemental context; foreign issuer IR results, 6-K, and 20-F remain the primary source stack."
        if companyfacts_available
        else "SEC companyfacts/XBRL not available or limited for this foreign issuer; using IR results and foreign issuer filings instead."
    )
    fallback_warnings = list(warnings or [])
    if not official_text.strip() and not sec_text.strip():
        fallback_warnings.append(
            "Foreign issuer detected. U.S.-style 8-K / 10-Q earnings exhibits may not apply. Search official IR results, 6-K, and 20-F."
        )
    return ForeignIssuerSnapshot(
        symbol=normalized,
        company_name=company,
        reporting_profile=REPORTING_PROFILE_FOREIGN_ISSUER,
        source_links=_dedupe_links(links),
        latest_source_label=latest_label,
        latest_source_date=latest_date,
        metric_snippets=metric_snippets,
        revenue_trend=revenue,
        profitability_trend=profitability,
        guidance_tone=guidance,
        orders_bookings_label=_metric_presence_label(metric_snippets.get("orders_bookings", []), loaded="Loaded"),
        reporting_basis_label=_reporting_basis_label(metric_snippets.get("currency_basis", []), combined_text),
        source_freshness=_source_freshness_label(links, official_text, sec_text),
        companyfacts_note=companyfacts_note,
        warnings=fallback_warnings,
        filings_lines=filings,
    )


def foreign_issuer_source_links(
    symbol: str,
    company_name: str = "",
    filings_lines: list[str] | None = None,
    discovered_links: list[tuple[str, str, str]] | None = None,
) -> list[tuple[str, str, str]]:
    normalized = _normalize_symbol(symbol)
    links: list[tuple[str, str, str]] = []
    links.extend(KNOWN_FOREIGN_IR_SOURCES.get(normalized, []))
    if not links:
        issuer_term = f"{company_name or normalized} {normalized}".strip()
        searches = [
            ("Official investor relations financial results page search", f"{issuer_term} investor relations financial results"),
            ("Latest quarterly result page or PDF search", f"{issuer_term} quarterly results press release pdf"),
            ("Company annual report page search", f"{issuer_term} annual report investor relations"),
            ("Official company press releases / announcements search", f"{issuer_term} press releases financial results"),
            ("Company investor calendar search", f"{issuer_term} investor calendar financial results"),
            ("Investor presentation search", f"{issuer_term} investor presentation results pdf"),
        ]
        links.extend((label, "--", f"https://www.google.com/search?q={quote_plus(query)}") for label, query in searches)
    links.extend(discovered_links or [])
    for line in filings_lines or []:
        label, date, url = _sec_source_from_line(line)
        if url:
            links.append((label, date, url))
    links.extend(
        [
            ("SEC 6-K filings search", "--", f"https://www.sec.gov/edgar/search/#/q={quote_plus(normalized)}&forms=6-K"),
            ("SEC 20-F filings search", "--", f"https://www.sec.gov/edgar/search/#/q={quote_plus(normalized)}&forms=20-F"),
        ]
    )
    return _dedupe_links(links)


def fetch_known_official_ir_texts(
    symbol: str,
    *,
    timeout_seconds: int = 7,
) -> tuple[str, list[tuple[str, str, str]], list[str]]:
    normalized = _normalize_symbol(symbol)
    known_links = KNOWN_FOREIGN_IR_SOURCES.get(normalized, [])
    if not known_links:
        return "", [], []

    texts: list[str] = []
    discovered: list[tuple[str, str, str]] = []
    warnings: list[str] = []
    session = requests.Session()
    headers = {"User-Agent": "PortfolioRiskCockpit foreign-issuer research"}
    for label, _date, url in known_links[:2]:
        try:
            response = session.get(url, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
        except Exception as exc:
            warnings.append(f"{label} could not be loaded automatically: {exc}")
            continue
        raw = response.text
        text = html_to_text(raw)
        if text.strip():
            texts.append(f"{label}\nSource: {url}\n{text}")
        discovered.extend(_discover_official_links_from_html(raw, base_domain=_base_domain(url)))
    return "\n\n".join(texts), _dedupe_links(discovered), warnings


def extract_foreign_issuer_metrics(text: str) -> dict[str, list[str]]:
    normalized = _normalize_text(text)
    snippets: dict[str, list[str]] = {}
    for key, keywords in FOREIGN_METRIC_KEYWORDS.items():
        found = _find_snippets(normalized, keywords, limit=4)
        if found:
            snippets[key] = found
    period = _find_period_snippets(normalized)
    if period:
        snippets["period_date"] = period
    return snippets


def format_foreign_issuer_earnings_text(snapshot: ForeignIssuerSnapshot) -> str:
    lines = [
        f"FOREIGN ISSUER RESULTS MODE - {snapshot.symbol}",
        "=" * (30 + len(snapshot.symbol)),
        "",
        f"Company: {snapshot.company_name}",
        f"Reporting profile: {snapshot.reporting_profile}",
        "",
        "Source mode:",
        "- Foreign issuer source mode.",
        "- U.S.-style 8-K / 10-Q earnings exhibits may not apply.",
        "- Primary stack: official IR financial results, company press releases, SEC 6-K, SEC 20-F / 40-F, annual report, and companyfacts only when useful.",
        "",
        f"Latest results source: {snapshot.latest_source_label} ({snapshot.latest_source_date})",
        f"Source freshness: {snapshot.source_freshness}",
        f"Companyfacts note: {snapshot.companyfacts_note}",
        "",
        "Extracted result checks:",
        f"- Revenue / sales: {_snippet_or_status(snapshot, 'revenue', snapshot.revenue_trend)}",
        f"- Net income / profitability: {_snippet_or_status(snapshot, 'net_income', snapshot.profitability_trend)}",
        f"- EPS: {_snippet_or_status(snapshot, 'eps', 'Not found')}",
        f"- Gross margin: {_snippet_or_status(snapshot, 'gross_margin', 'Not found')}",
        f"- Guidance / outlook: {_snippet_or_status(snapshot, 'guidance_outlook', snapshot.guidance_tone)}",
        f"- Orders / bookings / backlog: {_snippet_or_status(snapshot, 'orders_bookings', snapshot.orders_bookings_label)}",
        f"- Dividend / buyback: {_snippet_or_status(snapshot, 'dividend_buyback', 'Not found')}",
        f"- Currency / reporting basis: {snapshot.reporting_basis_label}",
    ]
    if snapshot.warnings:
        lines.extend(["", "Fallback notes:", *[f"- {warning}" for warning in snapshot.warnings]])
    lines.extend(["", "Source links:", *[f"- {label}: {url or '--'}" for label, _date, url in snapshot.source_links]])
    return "\n".join(lines)


def format_foreign_issuer_fundamentals_text(snapshot: ForeignIssuerSnapshot) -> str:
    lines = [
        f"FOREIGN ISSUER FUNDAMENTALS - {snapshot.symbol}",
        "=" * (30 + len(snapshot.symbol)),
        "",
        f"Company: {snapshot.company_name}",
        f"Reporting profile: {snapshot.reporting_profile}",
        "",
        "Fundamental source stack:",
        "- Use 20-F / 40-F annual reports, official annual report pages, and IR quarterly results when companyfacts is limited.",
        "- Use 6-K for interim foreign issuer reports, quarterly results, and earnings-related releases.",
        "- Treat SEC companyfacts/XBRL as supplemental for this profile.",
        "",
        "Fundamental checks:",
        f"- Revenue trend: {_snippet_or_status(snapshot, 'revenue', snapshot.revenue_trend)}",
        f"- Net income / margin: {_snippet_or_status(snapshot, 'net_income', snapshot.profitability_trend)}",
        f"- Gross margin: {_snippet_or_status(snapshot, 'gross_margin', 'Not found')}",
        f"- Orders / bookings / backlog: {_snippet_or_status(snapshot, 'orders_bookings', snapshot.orders_bookings_label)}",
        f"- R&D / capex: {_snippet_or_status(snapshot, 'rd_capex', 'Not found')}",
        f"- Balance sheet / cash: {_snippet_or_status(snapshot, 'balance_sheet_cash', 'Not found')}",
        f"- Geographic / customer concentration: {_snippet_or_status(snapshot, 'geography_customer', 'Not found')}",
        f"- FX / reporting currency: {snapshot.reporting_basis_label}",
        "",
        snapshot.companyfacts_note,
    ]
    if snapshot.warnings:
        lines.extend(["", "Fallback notes:", *[f"- {warning}" for warning in snapshot.warnings]])
    return "\n".join(lines)


def format_foreign_issuer_results_explanation(snapshot: ForeignIssuerSnapshot) -> str:
    fields = _clean_result_fields(snapshot)
    verdicts = _result_verdicts(snapshot, fields)
    source_groups = _group_source_links(snapshot.source_links)
    history_rows = _results_history_rows(snapshot, fields)
    raw_extracts = _raw_extract_lines(snapshot)

    lines = [
        f"Foreign Issuer Results Explanation - {snapshot.symbol}",
        "=" * (39 + len(snapshot.symbol)),
        "",
        "1. Bottom Line",
        _bottom_line(snapshot, verdicts, fields),
        "",
        "2. What Foreign Issuer Means",
        (
            f"{snapshot.symbol} is not a normal U.S. domestic filer. It may report through investor relations pages, "
            "annual reports, 6-K filings, and 20-F filings instead of the usual 10-Q / 8-K earnings exhibit path. "
            "That means foreign issuer mode is a source-routing choice, not a broken earnings scan."
        ),
        "",
        "3. Latest Results Snapshot",
        f"- Latest period detected: {fields['period']}",
        f"- Revenue / sales: {fields['revenue']}",
        f"- Net income: {fields['net_income']}",
        f"- EPS: {fields['eps']}",
        f"- Gross margin: {fields['gross_margin']}",
        f"- Guidance / outlook: {fields['guidance']}",
        f"- Orders / bookings / backlog: {fields['orders_bookings']}",
        f"- Reporting currency / accounting basis: {fields['reporting_basis']}",
        f"- Source freshness: {snapshot.source_freshness}",
        "",
        "4. Good / Bad / Watch",
        "Good:",
        *_prefixed_lines(_good_lines(snapshot, fields)),
        "",
        "Bad / Missing:",
        *_prefixed_lines(_bad_missing_lines(snapshot, fields)),
        "",
        "Watch:",
        *_prefixed_lines(_watch_lines(snapshot, fields)),
        "",
        "5. Results History Read",
        "Period | Sales / revenue | Net income | EPS | Gross margin | Guidance / key note | Source",
        "--- | --- | --- | --- | --- | --- | ---",
        *[_history_row_text(row) for row in history_rows],
        "",
        "6. Result Verdict",
        f"- Revenue trend: {verdicts['revenue']} - {_verdict_reason('revenue', verdicts['revenue'], fields)}",
        f"- Profitability trend: {verdicts['profitability']} - {_verdict_reason('profitability', verdicts['profitability'], fields)}",
        f"- Guidance: {verdicts['guidance']} - {_verdict_reason('guidance', verdicts['guidance'], fields)}",
        f"- Source quality: {verdicts['source_quality']} - {_verdict_reason('source_quality', verdicts['source_quality'], fields)}",
        f"- Confidence: {verdicts['confidence']} - {_verdict_reason('confidence', verdicts['confidence'], fields)}",
        "",
        "7. Source Links",
        "Official company sources:",
        *_prefixed_lines(_source_lines(source_groups["official"])),
        "",
        "SEC foreign issuer filings:",
        *_prefixed_lines(_source_lines(source_groups["sec"])),
        "",
        "Supplemental:",
        *_prefixed_lines(_source_lines(source_groups["supplemental"])),
        "",
        "8. Foreign Issuer Source Glossary",
        "- 6-K: foreign issuer interim/current report, often used for quarterly results or material updates.",
        "- 20-F: annual report equivalent for foreign issuers.",
        "- IR results page: official company investor results hub.",
        "- Companyfacts/XBRL: supplemental structured SEC data, but it may be limited for foreign issuers.",
        "",
        "9. Source Details / Raw Extracts",
        *raw_extracts,
    ]
    return "\n".join(lines)


def foreign_issuer_earnings_cards(snapshot: ForeignIssuerSnapshot) -> list[tuple[str, str, str, str]]:
    return [
        ("Foreign Issuer Mode", "IR Results", "info", "Uses official IR results, 6-K, 20-F / 40-F, annual reports, and company press releases."),
        ("Latest Results", _short_label(snapshot.latest_source_label), "info", "Latest foreign issuer results source."),
        ("Revenue / Sales Trend", snapshot.revenue_trend, _trend_status(snapshot.revenue_trend), "Read from IR results, 6-K, 20-F, or annual report text."),
        ("Profitability", snapshot.profitability_trend, _trend_status(snapshot.profitability_trend), "Net income, EPS, and margins from loaded foreign issuer text."),
        ("Guidance / Outlook", snapshot.guidance_tone, _tone_status(snapshot.guidance_tone), "Guidance, outlook, expects, and forecast language."),
        ("Orders / Bookings", snapshot.orders_bookings_label, "mixed" if snapshot.orders_bookings_label == "Loaded" else "info", "Bookings, orders, backlog, or order intake when available."),
        ("Currency / Basis", snapshot.reporting_basis_label, "info", "Reporting currency and accounting basis when detected."),
        ("Source Freshness", snapshot.source_freshness, "info", "Official IR and SEC foreign issuer source availability."),
    ]


def foreign_issuer_fundamental_cards(snapshot: ForeignIssuerSnapshot) -> list[tuple[str, str, str, str]]:
    return [
        ("Foreign Issuer Fundamentals", "20-F / IR", "info", "Uses annual reports, 20-F / 40-F, 6-K, and official IR results."),
        ("Revenue Trend", snapshot.revenue_trend, _trend_status(snapshot.revenue_trend), "Revenue or net sales read from loaded foreign issuer sources."),
        ("Net Income / Margin", snapshot.profitability_trend, _trend_status(snapshot.profitability_trend), "Net income, EPS, gross margin, or operating margin."),
        ("Gross Margin", _metric_presence_label(snapshot.metric_snippets.get("gross_margin", [])), "mixed", "Gross margin is a key profitability metric when available."),
        ("Orders / Bookings / Backlog", snapshot.orders_bookings_label, "mixed" if snapshot.orders_bookings_label == "Loaded" else "info", "Useful for equipment and cyclical companies when available."),
        ("R&D / Capex", _metric_presence_label(snapshot.metric_snippets.get("rd_capex", [])), "info", "R&D and capex from annual reports or result packages when available."),
        ("Balance Sheet / Cash", _metric_presence_label(snapshot.metric_snippets.get("balance_sheet_cash", [])), "info", "Cash and balance sheet context from loaded source text."),
        ("FX / Reporting Currency", snapshot.reporting_basis_label, "info", "Foreign issuer reporting basis can differ from U.S. domestic filings."),
    ]


def foreign_issuer_interpretation(snapshot: ForeignIssuerSnapshot) -> list[str]:
    lines = [
        "Foreign issuer detected; missing U.S.-style 8-K / 10-Q earnings exhibits should not be treated as missing earnings.",
        f"Use {snapshot.latest_source_label.lower()} plus 6-K / 20-F / annual report sources for the current read.",
        f"Revenue trend appears {snapshot.revenue_trend.lower()} and profitability appears {snapshot.profitability_trend.lower()} from loaded text.",
        f"Guidance / outlook language is {snapshot.guidance_tone.lower()}.",
    ]
    if snapshot.orders_bookings_label == "Loaded":
        lines.append("Orders, bookings, or backlog language was detected; treat it as an important demand indicator.")
    return lines


def foreign_issuer_risks(snapshot: ForeignIssuerSnapshot) -> list[str]:
    risks = [
        "Foreign issuer filings may use 6-K / 20-F / annual reports instead of the U.S. domestic 8-K and 10-Q earnings pattern.",
        "Companyfacts can be limited or supplemental for foreign issuers, so verify the official IR result package.",
    ]
    if snapshot.guidance_tone in {"Negative", "Mixed", "Limited"}:
        risks.append("Guidance detail is limited or mixed in the loaded foreign issuer text.")
    if snapshot.reporting_basis_label in {"Not detected", "Limited"}:
        risks.append("Reporting currency and accounting basis should be verified from the official result package.")
    return risks


def _clean_result_fields(snapshot: ForeignIssuerSnapshot) -> dict[str, str]:
    period = _latest_period_detected(snapshot)
    return {
        "period": period,
        "revenue": _clean_revenue(snapshot, period),
        "net_income": _clean_money_metric(snapshot, "net_income", ("net income", "profit for the period", "profit attributable", "income from operations"), period=period),
        "eps": _clean_eps(snapshot, period),
        "gross_margin": _clean_gross_margin(snapshot, period),
        "guidance": _clean_guidance(snapshot, period),
        "orders_bookings": _clean_money_metric(snapshot, "orders_bookings", ("order intake", "orders", "net bookings", "bookings", "backlog")),
        "reporting_basis": snapshot.reporting_basis_label if snapshot.reporting_basis_label not in {"", "Not detected"} else NOT_CLEANLY_EXTRACTED,
    }


def _bottom_line(snapshot: ForeignIssuerSnapshot, verdicts: dict[str, str], fields: dict[str, str]) -> str:
    result = _overall_result_picture(verdicts)
    reasons = []
    if fields["revenue"] != NOT_CLEANLY_EXTRACTED:
        reasons.append("sales were found")
    if fields["net_income"] != NOT_CLEANLY_EXTRACTED:
        reasons.append("profit was found")
    if verdicts["guidance"] == "Positive":
        reasons.append("guidance appears constructive")
    if fields["orders_bookings"] == NOT_CLEANLY_EXTRACTED:
        reasons.append("orders/bookings are not cleanly extracted yet")
    if not reasons:
        reasons.append("source links are prepared, but clean result values are still limited")
    return (
        f"{snapshot.symbol}'s latest foreign-issuer result read is {result.lower()}. "
        f"The main reasons: {', '.join(reasons)}. Treat this as an earnings-equivalent read from IR, 6-K, and 20-F sources, "
        "not as a failed U.S.-style earnings exhibit scan."
    )


def _overall_result_picture(verdicts: dict[str, str]) -> str:
    if verdicts["revenue"] == "Weak" or verdicts["profitability"] == "Weak":
        return "Weak"
    if verdicts["revenue"] == "Positive" and verdicts["profitability"] == "Positive" and verdicts["guidance"] == "Positive":
        return "Good"
    if "Positive" in {verdicts["revenue"], verdicts["profitability"], verdicts["guidance"]}:
        return "Mixed"
    if "Mixed" in {verdicts["revenue"], verdicts["profitability"], verdicts["guidance"]}:
        return "Mixed"
    return "Unclear"


def _good_lines(snapshot: ForeignIssuerSnapshot, fields: dict[str, str]) -> list[str]:
    lines = []
    if snapshot.guidance_tone == "Positive" or fields["guidance"] != NOT_CLEANLY_EXTRACTED:
        lines.append("Guidance or outlook language appears positive or at least usable.")
    if fields["revenue"] != NOT_CLEANLY_EXTRACTED:
        lines.append("Revenue/sales figures were found.")
    if fields["net_income"] != NOT_CLEANLY_EXTRACTED or fields["gross_margin"] != NOT_CLEANLY_EXTRACTED or fields["eps"] != NOT_CLEANLY_EXTRACTED:
        lines.append("Profitability figures were found.")
    if "loaded" in snapshot.source_freshness.lower():
        lines.append("Official IR and SEC foreign issuer sources are loaded.")
    return lines or ["Official foreign issuer source links are prepared."]


def _bad_missing_lines(snapshot: ForeignIssuerSnapshot, fields: dict[str, str]) -> list[str]:
    lines = []
    if fields["orders_bookings"] == NOT_CLEANLY_EXTRACTED:
        lines.append("Orders/bookings/backlog were not cleanly found.")
    if not _history_has_values(snapshot):
        lines.append("Quarterly trend history is not yet cleanly extracted.")
    if any(_snippet_is_noisy(snippet) for snippets in snapshot.metric_snippets.values() for snippet in snippets):
        lines.append("Some raw snippets are noisy and need better parsing.")
    for label in ("revenue", "net_income", "eps", "gross_margin"):
        if fields[label] == NOT_CLEANLY_EXTRACTED and snapshot.metric_snippets.get(label):
            lines.append(f"{_field_display_name(label)} was mentioned, but the parser did not isolate a clean value.")
            break
    return lines or ["No major missing item was obvious from the loaded result text."]


def _watch_lines(snapshot: ForeignIssuerSnapshot, fields: dict[str, str]) -> list[str]:
    lines = [
        "Gross margin guidance and whether the achieved margin matches the outlook.",
        "Sales guidance range and whether quarterly sales are tracking toward it.",
    ]
    if fields["orders_bookings"] == NOT_CLEANLY_EXTRACTED:
        lines.append("Bookings/orders/backlog when available, because they can lead future sales for equipment companies.")
    else:
        lines.append("Bookings/orders/backlog trend and whether it confirms demand.")
    lines.append("Currency/reporting basis so EUR, USD, IFRS, and US GAAP numbers are not mixed incorrectly.")
    source_text = " ".join(snippet for snippets in snapshot.metric_snippets.values() for snippet in snippets).lower()
    if any(term in source_text for term in ("china", "export control", "export controls", "macro", "demand")):
        lines.append("China/export controls and macro demand, because those themes appeared in loaded source text.")
    else:
        lines.append("China/export controls and macro demand if they appear in future result text.")
    return lines


def _result_verdicts(snapshot: ForeignIssuerSnapshot, fields: dict[str, str]) -> dict[str, str]:
    clean_count = sum(1 for key in ("revenue", "net_income", "eps", "gross_margin", "guidance") if fields[key] != NOT_CLEANLY_EXTRACTED)
    source_quality = _source_quality(snapshot)
    confidence = "High" if source_quality == "Good" and clean_count >= 4 else "Medium" if source_quality in {"Good", "Partial"} and clean_count >= 2 else "Low"
    return {
        "revenue": _verdict_from_trend(snapshot.revenue_trend, fields["revenue"]),
        "profitability": _verdict_from_trend(snapshot.profitability_trend, fields["net_income"], fields["eps"], fields["gross_margin"]),
        "guidance": _verdict_from_guidance(snapshot.guidance_tone, fields["guidance"]),
        "source_quality": source_quality,
        "confidence": confidence,
    }


def _verdict_from_trend(trend: str, *values: str) -> str:
    if any(value != NOT_CLEANLY_EXTRACTED for value in values):
        if trend == "Weak":
            return "Weak"
        if trend == "Improving":
            return "Positive"
        return "Mixed"
    return "Unclear"


def _verdict_from_guidance(guidance_tone: str, value: str) -> str:
    if value == NOT_CLEANLY_EXTRACTED and guidance_tone == "Limited":
        return "Unclear"
    if guidance_tone == "Positive":
        return "Positive"
    if guidance_tone == "Negative":
        return "Weak"
    return "Mixed" if value != NOT_CLEANLY_EXTRACTED else "Unclear"


def _source_quality(snapshot: ForeignIssuerSnapshot) -> str:
    groups = _group_source_links(snapshot.source_links)
    has_official = bool(groups["official"])
    has_sec = bool(groups["sec"])
    loaded = "loaded" in snapshot.source_freshness.lower()
    if has_official and has_sec and loaded:
        return "Good"
    if has_official or has_sec:
        return "Partial"
    return "Weak"


def _verdict_reason(kind: str, verdict: str, fields: dict[str, str]) -> str:
    if kind == "revenue":
        if fields["revenue"] == NOT_CLEANLY_EXTRACTED:
            return "Revenue was not isolated as a clean period/value pair."
        return f"Sales value parsed as {fields['revenue']}."
    if kind == "profitability":
        clean = [fields[key] for key in ("net_income", "eps", "gross_margin") if fields[key] != NOT_CLEANLY_EXTRACTED]
        return "Profitability fields parsed: " + "; ".join(clean[:2]) + "." if clean else "Profitability was mentioned but not isolated cleanly."
    if kind == "guidance":
        return f"Guidance read: {fields['guidance']}." if fields["guidance"] != NOT_CLEANLY_EXTRACTED else "No clean guidance value was isolated."
    if kind == "source_quality":
        return "Official company and SEC foreign issuer sources are both represented." if verdict == "Good" else "Only part of the preferred source stack is loaded or linked."
    if kind == "confidence":
        return "Confidence reflects clean value extraction plus source quality."
    return ""


def _clean_revenue(snapshot: ForeignIssuerSnapshot, period: str = "") -> str:
    return _clean_money_metric(snapshot, "revenue", ("total net sales", "net sales", "revenue", "sales"), period=period)


def _clean_money_metric(
    snapshot: ForeignIssuerSnapshot,
    key: str,
    metric_terms: tuple[str, ...],
    *,
    period: str = "",
    strict_period: bool = False,
) -> str:
    for snippet in _preferred_snippets(snapshot.metric_snippets.get(key, []), period=period, strict_period=strict_period):
        for term in sorted(metric_terms, key=len, reverse=True):
            clean_term = re.escape(term)
            before = re.search(rf"(?P<value>{MONEY_RE})\s+(?P<metric>{clean_term})\b", snippet, flags=re.IGNORECASE)
            after = re.search(rf"\b(?P<metric>{clean_term})\b\s*(?:of|was|were|to|at|:)?\s*(?P<value>{MONEY_RE})", snippet, flags=re.IGNORECASE)
            match = before or after
            if match:
                return f"{_metric_display_name(term)}: {_clean_value(match.group('value'))}"
    return NOT_CLEANLY_EXTRACTED


def _clean_eps(snapshot: ForeignIssuerSnapshot, period: str = "") -> str:
    for snippet in _preferred_snippets(snapshot.metric_snippets.get("eps", []), period=period, strict_period=period.startswith("Q")):
        before = re.search(rf"(?P<value>{MONEY_RE})\s+(?:diluted\s+|basic\s+)?(?:earnings per share|eps)", snippet, flags=re.IGNORECASE)
        after = re.search(rf"(?:diluted\s+|basic\s+)?(?:earnings per share|eps)\s*(?:of|was|were|:)?\s*(?P<value>{MONEY_RE})", snippet, flags=re.IGNORECASE)
        match = before or after
        if match:
            label = "Diluted EPS" if "diluted" in snippet.lower() else "Basic EPS" if "basic" in snippet.lower() else "EPS"
            return f"{label}: {_clean_value(match.group('value'))}"
    return NOT_CLEANLY_EXTRACTED


def _clean_gross_margin(snapshot: ForeignIssuerSnapshot, period: str = "") -> str:
    snippets = _preferred_snippets(snapshot.metric_snippets.get("gross_margin", []) + snapshot.metric_snippets.get("guidance_outlook", []), period=period)
    for snippet in snippets:
        range_match = re.search(rf"gross margin\s+(?:between|of between|range of)?\s*(?P<low>{PERCENT_RE})\s*(?:and|to|-)\s*(?P<high>{PERCENT_RE})", snippet, flags=re.IGNORECASE)
        if range_match:
            return f"Gross margin: {range_match.group('low')} to {range_match.group('high')}"
        before = re.search(rf"(?P<value>{PERCENT_RE})\s+gross margin", snippet, flags=re.IGNORECASE)
        after = re.search(rf"gross margin\s*(?:of|was|at|:)?\s*(?P<value>{PERCENT_RE})", snippet, flags=re.IGNORECASE)
        match = before or after
        if match:
            return f"Gross margin: {match.group('value')}"
    return NOT_CLEANLY_EXTRACTED


def _clean_guidance(snapshot: ForeignIssuerSnapshot, period: str = "") -> str:
    for snippet in _preferred_snippets(snapshot.metric_snippets.get("guidance_outlook", []), period=period):
        sales_range = re.search(
            rf"(?P<year>20\d{{2}})?.{{0,80}}(?:total\s+)?(?:net sales|revenue|sales).{{0,60}}between\s+(?P<low>{MONEY_RE})\s+and\s+(?P<high>{MONEY_RE})",
            snippet,
            flags=re.IGNORECASE,
        )
        margin_range = re.search(rf"gross margin.{0,40}?between\s+(?P<low>{PERCENT_RE})\s+and\s+(?P<high>{PERCENT_RE})", snippet, flags=re.IGNORECASE)
        parts = []
        if sales_range:
            year = sales_range.group("year") or "Latest"
            parts.append(f"{year} sales guidance: {_clean_value(sales_range.group('low'))} to {_clean_value(sales_range.group('high'))}")
        if margin_range:
            parts.append(f"gross margin: {margin_range.group('low')} to {margin_range.group('high')}")
        if parts:
            return "; ".join(parts)
        clean_sentence = _clean_guidance_sentence(snippet)
        if clean_sentence:
            return clean_sentence
    return NOT_CLEANLY_EXTRACTED


def _clean_guidance_sentence(snippet: str) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", _remove_nav_noise(snippet))
    for sentence in sentences:
        lower = sentence.lower()
        if any(term in lower for term in ("guidance", "outlook", "expects", "forecast")) and len(sentence) <= 180:
            return sentence.strip()
    return ""


def _latest_period_detected(snapshot: ForeignIssuerSnapshot) -> str:
    all_text = " ".join(snippet for snippets in snapshot.metric_snippets.values() for snippet in snippets)
    period = _period_from_text(all_text)
    if period:
        return period
    for _label, _date, url in snapshot.source_links:
        period = _period_from_url(url)
        if period:
            return period
    return NOT_CLEANLY_EXTRACTED


def _period_from_text(text: str) -> str:
    match = re.search(r"\b(Q[1-4])\s+(20\d{2})\b", text, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1).upper()} {match.group(2)}"
    match = re.search(r"\b(20\d{2})\s+(?:full-year|full year|annual)\b", text, flags=re.IGNORECASE)
    if match:
        return f"FY {match.group(1)}"
    return ""


def _period_from_url(url: str) -> str:
    match = re.search(r"/q([1-4])-(20\d{2})\b", url.lower())
    if match:
        return f"Q{match.group(1)} {match.group(2)}"
    match = re.search(r"/(20\d{2})(?:\b|/)", url.lower())
    if match and "annual" in url.lower():
        return f"FY {match.group(1)}"
    return ""


def _results_history_rows(snapshot: ForeignIssuerSnapshot, fields: dict[str, str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for label, _date, url in snapshot.source_links:
        if "quarter" not in label.lower() and "/q" not in url.lower():
            continue
        period = _period_from_url(url)
        if not period or period in seen:
            continue
        seen.add(period)
        is_latest = period == fields["period"]
        rows.append(
            {
                "period": period,
                "revenue": fields["revenue"] if is_latest and fields["revenue"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "net_income": fields["net_income"] if is_latest and fields["net_income"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "eps": fields["eps"] if is_latest and fields["eps"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "gross_margin": fields["gross_margin"] if is_latest and fields["gross_margin"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "guidance": fields["guidance"] if is_latest and fields["guidance"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "source": _history_source_label(label, url),
            }
        )
        if len(rows) >= 5:
            break
    if not rows:
        rows.append(
            {
                "period": fields["period"],
                "revenue": fields["revenue"] if fields["revenue"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "net_income": fields["net_income"] if fields["net_income"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "eps": fields["eps"] if fields["eps"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "gross_margin": fields["gross_margin"] if fields["gross_margin"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "guidance": fields["guidance"] if fields["guidance"] != NOT_CLEANLY_EXTRACTED else VALUES_NOT_EXTRACTED,
                "source": _short_label(snapshot.latest_source_label),
            }
        )
    return rows


def _history_has_values(snapshot: ForeignIssuerSnapshot) -> bool:
    return False


def _history_row_text(row: dict[str, str]) -> str:
    return " | ".join(
        _table_cell(row[key])
        for key in ("period", "revenue", "net_income", "eps", "gross_margin", "guidance", "source")
    )


def _table_cell(value: str) -> str:
    clean = re.sub(r"\s+", " ", value).strip()
    if len(clean) > 70:
        clean = clean[:67].rstrip() + "..."
    return clean.replace("|", "/")


def _history_source_label(label: str, url: str) -> str:
    lower = f"{label} {url}".lower()
    if "sec" in lower:
        return "SEC filing"
    if "annual" in lower:
        return "Annual report"
    if "quarter" in lower or "/q" in lower:
        return "IR result page"
    return _short_label(label)


def _group_source_links(source_links: list[tuple[str, str, str]]) -> dict[str, list[tuple[str, str, str]]]:
    groups = {"official": [], "sec": [], "supplemental": []}
    for row in source_links:
        label = row[0].lower()
        if "companyfacts" in label or "xbrl" in label:
            groups["supplemental"].append(row)
        elif label.startswith("sec ") or " sec " in f" {label}":
            groups["sec"].append(row)
        else:
            groups["official"].append(row)
    groups["official"] = groups["official"][:10]
    groups["sec"] = groups["sec"][:8]
    groups["supplemental"] = groups["supplemental"][:4]
    return groups


def _source_lines(rows: list[tuple[str, str, str]]) -> list[str]:
    if not rows:
        return ["Not loaded yet."]
    lines = []
    for label, date, url in rows:
        date_part = "" if not date or date == "--" else f" ({date})"
        lines.append(f"{label}{date_part}: {url or '--'}")
    return lines


def _raw_extract_lines(snapshot: ForeignIssuerSnapshot) -> list[str]:
    lines: list[str] = []
    order = ("revenue", "net_income", "eps", "gross_margin", "guidance_outlook", "orders_bookings", "dividend_buyback", "currency_basis")
    for key in order:
        snippets = snapshot.metric_snippets.get(key, [])
        if not snippets:
            continue
        section_lines = []
        for snippet in snippets[:2]:
            excerpt = _raw_excerpt(snippet)
            if _raw_excerpt_is_noise(excerpt):
                continue
            section_lines.append(f"- {excerpt}")
        if section_lines:
            lines.append(f"{_field_display_name(key)}:")
            lines.extend(section_lines)
    if not lines:
        lines.append("- No raw extracts loaded.")
    return lines


def _raw_excerpt(snippet: str) -> str:
    clean = _remove_nav_noise(snippet)
    clean = re.sub(r"\s+", " ", clean).strip(" -|")
    if len(clean) > 150:
        clean = clean[:147].rstrip() + "..."
    return clean or NOT_CLEANLY_EXTRACTED


def _raw_excerpt_is_noise(excerpt: str) -> bool:
    lower = excerpt.lower()
    if any(
        term in lower
        for term in (
            "news back",
            "customer support",
            "media library",
            "contact media",
            "search search",
            "back financial results overview",
            "return & financing",
        )
    ):
        return not re.search(MONEY_RE, excerpt, flags=re.IGNORECASE) and not re.search(PERCENT_RE, excerpt)
    return False


def _preferred_snippets(snippets: list[str], *, period: str = "", strict_period: bool = False) -> list[str]:
    clean = [_remove_nav_noise(snippet) for snippet in snippets]
    if strict_period:
        clean = [snippet for snippet in clean if _snippet_matches_period(snippet, period)]
    return sorted(clean, key=lambda value: (_snippet_period_priority(value, period), _snippet_is_noisy(value), len(value)))


def _snippet_period_priority(snippet: str, period: str) -> int:
    lower = snippet.lower()
    if _snippet_matches_period(snippet, period):
        return 0
    if re.search(r"\bq[1-4]\s+20\d{2}\b", lower) or "quarter" in lower:
        return 1
    if "reports" in lower or "financial results" in lower:
        return 2
    if "annual" in lower or "full-year" in lower or "full year" in lower:
        return 4
    return 3


def _snippet_matches_period(snippet: str, period: str) -> bool:
    if not period or period == NOT_CLEANLY_EXTRACTED:
        return False
    lower = snippet.lower()
    clean_period = period.lower()
    if clean_period in lower:
        return True
    q_match = re.match(r"q([1-4])\s+(20\d{2})", clean_period)
    if q_match:
        return f"q{q_match.group(1)}-{q_match.group(2)}" in lower or f"first quarter {q_match.group(2)}" in lower
    fy_match = re.match(r"fy\s+(20\d{2})", clean_period)
    return bool(fy_match and fy_match.group(1) in lower and ("annual" in lower or "full-year" in lower or "full year" in lower))


def _remove_nav_noise(snippet: str) -> str:
    value = re.sub(r"\s+", " ", snippet or "").strip()
    lower = value.lower()
    noisy_terms = ("suppliernet", "customernet", "search search home", "home investors", "news back news overview")
    useful_terms = (
        "reports ",
        "q1 ",
        "q2 ",
        "q3 ",
        "q4 ",
        "20",
        "total net sales",
        "net sales",
        "revenue",
        "net income",
        "gross margin",
        "earnings per share",
        "outlook",
        "guidance",
        "order intake",
        "bookings",
        "backlog",
    )
    if any(term in lower for term in noisy_terms):
        starts = [
            lower.find(term)
            for term in useful_terms
            if lower.find(term) >= 0
        ]
        if starts:
            value = value[min(starts):]
    else:
        starts = [lower.find(term) for term in useful_terms if 0 < lower.find(term) <= 80]
        if starts:
            value = value[min(starts):]
    value = re.sub(r"\b(?:SupplierNet|CustomerNet)\b", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\bSearch Search Home\b", "", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def _snippet_is_noisy(snippet: str) -> bool:
    lower = snippet.lower()
    return any(term in lower for term in ("suppliernet", "customernet", "search search", "home investors", "news back news"))


def _clean_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _metric_display_name(term: str) -> str:
    labels = {
        "total net sales": "Total net sales",
        "net sales": "Net sales",
        "revenue": "Revenue",
        "sales": "Sales",
        "net income": "Net income",
        "profit for the period": "Profit for the period",
        "profit attributable": "Profit attributable",
        "income from operations": "Income from operations",
        "order intake": "Order intake",
        "orders": "Orders",
        "net bookings": "Net bookings",
        "bookings": "Bookings",
        "backlog": "Backlog",
    }
    return labels.get(term, term.title())


def _field_display_name(key: str) -> str:
    labels = {
        "revenue": "Revenue",
        "net_income": "Net income",
        "eps": "EPS",
        "gross_margin": "Gross margin",
        "guidance_outlook": "Guidance / outlook",
        "orders_bookings": "Orders / bookings",
        "dividend_buyback": "Dividend / buyback",
        "currency_basis": "Currency / basis",
    }
    return labels.get(key, key.replace("_", " ").title())


def _prefixed_lines(lines: list[str]) -> list[str]:
    return [f"- {line}" for line in lines]


def _profile_strings(quote: dict[str, Any] | None, position_asset_type: str | None, company_title: str) -> list[str]:
    values = []
    if position_asset_type:
        values.append(str(position_asset_type))
    if company_title:
        values.append(company_title)
    values.extend(_strings_for_matching_keys(quote, ("asset", "type", "security", "product", "description", "name", "country", "exchange")))
    return values


def _strings_for_matching_keys(value: Any, key_terms: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            lower_key = str(key).lower()
            if isinstance(child, str) and any(term in lower_key for term in key_terms):
                found.append(child)
            elif isinstance(child, (dict, list)):
                found.extend(_strings_for_matching_keys(child, key_terms))
    elif isinstance(value, list):
        for child in value:
            found.extend(_strings_for_matching_keys(child, key_terms))
    return found


def _metadata_indicates_foreign_issuer(metadata: str) -> bool:
    if any(term in metadata for term in (" ADR", "/ADR", "ADS", "DEPOSITARY", "FOREIGN", "ORDINARY SHARES", "NEW YORK REGISTRY")):
        return True
    if any(term in metadata for term in (" PLC", " N.V.", " NV", " S.A.", " SA", " SE", " AG", " A/S", " LTD", " LIMITED")):
        return True
    if re.search(r"/(CAN|UK|NLD|CH|JP|CN|BR|IE|SG|IL|LU|FR|DE|NL)/", metadata):
        return True
    country_match = re.search(r"\bCOUNTRY\s*[:=]?\s*([A-Z]{2,}|[A-Za-z ]{4,})", metadata)
    return bool(country_match and "UNITED STATES" not in country_match.group(1).upper() and country_match.group(1).upper() != "US")


def _discover_official_links_from_html(raw_html: str, *, base_domain: str) -> list[tuple[str, str, str]]:
    links: list[tuple[str, str, str]] = []
    for href, text in re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw_html):
        url = _absolute_url(href, base_domain)
        if not url.startswith("https://www.asml.com/") and not url.startswith("https://ourbrand.asml.com/"):
            continue
        label_text = html_to_text(text).lower()
        haystack = f"{url.lower()} {label_text}"
        if "financial-results/q" in haystack:
            links.append(("Latest quarterly result page or PDF", "--", url))
        elif "annual-report" in haystack and "Company annual report page" not in [row[0] for row in links]:
            links.append(("Annual report", "--", url))
        elif "presentation" in haystack and "Investor presentation" not in [row[0] for row in links]:
            links.append(("Investor presentation", "--", url))
        elif "press-release" in haystack and "financial-results" in haystack:
            links.append(("Latest quarterly result press release", "--", url))
    return links[:8]


def _absolute_url(href: str, base_domain: str) -> str:
    clean = href.strip()
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean
    if clean.startswith("//"):
        return "https:" + clean
    if clean.startswith("/"):
        return base_domain.rstrip("/") + clean
    return base_domain.rstrip("/") + "/" + clean


def _base_domain(url: str) -> str:
    match = re.match(r"^(https?://[^/]+)", url)
    return match.group(1) if match else ""


def _find_snippets(text: str, keywords: tuple[str, ...], *, limit: int) -> list[str]:
    snippets: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        pattern = re.compile(rf"(.{{0,100}}\b{re.escape(keyword)}\b.{{0,175}})", re.IGNORECASE)
        for match in pattern.finditer(text):
            snippet = _clean_snippet(match.group(1))
            normalized = snippet.lower()
            if len(snippet) < 24 or normalized in seen:
                continue
            seen.add(normalized)
            snippets.append(snippet)
            if len(snippets) >= limit:
                return snippets
    return snippets


def _find_period_snippets(text: str) -> list[str]:
    matches = re.findall(r"\b(?:Q[1-4]|FY|full-year|full year)\s+20\d{2}\b|\b20\d{2}\s+(?:annual|quarterly|first-quarter|second-quarter|third-quarter|fourth-quarter)\b", text, flags=re.IGNORECASE)
    clean = []
    seen = set()
    for match in matches:
        value = match.strip()
        lower = value.lower()
        if lower not in seen:
            seen.add(lower)
            clean.append(value)
        if len(clean) >= 3:
            break
    return clean


def _clean_snippet(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" -|*\t\n")
    if len(value) > 260:
        value = value[:259].rstrip() + "..."
    return value


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _trend_from_snippets(snippets: list[str]) -> str:
    if not snippets:
        return "Limited"
    lower = " ".join(snippets).lower()
    positive = sum(term in lower for term in ("increase", "increased", "growth", "higher", "strong", "improved", "up "))
    negative = sum(term in lower for term in ("decline", "decrease", "decreased", "lower", "weak", "pressure", "down "))
    if positive > negative:
        return "Improving"
    if negative > positive:
        return "Weak"
    return "Mixed"


def _profitability_from_snippets(metric_snippets: dict[str, list[str]]) -> str:
    snippets = []
    for key in ("net_income", "eps", "gross_margin"):
        snippets.extend(metric_snippets.get(key, []))
    return _trend_from_snippets(snippets)


def _guidance_from_snippets(snippets: list[str]) -> str:
    if not snippets:
        return "Limited"
    lower = " ".join(snippets).lower()
    positive = sum(term in lower for term in ("raise", "raised", "reaffirm", "growth", "higher", "strong", "expects"))
    negative = sum(term in lower for term in ("lower", "decline", "weak", "risk", "pressure", "uncertain"))
    if positive > negative:
        return "Positive"
    if negative > positive:
        return "Negative"
    return "Mixed"


def _reporting_basis_label(snippets: list[str], text: str) -> str:
    lower = " ".join(snippets).lower() + " " + text[:6000].lower()
    basis = []
    if "us gaap" in lower:
        basis.append("US GAAP")
    if "ifrs" in lower:
        basis.append("IFRS")
    if "\u20ac" in lower or "eur" in lower or "euro" in lower:
        basis.append("EUR")
    if "$" in lower or "usd" in lower:
        basis.append("USD")
    if not basis:
        return "Not detected"
    return " / ".join(dict.fromkeys(basis))


def _metric_presence_label(snippets: list[str] | None, *, loaded: str = "Loaded") -> str:
    return loaded if snippets else "Not found"


def _source_freshness_label(links: list[tuple[str, str, str]], official_text: str, sec_text: str) -> str:
    has_ir = any("investor" in label.lower() or "annual report" in label.lower() for label, _date, _url in links)
    has_sec = any("sec " in label.lower() or label.lower().startswith("sec") for label, _date, _url in links)
    if official_text.strip() and sec_text.strip():
        return "IR + SEC loaded"
    if official_text.strip():
        return "IR loaded"
    if sec_text.strip():
        return "SEC foreign filings loaded"
    if has_ir and has_sec:
        return "IR + SEC links prepared"
    if has_ir:
        return "IR links prepared"
    return "Search links prepared"


def _latest_source(links: list[tuple[str, str, str]], filings_lines: list[str]) -> tuple[str, str]:
    for label, date, url in links:
        lower = label.lower()
        if ("latest quarterly" in lower or "financial results" in lower or "press release" in lower) and url:
            return label, date or "--"
    for line in filings_lines:
        if line.startswith("6-K"):
            return "SEC 6-K filing", _filing_date_from_line(line)
    for line in filings_lines:
        if line.startswith(("20-F", "40-F")):
            return f"SEC {line.split(' ', 1)[0]} annual filing", _filing_date_from_line(line)
    return "Foreign issuer source mode", "--"


def _snippet_or_status(snapshot: ForeignIssuerSnapshot, key: str, fallback: str) -> str:
    snippets = snapshot.metric_snippets.get(key, [])
    if not snippets:
        return fallback
    return snippets[0]


def _short_label(label: str) -> str:
    if "Official investor relations" in label:
        return "Official IR"
    if "quarterly" in label.lower():
        return "IR Results"
    if "6-K" in label:
        return "6-K Scan"
    if "20-F" in label:
        return "20-F Scan"
    return label[:28]


def _trend_status(label: str) -> str:
    if label == "Improving":
        return "good"
    if label == "Weak":
        return "bad"
    if label in {"Limited", "Not found"}:
        return "info"
    return "mixed"


def _tone_status(label: str) -> str:
    if label == "Positive":
        return "good"
    if label == "Negative":
        return "bad"
    if label == "Limited":
        return "info"
    return "mixed"


def _sec_source_from_line(line: str) -> tuple[str, str, str]:
    form = line.split(" filed ", 1)[0].strip() or "SEC foreign issuer filing"
    date = _filing_date_from_line(line)
    url = ""
    if "http" in line:
        url = "http" + line.split("http", 1)[1].strip()
    return f"SEC {form} filing", date, url


def _filing_date_from_line(line: str) -> str:
    marker = "filed "
    if marker not in line:
        return "--"
    return line.split(marker, 1)[1].split(" ", 1)[0]


def _dedupe_links(links: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    deduped: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, date, url in links:
        key = (label.lower(), url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((label, date or "--", url))
    return deduped


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()
