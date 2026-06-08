from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.analytics.technical_analysis import Candle, ema_series, macd, rsi, simple_moving_average
from app.core.portfolio import Portfolio, Position

PRICE_HISTORY_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "schwab_price_history_cache.json"
PRICE_HISTORY_CACHE_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class AdvancedIndicatorSnapshot:
    symbol: str
    latest_close: float | None
    sma_20: float | None
    sma_50: float | None
    sma_100: float | None
    sma_200: float | None
    ema_12: float | None
    ema_26: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    rsi_14: float | None
    bollinger_upper: float | None
    bollinger_middle: float | None
    bollinger_lower: float | None
    atr_14: float | None
    volume_average_20: float | None
    week_52_high: float | None
    week_52_low: float | None
    swing_high: float | None
    swing_low: float | None
    fibonacci_levels: dict[str, float]
    trend: str
    volatility: str
    momentum: str
    support: float | None
    resistance: float | None
    notes: list[str]


@dataclass(frozen=True)
class PortfolioSymbolContext:
    symbol: str
    is_held: bool
    quantity: float
    average_cost: float | None
    last_price: float | None
    market_value: float
    portfolio_value: float
    portfolio_weight: float
    unrealized_pnl: float | None
    day_pnl: float | None
    cash_available: float = 0.0


@dataclass(frozen=True)
class ScenarioRow:
    scenario: str
    symbol_price: float
    position_pnl: float
    portfolio_pnl_impact: float
    new_portfolio_value: float


@dataclass(frozen=True)
class GeneratedRiskBudget:
    amount: float | None
    base_amount: float | None
    technical_amount: float | None
    portfolio_cap: float | None
    cash_cap: float | None
    factors: tuple[str, ...]


@dataclass(frozen=True)
class GeneratedStockPosition:
    quantity: float
    entry_price: float | None
    stop_price: float | None
    risk_dollars: float | None
    notional: float
    portfolio_weight: float
    per_share_risk: float | None
    basis: str


@dataclass(frozen=True)
class StopTranche:
    label: str
    shares: float
    stop_price: float
    reason: str
    max_loss: float
    portfolio_impact: float | None
    remaining_shares: float


@dataclass(frozen=True)
class StopLadderPlan:
    entry_price: float
    total_shares: float
    single_stop_price: float
    single_stop_risk: float
    single_stop_portfolio_impact: float | None
    laddered_risk: float
    laddered_portfolio_impact: float | None
    savings: float
    savings_percent: float | None
    savings_portfolio_impact: float | None
    savings_label: str
    tradeoff: str
    tranches: tuple[StopTranche, ...]


@dataclass(frozen=True)
class _StopCandidate:
    price: float
    reason: str
    priority: int = 0


@dataclass(frozen=True)
class CurrentModelScenarioRow:
    scenario: str
    symbol_price: float
    current_shares: float
    current_position_pnl: float
    current_portfolio_pnl_impact: float
    current_new_portfolio_value: float
    model_shares: float | None
    model_position_pnl: float | None
    model_portfolio_pnl_impact: float | None
    model_new_portfolio_value: float | None


@dataclass(frozen=True)
class DataSourceStatus:
    source: str
    status: str
    fetched_at: str
    message: str = ""


def calculate_advanced_indicators(symbol: str, candles: list[Candle]) -> AdvancedIndicatorSnapshot:
    clean_symbol = symbol.strip().upper()
    if not candles:
        return AdvancedIndicatorSnapshot(
            symbol=clean_symbol,
            latest_close=None,
            sma_20=None,
            sma_50=None,
            sma_100=None,
            sma_200=None,
            ema_12=None,
            ema_26=None,
            macd=None,
            macd_signal=None,
            macd_histogram=None,
            rsi_14=None,
            bollinger_upper=None,
            bollinger_middle=None,
            bollinger_lower=None,
            atr_14=None,
            volume_average_20=None,
            week_52_high=None,
            week_52_low=None,
            swing_high=None,
            swing_low=None,
            fibonacci_levels={},
            trend="unknown",
            volatility="unknown",
            momentum="unknown",
            support=None,
            resistance=None,
            notes=["Price history unavailable; technical indicators are limited."],
        )

    closes = [candle.close for candle in candles]
    highs = [candle.high for candle in candles]
    lows = [candle.low for candle in candles]
    volumes = [candle.volume for candle in candles]
    latest_close = closes[-1]
    sma_20 = simple_moving_average(closes, 20)
    sma_50 = simple_moving_average(closes, 50)
    sma_100 = simple_moving_average(closes, 100)
    sma_200 = simple_moving_average(closes, 200)
    ema_12 = _last(ema_series(closes, 12))
    ema_26 = _last(ema_series(closes, 26))
    macd_line, signal_line, histogram = macd(closes)
    rsi_14 = rsi(closes, 14)
    bb_middle, bb_upper, bb_lower = bollinger_bands(closes, 20)
    atr_14 = average_true_range(candles, 14)
    volume_average_20 = simple_moving_average(volumes, 20)
    week_window = candles[-252:] if len(candles) >= 252 else candles
    week_52_high = max(candle.high for candle in week_window)
    week_52_low = min(candle.low for candle in week_window)
    recent_window = candles[-60:] if len(candles) >= 60 else candles
    swing_high = max(candle.high for candle in recent_window)
    swing_low = min(candle.low for candle in recent_window)
    support = recent_support(lows, latest_close)
    resistance = recent_resistance(highs, latest_close)
    fibs = fibonacci_retracements(swing_high=swing_high, swing_low=swing_low)

    return AdvancedIndicatorSnapshot(
        symbol=clean_symbol,
        latest_close=latest_close,
        sma_20=sma_20,
        sma_50=sma_50,
        sma_100=sma_100,
        sma_200=sma_200,
        ema_12=ema_12,
        ema_26=ema_26,
        macd=macd_line,
        macd_signal=signal_line,
        macd_histogram=histogram,
        rsi_14=rsi_14,
        bollinger_upper=bb_upper,
        bollinger_middle=bb_middle,
        bollinger_lower=bb_lower,
        atr_14=atr_14,
        volume_average_20=volume_average_20,
        week_52_high=week_52_high,
        week_52_low=week_52_low,
        swing_high=swing_high,
        swing_low=swing_low,
        fibonacci_levels=fibs,
        trend=classify_advanced_trend(latest_close, sma_20, sma_50, sma_200),
        volatility=classify_volatility(latest_close, atr_14),
        momentum=classify_momentum(rsi_14, histogram),
        support=support,
        resistance=resistance,
        notes=indicator_notes(latest_close, sma_20, sma_50, sma_200, rsi_14, atr_14),
    )


