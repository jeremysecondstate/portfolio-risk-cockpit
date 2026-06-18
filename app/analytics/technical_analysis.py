from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from enum import Enum
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.analytics.capital_structure_pressure import (
    CapitalStructurePressureReport,
    capital_structure_technical_modifier,
    format_capital_structure_pressure_section,
)
from app.data.technical_source_routing import SourceDecision, TechnicalAnalysisDataPlan


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
class TimeframeSourceRead:
    key: str
    label: str
    source: str
    freshness: str
    candle_count: int
    reason: str


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
class LevelProximityRead:
    nearest_support: TechnicalLevel | None
    nearest_resistance: TechnicalLevel | None
    distance_to_support_percent: float | None
    distance_to_resistance_percent: float | None
    range_position: str
    risk_reward_location: str
    stop_atr_multiple: float | None
    stop_read: str
    lines: list[str]


@dataclass(frozen=True)
class RsiContextRead:
    rsi: float | None
    zone: str
    context: str
    warning: str


@dataclass(frozen=True)
class TechnicalSetupClassification:
    regime: str
    setup: str
    timing: str
    action_quality: str
    confidence: str
    invalidation_level: float | None
    confirmation_level: float | None
    main_reason: str
    warnings: list[str]
    level_proximity: LevelProximityRead | None = None
    rsi_context: list[RsiContextRead] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CapitalStructureIndicatorRead:
    technical_score: float
    read: str
    supply_overhang_score: float
    dilution_pressure_score: float
    warrant_conversion_proximity_score: float
    offering_activity_score: float
    float_quality_score: float
    foreign_issuer_confidence_modifier: float
    option_exposure_mismatch_score: float
    chase_risk_score: float
    nearest_supply_level: float | None
    nearest_supply_level_label: str | None
    nearest_supply_level_distance_percent: float | None
    source_count: int
    explanation_lines: list[str]
    warnings: list[str]
    recommendation_lines: list[str]


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
    option_contracts: float | None = None
    option_contract_multiplier: int = 100


@dataclass(frozen=True)
class TicketCheck:
    entry_quality: str
    stop_quality: str
    risk_note: str
    verdict: str
    lines: list[str]
    score: ScoreComponent
    risk_reward: str = "Reward/risk unavailable."
    risk_reward_ratio: float | None = None
    target_price: float | None = None
    risk_reward_read: str = "unknown"


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
    source: str = "Schwab"
    freshness: str = "fresh"
    source_note: str = ""


@dataclass(frozen=True)
class MarketIntelligenceDecisionRead:
    decision_lines: list[str]
    source_conflicts: list[str]
    warnings: list[str]
    volume_score_adjustment: float = 0.0
    volume_score_reason: str = ""
    volatility_score_adjustment: float = 0.0
    volatility_score_reason: str = ""
    ticket_score_adjustment: float = 0.0
    ticket_score_reason: str = ""
    confidence_penalty: int = 0
    market_intelligence_score: ScoreComponent | None = None


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
    capital_structure_indicator: CapitalStructureIndicatorRead | None = None
    setup_classification: TechnicalSetupClassification = field(
        default_factory=lambda: TechnicalSetupClassification(
            regime="unknown",
            setup="unknown",
            timing="unknown",
            action_quality="no_edge",
            confidence="low",
            invalidation_level=None,
            confirmation_level=None,
            main_reason="Technical setup classification was not built.",
            warnings=[],
        )
    )
    market_intelligence: Any | None = None
    market_intelligence_source_statuses: tuple[Any, ...] = ()
    market_intelligence_decision: MarketIntelligenceDecisionRead | None = None
    timeframe_source_labels: dict[str, TimeframeSourceRead] = field(default_factory=dict)
    data_plan: TechnicalAnalysisDataPlan | None = None


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
    external_intelligence: Any | None = None,
) -> TechnicalCommandCenterReport:
    clean_symbol = symbol.strip().upper()
    snapshots: dict[str, TimeframeTechnicalSnapshot] = {}
    report_warnings = list(warnings or [])
    selected_timeframe_candles, timeframe_source_labels = _select_decision_weighted_timeframe_candles(
        clean_symbol,
        timeframe_candles,
        external_intelligence,
    )

    spec_by_key = {spec.key: spec for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES}
    for key, candles in selected_timeframe_candles.items():
        spec = spec_by_key.get(key, TimeframeSpec(key, key, "", 0, "", 0, "custom"))
        source_read = timeframe_source_labels.get(
            key,
            TimeframeSourceRead(key, spec.label, "Schwab", _timeframe_freshness(candles, spec), len(candles), "Schwab candles were used."),
        )
        snapshot = build_timeframe_technical_snapshot(
            clean_symbol,
            spec,
            candles,
            source=source_read.source,
            freshness=source_read.freshness,
            source_note=source_read.reason,
        )
        snapshots[key] = snapshot
        if not candles:
            report_warnings.append(f"{spec.label}: no candles were available.")

    if not snapshots:
        report_warnings.append("No usable Schwab price-history timeframe was available.")

    benchmark_reads = build_relative_strength_reads(
        snapshots,
        benchmark_candles or {},
        symbol_candles=_daily_candles_from_timeframes(selected_timeframe_candles),
    )
    if benchmark_candles is None or not benchmark_candles or not any(read.verdict != "unknown" for read in benchmark_reads):
        report_warnings.append("Benchmark relative-strength data unavailable; PRC excludes relative-strength adjustment.")
    if quote_snapshot is not None:
        report_warnings.extend(quote_snapshot.data_quality_warnings)
    if external_intelligence is not None:
        report_warnings.extend(_external_intelligence_warning_lines(external_intelligence))
    active_ticket = ticket or TechnicalTicket()
    relative_strength_score = prc_relative_strength_score(benchmark_reads)
    prc_indexes = build_prc_index_prices(
        clean_symbol,
        selected_timeframe_candles,
        quote_snapshot=quote_snapshot,
        relative_strength_score=relative_strength_score,
        benchmark_data_available=any(read.verdict != "unknown" for read in benchmark_reads),
        ticket=active_ticket,
    )
    ticket_check = build_ticket_check(snapshots, active_ticket)
    setup_classification = build_technical_setup_classification(snapshots, ticket_check=ticket_check)
    capital_structure_indicator = build_capital_structure_indicator_read(
        capital_structure_pressure,
        snapshots,
        ticket=active_ticket,
        prc_indexes=prc_indexes,
    )
    market_intelligence_decision = _build_market_intelligence_decision_read(
        clean_symbol,
        external_intelligence,
        quote_snapshot=quote_snapshot,
        timeframe_source_labels=timeframe_source_labels,
        snapshots=snapshots,
    )
    if market_intelligence_decision is not None:
        report_warnings.extend(market_intelligence_decision.warnings)
    scores = score_command_center(snapshots, benchmark_reads, ticket_check, capital_structure_indicator=capital_structure_indicator)
    scores = _apply_market_intelligence_score_adjustments(scores, market_intelligence_decision)
    score_weights = {
        "Trend": 0.20,
        "Momentum": 0.16,
        "Volume": 0.12,
        "Volatility/Risk": 0.14,
        "Relative Strength": 0.14,
        "Alignment": 0.12,
        "Ticket Quality": 0.12,
    }
    if capital_structure_indicator is not None:
        score_weights["Capital Structure / Supply"] = 0.10
    if market_intelligence_decision is not None and market_intelligence_decision.market_intelligence_score is not None:
        score_weights["Market Intelligence"] = 0.10
    overall_score = _weighted_score(scores, score_weights)
    overall_read = _overall_read(overall_score, scores)
    confidence = _confidence_label(
        snapshots,
        benchmark_reads,
        report_warnings,
        capital_structure_indicator=capital_structure_indicator,
        market_intelligence_decision=market_intelligence_decision,
    )
    best_action = _best_action(
        overall_read,
        snapshots,
        ticket_check,
        capital_structure_indicator=capital_structure_indicator,
        market_intelligence_decision=market_intelligence_decision,
    )
    key_triggers = _build_action_triggers(snapshots)
    plain_english_plan = _plain_english_plan(
        overall_read,
        snapshots,
        ticket_check,
        key_triggers,
        capital_structure_indicator=capital_structure_indicator,
        market_intelligence_decision=market_intelligence_decision,
    )
    data_plan = _build_technical_analysis_data_plan(
        clean_symbol,
        timeframe_source_labels=timeframe_source_labels,
        quote_snapshot=quote_snapshot,
        external_intelligence=external_intelligence,
        capital_structure_pressure=capital_structure_pressure,
    )

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
        capital_structure_indicator=capital_structure_indicator,
        setup_classification=setup_classification,
        market_intelligence=external_intelligence,
        market_intelligence_source_statuses=_external_intelligence_source_statuses(external_intelligence),
        market_intelligence_decision=market_intelligence_decision,
        timeframe_source_labels=timeframe_source_labels,
        data_plan=data_plan,
    )


def _external_intelligence_source_statuses(external_intelligence: Any | None) -> tuple[Any, ...]:
    if external_intelligence is None:
        return ()
    statuses = getattr(external_intelligence, "source_statuses", ()) or ()
    try:
        return tuple(statuses)
    except TypeError:
        return ()


def _external_intelligence_warning_lines(external_intelligence: Any) -> list[str]:
    warnings = [str(warning) for warning in (getattr(external_intelligence, "warnings", ()) or ()) if str(warning).strip()]
    for status in _external_intelligence_source_statuses(external_intelligence):
        status_value = str(getattr(status, "status", "") or "").strip().lower()
        if status_value not in {"warning", "error"}:
            continue
        source = str(getattr(status, "source", "") or "Market intelligence source").strip()
        message = str(getattr(status, "message", "") or "").strip()
        warnings.append(f"{source}: {message}" if message else f"{source}: {status_value}.")
    return _dedupe(warnings)


