from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SignalBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    MIXED = "mixed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Candle:
    datetime_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class TechnicalAnalysisReport:
    symbol: str
    candle_count: int
    latest_close: float
    sma_fast: float | None
    sma_slow: float | None
    rsi: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    lines: list[str]
    trend_bias: SignalBias
    rsi_bias: SignalBias
    macd_bias: SignalBias
    overall_bias: SignalBias


@dataclass(frozen=True)
class MultiTimeframeTechnicalReport:
    symbol: str
    intraday: TechnicalAnalysisReport
    daily: TechnicalAnalysisReport
    comparison_lines: list[str]


def candles_from_price_history(payload: Any) -> list[Candle]:
    if not isinstance(payload, dict):
        raise ValueError("Unexpected price-history response; expected an object.")

    raw_candles = payload.get("candles") or []
    if not isinstance(raw_candles, list):
        raise ValueError("Unexpected price-history response; missing candles list.")

    candles: list[Candle] = []
    for item in raw_candles:
        if not isinstance(item, dict):
            continue
        try:
            candles.append(
                Candle(
                    datetime_ms=int(item.get("datetime") or 0),
                    open=float(item.get("open")),
                    high=float(item.get("high")),
                    low=float(item.get("low")),
                    close=float(item.get("close")),
                    volume=float(item.get("volume") or 0),
                )
            )
        except (TypeError, ValueError):
            continue

    return candles


def analyze_candles(symbol: str, candles: list[Candle]) -> TechnicalAnalysisReport:
    if len(candles) < 35:
        raise ValueError("At least 35 candles are required for SMA/RSI/MACD analysis.")

    closes = [candle.close for candle in candles]
    latest_close = closes[-1]
    sma_fast = simple_moving_average(closes, 20)
    sma_slow = simple_moving_average(closes, 50)
    rsi_value = rsi(closes, 14)
    macd_line, signal_line, histogram = macd(closes)

    trend_bias = classify_trend(latest_close, sma_fast, sma_slow)
    rsi_bias = classify_rsi(rsi_value)
    macd_bias = classify_macd(macd_line, signal_line, histogram)
    overall_bias = combine_biases([trend_bias, rsi_bias, macd_bias])

    lines = [
        _trend_summary(latest_close, sma_fast, sma_slow),
        _rsi_summary(rsi_value),
        _macd_summary(macd_line, signal_line, histogram),
    ]

    return TechnicalAnalysisReport(
        symbol=symbol.strip().upper(),
        candle_count=len(candles),
        latest_close=latest_close,
        sma_fast=sma_fast,
        sma_slow=sma_slow,
        rsi=rsi_value,
        macd=macd_line,
        macd_signal=signal_line,
        macd_histogram=histogram,
        lines=lines,
        trend_bias=trend_bias,
        rsi_bias=rsi_bias,
        macd_bias=macd_bias,
        overall_bias=overall_bias,
    )


def compare_timeframes(
    symbol: str,
    intraday: TechnicalAnalysisReport,
    daily: TechnicalAnalysisReport,
) -> MultiTimeframeTechnicalReport:
    lines: list[str] = []

    if daily.overall_bias == intraday.overall_bias and daily.overall_bias in {SignalBias.BULLISH, SignalBias.BEARISH}:
        lines.append(
            f"Alignment: Both daily and intraday reads lean {daily.overall_bias.value}. That is stronger confirmation than either timeframe alone."
        )
    elif daily.overall_bias == SignalBias.BULLISH and intraday.overall_bias == SignalBias.BEARISH:
        lines.append(
            "Alignment: Daily trend leans bullish, but intraday leans bearish. This can mean a short-term pullback inside a larger uptrend."
        )
    elif daily.overall_bias == SignalBias.BEARISH and intraday.overall_bias == SignalBias.BULLISH:
        lines.append(
            "Alignment: Daily trend leans bearish, but intraday leans bullish. This can mean a short-term bounce inside a weaker larger trend."
        )
    else:
        lines.append(
            f"Alignment: Mixed. Daily overall is {daily.overall_bias.value}; intraday overall is {intraday.overall_bias.value}."
        )

    lines.append(_compare_rsi(daily.rsi, intraday.rsi))
    lines.append(_compare_macd(daily.macd_bias, intraday.macd_bias))
    lines.append(
        "Practical read: use the daily candle read for bigger-picture context and the 5-minute read for timing. Agreement matters more than one signal alone."
    )

    return MultiTimeframeTechnicalReport(
        symbol=symbol.strip().upper(),
        intraday=intraday,
        daily=daily,
        comparison_lines=lines,
    )


