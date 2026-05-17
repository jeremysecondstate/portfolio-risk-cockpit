from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.analytics.technical_analysis import Candle
from app.core.order_models import OrderSide
from app.core.portfolio import Portfolio


class SetupVerdict(str, Enum):
    INCOMPLETE = "incomplete"
    FAVORABLE = "favorable"
    WATCH = "watch"
    UNFAVORABLE = "unfavorable"


@dataclass(frozen=True)
class SupportResistanceLevels:
    latest_close: float
    recent_low: float
    recent_high: float
    support: float | None
    resistance: float | None
    support_distance_percent: float | None
    resistance_distance_percent: float | None


@dataclass(frozen=True)
class RiskRewardPlan:
    entry_price: float
    stop_price: float | None
    target_price: float | None
    quantity: float
    side: OrderSide
    dollars_at_risk: float | None
    portfolio_risk_percent: float | None
    reward_dollars: float | None
    risk_reward_ratio: float | None
    target_for_2r: float | None
    target_for_3r: float | None
    stop_distance_percent: float | None
    target_distance_percent: float | None
    verdict: SetupVerdict
    verdict_reason: str


def calculate_support_resistance(candles: list[Candle], *, lookback: int = 50) -> SupportResistanceLevels:
    if not candles:
        raise ValueError("At least one candle is required for support/resistance analysis.")

    recent = candles[-lookback:] if len(candles) > lookback else candles
    latest_close = candles[-1].close
    recent_low = min(candle.low for candle in recent)
    recent_high = max(candle.high for candle in recent)

    lows_below = [candle.low for candle in recent if candle.low < latest_close]
    highs_above = [candle.high for candle in recent if candle.high > latest_close]
    support = max(lows_below) if lows_below else recent_low
    resistance = min(highs_above) if highs_above else recent_high

    support_distance_percent = _percent_distance(latest_close, support) if support is not None else None
    resistance_distance_percent = _percent_distance(latest_close, resistance) if resistance is not None else None

    return SupportResistanceLevels(
        latest_close=latest_close,
        recent_low=recent_low,
        recent_high=recent_high,
        support=support,
        resistance=resistance,
        support_distance_percent=support_distance_percent,
        resistance_distance_percent=resistance_distance_percent,
    )


def calculate_risk_reward_plan(
    *,
    portfolio: Portfolio,
    side: OrderSide,
    entry_price: float,
    stop_price: float | None,
    target_price: float | None,
    quantity: float,
) -> RiskRewardPlan:
    if entry_price <= 0:
        raise ValueError("Entry price must be positive.")
    if quantity <= 0:
        raise ValueError("Quantity must be positive.")

    dollars_at_risk: float | None = None
    portfolio_risk_percent: float | None = None
    reward_dollars: float | None = None
    risk_reward_ratio: float | None = None
    target_for_2r: float | None = None
    target_for_3r: float | None = None
    stop_distance_percent: float | None = None
    target_distance_percent: float | None = None

    if stop_price is not None and stop_price > 0:
        per_share_risk = _per_share_risk(side, entry_price, stop_price)
        stop_distance_percent = abs((entry_price - stop_price) / entry_price) * 100
        if per_share_risk > 0:
            dollars_at_risk = round(per_share_risk * quantity, 2)
            portfolio_risk_percent = round((dollars_at_risk / max(portfolio.total_value, 0.01)) * 100, 3)
            target_for_2r = _target_for_r(side, entry_price, per_share_risk, 2)
            target_for_3r = _target_for_r(side, entry_price, per_share_risk, 3)

    if target_price is not None and target_price > 0:
        per_share_reward = _per_share_reward(side, entry_price, target_price)
        target_distance_percent = abs((target_price - entry_price) / entry_price) * 100
        if per_share_reward > 0:
            reward_dollars = round(per_share_reward * quantity, 2)

    if dollars_at_risk and reward_dollars:
        risk_reward_ratio = round(reward_dollars / dollars_at_risk, 2)

    verdict, verdict_reason = _setup_verdict(
        dollars_at_risk=dollars_at_risk,
        portfolio_risk_percent=portfolio_risk_percent,
        risk_reward_ratio=risk_reward_ratio,
    )

    return RiskRewardPlan(
        entry_price=entry_price,
        stop_price=stop_price,
        target_price=target_price,
        quantity=quantity,
        side=side,
        dollars_at_risk=dollars_at_risk,
        portfolio_risk_percent=portfolio_risk_percent,
        reward_dollars=reward_dollars,
        risk_reward_ratio=risk_reward_ratio,
        target_for_2r=target_for_2r,
        target_for_3r=target_for_3r,
        stop_distance_percent=stop_distance_percent,
        target_distance_percent=target_distance_percent,
        verdict=verdict,
        verdict_reason=verdict_reason,
    )


def _per_share_risk(side: OrderSide, entry_price: float, stop_price: float) -> float:
    if side == OrderSide.BUY:
        return entry_price - stop_price
    return stop_price - entry_price


def _per_share_reward(side: OrderSide, entry_price: float, target_price: float) -> float:
    if side == OrderSide.BUY:
        return target_price - entry_price
    return entry_price - target_price


def _target_for_r(side: OrderSide, entry_price: float, per_share_risk: float, multiple: int) -> float:
    if side == OrderSide.BUY:
        return round(entry_price + (per_share_risk * multiple), 2)
    return round(entry_price - (per_share_risk * multiple), 2)


def _setup_verdict(
    *,
    dollars_at_risk: float | None,
    portfolio_risk_percent: float | None,
    risk_reward_ratio: float | None,
) -> tuple[SetupVerdict, str]:
    if dollars_at_risk is None or portfolio_risk_percent is None:
        return SetupVerdict.INCOMPLETE, "Add a valid stop price to calculate capital at risk."

    if portfolio_risk_percent > 2.0:
        return SetupVerdict.UNFAVORABLE, "Portfolio risk is above 2%; size down or use a tighter stop."

    if risk_reward_ratio is None:
        if portfolio_risk_percent <= 1.0:
            return SetupVerdict.WATCH, "Risk is controlled, but add a target to evaluate reward."
        return SetupVerdict.WATCH, "Risk is measurable, but add a target and consider lowering portfolio risk."

    if risk_reward_ratio >= 2.0 and portfolio_risk_percent <= 1.0:
        return SetupVerdict.FAVORABLE, "Risk is controlled and reward is at least 2R."

    if risk_reward_ratio < 1.0:
        return SetupVerdict.UNFAVORABLE, "Reward is smaller than risk."

    return SetupVerdict.WATCH, "Setup is measurable, but reward/risk or portfolio risk could be better."


def _percent_distance(current_price: float, level: float) -> float:
    if current_price <= 0:
        return 0.0
    return round(((level - current_price) / current_price) * 100, 2)
