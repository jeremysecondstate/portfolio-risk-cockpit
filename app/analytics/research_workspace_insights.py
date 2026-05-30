from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analytics.stock_research import AdvancedIndicatorSnapshot, PortfolioSymbolContext
from app.macro.models import MacroRelease, MacroSnapshot


@dataclass(frozen=True)
class MacroMetricReadout:
    group: str
    metric: str
    latest_value: str
    prior_value: str
    change: str
    period: str
    source: str
    freshness: str
    simple_read: str
    status: str
    interpretation: str


@dataclass(frozen=True)
class TechnicalNarrative:
    rows: dict[str, str]
    indicator_agreement: str
    agreement_status: str
    agreement_explanation: str
    position_meaning: str


@dataclass(frozen=True)
class OptionCandidate:
    key: str
    group: str
    strategy: str
    expiration: str
    strike: float | None
    option_type: str
    bid: float | None
    ask: float | None
    mark: float | None
    midpoint: float | None
    max_loss: float | None
    max_gain: float | None
    breakeven: float | None
    why: str
    works_if: str
    goes_wrong_if: str
    relation_to_position: str
    confidence: str
    contract_symbol: str
    underlying: str
    underlying_price: float | None


@dataclass(frozen=True)
class CombinedOptionScenarioRow:
    move_label: str
    underlying_price: float
    stock_pnl: float
    option_pnl: float
    combined_pnl: float
    portfolio_impact: float
    read: str


@dataclass(frozen=True)
class EarningsWorkspaceSummary:
    snapshot: dict[str, str]
    guidance_tone: str
    revenue_trend: str
    profitability_trend: str
    risks: list[str]
    source_links: list[tuple[str, str, str]]
    interpretation: list[str]


TERM_HELPERS = {
    "RSI": "RSI measures speed of recent moves. Above 70 can be stretched; below 30 can be washed out.",
    "MACD": "MACD compares fast and slow moving averages to show whether momentum is improving or fading.",
    "ATR": "ATR is the average daily range. Higher ATR means wider normal price swings.",
    "SMA / EMA": "SMA and EMA are moving averages. EMA reacts faster; SMA is smoother.",
    "Bollinger Bands": "Bollinger Bands show a normal range around the 20-day average; touches can mean stretched price.",
    "Support": "Support is a nearby price area where buyers recently showed up.",
    "Resistance": "Resistance is a nearby price area where sellers recently showed up.",
    "Fibonacci retracement": "Fibonacci retracement levels are possible pullback zones between the recent swing low and swing high.",
    "Swing high / swing low": "Swing high and swing low are the recent local peak and trough used to frame the move.",
    "Confirmation": "Confirmation is the exact price/condition that would make the setup look more valid.",
    "Risk line": "The risk line is the price where the setup weakens enough to reassess or reduce exposure.",
}


def build_macro_metric_cards(snapshot: MacroSnapshot | None) -> list[MacroMetricReadout]:
    if snapshot is None:
        return [
            MacroMetricReadout(
                group=group,
                metric="Unavailable",
                latest_value="--",
                prior_value="--",
                change="--",
                period="--",
                source="--",
                freshness="unavailable",
                simple_read="Mixed",
                status="info",
                interpretation="Official macro data was not available. Historical comparison unavailable.",
            )
            for group in ("Inflation", "Labor", "Growth / Consumer", "Rates / Treasury", "Energy")
        ]

    groups = {
        "Inflation": ("CPI", "Core CPI", "PPI"),
        "Labor": ("Payroll", "Unemployment", "Average Hourly"),
        "Growth / Consumer": ("GDP", "Retail", "Personal Consumption", "Consumer"),
        "Rates / Treasury": ("Treasury", "Fed", "Funds", "Yield"),
        "Energy": ("Energy", "Crude", "Gasoline", "Oil"),
    }
    readouts: list[MacroMetricReadout] = []
    for group, terms in groups.items():
        releases = [release for release in snapshot.releases if _matches_metric(release, terms)]
        if not releases and group == "Inflation":
            releases = [release for release in snapshot.releases if release.category == "inflation"]
        elif not releases and group == "Labor":
            releases = [release for release in snapshot.releases if release.category == "labor"]
        elif not releases and group == "Rates / Treasury":
            releases = [release for release in snapshot.releases if release.category in {"treasury", "rates"}]
        elif not releases:
            releases = [release for release in snapshot.releases if release.category in _group_categories(group)]
        readouts.append(_macro_group_readout(group, releases[:3]))
    readouts.append(_overall_macro_readout(readouts))
    return readouts


