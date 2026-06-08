from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import quote_plus, urljoin

import requests

from app.data.sec_edgar import SecEarningsRelease, SecEarningsReport, html_to_text

MAX_SNIPPET_CHARS = 260
NASDAQ_EARNINGS_CALENDAR_URL = "https://api.nasdaq.com/api/calendar/earnings?date={date}"
NASDAQ_EARNINGS_PAGE_URL = "https://www.nasdaq.com/market-activity/earnings"

HPE_IR_NEWS_LIBRARY_URL = "https://investors.hpe.com/news-and-events/investor-news-library/{year}"
KNOWN_COMPANY_EARNINGS_SOURCES: dict[str, tuple[tuple[str, str, str], ...]] = {
    "HPE": (
        ("Hewlett Packard Enterprise investor news library", "company_ir", HPE_IR_NEWS_LIBRARY_URL),
        ("Hewlett Packard Enterprise quarterly results", "company_ir", "https://investors.hpe.com/financial/quarterly-results"),
        ("Hewlett Packard Enterprise news releases", "company_press", "https://www.hpe.com/us/en/newsroom.html"),
    ),
    "HPQ": (
        ("HP Inc. investor news", "company_ir", "https://investor.hp.com/news-events/news/default.aspx"),
        ("HP Inc. press releases", "company_press", "https://www.hp.com/us-en/newsroom/press-releases.html"),
    ),
}

DOMESTIC_METRIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "period": ("quarter ended", "fiscal 2026", "fiscal 2025", "first quarter", "second quarter", "third quarter", "fourth quarter"),
    "revenue": ("revenue", "net revenue", "sales"),
    "eps": ("diluted net eps", "diluted eps", "earnings per share", "eps"),
    "net_income": ("net income", "net earnings"),
    "margins": ("gross margin", "operating margin", "profit margin", "gross profit", "operating income"),
    "liquidity_cashflow": ("liquidity", "cash flow", "free cash flow", "operating cash flow", "net cash provided by operating activities", "cash and cash equivalents"),
    "guidance": ("guidance", "outlook", "expects", "forecast", "fiscal year"),
    "dividend_buyback": ("dividend", "dividends", "share repurchase", "share repurchases", "repurchased", "repurchases", "buyback"),
    "segments": ("segment revenue", "platform revenue", "segment", "platform", "data center", "gaming", "automotive", "networking", "server", "servers", "hybrid cloud", "cloud & ai", "ai systems", "backlog", "orders"),
    "mdna_growth_drivers": ("management's discussion", "md&a", "driven by", "primarily due to", "growth was attributable", "increase was due", "demand", "shipments"),
    "risks": ("risk", "risks", "demand", "supply", "export controls", "customer concentration", "tariffs", "inventory", "margin pressure"),
}


@dataclass(frozen=True)
class EarningsCalendarEvent:
    symbol: str
    company_name: str
    event_date: str
    timing: str
    fiscal_quarter_ending: str = ""
    source_label: str = "Nasdaq earnings calendar"
    source_url: str = NASDAQ_EARNINGS_PAGE_URL


@dataclass(frozen=True)
class CompanyEarningsRelease:
    label: str
    date: str
    url: str
    text: str
    source_kind: str = "company_ir"


@dataclass(frozen=True)
class EarningsFreshness:
    event_label: str
    event_date: str
    event_timing: str
    latest_loaded_source_date: str
    latest_sec_filing_date: str
    latest_company_ir_release_date: str
    source_label: str
    source_kind: str
    status: str
    card_label: str
    card_status: str
    verdict: str


@dataclass(frozen=True)
class EarningsReleaseDigest:
    title: str
    filing_date: str
    filing_items: str
    exhibit_type: str
    source_url: str
    headline_snippets: list[str]
    guidance_snippets: list[str]
    margin_cashflow_snippets: list[str]
    symbol: str = ""
    company_name: str = ""
    source_label: str = "SEC 8-K earnings exhibit"
    source_kind: str = "sec_8k"
    source_date: str = ""
    calendar_event_date: str = ""
    calendar_event_timing: str = ""
    latest_sec_filing_date: str = ""
    company_ir_release_date: str = ""
    freshness_event_label: str = "unknown"
    freshness_status: str = "unknown"
    freshness_card_label: str = "SEC Scan"
    freshness_card_status: str = "info"
    freshness_verdict: str = "Same-day earnings freshness check was not loaded."
    metric_snippets: dict[str, list[str]] = field(default_factory=dict)
    source_details: list[tuple[str, str, str]] = field(default_factory=list)
    good_bullets: list[str] = field(default_factory=list)
    bad_missing_bullets: list[str] = field(default_factory=list)
    watch_bullets: list[str] = field(default_factory=list)


