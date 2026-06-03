from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analytics.crypto_sentiment import CryptoSentimentSnapshot
from app.analytics.hyperliquid_market_data import LiquiditySnapshot, MultiTimeframeCryptoSnapshot, PerpStructureSnapshot
from app.analytics.crypto_market_data import CryptoCandleResult, normalize_crypto_symbol
from app.analytics.research_scoring import BadgeReadout, direction_strength_label, risk_heat_label, score_momentum, score_technicals
from app.analytics.stock_research import AdvancedIndicatorSnapshot, DataSourceStatus, ScenarioRow


@dataclass(frozen=True)
class CryptoExposure:
    coin: str
    spot_quantity: float
    spot_value: float
    spot_pnl: float | None
    perp_quantity: float
    perp_notional: float
    perp_direction: str
    perp_entry: float | None
    perp_mark: float | None
    perp_unrealized_pnl: float | None
    portfolio_value: float
    open_orders: int

    @property
    def net_exposure(self) -> float:
        signed_perp = self.perp_notional if self.perp_direction == "long" else -self.perp_notional if self.perp_direction == "short" else 0.0
        return self.spot_value + signed_perp

    @property
    def hedge_ratio(self) -> float | None:
        if self.spot_value <= 0:
            return None
        return self.perp_notional / self.spot_value

    @property
    def portfolio_share(self) -> float:
        return (abs(self.spot_value) + abs(self.perp_notional)) / max(self.portfolio_value, 0.01)


@dataclass(frozen=True)
class CryptoScenarioRow:
    scenario: str
    price: float
    spot_pnl: float
    perp_pnl: float
    net_pnl: float
    portfolio_impact: float
    hedge_read: str


@dataclass(frozen=True)
class CryptoDecisionReadout:
    technical_score: float
    risk_score: float
    momentum_score: float
    composite_score: float
    overall: BadgeReadout
    risk_level: BadgeReadout
    trend: BadgeReadout
    trend_alignment: BadgeReadout
    momentum: BadgeReadout
    volatility: BadgeReadout
    liquidity: BadgeReadout
    funding_bias: BadgeReadout
    exposure: BadgeReadout
    sentiment: BadgeReadout
    action_bias: BadgeReadout
    score_components: dict[str, float]
    why_bullets: list[str]
    invalidations: list[str]
    summary: list[str]
    operator_view: dict[str, str]
    top_things: list[str]


def build_crypto_exposure(portfolio: Any, coin: str, open_orders: list[Any] | None = None) -> CryptoExposure:
    clean = normalize_crypto_symbol(coin)
    spot_quantity = 0.0
    spot_value = 0.0
    spot_pnl: float | None = None
    perp_quantity = 0.0
    perp_notional = 0.0
    perp_signed = 0.0
    perp_entry: float | None = None
    perp_mark: float | None = None
    perp_unrealized: float | None = None

    for position in getattr(portfolio, "positions", {}).values():
        symbol = str(getattr(position, "symbol", "")).upper()
        asset_type = str(getattr(position, "asset_type", "")).lower()
        position_coin = normalize_crypto_symbol(symbol)
        if position_coin != clean:
            continue
        is_perp = "-PERP" in symbol or asset_type.startswith("perp")
        is_spot = asset_type == "spot" or (not is_perp and clean == symbol)
        if is_spot:
            spot_quantity += abs(float(getattr(position, "quantity", 0.0) or 0.0))
            spot_value += abs(float(getattr(position, "market_value", 0.0) or 0.0))
            pnl = getattr(position, "unrealized_profit_loss", None)
            if pnl is not None:
                spot_pnl = (spot_pnl or 0.0) + float(pnl)
        elif is_perp:
            notional = abs(float(getattr(position, "market_value", 0.0) or 0.0))
            quantity = abs(float(getattr(position, "quantity", 0.0) or 0.0))
            signed = notional
            signed_qty = quantity
            if "short" in asset_type or symbol.endswith("-SHORT"):
                signed *= -1
                signed_qty *= -1
            perp_notional += notional
            perp_signed += signed
            perp_quantity += signed_qty
            perp_entry = float(getattr(position, "average_cost", 0.0) or 0.0) or perp_entry
            perp_mark = float(getattr(position, "last_price", 0.0) or 0.0) or perp_mark
            pnl = getattr(position, "unrealized_profit_loss", None)
            if pnl is not None:
                perp_unrealized = (perp_unrealized or 0.0) + float(pnl)

    direction = "long" if perp_signed > 0 else "short" if perp_signed < 0 else "none"
    order_count = 0
    for order in open_orders or []:
        raw_coin = getattr(order, "coin", None) or (order.get("coin") if isinstance(order, dict) else "")
        if normalize_crypto_symbol(str(raw_coin)) == clean:
            order_count += 1

    return CryptoExposure(
        coin=clean,
        spot_quantity=spot_quantity,
        spot_value=spot_value,
        spot_pnl=spot_pnl,
        perp_quantity=perp_quantity,
        perp_notional=perp_notional,
        perp_direction=direction,
        perp_entry=perp_entry,
        perp_mark=perp_mark,
        perp_unrealized_pnl=perp_unrealized,
        portfolio_value=float(getattr(portfolio, "total_value", 0.0) or 0.0),
        open_orders=order_count,
    )


