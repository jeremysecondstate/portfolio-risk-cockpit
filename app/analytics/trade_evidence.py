from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.analytics.research_scoring import BadgeReadout, ResearchDecisionReadout, direction_strength_label, risk_heat_label
from app.analytics.stock_research import (
    AdvancedIndicatorSnapshot,
    DataSourceStatus,
    PortfolioSymbolContext,
    build_planned_stock_context,
    build_scenario_rows,
    distance_to_price,
    generated_risk_budget,
    technical_scenario_moves,
)
from app.analytics.technical_analysis import Candle, TimeframeTechnicalSnapshot, simple_moving_average

TRADE_EVIDENCE_SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "data" / "trade_evidence_snapshots.jsonl"
BENCHMARK_SYMBOLS = ("SPY", "QQQ", "IWM")


@dataclass(frozen=True)
class EvidenceGrade:
    category: str
    grade: str
    status: str
    score: float | None
    why: str


@dataclass(frozen=True)
class EvidenceLayer:
    label: str
    status: str
    score: float | None
    lines: list[str]
    missing: list[str]


@dataclass(frozen=True)
class TradeEvidenceReport:
    symbol: str
    verdict: str
    posture: str
    setup_type: str
    confidence: str
    grades: list[EvidenceGrade]
    supporting_evidence: list[str]
    contradictory_evidence: list[str]
    market_regime: list[str]
    relative_strength: list[str]
    multi_timeframe: list[str]
    volatility_levels: list[str]
    options_iv: list[str]
    liquidity_execution: list[str]
    event_risk: list[str]
    portfolio_impact: list[str]
    dumb_if: list[str]
    changes_mind: list[str]
    missing_data: list[str]


def build_trade_evidence_report(
    *,
    symbol: str,
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    decision: ResearchDecisionReadout,
    scenario_rows: list[Any],
    earnings_text: str,
    macro_text: str,
    statuses: list[DataSourceStatus],
    quote: dict[str, Any] | None = None,
    option_chain_rows: list[dict[str, Any]] | None = None,
    symbol_candles: list[Candle] | None = None,
    command_center_snapshots: dict[str, TimeframeTechnicalSnapshot] | None = None,
    market_indicators: dict[str, AdvancedIndicatorSnapshot] | None = None,
    market_candles: dict[str, list[Candle]] | None = None,
) -> TradeEvidenceReport:
    clean_symbol = symbol.strip().upper()
    symbol_candles = symbol_candles or []
    market_indicators = market_indicators or {}
    market_candles = market_candles or {}
    option_chain_rows = option_chain_rows or []

    setup_type = classify_setup_type(indicators, symbol_candles)
    market_layer = market_regime_layer(decision, market_indicators, statuses)
    relative_layer = relative_strength_layer(clean_symbol, symbol_candles, market_candles)
    timeframe_layer = multi_timeframe_layer(indicators, symbol_candles, command_center_snapshots=command_center_snapshots)
    levels_layer = volatility_levels_layer(indicators, context)
    options_layer = options_iv_layer(clean_symbol, context, option_chain_rows)
    execution_layer = liquidity_execution_layer(quote, indicators, context, option_chain_rows)
    event_layer = event_risk_layer(decision, earnings_text, macro_text, statuses)
    portfolio_layer = portfolio_impact_layer(indicators, context, decision, earnings_text)

    technical_score = _technical_evidence_score(indicators, decision)
    grades = [
        _grade("Technical Setup", technical_score, _technical_grade_why(indicators, decision)),
        _grade("Market Regime", market_layer.score, market_layer.label),
        _grade("Relative Strength", relative_layer.score, relative_layer.label),
        _grade("Multi-Timeframe", timeframe_layer.score, timeframe_layer.label),
        _grade("Volatility Levels", levels_layer.score, levels_layer.label),
        _grade("Options / IV", options_layer.score, options_layer.label),
        _grade("Liquidity / Execution", execution_layer.score, execution_layer.label),
        _grade("Portfolio Fit", portfolio_layer.score, portfolio_layer.label),
        _grade("Event Risk", event_layer.score, event_layer.label),
    ]

    supporting = _supporting_evidence(
        indicators,
        decision,
        market_layer,
        relative_layer,
        timeframe_layer,
        levels_layer,
        options_layer,
        execution_layer,
        portfolio_layer,
    )
    contradictions = _contradictions(
        indicators,
        decision,
        market_layer,
        relative_layer,
        timeframe_layer,
        levels_layer,
        options_layer,
        execution_layer,
        event_layer,
        portfolio_layer,
    )
    missing = _dedupe(
        [
            *market_layer.missing,
            *relative_layer.missing,
            *timeframe_layer.missing,
            *levels_layer.missing,
            *options_layer.missing,
            *execution_layer.missing,
            *event_layer.missing,
            *portfolio_layer.missing,
            *_missing_from_statuses(statuses),
        ]
    )
    posture = _posture(indicators, grades, contradictions, missing, decision)
    confidence = _confidence(posture, grades, missing)
    verdict = _verdict(clean_symbol, posture, setup_type, decision, supporting, contradictions, missing)

    return TradeEvidenceReport(
        symbol=clean_symbol,
        verdict=verdict,
        posture=posture,
        setup_type=setup_type,
        confidence=confidence,
        grades=grades,
        supporting_evidence=supporting or ["No strong supporting evidence is loaded yet."],
        contradictory_evidence=contradictions or ["No major contradiction is obvious from the loaded data."],
        market_regime=market_layer.lines,
        relative_strength=relative_layer.lines,
        multi_timeframe=timeframe_layer.lines,
        volatility_levels=levels_layer.lines,
        options_iv=options_layer.lines,
        liquidity_execution=execution_layer.lines,
        event_risk=event_layer.lines,
        portfolio_impact=portfolio_layer.lines,
        dumb_if=_dumb_if(setup_type, contradictions, levels_layer, options_layer, execution_layer, event_layer, portfolio_layer),
        changes_mind=_changes_mind(indicators, decision, market_layer, relative_layer, options_layer, event_layer),
        missing_data=missing or ["No critical data source gap was detected in this run."],
    )