def inflation_read_from_metrics(readouts: list[MacroMetricReadout]) -> str:
    inflation = next((readout for readout in readouts if readout.group == "Inflation"), None)
    return inflation.simple_read if inflation else "Mixed"


def macro_why_it_matters(symbol: str, sector: str | None, macro_read: str) -> str:
    clean_symbol = symbol.upper()
    clean_sector = (sector or _symbol_sector_guess(clean_symbol)).lower()
    backdrop = macro_read.lower()
    pressure = "headwind" if any(term in backdrop for term in ("hot", "headwind", "strong")) else "tailwind" if "cool" in backdrop or "tailwind" in backdrop else "mixed input"
    if clean_symbol in {"SPY", "QQQ", "DIA", "IWM", "VOO", "VTI"} or "etf" in clean_sector:
        return f"For an ETF/broad-market read, macro is a {pressure}: inflation, rates, and labor shape index multiples and risk appetite."
    if any(term in clean_sector for term in ("technology", "communication", "software", "internet", "growth")):
        return f"For growth/tech, macro is a {pressure}: higher yields can compress valuation multiples, while cooler rates usually help long-duration earnings."
    if any(term in clean_sector for term in ("industrial", "defense", "aerospace")) or clean_symbol in {"NOC", "LMT", "RTX", "GD"}:
        return f"For industrials/defense, watch rates, fiscal spending, and demand visibility. Hot rates can pressure multiples, while stable budgets can cushion revenue."
    if any(term in clean_sector for term in ("consumer", "retail", "restaurant")):
        return f"For consumer names, inflation and labor matter directly: sticky prices can squeeze demand, while strong jobs can support spending."
    return f"For {clean_symbol}, macro is a {pressure}. Treat it as context around the company-specific trend, earnings, and valuation."


def build_technical_narrative(
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    macro_label: str = "Mixed",
) -> TechnicalNarrative:
    confirmation = confirmation_text(indicators)
    risk_line = risk_line_text(indicators)
    support = _money(indicators.support)
    resistance = _money(indicators.resistance)
    volatility = (
        f"{indicators.volatility}; ATR {_money(indicators.atr_14)} means ordinary daily movement can be that wide."
        if indicators.atr_14 is not None
        else f"{indicators.volatility}; ATR is unavailable."
    )
    rows = {
        "Trend": f"{indicators.trend.title()}. Price is being compared with the 20/50/200-day averages.",
        "Momentum": f"{indicators.momentum.title()}. RSI {_number(indicators.rsi_14)} and MACD histogram {_number(indicators.macd_histogram)} frame the read.",
        "Volatility": volatility,
        "Support / Resistance": f"Support near {support}; resistance near {resistance}. Support is where buyers recently appeared; resistance is where sellers recently appeared.",
        "Confirmation Level": confirmation,
        "Invalidation / Risk Line": risk_line,
        "Fibonacci Context": fibonacci_explanation(indicators),
    }
    agreement, status, explanation = indicator_agreement_classification(indicators, macro_label)
    return TechnicalNarrative(
        rows=rows,
        indicator_agreement=agreement,
        agreement_status=status,
        agreement_explanation=explanation,
        position_meaning=current_position_meaning(context),
    )


def confirmation_text(indicators: AdvancedIndicatorSnapshot) -> str:
    level = indicators.resistance or indicators.swing_high
    if level is None:
        return "Confirmation means price closes above a clear resistance level, ideally with stronger volume. A specific level is unavailable."
    return f"Confirmation means a move above {_money(level)}, ideally with stronger volume or a close that holds that level."


def risk_line_text(indicators: AdvancedIndicatorSnapshot) -> str:
    level = indicators.support or indicators.swing_low
    if level is None:
        return "Risk worsens when price loses nearby support. A specific risk line is unavailable."
    return f"Risk worsens below {_money(level)} because price loses nearby support and the setup has less proof buyers are defending it."


