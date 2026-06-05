from __future__ import annotations

from dataclasses import dataclass

from app.analytics.decision_engine import ThesisReadout, build_thesis_readout
from app.analytics.stock_research import AdvancedIndicatorSnapshot, DataSourceStatus, PortfolioSymbolContext, ScenarioRow


@dataclass(frozen=True)
class BadgeReadout:
    title: str
    label: str
    status: str
    score: float
    why: str


@dataclass(frozen=True)
class ResearchDecisionReadout:
    technical_score: float
    risk_score: float
    momentum_score: float
    macro_score: float
    earnings_risk_score: float
    valuation_score: float | None
    portfolio_impact_score: float
    overall: BadgeReadout
    risk_level: BadgeReadout
    trend: BadgeReadout
    momentum: BadgeReadout
    volatility: BadgeReadout
    earnings_risk: BadgeReadout
    macro_backdrop: BadgeReadout
    position_impact: BadgeReadout
    action_bias: BadgeReadout
    valuation: BadgeReadout
    growth: BadgeReadout
    profitability: BadgeReadout
    balance_sheet: BadgeReadout
    cash_flow: BadgeReadout
    thesis: ThesisReadout
    summary: list[str]
    matters: list[str]
    changes_view: list[str]
    macro_good: list[str]
    macro_bad: list[str]
    macro_watch: list[str]
    top_things: list[str]
    operator_view: dict[str, str]


def build_decision_readout(
    *,
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    scenario_rows: list[ScenarioRow],
    earnings_text: str,
    fundamentals_text: str,
    macro_text: str,
    statuses: list[DataSourceStatus],
) -> ResearchDecisionReadout:
    technical_score = score_technicals(indicators)
    momentum_score = score_momentum(indicators)
    macro_score = score_macro_text(macro_text)
    earnings_risk_score = score_earnings_risk(earnings_text)
    portfolio_impact_score = score_portfolio_impact(context)
    valuation_score = score_valuation(fundamentals_text)
    risk_score = score_risk(indicators, context, earnings_risk_score, statuses)

    overall_score = (technical_score * 0.45) + (momentum_score * 0.20) + (macro_score * 0.15) - (risk_score - 50) * 0.20
    overall = _direction_badge("Overall Read", overall_score, bullish="Bullish", neutral="Neutral", bearish="Bearish")
    risk_level = _risk_badge("Risk Level", risk_score, why=_risk_why(indicators, context, earnings_risk_score, statuses))
    trend = _trend_badge(indicators)
    momentum = _momentum_badge(momentum_score, indicators)
    volatility = _volatility_badge(indicators)
    earnings_risk = _risk_badge("Earnings Risk", earnings_risk_score, why=_earnings_why(earnings_text))
    macro_backdrop = _direction_badge("Macro Backdrop", macro_score, bullish="Tailwind", neutral="Neutral", bearish="Headwind")
    position_impact = _position_badge(portfolio_impact_score, context)
    valuation = _valuation_badge(valuation_score)
    growth = _text_factor_badge("Growth", fundamentals_text, positives=("revenue growth", "increased", "growth", "higher"), negatives=("decline", "decreased", "lower", "weak"))
    profitability = _text_factor_badge("Profitability", fundamentals_text, positives=("margin", "profit", "income increased"), negatives=("loss", "margin pressure", "income decreased"))
    balance_sheet = _text_factor_badge("Balance Sheet", fundamentals_text, positives=("cash", "liquidity"), negatives=("debt", "leverage", "going concern"))
    cash_flow = _text_factor_badge("Cash Flow", fundamentals_text, positives=("free cash flow", "operating cash"), negatives=("cash used", "negative free cash"))
    thesis = build_thesis_readout(
        indicators=indicators,
        context=context,
        fundamentals_text=fundamentals_text,
        valuation_score=valuation_score,
        macro_score=macro_score,
        earnings_risk_score=earnings_risk_score,
        technical_score=technical_score,
        momentum_score=momentum_score,
        macro_text=macro_text,
    )
    action_bias = _thesis_action_badge(thesis, overall.score)

    summary = simple_summary(overall, risk_level, macro_backdrop, context, indicators)
    summary.insert(1, thesis.trade_judgment)
    matters = what_matters(indicators, context, earnings_risk, macro_backdrop)
    matters.append(f"THESIS: {thesis.recommendation}; preferred vehicle: {thesis.preferred_vehicle}.")
    changes_view = what_changes_view(indicators, context, macro_backdrop)
    if thesis.invalidation:
        changes_view.insert(0, thesis.invalidation)
    macro_good, macro_bad, macro_watch = macro_bullets(macro_text, macro_backdrop)
    top_things = top_three_things(overall, risk_level, macro_backdrop, indicators)
    operator_view = build_operator_view(action_bias, position_impact, indicators, context, macro_backdrop)
    operator_view["Thesis read"] = thesis.trade_judgment
    operator_view["Preferred vehicle"] = thesis.preferred_vehicle
    operator_view["What proves it wrong"] = thesis.invalidation

    return ResearchDecisionReadout(
        technical_score=technical_score,
        risk_score=risk_score,
        momentum_score=momentum_score,
        macro_score=macro_score,
        earnings_risk_score=earnings_risk_score,
        valuation_score=valuation_score,
        portfolio_impact_score=portfolio_impact_score,
        overall=overall,
        risk_level=risk_level,
        trend=trend,
        momentum=momentum,
        volatility=volatility,
        earnings_risk=earnings_risk,
        macro_backdrop=macro_backdrop,
        position_impact=position_impact,
        action_bias=action_bias,
        valuation=valuation,
        growth=growth,
        profitability=profitability,
        balance_sheet=balance_sheet,
        cash_flow=cash_flow,
        thesis=thesis,
        summary=summary,
        matters=matters,
        changes_view=changes_view,
        macro_good=macro_good,
        macro_bad=macro_bad,
        macro_watch=macro_watch,
        top_things=top_things,
        operator_view=operator_view,
    )