def _build_technical_analysis_data_plan(
    symbol: str,
    *,
    timeframe_source_labels: Mapping[str, TimeframeSourceRead],
    quote_snapshot: QuoteSnapshot | None,
    external_intelligence: Any | None,
    capital_structure_pressure: CapitalStructurePressureReport | None,
) -> TechnicalAnalysisDataPlan:
    decisions: list[SourceDecision] = [
        SourceDecision(
            "account_holdings_orders",
            "Schwab",
            status="authoritative",
            reason="Schwab remains account, holdings, position, preview, cancel, and order truth.",
        )
    ]
    fmp_profile = _external_mapping(getattr(external_intelligence, "fmp_profile", None))
    fmp_quote = _external_mapping(getattr(external_intelligence, "fmp_quote", None))
    fmp_fundamentals = _external_mapping(getattr(external_intelligence, "fmp_fundamentals", None))
    equity_tape = _external_mapping(getattr(external_intelligence, "databento_equity_tape", None))
    futures_context = getattr(external_intelligence, "databento_futures_context", None)
    futures_count = len(futures_context) if isinstance(futures_context, Mapping) else 0
    source_conflicts = _quote_source_conflicts(symbol, quote_snapshot, equity_tape=equity_tape, fmp_quote=fmp_quote)

    quote_sources = []
    if equity_tape:
        quote_sources.append("Databento")
    if quote_snapshot is not None:
        quote_sources.append("Schwab quote")
    if fmp_quote:
        quote_sources.append("FMP quote")
    decisions.append(
        SourceDecision(
            "selected_equity_quote_tape",
            " + ".join(quote_sources[:2]) if quote_sources else "unavailable",
            fallback_sources=tuple(source for source in ("FMP quote", "Schwab quote") if source in quote_sources[1:]),
            status="conflicting" if source_conflicts else "used" if quote_sources else "unavailable",
            reason=(
                "Databento and Schwab quote are reconciled for selected-equity tape; FMP quote remains fallback/context."
                if quote_sources
                else "No provider supplied selected-equity quote/tape context."
            ),
            diagnostics={
                "conflicts": tuple(source_conflicts),
                "databento_price": equity_tape.get("price"),
                "schwab_last": quote_snapshot.last if quote_snapshot is not None else None,
                "fmp_price": fmp_quote.get("price"),
            },
        )
    )

    for key, source_read in timeframe_source_labels.items():
        status = "fallback" if "fallback" in source_read.source.lower() else "used"
        if source_read.source == "unavailable":
            status = "skipped"
        elif source_read.freshness == "stale":
            status = "stale"
        decisions.append(
            SourceDecision(
                f"technical_history:{key}",
                source_read.source,
                fallback_sources=("Schwab price history", "Databento technical history"),
                status=status,
                reason=source_read.reason,
                diagnostics={
                    "label": source_read.label,
                    "freshness": source_read.freshness,
                    "candle_count": source_read.candle_count,
                    **_timeframe_plan_diagnostics(key, external_intelligence),
                },
            )
        )

    decisions.append(
        SourceDecision(
            "company_profile",
            "FMP profile" if fmp_profile else "SEC ticker map fallback",
            fallback_sources=("SEC ticker map",),
            status="used" if fmp_profile else "fallback",
            reason=(
                "FMP supplied clean company/profile metadata for UI context."
                if fmp_profile
                else "FMP profile was unavailable; SEC identity/ticker mapping remains the fallback."
            ),
            diagnostics={
                field: fmp_profile.get(field)
                for field in ("company_name", "exchange", "sector", "industry", "cik")
                if fmp_profile.get(field) not in (None, "")
            },
        )
    )

    sufficient_fmp_fundamentals = _fmp_fundamentals_sufficient(fmp_fundamentals)
    decisions.append(
        SourceDecision(
            "fundamentals",
            "FMP fundamentals" if sufficient_fmp_fundamentals else "SEC companyfacts fallback",
            fallback_sources=("SEC companyfacts",),
            status="used" if sufficient_fmp_fundamentals else "fallback",
            reason=(
                "FMP supplied enough clean profile/fundamental fields for UI cards; SEC companyfacts remains verification/fallback."
                if sufficient_fmp_fundamentals
                else "FMP fundamentals were missing or incomplete; SEC companyfacts remains the fallback for standardized facts."
            ),
            diagnostics={
                field: fmp_fundamentals.get(field)
                for field in ("market_cap", "pe_ratio", "eps", "revenue_growth", "shares_float", "shares_outstanding")
                if fmp_fundamentals.get(field) not in (None, "")
            },
        )
    )

    source_label = str(getattr(capital_structure_pressure, "source_label", "") or "")
    decisions.append(
        SourceDecision(
            "capital_structure_terms",
            source_label or ("SEC filing text" if capital_structure_pressure is not None else "SEC filing text unavailable"),
            fallback_sources=("SEC recent filings scan",),
            status="used" if capital_structure_pressure is not None and capital_structure_pressure.read != "Unknown" else "fallback",
            reason=(
                "SEC source text remains the source of record for warrants, convertibles, preferreds, offerings, resale language, and source excerpts."
                if capital_structure_pressure is not None
                else "Capital-structure filing text was unavailable."
            ),
            diagnostics={
                "filings_analyzed": getattr(capital_structure_pressure, "filings_analyzed", 0),
                "source_diagnostics": getattr(capital_structure_pressure, "source_diagnostics", {}),
            },
        )
    )

    decisions.append(
        SourceDecision(
            "macro",
            "Official macro sources",
            fallback_sources=("cached official-source fallback",),
            status="official",
            reason="BLS, BEA, Treasury, Census, Federal Reserve, and EIA remain official macro sources.",
        )
    )
    decisions.append(
        SourceDecision(
            "futures_cross_asset",
            "Databento CME context" if futures_count else "Databento CME context skipped",
            status="used" if futures_count else "skipped",
            reason="Databento CME/futures context is kept separate from selected-equity quote and fundamental fields.",
            diagnostics={"context_rows": futures_count},
        )
    )

    provider_statuses = _external_intelligence_source_statuses(external_intelligence)
    if provider_statuses:
        decisions.append(
            SourceDecision(
                "provider_diagnostics",
                "provider status log",
                status="diagnostic",
                reason="Provider calls, cache use, skipped providers, stale/fallback statuses, and warnings are surfaced in the status log.",
                diagnostics={
                    "statuses": tuple(
                        f"{getattr(status, 'source', '')}: {getattr(status, 'status', '')}; {getattr(status, 'message', '')}"
                        for status in provider_statuses
                    )
                },
            )
        )

    warnings = tuple(_dedupe(source_conflicts))
    return TechnicalAnalysisDataPlan(symbol=symbol.upper(), decisions=tuple(decisions), warnings=warnings)


def _timeframe_plan_diagnostics(key: str, external_intelligence: Any | None) -> dict[str, Any]:
    diagnostics = _external_databento_timeframe_diagnostics(external_intelligence).get(key, {})
    if not isinstance(diagnostics, Mapping):
        return {}
    return {
        f"databento_{name}": value
        for name, value in diagnostics.items()
        if value not in (None, "", (), [], {})
    }


def _fmp_fundamentals_sufficient(fundamentals: Mapping[str, Any]) -> bool:
    required_any = ("market_cap", "shares_float", "shares_outstanding", "pe_ratio", "eps", "revenue_growth")
    return sum(1 for field in required_any if _optional_number(fundamentals.get(field)) is not None) >= 2


def _select_decision_weighted_timeframe_candles(
    symbol: str,
    timeframe_candles: dict[str, list[Candle]],
    external_intelligence: Any | None,
) -> tuple[dict[str, list[Candle]], dict[str, TimeframeSourceRead]]:
    selected: dict[str, list[Candle]] = {key: list(candles or []) for key, candles in timeframe_candles.items()}
    source_reads: dict[str, TimeframeSourceRead] = {}
    spec_by_key = {spec.key: spec for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES}
    databento_timeframe_candles = _external_databento_timeframe_candles(symbol, external_intelligence)
    databento_diagnostics = _external_databento_timeframe_diagnostics(external_intelligence)
    databento_base = _external_databento_base_candles(symbol, external_intelligence)
    keys = list(selected)
    for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES:
        has_explicit_history = bool(databento_timeframe_candles.get(spec.key))
        has_tape_derived_intraday = spec.frequency_type == "minute" and bool(_databento_timeframe_candles(databento_base, spec))
        if spec.key not in selected and (has_explicit_history or has_tape_derived_intraday):
            selected[spec.key] = []
            keys.append(spec.key)
    if databento_base:
        for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES:
            if spec.frequency_type != "daily":
                continue
            if spec.key not in selected:
                selected[spec.key] = []
                keys.append(spec.key)

    for key in keys:
        spec = spec_by_key.get(key, TimeframeSpec(key, key, "", 0, "", 0, "custom"))
        schwab_candles = list(timeframe_candles.get(key, []) or [])
        schwab_freshness = _timeframe_freshness(schwab_candles, spec)
        explicit_databento_candles = list(databento_timeframe_candles.get(key, ()) or [])
        if explicit_databento_candles:
            databento_candles = explicit_databento_candles
            databento_reason = "Databento timeframe-aware technical history was available."
        elif spec.key in spec_by_key and spec.frequency_type == "minute":
            databento_candles = _databento_timeframe_candles(databento_base, spec)
            databento_reason = "Databento short tape/context candles were used only for intraday timing."
        else:
            databento_candles = []
            databento_reason = "Databento short tape/context candles were skipped for daily/regime history."
        databento_freshness = _timeframe_freshness(databento_candles, spec)
        use_databento = bool(databento_candles) and (
            not schwab_candles
            or schwab_freshness == "stale" and databento_freshness != "stale"
            or (len(schwab_candles) < 35 <= len(databento_candles))
        )
        if use_databento:
            selected[key] = databento_candles
            source = "Databento" if not schwab_candles else "Databento fallback"
            reason = (
                f"{spec.label}: Databento selected-symbol candles were used because "
                + ("Schwab candles were unavailable." if not schwab_candles else f"Schwab candles were {schwab_freshness} or too sparse.")
            )
            reason += f" {databento_reason}"
            tf_diag = databento_diagnostics.get(key, {})
            lookback = tf_diag.get("requested_lookback_minutes") if isinstance(tf_diag, Mapping) else None
            if lookback:
                reason += f" Requested Databento lookback: {lookback} minute(s)."
            source_reads[key] = TimeframeSourceRead(key, spec.label, source, databento_freshness, len(databento_candles), reason)
        elif schwab_candles:
            reason = f"{spec.label}: Schwab candles remain the primary technical-history source."
            if databento_candles:
                reason += " Databento was available but did not replace fresher Schwab history."
            elif spec.frequency_type == "daily" and databento_base:
                reason += " Databento short tape context was not used as daily/regime history."
            source_reads[key] = TimeframeSourceRead(key, spec.label, "Schwab", schwab_freshness, len(schwab_candles), reason)
        else:
            reason = f"{spec.label}: no Schwab or timeframe-appropriate Databento candles were available."
            if spec.frequency_type == "daily" and databento_base:
                reason += " Databento short tape context was skipped so it cannot masquerade as daily/regime history."
            source_reads[key] = TimeframeSourceRead(key, spec.label, "unavailable", "unavailable", 0, reason)

    return selected, source_reads


def _external_databento_timeframe_candles(symbol: str, external_intelligence: Any | None) -> dict[str, tuple[Candle, ...]]:
    if external_intelligence is None:
        return {}
    raw = getattr(external_intelligence, "databento_technical_candles", None)
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, tuple[Candle, ...]] = {}
    for key, rows in raw.items():
        try:
            candles = tuple(sorted((row for row in rows or () if isinstance(row, Candle)), key=lambda candle: candle.datetime_ms))
        except TypeError:
            candles = ()
        if candles:
            result[str(key)] = candles
    return result


def _external_databento_timeframe_diagnostics(external_intelligence: Any | None) -> dict[str, Mapping[str, Any]]:
    if external_intelligence is None:
        return {}
    provenance = getattr(external_intelligence, "provenance", None)
    if not isinstance(provenance, Mapping):
        return {}
    diagnostics = provenance.get("databento_technical_history")
    if not isinstance(diagnostics, Mapping):
        return {}
    return {str(key): value for key, value in diagnostics.items() if isinstance(value, Mapping)}


def _external_databento_base_candles(symbol: str, external_intelligence: Any | None) -> list[Candle]:
    if external_intelligence is None:
        return []
    candles_by_symbol = getattr(external_intelligence, "databento_equity_candles", None)
    if not isinstance(candles_by_symbol, Mapping):
        return []
    clean_symbol = symbol.strip().upper()
    selected = None
    for key, rows in candles_by_symbol.items():
        if str(key or "").strip().upper() == clean_symbol:
            selected = rows
            break
    if selected is None and len(candles_by_symbol) == 1:
        selected = next(iter(candles_by_symbol.values()))
    try:
        candles = [row for row in selected or () if isinstance(row, Candle)]
    except TypeError:
        candles = []
    return sorted(candles, key=lambda candle: candle.datetime_ms)


def _databento_timeframe_candles(candles: list[Candle], spec: TimeframeSpec) -> list[Candle]:
    if not candles:
        return []
    if spec.frequency_type == "minute":
        if spec.frequency <= 1:
            return list(candles)
        return _aggregate_candles_by_interval(candles, interval_ms=spec.frequency * 60_000)
    if spec.frequency_type == "daily":
        return _aggregate_candles_by_utc_date(candles)
    return []


