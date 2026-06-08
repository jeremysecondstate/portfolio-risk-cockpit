from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class EarningsMetric:
    label: str
    latest_text: str
    prior_text: str = "--"
    change_text: str = "--"
    read: str = ""
    latest_value: float | None = None
    prior_value: float | None = None


@dataclass(frozen=True)
class EarningsFilingSummary:
    symbol: str
    company_name: str = ""
    source_label: str = ""
    source_date: str = ""
    source_url: str = ""
    headline: str = ""
    metrics: tuple[EarningsMetric, ...] = ()
    platform_rows: tuple[EarningsMetric, ...] = ()
    growth_drivers: tuple[str, ...] = ()
    quality_points: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    capital_return: tuple[str, ...] = ()
    source_notes: tuple[str, ...] = ()
    raw_excerpt: str = ""

    @property
    def has_structured_data(self) -> bool:
        return bool(self.metrics or self.platform_rows or self.growth_drivers or self.risks)


_METRIC_LABELS = (
    "Revenue",
    "Gross profit",
    "Operating income",
    "Net income",
    "Diluted EPS",
    "Operating cash flow",
    "Cash, cash equivalents, and marketable debt securities",
    "Other income (expense), net",
)

_PLATFORM_LABELS = (
    "Data Center",
    "Hyperscale",
    "AI Clouds, Industrial, & Enterprise",
    "Edge Computing",
)


def build_earnings_filing_summary(
    symbol: str,
    text: str,
    *,
    company_name: str = "",
    source_label: str = "",
    source_date: str = "",
    source_url: str = "",
) -> EarningsFilingSummary:
    """Build a plain-English, table-ready summary from SEC 10-Q/10-K/8-K text.

    The parser is deterministic and intentionally conservative: it extracts numbers
    and business/risk language that are already in the filing text, then formats them
    for the Tk cockpit. It is not an LLM layer and does not invent guidance.
    """

    normalized = _normalize(text)
    metrics = _financial_metrics(normalized)
    platforms = _platform_metrics(normalized)
    headline = _headline(symbol, metrics, platforms)
    return EarningsFilingSummary(
        symbol=symbol.strip().upper(),
        company_name=company_name,
        source_label=source_label,
        source_date=source_date,
        source_url=source_url,
        headline=headline,
        metrics=tuple(metrics),
        platform_rows=tuple(platforms),
        growth_drivers=tuple(_growth_drivers(normalized, platforms)),
        quality_points=tuple(_quality_points(normalized, metrics)),
        risks=tuple(_risk_points(normalized)),
        capital_return=tuple(_capital_return_points(normalized)),
        source_notes=tuple(_source_notes(source_label, source_date, source_url)),
        raw_excerpt=_raw_excerpt(text),
    )


def parse_earnings_filing_summary_from_readout(
    symbol: str,
    earnings_text: str,
    fundamentals_text: str = "",
    filings_lines: Iterable[str] | None = None,
) -> EarningsFilingSummary:
    combined = "\n".join(
        part
        for part in (
            earnings_text or "",
            fundamentals_text or "",
            "\n".join(filings_lines or ()),
        )
        if part
    )
    summary = build_earnings_filing_summary(symbol, combined)
    if summary.has_structured_data:
        return summary
    table_summary = _summary_from_formatted_tables(symbol, earnings_text)
    if table_summary.has_structured_data:
        return table_summary
    return summary


def format_earnings_filing_summary(summary: EarningsFilingSummary, *, original_text: str = "") -> str:
    if not summary.has_structured_data:
        return original_text
    title = f"Earnings Filing Readout - {summary.symbol}".strip()
    lines = [title, "=" * min(len(title), 80), "", "Headline", summary.headline or "Structured filing readout is available.", ""]
    if summary.source_label or summary.source_date or summary.source_url:
        lines.extend(
            [
                "Source Freshness",
                f"- Loaded source: {summary.source_label or '--'}",
                f"- Source date: {summary.source_date or '--'}",
                f"- Source URL: {summary.source_url or '--'}",
                "",
            ]
        )
    lines.extend(_markdown_metric_table("Key Financial Snapshot", summary.metrics))
    lines.extend(_markdown_metric_table("Platform / Segment Revenue", summary.platform_rows))
    lines.extend(_bullet_section("What is driving the quarter", summary.growth_drivers))
    lines.extend(_bullet_section("Quality of earnings", summary.quality_points))
    lines.extend(_bullet_section("Risks to watch", summary.risks))
    lines.extend(_bullet_section("Capital return / cash use", summary.capital_return))
    if summary.raw_excerpt:
        lines.extend(["", "Source excerpt", summary.raw_excerpt])
    if original_text:
        lines.extend(["", "Original / raw generated readout", original_text.strip()])
    return "\n".join(lines).strip()


