from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
from typing import Any

import requests

from app.core.portfolio import CashPosition, Portfolio, Position


DEFAULT_INFO_URL = "https://api.hyperliquid.xyz/info"
ZERO_EPSILON = 0.00000001
CASH_LIKE_COINS = {"USDC", "USD"}
SPOT_COIN_ALIASES: dict[str, tuple[str, ...]] = {
    # Hyperliquid can expose wrapped spot tokens with a U-prefix while the UI
    # shows the familiar ticker. Treat both spellings as the same market.
    "BTC": ("BTC", "UBTC"),
    "UBTC": ("UBTC", "BTC"),
    "ETH": ("ETH", "UETH"),
    "UETH": ("UETH", "ETH"),
}
DEFAULT_SPOT_MID_KEYS: dict[str, tuple[str, ...]] = {
    # Hyperliquid allMids can expose spot markets by @index. Keep this as a
    # harmless fallback while also supporting direct coin/name matches.
    "HYPE": ("HYPE", "HYPE/USDC", "@107"),
}
SPOT_CURRENT_VALUE_KEYS = ("usdValue", "usdcValue", "currentValue", "marketValue", "value")
SPOT_ENTRY_NOTIONAL_KEYS = (
    "entryNtl",
    "entryNotional",
    "entryUsd",
    "costBasis",
    "costBasisUsd",
    "initialUsd",
    "initialNotional",
    "purchaseValue",
)
SPOT_AVERAGE_COST_KEYS = ("avgEntryPx", "averageEntryPrice", "avgCost", "averageCost")
SPOT_PNL_KEYS = (
    "unrealizedPnl",
    "unrealizedPnlUsd",
    "unrealizedProfitLoss",
    "openPnl",
    "pnl",
    "PNL",
    "profitAndLoss",
    "spotPnl",
    "uPnl",
)


@dataclass(frozen=True)
class HyperliquidSnapshot:
    """Read-only Hyperliquid account data pulled from the public info API."""

    user: str
    clearinghouse_state: dict[str, Any]
    spot_state: dict[str, Any]
    open_orders: list[dict[str, Any]]
    all_mids: dict[str, Any]
    spot_meta_and_asset_ctxs: Any
    fetched_at: datetime