def _aggregate_candles_by_interval(candles: list[Candle], *, interval_ms: int) -> list[Candle]:
    if interval_ms <= 0:
        return list(candles)
    groups: dict[int, list[Candle]] = {}
    for candle in sorted(candles, key=lambda row: row.datetime_ms):
        groups.setdefault(candle.datetime_ms // interval_ms, []).append(candle)
    return [_aggregate_candle_group(rows) for _bucket, rows in sorted(groups.items()) if rows]


def _aggregate_candles_by_utc_date(candles: list[Candle]) -> list[Candle]:
    groups: dict[str, list[Candle]] = {}
    for candle in sorted(candles, key=lambda row: row.datetime_ms):
        parsed = _candle_datetime_utc(candle)
        key = parsed.date().isoformat() if parsed is not None else str(candle.datetime_ms)
        groups.setdefault(key, []).append(candle)
    return [_aggregate_candle_group(rows) for _key, rows in sorted(groups.items()) if rows]


def _aggregate_candle_group(rows: list[Candle]) -> Candle:
    ordered = sorted(rows, key=lambda row: row.datetime_ms)
    return Candle(
        datetime_ms=ordered[0].datetime_ms,
        open=ordered[0].open,
        high=max(candle.high for candle in ordered),
        low=min(candle.low for candle in ordered),
        close=ordered[-1].close,
        volume=sum(candle.volume for candle in ordered),
    )


def _timeframe_freshness(candles: list[Candle], spec: TimeframeSpec) -> str:
    if not candles:
        return "unavailable"
    latest = _candle_datetime_utc(max(candles, key=lambda candle: candle.datetime_ms))
    if latest is None:
        return "fallback"
    age = datetime.now(timezone.utc) - latest
    if age < timedelta(days=-1):
        return "fallback"
    if age > _freshness_threshold(spec):
        return "stale"
    return "fresh"


def _freshness_threshold(spec: TimeframeSpec) -> timedelta:
    if spec.frequency_type == "minute":
        minutes = max(1, spec.frequency)
        if minutes <= 1:
            return timedelta(minutes=20)
        if minutes <= 5:
            return timedelta(minutes=45)
        return timedelta(hours=4)
    if spec.frequency_type == "daily":
        return timedelta(days=7)
    return timedelta(days=1)


def _candle_datetime_utc(candle: Candle) -> datetime | None:
    if candle.datetime_ms < 946_684_800_000:
        return None
    try:
        return datetime.fromtimestamp(candle.datetime_ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def build_timeframe_technical_snapshot(
    symbol: str,
    spec: TimeframeSpec,
    candles: list[Candle],
    *,
    source: str = "Schwab",
    freshness: str = "fresh",
    source_note: str = "",
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
            source=source,
            freshness=freshness,
            source_note=source_note,
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
        source=source,
        freshness=freshness,
        source_note=source_note,
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
    level_read = (
        build_level_proximity_read(primary, entry_price=entry, stop_price=stop, side=side)
        if primary is not None
        else None
    )
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
        if level_read is not None:
            lines.append(f"Entry location: {_humanize_classification_value(level_read.range_position)}; risk/reward location {level_read.risk_reward_location}.")
        entry_quality = _entry_quality(side, entry, latest, vwap_value, support, resistance)

    if stop is None:
        stop_quality = "no stop"
        lines.append("Stop: no stop price was provided, so downside risk is undefined in this read.")
    else:
        stop_quality = _stop_quality(side, entry, stop, atr, support, resistance)
        lines.append(f"Stop: {_money(stop)}. {stop_quality}.")
    if level_read is not None:
        lines.append(f"Stop logic: {level_read.stop_read}")

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

    risk_reward, risk_reward_ratio, target_price, risk_reward_read = _ticket_risk_reward_read(side, entry, stop, support, resistance)
    lines.append(risk_reward)

    score = score_ticket(entry_quality, stop_quality, risk_note)
    if risk_reward_read == "poor":
        score = ScoreComponent(score.name, _clamp_score(score.score - 12), f"{score.reason}; poor reward/risk")
    elif risk_reward_read == "good":
        score = ScoreComponent(score.name, _clamp_score(score.score + 6), f"{score.reason}; favorable reward/risk")
    verdict = _ticket_verdict(entry_quality, stop_quality, score.score)
    if risk_reward_read == "poor":
        verdict = "Risk/reward is poor for the visible support/resistance map."
    elif score.score >= 70 and risk_reward_read == "good":
        verdict = "Order is coherent; risk is defined and reward/risk is favorable."
    return TicketCheck(
        entry_quality,
        stop_quality,
        risk_note,
        verdict,
        lines,
        score,
        risk_reward=risk_reward,
        risk_reward_ratio=risk_reward_ratio,
        target_price=target_price,
        risk_reward_read=risk_reward_read,
    )


def score_command_center(
    snapshots: dict[str, TimeframeTechnicalSnapshot],
    benchmark_reads: list[RelativeStrengthRead],
    ticket_check: TicketCheck,
    *,
    capital_structure_indicator: CapitalStructureIndicatorRead | None = None,
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
    scores = {
        "Trend": trend,
        "Momentum": momentum,
        "Volume": volume_score,
        "Volatility/Risk": risk,
        "Relative Strength": relative_strength,
        "Alignment": alignment,
        "Ticket Quality": ticket_check.score,
    }
    if capital_structure_indicator is not None:
        scores["Capital Structure / Supply"] = ScoreComponent(
            "Capital Structure / Supply",
            capital_structure_indicator.technical_score,
            _capital_structure_score_reason(capital_structure_indicator),
        )
    return scores


def _build_market_intelligence_decision_read(
    symbol: str,
    external_intelligence: Any | None,
    *,
    quote_snapshot: QuoteSnapshot | None,
    timeframe_source_labels: dict[str, TimeframeSourceRead],
    snapshots: dict[str, TimeframeTechnicalSnapshot],
) -> MarketIntelligenceDecisionRead | None:
    if external_intelligence is None:
        return None

    decision_lines: list[str] = []
    warnings: list[str] = []
    confidence_penalty = 0
    fmp_profile = _external_mapping(getattr(external_intelligence, "fmp_profile", None))
    fmp_quote = _external_mapping(getattr(external_intelligence, "fmp_quote", None))
    fmp_fundamentals = _external_mapping(getattr(external_intelligence, "fmp_fundamentals", None))
    equity_tape = _external_mapping(getattr(external_intelligence, "databento_equity_tape", None))
    source_conflicts = _quote_source_conflicts(symbol, quote_snapshot, equity_tape=equity_tape, fmp_quote=fmp_quote)
    if source_conflicts:
        warnings.extend(source_conflicts)
        confidence_penalty += 1
        decision_lines.append("Source conflict detected; Schwab remains account/position truth and confidence is demoted until quotes converge.")

    databento_frames = [
        read
        for read in timeframe_source_labels.values()
        if read.source.lower().startswith("databento") and read.candle_count > 0
    ]
    if databento_frames:
        names = ", ".join(read.key for read in databento_frames[:4])
        decision_lines.append(f"Databento selected-equity candles influenced VWAP, realized volatility, and volume reads for {names}.")

    tape_fetched_at = _optional_text(equity_tape.get("fetched_at") or equity_tape.get("timestamp"))
    if tape_fetched_at:
        tape_age = _age_from_iso(tape_fetched_at)
        if tape_age is not None and tape_age > timedelta(minutes=20):
            warnings.append(f"Databento tape freshness is stale ({tape_fetched_at}); quote/tape confidence is lower.")
            confidence_penalty += 1

    volume_adjustment, volume_reason = _databento_volume_adjustment(equity_tape)
    volatility_adjustment, volatility_reason = _databento_volatility_adjustment(databento_frames, snapshots)
    ticket_adjustment, ticket_reason = _liquidity_ticket_adjustment(quote_snapshot, source_conflicts)
    market_intelligence_score = _market_intelligence_score(fmp_profile, fmp_quote, fmp_fundamentals, equity_tape, source_conflicts)

    if fmp_profile or fmp_fundamentals:
        profile_bits = [field for field in ("company_name", "sector", "industry", "exchange", "cik") if _optional_text(fmp_profile.get(field))]
        fundamental_bits = [field for field in ("market_cap", "pe_ratio", "eps", "revenue_growth", "shares_float", "shares_outstanding") if _optional_number(fmp_fundamentals.get(field)) is not None]
        bits = []
        if profile_bits:
            bits.append("profile " + "/".join(profile_bits[:4]))
        if fundamental_bits:
            bits.append("fundamentals " + "/".join(fundamental_bits[:5]))
        if bits:
            decision_lines.append("FMP decision context loaded: " + "; ".join(bits) + ".")

    if equity_tape:
        tape_bits = []
        tape_price = _optional_number(equity_tape.get("price"))
        tape_volume = _optional_number(equity_tape.get("volume"))
        tape_avg_volume = _optional_number(equity_tape.get("avg_volume"))
        if tape_price is not None:
            tape_bits.append(f"price {_money(tape_price)}")
        if tape_volume is not None:
            tape_bits.append(f"volume {_format_optional(tape_volume)}")
        if tape_volume is not None and tape_avg_volume not in (None, 0):
            tape_bits.append(f"relative volume {tape_volume / tape_avg_volume:.2f}x")
        if tape_bits:
            decision_lines.append("Databento tape influenced confirmation: " + ", ".join(tape_bits) + ".")

    if not any((decision_lines, warnings, market_intelligence_score)):
        return None
    return MarketIntelligenceDecisionRead(
        decision_lines=_dedupe(decision_lines),
        source_conflicts=_dedupe(source_conflicts),
        warnings=_dedupe(warnings),
        volume_score_adjustment=volume_adjustment,
        volume_score_reason=volume_reason,
        volatility_score_adjustment=volatility_adjustment,
        volatility_score_reason=volatility_reason,
        ticket_score_adjustment=ticket_adjustment,
        ticket_score_reason=ticket_reason,
        confidence_penalty=confidence_penalty,
        market_intelligence_score=market_intelligence_score,
    )


def _apply_market_intelligence_score_adjustments(
    scores: dict[str, ScoreComponent],
    decision: MarketIntelligenceDecisionRead | None,
) -> dict[str, ScoreComponent]:
    if decision is None:
        return scores
    adjusted = dict(scores)
    if decision.volume_score_adjustment:
        adjusted["Volume"] = _adjust_score_component(
            adjusted.get("Volume", ScoreComponent("Volume", 50, "Volume confirmation is limited.")),
            decision.volume_score_adjustment,
            decision.volume_score_reason,
        )
    if decision.volatility_score_adjustment:
        adjusted["Volatility/Risk"] = _adjust_score_component(
            adjusted.get("Volatility/Risk", ScoreComponent("Volatility/Risk", 50, "Volatility evidence is limited.")),
            decision.volatility_score_adjustment,
            decision.volatility_score_reason,
        )
    if decision.ticket_score_adjustment:
        adjusted["Ticket Quality"] = _adjust_score_component(
            adjusted.get("Ticket Quality", ScoreComponent("Ticket Quality", 50, "Ticket evidence is limited.")),
            decision.ticket_score_adjustment,
            decision.ticket_score_reason,
        )
    if decision.market_intelligence_score is not None:
        adjusted["Market Intelligence"] = decision.market_intelligence_score
    return adjusted


def _adjust_score_component(component: ScoreComponent, adjustment: float, reason: str) -> ScoreComponent:
    clean_reason = reason.strip()
    if not clean_reason:
        clean_reason = f"market intelligence adjustment {adjustment:+.0f}"
    return ScoreComponent(
        component.name,
        _clamp_score(component.score + adjustment),
        f"{component.reason}; {clean_reason}",
    )


def _quote_source_conflicts(
    symbol: str,
    quote_snapshot: QuoteSnapshot | None,
    *,
    equity_tape: Mapping[str, Any],
    fmp_quote: Mapping[str, Any],
) -> list[str]:
    points: list[tuple[str, float]] = []
    schwab_price = _quote_snapshot_price(quote_snapshot)
    if schwab_price is not None:
        points.append(("Schwab quote", schwab_price))
    databento_price = _optional_number(equity_tape.get("price"))
    if databento_price is not None:
        points.append(("Databento tape", databento_price))
    fmp_price = _optional_number(fmp_quote.get("price"))
    if fmp_price is not None:
        points.append(("FMP quote", fmp_price))

    conflicts: list[str] = []
    for index, (left_source, left_price) in enumerate(points):
        for right_source, right_price in points[index + 1:]:
            if left_price <= 0 or right_price <= 0:
                continue
            midpoint = (left_price + right_price) / 2
            diff_percent = abs(left_price - right_price) / midpoint * 100
            if diff_percent >= 1.0 and abs(left_price - right_price) >= 0.03:
                conflicts.append(
                    f"Source conflict: {symbol.upper()} {left_source} {_money(left_price)} vs {right_source} {_money(right_price)} differ by {diff_percent:.2f}%."
                )
    return conflicts


def _quote_snapshot_price(quote_snapshot: QuoteSnapshot | None) -> float | None:
    if quote_snapshot is None:
        return None
    return quote_snapshot.last or quote_snapshot.mark


def _databento_volume_adjustment(equity_tape: Mapping[str, Any]) -> tuple[float, str]:
    volume = _optional_number(equity_tape.get("volume"))
    avg_volume = _optional_number(equity_tape.get("avg_volume"))
    if volume is None or avg_volume in (None, 0):
        return 0.0, ""
    relative = volume / avg_volume
    if relative >= 1.5:
        return 6.0, f"Databento relative volume confirms participation at {relative:.2f}x"
    if relative <= 0.60:
        return -5.0, f"Databento relative volume is light at {relative:.2f}x"
    return 0.0, ""


def _databento_volatility_adjustment(
    databento_frames: list[TimeframeSourceRead],
    snapshots: dict[str, TimeframeTechnicalSnapshot],
) -> tuple[float, str]:
    if not databento_frames:
        return 0.0, ""
    realized = [
        snapshot.realized_vol_20
        for read in databento_frames
        if (snapshot := snapshots.get(read.key)) is not None and snapshot.realized_vol_20 is not None
    ]
    if not realized:
        return 0.0, ""
    max_realized = max(realized)
    if max_realized >= 8.0:
        return -4.0, f"Databento-derived realized volatility is elevated at {max_realized:.1f}%"
    if max_realized <= 2.5:
        return 2.0, f"Databento-derived realized volatility is contained at {max_realized:.1f}%"
    return 0.0, ""


def _liquidity_ticket_adjustment(
    quote_snapshot: QuoteSnapshot | None,
    source_conflicts: list[str],
) -> tuple[float, str]:
    if source_conflicts:
        return -10.0, "source conflict raises slippage/quote-confidence risk"
    spread = spread_percent(quote_snapshot)
    if spread is None:
        return 0.0, ""
    if spread >= 1.0:
        return -8.0, f"Schwab bid/ask spread is wide at {spread:.2f}%"
    if spread <= 0.10:
        return 3.0, f"Schwab bid/ask spread is tight at {spread:.2f}%"
    return 0.0, ""


def _market_intelligence_score(
    fmp_profile: Mapping[str, Any],
    fmp_quote: Mapping[str, Any],
    fmp_fundamentals: Mapping[str, Any],
    equity_tape: Mapping[str, Any],
    source_conflicts: list[str],
) -> ScoreComponent | None:
    if not any((fmp_profile, fmp_quote, fmp_fundamentals, equity_tape, source_conflicts)):
        return None
    score = 50.0
    reasons: list[str] = []
    profile_fields = [field for field in ("company_name", "sector", "industry", "exchange", "cik") if _optional_text(fmp_profile.get(field))]
    if profile_fields:
        score += min(10.0, len(profile_fields) * 2.0)
        reasons.append("FMP profile/company context loaded")
    fundamental_fields = [
        field
        for field in ("market_cap", "pe_ratio", "eps", "revenue_growth", "shares_float", "shares_outstanding")
        if _optional_number(fmp_fundamentals.get(field)) is not None
    ]
    if fundamental_fields:
        score += min(12.0, len(fundamental_fields) * 2.0)
        reasons.append("FMP valuation/growth/float fields loaded")
    revenue_growth = _optional_number(fmp_fundamentals.get("revenue_growth"))
    if revenue_growth is not None:
        if revenue_growth >= 10:
            score += 6.0
            reasons.append(f"FMP revenue growth is strong at {revenue_growth:.1f}%")
        elif revenue_growth < 0:
            score -= 8.0
            reasons.append(f"FMP revenue growth is negative at {revenue_growth:.1f}%")
    pe_ratio = _optional_number(fmp_fundamentals.get("pe_ratio"))
    if pe_ratio is not None and pe_ratio >= 80:
        score -= 5.0
        reasons.append(f"FMP P/E is extended at {pe_ratio:.1f}")
    shares_float = _optional_number(fmp_fundamentals.get("shares_float"))
    if shares_float is not None and shares_float < 20_000_000:
        score -= 5.0
        reasons.append("FMP float is below 20M shares")
    if equity_tape:
        score += 6.0
        reasons.append("Databento selected-equity tape loaded")
    if source_conflicts:
        score -= 18.0
        reasons.append("quote source conflict demotes confidence")
    return ScoreComponent("Market Intelligence", _clamp_score(score), _reason(reasons, "External market intelligence was available."))


def _age_from_iso(value: str) -> timedelta | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - parsed


def build_capital_structure_indicator_read(
    report: CapitalStructurePressureReport | None,
    snapshots: dict[str, TimeframeTechnicalSnapshot],
    *,
    ticket: TechnicalTicket | None = None,
    prc_indexes: dict[str, PrcIndexPrice] | None = None,
) -> CapitalStructureIndicatorRead | None:
    if report is None or report.read == "Unknown":
        return None

    parsed_count = _capital_structure_parsed_term_count(report)
    option_mismatch_score, option_lines, option_warnings = _option_exposure_mismatch_read(ticket)
    has_pressure_context = bool(report.signals) or _clamp_score(report.supply_overhang_score) > 0
    if parsed_count == 0 and not report.possible_supply_levels and option_mismatch_score <= 0 and not has_pressure_context:
        return None

    parsed = report.parsed_terms
    active_snapshot = _preferred_snapshot(snapshots, "timing") or _preferred_snapshot(snapshots, "setup") or _preferred_snapshot(snapshots, "regime")
    supply_overhang_score = _clamp_score(report.supply_overhang_score)
    dilution_pressure_score = _capital_structure_dilution_pressure_score(report)
    offering_activity_score = _capital_structure_offering_activity_score(report)
    float_quality_score = _capital_structure_float_quality_score(report)
    foreign_modifier = -12.0 if parsed.ads_adr_structures else 0.0
    (
        proximity_score,
        nearest_level,
        nearest_level_label,
        nearest_level_distance,
        proximity_lines,
        proximity_warnings,
        supply_absorption,
    ) = _capital_structure_supply_level_proximity(report, active_snapshot, prc_indexes=prc_indexes or {})
    weak_confirmation = active_snapshot is not None and not _volume_confirms_up(active_snapshot)
    extended = active_snapshot is not None and active_snapshot.vwap_distance_percent is not None and active_snapshot.vwap_distance_percent > 4.0
    chase_risk_score = max(
        proximity_score + (15 if weak_confirmation and proximity_score >= 35 else 0),
        offering_activity_score + (10 if weak_confirmation or extended else 0),
        dilution_pressure_score + (8 if weak_confirmation else 0),
        option_mismatch_score,
    )
    chase_risk_score = _clamp_score(chase_risk_score)
    risk_pressure = _clamp_score(
        (supply_overhang_score * 0.28)
        + (dilution_pressure_score * 0.18)
        + (proximity_score * 0.22)
        + (offering_activity_score * 0.14)
        + ((100.0 - float_quality_score) * 0.10)
        + (option_mismatch_score * 0.14)
        + abs(foreign_modifier)
    )
    technical_score = _clamp_score(100.0 - risk_pressure)

    source_labels = _capital_structure_source_labels(report)
    explanation_lines = [
        f"Supply overhang risk {supply_overhang_score:.0f}/100 comes from the filing pressure scan.",
        f"Dilution pressure risk {dilution_pressure_score:.0f}/100 reflects parsed preferred, convertible, offering, and dilution-warning terms.",
        f"Offering activity risk {offering_activity_score:.0f}/100 reflects parsed ATM, shelf, resale, and offering programs.",
        f"Float quality score {float_quality_score:.0f}/100 reflects parsed share-class and ADS/ADR complexity.",
    ]
    if source_labels:
        explanation_lines.append(f"Filing sources in this read came from: {', '.join(source_labels[:4])}.")
    no_level_reason = _capital_structure_no_level_explanation(report)
    if nearest_level is None:
        explanation_lines.append(no_level_reason)
    explanation_lines.extend(proximity_lines)
    explanation_lines.extend(option_lines)
    if foreign_modifier < 0:
        explanation_lines.append("Foreign issuer / ADS structure applies a confidence haircut until issuer or depositary terms are verified.")

    warnings = _dedupe([*proximity_warnings, *option_warnings])
    if offering_activity_score >= 45:
        warnings.append("Active ATM, shelf, resale, or offering language can increase chase and rally-fade risk.")
    if dilution_pressure_score >= 50:
        warnings.append("Preferred, convertible, or dilution terms make this setup more dilution-sensitive.")
    if foreign_modifier < 0:
        warnings.append("ADS/ADR or foreign issuer structure lowers confidence until source documents are verified.")

    read = _capital_structure_indicator_read_label(
        technical_score=technical_score,
        supply_overhang_score=supply_overhang_score,
        dilution_pressure_score=dilution_pressure_score,
        offering_activity_score=offering_activity_score,
        proximity_score=proximity_score,
        float_quality_score=float_quality_score,
        foreign_modifier=foreign_modifier,
        option_mismatch_score=option_mismatch_score,
        chase_risk_score=chase_risk_score,
        supply_absorption=supply_absorption,
    )
    recommendation_lines = _capital_structure_recommendation_lines(
        read,
        technical_score=technical_score,
        nearest_level=nearest_level,
        nearest_level_label=nearest_level_label,
        nearest_level_distance=nearest_level_distance,
        no_level_reason=no_level_reason if nearest_level is None else None,
    )

    return CapitalStructureIndicatorRead(
        technical_score=technical_score,
        read=read,
        supply_overhang_score=supply_overhang_score,
        dilution_pressure_score=dilution_pressure_score,
        warrant_conversion_proximity_score=proximity_score,
        offering_activity_score=offering_activity_score,
        float_quality_score=float_quality_score,
        foreign_issuer_confidence_modifier=foreign_modifier,
        option_exposure_mismatch_score=option_mismatch_score,
        chase_risk_score=chase_risk_score,
        nearest_supply_level=nearest_level,
        nearest_supply_level_label=nearest_level_label,
        nearest_supply_level_distance_percent=nearest_level_distance,
        source_count=len(source_labels),
        explanation_lines=_dedupe(explanation_lines),
        warnings=_dedupe(warnings),
        recommendation_lines=recommendation_lines,
    )


def _capital_structure_dilution_pressure_score(report: CapitalStructurePressureReport) -> float:
    parsed = report.parsed_terms
    score = 0.0
    score += min(len(parsed.preferred_series), 3) * 16
    score += min(len(parsed.convertibles), 3) * 20
    score += 14 if any(item.conversion_price is not None or item.conversion_rate for item in parsed.preferred_series) else 0
    score += 16 if any(item.conversion_price is not None or item.conversion_rate for item in parsed.convertibles) else 0
    score += 14 if any(item.program_type in {"ATM program", "Resale prospectus", "Offering"} for item in parsed.offering_programs) else 0
    score += 18 if any(signal.label == "Dilution warning" for signal in report.signals) else 0
    return _clamp_score(score)


def _capital_structure_offering_activity_score(report: CapitalStructurePressureReport) -> float:
    score = 0.0
    for program in report.parsed_terms.offering_programs:
        if program.program_type == "ATM program":
            score += 35
        elif program.program_type == "Resale prospectus":
            score += 28
        elif program.program_type == "Shelf registration":
            score += 22
        elif program.program_type == "Offering":
            score += 18
    if any(signal.label == "Shelf / registration capacity" for signal in report.signals):
        score += 10
    return _clamp_score(score)


def _capital_structure_float_quality_score(report: CapitalStructurePressureReport) -> float:
    parsed = report.parsed_terms
    score = 100.0
    if parsed.common_share_classes:
        score -= min(len(parsed.common_share_classes), 4) * 8
    class_text = " ".join((item.class_name or "") for item in parsed.common_share_classes).lower()
    if any(term in class_text for term in ("non-voting", "high-vote", "super-voting")):
        score -= 18
    if parsed.ads_adr_structures:
        score -= 20
    return _clamp_score(score)


def _capital_structure_supply_level_proximity(
    report: CapitalStructurePressureReport,
    snapshot: TimeframeTechnicalSnapshot | None,
    *,
    prc_indexes: dict[str, PrcIndexPrice],
) -> tuple[float, float | None, str | None, float | None, list[str], list[str], bool]:
    latest = snapshot.latest_close if snapshot is not None else None
    if latest is None or latest <= 0 or not report.possible_supply_levels:
        return 0.0, None, None, None, [], [], False

    nearest = min(report.possible_supply_levels, key=lambda level: abs(level.price - latest) / latest)
    distance = _percent(nearest.price - latest, latest)
    absolute_distance = abs(distance) if distance is not None else None
    atr_band = max(1.0, min(6.0, (snapshot.atr_percent or 2.0) * 1.25)) if snapshot is not None else 2.5
    confirms = _volume_confirms_up(snapshot)
    distribution = _distribution_heavy(snapshot)
    score = 10.0
    lines = [
        f"Nearest filing-derived supply level is {nearest.label} at {_money(nearest.price)}; distance {_fmt_percent(distance)} from latest price."
    ]
    warnings: list[str] = []
    supply_absorption = False

    if absolute_distance is not None and absolute_distance <= atr_band:
        if latest > nearest.price and confirms:
            score = 28
            supply_absorption = True
            lines.append("Price is above the parsed supply level with confirming volume; treat as possible supply absorption, not a target.")
        elif distribution or not confirms:
            score = 68
            warnings.append("Price is near a parsed filing supply level without strong volume confirmation; rally-fade risk is elevated.")
        else:
            score = 54
            warnings.append("Price is near a parsed filing supply level; require volume/VWAP confirmation.")
    elif absolute_distance is not None and absolute_distance <= atr_band * 2:
        score = 34

    resistance = _nearest_resistance(snapshot)
    if resistance is not None and resistance.low <= nearest.price <= resistance.high:
        score += 8
        lines.append("The filing-derived supply level overlaps the nearest technical resistance zone.")

    prc = prc_indexes.get(snapshot.key) if snapshot is not None else None
    if prc is not None and prc.index_price is not None:
        prc_distance = abs(_percent(nearest.price - prc.index_price, prc.index_price) or 0.0)
        if prc_distance <= atr_band:
            score += 5
            lines.append("The filing-derived supply level is close to the PRC Pressure Line reference.")

    return _clamp_score(score), nearest.price, nearest.label, distance, lines, warnings, supply_absorption


def _option_exposure_mismatch_read(ticket: TechnicalTicket | None) -> tuple[float, list[str], list[str]]:
    if ticket is None or ticket.option_contracts is None or ticket.option_contracts <= 0:
        return 0.0, [], []
    controlled_shares = abs(ticket.option_contracts) * max(ticket.option_contract_multiplier, 1)
    stock_shares = abs(ticket.quantity or 0.0)
    if stock_shares <= 0:
        score = 90.0
        ratio_text = "no modeled stock exposure"
    else:
        ratio = controlled_shares / stock_shares
        ratio_text = f"{ratio:.1f}x modeled stock exposure"
        if ratio >= 10:
            score = 90.0
        elif ratio >= 3:
            score = 75.0
        elif ratio >= 1.5:
            score = 55.0
        else:
            score = 15.0
    line = f"Option exposure mismatch: {ticket.option_contracts:g} contract(s) control about {controlled_shares:,.0f} shares versus {stock_shares:,.0f} modeled shares ({ratio_text})."
    warnings = [line] if score >= 55 else []
    return score, [line], warnings


def _capital_structure_indicator_read_label(
    *,
    technical_score: float,
    supply_overhang_score: float,
    dilution_pressure_score: float,
    offering_activity_score: float,
    proximity_score: float,
    float_quality_score: float,
    foreign_modifier: float,
    option_mismatch_score: float,
    chase_risk_score: float,
    supply_absorption: bool,
) -> str:
    if supply_absorption:
        return "supply_absorption"
    if option_mismatch_score >= 55:
        return "option_size_mismatch"
    if chase_risk_score >= 58 or proximity_score >= 58 or (offering_activity_score >= 45 and proximity_score >= 45):
        return "rally_fade_risk"
    if dilution_pressure_score >= 58:
        return "dilution_sensitive"
    if offering_activity_score >= 48:
        return "offering_pressure"
    if foreign_modifier < 0:
        return "verification_needed"
    if float_quality_score <= 62:
        return "float_quality_watch"
    if supply_overhang_score >= 25:
        return "supply_context"
    if technical_score >= 72:
        return "clean"
    return "supply_context"


def _capital_structure_recommendation_lines(
    read: str,
    *,
    technical_score: float,
    nearest_level: float | None,
    nearest_level_label: str | None,
    nearest_level_distance: float | None,
    no_level_reason: str | None = None,
) -> list[str]:
    level_text = ""
    if nearest_level is not None:
        level_text = f" near {nearest_level_label or 'parsed supply level'} {_money(nearest_level)} ({_fmt_percent(nearest_level_distance)} from latest)"
    if read == "supply_absorption":
        return [f"Watch for absorption{level_text}; require volume/VWAP hold and do not treat the filing level as a price target."]
    if read == "rally_fade_risk":
        if no_level_reason and nearest_level is None:
            return [f"Avoid chase while filing pressure is active. {no_level_reason} Wait for stronger volume/VWAP confirmation before trusting a breakout."]
        return [f"Avoid chase{level_text}; wait for stronger volume/VWAP confirmation before trusting a breakout."]
    if read == "dilution_sensitive":
        if no_level_reason and nearest_level is None:
            return [f"Treat the setup as dilution-sensitive. {no_level_reason} Bullish reads need stronger participation and clean VWAP behavior."]
        return ["Treat the setup as dilution-sensitive; bullish reads need stronger participation and clean VWAP behavior."]
    if read == "offering_pressure":
        if no_level_reason and nearest_level is None:
            return [f"Breakout trigger only while offering pressure is active. {no_level_reason}"]
        return ["Breakout trigger only while ATM, shelf, resale, or offering pressure is active."]
    if read == "option_size_mismatch":
        return ["Hedge/speculation sizing mismatch detected; option contract exposure is large versus modeled stock exposure."]
    if read == "verification_needed":
        return ["Verify foreign issuer, ADS/ADR, and ordinary-share documents before raising confidence from U.S. filing text alone."]
    if read == "clean":
        return [f"Capital-structure indicator is clean enough to preserve chart confidence ({technical_score:.0f}/100), subject to normal confirmation."]
    if no_level_reason:
        return [f"{no_level_reason} Use the filing-pressure read as context only; do not infer a supply level."]
    return ["Use filing-derived supply terms as context only; they modify confidence and chase risk, not price direction."]


def _capital_structure_score_reason(indicator: CapitalStructureIndicatorRead) -> str:
    return (
        f"{indicator.read}; technical score {indicator.technical_score:.0f}/100 from "
        f"supply {indicator.supply_overhang_score:.0f}, dilution {indicator.dilution_pressure_score:.0f}, "
        f"level proximity {indicator.warrant_conversion_proximity_score:.0f}, offering {indicator.offering_activity_score:.0f}, "
        f"float quality {indicator.float_quality_score:.0f}"
    )


def _capital_structure_source_labels(report: CapitalStructurePressureReport) -> list[str]:
    labels: list[str] = []
    parsed = report.parsed_terms
    groups = (
        parsed.common_share_classes,
        parsed.preferred_series,
        parsed.warrants,
        parsed.convertibles,
        parsed.offering_programs,
        parsed.ads_adr_structures,
    )
    for group in groups:
        for item in group:
            form = getattr(item, "source_form", "")
            source_date = getattr(item, "source_date", "")
            if form or source_date:
                labels.append(f"{form or 'filing'} filed {source_date or '--'}")
    for level in report.possible_supply_levels:
        labels.append(level.source)
    for signal in report.signals:
        form = getattr(signal, "source_form", "")
        source_date = getattr(signal, "source_date", "")
        if form or source_date:
            labels.append(f"{form or 'filing'} filed {source_date or '--'}")
    return _dedupe(labels)


def _capital_structure_parsed_term_count(report: CapitalStructurePressureReport) -> int:
    parsed = report.parsed_terms
    return (
        len(parsed.common_share_classes)
        + len(parsed.preferred_series)
        + len(parsed.warrants)
        + len(parsed.convertibles)
        + len(parsed.offering_programs)
        + len(parsed.ads_adr_structures)
    )


def _capital_structure_no_level_explanation(report: CapitalStructurePressureReport) -> str:
    if report.possible_supply_levels:
        return "A source-backed filing price level was parsed."
    parsed_count = _capital_structure_parsed_term_count(report)
    if report.signals:
        labels = ", ".join(_dedupe([signal.label for signal in report.signals])[:4])
        return (
            f"Pressure signals were found, but no explicit price level was parsed. "
            f"SEC scan found filing-derived pressure signal(s) ({labels}) but no supported warrant exercise, "
            "conversion, offering, purchase, or resale price level was parsed; no supply level is inferred."
        )
    if parsed_count:
        return (
            f"SEC scan parsed {parsed_count} source-backed capital-structure term(s), but none included a supported "
            "exercise, conversion, offering, purchase, or resale price level; no supply level is inferred."
        )
    return (
        f"SEC scan reviewed {report.filings_analyzed} filing(s), but no supported capital-structure terms or "
        "filing-derived price levels were detected; no supply level is inferred."
    )


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
    if entry_quality in {"pullback entry is better", "wait for confirmation", "middle of range / no edge"}:
        score -= 10
    elif entry_quality in {"technically coherent", "entry near support", "entry near resistance"}:
        score += 15
    elif entry_quality in {"breakout entry only", "breakdown entry only"}:
        score -= 4
    elif "chasing" in entry_quality:
        score -= 18
    if "inside normal noise" in stop_quality or "wrong side" in risk_note:
        score -= 22
    elif "reasonable" in stop_quality or "below support" in stop_quality or "above resistance" in stop_quality:
        score += 18
    elif "very wide" in stop_quality:
        score -= 6
    elif "no stop" in stop_quality:
        score -= 20
    return ScoreComponent("Ticket Quality", _clamp_score(score), "; ".join(reason for reason in reasons if reason))


def classify_regime(snapshot: TimeframeTechnicalSnapshot | None) -> str:
    if snapshot is None or snapshot.latest_close is None or snapshot.candle_count <= 0:
        return "unknown"
    trend_score = _snapshot_score(snapshot, "Trend")
    latest = snapshot.latest_close
    ema_21 = snapshot.ema_21
    ema_50 = snapshot.ema_50
    sma_50 = snapshot.sma_50
    above_intermediate = (
        (ema_50 is not None and latest >= ema_50)
        or (sma_50 is not None and latest >= sma_50)
    )
    below_intermediate = (
        (ema_50 is not None and latest <= ema_50)
        or (sma_50 is not None and latest <= sma_50)
    )

    if trend_score >= 64 and snapshot.trend_structure != "lower-high / lower-low":
        return "bullish"
    if snapshot.trend_structure == "higher-high / higher-low" and above_intermediate:
        return "bullish"
    if ema_21 is not None and ema_50 is not None and latest > ema_21 > ema_50:
        return "bullish"
    if trend_score <= 38 and snapshot.trend_structure != "higher-high / higher-low":
        return "bearish"
    if snapshot.trend_structure == "lower-high / lower-low" and below_intermediate:
        return "bearish"
    if ema_21 is not None and ema_50 is not None and latest < ema_21 < ema_50:
        return "bearish"
    return "range"


def classify_setup(
    daily: TimeframeTechnicalSnapshot | None,
    setup: TimeframeTechnicalSnapshot | None,
    timing: TimeframeTechnicalSnapshot | None,
) -> str:
    primary = setup or timing or daily
    if primary is None or primary.latest_close is None:
        return "unknown"
    regime = classify_regime(daily)
    setup_read = build_level_proximity_read(primary)
    timing_read = build_level_proximity_read(timing) if timing is not None else None
    support_broken = _support_broken(primary, setup_read) or (timing is not None and _support_broken(timing, timing_read))
    resistance_reclaimed = _resistance_reclaimed(primary, setup_read)
    distribution = _distribution_heavy(primary) or (timing is not None and _distribution_heavy(timing))
    constructive_volume = _volume_confirms_up(primary) or (timing is not None and _volume_confirms_up(timing))

    if support_broken and (distribution or primary.trend_structure == "lower-high / lower-low" or _snapshot_score(primary, "Trend") <= 44):
        return "breakdown"
    if resistance_reclaimed:
        return "breakout" if constructive_volume else "chop"
    if regime == "bullish" and _support_holding(primary, setup_read) and (_pullback_pressure(primary) or (timing is not None and _pullback_pressure(timing))):
        return "pullback"
    if regime == "bullish" and support_broken:
        return "breakdown"
    if regime == "bearish":
        if _intraday_bounce(primary) or (timing is not None and _intraday_bounce(timing)):
            return "mean-reversion"
        if distribution or _snapshot_score(primary, "Trend") <= 44:
            return "breakdown"
    if _support_holding(primary, setup_read) and _washed_out(primary) and constructive_volume and not distribution:
        return "mean-reversion" if regime != "bullish" else "pullback"
    if primary.trend_structure == "range/chop" or setup_read.range_position == "middle" or primary.range_state == "compressing":
        return "chop"
    if _snapshot_score(primary, "Trend") >= 64 and _snapshot_score(primary, "Momentum") >= 56 and constructive_volume:
        return "breakout" if setup_read.range_position in {"breakout", "near_resistance"} else "pullback"
    return "chop"


def classify_timing(
    daily: TimeframeTechnicalSnapshot | None,
    setup: TimeframeTechnicalSnapshot | None,
    timing: TimeframeTechnicalSnapshot | None,
) -> str:
    if timing is None or timing.latest_close is None:
        return "unknown"
    setup_class = classify_setup(daily, setup, timing)
    timing_read = build_level_proximity_read(timing)
    trend_score = _snapshot_score(timing, "Trend")
    momentum_score = _snapshot_score(timing, "Momentum")
    volume_score = _snapshot_score(timing, "Volume")

    if _support_broken(timing, timing_read) or setup_class == "breakdown":
        return "failed"
    if _timing_extended(timing, timing_read):
        if setup_class == "breakout" and _volume_confirms_up(timing) and volume_score >= 58:
            return "confirmed"
        return "extended"
    if setup_class == "breakout":
        if timing_read.range_position == "breakout" and _volume_confirms_up(timing):
            return "confirmed"
        if trend_score >= 60 and momentum_score >= 55 and _volume_confirms_up(timing):
            return "confirmed"
        return "early"
    if setup_class in {"pullback", "mean-reversion"}:
        if _reversal_evidence(timing):
            return "confirmed"
        return "early"
    if setup_class == "chop":
        return "unknown"
    if trend_score >= 62 and momentum_score >= 56 and volume_score >= 55:
        return "confirmed"
    return "early"


def classify_action_quality(
    regime: str,
    setup: str,
    timing: str,
    level_read: LevelProximityRead | None = None,
    ticket_check: TicketCheck | None = None,
) -> str:
    if setup == "breakdown" or timing == "failed":
        return "protect_or_trim"
    if timing == "extended" or (level_read is not None and level_read.risk_reward_location == "poor"):
        return "avoid_chase"
    if ticket_check is not None:
        if "chasing" in ticket_check.entry_quality:
            return "avoid_chase"
        if "no stop" in ticket_check.stop_quality or "inside normal noise" in ticket_check.stop_quality:
            return "wait_for_trigger"
    if setup == "chop" or regime == "unknown" or timing == "unknown":
        return "no_edge"
    if setup == "breakout" and timing == "confirmed":
        return "good_entry"
    if setup == "pullback" and timing == "confirmed" and level_read is not None and level_read.risk_reward_location == "good":
        return "good_entry"
    if setup == "mean-reversion" and timing == "confirmed" and regime != "bearish":
        return "good_entry"
    return "wait_for_trigger"


def build_level_proximity_read(
    snapshot: TimeframeTechnicalSnapshot,
    *,
    entry_price: float | None = None,
    stop_price: float | None = None,
    side: str = "buy",
) -> LevelProximityRead:
    latest = snapshot.latest_close
    reference = entry_price if entry_price is not None and entry_price > 0 else latest
    support = _nearest_support(snapshot)
    resistance = _nearest_resistance(snapshot)
    if reference is None or reference <= 0:
        return LevelProximityRead(
            nearest_support=support,
            nearest_resistance=resistance,
            distance_to_support_percent=None,
            distance_to_resistance_percent=None,
            range_position="unknown",
            risk_reward_location="unknown",
            stop_atr_multiple=None,
            stop_read="Price reference unavailable for stop/ATR read.",
            lines=["Level proximity unavailable because the snapshot has no latest price."],
        )

    distance_to_support = _percent(reference - support.center, reference) if support is not None else None
    distance_to_resistance = _percent(resistance.center - reference, reference) if resistance is not None else None
    range_position = _range_position(reference, support, resistance, snapshot.atr_percent)
    risk_reward_location = _risk_reward_location(side, range_position, distance_to_support, distance_to_resistance, snapshot)
    stop_atr_multiple, stop_read = _stop_atr_read(side, reference, stop_price, snapshot.atr_14, support, resistance)
    lines = [
        f"Nearest support: {format_level(support)}; distance {_fmt_percent(distance_to_support)}.",
        f"Nearest resistance: {format_level(resistance)}; distance {_fmt_percent(distance_to_resistance)}.",
        f"Location: {_humanize_classification_value(range_position)}; risk/reward location is {risk_reward_location}.",
        f"Stop/ATR read: {stop_read}",
    ]
    return LevelProximityRead(
        nearest_support=support,
        nearest_resistance=resistance,
        distance_to_support_percent=distance_to_support,
        distance_to_resistance_percent=distance_to_resistance,
        range_position=range_position,
        risk_reward_location=risk_reward_location,
        stop_atr_multiple=stop_atr_multiple,
        stop_read=stop_read,
        lines=lines,
    )


def build_technical_setup_classification(
    snapshots: dict[str, TimeframeTechnicalSnapshot],
    *,
    ticket_check: TicketCheck | None = None,
) -> TechnicalSetupClassification:
    daily = _preferred_snapshot(snapshots, "regime")
    setup_snapshot = _preferred_snapshot(snapshots, "setup")
    timing = _preferred_snapshot(snapshots, "timing")
    level_snapshot = timing or setup_snapshot or daily
    regime = classify_regime(daily)
    setup = classify_setup(daily, setup_snapshot, timing)
    timing_read = classify_timing(daily, setup_snapshot, timing)
    level_read = build_level_proximity_read(level_snapshot) if level_snapshot is not None else None
    action_quality = classify_action_quality(regime, setup, timing_read, level_read, ticket_check)
    invalidation_level = level_read.nearest_support.low if level_read is not None and level_read.nearest_support is not None else None
    confirmation_level = level_read.nearest_resistance.high if level_read is not None and level_read.nearest_resistance is not None else None
    rsi_context = [
        read
        for read in (
            build_rsi_context_read(daily, regime=regime),
            build_rsi_context_read(setup_snapshot, regime=regime),
            build_rsi_context_read(timing, regime=regime),
        )
        if read is not None
    ]
    warnings = _classification_warnings(regime, setup, timing_read, daily, setup_snapshot, timing, level_read, rsi_context, ticket_check)
    confidence = _classification_confidence(regime, setup, timing_read, snapshots, level_read, warnings)
    main_reason = _classification_main_reason(regime, setup, timing_read, level_read, ticket_check)
    lines = [
        f"Daily regime answers structural health: {regime}.",
        f"30m setup answers tradability: {setup}.",
        f"5m/1m timing answers entry quality now: {timing_read}.",
    ]
    if level_read is not None:
        lines.extend(level_read.lines)
    lines.extend(read.context for read in rsi_context if read.context)

    return TechnicalSetupClassification(
        regime=regime,
        setup=setup,
        timing=timing_read,
        action_quality=action_quality,
        confidence=confidence,
        invalidation_level=invalidation_level,
        confirmation_level=confirmation_level,
        main_reason=main_reason,
        warnings=_dedupe(warnings),
        level_proximity=level_read,
        rsi_context=rsi_context,
        lines=lines,
    )


def build_rsi_context_read(snapshot: TimeframeTechnicalSnapshot | None, *, regime: str = "unknown") -> RsiContextRead | None:
    if snapshot is None:
        return None
    value = snapshot.rsi_14
    if value is None:
        return RsiContextRead(None, "unknown", f"{snapshot.label} RSI unavailable.", "")
    zone = _rsi_zone(value)
    if zone == "constructive_neutral":
        context = f"{snapshot.label} RSI {value:.1f}: constructive neutral, useful only with trend/level confirmation."
        warning = ""
    elif zone == "soft_pullback":
        context = f"{snapshot.label} RSI {value:.1f}: soft pullback zone."
        warning = "" if regime == "bullish" else "Soft RSI is not bullish without a constructive higher-timeframe regime."
    elif zone == "washed_out":
        context = f"{snapshot.label} RSI {value:.1f}: washed out; constructive only if support and regime hold."
        warning = "Washed-out RSI needs support/reversal evidence before it is constructive."
    elif zone == "dangerous_oversold":
        context = f"{snapshot.label} RSI {value:.1f}: oversold but dangerous unless reversal evidence appears."
        warning = "RSI below 25 is not a buy signal by itself."
    elif zone == "extended":
        context = f"{snapshot.label} RSI {value:.1f}: extended; avoid chasing unless breakout volume confirms."
        warning = "RSI above 70 raises chase risk without volume-confirmed breakout evidence."
    else:
        context = f"{snapshot.label} RSI {value:.1f}: neutral context."
        warning = ""
    return RsiContextRead(value, zone, context, warning)


def _snapshot_score(snapshot: TimeframeTechnicalSnapshot | None, name: str, fallback: float = 50.0) -> float:
    if snapshot is None:
        return fallback
    return snapshot.scores.get(name, ScoreComponent(name, fallback, "")).score


def _range_position(
    reference: float,
    support: TechnicalLevel | None,
    resistance: TechnicalLevel | None,
    atr_percent: float | None,
) -> str:
    proximity_threshold = max(0.75, min(2.25, (atr_percent or 1.5) * 0.85))
    if support is not None and reference < support.low:
        return "breakdown"
    if resistance is not None and reference > resistance.high:
        return "breakout"
    if support is not None and support.low <= reference <= support.high:
        return "near_support"
    if resistance is not None and resistance.low <= reference <= resistance.high:
        return "near_resistance"
    if support is not None:
        support_gap = _percent(reference - support.high, reference)
        if support_gap is not None and 0 <= support_gap <= proximity_threshold:
            return "near_support"
    if resistance is not None:
        resistance_gap = _percent(resistance.low - reference, reference)
        if resistance_gap is not None and 0 <= resistance_gap <= proximity_threshold:
            return "near_resistance"
    if support is not None and resistance is not None:
        range_width = resistance.center - support.center
        if range_width > 0:
            position = (reference - support.center) / range_width
            if position <= 0.30:
                return "near_support"
            if position >= 0.70:
                return "near_resistance"
            return "middle"
    return "unknown"


def _risk_reward_location(
    side: str,
    range_position: str,
    distance_to_support: float | None,
    distance_to_resistance: float | None,
    snapshot: TimeframeTechnicalSnapshot,
) -> str:
    clean_side = side.lower()
    if range_position == "unknown":
        return "unknown"
    if range_position == "middle":
        return "no_edge"
    if clean_side == "sell":
        if range_position in {"near_resistance", "breakdown"}:
            return "good"
        if range_position in {"near_support", "breakout"}:
            return "poor"
    else:
        if range_position == "near_support":
            return "good"
        if range_position == "breakout":
            return "good" if _volume_confirms_up(snapshot) else "poor"
        if range_position == "near_resistance":
            return "poor"
        if range_position == "breakdown":
            return "poor"
    if distance_to_support is not None and distance_to_resistance is not None:
        if clean_side == "sell":
            return "good" if distance_to_resistance < distance_to_support else "poor"
        return "good" if distance_to_support < distance_to_resistance else "poor"
    return "unknown"


def _stop_atr_read(
    side: str,
    reference: float,
    stop_price: float | None,
    atr_14: float | None,
    support: TechnicalLevel | None,
    resistance: TechnicalLevel | None,
) -> tuple[float | None, str]:
    clean_side = side.lower()
    if atr_14 is None or atr_14 <= 0:
        return None, "ATR unavailable, so stop placement cannot be normalized."
    if stop_price is not None and stop_price > 0:
        risk = reference - stop_price if clean_side != "sell" else stop_price - reference
        if risk <= 0:
            return None, "Stop is on the wrong side of entry for this side."
        multiple = risk / atr_14
        if multiple < 0.70:
            return multiple, f"Stop is inside normal ATR noise at {multiple:.2f}x ATR."
        if multiple > 3.0:
            return multiple, f"Stop is wide at {multiple:.2f}x ATR."
        return multiple, f"Stop distance is reasonable at {multiple:.2f}x ATR."
    if clean_side == "sell" and resistance is not None:
        multiple = max(resistance.high - reference, 0.0) / atr_14
        return multiple, f"No ticket stop; a stop above resistance would be about {multiple:.2f}x ATR from reference."
    if clean_side != "sell" and support is not None:
        multiple = max(reference - support.low, 0.0) / atr_14
        return multiple, f"No ticket stop; a stop below support would be about {multiple:.2f}x ATR from reference."
    return None, "No stop and no nearby level were available for a stop/ATR read."


def _support_broken(snapshot: TimeframeTechnicalSnapshot | None, read: LevelProximityRead | None) -> bool:
    if snapshot is None or snapshot.latest_close is None:
        return False
    if read is not None and read.range_position == "breakdown":
        return True
    support = read.nearest_support if read is not None else _nearest_support(snapshot)
    return support is not None and snapshot.latest_close < support.low


def _support_holding(snapshot: TimeframeTechnicalSnapshot | None, read: LevelProximityRead | None) -> bool:
    if snapshot is None or snapshot.latest_close is None:
        return False
    if _support_broken(snapshot, read):
        return False
    if read is not None and read.range_position == "near_support":
        return True
    support = read.nearest_support if read is not None else _nearest_support(snapshot)
    if support is None:
        return False
    distance = _percent(snapshot.latest_close - support.high, snapshot.latest_close)
    return distance is not None and 0 <= distance <= max(2.0, snapshot.atr_percent or 0.0)


def _resistance_reclaimed(snapshot: TimeframeTechnicalSnapshot | None, read: LevelProximityRead | None) -> bool:
    if snapshot is None or snapshot.latest_close is None:
        return False
    if read is not None and read.range_position == "breakout":
        return True
    resistance = read.nearest_resistance if read is not None else _nearest_resistance(snapshot)
    return resistance is not None and snapshot.latest_close > resistance.high


def _volume_confirms_up(snapshot: TimeframeTechnicalSnapshot | None) -> bool:
    if snapshot is None:
        return False
    volume = snapshot.volume_read
    relative = volume.relative_volume
    return (
        volume.accumulation_read == "accumulation"
        or (relative is not None and relative >= 1.15 and (snapshot.close_location is None or snapshot.close_location >= 0.0))
        or _snapshot_score(snapshot, "Volume") >= 62
    )


def _distribution_heavy(snapshot: TimeframeTechnicalSnapshot | None) -> bool:
    if snapshot is None:
        return False
    volume = snapshot.volume_read
    return (
        volume.accumulation_read == "distribution"
        or _snapshot_score(snapshot, "Volume") <= 38
        or (snapshot.close_location is not None and snapshot.close_location <= -0.45 and (volume.relative_volume or 0.0) >= 1.0)
    )


def _pullback_pressure(snapshot: TimeframeTechnicalSnapshot | None) -> bool:
    if snapshot is None:
        return False
    rsi_read = build_rsi_context_read(snapshot)
    rsi_zone = rsi_read.zone if rsi_read is not None else "unknown"
    return (
        rsi_zone in {"soft_pullback", "washed_out", "dangerous_oversold"}
        or _snapshot_score(snapshot, "Momentum") <= 48
        or (snapshot.vwap_distance_percent is not None and snapshot.vwap_distance_percent < -0.60)
        or (snapshot.macd_histogram is not None and snapshot.macd_histogram < 0)
    )


def _washed_out(snapshot: TimeframeTechnicalSnapshot | None) -> bool:
    if snapshot is None or snapshot.rsi_14 is None:
        return False
    return snapshot.rsi_14 < 35


def _intraday_bounce(snapshot: TimeframeTechnicalSnapshot | None) -> bool:
    if snapshot is None:
        return False
    return (
        _snapshot_score(snapshot, "Momentum") >= 56
        or (snapshot.macd_histogram_change is not None and snapshot.macd_histogram_change > 0)
        or (snapshot.roc_5 is not None and snapshot.roc_5 > 0)
    )


def _reversal_evidence(snapshot: TimeframeTechnicalSnapshot | None) -> bool:
    if snapshot is None:
        return False
    return (
        (snapshot.macd_histogram_change is not None and snapshot.macd_histogram_change > 0)
        and (snapshot.close_location is None or snapshot.close_location >= 0.0)
        and not _distribution_heavy(snapshot)
    ) or (_volume_confirms_up(snapshot) and (snapshot.roc_5 or 0.0) > 0)


def _timing_extended(snapshot: TimeframeTechnicalSnapshot, read: LevelProximityRead) -> bool:
    return (
        (snapshot.rsi_14 is not None and snapshot.rsi_14 > 70)
        or (snapshot.vwap_distance_percent is not None and snapshot.vwap_distance_percent > 4.0)
        or (read.range_position == "near_resistance" and not _volume_confirms_up(snapshot))
    )


def _rsi_zone(value: float) -> str:
    if 45 <= value <= 60:
        return "constructive_neutral"
    if 35 <= value < 45:
        return "soft_pullback"
    if 25 <= value < 35:
        return "washed_out"
    if value < 25:
        return "dangerous_oversold"
    if value > 70:
        return "extended"
    return "neutral"


def _classification_warnings(
    regime: str,
    setup: str,
    timing: str,
    daily: TimeframeTechnicalSnapshot | None,
    setup_snapshot: TimeframeTechnicalSnapshot | None,
    timing_snapshot: TimeframeTechnicalSnapshot | None,
    level_read: LevelProximityRead | None,
    rsi_context: list[RsiContextRead],
    ticket_check: TicketCheck | None,
) -> list[str]:
    warnings: list[str] = [read.warning for read in rsi_context if read.warning]
    primary = setup_snapshot or timing_snapshot or daily
    if setup == "breakout" and primary is not None and not _volume_confirms_up(primary):
        warnings.append("Breakout label needs volume confirmation; current volume confirmation is incomplete.")
    if setup == "pullback" and primary is not None and _distribution_heavy(primary):
        warnings.append("Pullback is lower quality because volume looks distribution-heavy.")
    if setup == "breakdown":
        warnings.append("Support broke or lower-high/lower-low structure is deteriorating; do not treat oversold RSI as bullish by itself.")
    if regime == "bearish" and setup == "mean-reversion":
        warnings.append("Intraday strength is a countertrend bounce inside a bearish daily regime, not a trend reversal.")
    if level_read is not None:
        if level_read.range_position == "middle":
            warnings.append("Price is in the middle of the range; level location has no clear edge.")
        if level_read.range_position == "near_resistance" and setup != "breakout":
            warnings.append("Entry location is close to resistance; chase risk is elevated.")
        if level_read.range_position == "breakdown":
            warnings.append("Support broke on the active technical timeframe.")
    if timing == "extended":
        warnings.append("Timing is extended; avoid chasing unless the breakout remains volume-confirmed.")
    if ticket_check is not None:
        if "no stop" in ticket_check.stop_quality:
            warnings.append("No stop means ticket risk is undefined.")
        if "inside normal noise" in ticket_check.stop_quality:
            warnings.append("Ticket stop is inside normal ATR noise.")
        if "chasing" in ticket_check.entry_quality:
            warnings.append("Ticket entry is chasing relative to nearby resistance/extension.")
    return warnings


def _classification_confidence(
    regime: str,
    setup: str,
    timing: str,
    snapshots: dict[str, TimeframeTechnicalSnapshot],
    level_read: LevelProximityRead | None,
    warnings: list[str],
) -> str:
    usable = sum(1 for snapshot in snapshots.values() if snapshot.candle_count >= 35)
    clear_setup = regime != "unknown" and setup != "unknown" and timing != "unknown"
    has_levels = level_read is not None and (level_read.nearest_support is not None or level_read.nearest_resistance is not None)
    severe_warning = any("broke" in warning.lower() or "undefined" in warning.lower() for warning in warnings)
    if usable >= 3 and clear_setup and has_levels and not severe_warning and setup != "chop":
        return "high"
    if usable >= 2 and clear_setup and has_levels:
        return "medium"
    return "low"


def _classification_main_reason(
    regime: str,
    setup: str,
    timing: str,
    level_read: LevelProximityRead | None,
    ticket_check: TicketCheck | None,
) -> str:
    level_text = ""
    if level_read is not None:
        level_text = f" Price is {_humanize_classification_value(level_read.range_position)} with {level_read.risk_reward_location} risk/reward location."
    ticket_text = f" Ticket: {ticket_check.verdict}" if ticket_check is not None else ""
    return (
        f"Daily regime is {regime}; 30m setup is {setup}; near-term timing is {timing}."
        f"{level_text}{ticket_text}"
    )


def _humanize_classification_value(value: str) -> str:
    return value.replace("_", " ")


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
        f"- Setup classification: {report.setup_classification.setup}; action quality {report.setup_classification.action_quality}.",
        f"- Risk heat: {report.scores['Volatility/Risk'].score:.0f}/100; {report.scores['Volatility/Risk'].reason}.",
        f"- Best action: {report.best_action}.",
        f"- Confidence: {report.confidence}.",
        "- This is analysis, not a trade recommendation.",
    ]
    if report.capital_structure_indicator is not None:
        lines.insert(
            8,
            f"- Capital structure / supply: {report.capital_structure_indicator.read} "
            f"({report.capital_structure_indicator.technical_score:.0f}/100).",
        )
    lines.extend(_format_setup_classification_section(report.setup_classification))
    lines.extend(_format_prc_report_section(report, daily, setup, timing))
    if report.capital_structure_pressure is not None:
        lines.extend(
            format_capital_structure_pressure_section(
                report.capital_structure_pressure,
                technical_read=report.overall_read,
            )
        )
    if report.capital_structure_indicator is not None:
        lines.extend(_format_capital_structure_indicator_section(report.capital_structure_indicator))
    lines.extend(["", "SCORE BREAKDOWN"])
    for name, component in report.scores.items():
        lines.append(f"- {name}: {component.score:.0f}/100 because {component.reason}.")
    lines.extend(["", "TIMEFRAME STACK"])
    for snapshot in report.snapshots.values():
        source_suffix = f" Source: {snapshot.source} / {snapshot.freshness}." if snapshot.source else ""
        lines.append(f"- {snapshot.label}: {_short_snapshot_read(snapshot)}{source_suffix}")
    lines.append(f"- Alignment: {report.scores['Alignment'].reason}.")
    lines.extend(_format_timeframe_source_section(report))
    lines.extend(_format_source_routing_plan_section(report))

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
    lines.append(f"- Risk/reward read: {report.ticket_check.risk_reward}")
    lines.append(f"- Verdict: {report.ticket_check.verdict}.")

    lines.extend(["", "PLAIN-ENGLISH PLAN"])
    if report.capital_structure_pressure is not None:
        lines.append(
            f"- {capital_structure_technical_modifier(report.overall_read, report.capital_structure_pressure)}"
        )
    lines.extend(f"- {line}" for line in report.plain_english_plan)
    lines.extend(_format_market_intelligence_section(report))
    if report.warnings:
        lines.extend(["", "DATA WARNINGS"])
        lines.extend(f"- {warning}" for warning in report.warnings)
    return "\n".join(lines)


def _format_market_intelligence_section(report: TechnicalCommandCenterReport) -> list[str]:
    intelligence = getattr(report, "market_intelligence", None)
    statuses = tuple(getattr(report, "market_intelligence_source_statuses", ()) or ())
    decision = getattr(report, "market_intelligence_decision", None)
    if intelligence is None and not statuses:
        return []

    lines = ["", "MARKET INTELLIGENCE SOURCES", "---------------------------"]
    if statuses:
        for status in statuses:
            source = str(getattr(status, "source", "") or "Market intelligence source")
            state = str(getattr(status, "status", "") or "unknown")
            fetched_at = str(getattr(status, "fetched_at", "") or "--")
            message = str(getattr(status, "message", "") or "")
            lines.append(f"- {source}: {state}; fetched {fetched_at}; {message}")
    else:
        lines.append("- No external market-intelligence source status rows were reported.")

    if intelligence is None:
        return lines

    if decision is not None:
        decision_lines = list(getattr(decision, "decision_lines", []) or [])
        conflicts = list(getattr(decision, "source_conflicts", []) or [])
        if decision_lines or conflicts:
            lines.extend(["", "DECISION-WEIGHTED READ", "----------------------"])
            lines.extend(f"- {line}" for line in decision_lines)
            lines.extend(f"- {line}" for line in conflicts)

    context_lines = _format_market_intelligence_context(intelligence)
    if context_lines:
        lines.extend(["", "MARKET INTELLIGENCE CONTEXT", "---------------------------"])
        lines.extend(context_lines)
    return lines


def _format_timeframe_source_section(report: TechnicalCommandCenterReport) -> list[str]:
    source_reads = getattr(report, "timeframe_source_labels", {}) or {}
    if not source_reads:
        return []
    lines = ["", "TIMEFRAME DATA SOURCES", "----------------------"]
    for key, read in source_reads.items():
        lines.append(f"- {read.label} ({key}): {read.source}; {read.freshness}; {read.candle_count} candle(s). {read.reason}")
    return lines


def _format_source_routing_plan_section(report: TechnicalCommandCenterReport) -> list[str]:
    data_plan = getattr(report, "data_plan", None)
    decisions = tuple(getattr(data_plan, "decisions", ()) or ())
    if not decisions:
        return []
    lines = ["", "SOURCE ROUTING PLAN", "-------------------"]
    for decision in decisions:
        if decision.domain == "provider_diagnostics":
            status_rows = tuple((decision.diagnostics or {}).get("statuses", ()) or ())
            if status_rows:
                lines.append(f"- Provider diagnostics: {len(status_rows)} status row(s) captured; showing first 8.")
                lines.extend(f"  - {row}" for row in status_rows[:8])
            continue
        fallback = f"; fallback: {', '.join(decision.fallback_sources)}" if decision.fallback_sources else ""
        reason = f" {decision.reason}" if decision.reason else ""
        lines.append(
            f"- {decision.domain.replace('_', ' ').replace(':', ' / ')}: "
            f"{decision.selected_source}; status {decision.status}{fallback}.{reason}"
        )
        diag_text = _short_decision_diagnostics(decision.diagnostics)
        if diag_text:
            lines.append(f"  - Diagnostics: {diag_text}")
    return lines


def _short_decision_diagnostics(diagnostics: Mapping[str, Any]) -> str:
    if not diagnostics:
        return ""
    parts: list[str] = []
    for key, value in diagnostics.items():
        if value in (None, "", (), [], {}):
            continue
        if key == "conflicts" and value:
            parts.append(f"conflicts={len(value)}")
            continue
        if isinstance(value, Mapping):
            nested = ",".join(str(nested_key) for nested_key in list(value)[:4])
            parts.append(f"{key}={nested or 'present'}")
            continue
        if isinstance(value, (tuple, list)):
            parts.append(f"{key}={len(value)}")
            continue
        parts.append(f"{key}={value}")
        if len(parts) >= 5:
            break
    return "; ".join(parts)


def _format_market_intelligence_context(intelligence: Any) -> list[str]:
    lines: list[str] = []
    fmp_profile = _external_mapping(getattr(intelligence, "fmp_profile", None))
    fmp_quote = _external_mapping(getattr(intelligence, "fmp_quote", None))
    fmp_fundamentals = _external_mapping(getattr(intelligence, "fmp_fundamentals", None))
    equity_tape = _external_mapping(getattr(intelligence, "databento_equity_tape", None))
    futures_context = _external_mapping(getattr(intelligence, "databento_futures_context", None))
    equity_candles = getattr(intelligence, "databento_equity_candles", None)
    technical_candles = getattr(intelligence, "databento_technical_candles", None)

    profile_bits = [
        _optional_text(fmp_profile.get("company_name")),
        _optional_text(fmp_profile.get("exchange")),
        _slash_join(_optional_text(fmp_profile.get("sector")), _optional_text(fmp_profile.get("industry"))),
    ]
    profile_line = "; ".join(bit for bit in profile_bits if bit)
    if profile_line:
        lines.append(f"- FMP profile/classification: {profile_line}.")

    quote_bits = [
        f"price {_money(_optional_number(fmp_quote.get('price')))}" if _optional_number(fmp_quote.get("price")) is not None else "",
        f"volume {_format_optional(_optional_number(fmp_quote.get('volume')))}" if _optional_number(fmp_quote.get("volume")) is not None else "",
        f"change {_fmt_percent(_optional_number(fmp_quote.get('change_percent')))}" if _optional_number(fmp_quote.get("change_percent")) is not None else "",
    ]
    if any(quote_bits):
        lines.append(f"- FMP quote/tape: {', '.join(bit for bit in quote_bits if bit)}.")

    fundamental_bits = [
        f"market cap {_money(_optional_number(fmp_fundamentals.get('market_cap')))}" if _optional_number(fmp_fundamentals.get("market_cap")) is not None else "",
        f"P/E {_format_optional(_optional_number(fmp_fundamentals.get('pe_ratio')))}" if _optional_number(fmp_fundamentals.get("pe_ratio")) is not None else "",
        f"EPS {_format_optional(_optional_number(fmp_fundamentals.get('eps')))}" if _optional_number(fmp_fundamentals.get("eps")) is not None else "",
        f"revenue growth {_fmt_percent(_optional_number(fmp_fundamentals.get('revenue_growth')))}" if _optional_number(fmp_fundamentals.get("revenue_growth")) is not None else "",
        f"float {_format_optional(_optional_number(fmp_fundamentals.get('shares_float')))}" if _optional_number(fmp_fundamentals.get("shares_float")) is not None else "",
        f"shares out {_format_optional(_optional_number(fmp_fundamentals.get('shares_outstanding')))}" if _optional_number(fmp_fundamentals.get("shares_outstanding")) is not None else "",
    ]
    if any(fundamental_bits):
        lines.append(f"- FMP fundamentals: {', '.join(bit for bit in fundamental_bits if bit)}.")

    equity_bits = [
        f"price {_money(_optional_number(equity_tape.get('price')))}" if _optional_number(equity_tape.get("price")) is not None else "",
        f"volume {_format_optional(_optional_number(equity_tape.get('volume')))}" if _optional_number(equity_tape.get("volume")) is not None else "",
        f"change {_fmt_percent(_optional_number(equity_tape.get('change_percent')))}" if _optional_number(equity_tape.get("change_percent")) is not None else "",
        f"avg volume {_format_optional(_optional_number(equity_tape.get('avg_volume')))}" if _optional_number(equity_tape.get("avg_volume")) is not None else "",
        f"fresh {_optional_text(equity_tape.get('fetched_at'))}" if _optional_text(equity_tape.get("fetched_at")) else "",
    ]
    candle_count = _external_candle_count(equity_candles)
    if candle_count:
        equity_bits.append(f"{candle_count} Databento candle row(s)")
    if any(equity_bits):
        lines.append(f"- Databento US equities tape/candles: {', '.join(bit for bit in equity_bits if bit)}.")
    technical_rows = _external_timeframe_candle_counts(technical_candles)
    if technical_rows:
        rows_text = ", ".join(f"{key} {count}" for key, count in technical_rows.items())
        lines.append(f"- Databento timeframe-aware technical history: {rows_text} candle row(s).")

    futures_lines = _format_futures_context_lines(futures_context)
    if futures_lines:
        lines.append("- Databento CME/futures cross-asset context is kept separate from selected-equity quote/fundamental fields.")
        lines.extend(futures_lines)

    return lines


def _format_futures_context_lines(futures_context: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    for symbol, raw in list(futures_context.items())[:6]:
        payload = _external_mapping(raw)
        price = _optional_number(payload.get("price"))
        volume = _optional_number(payload.get("volume"))
        timestamp = _optional_text(payload.get("timestamp") or payload.get("fetched_at"))
        bits = [
            f"price {_money(price)}" if price is not None else "",
            f"volume {_format_optional(volume)}" if volume is not None else "",
            f"time {timestamp}" if timestamp else "",
        ]
        if any(bits):
            lines.append(f"  - {symbol}: {', '.join(bit for bit in bits if bit)}.")
    return lines


def _external_candle_count(candles_by_symbol: Any) -> int:
    if not isinstance(candles_by_symbol, Mapping):
        return 0
    count = 0
    for rows in candles_by_symbol.values():
        try:
            count += len(rows)
        except TypeError:
            continue
    return count


def _external_timeframe_candle_counts(candles_by_timeframe: Any) -> dict[str, int]:
    if not isinstance(candles_by_timeframe, Mapping):
        return {}
    result: dict[str, int] = {}
    for key, rows in candles_by_timeframe.items():
        try:
            count = len(rows)
        except TypeError:
            count = 0
        if count:
            result[str(key)] = count
    return result


def _external_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _optional_number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_text(value: Any) -> str:
    return str(value).strip() if value not in (None, "") else ""


def _slash_join(left: str, right: str) -> str:
    if left and right:
        return f"{left}/{right}"
    return left or right


def _format_capital_structure_indicator_section(indicator: CapitalStructureIndicatorRead) -> list[str]:
    lines = [
        "",
        "CAPITAL STRUCTURE / SUPPLY INDICATOR",
        "------------------------------------",
        f"- Read: {indicator.read}.",
        f"- Technical score: {indicator.technical_score:.0f}/100.",
        f"- Supply overhang risk: {indicator.supply_overhang_score:.0f}/100.",
        f"- Dilution pressure risk: {indicator.dilution_pressure_score:.0f}/100.",
        f"- Warrant/conversion level proximity risk: {indicator.warrant_conversion_proximity_score:.0f}/100.",
        f"- Offering activity risk: {indicator.offering_activity_score:.0f}/100.",
        f"- Float quality score: {indicator.float_quality_score:.0f}/100.",
        f"- Foreign issuer confidence modifier: {indicator.foreign_issuer_confidence_modifier:+.0f}.",
        f"- Option exposure mismatch risk: {indicator.option_exposure_mismatch_score:.0f}/100.",
        "- Filing-derived levels are risk/context modifiers, not support, resistance, target, or price-prediction claims.",
    ]
    if indicator.nearest_supply_level is not None:
        lines.append(
            f"- Nearest filing supply level: {indicator.nearest_supply_level_label or 'parsed level'} "
            f"{_money(indicator.nearest_supply_level)} ({_fmt_percent(indicator.nearest_supply_level_distance_percent)} from latest)."
        )
    lines.extend(f"- {line}" for line in indicator.explanation_lines[:6])
    if indicator.warnings:
        lines.extend(f"- Warning: {warning}" for warning in indicator.warnings[:4])
    return lines


def _format_setup_classification_section(classification: TechnicalSetupClassification) -> list[str]:
    lines = [
        "",
        "SETUP CLASSIFICATION",
        "--------------------",
        f"- Regime: {classification.regime}.",
        f"- Setup: {classification.setup}.",
        f"- Timing: {classification.timing}.",
        f"- Action quality: {_humanize_classification_value(classification.action_quality)}.",
        f"- Confidence: {classification.confidence}.",
        f"- Confirmation level: {_money(classification.confirmation_level)}.",
        f"- Invalidation / stop logic: {_money(classification.invalidation_level)}.",
        f"- Main reason: {classification.main_reason}",
    ]
    if classification.level_proximity is not None:
        proximity = classification.level_proximity
        lines.extend(
            [
                f"- Level location: {_humanize_classification_value(proximity.range_position)}; risk/reward location {proximity.risk_reward_location}.",
                f"- Distance to support: {_fmt_percent(proximity.distance_to_support_percent)}.",
                f"- Distance to resistance: {_fmt_percent(proximity.distance_to_resistance_percent)}.",
                f"- Stop/ATR read: {proximity.stop_read}",
            ]
        )
    if classification.rsi_context:
        lines.append("- RSI context:")
        lines.extend(f"  - {read.context}" for read in classification.rsi_context)
    if classification.warnings:
        lines.append("- Warnings:")
        lines.extend(f"  - {warning}" for warning in classification.warnings)
    return lines


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
        if resistance is not None and entry > resistance.high:
            return "breakout entry only"
        if resistance is not None and entry >= resistance.low * 0.995:
            return "entry is chasing near resistance"
        if support is not None and support.low * 0.995 <= entry <= support.high * 1.025:
            return "entry near support"
        if support is not None and resistance is not None and support.center < entry < resistance.center:
            midpoint = support.center + ((resistance.center - support.center) * 0.50)
            if entry >= midpoint:
                return "middle of range / no edge"
        if extended_from_vwap and entry > latest:
            return "pullback entry is better"
        if entry > latest * 1.03:
            return "wait for confirmation"
    else:
        if support is not None and entry < support.low:
            return "breakdown entry only"
        if support is not None and entry <= support.high * 1.005:
            return "entry is chasing near support"
        if resistance is not None and resistance.low * 0.975 <= entry <= resistance.high * 1.005:
            return "entry near resistance"
        if support is not None and resistance is not None and support.center < entry < resistance.center:
            midpoint = support.center + ((resistance.center - support.center) * 0.50)
            if entry <= midpoint:
                return "middle of range / no edge"
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
    if "chasing" in entry_quality:
        return "Entry is chasing; wait for a better location or confirmed breakout."
    if score >= 70:
        return "Risk is defined and technically coherent."
    if entry_quality in {"pullback entry is better", "watch"}:
        return "Pullback entry is better."
    if entry_quality == "middle of range / no edge":
        return "Middle of range; no clear ticket edge."
    if "breakout" in entry_quality:
        return "Wait for breakout confirmation."
    if "no stop" in stop_quality:
        return "No stop means risk is undefined."
    return "Wait for confirmation."


def _ticket_risk_reward_read(
    side: str,
    entry: float | None,
    stop: float | None,
    support: TechnicalLevel | None,
    resistance: TechnicalLevel | None,
) -> tuple[str, float | None, float | None, str]:
    if entry is None or stop is None:
        return "Reward/risk: unavailable because entry or stop is missing.", None, None, "unknown"
    if side == "buy":
        target = resistance.center if resistance is not None and resistance.center > entry else None
    else:
        target = support.center if support is not None and support.center < entry else None
    if target is None:
        return "Reward/risk: unavailable because a nearby target level was not available.", None, None, "unknown"
    risk = entry - stop if side == "buy" else stop - entry
    reward = target - entry if side == "buy" else entry - target
    if risk <= 0:
        return "Reward/risk: invalid because the stop is on the wrong side of entry.", None, target, "poor"
    if reward <= 0:
        return f"Reward/risk: poor because nearest target {_money(target)} is not beyond entry.", 0.0, target, "poor"
    ratio = reward / risk
    if ratio >= 2.0:
        read = "good"
        label = "favorable"
    elif ratio >= 1.2:
        read = "acceptable"
        label = "acceptable"
    else:
        read = "poor"
        label = "poor"
    return f"Reward/risk: {label} at {ratio:.2f}:1 using target {_money(target)}.", ratio, target, read


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
    *,
    capital_structure_indicator: CapitalStructureIndicatorRead | None = None,
    market_intelligence_decision: MarketIntelligenceDecisionRead | None = None,
) -> str:
    usable_timeframes = sum(1 for snapshot in snapshots.values() if snapshot.candle_count >= 35)
    usable_benchmarks = sum(1 for read in benchmark_reads if read.verdict != "unknown")
    if usable_timeframes >= 3 and usable_benchmarks >= 2 and not warnings:
        confidence = "High"
    elif usable_timeframes >= 2:
        confidence = "Medium"
    else:
        confidence = "Low"
    if capital_structure_indicator is not None:
        if capital_structure_indicator.foreign_issuer_confidence_modifier < 0 or capital_structure_indicator.technical_score < 45:
            confidence = _downgrade_confidence(confidence)
        elif capital_structure_indicator.chase_risk_score >= 75 and confidence == "High":
            confidence = "Medium"
    if market_intelligence_decision is not None:
        for _index in range(max(0, market_intelligence_decision.confidence_penalty)):
            confidence = _downgrade_confidence(confidence)
    return confidence


def _downgrade_confidence(confidence: str) -> str:
    if confidence == "High":
        return "Medium"
    return "Low"


def _best_action(
    overall_read: str,
    snapshots: dict[str, TimeframeTechnicalSnapshot],
    ticket_check: TicketCheck,
    *,
    capital_structure_indicator: CapitalStructureIndicatorRead | None = None,
    market_intelligence_decision: MarketIntelligenceDecisionRead | None = None,
) -> str:
    timing = _preferred_snapshot(snapshots, "timing")
    if market_intelligence_decision is not None and market_intelligence_decision.source_conflicts:
        return "Wait for source confirmation"
    if "inside normal noise" in ticket_check.stop_quality:
        return "Wait / widen or relocate stop"
    if capital_structure_indicator is not None:
        if capital_structure_indicator.read == "supply_absorption":
            return "Watch for absorption"
        if capital_structure_indicator.read == "rally_fade_risk" or capital_structure_indicator.chase_risk_score >= 75:
            return "Avoid chase"
    if ticket_check.entry_quality in {"pullback entry is better", "watch"}:
        return "Pullback / wait"
    if ticket_check.entry_quality in {"breakout entry only", "breakdown entry only"}:
        return "Breakout trigger only"
    if timing and timing.vwap_distance_percent is not None and timing.vwap_distance_percent > 4:
        return "Avoid chase"
    if capital_structure_indicator is not None:
        if overall_read == "Bullish" and capital_structure_indicator.technical_score < 58:
            return "Breakout trigger only"
        if capital_structure_indicator.read == "verification_needed" and overall_read in {"Bullish", "Mixed"}:
            return "Verify foreign issuer documents"
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
    *,
    capital_structure_indicator: CapitalStructureIndicatorRead | None = None,
    market_intelligence_decision: MarketIntelligenceDecisionRead | None = None,
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
    if capital_structure_indicator is not None:
        plan.extend(capital_structure_indicator.recommendation_lines)
    if market_intelligence_decision is not None:
        plan.extend(market_intelligence_decision.decision_lines[:3])
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
    zone = _rsi_zone(rsi_value)
    if zone == "extended":
        return f"RSI: {rsi_value:.1f}. Extended; avoid chasing unless breakout volume and level confirmation are present."
    if zone == "dangerous_oversold":
        return f"RSI: {rsi_value:.1f}. Oversold but dangerous unless support holds and reversal evidence appears."
    if zone == "washed_out":
        return f"RSI: {rsi_value:.1f}. Washed out; constructive only if support and higher-timeframe regime hold."
    if zone == "soft_pullback":
        return f"RSI: {rsi_value:.1f}. Soft pullback zone; not bearish by itself if support holds."
    if zone == "constructive_neutral":
        return f"RSI: {rsi_value:.1f}. Constructive neutral; useful as context, not standalone proof."
    return f"RSI: {rsi_value:.1f}. Neutral context."


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