def _financial_metrics(text: str) -> list[EarningsMetric]:
    metrics: list[EarningsMetric] = []
    values: dict[str, EarningsMetric] = {}

    def add(label: str, row: EarningsMetric | None) -> None:
        if row is not None and label not in values:
            values[label] = row
            metrics.append(row)

    revenue = _money_metric(text, "Revenue", (r"Revenue", r"Total revenue"), read="Total sales for the latest reported period.")
    gross_profit = _money_metric(text, "Gross profit", (r"Gross profit",), read="Revenue after direct product/service costs.")
    operating_income = _money_metric(text, "Operating income", (r"Operating income",), read="Core operating profit before interest, taxes, and investment gains/losses.")
    net_income = _money_metric(text, "Net income", (r"Net income",), read="Final profit after taxes and other income/expense.")
    eps = _per_share_metric(text, "Diluted EPS", (r"Diluted", r"Diluted net income per share", r"Net income per diluted share"), read="Profit per diluted share.")
    operating_cash = _money_metric(text, "Operating cash flow", (r"Net cash provided by operating activities", r"Operating cash flow"), read="Cash generated by the business during the period.")
    liquidity = _money_metric(text, "Cash + marketable debt securities", (r"Cash, cash equivalents, and marketable debt securities",), read="Liquid cushion from cash equivalents and marketable debt securities.")
    other_income = _money_metric(text, "Other income / expense", (r"Other income \(expense\), net", r"Other income, net"), read="Non-operating income/expense; check whether this is recurring operating profit.")

    for label, row in (
        ("Revenue", revenue),
        ("Gross profit", gross_profit),
        ("Operating income", operating_income),
        ("Net income", net_income),
        ("Diluted EPS", eps),
        ("Operating cash flow", operating_cash),
        ("Cash + marketable debt securities", liquidity),
        ("Other income / expense", other_income),
    ):
        add(label, row)

    if revenue and gross_profit and revenue.latest_value:
        latest_margin = gross_profit.latest_value / revenue.latest_value * 100
        prior_margin = None
        if gross_profit.prior_value is not None and revenue.prior_value:
            prior_margin = gross_profit.prior_value / revenue.prior_value * 100
        metrics.insert(
            min(2, len(metrics)),
            EarningsMetric(
                "Gross margin",
                f"{latest_margin:.1f}%",
                f"{prior_margin:.1f}%" if prior_margin is not None else "--",
                _change_text(latest_margin, prior_margin, already_pct=True),
                "How much revenue remains after direct costs.",
                latest_margin,
                prior_margin,
            ),
        )
    if revenue and operating_income and revenue.latest_value:
        latest_margin = operating_income.latest_value / revenue.latest_value * 100
        prior_margin = None
        if operating_income.prior_value is not None and revenue.prior_value:
            prior_margin = operating_income.prior_value / revenue.prior_value * 100
        metrics.insert(
            min(4, len(metrics)),
            EarningsMetric(
                "Operating margin",
                f"{latest_margin:.1f}%",
                f"{prior_margin:.1f}%" if prior_margin is not None else "--",
                _change_text(latest_margin, prior_margin, already_pct=True),
                "Core operating profit as a percentage of revenue.",
                latest_margin,
                prior_margin,
            ),
        )
    return metrics


def _platform_metrics(text: str) -> list[EarningsMetric]:
    rows: list[EarningsMetric] = []
    for label in _PLATFORM_LABELS:
        row = _money_metric(text, label, (re.escape(label),), read=_platform_read(label))
        if row is not None and row.latest_value is not None:
            rows.append(row)
    return rows


def _money_metric(text: str, label: str, labels: tuple[str, ...], *, read: str) -> EarningsMetric | None:
    latest, prior = _find_pair_after_label(text, labels)
    if latest is None:
        return None
    return EarningsMetric(label, _format_money_units(latest), _format_money_units(prior), _change_text(latest, prior), read, latest, prior)


def _per_share_metric(text: str, label: str, labels: tuple[str, ...], *, read: str) -> EarningsMetric | None:
    latest, prior = _find_pair_after_label(text, labels)
    if latest is None:
        return None
    return EarningsMetric(label, _format_dollar_per_share(latest), _format_dollar_per_share(prior), _change_text(latest, prior), read, latest, prior)