def exposure_label(exposure: CryptoExposure) -> BadgeReadout:
    spot = exposure.spot_value
    perp = exposure.perp_notional
    net = exposure.net_exposure
    if spot <= 0.01 and perp <= 0.01:
        return BadgeReadout("Spot/Perp Exposure", "No Position", "info", 0, "no active spot or perp exposure for this coin.")
    if perp <= 0.01:
        return BadgeReadout("Spot/Perp Exposure", "Net Long", "mixed", 35, "spot only exposure.")
    if spot <= 0.01:
        label = "Net Short" if exposure.perp_direction == "short" else "Net Long"
        return BadgeReadout("Spot/Perp Exposure", label, "bad" if exposure.perp_direction == "short" else "mixed", 75, "perp exposure has no spot hedge.")
    ratio = spot / perp if perp else 0.0
    if exposure.perp_direction == "short":
        if 0.75 <= ratio <= 1.25:
            return BadgeReadout("Spot/Perp Exposure", "Hedged", "good", 25, "spot roughly offsets the short perp.")
        if ratio < 0.75:
            return BadgeReadout("Spot/Perp Exposure", "Net Short", "bad", 70, "short perp is larger than spot.")
        return BadgeReadout("Spot/Perp Exposure", "Net Long", "mixed", 55, "spot is larger than the short perp.")
    return BadgeReadout("Spot/Perp Exposure", "Stacked Long", "mixed", 70, "spot and long perp point the same way.")


def build_crypto_scenarios(
    exposure: CryptoExposure,
    current_price: float | None,
    moves: tuple[float, ...] = (-0.30, -0.20, -0.10, -0.05, 0.05, 0.10, 0.20, 0.30),
) -> list[CryptoScenarioRow]:
    price = current_price or exposure.perp_mark or (exposure.spot_value / exposure.spot_quantity if exposure.spot_quantity else 0.0)
    rows: list[CryptoScenarioRow] = []
    signed_perp_multiplier = 1.0 if exposure.perp_direction == "long" else -1.0 if exposure.perp_direction == "short" else 0.0
    for move in moves:
        spot_pnl = exposure.spot_value * move
        perp_pnl = exposure.perp_notional * move * signed_perp_multiplier
        net = spot_pnl + perp_pnl
        projected_spot = max(exposure.spot_value * (1 + move), 0.0)
        projected_perp = max(exposure.perp_notional * (1 + abs(move)), 0.0)
        projected = CryptoExposure(
            exposure.coin,
            exposure.spot_quantity,
            projected_spot,
            exposure.spot_pnl,
            exposure.perp_quantity,
            projected_perp,
            exposure.perp_direction,
            exposure.perp_entry,
            price * (1 + move) if price else exposure.perp_mark,
            exposure.perp_unrealized_pnl,
            exposure.portfolio_value,
            exposure.open_orders,
        )
        rows.append(
            CryptoScenarioRow(
                scenario=f"{move:+.0%}",
                price=price * (1 + move) if price else 0.0,
                spot_pnl=spot_pnl,
                perp_pnl=perp_pnl,
                net_pnl=net,
                portfolio_impact=net / max(exposure.portfolio_value, 0.01),
                hedge_read=exposure_label(projected).label,
            )
        )
    return rows


