from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Type


def install_thesis_option_ticket_extension(app_cls: Type[tk.Tk]) -> None:
    """Allow the latest unified thesis option idea to fill the options ticket."""

    app_cls.use_current_thesis_option_ticket = _use_current_thesis_option_ticket  # type: ignore[attr-defined]


def _use_current_thesis_option_ticket(self: tk.Tk) -> None:
    ticket = getattr(self, "current_thesis_option_ticket", None)
    if ticket is None:
        messagebox.showerror(
            "No thesis option available",
            "Run Tech Analysis after loading the option chain first, then use this button to fill the thesis option ticket.",
        )
        return

    _ensure_options_vars_exist(self)

    self.symbol_var.set(ticket.symbol)
    self.options_symbol_var.set(ticket.symbol)
    self.options_strategy_var.set(ticket.strategy)
    self.options_action_var.set(ticket.action)
    self.options_expiration_var.set(ticket.expiration)
    self.options_type_var.set(ticket.option_type)
    self.options_order_type_var.set("LIMIT")
    self.options_tif_var.set("Day")
    self.options_contracts_var.set(str(ticket.contracts))
    self.options_strike_var.set(_format_number(ticket.strike))
    self.options_short_strike_var.set(_format_number(ticket.short_strike))
    self.options_bid_var.set(_format_optional_number(ticket.bid))
    self.options_ask_var.set(_format_optional_number(ticket.ask))
    self.options_mark_var.set(_format_optional_number(ticket.mark))
    self.options_premium_var.set(_format_number(ticket.premium))
    self.options_credit_var.set(_format_number(ticket.credit))

    _fill_context_defaults(self)

    if hasattr(self, "schwab_option_chain_status_var"):
        self.schwab_option_chain_status_var.set("Thesis option filled into ticket")
    if hasattr(self, "schwab_preview_status_var"):
        self.schwab_preview_status_var.set("Last Schwab preview: thesis option filled only")

    _append_ticket_fill_note(self, ticket.summary)


def _ensure_options_vars_exist(self: tk.Tk) -> None:
    # Options Lab normally creates these. Keep this defensive so the button never
    # crashes if the layout order changes.
    defaults = {
        "options_symbol_var": "",
        "options_strategy_var": "",
        "options_action_var": "",
        "options_expiration_var": "",
        "options_type_var": "",
        "options_order_type_var": "LIMIT",
        "options_tif_var": "Day",
        "options_underlying_price_var": "",
        "options_quantity_var": "100",
        "options_contracts_var": "1",
        "options_strike_var": "",
        "options_short_strike_var": "",
        "options_bid_var": "",
        "options_ask_var": "",
        "options_mark_var": "",
        "options_premium_var": "",
        "options_credit_var": "0",
        "options_portfolio_value_var": "",
        "options_cash_available_var": "",
        "options_initial_margin_var": "50",
        "options_maintenance_margin_var": "30",
        "options_stop_price_var": "",
        "options_target_price_var": "",
        "options_atr_var": "0",
        "options_rsi_var": "0",
        "options_sma_20_var": "0",
        "options_sma_50_var": "0",
        "options_sma_200_var": "0",
        "options_support_var": "0",
        "options_resistance_var": "0",
    }
    for name, value in defaults.items():
        if not hasattr(self, name):
            setattr(self, name, tk.StringVar(value=value))


def _fill_context_defaults(self: tk.Tk) -> None:
    underlying = _first_float(
        getattr(self, "limit_price_var", tk.StringVar(value="")).get(),
        getattr(self, "estimated_price_var", tk.StringVar(value="")).get(),
        getattr(self, "options_underlying_price_var", tk.StringVar(value="")).get(),
    )
    if underlying is not None and underlying > 0:
        self.options_underlying_price_var.set(_format_number(underlying))

    if not self.options_quantity_var.get().strip():
        self.options_quantity_var.set("100")
    if not self.options_initial_margin_var.get().strip():
        self.options_initial_margin_var.set("50")
    if not self.options_maintenance_margin_var.get().strip():
        self.options_maintenance_margin_var.set("30")

    try:
        portfolio = self.broker.get_portfolio()
        self.options_cash_available_var.set(f"{portfolio.cash:.2f}")
        self.options_portfolio_value_var.set(f"{portfolio.total_value:.2f}")
    except Exception:
        pass


def _append_ticket_fill_note(self: tk.Tk, summary: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None) or getattr(self, "preview_text", None)
    if output is None:
        return
    try:
        output.configure(state=tk.NORMAL)
        output.insert(
            tk.END,
            "\n\nTICKET FILLED FROM THESIS\n"
            "=========================\n"
            f"{summary}\n"
            "This filled the options ticket only. No order was submitted or previewed.\n",
        )
        output.configure(state=tk.DISABLED)
        output.see(tk.END)
    except Exception:
        return


def _first_float(*values: str) -> float | None:
    for value in values:
        try:
            cleaned = str(value).strip().replace(",", "")
            if cleaned:
                return float(cleaned)
        except ValueError:
            continue
    return None


def _format_number(value: float) -> str:
    formatted = f"{value:.2f}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _format_optional_number(value: float | None) -> str:
    return "" if value is None else _format_number(value)