def build_portfolio_symbol_context(portfolio: Portfolio, symbol: str, fallback_price: float | None = None) -> PortfolioSymbolContext:
    clean_symbol = symbol.strip().upper()
    position = portfolio.get_position(clean_symbol)
    portfolio_value = max(portfolio.total_value, 0.01)
    if position is None:
        last_price = fallback_price
        return PortfolioSymbolContext(
            symbol=clean_symbol,
            is_held=False,
            quantity=0.0,
            average_cost=None,
            last_price=last_price,
            market_value=0.0,
            portfolio_value=portfolio.total_value,
            portfolio_weight=0.0,
            unrealized_pnl=None,
            day_pnl=None,
            cash_available=portfolio.cash,
        )
    last_price = fallback_price if fallback_price is not None else position.last_price
    market_value = position.quantity * last_price
    return PortfolioSymbolContext(
        symbol=clean_symbol,
        is_held=True,
        quantity=position.quantity,
        average_cost=position.average_cost,
        last_price=last_price,
        market_value=market_value,
        portfolio_value=portfolio.total_value,
        portfolio_weight=market_value / portfolio_value,
        unrealized_pnl=position.unrealized_profit_loss,
        day_pnl=position.day_profit_loss,
        cash_available=portfolio.cash,
    )


def build_scenario_rows(context: PortfolioSymbolContext, moves: tuple[float, ...] = (-0.10, -0.05, -0.02, 0.02, 0.05, 0.10)) -> list[ScenarioRow]:
    if context.last_price is None or context.quantity == 0:
        base_price = context.last_price or 0.0
        return [ScenarioRow(f"{move:+.0%}", base_price * (1 + move), 0.0, 0.0, context.portfolio_value) for move in moves]
    rows: list[ScenarioRow] = []
    for move in moves:
        price = context.last_price * (1 + move)
        pnl = (price - context.last_price) * context.quantity
        rows.append(
            ScenarioRow(
                scenario=f"{move:+.0%}",
                symbol_price=price,
                position_pnl=pnl,
                portfolio_pnl_impact=pnl / max(context.portfolio_value, 0.01),
                new_portfolio_value=context.portfolio_value + pnl,
            )
        )
    return rows


def build_planned_stock_context(
    context: PortfolioSymbolContext,
    indicators: AdvancedIndicatorSnapshot,
    risk_budget: GeneratedRiskBudget,
) -> tuple[PortfolioSymbolContext, GeneratedStockPosition]:
    """Return an independent generated stock scenario position.

    The input context remains the source of actual held exposure for callers
    that need current-vs-model comparisons.
    """
    entry = context.last_price or indicators.latest_close
    if entry is None or entry <= 0 or risk_budget.amount is None or risk_budget.amount <= 0:
        planned = _planned_stock_context(context, quantity=0.0, entry_price=entry, notional=0.0)
        basis = _stock_plan_basis(
            context,
            model_quantity=0.0,
            reason="Insufficient price or risk budget.",
        )
        return planned, GeneratedStockPosition(0.0, entry, None, risk_budget.amount, 0.0, 0.0, None, basis)

    stop = _scenario_stop_price(context, indicators)
    if stop is None or stop <= 0 or stop >= entry:
        stop = entry * 0.97
    per_share_risk = max(entry - stop, 0.01)
    max_notional = min(
        max(context.cash_available, 0.0) * 0.06 if context.cash_available > 0 else entry,
        max(context.portfolio_value, 0.0) * 0.06,
        risk_budget.amount * 45.0,
    )
    risk_sized = risk_budget.amount / per_share_risk
    notional_sized = max_notional / entry if entry else 0.0
    cash_sized = max(context.cash_available, 0.0) / entry if entry else 0.0
    quantity = math.floor(max(0.0, min(risk_sized, notional_sized, cash_sized)))
    notional = quantity * entry
    planned = _planned_stock_context(context, quantity=quantity, entry_price=entry, notional=notional)
    basis = _stock_plan_basis(
        context,
        model_quantity=quantity,
        risk_amount=risk_budget.amount,
        per_share_risk=per_share_risk,
        stop_price=stop,
        risk_sized=risk_sized,
        notional_sized=notional_sized,
        cash_sized=cash_sized,
        max_notional=max_notional,
    )
    position = GeneratedStockPosition(
        quantity=quantity,
        entry_price=entry,
        stop_price=stop,
        risk_dollars=risk_budget.amount,
        notional=notional,
        portfolio_weight=notional / max(context.portfolio_value, 0.01),
        per_share_risk=per_share_risk,
        basis=basis,
    )
    return planned, position


def _planned_stock_context(
    context: PortfolioSymbolContext,
    *,
    quantity: float,
    entry_price: float | None,
    notional: float,
) -> PortfolioSymbolContext:
    return PortfolioSymbolContext(
        symbol=context.symbol,
        is_held=bool(context.is_held and quantity > 0),
        quantity=quantity,
        average_cost=entry_price if quantity else None,
        last_price=entry_price,
        market_value=notional,
        portfolio_value=context.portfolio_value,
        portfolio_weight=notional / max(context.portfolio_value, 0.01),
        unrealized_pnl=None,
        day_pnl=None,
        cash_available=context.cash_available,
    )


