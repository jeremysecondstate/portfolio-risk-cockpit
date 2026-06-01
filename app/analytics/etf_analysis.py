from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus


ETF_SECURITY_KINDS = {"etf", "fund"}
ETF_SEC_FORMS = (
    "N-1A",
    "N-1A/A",
    "497",
    "497K",
    "497J",
    "N-CSR",
    "N-CSRS",
    "N-PORT",
    "NPORT-P",
    "N-CEN",
    "N-PX",
    "N-30D",
    "N-30B-2",
)

KNOWN_ETF_SYMBOLS = {
    "ARKK",
    "BITO",
    "DIA",
    "GLD",
    "HYG",
    "IBIT",
    "IWM",
    "JEPI",
    "NASA",
    "QQQ",
    "SCHD",
    "SLV",
    "SPY",
    "TLT",
    "VOO",
    "VTI",
    "XLE",
    "XLK",
}

KNOWN_ISSUER_BY_SYMBOL = {
    "ARKK": "ARK",
    "DIA": "State Street Global Advisors",
    "GLD": "State Street Global Advisors",
    "IBIT": "BlackRock iShares",
    "IWM": "BlackRock iShares",
    "JEPI": "J.P. Morgan Asset Management",
    "QQQ": "Invesco",
    "SCHD": "Schwab Asset Management",
    "SLV": "BlackRock iShares",
    "SPY": "State Street Global Advisors",
    "TLT": "BlackRock iShares",
    "VOO": "Vanguard",
    "VTI": "Vanguard",
}


@dataclass(frozen=True)
class ETFHolding:
    symbol: str
    name: str
    weight: str
    sector: str = ""
    country: str = ""


@dataclass(frozen=True)
class ETFResearchSnapshot:
    symbol: str
    name: str
    issuer: str
    security_kind: str
    fund_type: str
    objective: str
    strategy: str
    index_name: str
    expense_ratio: str
    aum: str
    inception_date: str
    distribution_yield: str
    nav: str
    premium_discount: str
    holdings: list[ETFHolding]
    top_holdings: list[ETFHolding]
    sector_exposures: list[tuple[str, str]]
    country_exposures: list[tuple[str, str]]
    source_links: list[tuple[str, str, str]]
    freshness: str
    warnings: list[str]
    liquidity: str
    top_10_weight: str


@dataclass(frozen=True)
class ETFCard:
    title: str
    label: str
    status: str
    why: str


@dataclass(frozen=True)
class ETFReadout:
    document_cards: list[ETFCard]
    structure_cards: list[ETFCard]
    interpretation: list[str]
    risks: list[str]
    source_links: list[tuple[str, str, str]]
    plain_english_text: str


def detect_security_kind(
    symbol: str,
    quote: dict[str, Any] | None = None,
    position_asset_type: str | None = None,
) -> str:
    normalized = _normalize_symbol(symbol)
    position_text = str(position_asset_type or "").strip().upper()
    if position_text in {"FUND", "MUTUAL FUND", "MUTUAL_FUND", "MONEY MARKET", "MONEY_MARKET"}:
        return "fund"
    if position_text in {"ETF", "ETN", "EXCHANGE TRADED FUND", "EXCHANGE_TRADED_FUND"}:
        return "etf"
    source_text = " ".join(_classification_strings(quote, position_asset_type)).upper()

    if any(term in source_text for term in ("MUTUAL_FUND", "MUTUAL FUND", "MONEY_MARKET", "CLOSED_END_FUND", "CLOSED END FUND")):
        return "fund"
    if any(term in source_text for term in ("ETF", "ETN", "EXCHANGE_TRADED_FUND", "EXCHANGE TRADED FUND")):
        return "etf"
    if normalized in KNOWN_ETF_SYMBOLS:
        return "etf"

    description = " ".join(_descriptive_strings(quote)).upper()
    if any(term in description for term in (" ETF", "ETN", "EXCHANGE TRADED FUND")):
        return "etf"
    if any(term in source_text for term in ("COMMON_STOCK", "COMMON STOCK", "EQUITY", "STOCK")):
        return "equity"
    return "unknown"


