from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any

from app.core.portfolio import Position
from app.ui import hyperliquid_perp_ticket_use_mid_fix as base_what_if


def install_hyperliquid_existing_perp_what_if_extension(app_cls: type[tk.Tk]) -> None:
    """Include already-open perp exposure in dedicated Hyperliquid what-if output."""

    app_cls.run_hyperliquid_perp_what_if = _run_hyperliquid_perp_what_if_with_open_position  # type: ignore[attr-defined]


def _run_hyperliquid_perp_what_if_with_open_position(self: tk.Tk) -> None:
    try:
        base_what_if._ensure_perp_vars(self)
        ticket = self.parse_hyperliquid_ticket()
        leverage = base_what_if._optional_float(getattr(self, "hyperliquid_leverage_var").get(), default=1.0) or 1.0
        fee_rate = base_what_if._optional_float(getattr(self, "hyperliquid_fee_rate_var").get(), default=0.045) or 0.045
        tp_price = base_what_if._optional_float(getattr(self, "hyperliquid_target_price_var").get(), default=None)
        sl_price = base_what_if._optional_float(getattr(self, "stop_price_var").get(), default=None)
        is_long = ticket.is_buy
        if tp_price is None:
            tp_price = ticket.limit_price * (1.05 if is_long else 0.95)
            self.hyperliquid_target_price_var.set(base_what_if._format_price(tp_price))
        if sl_price is None:
            sl_price = ticket.limit_price * (0.97 if is_long else 1.03)
            self.stop_price_var.set(base_what_if._format_price(sl_price))

        proposed_signed_qty = ticket.size if ticket.is_buy else -ticket.size
        existing_perps = _existing_perp_positions_for_coin(self, ticket.coin)
        existing_signed_qty = sum(signed_qty for _position, signed_qty in existing_perps)

        tp_case = base_what_if._perp_case(ticket.limit_price, tp_price, ticket.size, is_long, leverage, fee_rate)
        sl_case = base_what_if._perp_case(ticket.limit_price, sl_price, ticket.size, is_long, leverage, fee_rate)
        spot_position = base_what_if._spot_position_for_coin(self, ticket.coin)
        tp_spot_lines = _spot_and_existing_perp_scenario_lines(
            "TP",
            ticket.coin,
            tp_price,
            spot_position,
            existing_perps,
            tp_case["net_pnl"],
        )
        sl_spot_lines = _spot_and_existing_perp_scenario_lines(
            "SL",
            ticket.coin,
            sl_price,
            spot_position,
            existing_perps,
            sl_case["net_pnl"],
        )

        notional = ticket.limit_price * ticket.size
        margin = notional / leverage if leverage > 0 else notional
        collateral = base_what_if._planning_collateral_usdc(self, margin)
        rough_liq = base_what_if._rough_liquidation_price(ticket.limit_price, ticket.size, is_long, margin, collateral)
        rr = base_what_if._risk_reward(tp_case["net_pnl"], sl_case["net_pnl"])
        direction = "LONG" if is_long else "SHORT"
        attach = "on" if self.hyperliquid_attach_tpsl_var.get() else "off"
        hedge_lines = _spot_hedge_lines_with_open_perp(
            self,
            ticket.coin,
            proposed_signed_qty,
            existing_signed_qty,
            ticket.limit_price,
        )

        self.hyperliquid_status_var.set("Hyperliquid: what-if ready")
        self._set_preview_text(
            "HYPERLIQUID PERP WHAT-IF\n"
            "========================\n\n"
            f"Market: {ticket.coin}-PERP\n"
            f"Direction: {direction}\n"
            f"Size: {ticket.size:g} {ticket.coin}\n"
            f"Entry / Limit: ${ticket.limit_price:,.4f}\n"
            f"Leverage: {leverage:g}x\n"
            f"Attach TP/SL: {attach}\n"
            f"Order value: ${notional:,.2f}\n"
            f"Estimated margin required: ${margin:,.2f}\n"
            f"Collateral used for liq estimate: ${collateral:,.2f}\n"
            f"Fee estimate: {fee_rate:g}% per side\n"
            f"Rough liquidation estimate: {base_what_if._format_liq(rough_liq)}\n\n"
            + "\n".join(hedge_lines) + "\n\n"
            "Take Profit scenario\n"
            f"- TP Price: ${tp_price:,.4f}\n"
            f"- Proposed perp gross P&L: ${tp_case['gross_pnl']:+,.2f}\n"
            f"- Proposed perp estimated fees: ${tp_case['fees']:,.2f}\n"
            f"- Proposed perp net gain/loss: ${tp_case['net_pnl']:+,.2f}\n"
            f"- Proposed perp ROI on estimated margin: {tp_case['margin_roi_percent']:+.2f}%\n"
            + "\n".join(tp_spot_lines) + "\n\n"
            "Stop Loss scenario\n"
            f"- SL Price: ${sl_price:,.4f}\n"
            f"- Proposed perp gross P&L: ${sl_case['gross_pnl']:+,.2f}\n"
            f"- Proposed perp estimated fees: ${sl_case['fees']:,.2f}\n"
            f"- Proposed perp net gain/loss: ${sl_case['net_pnl']:+,.2f}\n"
            f"- Proposed perp ROI on estimated margin: {sl_case['margin_roi_percent']:+.2f}%\n"
            + "\n".join(sl_spot_lines) + "\n\n"
            "Setup quality\n"
            f"- Reward/risk using proposed perp net P&L: {rr}\n"
            "- Existing open perp scenario P&L uses the synced perp mark as the starting point, with avg-cost context when available.\n"
            "- Spot scenario P&L uses the synced spot mark as the starting point, plus avg-cost context when available.\n"
            "- TP/SL fields are scenario inputs unless Attach TP/SL is on and child-order execution is wired.\n"
            "- Liquidation is an estimate: Hyperliquid can also account for maintenance margin, funding, open orders, and account mode."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: what-if failed")
        messagebox.showerror("Hyperliquid perp what-if failed", str(exc))


def _spot_hedge_lines_with_open_perp(
    self: tk.Tk,
    coin: str,
    proposed_signed_qty: float,
    existing_signed_qty: float,
    entry: float,
) -> list[str]:
    spot = base_what_if._spot_position_for_coin(self, coin)
    if spot is None:
        return ["Spot + perp exposure context", f"- No synced {coin} spot position found in the cockpit portfolio."]

    spot_qty = spot.quantity
    spot_value = spot_qty * spot.last_price
    proposed_notional = abs(proposed_signed_qty) * entry
    total_perp_signed_qty = existing_signed_qty + proposed_signed_qty
    net_delta = spot_qty + total_perp_signed_qty
    net_label = _net_label(net_delta)
    existing_label = _signed_qty_label(existing_signed_qty, coin)
    proposed_label = _signed_qty_label(proposed_signed_qty, coin)
    total_perp_label = _signed_qty_label(total_perp_signed_qty, coin)
    hedge_ratio = (abs(min(total_perp_signed_qty, 0.0)) / spot_qty * 100.0) if spot_qty > 0 else 0.0

    if total_perp_signed_qty < 0:
        interpretation = "Total short perp exposure offsets your synced spot. A 100% ratio is roughly spot-neutral before fees/funding."
    elif total_perp_signed_qty > 0:
        interpretation = "Total long perp exposure adds to your synced spot exposure. It is not a hedge."
    else:
        interpretation = "No net perp exposure after this ticket; your directional exposure is the synced spot position."

    return [
        "Spot + perp exposure context",
        f"- Synced spot: {spot_qty:g} {coin} worth about ${spot_value:,.2f}",
        f"- Existing synced perp exposure: {existing_label}",
        f"- Proposed ticket delta: {proposed_label}",
        f"- Proposed ticket notional: ${proposed_notional:,.2f}",
        f"- Total perp exposure after ticket: {total_perp_label}",
        f"- Hedge ratio vs spot after ticket: {hedge_ratio:.1f}%",
        f"- Net directional size after open perp + ticket: {net_delta:+g} {coin} ({net_label})",
        f"- {interpretation}",
    ]


def _spot_and_existing_perp_scenario_lines(
    label: str,
    coin: str,
    scenario_price: float,
    spot: Position | None,
    existing_perps: list[tuple[Position, float]],
    proposed_perp_net_pnl: float,
) -> list[str]:
    lines: list[str] = []
    spot_move_pnl = 0.0
    existing_perp_move_pnl = 0.0

    if spot is None:
        lines.append(f"- Spot at {label}: no synced {coin} spot position found.")
    else:
        spot_qty = spot.quantity
        current_spot_value = spot_qty * spot.last_price
        scenario_spot_value = spot_qty * scenario_price
        spot_move_pnl = scenario_spot_value - current_spot_value
        spot_open_pnl = scenario_spot_value - spot.cost_basis
        lines.extend(
            [
                f"- Spot value at {label}: ${scenario_spot_value:,.2f}",
                f"- Spot P&L from synced mark: ${spot_move_pnl:+,.2f}",
                f"- Spot open P&L vs avg cost: ${spot_open_pnl:+,.2f}",
            ]
        )

    if not existing_perps:
        lines.append(f"- Existing perp at {label}: no synced open {coin} perp position found.")
    else:
        existing_perp_open_pnl = 0.0
        existing_perp_notional = 0.0
        for position, signed_qty in existing_perps:
            existing_perp_move_pnl += (scenario_price - position.last_price) * signed_qty
            existing_perp_open_pnl += (scenario_price - position.average_cost) * signed_qty
            existing_perp_notional += abs(signed_qty) * scenario_price
        lines.extend(
            [
                f"- Existing perp value at {label}: ${existing_perp_notional:,.2f}",
                f"- Existing perp P&L from synced mark: ${existing_perp_move_pnl:+,.2f}",
                f"- Existing perp open P&L vs avg cost: ${existing_perp_open_pnl:+,.2f}",
            ]
        )

    combined = spot_move_pnl + existing_perp_move_pnl + proposed_perp_net_pnl
    lines.append(f"- Combined spot + existing perp move + proposed perp net P&L: ${combined:+,.2f}")
    return lines


def _existing_perp_positions_for_coin(self: tk.Tk, coin: str) -> list[tuple[Position, float]]:
    try:
        portfolio = self.broker.get_portfolio()
    except Exception:
        return []

    target_prefixes = (f"{coin}-PERP", f"HL:{coin}-PERP")
    matches: list[tuple[Position, float]] = []
    for raw_symbol, position in portfolio.positions.items():
        symbol = raw_symbol.upper()
        if not symbol.startswith(target_prefixes):
            continue
        signed_qty = _signed_perp_quantity(symbol, position.quantity)
        if abs(signed_qty) > 0.00000001:
            matches.append((position, signed_qty))
    return matches


def _signed_perp_quantity(symbol: str, quantity: float) -> float:
    if symbol.endswith("-PERP-SHORT"):
        return -abs(quantity)
    if symbol.endswith("-PERP"):
        return abs(quantity)
    return quantity


def _signed_qty_label(signed_qty: float, coin: str) -> str:
    if abs(signed_qty) <= 0.00000001:
        return f"0 {coin}"
    side = "long" if signed_qty > 0 else "short"
    return f"{signed_qty:+g} {coin} ({side})"


def _net_label(signed_qty: float) -> str:
    if signed_qty > 0.00000001:
        return "net long"
    if signed_qty < -0.00000001:
        return "net short"
    return "flat"