def classify_setup_type(indicators: AdvancedIndicatorSnapshot, candles: list[Candle] | None = None) -> str:
    last = indicators.latest_close
    if last is None:
        return "No-read"
    atr = indicators.atr_14 or 0.0
    near_resistance = _within_atr(last, indicators.resistance, atr, limit=0.8) or _within_percent(last, indicators.resistance, 0.025)
    near_support = _within_atr(last, indicators.support, atr, limit=0.8) or _within_percent(last, indicators.support, 0.025)
    if indicators.rsi_14 is not None and indicators.rsi_14 >= 72 and indicators.trend == "bullish":
        return "Extended trend / chase-risk continuation"
    if indicators.rsi_14 is not None and indicators.rsi_14 <= 32:
        return "Oversold bounce / mean reversion"
    if indicators.trend == "bullish" and indicators.momentum == "improving" and near_resistance:
        return "Breakout attempt"
    if indicators.trend == "bullish" and indicators.momentum == "improving":
        return "Trend continuation"
    if near_support and indicators.trend != "bearish":
        return "Support bounce"
    if indicators.trend == "bearish" and indicators.momentum == "weakening":
        return "Breakdown / bearish continuation"
    if candles and len(candles) >= 2:
        gap = (candles[-1].open - candles[-2].close) / max(candles[-2].close, 0.01)
        if abs(gap) >= 0.04:
            return "Gap reaction"
    return "Mixed / confirmation-needed setup"


def market_regime_layer(
    decision: ResearchDecisionReadout,
    market_indicators: dict[str, AdvancedIndicatorSnapshot],
    statuses: list[DataSourceStatus],
) -> EvidenceLayer:
    lines: list[str] = []
    missing: list[str] = []
    bullish = 0
    bearish = 0
    for benchmark in BENCHMARK_SYMBOLS:
        snapshot = market_indicators.get(benchmark)
        if snapshot is None or snapshot.latest_close is None:
            missing.append(f"{benchmark} market-regime candles are unavailable.")
            continue
        if snapshot.trend == "bullish":
            bullish += 1
        elif snapshot.trend == "bearish":
            bearish += 1
        lines.append(f"{benchmark}: {snapshot.trend} trend, {snapshot.momentum} momentum, volatility {snapshot.volatility}.")

    macro_score = _supportive_from_direction_score(decision.macro_score)
    if not lines:
        lines.append(f"Market ETF regime is not loaded; falling back to macro backdrop: {decision.macro_backdrop.label}.")
    lines.append(f"Macro backdrop: {decision.macro_backdrop.label} ({decision.macro_backdrop.why})")

    if bullish or bearish:
        trend_score = 50 + ((bullish - bearish) / max(bullish + bearish, 1)) * 35
        score = _clamp((trend_score * 0.65) + (macro_score * 0.35), 0, 100)
    else:
        score = macro_score if decision.macro_backdrop.label != "Neutral" else None

    if any(status.source == "Official macro" and status.status == "error" for status in statuses):
        missing.append("Official macro feed errored, so regime confidence is lower.")
    missing.append("VIX, breadth, put/call, sector ETF trend, and 10Y/DXY regime hooks are not configured in this v1.")
    label = _layer_label(score, good="Risk-on / supportive", mixed="Mixed regime", bad="Risk-off / hostile", none="Market regime no-read")
    return EvidenceLayer(label, _status_from_score(score), score, lines, missing)


def relative_strength_layer(
    symbol: str,
    symbol_candles: list[Candle],
    market_candles: dict[str, list[Candle]],
) -> EvidenceLayer:
    if not symbol_candles:
        return EvidenceLayer(
            "Relative strength no-read",
            "info",
            None,
            ["Selected-symbol candles are unavailable, so relative strength cannot be calculated."],
            ["Selected-symbol price history is unavailable."],
        )

    symbol_returns = _return_windows(symbol_candles)
    lines: list[str] = []
    diffs: list[float] = []
    missing: list[str] = []
    for benchmark in ("SPY", "QQQ"):
        candles = market_candles.get(benchmark)
        if not candles:
            missing.append(f"{benchmark} benchmark candles are unavailable for relative strength.")
            continue
        benchmark_returns = _return_windows(candles)
        for window in (5, 20, 60):
            symbol_return = symbol_returns.get(window)
            benchmark_return = benchmark_returns.get(window)
            if symbol_return is None or benchmark_return is None:
                continue
            diff = symbol_return - benchmark_return
            diffs.append(diff)
            result = "outperforming" if diff > 0 else "lagging" if diff < 0 else "matching"
            lines.append(f"{window}D vs {benchmark}: {result} by {_percent(diff)} ({symbol} {_percent(symbol_return)} vs {benchmark} {_percent(benchmark_return)}).")

    if not diffs:
        return EvidenceLayer(
            "Relative strength no-read",
            "info",
            None,
            lines or ["Relative strength needs selected-symbol and benchmark candles over 5D/20D/60D."],
            missing or ["Benchmark return windows are incomplete."],
        )

    average_diff = sum(diffs) / len(diffs)
    score = _clamp(50 + average_diff * 450, 0, 100)
    label = _layer_label(score, good="Leadership / relative strength", mixed="Mixed relative strength", bad="Relative weakness", none="Relative strength no-read")
    return EvidenceLayer(label, _status_from_score(score), score, lines[:8], missing)