def score_technicals(indicators: AdvancedIndicatorSnapshot) -> float:
    if indicators.latest_close is None:
        return 0.0
    score = 0.0
    if indicators.trend == "bullish":
        score += 35
    elif indicators.trend == "bearish":
        score -= 35
    if indicators.sma_20 and indicators.latest_close > indicators.sma_20:
        score += 10
    elif indicators.sma_20:
        score -= 10
    if indicators.sma_50 and indicators.latest_close > indicators.sma_50:
        score += 15
    elif indicators.sma_50:
        score -= 15
    if indicators.sma_200 and indicators.latest_close > indicators.sma_200:
        score += 15
    elif indicators.sma_200:
        score -= 15
    if indicators.rsi_14 is not None:
        if 45 <= indicators.rsi_14 <= 65:
            score += 8
        elif indicators.rsi_14 >= 75 or indicators.rsi_14 <= 25:
            score -= 8
    if indicators.macd_histogram is not None:
        score += 12 if indicators.macd_histogram > 0 else -12
    return _clamp(score, -100, 100)


def score_momentum(indicators: AdvancedIndicatorSnapshot) -> float:
    if indicators.rsi_14 is None and indicators.macd_histogram is None:
        return 0.0
    score = 0.0
    if indicators.rsi_14 is not None:
        score += _clamp((indicators.rsi_14 - 50) * 2.2, -55, 55)
        if 35 <= indicators.rsi_14 <= 45 and _constructive_pullback_tape(indicators):
            score += 12
        if indicators.rsi_14 >= 75:
            score -= 15
        if indicators.rsi_14 <= 25:
            score += 15
    if indicators.macd_histogram is not None:
        score += 30 if indicators.macd_histogram > 0 else -30
    return _clamp(score, -100, 100)


