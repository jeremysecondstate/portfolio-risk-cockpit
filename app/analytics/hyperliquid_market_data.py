from __future__ import annotations

import json
import math
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.analytics.stock_research import AdvancedIndicatorSnapshot, calculate_advanced_indicators
from app.analytics.technical_analysis import Candle


HYPERLIQUID_INFO_URL = "https://api.hyperliquid.xyz/info"
HYPERLIQUID_CANDLE_LIMIT = 5000
HYPERLIQUID_INTERVALS: tuple[str, ...] = (
    "1m",
    "3m",
    "5m",
    "15m",
    "30m",
    "1h",
    "2h",
    "4h",
    "8h",
    "12h",
    "1d",
    "3d",
    "1w",
    "1M",
)
HYPERLIQUID_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 3 * 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "2h": 2 * 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "8h": 8 * 60 * 60_000,
    "12h": 12 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
    "3d": 3 * 24 * 60 * 60_000,
    "1w": 7 * 24 * 60 * 60_000,
    "1M": 30 * 24 * 60 * 60_000,
}
DEFAULT_MATRIX_TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "4h", "8h", "12h", "1d", "3d", "1w", "1M")
TIMEFRAME_GROUPS: dict[str, tuple[str, ...]] = {
    "short-term": ("1m", "5m", "15m"),
    "intraday": ("30m", "1h", "4h"),
    "swing": ("8h", "12h", "1d"),
    "macro": ("3d", "1w", "1M"),
}


@dataclass(frozen=True)
class MarketDataSourceStatus:
    provider: str
    endpoint: str
    status: str
    fetched_at: str
    timeframe: str = ""
    candle_count: int | None = None
    message: str = ""


@dataclass(frozen=True)
class TimeframeIndicatorRead:
    timeframe: str
    group: str
    candle_count: int
    trend: str
    rsi: float | None
    macd: str
    atr_percent: float | None
    volume_regime: str
    support: float | None
    resistance: float | None
    score: float
    read: str
    source: str
    status: str


@dataclass(frozen=True)
class CrossTimeframeAlignment:
    label: str
    status: str
    trend_score: float
    momentum_score: float
    volatility_score: float
    volume_score: float
    breakout_score: float
    source_confidence_score: float
    group_reads: dict[str, str]
    why: str


@dataclass(frozen=True)
class MultiTimeframeCryptoSnapshot:
    symbol: str
    candles_by_timeframe: dict[str, list[Candle]]
    indicators_by_timeframe: dict[str, AdvancedIndicatorSnapshot]
    timeframe_reads: list[TimeframeIndicatorRead]
    alignment: CrossTimeframeAlignment
    source_statuses: list[MarketDataSourceStatus]
    fetched_at: str


@dataclass(frozen=True)
class L2Level:
    price: float
    size: float
    notional: float
    order_count: int | None = None


@dataclass(frozen=True)
class DepthBucket:
    bps: int
    bid_depth_usd: float
    ask_depth_usd: float


@dataclass(frozen=True)
class SlippageEstimate:
    order_size_usd: float
    buy_slippage_bps: float | None
    sell_slippage_bps: float | None
    read: str


@dataclass(frozen=True)
class LiquiditySnapshot:
    coin: str
    status: str
    fetched_at: str
    mid_price: float | None
    best_bid: float | None
    best_ask: float | None
    spread_bps: float | None
    top_bid_depth_usd: float
    top_ask_depth_usd: float
    depth_buckets: list[DepthBucket]
    imbalance: float | None
    slippage: list[SlippageEstimate]
    health: str
    reason: str
    warnings: list[str] = field(default_factory=list)
    source_status: MarketDataSourceStatus | None = None


@dataclass(frozen=True)
class PerpStructureSnapshot:
    coin: str
    is_perp_enabled: bool
    status: str
    fetched_at: str
    mark_price: float | None
    oracle_price: float | None
    mid_price: float | None
    premium_bps: float | None
    current_funding: float | None
    predicted_funding: float | None
    historical_funding_avg: float | None
    historical_funding_trend: str
    open_interest: float | None
    day_notional_volume: float | None
    max_leverage: float | None
    oi_cap_status: str
    carry_cost_8h: float | None
    carry_cost_daily: float | None
    carry_read: str
    source_statuses: list[MarketDataSourceStatus] = field(default_factory=list)


