from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any

from app.brokers.hyperliquid.trading import HyperliquidTradingConfig
from app.ui import options_lab_extension

_DELETE_ME = "DELETE ME"
LEVERAGE_PNL_EXPLANATION = "Leverage does not change dollar P&L for a fixed contract size. It changes margin required, ROI on margin, and liquidation distance."


@dataclass(frozen=True)
class TpslScenarioReadout:
    field: str
    price: float
    label: str
    warning: str | None
    valid: bool


def install_hyperliquid_perp_ticket(app_cls: type[tk.Tk] | None = None) -> None:
    """Make the dedicated Hyperliquid ticket mirror the exchange ticket more closely."""
    _patch_hyperliquid_tab_builder()
    _patch_options_layout_builder()
    if app_cls is not None:
        _patch_grid_row(app_cls)
        _patch_layout_after_build(app_cls)
        _patch_hyperliquid_actions(app_cls)


def _ensure_perp_vars(self: tk.Tk) -> None:
    if not hasattr(self, "hyperliquid_attach_tpsl_var"):
        self.hyperliquid_attach_tpsl_var = tk.BooleanVar(value=False)


def _patch_grid_row(app_cls: type[tk.Tk]) -> None:
    """Rewrite legacy Hyperliquid rows before the shared builder creates widgets."""
    original_grid_row = app_cls._grid_row

    def grid_row_without_delete_me(
        self: tk.Tk,
        parent: ttk.Frame,
        row: int,
        left_label: str,
        left_widget: tk.Widget,
        right_label: str | None = None,
        right_widget: tk.Widget | None = None,
    ) -> None:
        title = _labelframe_title(parent)
        if title == "Hyperliquid Perp Ticket":
            _ensure_perp_vars(self)
            if left_label == "Target price" and right_label == "Pain price":
                left_label = "TP Price"
                right_label = "SL Price"
                right_widget = ttk.Entry(parent, textvariable=_var(self, "hyperliquid_perp_stop_price_var", self.stop_price_var))
            elif left_label == "Stop price" and right_label == _DELETE_ME:
                left_label = "Leverage x"
                left_widget = ttk.Entry(parent, textvariable=_var(self, "hyperliquid_perp_leverage_var", self.hyperliquid_leverage_var))
                right_label = "Use Mid"
                right_widget = _make_hyperliquid_use_mid_button(self, parent)
        elif right_label == _DELETE_ME:
            if title == "Schwab Stock / ETF Ticket":
                right_label = "Use Mid"
                right_widget = _make_schwab_use_mid_button(self, parent)
            else:
                right_label = ""
                right_widget = ttk.Frame(parent, style="Canvas.TFrame")

        return original_grid_row(
            self,
            parent,
            row,
            left_label,
            left_widget,
            right_label,
            right_widget,
        )

    app_cls._grid_row = grid_row_without_delete_me


def _patch_hyperliquid_tab_builder() -> None:
    original_build_hyperliquid_tab = options_lab_extension._build_hyperliquid_trading_tab

    def build_hyperliquid_tab_with_exchange_labels(self: tk.Tk, parent: ttk.Frame) -> None:
        _ensure_perp_vars(self)
        original_build_hyperliquid_tab(self, parent)

    options_lab_extension._build_hyperliquid_trading_tab = build_hyperliquid_tab_with_exchange_labels


def _patch_options_layout_builder() -> None:
    original_build_layout = options_lab_extension._build_layout_with_options_lab

    def build_layout_then_remove_delete_me(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _remove_delete_me_controls(self))

    options_lab_extension._build_layout_with_options_lab = build_layout_then_remove_delete_me


def _patch_layout_after_build(app_cls: type[tk.Tk]) -> None:
    original_build_layout = app_cls._build_layout

    def build_layout_then_remove_delete_me(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _remove_delete_me_controls(self))

    app_cls._build_layout = build_layout_then_remove_delete_me