def fibonacci_explanation(indicators: AdvancedIndicatorSnapshot | None = None) -> str:
    base = "Fibonacci retracement levels are possible pullback zones between the recent swing low and swing high."
    if indicators is None or not indicators.fibonacci_levels:
        return base + " Historical comparison unavailable."
    levels = ", ".join(f"{label} {_money(value)}" for label, value in list(indicators.fibonacci_levels.items())[:3])
    return f"{base} Nearby levels: {levels}."


def indicator_agreement_classification(indicators: AdvancedIndicatorSnapshot, macro_label: str = "Mixed") -> tuple[str, str, str]:
    trend_bull = indicators.trend == "bullish"
    trend_bear = indicators.trend == "bearish"
    momentum_bull = indicators.momentum == "improving"
    momentum_bear = indicators.momentum == "weakening"
    volume_known = indicators.volume_average_20 is not None
    macro_headwind = "headwind" in macro_label.lower() or "hot" in macro_label.lower()
    if trend_bull and momentum_bull and not macro_headwind:
        return "Bullish", "good", "Trend and momentum agree, and macro is not fighting the setup."
    if trend_bear and momentum_bear:
        return "Bearish", "bad", "Price is below key trend references and momentum is weakening."
    pieces = [
        f"trend is {indicators.trend}",
        f"momentum is {indicators.momentum}",
        "volume context is loaded" if volume_known else "volume context is limited",
        f"macro is {macro_label.lower()}",
    ]
    return "Mixed", "mixed", ", ".join(pieces) + ", so the setup is mixed."


def current_position_meaning(context: PortfolioSymbolContext) -> str:
    if context.is_held:
        down_5 = (context.last_price or 0.0) * -0.05 * context.quantity
        return f"You already own {context.quantity:g} shares, so a -5% move would cost about {_money(down_5)} before any options hedge."
    return f"{context.symbol} is not currently held, so the signals describe a possible new trade rather than risk in an existing position."


def suggest_option_candidates(
    chain_rows: list[dict[str, Any]],
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    *,
    macro_label: str = "Mixed",
    earnings_text: str = "",
) -> list[OptionCandidate]:
    if not chain_rows:
        return []
    underlying = context.last_price or indicators.latest_close or _first_underlying(chain_rows)
    if underlying is None or underlying <= 0:
        return []

    agreement, _status, _why = indicator_agreement_classification(indicators, macro_label)
    earnings_soon = "soon" in earnings_text.lower() or "8-k" in earnings_text.lower()
    high_vol = indicators.volatility == "elevated"
    rows = sorted(chain_rows, key=lambda row: (row.get("dte") or 9999, abs(float(row.get("strike") or 0) - underlying)))

    candidates: list[OptionCandidate] = []
    if agreement == "Bullish":
        conservative = _candidate_from_row(_best_row(rows, underlying, "call", 0.0, 0.06), "call", "Recommended", "Conservative bullish call", context, underlying)
        speculative = _candidate_from_row(_best_row(rows, underlying, "call", 0.05, 0.14), "call", "Watchlist", "Speculative bullish call", context, underlying)
        for candidate in (conservative, speculative):
            if candidate:
                candidates.append(_with_candidate_reason(candidate, indicators, macro_label, earnings_soon, high_vol))
    elif agreement == "Bearish":
        bearish = _candidate_from_row(_best_row(rows, underlying, "put", -0.08, 0.0), "put", "Recommended", "Bearish put", context, underlying)
        if bearish:
            candidates.append(_with_candidate_reason(bearish, indicators, macro_label, earnings_soon, high_vol))
    else:
        wait = OptionCandidate(
            key="wait",
            group="Recommended",
            strategy="No-trade / wait",
            expiration="--",
            strike=None,
            option_type="--",
            bid=None,
            ask=None,
            mark=None,
            midpoint=None,
            max_loss=0.0,
            max_gain=None,
            breakeven=None,
            why=f"Indicator agreement is mixed and macro reads {macro_label.lower()}.",
            works_if="Patience works if price reaches confirmation or risk improves before capital is committed.",
            goes_wrong_if="The setup can move without you; the trade-off is missing an unconfirmed move.",
            relation_to_position=current_position_meaning(context),
            confidence="Watch",
            contract_symbol="",
            underlying=context.symbol,
            underlying_price=underlying,
        )
        candidates.append(wait)
        small = _candidate_from_row(_best_row(rows, underlying, "call", 0.0, 0.05), "call", "Watchlist", "Small-risk bullish call", context, underlying)
        if small:
            candidates.append(_with_candidate_reason(small, indicators, macro_label, earnings_soon, high_vol, confidence="Speculative"))

    if context.is_held:
        hedge = _candidate_from_row(_best_row(rows, underlying, "put", -0.10, -0.01), "put", "Watchlist", "Protective put / hedge", context, underlying)
        covered = _candidate_from_row(_best_row(rows, underlying, "call", 0.03, 0.12), "call", "Watchlist", "Income / covered-call candidate", context, underlying, credit=True)
        for candidate in (hedge, covered):
            if candidate:
                candidates.append(_with_candidate_reason(candidate, indicators, macro_label, earnings_soon, high_vol))

    deduped: list[OptionCandidate] = []
    seen: set[tuple[str, str, float | None]] = set()
    for candidate in candidates:
        key = (candidate.strategy, candidate.expiration, candidate.strike)
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped[:5]