def normalize_hyperliquid_interval(interval: str) -> str:
    raw = str(interval or "1d").strip()
    if raw == "1M":
        return "1M"
    lowered = raw.lower()
    aliases = {"1mo": "1M", "1mon": "1M", "1month": "1M", "month": "1M"}
    value = aliases.get(lowered, lowered)
    return value if value in HYPERLIQUID_INTERVALS else "1d"


def post_hyperliquid_info(payload: dict[str, Any], *, timeout_seconds: int = 8) -> Any:
    url = (os.getenv("HYPERLIQUID_INFO_URL") or HYPERLIQUID_INFO_URL).strip()
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "portfolio-risk-cockpit/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_hyperliquid_candles(
    symbol: str,
    *,
    days: int = 365,
    timeout_seconds: int = 8,
    interval: str = "1d",
) -> tuple[list[Candle], MarketDataSourceStatus]:
    timeframe = normalize_hyperliquid_interval(interval)
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    interval_ms = HYPERLIQUID_INTERVAL_MS[timeframe]
    requested = _requested_candle_count(days, interval_ms)
    start_time = max(0, now_ms - requested * interval_ms)
    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": symbol.strip().upper(),
            "interval": timeframe,
            "startTime": start_time,
            "endTime": now_ms,
        },
    }
    fetched_at = _now()
    try:
        raw = post_hyperliquid_info(payload, timeout_seconds=timeout_seconds)
        candles = parse_hyperliquid_candles(raw)
    except Exception as exc:
        return [], MarketDataSourceStatus("Hyperliquid", "candleSnapshot", "error", fetched_at, timeframe, 0, str(exc))
    status = "fresh" if candles else "unavailable"
    message = f"{len(candles)} {timeframe} candles from Hyperliquid."
    if len(candles) >= HYPERLIQUID_CANDLE_LIMIT:
        message += " Hyperliquid returns at most the most recent 5000 candles per request."
    return candles, MarketDataSourceStatus("Hyperliquid", "candleSnapshot", status, fetched_at, timeframe, len(candles), message)


def parse_hyperliquid_candles(payload: Any) -> list[Candle]:
    if not isinstance(payload, list):
        raise ValueError("Hyperliquid candleSnapshot expected a list.")
    candles: list[Candle] = []
    for item in payload:
        try:
            if isinstance(item, dict):
                timestamp = _to_int(item.get("t") or item.get("T") or item.get("time") or item.get("datetime"))
                open_ = _to_float(item.get("o") or item.get("open"))
                high = _to_float(item.get("h") or item.get("high"))
                low = _to_float(item.get("l") or item.get("low"))
                close = _to_float(item.get("c") or item.get("close"))
                volume = _to_float(item.get("v") or item.get("volume")) or 0.0
            elif isinstance(item, list) and len(item) >= 6:
                timestamp = _to_int(item[0])
                open_ = _to_float(item[1])
                high = _to_float(item[2])
                low = _to_float(item[3])
                close = _to_float(item[4])
                volume = _to_float(item[5]) or 0.0
            else:
                continue
            if timestamp is None or open_ is None or high is None or low is None or close is None:
                continue
            candles.append(Candle(timestamp, open_, high, low, close, volume))
        except (TypeError, ValueError):
            continue
    return sorted(candles, key=lambda candle: candle.datetime_ms)


def fetch_multi_timeframe_crypto_snapshot(
    symbol: str,
    *,
    timeframes: tuple[str, ...] = DEFAULT_MATRIX_TIMEFRAMES,
    timeout_seconds: int = 8,
) -> MultiTimeframeCryptoSnapshot:
    clean = symbol.strip().upper()
    normalized = tuple(dict.fromkeys(normalize_hyperliquid_interval(timeframe) for timeframe in timeframes))
    results: dict[str, tuple[list[Candle], MarketDataSourceStatus]] = {}
    max_workers = min(6, max(1, len(normalized)))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                fetch_hyperliquid_candles,
                clean,
                days=_matrix_days_for_timeframe(timeframe),
                timeout_seconds=timeout_seconds,
                interval=timeframe,
            ): timeframe
            for timeframe in normalized
        }
        for future in as_completed(futures):
            timeframe = futures[future]
            try:
                candles, status = future.result()
            except Exception as exc:
                status = MarketDataSourceStatus("Hyperliquid", "candleSnapshot", "error", _now(), timeframe, 0, str(exc))
                candles = []
            results[timeframe] = (candles, status)
    return build_multi_timeframe_crypto_snapshot(clean, results)