def _patch_hyperliquid_actions(app_cls: type[tk.Tk]) -> None:
    app_cls.preview_hyperliquid_ticket = _preview_hyperliquid_ticket_overview  # type: ignore[attr-defined]
    app_cls.run_hyperliquid_perp_what_if = _run_hyperliquid_perp_what_if_clean  # type: ignore[attr-defined]


def _var(self: tk.Tk, name: str, fallback: Any) -> Any:
    return getattr(self, name, fallback)


def _remove_delete_me_controls(self: tk.Tk) -> None:
    for widget in list(_walk_widgets(self)):
        if not _is_delete_me_label(widget):
            continue

        parent = widget.master
        if parent is None:
            continue

        grid_info = widget.grid_info()
        row = int(grid_info.get("row", 0))
        title = _labelframe_title(parent)

        widget.destroy()
        for existing in parent.grid_slaves(row=row, column=3):
            existing.destroy()

        if title == "Hyperliquid Perp Ticket":
            _grid_replacement_use_mid(
                parent,
                row,
                grid_info,
                _make_hyperliquid_use_mid_button(self, parent),
            )
        elif title == "Schwab Stock / ETF Ticket":
            _grid_replacement_use_mid(
                parent,
                row,
                grid_info,
                _make_schwab_use_mid_button(self, parent),
            )


def _preview_hyperliquid_ticket_overview(self: tk.Tk) -> None:
    try:
        _ensure_perp_vars(self)
        ticket = self.parse_hyperliquid_ticket()
        config = HyperliquidTradingConfig()
        leverage = _optional_float(getattr(self, "hyperliquid_leverage_var").get(), default=1.0) or 1.0
        fee_rate = _optional_float(getattr(self, "hyperliquid_fee_rate_var").get(), default=0.045) or 0.045
        tp_price = _optional_float(getattr(self, "hyperliquid_target_price_var").get(), default=None)
        sl_price = _optional_float(getattr(self, "stop_price_var").get(), default=None)
        attach_tpsl = bool(self.hyperliquid_attach_tpsl_var.get())
        notional = ticket.size * ticket.limit_price
        margin = notional / leverage if leverage > 0 else notional
        collateral = _planning_collateral_usdc(self, margin)
        liquidation_lines = _liquidation_readout_lines(ticket.limit_price, ticket.size, ticket.is_buy, leverage, collateral)
        closing_side = "SELL" if ticket.is_buy else "BUY"
        main_direction = "BUY / LONG" if ticket.is_buy else "SELL / SHORT"
        reduce_text = "reduce-only" if ticket.reduce_only else "not reduce-only"
        hedge_lines = _spot_hedge_lines(self, ticket.coin, ticket.size, ticket.is_buy, ticket.limit_price)
        tp_readout = _tpsl_scenario_readout("TP", ticket.limit_price, tp_price, ticket.is_buy) if tp_price is not None else None
        sl_readout = _tpsl_scenario_readout("SL", ticket.limit_price, sl_price, ticket.is_buy) if sl_price is not None else None

        lines = [
            "HYPERLIQUID ORDER OVERVIEW",
            "==========================",
            "",
            "Main order",
            f"- {main_direction} {ticket.size:g} {ticket.coin}",
            f"- Limit entry: ${ticket.limit_price:,.4f}",
            f"- Time in force: {ticket.tif}",
            f"- Leverage shown for planning: {leverage:g}x",
            f"- {reduce_text}",
            f"- Order value: ${notional:,.2f}",
            f"- Estimated margin: ${margin:,.2f}",
            f"- Collateral used for liq estimate: ${collateral:,.2f}",
            *liquidation_lines,
            f"- Fee estimate: {fee_rate:g}% per side",
            "",
            "Protection",
        ]

        if attach_tpsl:
            if tp_price is None and sl_price is None:
                lines.append("- Attach TP/SL is on, but no TP Price or SL Price is entered yet.")
            if tp_price is not None:
                lines.append(f"- {tp_readout.label if tp_readout else 'TP field scenario'}: {closing_side} reduce-only trigger near ${tp_price:,.4f}")
                if tp_readout and tp_readout.warning:
                    lines.append(f"- {tp_readout.warning}")
            if sl_price is not None:
                lines.append(f"- {sl_readout.label if sl_readout else 'SL field scenario'}: {closing_side} reduce-only trigger near ${sl_price:,.4f}")
                if sl_readout and sl_readout.warning:
                    lines.append(f"- {sl_readout.warning}")
            lines.append("- LIVE Submit will attempt these reduce-only child trigger order(s) immediately after the parent order is accepted.")
        else:
            lines.append("- Attach TP/SL is off. LIVE Submit is a single main order only.")

        lines.extend(["", *hedge_lines, "", "Environment gates", *config.validation_lines()])
        self.hyperliquid_status_var.set("Hyperliquid: overview ready")
        self._set_preview_text("\n".join(lines))
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: overview failed")
        messagebox.showerror("Hyperliquid overview failed", str(exc))