def analyze_earnings_release(release: SecEarningsRelease | None) -> EarningsReleaseDigest | None:
    if release is None:
        return None

    text = normalize_release_text(release.text)
    title = _guess_title(text) or f"Latest 8-K earnings exhibit for {release.company.ticker}"
    headline_snippets = _find_snippets(
        text,
        (
            "revenue",
            "net income",
            "diluted earnings per share",
            "diluted eps",
            "earnings per share",
            "gross margin",
            "operating income",
        ),
        limit=5,
    )
    guidance_snippets = _find_snippets(
        text,
        (
            "guidance",
            "outlook",
            "expects",
            "expect",
            "forecast",
            "next quarter",
            "fiscal year",
        ),
        limit=4,
    )
    margin_cashflow_snippets = _find_snippets(
        text,
        (
            "cash flow",
            "free cash flow",
            "operating cash flow",
            "gross margin",
            "operating margin",
            "capital expenditures",
            "capex",
        ),
        limit=4,
    )
    metric_snippets = extract_domestic_earnings_metrics(text)

    return EarningsReleaseDigest(
        title=title,
        filing_date=release.filing.filing_date,
        filing_items=release.filing.items or "--",
        exhibit_type=release.document.type or release.document.description or "exhibit",
        source_url=release.source_url,
        headline_snippets=headline_snippets,
        guidance_snippets=guidance_snippets,
        margin_cashflow_snippets=margin_cashflow_snippets,
        symbol=release.company.ticker,
        company_name=release.company.title,
        source_date=release.filing.filing_date,
        latest_sec_filing_date=release.filing.filing_date,
        metric_snippets=metric_snippets,
        source_details=[("SEC 8-K earnings exhibit", release.filing.filing_date, release.source_url)],
        good_bullets=_good_lines(metric_snippets),
        bad_missing_bullets=_bad_missing_lines(metric_snippets),
        watch_bullets=_watch_lines(release.company.ticker, metric_snippets),
    )


def analyze_earnings_sources(
    symbol: str,
    release: SecEarningsRelease | None,
    *,
    calendar_event: EarningsCalendarEvent | None = None,
    company_release: CompanyEarningsRelease | None = None,
    sec_report: SecEarningsReport | None = None,
    company_name: str = "",
    latest_sec_filing_date: str = "",
    today: date | None = None,
) -> EarningsReleaseDigest | None:
    freshness = build_earnings_freshness(
        symbol,
        calendar_event=calendar_event,
        company_release=company_release,
        sec_release=release,
        sec_report=sec_report,
        latest_sec_filing_date=latest_sec_filing_date,
        today=today,
    )
    use_company_release = company_release is not None and freshness.source_kind in {"company_ir", "company_press"}
    use_sec_release = release is not None and freshness.source_kind == "sec_8k"
    use_sec_report = sec_report is not None and freshness.source_kind == sec_report.source_kind
    if use_company_release:
        source_text = company_release.text
    elif use_sec_release:
        source_text = release.text
    elif use_sec_report:
        source_text = sec_report.text
    else:
        source_text = release.text if release else sec_report.text if sec_report else ""
    if not source_text.strip() and not calendar_event:
        return analyze_earnings_release(release)

    text = normalize_release_text(source_text)
    normalized_symbol = symbol.strip().upper()
    title = _guess_title(text) if text else None
    if title is None:
        title = f"Earnings source check for {normalized_symbol}"

    headline_snippets = _find_snippets(
        text,
        (
            "revenue",
            "net income",
            "diluted earnings per share",
            "diluted eps",
            "earnings per share",
            "gross margin",
            "operating income",
        ),
        limit=5,
    )
    guidance_snippets = _find_snippets(
        text,
        (
            "guidance",
            "outlook",
            "expects",
            "expect",
            "forecast",
            "next quarter",
            "fiscal year",
        ),
        limit=4,
    )
    margin_cashflow_snippets = _find_snippets(
        text,
        (
            "cash flow",
            "free cash flow",
            "operating cash flow",
            "gross margin",
            "operating margin",
            "capital expenditures",
            "capex",
        ),
        limit=4,
    )
    metric_snippets = extract_domestic_earnings_metrics(text)
    source_details = source_detail_rows(
        normalized_symbol,
        today=today,
        calendar_event=calendar_event,
        company_release=company_release,
        sec_release=release,
        sec_report=sec_report,
    )
    active_sec_source = release if use_sec_release else sec_report if use_sec_report else release or sec_report
    filing_date = active_sec_source.filing.filing_date if active_sec_source else "--"
    filing_items = release.filing.items if release else "Formal report" if sec_report else "--"
    if use_sec_release and release:
        exhibit_type = release.document.type or release.document.description
    elif sec_report:
        exhibit_type = sec_report.document.type or sec_report.filing.form
    else:
        exhibit_type = "--"
    source_url = (
        company_release.url
        if use_company_release and company_release
        else release.source_url
        if use_sec_release and release
        else sec_report.source_url
        if use_sec_report and sec_report
        else release.source_url
        if release
        else sec_report.source_url
        if sec_report
        else ""
    )
    company = company_name or (
        release.company.title
        if release
        else sec_report.company.title
        if sec_report
        else calendar_event.company_name
        if calendar_event
        else normalized_symbol
    )

    return EarningsReleaseDigest(
        title=title,
        filing_date=filing_date,
        filing_items=filing_items or "--",
        exhibit_type=exhibit_type or "--",
        source_url=source_url,
        headline_snippets=headline_snippets,
        guidance_snippets=guidance_snippets,
        margin_cashflow_snippets=margin_cashflow_snippets,
        symbol=normalized_symbol,
        company_name=company,
        source_label=freshness.source_label,
        source_kind=freshness.source_kind,
        source_date=freshness.latest_loaded_source_date,
        calendar_event_date=freshness.event_date,
        calendar_event_timing=freshness.event_timing,
        latest_sec_filing_date=freshness.latest_sec_filing_date,
        company_ir_release_date=freshness.latest_company_ir_release_date,
        freshness_event_label=freshness.event_label,
        freshness_status=freshness.status,
        freshness_card_label=freshness.card_label,
        freshness_card_status=freshness.card_status,
        freshness_verdict=freshness.verdict,
        metric_snippets=metric_snippets,
        source_details=source_details,
        good_bullets=_good_lines(metric_snippets),
        bad_missing_bullets=_bad_missing_lines(metric_snippets),
        watch_bullets=_watch_lines(normalized_symbol, metric_snippets),
    )