def build_crypto_decision(
    *,
    indicators: AdvancedIndicatorSnapshot,
    exposure: CryptoExposure,
    candle_result: CryptoCandleResult | None,
    funding_rate: float | None = None,
    sentiment_status: str = "not configured",
    multi_timeframe: MultiTimeframeCryptoSnapshot | None = None,
    liquidity: LiquiditySnapshot | None = None,
    perp_structure: PerpStructureSnapshot | None = None,
    sentiment: CryptoSentimentSnapshot | None = None,
) -> CryptoDecisionReadout:
    technical_score = score_technicals(indicators)
    momentum_score = score_momentum(indicators)
    exposure_badge = exposure_label(exposure)
    funding_value = funding_rate
    if perp_structure is not None and perp_structure.current_funding is not None:
        funding_value = perp_structure.current_funding
    sentiment_snapshot = sentiment
    risk_score = _crypto_risk_score(indicators, exposure, liquidity=liquidity, perp_structure=perp_structure)
    score_components = _crypto_score_components(
        indicators=indicators,
        exposure=exposure,
        technical_score=technical_score,
        momentum_score=momentum_score,
        risk_score=risk_score,
        multi_timeframe=multi_timeframe,
        liquidity=liquidity,
        perp_structure=perp_structure,
        sentiment=sentiment_snapshot,
    )
    overall_score = _composite_market_score(score_components)
    overall = _direction_badge("Overall Market Read", overall_score)
    risk = _risk_badge("Risk Heat", risk_score, _crypto_risk_why(indicators, exposure, liquidity=liquidity, perp_structure=perp_structure))
    trend = _trend_badge(indicators)
    trend_alignment = _trend_alignment_badge(multi_timeframe, trend)
    momentum = _momentum_badge(momentum_score)
    volatility = _volatility_badge(indicators)
    liquidity_badge = _liquidity_badge(liquidity)
    funding = _funding_badge(funding_value, exposure=exposure, perp_structure=perp_structure)
    sentiment_badge = _sentiment_badge(sentiment_status, sentiment_snapshot)
    action = _crypto_action_badge(overall_score, risk_score, exposure_badge, liquidity=liquidity, perp_structure=perp_structure)
    why_bullets = _crypto_why_bullets(score_components, trend_alignment, liquidity_badge, funding, sentiment_badge, exposure_badge)
    invalidations = _crypto_invalidations(indicators, liquidity, perp_structure, multi_timeframe)
    top = _crypto_top_things(exposure_badge, risk, indicators, candle_result, trend_alignment=trend_alignment, liquidity=liquidity_badge)
    summary = _crypto_summary(exposure, overall, risk, exposure_badge, candle_result, why_bullets=why_bullets)
    operator = _crypto_operator_view(action, exposure_badge, indicators, exposure, liquidity=liquidity, perp_structure=perp_structure)
    return CryptoDecisionReadout(
        technical_score=technical_score,
        risk_score=risk_score,
        momentum_score=momentum_score,
        composite_score=overall_score,
        overall=overall,
        risk_level=risk,
        trend=trend,
        trend_alignment=trend_alignment,
        momentum=momentum,
        volatility=volatility,
        liquidity=liquidity_badge,
        funding_bias=funding,
        exposure=exposure_badge,
        sentiment=sentiment_badge,
        action_bias=action,
        score_components=score_components,
        why_bullets=why_bullets,
        invalidations=invalidations,
        summary=summary,
        operator_view=operator,
        top_things=top,
    )


def _crypto_risk_score(
    indicators: AdvancedIndicatorSnapshot,
    exposure: CryptoExposure,
    *,
    liquidity: LiquiditySnapshot | None = None,
    perp_structure: PerpStructureSnapshot | None = None,
) -> float:
    score = 25.0
    if indicators.volatility == "elevated":
        score += 30
    elif indicators.volatility == "normal":
        score += 16
    if exposure.portfolio_share >= 0.10:
        score += 30
    elif exposure.portfolio_share >= 0.05:
        score += 18
    elif exposure.portfolio_share >= 0.02:
        score += 8
    if exposure.perp_notional > 0 and exposure.spot_value <= 0:
        score += 15
    if exposure.open_orders:
        score += min(exposure.open_orders * 3, 12)
    if liquidity is not None:
        if liquidity.health == "Dangerous":
            score += 25
        elif liquidity.health == "Thin":
            score += 15
        elif liquidity.health == "Deep":
            score -= 5
    if perp_structure is not None:
        if perp_structure.oi_cap_status == "At/near cap":
            score += 15
        if perp_structure.current_funding is not None and abs(perp_structure.current_funding) >= 0.0005:
            score += 10
    return max(0.0, min(100.0, score))