def _run_hyperliquid_perp_what_if_clean(self: tk.Tk) -> None:
    try:
        _ensure_perp_vars(self)
        ticket = self.parse_hyperliquid_ticket()
        leverage = _optional_float(getattr(self, "hyperliquid_leverage_var").get(), default=1.0) or 1.0
        fee_rate = _optional_float(getattr(self, "hyperliquid_fee_rate_var").get(), default=0.045) or 0.045
        tp_price = _optional_float(getattr(self, "hyperliquid_target_price_var").get(), default=None)
        sl_price = _optional_float(getattr(self, "stop_price_var").get(), default=None)
        is_long = ticket.is_buy
        if tp_price is None:
            tp_price = ticket.limit_price * (1.05 if is_long else 0.95)
            self.hyperliquid_target_price_var.set(_format_price(tp_price))
        if sl_price is None:
            sl_price = ticket.limit_price * (0.97 if is_long else 1.03)
            self.stop_price_var.set(_format_price(sl_price))

        tp_case = _perp_case(ticket.limit_price, tp_price, ticket.size, is_long, leverage, fee_rate)
        sl_case = _perp_case(ticket.limit_price, sl_price, ticket.size, is_long, leverage, fee_rate)
        tp_readout = _tpsl_scenario_readout("TP", ticket.limit_price, tp_price, is_long)
        sl_readout = _tpsl_scenario_readout("SL", ticket.limit_price, sl_price, is_long)
        spot_position = _spot_position_for_coin(self, ticket.coin)
        tp_spot_lines = _spot_scenario_lines("TP field", ticket.coin, tp_price, spot_position, tp_case["net_pnl"])
        sl_spot_lines = _spot_scenario_lines("SL field", ticket.coin, sl_price, spot_position, sl_case["net_pnl"])
        notional = ticket.limit_price * ticket.size
        margin = notional / leverage if leverage > 0 else notional
        collateral = _planning_collateral_usdc(self, margin)
        liquidation_lines = _liquidation_readout_lines(ticket.limit_price, ticket.size, is_long, leverage, collateral)
        rr = _risk_reward(tp_case["net_pnl"], sl_case["net_pnl"])
        direction = "LONG" if is_long else "SHORT"
        attach = "on" if self.hyperliquid_attach_tpsl_var.get() else "off"
        hedge_lines = _spot_hedge_lines(self, ticket.coin, ticket.size, is_long, ticket.limit_price)
        tpsl_warning_lines = _tpsl_warning_lines(tp_readout, sl_readout)

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
            + "\n".join(liquidation_lines) + "\n"
            f"{LEVERAGE_PNL_EXPLANATION}\n\n"
            + "\n".join(hedge_lines) + "\n\n"
            + "\n".join(tpsl_warning_lines) + ("\n\n" if tpsl_warning_lines else "")
            + f"{tp_readout.label}\n"
            f"- TP Price: ${tp_price:,.4f}\n"
            f"- Gross P&L: ${tp_case['gross_pnl']:+,.2f}\n"
            f"- Estimated fees: ${tp_case['fees']:,.2f}\n"
            f"- Net gain/loss: ${tp_case['net_pnl']:+,.2f}\n"
            f"- ROI on estimated margin: {tp_case['margin_roi_percent']:+.2f}%\n"
            + "\n".join(tp_spot_lines) + "\n\n"
            f"{sl_readout.label}\n"
            f"- SL Price: ${sl_price:,.4f}\n"
            f"- Gross P&L: ${sl_case['gross_pnl']:+,.2f}\n"
            f"- Estimated fees: ${sl_case['fees']:,.2f}\n"
            f"- Net gain/loss: ${sl_case['net_pnl']:+,.2f}\n"
            f"- ROI on estimated margin: {sl_case['margin_roi_percent']:+.2f}%\n"
            + "\n".join(sl_spot_lines) + "\n\n"
            "Setup quality\n"
            f"- Reward/risk using net P&L: {rr}\n"
            "- Spot scenario P&L uses the synced spot mark as the starting point, plus avg-cost context when available.\n"
            "- TP/SL fields are scenario inputs; when Attach TP/SL is on, LIVE Submit attempts reduce-only child trigger orders after the parent order is accepted.\n"
            "- Liquidation is an estimate: Hyperliquid can also account for maintenance margin, funding, open orders, and account mode."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: what-if failed")
        messagebox.showerror("Hyperliquid perp what-if failed", str(exc))