def build_earnings_freshness(
    symbol: str,
    *,
    calendar_event: EarningsCalendarEvent | None = None,
    company_release: CompanyEarningsRelease | None = None,
    sec_release: SecEarningsRelease | None = None,
    sec_report: SecEarningsReport | None = None,
    latest_sec_filing_date: str = "",
    today: date | None = None,
) -> EarningsFreshness:
    normalized_symbol = symbol.strip().upper()
    today_value = today or date.today()
    event_date = calendar_event.event_date if calendar_event else ""
    event_relation = _event_relation(event_date, today_value) if calendar_event else "unknown"
    event_timing = calendar_event.timing if calendar_event else ""
    sec_source_date = sec_release.filing.filing_date if sec_release else ""
    sec_report_date = sec_report.filing.filing_date if sec_report else ""
    sec_date = latest_sec_filing_date or sec_source_date or sec_report_date
    company_date = company_release.date if company_release else ""

    if company_release is not None and _date_is_fresh_for_event(company_date, event_date, today_value):
        return EarningsFreshness(
            event_label=event_relation,
            event_date=event_date,
            event_timing=event_timing,
            latest_loaded_source_date=company_date,
            latest_sec_filing_date=sec_date or "--",
            latest_company_ir_release_date=company_date,
            source_label=company_release.label,
            source_kind=company_release.source_kind,
            status="fresh",
            card_label="Fresh Release",
            card_status="good",
            verdict=f"Fresh earnings release found: {company_date} ({company_release.label}).",
        )

    if sec_release is not None and _date_is_fresh_for_event(sec_source_date, event_date, today_value):
        return EarningsFreshness(
            event_label=event_relation,
            event_date=event_date,
            event_timing=event_timing,
            latest_loaded_source_date=sec_source_date,
            latest_sec_filing_date=sec_date,
            latest_company_ir_release_date=company_date or "--",
            source_label="SEC 8-K earnings exhibit",
            source_kind="sec_8k",
            status="fresh",
            card_label="Fresh Release",
            card_status="good",
            verdict=f"Fresh earnings release found: {sec_source_date} (SEC 8-K earnings exhibit).",
        )

    near_term_event = event_relation in {"today", "imminent"}
    loaded_date = company_date or sec_source_date or sec_report_date or "--"
    if sec_report is not None:
        return EarningsFreshness(
            event_label=event_relation,
            event_date=event_date or "--",
            event_timing=event_timing,
            latest_loaded_source_date=sec_report_date,
            latest_sec_filing_date=sec_date or sec_report_date,
            latest_company_ir_release_date=company_date or "--",
            source_label=sec_report.source_label,
            source_kind=sec_report.source_kind,
            status="sec_report_fallback",
            card_label=sec_report.analyzed_label,
            card_status="mixed" if near_term_event else "info",
            verdict=_formal_report_fallback_verdict(sec_report),
        )

    if near_term_event and company_release is None and sec_release is None:
        event_text = "today" if event_relation == "today" else f"on {event_date}"
        return EarningsFreshness(
            event_label=event_relation,
            event_date=event_date,
            event_timing=event_timing,
            latest_loaded_source_date=loaded_date,
            latest_sec_filing_date=sec_date or "--",
            latest_company_ir_release_date=company_date or "--",
            source_label="No fresh earnings release loaded",
            source_kind="awaiting_release",
            status="awaiting",
            card_label="Earnings Today / Awaiting Release" if event_relation == "today" else "Earnings Imminent",
            card_status="mixed",
            verdict=f"Earnings expected {event_text}, but no fresh company IR or SEC earnings release was found yet.",
        )

    if near_term_event:
        event_text = "today" if event_relation == "today" else f"on {event_date}"
        pending_sentence = (
            "Earnings expected today, but no fresh company IR or SEC earnings release was found yet."
            if event_relation == "today"
            else "Earnings expected within the next trading day, but no fresh company IR or SEC earnings release was found yet."
        )
        return EarningsFreshness(
            event_label=event_relation,
            event_date=event_date,
            event_timing=event_timing,
            latest_loaded_source_date=loaded_date,
            latest_sec_filing_date=sec_date or "--",
            latest_company_ir_release_date=company_date or "--",
            source_label="Prior loaded earnings source",
            source_kind="stale_prior_source",
            status="stale",
            card_label="Potentially Stale",
            card_status="bad",
            verdict=(
                f"Potentially stale: earnings are expected {event_text}, but latest loaded result is from {loaded_date}. "
                f"{pending_sentence} Re-run after release or check official IR."
            ),
        )

    if sec_release is not None:
        return EarningsFreshness(
            event_label="not near-term" if calendar_event is None else event_relation,
            event_date=event_date or "--",
            event_timing=event_timing,
            latest_loaded_source_date=sec_source_date,
            latest_sec_filing_date=sec_date,
            latest_company_ir_release_date=company_date or "--",
            source_label="SEC 8-K earnings exhibit",
            source_kind="sec_8k",
            status="sec_scan",
            card_label="SEC Scan",
            card_status="info",
            verdict="No same-day or next-trading-day earnings event was detected; using the latest SEC 8-K earnings exhibit.",
        )

    return EarningsFreshness(
        event_label="unknown" if calendar_event is None else event_relation,
        event_date=event_date or "--",
        event_timing=event_timing,
        latest_loaded_source_date="--",
        latest_sec_filing_date=sec_date or "--",
        latest_company_ir_release_date=company_date or "--",
        source_label="No earnings source loaded",
        source_kind="unknown",
        status="unknown",
        card_label="No Fresh Release",
        card_status="info",
        verdict=f"No fresh earnings release was found for {normalized_symbol}.",
    )