def _direction_badge(title: str, score: float) -> BadgeReadout:
    if score >= 25:
        return BadgeReadout(title, "Bullish", "good", score, f"{direction_strength_label(score)} crypto market read.")
    if score <= -25:
        return BadgeReadout(title, "Bearish", "bad", score, f"{direction_strength_label(score)} crypto market read.")
    return BadgeReadout(title, "Mixed", "mixed", score, "signals are not aligned enough for a clean read.")


def _risk_badge(title: str, score: float, why: str) -> BadgeReadout:
    if score >= 70:
        return BadgeReadout(title, "High", "bad", score, why)
    if score >= 40:
        return BadgeReadout(title, "Medium", "mixed", score, why)
    return BadgeReadout(title, "Low", "good", score, why)


def _trend_badge(indicators: AdvancedIndicatorSnapshot) -> BadgeReadout:
    if indicators.trend == "bullish":
        return BadgeReadout("Trend", "Up", "good", 75, "selected timeframe is above key moving averages.")
    if indicators.trend == "bearish":
        return BadgeReadout("Trend", "Down", "bad", -75, "selected timeframe is below key moving averages.")
    if indicators.trend == "unknown":
        return BadgeReadout("Trend", "Unknown", "info", 0, "candles are unavailable.")
    return BadgeReadout("Trend", "Sideways", "mixed", 0, "moving averages are mixed.")


def _trend_alignment_badge(multi_timeframe: MultiTimeframeCryptoSnapshot | None, fallback: BadgeReadout) -> BadgeReadout:
    if multi_timeframe is None:
        return BadgeReadout("Trend Alignment", fallback.label, fallback.status, fallback.score, "multi-timeframe matrix unavailable; using selected timeframe.")
    alignment = multi_timeframe.alignment
    return BadgeReadout("Trend Alignment", alignment.label, alignment.status, alignment.trend_score, alignment.why)


def _momentum_badge(score: float) -> BadgeReadout:
    if score >= 30:
        return BadgeReadout("Momentum", "Strong", "good", score, "RSI/MACD lean positive.")
    if score <= -30:
        return BadgeReadout("Momentum", "Weak", "bad", score, "RSI/MACD lean negative.")
    return BadgeReadout("Momentum", "Neutral", "mixed", score, "momentum is not stretched.")


def _volatility_badge(indicators: AdvancedIndicatorSnapshot) -> BadgeReadout:
    if indicators.volatility == "elevated":
        return BadgeReadout("Volatility", "Hot", "bad", 80, "ATR is elevated versus price.")
    if indicators.volatility == "low":
        return BadgeReadout("Volatility", "Calm", "good", 20, "ATR is low versus price.")
    if indicators.volatility == "normal":
        return BadgeReadout("Volatility", "Normal", "mixed", 45, "ATR is in a normal range.")
    return BadgeReadout("Volatility", "Unknown", "info", 50, "ATR unavailable.")


def _liquidity_badge(liquidity: LiquiditySnapshot | None) -> BadgeReadout:
    if liquidity is None:
        return BadgeReadout("Liquidity", "Unavailable", "info", 0, "order book not loaded.")
    status = "good" if liquidity.health == "Deep" else "mixed" if liquidity.health in {"Normal", "Thin"} else "bad" if liquidity.health == "Dangerous" else "info"
    score = {"Deep": 80, "Normal": 55, "Thin": 30, "Dangerous": 10}.get(liquidity.health, 0)
    return BadgeReadout("Liquidity", liquidity.health, status, score, liquidity.reason)


