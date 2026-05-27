from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from app.data.sec_edgar import SecCompany


@dataclass(frozen=True)
class FactPoint:
    label: str
    concept: str
    unit: str
    value: float
    fy: int | None
    fp: str
    form: str
    filed: str
    end: str
    accession: str

    @property
    def period_label(self) -> str:
        if self.fy and self.fp:
            return f"FY{self.fy} {self.fp}"
        return self.end or self.filed or "Unknown period"


@dataclass(frozen=True)
class MetricSeries:
    label: str
    points: list[FactPoint]

    @property
    def latest(self) -> FactPoint | None:
        return self.points[-1] if self.points else None

    @property
    def previous(self) -> FactPoint | None:
        return self.points[-2] if len(self.points) >= 2 else None

    @property
    def year_ago(self) -> FactPoint | None:
        return self.points[-5] if len(self.points) >= 5 else None


@dataclass(frozen=True)
class FundamentalReport:
    company: SecCompany
    metrics: dict[str, MetricSeries]
    annual_metrics: dict[str, MetricSeries]
    source_note: str


METRIC_ALIASES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "revenue": (
        "Revenue",
        (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
        ),
        ("USD",),
    ),
    "net_income": (
        "Net income",
        ("NetIncomeLoss", "ProfitLoss"),
        ("USD",),
    ),
    "operating_income": (
        "Operating income",
        ("OperatingIncomeLoss",),
        ("USD",),
    ),
    "diluted_eps": (
        "Diluted EPS",
        ("EarningsPerShareDiluted", "IncomeLossFromContinuingOperationsPerDilutedShare"),
        ("USD/shares", "USD / shares", "USD"),
    ),
    "cash": (
        "Cash & equivalents",
        (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        ("USD",),
    ),
    "assets": (
        "Assets",
        ("Assets",),
        ("USD",),
    ),
    "liabilities": (
        "Liabilities",
        ("Liabilities",),
        ("USD",),
    ),
    "equity": (
        "Stockholders' equity",
        ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        ("USD",),
    ),
    "operating_cash_flow": (
        "Operating cash flow",
        ("NetCashProvidedByUsedInOperatingActivities",),
        ("USD",),
    ),
}

INCOME_STATEMENT_KEYS = {"revenue", "net_income", "operating_income", "diluted_eps", "operating_cash_flow"}
BALANCE_SHEET_KEYS = {"cash", "assets", "liabilities", "equity"}


def analyze_company_facts(company: SecCompany, payload: dict[str, Any]) -> FundamentalReport:
    facts = ((payload.get("facts") or {}).get("us-gaap") or {})
    if not isinstance(facts, dict):
        facts = {}

    metrics: dict[str, MetricSeries] = {}
    annual_metrics: dict[str, MetricSeries] = {}
    for key, (label, aliases, preferred_units) in METRIC_ALIASES.items():
        points = _extract_points(facts, label, aliases, preferred_units)
        quarterly_points = _quarterly_points(points, key)
        annual_points = _annual_points(points)
        metrics[key] = MetricSeries(label=label, points=quarterly_points[-8:])
        annual_metrics[key] = MetricSeries(label=label, points=annual_points[-4:])

    source_note = (
        "Source: SEC companyfacts XBRL JSON. Values are reported company facts; "
        "some issuers use different accounting concepts, so missing lines can be normal."
    )
    return FundamentalReport(company=company, metrics=metrics, annual_metrics=annual_metrics, source_note=source_note)


def format_earnings_snapshot(report: FundamentalReport) -> str:
    lines = [
        f"EARNINGS SNAPSHOT — {report.company.ticker}",
        "=" * (20 + len(report.company.ticker)),
        "",
        f"Company: {report.company.title}",
        f"CIK: {report.company.cik}",
        "",
        "Latest reported fundamentals:",
    ]

    for key in ["revenue", "net_income", "operating_income", "diluted_eps", "cash", "assets", "liabilities", "equity"]:
        metric = report.metrics.get(key)
        latest = metric.latest if metric else None
        if latest is None:
            lines.append(f"- {METRIC_ALIASES[key][0]}: --")
            continue
        change_qoq = percent_change(latest.value, metric.previous.value) if metric and metric.previous else None
        change_yoy = percent_change(latest.value, metric.year_ago.value) if metric and metric.year_ago else None
        suffix_parts = []
        if change_qoq is not None and key in INCOME_STATEMENT_KEYS:
            suffix_parts.append(f"QoQ {format_percent(change_qoq)}")
        if change_yoy is not None and key in INCOME_STATEMENT_KEYS:
            suffix_parts.append(f"YoY {format_percent(change_yoy)}")
        suffix = f" ({', '.join(suffix_parts)})" if suffix_parts else ""
        lines.append(
            f"- {metric.label}: {format_value(latest.value, latest.unit)} "
            f"[{latest.period_label}, filed {latest.filed}]{suffix}"
        )

    lines.extend(
        [
            "",
            "Read-through:",
            *[f"- {line}" for line in build_interpretation_lines(report, compact=True)],
            "",
            report.source_note,
            "This is analysis, not a trade recommendation.",
        ]
    )
    return "\n".join(lines)