def classify_trend(latest_close: float, sma_fast: float | None, sma_slow: float | None) -> SignalBias:
    if sma_fast is None or sma_slow is None:
        return SignalBias.UNKNOWN
    if latest_close > sma_fast > sma_slow:
        return SignalBias.BULLISH
    if latest_close < sma_fast < sma_slow:
        return SignalBias.BEARISH
    return SignalBias.MIXED


def classify_rsi(rsi_value: float | None) -> SignalBias:
    if rsi_value is None:
        return SignalBias.UNKNOWN
    if rsi_value >= 55:
        return SignalBias.BULLISH
    if rsi_value <= 45:
        return SignalBias.BEARISH
    return SignalBias.MIXED


def classify_macd(macd_line: float | None, signal_line: float | None, histogram: float | None) -> SignalBias:
    if macd_line is None or signal_line is None or histogram is None:
        return SignalBias.UNKNOWN
    if macd_line > signal_line and histogram > 0:
        return SignalBias.BULLISH
    if macd_line < signal_line and histogram < 0:
        return SignalBias.BEARISH
    return SignalBias.MIXED


def combine_biases(biases: list[SignalBias]) -> SignalBias:
    bullish = sum(1 for bias in biases if bias == SignalBias.BULLISH)
    bearish = sum(1 for bias in biases if bias == SignalBias.BEARISH)
    if bullish >= 2 and bullish > bearish:
        return SignalBias.BULLISH
    if bearish >= 2 and bearish > bullish:
        return SignalBias.BEARISH
    if bullish == 0 and bearish == 0:
        return SignalBias.UNKNOWN
    return SignalBias.MIXED


