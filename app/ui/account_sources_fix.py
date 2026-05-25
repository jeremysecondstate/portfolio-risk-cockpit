from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Type

from app.ui.options_lab import (
    OPTION_TYPES,
    ORDER_TYPES,
    STRATEGIES,
    _analyze_scenario,
    _format_analysis,
    _init_options_vars,
    _parse_scenario,
)
from app.ui.options_lab_extension import (
    _build_hyperliquid_trading_tab,
    _build_schwab_trading_tab,
    _ensure_execution_workspace_vars,
    _first_available_command,
    _run_workspace_action,
)
from app.ui.polished_theme import _make_paned


def install_account_sources_fix(app_cls: Type[tk.Tk]) -> None:
    """Keep Account Sources state without rendering a large top strip."""
    app_cls._build_layout = _build_layout_without_account_strip  # type: ignore[method-assign]
    app_cls.capture_current_portfolio_source = _capture_current_source_portfolio  # type: ignore[attr-defined]
    app_cls.sync_options_from_active_portfolio = _sync_options_values_from_active_portfolio  # type: ignore[attr-defined]


def _build_layout_without_account_strip(self: tk.Tk) -> None:
    root = ttk.Frame(self, style="Canvas.TFrame", padding=18)
    root.pack(fill=tk.BOTH, expand=True)
    self._build_header(root)

    tabs = ttk.Notebook(root)
    tabs.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

    cockpit_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=0)
    schwab_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    hyperliquid_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    tabs.add(cockpit_tab, text="Cockpit")
    tabs.add(schwab_tab, text="Schwab Trading")
    tabs.add(hyperliquid_tab, text="Hyperliquid Trading")

    self.active_portfolio_source_var = tk.StringVar(value="Active portfolio: current cockpit source")
    self.cockpit_source_portfolio = None
    self.cockpit_source_message = "Current cockpit portfolio"

    body = _make_paned(cockpit_tab, tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True)

    left = ttk.Frame(body, style="Canvas.TFrame")
    right = ttk.Frame(body, style="Canvas.TFrame")
    body.add(left, minsize=560, stretch="always")
    body.add(right, minsize=520, stretch="always")
    self.after_idle(lambda: body.sash_place(0, max(600, int(self.winfo_width() * 0.60)), 0))

    self._build_portfolio_panel(left)
    self._build_order_panel(right)
    _ensure_execution_workspace_vars(self)
    self.after_idle(lambda: _capture_current_source_portfolio(self))

    _build_schwab_trading_tab(self, schwab_tab, tabs, schwab_tab)
    _install_schwab_options_feature(self, schwab_tab)
    _build_hyperliquid_trading_tab(self, hyperliquid_tab)


def _install_schwab_options_feature(self: tk.Tk, schwab_tab: ttk.Frame) -> None:
    """Flatten Schwab option planning into the stock/ETF ticket.

    This keeps Schwab as one workspace: no nested what-if sub-tab, no duplicate
    action row, and the option-specific fields live directly inside the stock/ETF
    ticket beside the regular Schwab controls.
    """

    _init_options_vars(self)

    ticket = _find_labelframe(schwab_tab, "Schwab Stock / ETF Ticket")
    if ticket is not None:
        _replace_delete_me_with_use_mid(self, ticket)
        _add_integrated_option_fields(self, ticket)

    for button in _walk_buttons(schwab_tab):
        label = str(button.cget("text"))
        if label == "Open Options Lab" and _inside_labelframe(button, "Schwab Trading Workspace"):
            button.configure(
                text="Sync Schwab",
                command=lambda app=self: _run_schwab_workspace_action(app, "refresh_schwab_account", "connect_schwab"),
                style="Accent.TButton",
            )
        elif label == "Order Checklist" and _inside_labelframe(button, "Schwab Actions"):
            button.configure(
                text="Options What-If",
                command=lambda app=self: _run_schwab_integrated_options_what_if(app),
                style="Accent.TButton",
            )

    _set_schwab_mode_text(
        self,
        "SCHWAB TRADING WORKSPACE\n"
        "========================\n\n"
        "Use this single tab for stocks, ETFs, Schwab previews, order history, guarded live Schwab actions, and options what-if planning.\n\n"
        "The Schwab action buttons stay above the option-planning fields so guarded live/order-history controls remain visible.\n\n"
        "Sync Schwab refreshes account balances and positions. Options What-If writes the scenario analysis here without switching into a separate sub-tab.",
    )