def build_etf_research_snapshot(
    symbol: str,
    *,
    quote: dict[str, Any] | None = None,
    security_kind: str = "etf",
    sec_filing_lines: list[str] | None = None,
    sec_error: str = "",
) -> ETFResearchSnapshot:
    normalized = _normalize_symbol(symbol)
    name = _quote_text(quote, "description", "shortName", "longName", "name", "productName") or normalized
    issuer = _quote_text(quote, "issuer", "issuerName", "fundFamily") or _issuer_from_symbol_or_name(normalized, name)
    fund_type = _fund_type(normalized, name, security_kind)
    liquidity = _liquidity_read(quote)
    source_links = _manual_etf_source_links(normalized, issuer)
    for line in sec_filing_lines or []:
        label, date, url = _sec_source_from_line(line)
        if url:
            source_links.append((label, date, url))

    warnings = [
        "ETF companyfacts are not applicable. Using ETF/fund document sources instead.",
        "Issuer factsheet, holdings, fees, distributions, and fund reports are the primary research sources.",
    ]
    if sec_error:
        warnings.append(f"SEC fund filing lookup did not resolve automatically: {sec_error}")
    if not sec_filing_lines:
        warnings.append("ETF detected, but issuer/fund documents were not found automatically.")

    return ETFResearchSnapshot(
        symbol=normalized,
        name=name,
        issuer=issuer or "Issuer not loaded",
        security_kind="fund" if security_kind == "fund" else "etf",
        fund_type=fund_type,
        objective="Not loaded until issuer factsheet or prospectus is available.",
        strategy=_strategy_guess(normalized, name, fund_type),
        index_name=_index_guess(normalized, name),
        expense_ratio="Not loaded",
        aum="Not loaded",
        inception_date="Not loaded",
        distribution_yield="Not loaded",
        nav="Not loaded",
        premium_discount="Not loaded",
        holdings=[],
        top_holdings=[],
        sector_exposures=[],
        country_exposures=[],
        source_links=source_links,
        freshness="issuer documents not loaded",
        warnings=warnings,
        liquidity=liquidity,
        top_10_weight="Not loaded",
    )