def build_multi_timeframe_crypto_snapshot(
    symbol: str,
    candle_results: dict[str, tuple[list[Candle], MarketDataSourceStatus]],
) -> MultiTimeframeCryptoSnapshot:
    clean = symbol.strip().upper()
    candles_by_timeframe: dict[str, list[Candle]] = {}
    indicators_by_timeframe: dict[str, AdvancedIndicatorSnapshot] = {}
    reads: list[TimeframeIndicatorRead] = []
    statuses: list[MarketDataSourceStatus] = []

    for timeframe in HYPERLIQUID_INTERVALS:
        if timeframe not in candle_results:
            continue
        candles, status = candle_results[timeframe]
        candles_by_timeframe[timeframe] = candles
        statuses.append(status)
        indicators = calculate_advanced_indicators(clean, candles)
        indicators_by_timeframe[timeframe] = indicators
        reads.append(_build_timeframe_read(timeframe, candles, indicators, status))

    alignment = _build_alignment(reads, statuses)
    return MultiTimeframeCryptoSnapshot(
        symbol=clean,
        candles_by_timeframe=candles_by_timeframe,
        indicators_by_timeframe=indicators_by_timeframe,
        timeframe_reads=reads,
        alignment=alignment,
        source_statuses=statuses,
        fetched_at=_now(),
    )


def fetch_liquidity_snapshot(
    coin: str,
    *,
    exposure_notional: float = 0.0,
    order_sizes_usd: tuple[float, ...] = (1_000.0, 5_000.0, 25_000.0),
    timeout_seconds: int = 8,
) -> LiquiditySnapshot:
    clean = coin.strip().upper()
    fetched_at = _now()
    try:
        payload = post_hyperliquid_info({"type": "l2Book", "coin": clean}, timeout_seconds=timeout_seconds)
        return analyze_order_book(
            clean,
            payload,
            exposure_notional=exposure_notional,
            order_sizes_usd=order_sizes_usd,
            fetched_at=fetched_at,
        )
    except Exception as exc:
        status = MarketDataSourceStatus("Hyperliquid", "l2Book", "error", fetched_at, "", None, str(exc))
        return LiquiditySnapshot(clean, "error", fetched_at, None, None, None, None, 0.0, 0.0, [], None, [], "Unavailable", str(exc), ["Order book unavailable."], status)


def analyze_order_book(
    coin: str,
    payload: Any,
    *,
    exposure_notional: float = 0.0,
    order_sizes_usd: tuple[float, ...] = (1_000.0, 5_000.0, 25_000.0),
    fetched_at: str | None = None,
) -> LiquiditySnapshot:
    timestamp = fetched_at or _now()
    bids, asks = parse_l2_levels(payload)
    status = MarketDataSourceStatus("Hyperliquid", "l2Book", "fresh" if bids and asks else "unavailable", timestamp, "", None, "Visible L2 book loaded.")
    if not bids or not asks:
        return LiquiditySnapshot(coin, "unavailable", timestamp, None, None, None, None, 0.0, 0.0, [], None, [], "Unavailable", "Hyperliquid l2Book returned no usable bids/asks.", ["No usable visible depth."], status)

    bids = sorted(bids, key=lambda level: level.price, reverse=True)
    asks = sorted(asks, key=lambda level: level.price)
    best_bid = bids[0].price
    best_ask = asks[0].price
    mid = (best_bid + best_ask) / 2
    spread_bps = ((best_ask - best_bid) / mid) * 10_000 if mid > 0 else None
    buckets = [_depth_bucket(bps, bids, asks, mid) for bps in (10, 25, 50, 100)]
    depth_100 = next((bucket for bucket in buckets if bucket.bps == 100), buckets[-1])
    imbalance = _depth_imbalance(depth_100.bid_depth_usd, depth_100.ask_depth_usd)
    slippage = [_estimate_slippage(size, bids, asks, mid) for size in order_sizes_usd if size > 0]
    warnings: list[str] = []
    visible_depth = min(sum(level.notional for level in bids), sum(level.notional for level in asks))
    if exposure_notional > 0 and visible_depth > 0 and exposure_notional >= visible_depth * 0.25:
        warnings.append("Current exposure is large versus visible book depth.")
    if slippage and any((estimate.buy_slippage_bps or 0) >= 75 or (estimate.sell_slippage_bps or 0) >= 75 for estimate in slippage):
        warnings.append("A larger market order may move price materially.")
    health, reason = _liquidity_health(spread_bps, depth_100, slippage, warnings)
    return LiquiditySnapshot(
        coin=coin.strip().upper(),
        status="fresh",
        fetched_at=timestamp,
        mid_price=mid,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_bps=spread_bps,
        top_bid_depth_usd=bids[0].notional,
        top_ask_depth_usd=asks[0].notional,
        depth_buckets=buckets,
        imbalance=imbalance,
        slippage=slippage,
        health=health,
        reason=reason,
        warnings=warnings,
        source_status=status,
    )