def _funding_badge(
    funding_rate: float | None,
    *,
    exposure: CryptoExposure,
    perp_structure: PerpStructureSnapshot | None = None,
) -> BadgeReadout:
    if perp_structure is not None and not perp_structure.is_perp_enabled:
        return BadgeReadout("Perp Carry", "Spot Only", "info", 0, "Hyperliquid perp metadata was not available for this asset.")
    if funding_rate is None:
        return BadgeReadout("Perp Carry", "Not Configured", "info", 0, "funding/carry endpoint unavailable for this run.")
    cost = perp_structure.carry_cost_8h if perp_structure is not None else None
    if cost is not None and exposure.perp_notional > 0:
        why = f"estimated 8h carry for current {exposure.perp_direction} perp: ${cost:,.2f}."
    else:
        why = "funding rate is available; no active perp notional for cost estimate."
    if funding_rate < -0.0001:
        return BadgeReadout("Perp Carry", "Shorts Pay", "mixed", 40, why)
    if funding_rate > 0.0001:
        return BadgeReadout("Perp Carry", "Longs Pay", "mixed", 55, why)
    return BadgeReadout("Perp Carry", "Flat", "good", 20, "perp carry is near flat.")


def _sentiment_badge(status: str, sentiment: CryptoSentimentSnapshot | None = None) -> BadgeReadout:
    if sentiment is not None:
        badge_status = "good" if sentiment.label == "Positive" else "bad" if sentiment.label == "Negative" else "mixed" if sentiment.label == "Mixed" else "info"
        return BadgeReadout("Social / News", sentiment.label, badge_status, sentiment.score, sentiment.message or f"{sentiment.headline_count} headlines scanned.")
    text = status.lower()
    if text == "positive":
        return BadgeReadout("Social / News", "Positive", "good", 45, "fresh sentiment source leaned positive.")
    if text == "negative":
        return BadgeReadout("Social / News", "Negative", "bad", -45, "fresh sentiment source leaned negative.")
    if text == "mixed":
        return BadgeReadout("Social / News", "Mixed", "mixed", 0, "sentiment is mixed.")
    return BadgeReadout("Social / News", "Not Configured", "info", 0, "optional sentiment/news provider is not configured.")


def _crypto_action_badge(
    overall_score: float,
    risk_score: float,
    exposure: BadgeReadout,
    *,
    liquidity: LiquiditySnapshot | None = None,
    perp_structure: PerpStructureSnapshot | None = None,
) -> BadgeReadout:
    if risk_score >= 75:
        return BadgeReadout("Operator Action", "Reduce", "bad", overall_score, "risk heat is high relative to setup.")
    if liquidity is not None and liquidity.health == "Dangerous":
        return BadgeReadout("Operator Action", "Wait", "bad", overall_score, "visible liquidity is dangerous.")
    if perp_structure is not None and perp_structure.carry_cost_8h is not None and perp_structure.carry_cost_8h > 25:
        return BadgeReadout("Operator Action", "Hedge", "mixed", overall_score, "perp carry is a material cost.")
    if exposure.label == "Net Short" and overall_score > 20:
        return BadgeReadout("Operator Action", "Add Spot", "good", overall_score, "spot could soften the short-perp imbalance.")
    if exposure.label == "Net Long" and overall_score < -20:
        return BadgeReadout("Operator Action", "Hedge", "mixed", overall_score, "a hedge could reduce downside.")
    if overall_score <= -35:
        return BadgeReadout("Operator Action", "Avoid", "bad", overall_score, "market read is not supportive.")
    if overall_score >= 45 and risk_score < 55 and exposure.label in {"No Position", "Net Long"}:
        return BadgeReadout("Operator Action", "Add Spot", "good", overall_score, "trend/liquidity read is supportive without hot risk.")
    return BadgeReadout("Operator Action", "Watch", "mixed", overall_score, "wait for cleaner confirmation.")


def _crypto_risk_why(
    indicators: AdvancedIndicatorSnapshot,
    exposure: CryptoExposure,
    *,
    liquidity: LiquiditySnapshot | None = None,
    perp_structure: PerpStructureSnapshot | None = None,
) -> str:
    parts = [f"portfolio share {exposure.portfolio_share:.2%}", f"volatility {indicators.volatility}", f"open orders {exposure.open_orders}"]
    if liquidity is not None:
        parts.append(f"liquidity {liquidity.health.lower()}")
    if perp_structure is not None and perp_structure.current_funding is not None:
        parts.append(f"funding {perp_structure.current_funding:+.4%}")
    return "; ".join(parts) + "."


