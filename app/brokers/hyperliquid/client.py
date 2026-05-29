from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import os
from typing import Any

import requests

from app.core.portfolio import CashPosition, Portfolio, Position


DEFAULT_INFO_URL = "https://api.hyperliquid.xyz/info"
ZERO_EPSILON = 0.00000001
SPOT_PRICE_SANITY_MULTIPLE = 100.0
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
    user_fills: list[dict[str, Any]] = field(default_factory=list)
    user_non_funding_ledger_updates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SpotCostBasis:
    quantity: float = 0.0
    cost_basis: float = 0.0


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
        user_fills = self._safe_post_info(
            {"type": "userFills", "user": normalized_user, "aggregateByTime": False},
            default=[],
        )
        user_non_funding_ledger_updates = self._safe_post_info(
            {"type": "userNonFundingLedgerUpdates", "user": normalized_user, "startTime": 0},
            default=[],
        )

        if not isinstance(clearinghouse_state, dict):
            raise RuntimeError("Unexpected Hyperliquid clearinghouseState response shape.")
        if not isinstance(spot_state, dict):
            spot_state = {}
        if not isinstance(open_orders, list):
            open_orders = []
        if not isinstance(all_mids, dict):
            all_mids = {}
        if not isinstance(user_fills, list):
            user_fills = []
        if not isinstance(user_non_funding_ledger_updates, list):
            user_non_funding_ledger_updates = []

        return HyperliquidSnapshot(
            user=normalized_user,
            clearinghouse_state=clearinghouse_state,
            spot_state=spot_state,
            open_orders=[order for order in open_orders if isinstance(order, dict)],
            all_mids=all_mids,
            spot_meta_and_asset_ctxs=spot_meta_and_asset_ctxs,
            fetched_at=datetime.now(),
            user_fills=[fill for fill in user_fills if isinstance(fill, dict)],
            user_non_funding_ledger_updates=[
                update for update in user_non_funding_ledger_updates if isinstance(update, dict)
            ],
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
    reconstructed_spot_basis = _reconstruct_spot_cost_basis(snapshot)

    for raw_position in _asset_positions(snapshot.clearinghouse_state):
        position = _perp_position_from_hyperliquid(raw_position)
        if position is None:
            continue
        positions[position.symbol] = position
        perp_notional += position.market_value

    spot_cash = 0.0
    spot_positions_value = 0.0
    for raw_balance in _spot_balances(snapshot.spot_state):
        position, cash_position = _spot_position_from_hyperliquid(
            raw_balance,
            snapshot,
            reconstructed_spot_basis=reconstructed_spot_basis,
        )
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
    reconstructed_spot_basis = _reconstruct_spot_cost_basis(snapshot)

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
            token_index = _spot_token_index_from_balance(raw_balance)
            entry_ntl = _spot_api_entry_notional(raw_balance, total)
            reference_price = _reference_price(entry_ntl, total)
            current_value = _spot_current_value(
                coin,
                total,
                snapshot,
                raw_balance,
                token_index=token_index,
                reference_price=reference_price,
            )
            spot_price = (current_value / total) if current_value is not None and total and total > ZERO_EPSILON else _spot_mid_price(
                coin,
                snapshot,
                token_index=token_index,
                reference_price=reference_price,
            )
            if current_value is None and spot_price is not None and total and total > ZERO_EPSILON:
                current_value = total * spot_price
            entry_ntl = _spot_entry_notional(
                coin,
                total,
                raw_balance,
                snapshot,
                current_value,
                reconstructed_spot_basis=reconstructed_spot_basis,
            )
            pnl = (current_value - entry_ntl) if current_value is not None and entry_ntl is not None else None
            pnl_percent = (pnl / entry_ntl * 100) if pnl is not None and entry_ntl and entry_ntl > 0 else None
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
            "Spot P&L is read-only: current value minus entry notional.",
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


def _spot_position_from_hyperliquid(
    raw_balance: dict[str, Any],
    snapshot: HyperliquidSnapshot,
    *,
    reconstructed_spot_basis: dict[str, SpotCostBasis] | None = None,
) -> tuple[Position | None, CashPosition | None]:
    coin = str(raw_balance.get("coin") or raw_balance.get("token") or "").strip().upper()
    quantity = _first_number(raw_balance, ("total", "balance", "amount")) or 0.0
    if not coin or quantity <= ZERO_EPSILON:
        return None, None

    token_index = _spot_token_index_from_balance(raw_balance)
    api_entry_notional = _spot_api_entry_notional(raw_balance, quantity)
    entry_notional = api_entry_notional
    reference_price = _reference_price(entry_notional, quantity)
    current_value = _spot_current_value(
        coin,
        quantity,
        snapshot,
        raw_balance,
        token_index=token_index,
        reference_price=reference_price,
    )
    mid_price = _spot_mid_price(
        coin,
        snapshot,
        token_index=token_index,
        reference_price=reference_price,
    )

    if current_value is None and mid_price is not None:
        current_value = quantity * mid_price

    if coin in CASH_LIKE_COINS:
        return None, CashPosition(coin, round(current_value or quantity, 2), "Hyperliquid")

    entry_notional = _spot_entry_notional(
        coin,
        quantity,
        raw_balance,
        snapshot,
        current_value,
        reconstructed_spot_basis=reconstructed_spot_basis,
    )
    average_cost = (entry_notional / quantity) if entry_notional is not None and quantity > ZERO_EPSILON else None
    last_price = (
        (current_value / quantity) if current_value is not None and quantity > ZERO_EPSILON else None
    ) or mid_price or average_cost or 0.0

    open_profit_loss = (
        current_value - entry_notional
        if current_value is not None and entry_notional is not None
        else None
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
    *,
    token_index: int | None = None,
    reference_price: float | None = None,
) -> float | None:
    """Prefer Hyperliquid's own USD balance value so the cockpit mirrors the UI."""

    direct_usd_value = _first_number(raw_balance, SPOT_CURRENT_VALUE_KEYS)
    if direct_usd_value is not None:
        return direct_usd_value

    mid_price = _spot_mid_price(
        coin,
        snapshot,
        token_index=token_index,
        reference_price=reference_price,
    )
    if quantity is not None and mid_price is not None:
        return quantity * mid_price
    return None


def _spot_entry_notional(
    coin: str,
    quantity: float | None,
    raw_balance: dict[str, Any],
    snapshot: HyperliquidSnapshot,
    current_value: float | None,
    *,
    reconstructed_spot_basis: dict[str, SpotCostBasis] | None = None,
) -> float | None:
    history_basis = _spot_history_entry_notional(
        coin,
        quantity,
        snapshot,
        reconstructed_spot_basis=reconstructed_spot_basis,
    )
    if history_basis is not None:
        return history_basis

    api_entry_notional = _spot_api_entry_notional(raw_balance, quantity)
    if api_entry_notional is not None:
        return api_entry_notional

    if coin not in CASH_LIKE_COINS and current_value is not None:
        return current_value

    return None


def _spot_api_entry_notional(raw_balance: dict[str, Any], quantity: float | None) -> float | None:
    entry_notional = _first_number(raw_balance, SPOT_ENTRY_NOTIONAL_KEYS)
    if entry_notional is not None and entry_notional > ZERO_EPSILON:
        return entry_notional

    average_cost = _first_number(raw_balance, SPOT_AVERAGE_COST_KEYS)
    if average_cost is not None and average_cost > ZERO_EPSILON and quantity is not None and quantity > ZERO_EPSILON:
        return average_cost * quantity

    return None


def _spot_history_entry_notional(
    coin: str,
    quantity: float | None,
    snapshot: HyperliquidSnapshot,
    *,
    reconstructed_spot_basis: dict[str, SpotCostBasis] | None = None,
) -> float | None:
    if quantity is None or quantity <= ZERO_EPSILON:
        return None

    basis_by_coin = reconstructed_spot_basis
    if basis_by_coin is None:
        basis_by_coin = _reconstruct_spot_cost_basis(snapshot)

    basis = basis_by_coin.get(_canonical_spot_coin(coin))
    if basis is None or basis.quantity <= ZERO_EPSILON or basis.cost_basis <= ZERO_EPSILON:
        return None
    if not _spot_quantities_match(basis.quantity, quantity):
        return None
    return basis.cost_basis


def _reconstruct_spot_cost_basis(snapshot: HyperliquidSnapshot) -> dict[str, SpotCostBasis]:
    basis_by_coin: dict[str, SpotCostBasis] = {}
    for fill in sorted(snapshot.user_fills, key=_fill_sort_key):
        parsed = _spot_fill(fill, snapshot)
        if parsed is None:
            continue

        coin, is_buy, quantity, notional, fee, fee_token = parsed
        basis = basis_by_coin.setdefault(coin, SpotCostBasis())
        fee_token = _canonical_spot_coin(fee_token)
        usdc_fee = fee if fee_token in CASH_LIKE_COINS else 0.0

        if is_buy:
            basis.quantity += quantity
            basis.cost_basis += notional + usdc_fee
            continue

        if basis.quantity <= ZERO_EPSILON:
            continue
        sold_quantity = min(quantity, basis.quantity)
        basis.cost_basis -= basis.cost_basis * (sold_quantity / basis.quantity)
        basis.quantity -= sold_quantity
        if basis.quantity <= ZERO_EPSILON:
            basis.quantity = 0.0
            basis.cost_basis = 0.0

    return basis_by_coin


def _spot_fill(fill: dict[str, Any], snapshot: HyperliquidSnapshot) -> tuple[str, bool, float, float, float, str] | None:
    coin = _spot_fill_coin(fill, snapshot)
    if coin is None or coin in CASH_LIKE_COINS:
        return None

    side = str(fill.get("side") or "").strip().upper()
    is_buy = side in {"B", "BUY"}
    is_sell = side in {"A", "S", "SELL", "ASK"}
    if not is_buy and not is_sell:
        return None

    quantity = _first_number(fill, ("sz", "size", "quantity", "qty"))
    price = _first_number(fill, ("px", "price"))
    if quantity is None or price is None or quantity <= ZERO_EPSILON or price <= ZERO_EPSILON:
        return None

    notional = _first_number(fill, ("notional", "ntl", "usdc", "usdValue"))
    if notional is None or notional <= ZERO_EPSILON:
        notional = quantity * price

    fee = _first_number(fill, ("fee", "feeUsd", "feeUSDC")) or 0.0
    fee_token = str(fill.get("feeToken") or fill.get("feeCoin") or "USDC")
    return coin, is_buy, quantity, notional, fee, fee_token


def _spot_fill_coin(fill: dict[str, Any], snapshot: HyperliquidSnapshot) -> str | None:
    raw_coin = str(fill.get("coin") or fill.get("symbol") or fill.get("market") or "").strip().upper()
    if not raw_coin:
        return None
    if raw_coin.startswith("@"):
        return _spot_market_base_coin(raw_coin, snapshot)

    separator = "/" if "/" in raw_coin else "-" if "-" in raw_coin else ""
    if not separator:
        return None

    base, quote = raw_coin.split(separator, 1)
    if quote != "USDC":
        return None
    return _canonical_spot_coin(base)


def _spot_market_base_coin(market_key: str, snapshot: HyperliquidSnapshot) -> str | None:
    market_index = _to_int(market_key.lstrip("@"))
    payload = snapshot.spot_meta_and_asset_ctxs
    if market_index is None or not isinstance(payload, list) or not payload:
        return None

    meta = payload[0]
    universe = meta.get("universe") if isinstance(meta, dict) else None
    tokens = meta.get("tokens") if isinstance(meta, dict) else None
    if not isinstance(universe, list):
        return None

    token_names_by_index = _spot_token_names_by_index(tokens)
    for fallback_index, asset in enumerate(universe):
        if not isinstance(asset, dict):
            continue
        asset_index = _to_int(asset.get("index"))
        if asset_index not in {market_index, None} and fallback_index != market_index:
            continue
        if asset_index is None and fallback_index != market_index:
            continue
        base_index = _spot_market_base_token_index(asset)
        base_names = token_names_by_index.get(base_index, set()) if base_index is not None else set()
        if base_names:
            return _canonical_spot_coin(sorted(base_names)[0])
        market_names = _spot_market_names(asset, token_names_by_index)
        for name in sorted(market_names):
            if "/" in name or "-" in name:
                return _canonical_spot_coin(name.replace("-", "/").split("/", 1)[0])
    return None


def _canonical_spot_coin(coin: str) -> str:
    normalized = coin.strip().upper()
    for canonical, aliases in SPOT_COIN_ALIASES.items():
        if normalized in aliases:
            return canonical if not canonical.startswith("U") else aliases[-1]
    return normalized


def _spot_quantities_match(reconstructed_quantity: float, current_quantity: float) -> bool:
    tolerance = max(0.000001, abs(current_quantity) * 0.0001)
    return abs(reconstructed_quantity - current_quantity) <= tolerance


def _fill_sort_key(fill: dict[str, Any]) -> tuple[float, float]:
    return (
        _to_float(fill.get("time") or fill.get("timestamp")) or 0.0,
        _to_float(fill.get("tid") or fill.get("oid")) or 0.0,
    )


def _spot_token_index_from_balance(raw_balance: dict[str, Any]) -> int | None:
    for key in ("token", "tokenIndex", "token_index", "asset", "assetIndex", "index"):
        value = raw_balance.get(key)
        if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
            continue
        token_index = _to_int(value)
        if token_index is not None:
            return token_index
    return None


def _spot_mid_price(
    coin: str,
    snapshot: HyperliquidSnapshot,
    *,
    token_index: int | None = None,
    reference_price: float | None = None,
) -> float | None:
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
        value = _all_mids_value(snapshot.all_mids, key, reference_price=reference_price)
        if value is not None:
            return value

    meta_price = _spot_meta_price(
        coin,
        snapshot.spot_meta_and_asset_ctxs,
        snapshot.all_mids,
        token_index=token_index,
        reference_price=reference_price,
    )
    if meta_price is not None:
        return meta_price

    for key, raw_value in snapshot.all_mids.items():
        upper_key = str(key).upper()
        if _matches_spot_alias(upper_key, aliases):
            value = _candidate_price(_to_float(raw_value), reference_price)
            if value is not None:
                return value
    return None


def _spot_meta_price(
    coin: str,
    payload: Any,
    all_mids: dict[str, Any] | None = None,
    *,
    token_index: int | None = None,
    reference_price: float | None = None,
) -> float | None:
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
    matches: list[tuple[int, int, dict[str, Any], set[str]]] = []

    for market_index, asset in enumerate(universe):
        if not isinstance(asset, dict):
            continue

        market_names = _spot_market_names(asset, token_names_by_index)
        base_token_index = _spot_market_base_token_index(asset)
        exact_token_match = token_index is not None and base_token_index == token_index
        name_match = any(_matches_spot_alias(name, aliases) for name in market_names)
        if not exact_token_match and not name_match:
            continue
        matches.append((0 if exact_token_match else 1, market_index, asset, market_names))

    for _priority, market_index, asset, market_names in sorted(matches, key=lambda item: item[0]):
        for key in _spot_market_mid_keys(asset, market_index, market_names):
            price = _all_mids_value(mids, key, reference_price=reference_price)
            if price is not None:
                return price

        for ctx_index in _spot_asset_context_indexes(asset, market_index, len(asset_ctxs)):
            ctx = asset_ctxs[ctx_index] if isinstance(asset_ctxs[ctx_index], dict) else {}
            price = _candidate_price(
                _first_number(ctx, ("midPx", "markPx", "oraclePx", "price", "prevDayPx")),
                reference_price,
            )
            if price is not None:
                return price
    return None


def _spot_market_mid_keys(asset: dict[str, Any], market_index: int, market_names: set[str]) -> list[str]:
    keys: list[str] = []
    for key in sorted(market_names):
        _append_unique(keys, key)

    asset_index = _to_int(asset.get("index"))
    if asset_index is not None:
        _append_unique(keys, f"@{asset_index}")
        _append_unique(keys, f"@{10000 + asset_index}")
    _append_unique(keys, f"@{market_index}")
    _append_unique(keys, f"@{10000 + market_index}")
    return keys


def _spot_asset_context_indexes(asset: dict[str, Any], market_index: int, asset_ctxs_length: int) -> list[int]:
    indexes: list[int] = []
    asset_index = _to_int(asset.get("index"))
    if asset_index is not None and 0 <= asset_index < asset_ctxs_length:
        indexes.append(asset_index)
    if 0 <= market_index < asset_ctxs_length and market_index not in indexes:
        indexes.append(market_index)
    return indexes


def _spot_market_base_token_index(asset: dict[str, Any]) -> int | None:
    token_indices = asset.get("tokens")
    if isinstance(token_indices, list) and token_indices:
        return _to_int(token_indices[0])
    return _to_int(asset.get("baseTokenIndex") or asset.get("baseToken"))


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


def _all_mids_value(all_mids: dict[str, Any], key: str, *, reference_price: float | None = None) -> float | None:
    value = _candidate_price(_to_float(all_mids.get(key)), reference_price)
    if value is not None:
        return value

    upper_key = key.upper()
    for raw_key, raw_value in all_mids.items():
        if str(raw_key).upper() == upper_key:
            value = _candidate_price(_to_float(raw_value), reference_price)
            if value is not None:
                return value
    return None


def _candidate_price(value: float | None, reference_price: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    if _spot_price_is_plausible(value, reference_price):
        return value
    return None


def _spot_price_is_plausible(price: float, reference_price: float | None) -> bool:
    """Reject obviously wrong metadata matches, such as ZEC resolving to $0.06.

    If Hyperliquid provides entry notional, the live spot price should not be
    thousands of times away from the position's average cost. This still allows
    very large moves while preventing a mismatched @index/asset context from
    turning a normal spot balance into a near-total loss in the cockpit UI.
    """

    if reference_price is None or reference_price <= 0:
        return True
    lower_bound = reference_price / SPOT_PRICE_SANITY_MULTIPLE
    upper_bound = reference_price * SPOT_PRICE_SANITY_MULTIPLE
    return lower_bound <= price <= upper_bound


def _reference_price(entry_notional: float | None, quantity: float | None) -> float | None:
    if entry_notional is None or quantity is None or quantity <= ZERO_EPSILON:
        return None
    return entry_notional / quantity


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


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
