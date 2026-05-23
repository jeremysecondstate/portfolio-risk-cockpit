from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from typing import Any

import requests

from app.core.portfolio import Portfolio, Position


DEFAULT_INFO_URL = "https://api.hyperliquid.xyz/info"
ZERO_EPSILON = 0.00000001
CASH_LIKE_COINS = {"USDC", "USD"}


@dataclass(frozen=True)
class HyperliquidSnapshot:
    """Read-only Hyperliquid account data pulled from the public info API."""

    user: str
    clearinghouse_state: dict[str, Any]
    spot_state: dict[str, Any]
    open_orders: list[dict[str, Any]]
    fetched_at: datetime


class HyperliquidInfoClient:
    """Small read-only wrapper around Hyperliquid's public info endpoint.

    Balance/position sync only needs the user's master or sub-account address.
    Do not pass an API/agent wallet address here; those are for signed actions.
    """

    def __init__(self, info_url: str | None = None, timeout_seconds: int = 30) -> None:
        self.info_url = (info_url or os.getenv("HYPERLIQUID_INFO_URL") or DEFAULT_INFO_URL).strip()
        self.timeout_seconds = timeout_seconds

    def post_info(self, payload: dict[str, Any]) -> Any:
        response = requests.post(
            self.info_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError("Hyperliquid returned a non-JSON response.") from exc

    def fetch_snapshot(self, user: str, include_open_orders: bool = True) -> HyperliquidSnapshot:
        normalized_user = normalize_hyperliquid_address(user)
        clearinghouse_state = self.post_info({"type": "clearinghouseState", "user": normalized_user})
        spot_state = self.post_info({"type": "spotClearinghouseState", "user": normalized_user})
        open_orders = self.post_info({"type": "openOrders", "user": normalized_user}) if include_open_orders else []

        if not isinstance(clearinghouse_state, dict):
            raise RuntimeError("Unexpected Hyperliquid clearinghouseState response shape.")
        if not isinstance(spot_state, dict):
            spot_state = {}
        if not isinstance(open_orders, list):
            open_orders = []

        return HyperliquidSnapshot(
            user=normalized_user,
            clearinghouse_state=clearinghouse_state,
            spot_state=spot_state,
            open_orders=[order for order in open_orders if isinstance(order, dict)],
            fetched_at=datetime.now(),
        )


def normalize_hyperliquid_address(user: str) -> str:
    address = (user or "").strip()
    if not address:
        raise ValueError("Enter your Hyperliquid master or sub-account wallet address.")
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError(
            "Hyperliquid sync expects a 42-character 0x account address. "
            "Use the master/sub-account address, not the API wallet address."
        )
    return address


def portfolio_from_hyperliquid_snapshot(snapshot: HyperliquidSnapshot) -> tuple[Portfolio, str]:
    """Convert Hyperliquid state into the cockpit's stock-shaped Portfolio model.

    The cockpit's Portfolio object expects cash plus long-style market values.
    Perpetual futures are therefore shown as positive notional exposure, with
    SHORT added to the symbol when the Hyperliquid size is negative. Cash is the
    balancing line that makes total_value equal Hyperliquid equity plus spot value.
    """

    positions: dict[str, Position] = {}
    perp_notional = 0.0

    for raw_position in _asset_positions(snapshot.clearinghouse_state):
        position = _perp_position_from_hyperliquid(raw_position)
        if position is None:
            continue
        positions[position.symbol] = position
        perp_notional += position.market_value

    spot_cash = 0.0
    spot_positions_value = 0.0
    for raw_balance in _spot_balances(snapshot.spot_state):
        position, cash_value = _spot_position_from_hyperliquid(raw_balance)
        spot_cash += cash_value
        if position is None:
            continue
        positions[position.symbol] = position
        spot_positions_value += position.market_value

    perp_account_value = _perp_account_value(snapshot.clearinghouse_state)
    total_value = round(perp_account_value + spot_cash + spot_positions_value, 2)
    positions_value = round(perp_notional + spot_positions_value, 2)
    cash = round(total_value - positions_value, 2)

    source_message = (
        f"Loaded Hyperliquid account {_short_address(snapshot.user)} "
        f"at {snapshot.fetched_at.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return Portfolio(cash=cash, positions=positions), source_message


def format_hyperliquid_snapshot(snapshot: HyperliquidSnapshot, portfolio: Portfolio) -> str:
    state = snapshot.clearinghouse_state
    margin_summary = state.get("marginSummary") or state.get("crossMarginSummary") or {}
    withdrawable = _to_float(state.get("withdrawable"))
    account_value = _perp_account_value(state)

    lines = [
        "HYPERLIQUID PORTFOLIO SYNC",
        "===========================",
        "",
        f"Wallet: {_short_address(snapshot.user)}",
        f"Fetched: {snapshot.fetched_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "Mode: read-only public info API; no API wallet or private key used.",
        "",
        "Account summary:",
        f"- Perp account value: {_format_money(account_value)}",
        f"- Withdrawable: {_format_optional_money(withdrawable)}",
        f"- Total value loaded into cockpit: {_format_money(portfolio.total_value)}",
        f"- Positions loaded: {len(portfolio.positions)}",
        f"- Open orders: {len(snapshot.open_orders)}",
        "",
    ]

    total_margin_used = _to_float(margin_summary.get("totalMarginUsed"))
    total_notional = _to_float(margin_summary.get("totalNtlPos"))
    if total_margin_used is not None or total_notional is not None:
        lines.extend(
            [
                "Perp margin:",
                f"- Total margin used: {_format_optional_money(total_margin_used)}",
                f"- Total notional position: {_format_optional_money(total_notional)}",
                "",
            ]
        )

    perp_positions = [position for position in _asset_positions(snapshot.clearinghouse_state) if _perp_position_from_hyperliquid(position)]
    lines.append("Perp positions:")
    if not perp_positions:
        lines.append("- None")
    else:
        for raw_position in perp_positions:
            position_data = raw_position.get("position") or raw_position
            coin = str(position_data.get("coin") or "UNKNOWN").upper()
            signed_size = _to_float(position_data.get("szi")) or 0.0
            side = "LONG" if signed_size >= 0 else "SHORT"
            entry_px = _to_float(position_data.get("entryPx"))
            notional = _to_float(position_data.get("positionValue"))
            unrealized_pnl = _to_float(position_data.get("unrealizedPnl"))
            lines.append(
                f"- {coin}-PERP {side}: size {abs(signed_size):g}, "
                f"notional {_format_optional_money(notional)}, "
                f"entry {_format_optional_money(entry_px)}, "
                f"uPnL {_format_optional_money(unrealized_pnl)}"
            )

    lines.extend(["", "Spot balances:"])
    spot_balances = _spot_balances(snapshot.spot_state)
    if not spot_balances:
        lines.append("- None")
    else:
        for raw_balance in spot_balances:
            coin = str(raw_balance.get("coin") or raw_balance.get("token") or "UNKNOWN").upper()
            total = _first_number(raw_balance, ("total", "balance", "amount"))
            hold = _first_number(raw_balance, ("hold", "held", "locked"))
            entry_ntl = _first_number(raw_balance, ("entryNtl", "entryNotional", "usdValue"))
            lines.append(
                f"- {coin}: total {_format_optional_number(total)}, "
                f"held {_format_optional_number(hold)}, "
                f"notional/entry {_format_optional_money(entry_ntl)}"
            )

    lines.extend(["", "Open orders:"])
    if not snapshot.open_orders:
        lines.append("- None")
    else:
        for order in snapshot.open_orders[:12]:
            coin = order.get("coin", "UNKNOWN")
            side = order.get("side", "UNKNOWN")
            size = order.get("sz", order.get("size", "?"))
            price = order.get("limitPx", order.get("price", "?"))
            oid = order.get("oid", "?")
            lines.append(f"- {coin} {side} {size} @ {price} · oid {oid}")
        if len(snapshot.open_orders) > 12:
            lines.append(f"- ... {len(snapshot.open_orders) - 12} more")

    lines.extend(
        [
            "",
            "No order was submitted, replaced, or canceled.",
            "Use your main Hyperliquid account address for sync. API/agent wallets are only needed for future signed actions.",
        ]
    )
    return "\n".join(lines)


def _asset_positions(state: dict[str, Any]) -> list[dict[str, Any]]:
    positions = state.get("assetPositions") or []
    return [position for position in positions if isinstance(position, dict)] if isinstance(positions, list) else []


def _spot_balances(state: dict[str, Any]) -> list[dict[str, Any]]:
    balances = state.get("balances") or []
    return [balance for balance in balances if isinstance(balance, dict)] if isinstance(balances, list) else []


def _perp_position_from_hyperliquid(raw_position: dict[str, Any]) -> Position | None:
    position_data = raw_position.get("position") or raw_position
    if not isinstance(position_data, dict):
        return None

    coin = str(position_data.get("coin") or "").strip().upper()
    signed_size = _to_float(position_data.get("szi")) or _to_float(position_data.get("size")) or 0.0
    if not coin or abs(signed_size) <= ZERO_EPSILON:
        return None

    quantity = abs(signed_size)
    symbol = f"{coin}-PERP" if signed_size > 0 else f"{coin}-PERP-SHORT"
    position_value = abs(_to_float(position_data.get("positionValue")) or 0.0)
    entry_px = _to_float(position_data.get("entryPx"))
    mark_px = _first_number(position_data, ("markPx", "oraclePx", "midPx"))
    last_price = mark_px or (position_value / quantity if position_value > 0 else None) or entry_px or 0.0
    average_cost = entry_px or last_price

    return Position(
        symbol=symbol,
        quantity=round(quantity, 8),
        average_cost=round(average_cost, 4),
        last_price=round(last_price, 4),
        open_profit_loss=_to_float(position_data.get("unrealizedPnl")),
    )


def _spot_position_from_hyperliquid(raw_balance: dict[str, Any]) -> tuple[Position | None, float]:
    coin = str(raw_balance.get("coin") or raw_balance.get("token") or "").strip().upper()
    quantity = _first_number(raw_balance, ("total", "balance", "amount")) or 0.0
    if not coin or quantity <= ZERO_EPSILON:
        return None, 0.0

    price = _first_number(raw_balance, ("markPx", "midPx", "price"))
    entry_notional = _first_number(raw_balance, ("entryNtl", "entryNotional", "usdValue"))
    average_cost = (entry_notional / quantity) if entry_notional is not None and quantity > ZERO_EPSILON else None
    last_price = price or average_cost or (1.0 if coin in CASH_LIKE_COINS else 0.0)

    if coin in CASH_LIKE_COINS:
        return None, round(quantity * last_price, 2)

    return (
        Position(
            symbol=f"{coin}-SPOT",
            quantity=round(quantity, 8),
            average_cost=round(average_cost or last_price, 4),
            last_price=round(last_price, 4),
        ),
        0.0,
    )


def _perp_account_value(state: dict[str, Any]) -> float:
    margin_summary = state.get("marginSummary") if isinstance(state.get("marginSummary"), dict) else {}
    cross_margin_summary = state.get("crossMarginSummary") if isinstance(state.get("crossMarginSummary"), dict) else {}
    return (
        _to_float(margin_summary.get("accountValue"))
        or _to_float(cross_margin_summary.get("accountValue"))
        or _to_float(state.get("accountValue"))
        or _to_float(state.get("withdrawable"))
        or 0.0
    )


def _first_number(container: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _to_float(container.get(key))
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _short_address(address: str) -> str:
    if len(address) < 12:
        return address
    return f"{address[:6]}…{address[-4:]}"


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _format_optional_money(value: float | None) -> str:
    return "--" if value is None else _format_money(value)


def _format_optional_number(value: float | None) -> str:
    return "--" if value is None else f"{value:g}"
