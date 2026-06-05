from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from app.analytics.stock_research import AdvancedIndicatorSnapshot, PortfolioSymbolContext


@dataclass(frozen=True)
class PullbackOpportunity:
    classification: str
    is_candidate: bool
    score: float
    support_distance_pct: float | None
    reasons: list[str]
    rejections: list[str]


@dataclass(frozen=True)
class ScenarioProbability:
    scenario: str
    probability: float
    likelihood: str
    why: str
    reference: str


@dataclass(frozen=True)
class ThesisReadout:
    horizon: str
    setup_type: str
    recommendation: str
    confidence: str
    why: str
    invalidation: str
    preferred_vehicle: str
    warnings: list[str]
    technical_read: str
    trade_judgment: str
    forecast: list[ScenarioProbability]


def build_thesis_readout(
    *,
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    fundamentals_text: str,
    valuation_score: float | None,
    macro_score: float,
    earnings_risk_score: float,
    technical_score: float,
    momentum_score: float,
    macro_text: str = "",
    command_center_report: Any | None = None,
) -> ThesisReadout:
    technical_read = _technical_read_label(technical_score, command_center_report)
    fundamentals = score_fundamental_thesis(fundamentals_text)
    pullback = classify_pullback_opportunity(
        indicators,
        fundamentals_text,
        valuation_score,
        macro_text or str(macro_score),
        context,
        command_center_report,
        earnings_risk_score=earnings_risk_score,
    )
    support_broken = _support_broken(indicators)
    below_long_term = _below_long_term_average(indicators)
    weak_thesis = fundamentals["score"] <= -15
    severe_event = earnings_risk_score >= 78
    macro_headwind = macro_score <= -45 or "headwind" in (macro_text or "").lower()
    warnings: list[str] = []

    if context.is_held and 0 < context.quantity < 100:
        warnings.append(f"One option contract controls 100 shares; current position is only {context.quantity:g} shares.")
    if severe_event:
        warnings.append("Near-term earnings/event risk can overwhelm otherwise clean technical levels.")
    if valuation_score is not None and valuation_score <= -45:
        warnings.append("Valuation is a headwind; do not treat a pullback as automatically cheap.")
    if macro_headwind:
        warnings.append("Macro is a headwind, so confirmation matters more than usual.")

    if support_broken or (below_long_term and indicators.trend == "bearish" and weak_thesis):
        recommendation = "Trim" if context.is_held else "Avoid"
        setup_type = "breakdown"
        preferred = "Cash" if not context.is_held else "No Trade"
        confidence = "High" if weak_thesis and support_broken else "Medium"
        why = _join_reasons(
            [
                f"Technical read remains {technical_read.lower()}",
                "support is broken" if support_broken else "",
                "price is below long-term trend references" if below_long_term else "",
                f"fundamental thesis is {fundamentals['label'].lower()}",
            ]
        )
        trade_judgment = "Bearish tape and thesis deterioration."
        return ThesisReadout(
            horizon="investment" if weak_thesis else "swing",
            setup_type=setup_type,
            recommendation=recommendation,
            confidence=confidence,
            why=why,
            invalidation=_confirmation_text(indicators),
            preferred_vehicle=preferred,
            warnings=_dedupe([*warnings, *pullback.rejections]),
            technical_read=technical_read,
            trade_judgment=trade_judgment,
            forecast=build_scenario_forecast(indicators, macro_score=macro_score, earnings_risk_score=earnings_risk_score),
        )

    if pullback.is_candidate:
        held_small = context.is_held and context.portfolio_weight < 0.03
        recommendation = "Accumulate Pullback" if not context.is_held or held_small else "Hold"
        preferred = "Starter Shares" if not context.is_held or held_small else "Shares"
        confidence = "Medium" if fundamentals["score"] >= 18 and not severe_event else "Low"
        why = (
            f"Technical read remains {technical_read.lower()}, but the longer-term thesis has not broken: "
            + _join_reasons(pullback.reasons[:4])
            + "."
        )
        trade_judgment = "Bearish tape, but constructive pullback candidate."
        return ThesisReadout(
            horizon="investment",
            setup_type="pullback",
            recommendation=recommendation,
            confidence=confidence,
            why=why,
            invalidation=_support_invalidation_text(indicators),
            preferred_vehicle=preferred,
            warnings=_dedupe(warnings + pullback.rejections),
            technical_read=technical_read,
            trade_judgment=trade_judgment,
            forecast=build_scenario_forecast(indicators, macro_score=macro_score, earnings_risk_score=earnings_risk_score),
        )

    if context.is_held and (technical_score <= -25 or momentum_score <= -35 or macro_headwind):
        size_warrants_option = context.quantity >= 80 or context.portfolio_weight >= 0.05
        recommendation = "Hedge Only If Size Warrants" if size_warrants_option else "Hold"
        preferred = "Protective Put" if size_warrants_option else "No Trade"
        why = (
            "Held position has visible tape risk, but the practical response depends on exposure size. "
            "For small positions, waiting, trimming shares, or doing nothing is often cleaner than buying a 100-share option contract."
        )
        return ThesisReadout(
            horizon="hedge",
            setup_type="hedge" if size_warrants_option else "chop",
            recommendation=recommendation,
            confidence="Medium" if size_warrants_option else "Low",
            why=why,
            invalidation=_support_invalidation_text(indicators),
            preferred_vehicle=preferred,
            warnings=_dedupe(warnings + pullback.rejections),
            technical_read=technical_read,
            trade_judgment="Held position: hedge only if exposure justifies contract size.",
            forecast=build_scenario_forecast(indicators, macro_score=macro_score, earnings_risk_score=earnings_risk_score),
        )

    if indicators.trend == "bullish" and indicators.momentum == "improving" and not severe_event and not macro_headwind:
        preferred = "Shares" if context.is_held else "Starter Shares"
        return ThesisReadout(
            horizon="investment",
            setup_type="breakout" if _near_resistance(indicators) else "pullback",
            recommendation="Add Carefully",
            confidence="Medium" if fundamentals["score"] >= -10 else "Low",
            why=(
                f"Technical read is {technical_read.lower()} and momentum is improving. "
                "Use confirmation and sizing discipline rather than assuming the move is risk-free."
            ),
            invalidation=_support_invalidation_text(indicators),
            preferred_vehicle=preferred,
            warnings=_dedupe(warnings),
            technical_read=technical_read,
            trade_judgment="Bullish trend with improving momentum; add only with confirmation.",
            forecast=build_scenario_forecast(indicators, macro_score=macro_score, earnings_risk_score=earnings_risk_score),
        )

    no_trade_warnings = _dedupe(warnings + pullback.rejections)
    return ThesisReadout(
        horizon="unknown" if fundamentals["label"] == "Unknown" else "swing",
        setup_type="no-trade" if severe_event or pullback.rejections else "chop",
        recommendation="Wait for Confirmation",
        confidence="Medium" if no_trade_warnings else "Low",
        why=(
            f"Technical read is {technical_read.lower()}, while thesis evidence is {fundamentals['label'].lower()}. "
            "That mix does not justify forcing a trade."
        ),
        invalidation=_support_invalidation_text(indicators),
        preferred_vehicle="No Trade",
        warnings=no_trade_warnings,
        technical_read=technical_read,
        trade_judgment="Mixed setup; wait for confirmation.",
        forecast=build_scenario_forecast(indicators, macro_score=macro_score, earnings_risk_score=earnings_risk_score),
    )