def fetch_earnings_calendar_event(
    symbol: str,
    *,
    today: date | None = None,
    timeout_seconds: int = 6,
) -> EarningsCalendarEvent | None:
    normalized_symbol = symbol.strip().upper()
    today_value = today or date.today()
    for target_date in (today_value, _next_trading_day(today_value)):
        try:
            rows = _fetch_nasdaq_calendar_rows(target_date, timeout_seconds=timeout_seconds)
        except Exception:
            continue
        event = _calendar_event_from_rows(normalized_symbol, rows, target_date)
        if event is not None:
            return event
    return None


def fetch_official_company_earnings_release(
    symbol: str,
    *,
    company_name: str = "",
    event_date: str = "",
    today: date | None = None,
    timeout_seconds: int = 5,
) -> CompanyEarningsRelease | None:
    normalized_symbol = symbol.strip().upper()
    today_value = today or date.today()
    sources = [row for row in company_source_links(normalized_symbol, today=today_value) if "google.com/search" not in row[2]]
    if not sources:
        return None

    session = requests.Session()
    headers = {"User-Agent": "PortfolioRiskCockpit earnings freshness"}
    candidates: list[CompanyEarningsRelease] = []
    for label, _date, url in sources:
        source_kind = "company_press" if "press" in label.lower() or "news" in label.lower() else "company_ir"
        try:
            response = session.get(url, headers=headers, timeout=timeout_seconds)
            response.raise_for_status()
        except Exception:
            continue
        release = _extract_company_release_from_html(
            normalized_symbol,
            response.text,
            url,
            label=label,
            source_kind=source_kind,
            company_name=company_name,
            event_date=event_date,
            today=today_value,
        )
        if release is not None:
            candidates.append(release)

    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (_date_sort_value(item.date), _source_priority(item.source_kind)), reverse=True)[0]


def company_source_links(symbol: str, *, today: date | None = None) -> list[tuple[str, str, str]]:
    normalized_symbol = symbol.strip().upper()
    year = (today or date.today()).year
    rows = []
    for label, _kind, url in KNOWN_COMPANY_EARNINGS_SOURCES.get(normalized_symbol, ()):
        rows.append((label, "--", url.format(year=year)))
    if rows:
        return rows
    query = quote_plus(f"{normalized_symbol} investor relations earnings results press release")
    return [("Official IR earnings source search", "--", f"https://www.google.com/search?q={query}")]


def source_detail_rows(
    symbol: str,
    *,
    today: date | None,
    calendar_event: EarningsCalendarEvent | None,
    company_release: CompanyEarningsRelease | None,
    sec_release: SecEarningsRelease | None,
    sec_report: SecEarningsReport | None = None,
) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    if calendar_event is not None:
        rows.append((calendar_event.source_label, calendar_event.event_date, calendar_event.source_url))
    if company_release is not None:
        rows.append((company_release.label, company_release.date, company_release.url))
    rows.extend(company_source_links(symbol, today=today))
    if sec_release is not None:
        rows.append(("SEC 8-K earnings exhibit", sec_release.filing.filing_date, sec_release.source_url))
    if sec_report is not None:
        rows.append((sec_report.source_label, sec_report.filing.filing_date, sec_report.source_url))
    return _dedupe_source_rows(rows)


