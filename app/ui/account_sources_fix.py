from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Type

from app.core.order_models import OrderSide, OrderType, TimeInForce
from app.ui import options_lab
from app.ui.options_lab_extension import (
    _build_hyperliquid_trading_tab,
    _ensure_execution_workspace_vars,
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

    _build_schwab_trading_tab(self, schwab_tab)
    _build_hyperliquid_trading_tab(self, hyperliquid_tab)


def _build_schwab_trading_tab(self: tk.Tk, parent: ttk.Frame) -> None:
    """Build one clean Schwab workspace with stock/ETF and options controls together."""

    _ensure_execution_workspace_vars(self)
    options_lab._init_options_vars(self)

    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(1, weight=1)

    header = ttk.LabelFrame(parent, text="Schwab Trading Workspace", style="Card.TLabelframe")
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(
        header,
        text=(
            "One Schwab workspace for stocks, ETFs, and option what-if planning. "
            "Options fields are built into this tab; no nested options page."
        ),
        style="Subtle.TLabel",
        wraplength=1120,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Button(
        header,
        text="Sync Schwab",
        command=lambda: _run_schwab_workspace_action(self, "refresh_schwab_account", "connect_schwab"),
        style="Accent.TButton",
    ).grid(row=0, column=1, sticky="e")

    workspace = _make_paned(parent, tk.HORIZONTAL)
    workspace.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

    ticket_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    output_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    workspace.add(ticket_shell, minsize=620, stretch="always")
    workspace.add(output_shell, minsize=520, stretch="always")
    self.after_idle(lambda: _safe_sash_place(workspace, 0, max(700, int(parent.winfo_width() * 0.52)), 0))

    ticket_stack = _make_paned(ticket_shell, tk.VERTICAL)
    ticket_stack.pack(fill=tk.BOTH, expand=True)
    stock_shell = ttk.Frame(ticket_stack, style="Canvas.TFrame")
    option_shell = ttk.Frame(ticket_stack, style="Canvas.TFrame")
    action_shell = ttk.Frame(ticket_stack, style="Canvas.TFrame")
    ticket_stack.add(stock_shell, minsize=170, stretch="never")
    ticket_stack.add(option_shell, minsize=360, stretch="always")
    ticket_stack.add(action_shell, minsize=120, stretch="never")
    self.after_idle(lambda: _place_schwab_left_sashes(ticket_stack, ticket_shell))

    _build_stock_ticket(self, stock_shell)
    _build_options_ticket(self, option_shell)
    _build_schwab_actions(self, action_shell)
    _build_schwab_output(self, output_shell)

    _set_workspace_text(
        self.schwab_trading_preview_text,
        "SCHWAB TRADING WORKSPACE\n"
        "========================\n\n"
        "Stocks/ETFs and option what-if planning now live in one Schwab tab.\n\n"
        "Use the stock ticket for Schwab previews and guarded live actions. Use the option ticket below it for bid/ask/strike what-if planning."
    )


def _safe_sash_place(pane: tk.PanedWindow, index: int, x: int, y: int) -> None:
    try:
        pane.sash_place(index, x, y)
    except tk.TclError:
        return


def _place_schwab_left_sashes(stack: tk.PanedWindow, parent: ttk.Frame) -> None:
    height = max(parent.winfo_height(), 1)
    stock_height = 178
    option_height = max(390, int(height * 0.60))
    _safe_sash_place(stack, 0, 0, stock_height)
    _safe_sash_place(stack, 1, 0, stock_height + option_height)


def _build_stock_ticket(self: tk.Tk, parent: ttk.Frame) -> None:
    ticket = ttk.LabelFrame(parent, text="Schwab Stock / ETF Ticket", style="Card.TLabelframe")
    ticket.pack(fill=tk.BOTH, expand=True)
    ticket.columnconfigure(1, weight=1)
    ticket.columnconfigure(3, weight=1)

    self._grid_row(ticket, 0, "Symbol", ttk.Entry(ticket, textvariable=self.symbol_var), "Side", ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"))
    self._grid_row(ticket, 1, "Order type", ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"), "Time", ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=[t.value for t in TimeInForce], state="readonly"))
    self._grid_row(ticket, 2, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var), "Entry / Limit", ttk.Entry(ticket, textvariable=self.limit_price_var))
    self._grid_row(ticket, 3, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var), "Cancel order ID", ttk.Entry(ticket, textvariable=self.cancel_order_id_var))


def _build_options_ticket(self: tk.Tk, parent: ttk.Frame) -> None:
    ticket = ttk.LabelFrame(parent, text="Schwab Option What-If Ticket", style="Card.TLabelframe")
    ticket.pack(fill=tk.BOTH, expand=True)
    ticket.columnconfigure(1, weight=1)
    ticket.columnconfigure(3, weight=1)

    _grid_pair(ticket, 0, "Symbol", ttk.Entry(ticket, textvariable=self.options_symbol_var), "Underlying", ttk.Entry(ticket, textvariable=self.options_underlying_price_var))
    _grid_pair(ticket, 1, "Action", ttk.Combobox(ticket, textvariable=self.options_action_var, values=options_lab.ACTIONS, state="readonly"), "Strategy", ttk.Combobox(ticket, textvariable=self.options_strategy_var, values=options_lab.STRATEGIES, state="readonly"))
    _grid_pair(ticket, 2, "Contracts", ttk.Entry(ticket, textvariable=self.options_contracts_var), "Expiration", ttk.Entry(ticket, textvariable=self.options_expiration_var))
    _grid_pair(ticket, 3, "Strike", ttk.Entry(ticket, textvariable=self.options_strike_var), "Call / Put", ttk.Combobox(ticket, textvariable=self.options_type_var, values=options_lab.OPTION_TYPES, state="readonly"))
    _grid_pair(ticket, 4, "Bid", ttk.Entry(ticket, textvariable=self.options_bid_var), "Ask", ttk.Entry(ticket, textvariable=self.options_ask_var))
    _grid_pair(ticket, 5, "Mark", ttk.Entry(ticket, textvariable=self.options_mark_var), "Limit / Debit", ttk.Entry(ticket, textvariable=self.options_premium_var))
    _grid_pair(ticket, 6, "Order type", ttk.Combobox(ticket, textvariable=self.options_order_type_var, values=options_lab.ORDER_TYPES, state="readonly"), "Time in force", ttk.Combobox(ticket, textvariable=self.options_tif_var, values=options_lab.TIME_IN_FORCE, state="readonly"))
    _grid_pair(ticket, 7, "Short strike", ttk.Entry(ticket, textvariable=self.options_short_strike_var), "Credit", ttk.Entry(ticket, textvariable=self.options_credit_var))
    _grid_pair(ticket, 8, "Stop price", ttk.Entry(ticket, textvariable=self.options_stop_price_var), "Target price", ttk.Entry(ticket, textvariable=self.options_target_price_var))
    _grid_pair(ticket, 9, "RSI", ttk.Entry(ticket, textvariable=self.options_rsi_var), "ATR %", ttk.Entry(ticket, textvariable=self.options_atr_var))


def _build_schwab_actions(self: tk.Tk, parent: ttk.Frame) -> None:
    actions = ttk.LabelFrame(parent, text="Schwab Actions", style="Card.TLabelframe")
    actions.pack(fill=tk.BOTH, expand=True)
    for column in range(4):
        actions.columnconfigure(column, weight=1, uniform="schwab_actions")

    _add_workspace_button(actions, row=0, column=0, text="Preview Stock", command=lambda: _run_schwab_workspace_action(self, "run_schwab_preview"), style="Accent.TButton")
    _add_workspace_button(actions, row=0, column=1, text="Option What-If", command=lambda: options_lab.run_options_what_if(self), style="Accent.TButton")
    _add_workspace_button(actions, row=0, column=2, text="Load Option Technicals", command=lambda: _load_schwab_technicals(self))
    _add_workspace_button(actions, row=0, column=3, text="Tech Analysis", command=lambda: _run_schwab_workspace_action(self, "show_technical_analysis"))

    _add_workspace_button(actions, row=1, column=0, text="Connect", command=lambda: _run_schwab_workspace_action(self, "connect_schwab", "run_schwab_preview"))
    _add_workspace_button(actions, row=1, column=1, text="Recent Orders", command=lambda: _run_schwab_workspace_action(self, "load_selected_recent_orders", "load_schwab_open_orders"))
    _add_workspace_button(actions, row=1, column=2, text="Open Only", command=lambda: _run_schwab_workspace_action(self, "load_selected_open_orders_only", "load_schwab_open_orders_only"))
    _add_workspace_button(actions, row=1, column=3, text="Cancel Order", command=lambda: _run_schwab_workspace_action(self, "cancel_selected_order", "show_cancel_order_placeholder"), style="Danger.TButton")

    _add_workspace_button(actions, row=2, column=0, text="LIVE Submit", command=lambda: _run_schwab_workspace_action(self, "submit_selected_venue", "submit_live_schwab_order_guarded"), style="Danger.TButton", columnspan=2)
    _add_workspace_button(actions, row=2, column=2, text="Position Size", command=lambda: _run_schwab_workspace_action(self, "show_position_size"), columnspan=2)


def _build_schwab_output(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(3, weight=1)

    metrics = ttk.LabelFrame(parent, text="Option Risk + Margin", style="Card.TLabelframe")
    metrics.grid(row=0, column=0, sticky="ew")
    metrics.columnconfigure((0, 1, 2), weight=1)
    self.options_max_loss_label = _metric(metrics, "Max Loss", 0, 0)
    self.options_max_profit_label = _metric(metrics, "Max Profit", 0, 1)
    self.options_breakeven_label = _metric(metrics, "Breakeven", 0, 2)
    self.options_margin_label = _metric(metrics, "BP Effect", 2, 0)
    self.options_portfolio_risk_label = _metric(metrics, "Portfolio Risk", 2, 1)
    self.options_reward_risk_label = _metric(metrics, "Reward/Risk", 2, 2)

    summary = ttk.LabelFrame(parent, text="Selected Option Order", style="Card.TLabelframe")
    summary.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    summary.columnconfigure(0, weight=1)
    self.options_order_summary_label = ttk.Label(summary, text="--", style="Subtle.TLabel", wraplength=780)
    self.options_order_summary_label.grid(row=0, column=0, sticky="w")

    status = ttk.Frame(parent, style="Panel.TFrame")
    status.grid(row=2, column=0, sticky="ew", pady=(10, 0))
    status.columnconfigure((0, 1, 2), weight=1)
    ttk.Label(status, textvariable=self.schwab_status_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Label(status, textvariable=self.schwab_preview_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Label(status, textvariable=self.schwab_verification_status_var, style="Chip.TLabel").grid(row=0, column=2, sticky="ew")

    output = ttk.LabelFrame(parent, text="Analysis + Instructions", style="Card.TLabelframe")
    output.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
    output.rowconfigure(0, weight=1)
    output.columnconfigure(0, weight=1)
    self.schwab_trading_preview_text = _workspace_text(output)
    self.preview_text = self.schwab_trading_preview_text
    self.options_output_text = self.schwab_trading_preview_text
    options_lab.run_options_what_if(self)


def _workspace_text(parent: ttk.Frame) -> tk.Text:
    text = tk.Text(
        parent,
        height=18,
        wrap=tk.WORD,
        font=("Cascadia Mono", 10),
        padx=14,
        pady=12,
        relief=tk.FLAT,
        borderwidth=0,
        background="#0b1120",
        foreground="#dbeafe",
        insertbackground="#dbeafe",
        selectbackground="#1d4ed8",
    )
    text.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=text.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=scrollbar.set)
    return text


def _set_workspace_text(widget: tk.Text, content: str) -> None:
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, content)
    widget.configure(state=tk.DISABLED)


def _run_schwab_workspace_action(self: tk.Tk, *command_names: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    command = _first_available_command(self, *command_names)
    if output is None:
        command()
        return

    _ensure_execution_workspace_vars(self)
    self.trade_venue_var.set("Schwab")
    if hasattr(self, "on_trading_venue_changed"):
        try:
            self.on_trading_venue_changed()
        except Exception:
            pass
    self.preview_text = output
    command()


def _first_available_command(self: tk.Tk, *names: str) -> Callable[[], None]:
    for name in names:
        command = getattr(self, name, None)
        if callable(command):
            return command

    def _missing() -> None:
        messagebox.showinfo("Action unavailable", f"None of these actions are installed yet: {', '.join(names)}")

    return _missing


def _load_schwab_technicals(self: tk.Tk) -> None:
    command = getattr(self, "load_options_lab_technical_context", None)
    if callable(command):
        command()
    else:
        messagebox.showinfo("Action unavailable", "Schwab technical loading is not installed yet.")


def _grid_pair(parent: ttk.Frame, row: int, label_a: str, widget_a: tk.Widget, label_b: str, widget_b: tk.Widget) -> None:
    ttk.Label(parent, text=label_a, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
    widget_a.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=4)
    ttk.Label(parent, text=label_b, style="Subtle.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 8), pady=4)
    widget_b.grid(row=row, column=3, sticky="ew", pady=4)


def _add_workspace_button(
    parent: ttk.Frame,
    *,
    row: int,
    column: int,
    text: str,
    command: Callable[[], None],
    style: str = "TButton",
    columnspan: int = 1,
) -> None:
    ttk.Button(parent, text=text, command=command, style=style).grid(
        row=row,
        column=column,
        columnspan=columnspan,
        sticky="ew",
        padx=(0 if column == 0 else 6, 0),
        pady=(0 if row == 0 else 8, 0),
    )


def _metric(parent: ttk.Frame, title: str, row: int, column: int) -> ttk.Label:
    ttk.Label(parent, text=title, style="Subtle.TLabel").grid(row=row, column=column, sticky="w")
    label = ttk.Label(parent, text="--", font=("Segoe UI", 12, "bold"))
    label.grid(row=row + 1, column=column, sticky="w", pady=(2, 8))
    return label


def _build_options_lab_market_loader(self: tk.Tk, parent: ttk.Frame) -> None:
    return


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
        options_lab.run_options_what_if(self)
    except Exception:
        return
