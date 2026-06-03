from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.analytics.stock_research import AdvancedIndicatorSnapshot, GeneratedStockPosition, PortfolioSymbolContext
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
    dte: int | None = None
    score: float = 0.0
    score_reason: str = ""
    primary_risk: str = ""
    primary_payoff_path: str = ""
    liquidity_score: float = 0.0
    technical_fit_score: float = 0.0
    greek_score: float = 0.0
    risk_budget_score: float = 0.0
    expected_move_required: float | None = None
    spread_pct: float | None = None
    open_interest: float | None = None
    volume: float | None = None
    delta: float | None = None
    theta: float | None = None
    iv: float | None = None
    dte_bucket: str = "Unknown"
    score_breakdown: tuple[str, ...] = ()
    avoid_reason: str = ""
    better_than_stock: str = ""


@dataclass(frozen=True)
class CombinedOptionScenarioRow:
    move_label: str
    underlying_price: float
    stock_pnl: float
    option_value: float
    option_pnl: float
    combined_pnl: float
    portfolio_impact: float
    read: str


@dataclass(frozen=True)
class CurrentModelOptionScenarioRow:
    move_label: str
    underlying_price: float
    current_shares: float
    current_stock_pnl: float
    model_shares: float | None
    model_stock_pnl: float | None
    option_value: float
    option_pnl: float
    current_combined_pnl: float
    model_combined_pnl: float | None
    current_portfolio_impact: float
    model_portfolio_impact: float | None
    read: str


@dataclass(frozen=True)
class FundamentalVerdict:
    verdict: str
    action_bias: str
    confidence: str
    investment_read: str
    trade_read: str
    combined_read: str
    what_changes: list[str]


@dataclass(frozen=True)
class RiskPlan:
    recommendation: str
    status: str
    reason: str
    confirmation: str
    risk_line: str
    suggested_max_risk: float | None
    paired_option: str
    move_planner: list[tuple[str, str, str, str, str]]


@dataclass(frozen=True)
class EarningsWorkspaceSummary:
    snapshot: dict[str, str]
    guidance_tone: str
    revenue_trend: str
    profitability_trend: str
    risks: list[str]
    source_links: list[tuple[str, str, str]]
    interpretation: list[str]
    earnings_card_label: str
    earnings_card_status: str
    earnings_card_why: str
    freshness_label: str
    freshness_status: str
    freshness_verdict: str


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
    return f"{context.symbol} is not currently held, so the priority is evaluating a small generated starter position unless the setup turns clearly bearish or event risk is flashing red."


