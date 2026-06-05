from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Type

from app.brokers.hyperliquid.client import (
    HyperliquidInfoClient,
    format_hyperliquid_snapshot,
    portfolio_from_hyperliquid_snapshot,
)
from app.core.order_models import SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, OrderSide, OrderType, TimeInForce
from app.core.portfolio import Portfolio, Position
from app.ui.polished_theme import _make_paned


def install_advanced_actions_extension(app_cls: Type[tk.Tk]) -> None:
    """Tuck dangerous/live controls into a dedicated advanced section."""
    app_cls._build_order_panel = _build_order_panel  # type: ignore[method-assign]
    app_cls.sync_hyperliquid_account = _sync_hyperliquid_account  # type: ignore[method-assign]
    app_cls._merge_hyperliquid_portfolio = _merge_hyperliquid_portfolio  # type: ignore[method-assign]


def _sync_single_ticket_price(self: tk.Tk, *_args) -> None:
    """Keep legacy estimated-price plumbing aligned with the visible entry price."""
    if getattr(self, "_syncing_ticket_price", False):
        return
    try:
        self._syncing_ticket_price = True
        self.estimated_price_var.set(self.limit_price_var.get())
    finally:
        self._syncing_ticket_price = False


def _sync_hyperliquid_combined(self: tk.Tk) -> None:
    """Run the read-only Hyperliquid account sync if the cockpit has the connector installed."""
    if not hasattr(self, "sync_hyperliquid_account"):
        self._set_preview_text(
            "HYPERLIQUID SYNC NOT READY\n"
            "==========================\n\n"
            "Hyperliquid controls are not installed yet. Restart the app after pulling the latest repo changes."
        )
        return

    self.sync_hyperliquid_account()


def _hyperliquid_address_from_env() -> str:
    """Support the friendly HYPE env var plus the original generic name."""
    for key in ("HYPE_WALLET_ADDRESS", "HYPERLIQUID_USER_ADDRESS"):
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _sync_hyperliquid_account(self: tk.Tk) -> None:
    """Read Hyperliquid balances/positions and merge them into the cockpit."""
    default_address = _hyperliquid_address_from_env()
    address = default_address or simpledialog.askstring(
        "Hyperliquid Sync",
        "Enter your Hyperliquid master/sub-account wallet address.\n\n"
        "Tip: save HYPE_WALLET_ADDRESS=0x... in .env to skip this prompt.\n\n"
        "Use the account address, not the API/agent wallet address.",
    )
    if not address:
        return

    try:
        client = HyperliquidInfoClient()
        snapshot = client.fetch_snapshot(address)
        orders_table = getattr(self, "hyperliquid_workspace_open_orders_table", None)
        if orders_table is not None:
            try:
                from app.ui.options_lab_extension import _populate_workspace_open_orders_table

                _populate_workspace_open_orders_table(orders_table, snapshot.open_orders)
            except Exception:
                pass
        hyperliquid_portfolio, hyperliquid_source_message = portfolio_from_hyperliquid_snapshot(snapshot)
        merged_portfolio = self._merge_hyperliquid_portfolio(hyperliquid_portfolio)

        base_source_message = self.broker.source_message.split(" + Loaded Hyperliquid account ")[0]
        source_message = f"{base_source_message} + {hyperliquid_source_message}"
        self.broker.set_portfolio(merged_portfolio, source_message)
        self.last_hyperliquid_cash_adjustment = hyperliquid_portfolio.cash
        self.refresh_portfolio()
        self._set_preview_text(format_hyperliquid_snapshot(snapshot, hyperliquid_portfolio))
        messagebox.showinfo("Hyperliquid synced", hyperliquid_source_message)
    except Exception as exc:
        messagebox.showerror("Hyperliquid sync failed", str(exc))


def _hyperliquid_display_symbol(symbol: str) -> str:
    """Make Hyperliquid symbols look natural in the stock-style grid."""
    symbol = symbol.strip().upper()
    if symbol.endswith("-SPOT"):
        return symbol.removesuffix("-SPOT")
    return symbol


def _merge_hyperliquid_portfolio(self: tk.Tk, hyperliquid_portfolio: Portfolio) -> Portfolio:
    current = self.broker.get_portfolio()
    non_hyperliquid_cash = round(current.cash - getattr(self, "last_hyperliquid_cash_adjustment", 0.0), 2)
    previous_hyperliquid_symbols = set(getattr(self, "last_hyperliquid_display_symbols", set()))

    positions = {
        symbol: position
        for symbol, position in current.positions.items()
        if not symbol.startswith("HL:") and symbol not in previous_hyperliquid_symbols
    }

    display_symbols: set[str] = set()
    for symbol, position in hyperliquid_portfolio.positions.items():
        display_symbol = _hyperliquid_display_symbol(symbol)
        display_symbols.add(display_symbol)
        positions[display_symbol] = Position(
            symbol=display_symbol,
            quantity=position.quantity,
            average_cost=position.average_cost,
            last_price=position.last_price,
            day_profit_loss=position.day_profit_loss,
            day_profit_loss_percent=position.day_profit_loss_percent,
            open_profit_loss=position.open_profit_loss,
            unrealized_profit_loss_known=position.unrealized_profit_loss_known,
            cost_basis_estimated=position.cost_basis_estimated,
            raw_profit_loss=position.raw_profit_loss,
            custom_profit_loss=position.custom_profit_loss,
            custom_realized_profit_loss=position.custom_realized_profit_loss,
            custom_unrealized_profit_loss=position.custom_unrealized_profit_loss,
            custom_pnl_status=position.custom_pnl_status,
            basis_status=position.basis_status,
        )

    self.last_hyperliquid_display_symbols = display_symbols
    return Portfolio(
        cash=round(non_hyperliquid_cash + hyperliquid_portfolio.cash, 2),
        positions=positions,
    )