def score_risk(
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    earnings_risk_score: float,
    statuses: list[DataSourceStatus],
) -> float:
    score = 25.0
    if indicators.volatility == "elevated":
        score += 30
    elif indicators.volatility == "normal":
        score += 15
    elif indicators.volatility == "low":
        score += 5
    if context.portfolio_weight >= 0.10:
        score += 30
    elif context.portfolio_weight >= 0.05:
        score += 18
    elif context.portfolio_weight >= 0.02:
        score += 8
    score += earnings_risk_score * 0.25
    if any(status.status == "error" for status in statuses):
        score += 6
    return _clamp(score, 0, 100)


def score_macro_text(text: str) -> float:
    lower = text.lower()
    score = 0.0
    for phrase in ("tailwind", "cooler", "less hawkish", "rates down", "energy cool"):
        if phrase in lower:
            score += 18
    for phrase in ("headwind", "hotter", "hawkish", "higher inflation", "higher yield", "rates up", "energy inflation pressure"):
        if phrase in lower:
            score -= 18
    if "neutral/mixed" in lower or "neutral" in lower:
        score *= 0.7
    return _clamp(score, -100, 100)


def score_earnings_risk(text: str) -> float:
    lower = text.lower()
    if "unavailable" in lower or "unknown" in lower or not lower.strip():
        return 50.0
    if "earnings today" in lower or "earnings event: today" in lower or "awaiting release" in lower or "potentially stale" in lower:
        return 82.0
    if "earnings imminent" in lower or "earnings event: imminent" in lower:
        return 78.0
    if "within 10" in lower or "soon" in lower or "next earnings" in lower:
        return 75.0
    if "earnings release" in lower or "8-k" in lower:
        return 45.0
    return 35.0


def score_valuation(text: str) -> float | None:
    lower = text.lower()
    if "unavailable" in lower or "unknown" in lower or not lower.strip():
        return None
    score = 0.0
    if "expensive" in lower or "premium" in lower or "high p/e" in lower:
        score -= 45
    if "cheap" in lower or "discount" in lower or "low p/e" in lower:
        score += 45
    if "profitable" in lower or "cash flow" in lower:
        score += 10
    return _clamp(score, -100, 100)


def score_portfolio_impact(context: PortfolioSymbolContext) -> float:
    return _clamp(context.portfolio_weight * 1000, 0, 100)


def scenario_impact_bar_value(row: ScenarioRow, max_abs_impact: float | None = None) -> float:
    denominator = max_abs_impact if max_abs_impact and max_abs_impact > 0 else max(abs(row.portfolio_pnl_impact), 0.0001)
    return _clamp((row.portfolio_pnl_impact / denominator) * 100, -100, 100)


def direction_strength_label(score: float) -> str:
    if score >= 70:
        return "Very Strong"
    if score >= 35:
        return "Strong"
    if score >= 12:
        return "Leaning Bullish"
    if score <= -70:
        return "Very Weak"
    if score <= -35:
        return "Weak"
    if score <= -12:
        return "Leaning Bearish"
    return "Mixed"


def risk_heat_label(score: float) -> str:
    if score >= 85:
        return "Very Hot"
    if score >= 70:
        return "Hot"
    if score >= 55:
        return "Medium-Hot"
    if score >= 35:
        return "Medium"
    return "Cool"


def simple_summary(
    overall: BadgeReadout,
    risk_level: BadgeReadout,
    macro_backdrop: BadgeReadout,
    context: PortfolioSymbolContext,
    indicators: AdvancedIndicatorSnapshot,
) -> list[str]:
    size_text = "This is not currently in the portfolio." if not context.is_held else (
        "This position is small, so it should not move the whole portfolio much."
        if context.portfolio_weight < 0.02
        else "This position is large enough to matter to portfolio P&L."
    )
    trend_text = (
        "The chart looks constructive."
        if overall.label == "Bullish"
        else "The chart and risk signals are mixed."
        if overall.label == "Neutral"
        else "The chart is not giving a clean long setup right now."
    )
    macro_text = f"Macro is a {macro_backdrop.label.lower()} in this read."
    if indicators.latest_close is None:
        trend_text = "Price history is missing, so the technical read is limited."
    return [size_text, trend_text, f"{risk_level.label} risk: {risk_level.why}", macro_text]