def build_etf_readout(snapshot: ETFResearchSnapshot) -> ETFReadout:
    top_holding = snapshot.top_holdings[0] if snapshot.top_holdings else None
    top_holding_label = top_holding.symbol or top_holding.name if top_holding else "Not loaded"
    sector_bias = _dominant_exposure(snapshot.sector_exposures) or _theme_bias_from_type(snapshot.fund_type)
    country_bias = _dominant_exposure(snapshot.country_exposures)
    document_cards = [
        ETFCard("ETF Mode", "ETF/Fund", "info", "Company revenue, EPS, margins, and guidance do not apply."),
        ETFCard("Latest Factsheet", "Source link", "info", "Use the issuer factsheet for holdings, expense ratio, AUM, yield, and exposures."),
        ETFCard("Prospectus", "Source link", "info", "Use the summary prospectus for objective, fees, principal strategy, and risks."),
        ETFCard("Shareholder Report", "Source link", "info", "Annual/semiannual reports can show portfolio discussion and schedule of investments."),
        ETFCard("Holdings Freshness", snapshot.freshness.title(), "mixed", "Holdings should come from issuer holdings files, N-PORT, or shareholder reports."),
        ETFCard("Distribution Update", snapshot.distribution_yield, "info", "Distribution yield and dates should come from issuer distribution history."),
    ]
    structure_cards = [
        ETFCard("Fund Type", snapshot.fund_type, "info", "Classified from quote/security type, symbol, and fund description when available."),
        ETFCard("Expense Ratio", snapshot.expense_ratio, "info", "Fee drag comes from issuer factsheet or prospectus."),
        ETFCard("AUM / Assets", snapshot.aum, "info", "AUM helps frame liquidity and fund closure risk."),
        ETFCard("Top Holding", top_holding_label, "info", "Top holdings identify what actually drives the ETF."),
        ETFCard("Top 10 Weight", snapshot.top_10_weight, "mixed", "High top-10 weight means the basket may be concentrated."),
        ETFCard("Sector / Theme Bias", sector_bias, "mixed", "One dominant sector or theme can drive most of the risk."),
        ETFCard("Yield / Distribution", snapshot.distribution_yield, "info", "Yield can reflect credit, option income, leverage, or distribution volatility."),
        ETFCard("Liquidity", snapshot.liquidity, _liquidity_status(snapshot.liquidity), "Volume, AUM, and bid/ask spread determine tradability."),
        ETFCard("Index / Strategy", snapshot.index_name or snapshot.strategy, "info", "Index methodology or active strategy explains how holdings are selected."),
    ]
    interpretation = [
        "This is an ETF/fund, so company revenue, EPS, profitability, and guidance are not the right research questions.",
        "The important checks are holdings, strategy, fees, concentration, liquidity, distributions, and tracking/index behavior.",
        "If top holdings or one sector dominate, the ETF may behave like a concentrated basket rather than a broad diversifier.",
        "If it uses derivatives, leverage, futures, swaps, or options, path dependency, roll costs, funding, and tracking risk matter.",
    ]
    if country_bias:
        interpretation.append(f"Country exposure to watch: {country_bias}.")
    risks = _risk_lines(snapshot)
    plain_english = "\n".join(
        [
            f"ETF Read - {snapshot.symbol}",
            "",
            *[f"- {line}" for line in interpretation],
            "",
            "ETF-specific risks:",
            *[f"- {line}" for line in risks],
        ]
    )
    return ETFReadout(
        document_cards=document_cards,
        structure_cards=structure_cards,
        interpretation=interpretation,
        risks=risks,
        source_links=snapshot.source_links,
        plain_english_text=plain_english,
    )


def format_etf_documents_text(snapshot: ETFResearchSnapshot) -> str:
    lines = [
        f"ETF DOCUMENTS / UPDATES - {snapshot.symbol}",
        "=" * (24 + len(snapshot.symbol)),
        "",
        f"Fund name: {snapshot.name}",
        f"Issuer: {snapshot.issuer}",
        f"Fund type: {snapshot.fund_type}",
        "",
        "ETF research mode:",
        "- Company-style earnings releases, revenue, EPS, margins, and guidance do not apply to ETFs/funds.",
        "- Use issuer fund pages, factsheets, prospectus/summary prospectus, SAI, shareholder reports, N-PORT holdings, N-CEN, N-PX, and distribution history.",
        "- ETF companyfacts are not applicable. Using ETF/fund document sources instead.",
        "",
        "Document status:",
        f"- Factsheet: source link prepared; parsed document not loaded yet.",
        f"- Prospectus: source link prepared; parsed document not loaded yet.",
        f"- Holdings freshness: {snapshot.freshness}.",
        f"- Distribution update: {snapshot.distribution_yield}.",
    ]
    if snapshot.warnings:
        lines.extend(["", "Fallback notes:", *[f"- {warning}" for warning in snapshot.warnings]])
    lines.extend(["", "Source links:", *[f"- {label}: {url or '--'}" for label, _date, url in snapshot.source_links]])
    return "\n".join(lines)


