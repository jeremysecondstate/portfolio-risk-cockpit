from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    overall: BadgeReadout
    risk_level: BadgeReadout
    trend: BadgeReadout
    momentum: BadgeReadout
    volatility: BadgeReadout
    funding_bias: BadgeReadout
    exposure: BadgeReadout
    sentiment: BadgeReadout
    action_bias: BadgeReadout
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
    moves: tuple[float, ...] = (-0.20, -0.10, -0.05, -0.02, 0.02, 0.05, 0.10, 0.20),
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
    sentiment_status: str = "unknown",
) -> CryptoDecisionReadout:
    technical_score = score_technicals(indicators)
    momentum_score = score_momentum(indicators)
    exposure_badge = exposure_label(exposure)
    risk_score = _crypto_risk_score(indicators, exposure)
    overall_score = technical_score * 0.45 + momentum_score * 0.25 - (risk_score - 50) * 0.15
    if exposure_badge.label in {"Net Short", "Stacked Long"}:
        overall_score *= 0.9
    overall = _direction_badge("Overall Read", overall_score)
    risk = _risk_badge("Risk Level", risk_score, _crypto_risk_why(indicators, exposure))
    trend = _trend_badge(indicators)
    momentum = _momentum_badge(momentum_score)
    volatility = _volatility_badge(indicators)
    funding = _funding_badge(funding_rate)
    sentiment = _sentiment_badge(sentiment_status)
    action = _crypto_action_badge(overall_score, risk_score, exposure_badge)
    top = _crypto_top_things(exposure_badge, risk, indicators, candle_result)
    summary = _crypto_summary(exposure, overall, risk, exposure_badge, candle_result)
    operator = _crypto_operator_view(action, exposure_badge, indicators, exposure)
    return CryptoDecisionReadout(
        technical_score,
        risk_score,
        momentum_score,
        overall,
        risk,
        trend,
        momentum,
        volatility,
        funding,
        exposure_badge,
        sentiment,
        action,
        summary,
        operator,
        top,
    )


def _crypto_risk_score(indicators: AdvancedIndicatorSnapshot, exposure: CryptoExposure) -> float:
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
    return max(0.0, min(100.0, score))


def _direction_badge(title: str, score: float) -> BadgeReadout:
    if score >= 25:
        return BadgeReadout(title, "Bullish", "good", score, f"{direction_strength_label(score)} crypto technical read.")
    if score <= -25:
        return BadgeReadout(title, "Bearish", "bad", score, f"{direction_strength_label(score)} crypto technical read.")
    return BadgeReadout(title, "Neutral", "mixed", score, "mixed crypto read.")


def _risk_badge(title: str, score: float, why: str) -> BadgeReadout:
    if score >= 70:
        return BadgeReadout(title, "High", "bad", score, why)
    if score >= 40:
        return BadgeReadout(title, "Medium", "mixed", score, why)
    return BadgeReadout(title, "Low", "good", score, why)


def _trend_badge(indicators: AdvancedIndicatorSnapshot) -> BadgeReadout:
    if indicators.trend == "bullish":
        return BadgeReadout("Trend", "Up", "good", 75, "price is above key moving averages.")
    if indicators.trend == "bearish":
        return BadgeReadout("Trend", "Down", "bad", -75, "price is below key moving averages.")
    if indicators.trend == "unknown":
        return BadgeReadout("Trend", "Unknown", "info", 0, "candles are unavailable.")
    return BadgeReadout("Trend", "Sideways", "mixed", 0, "moving averages are mixed.")


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


def _funding_badge(funding_rate: float | None) -> BadgeReadout:
    if funding_rate is None:
        return BadgeReadout("Funding Bias", "Unknown", "info", 0, "funding data unavailable.")
    if funding_rate < -0.0001:
        return BadgeReadout("Funding Bias", "Favorable", "good", 40, "funding appears to credit this side.")
    if funding_rate > 0.0001:
        return BadgeReadout("Funding Bias", "Costly", "bad", 65, "funding may be a carry cost.")
    return BadgeReadout("Funding Bias", "Neutral", "mixed", 20, "funding is near flat.")


def _sentiment_badge(status: str) -> BadgeReadout:
    text = status.lower()
    if text == "positive":
        return BadgeReadout("Sentiment", "Positive", "good", 45, "fresh sentiment source leaned positive.")
    if text == "negative":
        return BadgeReadout("Sentiment", "Negative", "bad", -45, "fresh sentiment source leaned negative.")
    if text == "mixed":
        return BadgeReadout("Sentiment", "Mixed", "mixed", 0, "sentiment is mixed.")
    return BadgeReadout("Sentiment", "Unknown", "info", 0, "sentiment provider unavailable.")


def _crypto_action_badge(overall_score: float, risk_score: float, exposure: BadgeReadout) -> BadgeReadout:
    if risk_score >= 75:
        return BadgeReadout("Action Bias", "Reduce Risk", "bad", overall_score, "risk heat is high.")
    if exposure.label == "Net Short" and overall_score > 20:
        return BadgeReadout("Action Bias", "Add Spot", "good", overall_score, "spot could soften the short-perp imbalance.")
    if exposure.label == "Net Long" and overall_score < -20:
        return BadgeReadout("Action Bias", "Add Hedge", "mixed", overall_score, "a hedge could reduce downside.")
    if overall_score <= -35:
        return BadgeReadout("Action Bias", "Avoid", "bad", overall_score, "technical read is not supportive.")
    return BadgeReadout("Action Bias", "Watch", "mixed", overall_score, "wait for cleaner confirmation.")


def _crypto_risk_why(indicators: AdvancedIndicatorSnapshot, exposure: CryptoExposure) -> str:
    return f"portfolio share {exposure.portfolio_share:.2%}; volatility {indicators.volatility}; open orders {exposure.open_orders}."


def _crypto_top_things(
    exposure: BadgeReadout,
    risk: BadgeReadout,
    indicators: AdvancedIndicatorSnapshot,
    candle_result: CryptoCandleResult | None,
) -> list[str]:
    first = f"Exposure: {exposure.label}"
    second = f"Risk heat: {risk_heat_label(risk.score)}"
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
) -> list[str]:
    lines = [
        f"{exposure.coin} is currently {exposure_badge.label.lower()} based on synced spot/perp exposure.",
        f"The overall read is {overall.label.lower()} with {risk.label.lower()} risk heat.",
    ]
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
) -> dict[str, str]:
    key_level = "No clean level loaded"
    if indicators.resistance:
        key_level = f"Resistance ${indicators.resistance:,.4f}"
    elif indicators.support:
        key_level = f"Support ${indicators.support:,.4f}"
    return {
        "Current setup": exposure.label,
        "Bias": action.label,
        "Good thing": "Spot and perp are close to balanced." if exposure.label == "Hedged" else "There is a clear exposure read.",
        "Bad thing": "Perp has no spot hedge." if current.perp_notional and not current.spot_value else "Crypto volatility can move fast.",
        "Key level": key_level,
        "Best next check": "Candles, funding, open orders, and TP/SL.",
        "If price rises": "Spot gains; short perps lose." if current.perp_direction == "short" else "Long exposure gains.",
        "If price falls": "Short perps help; spot loses." if current.perp_direction == "short" else "Spot/long exposure loses.",
    }
