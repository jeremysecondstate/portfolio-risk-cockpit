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
CUSTOM_PNL_COINS = {"BTC", "HYPE"}
HISTORY_PARTIAL_THRESHOLD = 2000
HISTORY_PAGE_LIMIT = 2000
HISTORY_MAX_ROWS = 10000
HISTORICAL_PRICE_INTERVAL = "1h"
HISTORICAL_PRICE_WINDOW_MS = 6 * 60 * 60 * 1000


@dataclass(frozen=True)
class SpotEntryNotional:
    value: float | None
    known: bool


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
    user_funding: list[dict[str, Any]] = field(default_factory=list)
    historical_prices: dict[tuple[str, int], float] = field(default_factory=dict)
    history_warnings: list[str] = field(default_factory=list)


@dataclass
class SpotCostBasis:
    quantity: float = 0.0
    cost_basis: float = 0.0


@dataclass
class CustomSpotLot:
    coin: str
    quantity: float
    remaining_quantity: float
    unit_cost_usd: float
    total_cost_usd: float
    timestamp: int
    source: str
    basis_status: str


@dataclass
class CustomCoinPnl:
    coin: str
    current_quantity: float = 0.0
    current_value: float = 0.0
    raw_pnl: float | None = None
    realized_pnl: float | None = 0.0
    unrealized_pnl: float | None = 0.0
    total_pnl: float | None = 0.0
    deposit_basis_usd: float = 0.0
    acquisition_basis_usd: float = 0.0
    remaining_cost_basis_usd: float = 0.0
    acquired_quantity: float = 0.0
    sold_quantity: float = 0.0
    withdrawn_quantity: float = 0.0
    sale_proceeds_usd: float = 0.0
    buy_cost_usd: float = 0.0
    withdrawal_value_usd: float = 0.0
    fees_usd: float = 0.0
    missing_basis_quantity: float = 0.0
    missing_disposition_quantity: float = 0.0
    lots: list[CustomSpotLot] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    basis_status: str = "Incomplete"
    history_status: str = "Partial history loaded"