def classify_pullback_opportunity(
    indicators: AdvancedIndicatorSnapshot,
    fundamentals: str | dict[str, Any] | None,
    valuation: float | None,
    macro: str | float | None,
    context: PortfolioSymbolContext | None = None,
    command_center_report: Any | None = None,
    *,
    earnings_risk_score: float = 35.0,
) -> PullbackOpportunity:
    del context
    price = indicators.latest_close
    if price is None or price <= 0:
        return PullbackOpportunity("Incomplete", False, 0.0, None, [], ["Price history is unavailable."])

    fundamental = fundamentals if isinstance(fundamentals, dict) else score_fundamental_thesis(str(fundamentals or ""))
    fundamental_score = float(fundamental.get("score", 0.0) or 0.0)
    reasons: list[str] = []
    rejections: list[str] = []
    score = 0.0
    support_level = indicators.support or indicators.swing_low
    support_distance = _distance_pct(price, support_level)
    atr_pct = _atr_pct(indicators)
    near_support = support_distance is not None and support_distance >= -0.015 and support_distance <= max(0.035, atr_pct * 1.5)
    near_fib = _near_fibonacci_zone(indicators, price, tolerance=max(0.035, atr_pct * 1.5))
    long_term_ok = not _below_long_term_average(indicators)
    soft_rsi = indicators.rsi_14 is not None and 32 <= indicators.rsi_14 <= 50
    free_fall_rsi = indicators.rsi_14 is not None and indicators.rsi_14 < 30
    macro_score = _macro_score_value(macro)
    volume_distribution = _command_center_distribution(command_center_report)

    if _support_broken(indicators):
        rejections.append("support is broken, so this is not a buyable pullback yet")
    if _below_long_term_average(indicators) and indicators.trend == "bearish":
        rejections.append("price is below major long-term averages with bearish structure")
    if volume_distribution:
        rejections.append("volume/OBV evidence points to distribution")
    if earnings_risk_score >= 78:
        rejections.append("earnings/event risk is high")
    if fundamental_score <= -15:
        rejections.append("fundamentals are weak or deteriorating")
    if valuation is not None and valuation <= -45 and indicators.momentum == "weakening":
        rejections.append("valuation is demanding while momentum is deteriorating")
    if support_level is None and indicators.atr_14 is None:
        rejections.append("downside risk is undefined because no support or ATR line is available")
    if free_fall_rsi:
        rejections.append("RSI is in a free-fall zone rather than a controlled pullback")
    if macro_score <= -70:
        rejections.append("macro risk is severe")

    if long_term_ok:
        score += 22
        reasons.append("longer-term trend reference is not broken")
    if near_support or near_fib:
        score += 22
        reasons.append("price is near support or a pullback zone")
    if soft_rsi:
        score += 14
        reasons.append(f"RSI is soft but not washed out at {indicators.rsi_14:.1f}")
    if indicators.momentum in {"weakening", "neutral"} and not _support_broken(indicators):
        score += 8
        reasons.append(f"momentum is {indicators.momentum}, but structure has not failed")
    if fundamental_score >= 18:
        score += 20
        reasons.append(f"fundamentals are {fundamental.get('label', 'supportive').lower()}")
    elif fundamental_score >= -10:
        score += 8
        reasons.append("fundamentals are not a clear red flag")
    if valuation is None or valuation > -45:
        score += 6
        reasons.append("valuation is not an extreme red flag in the loaded data")
    if macro_score > -45 and earnings_risk_score < 75:
        score += 8
        reasons.append("macro and earnings risk are not severe")

    score = max(0.0, min(100.0, score))
    is_candidate = score >= 62 and not rejections and (near_support or near_fib)
    if is_candidate:
        classification = "Constructive Pullback Candidate"
    elif rejections:
        classification = "Rejected Buy-The-Dip"
    else:
        classification = "No Clear Pullback Edge"
    return PullbackOpportunity(classification, is_candidate, score, support_distance, reasons, rejections)