def parse_l2_levels(payload: Any) -> tuple[list[L2Level], list[L2Level]]:
    raw_levels = payload.get("levels") if isinstance(payload, dict) else payload
    if not isinstance(raw_levels, list) or len(raw_levels) < 2:
        raise ValueError("Hyperliquid l2Book expected levels with bid and ask arrays.")
    return _parse_book_side(raw_levels[0]), _parse_book_side(raw_levels[1])


def fetch_perp_structure_snapshot(
    coin: str,
    *,
    perp_notional: float = 0.0,
    perp_direction: str = "none",
    timeout_seconds: int = 8,
) -> PerpStructureSnapshot:
    clean = coin.strip().upper()
    statuses: list[MarketDataSourceStatus] = []
    fetched_at = _now()

    def safe_post(endpoint: str, payload: dict[str, Any], default: Any) -> Any:
        try:
            value = post_hyperliquid_info(payload, timeout_seconds=timeout_seconds)
            statuses.append(MarketDataSourceStatus("Hyperliquid", endpoint, "fresh", _now(), "", None, "Endpoint loaded."))
            return value
        except Exception as exc:
            statuses.append(MarketDataSourceStatus("Hyperliquid", endpoint, "error", _now(), "", None, str(exc)))
            return default

    end_time = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_time = max(0, end_time - 7 * 24 * 60 * 60 * 1000)
    meta_payload = safe_post("metaAndAssetCtxs", {"type": "metaAndAssetCtxs"}, None)
    predicted_payload = safe_post("predictedFundings", {"type": "predictedFundings"}, None)
    funding_history = safe_post(
        "fundingHistory",
        {"type": "fundingHistory", "coin": clean, "startTime": start_time, "endTime": end_time},
        [],
    )
    oi_cap_payload = safe_post("perpsAtOpenInterestCap", {"type": "perpsAtOpenInterestCap"}, None)
    return analyze_perp_structure(
        clean,
        meta_payload=meta_payload,
        predicted_payload=predicted_payload,
        funding_history_payload=funding_history,
        oi_cap_payload=oi_cap_payload,
        perp_notional=perp_notional,
        perp_direction=perp_direction,
        fetched_at=fetched_at,
        source_statuses=statuses,
    )