def _replace_delete_me_with_use_mid(self: tk.Tk, ticket: ttk.LabelFrame) -> None:
    """Replace the temporary placeholder controls with the real Schwab mid helper."""

    row_widgets = list(ticket.grid_slaves(row=3))
    placeholder_found = any(_widget_text(widget) == "DELETE ME" for widget in row_widgets)
    if not placeholder_found:
        return

    for widget in row_widgets:
        info = widget.grid_info()
        try:
            if int(info.get("column", -1)) >= 2:
                widget.destroy()
        except (TypeError, ValueError):
            continue

    ttk.Button(
        ticket,
        text="Use Mid",
        command=lambda app=self: _run_schwab_workspace_action(app, "use_schwab_mid_market", "use_selected_venue_mid_market"),
        style="Accent.TButton",
    ).grid(row=3, column=2, columnspan=2, sticky="ew", pady=7)


def _add_integrated_option_fields(self: tk.Tk, ticket: ttk.LabelFrame) -> None:
    if getattr(self, "_schwab_options_fields_integrated", False):
        return

    # The Schwab Actions frame is the primary execution control surface. Keep it
    # in its original row so all buttons remain visible, and place the optional
    # options-planning fields beneath the actions/status area.
    options = ttk.LabelFrame(ticket, text="Options Ticket Fields", style="Card.TLabelframe")
    options.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    options.columnconfigure(1, weight=1)
    options.columnconfigure(3, weight=1)

    _grid_pair(
        options,
        0,
        "Strategy",
        ttk.Combobox(options, textvariable=self.options_strategy_var, values=STRATEGIES, state="readonly"),
        "Contracts",
        ttk.Entry(options, textvariable=self.options_contracts_var),
    )
    _grid_pair(options, 1, "Expiration", ttk.Entry(options, textvariable=self.options_expiration_var), "Strike", ttk.Entry(options, textvariable=self.options_strike_var))
    _grid_pair(
        options,
        2,
        "Call / Put",
        ttk.Combobox(options, textvariable=self.options_type_var, values=OPTION_TYPES, state="readonly"),
        "Bid",
        ttk.Entry(options, textvariable=self.options_bid_var),
    )
    _grid_pair(options, 3, "Ask", ttk.Entry(options, textvariable=self.options_ask_var), "Mark", ttk.Entry(options, textvariable=self.options_mark_var))
    _grid_pair(options, 4, "Limit / Debit", ttk.Entry(options, textvariable=self.options_premium_var), "Short strike", ttk.Entry(options, textvariable=self.options_short_strike_var))
    _grid_pair(options, 5, "Credit", ttk.Entry(options, textvariable=self.options_credit_var), "Target price", ttk.Entry(options, textvariable=self.options_target_price_var))

    self._schwab_options_fields_integrated = True