@dataclass
class CustomHyperliquidPnl:
    coins: dict[str, CustomCoinPnl] = field(default_factory=dict)
    current_account_value: float = 0.0
    deposits_usd: float = 0.0
    withdrawals_usd: float = 0.0
    net_custom_pnl: float | None = None
    spot_realized_pnl: float | None = 0.0
    spot_unrealized_pnl: float | None = 0.0
    perp_realized_pnl: float = 0.0
    perp_unrealized_pnl: float = 0.0
    funding_usd: float = 0.0
    fees_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)
    history_status: str = "Partial history loaded"


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
        open_orders = (
            self._safe_post_info({"type": "frontendOpenOrders", "user": normalized_user}, default=None)
            if include_open_orders
            else []
        )
        if open_orders is None and include_open_orders:
            open_orders = self.post_info({"type": "openOrders", "user": normalized_user})
        all_mids = self._safe_post_info({"type": "allMids"}, default={})
        spot_meta_and_asset_ctxs = self._safe_post_info({"type": "spotMetaAndAssetCtxs"}, default=None)
        history_warnings: list[str] = []
        recent_user_fills = self._safe_post_info(
            {"type": "userFills", "user": normalized_user, "aggregateByTime": False},
            default=[],
        )
        timed_user_fills, fill_warnings = self._fetch_time_window_history(
            {
                "type": "userFillsByTime",
                "user": normalized_user,
                "aggregateByTime": False,
            },
            label="fill history",
            max_rows=HISTORY_MAX_ROWS,
        )
        history_warnings.extend(fill_warnings)
        user_fills = _dedupe_dict_rows(
            (timed_user_fills if isinstance(timed_user_fills, list) else [])
            + (recent_user_fills if isinstance(recent_user_fills, list) else []),
            key_fields=("hash", "tid", "oid", "time", "coin", "side", "sz", "px"),
        )
        user_non_funding_ledger_updates, ledger_warnings = self._fetch_time_window_history(
            {"type": "userNonFundingLedgerUpdates", "user": normalized_user},
            label="deposit/withdrawal ledger history",
            max_rows=HISTORY_MAX_ROWS,
        )
        history_warnings.extend(ledger_warnings)
        user_funding, funding_warnings = self._fetch_time_window_history(
            {"type": "userFunding", "user": normalized_user},
            label="funding history",
            max_rows=HISTORY_MAX_ROWS,
        )
        history_warnings.extend(funding_warnings)

        if not isinstance(clearinghouse_state, dict):
            raise RuntimeError("Unexpected Hyperliquid clearinghouseState response shape.")
        if not isinstance(spot_state, dict):
            spot_state = {}
        if not isinstance(open_orders, list):
            open_orders = []
        if not isinstance(all_mids, dict):
            all_mids = {}
        if not isinstance(user_non_funding_ledger_updates, list):
            user_non_funding_ledger_updates = []
        if not isinstance(user_funding, list):
            user_funding = []

        if len(user_fills) >= HISTORY_PARTIAL_THRESHOLD:
            history_warnings.append("Custom P&L is based on partial loaded fill history.")
        if len(user_non_funding_ledger_updates) >= HISTORY_PARTIAL_THRESHOLD:
            history_warnings.append("Custom P&L is based on partial loaded ledger history.")
        historical_prices = self._fetch_historical_prices_for_custom_pnl(
            user_non_funding_ledger_updates,
            all_mids if isinstance(all_mids, dict) else {},
            normalized_user,
        )

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
            user_funding=[funding for funding in user_funding if isinstance(funding, dict)],
            historical_prices=historical_prices,
            history_warnings=history_warnings,
        )

    def fetch_validator_health_snapshot(self) -> "HyperliquidValidatorHealthSnapshot":
        """Fetch global read-only validator/chain health data when supported."""

        from app.analytics.hyperliquid_chain_health import (
            HyperliquidValidatorHealthSnapshot,
            normalize_validator_summaries_payload,
        )

        missing = object()
        errors: list[str] = []
        warnings: list[str] = []

        validator_summaries_raw = self._safe_post_info({"type": "validatorSummaries"}, default=missing)
        if validator_summaries_raw is missing:
            validator_summaries_raw = None
            errors.append("validatorSummaries unavailable; score excludes core validator-set metrics.")
        validator_summaries = normalize_validator_summaries_payload(validator_summaries_raw)
        if validator_summaries_raw is not None and not validator_summaries:
            warnings.append("validatorSummaries returned an unexpected shape; no validator objects were normalized.")

        validator_stats = self._safe_post_info({"type": "validatorStats"}, default=missing)
        if validator_stats is missing:
            validator_stats = None
            warnings.append("validatorStats unavailable; score excludes per-validator performance metrics.")

        validator_l1_votes = self._safe_post_info({"type": "validatorL1Votes"}, default=missing)
        if validator_l1_votes is missing:
            validator_l1_votes = None
            warnings.append("validatorL1Votes unavailable; score excludes L1 vote participation metrics.")

        exchange_status = self._safe_post_info({"type": "exchangeStatus"}, default=missing)
        if exchange_status is missing:
            exchange_status = None
            warnings.append("exchangeStatus unavailable or unsupported; exchange-level sanity signal is missing.")

        all_mids = self._safe_post_info({"type": "allMids"}, default=missing)
        if all_mids is missing:
            all_mids_ok: bool | None = False
            warnings.append("allMids sanity check failed or endpoint was unavailable.")
        else:
            all_mids_ok = isinstance(all_mids, dict) and bool(all_mids)
            if not all_mids_ok:
                warnings.append("allMids sanity check returned an unexpected or empty shape.")

        return HyperliquidValidatorHealthSnapshot(
            fetched_at=datetime.now(),
            validator_summaries=validator_summaries,
            validator_stats=validator_stats,
            validator_l1_votes=validator_l1_votes,
            exchange_status=exchange_status,
            all_mids_ok=all_mids_ok,
            errors=errors,
            warnings=warnings,
            raw_validator_summaries=validator_summaries_raw,
        )

    def _safe_post_info(self, payload: dict[str, Any], default: Any) -> Any:
        """Optional read-only enrichment call; sync should still work without it."""
        try:
            return self.post_info(payload)
        except Exception:
            return default

    def _fetch_time_window_history(
        self,
        base_payload: dict[str, Any],
        *,
        label: str,
        max_rows: int,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        cursor = _history_start_time()
        end_time = int(datetime.now().timestamp() * 1000)

        while len(rows) < max_rows:
            payload = {
                **base_payload,
                "startTime": cursor,
                "endTime": end_time,
            }
            page = self._safe_post_info(payload, default=None)
            if page is None:
                warnings.append(f"Custom P&L history unavailable: Hyperliquid {label} could not be loaded.")
                break
            if not isinstance(page, list):
                warnings.append(f"Custom P&L history unavailable: Hyperliquid {label} returned an unexpected shape.")
                break

            page_rows = [row for row in page if isinstance(row, dict)]
            if not page_rows:
                break

            rows.extend(page_rows)
            page_times = [
                row_time
                for row in page_rows
                for row_time in (_row_time(row),)
                if row_time is not None and row_time >= cursor
            ]
            if len(page_rows) < HISTORY_PAGE_LIMIT or not page_times:
                break

            next_cursor = max(page_times) + 1
            if next_cursor <= cursor:
                warnings.append(f"Custom P&L is based on partial loaded {label}; pagination stopped at {cursor}.")
                break
            cursor = next_cursor

        if len(rows) >= max_rows:
            warnings.append(f"Custom P&L is based on partial loaded {label}; reached the {max_rows:,}-row API cap.")

        return rows[:max_rows], warnings

    def _fetch_historical_prices_for_custom_pnl(
        self,
        ledger_updates: list[dict[str, Any]],
        all_mids: dict[str, Any],
        user: str,
    ) -> dict[tuple[str, int], float]:
        prices: dict[tuple[str, int], float] = {}
        needed: set[tuple[str, int]] = set()
        for update in ledger_updates:
            if not isinstance(update, dict):
                continue
            for movement in _ledger_spot_movements(update, user):
                if movement["coin"] in CASH_LIKE_COINS or movement["quantity"] == 0:
                    continue
                if movement.get("usd_value") is not None:
                    continue
                needed.add((movement["coin"], int(movement["time"])))

        for coin, timestamp in sorted(needed):
            price = self._fetch_historical_price(coin, timestamp)
            if price is None:
                price = _to_float(all_mids.get(coin))
            if price is not None and price > 0:
                prices[(coin, timestamp)] = price
        return prices

    def _fetch_historical_price(self, coin: str, timestamp_ms: int) -> float | None:
        start_time = max(0, timestamp_ms - HISTORICAL_PRICE_WINDOW_MS)
        end_time = timestamp_ms + HISTORICAL_PRICE_WINDOW_MS
        candles = self._safe_post_info(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": coin,
                    "interval": HISTORICAL_PRICE_INTERVAL,
                    "startTime": start_time,
                    "endTime": end_time,
                },
            },
            default=[],
        )
        if not isinstance(candles, list) or not candles:
            return None
        best_candle: dict[str, Any] | None = None
        best_distance: float | None = None
        for candle in candles:
            if not isinstance(candle, dict):
                continue
            candle_time = _to_float(candle.get("t") or candle.get("T"))
            close = _to_float(candle.get("c"))
            if candle_time is None or close is None or close <= 0:
                continue
            distance = abs(candle_time - timestamp_ms)
            if best_distance is None or distance < best_distance:
                best_candle = candle
                best_distance = distance
        return _to_float(best_candle.get("c")) if best_candle else None


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
    custom_pnl = build_custom_hyperliquid_pnl(snapshot)

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
            custom_coin_pnl=custom_pnl.coins.get(_canonical_spot_coin(str(raw_balance.get("coin") or raw_balance.get("token") or ""))),
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
    custom_pnl = build_custom_hyperliquid_pnl(snapshot)

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
            entry = _spot_entry_notional_status(
                coin,
                total,
                raw_balance,
                snapshot,
                current_value,
                reconstructed_spot_basis=reconstructed_spot_basis,
            )
            pnl = (current_value - entry.value) if current_value is not None and entry.value is not None and entry.known else None
            pnl_percent = (pnl / entry.value * 100) if pnl is not None and entry.value and entry.value > 0 else None
            lines.append(
                f"- {coin}: total {_format_optional_number(total)}, "
                f"held {_format_optional_number(hold)}, "
                f"price {_format_optional_money(spot_price)}, "
                f"value {_format_optional_money(current_value)}, "
                f"entry {_format_optional_money(entry_ntl)}, "
                f"P&L {_format_optional_money(pnl)} ({_format_optional_percent(pnl_percent)})"
            )

    lines.extend(
        [
            "",
            "CUSTOM HYPERLIQUID P&L",
            "",
            "Account:",
            f"- Current total value: {_format_money(custom_pnl.current_account_value)}",
            f"- Total deposits: {_format_money(custom_pnl.deposits_usd)}",
            f"- Total withdrawals: {_format_money(custom_pnl.withdrawals_usd)}",
            f"- Custom account P&L: {_format_custom_money(custom_pnl.net_custom_pnl, custom_pnl.history_status)}",
            "",
        ]
    )
    for coin in ("BTC", "HYPE"):
        coin_pnl = custom_pnl.coins.get(coin)
        if coin_pnl is None:
            continue
        acquisition_label = "BTC deposit basis" if coin == "BTC" else "HYPE deposit/acquisition basis"
        lines.extend(
            [
                "",
                f"{coin}:",
                f"- Current {coin} value: {_format_money(coin_pnl.current_value)}",
                f"- {coin} buy cost: {_format_money(coin_pnl.buy_cost_usd)}",
                f"- {coin} sale proceeds: {_format_money(coin_pnl.sale_proceeds_usd)}",
                f"- {acquisition_label}: {_format_money(coin_pnl.acquisition_basis_usd)}",
                f"- {coin} withdrawal value: {_format_money(coin_pnl.withdrawal_value_usd)}",
                f"- {coin} fees: {_format_money(coin_pnl.fees_usd)}",
                f"- {coin} custom P&L: {_format_custom_money(coin_pnl.total_pnl, coin_pnl.basis_status)}",
                f"- Status: {coin_pnl.basis_status}",
            ]
        )
    lines.extend(
        [
            "",
            "Perps:",
            f"- Closed P&L: {_format_money(custom_pnl.perp_realized_pnl)}",
            f"- Unrealized P&L: {_format_money(custom_pnl.perp_unrealized_pnl)}",
            f"- Funding: {_format_money(custom_pnl.funding_usd)}",
            f"- Fees: {_format_money(custom_pnl.fees_usd)}",
            f"- Perp custom P&L: {_format_money(custom_pnl.perp_realized_pnl + custom_pnl.perp_unrealized_pnl + custom_pnl.funding_usd - custom_pnl.fees_usd)}",
        ]
    )
    if custom_pnl.warnings:
        lines.append("")
        lines.append("Custom P&L warnings:")
        for warning in custom_pnl.warnings[:8]:
            lines.append(f"- {warning}")
        if len(custom_pnl.warnings) > 8:
            lines.append(f"- ... {len(custom_pnl.warnings) - 8} more")

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
            "Spot P&L is read-only and shown only when Hyperliquid provides cost basis or matching fills let the cockpit reconstruct it.",
            "Spot prices resolve through Hyperliquid spot metadata when allMids only exposes @-indexed spot markets.",
            "API/agent wallets are only needed for future signed actions.",
        ]
    )
    return "\n".join(lines)