def analyze_perp_structure(
    coin: str,
    *,
    meta_payload: Any,
    predicted_payload: Any = None,
    funding_history_payload: Any = None,
    oi_cap_payload: Any = None,
    perp_notional: float = 0.0,
    perp_direction: str = "none",
    fetched_at: str | None = None,
    source_statuses: list[MarketDataSourceStatus] | None = None,
) -> PerpStructureSnapshot:
    clean = coin.strip().upper()
    timestamp = fetched_at or _now()
    statuses = source_statuses or []
    asset, ctx = _find_perp_asset(clean, meta_payload)
    if asset is None and ctx is None:
        return PerpStructureSnapshot(
            coin=clean,
            is_perp_enabled=False,
            status="unavailable",
            fetched_at=timestamp,
            mark_price=None,
            oracle_price=None,
            mid_price=None,
            premium_bps=None,
            current_funding=None,
            predicted_funding=None,
            historical_funding_avg=None,
            historical_funding_trend="Unavailable",
            open_interest=None,
            day_notional_volume=None,
            max_leverage=None,
            oi_cap_status="Spot only / unavailable",
            carry_cost_8h=None,
            carry_cost_daily=None,
            carry_read="Spot only or perp metadata unavailable.",
            source_statuses=statuses,
        )

    asset = asset or {}
    ctx = ctx or {}
    mark = _first_number(ctx, ("markPx", "markPrice", "mark"))
    oracle = _first_number(ctx, ("oraclePx", "oraclePrice", "oracle"))
    mid = _first_number(ctx, ("midPx", "mid", "midPrice"))
    premium_bps = _first_number(ctx, ("premiumBps", "premium_bps"))
    if premium_bps is None:
        premium = _first_number(ctx, ("premium",))
        if premium is not None:
            premium_bps = premium * 10_000 if abs(premium) < 1 else premium
    if premium_bps is None and mark is not None and oracle:
        premium_bps = ((mark - oracle) / oracle) * 10_000

    current_funding = _first_number(ctx, ("funding", "fundingRate"))
    predicted_funding = _extract_predicted_funding(predicted_payload, clean)
    history_rates = _funding_history_rates(funding_history_payload)
    historical_avg = sum(history_rates) / len(history_rates) if history_rates else None
    history_trend = _funding_trend(history_rates)
    open_interest = _first_number(ctx, ("openInterest", "oi"))
    day_volume = _first_number(ctx, ("dayNtlVlm", "dayNtlVol", "dayNotionalVolume", "volume24h"))
    max_leverage = _first_number(asset, ("maxLeverage", "max_leverage"))
    oi_cap_status = "At/near cap" if _coin_in_oi_cap(clean, oi_cap_payload) else "No cap flag"
    carry_8h = funding_carry_cost(perp_notional, perp_direction, current_funding)
    carry_daily = None if carry_8h is None else carry_8h * 3
    carry_read = _carry_read(current_funding, predicted_funding, carry_8h, perp_direction)
    return PerpStructureSnapshot(
        coin=clean,
        is_perp_enabled=True,
        status="fresh",
        fetched_at=timestamp,
        mark_price=mark,
        oracle_price=oracle,
        mid_price=mid,
        premium_bps=premium_bps,
        current_funding=current_funding,
        predicted_funding=predicted_funding,
        historical_funding_avg=historical_avg,
        historical_funding_trend=history_trend,
        open_interest=open_interest,
        day_notional_volume=day_volume,
        max_leverage=max_leverage,
        oi_cap_status=oi_cap_status,
        carry_cost_8h=carry_8h,
        carry_cost_daily=carry_daily,
        carry_read=carry_read,
        source_statuses=statuses,
    )


def funding_carry_cost(perp_notional: float, perp_direction: str, funding_rate: float | None) -> float | None:
    if funding_rate is None or perp_notional <= 0:
        return None
    direction = str(perp_direction or "none").lower()
    side_multiplier = 1.0 if direction == "long" else -1.0 if direction == "short" else 0.0
    if side_multiplier == 0:
        return 0.0
    return perp_notional * funding_rate * side_multiplier


def _requested_candle_count(days: int, interval_ms: int) -> int:
    lookback_ms = max(days, 1) * 24 * 60 * 60 * 1000
    return max(60, min(HYPERLIQUID_CANDLE_LIMIT, int(math.ceil(lookback_ms / interval_ms)) + 5))


def _matrix_days_for_timeframe(timeframe: str) -> int:
    return {
        "1m": 4,
        "3m": 7,
        "5m": 10,
        "15m": 21,
        "30m": 45,
        "1h": 90,
        "2h": 120,
        "4h": 180,
        "8h": 365,
        "12h": 365,
        "1d": 730,
        "3d": 1_200,
        "1w": 2_000,
        "1M": 3_000,
    }.get(normalize_hyperliquid_interval(timeframe), 365)


def _build_timeframe_read(
    timeframe: str,
    candles: list[Candle],
    indicators: AdvancedIndicatorSnapshot,
    source_status: MarketDataSourceStatus,
) -> TimeframeIndicatorRead:
    macd_read = "Unknown"
    if indicators.macd is not None and indicators.macd_signal is not None and indicators.macd_histogram is not None:
        if indicators.macd > indicators.macd_signal and indicators.macd_histogram > 0:
            macd_read = "Bullish"
        elif indicators.macd < indicators.macd_signal and indicators.macd_histogram < 0:
            macd_read = "Bearish"
        else:
            macd_read = "Mixed"
    atr_percent = None
    if indicators.atr_14 is not None and indicators.latest_close:
        atr_percent = (indicators.atr_14 / indicators.latest_close) * 100
    volume_regime = _volume_regime(candles, indicators)
    score = _timeframe_score(indicators, macd_read, volume_regime)
    read = _timeframe_read_label(indicators, macd_read, score)
    return TimeframeIndicatorRead(
        timeframe=timeframe,
        group=_timeframe_group(timeframe),
        candle_count=len(candles),
        trend=indicators.trend.title(),
        rsi=indicators.rsi_14,
        macd=macd_read,
        atr_percent=atr_percent,
        volume_regime=volume_regime,
        support=indicators.support,
        resistance=indicators.resistance,
        score=score,
        read=read,
        source=source_status.provider,
        status=source_status.status,
    )