def option_midpoint(bid: float | None, ask: float | None, mark: float | None = None) -> float | None:
    if bid is not None and ask is not None and bid >= 0 and ask > 0:
        return round((bid + ask) / 2, 2)
    if mark is not None and mark > 0:
        return round(mark, 2)
    if ask is not None and ask > 0:
        return round(ask, 2)
    return None


def option_expiration_payoff(candidate: OptionCandidate, underlying_price: float, *, contracts: int = 1) -> float:
    if candidate.strike is None or candidate.midpoint is None or candidate.option_type not in {"call", "put"}:
        return 0.0
    multiplier = max(contracts, 1) * 100
    if candidate.option_type == "call":
        intrinsic = max(underlying_price - candidate.strike, 0.0)
    else:
        intrinsic = max(candidate.strike - underlying_price, 0.0)
    return (intrinsic - candidate.midpoint) * multiplier


def combined_option_scenarios(
    candidate: OptionCandidate | None,
    context: PortfolioSymbolContext,
    moves: tuple[float, ...] = (-0.10, -0.05, -0.03, -0.02, 0.02, 0.03, 0.05, 0.10),
) -> list[CombinedOptionScenarioRow]:
    if candidate is None or candidate.underlying_price is None:
        return []
    rows: list[CombinedOptionScenarioRow] = []
    base = candidate.underlying_price
    total = max(context.portfolio_value, 0.01)
    for move in moves:
        price = base * (1 + move)
        stock_pnl = (price - base) * context.quantity if context.is_held else 0.0
        option_pnl = option_expiration_payoff(candidate, price)
        combined = stock_pnl + option_pnl
        if combined > 0:
            read = "Helps"
        elif combined < 0:
            read = "Hurts"
        else:
            read = "Flat"
        rows.append(CombinedOptionScenarioRow(f"{move:+.0%}", price, stock_pnl, option_pnl, combined, combined / total, read))
    return rows


def ticket_fields_for_option_candidate(candidate: OptionCandidate) -> dict[str, str]:
    if candidate.option_type not in {"call", "put"}:
        return {}
    covered_call = "covered" in candidate.strategy.lower()
    return {
        "symbol": candidate.underlying,
        "strategy": "Covered Call" if covered_call else "Long Put" if candidate.option_type == "put" else "Long Call",
        "action": "Sell" if covered_call else "Buy",
        "expiration": candidate.expiration,
        "option_type": "Put" if candidate.option_type == "put" else "Call",
        "order_type": "LIMIT",
        "time_in_force": "Day",
        "contracts": "1",
        "strike": "" if candidate.strike is None else _format_plain_number(candidate.strike),
        "short_strike": "" if candidate.strike is None else _format_plain_number(candidate.strike),
        "bid": "" if candidate.bid is None else _format_plain_number(candidate.bid),
        "ask": "" if candidate.ask is None else _format_plain_number(candidate.ask),
        "mark": "" if candidate.mark is None else _format_plain_number(candidate.mark),
        "premium": "" if candidate.midpoint is None else _format_plain_number(candidate.midpoint),
        "credit": "0",
    }


