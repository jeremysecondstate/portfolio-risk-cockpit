from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

from app.analytics.capital_structure_pressure import (
    CapitalStructurePressureReport,
    capital_structure_technical_modifier,
    format_capital_structure_pressure_section,
)


try:
    MARKET_TZ = ZoneInfo("America/New_York")
except Exception:
    MARKET_TZ = None

REGULAR_SESSION_START = time(9, 30)
REGULAR_SESSION_END = time(16, 0)


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


@dataclass(frozen=True)
class TimeframeSpec:
    key: str
    label: str
    period_type: str
    period: int
    frequency_type: str
    frequency: int
    role: str
    optional: bool = True


@dataclass(frozen=True)
class TechnicalLevel:
    kind: str
    low: float
    high: float
    center: float
    reason: str


@dataclass(frozen=True)
class VolatilityRead:
    atr_14: float | None
    atr_percent: float | None
    realized_vol_10: float | None
    realized_vol_20: float | None
    realized_vol_60: float | None
    range_state: str
    reason: str


@dataclass(frozen=True)
class VolumeRead:
    average_volume_20: float | None
    relative_volume: float | None
    up_down_volume_ratio: float | None
    obv: float | None
    accumulation_read: str
    reason: str


@dataclass(frozen=True)
class RelativeStrengthRead:
    benchmark: str
    return_1: float | None
    return_5: float | None
    return_20: float | None
    benchmark_return_1: float | None
    benchmark_return_5: float | None
    benchmark_return_20: float | None
    spread_1: float | None
    spread_5: float | None
    spread_20: float | None
    verdict: str
    reason: str


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    mark: float | None = None
    total_volume: float | None = None
    raw: Any = None
    data_quality_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PrcIndexComponents:
    close_component: float
    vwap_component: float
    typical_price_component: float
    volume_pressure_component: float
    volatility_adjustment: float
    spread_adjustment: float
    relative_strength_adjustment: float
    close_location_adjustment: float
    trend_slope_adjustment: float


@dataclass(frozen=True)
class PrcIndexSeriesPoint:
    datetime_ms: int
    index_price: float


@dataclass(frozen=True)
class PrcIndexSeries:
    points: list[PrcIndexSeriesPoint]


@dataclass(frozen=True)
class PrcIndexPrice:
    symbol: str
    timeframe_name: str
    latest_price: float | None
    index_price: float | None
    index_distance: float | None
    index_distance_percent: float | None
    index_slope: float | None
    confidence: str
    read: str
    components: PrcIndexComponents | None
    warnings: list[str]
    explanation_lines: list[str]
    series: PrcIndexSeries | None = None


@dataclass(frozen=True)
class ActionTrigger:
    label: str
    price: float | None
    reason: str


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    score: float
    reason: str


@dataclass(frozen=True)
class TechnicalTicket:
    side: str = "buy"
    quantity: float | None = None
    entry_price: float | None = None
    stop_price: float | None = None
    portfolio_value: float | None = None


@dataclass(frozen=True)
class TicketCheck:
    entry_quality: str
    stop_quality: str
    risk_note: str
    verdict: str
    lines: list[str]
    score: ScoreComponent


@dataclass(frozen=True)
class TimeframeTechnicalSnapshot:
    key: str
    label: str
    role: str
    candle_count: int
    latest_close: float | None
    sma_20: float | None
    sma_50: float | None
    ema_8: float | None
    ema_21: float | None
    ema_50: float | None
    rsi_14: float | None
    macd: float | None
    macd_signal: float | None
    macd_histogram: float | None
    macd_histogram_change: float | None
    atr_14: float | None
    atr_percent: float | None
    realized_vol_10: float | None
    realized_vol_20: float | None
    realized_vol_60: float | None
    roc_5: float | None
    roc_20: float | None
    recent_high: float | None
    recent_low: float | None
    support_zones: list[TechnicalLevel]
    resistance_zones: list[TechnicalLevel]
    trend_structure: str
    range_state: str
    close_location: float | None
    volume_read: VolumeRead
    vwap: float | None
    vwap_distance_percent: float | None
    gap_read: str
    opening_range_high: float | None
    opening_range_low: float | None
    lines: list[str]
    scores: dict[str, ScoreComponent] = field(default_factory=dict)
    session_vwap: float | None = None
    rolling_vwap_20: float | None = None
    multi_day_vwap: float | None = None
    realized_move_10_pct: float | None = None
    realized_move_20_pct: float | None = None
    realized_move_60_pct: float | None = None
    annualized_realized_vol_20: float | None = None
    intraday_range_percent: float | None = None
    vol_regime_percentile: float | None = None


@dataclass(frozen=True)
class TechnicalCommandCenterReport:
    symbol: str
    snapshots: dict[str, TimeframeTechnicalSnapshot]
    benchmark_reads: list[RelativeStrengthRead]
    ticket_check: TicketCheck
    scores: dict[str, ScoreComponent]
    overall_score: float
    overall_read: str
    confidence: str
    best_action: str
    key_triggers: list[ActionTrigger]
    warnings: list[str]
    plain_english_plan: list[str]
    prc_indexes: dict[str, PrcIndexPrice] = field(default_factory=dict)
    capital_structure_pressure: CapitalStructurePressureReport | None = None


DEFAULT_COMMAND_CENTER_TIMEFRAMES: tuple[TimeframeSpec, ...] = (
    TimeframeSpec("daily_1y", "1y daily", "year", 1, "daily", 1, "regime", optional=False),
    TimeframeSpec("setup_30m", "10d 30m", "day", 10, "minute", 30, "setup", optional=True),
    TimeframeSpec("timing_5m", "10d 5m", "day", 10, "minute", 5, "timing", optional=False),
    TimeframeSpec("timing_1m", "1d 1m", "day", 1, "minute", 1, "timing", optional=True),
)


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


def parse_quote_snapshot(symbol: str, payload: Any) -> QuoteSnapshot:
    """Parse Schwab quote payloads defensively without assuming one shape."""
    clean_symbol = symbol.strip().upper()
    warnings: list[str] = []
    raw_quote = _quote_payload_for_symbol(clean_symbol, payload)
    if raw_quote is None:
        warnings.append("Quote payload did not contain a recognizable quote object.")
        raw_quote = payload

    bid = _first_nested_number(raw_quote, ("bidPrice", "bid", "bid_price"))
    ask = _first_nested_number(raw_quote, ("askPrice", "ask", "ask_price"))
    last = _first_nested_number(raw_quote, ("lastPrice", "regularMarketLastPrice", "closePrice", "last", "last_price"))
    mark = _first_nested_number(raw_quote, ("mark", "markPrice", "mark_price"))
    total_volume = _first_nested_number(raw_quote, ("totalVolume", "total_volume", "volume"))

    if bid is not None and bid <= 0:
        warnings.append("Quote bid was non-positive and ignored.")
        bid = None
    if ask is not None and ask <= 0:
        warnings.append("Quote ask was non-positive and ignored.")
        ask = None
    if bid is not None and ask is not None and ask < bid:
        warnings.append("Quote ask was below bid; spread was ignored.")
        bid = None
        ask = None
    if not any(value is not None for value in (bid, ask, last, mark, total_volume)):
        warnings.append("Quote payload did not expose bid/ask/last/mark/volume fields.")

    return QuoteSnapshot(
        symbol=clean_symbol,
        bid=bid,
        ask=ask,
        last=last if last and last > 0 else None,
        mark=mark if mark and mark > 0 else None,
        total_volume=total_volume if total_volume and total_volume > 0 else None,
        raw=payload,
        data_quality_warnings=warnings,
    )


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


def build_technical_command_center_report(
    symbol: str,
    timeframe_candles: dict[str, list[Candle]],
    *,
    benchmark_candles: dict[str, list[Candle]] | None = None,
    quote_snapshot: QuoteSnapshot | None = None,
    ticket: TechnicalTicket | None = None,
    warnings: list[str] | None = None,
    capital_structure_pressure: CapitalStructurePressureReport | None = None,
) -> TechnicalCommandCenterReport:
    clean_symbol = symbol.strip().upper()
    snapshots: dict[str, TimeframeTechnicalSnapshot] = {}
    report_warnings = list(warnings or [])

    spec_by_key = {spec.key: spec for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES}
    for key, candles in timeframe_candles.items():
        spec = spec_by_key.get(key, TimeframeSpec(key, key, "", 0, "", 0, "custom"))
        snapshot = build_timeframe_technical_snapshot(clean_symbol, spec, candles)
        snapshots[key] = snapshot
        if not candles:
            report_warnings.append(f"{spec.label}: no candles were available.")

    if not snapshots:
        report_warnings.append("No usable Schwab price-history timeframe was available.")

    benchmark_reads = build_relative_strength_reads(
        snapshots,
        benchmark_candles or {},
        symbol_candles=_daily_candles_from_timeframes(timeframe_candles),
    )
    if benchmark_candles is None or not benchmark_candles or not any(read.verdict != "unknown" for read in benchmark_reads):
        report_warnings.append("Benchmark relative-strength data unavailable; PRC excludes relative-strength adjustment.")
    if quote_snapshot is not None:
        report_warnings.extend(quote_snapshot.data_quality_warnings)
    relative_strength_score = prc_relative_strength_score(benchmark_reads)
    prc_indexes = build_prc_index_prices(
        clean_symbol,
        timeframe_candles,
        quote_snapshot=quote_snapshot,
        relative_strength_score=relative_strength_score,
        benchmark_data_available=any(read.verdict != "unknown" for read in benchmark_reads),
        ticket=ticket or TechnicalTicket(),
    )
    ticket_check = build_ticket_check(snapshots, ticket or TechnicalTicket())
    scores = score_command_center(snapshots, benchmark_reads, ticket_check)
    overall_score = _weighted_score(
        scores,
        {
            "Trend": 0.20,
            "Momentum": 0.16,
            "Volume": 0.12,
            "Volatility/Risk": 0.14,
            "Relative Strength": 0.14,
            "Alignment": 0.12,
            "Ticket Quality": 0.12,
        },
    )
    overall_read = _overall_read(overall_score, scores)
    confidence = _confidence_label(snapshots, benchmark_reads, report_warnings)
    best_action = _best_action(overall_read, snapshots, ticket_check)
    key_triggers = _build_action_triggers(snapshots)
    plain_english_plan = _plain_english_plan(overall_read, snapshots, ticket_check, key_triggers)

    return TechnicalCommandCenterReport(
        symbol=clean_symbol,
        snapshots=snapshots,
        benchmark_reads=benchmark_reads,
        ticket_check=ticket_check,
        scores=scores,
        overall_score=overall_score,
        overall_read=overall_read,
        confidence=confidence,
        best_action=best_action,
        key_triggers=key_triggers,
        warnings=_dedupe(report_warnings),
        plain_english_plan=plain_english_plan,
        prc_indexes=prc_indexes,
        capital_structure_pressure=capital_structure_pressure,
    )