def simple_moving_average(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None

    gains: list[float] = []
    losses: list[float] = []
    deltas = [values[index] - values[index - 1] for index in range(1, len(values))]

    for delta in deltas[:period]:
        gains.append(max(delta, 0.0))
        losses.append(abs(min(delta, 0.0)))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    for delta in deltas[period:]:
        gain = max(delta, 0.0)
        loss = abs(min(delta, 0.0))
        average_gain = ((average_gain * (period - 1)) + gain) / period
        average_loss = ((average_loss * (period - 1)) + loss) / period

    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss
    return 100 - (100 / (1 + relative_strength))


def macd(values: list[float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> tuple[float | None, float | None, float | None]:
    if len(values) < slow_period + signal_period:
        return None, None, None

    fast_ema = ema_series(values, fast_period)
    slow_ema = ema_series(values, slow_period)
    macd_series: list[float] = []
    for fast_value, slow_value in zip(fast_ema, slow_ema):
        if fast_value is None or slow_value is None:
            macd_series.append(float("nan"))
        else:
            macd_series.append(fast_value - slow_value)

    usable_macd = [value for value in macd_series if value == value]
    if len(usable_macd) < signal_period:
        return None, None, None

    signal_series = ema_series(usable_macd, signal_period)
    macd_value = usable_macd[-1]
    signal_value = signal_series[-1]
    if signal_value is None:
        return macd_value, None, None

    return macd_value, signal_value, macd_value - signal_value


def ema_series(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []

    multiplier = 2 / (period + 1)
    result: list[float | None] = []
    ema_value: float | None = None

    for index, value in enumerate(values):
        if index + 1 < period:
            result.append(None)
            continue
        if index + 1 == period:
            ema_value = sum(values[:period]) / period
        else:
            assert ema_value is not None
            ema_value = (value - ema_value) * multiplier + ema_value
        result.append(ema_value)

    return result


def _trend_summary(latest_close: float, sma_fast: float | None, sma_slow: float | None) -> str:
    if sma_fast is None or sma_slow is None:
        return "Trend: Not enough candles for the 20/50 moving-average read."

    if latest_close > sma_fast > sma_slow:
        return (
            f"Trend: Bullish structure. Last price ${latest_close:,.2f} is above the 20-period SMA "
            f"(${sma_fast:,.2f}), and the 20-period SMA is above the 50-period SMA (${sma_slow:,.2f})."
        )
    if latest_close < sma_fast < sma_slow:
        return (
            f"Trend: Bearish structure. Last price ${latest_close:,.2f} is below the 20-period SMA "
            f"(${sma_fast:,.2f}), and the 20-period SMA is below the 50-period SMA (${sma_slow:,.2f})."
        )
    return (
        f"Trend: Mixed. Last price is ${latest_close:,.2f}, 20-period SMA is ${sma_fast:,.2f}, "
        f"and 50-period SMA is ${sma_slow:,.2f}."
    )


def _rsi_summary(rsi_value: float | None) -> str:
    if rsi_value is None:
        return "RSI: Not enough candles for a 14-period RSI read."
    if rsi_value >= 70:
        return f"RSI: {rsi_value:.1f}. This is traditionally considered overbought, meaning momentum is strong but pullback risk may be elevated."
    if rsi_value <= 30:
        return f"RSI: {rsi_value:.1f}. This is traditionally considered oversold, meaning selling pressure is stretched but reversal is not guaranteed."
    if rsi_value >= 55:
        return f"RSI: {rsi_value:.1f}. Momentum leans bullish but is not in the classic overbought zone."
    if rsi_value <= 45:
        return f"RSI: {rsi_value:.1f}. Momentum leans bearish but is not in the classic oversold zone."
    return f"RSI: {rsi_value:.1f}. Momentum is roughly neutral."


def _macd_summary(macd_line: float | None, signal_line: float | None, histogram: float | None) -> str:
    if macd_line is None or signal_line is None or histogram is None:
        return "MACD: Not enough candles for a 12/26/9 MACD read."
    if macd_line > signal_line and histogram > 0:
        return f"MACD: Bullish. MACD ({macd_line:.3f}) is above signal ({signal_line:.3f}); histogram is positive at {histogram:.3f}."
    if macd_line < signal_line and histogram < 0:
        return f"MACD: Bearish. MACD ({macd_line:.3f}) is below signal ({signal_line:.3f}); histogram is negative at {histogram:.3f}."
    return f"MACD: Mixed/transitioning. MACD is {macd_line:.3f}, signal is {signal_line:.3f}, histogram is {histogram:.3f}."


def _compare_rsi(daily_rsi: float | None, intraday_rsi: float | None) -> str:
    if daily_rsi is None or intraday_rsi is None:
        return "RSI comparison: Not enough data on one timeframe."
    if daily_rsi >= 55 and intraday_rsi <= 45:
        return f"RSI comparison: Daily RSI is constructive ({daily_rsi:.1f}), while intraday RSI is weak ({intraday_rsi:.1f}). This suggests short-term pressure against a stronger longer-term backdrop."
    if daily_rsi <= 45 and intraday_rsi >= 55:
        return f"RSI comparison: Daily RSI is weak ({daily_rsi:.1f}), while intraday RSI is improving ({intraday_rsi:.1f}). This suggests a short-term bounce attempt inside a weaker longer-term backdrop."
    return f"RSI comparison: Daily RSI is {daily_rsi:.1f}; intraday RSI is {intraday_rsi:.1f}."


def _compare_macd(daily_bias: SignalBias, intraday_bias: SignalBias) -> str:
    if daily_bias == intraday_bias and daily_bias in {SignalBias.BULLISH, SignalBias.BEARISH}:
        return f"MACD comparison: Both timeframes are {daily_bias.value}, which confirms momentum direction."
    if daily_bias == SignalBias.BULLISH and intraday_bias == SignalBias.BEARISH:
        return "MACD comparison: Daily MACD is bullish, but intraday MACD is bearish. Short-term momentum is pushing against the larger read."
    if daily_bias == SignalBias.BEARISH and intraday_bias == SignalBias.BULLISH:
        return "MACD comparison: Daily MACD is bearish, but intraday MACD is bullish. Short-term momentum is trying to rebound against the larger read."
    return f"MACD comparison: Daily MACD is {daily_bias.value}; intraday MACD is {intraday_bias.value}."