def build_earnings_workspace_summary(
    symbol: str,
    earnings_text: str,
    fundamentals_text: str,
    filings_lines: list[str],
) -> EarningsWorkspaceSummary:
    latest_8k = next((line for line in filings_lines if line.startswith("8-K")), "")
    latest_qk = next((line for line in filings_lines if line.startswith("10-Q") or line.startswith("10-K")), "")
    source_links = []
    for line in filings_lines[:8]:
        label, url = _split_source_line(line)
        source_links.append((label, _filing_date_from_line(line), url))
    if "companyfacts" in fundamentals_text.lower() or fundamentals_text.strip():
        source_links.append(("SEC companyfacts / XBRL", "latest loaded", "https://data.sec.gov/api/xbrl/companyfacts/"))
    revenue = _trend_from_text(fundamentals_text, "revenue")
    profitability = _trend_from_text(fundamentals_text, "net income", "operating income", "eps", "margin")
    guidance = _guidance_tone(earnings_text)
    interpretation = [
        f"Revenue trend looks {revenue.lower()}.",
        f"Margins/profitability appear {profitability.lower()}.",
        f"Guidance language is {guidance.lower()}.",
    ]
    if "no recent 8-k earnings-release exhibit" in earnings_text.lower() or "unavailable" in earnings_text.lower():
        interpretation.append("The latest filing scan does not appear to include a fresh earnings release.")
    risks = _earnings_risks(earnings_text, fundamentals_text)
    return EarningsWorkspaceSummary(
        snapshot={
            "Company": symbol.upper(),
            "Latest earnings release": latest_8k or "No earnings exhibit found",
            "Latest 10-Q / 10-K": latest_qk or "No 10-Q/10-K fallback found",
            "Reporting period": _reporting_period_from_line(latest_qk or latest_8k) or "--",
            "Source": "SEC filings and companyfacts",
        },
        guidance_tone=guidance,
        revenue_trend=revenue,
        profitability_trend=profitability,
        risks=risks,
        source_links=source_links,
        interpretation=interpretation,
    )


def _macro_group_readout(group: str, releases: list[MacroRelease]) -> MacroMetricReadout:
    if not releases:
        return MacroMetricReadout(group, "Unavailable", "--", "--", "--", "--", "--", "unavailable", "Mixed", "info", "Historical comparison unavailable.")
    primary = releases[0]
    latest = _format_macro_value(primary.actual, primary.unit)
    prior = _format_macro_value(primary.prior, primary.unit)
    change_value = None if primary.actual is None or primary.prior is None else primary.actual - primary.prior
    change = "--" if change_value is None else f"{change_value:+.2f}"
    simple, status = _macro_simple_read(primary)
    interpretation = _macro_interpretation(primary, simple)
    return MacroMetricReadout(group, primary.metric, latest, prior, change, primary.period or "--", primary.source, primary.freshness_status, simple, status, interpretation)


def _overall_macro_readout(readouts: list[MacroMetricReadout]) -> MacroMetricReadout:
    hot = sum(1 for readout in readouts if readout.simple_read in {"Hot", "Strong", "Headwind"})
    cool = sum(1 for readout in readouts if readout.simple_read in {"Cool", "Weak", "Tailwind"})
    if hot > cool:
        simple, status, text = "Headwind", "bad", "Inflation/rates or demand pressure is elevated, so entries need more discipline."
    elif cool > hot:
        simple, status, text = "Tailwind", "good", "Macro pressure is cooling enough to support risk appetite if growth holds up."
    else:
        simple, status, text = "Mixed", "mixed", "Macro signals conflict; symbol-level trend and earnings should carry more weight."
    return MacroMetricReadout("Overall Macro Backdrop", "Composite", "--", "--", "--", "--", "Official sources", "fresh/cache", simple, status, text)


def _macro_simple_read(release: MacroRelease) -> tuple[str, str]:
    if release.actual is None or release.prior is None:
        return "Mixed", "info"
    delta = release.actual - release.prior
    if abs(delta) < 0.0001:
        return "Mixed", "mixed"
    metric = release.metric.lower()
    category = release.category.lower()
    rising = delta > 0
    if "unemployment" in metric:
        return ("Weak", "bad") if rising else ("Strong", "good")
    if "treasury yield" in metric or category in {"treasury", "rates"}:
        return ("Headwind", "bad") if rising else ("Tailwind", "good")
    if category == "inflation" or any(term in metric for term in ("cpi", "ppi", "price")):
        return ("Hot", "bad") if rising else ("Cool", "good")
    if category in {"labor", "growth", "consumer"}:
        return ("Strong", "mixed") if rising else ("Weak", "bad")
    if category == "energy":
        return ("Hot", "bad") if rising else ("Cool", "good")
    return ("Strong", "mixed") if rising else ("Weak", "mixed")