def build_custom_hyperliquid_pnl(snapshot: HyperliquidSnapshot) -> CustomHyperliquidPnl:
    """Build an auditable custom P&L view without overwriting raw exchange P&L."""

    current_spot = _custom_current_spot_values(snapshot)
    coins = {
        coin: _build_custom_coin_pnl(snapshot, coin, current_spot.get(coin, {}))
        for coin in sorted(CUSTOM_PNL_COINS)
    }
    deposits_usd, withdrawals_usd, contribution_warnings = _custom_account_contributions(snapshot)
    current_account_value = _custom_current_account_value(snapshot, current_spot)
    missing_capital_basis = any("Missing historical price" in warning for warning in contribution_warnings)
    capital_history_unavailable = current_account_value > ZERO_EPSILON and not snapshot.user_non_funding_ledger_updates
    net_custom_pnl = (
        None
        if missing_capital_basis or capital_history_unavailable
        else current_account_value + withdrawals_usd - deposits_usd
    )

    spot_realized_values = [coin.realized_pnl for coin in coins.values()]
    spot_unrealized_values = [coin.unrealized_pnl for coin in coins.values()]
    spot_realized = None if any(value is None for value in spot_realized_values) else sum(spot_realized_values)
    spot_unrealized = None if any(value is None for value in spot_unrealized_values) else sum(spot_unrealized_values)

    warnings = list(snapshot.history_warnings)
    warnings.extend(contribution_warnings)
    for coin in coins.values():
        warnings.extend(coin.warnings)
    if not snapshot.user_non_funding_ledger_updates:
        warnings.append("Custom P&L is based on partial loaded history; no deposit/withdrawal ledger updates were loaded.")
    if not snapshot.user_fills:
        warnings.append("Custom P&L is based on partial loaded history; no fill history was loaded.")

    history_status = "Exact from full history"
    if capital_history_unavailable or any(coin.basis_status.startswith("History unavailable") for coin in coins.values()):
        history_status = "History unavailable"
    elif any(coin.basis_status.startswith("Missing") for coin in coins.values()):
        history_status = "Missing historical price"
    elif any(coin.basis_status.startswith("Partial") for coin in coins.values()):
        history_status = "Partial history"
    elif any(coin.basis_status.startswith("Estimated") for coin in coins.values()):
        history_status = "Estimated deposit basis"
    if warnings and history_status == "Exact from full history":
        history_status = "Partial history loaded"

    return CustomHyperliquidPnl(
        coins=coins,
        current_account_value=round(current_account_value, 2),
        deposits_usd=round(deposits_usd, 2),
        withdrawals_usd=round(withdrawals_usd, 2),
        net_custom_pnl=round(net_custom_pnl, 2) if net_custom_pnl is not None else None,
        spot_realized_pnl=round(spot_realized, 2) if spot_realized is not None else None,
        spot_unrealized_pnl=round(spot_unrealized, 2) if spot_unrealized is not None else None,
        perp_realized_pnl=round(_custom_perp_realized_pnl(snapshot), 2),
        perp_unrealized_pnl=round(_custom_perp_unrealized_pnl(snapshot), 2),
        funding_usd=round(_custom_funding_total(snapshot), 2),
        fees_usd=round(_custom_fee_total(snapshot), 2),
        warnings=_dedupe_strings(warnings),
        history_status=history_status,
    )