class HyperliquidInfoClient:
    """Small read-only wrapper around Hyperliquid's public info endpoint.

    Balance/position/P&L sync only needs the user's master or sub-account
    address. Do not pass an API/agent wallet address here; those are for signed
    actions such as placing or canceling orders.
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
        all_mids = self._safe_post_info({"type": "allMids"}, default={})
        spot_meta_and_asset_ctxs = self._safe_post_info({"type": "spotMetaAndAssetCtxs"}, default=None)

        if not isinstance(clearinghouse_state, dict):
            raise RuntimeError("Unexpected Hyperliquid clearinghouseState response shape.")
        if not isinstance(spot_state, dict):
            spot_state = {}
        if not isinstance(open_orders, list):
            open_orders = []
        if not isinstance(all_mids, dict):
            all_mids = {}

        return HyperliquidSnapshot(
            user=normalized_user,
            clearinghouse_state=clearinghouse_state,
            spot_state=spot_state,
            open_orders=[order for order in open_orders if isinstance(order, dict)],
            all_mids=all_mids,
            spot_meta_and_asset_ctxs=spot_meta_and_asset_ctxs,
            fetched_at=datetime.now(),
        )

    def _safe_post_info(self, payload: dict[str, Any], default: Any) -> Any:
        """Optional read-only enrichment call; sync should still work without it."""
        try:
            return self.post_info(payload)
        except Exception:
            return default


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
    """Convert Hyperliquid state into the cockpit's stock-shaped Portfolio model."""

    positions: dict[str, Position] = {}
    cash_positions: dict[str, CashPosition] = {}
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
        position, cash_position = _spot_position_from_hyperliquid(raw_balance, snapshot)
        if cash_position is not None:
            cash_positions[f"{cash_position.symbol}:HYPERLIQUID"] = cash_position
            spot_cash += cash_position.amount
            continue
        if position is None:
            continue
        positions[position.symbol] = position
        spot_positions_value += position.market_value

    perp_account_value = _perp_account_value(snapshot.clearinghouse_state)
    total_value = round(perp_account_value + spot_cash + spot_positions_value, 2)
    positions_value = round(perp_notional + spot_positions_value, 2)
    cash = round(total_value - positions_value, 2)
    perp_cash = round(cash - spot_cash, 2)
    if abs(perp_cash) > 0.005:
        cash_positions["USDC:HYPERLIQUID-PERP"] = CashPosition("USDC", perp_cash, "Hyperliquid Perps")

    source_message = (
        f"Loaded Hyperliquid account {_short_address(snapshot.user)} "
        f"at {snapshot.fetched_at.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    return Portfolio(cash=cash, positions=positions, cash_positions=cash_positions), source_message


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
            entry_ntl = _spot_entry_notional(raw_balance, total)
            current_value = _spot_current_value(coin, total, snapshot, raw_balance)
            direct_pnl = _spot_direct_pnl(raw_balance)
            pnl = direct_pnl if direct_pnl is not None else (
                (current_value - entry_ntl) if current_value is not None and entry_ntl is not None else None
            )
            if entry_ntl is None and current_value is not None and pnl is not None:
                entry_ntl = current_value - pnl
            pnl_percent = (pnl / entry_ntl * 100) if pnl is not None and entry_ntl and entry_ntl > 0 else None
            spot_price = (current_value / total) if current_value is not None and total and total > ZERO_EPSILON else _spot_mid_price(coin, snapshot)
            lines.append(
                f"- {coin}: total {_format_optional_number(total)}, "
                f"held {_format_optional_number(hold)}, "
                f"price {_format_optional_money(spot_price)}, "
                f"value {_format_optional_money(current_value)}, "
                f"entry {_format_optional_money(entry_ntl)}, "
                f"P&L {_format_optional_money(pnl)} ({_format_optional_percent(pnl_percent)})"
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
            "Spot P&L is read-only: it uses Hyperliquid's spot P&L field when present, otherwise current value minus entry notional.",
            "Spot prices resolve through Hyperliquid spot metadata when allMids only exposes @-indexed spot markets.",
            "API/agent wallets are only needed for future signed actions.",
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


def _spot_position_from_hyperliquid(raw_balance: dict[str, Any], snapshot: HyperliquidSnapshot) -> tuple[Position | None, CashPosition | None]:
    coin = str(raw_balance.get("coin") or raw_balance.get("token") or "").strip().upper()
    quantity = _first_number(raw_balance, ("total", "balance", "amount")) or 0.0
    if not coin or quantity <= ZERO_EPSILON:
        return None, None

    current_value = _spot_current_value(coin, quantity, snapshot, raw_balance)
    mid_price = _spot_mid_price(coin, snapshot)
    entry_notional = _spot_entry_notional(raw_balance, quantity)
    direct_pnl = _spot_direct_pnl(raw_balance)

    if current_value is None and mid_price is not None:
        current_value = quantity * mid_price

    if entry_notional is None and current_value is not None and direct_pnl is not None:
        entry_notional = current_value - direct_pnl

    average_cost = (entry_notional / quantity) if entry_notional is not None and quantity > ZERO_EPSILON else None

    if coin in CASH_LIKE_COINS:
        return None, CashPosition(coin, round(current_value or quantity, 2), "Hyperliquid")

    last_price = (
        (current_value / quantity) if current_value is not None and quantity > ZERO_EPSILON else None
    ) or mid_price or average_cost or 0.0
    open_profit_loss = direct_pnl if direct_pnl is not None else (
        (current_value - entry_notional) if current_value is not None and entry_notional is not None else None
    )

    return (
        Position(
            symbol=f"{coin}-SPOT",
            quantity=round(quantity, 8),
            average_cost=round(average_cost or last_price, 4),
            last_price=round(last_price, 4),
            open_profit_loss=round(open_profit_loss, 2) if open_profit_loss is not None else None,
        ),
        None,
    )


def _spot_current_value(
    coin: str,
    quantity: float | None,
    snapshot: HyperliquidSnapshot,
    raw_balance: dict[str, Any],
) -> float | None:
    """Prefer Hyperliquid's own USD balance value so the cockpit mirrors the UI."""

    direct_usd_value = _first_number(raw_balance, SPOT_CURRENT_VALUE_KEYS)
    if direct_usd_value is not None:
        return direct_usd_value

    mid_price = _spot_mid_price(coin, snapshot)
    if quantity is not None and mid_price is not None:
        return quantity * mid_price
    return None


def _spot_entry_notional(raw_balance: dict[str, Any], quantity: float | None) -> float | None:
    entry_notional = _first_number(raw_balance, SPOT_ENTRY_NOTIONAL_KEYS)
    if entry_notional is not None:
        return entry_notional

    average_cost = _first_number(raw_balance, SPOT_AVERAGE_COST_KEYS)
    if average_cost is not None and quantity is not None and quantity > ZERO_EPSILON:
        return average_cost * quantity

    return None


def _spot_direct_pnl(raw_balance: dict[str, Any]) -> float | None:
    return _first_number(raw_balance, SPOT_PNL_KEYS)


def _spot_mid_price(coin: str, snapshot: HyperliquidSnapshot) -> float | None:
    coin = coin.strip().upper()
    if coin in CASH_LIKE_COINS:
        return 1.0

    aliases = _spot_coin_aliases(coin)
    env_key = os.getenv(f"HYPERLIQUID_{coin}_MID_KEY", "").strip()
    candidate_keys = [env_key]
    for alias in aliases:
        candidate_keys.extend((alias, f"{alias}/USDC", f"{alias}-USDC"))
        candidate_keys.extend(DEFAULT_SPOT_MID_KEYS.get(alias, ()))

    for key in candidate_keys:
        if not key:
            continue
        value = _all_mids_value(snapshot.all_mids, key)
        if value is not None:
            return value

    meta_price = _spot_meta_price(coin, snapshot.spot_meta_and_asset_ctxs, snapshot.all_mids)
    if meta_price is not None:
        return meta_price

    for key, raw_value in snapshot.all_mids.items():
        upper_key = str(key).upper()
        if _matches_spot_alias(upper_key, aliases):
            value = _to_float(raw_value)
            if value is not None:
                return value
    return None


def _spot_meta_price(coin: str, payload: Any, all_mids: dict[str, Any] | None = None) -> float | None:
    if not isinstance(payload, list) or len(payload) < 2:
        return None
    meta, asset_ctxs = payload[0], payload[1]
    universe = meta.get("universe") if isinstance(meta, dict) else None
    tokens = meta.get("tokens") if isinstance(meta, dict) else None
    if not isinstance(universe, list) or not isinstance(asset_ctxs, list):
        return None

    aliases = _spot_coin_aliases(coin)
    token_names_by_index = _spot_token_names_by_index(tokens)
    mids = all_mids if isinstance(all_mids, dict) else {}

    for market_index, asset in enumerate(universe):
        if not isinstance(asset, dict):
            continue

        market_names = _spot_market_names(asset, token_names_by_index)
        if not any(_matches_spot_alias(name, aliases) for name in market_names):
            continue

        ctx = asset_ctxs[market_index] if market_index < len(asset_ctxs) and isinstance(asset_ctxs[market_index], dict) else {}
        price = _first_number(ctx, ("midPx", "markPx", "oraclePx", "price", "prevDayPx"))
        if price is not None:
            return price

        candidate_keys = set(market_names)
        candidate_keys.add(f"@{market_index}")
        asset_index = _to_int(asset.get("index"))
        if asset_index is not None:
            candidate_keys.add(f"@{asset_index}")
        for key in candidate_keys:
            price = _all_mids_value(mids, key)
            if price is not None:
                return price
    return None


def _spot_token_names_by_index(tokens: Any) -> dict[int, set[str]]:
    names_by_index: dict[int, set[str]] = {}
    if not isinstance(tokens, list):
        return names_by_index

    for fallback_index, token in enumerate(tokens):
        if not isinstance(token, dict):
            continue
        token_names = {
            str(value).strip().upper()
            for key in ("name", "coin", "token", "fullName")
            for value in (token.get(key),)
            if value not in (None, "")
        }
        if not token_names:
            continue

        names_by_index.setdefault(fallback_index, set()).update(token_names)
        explicit_index = _to_int(token.get("index"))
        if explicit_index is not None:
            names_by_index.setdefault(explicit_index, set()).update(token_names)
    return names_by_index


def _spot_market_names(asset: dict[str, Any], token_names_by_index: dict[int, set[str]]) -> set[str]:
    names = {
        str(value).strip().upper()
        for key in ("name", "coin", "token", "symbol")
        for value in (asset.get(key),)
        if value not in (None, "")
    }

    token_indices = asset.get("tokens")
    if isinstance(token_indices, list) and token_indices:
        base_index = _to_int(token_indices[0])
        quote_index = _to_int(token_indices[1]) if len(token_indices) > 1 else None
        base_names = token_names_by_index.get(base_index, set()) if base_index is not None else set()
        quote_names = token_names_by_index.get(quote_index, set()) if quote_index is not None else {"USDC"}
        names.update(base_names)
        for base in base_names:
            if quote_names:
                for quote in quote_names:
                    names.add(f"{base}/{quote}")
                    names.add(f"{base}-{quote}")
            else:
                names.add(f"{base}/USDC")
                names.add(f"{base}-USDC")
    return {name for name in names if name}


def _spot_coin_aliases(coin: str) -> tuple[str, ...]:
    normalized = coin.strip().upper()
    aliases = SPOT_COIN_ALIASES.get(normalized, (normalized,))
    result: list[str] = []
    for alias in aliases:
        alias = alias.strip().upper()
        if alias and alias not in result:
            result.append(alias)
    return tuple(result)


def _matches_spot_alias(value: str, aliases: tuple[str, ...]) -> bool:
    upper_value = value.strip().upper()
    for alias in aliases:
        if upper_value == alias or upper_value.startswith(f"{alias}/") or upper_value.startswith(f"{alias}-"):
            return True
    return False


def _all_mids_value(all_mids: dict[str, Any], key: str) -> float | None:
    value = _to_float(all_mids.get(key))
    if value is not None:
        return value

    upper_key = key.upper()
    for raw_key, raw_value in all_mids.items():
        if str(raw_key).upper() == upper_key:
            value = _to_float(raw_value)
            if value is not None:
                return value
    return None


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

    lowercase_values = {str(key).lower(): value for key, value in container.items()}
    for key in keys:
        value = _to_float(lowercase_values.get(key.lower()))
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


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(number)


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


def _format_optional_percent(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2f}%"