def build_timeframe_technical_snapshot(
    symbol: str,
    spec: TimeframeSpec,
    candles: list[Candle],
) -> TimeframeTechnicalSnapshot:
    clean_candles = sorted(candles, key=lambda candle: candle.datetime_ms)
    if not clean_candles:
        empty_volume = VolumeRead(None, None, None, None, "unknown", "No volume data was available.")
        return TimeframeTechnicalSnapshot(
            key=spec.key,
            label=spec.label,
            role=spec.role,
            candle_count=0,
            latest_close=None,
            sma_20=None,
            sma_50=None,
            ema_8=None,
            ema_21=None,
            ema_50=None,
            rsi_14=None,
            macd=None,
            macd_signal=None,
            macd_histogram=None,
            macd_histogram_change=None,
            atr_14=None,
            atr_percent=None,
            realized_vol_10=None,
            realized_vol_20=None,
            realized_vol_60=None,
            roc_5=None,
            roc_20=None,
            recent_high=None,
            recent_low=None,
            support_zones=[],
            resistance_zones=[],
            trend_structure="unknown",
            range_state="unknown",
            close_location=None,
            volume_read=empty_volume,
            vwap=None,
            vwap_distance_percent=None,
            gap_read="No candles available for gap read.",
            opening_range_high=None,
            opening_range_low=None,
            lines=["No candles available for this timeframe."],
            scores={},
        )

    closes = [candle.close for candle in clean_candles]
    latest_close = closes[-1]
    sma_20 = simple_moving_average(closes, 20)
    sma_50 = simple_moving_average(closes, 50)
    ema_8 = _last(ema_series(closes, 8))
    ema_21 = _last(ema_series(closes, 21))
    ema_50 = _last(ema_series(closes, 50))
    rsi_14 = rsi(closes, 14)
    macd_line, signal_line, histogram = macd(closes)
    previous_macd, previous_signal, previous_histogram = macd(closes[:-1]) if len(closes) > 1 else (None, None, None)
    del previous_macd, previous_signal
    macd_histogram_change = None if histogram is None or previous_histogram is None else histogram - previous_histogram
    atr_14 = average_true_range(clean_candles, 14)
    atr_percent = _percent(atr_14, latest_close)
    realized_move_10_pct = realized_move_window_pct(closes, 10)
    realized_move_20_pct = realized_move_window_pct(closes, 20)
    realized_move_60_pct = realized_move_window_pct(closes, 60)
    realized_vol_10 = realized_move_10_pct
    realized_vol_20 = realized_move_20_pct
    realized_vol_60 = realized_move_60_pct
    annualized_realized_vol_20 = annualized_realized_volatility(closes, 20, bars_per_year=_bars_per_year(spec))
    roc_5 = rate_of_change(closes, 5)
    roc_20 = rate_of_change(closes, 20)
    recent = clean_candles[-60:] if len(clean_candles) >= 60 else clean_candles
    recent_high = max(candle.high for candle in recent)
    recent_low = min(candle.low for candle in recent)
    support_zones, resistance_zones = support_resistance_zones(clean_candles, atr_14=atr_14)
    trend_structure = trend_structure_read(clean_candles)
    range_state = range_compression_read(clean_candles, atr_14)
    close_location = close_location_value(clean_candles[-1])
    volume_read = volume_participation_read(clean_candles)
    session_candles = latest_regular_session_candles(clean_candles) if spec.frequency_type == "minute" else []
    session_vwap_value = vwap(session_candles) if session_candles else None
    rolling_vwap_20_value = _last(rolling_vwap(clean_candles, 20)) if spec.frequency_type == "minute" and len(clean_candles) >= 20 else None
    multi_day_vwap_value = vwap(clean_candles) if spec.frequency_type == "minute" else None
    vwap_value = session_vwap_value if session_vwap_value is not None else rolling_vwap_20_value
    vwap_distance_percent = _percent(latest_close - vwap_value, vwap_value) if vwap_value else None
    intraday_range_percent = _intraday_range_percent(session_candles) if spec.frequency_type == "minute" else None
    vol_regime_percentile = volatility_regime_percentile(clean_candles)
    gap_read, opening_range_high, opening_range_low = gap_and_opening_range_read(clean_candles, is_intraday=spec.frequency_type == "minute")
    scores = {
        "Trend": score_trend(latest_close, ema_21, ema_50, sma_50, trend_structure),
        "Momentum": score_momentum(rsi_14, histogram, macd_histogram_change, roc_5, roc_20),
        "Volume": score_volume(volume_read, close_location),
        "Volatility/Risk": score_volatility(atr_percent, range_state, realized_vol_20),
    }
    lines = _snapshot_lines(
        label=spec.label,
        latest_close=latest_close,
        ema_21=ema_21,
        ema_50=ema_50,
        rsi_14=rsi_14,
        histogram=histogram,
        macd_histogram_change=macd_histogram_change,
        atr_14=atr_14,
        atr_percent=atr_percent,
        support_zones=support_zones,
        resistance_zones=resistance_zones,
        trend_structure=trend_structure,
        range_state=range_state,
        volume_read=volume_read,
        vwap_value=vwap_value,
        vwap_distance_percent=vwap_distance_percent,
        session_vwap=session_vwap_value,
        rolling_vwap_20=rolling_vwap_20_value,
        multi_day_vwap=multi_day_vwap_value,
        realized_move_20_pct=realized_move_20_pct,
        annualized_realized_vol_20=annualized_realized_vol_20,
    )

    return TimeframeTechnicalSnapshot(
        key=spec.key,
        label=spec.label,
        role=spec.role,
        candle_count=len(clean_candles),
        latest_close=latest_close,
        sma_20=sma_20,
        sma_50=sma_50,
        ema_8=ema_8,
        ema_21=ema_21,
        ema_50=ema_50,
        rsi_14=rsi_14,
        macd=macd_line,
        macd_signal=signal_line,
        macd_histogram=histogram,
        macd_histogram_change=macd_histogram_change,
        atr_14=atr_14,
        atr_percent=atr_percent,
        realized_vol_10=realized_vol_10,
        realized_vol_20=realized_vol_20,
        realized_vol_60=realized_vol_60,
        roc_5=roc_5,
        roc_20=roc_20,
        recent_high=recent_high,
        recent_low=recent_low,
        support_zones=support_zones,
        resistance_zones=resistance_zones,
        trend_structure=trend_structure,
        range_state=range_state,
        close_location=close_location,
        volume_read=volume_read,
        vwap=vwap_value,
        vwap_distance_percent=vwap_distance_percent,
        gap_read=gap_read,
        opening_range_high=opening_range_high,
        opening_range_low=opening_range_low,
        lines=lines,
        scores=scores,
        session_vwap=session_vwap_value,
        rolling_vwap_20=rolling_vwap_20_value,
        multi_day_vwap=multi_day_vwap_value,
        realized_move_10_pct=realized_move_10_pct,
        realized_move_20_pct=realized_move_20_pct,
        realized_move_60_pct=realized_move_60_pct,
        annualized_realized_vol_20=annualized_realized_vol_20,
        intraday_range_percent=intraday_range_percent,
        vol_regime_percentile=vol_regime_percentile,
    )


def average_true_range(candles: list[Candle], period: int = 14) -> float | None:
    if len(candles) <= period:
        return None
    ranges: list[float] = []
    for index in range(1, len(candles)):
        current = candles[index]
        previous_close = candles[index - 1].close
        ranges.append(max(current.high - current.low, abs(current.high - previous_close), abs(current.low - previous_close)))
    if len(ranges) < period:
        return None
    return sum(ranges[-period:]) / period


def realized_volatility(values: list[float], period: int) -> float | None:
    return realized_move_window_pct(values, period)