def _stock_plan_basis(
    context: PortfolioSymbolContext,
    *,
    model_quantity: float,
    reason: str | None = None,
    risk_amount: float | None = None,
    per_share_risk: float | None = None,
    stop_price: float | None = None,
    risk_sized: float | None = None,
    notional_sized: float | None = None,
    cash_sized: float | None = None,
    max_notional: float | None = None,
) -> str:
    prefix = f"Current actual shares: {_plain_shares(context.quantity)}; model target shares: {_plain_shares(model_quantity)}."
    if reason:
        return f"{prefix} {reason}"
    parts = [
        prefix,
        (
            f"Risk-sized from {_plain_money(risk_amount)} budget / {_plain_money(per_share_risk)} "
            f"per-share risk to {_plain_money(stop_price)}"
        ),
    ]
    if risk_sized is not None:
        parts[-1] += f" = {risk_sized:,.2f} shares before caps."
    else:
        parts[-1] += "."
    parts.append(
        f"Caps: {_plain_money(max_notional)} max notional ({_plain_number(notional_sized)} shares) "
        f"and cash capacity {_plain_number(cash_sized)} shares."
    )
    return " ".join(parts)


def build_current_model_scenario_rows(
    current_context: PortfolioSymbolContext,
    model_position: GeneratedStockPosition | None,
    moves: tuple[float, ...] = (-0.10, -0.05, -0.02, 0.02, 0.05, 0.10),
) -> list[CurrentModelScenarioRow]:
    base_price = current_context.last_price or (model_position.entry_price if model_position else None) or 0.0
    model_quantity = float(model_position.quantity) if model_position is not None and model_position.quantity > 0 else None
    model_entry = model_position.entry_price if model_position is not None else None
    has_model = model_quantity is not None and model_entry is not None and model_entry > 0
    rows: list[CurrentModelScenarioRow] = []
    for move in moves:
        price = base_price * (1 + move)
        current_pnl = (price - base_price) * current_context.quantity if current_context.quantity else 0.0
        model_pnl = (price - model_entry) * model_quantity if has_model else None
        rows.append(
            CurrentModelScenarioRow(
                scenario=f"{move:+.0%}",
                symbol_price=price,
                current_shares=current_context.quantity,
                current_position_pnl=current_pnl,
                current_portfolio_pnl_impact=current_pnl / max(current_context.portfolio_value, 0.01),
                current_new_portfolio_value=current_context.portfolio_value + current_pnl,
                model_shares=model_quantity if has_model else None,
                model_position_pnl=model_pnl,
                model_portfolio_pnl_impact=(
                    model_pnl / max(current_context.portfolio_value, 0.01)
                    if model_pnl is not None
                    else None
                ),
                model_new_portfolio_value=(
                    current_context.portfolio_value + model_pnl
                    if model_pnl is not None
                    else None
                ),
            )
        )
    return rows


def build_stop_ladder_plan(
    model_position: GeneratedStockPosition | None,
    indicators: AdvancedIndicatorSnapshot | None = None,
    *,
    portfolio_value: float | None = None,
    technical_report: Any | None = None,
) -> StopLadderPlan | None:
    """Build a planning-only scaled stop ladder for a generated stock plan."""
    if model_position is None:
        return None
    total_shares = math.floor(float(model_position.quantity or 0.0))
    if total_shares < 2:
        return None
    entry = _positive_float(model_position.entry_price)
    single_stop = _positive_float(model_position.stop_price)
    if entry is None or single_stop is None or single_stop >= entry:
        return None

    tactical = _select_tactical_stop(entry, single_stop, indicators, technical_report)
    if tactical is None:
        return None
    specs = _stop_ladder_specs(total_shares, entry, single_stop, tactical, indicators, technical_report)
    if len(specs) < 2:
        return None

    remaining = float(total_shares)
    tranches: list[StopTranche] = []
    for label, shares, candidate in specs:
        if shares <= 0:
            continue
        remaining = max(remaining - shares, 0.0)
        max_loss = max(entry - candidate.price, 0.0) * shares
        tranches.append(
            StopTranche(
                label=label,
                shares=float(shares),
                stop_price=candidate.price,
                reason=candidate.reason,
                max_loss=max_loss,
                portfolio_impact=_loss_portfolio_impact(max_loss, portfolio_value),
                remaining_shares=remaining,
            )
        )
    if not tranches:
        return None

    single_stop_risk = max(entry - single_stop, 0.0) * total_shares
    laddered_risk = sum(tranche.max_loss for tranche in tranches)
    savings = max(single_stop_risk - laddered_risk, 0.0)
    savings_percent = savings / single_stop_risk if single_stop_risk > 0 else None
    savings_label = _stop_ladder_savings_label(savings, single_stop_risk)
    tradeoff = _stop_ladder_tradeoff(savings_label, savings, tranches, total_shares)
    return StopLadderPlan(
        entry_price=entry,
        total_shares=float(total_shares),
        single_stop_price=single_stop,
        single_stop_risk=single_stop_risk,
        single_stop_portfolio_impact=_loss_portfolio_impact(single_stop_risk, portfolio_value),
        laddered_risk=laddered_risk,
        laddered_portfolio_impact=_loss_portfolio_impact(laddered_risk, portfolio_value),
        savings=savings,
        savings_percent=savings_percent,
        savings_portfolio_impact=(savings / portfolio_value if portfolio_value and portfolio_value > 0 else None),
        savings_label=savings_label,
        tradeoff=tradeoff,
        tranches=tuple(tranches),
    )