def format_earnings_release_digest(digest: EarningsReleaseDigest | None) -> str:
    if digest is None:
        return "\n".join(
            [
                "Earnings Release Explanation",
                "============================",
                "",
                "Bottom Line",
                "No recent 8-K earnings-release exhibit was found in the latest SEC filings scan.",
                "",
                "Freshness Check",
                "- Earnings event: unknown",
                "- Loaded source: --",
                "- Latest loaded source date: --",
                "- Latest SEC filing date: --",
                "- Latest company IR release date: --",
                "- Freshness verdict: No recent 8-K earnings-release exhibit was found.",
                "",
                "Latest Quarter Snapshot",
                "The report below falls back to standardized XBRL/companyfacts from 10-Q/10-K style filings.",
            ]
        )

    symbol = digest.symbol or ""
    title_prefix = _digest_title_prefix(digest)
    title = f"{title_prefix} - {symbol}".strip(" -")
    lines = [
        title,
        "=" * len(title),
        "",
        "Bottom Line",
        _bottom_line(digest),
        "",
        "Freshness Check",
        f"- Earnings event: {digest.freshness_event_label}",
        f"- Expected earnings date/time: {_display_date_time(digest.calendar_event_date, digest.calendar_event_timing)}",
        f"- Loaded source: {digest.source_label or '--'}",
        f"- Latest loaded source date: {digest.source_date or '--'}",
        f"- Latest SEC filing date: {digest.latest_sec_filing_date or digest.filing_date or '--'}",
        f"- Latest company IR release date: {digest.company_ir_release_date or '--'}",
        f"- Freshness verdict: {digest.freshness_verdict}",
        "",
        "Latest Quarter Snapshot",
        f"- Period: {_metric_value(digest.metric_snippets, 'period')}",
        f"- Revenue: {_metric_value(digest.metric_snippets, 'revenue')}",
        f"- EPS: {_metric_value(digest.metric_snippets, 'eps')}",
        f"- Net income: {_metric_value(digest.metric_snippets, 'net_income')}",
        f"- Gross margin / operating margin: {_metric_value(digest.metric_snippets, 'margins')}",
        f"- Liquidity / cash flow: {_metric_value(digest.metric_snippets, 'liquidity_cashflow')}",
        f"- Guidance: {_metric_value(digest.metric_snippets, 'guidance')}",
        f"- Buybacks / dividends: {_metric_value(digest.metric_snippets, 'dividend_buyback')}",
        f"- Segment / platform revenue: {_metric_value(digest.metric_snippets, 'segments')}",
        f"- MD&A growth drivers: {_metric_value(digest.metric_snippets, 'mdna_growth_drivers')}",
        f"- Relevant risks: {_metric_value(digest.metric_snippets, 'risks')}",
        "",
        "Good",
        *_prefixed_or_fallback(digest.good_bullets, "No clean positive earnings bullet was extracted from the loaded source."),
        "",
        "Bad / Missing",
        *_prefixed_or_fallback(digest.bad_missing_bullets, "No obvious negative bullet was extracted; still verify the source before trading."),
        "",
        "Watch",
        *_prefixed_or_fallback(digest.watch_bullets, "Watch guidance, margins, source freshness, and the next filing/call transcript."),
        "",
        "Source Details",
    ]
    if digest.source_details:
        lines.extend(_source_detail_lines(digest.source_details))
    else:
        lines.extend(
            [
                f"- Source: {digest.source_url or '--'}",
                f"- Filed: {digest.filing_date} | 8-K items: {digest.filing_items} | Exhibit: {digest.exhibit_type}",
            ]
        )
    raw_sections = _raw_source_excerpt_lines(digest)
    if raw_sections:
        lines.extend(["", "Raw excerpts:", *raw_sections])
    lines.extend(["", _digest_closing_note(digest)])
    return "\n".join(lines)


def extract_domestic_earnings_metrics(text: str) -> dict[str, list[str]]:
    normalized = normalize_release_text(text)
    metrics: dict[str, list[str]] = {}
    for key, keywords in DOMESTIC_METRIC_KEYWORDS.items():
        snippets = _find_snippets(normalized, keywords, limit=4)
        if snippets:
            metrics[key] = [_clean_metric_snippet(snippet) for snippet in snippets]
    return metrics


def normalize_release_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text or " ")
    text = text.replace("\u2022", "-").replace("\u2013", "-").replace("\u2014", "-").replace("\u00e2\u20ac\u00a2", "-")
    return text.strip()


def _append_snippet_bucket(lines: list[str], snippets: list[str]) -> None:
    if not snippets:
        lines.extend(
            [
                "Snippet availability:",
                "No clean snippet found; open the exhibit source for full detail.",
            ]
        )
        return
    for index, snippet in enumerate(snippets, start=1):
        if index > 1:
            lines.append("")
        lines.extend(
            [
                f"Snippet {index}:",
                snippet,
            ]
        )