def multi_timeframe_layer(
    indicators: AdvancedIndicatorSnapshot,
    candles: list[Candle],
    *,
    command_center_snapshots: dict[str, TimeframeTechnicalSnapshot] | None = None,
) -> EvidenceLayer:
    lines = [f"Daily/swing: trend {indicators.trend}, momentum {indicators.momentum}, volatility {indicators.volatility}."]
    missing: list[str] = []
    scores: list[float] = []
    if indicators.trend == "bullish":
        scores.append(75)
    elif indicators.trend == "bearish":
        scores.append(25)
    elif indicators.trend == "sideways":
        scores.append(50)
    else:
        missing.append("Daily trend is unavailable.")

    intraday_snapshot = _preferred_intraday_snapshot(command_center_snapshots or {})
    if intraday_snapshot is None:
        missing.append("Intraday 5m/15m technical context is unavailable for this evidence layer.")
    else:
        lines.append(_intraday_snapshot_line(intraday_snapshot))
        intraday_score = _snapshot_alignment_score(indicators, intraday_snapshot)
        if intraday_score is not None:
            scores.append(intraday_score)

    weekly_score, weekly_line = _weekly_trend_score(candles)
    if weekly_score is None:
        missing.append("Weekly trend is unavailable because daily history is too short.")
    else:
        scores.append(weekly_score)
    lines.append(weekly_line)

    if indicators.momentum == "improving":
        scores.append(65)
    elif indicators.momentum == "weakening":
        scores.append(35)
    elif indicators.momentum == "neutral":
        scores.append(50)

    score = sum(scores) / len(scores) if scores else None
    label = _layer_label(score, good="Timeframes aligned enough", mixed="Timeframes mixed", bad="Timeframes fighting the setup", none="Multi-timeframe no-read")
    return EvidenceLayer(label, _status_from_score(score), score, lines, missing)


def _preferred_intraday_snapshot(snapshots: dict[str, TimeframeTechnicalSnapshot]) -> TimeframeTechnicalSnapshot | None:
    candidates = [
        snapshot
        for snapshot in snapshots.values()
        if snapshot.candle_count > 0 and (snapshot.role in {"timing", "setup"} or "m" in snapshot.label.lower())
    ]
    if not candidates:
        return None
    key_order = {"timing_5m": 0, "timing_1m": 1, "setup_30m": 2}
    return sorted(candidates, key=lambda snapshot: (key_order.get(snapshot.key, 99), -snapshot.candle_count))[0]


def _intraday_snapshot_line(snapshot: TimeframeTechnicalSnapshot) -> str:
    vwap_note = ""
    if snapshot.session_vwap is not None:
        vwap_note = f", price vs session VWAP {_pct_points(snapshot.vwap_distance_percent)}"
    elif snapshot.rolling_vwap_20 is not None:
        vwap_note = f", price vs rolling 20-bar VWAP {_pct_points(snapshot.vwap_distance_percent)}"
    return (
        f"Intraday timing ({snapshot.label}): trend {snapshot.trend_structure}, "
        f"RSI {_number(snapshot.rsi_14)}, latest {_money(snapshot.latest_close)}{vwap_note}."
    )


def _snapshot_alignment_score(indicators: AdvancedIndicatorSnapshot, snapshot: TimeframeTechnicalSnapshot) -> float | None:
    components = [
        component.score
        for name, component in snapshot.scores.items()
        if name in {"Trend", "Momentum", "Volatility/Risk"}
    ]
    if not components:
        return None
    score = sum(components) / len(components)
    trend_score = snapshot.scores.get("Trend")
    if trend_score is not None:
        if indicators.trend == "bullish" and trend_score.score >= 60:
            score += 5
        elif indicators.trend == "bearish" and trend_score.score <= 40:
            score += 5
        elif indicators.trend in {"bullish", "bearish"} and 45 <= trend_score.score <= 55:
            score -= 5
        elif indicators.trend == "bullish" and trend_score.score <= 40:
            score -= 10
        elif indicators.trend == "bearish" and trend_score.score >= 60:
            score -= 10
    return _clamp(score, 0, 100)


def volatility_levels_layer(indicators: AdvancedIndicatorSnapshot, context: PortfolioSymbolContext) -> EvidenceLayer:
    price = context.last_price or indicators.latest_close
    missing: list[str] = []
    lines: list[str] = []
    scores: list[float] = []
    if price is None or price <= 0:
        return EvidenceLayer("Volatility-adjusted levels no-read", "info", None, ["Price is unavailable."], ["Current price is unavailable."])

    atr = indicators.atr_14
    if atr is None or atr <= 0:
        missing.append("ATR is unavailable, so support/resistance cannot be volatility-adjusted.")
        lines.append("ATR-adjusted level quality is unavailable.")
    else:
        atr_pct = atr / price
        lines.append(f"ATR 14: {_money(atr)} ({atr_pct:.1%} of price).")
        if indicators.support:
            support_atr = abs(price - indicators.support) / atr
            lines.append(f"Support {_money(indicators.support)} is {support_atr:.1f} ATR below price.")
            scores.append(_distance_score(support_atr, ideal_low=0.8, ideal_high=2.5))
        else:
            missing.append("Support level is unavailable.")
        if indicators.resistance:
            resistance_atr = abs(indicators.resistance - price) / atr
            lines.append(f"Resistance {_money(indicators.resistance)} is {resistance_atr:.1f} ATR above price.")
            if indicators.support and price > indicators.support:
                support_risk = max(price - indicators.support, 0.01)
                reward = max(indicators.resistance - price, 0.0)
                rr = reward / support_risk
                lines.append(f"Resistance-to-support reward/risk proxy is {rr:.1f}x.")
                scores.append(80 if rr >= 1.8 else 60 if rr >= 1.1 else 35)
        else:
            missing.append("Resistance level is unavailable.")
        if atr_pct >= 0.055:
            scores.append(35)
            lines.append("Volatility is hot enough that normal noise can overwhelm tight levels.")
        elif atr_pct <= 0.018:
            scores.append(70)
            lines.append("ATR is relatively calm; levels may be cleaner, but false calm is still possible.")
        else:
            scores.append(60)

    score = sum(scores) / len(scores) if scores else None
    label = _layer_label(score, good="Levels look volatility-aware", mixed="Levels need caution", bad="Levels look fragile", none="Volatility levels no-read")
    return EvidenceLayer(label, _status_from_score(score), score, lines, missing)