def build_scenario_forecast(
    indicators: AdvancedIndicatorSnapshot,
    *,
    macro_score: float,
    earnings_risk_score: float,
) -> list[ScenarioProbability]:
    price = indicators.latest_close
    weights = {
        "Rebound from support": 25.0,
        "Chop / sideways": 25.0,
        "Breakdown below support": 25.0,
        "Breakout above resistance": 25.0,
    }
    support_broken = _support_broken(indicators)
    atr_pct = _atr_pct(indicators)
    support_distance = _distance_pct(price, indicators.support or indicators.swing_low) if price is not None else None
    near_support = support_distance is not None and support_distance <= max(0.035, atr_pct * 1.5)
    near_resistance = _near_resistance(indicators)

    if indicators.trend == "bullish":
        weights["Rebound from support"] += 9
        weights["Breakout above resistance"] += 11
        weights["Breakdown below support"] -= 7
    elif indicators.trend == "bearish":
        weights["Breakdown below support"] += 17
        weights["Breakout above resistance"] -= 8
    else:
        weights["Chop / sideways"] += 10

    if near_support and not support_broken:
        weights["Rebound from support"] += 14
        weights["Breakdown below support"] += 4
    if support_broken:
        weights["Breakdown below support"] += 22
        weights["Rebound from support"] -= 10
    if near_resistance:
        weights["Breakout above resistance"] += 8
        weights["Chop / sideways"] += 3
    if indicators.momentum == "improving":
        weights["Breakout above resistance"] += 8
        weights["Rebound from support"] += 4
    elif indicators.momentum == "weakening":
        weights["Breakdown below support"] += 10
        weights["Chop / sideways"] += 4
    if indicators.rsi_14 is not None and 32 <= indicators.rsi_14 <= 45 and not support_broken:
        weights["Rebound from support"] += 6
    if indicators.volatility == "elevated" or earnings_risk_score >= 75:
        weights["Chop / sideways"] += 6
        weights["Breakdown below support"] += 6
    if macro_score <= -45:
        weights["Breakdown below support"] += 5
        weights["Breakout above resistance"] -= 4
    elif macro_score >= 45:
        weights["Breakout above resistance"] += 5

    cleaned = {key: max(5.0, value) for key, value in weights.items()}
    total = sum(cleaned.values()) or 1.0
    rows: list[ScenarioProbability] = []
    references = {
        "Rebound from support": _money(indicators.support or indicators.swing_low),
        "Chop / sideways": f"ATR {_money(indicators.atr_14)}",
        "Breakdown below support": _money(indicators.support or indicators.swing_low),
        "Breakout above resistance": _money(indicators.resistance or indicators.swing_high),
    }
    for scenario, weight in cleaned.items():
        probability = round((weight / total) * 100.0, 1)
        rows.append(
            ScenarioProbability(
                scenario=scenario,
                probability=probability,
                likelihood=_likelihood_label(probability),
                why=_forecast_reason(scenario, indicators),
                reference=references[scenario],
            )
        )
    rows.sort(key=lambda row: row.probability, reverse=True)
    return rows