def _build_alignment(
    reads: list[TimeframeIndicatorRead],
    statuses: list[MarketDataSourceStatus],
) -> CrossTimeframeAlignment:
    usable = [read for read in reads if read.candle_count > 0 and read.status in {"fresh", "fresh/cache", "stale"}]
    if not usable:
        return CrossTimeframeAlignment(
            "Unavailable",
            "info",
            0.0,
            0.0,
            50.0,
            0.0,
            0.0,
            0.0,
            {group: "Unavailable" for group in TIMEFRAME_GROUPS},
            "No Hyperliquid timeframe matrix candles were available.",
        )

    group_reads: dict[str, str] = {}
    group_scores: list[float] = []
    for group, timeframes in TIMEFRAME_GROUPS.items():
        group_items = [read for read in usable if read.timeframe in timeframes]
        if not group_items:
            group_reads[group] = "Unavailable"
            continue
        group_score = sum(read.score for read in group_items) / len(group_items)
        group_scores.append(group_score)
        group_reads[group] = _alignment_group_label(group_score)

    trend_score = sum(read.score for read in usable) / len(usable)
    bullish = sum(1 for read in usable if read.score >= 25)
    bearish = sum(1 for read in usable if read.score <= -25)
    momentum_score = ((bullish - bearish) / max(len(usable), 1)) * 100
    hot_atr = sum(1 for read in usable if read.atr_percent is not None and read.atr_percent >= 4.0)
    volatility_score = min(100.0, 30.0 + hot_atr * 12.0)
    volume_score = (sum(1 for read in usable if read.volume_regime == "Expanding") / max(len(usable), 1)) * 100
    breakout_score = _breakout_confirmation_score(usable)
    source_confidence = (sum(1 for status in statuses if status.status in {"fresh", "fresh/cache"}) / max(len(statuses), 1)) * 100

    if trend_score >= 35 and bullish >= max(2, bearish + 2):
        label, status = "Bullish Stack", "good"
    elif trend_score <= -35 and bearish >= max(2, bullish + 2):
        label, status = "Bearish Stack", "bad"
    elif abs(trend_score) <= 20:
        label, status = "Mixed", "mixed"
    else:
        label, status = ("Leaning Bullish", "mixed") if trend_score > 0 else ("Leaning Bearish", "mixed")

    why = (
        f"{bullish} bullish, {bearish} bearish, {len(usable) - bullish - bearish} mixed timeframes; "
        f"source confidence {source_confidence:.0f}%."
    )
    return CrossTimeframeAlignment(
        label,
        status,
        trend_score,
        momentum_score,
        volatility_score,
        volume_score,
        breakout_score,
        source_confidence,
        group_reads,
        why,
    )


def _parse_book_side(rows: Any) -> list[L2Level]:
    levels: list[L2Level] = []
    if not isinstance(rows, list):
        return levels
    for row in rows:
        if isinstance(row, dict):
            price = _to_float(row.get("px") or row.get("price"))
            size = _to_float(row.get("sz") or row.get("size"))
            count = _to_int(row.get("n") or row.get("count"))
        elif isinstance(row, list) and len(row) >= 2:
            price = _to_float(row[0])
            size = _to_float(row[1])
            count = _to_int(row[2]) if len(row) > 2 else None
        else:
            continue
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        levels.append(L2Level(price, size, price * size, count))
    return levels


def _depth_bucket(bps: int, bids: list[L2Level], asks: list[L2Level], mid: float) -> DepthBucket:
    bid_floor = mid * (1 - bps / 10_000)
    ask_ceiling = mid * (1 + bps / 10_000)
    bid_depth = sum(level.notional for level in bids if level.price >= bid_floor)
    ask_depth = sum(level.notional for level in asks if level.price <= ask_ceiling)
    return DepthBucket(bps, bid_depth, ask_depth)


def _depth_imbalance(bid_depth: float, ask_depth: float) -> float | None:
    denominator = bid_depth + ask_depth
    if denominator <= 0:
        return None
    return (bid_depth - ask_depth) / denominator