def _build_custom_coin_pnl(
    snapshot: HyperliquidSnapshot,
    coin: str,
    current: dict[str, Any],
) -> CustomCoinPnl:
    current_quantity = float(current.get("quantity") or 0.0)
    current_value = float(current.get("value") or 0.0)
    current_price = (current_value / current_quantity) if current_quantity > ZERO_EPSILON else _to_float(current.get("price"))
    raw_pnl = current.get("raw_pnl") if isinstance(current.get("raw_pnl"), (int, float)) else None

    lots: list[CustomSpotLot] = []
    realized_pnl = 0.0
    acquired_quantity = 0.0
    sold_quantity = 0.0
    withdrawn_quantity = 0.0
    deposit_basis = 0.0
    acquisition_basis = 0.0
    sale_proceeds = 0.0
    buy_cost = 0.0
    withdrawal_value = 0.0
    fees_usd = 0.0
    missing_basis_quantity = 0.0
    missing_disposition_quantity = 0.0
    incomplete = False
    estimated = False
    warnings: list[str] = []

    for event in _custom_coin_events(snapshot, coin):
        quantity = float(event["quantity"])
        timestamp = int(event.get("time") or 0)
        source = str(event.get("source") or "unknown")
        fee_usd = float(event.get("fee_usd") or 0.0)
        fees_usd += fee_usd

        if event["kind"] in {"buy", "acquisition"} and quantity > ZERO_EPSILON:
            basis_value, basis_status, basis_warning = _custom_event_basis_value(
                snapshot,
                coin,
                timestamp,
                quantity,
                event.get("usd_value"),
                current_price,
                source,
            )
            acquired_quantity += quantity
            if basis_warning:
                warnings.append(basis_warning)
            if basis_value is None:
                missing_basis_quantity += quantity
                incomplete = True
                continue
            if basis_status == "estimated":
                estimated = True
            if event["kind"] == "buy":
                buy_cost += basis_value
            else:
                acquisition_basis += basis_value
                if source in {"deposit", "spotTransfer"}:
                    deposit_basis += basis_value
            lot = CustomSpotLot(
                coin=coin,
                quantity=quantity,
                remaining_quantity=quantity,
                unit_cost_usd=basis_value / quantity,
                total_cost_usd=basis_value,
                timestamp=timestamp,
                source=source,
                basis_status=basis_status,
            )
            lots.append(lot)
            continue

        if event["kind"] == "sell" and quantity > ZERO_EPSILON:
            sold_quantity += quantity
            proceeds = float(event.get("usd_value") or 0.0)
            sale_proceeds += proceeds
            consumed_cost, missing = _consume_custom_lots(lots, quantity)
            if missing > ZERO_EPSILON:
                missing_basis_quantity += missing
                incomplete = True
                warnings.append(f"{coin} custom P&L incomplete: missing acquisition basis for {missing:g} {coin}.")
            realized_pnl += proceeds - consumed_cost - fee_usd
            continue

        if event["kind"] == "disposition" and quantity > ZERO_EPSILON:
            withdrawn_quantity += quantity
            disposition_value, value_status, value_warning = _custom_event_basis_value(
                snapshot,
                coin,
                timestamp,
                quantity,
                event.get("usd_value"),
                current_price,
                source,
            )
            if value_warning:
                warnings.append(value_warning)
            if value_status == "estimated":
                estimated = True
            consumed_cost, missing = _consume_custom_lots(lots, quantity)
            if missing > ZERO_EPSILON:
                missing_basis_quantity += missing
                incomplete = True
                warnings.append(f"{coin} custom P&L incomplete: missing acquisition basis for {missing:g} {coin}.")
            if disposition_value is None:
                incomplete = True
                warnings.append(f"{coin} custom P&L incomplete: missing withdrawal/transfer value for {quantity:g} {coin}.")
            else:
                withdrawal_value += disposition_value
                realized_pnl += disposition_value - consumed_cost - fee_usd

    loaded_remaining_quantity = sum(lot.remaining_quantity for lot in lots)
    if loaded_remaining_quantity > current_quantity + _quantity_tolerance(current_quantity):
        missing_disposition_quantity = loaded_remaining_quantity - current_quantity
        _consume_custom_lots(lots, missing_disposition_quantity)
        incomplete = True
        warnings.append(f"{coin} custom P&L incomplete: missing disposition history for {missing_disposition_quantity:g} {coin}.")
    elif current_quantity > loaded_remaining_quantity + _quantity_tolerance(current_quantity):
        missing = current_quantity - loaded_remaining_quantity
        missing_basis_quantity += missing
        incomplete = True
        warnings.append(f"{coin} custom P&L incomplete: missing acquisition basis for {missing:g} {coin}.")

    remaining_cost_basis = sum(lot.remaining_quantity * lot.unit_cost_usd for lot in lots)
    known_quantity = sum(lot.remaining_quantity for lot in lots)
    known_current_value = (current_price or 0.0) * known_quantity
    if current_quantity > ZERO_EPSILON and known_quantity <= ZERO_EPSILON and missing_basis_quantity > ZERO_EPSILON:
        unrealized_pnl: float | None = None
    else:
        unrealized_pnl = known_current_value - remaining_cost_basis

    practical_total_pnl = current_value + sale_proceeds + withdrawal_value - buy_cost - acquisition_basis - fees_usd
    known_basis = buy_cost + acquisition_basis
    can_show_partial = known_basis > ZERO_EPSILON and current_value is not None
    if not incomplete and unrealized_pnl is not None:
        total_pnl = realized_pnl + unrealized_pnl
    elif can_show_partial:
        total_pnl = practical_total_pnl
    else:
        total_pnl = None
    basis_status = _custom_basis_status(
        coin,
        incomplete=incomplete,
        estimated=estimated,
        missing_basis_quantity=missing_basis_quantity,
        missing_disposition_quantity=missing_disposition_quantity,
        known_basis=known_basis,
        has_history=bool(snapshot.user_fills or snapshot.user_non_funding_ledger_updates),
    )
    history_status = "Partial history loaded" if snapshot.history_warnings else basis_status

    return CustomCoinPnl(
        coin=coin,
        current_quantity=round(current_quantity, 10),
        current_value=round(current_value, 2),
        raw_pnl=round(raw_pnl, 2) if raw_pnl is not None else None,
        realized_pnl=round(realized_pnl, 2),
        unrealized_pnl=round(unrealized_pnl, 2) if unrealized_pnl is not None else None,
        total_pnl=round(total_pnl, 2) if total_pnl is not None else None,
        deposit_basis_usd=round(deposit_basis, 2),
        acquisition_basis_usd=round(acquisition_basis, 2),
        remaining_cost_basis_usd=round(remaining_cost_basis, 2),
        acquired_quantity=round(acquired_quantity, 10),
        sold_quantity=round(sold_quantity, 10),
        withdrawn_quantity=round(withdrawn_quantity, 10),
        sale_proceeds_usd=round(sale_proceeds, 2),
        buy_cost_usd=round(buy_cost, 2),
        withdrawal_value_usd=round(withdrawal_value, 2),
        fees_usd=round(fees_usd, 2),
        missing_basis_quantity=round(missing_basis_quantity, 10),
        missing_disposition_quantity=round(missing_disposition_quantity, 10),
        lots=lots,
        warnings=_dedupe_strings(warnings),
        basis_status=basis_status,
        history_status=history_status,
    )