def format_etf_structure_text(snapshot: ETFResearchSnapshot) -> str:
    lines = [
        f"ETF STRUCTURE / HOLDINGS - {snapshot.symbol}",
        "=" * (25 + len(snapshot.symbol)),
        "",
        f"Fund name: {snapshot.name}",
        f"Issuer: {snapshot.issuer}",
        f"Fund type: {snapshot.fund_type}",
        f"Objective: {snapshot.objective}",
        f"Strategy: {snapshot.strategy}",
        f"Index / benchmark: {snapshot.index_name}",
        "",
        "ETF structure fields:",
        f"- Expense ratio: {snapshot.expense_ratio}",
        f"- AUM / assets: {snapshot.aum}",
        f"- Inception date: {snapshot.inception_date}",
        f"- Distribution yield: {snapshot.distribution_yield}",
        f"- NAV: {snapshot.nav}",
        f"- Premium/discount: {snapshot.premium_discount}",
        f"- Liquidity: {snapshot.liquidity}",
        f"- Top 10 holdings weight: {snapshot.top_10_weight}",
        "",
        "Holdings:",
    ]
    if snapshot.top_holdings:
        lines.extend(f"- {holding.symbol or holding.name}: {holding.weight}" for holding in snapshot.top_holdings)
    else:
        lines.append("- Holdings not loaded yet. Use issuer holdings files, factsheet, shareholder report, or N-PORT.")
    lines.extend(
        [
            "",
            "Plain-English ETF read:",
            "- The key questions are holdings, strategy, fees, concentration, liquidity, and distribution profile.",
            "- Broad index ETFs are usually driven by index composition and macro risk appetite.",
            "- Thematic, option-income, bond, commodity, crypto, leveraged, or inverse ETFs need ETF-specific risk checks.",
        ]
    )
    return "\n".join(lines)


def _classification_strings(quote: dict[str, Any] | None, position_asset_type: str | None) -> list[str]:
    values = []
    if position_asset_type:
        values.append(str(position_asset_type))
    values.extend(_strings_for_matching_keys(quote, ("asset", "type", "security", "product", "description", "name")))
    return values


def _descriptive_strings(quote: dict[str, Any] | None) -> list[str]:
    return _strings_for_matching_keys(quote, ("description", "name", "title", "issuer"))


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


def _quote_text(quote: dict[str, Any] | None, *keys: str) -> str:
    if not isinstance(quote, dict):
        return ""
    lowered = {key.lower() for key in keys}
    stack: list[Any] = [quote]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if str(key).lower() in lowered and isinstance(value, str) and value.strip():
                    return value.strip()
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(current, list):
            stack.extend(current)
    return ""


def _issuer_from_symbol_or_name(symbol: str, name: str) -> str:
    if symbol in KNOWN_ISSUER_BY_SYMBOL:
        return KNOWN_ISSUER_BY_SYMBOL[symbol]
    lower = name.lower()
    candidates = (
        ("vanguard", "Vanguard"),
        ("ishares", "BlackRock iShares"),
        ("blackrock", "BlackRock"),
        ("spdr", "State Street Global Advisors"),
        ("schwab", "Schwab Asset Management"),
        ("invesco", "Invesco"),
        ("jpmorgan", "J.P. Morgan Asset Management"),
        ("jp morgan", "J.P. Morgan Asset Management"),
        ("ark", "ARK"),
        ("proshares", "ProShares"),
    )
    for needle, issuer in candidates:
        if needle in lower:
            return issuer
    return ""


def _fund_type(symbol: str, name: str, security_kind: str) -> str:
    lower = f"{symbol} {name}".lower()
    if security_kind == "fund":
        return "Fund"
    if any(term in lower for term in ("treasury", "bond", "credit", "income", "hyg", "tlt")):
        return "Bond / income ETF"
    if any(term in lower for term in ("gold", "silver", "commodity", "gld", "slv")):
        return "Commodity ETF"
    if any(term in lower for term in ("bitcoin", "crypto", "bito", "ibit")):
        return "Crypto ETF"
    if any(term in lower for term in ("covered call", "options", "jepi")):
        return "Option-income ETF"
    if any(term in lower for term in ("leveraged", "inverse", "ultra", "2x", "3x", "bear")):
        return "Leveraged / inverse ETF"
    if symbol in {"SPY", "VOO", "VTI", "QQQ", "DIA", "IWM"}:
        return "Broad-market ETF"
    if any(term in lower for term in ("space", "robotics", "innovation", "thematic", "ark")):
        return "Thematic ETF"
    return "ETF / fund"