def _estimate_slippage(order_size_usd: float, bids: list[L2Level], asks: list[L2Level], mid: float) -> SlippageEstimate:
    buy_slippage = _side_slippage(order_size_usd, asks, mid, side="buy")
    sell_slippage = _side_slippage(order_size_usd, bids, mid, side="sell")
    worst = max(value for value in (buy_slippage or 0.0, sell_slippage or 0.0))
    if worst >= 100:
        read = "Dangerous"
    elif worst >= 40:
        read = "Thin"
    elif worst >= 15:
        read = "Normal"
    else:
        read = "Deep"
    return SlippageEstimate(order_size_usd, buy_slippage, sell_slippage, read)


def _side_slippage(order_size_usd: float, levels: list[L2Level], mid: float, *, side: str) -> float | None:
    if order_size_usd <= 0 or mid <= 0:
        return None
    target_qty = order_size_usd / mid
    remaining = target_qty
    quote_value = 0.0
    filled_qty = 0.0
    for level in levels:
        qty = min(level.size, remaining)
        quote_value += qty * level.price
        filled_qty += qty
        remaining -= qty
        if remaining <= 0:
            break
    if remaining > 0 or filled_qty <= 0:
        return None
    average_price = quote_value / filled_qty
    if side == "buy":
        return max(0.0, ((average_price - mid) / mid) * 10_000)
    return max(0.0, ((mid - average_price) / mid) * 10_000)


def _liquidity_health(
    spread_bps: float | None,
    depth_100: DepthBucket,
    slippage: list[SlippageEstimate],
    warnings: list[str],
) -> tuple[str, str]:
    worst_slippage = max((value for estimate in slippage for value in (estimate.buy_slippage_bps, estimate.sell_slippage_bps) if value is not None), default=None)
    min_depth_100 = min(depth_100.bid_depth_usd, depth_100.ask_depth_usd)
    if spread_bps is None:
        return "Unavailable", "No valid top-of-book spread."
    if spread_bps >= 100 or (worst_slippage is not None and worst_slippage >= 100) or min_depth_100 < 10_000:
        return "Dangerous", f"Spread {spread_bps:.1f} bps; 100 bps depth ${min_depth_100:,.0f}."
    if spread_bps >= 35 or (worst_slippage is not None and worst_slippage >= 40) or min_depth_100 < 50_000 or warnings:
        return "Thin", f"Visible depth is limited; spread {spread_bps:.1f} bps."
    if spread_bps <= 8 and min_depth_100 >= 250_000 and (worst_slippage is None or worst_slippage < 15):
        return "Deep", f"Tight spread and 100 bps depth near ${min_depth_100:,.0f}."
    return "Normal", f"Spread {spread_bps:.1f} bps; 100 bps depth ${min_depth_100:,.0f}."


def _find_perp_asset(coin: str, payload: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(payload, list) or len(payload) < 2:
        return None, None
    meta = payload[0] if isinstance(payload[0], dict) else {}
    asset_ctxs = payload[1] if isinstance(payload[1], list) else []
    universe = meta.get("universe") if isinstance(meta, dict) else []
    if not isinstance(universe, list):
        return None, None
    for index, asset in enumerate(universe):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or asset.get("coin") or "").strip().upper()
        if name != coin:
            continue
        ctx = asset_ctxs[index] if index < len(asset_ctxs) and isinstance(asset_ctxs[index], dict) else {}
        return asset, ctx
    return None, None


def _extract_predicted_funding(payload: Any, coin: str) -> float | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        direct_coin = str(payload.get("coin") or payload.get("name") or "").strip().upper()
        if direct_coin == coin:
            value = _first_number(payload, ("fundingRate", "predictedFundingRate", "funding", "rate"))
            if value is not None:
                return value
        for value in payload.values():
            found = _extract_predicted_funding(value, coin)
            if found is not None:
                return found
    if isinstance(payload, list):
        if payload and str(payload[0]).strip().upper() == coin:
            return _first_number_from_any(payload[1:])
        for item in payload:
            found = _extract_predicted_funding(item, coin)
            if found is not None:
                return found
    return None


def _funding_history_rates(payload: Any) -> list[float]:
    if not isinstance(payload, list):
        return []
    rates: list[float] = []
    for row in payload:
        if isinstance(row, dict):
            value = _first_number(row, ("fundingRate", "funding", "rate"))
        elif isinstance(row, list):
            value = _first_number_from_any(row)
        else:
            value = None
        if value is not None:
            rates.append(value)
    return rates


def _funding_trend(rates: list[float]) -> str:
    if len(rates) < 3:
        return "Unavailable"
    recent = sum(rates[-3:]) / 3
    prior = sum(rates[:-3]) / max(len(rates) - 3, 1)
    if recent > prior + 0.00005:
        return "Rising"
    if recent < prior - 0.00005:
        return "Falling"
    return "Stable"