def _find_snippets(text: str, keywords: tuple[str, ...], *, limit: int) -> list[str]:
    snippets: list[str] = []
    seen_normalized: set[str] = set()
    for keyword in keywords:
        pattern = re.compile(rf"(.{{0,95}}\b{re.escape(keyword)}\b.{{0,165}})", re.IGNORECASE)
        for match in pattern.finditer(text):
            snippet = _clean_snippet(match.group(1))
            normalized = snippet.lower()
            if normalized in seen_normalized or len(snippet) < 30:
                continue
            seen_normalized.add(normalized)
            snippets.append(snippet)
            if len(snippets) >= limit:
                return snippets
    return snippets


def _clean_snippet(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" -\u2013\u2014|\u2022\t\n")
    if len(value) > MAX_SNIPPET_CHARS:
        value = value[: MAX_SNIPPET_CHARS - 1].rstrip() + "..."
    return value


def _guess_title(text: str) -> str | None:
    candidates = re.split(r"(?<=[.!?])\s+|\s{2,}", text[:2000])
    for candidate in candidates[:20]:
        candidate = _clean_snippet(candidate)
        if not candidate:
            continue
        lower = candidate.lower()
        if "results" in lower or "earnings" in lower or "reports" in lower or "announces" in lower:
            return candidate[:120]
    return None


def _fetch_nasdaq_calendar_rows(target_date: date, *, timeout_seconds: int) -> list[dict]:
    url = NASDAQ_EARNINGS_CALENDAR_URL.format(date=target_date.isoformat())
    response = requests.get(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 PortfolioRiskCockpit",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.nasdaq.com",
            "Referer": NASDAQ_EARNINGS_PAGE_URL,
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    payload = response.json()
    rows = ((payload.get("data") or {}).get("rows") or []) if isinstance(payload, dict) else []
    return rows if isinstance(rows, list) else []


def _calendar_event_from_rows(symbol: str, rows: list[dict], target_date: date) -> EarningsCalendarEvent | None:
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_symbol = str(row.get("symbol") or "").strip().upper()
        if row_symbol != symbol:
            continue
        return EarningsCalendarEvent(
            symbol=symbol,
            company_name=str(row.get("name") or symbol),
            event_date=target_date.isoformat(),
            timing=str(row.get("time") or ""),
            fiscal_quarter_ending=str(row.get("fiscalQuarterEnding") or ""),
            source_url=f"{NASDAQ_EARNINGS_PAGE_URL}?date={target_date.isoformat()}",
        )
    return None


def _next_trading_day(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _extract_company_release_from_html(
    symbol: str,
    raw_html: str,
    source_url: str,
    *,
    label: str,
    source_kind: str,
    company_name: str,
    event_date: str,
    today: date,
) -> CompanyEarningsRelease | None:
    text = html_to_text(raw_html)
    if not source_matches_symbol(symbol, company_name, text, source_url):
        return None

    candidates: list[CompanyEarningsRelease] = []
    normalized = normalize_release_text(text)
    for date_text, start in _dated_positions(normalized):
        parsed_date = _parse_source_date(date_text)
        if parsed_date is None:
            continue
        source_date = parsed_date.isoformat()
        if event_date and not _date_is_fresh_for_event(source_date, event_date, today):
            continue
        window = normalized[max(0, start - 220) : start + 1200]
        if not _looks_like_results_release(window):
            continue
        if not source_matches_symbol(symbol, company_name, window, source_url):
            continue
        release_title = _guess_title(window) or label
        candidates.append(
            CompanyEarningsRelease(
                label=release_title,
                date=source_date,
                url=_candidate_url_for_source(raw_html, source_url, release_title, date_text),
                text=window,
                source_kind=source_kind,
            )
        )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: _date_sort_value(item.date), reverse=True)[0]


def source_matches_symbol(symbol: str, company_name: str, text: str, url: str = "") -> bool:
    normalized_symbol = symbol.strip().upper()
    lower_text = normalize_release_text(text).lower()
    lower_url = (url or "").lower()
    lower_company = (company_name or "").lower()

    if normalized_symbol == "HPE":
        if ("hp.com" in lower_url or "investor.hp.com" in lower_url) and "hpe.com" not in lower_url:
            return False
        if "hp inc" in lower_text or "nyse: hpq" in lower_text or " hpq " in f" {lower_text} ":
            return False
        return (
            "hewlett packard enterprise" in lower_text
            or "nyse: hpe" in lower_text
            or " hpe " in f" {lower_text} "
            or "hpe.com" in lower_url
        )
    if normalized_symbol == "HPQ":
        if "hpe.com" in lower_url or "hewlett packard enterprise" in lower_text or "nyse: hpe" in lower_text:
            return False
        return (
            "hp inc" in lower_text
            or "nyse: hpq" in lower_text
            or " hpq " in f" {lower_text} "
            or "hp.com" in lower_url
            or "investor.hp.com" in lower_url
        )

    if normalized_symbol and re.search(rf"(?<![A-Z0-9]){re.escape(normalized_symbol)}(?![A-Z0-9])", text, flags=re.IGNORECASE):
        return True
    company_tokens = [token for token in re.split(r"[^a-z0-9]+", lower_company) if len(token) >= 4]
    return bool(company_tokens and sum(token in lower_text for token in company_tokens[:4]) >= min(2, len(company_tokens)))


def _dated_positions(text: str) -> list[tuple[str, int]]:
    pattern = re.compile(
        r"\b(?:\d{1,2}/\d{1,2}/20\d{2}|20\d{2}-\d{2}-\d{2}|"
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+20\d{2})\b",
        re.IGNORECASE,
    )
    return [(match.group(0), match.start()) for match in pattern.finditer(text)]


