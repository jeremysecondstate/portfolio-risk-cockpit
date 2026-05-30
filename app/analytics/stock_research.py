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


@dataclass(frozen=True)
class ScenarioRow:
    scenario: str
    symbol_price: float
    position_pnl: float
    portfolio_pnl_impact: float
    new_portfolio_value: float


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
    last_price = context.last_price or indicators.latest_close
    portfolio_cap = max(context.portfolio_value, 0.0) * 0.005
    if context.is_held and context.quantity and last_price:
        risk_level = indicators.support or (last_price - indicators.atr_14 if indicators.atr_14 else None)
        if risk_level is not None and risk_level > 0:
            technical_budget = abs(last_price - risk_level) * abs(context.quantity)
        elif indicators.atr_14:
            technical_budget = abs(indicators.atr_14) * abs(context.quantity)
        else:
            technical_budget = abs(context.market_value) * 0.03
    else:
        technical_budget = portfolio_cap
    candidates = [value for value in (technical_budget, portfolio_cap, requested_cap) if value is not None and value > 0]
    return min(candidates) if candidates else None


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