def _find_pair_after_label(text: str, labels: tuple[str, ...]) -> tuple[float | None, float | None]:
    for label in labels:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){label}(?![A-Za-z0-9])\s+\$?\s*\(?([0-9][0-9,.]*\s*[BM]?)\)?\s+\$?\s*\(?([0-9][0-9,.]*\s*[BM]?)\)?",
            re.IGNORECASE,
        )
        match = pattern.search(text)
        if match:
            return _parse_amount(match.group(1)), _parse_amount(match.group(2))
    return None, None


def _parse_amount(value: str | None) -> float | None:
    if value is None:
        return None
    clean = value.strip().replace("$", "").replace(",", "").replace(" ", "")
    multiplier = 1.0
    if clean.upper().endswith("B"):
        multiplier = 1000.0
        clean = clean[:-1]
    elif clean.upper().endswith("M"):
        clean = clean[:-1]
    try:
        return float(clean) * multiplier
    except ValueError:
        return None


def _change_text(latest: float | None, prior: float | None, *, already_pct: bool = False) -> str:
    if latest is None or prior is None or prior == 0:
        return "--"
    if already_pct:
        return f"{latest - prior:+.1f} pts"
    return f"{(latest - prior) / abs(prior) * 100:+.1f}%"


def _format_money_units(value: float | None) -> str:
    if value is None:
        return "--"
    if abs(value) >= 1000:
        return f"${value / 1000:.1f}B"
    return f"${value:.0f}M"


def _format_dollar_per_share(value: float | None) -> str:
    if value is None:
        return "--"
    return f"${value:.2f}"


def _headline(symbol: str, metrics: list[EarningsMetric], platforms: list[EarningsMetric]) -> str:
    by_label = {metric.label: metric for metric in metrics}
    pieces: list[str] = []
    revenue = by_label.get("Revenue")
    net_income = by_label.get("Net income")
    gross_margin = by_label.get("Gross margin")
    if revenue:
        pieces.append(f"revenue {revenue.latest_text} ({revenue.change_text} YoY)")
    if net_income:
        pieces.append(f"net income {net_income.latest_text} ({net_income.change_text} YoY)")
    if gross_margin:
        pieces.append(f"gross margin {gross_margin.latest_text}")
    data_center = next((row for row in platforms if row.label == "Data Center"), None)
    if data_center:
        pieces.append(f"Data Center {data_center.latest_text} ({data_center.change_text} YoY)")
    if not pieces:
        return f"{symbol.upper()} filing data loaded; structured extraction is limited, so verify the source filing."
    return f"{symbol.upper()} latest filing read: " + "; ".join(pieces) + "."


def _growth_drivers(text: str, platforms: list[EarningsMetric]) -> list[str]:
    lines: list[str] = []
    data_center = next((row for row in platforms if row.label == "Data Center"), None)
    if data_center:
        lines.append(f"Data Center is the main engine at {data_center.latest_text}, {data_center.change_text} versus the comparable period.")
    blackwell = _sentence_with(text, ("blackwell", "majority", "shipments")) or _sentence_with(text, ("blackwell", "revenue"))
    if blackwell:
        lines.append(_clean_sentence(blackwell))
    ai = _sentence_with(text, ("accelerated computing", "ai")) or _sentence_with(text, ("ai infrastructure",))
    if ai:
        lines.append(_clean_sentence(ai))
    edge = next((row for row in platforms if row.label == "Edge Computing"), None)
    if edge:
        lines.append(f"Edge Computing contributed {edge.latest_text}, {edge.change_text} versus the comparable period.")
    return _dedupe(lines)[:5]