def options_iv_layer(symbol: str, context: PortfolioSymbolContext, chain_rows: list[dict[str, Any]]) -> EvidenceLayer:
    if not chain_rows:
        return EvidenceLayer(
            "Options chain no-read",
            "info",
            None,
            ["Option chain is unavailable; IV, expected move, skew, and contract liquidity cannot be judged."],
            ["Schwab option chain is unavailable for this report."],
        )
    underlying = context.last_price or _first_underlying(chain_rows)
    near_rows = _near_option_rows(chain_rows, underlying)
    ivs = [_contract_iv(contract) for row in near_rows for contract in (row.get("call"), row.get("put")) if isinstance(contract, dict)]
    ivs = [iv for iv in ivs if iv is not None and iv > 0]
    lines: list[str] = []
    missing: list[str] = []
    scores: list[float] = []
    if ivs:
        average_iv = sum(ivs) / len(ivs)
        dte = _nearest_dte(near_rows)
        if underlying and dte:
            expected_pct = average_iv * math.sqrt(max(dte, 1) / 365)
            lines.append(f"Nearest-chain IV: {average_iv:.1%}; expected move by {dte}d is about +/-{expected_pct:.1%} ({_money(underlying * expected_pct)}).")
        else:
            lines.append(f"Nearest-chain IV: {average_iv:.1%}; expected move needs underlying price and DTE.")
            missing.append("Expected move could not be calculated from option DTE/underlying price.")
        if average_iv >= 0.70:
            lines.append("IV is very high; long premium has a large hurdle and defined-risk structures deserve extra scrutiny.")
            scores.append(30)
        elif average_iv >= 0.45:
            lines.append("IV is elevated; buying premium may be expensive unless the expected move is justified.")
            scores.append(45)
        elif average_iv <= 0.20:
            lines.append("IV is relatively low; premium buying has a lower volatility hurdle, but direction still must be right.")
            scores.append(72)
        else:
            lines.append("IV is moderate from the available chain.")
            scores.append(62)
    else:
        lines.append("Option IV fields are missing from the loaded chain.")
        missing.append("Implied volatility fields are unavailable in the option chain.")

    spread_score, spread_line = _option_spread_score(near_rows)
    if spread_score is None:
        missing.append("Option bid/ask spread quality is unavailable.")
    else:
        scores.append(spread_score)
    lines.append(spread_line)
    oi_volume_score, oi_line = _option_interest_score(near_rows)
    if oi_volume_score is None:
        missing.append("Option open interest/volume fields are unavailable.")
    else:
        scores.append(oi_volume_score)
    lines.append(oi_line)
    missing.append("IV rank/percentile, skew, term structure, and max-pain are not configured in this v1.")
    score = sum(scores) / len(scores) if scores else None
    label = _layer_label(score, good="Options conditions usable", mixed="Options conditions mixed", bad="Options conditions dangerous", none="Options no-read")
    return EvidenceLayer(label, _status_from_score(score), score, lines, missing)


def liquidity_execution_layer(
    quote: dict[str, Any] | None,
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    chain_rows: list[dict[str, Any]],
) -> EvidenceLayer:
    lines: list[str] = []
    missing: list[str] = []
    scores: list[float] = []
    bid, ask = _quote_bid_ask(quote)
    price = context.last_price or indicators.latest_close
    if bid is not None and ask is not None and ask > bid and price:
        spread_bps = (ask - bid) / price * 10_000
        lines.append(f"Stock spread: {spread_bps:.1f} bps ({_money(bid)} bid / {_money(ask)} ask).")
        scores.append(85 if spread_bps <= 5 else 70 if spread_bps <= 15 else 45 if spread_bps <= 40 else 25)
    else:
        missing.append("Stock bid/ask spread is unavailable.")
        lines.append("Stock bid/ask spread is unavailable.")

    if indicators.volume_average_20 is not None:
        lines.append(f"20-day average volume: {indicators.volume_average_20:,.0f} shares.")
        if indicators.volume_average_20 >= 1_000_000:
            scores.append(78)
        elif indicators.volume_average_20 >= 250_000:
            scores.append(62)
        else:
            scores.append(35)
    else:
        missing.append("Average stock volume is unavailable.")

    option_spread_score, option_spread_line = _option_spread_score(_near_option_rows(chain_rows, price))
    if option_spread_score is not None:
        lines.append(option_spread_line)
        scores.append(option_spread_score)
    elif chain_rows:
        missing.append("Option spread quality is unavailable.")
    else:
        missing.append("Option-chain execution quality is unavailable.")
    score = sum(scores) / len(scores) if scores else None
    label = _layer_label(score, good="Execution quality clean", mixed="Execution quality mixed", bad="Execution quality poor", none="Execution no-read")
    return EvidenceLayer(label, _status_from_score(score), score, lines, missing)