def _coin_in_oi_cap(coin: str, payload: Any) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if str(key).strip().upper() == coin and bool(value):
                return True
        return any(_coin_in_oi_cap(coin, value) for value in payload.values())
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, str) and item.strip().upper() == coin:
                return True
            if isinstance(item, dict) and str(item.get("coin") or item.get("name") or "").strip().upper() == coin:
                return True
            if _coin_in_oi_cap(coin, item):
                return True
    return False


def _carry_read(current_funding: float | None, predicted_funding: float | None, carry_8h: float | None, direction: str) -> str:
    rate = predicted_funding if predicted_funding is not None else current_funding
    if rate is None:
        return "Funding / carry unavailable."
    abs_rate = abs(rate)
    side = str(direction or "none").lower()
    cost_text = "flat" if carry_8h is None else ("cost" if carry_8h > 0 else "credit" if carry_8h < 0 else "flat")
    if abs_rate >= 0.0005:
        return f"Perp carry is elevated; current side sees an estimated {cost_text} per 8h."
    if abs_rate >= 0.0001:
        return f"Perp carry is active but not extreme for the current {side} exposure."
    return "Perp carry is near flat."


def _timeframe_group(timeframe: str) -> str:
    for group, timeframes in TIMEFRAME_GROUPS.items():
        if timeframe in timeframes:
            return group
    return "other"


def _volume_regime(candles: list[Candle], indicators: AdvancedIndicatorSnapshot) -> str:
    if not candles or indicators.volume_average_20 is None or indicators.volume_average_20 <= 0:
        return "Unknown"
    latest = candles[-1].volume
    if latest >= indicators.volume_average_20 * 1.5:
        return "Expanding"
    if latest <= indicators.volume_average_20 * 0.65:
        return "Quiet"
    return "Normal"


def _timeframe_score(indicators: AdvancedIndicatorSnapshot, macd_read: str, volume_regime: str) -> float:
    if indicators.latest_close is None:
        return 0.0
    score = 0.0
    if indicators.trend == "bullish":
        score += 35
    elif indicators.trend == "bearish":
        score -= 35
    if indicators.rsi_14 is not None:
        score += max(-30.0, min(30.0, (indicators.rsi_14 - 50) * 1.2))
    if macd_read == "Bullish":
        score += 20
    elif macd_read == "Bearish":
        score -= 20
    if volume_regime == "Expanding" and abs(score) >= 20:
        score += 8 if score > 0 else -8
    if indicators.volatility == "elevated":
        score -= 5 if score > 0 else 0
    return max(-100.0, min(100.0, score))


def _timeframe_read_label(indicators: AdvancedIndicatorSnapshot, macd_read: str, score: float) -> str:
    if indicators.latest_close is None:
        return "Unavailable"
    if score >= 35:
        return "Bullish confirmation"
    if score <= -35:
        return "Bearish pressure"
    if macd_read in {"Bullish", "Bearish"}:
        return f"{macd_read} momentum, mixed trend"
    return "Mixed / range"


def _alignment_group_label(score: float) -> str:
    if score >= 35:
        return "Bullish"
    if score <= -35:
        return "Bearish"
    if abs(score) <= 12:
        return "Mixed"
    return "Leaning bullish" if score > 0 else "Leaning bearish"


def _breakout_confirmation_score(reads: list[TimeframeIndicatorRead]) -> float:
    confirmed = 0
    available = 0
    for read in reads:
        if read.support is None and read.resistance is None:
            continue
        available += 1
        if read.score >= 35 and read.resistance is not None:
            confirmed += 1
        elif read.score <= -35 and read.support is not None:
            confirmed -= 1
    if not available:
        return 0.0
    return (confirmed / available) * 100


def _first_number(container: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _to_float(container.get(key))
        if value is not None:
            return value
    lowered = {str(key).lower(): value for key, value in container.items()}
    for key in keys:
        value = _to_float(lowered.get(key.lower()))
        if value is not None:
            return value
    return None


def _first_number_from_any(value: Any) -> float | None:
    if isinstance(value, dict):
        return _first_number(value, ("fundingRate", "predictedFundingRate", "funding", "rate", "value"))
    if isinstance(value, list):
        for item in value:
            found = _first_number_from_any(item)
            if found is not None:
                return found
    return _to_float(value)


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(number)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