def _stop_ladder_specs(
    total_shares: int,
    entry: float,
    thesis_stop: float,
    tactical: _StopCandidate,
    indicators: AdvancedIndicatorSnapshot | None,
    technical_report: Any | None,
) -> list[tuple[str, int, _StopCandidate]]:
    if total_shares == 2:
        return [
            ("Tactical warning stop", 1, tactical),
            ("Thesis invalidation stop", 1, _StopCandidate(thesis_stop, _thesis_stop_reason(thesis_stop, indicators, technical_report))),
        ]

    tactical_shares = max(1, total_shares // 3)
    intermediate_shares = max(1, total_shares // 3)
    thesis_shares = total_shares - tactical_shares - intermediate_shares
    if thesis_shares <= 0:
        thesis_shares = 1
        intermediate_shares = max(0, total_shares - tactical_shares - thesis_shares)
    intermediate = _select_intermediate_stop(tactical.price, thesis_stop, indicators, technical_report)
    specs = [("Tactical warning stop", tactical_shares, tactical)]
    if intermediate_shares > 0 and intermediate is not None:
        specs.append(("Intermediate de-risk stop", intermediate_shares, intermediate))
    else:
        thesis_shares += intermediate_shares
    specs.append(("Thesis invalidation stop", thesis_shares, _StopCandidate(thesis_stop, _thesis_stop_reason(thesis_stop, indicators, technical_report))))
    return specs


def _select_tactical_stop(
    entry: float,
    thesis_stop: float,
    indicators: AdvancedIndicatorSnapshot | None,
    technical_report: Any | None,
) -> _StopCandidate | None:
    distance = entry - thesis_stop
    if distance <= 0:
        return None
    min_gap = _stop_ladder_min_gap(entry, thesis_stop)
    lower = thesis_stop + min_gap
    upper = entry - min_gap
    if lower >= upper:
        return None
    desired = entry - (distance * 0.55)
    candidates = [
        candidate
        for candidate in _tactical_stop_candidates(entry, thesis_stop, indicators, technical_report)
        if lower <= candidate.price <= upper
    ]
    if candidates:
        return min(candidates, key=lambda candidate: (candidate.priority, abs(candidate.price - desired)))
    fallback_price = min(max(desired, lower), upper)
    return _StopCandidate(fallback_price, "ATR-fraction fallback for an early partial risk cut.", priority=2)


def _select_intermediate_stop(
    tactical_stop: float,
    thesis_stop: float,
    indicators: AdvancedIndicatorSnapshot | None,
    technical_report: Any | None,
) -> _StopCandidate | None:
    if tactical_stop <= thesis_stop:
        return None
    min_gap = max((tactical_stop - thesis_stop) * 0.08, 0.01)
    lower = thesis_stop + min_gap
    upper = tactical_stop - min_gap
    if lower >= upper:
        return None
    desired = thesis_stop + ((tactical_stop - thesis_stop) * 0.50)
    candidates = [
        candidate
        for candidate in _technical_stop_candidates(indicators, technical_report, priority=0)
        if lower <= candidate.price <= upper
    ]
    if candidates:
        selected = min(candidates, key=lambda candidate: abs(candidate.price - desired))
        return _StopCandidate(selected.price, f"Intermediate tranche near {selected.reason.lower()}", selected.priority)
    return _StopCandidate(desired, "Middle tranche between tactical warning and thesis invalidation.", priority=2)


def _tactical_stop_candidates(
    entry: float,
    thesis_stop: float,
    indicators: AdvancedIndicatorSnapshot | None,
    technical_report: Any | None,
) -> list[_StopCandidate]:
    candidates = _technical_stop_candidates(indicators, technical_report, priority=0)
    if indicators is not None and indicators.atr_14 and indicators.atr_14 > 0:
        candidates.append(_StopCandidate(entry - abs(indicators.atr_14) * 0.50, "ATR fraction warning level.", priority=1))
        candidates.append(_StopCandidate(entry - abs(indicators.atr_14) * 0.65, "ATR fraction warning level.", priority=1))
    distance = entry - thesis_stop
    if distance > 0:
        candidates.append(_StopCandidate(entry - distance * 0.55, "Distance-to-thesis warning level.", priority=1))
    return _dedupe_stop_candidates(candidates)


def _technical_stop_candidates(
    indicators: AdvancedIndicatorSnapshot | None,
    technical_report: Any | None,
    *,
    priority: int,
) -> list[_StopCandidate]:
    candidates: list[_StopCandidate] = []
    if indicators is not None:
        _add_stop_candidate(candidates, indicators.support, "Nearby support.", priority)
        _add_stop_candidate(candidates, indicators.swing_low, "Prior low.", priority)
        _add_stop_candidate(candidates, indicators.bollinger_lower, "Lower volatility band.", priority)

    classification = getattr(technical_report, "setup_classification", None)
    _add_stop_candidate(candidates, getattr(classification, "confirmation_level", None), "Reclaimed trigger.", priority)
    _add_stop_candidate(candidates, getattr(classification, "invalidation_level", None), "Technical invalidation level.", priority)
    level_proximity = getattr(classification, "level_proximity", None)
    support = getattr(level_proximity, "nearest_support", None)
    _add_stop_candidate(candidates, getattr(support, "high", None), "Nearby support zone.", priority)
    _add_stop_candidate(candidates, getattr(support, "center", None), "Nearby support zone.", priority)

    for trigger in _iter_values(getattr(technical_report, "key_triggers", None)):
        label = str(getattr(trigger, "label", "") or "").lower()
        reason = "Risk-warning level."
        if "pullback" in label:
            reason = "Pullback support zone."
        elif "breakout" in label:
            reason = "Reclaimed trigger."
        elif "invalidation" in label:
            reason = "Technical invalidation level."
        _add_stop_candidate(candidates, getattr(trigger, "price", None), reason, priority)

    for snapshot in _iter_values(getattr(technical_report, "snapshots", None)):
        _add_stop_candidate(candidates, getattr(snapshot, "vwap", None), "VWAP.", priority)
        _add_stop_candidate(candidates, getattr(snapshot, "session_vwap", None), "Session VWAP.", priority)
        _add_stop_candidate(candidates, getattr(snapshot, "rolling_vwap_20", None), "Rolling VWAP.", priority)
        _add_stop_candidate(candidates, getattr(snapshot, "multi_day_vwap", None), "Multi-day VWAP.", priority)
        _add_stop_candidate(candidates, getattr(snapshot, "recent_low", None), "Prior low.", priority)
        for level in _iter_values(getattr(snapshot, "support_zones", None)):
            _add_stop_candidate(candidates, getattr(level, "high", None), "Nearby support zone.", priority)
            _add_stop_candidate(candidates, getattr(level, "center", None), "Nearby support zone.", priority)

    for prc in _iter_values(getattr(technical_report, "prc_indexes", None)):
        _add_stop_candidate(candidates, getattr(prc, "index_price", None), "Risk-warning level.", priority)
    capital_read = getattr(technical_report, "capital_structure_indicator", None)
    _add_stop_candidate(candidates, getattr(capital_read, "nearest_supply_level", None), "Risk-warning level.", priority)
    return _dedupe_stop_candidates(candidates)


def _add_stop_candidate(candidates: list[_StopCandidate], value: Any, reason: str, priority: int) -> None:
    price = _positive_float(value)
    if price is not None:
        candidates.append(_StopCandidate(price, reason, priority))


def _dedupe_stop_candidates(candidates: list[_StopCandidate]) -> list[_StopCandidate]:
    selected: dict[float, _StopCandidate] = {}
    for candidate in candidates:
        key = round(candidate.price, 4)
        current = selected.get(key)
        if current is None or candidate.priority < current.priority:
            selected[key] = candidate
    return list(selected.values())


def _iter_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, dict):
        return list(value.values())
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _stop_ladder_min_gap(entry: float, thesis_stop: float) -> float:
    distance = max(entry - thesis_stop, 0.0)
    return max(distance * 0.05, entry * 0.001, 0.01)


def _loss_portfolio_impact(loss: float, portfolio_value: float | None) -> float | None:
    if portfolio_value is None or portfolio_value <= 0:
        return None
    return -loss / portfolio_value


def _thesis_stop_reason(
    thesis_stop: float,
    indicators: AdvancedIndicatorSnapshot | None,
    technical_report: Any | None,
) -> str:
    classification = getattr(technical_report, "setup_classification", None)
    invalidation = _positive_float(getattr(classification, "invalidation_level", None))
    if invalidation is not None and abs(invalidation - thesis_stop) <= max(thesis_stop * 0.005, 0.05):
        return "Existing model stop near technical invalidation."
    if indicators is not None:
        for level, reason in (
            (indicators.support, "Existing model stop near support/risk line."),
            (indicators.swing_low, "Existing model stop near prior low/invalidation."),
            (indicators.bollinger_lower, "Existing model stop near lower volatility band."),
        ):
            price = _positive_float(level)
            if price is not None and abs(price - thesis_stop) <= max(thesis_stop * 0.005, 0.05):
                return reason
    return "Existing model stop / thesis invalidation."


def _stop_ladder_savings_label(savings: float, single_stop_risk: float) -> str:
    if savings <= 0.01:
        return "No savings"
    threshold = max(5.0, single_stop_risk * 0.05)
    if savings < threshold:
        return "Negligible savings"
    return "Meaningful savings"


def _stop_ladder_tradeoff(
    savings_label: str,
    savings: float,
    tranches: list[StopTranche],
    total_shares: int,
) -> str:
    first_remaining = tranches[0].remaining_shares if tranches else float(total_shares)
    if savings_label == "Meaningful savings":
        return (
            f"Ladder saves {_plain_money(savings)} versus one stop, while "
            f"{_plain_shares(first_remaining)} remain for the deeper thesis stop."
        )
    if savings_label == "Negligible savings":
        return (
            f"Savings are negligible ({_plain_money(savings)}) before fees/slippage; "
            "the main tradeoff is an earlier partial exit versus rebound exposure."
        )
    return "The ladder does not reduce max loss versus the one-stop plan; treat it only as a behavior-planning view."


def technical_scenario_moves(context: PortfolioSymbolContext, indicators: AdvancedIndicatorSnapshot) -> tuple[float, ...]:
    """Build scenario moves from current technical levels instead of arbitrary user input."""
    last_price = context.last_price or indicators.latest_close
    if last_price is None or last_price <= 0:
        return (-0.10, -0.05, -0.02, 0.02, 0.05, 0.10)

    moves: set[float] = set()

    atr_move = _move_from_distance(indicators.atr_14, last_price)
    if atr_move is not None:
        moves.update({-atr_move, atr_move, -min(atr_move * 2, 0.25), min(atr_move * 2, 0.25)})

    for level in (
        indicators.support,
        indicators.resistance,
        indicators.bollinger_lower,
        indicators.bollinger_upper,
        indicators.swing_low,
        indicators.swing_high,
    ):
        move = distance_to_price(last_price, level)
        if move is not None and 0.005 <= abs(move) <= 0.35:
            moves.add(_round_move(move))

    negative = sorted(move for move in moves if move < 0)
    positive = sorted(move for move in moves if move > 0)
    if not negative:
        negative = [-0.05, -0.10]
    if not positive:
        positive = [0.05, 0.10]

    selected = negative[:3] + positive[:3]
    selected.extend([-0.10, 0.10])
    return tuple(sorted(set(_round_move(move) for move in selected if 0.005 <= abs(move) <= 0.35)))


def _scenario_stop_price(context: PortfolioSymbolContext, indicators: AdvancedIndicatorSnapshot) -> float | None:
    entry = context.last_price or indicators.latest_close
    if entry is None or entry <= 0:
        return None
    candidates: list[float] = []
    for level in (indicators.support, indicators.swing_low, indicators.bollinger_lower):
        if level is not None and 0 < level < entry:
            candidates.append(level)
    if indicators.atr_14 and indicators.atr_14 > 0:
        candidates.append(entry - abs(indicators.atr_14))
    if not candidates:
        candidates.append(entry * 0.97)
    stop = max(value for value in candidates if value > 0)
    minimum_distance = max(entry * 0.01, abs(indicators.atr_14 or 0.0) * 0.75, 0.01)
    if entry - stop < minimum_distance:
        stop = max(entry - minimum_distance, entry * 0.90)
    return stop


def _plain_money(value: float | None) -> str:
    if value is None:
        return "--"
    prefix = "-$" if value < 0 else "$"
    return f"{prefix}{abs(value):,.2f}"


def _plain_shares(value: float | None) -> str:
    if value is None:
        return "-- shares"
    return f"{float(value):g} shares"


def _plain_number(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{float(value):,.2f}"


def technical_scenario_basis(context: PortfolioSymbolContext, indicators: AdvancedIndicatorSnapshot) -> str:
    last_price = context.last_price or indicators.latest_close
    if last_price is None or last_price <= 0:
        return "Fallback moves: price history is unavailable."
    parts: list[str] = []
    if indicators.atr_14:
        parts.append(f"ATR ${indicators.atr_14:,.2f} ({abs(indicators.atr_14 / last_price):.1%})")
    if indicators.support:
        parts.append(f"support ${indicators.support:,.2f} ({distance_to_price(last_price, indicators.support):+.1%})")
    if indicators.resistance:
        parts.append(f"resistance ${indicators.resistance:,.2f} ({distance_to_price(last_price, indicators.resistance):+.1%})")
    if not parts:
        return "Technical fallback moves: no clear ATR/support/resistance levels were available."
    return "Scenario moves are generated from " + ", ".join(parts[:3]) + "."


def recommended_risk_budget(
    context: PortfolioSymbolContext,
    indicators: AdvancedIndicatorSnapshot,
    requested_cap: float | None = None,
) -> float | None:
    """Compatibility wrapper for older call sites.

    The generated risk budget is portfolio-derived. ``requested_cap`` is
    retained so older UI/tests can pass it, but it is only a final safety clamp
    and is not treated as the source of truth.
    """
    budget = generated_risk_budget(context, indicators)
    if budget.amount is None:
        return None
    if requested_cap is not None and requested_cap > 0:
        return min(budget.amount, requested_cap)
    return budget.amount


def generated_risk_budget(
    context: PortfolioSymbolContext,
    indicators: AdvancedIndicatorSnapshot,
    *,
    macro_label: str = "Mixed",
    risk_level_label: str = "Medium",
    action_bias_label: str = "",
    earnings_text: str = "",
    fundamentals_text: str = "",
) -> GeneratedRiskBudget:
    total_value = max(context.portfolio_value, 0.0)
    if total_value <= 0:
        return GeneratedRiskBudget(None, None, None, None, None, ("Portfolio value unavailable.",))

    last_price = context.last_price or indicators.latest_close
    portfolio_cap = total_value * (0.006 if context.is_held else 0.004)
    cash = max(context.cash_available, 0.0)
    cash_cap = cash * (0.025 if context.is_held else 0.035) if cash > 0 else None
    technical_budget = _technical_budget_from_position(context, indicators, last_price)

    candidates = [portfolio_cap]
    if cash_cap is not None:
        candidates.append(cash_cap)
    if technical_budget is not None:
        candidates.append(technical_budget)
    base_amount = min(value for value in candidates if value > 0)

    factor_rows = _risk_budget_factors(
        context,
        indicators,
        macro_label=macro_label,
        risk_level_label=risk_level_label,
        action_bias_label=action_bias_label,
        earnings_text=earnings_text,
        fundamentals_text=fundamentals_text,
    )
    multiplier = 1.0
    factor_notes: list[str] = []
    for factor, note in factor_rows:
        multiplier *= factor
        factor_notes.append(note)

    raw_amount = base_amount * multiplier
    hard_cap = total_value * 0.01
    if cash_cap is not None:
        hard_cap = min(hard_cap, cash * 0.05)
    if context.is_held and technical_budget is not None:
        hard_cap = min(hard_cap, max(technical_budget, 25.0))
    amount = min(raw_amount, hard_cap)
    if amount > 0:
        amount = max(min(_round_budget(amount), hard_cap), 0.0)
    return GeneratedRiskBudget(
        amount=amount,
        base_amount=base_amount,
        technical_amount=technical_budget,
        portfolio_cap=portfolio_cap,
        cash_cap=cash_cap,
        factors=tuple(factor_notes),
    )


def _technical_budget_from_position(
    context: PortfolioSymbolContext,
    indicators: AdvancedIndicatorSnapshot,
    last_price: float | None,
) -> float | None:
    if context.is_held and context.quantity and last_price:
        risk_level = indicators.support if indicators.support and indicators.support < last_price else None
        if risk_level is None and indicators.atr_14:
            risk_level = last_price - abs(indicators.atr_14)
        if risk_level is not None and risk_level > 0:
            return abs(last_price - risk_level) * abs(context.quantity)
        if indicators.atr_14:
            return abs(indicators.atr_14) * abs(context.quantity)
        return abs(context.market_value) * 0.03 if context.market_value else None
    return None


def _risk_budget_factors(
    context: PortfolioSymbolContext,
    indicators: AdvancedIndicatorSnapshot,
    *,
    macro_label: str,
    risk_level_label: str,
    action_bias_label: str,
    earnings_text: str,
    fundamentals_text: str,
) -> list[tuple[float, str]]:
    factors = [
        _cash_factor(context),
        _exposure_factor(context),
        _trend_factor(indicators.trend),
        _momentum_factor(indicators.momentum),
        _volatility_factor(indicators.volatility),
        _macro_factor(macro_label),
        _risk_level_factor(risk_level_label),
        _action_bias_factor(action_bias_label),
        _event_risk_factor(earnings_text, fundamentals_text),
        _pnl_factor(context),
    ]
    return [item for item in factors if item is not None]


def _cash_factor(context: PortfolioSymbolContext) -> tuple[float, str]:
    total = max(context.portfolio_value, 0.01)
    cash_ratio = max(context.cash_available, 0.0) / total
    if cash_ratio >= 0.40:
        return 1.10, f"cash/liquidity strong ({cash_ratio:.1%}) x1.10"
    if cash_ratio < 0.05:
        return 0.55, f"cash/liquidity tight ({cash_ratio:.1%}) x0.55"
    if cash_ratio < 0.15:
        return 0.80, f"cash/liquidity modest ({cash_ratio:.1%}) x0.80"
    return 1.00, f"cash/liquidity normal ({cash_ratio:.1%}) x1.00"


def _exposure_factor(context: PortfolioSymbolContext) -> tuple[float, str]:
    weight = abs(context.portfolio_weight)
    if weight >= 0.15:
        return 0.25, f"position concentration high ({weight:.1%}) x0.25"
    if weight >= 0.10:
        return 0.45, f"position concentration elevated ({weight:.1%}) x0.45"
    if weight >= 0.05:
        return 0.70, f"position concentration watched ({weight:.1%}) x0.70"
    if context.is_held:
        return 0.90, f"existing position modest ({weight:.1%}) x0.90"
    return 1.00, "not currently held x1.00"


def _trend_factor(trend: str) -> tuple[float, str]:
    lower = trend.lower()
    if lower == "bullish":
        return 1.10, "technical trend bullish x1.10"
    if lower == "bearish":
        return 0.55, "technical trend bearish x0.55"
    if lower == "sideways":
        return 0.85, "technical trend sideways x0.85"
    return 0.75, "technical trend unknown x0.75"


def _momentum_factor(momentum: str) -> tuple[float, str]:
    lower = momentum.lower()
    if lower == "improving":
        return 1.05, "momentum improving x1.05"
    if lower == "weakening":
        return 0.75, "momentum weakening x0.75"
    if lower == "neutral":
        return 0.95, "momentum neutral x0.95"
    return 0.85, "momentum unknown x0.85"


def _volatility_factor(volatility: str) -> tuple[float, str]:
    lower = volatility.lower()
    if lower == "elevated":
        return 0.65, "volatility elevated x0.65"
    if lower == "low":
        return 1.05, "volatility low x1.05"
    if lower == "normal":
        return 1.00, "volatility normal x1.00"
    return 0.85, "volatility unknown x0.85"


def _macro_factor(label: str) -> tuple[float, str]:
    lower = label.lower()
    if "headwind" in lower or "hot" in lower:
        return 0.60, f"macro {label.lower()} x0.60"
    if "tailwind" in lower or "cool" in lower:
        return 1.08, f"macro {label.lower()} x1.08"
    if "neutral" in lower:
        return 0.95, f"macro {label.lower()} x0.95"
    return 0.80, f"macro {label.lower() or 'mixed'} x0.80"


def _risk_level_factor(label: str) -> tuple[float, str]:
    lower = label.lower()
    if "hot" in lower or "high" in lower:
        return 0.60, f"risk level {label.lower()} x0.60"
    if "medium" in lower:
        return 0.85, f"risk level {label.lower()} x0.85"
    if "low" in lower:
        return 1.05, f"risk level {label.lower()} x1.05"
    return 0.90, f"risk level {label.lower() or 'unknown'} x0.90"


def _action_bias_factor(label: str) -> tuple[float, str]:
    lower = label.lower()
    if "avoid" in lower:
        return 0.25, f"action bias {label.lower()} x0.25"
    if "trim" in lower or "reduce" in lower:
        return 0.45, f"action bias {label.lower()} x0.45"
    if "watch" in lower or "wait" in lower:
        return 0.65, f"action bias {label.lower()} x0.65"
    if "add" in lower or "bullish" in lower or "buy" in lower:
        return 1.05, f"action bias {label.lower()} x1.05"
    return 0.90, f"action bias {label.lower() or 'mixed'} x0.90"


def _event_risk_factor(earnings_text: str, fundamentals_text: str) -> tuple[float, str]:
    lower = f"{earnings_text}\n{fundamentals_text}".lower()
    if any(term in lower for term in ("upcoming earnings", "earnings soon", "event risk", "guidance withdrawn", "going concern")):
        return 0.60, "earnings/news event risk flagged x0.60"
    if "unavailable" in lower:
        return 0.85, "earnings/news incomplete x0.85"
    return 1.00, "earnings/news no major event flag x1.00"


def _pnl_factor(context: PortfolioSymbolContext) -> tuple[float, str]:
    if not context.is_held:
        return 1.00, "no existing unrealized P&L drag x1.00"
    market_value = max(abs(context.market_value), 0.01)
    unrealized_ratio = (context.unrealized_pnl or 0.0) / market_value
    day_ratio = (context.day_pnl or 0.0) / market_value
    if unrealized_ratio <= -0.08 or day_ratio <= -0.03:
        return 0.65, "position P&L under pressure x0.65"
    if unrealized_ratio >= 0.15:
        return 0.90, "large unrealized gain favors discipline x0.90"
    return 1.00, "position P&L normal x1.00"


def _round_budget(amount: float) -> float:
    if amount >= 250:
        return round(amount / 25.0) * 25.0
    if amount >= 50:
        return round(amount / 10.0) * 10.0
    return round(amount, 2)


def _move_from_distance(distance: float | None, price: float) -> float | None:
    if distance is None or price <= 0:
        return None
    move = abs(distance / price)
    if move < 0.005:
        return None
    return _round_move(min(move, 0.35))


def _round_move(move: float) -> float:
    return round(float(move), 4)


def suggested_position_size(*, entry_price: float | None, stop_price: float | None, max_risk_dollars: float | None) -> float | None:
    if entry_price is None or stop_price is None or max_risk_dollars is None:
        return None
    per_share_risk = abs(entry_price - stop_price)
    if per_share_risk <= 0:
        return None
    return max_risk_dollars / per_share_risk


def distance_to_price(last_price: float | None, level: float | None) -> float | None:
    if last_price is None or level is None or last_price == 0:
        return None
    return (level - last_price) / last_price


def bollinger_bands(values: list[float], period: int = 20, deviations: float = 2.0) -> tuple[float | None, float | None, float | None]:
    if len(values) < period:
        return None, None, None
    window = values[-period:]
    middle = sum(window) / period
    variance = sum((value - middle) ** 2 for value in window) / period
    stdev = math.sqrt(variance)
    return middle, middle + deviations * stdev, middle - deviations * stdev


def average_true_range(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    true_ranges: list[float] = []
    for index in range(1, len(candles)):
        candle = candles[index]
        previous_close = candles[index - 1].close
        true_ranges.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
    if len(true_ranges) < period:
        return None
    return sum(true_ranges[-period:]) / period


def recent_support(lows: list[float], last_price: float, lookback: int = 60) -> float | None:
    candidates = [value for value in lows[-lookback:] if value <= last_price]
    return max(candidates) if candidates else (min(lows[-lookback:]) if lows else None)


def recent_resistance(highs: list[float], last_price: float, lookback: int = 60) -> float | None:
    candidates = [value for value in highs[-lookback:] if value >= last_price]
    return min(candidates) if candidates else (max(highs[-lookback:]) if highs else None)


def fibonacci_retracements(*, swing_high: float | None, swing_low: float | None) -> dict[str, float]:
    if swing_high is None or swing_low is None or swing_high <= swing_low:
        return {}
    spread = swing_high - swing_low
    return {
        "23.6%": swing_high - spread * 0.236,
        "38.2%": swing_high - spread * 0.382,
        "50.0%": swing_high - spread * 0.500,
        "61.8%": swing_high - spread * 0.618,
        "78.6%": swing_high - spread * 0.786,
    }


def classify_advanced_trend(last_price: float, sma_20: float | None, sma_50: float | None, sma_200: float | None) -> str:
    if sma_20 is None or sma_50 is None:
        return "unknown"
    if sma_200 is not None and last_price > sma_20 > sma_50 > sma_200:
        return "bullish"
    if sma_200 is not None and last_price < sma_20 < sma_50 < sma_200:
        return "bearish"
    if last_price > sma_20 > sma_50:
        return "bullish"
    if last_price < sma_20 < sma_50:
        return "bearish"
    return "sideways"


def classify_volatility(last_price: float, atr_14: float | None) -> str:
    if atr_14 is None or last_price <= 0:
        return "unknown"
    atr_percent = atr_14 / last_price
    if atr_percent >= 0.045:
        return "elevated"
    if atr_percent <= 0.018:
        return "low"
    return "normal"


def classify_momentum(rsi_14: float | None, macd_histogram: float | None) -> str:
    if rsi_14 is None or macd_histogram is None:
        return "unknown"
    if rsi_14 >= 55 and macd_histogram > 0:
        return "improving"
    if rsi_14 <= 45 and macd_histogram < 0:
        return "weakening"
    return "neutral"


def indicator_notes(
    last_price: float,
    sma_20: float | None,
    sma_50: float | None,
    sma_200: float | None,
    rsi_14: float | None,
    atr_14: float | None,
) -> list[str]:
    notes: list[str] = []
    if sma_20 is not None:
        notes.append(f"Last price is {last_price / sma_20 - 1:+.1%} versus SMA 20.")
    if sma_50 is not None:
        notes.append(f"Last price is {last_price / sma_50 - 1:+.1%} versus SMA 50.")
    if sma_200 is not None:
        notes.append(f"Last price is {last_price / sma_200 - 1:+.1%} versus SMA 200.")
    if rsi_14 is not None:
        notes.append(f"RSI 14 is {rsi_14:.1f}.")
    if atr_14 is not None and last_price:
        notes.append(f"ATR 14 is {atr_14:.2f}, about {atr_14 / last_price:.1%} of price.")
    return notes or ["Not enough history for a full indicator stack."]


def load_cached_price_history(symbol: str, *, max_age_seconds: int = PRICE_HISTORY_CACHE_TTL_SECONDS) -> dict[str, Any] | None:
    cache = _read_history_cache()
    entry = cache.get(symbol.strip().upper())
    if not isinstance(entry, dict):
        return None
    fetched_at = float(entry.get("fetched_at_epoch") or 0)
    if time.time() - fetched_at > max_age_seconds:
        return None
    payload = entry.get("payload")
    return payload if isinstance(payload, dict) else None


def save_cached_price_history(symbol: str, payload: dict[str, Any]) -> None:
    cache = _read_history_cache()
    cache[symbol.strip().upper()] = {"fetched_at_epoch": time.time(), "payload": payload}
    PRICE_HISTORY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRICE_HISTORY_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _read_history_cache() -> dict[str, Any]:
    try:
        if not PRICE_HISTORY_CACHE_PATH.exists():
            return {}
        payload = json.loads(PRICE_HISTORY_CACHE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _last(values: list[float | None]) -> float | None:
    return next((value for value in reversed(values) if value is not None), None)