def event_risk_layer(
    decision: ResearchDecisionReadout,
    earnings_text: str,
    macro_text: str,
    statuses: list[DataSourceStatus],
) -> EvidenceLayer:
    supportive_score = _clamp(100 - decision.earnings_risk_score, 0, 100)
    lines = [f"Earnings risk: {decision.earnings_risk.label} ({decision.earnings_risk.why})"]
    event_lines = _event_lines(earnings_text)
    lines.extend(event_lines or ["No specific near-term earnings event line was found in loaded sources."])
    if "fomc" in macro_text.lower() or "cpi" in macro_text.lower() or "payroll" in macro_text.lower():
        lines.append("Macro feed includes inflation/labor/rates context, but this is not a forward event calendar.")
    missing: list[str] = ["Forward CPI/FOMC/jobs/Fed-speaker/Treasury-auction calendar hooks are not configured in this v1."]
    if any(status.source == "SEC filings/earnings" and status.status == "error" for status in statuses):
        missing.append("SEC earnings/filing layer errored.")
    label = _layer_label(supportive_score, good="Event risk manageable", mixed="Event risk needs caution", bad="Event risk dominates", none="Event risk no-read")
    return EvidenceLayer(label, _status_from_score(supportive_score), supportive_score, lines, missing)


def portfolio_impact_layer(
    indicators: AdvancedIndicatorSnapshot,
    context: PortfolioSymbolContext,
    decision: ResearchDecisionReadout,
    earnings_text: str,
) -> EvidenceLayer:
    risk_budget = generated_risk_budget(
        context,
        indicators,
        macro_label=decision.macro_backdrop.label,
        risk_level_label=decision.risk_level.label,
        action_bias_label=decision.action_bias.label,
        earnings_text=earnings_text,
    )
    planned_context, stock_plan = build_planned_stock_context(context, indicators, risk_budget)
    moves = technical_scenario_moves(planned_context, indicators)
    rows = build_scenario_rows(planned_context, moves)
    worst = min(rows, key=lambda row: row.position_pnl, default=None)
    lines = [
        f"Current exposure: {_money(context.market_value)}; portfolio weight {context.portfolio_weight:.2%}; cash {_money(context.cash_available)}.",
        f"Generated risk budget: {_money(risk_budget.amount)} based on portfolio, cash, technical line, macro, event risk, and position state.",
        f"Model stock scenario: {stock_plan.quantity:g} shares, {_money(stock_plan.notional)} notional, {stock_plan.portfolio_weight:.2%} portfolio weight.",
    ]
    if worst is not None:
        lines.append(f"Worst loaded model scenario: {worst.scenario} move, {_money(worst.position_pnl)} P/L, {worst.portfolio_pnl_impact:+.2%} portfolio impact.")
    scores: list[float] = []
    weight = abs(context.portfolio_weight)
    if weight >= 0.15:
        scores.append(20)
    elif weight >= 0.10:
        scores.append(35)
    elif weight >= 0.05:
        scores.append(55)
    elif context.is_held:
        scores.append(70)
    else:
        scores.append(65)
    cash_ratio = max(context.cash_available, 0.0) / max(context.portfolio_value, 0.01)
    scores.append(30 if cash_ratio < 0.05 else 55 if cash_ratio < 0.15 else 75)
    if stock_plan.portfolio_weight >= 0.08:
        scores.append(35)
    elif stock_plan.portfolio_weight > 0:
        scores.append(65)
    score = sum(scores) / len(scores)
    label = _layer_label(score, good="Portfolio fit acceptable", mixed="Portfolio fit needs sizing discipline", bad="Portfolio fit is the problem", none="Portfolio fit no-read")
    return EvidenceLayer(label, _status_from_score(score), score, lines, [])


def evidence_scorecards(report: TradeEvidenceReport) -> list[BadgeReadout]:
    cards = [BadgeReadout("Posture", report.posture, _posture_status(report.posture), 0, report.verdict)]
    cards.extend(BadgeReadout(grade.category, grade.grade, grade.status, grade.score or 0.0, grade.why) for grade in report.grades[:7])
    return cards


def format_trade_evidence_report(report: TradeEvidenceReport) -> str:
    lines = [
        f"Schwab Trade Evidence Desk - {report.symbol}",
        "=" * min(len(f"Schwab Trade Evidence Desk - {report.symbol}"), 80),
        "",
        f"Verdict: {report.verdict}",
        f"Posture: {report.posture}",
        f"Setup type: {report.setup_type}",
        f"Confidence: {report.confidence}",
        "",
        "Scorecard:",
    ]
    lines.extend(f"- {grade.category}: {grade.grade} - {grade.why}" for grade in report.grades)
    _extend_section(lines, "Supporting evidence", report.supporting_evidence)
    _extend_section(lines, "Contradictions", report.contradictory_evidence)
    _extend_section(lines, "Market regime", report.market_regime)
    _extend_section(lines, "Relative strength", report.relative_strength)
    _extend_section(lines, "Multi-timeframe trend", report.multi_timeframe)
    _extend_section(lines, "Volatility-adjusted levels", report.volatility_levels)
    _extend_section(lines, "Options IV / expected move", report.options_iv)
    _extend_section(lines, "Liquidity / execution quality", report.liquidity_execution)
    _extend_section(lines, "Event risk", report.event_risk)
    _extend_section(lines, "Portfolio impact", report.portfolio_impact)
    _extend_section(lines, "What would make this trade dumb?", report.dumb_if)
    _extend_section(lines, "What would change my mind?", report.changes_mind)
    _extend_section(lines, "Missing data / confidence limits", report.missing_data)
    lines.extend(
        [
            "",
            "Boundary:",
            "- This is a trade-quality and risk-posture assessment, not a recommendation to buy, sell, open, close, or size a position.",
        ]
    )
    return "\n".join(lines)


def trade_evidence_snapshot_payload(report: TradeEvidenceReport) -> dict[str, Any]:
    return {
        "captured_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "symbol": report.symbol,
        "posture": report.posture,
        "setup_type": report.setup_type,
        "confidence": report.confidence,
        "verdict": report.verdict,
        "grades": [grade.__dict__ for grade in report.grades],
        "supporting_evidence": report.supporting_evidence,
        "contradictory_evidence": report.contradictory_evidence,
        "dumb_if": report.dumb_if,
        "changes_mind": report.changes_mind,
        "missing_data": report.missing_data,
    }