def _quality_points(text: str, metrics: list[EarningsMetric]) -> list[str]:
    lines: list[str] = []
    by_label = {metric.label: metric for metric in metrics}
    gross_margin = by_label.get("Gross margin")
    if gross_margin:
        lines.append(f"Gross margin was {gross_margin.latest_text} versus {gross_margin.prior_text}; direct-cost leverage is a major quality signal.")
    operating_income = by_label.get("Operating income")
    net_income = by_label.get("Net income")
    if operating_income and net_income:
        lines.append(f"Operating income was {operating_income.latest_text}; net income was {net_income.latest_text}, so compare core operations with below-the-line items.")
    other_income = by_label.get("Other income / expense")
    if other_income and other_income.latest_value and abs(other_income.latest_value) >= 1000:
        lines.append(f"Other income was {other_income.latest_text}; treat that separately from recurring product/service profit.")
    public_gain = _first_billions(text, r"unrealized gains? on investments in publicly[- ]held equity securities(?: of)?\s+\$?([0-9.]+)\s*billion")
    private_gain = _first_billions(text, r"non-marketable equity securities(?: of)?\s+\$?([0-9.]+)\s*billion")
    if public_gain or private_gain:
        pieces = []
        if public_gain:
            pieces.append(f"{public_gain}")
        if private_gain:
            pieces.append(f"{private_gain}")
        lines.append("Accounting profit included investment gains (" + ", ".join(pieces) + "); this is not the same as chip/system revenue.")
    operating_cash = by_label.get("Operating cash flow")
    if operating_cash:
        lines.append(f"Operating cash flow was {operating_cash.latest_text}, a useful check against headline net income quality.")
    return _dedupe(lines)[:6]


def _risk_points(text: str) -> list[str]:
    lower = text.lower()
    lines: list[str] = []
    concentration = re.search(r"three direct customers represented\s+([0-9]+%)\s*,\s*([0-9]+%)\s*,?\s+and\s+([0-9]+%)\s+of total revenue", text, flags=re.IGNORECASE)
    if concentration:
        lines.append(f"Customer concentration is high: three direct customers represented {concentration.group(1)}, {concentration.group(2)}, and {concentration.group(3)} of revenue.")
    elif "customer concentration" in lower or "limited number of" in lower:
        lines.append("Revenue is concentrated among a limited number of direct or indirect customers; verify concentration language.")
    if "export control" in lower or "china" in lower or "h200" in lower or "h20" in lower:
        sentence = _sentence_with(text, ("export", "china")) or _sentence_with(text, ("h200", "china"))
        lines.append(_clean_sentence(sentence) if sentence else "China/export-control restrictions remain a material demand and competitive-position risk.")
    if "data centers" in lower and "energy" in lower and "capital" in lower:
        sentence = _sentence_with(text, ("data centers", "energy", "capital"))
        lines.append(_clean_sentence(sentence) if sentence else "Future growth depends on data-center, energy, and capital availability for customer AI infrastructure buildouts.")
    manuf = _first_billions(text, r"commitments were\s+\$?([0-9.]+)\s*billion")
    if manuf:
        lines.append(f"Large forward commitments matter: manufacturing/supply/capacity commitments were {manuf}.")
    cloud = _first_billions(text, r"cloud service agreement commitments.*?\$?([0-9.]+)\s*billion")
    if cloud:
        lines.append(f"Cloud service commitments add fixed future cash requirements: {cloud} disclosed.")
    if "market price volatility" in lower and "equity securities" in lower:
        lines.append("Public and private equity investments can swing reported earnings through unrealized gains/losses.")
    return _dedupe(lines)[:7]


def _capital_return_points(text: str) -> list[str]:
    lines: list[str] = []
    buyback = _first_billions(text, r"repurchased\s+[0-9.]+\s+million shares.*?\$?([0-9.]+)\s*billion")
    if buyback:
        lines.append(f"Share repurchases were {buyback} during the period.")
    authorization = _first_billions(text, r"approved an additional\s+\$?([0-9.]+)\s+billion\s+in share repurchase")
    if authorization:
        lines.append(f"The board approved an additional {authorization} buyback authorization.")
    dividend = re.search(r"increased our quarterly cash dividend from\s+\$?([0-9.]+)\s+per share to\s+\$?([0-9.]+)\s+per share", text, flags=re.IGNORECASE)
    if dividend:
        lines.append(f"Quarterly dividend increased from ${float(dividend.group(1)):.2f} to ${float(dividend.group(2)):.2f} per share.")
    return _dedupe(lines)[:5]


def _source_notes(source_label: str, source_date: str, source_url: str) -> list[str]:
    notes = []
    if source_label:
        notes.append(f"Source: {source_label}.")
    if source_date:
        notes.append(f"Filed / loaded date: {source_date}.")
    if source_url:
        notes.append(f"URL: {source_url}.")
    return notes


