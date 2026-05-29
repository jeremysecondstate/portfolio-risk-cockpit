from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Type

from app.analytics.technical_analysis import (
    analyze_candles,
    candles_from_price_history,
    simple_moving_average,
)
from app.analytics.trade_setup import calculate_support_resistance
from app.brokers.hyperliquid.client import HyperliquidInfoClient
from app.brokers.hyperliquid.trading import normalize_hyperliquid_coin
from app.core.order_models import OrderSide, OrderType, TimeInForce
from app.ui.options_lab import build_options_lab_tab, run_options_what_if
from app.ui.polished_theme import _make_paned


def install_options_lab_extension(app_cls: Type[tk.Tk]) -> None:
    """Add the Options What-If Lab and Schwab/Hyperliquid cockpit layout."""

    app_cls._build_layout = _build_layout_with_options_lab  # type: ignore[method-assign]
    app_cls.load_options_lab_technical_context = _load_options_lab_technical_context  # type: ignore[attr-defined]
    app_cls.use_current_cockpit_source_portfolio = _use_current_cockpit_source_portfolio  # type: ignore[attr-defined]
    app_cls.use_hyperliquid_mid_market = _use_hyperliquid_mid_market  # type: ignore[attr-defined]
    app_cls.run_hyperliquid_perp_what_if = _run_hyperliquid_perp_what_if  # type: ignore[attr-defined]


def _build_layout_with_options_lab(self: tk.Tk) -> None:
    root = ttk.Frame(self, style="Canvas.TFrame", padding=18)
    root.pack(fill=tk.BOTH, expand=True)

    self._build_header(root)

    tabs = ttk.Notebook(root)
    tabs.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

    cockpit_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=0)
    schwab_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    hyperliquid_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    options_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    tabs.add(cockpit_tab, text="Cockpit")
    tabs.add(schwab_tab, text="Schwab Trading")
    tabs.add(hyperliquid_tab, text="Hyperliquid Trading")
    tabs.add(options_tab, text="Options What-If Lab")

    self.active_portfolio_source_var = tk.StringVar(value="Active portfolio: current cockpit source")
    self.cockpit_source_portfolio = None
    self.cockpit_source_message = "Current cockpit portfolio"

    _build_account_sources_panel(self, cockpit_tab)

    body = _make_paned(cockpit_tab, tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

    left = ttk.Frame(body, style="Canvas.TFrame")
    right = ttk.Frame(body, style="Canvas.TFrame")
    body.add(left, minsize=560, stretch="always")
    body.add(right, minsize=520, stretch="always")
    self.after_idle(lambda: body.sash_place(0, max(600, int(self.winfo_width() * 0.60)), 0))

    self._build_portfolio_panel(left)
    self._build_order_panel(right)
    _ensure_execution_workspace_vars(self)
    self.after_idle(lambda: _capture_current_source_portfolio(self))

    _build_schwab_trading_tab(self, schwab_tab, tabs, options_tab)
    _build_hyperliquid_trading_tab(self, hyperliquid_tab)

    build_options_lab_tab(self, options_tab)
    _build_options_lab_market_loader(self, options_tab)


def _ensure_execution_workspace_vars(self: tk.Tk) -> None:
    """Keep the dedicated venue tabs safe even if extensions load in a different order."""
    if not hasattr(self, "trade_venue_var"):
        self.trade_venue_var = tk.StringVar(value="Schwab")
    if not hasattr(self, "hyperliquid_coin_var"):
        self.hyperliquid_coin_var = tk.StringVar(value="")
    if not hasattr(self, "hyperliquid_tif_var"):
        self.hyperliquid_tif_var = tk.StringVar(value="Gtc")
    if not hasattr(self, "hyperliquid_reduce_only_var"):
        self.hyperliquid_reduce_only_var = tk.BooleanVar(value=False)
    if not hasattr(self, "hyperliquid_status_var"):
        self.hyperliquid_status_var = tk.StringVar(value="Hyperliquid: preview only")
    if not hasattr(self, "hyperliquid_target_price_var"):
        self.hyperliquid_target_price_var = tk.StringVar(value="")
    if not hasattr(self, "hyperliquid_bad_price_var"):
        self.hyperliquid_bad_price_var = tk.StringVar(value="")
    if not hasattr(self, "hyperliquid_leverage_var"):
        self.hyperliquid_leverage_var = tk.StringVar(value="1")
    if not hasattr(self, "hyperliquid_fee_rate_var"):
        self.hyperliquid_fee_rate_var = tk.StringVar(value="0.045")


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
    text.pack(fill=tk.BOTH, expand=True)
    return text


def _set_workspace_text(widget: tk.Text, content: str) -> None:
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, content)
    widget.configure(state=tk.DISABLED)