def _strategy_guess(symbol: str, name: str, fund_type: str) -> str:
    if symbol == "SPY":
        return "Tracks the S&P 500 Index."
    if symbol == "QQQ":
        return "Tracks the Nasdaq-100 Index."
    if symbol == "IWM":
        return "Tracks the Russell 2000 Index."
    if symbol == "VOO":
        return "Tracks the S&P 500 Index."
    if symbol == "VTI":
        return "Tracks a broad U.S. total-market equity index."
    if "Broad-market" in fund_type:
        return "Broad-market index exposure; confirm index methodology from issuer documents."
    if "Bond" in fund_type:
        return "Bond or income exposure; duration, credit quality, and rate sensitivity matter more than EPS."
    if "Commodity" in fund_type:
        return "Commodity exposure; spot/futures structure, storage, roll, or trust mechanics can drive tracking."
    if "Crypto" in fund_type:
        return "Crypto-linked exposure; custody, futures/spot structure, fees, and volatility dominate the read."
    if "Option-income" in fund_type:
        return "Options-income strategy; upside caps, distribution quality, and volatility regime matter."
    if "Leveraged" in fund_type:
        return "Leveraged or inverse exposure; daily reset and path dependency can dominate holding-period results."
    if "Thematic" in fund_type or "space" in name.lower():
        return "Thematic basket exposure; holdings concentration and theme purity matter."
    return "Not loaded until issuer factsheet, prospectus, or index methodology is available."


def _index_guess(symbol: str, name: str) -> str:
    known = {
        "SPY": "S&P 500 Index",
        "VOO": "S&P 500 Index",
        "QQQ": "Nasdaq-100 Index",
        "IWM": "Russell 2000 Index",
        "DIA": "Dow Jones Industrial Average",
        "VTI": "U.S. total market index",
    }
    if symbol in known:
        return known[symbol]
    lower = name.lower()
    if "s&p 500" in lower or "sp 500" in lower:
        return "S&P 500 Index"
    if "nasdaq-100" in lower or "nasdaq 100" in lower:
        return "Nasdaq-100 Index"
    return "Not loaded"


def _liquidity_read(quote: dict[str, Any] | None) -> str:
    volume = _first_number(quote, "totalVolume", "volume", "regularMarketVolume")
    if volume is None:
        return "Volume/spread not loaded"
    if volume >= 1_000_000:
        return f"Liquid volume ({volume:,.0f})"
    if volume >= 100_000:
        return f"Moderate volume ({volume:,.0f})"
    return f"Thin volume ({volume:,.0f})"


def _first_number(value: Any, *keys: str) -> float | None:
    if isinstance(value, dict):
        lowered = {key.lower() for key in keys}
        for key, child in value.items():
            if str(key).lower() in lowered:
                number = _to_float(child)
                if number is not None:
                    return number
            if isinstance(child, (dict, list)):
                nested = _first_number(child, *keys)
                if nested is not None:
                    return nested
    elif isinstance(value, list):
        for child in value:
            nested = _first_number(child, *keys)
            if nested is not None:
                return nested
    return None