def what_matters(
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    earnings_risk: BadgeReadout,
    macro_backdrop: BadgeReadout,
) -> list[str]:
    lines = []
    if indicators.trend == "bullish":
        lines.append("GOOD: price structure is leaning up.")
    elif indicators.trend == "bearish":
        lines.append("BAD: price structure is leaning down.")
    else:
        lines.append("WATCH: trend is mixed or not fully confirmed.")
    lines.append(f"POSITION: {context.portfolio_weight:.2%} of portfolio.")
    lines.append(f"EARNINGS: {earnings_risk.label} risk.")
    lines.append(f"MACRO: {macro_backdrop.label}.")
    return lines


def what_changes_view(
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    macro_backdrop: BadgeReadout,
) -> list[str]:
    lines = []
    if indicators.resistance:
        lines.append(f"Bull case improves above nearby resistance around ${indicators.resistance:,.2f}.")
    if indicators.support:
        lines.append(f"Risk worsens if price loses support around ${indicators.support:,.2f}.")
    if context.portfolio_weight >= 0.05:
        lines.append("Portfolio risk drops if position size is trimmed below 5%.")
    if macro_backdrop.label == "Headwind":
        lines.append("Macro view improves if inflation/rates data cools.")
    return lines or ["More price history or fresh source data would sharpen this view."]


def macro_bullets(text: str, macro_backdrop: BadgeReadout) -> tuple[list[str], list[str], list[str]]:
    lower = text.lower()
    good: list[str] = []
    bad: list[str] = []
    watch: list[str] = []
    if "cooler" in lower or "tailwind" in lower:
        good.append("Cooler inflation/rates language is supportive for risk assets.")
    if "hotter" in lower or "hawkish" in lower or macro_backdrop.label == "Headwind":
        bad.append("Hot inflation or hawkish rates language can pressure stock multiples.")
    if "energy" in lower:
        watch.append("Energy data can feed inflation expectations.")
    if "labor" in lower:
        watch.append("Labor data can shift Fed/rate expectations.")
    if not good:
        good.append("No clear macro tailwind found in the loaded snapshot.")
    if not bad:
        bad.append("No single severe macro warning found in the loaded snapshot.")
    if not watch:
        watch.append("Watch the next official inflation, labor, and Treasury releases.")
    return good, bad, watch


def top_three_things(
    overall: BadgeReadout,
    risk_level: BadgeReadout,
    macro_backdrop: BadgeReadout,
    indicators: AdvancedIndicatorSnapshot,
) -> list[str]:
    positive = "Chart constructive" if overall.label == "Bullish" else "Setup is still mixed"
    if indicators.trend == "bullish":
        positive = "Trend is above key averages"
    elif indicators.momentum == "improving":
        positive = "Momentum is improving"

    risk = f"Risk is {risk_level.label.lower()}"
    if macro_backdrop.label == "Headwind":
        risk = "Macro is a headwind"
    elif indicators.volatility == "elevated":
        risk = "Volatility is hot"

    if indicators.resistance:
        trigger = f"Break above ${indicators.resistance:,.2f} improves setup"
    elif indicators.support:
        trigger = f"Lose ${indicators.support:,.2f} and risk worsens"
    else:
        trigger = "Need cleaner price history for key levels"
    return [positive, risk, trigger]


def build_operator_view(
    action_bias: BadgeReadout,
    position_impact: BadgeReadout,
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    macro_backdrop: BadgeReadout,
) -> dict[str, str]:
    entry = (
        f"Confirmation above ${indicators.resistance:,.2f}"
        if indicators.resistance
        else "Wait for a cleaner confirmation level"
    )
    risk_line = (
        f"Risk worsens below ${indicators.support:,.2f}"
        if indicators.support
        else "No clean risk line from current candles"
    )
    danger = "Macro/rates are the main drag." if macro_backdrop.label == "Headwind" else "Mixed technicals are the main uncertainty."
    if indicators.volatility == "elevated":
        danger = "Volatility is hot, so bad entries can move fast."
    if not context.is_held:
        danger = "Not held yet; this is a watchlist setup."
    upside = entry if indicators.resistance else "Fresh highs or improving momentum would help."
    return {
        "Bias": action_bias.label,
        "Entry / confirmation": entry,
        "Risk line": risk_line,
        "Position impact": position_impact.label,
        "Main danger": danger,
        "Main upside trigger": upside,
    }