def _custom_current_spot_values(snapshot: HyperliquidSnapshot) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    reconstructed_spot_basis = _reconstruct_spot_cost_basis(snapshot)
    for raw_balance in _spot_balances(snapshot.spot_state):
        coin = _canonical_spot_coin(str(raw_balance.get("coin") or raw_balance.get("token") or "").strip().upper())
        quantity = _first_number(raw_balance, ("total", "balance", "amount")) or 0.0
        if not coin or quantity <= ZERO_EPSILON:
            continue
        token_index = _spot_token_index_from_balance(raw_balance)
        api_entry = _spot_api_entry_notional(raw_balance, quantity)
        reference_price = _reference_price(api_entry, quantity)
        current_value = _spot_current_value(
            coin,
            quantity,
            snapshot,
            raw_balance,
            token_index=token_index,
            reference_price=reference_price,
        )
        price = (
            current_value / quantity
            if current_value is not None and quantity > ZERO_EPSILON
            else _spot_mid_price(coin, snapshot, token_index=token_index, reference_price=reference_price)
        )
        if current_value is None and price is not None:
            current_value = quantity * price
        entry = _spot_entry_notional_status(
            coin,
            quantity,
            raw_balance,
            snapshot,
            current_value,
            reconstructed_spot_basis=reconstructed_spot_basis,
        )
        values[coin] = {
            "quantity": quantity,
            "value": current_value or 0.0,
            "price": price,
            "raw_balance": raw_balance,
            "raw_pnl": _spot_raw_pnl(raw_balance, current_value, entry),
        }
    return values