def _parse_source_date(value: str) -> date | None:
    clean = value.strip().replace(".", "")
    formats = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y")
    for fmt in formats:
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def _looks_like_results_release(text: str) -> bool:
    lower = text.lower()
    has_results = any(term in lower for term in ("reports", "announces", "financial results", "quarter results", "earnings"))
    has_quarter = any(term in lower for term in ("quarter", "fiscal", "q1", "q2", "q3", "q4"))
    has_metric = any(term in lower for term in ("revenue", "eps", "earnings per share", "net income", "gross margin", "guidance", "outlook"))
    webcast_only = "webcast" in lower and not any(term in lower for term in ("financial results", "revenue", "eps", "net income"))
    return has_results and has_quarter and has_metric and not webcast_only


def _candidate_url_for_source(raw_html: str, source_url: str, title: str, date_text: str) -> str:
    title_terms = [term for term in re.split(r"[^A-Za-z0-9]+", title.lower()) if len(term) >= 5][:4]
    for href, label_html in re.findall(r'(?is)<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', raw_html):
        label_text = html_to_text(label_html).lower()
        if title_terms and all(term in label_text for term in title_terms[:2]):
            return urljoin(source_url, href)
        if date_text in href or date_text.replace("/", "-") in href:
            return urljoin(source_url, href)
    return source_url


def _event_relation(event_date: str, today: date) -> str:
    parsed = _parse_iso_date(event_date)
    if parsed is None:
        return "unknown"
    if parsed == today:
        return "today"
    if parsed == _next_trading_day(today):
        return "imminent"
    if parsed > today:
        return "upcoming"
    return "past"


def _date_is_fresh_for_event(source_date: str, event_date: str, today: date) -> bool:
    parsed_source = _parse_iso_date(source_date)
    if parsed_source is None:
        return False
    parsed_event = _parse_iso_date(event_date)
    if parsed_event is not None:
        return parsed_source >= parsed_event
    return parsed_source >= today


def _formal_report_fallback_verdict(report: SecEarningsReport) -> str:
    form = report.filing.form.upper()
    report_context = "quarterly report" if form == "10-Q" else "annual report" if form == "10-K" else "formal report"
    return f"No 8-K earnings-release exhibit found; using recent SEC {form} financial statements and MD&A as earnings context ({report_context} filed {report.filing.filing_date})."