def _crypto_top_things(
    exposure: BadgeReadout,
    risk: BadgeReadout,
    indicators: AdvancedIndicatorSnapshot,
    candle_result: CryptoCandleResult | None,
    *,
    trend_alignment: BadgeReadout | None = None,
    liquidity: BadgeReadout | None = None,
) -> list[str]:
    first = f"Exposure: {exposure.label}"
    second = f"Risk heat: {risk_heat_label(risk.score)}"
    if trend_alignment is not None:
        first = f"Trend alignment: {trend_alignment.label}"
    if liquidity is not None:
        second = f"Liquidity: {liquidity.label}; risk {risk.label}"
    if candle_result is None or not candle_result.candles:
        third = "Candles unavailable; exposure still works"
    elif indicators.resistance:
        third = f"Break above ${indicators.resistance:,.4f} improves setup"
    elif indicators.support:
        third = f"Lose ${indicators.support:,.4f} worsens setup"
    else:
        third = "Watch price confirmation"
    return [first, second, third]


def _crypto_summary(
    exposure: CryptoExposure,
    overall: BadgeReadout,
    risk: BadgeReadout,
    exposure_badge: BadgeReadout,
    candle_result: CryptoCandleResult | None,
    *,
    why_bullets: list[str] | None = None,
) -> list[str]:
    lines = [
        f"{exposure.coin} is currently {exposure_badge.label.lower()} based on synced spot/perp exposure.",
        f"The overall read is {overall.label.lower()} with {risk.label.lower()} risk heat.",
    ]
    for bullet in (why_bullets or [])[:3]:
        lines.append(bullet)
    if candle_result is None or not candle_result.candles:
        lines.append("Price history is unavailable, so the dashboard leans on exposure and synced account data.")
    elif exposure.perp_direction == "short":
        lines.append("If price falls, the short perp helps; if price rises, the short perp drags.")
    else:
        lines.append("If price rises, spot/long exposure helps; if price falls, downside risk rises.")
    return lines


def _crypto_operator_view(
    action: BadgeReadout,
    exposure: BadgeReadout,
    indicators: AdvancedIndicatorSnapshot,
    current: CryptoExposure,
    *,
    liquidity: LiquiditySnapshot | None = None,
    perp_structure: PerpStructureSnapshot | None = None,
) -> dict[str, str]:
    key_level = "No clean level loaded"
    if indicators.resistance:
        key_level = f"Resistance ${indicators.resistance:,.4f}"
    elif indicators.support:
        key_level = f"Support ${indicators.support:,.4f}"
    return {
        "Current setup": exposure.label,
        "Operator action": action.label,
        "Good thing": _operator_good(exposure, liquidity),
        "Main risk": _operator_risk(current, liquidity, perp_structure),
        "Key level": key_level,
        "Best next check": "Timeframe matrix, order book depth, perp carry, open orders, and stop/TP distance.",
        "If price rises": "Spot gains; short perps lose." if current.perp_direction == "short" else "Long exposure gains.",
        "If price falls": "Short perps help; spot loses." if current.perp_direction == "short" else "Spot/long exposure loses.",
    }


def _crypto_score_components(
    *,
    indicators: AdvancedIndicatorSnapshot,
    exposure: CryptoExposure,
    technical_score: float,
    momentum_score: float,
    risk_score: float,
    multi_timeframe: MultiTimeframeCryptoSnapshot | None,
    liquidity: LiquiditySnapshot | None,
    perp_structure: PerpStructureSnapshot | None,
    sentiment: CryptoSentimentSnapshot | None,
) -> dict[str, float]:
    trend_score = multi_timeframe.alignment.trend_score if multi_timeframe is not None else technical_score
    volatility_score = 100.0 - risk_score
    liquidity_score = {"Deep": 80.0, "Normal": 55.0, "Thin": 20.0, "Dangerous": -35.0}.get(liquidity.health, 0.0) if liquidity else 0.0
    carry_score = 0.0
    if perp_structure is not None:
        if not perp_structure.is_perp_enabled:
            carry_score = 10.0
        elif perp_structure.current_funding is not None:
            carry_score = -min(70.0, abs(perp_structure.current_funding) * 100_000)
    sentiment_score = sentiment.score if sentiment is not None and sentiment.status != "not configured" else 0.0
    exposure_score = -min(80.0, exposure.portfolio_share * 500)
    if exposure.hedge_ratio is not None and 0.75 <= exposure.hedge_ratio <= 1.25 and exposure.perp_direction == "short":
        exposure_score += 30
    source_confidence = multi_timeframe.alignment.source_confidence_score if multi_timeframe is not None else (80.0 if indicators.latest_close is not None else 10.0)
    return {
        "Trend": trend_score,
        "Momentum": momentum_score,
        "Volatility": volatility_score,
        "Liquidity": liquidity_score,
        "Perp crowding / carry": carry_score,
        "Sentiment": sentiment_score,
        "Exposure / risk": exposure_score,
        "Source confidence": source_confidence,
    }