def _planning_collateral_usdc(self: tk.Tk, fallback_margin: float) -> float:
    try:
        portfolio = self.broker.get_portfolio()
    except Exception:
        return max(fallback_margin, 0.0)

    hyperliquid_usdc = 0.0
    for cash in portfolio.cash_positions.values():
        if cash.symbol.upper() in {"USDC", "USD"} and "HYPERLIQUID" in cash.source.upper():
            hyperliquid_usdc += cash.amount
    if hyperliquid_usdc > 0:
        return hyperliquid_usdc
    return max(portfolio.cash, fallback_margin, 0.0)


def _tpsl_scenario_readout(field: str, entry: float, price: float, is_long: bool) -> TpslScenarioReadout:
    clean_field = field.upper()
    direction = "LONG" if is_long else "SHORT"
    relation = "above" if price > entry else "below" if price < entry else "at"
    if clean_field == "TP":
        valid = price > entry if is_long else price < entry
        label = "Take Profit scenario" if valid else f"TP field scenario - INVALID for {direction} take-profit"
        if valid:
            warning = None
        elif relation == "at":
            warning = f"TP is at entry for a {direction}. This is flat before fees, not take profit."
        else:
            warning = f"TP is {relation} entry for a {direction}. This is a loss scenario, not take profit."
    elif clean_field == "SL":
        valid = price < entry if is_long else price > entry
        label = "Stop Loss scenario" if valid else f"SL field scenario - INVALID for {direction} stop-loss"
        if valid:
            warning = None
        elif relation == "at":
            warning = f"SL is at entry for a {direction}. This is flat before fees, not stop loss."
        else:
            warning = f"SL is {relation} entry for a {direction}. This is a profit scenario, not stop loss."
    else:
        valid = True
        label = f"{clean_field} field scenario"
        warning = None
    return TpslScenarioReadout(clean_field, price, label, warning, valid)


def _tpsl_warning_lines(*readouts: TpslScenarioReadout) -> list[str]:
    warnings = [readout.warning for readout in readouts if readout.warning]
    if not warnings:
        return []
    return ["TP/SL direction checks", *[f"- {warning}" for warning in warnings]]