def _platform_read(label: str) -> str:
    return {
        "Data Center": "Core AI infrastructure / accelerated computing platform revenue.",
        "Hyperscale": "Public cloud and very large internet-company data-center demand.",
        "AI Clouds, Industrial, & Enterprise": "AI factories, sovereign/enterprise/industrial customers, and AI cloud demand.",
        "Edge Computing": "PCs, workstations, robotics/auto/edge devices, and similar endpoints.",
    }.get(label, "Reported platform or segment revenue.")


def _first_billions(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return ""
    try:
        value = float(match.group(1))
    except ValueError:
        return ""
    return f"${value:.1f}B"


def _sentence_with(text: str, terms: tuple[str, ...]) -> str:
    normalized = _normalize(text)
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    for sentence in sentences:
        lower = sentence.lower()
        if all(term.lower() in lower for term in terms):
            return sentence
    return ""


def _clean_sentence(sentence: str) -> str:
    clean = " ".join(str(sentence or "").split()).strip(" -")
    if len(clean) > 260:
        clean = clean[:257].rstrip() + "..."
    return clean


def _dedupe(lines: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in lines:
        clean = _clean_sentence(line)
        if not clean or clean.lower() in seen:
            continue
        seen.add(clean.lower())
        result.append(clean)
    return result


def _raw_excerpt(text: str) -> str:
    clean = _normalize(text)
    if len(clean) <= 900:
        return clean
    anchors = ["First Quarter", "Revenue by Market Platform", "Results of Operations", "Risk Factors"]
    for anchor in anchors:
        index = clean.lower().find(anchor.lower())
        if index >= 0:
            return clean[index : index + 900].strip() + "..."
    return clean[:900].strip() + "..."


def _normalize(text: str) -> str:
    text = re.sub(r"[\t\r\f\v]+", " ", text or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _markdown_metric_table(title: str, rows: tuple[EarningsMetric, ...]) -> list[str]:
    if not rows:
        return []
    lines = [title, "| Metric | Latest | Prior / comparable | Change | Read |", "| --- | ---: | ---: | ---: | --- |"]
    for row in rows:
        lines.append(f"| {row.label} | {row.latest_text} | {row.prior_text} | {row.change_text} | {row.read} |")
    lines.append("")
    return lines


def _bullet_section(title: str, rows: tuple[str, ...]) -> list[str]:
    if not rows:
        return []
    return ["", title, *[f"- {row}" for row in rows]]


def _summary_from_formatted_tables(symbol: str, text: str) -> EarningsFilingSummary:
    metrics: list[EarningsMetric] = []
    platforms: list[EarningsMetric] = []
    in_platform = False
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        lower = line.lower()
        if lower.startswith("platform / segment revenue"):
            in_platform = True
            continue
        if lower.startswith("what is driving") or lower.startswith("quality of earnings") or lower.startswith("risks to watch"):
            in_platform = False
        if not line.startswith("|") or "---" in line or "Metric" in line:
            continue
        pieces = [piece.strip() for piece in line.strip("|").split("|")]
        if len(pieces) < 5:
            continue
        row = EarningsMetric(pieces[0], pieces[1], pieces[2], pieces[3], pieces[4])
        if in_platform or row.label in _PLATFORM_LABELS:
            platforms.append(row)
        else:
            metrics.append(row)
    headline = _extract_section_line(text, "Headline") or _headline(symbol, metrics, platforms)
    return EarningsFilingSummary(
        symbol=symbol.strip().upper(),
        headline=headline,
        metrics=tuple(metrics),
        platform_rows=tuple(platforms),
        growth_drivers=tuple(_extract_bullet_section(text, "What is driving the quarter")),
        quality_points=tuple(_extract_bullet_section(text, "Quality of earnings")),
        risks=tuple(_extract_bullet_section(text, "Risks to watch")),
        capital_return=tuple(_extract_bullet_section(text, "Capital return / cash use")),
        raw_excerpt=_raw_excerpt(text),
    )


def _extract_section_line(text: str, title: str) -> str:
    lines = list((text or "").splitlines())
    for index, line in enumerate(lines):
        if line.strip().lower() == title.lower():
            for candidate in lines[index + 1 : index + 4]:
                clean = candidate.strip()
                if clean:
                    return clean
    return ""


def _extract_bullet_section(text: str, title: str) -> list[str]:
    lines = list((text or "").splitlines())
    result: list[str] = []
    active = False
    for line in lines:
        clean = line.strip()
        if clean.lower() == title.lower():
            active = True
            continue
        if active and clean and not clean.startswith("-") and not clean.startswith("|"):
            break
        if active and clean.startswith("-"):
            result.append(clean[1:].strip())
    return result