def _macro_interpretation(release: MacroRelease, simple: str) -> str:
    actual = _format_macro_value(release.actual, release.unit)
    prior = _format_macro_value(release.prior, release.unit)
    metric = release.metric.lower()
    if "core cpi" in metric:
        return f"Core CPI moved from {prior} to {actual}. Core excludes food and energy; sticky core inflation can keep the Fed cautious."
    if "cpi" in metric:
        return f"CPI moved from {prior} to {actual}. That means inflation pressure is {simple.lower()} versus the prior reading."
    if "ppi" in metric:
        return f"PPI moved from {prior} to {actual}. PPI can hint at future business cost pressure and possible margin pressure."
    if "payroll" in metric:
        return f"Payrolls moved from {prior} to {actual}. Stronger hiring can support demand but may also keep rates higher."
    if "treasury" in metric or "yield" in metric:
        return f"Treasury yields moved from {prior} to {actual}. Rising yields usually pressure long-duration growth stocks."
    return f"{release.metric} moved from {prior} to {actual}. Historical comparison is limited to actual versus prior."


def _matches_metric(release: MacroRelease, terms: tuple[str, ...]) -> bool:
    haystack = f"{release.metric} {release.category}".lower()
    return any(term.lower() in haystack for term in terms)


def _group_categories(group: str) -> set[str]:
    return {
        "Growth / Consumer": {"growth", "consumer"},
        "Energy": {"energy"},
    }.get(group, set())


def _symbol_sector_guess(symbol: str) -> str:
    if symbol in {"GOOG", "GOOGL", "META", "NFLX"}:
        return "communication technology growth"
    if symbol in {"AAPL", "MSFT", "NVDA", "AMD", "TSLA"}:
        return "technology growth"
    if symbol in {"NOC", "LMT", "RTX", "GD"}:
        return "industrial aerospace defense"
    if symbol in {"AMZN", "WMT", "TGT", "COST", "MCD"}:
        return "consumer"
    if symbol in {"SPY", "QQQ", "DIA", "IWM", "VOO", "VTI"}:
        return "ETF"
    return ""


def _best_row(rows: list[dict[str, Any]], underlying: float, option_type: str, min_moneyness: float, max_moneyness: float) -> dict[str, Any] | None:
    side = "call" if option_type == "call" else "put"
    candidates = []
    for row in rows:
        contract = row.get(side)
        if not isinstance(contract, dict):
            continue
        strike = _to_float(row.get("strike"))
        if strike is None:
            continue
        moneyness = (strike - underlying) / underlying
        if min_moneyness <= moneyness <= max_moneyness:
            candidates.append(row)
    return candidates[0] if candidates else next((row for row in rows if isinstance(row.get(side), dict)), None)


def _candidate_from_row(row: dict[str, Any] | None, option_type: str, group: str, strategy: str, context: PortfolioSymbolContext, underlying: float, *, credit: bool = False) -> OptionCandidate | None:
    if row is None:
        return None
    contract = row.get(option_type)
    if not isinstance(contract, dict):
        return None
    bid = _first_number(contract, "bid")
    ask = _first_number(contract, "ask")
    mark = _first_number(contract, "mark")
    midpoint = option_midpoint(bid, ask, mark)
    strike = _to_float(row.get("strike"))
    debit = midpoint or 0.0
    max_loss = debit * 100 if not credit else None
    max_gain = None
    breakeven = None
    if strike is not None and midpoint is not None:
        breakeven = strike + midpoint if option_type == "call" else strike - midpoint
    return OptionCandidate(
        key=f"{strategy}:{row.get('expiration_label')}:{strike}:{option_type}",
        group=group,
        strategy=strategy,
        expiration=str(row.get("expiration_label") or row.get("expiration_date") or "--"),
        strike=strike,
        option_type=option_type,
        bid=bid,
        ask=ask,
        mark=mark,
        midpoint=midpoint,
        max_loss=max_loss,
        max_gain=max_gain,
        breakeven=breakeven,
        why="Selected from the loaded chain near the current technical setup.",
        works_if="The underlying moves through confirmation before time decay overwhelms the premium.",
        goes_wrong_if="Price chops sideways, loses support, or implied volatility falls after the entry.",
        relation_to_position=current_position_meaning(context),
        confidence="Good" if group == "Recommended" else "Watch",
        contract_symbol=str(contract.get("symbol") or ""),
        underlying=str(row.get("underlying") or context.symbol),
        underlying_price=underlying,
    )


