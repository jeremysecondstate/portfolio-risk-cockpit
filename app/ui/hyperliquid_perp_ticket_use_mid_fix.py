from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from app.brokers.hyperliquid.trading import HyperliquidTradingConfig
from app.ui import options_lab_extension

_DELETE_ME = "DELETE ME"


def install_hyperliquid_perp_ticket_use_mid_fix(app_cls: type[tk.Tk] | None = None) -> None:
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
                right_widget = ttk.Entry(parent, textvariable=self.stop_price_var)
            elif left_label == "Stop price" and right_label == _DELETE_ME:
                left_label = "Leverage x"
                left_widget = ttk.Entry(parent, textvariable=self.hyperliquid_leverage_var)
                right_label = "Use Mid"
                right_widget = _make_hyperliquid_use_mid_button(self, parent)
            elif left_label == "Leverage x" and right_label == "Fee % / side":
                left_label = "Attach TP/SL"
                left_widget = ttk.Checkbutton(parent, variable=self.hyperliquid_attach_tpsl_var)
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
        leverage = _optional_float(getattr(self, "hyperliquid_leverage_var").get(), default=1.0)
        fee_rate = _optional_float(getattr(self, "hyperliquid_fee_rate_var").get(), default=0.045)
        tp_price = _optional_float(getattr(self, "hyperliquid_target_price_var").get(), default=None)
        sl_price = _optional_float(getattr(self, "stop_price_var").get(), default=None)
        attach_tpsl = bool(self.hyperliquid_attach_tpsl_var.get())
        notional = ticket.size * ticket.limit_price
        margin = notional / leverage if leverage > 0 else notional
        closing_side = "SELL" if ticket.is_buy else "BUY"
        main_direction = "BUY / LONG" if ticket.is_buy else "SELL / SHORT"
        reduce_text = "reduce-only" if ticket.reduce_only else "not reduce-only"

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
            f"- Fee estimate: {fee_rate:g}% per side",
            "",
            "Protection",
        ]

        if attach_tpsl:
            if tp_price is None and sl_price is None:
                lines.append("- Attach TP/SL is on, but no TP Price or SL Price is entered yet.")
            if tp_price is not None:
                lines.append(f"- Take profit: {closing_side} reduce-only trigger near ${tp_price:,.4f}")
            if sl_price is not None:
                lines.append(f"- Stop loss: {closing_side} reduce-only trigger near ${sl_price:,.4f}")
            lines.append("- TP/SL trigger orders are reviewed here; live child-order wiring is a separate safety step.")
        else:
            lines.append("- Attach TP/SL is off. LIVE Submit is a single main order only.")

        lines.extend([
            "",
            "Environment gates",
            *config.validation_lines(),
        ])
        self.hyperliquid_status_var.set("Hyperliquid: overview ready")
        self._set_preview_text("\n".join(lines))
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: overview failed")
        messagebox.showerror("Hyperliquid overview failed", str(exc))


def _run_hyperliquid_perp_what_if_clean(self: tk.Tk) -> None:
    try:
        _ensure_perp_vars(self)
        ticket = self.parse_hyperliquid_ticket()
        leverage = _optional_float(getattr(self, "hyperliquid_leverage_var").get(), default=1.0)
        fee_rate = _optional_float(getattr(self, "hyperliquid_fee_rate_var").get(), default=0.045)
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
        notional = ticket.limit_price * ticket.size
        margin = notional / leverage if leverage > 0 else notional
        rough_liq = _rough_liquidation_price(ticket.limit_price, is_long, leverage)
        rr = _risk_reward(tp_case["net_pnl"], sl_case["net_pnl"])
        direction = "LONG" if is_long else "SHORT"
        attach = "on" if self.hyperliquid_attach_tpsl_var.get() else "off"

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
            f"Fee estimate: {fee_rate:g}% per side\n"
            f"Rough liquidation warning line: ${rough_liq:,.4f}\n\n"
            "Take Profit scenario\n"
            f"- TP Price: ${tp_price:,.4f}\n"
            f"- Gross P&L: ${tp_case['gross_pnl']:+,.2f}\n"
            f"- Estimated fees: ${tp_case['fees']:,.2f}\n"
            f"- Net gain/loss: ${tp_case['net_pnl']:+,.2f}\n"
            f"- ROI on estimated margin: {tp_case['margin_roi_percent']:+.2f}%\n\n"
            "Stop Loss scenario\n"
            f"- SL Price: ${sl_price:,.4f}\n"
            f"- Gross P&L: ${sl_case['gross_pnl']:+,.2f}\n"
            f"- Estimated fees: ${sl_case['fees']:,.2f}\n"
            f"- Net gain/loss: ${sl_case['net_pnl']:+,.2f}\n"
            f"- ROI on estimated margin: {sl_case['margin_roi_percent']:+.2f}%\n\n"
            "Setup quality\n"
            f"- Reward/risk using net P&L: {rr}\n"
            "- TP/SL fields are scenario inputs unless Attach TP/SL is on and child-order execution is wired."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: what-if failed")
        messagebox.showerror("Hyperliquid perp what-if failed", str(exc))


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
        command=lambda: options_lab_extension._run_workspace_action(
            self,
            venue="Hyperliquid",
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


def _rough_liquidation_price(entry: float, is_long: bool, leverage: float) -> float:
    if leverage <= 1:
        return 0.0 if is_long else entry * 2.0
    return entry * (1.0 - 1.0 / leverage) if is_long else entry * (1.0 + 1.0 / leverage)


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


def _walk_widgets(root: tk.Widget):
    for child in root.winfo_children():
        yield child
        yield from _walk_widgets(child)