def _estimated_margin_required(entry: float, size: float, leverage: float) -> float:
    notional = entry * size
    return notional / leverage if leverage > 0 else notional


def _isolated_liquidation_price(entry: float, size: float, is_long: bool, leverage: float) -> float | None:
    if entry <= 0 or size <= 0 or leverage <= 0:
        return None
    margin_required = _estimated_margin_required(entry, size, leverage)
    move_to_zero_margin = margin_required / size
    if is_long:
        return max(entry - move_to_zero_margin, 0.0)
    return entry + move_to_zero_margin


def _liquidation_readout_lines(entry: float, size: float, is_long: bool, leverage: float, collateral: float) -> list[str]:
    margin_required = _estimated_margin_required(entry, size, leverage)
    isolated_liq = _isolated_liquidation_price(entry, size, is_long, leverage)
    cross_liq = _rough_liquidation_price(entry, size, is_long, margin_required, collateral)
    return [
        f"- Isolated-style liquidation estimate using ticket margin/leverage: {_format_liq(isolated_liq)}",
        f"- Cross-margin rough liquidation estimate using account collateral: {_format_liq(cross_liq)}",
        "- Cross-margin estimate is not an isolated liquidation estimate.",
    ]


def _rough_liquidation_price(entry: float, size: float, is_long: bool, margin_required: float, collateral: float) -> float | None:
    """Approximate cross-style liquidation using available USDC buffer.

    Hyperliquid's UI uses account state, maintenance margin, open orders, and account mode.
    This estimate is intentionally conservative and closer to the exchange UI than the old
    isolated entry +/- 1/leverage shortcut.
    """
    if size <= 0 or entry <= 0:
        return None
    loss_budget = max(collateral - margin_required, 0.0)
    if is_long:
        return max(entry - loss_budget / size, 0.0)
    return entry + loss_budget / size


def _spot_hedge_lines(self: tk.Tk, coin: str, perp_size: float, is_long: bool, entry: float) -> list[str]:
    spot = _spot_position_for_coin(self, coin)
    if spot is None:
        return ["Spot hedge context", f"- No synced {coin} spot position found in the cockpit portfolio."]

    spot_qty = spot.quantity
    spot_value = spot_qty * spot.last_price
    perp_notional = perp_size * entry
    hedge_ratio = (perp_size / spot_qty * 100.0) if spot_qty > 0 else 0.0
    net_delta = spot_qty + perp_size if is_long else spot_qty - perp_size
    net_label = "net long" if net_delta > 0 else "net short" if net_delta < 0 else "flat"
    if is_long:
        interpretation = "This perp adds to your existing spot exposure. It is not a hedge."
    else:
        interpretation = "This short offsets part of your spot exposure. A 100% ratio is roughly spot-neutral before fees/funding."

    return [
        "Spot hedge context",
        f"- Synced spot: {spot_qty:g} {coin} worth about ${spot_value:,.2f}",
        f"- Perp notional: ${perp_notional:,.2f}",
        f"- Hedge ratio vs spot size: {hedge_ratio:.1f}%",
        f"- Net directional size after this ticket: {net_delta:+g} {coin} ({net_label})",
        f"- {interpretation}",
    ]


def _spot_scenario_lines(label: str, coin: str, scenario_price: float, spot, perp_net_pnl: float) -> list[str]:
    if spot is None:
        return [f"- Spot at {label}: no synced {coin} spot position found."]

    spot_qty = spot.quantity
    current_spot_value = spot_qty * spot.last_price
    scenario_spot_value = spot_qty * scenario_price
    spot_move_pnl = scenario_spot_value - current_spot_value
    spot_open_pnl = scenario_spot_value - spot.cost_basis
    combined_move_and_perp = spot_move_pnl + perp_net_pnl

    return [
        f"- Spot value at {label}: ${scenario_spot_value:,.2f}",
        f"- Spot P&L from synced mark: ${spot_move_pnl:+,.2f}",
        f"- Spot open P&L vs avg cost: ${spot_open_pnl:+,.2f}",
        f"- Combined spot move + perp net P&L: ${combined_move_and_perp:+,.2f}",
    ]