def _direction_badge(title: str, score: float, *, bullish: str, neutral: str, bearish: str) -> BadgeReadout:
    if score >= 25:
        return BadgeReadout(title, bullish, "good", score, f"score {score:.0f} is supportive.")
    if score <= -25:
        return BadgeReadout(title, bearish, "bad", score, f"score {score:.0f} is a headwind.")
    return BadgeReadout(title, neutral, "mixed", score, f"score {score:.0f} is mixed.")


def _risk_badge(title: str, score: float, *, why: str) -> BadgeReadout:
    if score >= 70:
        return BadgeReadout(title, "High", "bad", score, why)
    if score >= 40:
        return BadgeReadout(title, "Medium", "mixed", score, why)
    return BadgeReadout(title, "Low", "good", score, why)


def _trend_badge(indicators: AdvancedIndicatorSnapshot) -> BadgeReadout:
    if indicators.trend == "bullish":
        return BadgeReadout("Trend", "Up", "good", 75, "price is above key moving averages.")
    if indicators.trend == "bearish":
        return BadgeReadout("Trend", "Down", "bad", -75, "price is below key moving averages.")
    if indicators.trend == "unknown":
        return BadgeReadout("Trend", "Unknown", "info", 0, "price history is unavailable.")
    return BadgeReadout("Trend", "Sideways", "mixed", 0, "price is not cleanly stacked above or below averages.")


def _momentum_badge(score: float, indicators: AdvancedIndicatorSnapshot) -> BadgeReadout:
    if score >= 30:
        return BadgeReadout("Momentum", "Strong", "good", score, "RSI/MACD lean positive.")
    if score <= -30:
        return BadgeReadout("Momentum", "Weak", "bad", score, "RSI/MACD lean negative.")
    label = "Neutral" if indicators.rsi_14 is not None else "Unknown"
    return BadgeReadout("Momentum", label, "mixed" if label == "Neutral" else "info", score, "momentum is not stretched either way.")


def _volatility_badge(indicators: AdvancedIndicatorSnapshot) -> BadgeReadout:
    if indicators.volatility == "elevated":
        return BadgeReadout("Volatility", "Hot", "bad", 80, "ATR is elevated versus price.")
    if indicators.volatility == "low":
        return BadgeReadout("Volatility", "Calm", "good", 20, "ATR is low versus price.")
    if indicators.volatility == "normal":
        return BadgeReadout("Volatility", "Normal", "mixed", 45, "ATR is in a normal range.")
    return BadgeReadout("Volatility", "Unknown", "info", 50, "ATR is unavailable.")


def _position_badge(score: float, context: PortfolioSymbolContext) -> BadgeReadout:
    if not context.is_held:
        return BadgeReadout("Position Impact", "Watchlist", "info", score, "symbol is not currently held.")
    if context.portfolio_weight >= 0.10:
        return BadgeReadout("Position Impact", "Large", "bad", score, "position is above 10% of portfolio.")
    if context.portfolio_weight >= 0.03:
        return BadgeReadout("Position Impact", "Moderate", "mixed", score, "position is meaningful but not dominant.")
    return BadgeReadout("Position Impact", "Small", "good", score, "position is a small slice of portfolio.")


def _valuation_badge(score: float | None) -> BadgeReadout:
    if score is None:
        return BadgeReadout("Valuation", "Unknown", "info", 0, "fundamental valuation fields are unavailable.")
    if score >= 25:
        return BadgeReadout("Valuation", "Cheap/Fair", "good", score, "available fundamentals lean supportive.")
    if score <= -25:
        return BadgeReadout("Valuation", "Expensive", "bad", score, "available fundamentals look demanding.")
    return BadgeReadout("Valuation", "Fair/Unknown", "mixed", score, "available fundamentals are mixed.")