def format_fundamental_analysis(report: FundamentalReport) -> str:
    lines = [
        f"FUNDAMENTAL ANALYSIS — {report.company.ticker}",
        "=" * (25 + len(report.company.ticker)),
        "",
        f"Company: {report.company.title}",
        f"CIK: {report.company.cik}",
        "",
        "Quarterly trend table:",
    ]

    for key in ["revenue", "net_income", "operating_income", "diluted_eps"]:
        lines.extend(_format_metric_trend(report.metrics.get(key)))

    lines.extend(["", "Balance-sheet context:"])
    for key in ["cash", "assets", "liabilities", "equity"]:
        lines.extend(_format_metric_trend(report.metrics.get(key), max_points=4))

    lines.extend(["", "Annual context:"])
    for key in ["revenue", "net_income", "operating_cash_flow"]:
        lines.extend(_format_metric_trend(report.annual_metrics.get(key), max_points=4))

    lines.extend(
        [
            "",
            "Cockpit interpretation:",
            *[f"- {line}" for line in build_interpretation_lines(report, compact=False)],
            "",
            "What to verify in the actual filings:",
            "- Check the latest 10-Q/10-K MD&A for management's explanation of revenue, margin, and cash-flow changes.",
            "- Check risk factors and liquidity notes before treating the numbers as a green light.",
            "- For earnings-release color, open the latest 8-K with an earnings exhibit from the SEC Filings button.",
            "",
            report.source_note,
            "This is analysis, not a trade recommendation.",
        ]
    )
    return "\n".join(lines)


def summarize_report_for_holdings(report: FundamentalReport) -> str:
    revenue = report.metrics.get("revenue")
    income = report.metrics.get("net_income")
    cash = report.metrics.get("cash")
    revenue_yoy = _metric_yoy(revenue)
    income_yoy = _metric_yoy(income)
    cash_latest = cash.latest if cash else None

    flags: list[str] = []
    if revenue_yoy is not None:
        flags.append(f"Revenue YoY {format_percent(revenue_yoy)}")
    if income_yoy is not None:
        flags.append(f"Net income YoY {format_percent(income_yoy)}")
    if cash_latest is not None:
        flags.append(f"Cash {format_value(cash_latest.value, cash_latest.unit)}")
    if not flags:
        flags.append("Limited standardized SEC facts found")
    return f"{report.company.ticker}: " + "; ".join(flags)


def build_interpretation_lines(report: FundamentalReport, *, compact: bool) -> list[str]:
    lines: list[str] = []
    revenue = report.metrics.get("revenue")
    income = report.metrics.get("net_income")
    operating_income = report.metrics.get("operating_income")
    cash = report.metrics.get("cash")
    liabilities = report.metrics.get("liabilities")
    assets = report.metrics.get("assets")

    revenue_yoy = _metric_yoy(revenue)
    income_yoy = _metric_yoy(income)
    operating_yoy = _metric_yoy(operating_income)

    if revenue_yoy is None:
        lines.append("Revenue trend is not available from the standardized concepts pulled for this issuer.")
    elif revenue_yoy > 10:
        lines.append(f"Revenue growth is strong on the latest comparable period ({format_percent(revenue_yoy)} YoY).")
    elif revenue_yoy > 0:
        lines.append(f"Revenue is growing, but at a more moderate pace ({format_percent(revenue_yoy)} YoY).")
    else:
        lines.append(f"Revenue is contracting on the latest comparable period ({format_percent(revenue_yoy)} YoY).")

    if income_yoy is None:
        lines.append("Net-income trend is not available from the standardized concepts pulled for this issuer.")
    elif income_yoy > 0:
        lines.append(f"Net income improved versus the comparable period ({format_percent(income_yoy)} YoY).")
    else:
        lines.append(f"Net income weakened versus the comparable period ({format_percent(income_yoy)} YoY).")

    if operating_yoy is not None:
        direction = "expanded" if operating_yoy > 0 else "compressed"
        lines.append(f"Operating income {direction} versus the comparable period ({format_percent(operating_yoy)} YoY).")

    cash_latest = cash.latest if cash else None
    liabilities_latest = liabilities.latest if liabilities else None
    assets_latest = assets.latest if assets else None
    if cash_latest and liabilities_latest:
        cash_to_liabilities = cash_latest.value / abs(liabilities_latest.value) if liabilities_latest.value else None
        if cash_to_liabilities is not None:
            lines.append(f"Cash equals roughly {cash_to_liabilities:.1%} of reported liabilities in the latest snapshot.")
    if assets_latest and liabilities_latest and assets_latest.value:
        liability_ratio = liabilities_latest.value / assets_latest.value
        lines.append(f"Liabilities are roughly {liability_ratio:.1%} of reported assets.")

    if not compact:
        latest_periods = _latest_period_labels(report)
        if latest_periods:
            lines.append("Latest facts used: " + ", ".join(latest_periods) + ".")
        lines.append("Treat this as a first-pass screen; always open the underlying 10-Q/10-K for footnotes and one-time items.")

    return lines[:5] if compact else lines