def append_trade_evidence_snapshot(
    report: TradeEvidenceReport,
    path: str | Path = TRADE_EVIDENCE_SNAPSHOT_PATH,
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(trade_evidence_snapshot_payload(report), sort_keys=True) + "\n")
    return output


def _supporting_evidence(
    indicators: AdvancedIndicatorSnapshot,
    decision: ResearchDecisionReadout,
    market_layer: EvidenceLayer,
    relative_layer: EvidenceLayer,
    timeframe_layer: EvidenceLayer,
    levels_layer: EvidenceLayer,
    options_layer: EvidenceLayer,
    execution_layer: EvidenceLayer,
    portfolio_layer: EvidenceLayer,
) -> list[str]:
    lines: list[str] = []
    if decision.overall.label == "Bullish" or decision.technical_score >= 35:
        lines.append(f"Technical score is supportive: {direction_strength_label(decision.technical_score)} ({decision.technical_score:.0f}).")
    if indicators.trend == "bullish":
        lines.append("Price trend is bullish against the loaded moving-average stack.")
    if indicators.momentum == "improving":
        lines.append("Momentum is improving by RSI/MACD read.")
    for layer, label in (
        (market_layer, "Market regime"),
        (relative_layer, "Relative strength"),
        (timeframe_layer, "Multi-timeframe read"),
        (levels_layer, "Volatility-adjusted levels"),
        (options_layer, "Options/IV"),
        (execution_layer, "Execution quality"),
        (portfolio_layer, "Portfolio fit"),
    ):
        if layer.score is not None and layer.score >= 65:
            lines.append(f"{label}: {layer.label}.")
    return lines[:8]


def _contradictions(
    indicators: AdvancedIndicatorSnapshot,
    decision: ResearchDecisionReadout,
    market_layer: EvidenceLayer,
    relative_layer: EvidenceLayer,
    timeframe_layer: EvidenceLayer,
    levels_layer: EvidenceLayer,
    options_layer: EvidenceLayer,
    execution_layer: EvidenceLayer,
    event_layer: EvidenceLayer,
    portfolio_layer: EvidenceLayer,
) -> list[str]:
    lines: list[str] = []
    if decision.overall.label == "Bearish" or decision.technical_score <= -25:
        lines.append(f"Technical score is a headwind: {direction_strength_label(decision.technical_score)} ({decision.technical_score:.0f}).")
    if indicators.trend == "bearish":
        lines.append("Trend is bearish; bullish/add-risk setups need stronger confirmation.")
    if indicators.momentum == "weakening":
        lines.append("Momentum is weakening by RSI/MACD read.")
    for layer, label in (
        (market_layer, "Market regime"),
        (relative_layer, "Relative strength"),
        (timeframe_layer, "Multi-timeframe read"),
        (levels_layer, "Volatility-adjusted levels"),
        (options_layer, "Options/IV"),
        (execution_layer, "Execution quality"),
        (event_layer, "Event risk"),
        (portfolio_layer, "Portfolio fit"),
    ):
        if layer.score is not None and layer.score <= 40:
            lines.append(f"{label}: {layer.label}.")
    if decision.risk_score >= 70:
        lines.append(f"Risk heat is {risk_heat_label(decision.risk_score)} ({decision.risk_score:.0f}/100).")
    return lines[:10]


def _dumb_if(
    setup_type: str,
    contradictions: list[str],
    levels_layer: EvidenceLayer,
    options_layer: EvidenceLayer,
    execution_layer: EvidenceLayer,
    event_layer: EvidenceLayer,
    portfolio_layer: EvidenceLayer,
) -> list[str]:
    lines = [
        "The actual order ignores the report posture and uses size as if the setup were clean.",
        "Price loses the invalidation/risk line and the thesis is still treated as intact.",
    ]
    if "Breakout" in setup_type:
        lines.append("The breakout fails back below resistance without volume or follow-through.")
    if event_layer.score is not None and event_layer.score <= 40:
        lines.append("Earnings or macro event risk sits inside the intended holding window.")
    if options_layer.score is not None and options_layer.score <= 45:
        lines.append("The option spread/IV hurdle is accepted without requiring a move larger than the expected move.")
    if execution_layer.score is not None and execution_layer.score <= 45:
        lines.append("Bid/ask spreads are wide enough that the planned entry assumes unrealistic fills.")
    if portfolio_layer.score is not None and portfolio_layer.score <= 45:
        lines.append("The trade adds concentration when portfolio fit is already the main problem.")
    for contradiction in contradictions[:3]:
        lines.append(f"The contradiction is ignored: {contradiction}")
    return _dedupe(lines)[:8]


def _changes_mind(
    indicators: AdvancedIndicatorSnapshot,
    decision: ResearchDecisionReadout,
    market_layer: EvidenceLayer,
    relative_layer: EvidenceLayer,
    options_layer: EvidenceLayer,
    event_layer: EvidenceLayer,
) -> list[str]:
    lines: list[str] = []
    if indicators.resistance:
        lines.append(f"More constructive: price holds above resistance around {_money(indicators.resistance)} with volume confirmation.")
    if indicators.support:
        lines.append(f"More defensive: price loses support around {_money(indicators.support)} or cannot reclaim it quickly.")
    if market_layer.score is not None and market_layer.score < 55:
        lines.append("Market regime improves if SPY/QQQ/IWM regain bullish trend or macro stops reading as a headwind.")
    if relative_layer.score is not None and relative_layer.score < 55:
        lines.append("Relative-strength read improves if the symbol outperforms SPY/QQQ over the next 5D/20D windows.")
    if options_layer.score is not None and options_layer.score < 55:
        lines.append("Options read improves if IV/spreads cool or a defined-risk structure better matches the expected move.")
    if event_layer.score is not None and event_layer.score < 55:
        lines.append("Event risk improves after the earnings/macro catalyst is known and the price reaction is absorbed.")
    if decision.risk_score >= 70:
        lines.append("Overall posture improves if risk heat drops below hot while technical evidence remains intact.")
    return lines or ["More source coverage and cleaner confirmation would sharpen the read."]


