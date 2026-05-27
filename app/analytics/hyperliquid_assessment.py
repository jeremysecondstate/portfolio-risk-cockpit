from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.portfolio import Portfolio, Position

ZERO_EPSILON = 0.00000001


@dataclass(frozen=True)
class HyperliquidExposureSnapshot:
    total_value: float
    perp_account_value: float | None
    withdrawable: float | None
    margin_used: float | None
    perp_notional: float
    spot_value: float
    cash_value: float
    long_perp_notional: float
    short_perp_notional: float
    net_perp_notional: float
    largest_position_symbol: str | None
    largest_position_value: float
    open_order_count: int


def format_hyperliquid_position_assessment(snapshot: Any, portfolio: Portfolio) -> str:
    exposure = _build_exposure_snapshot(snapshot, portfolio)
    lines = [
        "POSITION ASSESSMENT",
        "===================",
        "",
        "Portfolio mix:",
        f"- Total Hyperliquid value loaded: {_format_money(exposure.total_value)}",
        f"- Spot / cash value: {_format_money(exposure.spot_value + exposure.cash_value)} ({_format_percent(_ratio(exposure.spot_value + exposure.cash_value, exposure.total_value))})",
        f"- Perp notional exposure: {_format_money(exposure.perp_notional)} ({_format_percent(_ratio(exposure.perp_notional, exposure.total_value))} of account value)",
        f"- Net perp direction: {_format_net_direction(exposure.net_perp_notional, exposure.total_value)}",
        "",
        "Margin and liquidity:",
        f"- Perp account value: {_format_optional_money(exposure.perp_account_value)}",
        f"- Withdrawable: {_format_optional_money(exposure.withdrawable)}",
        f"- Margin used: {_format_optional_money(exposure.margin_used)} ({_format_percent(_ratio(exposure.margin_used, exposure.perp_account_value))} of perp account value)",
        f"- Open orders: {exposure.open_order_count}",
        "",
        "Concentration:",
        _concentration_line(exposure),
        "",
        "Assessment:",
    ]
    lines.extend(f"- {line}" for line in _assessment_lines(exposure))
    lines.extend(
        [
            "",
            "Suggested review steps:",
            "- Confirm open orders still match the intended thesis; stale reduce/add orders can distort risk.",
            "- Compare perp notional to total account value before adding size; notional can grow faster than cash/spot balances.",
            "- For hedged coins, compare spot size versus perp direction so the cockpit view matches your actual exposure intent.",
            "- This is a risk assessment, not a recommendation to add, close, or reverse positions.",
        ]
    )
    return "\n".join(lines)


def _build_exposure_snapshot(snapshot: Any, portfolio: Portfolio) -> HyperliquidExposureSnapshot:
    state = getattr(snapshot, "clearinghouse_state", {}) or {}
    margin_summary = state.get("marginSummary") or state.get("crossMarginSummary") or {}
    perp_account_value = _first_number(margin_summary, "accountValue", "totalRawUsd") or _first_number(state, "accountValue")
    withdrawable = _first_number(state, "withdrawable")
    margin_used = _first_number(margin_summary, "totalMarginUsed")
    perp_notional_from_api = _first_number(margin_summary, "totalNtlPos")

    perp_positions = [position for position in portfolio.positions.values() if "-PERP" in position.symbol]
    spot_positions = [position for position in portfolio.positions.values() if position.symbol.endswith("-SPOT")]
    cash_value = sum(cash.market_value for cash in portfolio.display_cash_positions())
    spot_value = sum(abs(position.market_value) for position in spot_positions)
    perp_notional = abs(perp_notional_from_api) if perp_notional_from_api is not None else sum(abs(position.market_value) for position in perp_positions)

    long_perp_notional = 0.0
    short_perp_notional = 0.0
    for position in perp_positions:
        value = abs(position.market_value)
        if position.symbol.endswith("-SHORT"):
            short_perp_notional += value
        else:
            long_perp_notional += value

    net_perp_notional = long_perp_notional - short_perp_notional
    largest = _largest_position(portfolio.positions.values())
    open_orders = getattr(snapshot, "open_orders", []) or []

    return HyperliquidExposureSnapshot(
        total_value=max(portfolio.total_value, 0.0),
        perp_account_value=perp_account_value,
        withdrawable=withdrawable,
        margin_used=margin_used,
        perp_notional=perp_notional,
        spot_value=spot_value,
        cash_value=cash_value,
        long_perp_notional=long_perp_notional,
        short_perp_notional=short_perp_notional,
        net_perp_notional=net_perp_notional,
        largest_position_symbol=largest.symbol if largest is not None else None,
        largest_position_value=abs(largest.market_value) if largest is not None else 0.0,
        open_order_count=len(open_orders) if isinstance(open_orders, list) else 0,
    )