def _parse_iso_date(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _date_sort_value(value: str) -> date:
    return _parse_iso_date(value) or date.min


def _source_priority(source_kind: str) -> int:
    return {"company_ir": 4, "company_press": 3, "sec_8k": 2, "sec_10q_fallback": 2, "sec_10k_fallback": 2, "transcript": 1}.get(source_kind, 0)


def _dedupe_source_rows(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    deduped: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, row_date, url in rows:
        key = (label.lower(), url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((label, row_date or "--", url))
    return deduped


def _display_date_time(date_value: str, timing: str) -> str:
    if not date_value:
        return "--"
    timing_label = timing.replace("time-", "").replace("-", " ").strip()
    return f"{date_value} {timing_label}".strip()


def _metric_value(metrics: dict[str, list[str]], key: str) -> str:
    snippets = metrics.get(key) or []
    if not snippets:
        return "Not cleanly extracted"
    return snippets[0]


def _good_lines(metrics: dict[str, list[str]]) -> list[str]:
    lines = []
    combined = " ".join(snippet for snippets in metrics.values() for snippet in snippets).lower()
    if any(term in combined for term in ("up ", "increase", "increased", "growth", "strong", "improved", "higher")):
        lines.append("Growth or improvement language appears in the loaded earnings source.")
    if metrics.get("guidance") and any(term in " ".join(metrics["guidance"]).lower() for term in ("raise", "raised", "reaffirm", "higher", "growth")):
        lines.append("Guidance language leans constructive.")
    if metrics.get("liquidity_cashflow"):
        lines.append("Liquidity or cash-flow detail was found; verify whether free cash flow is positive or improving.")
    if metrics.get("segments"):
        lines.append("Segment detail was found, including at least one operating segment or demand indicator.")
    if metrics.get("mdna_growth_drivers"):
        lines.append("MD&A growth-driver language was found in the formal filing context.")
    return lines


def _bad_missing_lines(metrics: dict[str, list[str]]) -> list[str]:
    lines = []
    combined = " ".join(snippet for snippets in metrics.values() for snippet in snippets).lower()
    if any(term in combined for term in ("down ", "decline", "decreased", "lower", "weak", "pressure", "loss")):
        lines.append("Some loaded language points to decline, pressure, weakness, or losses.")
    for key, label in (
        ("revenue", "Revenue"),
        ("eps", "EPS"),
        ("net_income", "Net income"),
        ("margins", "Margin"),
        ("liquidity_cashflow", "Liquidity / cash flow"),
        ("segments", "Segment / platform revenue"),
    ):
        if not metrics.get(key):
            lines.append(f"{label} was not cleanly extracted from the loaded source.")
    return lines[:6]


def _watch_lines(symbol: str, metrics: dict[str, list[str]]) -> list[str]:
    normalized_symbol = symbol.strip().upper()
    lines = [
        "Guidance and margin commentary can matter more than headline revenue around earnings.",
        "Re-check the next SEC 8-K / 10-Q and any call transcript before treating this as final.",
    ]
    if metrics.get("segments") or normalized_symbol == "HPE":
        lines.append("For infrastructure names, watch networking, servers, hybrid cloud, AI systems, backlog, and orders.")
    if metrics.get("liquidity_cashflow"):
        lines.append("Free cash flow and buyback/dividend language can change balance-sheet read-through.")
    if metrics.get("risks"):
        lines.append("Review filing risk language for demand, supply, export controls, concentration, tariffs, inventory, and margin pressure.")
    return lines


def _bottom_line(digest: EarningsReleaseDigest) -> str:
    status = digest.freshness_status
    if status in {"stale", "awaiting"}:
        return "- Stale/Needs Refresh: " + digest.freshness_verdict
    if status == "fresh":
        read = _quality_read(digest.metric_snippets)
        return f"- {read}: {digest.freshness_verdict}"
    if digest.source_kind == "sec_8k":
        read = _quality_read(digest.metric_snippets)
        return f"- {read}: latest loaded read comes from SEC 8-K source data. Confirm whether a newer company IR release exists before trading around earnings."
    if digest.source_kind in {"sec_10q_fallback", "sec_10k_fallback"}:
        read = _quality_read(digest.metric_snippets)
        return f"- {read}: {digest.freshness_verdict}"
    return "- Mixed: source data is incomplete; use this as a starting point, not a final earnings read."


def _digest_title_prefix(digest: EarningsReleaseDigest) -> str:
    if digest.source_kind == "sec_10q_fallback":
        return "SEC 10-Q Earnings Context"
    if digest.source_kind == "sec_10k_fallback":
        return "SEC 10-K Earnings Context"
    return "Earnings Release Explanation"


def _digest_closing_note(digest: EarningsReleaseDigest) -> str:
    if digest.source_kind in {"sec_10q_fallback", "sec_10k_fallback"}:
        return "Use this as formal SEC filing earnings context. It is not an 8-K earnings-release exhibit; reconcile against any later company IR release, 8-K, or call transcript."
    return "Use this as a fast official-source earnings layer. Reconcile against the formal 10-Q/10-K and XBRL facts when those are available."


def _quality_read(metrics: dict[str, list[str]]) -> str:
    combined = " ".join(snippet for snippets in metrics.values() for snippet in snippets).lower()
    positive = sum(term in combined for term in ("up ", "increase", "increased", "growth", "strong", "improved", "higher", "raise", "raised"))
    negative = sum(term in combined for term in ("down ", "decline", "decreased", "lower", "weak", "pressure", "loss"))
    if positive > negative + 1:
        return "Good"
    if negative > positive + 1:
        return "Weak"
    return "Mixed"


def _prefixed_or_fallback(lines: list[str], fallback: str) -> list[str]:
    return [f"- {line}" for line in (lines or [fallback])]


def _source_detail_lines(rows: list[tuple[str, str, str]]) -> list[str]:
    output = []
    for label, row_date, url in rows:
        date_part = row_date or "--"
        output.append(f"- {label} ({date_part}): {url or '--'}")
    return output


def _raw_source_excerpt_lines(digest: EarningsReleaseDigest) -> list[str]:
    snippets = []
    snippets.extend(digest.headline_snippets[:2])
    snippets.extend(digest.guidance_snippets[:2])
    snippets.extend(digest.margin_cashflow_snippets[:2])
    output: list[str] = []
    seen: set[str] = set()
    for snippet in snippets:
        excerpt = _clean_metric_snippet(snippet, limit=180)
        key = excerpt.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(f"- {excerpt}")
        if len(output) >= 5:
            break
    return output


def _clean_metric_snippet(value: str, *, limit: int = 180) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip(" -|")
    if len(clean) > limit:
        clean = clean[: limit - 3].rstrip() + "..."
    return clean or "Not cleanly extracted"