def score_fundamental_thesis(text: str) -> dict[str, Any]:
    lower = text.lower()
    if not lower.strip() or "unavailable" in lower:
        return {"score": 0.0, "label": "Unknown", "reasons": ["fundamental source data unavailable"]}

    score = 0.0
    reasons: list[str] = []
    metric_values = _fundamental_percent_values(text)
    for label, value in metric_values:
        if value >= 10:
            score += 16
            reasons.append(f"{label} improved {value:+.1f}%")
        elif value > 0:
            score += 8
            reasons.append(f"{label} improved {value:+.1f}%")
        elif value <= -10:
            score -= 22
            reasons.append(f"{label} fell {value:+.1f}%")
        else:
            score -= 12
            reasons.append(f"{label} softened {value:+.1f}%")

    positive_terms = ("revenue growth", "net income improved", "operating income expanded", "cash flow", "profitable", "liquidity", "strong")
    negative_terms = ("margin pressure", "going concern", "cash used", "negative free cash", "debt pressure", "weak", "decline", "decreased")
    score += sum(6 for term in positive_terms if term in lower)
    score -= sum(10 for term in negative_terms if term in lower)
    if "cash equals roughly" in lower and "liabilities" in lower:
        score += 6
    if "liabilities are roughly" in lower and re.search(r"liabilities are roughly\s+(?:8\d|9\d|100)", lower):
        score -= 12

    score = max(-100.0, min(100.0, score))
    if score >= 45:
        label = "Strong"
    elif score >= 18:
        label = "Good"
    elif score <= -35:
        label = "Weak"
    elif score <= -15:
        label = "Deteriorating"
    else:
        label = "Mixed"
    return {"score": score, "label": label, "reasons": reasons or ["fundamental language is mixed"]}


def _fundamental_percent_values(text: str) -> list[tuple[str, float]]:
    values: list[tuple[str, float]] = []
    current_label = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        header = line.rstrip(":").lower()
        if header in {"revenue", "net income", "operating income", "diluted eps", "operating cash flow"}:
            current_label = header
            continue
        if "latest comparable-period change:" in line.lower() or "latest annual comparable-period change:" in line.lower():
            value = _parse_percent(line)
            if value is not None and current_label:
                values.append((current_label, value))
                continue
        match = re.match(r"-\s*(Revenue|Net income|Operating income|Diluted EPS|Operating cash flow):.*?\bYoY\s+([+-]?\d+(?:\.\d+)?)%", line, flags=re.IGNORECASE)
        if match:
            values.append((match.group(1).lower(), float(match.group(2))))
    return values[:6]