def _with_candidate_reason(
    candidate: OptionCandidate,
    indicators: AdvancedIndicatorSnapshot,
    macro_label: str,
    earnings_soon: bool,
    high_vol: bool,
    *,
    confidence: str | None = None,
) -> OptionCandidate:
    warnings = []
    if earnings_soon:
        warnings.append("earnings/8-K risk can inflate IV")
    if high_vol:
        warnings.append("premium may be expensive because volatility is elevated")
    why = f"{candidate.strategy} fits a {indicators.trend}/{indicators.momentum} setup with macro {macro_label.lower()}."
    if warnings:
        why += " Watch: " + "; ".join(warnings) + "."
    return OptionCandidate(**{**candidate.__dict__, "why": why, "confidence": confidence or candidate.confidence})


def _first_underlying(rows: list[dict[str, Any]]) -> float | None:
    for row in rows:
        value = _to_float(row.get("underlyingPrice") or row.get("underlying_price"))
        if value is not None:
            return value
    return None


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _to_float(source.get(key))
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace("$", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _guidance_tone(text: str) -> str:
    lower = text.lower()
    positive = sum(term in lower for term in ("raise", "raised", "reaffirm", "growth", "expects higher", "strong"))
    negative = sum(term in lower for term in ("lower", "decline", "pressure", "weak", "risk", "miss"))
    if positive > negative:
        return "Positive"
    if negative > positive:
        return "Negative"
    if "unavailable" in lower or not lower.strip():
        return "Unavailable"
    return "Mixed"


def _trend_from_text(text: str, *terms: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ("yoy +", "increase", "increased", "growth", "improving")) and any(term in lower for term in terms):
        return "Improving"
    if any(term in lower for term in ("decline", "decrease", "weaker", "negative")) and any(term in lower for term in terms):
        return "Weak"
    if "unavailable" in lower or not lower.strip():
        return "Unavailable"
    return "Mixed"


def _earnings_risks(earnings_text: str, fundamentals_text: str) -> list[str]:
    risks: list[str] = []
    lower = f"{earnings_text}\n{fundamentals_text}".lower()
    if "guidance" not in lower and "outlook" not in lower:
        risks.append("Guidance detail is limited in the loaded source.")
    if "margin" in lower or "pressure" in lower:
        risks.append("Margin pressure appears in the loaded text; verify the filing context.")
    if "unavailable" in lower:
        risks.append("Some earnings data is unavailable, so avoid over-reading the snapshot.")
    return risks or ["No obvious earnings risk bullet was found; verify the filing before trading around earnings."]


def _split_source_line(line: str) -> tuple[str, str]:
    left, sep, url = line.partition("http")
    label = left.strip(" :-") or "SEC filing"
    return label, ("http" + url if sep else "")


def _filing_date_from_line(line: str) -> str:
    marker = "filed "
    if marker not in line:
        return "--"
    return line.split(marker, 1)[1].split(" ", 1)[0]


def _reporting_period_from_line(line: str) -> str:
    marker = "period "
    if marker not in line:
        return ""
    return line.split(marker, 1)[1].split(":", 1)[0].strip()


def _format_macro_value(value: float | None, unit: str) -> str:
    if value is None:
        return "--"
    suffix = f" {unit}" if unit else ""
    return f"{value:g}{suffix}"


def _money(value: float | None) -> str:
    if value is None:
        return "--"
    prefix = "-$" if value < 0 else "$"
    return f"{prefix}{abs(value):,.2f}"


def _number(value: float | None) -> str:
    return "--" if value is None else f"{value:,.2f}"


def _format_plain_number(value: float) -> str:
    formatted = f"{value:.2f}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