def _format_metric_trend(metric: MetricSeries | None, *, max_points: int = 5) -> list[str]:
    if metric is None or not metric.points:
        return ["", "--: no standardized points found"]
    lines = ["", f"{metric.label}:"]
    for point in metric.points[-max_points:]:
        lines.append(
            f"- {point.period_label}: {format_value(point.value, point.unit)} "
            f"(form {point.form}, filed {point.filed})"
        )
    latest = metric.latest
    previous = metric.previous
    year_ago = metric.year_ago
    if latest and previous:
        lines.append(f"  Latest sequential change: {format_percent(percent_change(latest.value, previous.value))}")
    if latest and year_ago:
        lines.append(f"  Latest comparable-period change: {format_percent(percent_change(latest.value, year_ago.value))}")
    return lines


def _extract_points(
    facts: dict[str, Any],
    label: str,
    aliases: Iterable[str],
    preferred_units: Iterable[str],
) -> list[FactPoint]:
    for concept in aliases:
        fact = facts.get(concept)
        if not isinstance(fact, dict):
            continue
        units = fact.get("units") or {}
        if not isinstance(units, dict):
            continue
        unit, raw_points = _choose_unit(units, preferred_units)
        if not raw_points:
            continue
        points = [_fact_point_from_raw(label, concept, unit, raw) for raw in raw_points if isinstance(raw, dict)]
        points = [point for point in points if point is not None]
        if points:
            return _dedupe_and_sort(points)
    return []


def _choose_unit(units: dict[str, Any], preferred_units: Iterable[str]) -> tuple[str, list[dict[str, Any]]]:
    for preferred in preferred_units:
        raw_points = units.get(preferred)
        if isinstance(raw_points, list):
            return preferred, raw_points
    for unit, raw_points in units.items():
        if isinstance(raw_points, list):
            return str(unit), raw_points
    return "", []


def _fact_point_from_raw(label: str, concept: str, unit: str, raw: dict[str, Any]) -> FactPoint | None:
    value = _to_float(raw.get("val"))
    if value is None:
        return None
    form = str(raw.get("form") or "").upper()
    if form not in {"10-Q", "10-K", "20-F", "40-F"}:
        return None
    return FactPoint(
        label=label,
        concept=concept,
        unit=unit,
        value=value,
        fy=_to_int(raw.get("fy")),
        fp=str(raw.get("fp") or "").upper(),
        form=form,
        filed=str(raw.get("filed") or ""),
        end=str(raw.get("end") or ""),
        accession=str(raw.get("accn") or ""),
    )


def _quarterly_points(points: list[FactPoint], key: str) -> list[FactPoint]:
    allowed_periods = {"Q1", "Q2", "Q3", "Q4"}
    filtered = [point for point in points if point.fp in allowed_periods]
    if key in BALANCE_SHEET_KEYS:
        return filtered
    framed = [point for point in filtered if _looks_quarterly_frame(point)]
    return framed or filtered


def _annual_points(points: list[FactPoint]) -> list[FactPoint]:
    annual = [point for point in points if point.fp == "FY" or point.form in {"10-K", "20-F", "40-F"}]
    return _dedupe_and_sort(annual)


def _looks_quarterly_frame(point: FactPoint) -> bool:
    return point.fp in {"Q1", "Q2", "Q3", "Q4"}


def _dedupe_and_sort(points: list[FactPoint]) -> list[FactPoint]:
    by_key: dict[tuple[str, str, str, str], FactPoint] = {}
    for point in points:
        key = (point.end, str(point.fy), point.fp, point.form)
        existing = by_key.get(key)
        if existing is None or point.filed >= existing.filed:
            by_key[key] = point
    return sorted(by_key.values(), key=lambda point: (_date_sort_key(point.end), _date_sort_key(point.filed)))


def _latest_period_labels(report: FundamentalReport) -> list[str]:
    labels = []
    for key in ["revenue", "net_income", "cash"]:
        metric = report.metrics.get(key)
        if metric and metric.latest:
            labels.append(f"{metric.label} {metric.latest.period_label}")
    return labels


def _metric_yoy(metric: MetricSeries | None) -> float | None:
    if metric is None or metric.latest is None or metric.year_ago is None:
        return None
    return percent_change(metric.latest.value, metric.year_ago.value)


def percent_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return ((current - previous) / abs(previous)) * 100


def format_percent(value: float | None) -> str:
    if value is None:
        return "--"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def format_value(value: float, unit: str) -> str:
    if "shares" in unit.lower() or abs(value) < 1000:
        return f"{value:,.2f}"
    abs_value = abs(value)
    if abs_value >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:,.2f}T"
    if abs_value >= 1_000_000_000:
        return f"${value / 1_000_000_000:,.2f}B"
    if abs_value >= 1_000_000:
        return f"${value / 1_000_000:,.2f}M"
    return f"${value:,.2f}"


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_sort_key(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except (TypeError, ValueError):
        return datetime.min