def _run_schwab_workspace_action(self: tk.Tk, *command_names: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    command = _first_available_command(self, *command_names)
    if output is None:
        command()
        return
    _run_workspace_action(self, venue="Schwab", preview_widget=output, command=command)


def _run_schwab_integrated_options_what_if(self: tk.Tk) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is not None:
        self.preview_text = output

    try:
        _sync_integrated_options_values(self)
        scenario = _parse_scenario(self)
        analysis = _analyze_scenario(scenario, self)
        _set_schwab_mode_text(self, _format_analysis(scenario, analysis))
        if hasattr(self, "schwab_preview_status_var"):
            self.schwab_preview_status_var.set("Last Schwab preview: options what-if only")
    except Exception as exc:
        messagebox.showerror("Options what-if failed", str(exc))


def _sync_integrated_options_values(self: tk.Tk) -> None:
    if not hasattr(self, "options_symbol_var"):
        _init_options_vars(self)

    symbol = self.symbol_var.get().strip().upper()
    if symbol:
        self.options_symbol_var.set(symbol)

    side = self.side_var.get().strip().lower()
    self.options_action_var.set("Sell" if side == "sell" else "Buy")

    order_type = self.order_type_var.get().strip().upper()
    self.options_order_type_var.set(order_type if order_type in ORDER_TYPES else "LIMIT")

    tif = self.time_in_force_var.get().strip().lower()
    self.options_tif_var.set("GTC" if tif == "gtc" else "Day")

    quantity = self.quantity_var.get().strip()
    if quantity:
        self.options_quantity_var.set(quantity)

    stop_price = self.stop_price_var.get().strip()
    if stop_price:
        self.options_stop_price_var.set(stop_price)

    try:
        portfolio = self.broker.get_portfolio()
        self.options_cash_available_var.set(f"{portfolio.cash:.2f}")
        self.options_portfolio_value_var.set(f"{portfolio.total_value:.2f}")
        position = portfolio.get_position(symbol)
        if position is not None:
            self.options_underlying_price_var.set(f"{position.last_price:.2f}")
    except Exception:
        return


def _set_schwab_mode_text(self: tk.Tk, content: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is None:
        return
    try:
        output.configure(state=tk.NORMAL)
        output.delete("1.0", tk.END)
        output.insert(tk.END, content)
        output.configure(state=tk.DISABLED)
    except Exception:
        return


def _grid_pair(parent: ttk.Frame, row: int, label_a: str, widget_a: tk.Widget, label_b: str, widget_b: tk.Widget) -> None:
    ttk.Label(parent, text=label_a, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
    widget_a.grid(row=row, column=1, sticky="ew", padx=(0, 14), pady=5)
    ttk.Label(parent, text=label_b, style="Subtle.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 8), pady=5)
    widget_b.grid(row=row, column=3, sticky="ew", pady=5)


def _find_labelframe(root: tk.Widget, title: str) -> ttk.LabelFrame | None:
    if _widget_class(root) == "TLabelframe":
        try:
            if str(root.cget("text")) == title:
                return root  # type: ignore[return-value]
        except Exception:
            pass
    for child in root.winfo_children():
        found = _find_labelframe(child, title)
        if found is not None:
            return found
    return None


def _walk_buttons(root: tk.Widget):
    for child in root.winfo_children():
        if _widget_class(child) == "TButton":
            yield child
        yield from _walk_buttons(child)


def _inside_labelframe(widget: tk.Widget, title: str) -> bool:
    parent = widget.master
    while parent is not None:
        if _widget_class(parent) == "TLabelframe":
            try:
                if str(parent.cget("text")) == title:
                    return True
            except Exception:
                pass
        parent = parent.master
    return False


def _widget_text(widget: tk.Widget) -> str:
    try:
        return str(widget.cget("text"))
    except Exception:
        return ""


def _widget_class(widget: tk.Widget) -> str:
    try:
        return str(widget.winfo_class())
    except Exception:
        return ""


def _capture_current_source_portfolio(self: tk.Tk) -> None:
    try:
        self.cockpit_source_portfolio = self.broker.get_portfolio()
        self.cockpit_source_message = getattr(self.broker, "source_message", "Current cockpit portfolio")
        if hasattr(self, "active_portfolio_source_var"):
            self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
    except Exception:
        return


def _sync_options_values_from_active_portfolio(self: tk.Tk) -> None:
    if not hasattr(self, "options_cash_available_var"):
        return
    try:
        portfolio = self.broker.get_portfolio()
        self.options_cash_available_var.set(f"{portfolio.cash:.2f}")
        self.options_portfolio_value_var.set(f"{portfolio.total_value:.2f}")
        position = portfolio.get_position(self.options_symbol_var.get())
        if position is not None:
            self.options_underlying_price_var.set(f"{position.last_price:.2f}")
        _run_schwab_integrated_options_what_if(self)
    except Exception:
        return