def _first_available_command(self: tk.Tk, *names: str) -> Callable[[], None]:
    for name in names:
        command = getattr(self, name, None)
        if callable(command):
            return command

    def _missing() -> None:
        messagebox.showinfo(
            "Action unavailable",
            f"None of these actions are installed yet: {', '.join(names)}",
        )

    return _missing


def _run_workspace_action(
    self: tk.Tk,
    *,
    venue: str,
    preview_widget: tk.Text,
    command: Callable[[], None],
) -> None:
    _ensure_execution_workspace_vars(self)
    self.trade_venue_var.set(venue)
    if hasattr(self, "on_trading_venue_changed"):
        try:
            self.on_trading_venue_changed()
        except Exception:
            pass
    self.preview_text = preview_widget
    command()


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
        padx=(0 if column == 0 else 4, 0),
        pady=(0 if row == 0 else 8, 8),
        ipady=2,
    )


def _build_schwab_trading_tab(
    self: tk.Tk,
    parent: ttk.Frame,
    tabs: ttk.Notebook,
    options_tab: ttk.Frame,
) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(1, weight=1)

    header = ttk.LabelFrame(parent, text="Schwab Trading Workspace", style="Card.TLabelframe")
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(
        header,
        text=(
            "Dedicated Schwab execution surface for stocks, ETFs, and option planning. "
            "The original Cockpit tab is unchanged as a fallback."
        ),
        style="Subtle.TLabel",
        wraplength=1120,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Button(
        header,
        text="Open Options Lab",
        command=lambda: tabs.select(options_tab),
        style="Accent.TButton",
    ).grid(row=0, column=1, sticky="e")

    workspace = _make_paned(parent, tk.HORIZONTAL)
    workspace.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

    ticket_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    output_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    workspace.add(ticket_shell, minsize=540, stretch="never")
    workspace.add(output_shell, minsize=520, stretch="always")

    ticket = ttk.LabelFrame(ticket_shell, text="Schwab Stock / ETF Ticket", style="Card.TLabelframe")
    ticket.pack(fill=tk.BOTH, expand=True)
    ticket.columnconfigure(1, weight=1)
    ticket.columnconfigure(3, weight=1)

    self._grid_row(
        ticket,
        0,
        "Symbol",
        ttk.Entry(ticket, textvariable=self.symbol_var),
        "Side",
        ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"),
    )
    self._grid_row(
        ticket,
        1,
        "Order type",
        ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"),
        "Time",
        ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=[t.value for t in TimeInForce], state="readonly"),
    )
    self._grid_row(ticket, 2, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var), "Entry / Limit", ttk.Entry(ticket, textvariable=self.limit_price_var))
    self._grid_row(ticket, 3, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var), "DELETE ME", ttk.Entry(ticket, textvariable=self.confirmation_var))
    ttk.Label(ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=4, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    schwab_output_frame = ttk.LabelFrame(output_shell, text="Schwab Analysis + Order Output", style="Card.TLabelframe")
    schwab_output_frame.pack(fill=tk.BOTH, expand=True)
    self.schwab_trading_preview_text = _workspace_text(schwab_output_frame)

    actions = ttk.LabelFrame(ticket, text="Schwab Actions", style="Card.TLabelframe")
    actions.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    for column in range(3):
        actions.columnconfigure(column, weight=1, uniform="schwab_actions")

    def schwab_action(*names: str) -> Callable[[], None]:
        return lambda: _run_workspace_action(
            self,
            venue="Schwab",
            preview_widget=self.schwab_trading_preview_text,
            command=_first_available_command(self, *names),
        )

    _add_workspace_button(actions, row=0, column=0, text="Connect Schwab", command=schwab_action("connect_schwab", "run_schwab_preview"))
    _add_workspace_button(actions, row=0, column=1, text="Refresh Account", command=schwab_action("refresh_schwab_account", "refresh_portfolio"))
    _add_workspace_button(actions, row=0, column=2, text="Tech Analysis", command=schwab_action("show_technical_analysis"))
    _add_workspace_button(actions, row=1, column=0, text="Preview Risk", command=schwab_action("preview_order"), style="Accent.TButton")
    _add_workspace_button(actions, row=1, column=1, text="Preview Schwab Order", command=schwab_action("run_schwab_preview"))
    _add_workspace_button(actions, row=1, column=2, text="Position Size", command=schwab_action("show_position_size"))
    _add_workspace_button(actions, row=2, column=0, text="Recent Orders", command=schwab_action("load_selected_recent_orders", "load_schwab_open_orders"))
    _add_workspace_button(actions, row=2, column=1, text="Open Only", command=schwab_action("load_selected_open_orders_only", "load_schwab_open_orders_only"))
    _add_workspace_button(actions, row=2, column=2, text="Order Checklist", command=schwab_action("show_manual_checklist"))
    _add_workspace_button(actions, row=3, column=0, text="Cancel Order", command=schwab_action("cancel_selected_order", "show_cancel_order_placeholder"), style="Danger.TButton")
    _add_workspace_button(actions, row=3, column=1, text="Live Safety", command=schwab_action("show_live_submit_safety_review"))
    _add_workspace_button(actions, row=3, column=2, text="LIVE Submit", command=schwab_action("submit_selected_venue", "submit_live_schwab_order_guarded"), style="Danger.TButton")

    status = ttk.Frame(ticket, style="Panel.TFrame")
    status.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    status.columnconfigure((0, 1, 2), weight=1)
    ttk.Label(status, textvariable=self.schwab_status_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Label(status, textvariable=self.schwab_preview_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Label(status, textvariable=self.schwab_verification_status_var, style="Chip.TLabel").grid(row=0, column=2, sticky="ew")

    _set_workspace_text(
        self.schwab_trading_preview_text,
        "SCHWAB TRADING WORKSPACE\n"
        "========================\n\n"
        "Use this tab for stocks, ETFs, Schwab previews, order history, and guarded live Schwab actions.\n\n"
        "Options still live in the Options What-If Lab; use the button above when the weekly setup needs calls/puts instead of shares.",
    )


def _build_hyperliquid_trading_tab(self: tk.Tk, parent: ttk.Frame) -> None:
    _ensure_execution_workspace_vars(self)
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(1, weight=1)

    header = ttk.LabelFrame(parent, text="Hyperliquid Trading Workspace", style="Card.TLabelframe")
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(
        header,
        text=(
            "Dedicated Hyperliquid execution surface for spot and perp tickets. "
            "This keeps crypto controls away from Schwab stock and option workflows."
        ),
        style="Subtle.TLabel",
        wraplength=1120,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Button(
        header,
        text="Sync Hyperliquid",
        command=lambda: _run_workspace_action(
            self,
            venue="Hyperliquid",
            preview_widget=self.hyperliquid_trading_preview_text,
            command=_first_available_command(self, "sync_hyperliquid_account"),
        ),
        style="Accent.TButton",
    ).grid(row=0, column=1, sticky="e")

    workspace = _make_paned(parent, tk.HORIZONTAL)
    workspace.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

    ticket_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    output_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    workspace.add(ticket_shell, minsize=760, stretch="always")
    workspace.add(output_shell, minsize=520, stretch="always")

    hyperliquid_output_frame = ttk.LabelFrame(output_shell, text="Hyperliquid Analysis + Order Output", style="Card.TLabelframe")
    hyperliquid_output_frame.pack(fill=tk.BOTH, expand=True)
    self.hyperliquid_trading_preview_text = _workspace_text(hyperliquid_output_frame)

    def hyperliquid_action(*names: str) -> Callable[[], None]:
        return lambda: _run_workspace_action(
            self,
            venue="Hyperliquid",
            preview_widget=self.hyperliquid_trading_preview_text,
            command=_first_available_command(self, *names),
        )

    from app.ui import hyperliquid_trading_extension as hyperliquid_ui

    hyperliquid_ui._ensure_hyperliquid_vars(self)
    hyperliquid_ui._configure_compact_ticket_styles(self)

    tickets = ttk.Frame(ticket_shell, style="Canvas.TFrame")
    tickets.pack(fill=tk.BOTH, expand=True)
    for column in range(2):
        tickets.columnconfigure(column, weight=1, uniform="hyperliquid_tickets")
    tickets.rowconfigure(0, weight=1)

    spot_ticket = ttk.LabelFrame(tickets, text="Hyperliquid Spot Ticket", style="Card.TLabelframe")
    spot_ticket.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    spot_ticket.columnconfigure(1, weight=1)
    spot_ticket.columnconfigure(3, weight=1)

    perp_ticket = ttk.LabelFrame(tickets, text="Hyperliquid Perp Ticket", style="Card.TLabelframe")
    perp_ticket.grid(row=0, column=1, sticky="nsew")
    perp_ticket.columnconfigure(1, weight=1)
    perp_ticket.columnconfigure(3, weight=1)

    self._grid_row(spot_ticket, 0, "Market", ttk.Entry(spot_ticket, textvariable=self.symbol_var), "HL Coin", ttk.Entry(spot_ticket, textvariable=self.hyperliquid_coin_var))
    self._grid_row(
        spot_ticket,
        1,
        "Side",
        ttk.Combobox(spot_ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"),
        "Order type",
        ttk.Combobox(spot_ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"),
    )
    hyperliquid_ui._grid_hyperliquid_quantity_row(spot_ticket, self, 2)
    hyperliquid_ui._grid_hyperliquid_size_controls(spot_ticket, self, 3)
    self._grid_row(
        spot_ticket,
        4,
        "Stop price",
        ttk.Entry(spot_ticket, textvariable=self.stop_price_var),
        "Use Mid",
        ttk.Button(spot_ticket, text="Use Mid", command=hyperliquid_action("use_hyperliquid_cockpit_spot_mid_market"), style="Accent.TButton"),
    )
    self._grid_row(
        spot_ticket,
        5,
        "HL TIF",
        ttk.Combobox(spot_ticket, textvariable=self.hyperliquid_tif_var, values=["Alo", "Ioc", "Gtc"], state="readonly"),
        "",
        ttk.Frame(spot_ticket, style="Canvas.TFrame"),
    )
    ttk.Label(spot_ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=6, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(spot_ticket, textvariable=self.cancel_order_id_var).grid(row=6, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    spot_actions = ttk.LabelFrame(spot_ticket, text="Spot Actions", style="Card.TLabelframe")
    spot_actions.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    for column in range(3):
        spot_actions.columnconfigure(column, weight=1, uniform="hyperliquid_spot_actions")

    _add_workspace_button(spot_actions, row=0, column=0, text="Use Mid", command=hyperliquid_action("use_hyperliquid_cockpit_spot_mid_market"), style="Accent.TButton")
    _add_workspace_button(spot_actions, row=0, column=1, text="Preview Spot", command=hyperliquid_action("preview_hyperliquid_spot_ticket"), style="Accent.TButton")
    _add_workspace_button(spot_actions, row=0, column=2, text="Open Orders", command=hyperliquid_action("load_hyperliquid_open_orders"))
    _add_workspace_button(spot_actions, row=1, column=0, text="Edit Order", command=hyperliquid_action("show_hyperliquid_order_edit_dialog"))
    _add_workspace_button(spot_actions, row=1, column=1, text="Cancel Order", command=hyperliquid_action("cancel_hyperliquid_order_guarded"), style="Danger.TButton")
    _add_workspace_button(spot_actions, row=1, column=2, text="LIVE Submit", command=hyperliquid_action("show_hyperliquid_spot_live_submit_safety_review"), style="Danger.TButton")

    ticket = perp_ticket
    self._grid_row(ticket, 0, "Coin", ttk.Entry(ticket, textvariable=self.hyperliquid_coin_var), "Symbol", ttk.Entry(ticket, textvariable=self.symbol_var))
    self._grid_row(
        ticket,
        1,
        "Direction",
        ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"),
        "Order type",
        ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"),
    )
    self._grid_row(ticket, 2, "Size", ttk.Entry(ticket, textvariable=self.quantity_var), "Entry / Limit", ttk.Entry(ticket, textvariable=self.limit_price_var))
    self._grid_row(ticket, 3, "Target price", ttk.Entry(ticket, textvariable=self.hyperliquid_target_price_var), "Pain price", ttk.Entry(ticket, textvariable=self.hyperliquid_bad_price_var))
    self._grid_row(ticket, 4, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var), "DELETE ME", ttk.Entry(ticket, textvariable=self.confirmation_var))
    self._grid_row(ticket, 5, "HL TIF", ttk.Combobox(ticket, textvariable=self.hyperliquid_tif_var, values=["Alo", "Ioc", "Gtc"], state="readonly"), "Reduce-only", ttk.Checkbutton(ticket, variable=self.hyperliquid_reduce_only_var),
    )
    self._grid_row(ticket, 6, "Leverage x", ttk.Entry(ticket, textvariable=self.hyperliquid_leverage_var), "Fee % / side", ttk.Entry(ticket, textvariable=self.hyperliquid_fee_rate_var))
    ttk.Label(ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=7, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=7, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    actions = ttk.LabelFrame(ticket, text="Hyperliquid Actions", style="Card.TLabelframe")
    actions.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    for column in range(3):
        actions.columnconfigure(column, weight=1, uniform="hyperliquid_actions")

    _add_workspace_button(actions, row=0, column=0, text="Use Mid", command=hyperliquid_action("use_hyperliquid_mid_market"), style="Accent.TButton")
    _add_workspace_button(actions, row=0, column=1, text="Perp What-If", command=hyperliquid_action("run_hyperliquid_perp_what_if"), style="Accent.TButton")
    _add_workspace_button(actions, row=0, column=2, text="Preview Perp Ticket", command=hyperliquid_action("preview_hyperliquid_ticket", "preview_order"))
    _add_workspace_button(actions, row=1, column=0, text="Sync Account", command=hyperliquid_action("sync_hyperliquid_account"))
    _add_workspace_button(actions, row=1, column=1, text="Tech Analysis", command=hyperliquid_action("show_technical_analysis"))
    _add_workspace_button(actions, row=1, column=2, text="Position Size", command=hyperliquid_action("show_position_size"))
    _add_workspace_button(actions, row=2, column=0, text="Recent Orders", command=hyperliquid_action("load_selected_recent_orders", "load_hyperliquid_open_orders"))
    _add_workspace_button(actions, row=2, column=1, text="Open Only", command=hyperliquid_action("load_selected_open_orders_only", "load_hyperliquid_open_orders"))
    _add_workspace_button(actions, row=2, column=2, text="Live Safety", command=hyperliquid_action("show_hyperliquid_live_submit_safety_review"))
    _add_workspace_button(actions, row=3, column=0, text="Cancel Order", command=hyperliquid_action("cancel_selected_order", "cancel_hyperliquid_order_guarded"), style="Danger.TButton")
    _add_workspace_button(actions, row=3, column=1, text="LIVE Submit", command=hyperliquid_action("submit_selected_venue"), style="Danger.TButton", columnspan=2)

    status = ttk.Frame(ticket, style="Panel.TFrame")
    status.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    status.columnconfigure((0, 1, 2), weight=1)
    ttk.Label(status, textvariable=self.hyperliquid_status_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Label(status, textvariable=self.schwab_verification_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Label(status, text="Venue locked: Hyperliquid", style="Chip.TLabel").grid(row=0, column=2, sticky="ew")

    _set_workspace_text(
        self.hyperliquid_trading_preview_text,
        "HYPERLIQUID TRADING WORKSPACE\n"
        "=============================\n\n"
        "Use Mid pulls the current Hyperliquid allMids price into Entry / Limit.\n\n"
        "Perp What-If compares your target and pain prices against the entry. It estimates gross P&L, fees, net P&L, account-risk-style ROI on margin, and a rough liquidation line.\n\n"
        "The rough liquidation line ignores maintenance margin, funding, slippage, partial fills, and account-wide margin. Treat it as a planning warning, not an exchange quote.",
    )


def _hyperliquid_selected_coin(self: tk.Tk) -> str:
    _ensure_execution_workspace_vars(self)
    raw = self.hyperliquid_coin_var.get().strip() or self.symbol_var.get().strip()
    coin = normalize_hyperliquid_coin(raw)
    self.hyperliquid_coin_var.set(coin)
    self.symbol_var.set(coin)
    return coin


def _lookup_hyperliquid_mid(coin: str) -> float:
    all_mids = HyperliquidInfoClient(timeout_seconds=10).post_info({"type": "allMids"})
    if not isinstance(all_mids, dict):
        raise RuntimeError("Hyperliquid allMids returned an unexpected response.")
    candidates = (coin, f"{coin}-PERP", f"{coin}/USDC")
    upper_mids = {str(key).upper(): value for key, value in all_mids.items()}
    for candidate in candidates:
        raw = upper_mids.get(candidate.upper())
        if raw is None:
            continue
        price = _to_float(raw)
        if price is not None and price > 0:
            return price
    raise RuntimeError(f"No Hyperliquid mid-market price found for {coin}. Try HYPE, BTC, ETH, or another listed coin.")


def _use_hyperliquid_mid_market(self: tk.Tk) -> None:
    try:
        coin = _hyperliquid_selected_coin(self)
        mid = _lookup_hyperliquid_mid(coin)
        self.limit_price_var.set(f"{mid:.4f}".rstrip("0").rstrip("."))
        self.hyperliquid_status_var.set(f"Hyperliquid: {coin} mid ${mid:,.4f}")
        self._set_preview_text(
            "HYPERLIQUID MID-MARKET PRICE\n"
            "============================\n\n"
            f"Coin: {coin}\n"
            f"Mid-market price: ${mid:,.4f}\n\n"
            "Entry / Limit was updated from Hyperliquid allMids. No order was submitted."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: mid failed")
        messagebox.showerror("Hyperliquid mid-market lookup failed", str(exc))


def _run_hyperliquid_perp_what_if(self: tk.Tk) -> None:
    try:
        coin = _hyperliquid_selected_coin(self)
        side = self.side_var.get().strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("Direction must be buy or sell.")
        size = _required_float(self.quantity_var.get(), "Size")
        entry = _required_float(self.limit_price_var.get(), "Entry / Limit")
        leverage = _optional_float(self.hyperliquid_leverage_var.get(), default=1.0)
        fee_rate_percent = _optional_float(self.hyperliquid_fee_rate_var.get(), default=0.045)
        if size <= 0 or entry <= 0:
            raise ValueError("Size and Entry / Limit must be positive.")
        if leverage <= 0:
            raise ValueError("Leverage must be positive.")
        if fee_rate_percent < 0:
            raise ValueError("Fee % / side cannot be negative.")

        is_long = side == "buy"
        default_target = entry * (1.05 if is_long else 0.95)
        default_pain = entry * (0.97 if is_long else 1.03)
        target = _optional_float(self.hyperliquid_target_price_var.get(), default=default_target)
        pain = _optional_float(self.hyperliquid_bad_price_var.get() or self.stop_price_var.get(), default=default_pain)
        self.hyperliquid_target_price_var.set(_format_price(target))
        self.hyperliquid_bad_price_var.set(_format_price(pain))

        target_case = _perp_case(entry, target, size, is_long, leverage, fee_rate_percent)
        pain_case = _perp_case(entry, pain, size, is_long, leverage, fee_rate_percent)
        breakeven = _breakeven_price(entry, is_long, fee_rate_percent)
        notional = entry * size
        margin = notional / leverage
        rough_liq = _rough_liquidation_price(entry, is_long, leverage)
        rr = _risk_reward(target_case["net_pnl"], pain_case["net_pnl"])
        direction = "LONG" if is_long else "SHORT"
        favorable_word = "above" if is_long else "below"
        pain_word = "below" if is_long else "above"

        self.hyperliquid_status_var.set("Hyperliquid: what-if ready")
        self._set_preview_text(
            "HYPERLIQUID PERP WHAT-IF\n"
            "========================\n\n"
            "No order was submitted. This is a local scenario model for deciding whether the setup is worth taking.\n\n"
            f"Market: {coin}-PERP\n"
            f"Direction: {direction}\n"
            f"Size: {size:g} {coin}\n"
            f"Entry: ${entry:,.4f}\n"
            f"Notional: ${notional:,.2f}\n"
            f"Leverage used for margin math: {leverage:g}x\n"
            f"Estimated initial margin: ${margin:,.2f}\n"
            f"Fee estimate: {fee_rate_percent:g}% per side, entry + exit included\n\n"
            "Decision map:\n"
            f"- Good if price moves {favorable_word} entry toward target.\n"
            f"- Bad if price moves {pain_word} entry toward pain/stop.\n"
            f"- Fee-adjusted breakeven exit: ${breakeven:,.4f}\n"
            f"- Rough liquidation warning line: ${rough_liq:,.4f}\n\n"
            "Target scenario:\n"
            f"- Exit price: ${target:,.4f}\n"
            f"- Price move: {target_case['move_percent']:+.2f}%\n"
            f"- Gross P&L: ${target_case['gross_pnl']:+,.2f}\n"
            f"- Estimated fees: ${target_case['fees']:,.2f}\n"
            f"- Net P&L: ${target_case['net_pnl']:+,.2f}\n"
            f"- ROI on estimated margin: {target_case['margin_roi_percent']:+.2f}%\n\n"
            "Pain / stop scenario:\n"
            f"- Exit price: ${pain:,.4f}\n"
            f"- Price move: {pain_case['move_percent']:+.2f}%\n"
            f"- Gross P&L: ${pain_case['gross_pnl']:+,.2f}\n"
            f"- Estimated fees: ${pain_case['fees']:,.2f}\n"
            f"- Net P&L: ${pain_case['net_pnl']:+,.2f}\n"
            f"- ROI on estimated margin: {pain_case['margin_roi_percent']:+.2f}%\n\n"
            "Setup quality:\n"
            f"- Reward/risk using net P&L: {rr}\n"
            f"- Max modeled loss at pain/stop: ${min(pain_case['net_pnl'], 0):+,.2f}\n\n"
            "Formula notes:\n"
            "- Long P&L = (exit - entry) × size.\n"
            "- Short P&L = (entry - exit) × size.\n"
            "- Net P&L subtracts estimated entry and exit fees.\n"
            "- Rough liquidation ignores maintenance margin, funding, slippage, partial fills, and account-wide margin."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: what-if failed")
        messagebox.showerror("Hyperliquid perp what-if failed", str(exc))


def _perp_case(entry: float, exit_price: float, size: float, is_long: bool, leverage: float, fee_rate_percent: float) -> dict[str, float]:
    signed_move = (exit_price - entry) / entry
    gross_pnl = (exit_price - entry) * size if is_long else (entry - exit_price) * size
    fees = (entry * size + exit_price * size) * (fee_rate_percent / 100.0)
    net_pnl = gross_pnl - fees
    margin = (entry * size) / leverage
    margin_roi_percent = (net_pnl / margin * 100.0) if margin > 0 else 0.0
    return {
        "move_percent": signed_move * 100.0,
        "gross_pnl": gross_pnl,
        "fees": fees,
        "net_pnl": net_pnl,
        "margin_roi_percent": margin_roi_percent,
    }


def _breakeven_price(entry: float, is_long: bool, fee_rate_percent: float) -> float:
    fee = fee_rate_percent / 100.0
    if is_long:
        return entry * (1.0 + fee) / max(1.0 - fee, 0.000001)
    return entry * (1.0 - fee) / (1.0 + fee)


def _rough_liquidation_price(entry: float, is_long: bool, leverage: float) -> float:
    if leverage <= 1:
        return 0.0 if is_long else entry * 2.0
    return entry * (1.0 - 1.0 / leverage) if is_long else entry * (1.0 + 1.0 / leverage)


def _risk_reward(reward_net: float, pain_net: float) -> str:
    risk = abs(min(pain_net, 0.0))
    reward = max(reward_net, 0.0)
    if risk <= 0 or reward <= 0:
        return "n/a — target must be profitable and pain/stop must be a loss"
    return f"{reward / risk:.2f} : 1"


def _required_float(raw: str, label: str) -> float:
    value = _to_float(raw)
    if value is None:
        raise ValueError(f"{label} must be a number.")
    return value


def _optional_float(raw: str, *, default: float) -> float:
    value = _to_float(raw)
    return default if value is None else value


def _to_float(raw: object) -> float | None:
    try:
        text = str(raw).strip().replace(",", "")
        if text == "":
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _format_price(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _build_account_sources_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    panel = ttk.LabelFrame(parent, text="Account Sources", style="Card.TLabelframe")
    panel.pack(fill=tk.X)
    panel.columnconfigure(0, weight=1)

    ttk.Label(
        panel,
        text=(
            "Schwab/current portfolio powers the Cockpit and Options What-If Lab. "
            "Hyperliquid can be synced from the Trade Planner."
        ),
        style="Subtle.TLabel",
        wraplength=1180,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))

    buttons = ttk.Frame(panel, style="Panel.TFrame")
    buttons.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    for column in range(3):
        buttons.columnconfigure(column, weight=1, uniform="sources")

    ttk.Button(buttons, text="Connect Schwab", command=self.connect_schwab).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Refresh Schwab", command=lambda: _refresh_current_source(self)).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Use Schwab/Current", command=self.use_current_cockpit_source_portfolio, style="Accent.TButton").grid(row=0, column=2, sticky="ew")

    status = ttk.Frame(panel, style="Panel.TFrame")
    status.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    status.columnconfigure(0, weight=1)
    ttk.Label(status, textvariable=self.active_portfolio_source_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew")


def _build_options_lab_market_loader(self: tk.Tk, parent: ttk.Frame) -> None:
    loader = ttk.LabelFrame(parent, text="Optional Schwab Technical Context Loader", style="Card.TLabelframe")
    loader.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    loader.columnconfigure(0, weight=1)

    ttk.Label(
        loader,
        text=(
            "Pulls recent daily Schwab candles for the sandbox symbol and fills underlying price, RSI, "
            "20/50/200 SMA, ATR %, support, and resistance. No order preview or order submission is made."
        ),
        style="Subtle.TLabel",
        wraplength=860,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Button(
        loader,
        text="Load Schwab Technicals",
        command=self.load_options_lab_technical_context,
        style="Accent.TButton",
    ).grid(row=0, column=1, sticky="e")


def _capture_current_source_portfolio(self: tk.Tk) -> None:
    try:
        self.cockpit_source_portfolio = self.broker.get_portfolio()
        self.cockpit_source_message = getattr(self.broker, "source_message", "Current cockpit portfolio")
    except Exception:
        return


def _refresh_current_source(self: tk.Tk) -> None:
    try:
        self.refresh_schwab_account()
    except Exception:
        self.refresh_portfolio()
    _capture_current_source_portfolio(self)
    self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
    _sync_options_values_from_active_portfolio(self)


def _use_current_cockpit_source_portfolio(self: tk.Tk) -> None:
    try:
        if self.cockpit_source_portfolio is None:
            _capture_current_source_portfolio(self)
        if self.cockpit_source_portfolio is None:
            raise RuntimeError("No current cockpit source portfolio is available yet.")

        self.broker.set_portfolio(self.cockpit_source_portfolio, self.cockpit_source_message)
        self.refresh_portfolio()
        self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
        _sync_options_values_from_active_portfolio(self)
    except Exception as exc:
        messagebox.showerror("Use current portfolio failed", str(exc))


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
        run_options_what_if(self)
    except Exception:
        return


def _load_options_lab_technical_context(self: tk.Tk) -> None:
    symbol = self.options_symbol_var.get().strip().upper()
    if not symbol:
        messagebox.showerror("Options lab technicals failed", "Enter a symbol first.")
        return

    try:
        session = self._authorize_schwab_session()
        if session is None:
            return

        status_code, payload = session.get_price_history(
            symbol,
            period_type="year",
            period=1,
            frequency_type="daily",
            frequency=1,
            need_extended_hours_data=False,
        )
        if status_code != 200:
            raise RuntimeError(f"Schwab daily price history returned HTTP {status_code}: {payload}")

        candles = candles_from_price_history(payload)
        report = analyze_candles(symbol, candles)
        levels = calculate_support_resistance(candles, lookback=50)
        closes = [candle.close for candle in candles]
        sma_200 = simple_moving_average(closes, 200)
        atr_percent = _average_true_range_percent(candles, period=14)

        self.options_underlying_price_var.set(f"{report.latest_close:.2f}")
        if report.rsi is not None:
            self.options_rsi_var.set(f"{report.rsi:.1f}")
        if report.sma_fast is not None:
            self.options_sma_20_var.set(f"{report.sma_fast:.2f}")
        if report.sma_slow is not None:
            self.options_sma_50_var.set(f"{report.sma_slow:.2f}")
        if sma_200 is not None:
            self.options_sma_200_var.set(f"{sma_200:.2f}")
        if levels.support is not None:
            self.options_support_var.set(f"{levels.support:.2f}")
        if levels.resistance is not None:
            self.options_resistance_var.set(f"{levels.resistance:.2f}")
        if atr_percent is not None:
            self.options_atr_var.set(f"{atr_percent:.2f}")

        self.schwab_status_var.set("Schwab session: connected")
        run_options_what_if(self)
    except Exception as exc:
        messagebox.showerror("Options lab technicals failed", str(exc))


def _average_true_range_percent(candles, *, period: int) -> float | None:
    if len(candles) <= period:
        return None

    true_ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        true_range = max(
            candle.high - candle.low,
            abs(candle.high - previous_close),
            abs(candle.low - previous_close),
        )
        true_ranges.append(true_range)
        previous_close = candle.close

    recent_ranges = true_ranges[-period:]
    latest_close = candles[-1].close
    if not recent_ranges or latest_close <= 0:
        return None
    return (sum(recent_ranges) / len(recent_ranges) / latest_close) * 100