def _text_factor_badge(title: str, text: str, *, positives: tuple[str, ...], negatives: tuple[str, ...]) -> BadgeReadout:
    lower = text.lower()
    if "unavailable" in lower or not lower.strip():
        return BadgeReadout(title, "Unknown", "info", 0, "source data unavailable.")
    positive_hits = sum(1 for phrase in positives if phrase in lower)
    negative_hits = sum(1 for phrase in negatives if phrase in lower)
    if positive_hits > negative_hits:
        return BadgeReadout(title, "Strong" if title != "Balance Sheet" else "Safe", "good", 60, "positive language appears in source data.")
    if negative_hits > positive_hits:
        return BadgeReadout(title, "Weak" if title != "Balance Sheet" else "Risky", "bad", -60, "risk language appears in source data.")
    return BadgeReadout(title, "OK/Mixed" if title != "Balance Sheet" else "Watch", "mixed", 0, "available data is mixed.")


def _action_badge(overall_score: float, risk_score: float, context: PortfolioSymbolContext) -> BadgeReadout:
    if risk_score >= 75 and context.is_held:
        return BadgeReadout("Action Bias", "Hedge/Trim", "bad", overall_score, "risk is high relative to the setup.")
    if overall_score >= 35 and risk_score < 55:
        return BadgeReadout("Action Bias", "Add Carefully", "good", overall_score, "setup is supportive and risk is contained.")
    if overall_score <= -35:
        return BadgeReadout("Action Bias", "Avoid", "bad", overall_score, "setup is not supportive.")
    return BadgeReadout("Action Bias", "Watch", "mixed", overall_score, "mixed read; wait for confirmation.")


def _thesis_action_badge(thesis: ThesisReadout, score: float) -> BadgeReadout:
    status_by_recommendation = {
        "Accumulate Pullback": "good",
        "Add Carefully": "good",
        "Hold": "mixed",
        "Wait for Confirmation": "mixed",
        "Hedge Only If Size Warrants": "mixed",
        "Avoid": "bad",
        "Trim": "bad",
    }
    status = status_by_recommendation.get(thesis.recommendation, "mixed")
    why = f"{thesis.trade_judgment} Preferred vehicle: {thesis.preferred_vehicle}. {thesis.why}"
    return BadgeReadout("Action Bias", thesis.recommendation, status, score, why)


def _constructive_pullback_tape(indicators: AdvancedIndicatorSnapshot) -> bool:
    price = indicators.latest_close
    if price is None or price <= 0:
        return False
    support = indicators.support or indicators.swing_low
    support_ok = support is not None and price >= support * 0.985 and (price - support) / price <= 0.05
    long_term_ok = indicators.sma_200 is None or price >= indicators.sma_200 * 0.98
    trend_ok = indicators.trend in {"bullish", "sideways"}
    return support_ok and long_term_ok and trend_ok


def _risk_why(
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    earnings_risk_score: float,
    statuses: list[DataSourceStatus],
) -> str:
    parts: list[str] = []
    parts.append(f"position weight {context.portfolio_weight:.2%}")
    parts.append(f"volatility {indicators.volatility}")
    if earnings_risk_score >= 70:
        parts.append("earnings risk elevated")
    if any(status.status == "error" for status in statuses):
        parts.append("some data sources errored")
    return "; ".join(parts) + "."


def _earnings_why(text: str) -> str:
    lower = text.lower()
    if "unavailable" in lower or "unknown" in lower:
        return "earnings source is unavailable or incomplete."
    if "earnings today" in lower or "earnings event: today" in lower or "awaiting release" in lower:
        return "earnings event is today and the release may still be pending."
    if "potentially stale" in lower:
        return "loaded earnings source may be stale versus a near-term event."
    if "earnings imminent" in lower or "earnings event: imminent" in lower:
        return "earnings event is near enough to affect risk and options premium."
    if "soon" in lower or "next earnings" in lower:
        return "next earnings timing may be near."
    return "no near-term earnings shock detected in loaded text."


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