def suggest_option_candidates(
    chain_rows: list[dict[str, Any]],
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    *,
    macro_label: str = "Mixed",
    earnings_text: str = "",
    risk_budget: float | None = None,
    stock_plan: GeneratedStockPosition | None = None,
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
    candidates.append(
        _with_candidate_reason(
            _wait_candidate(context, underlying, agreement, macro_label),
            indicators,
            macro_label,
            earnings_soon,
            high_vol,
            context=context,
            risk_budget=risk_budget,
            stock_plan=stock_plan,
        )
    )

    for row in rows:
        strike = _to_float(row.get("strike"))
        if strike is None:
            continue
        moneyness = (strike - underlying) / underlying

        call_candidate: OptionCandidate | None = None
        if isinstance(row.get("call"), dict):
            if context.is_held and 0.01 <= moneyness <= 0.18:
                call_candidate = _candidate_from_row(row, "call", "Covered Call", "Income / covered-call candidate", context, underlying, credit=True)
            elif not context.is_held and agreement == "Bullish" and -0.03 <= moneyness <= 0.12:
                call_candidate = _candidate_from_row(row, "call", "Starter Long Call", "Starter long call", context, underlying)
            elif not context.is_held and agreement == "Mixed" and -0.02 <= moneyness <= 0.08:
                call_candidate = _candidate_from_row(row, "call", "Speculative", "Small-risk bullish call", context, underlying)
        if call_candidate is not None:
            candidates.append(
                _with_candidate_reason(
                    call_candidate,
                    indicators,
                    macro_label,
                    earnings_soon,
                    high_vol,
                    context=context,
                    risk_budget=risk_budget,
                    stock_plan=stock_plan,
                    confidence="Speculative" if call_candidate.group == "Speculative" else None,
                )
            )
        if context.is_held and agreement == "Bullish" and -0.03 <= moneyness <= 0.08:
            add_on_call = _candidate_from_row(row, "call", "Starter Long Call", "Add-on long call", context, underlying)
            if add_on_call is not None:
                candidates.append(
                    _with_candidate_reason(
                        add_on_call,
                        indicators,
                        macro_label,
                        earnings_soon,
                        high_vol,
                        context=context,
                        risk_budget=risk_budget,
                        stock_plan=stock_plan,
                    )
                )

        put_candidate: OptionCandidate | None = None
        if isinstance(row.get("put"), dict):
            if context.is_held and -0.18 <= moneyness <= 0.03:
                put_candidate = _candidate_from_row(row, "put", "Protective Put", "Protective put / hedge", context, underlying)
            elif not context.is_held and agreement == "Bearish" and -0.10 <= moneyness <= 0.05:
                put_candidate = _candidate_from_row(row, "put", "Starter Long Put", "Starter long put", context, underlying)
            elif not context.is_held and agreement == "Mixed" and indicators.momentum == "weakening" and -0.08 <= moneyness <= 0.04:
                put_candidate = _candidate_from_row(row, "put", "Starter Long Put", "Small-risk bearish put", context, underlying)
        if put_candidate is not None:
            candidates.append(
                _with_candidate_reason(
                    put_candidate,
                    indicators,
                    macro_label,
                    earnings_soon,
                    high_vol,
                    context=context,
                    risk_budget=risk_budget,
                    stock_plan=stock_plan,
                )
            )

    if context.is_held:
        hedge = max((item for item in candidates if item.group == "Protective Put"), key=lambda item: item.score, default=None)
        covered = max((item for item in candidates if item.group == "Covered Call"), key=lambda item: item.score, default=None)
        collar = _collar_candidate_from_pair(hedge, covered, context, underlying, indicators, macro_label)
        if collar is not None:
            candidates.append(collar)

    deduped: list[OptionCandidate] = []
    seen: set[tuple[str, str, float | None]] = set()
    for candidate in candidates:
        key = (candidate.strategy, candidate.expiration, candidate.strike)
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    deduped = _raise_wait_when_options_are_weak(deduped)
    deduped.sort(key=lambda item: item.score, reverse=True)
    return deduped[:8]


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
    is_short_call = "covered" in candidate.strategy.lower() or candidate.group == "Income"
    if candidate.option_type == "call":
        intrinsic = max(underlying_price - candidate.strike, 0.0)
    else:
        intrinsic = max(candidate.strike - underlying_price, 0.0)
    if is_short_call:
        return (candidate.midpoint - intrinsic) * multiplier
    return (intrinsic - candidate.midpoint) * multiplier


def option_expiration_value(candidate: OptionCandidate, underlying_price: float, *, contracts: int = 1) -> float:
    if candidate.strike is None or candidate.option_type not in {"call", "put"}:
        return 0.0
    multiplier = max(contracts, 1) * 100
    if candidate.option_type == "call":
        intrinsic = max(underlying_price - candidate.strike, 0.0)
    else:
        intrinsic = max(candidate.strike - underlying_price, 0.0)
    if "covered" in candidate.strategy.lower() or candidate.group == "Income":
        return -intrinsic * multiplier
    return intrinsic * multiplier


def combined_option_scenarios(
    candidate: OptionCandidate | None,
    context: PortfolioSymbolContext,
    moves: tuple[float, ...] = (-0.10, -0.05, -0.03, -0.02, 0.0, 0.02, 0.03, 0.05, 0.10),
) -> list[CombinedOptionScenarioRow]:
    if candidate is None or candidate.underlying_price is None:
        return []
    rows: list[CombinedOptionScenarioRow] = []
    base = candidate.underlying_price
    total = max(context.portfolio_value, 0.01)
    for move in moves:
        price = base * (1 + move)
        stock_pnl = (price - base) * context.quantity if context.is_held else 0.0
        option_value = option_expiration_value(candidate, price)
        option_pnl = option_expiration_payoff(candidate, price)
        combined = stock_pnl + option_pnl
        if candidate.option_type == "put" and stock_pnl < 0 and combined > stock_pnl:
            read = f"Hedges; protection benefit {_money(combined - stock_pnl)}"
        elif candidate.option_type == "call" and combined > stock_pnl:
            read = "Amplifies upside; premium is at risk if move is too small"
        elif combined > 0:
            read = "Helps"
        elif combined < 0:
            read = "Hurts"
        else:
            read = "Flat"
        rows.append(CombinedOptionScenarioRow(f"{move:+.0%}", price, stock_pnl, option_value, option_pnl, combined, combined / total, read))
    return rows


def combined_current_model_option_scenarios(
    candidate: OptionCandidate | None,
    current_context: PortfolioSymbolContext,
    model_position: GeneratedStockPosition | None,
    moves: tuple[float, ...] = (-0.10, -0.05, -0.03, -0.02, 0.0, 0.02, 0.03, 0.05, 0.10),
) -> list[CurrentModelOptionScenarioRow]:
    if candidate is None or candidate.underlying_price is None:
        return []
    rows: list[CurrentModelOptionScenarioRow] = []
    base = candidate.underlying_price
    current_entry = current_context.last_price or base
    current_quantity = float(current_context.quantity or 0.0)
    total = max(current_context.portfolio_value, 0.01)
    model_quantity = float(model_position.quantity) if model_position is not None and model_position.quantity > 0 else None
    model_entry = model_position.entry_price if model_position is not None else None
    has_model = model_quantity is not None and model_entry is not None and model_entry > 0
    for move in moves:
        price = base * (1 + move)
        current_stock_pnl = (price - current_entry) * current_quantity if current_quantity else 0.0
        model_stock_pnl = (price - model_entry) * model_quantity if has_model else None
        option_value = option_expiration_value(candidate, price)
        option_pnl = option_expiration_payoff(candidate, price)
        current_combined = current_stock_pnl + option_pnl
        model_combined = model_stock_pnl + option_pnl if model_stock_pnl is not None else None
        rows.append(
            CurrentModelOptionScenarioRow(
                move_label=f"{move:+.0%}",
                underlying_price=price,
                current_shares=current_quantity,
                current_stock_pnl=current_stock_pnl,
                model_shares=model_quantity if has_model else None,
                model_stock_pnl=model_stock_pnl,
                option_value=option_value,
                option_pnl=option_pnl,
                current_combined_pnl=current_combined,
                model_combined_pnl=model_combined,
                current_portfolio_impact=current_combined / total,
                model_portfolio_impact=(model_combined / total if model_combined is not None else None),
                read=_current_model_option_read(
                    candidate,
                    current_quantity=current_quantity,
                    current_combined=current_combined,
                    model_stock_pnl=model_stock_pnl,
                    model_combined=model_combined,
                ),
            )
        )
    return rows


def _current_model_option_read(
    candidate: OptionCandidate,
    *,
    current_quantity: float,
    current_combined: float,
    model_stock_pnl: float | None,
    model_combined: float | None,
) -> str:
    if model_stock_pnl is None or model_combined is None:
        return "Model stock scenario unavailable; option estimate still shown"
    if current_quantity <= 0:
        return "No current shares; model columns show the generated starter path"
    if candidate.option_type == "put" and model_stock_pnl < 0 and model_combined > model_stock_pnl:
        return f"Model hedge benefit {_money(model_combined - model_stock_pnl)}"
    if candidate.option_type == "call" and model_combined > current_combined:
        return "Model path shows added upside leverage"
    if model_combined > 0:
        return "Model combined helps"
    if model_combined < 0:
        return "Model combined hurts"
    return "Model path flat"


def option_breakeven_explanation(candidate: OptionCandidate) -> str:
    if candidate.breakeven is None or candidate.option_type not in {"call", "put"}:
        return "Breakeven is unavailable because the candidate has no usable strike/premium."
    direction = "above" if candidate.option_type == "call" else "below"
    return f"The stock must be {direction} {_money(candidate.breakeven)} by expiration for this {candidate.option_type} to profit at expiration."


def option_timeline_text(candidate: OptionCandidate, earnings_text: str = "") -> str:
    dte = candidate.dte
    if dte is None:
        term = "term unknown"
        dte_text = "DTE unavailable"
    elif dte <= 14:
        term = "short-term"
        dte_text = f"{dte} days"
    elif dte <= 60:
        term = "medium-term"
        dte_text = f"{dte} days"
    else:
        term = "longer-term"
        dte_text = f"{dte} days"
    earnings_risk = " Earnings before expiration: IV/event risk high." if ("soon" in earnings_text.lower() or "8-k" in earnings_text.lower()) else ""
    return f"Today -> {dte_text} -> Expiration ({term}).{earnings_risk}"


def selected_candidate_detail(candidate: OptionCandidate, context: PortfolioSymbolContext, earnings_text: str = "") -> list[str]:
    cost = (candidate.midpoint or 0.0) * 100
    rows = combined_option_scenarios(candidate, context)
    best = max(rows, key=lambda row: row.combined_pnl, default=None)
    worst = min(rows, key=lambda row: row.combined_pnl, default=None)
    lines = [
        f"{candidate.group}: {candidate.strategy}",
        "",
        "Contract basics:",
        f"- Type: {candidate.option_type.upper()}  Expiration: {candidate.expiration}  DTE: {candidate.dte if candidate.dte is not None else '--'}",
        f"- Strike: {_money(candidate.strike)}  Bid/Ask/Mid: {_money(candidate.bid)} / {_money(candidate.ask)} / {_money(candidate.midpoint)}",
        f"- Contract cost/credit estimate for 1 contract: {_money(cost)}.",
        f"- Max loss: {'unlimited/stock assignment style' if candidate.max_loss is None else _money(candidate.max_loss)}.",
        f"- Max gain: {'not capped for simple long option' if candidate.max_gain is None else _money(candidate.max_gain)}.",
        f"- {option_breakeven_explanation(candidate)}",
        f"- Score: {candidate.score:.0f}/100. {candidate.score_reason}",
        f"- Better/worse than stock: {candidate.better_than_stock or 'No stock-only comparison was available.'}",
        "",
        "Score breakdown:",
        *[f"- {line}" for line in candidate.score_breakdown],
        *([f"- Avoid reason: {candidate.avoid_reason}"] if candidate.avoid_reason else []),
        "",
        f"Timeline: {option_timeline_text(candidate, earnings_text)}",
        "",
        f"Why this candidate: {candidate.why}",
        f"What must happen: {candidate.works_if}",
        f"What goes wrong: {candidate.goes_wrong_if}",
        f"Position interaction: {candidate.relation_to_position}",
        f"Primary risk: {candidate.primary_risk}",
        f"Primary payoff path: {candidate.primary_payoff_path}",
        f"Best-case simple read: {best.move_label if best else '--'} move -> combined {_money(best.combined_pnl) if best else '--'}.",
        f"Worst-case simple read: {worst.move_label if worst else '--'} move -> combined {_money(worst.combined_pnl) if worst else '--'}.",
        "",
        "Expiration-style estimate, not live option pricing.",
    ]
    return lines


def protective_put_benefit(candidate: OptionCandidate, context: PortfolioSymbolContext, move: float = -0.05) -> float | None:
    if candidate.option_type != "put" or candidate.underlying_price is None:
        return None
    rows = combined_option_scenarios(candidate, context, moves=(move,))
    if not rows:
        return None
    return rows[0].combined_pnl - rows[0].stock_pnl


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
    foreign_mode = "foreign issuer" in f"{earnings_text}\n{fundamentals_text}".lower()
    freshness = _earnings_freshness_fields(earnings_text)
    card_label, card_status, card_why = _earnings_card_from_freshness(freshness, latest_8k)
    source_links = _source_links_from_earnings_text(earnings_text)
    for line in filings_lines[:8]:
        label, url = _split_source_line(line)
        source_links.append((label, _filing_date_from_line(line), url))
    if "companyfacts" in fundamentals_text.lower() or fundamentals_text.strip():
        source_links.append(("SEC companyfacts / XBRL", "latest loaded", "https://data.sec.gov/api/xbrl/companyfacts/"))
    source_links = _dedupe_source_links(source_links)
    revenue = _trend_from_text(fundamentals_text, "revenue")
    profitability = _trend_from_text(fundamentals_text, "net income", "operating income", "eps", "margin")
    guidance = _guidance_tone(earnings_text)
    interpretation = []
    if freshness["verdict"] != "--":
        interpretation.append(freshness["verdict"])
    interpretation.extend(
        [
        f"Revenue trend looks {revenue.lower()}.",
        f"Margins/profitability appear {profitability.lower()}.",
        f"Guidance language is {guidance.lower()}.",
        ]
    )
    if foreign_mode:
        interpretation.append("Foreign issuer mode is active, so official IR results, 6-K, 20-F, and annual reports are the primary source stack.")
    elif "no recent 8-k earnings-release exhibit" in earnings_text.lower() or "unavailable" in earnings_text.lower():
        interpretation.append("The latest filing scan does not appear to include a fresh earnings release.")
    risks = _earnings_risks(earnings_text, fundamentals_text)
    return EarningsWorkspaceSummary(
        snapshot={
            "Company": symbol.upper(),
            "Latest earnings release": latest_8k or ("Latest foreign issuer results" if foreign_mode else "No earnings exhibit found"),
            "Latest 10-Q / 10-K": latest_qk or "No 10-Q/10-K fallback found",
            "Reporting period": _reporting_period_from_line(latest_qk or latest_8k) or "--",
            "Source": "Foreign issuer IR / 6-K / 20-F" if foreign_mode else "SEC filings and companyfacts",
            "Earnings event": freshness["event"],
            "Latest loaded source date": freshness["loaded_date"],
            "Latest SEC filing date": freshness["sec_date"],
            "Latest company IR release date": freshness["ir_date"],
            "Freshness verdict": freshness["verdict"],
        },
        guidance_tone=guidance,
        revenue_trend=revenue,
        profitability_trend=profitability,
        risks=risks,
        source_links=source_links,
        interpretation=interpretation,
        earnings_card_label=card_label,
        earnings_card_status=card_status,
        earnings_card_why=card_why,
        freshness_label=freshness["event"],
        freshness_status=card_status,
        freshness_verdict=freshness["verdict"],
    )


def build_fundamental_verdict(
    fundamentals_text: str,
    indicators: AdvancedIndicatorSnapshot,
    macro_label: str,
) -> FundamentalVerdict:
    lower = fundamentals_text.lower()
    if not lower.strip() or "unavailable" in lower:
        return FundamentalVerdict(
            verdict="Unknown",
            action_bias="Unknown / insufficient data",
            confidence="Low",
            investment_read="Investment read: insufficient standardized data.",
            trade_read=_trade_read(indicators, macro_label),
            combined_read="Combined read: wait for better fundamental data before sizing risk from this tab.",
            what_changes=["SEC companyfacts becomes available.", "Fresh 10-Q/10-K data loads.", "Price confirms above resistance."],
        )

    positive_terms = ("revenue growth", "revenue is strong", "net income improved", "operating income expanded", "cash flow", "free cash flow", "positive", "strong")
    negative_terms = ("decline", "decreased", "weak", "loss", "cash used", "negative", "debt", "pressure")
    positive = sum(1 for term in positive_terms if term in lower)
    negative = sum(1 for term in negative_terms if term in lower)
    data_points = sum(1 for term in ("revenue", "net income", "operating income", "cash", "liabilities", "companyfacts", "10-q", "10-k") if term in lower)
    score = positive * 18 - negative * 16 + min(data_points, 8) * 5
    if score >= 80:
        verdict, action = "Strong", "Supports owning"
        investment = "Investment read: favorable. Fundamentals look strong enough to support owning this."
    elif score >= 45:
        verdict, action = "Good", "Supports adding on pullbacks"
        investment = "Investment read: favorable, but add only when price and risk confirm."
    elif score >= -15:
        verdict, action = "Mixed", "Supports watch only"
        investment = "Investment read: mixed. The data does not justify aggressive new risk by itself."
    elif score <= -35:
        verdict, action = "Avoid", "Avoid until fundamentals improve"
        investment = "Investment read: unfavorable. Fundamentals do not justify new risk right now."
    else:
        verdict, action = "Weak", "Supports trimming"
        investment = "Investment read: weak. Own only with a specific risk plan."
    confidence = "High" if data_points >= 6 and abs(score) >= 45 else "Medium" if data_points >= 4 else "Low"
    trade = _trade_read(indicators, macro_label)
    combined = _combined_fundamental_trade_read(verdict, trade, macro_label)
    return FundamentalVerdict(
        verdict=verdict,
        action_bias=action,
        confidence=confidence,
        investment_read=investment,
        trade_read=trade,
        combined_read=combined,
        what_changes=[
            "Revenue trend deteriorates or improves in the next filing.",
            "Cash flow weakens or strengthens versus the latest period.",
            "Debt/liabilities worsen relative to assets.",
            risk_line_text(indicators),
            "Earnings/guidance disappoints or confirms the trend.",
            f"Macro/rates move from {macro_label.lower()} to a clearer tailwind or headwind.",
        ],
    )


def build_risk_plan(
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    macro_label: str,
    fundamental_verdict: str,
    candidate: OptionCandidate | None,
    max_risk: float | None,
) -> RiskPlan:
    agreement, _status, reason = indicator_agreement_classification(indicators, macro_label)
    support = indicators.support or indicators.swing_low
    stop = support
    suggested_size = None
    if max_risk is not None and context.last_price is not None and stop is not None and context.last_price > stop:
        suggested_size = max_risk / max(context.last_price - stop, 0.01)
    if macro_label == "Headwind" and context.is_held and candidate and candidate.option_type == "put":
        recommendation, status = "Hedge with put", "mixed"
        reason_text = "Macro is a headwind and the position is already held; a put can cap some downside, but only if premium is reasonable."
    elif agreement == "Bullish" and fundamental_verdict in {"Strong", "Good"}:
        recommendation, status = "Add carefully", "good"
        reason_text = "Fundamentals and technicals are aligned; wait for confirmation and keep max loss controlled."
    elif agreement == "Bullish":
        recommendation, status = "Speculative call only", "mixed"
        reason_text = "Technicals lean constructive, but fundamentals/macro are not fully aligned."
    elif context.portfolio_weight >= 0.10:
        recommendation, status = "Trim", "bad"
        reason_text = "Position weight is large enough that downside scenarios matter more than adding exposure."
    elif candidate and "covered" in candidate.strategy.lower() and context.is_held:
        recommendation, status = "Covered call candidate", "mixed"
        reason_text = "Existing shares can support income, but upside is capped above the strike."
    elif not context.is_held and agreement != "Bearish":
        recommendation, status = "Consider starter", "mixed"
        reason_text = "There is no current exposure; use the generated stock scenario size as a small starter candidate, but require confirmation because the setup is not fully green."
    elif macro_label == "Headwind":
        recommendation, status = "Watch", "mixed"
        reason_text = "Macro is fighting the setup; wait for confirmation instead of forcing premium risk."
    else:
        recommendation, status = "Watch", "info"
        reason_text = reason
    paired = candidate.strategy if candidate else "No option candidate loaded"
    moves = [
        ("Do nothing / watch", "Mixed setup or macro headwind.", "Avoids premium and bad entries.", "Can miss a fast breakout.", "Best when price is below confirmation."),
        ("Starter stock position", "No current shares and risk is not flashing red.", "Gets exposure without using full size.", "Still has downside risk.", "Size from generated risk budget and technical stop."),
        ("Add shares", "Fundamentals strong and price confirms.", "Keeps payoff simple.", "Adds full downside exposure.", f"Use only above {_money(indicators.resistance)}."),
        ("Trim shares", "Position is large or support breaks.", "Reduces portfolio drawdown.", "Gives up rebound exposure.", f"Most relevant below {_money(support)}."),
        ("Buy protective put", "Held shares plus downside/event risk.", "Offsets some share losses.", "Premium can expire worthless.", "Insurance, not free protection."),
        ("Buy bullish call", "Bullish setup but smaller defined risk desired.", "Caps debit at premium.", "Needs move before expiration.", "Useful only if breakeven is realistic."),
        ("Covered call / income", "Held shares and upside looks capped.", "Collects premium.", "Caps upside above strike.", "Avoid if you want unlimited upside."),
        ("Avoid new risk", "Macro/earnings/technicals conflict.", "Preserves capital.", "No participation.", "Correct when premium is unattractive."),
    ]
    return RiskPlan(
        recommendation=recommendation,
        status=status,
        reason=reason_text,
        confirmation=confirmation_text(indicators),
        risk_line=risk_line_text(indicators),
        suggested_max_risk=max_risk,
        paired_option=paired,
        move_planner=moves,
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
    open_interest = _first_number(contract, "openInterest", "open_interest", "openInterestLong")
    volume = _first_number(contract, "totalVolume", "volume")
    delta = _first_number(contract, "delta")
    theta = _first_number(contract, "theta")
    iv = _normalize_iv(_first_number(contract, "impliedVolatility", "iv", "volatility", "volatilityPercent"))
    dte = _to_int(row.get("dte"))
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
        confidence="Good" if group == "Best Fit" else "Watch",
        contract_symbol=str(contract.get("symbol") or ""),
        underlying=str(row.get("underlying") or context.symbol),
        underlying_price=underlying,
        dte=dte,
        expected_move_required=_expected_move_required(option_type, breakeven, underlying),
        spread_pct=_option_spread_pct(bid, ask),
        open_interest=open_interest,
        volume=volume,
        delta=delta,
        theta=theta,
        iv=iv,
        dte_bucket=_dte_bucket(dte),
    )


def _with_candidate_reason(
    candidate: OptionCandidate,
    indicators: AdvancedIndicatorSnapshot,
    macro_label: str,
    earnings_soon: bool,
    high_vol: bool,
    *,
    context: PortfolioSymbolContext | None = None,
    risk_budget: float | None = None,
    stock_plan: GeneratedStockPosition | None = None,
    confidence: str | None = None,
) -> OptionCandidate:
    warnings = []
    if earnings_soon:
        warnings.append("earnings/8-K risk can inflate IV")
    if high_vol:
        warnings.append("premium may be expensive because volatility is elevated")
    if candidate.option_type in {"call", "put"}:
        why = f"{candidate.strategy} fits a {indicators.trend}/{indicators.momentum} setup with macro {macro_label.lower()}."
    else:
        why = candidate.why
    if warnings:
        why += " Watch: " + "; ".join(warnings) + "."
    scoring = _option_candidate_scoring(
        candidate,
        indicators,
        macro_label,
        earnings_soon=earnings_soon,
        context=context,
        risk_budget=risk_budget,
        stock_plan=stock_plan,
    )
    score = scoring["score"]
    fit = confidence or _fit_from_score(score)
    primary_risk = _primary_option_risk(candidate, earnings_soon, high_vol)
    primary_payoff = _primary_payoff_path(candidate)
    return OptionCandidate(
        **{
            **candidate.__dict__,
            "why": why,
            "confidence": fit,
            "score": score,
            "score_reason": scoring["score_reason"],
            "primary_risk": primary_risk,
            "primary_payoff_path": primary_payoff,
            "liquidity_score": scoring["liquidity_score"],
            "technical_fit_score": scoring["technical_fit_score"],
            "greek_score": scoring["greek_score"],
            "risk_budget_score": scoring["risk_budget_score"],
            "score_breakdown": scoring["score_breakdown"],
            "avoid_reason": scoring["avoid_reason"],
            "better_than_stock": scoring["better_than_stock"],
        }
    )


def option_candidate_score(
    candidate: OptionCandidate,
    indicators: AdvancedIndicatorSnapshot,
    macro_label: str,
    *,
    earnings_soon: bool = False,
) -> tuple[float, str]:
    scoring = _option_candidate_scoring(candidate, indicators, macro_label, earnings_soon=earnings_soon)
    return scoring["score"], scoring["score_reason"]


def _option_candidate_scoring(
    candidate: OptionCandidate,
    indicators: AdvancedIndicatorSnapshot,
    macro_label: str,
    *,
    earnings_soon: bool = False,
    context: PortfolioSymbolContext | None = None,
    risk_budget: float | None = None,
    stock_plan: GeneratedStockPosition | None = None,
) -> dict[str, Any]:
    if candidate.option_type not in {"call", "put"}:
        return _wait_candidate_scoring(candidate, indicators, macro_label, earnings_soon=earnings_soon, context=context)

    technical_score, technical_reason = _technical_fit_score(candidate, indicators, macro_label, context)
    liquidity_score, liquidity_reason = _liquidity_fit_score(candidate)
    greek_score, greek_reason = _greek_fit_score(candidate)
    risk_score, risk_reason = _risk_budget_fit_score(candidate, risk_budget)
    move_adjust, move_reason = _required_move_adjustment(candidate)
    stock_adjust, stock_note = _stock_comparison_adjustment(candidate, context, stock_plan, risk_budget)
    event_adjust = -7.0 if earnings_soon and not _is_covered_call(candidate) else -3.0 if earnings_soon else 0.0
    event_reason = "earnings/event risk can distort premium" if earnings_soon else ""

    score = (
        technical_score * 0.34
        + liquidity_score * 0.22
        + greek_score * 0.18
        + risk_score * 0.22
        + move_adjust
        + stock_adjust
        + event_adjust
    )
    if candidate.dte is not None:
        if 14 <= candidate.dte <= 60:
            score += 3.0
        elif candidate.dte < 7:
            score -= 10.0
        elif candidate.dte > 120:
            score -= 3.0

    score = max(0.0, min(100.0, score))
    reasons = [technical_reason, liquidity_reason, greek_reason, risk_reason, move_reason, stock_note, event_reason]
    score_reason = "; ".join(reason for reason in reasons if reason) or "Balanced candidate score."
    avoid_reason = _candidate_avoid_reason(candidate, technical_score, liquidity_score, greek_score, risk_score)
    breakdown = (
        f"Technical fit {technical_score:.0f}/100: {technical_reason}",
        f"Liquidity fit {liquidity_score:.0f}/100: {liquidity_reason}",
        f"Greek fit {greek_score:.0f}/100: {greek_reason}",
        f"Risk-budget fit {risk_score:.0f}/100: {risk_reason}",
        f"Move to breakeven: {_move_required_label(candidate.expected_move_required)}. {move_reason}",
        f"Stock comparison: {stock_note or 'No model stock comparison was available.'}",
    )
    return {
        "score": score,
        "score_reason": score_reason,
        "liquidity_score": liquidity_score,
        "technical_fit_score": technical_score,
        "greek_score": greek_score,
        "risk_budget_score": risk_score,
        "score_breakdown": breakdown,
        "avoid_reason": avoid_reason,
        "better_than_stock": stock_note,
    }


def _wait_candidate_scoring(
    candidate: OptionCandidate,
    indicators: AdvancedIndicatorSnapshot,
    macro_label: str,
    *,
    earnings_soon: bool,
    context: PortfolioSymbolContext | None,
) -> dict[str, Any]:
    agreement, _status, _why = indicator_agreement_classification(indicators, macro_label)
    score = 40.0
    if agreement == "Mixed":
        score += 24.0
    if agreement == "Bearish" and not (context and context.is_held):
        score += 10.0
    if "headwind" in macro_label.lower():
        score += 9.0
    if earnings_soon:
        score += 8.0
    if indicators.volatility == "elevated":
        score += 6.0
    if agreement == "Bullish" and "tailwind" in macro_label.lower() and not earnings_soon:
        score -= 5.0
    score = max(0.0, min(100.0, score))
    reason = "Wait/no-trade ranks higher when confirmation is mixed, macro is a headwind, or premium risk is not justified."
    breakdown = (
        f"Technical fit {score:.0f}/100: {agreement.lower()} setup with macro {macro_label.lower()}.",
        "Liquidity fit 100/100: no spread risk because no contract is selected.",
        "Greek fit 100/100: no delta/theta/IV exposure is taken.",
        "Risk-budget fit 100/100: no capital is committed.",
        "Move to breakeven: not applicable.",
        "Stock comparison: waiting keeps the stock-plan optional instead of forcing an option trade.",
    )
    return {
        "score": score,
        "score_reason": reason,
        "liquidity_score": 100.0,
        "technical_fit_score": score,
        "greek_score": 100.0,
        "risk_budget_score": 100.0,
        "score_breakdown": breakdown,
        "avoid_reason": "",
        "better_than_stock": "Waiting keeps the model stock plan optional until confirmation improves.",
    }


def _wait_candidate(context: PortfolioSymbolContext, underlying: float, agreement: str, macro_label: str) -> OptionCandidate:
    if agreement == "Bullish" and "headwind" not in macro_label.lower():
        why = "The setup is constructive, but waiting remains the benchmark if the chain is illiquid or overpriced."
    elif agreement == "Bearish":
        why = f"Indicator agreement is bearish and macro reads {macro_label.lower()}, so do not force a bullish option."
    else:
        why = f"Indicator agreement is mixed and macro reads {macro_label.lower()}."
    return OptionCandidate(
        key="wait",
        group="Wait / No Trade",
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
        why=why,
        works_if="Patience works if price reaches confirmation or risk improves before capital is committed.",
        goes_wrong_if="The setup can move without you; the trade-off is missing an unconfirmed move.",
        relation_to_position=current_position_meaning(context),
        confidence="Watch",
        contract_symbol="",
        underlying=context.symbol,
        underlying_price=underlying,
        score=45.0,
        score_reason="No-trade is preferred until confirmation improves or premium becomes more attractive.",
        primary_risk="Missing an unconfirmed move.",
        primary_payoff_path="Capital is preserved until a cleaner setup appears.",
    )


def _raise_wait_when_options_are_weak(candidates: list[OptionCandidate]) -> list[OptionCandidate]:
    actionable = [item for item in candidates if item.option_type in {"call", "put", "collar"}]
    wait = next((item for item in candidates if item.strategy == "No-trade / wait"), None)
    if wait is None or not actionable:
        return candidates
    best_actionable = max(actionable, key=lambda item: item.score)
    if best_actionable.score >= 50.0:
        return candidates
    wait_score = max(wait.score, best_actionable.score + 2.0, 50.0)
    updated_wait = OptionCandidate(
        **{
            **wait.__dict__,
            "score": min(100.0, wait_score),
            "confidence": "Watch",
            "score_reason": "No actionable option cleared the minimum quality bar, so wait/no-trade ranks first.",
            "score_breakdown": (
                f"Technical fit {wait_score:.0f}/100: no loaded contract cleared the minimum quality bar.",
                "Liquidity fit 100/100: no spread risk because no contract is selected.",
                "Greek fit 100/100: no delta/theta/IV exposure is taken.",
                "Risk-budget fit 100/100: no capital is committed.",
                "Move to breakeven: not applicable.",
                f"Stock comparison: best actionable candidate was {best_actionable.strategy} at {best_actionable.score:.0f}/100.",
            ),
            "better_than_stock": f"Waiting is cleaner than forcing {best_actionable.strategy} at {best_actionable.score:.0f}/100.",
        }
    )
    return [updated_wait if item is wait else item for item in candidates]


def _collar_candidate_from_pair(
    hedge: OptionCandidate | None,
    covered: OptionCandidate | None,
    context: PortfolioSymbolContext,
    underlying: float,
    indicators: AdvancedIndicatorSnapshot,
    macro_label: str,
) -> OptionCandidate | None:
    if hedge is None or covered is None:
        return None
    net_debit = (hedge.midpoint or 0.0) - (covered.midpoint or 0.0)
    max_loss = max(net_debit, 0.0) * 100 if net_debit > 0 else 0.0
    score = max(0.0, min(100.0, (hedge.score + covered.score) / 2 + (6 if "headwind" in macro_label.lower() else 0)))
    technical = (hedge.technical_fit_score + covered.technical_fit_score) / 2
    liquidity = min(hedge.liquidity_score, covered.liquidity_score)
    greek = (hedge.greek_score + covered.greek_score) / 2
    risk = (hedge.risk_budget_score + covered.risk_budget_score) / 2
    score_reason = (
        f"Combines {hedge.strategy} with {covered.strategy}; downside is partly hedged while covered-call credit offsets put cost."
    )
    breakdown = (
        f"Technical fit {technical:.0f}/100: collar fits held shares when risk needs definition more than upside leverage.",
        f"Liquidity fit {liquidity:.0f}/100: uses the weaker liquidity score of the put/call legs.",
        f"Greek fit {greek:.0f}/100: mixes put protection with short-call upside cap.",
        f"Risk-budget fit {risk:.0f}/100: estimated net debit {_money(max_loss)} before assignment/cap effects.",
        "Move to breakeven: structure uses two legs; inspect both strikes.",
        "Stock comparison: collar may be cleaner than stock-only when held shares need defined downside and capped upside is acceptable.",
    )
    return OptionCandidate(
        key=f"collar:{hedge.expiration}:{hedge.strike}:{covered.strike}",
        group="Collar Candidate",
        strategy="Collar candidate",
        expiration=hedge.expiration,
        strike=None,
        option_type="collar",
        bid=None,
        ask=None,
        mark=None,
        midpoint=round(net_debit, 2),
        max_loss=max_loss,
        max_gain=None,
        breakeven=None,
        why=score_reason,
        works_if="Shares stay held and downside protection is worth giving up upside above the covered-call strike.",
        goes_wrong_if="The stock rallies through the call strike or the put debit/width does not justify the hedge.",
        relation_to_position=current_position_meaning(context),
        confidence=_fit_from_score(score),
        contract_symbol="",
        underlying=context.symbol,
        underlying_price=underlying,
        dte=hedge.dte,
        score=score,
        score_reason=score_reason,
        primary_risk="Upside may be capped while the put still costs net premium or complexity.",
        primary_payoff_path="Best path is held-share downside being cushioned without forfeiting more upside than intended.",
        liquidity_score=liquidity,
        technical_fit_score=technical,
        greek_score=greek,
        risk_budget_score=risk,
        dte_bucket=hedge.dte_bucket,
        score_breakdown=breakdown,
        better_than_stock="Collar can be better than stock-only when the goal is held-share protection, not upside leverage.",
    )


def _technical_fit_score(
    candidate: OptionCandidate,
    indicators: AdvancedIndicatorSnapshot,
    macro_label: str,
    context: PortfolioSymbolContext | None,
) -> tuple[float, str]:
    macro = macro_label.lower()
    trend = indicators.trend
    momentum = indicators.momentum
    held = bool(context and context.is_held)
    strategy = candidate.strategy.lower()
    if candidate.option_type == "call" and _is_covered_call(candidate):
        score = 58.0 + (12.0 if held else -18.0)
        if trend == "sideways" or "headwind" in macro:
            score += 10.0
        if trend == "bullish" and momentum == "improving":
            score -= 6.0
        reason = "covered calls fit held shares best when upside is mixed or income is the goal"
    elif candidate.option_type == "call":
        score = 44.0
        if trend == "bullish":
            score += 24.0
        if momentum == "improving":
            score += 12.0
        if "tailwind" in macro:
            score += 7.0
        if trend == "bearish":
            score -= 18.0
        if momentum == "weakening":
            score -= 12.0
        if "headwind" in macro:
            score -= 15.0
        reason = "long calls need bullish trend, improving momentum, and no major macro headwind"
    elif candidate.option_type == "put" and ("protective" in strategy or "hedge" in strategy):
        score = 54.0 + (16.0 if held else -10.0)
        if trend == "bearish":
            score += 13.0
        if momentum == "weakening":
            score += 9.0
        if "headwind" in macro:
            score += 9.0
        if trend == "bullish" and momentum == "improving" and "tailwind" in macro:
            score -= 10.0
        reason = "protective puts fit held shares when downside, macro, or momentum risk is visible"
    else:
        score = 44.0
        if trend == "bearish":
            score += 24.0
        if momentum == "weakening":
            score += 12.0
        if "headwind" in macro:
            score += 8.0
        if trend == "bullish":
            score -= 16.0
        if momentum == "improving":
            score -= 8.0
        reason = "long puts need downside trend or a clear hedge/speculation reason"
    return max(0.0, min(100.0, score)), reason


def _liquidity_fit_score(candidate: OptionCandidate) -> tuple[float, str]:
    spread = candidate.spread_pct
    if spread is None:
        score = 52.0
        reason = "bid/ask spread is unavailable"
    elif spread <= 0.12:
        score = 90.0
        reason = f"spread is tight at {spread:.1%}"
    elif spread <= 0.25:
        score = 72.0
        reason = f"spread is usable at {spread:.1%}"
    elif spread <= 0.45:
        score = 46.0
        reason = f"spread is wide at {spread:.1%}"
    else:
        score = 22.0
        reason = f"spread is very wide at {spread:.1%}"
    if candidate.open_interest is not None:
        if candidate.open_interest >= 500:
            score += 5.0
        elif candidate.open_interest < 50:
            score -= 8.0
    if candidate.volume is not None:
        if candidate.volume >= 50:
            score += 4.0
        elif candidate.volume < 5:
            score -= 5.0
    extra = []
    if candidate.open_interest is not None:
        extra.append(f"OI {candidate.open_interest:g}")
    if candidate.volume is not None:
        extra.append(f"volume {candidate.volume:g}")
    if extra:
        reason += "; " + ", ".join(extra)
    return max(0.0, min(100.0, score)), reason


def _greek_fit_score(candidate: OptionCandidate) -> tuple[float, str]:
    score = 55.0
    reasons: list[str] = []
    abs_delta = abs(candidate.delta) if candidate.delta is not None else None
    if abs_delta is None:
        reasons.append("delta unavailable")
    elif _is_covered_call(candidate):
        if 0.15 <= abs_delta <= 0.35:
            score += 24.0
            reasons.append(f"covered-call delta {candidate.delta:.2f} is practical")
        elif abs_delta > 0.55:
            score -= 16.0
            reasons.append(f"covered-call delta {candidate.delta:.2f} is high")
        else:
            score += 6.0
            reasons.append(f"delta {candidate.delta:.2f} is usable")
    elif candidate.option_type == "call":
        if 0.35 <= abs_delta <= 0.60:
            score += 24.0
            reasons.append(f"call delta {candidate.delta:.2f} gives meaningful participation")
        elif 0.20 <= abs_delta < 0.35 or 0.60 < abs_delta <= 0.75:
            score += 8.0
            reasons.append(f"call delta {candidate.delta:.2f} is usable")
        else:
            score -= 12.0
            reasons.append(f"call delta {candidate.delta:.2f} is not ideal")
    else:
        if 0.25 <= abs_delta <= 0.55:
            score += 22.0
            reasons.append(f"put delta {candidate.delta:.2f} gives visible downside response")
        elif 0.15 <= abs_delta < 0.25 or 0.55 < abs_delta <= 0.75:
            score += 7.0
            reasons.append(f"put delta {candidate.delta:.2f} is usable")
        else:
            score -= 10.0
            reasons.append(f"put delta {candidate.delta:.2f} is not ideal")
    if candidate.iv is not None:
        if candidate.iv >= 0.80:
            score -= 13.0
            reasons.append(f"IV is high at {candidate.iv:.1%}")
        elif candidate.iv <= 0.45:
            score += 5.0
            reasons.append(f"IV is not extreme at {candidate.iv:.1%}")
    if candidate.dte is not None:
        if 14 <= candidate.dte <= 60:
            score += 8.0
            reasons.append(f"{candidate.dte} DTE is practical")
        elif candidate.dte < 7:
            score -= 14.0
            reasons.append(f"{candidate.dte} DTE is too short")
    return max(0.0, min(100.0, score)), "; ".join(reasons) or "Greek data is limited."


def _risk_budget_fit_score(candidate: OptionCandidate, risk_budget: float | None) -> tuple[float, str]:
    if _is_covered_call(candidate):
        return 72.0, "covered call is a credit structure, but assignment/capped-upside risk still matters"
    max_loss = candidate.max_loss
    if risk_budget is not None and risk_budget > 0 and max_loss is not None:
        if max_loss <= risk_budget * 0.5:
            score = 94.0
        elif max_loss <= risk_budget:
            score = 82.0
        elif max_loss <= risk_budget * 1.5:
            score = 58.0
        elif max_loss <= risk_budget * 2.5:
            score = 36.0
        else:
            score = 18.0
        return score, f"max loss {_money(max_loss)} versus generated risk {_money(risk_budget)}"
    if candidate.midpoint is not None and candidate.underlying_price:
        premium_pct = candidate.midpoint / max(candidate.underlying_price, 0.01)
        if premium_pct <= 0.015:
            score = 86.0
        elif premium_pct <= 0.035:
            score = 70.0
        elif premium_pct <= 0.060:
            score = 48.0
        else:
            score = 28.0
        return score, f"premium is {premium_pct:.1%} of underlying price"
    return 48.0, "premium/risk budget comparison is unavailable"


def _required_move_adjustment(candidate: OptionCandidate) -> tuple[float, str]:
    move = candidate.expected_move_required
    if move is None or _is_covered_call(candidate):
        return 0.0, "breakeven move is not the main rank driver for this structure"
    if move <= 0.03:
        return 8.0, "breakeven is nearby"
    if move <= 0.06:
        return 3.0, "breakeven needs a moderate move"
    if move <= 0.10:
        return -7.0, "breakeven needs a large move"
    return -16.0, "breakeven needs an unusually large move"


def _stock_comparison_adjustment(
    candidate: OptionCandidate,
    context: PortfolioSymbolContext | None,
    stock_plan: GeneratedStockPosition | None,
    risk_budget: float | None,
) -> tuple[float, str]:
    if candidate.option_type == "put" and ("protective" in candidate.strategy.lower() or "hedge" in candidate.strategy.lower()):
        return 0.0, "hedge should be judged against held-share downside, not upside participation"
    if _is_covered_call(candidate):
        return 0.0, "covered call may be better than stock-only only when income is worth the capped upside"
    if candidate.option_type != "call" or context is None or context.is_held or stock_plan is None:
        return 0.0, ""
    quantity = float(getattr(stock_plan, "quantity", 0.0) or 0.0)
    entry = float(getattr(stock_plan, "entry_price", 0.0) or 0.0)
    if quantity <= 0 or entry <= 0:
        return 0.0, "no usable stock-only model exists for comparison"
    stock_risk = getattr(stock_plan, "risk_dollars", None)
    if stock_risk is None and getattr(stock_plan, "per_share_risk", None) is not None:
        stock_risk = float(stock_plan.per_share_risk or 0.0) * quantity
    max_loss = candidate.max_loss or 0.0
    required_move = candidate.expected_move_required or 0.0
    if stock_risk and max_loss > stock_risk * 1.15:
        return -11.0, f"stock-only model is cleaner: option debit {_money(max_loss)} exceeds model stock risk {_money(stock_risk)}"
    if risk_budget and max_loss > risk_budget:
        return -8.0, f"stock-only model is cleaner: option debit {_money(max_loss)} exceeds generated risk {_money(risk_budget)}"
    if required_move > 0.07:
        return -6.0, f"stock-only model is cleaner unless price can move {required_move:.1%} before expiration"
    return 2.0, "option can add defined-risk leverage, but stock-only remains the cleaner benchmark"


def _candidate_avoid_reason(
    candidate: OptionCandidate,
    technical_score: float,
    liquidity_score: float,
    greek_score: float,
    risk_score: float,
) -> str:
    flags: list[str] = []
    if technical_score < 42:
        flags.append("technical setup does not fit")
    if liquidity_score < 45:
        flags.append("spread/liquidity is weak")
    if greek_score < 42:
        flags.append("Greek profile is poor or unavailable")
    if risk_score < 45:
        flags.append("debit is too large for the risk budget")
    if candidate.expected_move_required is not None and candidate.expected_move_required > 0.10 and not _is_covered_call(candidate):
        flags.append("breakeven requires too much move")
    return "Avoid/low rank: " + "; ".join(flags) + "." if flags else ""


def _is_covered_call(candidate: OptionCandidate) -> bool:
    return candidate.option_type == "call" and ("covered" in candidate.strategy.lower() or candidate.group in {"Income", "Covered Call"})


def _expected_move_required(option_type: str, breakeven: float | None, underlying: float | None) -> float | None:
    if breakeven is None or underlying is None or underlying <= 0:
        return None
    if option_type == "call":
        return max(0.0, (breakeven - underlying) / underlying)
    if option_type == "put":
        return max(0.0, (underlying - breakeven) / underlying)
    return None


def _option_spread_pct(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or ask <= 0:
        return None
    midpoint = max((bid + ask) / 2, 0.01)
    return max(0.0, (ask - bid) / midpoint)


def _normalize_iv(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100.0 if value > 1.5 else value


def _dte_bucket(dte: int | None) -> str:
    if dte is None:
        return "Unknown"
    if dte < 14:
        return "Short"
    if dte <= 60:
        return "Medium"
    return "Long"


def _move_required_label(value: float | None) -> str:
    return "--" if value is None else f"{value:+.1%}"


def _fit_from_score(score: float) -> str:
    if score >= 72:
        return "Good"
    if score >= 52:
        return "Watch"
    if score >= 35:
        return "Speculative"
    return "Avoid"


def _primary_option_risk(candidate: OptionCandidate, earnings_soon: bool, high_vol: bool) -> str:
    if candidate.option_type == "call":
        base = "This call needs the stock to rise above breakeven before expiration; otherwise premium decays."
    elif candidate.option_type == "put":
        base = "This put can lose premium if the stock stays above the strike/breakeven."
    else:
        base = "No contract risk because no option is selected."
    if earnings_soon:
        base += " Earnings/event risk can make IV expensive."
    if high_vol:
        base += " Volatility is elevated, so premium may be rich."
    return base


def _primary_payoff_path(candidate: OptionCandidate) -> str:
    if candidate.option_type == "call":
        return f"Best path is a move above {_money(candidate.breakeven)} by expiration."
    if candidate.option_type == "put":
        return f"Best path is a move below {_money(candidate.breakeven)} by expiration, especially if shares need protection."
    return "Best path is waiting for confirmation before paying option premium."


def _trade_read(indicators: AdvancedIndicatorSnapshot, macro_label: str) -> str:
    if indicators.trend == "bullish" and indicators.momentum == "improving" and macro_label != "Headwind":
        return f"Trade read: favorable above confirmation. {confirmation_text(indicators)}"
    if indicators.trend == "bearish" or macro_label == "Headwind":
        return f"Trade read: wait or hedge. {risk_line_text(indicators)}"
    return f"Trade read: mixed. {confirmation_text(indicators)}"


def _combined_fundamental_trade_read(verdict: str, trade: str, macro_label: str) -> str:
    if verdict in {"Strong", "Good"} and "favorable" in trade.lower():
        return "Combined read: fundamentals and trade setup support risk, with normal position discipline."
    if verdict in {"Strong", "Good"}:
        return f"Combined read: fundamentals say yes, but trade timing is not clean because macro is {macro_label.lower()} or price has not confirmed."
    if verdict == "Mixed":
        return "Combined read: watch only until either fundamentals or price action improves."
    return "Combined read: do not add risk until the business trend and chart improve."


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


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
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


def _earnings_freshness_fields(earnings_text: str) -> dict[str, str]:
    fields = {
        "event": "unknown",
        "loaded_date": "--",
        "sec_date": "--",
        "ir_date": "--",
        "verdict": "--",
    }
    for raw_line in earnings_text.splitlines():
        line = raw_line.strip().lstrip("-").strip()
        lower = line.lower()
        if lower.startswith("earnings event:"):
            fields["event"] = line.split(":", 1)[1].strip() or "unknown"
        elif lower.startswith("latest loaded source date:"):
            fields["loaded_date"] = line.split(":", 1)[1].strip() or "--"
        elif lower.startswith("latest sec filing date:"):
            fields["sec_date"] = line.split(":", 1)[1].strip() or "--"
        elif lower.startswith("latest company ir release date:"):
            fields["ir_date"] = line.split(":", 1)[1].strip() or "--"
        elif lower.startswith("freshness verdict:"):
            fields["verdict"] = line.split(":", 1)[1].strip() or "--"
    return fields


def _earnings_card_from_freshness(fields: dict[str, str], latest_8k: str) -> tuple[str, str, str]:
    verdict = fields.get("verdict", "--")
    lower = verdict.lower()
    event = fields.get("event", "unknown").lower()
    if "fresh earnings release found" in lower:
        return "Fresh Release", "good", verdict
    if "potentially stale" in lower:
        return "Potentially Stale", "bad", verdict
    if event == "today" and ("no fresh" in lower or "expected today" in lower):
        return "Earnings Today / Awaiting Release", "mixed", verdict
    if event == "today":
        return "Earnings Today", "mixed", verdict
    if event == "imminent":
        return "Earnings Imminent", "mixed", verdict
    if latest_8k:
        return "SEC Scan", "info", latest_8k
    return "No Fresh Release", "info", verdict


def _source_links_from_earnings_text(earnings_text: str) -> list[tuple[str, str, str]]:
    links: list[tuple[str, str, str]] = []
    for raw_line in earnings_text.splitlines():
        line = raw_line.strip()
        match = re.match(r"-\s*(?P<label>.+?)\s*\((?P<date>[^)]*)\):\s*(?P<url>https?://\S+|--)", line)
        if not match:
            continue
        url = match.group("url").rstrip(".,;")
        if url == "--":
            continue
        links.append((match.group("label").strip(), match.group("date").strip() or "--", url))
    return links


def _dedupe_source_links(rows: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    deduped: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, row_date, url in rows:
        key = (label.lower(), url)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((label, row_date, url))
    return deduped