def _composite_market_score(components: dict[str, float]) -> float:
    weighted = (
        components.get("Trend", 0.0) * 0.25
        + components.get("Momentum", 0.0) * 0.16
        + components.get("Volatility", 0.0) * 0.10
        + components.get("Liquidity", 0.0) * 0.14
        + components.get("Perp crowding / carry", 0.0) * 0.12
        + components.get("Sentiment", 0.0) * 0.08
        + components.get("Exposure / risk", 0.0) * 0.10
        + (components.get("Source confidence", 0.0) - 50.0) * 0.05
    )
    return max(-100.0, min(100.0, weighted))


def _crypto_why_bullets(
    components: dict[str, float],
    trend_alignment: BadgeReadout,
    liquidity: BadgeReadout,
    carry: BadgeReadout,
    sentiment: BadgeReadout,
    exposure: BadgeReadout,
) -> list[str]:
    return [
        f"Trend alignment is {trend_alignment.label.lower()} with score {components.get('Trend', 0):.0f}.",
        f"Liquidity is {liquidity.label.lower()}: {liquidity.why}",
        f"Perp carry is {carry.label.lower()}: {carry.why}",
        f"Exposure is {exposure.label.lower()}; source confidence {components.get('Source confidence', 0):.0f}%.",
        f"Social/news read is {sentiment.label.lower()}.",
    ]


def _crypto_invalidations(
    indicators: AdvancedIndicatorSnapshot,
    liquidity: LiquiditySnapshot | None,
    perp_structure: PerpStructureSnapshot | None,
    multi_timeframe: MultiTimeframeCryptoSnapshot | None,
) -> list[str]:
    lines: list[str] = []
    if indicators.resistance:
        lines.append(f"Break and hold above ${indicators.resistance:,.4f} would improve the long setup.")
    if indicators.support:
        lines.append(f"Losing ${indicators.support:,.4f} would weaken the setup.")
    if liquidity is not None and liquidity.health in {"Thin", "Dangerous"}:
        lines.append("Liquidity improves if spread tightens and 100 bps depth rebuilds.")
    if perp_structure is not None and perp_structure.current_funding is not None:
        lines.append("Carry read changes if funding flips sharply or predicted funding diverges.")
    if multi_timeframe is not None:
        lines.append("Market read changes if short-term and swing timeframe groups stop agreeing.")
    return lines or ["More fresh market data would sharpen invalidation levels."]


def _operator_good(exposure: BadgeReadout, liquidity: LiquiditySnapshot | None) -> str:
    if liquidity is not None and liquidity.health in {"Deep", "Normal"}:
        return f"Visible book liquidity is {liquidity.health.lower()}."
    if exposure.label == "Hedged":
        return "Spot and perp are close to balanced."
    return "There is a clear exposure read."


def _operator_risk(
    current: CryptoExposure,
    liquidity: LiquiditySnapshot | None,
    perp_structure: PerpStructureSnapshot | None,
) -> str:
    if liquidity is not None and liquidity.health in {"Thin", "Dangerous"}:
        return "Thin visible liquidity can turn market orders and stops into slippage."
    if perp_structure is not None and perp_structure.oi_cap_status == "At/near cap":
        return "Asset is flagged at/near the Hyperliquid open-interest cap."
    if current.perp_notional and not current.spot_value:
        return "Perp has no spot hedge."
    return "Crypto volatility can move fast."