def _assessment_lines(exposure: HyperliquidExposureSnapshot) -> list[str]:
    lines: list[str] = []
    notional_ratio = _ratio(exposure.perp_notional, exposure.total_value)
    margin_ratio = _ratio(exposure.margin_used, exposure.perp_account_value)
    concentration_ratio = _ratio(exposure.largest_position_value, exposure.total_value)

    if notional_ratio is None:
        lines.append("Perp leverage read is unavailable because account value or notional was missing.")
    elif notional_ratio >= 1.5:
        lines.append(f"Perp notional is high at {_format_percent(notional_ratio)} of loaded value; size changes can move risk quickly.")
    elif notional_ratio >= 0.75:
        lines.append(f"Perp notional is meaningful at {_format_percent(notional_ratio)} of loaded value; monitor liquidation and margin buffers.")
    elif exposure.perp_notional > ZERO_EPSILON:
        lines.append(f"Perp notional is modest relative to loaded value at {_format_percent(notional_ratio)}.")
    else:
        lines.append("No active perp notional was detected; the account is currently spot/cash weighted.")

    if margin_ratio is not None:
        if margin_ratio >= 0.50:
            lines.append(f"Margin usage is elevated at {_format_percent(margin_ratio)} of perp account value.")
        elif margin_ratio >= 0.25:
            lines.append(f"Margin usage is moderate at {_format_percent(margin_ratio)} of perp account value.")
        else:
            lines.append(f"Margin usage appears light at {_format_percent(margin_ratio)} of perp account value.")

    if abs(exposure.net_perp_notional) <= exposure.total_value * 0.05:
        lines.append("Long and short perp notionals are roughly balanced, so directional perp exposure appears hedged/market-neutral-ish.")
    elif exposure.net_perp_notional > 0:
        lines.append("Net perp exposure leans long after offsetting short notional.")
    else:
        lines.append("Net perp exposure leans short after offsetting long notional.")

    if concentration_ratio is not None:
        if concentration_ratio >= 0.50:
            lines.append(f"Largest position concentration is high: {exposure.largest_position_symbol} is {_format_percent(concentration_ratio)} of loaded value.")
        elif concentration_ratio >= 0.30:
            lines.append(f"Largest position concentration is notable: {exposure.largest_position_symbol} is {_format_percent(concentration_ratio)} of loaded value.")
        elif exposure.largest_position_symbol:
            lines.append(f"Largest position is {exposure.largest_position_symbol} at {_format_percent(concentration_ratio)} of loaded value.")

    if exposure.open_order_count >= 8:
        lines.append(f"There are {exposure.open_order_count} open orders; review for stale orders or unintended adds/reductions.")
    elif exposure.open_order_count > 0:
        lines.append(f"There are {exposure.open_order_count} open orders; make sure they still match the active plan.")
    else:
        lines.append("No open orders were detected, which keeps the current exposure easier to reason about.")

    return lines


def _largest_position(positions) -> Position | None:
    clean_positions = [position for position in positions if abs(position.market_value) > ZERO_EPSILON]
    if not clean_positions:
        return None
    return max(clean_positions, key=lambda position: abs(position.market_value))


def _concentration_line(exposure: HyperliquidExposureSnapshot) -> str:
    if not exposure.largest_position_symbol:
        return "- No non-cash position concentration detected."
    ratio = _ratio(exposure.largest_position_value, exposure.total_value)
    return f"- Largest position: {exposure.largest_position_symbol} at {_format_money(exposure.largest_position_value)} ({_format_percent(ratio)} of loaded value)."


def _format_net_direction(net_notional: float, total_value: float) -> str:
    ratio = _ratio(abs(net_notional), total_value)
    if abs(net_notional) <= ZERO_EPSILON:
        return "roughly flat"
    direction = "long" if net_notional > 0 else "short"
    return f"{_format_money(abs(net_notional))} net {direction} ({_format_percent(ratio)} of loaded value)"


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or abs(denominator) <= ZERO_EPSILON:
        return None
    return numerator / denominator


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if value in (None, ""):
            continue
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return None


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _format_optional_money(value: float | None) -> str:
    return "--" if value is None else _format_money(value)


def _format_percent(value: float | None) -> str:
    return "--" if value is None else f"{value * 100:.1f}%"