def _custom_coin_events(snapshot: HyperliquidSnapshot, coin: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for update in snapshot.user_non_funding_ledger_updates:
        for movement in _ledger_spot_movements(update, snapshot.user):
            if movement["coin"] != coin:
                continue
            quantity = float(movement["quantity"])
            if quantity > ZERO_EPSILON:
                events.append({**movement, "kind": "acquisition", "quantity": quantity})
            elif quantity < -ZERO_EPSILON:
                events.append({**movement, "kind": "disposition", "quantity": abs(quantity)})

    for fill in snapshot.user_fills:
        parsed = _spot_fill(fill, snapshot)
        if parsed is None:
            continue
        fill_coin, is_buy, quantity, notional, fee, fee_token = parsed
        if fill_coin != coin:
            continue
        fee_usd = fee if _canonical_spot_coin(fee_token) in CASH_LIKE_COINS else 0.0
        events.append(
            {
                "kind": "buy" if is_buy else "sell",
                "coin": coin,
                "quantity": quantity,
                "usd_value": notional,
                "fee_usd": fee_usd,
                "time": int(_to_float(fill.get("time") or fill.get("timestamp")) or 0),
                "source": "buy" if is_buy else "sell",
            }
        )
    return sorted(events, key=lambda event: (int(event.get("time") or 0), _custom_event_priority(str(event.get("kind")))))


def _custom_event_priority(kind: str) -> int:
    return {"acquisition": 0, "buy": 1, "sell": 2, "disposition": 3}.get(kind, 9)


def _custom_event_basis_value(
    snapshot: HyperliquidSnapshot,
    coin: str,
    timestamp: int,
    quantity: float,
    usd_value: Any,
    current_price: float | None,
    source: str,
) -> tuple[float | None, str, str | None]:
    direct_value = _to_float(usd_value)
    if direct_value is not None and abs(direct_value) > ZERO_EPSILON:
        return abs(direct_value), "exact", None
    historical_price = snapshot.historical_prices.get((coin, timestamp))
    if historical_price is not None and historical_price > 0:
        return quantity * historical_price, "estimated", f"{coin} {source} basis is estimated from nearest loaded candle."
    if current_price is not None and current_price > 0:
        return quantity * current_price, "estimated", (
            f"{coin} {source} basis is estimated because historical price was unavailable; using current mark as temporary basis estimate."
        )
    return None, "incomplete", f"Missing historical price for {quantity:g} {coin} {source} at {timestamp}."


def _consume_custom_lots(lots: list[CustomSpotLot], quantity: float) -> tuple[float, float]:
    remaining = quantity
    consumed_cost = 0.0
    for lot in lots:
        if remaining <= ZERO_EPSILON:
            break
        if lot.remaining_quantity <= ZERO_EPSILON:
            continue
        consumed_quantity = min(lot.remaining_quantity, remaining)
        consumed_cost += consumed_quantity * lot.unit_cost_usd
        lot.remaining_quantity = round(lot.remaining_quantity - consumed_quantity, 12)
        remaining = round(remaining - consumed_quantity, 12)
    return consumed_cost, max(0.0, remaining)


def _custom_basis_status(
    coin: str,
    *,
    incomplete: bool,
    estimated: bool,
    missing_basis_quantity: float,
    missing_disposition_quantity: float,
    known_basis: float,
    has_history: bool,
) -> str:
    if missing_basis_quantity > ZERO_EPSILON:
        if known_basis > ZERO_EPSILON:
            return "Partial history"
        return "History unavailable" if not has_history else f"Missing {coin} basis for {missing_basis_quantity:g} {coin}"
    if missing_disposition_quantity > ZERO_EPSILON:
        return "Partial history"
    if incomplete:
        return "Partial history"
    if estimated:
        return "Estimated deposit basis"
    return "Exact from full history"


def _ledger_spot_movements(update: dict[str, Any], user: str) -> list[dict[str, Any]]:
    delta = update.get("delta") if isinstance(update.get("delta"), dict) else update
    if not isinstance(delta, dict):
        return []
    if not _ledger_update_is_completed(update, delta):
        return []

    update_type = str(delta.get("type") or "").strip()
    timestamp = int(_to_float(update.get("time") or delta.get("time")) or 0)
    user_lc = user.lower()
    movements: list[dict[str, Any]] = []

    if update_type == "deposit":
        usdc = _to_float(delta.get("usdc"))
        if usdc is not None:
            movements.append(_movement("USDC", abs(usdc), abs(usdc), timestamp, "deposit", True, delta))
            return movements
        coin = _ledger_delta_coin(delta)
        amount = _ledger_delta_amount(delta)
        if coin and amount is not None:
            movements.append(
                _movement(
                    coin,
                    abs(amount),
                    _first_number(delta, ("usdcValue", "usdValue", "value", "accountValueChange")),
                    timestamp,
                    "deposit",
                    True,
                    delta,
                    fee_usd=_spot_transfer_fee_usd(delta, coin),
                )
            )
        return movements

    if update_type == "withdraw":
        usdc = _to_float(delta.get("usdc"))
        if usdc is not None:
            fee = abs(_to_float(delta.get("fee")) or 0.0)
            movements.append(_movement("USDC", -abs(usdc), abs(usdc), timestamp, "withdraw", True, delta, fee_usd=fee))
            return movements
        coin = _ledger_delta_coin(delta)
        amount = _ledger_delta_amount(delta)
        if coin and amount is not None:
            movements.append(
                _movement(
                    coin,
                    -abs(amount),
                    _first_number(delta, ("usdcValue", "usdValue", "value", "accountValueChange")),
                    timestamp,
                    "withdraw",
                    True,
                    delta,
                    fee_usd=_spot_transfer_fee_usd(delta, coin),
                )
            )
        return movements

    if update_type in {"internalTransfer", "subAccountTransfer"}:
        usdc = _to_float(delta.get("usdc"))
        if usdc is None:
            return movements
        source_user = _ledger_source_user(delta)
        destination = _ledger_destination_user(delta)
        signed = usdc
        if source_user == user_lc and destination != user_lc:
            signed = -abs(usdc)
        elif destination == user_lc:
            signed = abs(usdc)
        fee = abs(_first_number(delta, ("feeUsd", "feeUSDC", "usdcFee", "fee")) or 0.0)
        movements.append(_movement("USDC", signed, abs(usdc), timestamp, update_type, True, delta, fee_usd=fee))
        return movements

    if update_type == "spotTransfer":
        coin = _ledger_delta_coin(delta)
        amount = _ledger_delta_amount(delta)
        if not coin or amount is None:
            return movements
        source_user = _ledger_source_user(delta)
        destination = _ledger_destination_user(delta)
        signed = amount
        if source_user == user_lc and destination != user_lc:
            signed = -abs(amount)
        elif destination == user_lc:
            signed = abs(amount)
        fee = _spot_transfer_fee_usd(delta, coin)
        movements.append(
            _movement(
                coin,
                signed,
                _first_number(delta, ("usdcValue", "usdValue")),
                timestamp,
                "spotTransfer",
                True,
                delta,
                fee_usd=fee,
            )
        )
        return movements

    if update_type == "spotGenesis":
        coin = _ledger_delta_coin(delta)
        amount = _ledger_delta_amount(delta)
        if coin and amount is not None:
            movements.append(_movement(coin, abs(amount), None, timestamp, "spotGenesis", False, delta))
        return movements

    if update_type == "cStakingTransfer":
        coin = _ledger_delta_coin(delta) or "HYPE"
        amount = _ledger_delta_amount(delta)
        if coin and amount is not None:
            is_deposit = bool(delta.get("isDeposit"))
            movements.append(_movement(coin, -abs(amount) if is_deposit else abs(amount), None, timestamp, "cStakingTransfer", False, delta))
        return movements

    return movements


def _ledger_update_is_completed(update: dict[str, Any], delta: dict[str, Any]) -> bool:
    status = str(
        delta.get("status")
        or update.get("status")
        or delta.get("state")
        or update.get("state")
        or ""
    ).strip().lower()
    if not status:
        return True
    return status in {"completed", "complete", "success", "succeeded", "confirmed", "ok"}


def _ledger_delta_coin(delta: dict[str, Any]) -> str:
    return _canonical_spot_coin(str(delta.get("token") or delta.get("coin") or delta.get("asset") or "").strip().upper())


def _ledger_delta_amount(delta: dict[str, Any]) -> float | None:
    return _first_number(delta, ("amount", "sz", "size", "quantity", "qty"))


def _ledger_source_user(delta: dict[str, Any]) -> str:
    return str(delta.get("user") or delta.get("source") or delta.get("from") or delta.get("src") or "").lower()


def _ledger_destination_user(delta: dict[str, Any]) -> str:
    return str(delta.get("destination") or delta.get("to") or delta.get("dst") or "").lower()


def _spot_transfer_fee_usd(delta: dict[str, Any], coin: str) -> float:
    explicit_usd_fee = _first_number(delta, ("feeUsd", "feeUSDC", "usdcFee"))
    if explicit_usd_fee is not None:
        return abs(explicit_usd_fee)
    raw_fee = abs(_to_float(delta.get("fee")) or 0.0)
    return raw_fee if coin in CASH_LIKE_COINS else 0.0


def _movement(
    coin: str,
    quantity: float,
    usd_value: float | None,
    timestamp: int,
    source: str,
    counts_as_capital: bool,
    raw: dict[str, Any],
    *,
    fee_usd: float = 0.0,
) -> dict[str, Any]:
    return {
        "coin": _canonical_spot_coin(coin),
        "quantity": quantity,
        "usd_value": abs(usd_value) if usd_value is not None else None,
        "time": timestamp,
        "source": source,
        "counts_as_capital": counts_as_capital,
        "raw": raw,
        "fee_usd": fee_usd,
    }


def _custom_account_contributions(snapshot: HyperliquidSnapshot) -> tuple[float, float, list[str]]:
    deposits = 0.0
    withdrawals = 0.0
    warnings: list[str] = []
    current_spot = _custom_current_spot_values(snapshot)
    for update in snapshot.user_non_funding_ledger_updates:
        for movement in _ledger_spot_movements(update, snapshot.user):
            if not movement.get("counts_as_capital"):
                continue
            quantity = float(movement["quantity"])
            if abs(quantity) <= ZERO_EPSILON:
                continue
            coin = movement["coin"]
            if coin in CASH_LIKE_COINS:
                value = abs(_to_float(movement.get("usd_value")) or quantity)
            else:
                current_price = _to_float(current_spot.get(coin, {}).get("price"))
                value, _status, warning = _custom_event_basis_value(
                    snapshot,
                    coin,
                    int(movement.get("time") or 0),
                    abs(quantity),
                    movement.get("usd_value"),
                    current_price,
                    str(movement.get("source") or "movement"),
                )
                if warning:
                    warnings.append(warning)
                if value is None:
                    continue
            if quantity > 0:
                deposits += value
            else:
                withdrawals += value
    return deposits, withdrawals, _dedupe_strings(warnings)


def _custom_current_account_value(snapshot: HyperliquidSnapshot, current_spot: dict[str, dict[str, Any]]) -> float:
    spot_value = sum(float(row.get("value") or 0.0) for row in current_spot.values())
    return _perp_account_value(snapshot.clearinghouse_state) + spot_value


def _custom_perp_realized_pnl(snapshot: HyperliquidSnapshot) -> float:
    total = 0.0
    for fill in snapshot.user_fills:
        if _spot_fill(fill, snapshot) is not None:
            continue
        total += _to_float(fill.get("closedPnl") or fill.get("closedPnL")) or 0.0
    return total


def _custom_perp_unrealized_pnl(snapshot: HyperliquidSnapshot) -> float:
    total = 0.0
    for raw_position in _asset_positions(snapshot.clearinghouse_state):
        position_data = raw_position.get("position") or raw_position
        if isinstance(position_data, dict):
            total += _to_float(position_data.get("unrealizedPnl")) or 0.0
    return total


def _custom_funding_total(snapshot: HyperliquidSnapshot) -> float:
    total = 0.0
    for row in snapshot.user_funding:
        delta = row.get("delta") if isinstance(row.get("delta"), dict) else row
        if isinstance(delta, dict):
            total += _first_number(delta, ("usdc", "amount", "funding")) or 0.0
    return total


def _custom_fee_total(snapshot: HyperliquidSnapshot) -> float:
    total = 0.0
    for fill in snapshot.user_fills:
        if _spot_fill(fill, snapshot) is not None:
            continue
        fee = _first_number(fill, ("fee", "feeUsd", "feeUSDC"))
        fee_token = _canonical_spot_coin(str(fill.get("feeToken") or fill.get("feeCoin") or "USDC"))
        if fee is not None and fee_token in CASH_LIKE_COINS:
            total += abs(fee)
    return total


def _quantity_tolerance(quantity: float) -> float:
    return max(0.0000001, abs(quantity) * 0.0001)


def _history_start_time() -> int:
    return _to_int(os.getenv("HYPERLIQUID_HISTORY_START_TIME_MS")) or 0


def _row_time(row: dict[str, Any]) -> int | None:
    return _to_int(row.get("time") or row.get("timestamp") or row.get("statusTimestamp"))


def _dedupe_dict_rows(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = tuple(row.get(field) for field in key_fields)
        if all(value is None for value in key):
            key = (id(row),)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _dedupe_strings(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


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
    custom_coin_pnl: CustomCoinPnl | None = None,
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

    entry = _spot_entry_notional_status(
        coin,
        quantity,
        raw_balance,
        snapshot,
        current_value,
        reconstructed_spot_basis=reconstructed_spot_basis,
    )
    entry_notional = entry.value
    average_cost = (entry_notional / quantity) if entry_notional is not None and quantity > ZERO_EPSILON else None
    last_price = (
        (current_value / quantity) if current_value is not None and quantity > ZERO_EPSILON else None
    ) or mid_price or average_cost or 0.0

    raw_profit_loss = _spot_raw_pnl(raw_balance, current_value, entry)

    custom_profit_loss = None
    custom_realized_profit_loss = None
    custom_unrealized_profit_loss = None
    custom_pnl_status = ""
    basis_status = ""
    if custom_coin_pnl is not None:
        custom_profit_loss = custom_coin_pnl.total_pnl
        custom_realized_profit_loss = custom_coin_pnl.realized_pnl
        custom_unrealized_profit_loss = custom_coin_pnl.unrealized_pnl
        custom_pnl_status = custom_coin_pnl.basis_status
        basis_status = custom_coin_pnl.basis_status
    elif raw_profit_loss is not None:
        custom_profit_loss = raw_profit_loss
        custom_unrealized_profit_loss = raw_profit_loss
        custom_pnl_status = "Raw P&L available"
        basis_status = "Raw P&L available" if entry.known else ""

    return (
        Position(
            symbol=f"{coin}-SPOT",
            quantity=round(quantity, 8),
            average_cost=round(average_cost or last_price, 4),
            last_price=round(last_price, 4),
            open_profit_loss=round(raw_profit_loss, 2) if raw_profit_loss is not None else None,
            unrealized_profit_loss_known=raw_profit_loss is not None,
            cost_basis_estimated=not entry.known,
            raw_profit_loss=round(raw_profit_loss, 2) if raw_profit_loss is not None else None,
            custom_profit_loss=round(custom_profit_loss, 2) if custom_profit_loss is not None else None,
            custom_realized_profit_loss=(
                round(custom_realized_profit_loss, 2) if custom_realized_profit_loss is not None else None
            ),
            custom_unrealized_profit_loss=(
                round(custom_unrealized_profit_loss, 2) if custom_unrealized_profit_loss is not None else None
            ),
            custom_pnl_status=custom_pnl_status,
            basis_status=basis_status,
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
    return _spot_entry_notional_status(
        coin,
        quantity,
        raw_balance,
        snapshot,
        current_value,
        reconstructed_spot_basis=reconstructed_spot_basis,
    ).value


def _spot_entry_notional_status(
    coin: str,
    quantity: float | None,
    raw_balance: dict[str, Any],
    snapshot: HyperliquidSnapshot,
    current_value: float | None,
    *,
    reconstructed_spot_basis: dict[str, SpotCostBasis] | None = None,
) -> SpotEntryNotional:
    history_basis = _spot_history_entry_notional(
        coin,
        quantity,
        snapshot,
        reconstructed_spot_basis=reconstructed_spot_basis,
    )
    if history_basis is not None:
        return SpotEntryNotional(history_basis, True)

    api_entry_notional = _spot_api_entry_notional(raw_balance, quantity)
    if api_entry_notional is not None:
        return SpotEntryNotional(api_entry_notional, True)

    if coin not in CASH_LIKE_COINS and current_value is not None:
        return SpotEntryNotional(current_value, False)

    return SpotEntryNotional(None, False)


def _spot_raw_pnl(
    raw_balance: dict[str, Any],
    current_value: float | None,
    entry: SpotEntryNotional,
) -> float | None:
    if current_value is not None and entry.value is not None and entry.known:
        return current_value - entry.value
    direct_pnl = _first_number(raw_balance, ("pnl", "unrealizedPnl", "unrealizedPnlUsd", "profitLoss"))
    if direct_pnl is not None:
        return direct_pnl
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
    direction = str(fill.get("dir") or fill.get("direction") or "").strip().lower()
    is_buy = side in {"B", "BUY"} or direction in {"buy", "spot buy"}
    is_sell = side in {"A", "S", "SELL", "ASK"} or direction in {"sell", "spot sell"}
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
        direction = str(fill.get("dir") or fill.get("direction") or "").strip().lower()
        if direction in {"buy", "sell", "spot buy", "spot sell"}:
            return _canonical_spot_coin(raw_coin)
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


def _format_custom_money(value: float | None, status: str) -> str:
    return status or "--" if value is None else _format_money(value)


def _format_optional_number(value: float | None) -> str:
    return "--" if value is None else f"{value:g}"


def _format_optional_percent(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2f}%"