def realized_move_window_pct(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    returns: list[float] = []
    for index in range(len(values) - period, len(values)):
        previous = values[index - 1]
        current = values[index]
        if previous <= 0 or current <= 0:
            continue
        returns.append(math.log(current / previous))
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(len(returns)) * 100


def annualized_realized_volatility(values: list[float], period: int, *, bars_per_year: float) -> float | None:
    if len(values) <= period or bars_per_year <= 0:
        return None
    returns: list[float] = []
    for index in range(len(values) - period, len(values)):
        previous = values[index - 1]
        current = values[index]
        if previous <= 0 or current <= 0:
            continue
        returns.append(math.log(current / previous))
    if len(returns) < 2:
        return None
    return statistics.stdev(returns) * math.sqrt(bars_per_year) * 100


def _bars_per_year(spec: TimeframeSpec) -> float:
    if spec.frequency_type == "daily":
        return 252.0
    if spec.frequency_type == "minute" and spec.frequency > 0:
        return (390 / spec.frequency) * 252
    return 252.0


def rate_of_change(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    prior = values[-period - 1]
    if prior == 0:
        return None
    return ((values[-1] - prior) / prior) * 100


def bounded(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def normalize_adjustment(value: float | None, *, denominator: float, min_value: float = -1.0, max_value: float = 1.0) -> float:
    if value is None or denominator == 0:
        return 0.0
    return bounded(value / denominator, min_value, max_value)


def typical_price(candle: Candle) -> float:
    return (candle.high + candle.low + candle.close) / 3


def vwap(candles: list[Candle]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for candle in candles:
        if candle.volume <= 0:
            continue
        numerator += typical_price(candle) * candle.volume
        denominator += candle.volume
    if denominator <= 0:
        return None
    return numerator / denominator


def rolling_vwap(candles: list[Candle], period: int) -> list[float | None]:
    if period <= 0:
        raise ValueError("rolling VWAP period must be positive.")
    result: list[float | None] = []
    for index in range(len(candles)):
        if index + 1 < period:
            result.append(None)
            continue
        result.append(vwap(candles[index + 1 - period:index + 1]))
    return result


def latest_regular_session_candles(candles: list[Candle]) -> list[Candle]:
    groups = _regular_session_groups(candles)
    if not groups:
        return []
    latest_date = max(groups)
    return groups[latest_date]


def volatility_regime_percentile(candles: list[Candle], lookback: int = 60) -> float | None:
    if len(candles) < 3:
        return None
    clean = sorted(candles, key=lambda candle: candle.datetime_ms)
    ranges: list[float] = []
    for index, candle in enumerate(clean):
        if index == 0:
            ranges.append(candle.high - candle.low)
            continue
        previous_close = clean[index - 1].close
        ranges.append(max(candle.high - candle.low, abs(candle.high - previous_close), abs(candle.low - previous_close)))
    recent = ranges[-lookback:]
    if len(recent) < 3:
        return None
    latest = recent[-1]
    below_or_equal = sum(1 for value in recent if value <= latest)
    return (below_or_equal / len(recent)) * 100


def _intraday_range_percent(candles: list[Candle]) -> float | None:
    if not candles:
        return None
    high = max(candle.high for candle in candles)
    low = min(candle.low for candle in candles)
    latest_close = candles[-1].close
    return _percent(high - low, latest_close)


def relative_volume(candles: list[Candle], lookback: int = 20) -> float | None:
    if len(candles) < lookback or lookback <= 0:
        return None
    average_volume = simple_moving_average([candle.volume for candle in candles], lookback)
    if average_volume is None or average_volume <= 0:
        return None
    return candles[-1].volume / average_volume


def volume_pressure_score(candles: list[Candle], lookback: int = 20) -> float:
    if not candles:
        return 0.0
    recent = candles[-lookback:] if lookback > 0 else candles
    weights: list[float] = []
    scores: list[float] = []
    avg_volume = simple_moving_average([candle.volume for candle in recent], min(len(recent), max(1, lookback))) or 0.0
    for candle in recent:
        location = close_location_value(candle)
        direction = 1.0 if candle.close > candle.open else -1.0 if candle.close < candle.open else 0.0
        pressure = ((location if location is not None else 0.0) * 0.70) + (direction * 0.30)
        relative_weight = 1.0
        if avg_volume > 0 and candle.volume > 0:
            relative_weight = bounded(candle.volume / avg_volume, 0.25, 2.0)
        scores.append(pressure * relative_weight)
        weights.append(relative_weight)
    if not scores or sum(weights) <= 0:
        return 0.0
    return bounded(sum(scores) / sum(weights), -1.0, 1.0)


def spread_percent(quote: QuoteSnapshot | None) -> float | None:
    if quote is None or quote.bid is None or quote.ask is None:
        return None
    if quote.bid <= 0 or quote.ask <= 0 or quote.ask < quote.bid:
        return None
    mid = (quote.bid + quote.ask) / 2
    if mid <= 0:
        return None
    return ((quote.ask - quote.bid) / mid) * 100


def atr(candles: list[Candle], period: int = 14) -> float | None:
    return average_true_range(candles, period)


def support_resistance_zones(candles: list[Candle], *, atr_14: float | None = None, lookback: int = 80) -> tuple[list[TechnicalLevel], list[TechnicalLevel]]:
    if not candles:
        return [], []
    recent = candles[-lookback:] if len(candles) > lookback else candles
    latest = candles[-1].close
    tolerance = max(latest * 0.0075, (atr_14 or 0) * 0.35, 0.01)
    supports = _cluster_levels(_pivot_values(recent, "low"), tolerance, "support", latest)
    resistances = _cluster_levels(_pivot_values(recent, "high"), tolerance, "resistance", latest)
    if not supports:
        low = min(candle.low for candle in recent)
        supports = [TechnicalLevel("support", low, low, low, "Recent lookback low.")]
    if not resistances:
        high = max(candle.high for candle in recent)
        resistances = [TechnicalLevel("resistance", high, high, high, "Recent lookback high.")]
    supports = [level for level in supports if level.center <= latest] or supports
    resistances = [level for level in resistances if level.center >= latest] or resistances
    supports.sort(key=lambda level: abs(latest - level.center))
    resistances.sort(key=lambda level: abs(level.center - latest))
    return supports[:3], resistances[:3]


def trend_structure_read(candles: list[Candle]) -> str:
    if len(candles) < 10:
        return "not enough data"
    recent = candles[-80:] if len(candles) > 80 else candles
    highs = _pivot_values(recent, "high")
    lows = _pivot_values(recent, "low")
    if len(highs) < 2 or len(lows) < 2:
        return "range/chop"
    first_high, second_high = highs[-2], highs[-1]
    first_low, second_low = lows[-2], lows[-1]
    if second_high > first_high and second_low > first_low:
        return "higher-high / higher-low"
    if second_high < first_high and second_low < first_low:
        return "lower-high / lower-low"
    return "range/chop"


def range_compression_read(candles: list[Candle], atr_14: float | None) -> str:
    if len(candles) < 40 or atr_14 is None:
        return "not enough data"
    recent_ranges = [candle.high - candle.low for candle in candles[-10:]]
    prior_ranges = [candle.high - candle.low for candle in candles[-40:-10]]
    if not prior_ranges:
        return "not enough data"
    recent_avg = sum(recent_ranges) / len(recent_ranges)
    prior_avg = sum(prior_ranges) / len(prior_ranges)
    if recent_avg <= prior_avg * 0.75:
        return "compressing"
    if recent_avg >= prior_avg * 1.25:
        return "expanding"
    return "normal"


def close_location_value(candle: Candle) -> float | None:
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return None
    return (((candle.close - candle.low) / candle_range) * 2) - 1


def volume_participation_read(candles: list[Candle]) -> VolumeRead:
    if not candles:
        return VolumeRead(None, None, None, None, "unknown", "No candles were available.")
    volumes = [candle.volume for candle in candles]
    average_volume_20 = simple_moving_average(volumes, 20)
    relative_volume = None
    if average_volume_20 and average_volume_20 > 0:
        relative_volume = candles[-1].volume / average_volume_20
    up_volume = 0.0
    down_volume = 0.0
    obv = 0.0
    for index in range(1, len(candles)):
        volume = candles[index].volume
        if candles[index].close > candles[index - 1].close:
            up_volume += volume
            obv += volume
        elif candles[index].close < candles[index - 1].close:
            down_volume += volume
            obv -= volume
    up_down_ratio = up_volume / down_volume if down_volume > 0 else None
    if up_down_ratio is not None and up_down_ratio >= 1.25 and obv > 0:
        accumulation_read = "accumulation"
    elif up_down_ratio is not None and up_down_ratio <= 0.80 and obv < 0:
        accumulation_read = "distribution"
    else:
        accumulation_read = "mixed/neutral"
    reason = "Relative volume unavailable." if relative_volume is None else f"Latest volume is {relative_volume:.2f}x the 20-candle average."
    return VolumeRead(average_volume_20, relative_volume, up_down_ratio, obv, accumulation_read, reason)


def gap_and_opening_range_read(candles: list[Candle], *, is_intraday: bool) -> tuple[str, float | None, float | None]:
    if len(candles) < 2:
        return "Not enough candles for gap read.", None, None

    clean_candles = sorted(candles, key=lambda candle: candle.datetime_ms)
    if is_intraday:
        groups = _regular_session_groups(clean_candles)
        if not groups:
            return "No regular-session candles available for gap read.", None, None

        latest_session_date = max(groups)
        current_session = groups[latest_session_date]
        previous_session_dates = [session_date for session_date in groups if session_date < latest_session_date]
        opening_range = _opening_range_candles(current_session)
        opening_high = max(candle.high for candle in opening_range) if opening_range else None
        opening_low = min(candle.low for candle in opening_range) if opening_range else None
        if not previous_session_dates:
            return "Prior regular-session close unavailable for gap read.", opening_high, opening_low

        prior_session = groups[max(previous_session_dates)]
        current_open = current_session[0].open
        prior_close = prior_session[-1].close
        gap_percent = _percent(current_open - prior_close, prior_close)
        latest = current_session[-1].close
        extended_read = _extended_hours_gap_read(clean_candles, latest_session_date, prior_close)
        if gap_percent is None or abs(gap_percent) < 0.5:
            line = "No material regular-session gap detected."
        else:
            direction = "up" if gap_percent > 0 else "down"
            filled = latest <= prior_close if gap_percent > 0 else latest >= prior_close
            status = "filled" if filled else "not filled"
            line = f"Regular-session gap {direction} {gap_percent:+.2f}%; {status}."
        if extended_read:
            line = f"{line} {extended_read}"
        return line, opening_high, opening_low

    current_open = clean_candles[-1].open
    prior_close = clean_candles[-2].close
    gap_percent = _percent(current_open - prior_close, prior_close)
    opening_range = [clean_candles[-1]]
    opening_high = max(candle.high for candle in opening_range)
    opening_low = min(candle.low for candle in opening_range)
    latest = clean_candles[-1].close
    if gap_percent is None or abs(gap_percent) < 0.5:
        return "No material gap detected.", opening_high, opening_low
    direction = "up" if gap_percent > 0 else "down"
    filled = (latest <= prior_close if gap_percent > 0 else latest >= prior_close)
    status = "filled" if filled else "not filled"
    return f"Gap {direction} {gap_percent:+.2f}%; {status}.", opening_high, opening_low


def _regular_session_groups(candles: list[Candle]) -> dict[Any, list[Candle]]:
    groups: dict[Any, list[Candle]] = {}
    for candle in sorted(candles, key=lambda row: row.datetime_ms):
        market_dt = _market_datetime(candle)
        if not _is_regular_session_time(market_dt.time()):
            continue
        groups.setdefault(market_dt.date(), []).append(candle)
    return groups


def _opening_range_candles(session_candles: list[Candle]) -> list[Candle]:
    if not session_candles:
        return []
    first_dt = _market_datetime(session_candles[0])
    cutoff = first_dt + timedelta(minutes=30)
    opening = [candle for candle in session_candles if _market_datetime(candle) < cutoff]
    return opening or session_candles[:1]


def _extended_hours_gap_read(candles: list[Candle], session_date: Any, prior_close: float) -> str:
    extended = [
        candle
        for candle in candles
        if _market_datetime(candle).date() == session_date
        and not _is_regular_session_time(_market_datetime(candle).time())
    ]
    if not extended:
        return ""
    latest_extended = extended[-1]
    extended_percent = _percent(latest_extended.close - prior_close, prior_close)
    if extended_percent is None:
        return ""
    market_time = _market_datetime(latest_extended).time()
    label = "Premarket" if market_time < REGULAR_SESSION_START else "After-hours"
    return f"{label} read: latest extended close {_money(latest_extended.close)} is {_fmt_percent(extended_percent)} versus prior regular close."


def _is_regular_session_time(value: time) -> bool:
    return REGULAR_SESSION_START <= value <= REGULAR_SESSION_END


def _market_datetime(candle: Candle) -> datetime:
    utc_dt = datetime.fromtimestamp(candle.datetime_ms / 1000, tz=timezone.utc)
    if MARKET_TZ is not None:
        return utc_dt.astimezone(MARKET_TZ)
    return utc_dt.astimezone(_fallback_us_eastern_tz(utc_dt))


def _fallback_us_eastern_tz(utc_dt: datetime) -> timezone:
    year = utc_dt.year
    dst_start = datetime(year, 3, _nth_weekday_of_month(year, 3, 6, 2), 7, tzinfo=timezone.utc)
    dst_end = datetime(year, 11, _nth_weekday_of_month(year, 11, 6, 1), 6, tzinfo=timezone.utc)
    offset_hours = -4 if dst_start <= utc_dt < dst_end else -5
    label = "EDT" if offset_hours == -4 else "EST"
    return timezone(timedelta(hours=offset_hours), label)


def _nth_weekday_of_month(year: int, month: int, weekday: int, occurrence: int) -> int:
    first = datetime(year, month, 1)
    days_until_weekday = (weekday - first.weekday()) % 7
    return 1 + days_until_weekday + ((occurrence - 1) * 7)


def build_relative_strength_reads(
    snapshots: dict[str, TimeframeTechnicalSnapshot],
    benchmark_candles: dict[str, list[Candle]],
    *,
    symbol_candles: list[Candle],
) -> list[RelativeStrengthRead]:
    del snapshots
    reads: list[RelativeStrengthRead] = []
    for benchmark, candles in sorted(benchmark_candles.items()):
        clean_benchmark = benchmark.strip().upper()
        if not candles or not symbol_candles:
            reads.append(RelativeStrengthRead(clean_benchmark, None, None, None, None, None, None, None, None, None, "unknown", "Benchmark or symbol candles unavailable."))
            continue
        symbol_returns = {period: rate_of_change([c.close for c in symbol_candles], period) for period in (1, 5, 20)}
        benchmark_returns = {period: rate_of_change([c.close for c in candles], period) for period in (1, 5, 20)}
        spreads = {
            period: None
            if symbol_returns[period] is None or benchmark_returns[period] is None
            else symbol_returns[period] - benchmark_returns[period]
            for period in (1, 5, 20)
        }
        usable = [value for value in spreads.values() if value is not None]
        average_spread = sum(usable) / len(usable) if usable else None
        if average_spread is None:
            verdict = "unknown"
        elif average_spread >= 2.0:
            verdict = "outperforming"
        elif average_spread <= -2.0:
            verdict = "underperforming"
        else:
            verdict = "moving with"
        reason = _relative_strength_reason(clean_benchmark, spreads, verdict)
        reads.append(
            RelativeStrengthRead(
                clean_benchmark,
                symbol_returns[1],
                symbol_returns[5],
                symbol_returns[20],
                benchmark_returns[1],
                benchmark_returns[5],
                benchmark_returns[20],
                spreads[1],
                spreads[5],
                spreads[20],
                verdict,
                reason,
            )
        )
    return reads


def prc_relative_strength_score(reads: list[RelativeStrengthRead]) -> float | None:
    usable: list[float] = []
    for read in reads:
        blended = _blend_relative_strength_spreads(read)
        if blended is not None:
            usable.append(blended)
    if not usable:
        return None
    return bounded(sum(usable) / len(usable), -1.0, 1.0)


def build_prc_index_prices(
    symbol: str,
    timeframe_candles: dict[str, list[Candle]],
    *,
    quote_snapshot: QuoteSnapshot | None = None,
    relative_strength_score: float | None = None,
    benchmark_data_available: bool = False,
    ticket: TechnicalTicket | None = None,
) -> dict[str, PrcIndexPrice]:
    spec_by_key = {spec.key: spec for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES}
    prc_indexes: dict[str, PrcIndexPrice] = {}
    for key, candles in timeframe_candles.items():
        spec = spec_by_key.get(key, TimeframeSpec(key, key, "", 0, "", 0, "custom"))
        prc_indexes[key] = build_prc_index_price(
            symbol,
            spec.label,
            candles,
            quote_snapshot=quote_snapshot,
            relative_strength_score=relative_strength_score,
            benchmark_data_available=benchmark_data_available,
            ticket=ticket,
        )
    return prc_indexes


def build_prc_index_price(
    symbol: str,
    timeframe_name: str,
    candles: list[Candle],
    *,
    quote_snapshot: QuoteSnapshot | None = None,
    relative_strength_score: float | None = None,
    benchmark_data_available: bool = False,
    ticket: TechnicalTicket | None = None,
) -> PrcIndexPrice:
    clean_candles = sorted(candles, key=lambda candle: candle.datetime_ms)
    warnings: list[str] = []
    if not clean_candles:
        return PrcIndexPrice(
            symbol=symbol.strip().upper(),
            timeframe_name=timeframe_name,
            latest_price=None,
            index_price=None,
            index_distance=None,
            index_distance_percent=None,
            index_slope=None,
            confidence="Low",
            read="Unavailable",
            components=None,
            warnings=["No candles were available for PRC Pressure Line."],
            explanation_lines=["PRC Pressure Line requires at least candle data."],
            series=None,
        )

    core = _calculate_prc_index_core(clean_candles, quote_snapshot=quote_snapshot, relative_strength_score=relative_strength_score)
    latest_price = clean_candles[-1].close
    index_price = core["index_price"]
    index_distance = latest_price - index_price
    index_distance_percent = _percent(index_distance, index_price)
    series = _build_prc_series(clean_candles, quote_snapshot=quote_snapshot, relative_strength_score=relative_strength_score)
    index_slope = _prc_series_slope(series)
    volume_pressure = volume_pressure_score(clean_candles)
    atr_14 = average_true_range(clean_candles, 14)
    atr_percent = _percent(atr_14, latest_price)
    vwap_distance_percent = _percent(latest_price - (core["vwap_anchor"] or latest_price), core["vwap_anchor"] or latest_price)

    if relative_strength_score is None:
        warnings.append("Benchmark relative-strength data unavailable; PRC excludes relative-strength adjustment.")
    if quote_snapshot is None:
        warnings.append("Quote data unavailable; PRC uses candle-only spread/liquidity assumptions.")
    elif spread_percent(quote_snapshot) is None:
        warnings.append("Quote bid/ask unavailable; PRC excludes spread adjustment.")

    confidence = _prc_confidence(clean_candles, quote_snapshot, benchmark_data_available)
    read = _classify_prc_read(
        index_distance_percent=index_distance_percent,
        index_slope=index_slope,
        volume_pressure=volume_pressure,
        atr_percent=atr_percent,
        vwap_distance_percent=vwap_distance_percent,
        range_state=range_compression_read(clean_candles, atr_14),
    )
    explanation_lines = _prc_explanation_lines(
        latest_price=latest_price,
        index_price=index_price,
        index_distance_percent=index_distance_percent,
        index_slope=index_slope,
        volume_pressure=volume_pressure,
        spread=spread_percent(quote_snapshot),
        ticket=ticket,
        atr_14=atr_14,
        read=read,
    )

    return PrcIndexPrice(
        symbol=symbol.strip().upper(),
        timeframe_name=timeframe_name,
        latest_price=latest_price,
        index_price=index_price,
        index_distance=index_distance,
        index_distance_percent=index_distance_percent,
        index_slope=index_slope,
        confidence=confidence,
        read=read,
        components=core["components"],
        warnings=_dedupe(warnings),
        explanation_lines=explanation_lines,
        series=series,
    )


def build_ticket_check(snapshots: dict[str, TimeframeTechnicalSnapshot], ticket: TechnicalTicket) -> TicketCheck:
    primary = _preferred_snapshot(snapshots, "timing") or _preferred_snapshot(snapshots, "regime")
    latest = primary.latest_close if primary else None
    entry = ticket.entry_price
    stop = ticket.stop_price
    quantity = ticket.quantity
    side = ticket.side.lower() if ticket.side else "buy"
    support = _nearest_support(primary) if primary else None
    resistance = _nearest_resistance(primary) if primary else None
    atr = primary.atr_14 if primary else None
    vwap_value = primary.vwap if primary else None
    lines: list[str] = []

    if entry is None:
        lines.append("Entry: no entry/limit price was provided.")
        entry_quality = "incomplete"
    else:
        latest_text = _money(latest)
        lines.append(f"Entry: {_money(entry)} versus latest close {latest_text}.")
        if vwap_value is not None:
            lines.append(f"Entry vs VWAP: {_money(entry)} versus VWAP {_money(vwap_value)} ({_fmt_percent(_percent(entry - vwap_value, vwap_value))}).")
        if support is not None:
            lines.append(f"Entry vs support: nearest support zone {format_level(support)}.")
        if resistance is not None:
            lines.append(f"Entry vs resistance: nearest resistance zone {format_level(resistance)}.")
        entry_quality = _entry_quality(side, entry, latest, vwap_value, support, resistance)

    if stop is None:
        stop_quality = "no stop"
        lines.append("Stop: no stop price was provided, so downside risk is not defined in this read.")
    else:
        stop_quality = _stop_quality(side, entry, stop, atr, support, resistance)
        lines.append(f"Stop: {_money(stop)}. {stop_quality}.")

    risk_note = "Risk dollars unavailable; add entry, quantity, and stop."
    if entry is not None and stop is not None and quantity and quantity > 0:
        per_share = (entry - stop) if side == "buy" else (stop - entry)
        if per_share > 0:
            dollars = per_share * quantity
            portfolio_text = ""
            if ticket.portfolio_value and ticket.portfolio_value > 0:
                portfolio_text = f", {dollars / ticket.portfolio_value:.2%} of portfolio"
            risk_note = f"Estimated defined risk: ${dollars:,.2f}{portfolio_text}."
        else:
            risk_note = "Stop is on the wrong side of entry for the selected side."
    lines.append(risk_note)

    score = score_ticket(entry_quality, stop_quality, risk_note)
    verdict = _ticket_verdict(entry_quality, stop_quality, score.score)
    return TicketCheck(entry_quality, stop_quality, risk_note, verdict, lines, score)


def score_command_center(
    snapshots: dict[str, TimeframeTechnicalSnapshot],
    benchmark_reads: list[RelativeStrengthRead],
    ticket_check: TicketCheck,
) -> dict[str, ScoreComponent]:
    daily = _preferred_snapshot(snapshots, "regime")
    timing = _preferred_snapshot(snapshots, "timing")
    all_snapshots = [snapshot for snapshot in snapshots.values() if snapshot.candle_count > 0]
    trend = _average_component("Trend", [snapshot.scores.get("Trend") for snapshot in all_snapshots])
    momentum = _average_component("Momentum", [snapshot.scores.get("Momentum") for snapshot in all_snapshots])
    volume_score = _average_component("Volume", [snapshot.scores.get("Volume") for snapshot in all_snapshots])
    risk = _average_component("Volatility/Risk", [snapshot.scores.get("Volatility/Risk") for snapshot in all_snapshots])
    relative_strength = score_relative_strength(benchmark_reads)
    alignment = score_alignment(daily, timing)
    return {
        "Trend": trend,
        "Momentum": momentum,
        "Volume": volume_score,
        "Volatility/Risk": risk,
        "Relative Strength": relative_strength,
        "Alignment": alignment,
        "Ticket Quality": ticket_check.score,
    }


def score_trend(latest: float, ema_21: float | None, ema_50: float | None, sma_50: float | None, structure: str) -> ScoreComponent:
    score = 50.0
    reasons: list[str] = []
    if ema_21 is not None and latest > ema_21:
        score += 12
        reasons.append("price is above EMA 21")
    elif ema_21 is not None:
        score -= 12
        reasons.append("price is below EMA 21")
    if ema_50 is not None and ema_21 is not None and ema_21 > ema_50:
        score += 12
        reasons.append("EMA 21 is above EMA 50")
    elif ema_50 is not None and ema_21 is not None:
        score -= 12
        reasons.append("EMA 21 is below EMA 50")
    if sma_50 is not None and latest > sma_50:
        score += 8
        reasons.append("price is above SMA 50")
    elif sma_50 is not None:
        score -= 8
        reasons.append("price is below SMA 50")
    if structure == "higher-high / higher-low":
        score += 12
        reasons.append("swing structure is improving")
    elif structure == "lower-high / lower-low":
        score -= 12
        reasons.append("swing structure is deteriorating")
    return ScoreComponent("Trend", _clamp_score(score), _reason(reasons, "Trend evidence is limited."))


def score_momentum(rsi_14: float | None, histogram: float | None, histogram_change: float | None, roc_5: float | None, roc_20: float | None) -> ScoreComponent:
    score = 50.0
    reasons: list[str] = []
    if rsi_14 is not None:
        if 50 <= rsi_14 <= 68:
            score += 14
            reasons.append(f"RSI is constructive at {rsi_14:.1f}")
        elif rsi_14 > 75:
            score -= 8
            reasons.append(f"RSI is stretched at {rsi_14:.1f}")
        elif 35 <= rsi_14 < 45:
            score -= 8
            reasons.append(f"RSI is soft at {rsi_14:.1f}; this can be pullback pressure if support holds")
        elif rsi_14 < 35:
            score -= 14
            reasons.append(f"RSI is weak at {rsi_14:.1f}")
    if histogram is not None:
        if histogram > 0:
            score += 10
            reasons.append("MACD histogram is positive")
        elif histogram < 0:
            score -= 10
            reasons.append("MACD histogram is negative")
    if histogram_change is not None:
        if histogram_change > 0:
            score += 6
            reasons.append("MACD histogram is accelerating")
        elif histogram_change < 0:
            score -= 6
            reasons.append("MACD histogram is decelerating")
    for label, value in (("5-candle ROC", roc_5), ("20-candle ROC", roc_20)):
        if value is not None and value > 0:
            score += 4
            reasons.append(f"{label} is positive")
        elif value is not None and value < 0:
            score -= 4
            reasons.append(f"{label} is negative")
    return ScoreComponent("Momentum", _clamp_score(score), _reason(reasons, "Momentum evidence is limited."))


def score_volume(volume_read: VolumeRead, close_location: float | None) -> ScoreComponent:
    score = 50.0
    reasons: list[str] = []
    if volume_read.relative_volume is not None:
        if volume_read.relative_volume >= 1.25:
            score += 12
            reasons.append(f"relative volume is elevated at {volume_read.relative_volume:.2f}x")
        elif volume_read.relative_volume < 0.70:
            score -= 6
            reasons.append(f"relative volume is light at {volume_read.relative_volume:.2f}x")
    if volume_read.accumulation_read == "accumulation":
        score += 14
        reasons.append("up-volume/OBV points to accumulation")
    elif volume_read.accumulation_read == "distribution":
        score -= 14
        reasons.append("down-volume/OBV points to distribution")
    if close_location is not None:
        if close_location >= 0.40:
            score += 8
            reasons.append("latest close is near the candle high")
        elif close_location <= -0.40:
            score -= 8
            reasons.append("latest close is near the candle low")
    return ScoreComponent("Volume", _clamp_score(score), _reason(reasons, "Volume confirmation is limited."))


def score_volatility(atr_percent: float | None, range_state: str, realized_vol_20: float | None) -> ScoreComponent:
    score = 60.0
    reasons: list[str] = []
    if atr_percent is not None:
        if atr_percent <= 2.5:
            score += 10
            reasons.append(f"ATR% is contained at {atr_percent:.2f}%")
        elif atr_percent >= 8:
            score -= 18
            reasons.append(f"ATR% is hot at {atr_percent:.2f}%")
        elif atr_percent >= 5:
            score -= 8
            reasons.append(f"ATR% is elevated at {atr_percent:.2f}%")
    if range_state == "compressing":
        score += 8
        reasons.append("range is compressing, so a cleaner trigger may form")
    elif range_state == "expanding":
        score -= 8
        reasons.append("range is expanding, so chase/slippage risk is higher")
    if realized_vol_20 is not None and realized_vol_20 >= 8:
        score -= 6
        reasons.append(f"20-candle realized move intensity is high at {realized_vol_20:.1f}%")
    return ScoreComponent("Volatility/Risk", _clamp_score(score), _reason(reasons, "Volatility evidence is limited."))


def score_relative_strength(reads: list[RelativeStrengthRead]) -> ScoreComponent:
    if not reads:
        return ScoreComponent("Relative Strength", 50, "Benchmark candles were unavailable.")
    score = 50.0
    reasons: list[str] = []
    for read in reads:
        spread = read.spread_20 if read.spread_20 is not None else read.spread_5
        if spread is None:
            continue
        if spread >= 2:
            score += 8
        elif spread <= -2:
            score -= 8
        reasons.append(f"vs {read.benchmark}: {read.verdict} ({spread:+.2f}% spread)")
    return ScoreComponent("Relative Strength", _clamp_score(score), _reason(reasons, "Relative strength evidence is limited."))


def score_alignment(daily: TimeframeTechnicalSnapshot | None, timing: TimeframeTechnicalSnapshot | None) -> ScoreComponent:
    if daily is None or timing is None:
        return ScoreComponent("Alignment", 45, "Daily and intraday stack was incomplete.")
    daily_score = daily.scores.get("Trend", ScoreComponent("Trend", 50, "")).score + daily.scores.get("Momentum", ScoreComponent("Momentum", 50, "")).score
    timing_score = timing.scores.get("Trend", ScoreComponent("Trend", 50, "")).score + timing.scores.get("Momentum", ScoreComponent("Momentum", 50, "")).score
    daily_bullish = daily_score >= 110
    timing_bullish = timing_score >= 110
    daily_bearish = daily_score <= 90
    timing_bearish = timing_score <= 90
    if daily_bullish and timing_bullish:
        return ScoreComponent("Alignment", 82, "Daily regime and intraday timing both lean constructive.")
    if daily_bearish and timing_bearish:
        return ScoreComponent("Alignment", 25, "Daily regime and intraday timing both lean weak.")
    if daily_bullish and timing_bearish:
        return ScoreComponent("Alignment", 48, "Daily regime is constructive, but intraday timing is pulling back.")
    if daily_bearish and timing_bullish:
        return ScoreComponent("Alignment", 42, "Intraday timing is bouncing inside a weaker daily regime.")
    return ScoreComponent("Alignment", 55, "Timeframes are mixed rather than strongly aligned.")


def score_ticket(entry_quality: str, stop_quality: str, risk_note: str) -> ScoreComponent:
    score = 50.0
    reasons = [entry_quality, stop_quality]
    if entry_quality in {"pullback entry is better", "wait for confirmation"}:
        score -= 10
    elif entry_quality == "technically coherent":
        score += 15
    elif entry_quality == "breakout entry only":
        score -= 4
    if "inside normal noise" in stop_quality or "wrong side" in risk_note:
        score -= 22
    elif "reasonable" in stop_quality or "below support" in stop_quality or "above resistance" in stop_quality:
        score += 18
    elif "very wide" in stop_quality:
        score -= 6
    elif "no stop" in stop_quality:
        score -= 20
    return ScoreComponent("Ticket Quality", _clamp_score(score), "; ".join(reason for reason in reasons if reason))


def format_technical_command_center_report(report: TechnicalCommandCenterReport) -> str:
    daily = _preferred_snapshot(report.snapshots, "regime")
    setup = _preferred_snapshot(report.snapshots, "setup")
    timing = _preferred_snapshot(report.snapshots, "timing")
    lines = [
        f"TECHNICAL COMMAND CENTER - {report.symbol}",
        "=" * (29 + len(report.symbol)),
        "",
        "EXECUTIVE READ",
        f"- Overall technical read: {report.overall_read} ({report.overall_score:.0f}/100).",
        f"- Regime: {_short_snapshot_read(daily)}",
        f"- Setup: {_short_snapshot_read(setup)}",
        f"- Timing: {_short_snapshot_read(timing)}",
        f"- Risk heat: {report.scores['Volatility/Risk'].score:.0f}/100; {report.scores['Volatility/Risk'].reason}.",
        f"- Best action: {report.best_action}.",
        f"- Confidence: {report.confidence}.",
        "- This is analysis, not a trade recommendation.",
    ]
    lines.extend(_format_prc_report_section(report, daily, setup, timing))
    if report.capital_structure_pressure is not None:
        lines.extend(
            format_capital_structure_pressure_section(
                report.capital_structure_pressure,
                technical_read=report.overall_read,
            )
        )
    lines.extend(["", "SCORE BREAKDOWN"])
    for name, component in report.scores.items():
        lines.append(f"- {name}: {component.score:.0f}/100 because {component.reason}.")
    lines.extend(["", "TIMEFRAME STACK"])
    for snapshot in report.snapshots.values():
        lines.append(f"- {snapshot.label}: {_short_snapshot_read(snapshot)}")
    lines.append(f"- Alignment: {report.scores['Alignment'].reason}.")

    lines.extend(["", "KEY LEVELS"])
    level_snapshot = timing or setup or daily
    if level_snapshot is None:
        lines.append("- No levels available.")
    else:
        support = _nearest_support(level_snapshot)
        resistance = _nearest_resistance(level_snapshot)
        lines.append(f"- Nearest support zone: {format_level(support)}.")
        lines.append(f"- Nearest resistance zone: {format_level(resistance)}.")
        lines.append(f"- Breakout trigger: {_money(resistance.center if resistance else None)}.")
        lines.append(f"- Breakdown / invalidation: {_money(support.center if support else None)}.")
        lines.append(f"- ATR(14): {_money(level_snapshot.atr_14)} / ATR%: {_fmt_percent(level_snapshot.atr_percent)}.")
        lines.append(f"- {_vwap_summary(level_snapshot)}")

    lines.extend(["", "VOLUME / PARTICIPATION"])
    volume_snapshot = timing or daily
    if volume_snapshot is None:
        lines.append("- Volume read unavailable.")
    else:
        vr = volume_snapshot.volume_read
        lines.append(f"- Relative volume: {_format_optional(vr.relative_volume, suffix='x')}.")
        lines.append(f"- Up/down volume read: {_format_optional(vr.up_down_volume_ratio)}; {vr.accumulation_read}.")
        lines.append(f"- Accumulation/distribution read: {vr.reason}.")

    lines.extend(["", "RELATIVE STRENGTH"])
    if not report.benchmark_reads:
        lines.append("- Benchmark reads unavailable.")
    for read in report.benchmark_reads:
        lines.append(f"- vs {read.benchmark}: {read.reason}.")
    rs_summary = _relative_strength_summary(report.benchmark_reads, report.symbol)
    if rs_summary:
        lines.append(f"- Summary: {rs_summary}")

    lines.extend(["", "TICKET CHECK"])
    lines.extend(f"- {line}" for line in report.ticket_check.lines)
    lines.append(f"- Entry quality: {report.ticket_check.entry_quality}.")
    lines.append(f"- Stop quality: {report.ticket_check.stop_quality}.")
    lines.append(f"- Verdict: {report.ticket_check.verdict}.")

    lines.extend(["", "PLAIN-ENGLISH PLAN"])
    if report.capital_structure_pressure is not None:
        lines.append(
            f"- {capital_structure_technical_modifier(report.overall_read, report.capital_structure_pressure)}"
        )
    lines.extend(f"- {line}" for line in report.plain_english_plan)
    if report.warnings:
        lines.extend(["", "DATA WARNINGS"])
        lines.extend(f"- {warning}" for warning in report.warnings)
    return "\n".join(lines)


def _format_prc_report_section(
    report: TechnicalCommandCenterReport,
    daily: TimeframeTechnicalSnapshot | None,
    setup: TimeframeTechnicalSnapshot | None,
    timing: TimeframeTechnicalSnapshot | None,
) -> list[str]:
    if not report.prc_indexes:
        return [
            "",
        "PRC PRESSURE LINE",
        "---------------",
        "- PRC Pressure Line unavailable.",
        "- PRC Pressure Line is a synthetic internal indicator, not an official exchange price or target price.",
        ]

    primary_snapshot = timing or setup or daily
    primary = _preferred_prc_index(report.prc_indexes, primary_snapshot)
    if primary is None:
        return [
            "",
            "PRC PRESSURE LINE",
            "---------------",
            "- PRC Pressure Line unavailable.",
            "- PRC Pressure Line is a synthetic internal indicator, not an official exchange price or target price.",
        ]

    lines = [
        "",
        "PRC PRESSURE LINE",
        "---------------",
        "- PRC Pressure Line is a synthetic internal indicator, not an official exchange price or target price.",
        f"- Timeframe: {primary.timeframe_name}.",
        f"- Latest price: {_money(primary.latest_price)}.",
        f"- PRC Pressure Line: {_money(primary.index_price)}.",
        f"- Price vs PRC Pressure Line: {_fmt_percent(primary.index_distance_percent)}.",
        f"- PRC Pressure Line slope: {_prc_slope_label(primary.index_slope)}.",
        f"- PRC read: {primary.read}.",
        f"- Confidence: {primary.confidence}.",
    ]
    for explanation in primary.explanation_lines:
        lines.append(f"- Why: {explanation}")
    for warning in primary.warnings:
        lines.append(f"- Data note: {warning}")

    if primary.components is not None:
        components = primary.components
        lines.extend(
            [
                "",
                "PRC COMPONENTS",
                "--------------",
                f"- Close anchor: {_money(components.close_component)}.",
                f"- VWAP anchor: {_money(components.vwap_component)}.",
                f"- Typical-price anchor: {_money(components.typical_price_component)}.",
                f"- Volume pressure: {_signed_money(components.volume_pressure_component)}.",
                f"- Close-location pressure: {_signed_money(components.close_location_adjustment)}.",
                f"- Trend adjustment: {_signed_money(components.trend_slope_adjustment)}.",
                f"- Spread adjustment: {_signed_money(components.spread_adjustment)}.",
                f"- Relative-strength adjustment: {_signed_money(components.relative_strength_adjustment)}.",
                f"- Volatility clamp: {_signed_money(components.volatility_adjustment)}.",
            ]
        )

    comparison = _prc_timeframe_comparison(report.prc_indexes)
    if comparison:
        lines.extend(["", "PRC PRESSURE-LINE TIMEFRAME READ"])
        lines.extend(f"- {line}" for line in comparison)

    lines.extend(
        [
            "",
            "PLAIN-ENGLISH USE",
            "-----------------",
            "- Above a rising PRC Pressure Line = constructive momentum.",
            "- Far above the PRC Pressure Line = possible chase risk.",
            "- Below a rising PRC Pressure Line = pullback/reclaim setup.",
            "- Below a falling PRC Pressure Line = weak tape.",
            "- This internal synthetic indicator is for confirmation/non-confirmation only.",
        ]
    )
    return lines


def _preferred_prc_index(
    prc_indexes: dict[str, PrcIndexPrice],
    snapshot: TimeframeTechnicalSnapshot | None,
) -> PrcIndexPrice | None:
    if snapshot is not None:
        prc = prc_indexes.get(snapshot.key)
        if prc is not None and prc.index_price is not None:
            return prc
    for key in ("timing_5m", "timing_1m", "setup_30m", "daily_1y"):
        prc = prc_indexes.get(key)
        if prc is not None and prc.index_price is not None:
            return prc
    return next((prc for prc in prc_indexes.values() if prc.index_price is not None), None)


def _prc_timeframe_comparison(prc_indexes: dict[str, PrcIndexPrice]) -> list[str]:
    daily = prc_indexes.get("daily_1y")
    timing = prc_indexes.get("timing_5m") or prc_indexes.get("timing_1m")
    if daily is None or timing is None or daily.index_price is None or timing.index_price is None:
        return []
    daily_slope = daily.index_slope or 0.0
    timing_distance = timing.index_distance_percent or 0.0
    daily_distance = daily.index_distance_percent or 0.0
    lines: list[str] = []
    if daily_slope > 0.10 and timing_distance < -0.50:
        lines.append("Daily PRC Pressure Line is rising while intraday trades below its pressure line: constructive pullback; wait for reclaim.")
    elif daily_slope < -0.10 and timing_distance > 0.50:
        lines.append("Daily PRC Pressure Line is falling while intraday is above its pressure line: short-term bounce against weak regime.")
    elif daily_slope > 0.10 and (timing.index_slope or 0.0) > 0.10 and daily_distance >= -0.25 and timing_distance >= -0.25:
        lines.append("Daily and intraday PRC Pressure Lines are rising with price reclaiming both: confirmed technical pressure.")
    elif daily_distance > 2.0 and timing_distance > 2.0:
        lines.append("Price is far above both PRC Pressure Lines: strength is present, but chase risk is elevated.")
    else:
        lines.append("Daily and intraday PRC Pressure Lines are mixed; use reclaim/loss of the pressure line as confirmation.")
    return lines


def _daily_candles_from_timeframes(timeframe_candles: dict[str, list[Candle]]) -> list[Candle]:
    for key in ("daily_1y", "daily", "regime"):
        candles = timeframe_candles.get(key)
        if candles:
            return candles
    for key, candles in timeframe_candles.items():
        if "daily" in key and candles:
            return candles
    return next((candles for candles in timeframe_candles.values() if candles), [])


def _calculate_prc_index_core(
    candles: list[Candle],
    *,
    quote_snapshot: QuoteSnapshot | None,
    relative_strength_score: float | None,
) -> dict[str, Any]:
    latest = candles[-1]
    latest_close = latest.close
    latest_vwap = _last(rolling_vwap(candles, min(20, len(candles)))) if candles else None
    vwap_anchor = latest_vwap if latest_vwap is not None else latest_close
    latest_typical = typical_price(latest)

    # The PRC Pressure Line is intentionally transparent: a close/VWAP/typical-price anchor plus
    # bounded pressure adjustments. It is an internal technical reference only.
    close_component = 0.55 * latest_close
    vwap_component = 0.35 * vwap_anchor
    typical_price_component = 0.10 * latest_typical
    raw_index = close_component + vwap_component + typical_price_component

    volume_pressure = volume_pressure_score(candles)
    latest_close_location = close_location_value(latest) or 0.0
    short_roc = rate_of_change([candle.close for candle in candles], 5)
    spread = spread_percent(quote_snapshot)
    atr_14 = average_true_range(candles, 14)
    atr_percent = _percent(atr_14, latest_close)

    volume_adjustment = latest_close * 0.004 * volume_pressure
    close_location_adjustment = latest_close * 0.002 * latest_close_location
    trend_adjustment = latest_close * 0.003 * bounded((short_roc or 0.0) / 5.0, -1.0, 1.0)
    relative_strength_adjustment = latest_close * 0.003 * bounded(relative_strength_score or 0.0, -1.0, 1.0)
    spread_adjustment = 0.0
    if spread is not None:
        spread_adjustment = -latest_close * 0.0015 * bounded((spread - 0.25) / 1.5, 0.0, 1.0)

    pre_volatility_index = (
        raw_index
        + volume_adjustment
        + close_location_adjustment
        + trend_adjustment
        + relative_strength_adjustment
        + spread_adjustment
    )
    volatility_adjustment = 0.0
    if atr_percent is not None and atr_percent > 6.0:
        volatility_adjustment = (latest_close - pre_volatility_index) * bounded((atr_percent - 6.0) / 8.0, 0.0, 0.50)

    unclamped_index = pre_volatility_index + volatility_adjustment
    clamp_band = (2.5 * atr_14) if atr_14 is not None and atr_14 > 0 else latest_close * 0.08
    index_price = bounded(unclamped_index, latest_close - clamp_band, latest_close + clamp_band)
    volatility_adjustment += index_price - unclamped_index

    return {
        "index_price": index_price,
        "vwap_anchor": vwap_anchor,
        "components": PrcIndexComponents(
            close_component=close_component,
            vwap_component=vwap_component,
            typical_price_component=typical_price_component,
            volume_pressure_component=volume_adjustment,
            volatility_adjustment=volatility_adjustment,
            spread_adjustment=spread_adjustment,
            relative_strength_adjustment=relative_strength_adjustment,
            close_location_adjustment=close_location_adjustment,
            trend_slope_adjustment=trend_adjustment,
        ),
    }


def _build_prc_series(
    candles: list[Candle],
    *,
    quote_snapshot: QuoteSnapshot | None,
    relative_strength_score: float | None,
) -> PrcIndexSeries | None:
    if len(candles) < 8:
        return None
    start = max(5, len(candles) - 30)
    points: list[PrcIndexSeriesPoint] = []
    for end in range(start, len(candles) + 1):
        point_candles = candles[:end]
        core = _calculate_prc_index_core(point_candles, quote_snapshot=quote_snapshot, relative_strength_score=relative_strength_score)
        points.append(PrcIndexSeriesPoint(point_candles[-1].datetime_ms, core["index_price"]))
    return PrcIndexSeries(points)


def _prc_series_slope(series: PrcIndexSeries | None) -> float | None:
    if series is None or len(series.points) < 6:
        return None
    prior = series.points[-6].index_price
    latest = series.points[-1].index_price
    return _percent(latest - prior, prior)


def _prc_confidence(candles: list[Candle], quote_snapshot: QuoteSnapshot | None, benchmark_data_available: bool) -> str:
    has_volume = relative_volume(candles) is not None and any(candle.volume > 0 for candle in candles)
    has_spread = spread_percent(quote_snapshot) is not None
    if len(candles) >= 60 and has_volume and has_spread and benchmark_data_available:
        return "High"
    if len(candles) >= 20 and has_volume:
        return "Medium"
    return "Low"


def _classify_prc_read(
    *,
    index_distance_percent: float | None,
    index_slope: float | None,
    volume_pressure: float,
    atr_percent: float | None,
    vwap_distance_percent: float | None,
    range_state: str,
) -> str:
    distance = index_distance_percent or 0.0
    slope = index_slope or 0.0
    atr_threshold = max(1.0, min(4.0, (atr_percent or 1.0) * 0.75))
    far_above = distance > max(2.0, atr_threshold * 1.25)
    far_below = distance < -max(1.0, atr_threshold)

    if far_above or (vwap_distance_percent is not None and vwap_distance_percent > 4.0):
        return "Chasing / extended"
    if far_below and slope > 0.10 and volume_pressure > -0.55:
        return "Pullback opportunity"
    if distance < -0.50 and slope < -0.10 and volume_pressure < -0.20:
        return "Distribution / weak"
    if abs(distance) <= 0.60 and (range_state == "compressing" or abs(volume_pressure) < 0.20):
        return "Compression / wait"
    if distance >= -0.50 and slope > 0.10 and volume_pressure > 0.15:
        return "Accumulation / constructive"
    if distance < -0.50:
        return "Distribution / weak" if volume_pressure < -0.10 else "Pullback opportunity"
    return "Compression / wait"


def _prc_explanation_lines(
    *,
    latest_price: float,
    index_price: float,
    index_distance_percent: float | None,
    index_slope: float | None,
    volume_pressure: float,
    spread: float | None,
    ticket: TechnicalTicket | None,
    atr_14: float | None,
    read: str,
) -> list[str]:
    lines = [
        "PRC Pressure Line is a synthetic internal indicator, not an official exchange price or target price.",
        "Formula: 55% close anchor, 35% VWAP anchor, 10% typical price, then bounded pressure adjustments.",
        f"Price is {_fmt_percent(index_distance_percent)} versus the PRC Pressure Line; slope is {_prc_slope_label(index_slope)}.",
        f"Volume pressure score is {volume_pressure:+.2f}; read is {read}.",
    ]
    if spread is None:
        lines.append("Spread: unavailable; liquidity adjustment is excluded.")
    else:
        lines.append(f"Spread: {spread:.2f}%; liquidity is {_spread_read(spread)}.")
    lines.extend(_prc_ticket_lines(ticket, latest_price, index_price, index_distance_percent, index_slope, atr_14))
    return lines


def _prc_ticket_lines(
    ticket: TechnicalTicket | None,
    latest_price: float,
    index_price: float,
    index_distance_percent: float | None,
    index_slope: float | None,
    atr_14: float | None,
) -> list[str]:
    if ticket is None:
        return []
    lines: list[str] = []
    entry = ticket.entry_price
    stop = ticket.stop_price
    slope = index_slope or 0.0
    if entry is not None:
        entry_vs_prc = _percent(entry - index_price, index_price)
        if entry_vs_prc is not None and entry_vs_prc < -0.75 and slope >= 0:
            lines.append("Entry is below the PRC Pressure Line: favorable pullback entry if trend confirms.")
        elif entry_vs_prc is not None and abs(entry_vs_prc) <= 0.75:
            lines.append("Entry is near the PRC Pressure Line: balanced versus the synthetic pressure anchor.")
        elif entry_vs_prc is not None and entry_vs_prc > 1.50:
            lines.append("Entry is far above the PRC Pressure Line: chase risk is elevated.")
        elif entry_vs_prc is not None and entry_vs_prc < -0.75 and slope < 0:
            lines.append("Entry is below a falling PRC Pressure Line: discount may be weakness, not opportunity.")
        else:
            lines.append(f"Entry is {_fmt_percent(entry_vs_prc)} from the PRC Pressure Line.")
    if stop is not None:
        stop_gap = abs(latest_price - stop)
        if atr_14 is not None and atr_14 > 0 and stop_gap < atr_14 * 0.70:
            lines.append("Stop is inside normal PRC Pressure Line/ATR noise.")
        elif stop < index_price:
            lines.append("Stop is below the current PRC Pressure Line.")
        elif atr_14 is not None and atr_14 > 0 and stop_gap > atr_14 * 3:
            lines.append("Stop is wide relative to current technical risk.")
    return lines


def _blend_relative_strength_spreads(read: RelativeStrengthRead) -> float | None:
    values: list[tuple[float, float]] = []
    if read.spread_5 is not None:
        values.append((read.spread_5, 0.45))
    if read.spread_20 is not None:
        values.append((read.spread_20, 0.45))
    if read.spread_1 is not None:
        values.append((read.spread_1, 0.10))
    if not values:
        return None
    total_weight = sum(weight for _, weight in values)
    blended_spread = sum(value * weight for value, weight in values) / total_weight
    return bounded(blended_spread / 8.0, -1.0, 1.0)


def _prc_slope_label(index_slope: float | None) -> str:
    if index_slope is None:
        return "unavailable"
    if index_slope > 0.10:
        return f"rising ({index_slope:+.2f}%)"
    if index_slope < -0.10:
        return f"falling ({index_slope:+.2f}%)"
    return f"flat ({index_slope:+.2f}%)"


def _spread_read(value: float) -> str:
    if value <= 0.20:
        return "tight"
    if value <= 0.75:
        return "normal"
    return "wide"


def _quote_payload_for_symbol(symbol: str, payload: Any) -> Any:
    if not isinstance(payload, dict):
        return None
    candidates = [symbol, symbol.upper(), symbol.lower()]
    for key in candidates:
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    if len(payload) == 1:
        only_value = next(iter(payload.values()))
        if isinstance(only_value, dict):
            return only_value
    if any(key in payload for key in ("quote", "regular", "extended", "reference", "bidPrice", "askPrice", "lastPrice")):
        return payload
    return None


def _first_nested_number(payload: Any, keys: tuple[str, ...]) -> float | None:
    if not isinstance(payload, dict):
        return None
    stack: list[Any] = [payload]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if not isinstance(current, dict):
            continue
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        for key in keys:
            if key in current:
                value = _optional_number(current.get(key))
                if value is not None:
                    return value
        for preferred in ("quote", "regular", "extended", "reference"):
            child = current.get(preferred)
            if isinstance(child, dict):
                stack.append(child)
        for child in current.values():
            if isinstance(child, dict):
                stack.append(child)
    return None


def _optional_number(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def _pivot_values(candles: list[Candle], kind: str) -> list[float]:
    if len(candles) < 5:
        values = [candle.low if kind == "low" else candle.high for candle in candles]
        return values
    pivots: list[float] = []
    for index in range(2, len(candles) - 2):
        window = candles[index - 2:index + 3]
        value = candles[index].low if kind == "low" else candles[index].high
        compare = [candle.low if kind == "low" else candle.high for candle in window]
        if kind == "low" and value <= min(compare):
            pivots.append(value)
        if kind == "high" and value >= max(compare):
            pivots.append(value)
    return pivots


def _cluster_levels(values: list[float], tolerance: float, kind: str, latest: float) -> list[TechnicalLevel]:
    if not values:
        return []
    clusters: list[list[float]] = []
    for value in sorted(values):
        if not clusters or abs(value - (sum(clusters[-1]) / len(clusters[-1]))) > tolerance:
            clusters.append([value])
        else:
            clusters[-1].append(value)
    levels: list[TechnicalLevel] = []
    for cluster in clusters:
        if len(cluster) < 1:
            continue
        low = min(cluster)
        high = max(cluster)
        center = sum(cluster) / len(cluster)
        touches = len(cluster)
        distance = abs(center - latest)
        reason = f"{touches} pivot touch{'es' if touches != 1 else ''}; {distance / max(latest, 0.01):.1%} from latest."
        levels.append(TechnicalLevel(kind, low, high, center, reason))
    return levels


def _snapshot_lines(
    *,
    label: str,
    latest_close: float,
    ema_21: float | None,
    ema_50: float | None,
    rsi_14: float | None,
    histogram: float | None,
    macd_histogram_change: float | None,
    atr_14: float | None,
    atr_percent: float | None,
    support_zones: list[TechnicalLevel],
    resistance_zones: list[TechnicalLevel],
    trend_structure: str,
    range_state: str,
    volume_read: VolumeRead,
    vwap_value: float | None,
    vwap_distance_percent: float | None,
    session_vwap: float | None,
    rolling_vwap_20: float | None,
    multi_day_vwap: float | None,
    realized_move_20_pct: float | None,
    annualized_realized_vol_20: float | None,
) -> list[str]:
    support = _nearest_level(support_zones)
    resistance = _nearest_level(resistance_zones)
    lines = [
        f"{label}: latest close {_money(latest_close)}.",
        f"Trend: {trend_structure}; EMA21 {_money(ema_21)}, EMA50 {_money(ema_50)}.",
        f"Momentum: RSI {_format_optional(rsi_14)}, MACD histogram {_format_optional(histogram)}, histogram change {_format_optional(macd_histogram_change)}.",
        f"Risk: ATR {_money(atr_14)} ({_fmt_percent(atr_percent)}); 20-candle realized move {_fmt_percent(realized_move_20_pct)}; annualized realized vol {_fmt_percent(annualized_realized_vol_20)}; range is {range_state}.",
        f"Levels: support {format_level(support)}, resistance {format_level(resistance)}.",
        f"Volume: {volume_read.accumulation_read}; {volume_read.reason}",
    ]
    vwap_parts: list[str] = []
    if session_vwap is not None:
        vwap_parts.append(f"session {_money(session_vwap)}")
    if rolling_vwap_20 is not None:
        vwap_parts.append(f"rolling 20-bar {_money(rolling_vwap_20)}")
    if multi_day_vwap is not None:
        vwap_parts.append(f"multi-day {_money(multi_day_vwap)}")
    if vwap_parts:
        lines.append(f"VWAP anchors: {', '.join(vwap_parts)}; price is {_fmt_percent(vwap_distance_percent)} from active VWAP.")
    elif vwap_value is not None:
        lines.append(f"VWAP: {_money(vwap_value)}; price is {_fmt_percent(vwap_distance_percent)} from VWAP.")
    return lines


def _relative_strength_reason(benchmark: str, spreads: dict[int, float | None], verdict: str) -> str:
    parts = []
    for period in (1, 5, 20):
        spread = spreads.get(period)
        if spread is not None:
            parts.append(f"{period}d spread {spread:+.2f}%")
    spread_text = ", ".join(parts) if parts else "spreads unavailable"
    return f"{verdict} {benchmark}; {spread_text}"


def _relative_strength_summary(reads: list[RelativeStrengthRead], symbol: str) -> str:
    usable = [read for read in reads if read.verdict != "unknown"]
    if not usable:
        return ""
    outperforming = [read.benchmark for read in usable if read.verdict == "outperforming"]
    underperforming = [read.benchmark for read in usable if read.verdict == "underperforming"]
    if len(outperforming) == len(usable):
        return f"{symbol} is outperforming SPY/QQQ/IWM, confirming stock-specific strength."
    if "IWM" in [read.benchmark for read in usable if read.verdict in {"outperforming", "moving with"}] and len(underperforming) < len(usable):
        return f"{symbol} is moving with small-cap beta more than mega-cap leadership."
    if len(underperforming) == len(usable):
        return f"{symbol} is underperforming the benchmark stack; confirmation is weak."
    return f"{symbol} has mixed benchmark confirmation."


def _preferred_snapshot(snapshots: dict[str, TimeframeTechnicalSnapshot], role: str) -> TimeframeTechnicalSnapshot | None:
    role_matches = [snapshot for snapshot in snapshots.values() if snapshot.role == role and snapshot.candle_count > 0]
    if role_matches:
        return sorted(role_matches, key=lambda snapshot: snapshot.candle_count, reverse=True)[0]
    key_order = {
        "regime": ("daily_1y", "daily"),
        "setup": ("setup_30m", "setup_15m"),
        "timing": ("timing_5m", "timing_1m", "intraday"),
    }.get(role, ())
    for key in key_order:
        snapshot = snapshots.get(key)
        if snapshot and snapshot.candle_count > 0:
            return snapshot
    return None


def _nearest_support(snapshot: TimeframeTechnicalSnapshot | None) -> TechnicalLevel | None:
    return _nearest_level(snapshot.support_zones if snapshot else [])


def _nearest_resistance(snapshot: TimeframeTechnicalSnapshot | None) -> TechnicalLevel | None:
    return _nearest_level(snapshot.resistance_zones if snapshot else [])


def _nearest_level(levels: list[TechnicalLevel]) -> TechnicalLevel | None:
    return levels[0] if levels else None


def _entry_quality(
    side: str,
    entry: float,
    latest: float | None,
    vwap_value: float | None,
    support: TechnicalLevel | None,
    resistance: TechnicalLevel | None,
) -> str:
    if latest is None:
        return "incomplete"
    extended_from_vwap = vwap_value is not None and abs(_percent(entry - vwap_value, vwap_value) or 0) >= 3.0
    if side == "buy":
        if resistance is not None and entry >= resistance.center:
            return "breakout entry only"
        if support is not None and entry <= support.high * 1.02:
            return "technically coherent"
        if extended_from_vwap and entry > latest:
            return "pullback entry is better"
        if entry > latest * 1.03:
            return "wait for confirmation"
    else:
        if support is not None and entry <= support.center:
            return "breakdown entry only"
        if resistance is not None and entry >= resistance.low * 0.98:
            return "technically coherent"
        if extended_from_vwap and entry < latest:
            return "pullback entry is better"
    return "watch"


def _stop_quality(
    side: str,
    entry: float | None,
    stop: float,
    atr: float | None,
    support: TechnicalLevel | None,
    resistance: TechnicalLevel | None,
) -> str:
    if entry is None:
        return "stop present, but entry missing"
    per_share = entry - stop if side == "buy" else stop - entry
    if per_share <= 0:
        return "stop is on the wrong side of entry"
    if atr is not None and atr > 0:
        atr_multiple = per_share / atr
        if atr_multiple < 0.7:
            return "stop is likely inside normal noise"
        if atr_multiple > 3.0:
            return "stop is very wide versus ATR"
    if side == "buy" and support is not None:
        if stop < support.low:
            return "reasonable; below support"
        if support.low <= stop <= support.high:
            return "inside support/noise"
    if side != "buy" and resistance is not None:
        if stop > resistance.high:
            return "reasonable; above resistance"
        if resistance.low <= stop <= resistance.high:
            return "inside resistance/noise"
    return "reasonable versus ATR"


def _ticket_verdict(entry_quality: str, stop_quality: str, score: float) -> str:
    if "inside normal noise" in stop_quality:
        return "Stop is likely inside normal noise."
    if score >= 70:
        return "Risk is defined and technically coherent."
    if entry_quality in {"pullback entry is better", "watch"}:
        return "Pullback entry is better."
    if "breakout" in entry_quality:
        return "Wait for breakout confirmation."
    if "no stop" in stop_quality:
        return "Wait for confirmation; risk is not defined."
    return "Wait for confirmation."


def _average_component(name: str, components: list[ScoreComponent | None]) -> ScoreComponent:
    clean = [component for component in components if component is not None]
    if not clean:
        return ScoreComponent(name, 50, f"{name.lower()} evidence was unavailable.")
    score = sum(component.score for component in clean) / len(clean)
    reasons = "; ".join(component.reason for component in clean[:2])
    return ScoreComponent(name, _clamp_score(score), reasons)


def _weighted_score(scores: dict[str, ScoreComponent], weights: dict[str, float]) -> float:
    total_weight = 0.0
    total = 0.0
    for name, weight in weights.items():
        component = scores.get(name)
        if component is None:
            continue
        total += component.score * weight
        total_weight += weight
    if total_weight <= 0:
        return 50.0
    return _clamp_score(total / total_weight)


def _overall_read(score: float, scores: dict[str, ScoreComponent]) -> str:
    risk_score = scores.get("Volatility/Risk", ScoreComponent("", 50, "")).score
    ticket_score = scores.get("Ticket Quality", ScoreComponent("", 50, "")).score
    if score >= 72 and risk_score >= 45:
        return "Bullish"
    if score <= 38:
        return "Bearish"
    if ticket_score < 45 or risk_score < 42:
        return "Watch"
    return "Mixed"


def _confidence_label(
    snapshots: dict[str, TimeframeTechnicalSnapshot],
    benchmark_reads: list[RelativeStrengthRead],
    warnings: list[str],
) -> str:
    usable_timeframes = sum(1 for snapshot in snapshots.values() if snapshot.candle_count >= 35)
    usable_benchmarks = sum(1 for read in benchmark_reads if read.verdict != "unknown")
    if usable_timeframes >= 3 and usable_benchmarks >= 2 and not warnings:
        return "High"
    if usable_timeframes >= 2:
        return "Medium"
    return "Low"


def _best_action(overall_read: str, snapshots: dict[str, TimeframeTechnicalSnapshot], ticket_check: TicketCheck) -> str:
    timing = _preferred_snapshot(snapshots, "timing")
    if "inside normal noise" in ticket_check.stop_quality:
        return "Wait / widen or relocate stop"
    if ticket_check.entry_quality in {"pullback entry is better", "watch"}:
        return "Pullback / wait"
    if ticket_check.entry_quality in {"breakout entry only", "breakdown entry only"}:
        return "Breakout trigger only"
    if timing and timing.vwap_distance_percent is not None and timing.vwap_distance_percent > 4:
        return "Avoid chase"
    if overall_read == "Bullish":
        return "Defined-risk long only if trigger holds"
    if overall_read == "Bearish":
        return "Avoid or hedge"
    return "Wait"


def _build_action_triggers(snapshots: dict[str, TimeframeTechnicalSnapshot]) -> list[ActionTrigger]:
    snapshot = _preferred_snapshot(snapshots, "timing") or _preferred_snapshot(snapshots, "setup") or _preferred_snapshot(snapshots, "regime")
    if snapshot is None:
        return [ActionTrigger("Data needed", None, "No price-history snapshot was available.")]
    support = _nearest_support(snapshot)
    resistance = _nearest_resistance(snapshot)
    triggers = [
        ActionTrigger("Breakout trigger", resistance.center if resistance else None, "Move above nearby resistance improves confirmation."),
        ActionTrigger("Pullback zone", support.high if support else None, "Constructive pullback is cleaner near support if buyers defend it."),
        ActionTrigger("Invalidation", support.low if support else None, "Break below support weakens the setup."),
    ]
    return triggers


def _plain_english_plan(
    overall_read: str,
    snapshots: dict[str, TimeframeTechnicalSnapshot],
    ticket_check: TicketCheck,
    triggers: list[ActionTrigger],
) -> list[str]:
    breakout = next((trigger for trigger in triggers if trigger.label == "Breakout trigger"), None)
    invalidation = next((trigger for trigger in triggers if trigger.label == "Invalidation"), None)
    pullback = next((trigger for trigger in triggers if trigger.label == "Pullback zone"), None)
    daily = _preferred_snapshot(snapshots, "regime")
    plan: list[str] = []
    if overall_read == "Bullish":
        plan.append(f"If bullish: prefer confirmation above {_money(breakout.price if breakout else None)} or a controlled pullback near {_money(pullback.price if pullback else None)}.")
    else:
        plan.append(f"If bullish: require a reclaim of {_money(breakout.price if breakout else None)} before assuming momentum has confirmation.")
    plan.append(f"If bearish: losing {_money(invalidation.price if invalidation else None)} weakens the setup and argues for smaller risk or no new risk.")
    if daily is not None:
        plan.append(f"What would change the view: daily trend/EMA alignment changes, volume confirms the opposite direction, or price rejects the key trigger.")
    plan.append(f"Ticket verdict: {ticket_check.verdict}")
    return plan


def _short_snapshot_read(snapshot: TimeframeTechnicalSnapshot | None) -> str:
    if snapshot is None or snapshot.latest_close is None:
        return "unavailable"
    trend = snapshot.scores.get("Trend", ScoreComponent("Trend", 50, "")).score
    momentum = snapshot.scores.get("Momentum", ScoreComponent("Momentum", 50, "")).score
    label = "constructive" if trend >= 62 and momentum >= 55 else "weak" if trend <= 40 and momentum <= 45 else "mixed"
    return f"{snapshot.label} {label}; close {_money(snapshot.latest_close)}, RSI {_format_optional(snapshot.rsi_14)}, ATR% {_fmt_percent(snapshot.atr_percent)}"


def format_level(level: TechnicalLevel | None) -> str:
    if level is None:
        return "--"
    if abs(level.high - level.low) < 0.005:
        return f"{_money(level.center)} ({level.reason})"
    return f"{_money(level.low)}-{_money(level.high)} ({level.reason})"


def _vwap_summary(snapshot: TimeframeTechnicalSnapshot) -> str:
    parts: list[str] = []
    if snapshot.session_vwap is not None:
        parts.append(f"session VWAP {_money(snapshot.session_vwap)}")
    if snapshot.rolling_vwap_20 is not None:
        parts.append(f"rolling 20-bar VWAP {_money(snapshot.rolling_vwap_20)}")
    if snapshot.multi_day_vwap is not None:
        parts.append(f"multi-day VWAP {_money(snapshot.multi_day_vwap)}")
    if not parts and snapshot.vwap is not None:
        parts.append(f"VWAP {_money(snapshot.vwap)}")
    if not parts:
        return "VWAP anchors unavailable."
    return f"{'; '.join(parts)} / price vs active VWAP: {_fmt_percent(snapshot.vwap_distance_percent)}."


def _format_optional(value: float | None, *, suffix: str = "") -> str:
    if value is None:
        return "--"
    return f"{value:,.2f}{suffix}"


def _fmt_percent(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2f}%"


def _money(value: float | None) -> str:
    return "--" if value is None else f"${value:,.2f}"


def _signed_money(value: float | None) -> str:
    return "--" if value is None else f"{value:+,.2f}"


def _percent(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return (numerator / denominator) * 100


def _last(values: list[float | None]) -> float | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _clamp_score(score: float) -> float:
    return max(0.0, min(100.0, score))


def _reason(reasons: list[str], fallback: str) -> str:
    return "; ".join(reasons) if reasons else fallback


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


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