def _parse_percent(text: str) -> float | None:
    match = re.search(r"([+-]?\d+(?:,\d{3})*(?:\.\d+)?)\s*%", text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _support_broken(indicators: AdvancedIndicatorSnapshot) -> bool:
    price = indicators.latest_close
    support = indicators.support or indicators.swing_low
    if price is None or support is None or support <= 0:
        return False
    tolerance = max(0.015, _atr_pct(indicators) * 0.5)
    return price < support * (1 - tolerance)


def _below_long_term_average(indicators: AdvancedIndicatorSnapshot) -> bool:
    price = indicators.latest_close
    if price is None:
        return False
    if indicators.sma_200 is not None:
        return price < indicators.sma_200 * 0.98
    return indicators.sma_50 is not None and price < indicators.sma_50 * 0.97


def _near_resistance(indicators: AdvancedIndicatorSnapshot) -> bool:
    price = indicators.latest_close
    resistance = indicators.resistance or indicators.swing_high
    if price is None or resistance is None or resistance <= 0:
        return False
    return 0 <= (resistance - price) / price <= max(0.04, _atr_pct(indicators) * 2)


def _near_fibonacci_zone(indicators: AdvancedIndicatorSnapshot, price: float, *, tolerance: float) -> bool:
    for level in indicators.fibonacci_levels.values():
        if level > 0 and abs(price - level) / price <= tolerance:
            return True
    return False


def _command_center_distribution(command_center_report: Any | None) -> bool:
    if command_center_report is None:
        return False
    score = getattr(getattr(command_center_report, "scores", {}), "get", lambda _key, _default=None: None)("Volume")
    if score is not None and float(getattr(score, "score", 50.0) or 50.0) < 38:
        return True
    snapshots = getattr(command_center_report, "snapshots", {}) or {}
    for snapshot in snapshots.values():
        volume_read = getattr(snapshot, "volume_read", None)
        if "distribution" in str(getattr(volume_read, "accumulation_read", "")).lower():
            return True
    return False


def _atr_pct(indicators: AdvancedIndicatorSnapshot) -> float:
    price = indicators.latest_close or 0.0
    if price <= 0 or indicators.atr_14 is None:
        return 0.025
    return max(0.0, min(0.20, indicators.atr_14 / price))


def _distance_pct(price: float, level: float | None) -> float | None:
    if level is None or price <= 0:
        return None
    return (price - level) / price


def _macro_score_value(macro: str | float | None) -> float:
    if isinstance(macro, (int, float)):
        return float(macro)
    lower = str(macro or "").lower()
    score = 0.0
    for phrase in ("tailwind", "cooler", "rates down", "less hawkish"):
        if phrase in lower:
            score += 25
    for phrase in ("headwind", "hotter", "hawkish", "rates up", "higher yield"):
        if phrase in lower:
            score -= 25
    return max(-100.0, min(100.0, score))


def _technical_read_label(technical_score: float, command_center_report: Any | None) -> str:
    if command_center_report is not None:
        read = str(getattr(command_center_report, "overall_read", "") or "").strip()
        if read:
            return read
    if technical_score >= 25:
        return "Bullish"
    if technical_score <= -25:
        return "Bearish"
    return "Mixed"


def _support_invalidation_text(indicators: AdvancedIndicatorSnapshot) -> str:
    support = indicators.support or indicators.swing_low
    if support is None:
        return "Invalidation requires a clear support/risk line; current data does not provide one."
    return f"Losing support near {_money(support)} would invalidate the constructive thesis."


def _confirmation_text(indicators: AdvancedIndicatorSnapshot) -> str:
    resistance = indicators.resistance or indicators.swing_high
    if resistance is None:
        return "A reclaim of trend and resistance would be needed before changing the avoid read."
    return f"A reclaim above {_money(resistance)} with improving momentum would change the avoid read."


def _forecast_reason(scenario: str, indicators: AdvancedIndicatorSnapshot) -> str:
    if scenario == "Rebound from support":
        return "Model estimate from support distance, RSI zone, ATR, and trend. Not a guarantee."
    if scenario == "Breakdown below support":
        return "Model estimate from support integrity, trend, momentum, event risk, and volatility."
    if scenario == "Breakout above resistance":
        return "Model estimate from resistance distance, trend, momentum, and macro context."
    return "Model estimate from mixed trend, ATR, realized volatility proxy, and event risk."


def _likelihood_label(probability: float) -> str:
    if probability >= 35:
        return "Higher"
    if probability >= 22:
        return "Medium"
    return "Lower"


def _join_reasons(values: list[str]) -> str:
    clean = [value for value in values if value]
    return "; ".join(clean) if clean else "evidence is mixed"


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = str(value).strip()
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _money(value: float | None) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"${value:,.2f}"