def _spot_position_for_coin(self: tk.Tk, coin: str):
    try:
        portfolio = self.broker.get_portfolio()
    except Exception:
        return None
    candidates = (f"HL:{coin}-SPOT", f"{coin}-SPOT", f"HL:{coin}", coin)
    for symbol in candidates:
        position = portfolio.positions.get(symbol.upper())
        if position is not None and position.quantity > 0:
            return position
    return None


def _grid_replacement_use_mid(
    parent: tk.Widget,
    row: int,
    grid_info: dict[str, Any],
    button: ttk.Button,
) -> None:
    ttk.Label(parent, text="Use Mid", style="Subtle.TLabel").grid(
        row=row,
        column=2,
        sticky="w",
        padx=(0, 8),
        pady=grid_info.get("pady", 0),
    )
    button.grid(row=row, column=3, sticky="ew", pady=grid_info.get("pady", 0))


def _is_delete_me_label(widget: tk.Widget) -> bool:
    try:
        return widget.winfo_class() in {"TLabel", "Label"} and widget.cget("text") == _DELETE_ME
    except Exception:
        return False


def _labelframe_title(widget: tk.Widget) -> str:
    try:
        return str(widget.cget("text"))
    except Exception:
        return ""


def _make_hyperliquid_use_mid_button(self: tk.Tk, parent: tk.Widget) -> ttk.Button:
    return ttk.Button(
        parent,
        text="Use Mid",
        command=lambda: options_lab_extension._run_hyperliquid_ticket_action(
            self,
            ticket_kind="perp",
            preview_widget=self.hyperliquid_trading_preview_text,
            command=options_lab_extension._first_available_command(self, "use_hyperliquid_mid_market"),
        ),
        style="Accent.TButton",
    )


def _make_schwab_use_mid_button(self: tk.Tk, parent: tk.Widget) -> ttk.Button:
    return ttk.Button(
        parent,
        text="Use Mid",
        command=lambda: options_lab_extension._run_workspace_action(
            self,
            venue="Schwab",
            preview_widget=self.schwab_trading_preview_text,
            command=options_lab_extension._first_available_command(self, "use_schwab_mid_market"),
        ),
        style="Accent.TButton",
    )


def _perp_case(entry: float, exit_price: float, size: float, is_long: bool, leverage: float, fee_rate_percent: float) -> dict[str, float]:
    signed_move = (exit_price - entry) / entry
    gross_pnl = (exit_price - entry) * size if is_long else (entry - exit_price) * size
    fees = (entry * size + exit_price * size) * (fee_rate_percent / 100.0)
    net_pnl = gross_pnl - fees
    margin = (entry * size) / leverage if leverage > 0 else entry * size
    margin_roi_percent = (net_pnl / margin * 100.0) if margin > 0 else 0.0
    return {"move_percent": signed_move * 100.0, "gross_pnl": gross_pnl, "fees": fees, "net_pnl": net_pnl, "margin_roi_percent": margin_roi_percent}


def _risk_reward(reward_net: float, stop_net: float) -> str:
    risk = abs(min(stop_net, 0.0))
    reward = max(reward_net, 0.0)
    if risk <= 0 or reward <= 0:
        return "n/a — TP must be profitable and SL must be a loss"
    return f"{reward / risk:.2f} : 1"


def _optional_float(raw: object, *, default: float | None) -> float | None:
    try:
        text = str(raw).strip().replace(",", "")
        if text == "":
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _format_price(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_liq(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"${value:,.2f}"


def _walk_widgets(root: tk.Widget):
    for child in root.winfo_children():
        yield child
        yield from _walk_widgets(child)