def _manual_etf_source_links(symbol: str, issuer: str) -> list[tuple[str, str, str]]:
    issuer_term = f"{issuer} " if issuer and not issuer.startswith("Issuer") else ""
    searches = [
        ("Official issuer fund page search", f"{issuer_term}{symbol} ETF official issuer fund page"),
        ("Fund factsheet PDF search", f"{issuer_term}{symbol} ETF factsheet pdf"),
        ("Prospectus / summary prospectus search", f"{issuer_term}{symbol} ETF prospectus summary prospectus"),
        ("Statement of Additional Information / SAI search", f"{issuer_term}{symbol} ETF SAI statement of additional information"),
        ("Annual / semiannual shareholder report search", f"{issuer_term}{symbol} ETF annual semiannual shareholder report"),
        ("Holdings file / CSV search", f"{issuer_term}{symbol} ETF holdings csv"),
        ("Index methodology search", f"{issuer_term}{symbol} ETF index methodology"),
        ("Distribution / dividend history search", f"{issuer_term}{symbol} ETF distribution dividend history"),
    ]
    links = [(label, "--", f"https://www.google.com/search?q={quote_plus(query)}") for label, query in searches]
    links.append(("SEC fund filing search", "--", f"https://www.sec.gov/edgar/search/#/q={quote_plus(symbol)}"))
    return links


def _sec_source_from_line(line: str) -> tuple[str, str, str]:
    form = line.split(" filed ", 1)[0].strip() or "SEC fund filing"
    date = "--"
    if " filed " in line:
        date = line.split(" filed ", 1)[1].split(" ", 1)[0]
    url = ""
    if "http" in line:
        url = "http" + line.split("http", 1)[1].strip()
    return f"SEC fund filing {form}", date, url


def _dominant_exposure(rows: list[tuple[str, str]]) -> str:
    if not rows:
        return ""
    label, weight = rows[0]
    return f"{label} {weight}".strip()


def _theme_bias_from_type(fund_type: str) -> str:
    if "Broad-market" in fund_type:
        return "Broad market"
    if "Bond" in fund_type:
        return "Rates / credit"
    if "Commodity" in fund_type:
        return "Commodity"
    if "Crypto" in fund_type:
        return "Crypto"
    if "Option-income" in fund_type:
        return "Options income"
    if "Leveraged" in fund_type:
        return "Leveraged / inverse"
    if "Thematic" in fund_type:
        return "Thematic"
    return "Not loaded"


def _liquidity_status(value: str) -> str:
    lower = value.lower()
    if "liquid" in lower:
        return "good"
    if "thin" in lower:
        return "bad"
    if "moderate" in lower:
        return "mixed"
    return "info"


def _risk_lines(snapshot: ETFResearchSnapshot) -> list[str]:
    risks = [
        f"Concentration risk: top holding and top-10 weights are {snapshot.top_10_weight.lower()}.",
        f"Sector/theme risk: {_theme_bias_from_type(snapshot.fund_type)} exposure may dominate behavior.",
        f"Liquidity risk: {snapshot.liquidity}. Confirm bid/ask spread and AUM before sizing.",
        f"Fee drag: expense ratio is {snapshot.expense_ratio.lower()}; compare it with similar ETFs.",
        f"Yield/distribution risk: distribution yield is {snapshot.distribution_yield.lower()}; high yield can hide option, credit, leverage, or volatility risk.",
        f"Tracking/index risk: {snapshot.index_name or snapshot.strategy}. Confirm methodology and rebalance rules.",
    ]
    if any(term in snapshot.fund_type.lower() for term in ("leveraged", "inverse", "option", "commodity", "crypto")):
        risks.append("Derivative/structure risk: futures, options, leverage, custody, roll, or daily reset mechanics may matter.")
    if any(term in snapshot.fund_type.lower() for term in ("bond", "income")):
        risks.append("Macro sensitivity: rates, duration, inflation, and credit spreads can matter more than company fundamentals.")
    elif any(term in snapshot.fund_type.lower() for term in ("commodity", "crypto")):
        risks.append("Macro sensitivity: dollar, real rates, spot/futures structure, and volatility can dominate returns.")
    else:
        risks.append("Macro sensitivity: rates, inflation, dollar, commodities, and risk appetite can drive the basket.")
    return risks


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace("$", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