def _build_order_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    stack = _make_paned(parent, tk.VERTICAL)
    stack.pack(fill=tk.BOTH, expand=True)

    ticket_shell = ttk.Frame(stack, style="Canvas.TFrame")
    preview_shell = ttk.Frame(stack, style="Canvas.TFrame")
    explainer_shell = ttk.Frame(stack, style="Canvas.TFrame")
    stack.add(ticket_shell, minsize=305, stretch="never")
    stack.add(preview_shell, minsize=220, stretch="always")
    stack.add(explainer_shell, minsize=78, stretch="never")

    ticket = ttk.LabelFrame(ticket_shell, text="Trade Planner", style="Card.TLabelframe")
    ticket.pack(fill=tk.BOTH, expand=True)
    ticket.columnconfigure(1, weight=1)
    ticket.columnconfigure(3, weight=1)

    self.estimated_price_var.set(self.limit_price_var.get())
    self.limit_price_var.trace_add("write", lambda *_args: _sync_single_ticket_price(self))

    self._grid_row(ticket, 0, "Symbol", ttk.Entry(ticket, textvariable=self.symbol_var), "Side", ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"))
    self._grid_row(ticket, 1, "Order type", ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"), "Time", ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, state="readonly"))
    self._grid_row(ticket, 2, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var), "Entry / Limit", ttk.Entry(ticket, textvariable=self.limit_price_var))
    self._grid_row(ticket, 3, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var))

    ttk.Label(ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=4, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    primary_actions = ttk.Frame(ticket, style="Panel.TFrame")
    primary_actions.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(16, 0))
    for column in range(5):
        primary_actions.columnconfigure(column, weight=1, uniform="primary_actions")

    primary_buttons = [
        ("Preview Risk", self.preview_order, "Accent.TButton"),
        ("Connect Schwab", self.connect_schwab, "TButton"),
        ("Connect Hyperliquid", lambda: _sync_hyperliquid_combined(self), "TButton"),
        ("Refresh Schwab", self.refresh_schwab_account, "TButton"),
        ("Tech Analysis", self.show_technical_analysis, "TButton"),
    ]
    for column, (label, command, style_name) in enumerate(primary_buttons):
        ttk.Button(primary_actions, text=label, command=command, style=style_name).grid(
            row=0,
            column=column,
            sticky="nsew",
            padx=(0 if column == 0 else 4, 0 if column == len(primary_buttons) - 1 else 4),
            ipady=2,
        )

    secondary_actions = ttk.Frame(ticket, style="Panel.TFrame")
    secondary_actions.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    for column in range(5):
        secondary_actions.columnconfigure(column, weight=1, uniform="secondary_actions")
    for index, (label, command) in enumerate([
        ("Preview Order", self.run_schwab_preview),
        ("Recent Orders", self.load_schwab_open_orders),
        ("Open Only", self.load_schwab_open_orders_only),
        ("Reset Session", self.reset_schwab_session),
    ]):
        ttk.Button(secondary_actions, text=label, command=command).grid(
            row=0,
            column=index,
            sticky="nsew",
            padx=(0 if index == 0 else 4, 0 if index == 4 else 4),
            ipady=1,
        )

    advanced = ttk.LabelFrame(ticket, text="Advanced / Live Actions", style="Card.TLabelframe")
    advanced.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(10, 0))
    advanced.columnconfigure(0, weight=2)
    advanced.columnconfigure(1, weight=1)
    advanced.columnconfigure(2, weight=1)
    ttk.Label(
        advanced,
        text="Use only after preview/checks. These controls can affect Schwab orders.",
        style="Subtle.TLabel",
    ).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=(0, 6))
    ttk.Button(advanced, text="Cancel Order", command=self.show_cancel_order_placeholder, style="Danger.TButton").grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=(0, 6))
    ttk.Button(advanced, text="LIVE Submit", command=self.submit_live_schwab_order, style="Danger.TButton").grid(row=0, column=2, sticky="ew", pady=(0, 6))

    status_bar = ttk.Frame(ticket, style="Panel.TFrame")
    status_bar.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    status_bar.columnconfigure((0, 1, 2), weight=1)
    ttk.Label(status_bar, textvariable=self.schwab_status_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.schwab_preview_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.schwab_verification_status_var, style="Chip.TLabel").grid(row=0, column=2, sticky="ew", pady=(4, 0))

    results = ttk.LabelFrame(preview_shell, text="Analysis + Instructions", style="Card.TLabelframe")
    results.pack(fill=tk.BOTH, expand=True)

    self.preview_text = tk.Text(
        results,
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
    self.preview_text.pack(fill=tk.BOTH, expand=True)
    self._set_preview_text(
        "Create a ticket, then use Preview Risk, Tech Analysis, or Trade Setup.\n\n"
        "Entry / Limit is the single planning price used for local risk, trade setup, and Schwab limit-order preview.\n\n"
        "Connect Hyperliquid reads HYPE_WALLET_ADDRESS from .env when present and merges clean symbols like HYPE into the active cockpit portfolio."
    )

    explainer = ttk.LabelFrame(explainer_shell, text="Order Type Cheat Sheet", style="Card.TLabelframe")
    explainer.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        explainer,
        text=(
            "Limit buy = maximum entry price. Limit sell = minimum exit price. "
            "Stop = trigger price. Stop-limit = trigger plus limit, but may not fill."
        ),
        wraplength=460,
        style="Subtle.TLabel",
    ).pack(anchor=tk.W)