def _posture(
    indicators: AdvancedIndicatorSnapshot,
    grades: list[EvidenceGrade],
    contradictions: list[str],
    missing: list[str],
    decision: ResearchDecisionReadout,
) -> str:
    if indicators.latest_close is None:
        return "NO-READ"
    bad = sum(1 for grade in grades if grade.status == "bad")
    no_read = sum(1 for grade in grades if grade.grade == "NO READ")
    if decision.risk_score >= 78 or bad >= 3:
        return "DEFENSIVE"
    if bad >= 2 and len(contradictions) >= 3:
        return "DEFENSIVE"
    if bad >= 1 or len(contradictions) >= 3 or no_read >= 3:
        return "CAUTIOUS"
    if any("option chain" in item.lower() or "benchmark" in item.lower() for item in missing):
        return "CAUTIOUS"
    return "NORMAL"


def _confidence(posture: str, grades: list[EvidenceGrade], missing: list[str]) -> str:
    no_read = sum(1 for grade in grades if grade.grade == "NO READ")
    if posture == "NO-READ" or no_read >= 3:
        return "Low"
    if len(missing) >= 5 or no_read >= 1:
        return "Medium-Low"
    if any(grade.status == "bad" for grade in grades):
        return "Medium"
    return "Medium-High"


def _verdict(
    symbol: str,
    posture: str,
    setup_type: str,
    decision: ResearchDecisionReadout,
    supporting: list[str],
    contradictions: list[str],
    missing: list[str],
) -> str:
    direction = decision.overall.label.lower()
    if posture == "NO-READ":
        return f"No-read for {symbol}. Core price/evidence data is missing, so the setup cannot be judged cleanly."
    if posture == "DEFENSIVE":
        blocker = contradictions[0] if contradictions else "risk evidence dominates the setup."
        return f"Defensive {direction} read. Setup type is {setup_type}; the main issue is: {blocker}"
    if posture == "CAUTIOUS":
        reason = contradictions[0] if contradictions else (missing[0] if missing else "evidence is mixed.")
        return f"Cautious {direction} read. Setup type is {setup_type}; evidence exists, but {reason}"
    support = supporting[0] if supporting else "loaded evidence is not fighting the setup."
    return f"Normal {direction} read. Setup type is {setup_type}; {support}"


def _technical_evidence_score(indicators: AdvancedIndicatorSnapshot, decision: ResearchDecisionReadout) -> float | None:
    if indicators.latest_close is None:
        return None
    return _clamp(50 + decision.technical_score / 2, 0, 100)


def _technical_grade_why(indicators: AdvancedIndicatorSnapshot, decision: ResearchDecisionReadout) -> str:
    if indicators.latest_close is None:
        return "Price history is unavailable."
    return f"Trend {indicators.trend}, momentum {indicators.momentum}; technical score {decision.technical_score:.0f}."


def _grade(category: str, score: float | None, why: str) -> EvidenceGrade:
    if score is None:
        return EvidenceGrade(category, "NO READ", "info", None, why)
    if score >= 85:
        grade = "A"
    elif score >= 70:
        grade = "B"
    elif score >= 55:
        grade = "C"
    elif score >= 40:
        grade = "D"
    else:
        grade = "F"
    return EvidenceGrade(category, grade, _status_from_score(score), score, why)


def _layer_label(score: float | None, *, good: str, mixed: str, bad: str, none: str) -> str:
    if score is None:
        return none
    if score >= 65:
        return good
    if score <= 40:
        return bad
    return mixed


def _status_from_score(score: float | None) -> str:
    if score is None:
        return "info"
    if score >= 65:
        return "good"
    if score <= 40:
        return "bad"
    return "mixed"


def _posture_status(posture: str) -> str:
    if posture == "NORMAL":
        return "good"
    if posture == "CAUTIOUS":
        return "mixed"
    if posture == "DEFENSIVE":
        return "bad"
    return "info"


def _supportive_from_direction_score(score: float) -> float:
    return _clamp(50 + score / 2, 0, 100)


def _weekly_trend_score(candles: list[Candle]) -> tuple[float | None, str]:
    if len(candles) < 80:
        return None, "Weekly/position: not enough daily history for a weekly proxy."
    weekly_closes = [candles[index].close for index in range(4, len(candles), 5)]
    if not weekly_closes or weekly_closes[-1] != candles[-1].close:
        weekly_closes.append(candles[-1].close)
    sma_10 = simple_moving_average(weekly_closes, 10)
    sma_40 = simple_moving_average(weekly_closes, 40)
    latest = weekly_closes[-1]
    if sma_10 is None:
        return None, "Weekly/position: not enough weekly proxy closes."
    if sma_40 is not None and latest > sma_10 > sma_40:
        return 78, f"Weekly/position: constructive. Latest {_money(latest)} is above 10-week and 40-week proxies."
    if sma_40 is not None and latest < sma_10 < sma_40:
        return 25, f"Weekly/position: weak. Latest {_money(latest)} is below 10-week and 40-week proxies."
    if latest > sma_10:
        return 62, f"Weekly/position: improving but not fully stacked. Latest {_money(latest)} is above 10-week proxy."
    return 38, f"Weekly/position: soft. Latest {_money(latest)} is below 10-week proxy."


def _return_windows(candles: list[Candle]) -> dict[int, float | None]:
    return {window: _window_return(candles, window) for window in (5, 20, 60)}


def _window_return(candles: list[Candle], window: int) -> float | None:
    if len(candles) <= window:
        return None
    start = candles[-window - 1].close
    end = candles[-1].close
    if start <= 0:
        return None
    return (end / start) - 1


def _distance_score(value: float, *, ideal_low: float, ideal_high: float) -> float:
    if value < 0.45:
        return 32
    if ideal_low <= value <= ideal_high:
        return 78
    if value <= ideal_high * 1.8:
        return 58
    return 42


def _near_option_rows(rows: list[dict[str, Any]], underlying: float | None) -> list[dict[str, Any]]:
    usable = [row for row in rows if isinstance(row, dict)]
    if underlying is None or underlying <= 0:
        return usable[:12]
    return sorted(usable, key=lambda row: (row.get("dte") or 9999, abs((_to_float(row.get("strike")) or 0.0) - underlying)))[:12]


def _nearest_dte(rows: list[dict[str, Any]]) -> int | None:
    values = [_to_int(row.get("dte")) for row in rows]
    values = [value for value in values if value is not None and value > 0]
    return min(values) if values else None


def _contract_iv(contract: dict[str, Any]) -> float | None:
    value = _first_number(contract, "impliedVolatility", "volatility", "theoreticalVolatility")
    if value is None or value <= 0:
        return None
    if value > 3:
        value = value / 100
    return value if value <= 5 else None


def _option_spread_score(rows: list[dict[str, Any]]) -> tuple[float | None, str]:
    spreads: list[float] = []
    for row in rows:
        for side in ("call", "put"):
            contract = row.get(side)
            if not isinstance(contract, dict):
                continue
            bid = _first_number(contract, "bid", "bidPrice")
            ask = _first_number(contract, "ask", "askPrice")
            if bid is None or ask is None or ask <= 0 or ask < bid:
                continue
            mid = max((bid + ask) / 2, 0.01)
            spreads.append((ask - bid) / mid)
    if not spreads:
        return None, "Option bid/ask spread quality is unavailable."
    average = sum(spreads) / len(spreads)
    score = 82 if average <= 0.10 else 65 if average <= 0.22 else 42 if average <= 0.40 else 22
    return score, f"Average near-chain option spread is {average:.1%} of mid premium."


def _option_interest_score(rows: list[dict[str, Any]]) -> tuple[float | None, str]:
    interest: list[int] = []
    volume: list[int] = []
    for row in rows:
        for side in ("call", "put"):
            contract = row.get(side)
            if not isinstance(contract, dict):
                continue
            oi = _first_int(contract, "openInterest", "open_interest")
            vol = _first_int(contract, "totalVolume", "volume")
            if oi is not None:
                interest.append(oi)
            if vol is not None:
                volume.append(vol)
    if not interest and not volume:
        return None, "Option open interest/volume is unavailable."
    avg_oi = sum(interest) / len(interest) if interest else 0.0
    avg_vol = sum(volume) / len(volume) if volume else 0.0
    score = 78 if avg_oi >= 500 or avg_vol >= 150 else 60 if avg_oi >= 100 or avg_vol >= 25 else 38
    return score, f"Near-chain option liquidity: average OI {avg_oi:,.0f}; average volume {avg_vol:,.0f}."


def _quote_bid_ask(quote: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not quote:
        return None, None
    body = quote.get("quote") if isinstance(quote.get("quote"), dict) else quote
    return _first_number(body, "bidPrice", "bid"), _first_number(body, "askPrice", "ask")


def _first_underlying(rows: list[dict[str, Any]]) -> float | None:
    for row in rows:
        value = _first_number(row, "underlyingPrice", "underlying_price")
        if value is not None:
            return value
    return None


def _event_lines(text: str) -> list[str]:
    keywords = ("earnings event", "next earnings", "freshness verdict", "guidance", "awaiting release", "imminent", "today")
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip(" -\t")
        if line and any(keyword in line.lower() for keyword in keywords):
            lines.append(line[:180] + ("..." if len(line) > 180 else ""))
    return lines[:5]


def _missing_from_statuses(statuses: list[DataSourceStatus]) -> list[str]:
    missing: list[str] = []
    for status in statuses:
        if status.status == "error":
            missing.append(f"{status.source} error: {status.message or 'unavailable'}.")
        elif status.status in {"cached", "stale"}:
            missing.append(f"{status.source} is {status.status}; confidence is lower than fresh data.")
    return missing


def _extend_section(lines: list[str], title: str, rows: list[str]) -> None:
    lines.extend(["", f"{title}:"])
    lines.extend(f"- {row}" for row in rows)


def _within_atr(price: float, level: float | None, atr: float, *, limit: float) -> bool:
    if level is None or atr <= 0:
        return False
    return abs(price - level) / atr <= limit


def _within_percent(price: float, level: float | None, percent: float) -> bool:
    if level is None or price <= 0:
        return False
    return abs(price - level) / price <= percent


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _to_float(source.get(key))
        if value is not None:
            return value
    return None


def _first_int(source: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _to_int(source.get(key))
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


def _to_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _money(value: float | None) -> str:
    if value is None:
        return "--"
    prefix = "-$" if value < 0 else "$"
    return f"{prefix}{abs(value):,.2f}"


def _number(value: float | None) -> str:
    return "--" if value is None else f"{value:.1f}"


def _pct_points(value: float | None) -> str:
    return "--" if value is None else f"{value:+.1f}%"


def _percent(value: float | None) -> str:
    return "--" if value is None else f"{value:+.1%}"


def _dedupe(lines: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        clean = str(line).strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        deduped.append(clean)
    return deduped


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
