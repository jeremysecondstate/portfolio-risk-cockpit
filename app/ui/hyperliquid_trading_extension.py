from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Type

from app.brokers.hyperliquid.trading import (
    HyperliquidOrderTicket,
    HyperliquidTradingConfig,
    normalize_hyperliquid_coin,
)
from app.core.order_models import OrderSide, OrderType, TimeInForce
from app.ui import polished_theme
from app.ui.polished_theme import _make_paned


TRADING_VENUES = ["Schwab", "Hyperliquid"]
HYPERLIQUID_TIFS = ["Alo", "Ioc", "Gtc"]


def install_hyperliquid_trading_extension(app_cls: Type[tk.Tk]) -> None:
    """Add a venue selector and guarded Hyperliquid ticket workflow."""

    app_cls._build_order_panel = _build_order_panel_with_hyperliquid  # type: ignore[method-assign]
    app_cls.preview_selected_venue = _preview_selected_venue  # type: ignore[attr-defined]
    app_cls.show_selected_live_safety_review = _show_selected_live_safety_review  # type: ignore[attr-defined]
    app_cls.preview_hyperliquid_ticket = _preview_hyperliquid_ticket  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_live_submit_safety_review = _show_hyperliquid_live_submit_safety_review  # type: ignore[attr-defined]
    app_cls.parse_hyperliquid_ticket = _parse_hyperliquid_ticket  # type: ignore[attr-defined]
    app_cls.on_trading_venue_changed = _on_trading_venue_changed  # type: ignore[attr-defined]


def _ensure_hyperliquid_vars(self: tk.Tk) -> None:
    if hasattr(self, "trade_venue_var"):
        return
    self.trade_venue_var = tk.StringVar(value="Schwab")
    self.hyperliquid_coin_var = tk.StringVar(value="HYPE")
    self.hyperliquid_tif_var = tk.StringVar(value="Gtc")
    self.hyperliquid_reduce_only_var = tk.BooleanVar(value=False)
    self.hyperliquid_status_var = tk.StringVar(value="Hyperliquid: preview only")


def _build_order_panel_with_hyperliquid(self: tk.Tk, parent: ttk.Frame) -> None:
    _ensure_hyperliquid_vars(self)

    stack = _make_paned(parent, tk.VERTICAL)
    stack.pack(fill=tk.BOTH, expand=True)

    ticket_shell = ttk.Frame(stack, style="Canvas.TFrame")
    preview_shell = ttk.Frame(stack, style="Canvas.TFrame")
    explainer_shell = ttk.Frame(stack, style="Canvas.TFrame")
    stack.add(ticket_shell, minsize=290, stretch="never")
    stack.add(preview_shell, minsize=220, stretch="always")
    stack.add(explainer_shell, minsize=78, stretch="never")

    ticket = ttk.LabelFrame(ticket_shell, text="Trade Planner", style="Card.TLabelframe")
    ticket.pack(fill=tk.BOTH, expand=True)
    ticket.columnconfigure(1, weight=1)
    ticket.columnconfigure(3, weight=1)

    self.estimated_price_var.set(self.limit_price_var.get())
    self.limit_price_var.trace_add("write", lambda *_args: polished_theme._sync_single_ticket_price(self))

    self._grid_row(
        ticket,
        0,
        "Venue",
        ttk.Combobox(ticket, textvariable=self.trade_venue_var, values=TRADING_VENUES, state="readonly"),
        "HL Coin",
        ttk.Entry(ticket, textvariable=self.hyperliquid_coin_var),
    )
    self._grid_row(ticket, 1, "Symbol", ttk.Entry(ticket, textvariable=self.symbol_var), "Side", ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"))
    self._grid_row(ticket, 2, "Order type", ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"), "Time", ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=[t.value for t in TimeInForce], state="readonly"))
    self._grid_row(ticket, 3, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var), "Entry / Limit", ttk.Entry(ticket, textvariable=self.limit_price_var))
    self._grid_row(ticket, 4, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var), "Type CONFIRM", ttk.Entry(ticket, textvariable=self.confirmation_var))
    self._grid_row(
        ticket,
        5,
        "HL TIF",
        ttk.Combobox(ticket, textvariable=self.hyperliquid_tif_var, values=HYPERLIQUID_TIFS, state="readonly"),
        "HL Reduce-only",
        ttk.Checkbutton(ticket, variable=self.hyperliquid_reduce_only_var),
    )

    ttk.Label(ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=6, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=6, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    primary_actions = ttk.Frame(ticket, style="Panel.TFrame")
    primary_actions.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(12, 0))
    for column in range(5):
        primary_actions.columnconfigure(column, weight=1, uniform="venue_actions")
    ttk.Button(primary_actions, text="Preview Venue", command=self.preview_selected_venue, style="Accent.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Connect Schwab", command=self.connect_schwab).grid(row=0, column=1, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Refresh Schwab", command=self.refresh_schwab_account).grid(row=0, column=2, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Sync Hyperliquid", command=self.sync_hyperliquid_account).grid(row=0, column=3, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Tech Analysis", command=self.show_technical_analysis).grid(row=0, column=4, sticky="ew")

    secondary_actions = ttk.Frame(ticket, style="Panel.TFrame")
    secondary_actions.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    for column in range(4):
        secondary_actions.columnconfigure(column, weight=1, uniform="actions")
    for index, (label, command, style_name) in enumerate([
        ("Trade Setup", self.show_position_size, "TButton"),
        ("Schwab Preview", self.run_schwab_preview, "TButton"),
        ("Recent Orders", self.load_schwab_open_orders, "TButton"),
        ("Open Only", self.load_schwab_open_orders_only, "TButton"),
        ("Reset Session", self.reset_schwab_session, "TButton"),
        ("Cancel Order", self.show_cancel_order_placeholder, "Danger.TButton"),
        ("Live Safety", self.show_selected_live_safety_review, "Danger.TButton"),
        ("LIVE Submit", self.show_selected_live_safety_review, "Danger.TButton"),
    ]):
        ttk.Button(secondary_actions, text=label, command=command, style=style_name).grid(
            row=index // 4,
            column=index % 4,
            sticky="ew",
            padx=(0 if index % 4 == 0 else 6, 0),
            pady=(0 if index < 4 else 6, 0),
        )

    status_bar = ttk.Frame(ticket, style="Panel.TFrame")
    status_bar.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    status_bar.columnconfigure((0, 1, 2, 3), weight=1)
    ttk.Label(status_bar, textvariable=self.schwab_status_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.schwab_preview_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.schwab_verification_status_var, style="Chip.TLabel").grid(row=0, column=2, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.hyperliquid_status_var, style="Chip.TLabel").grid(row=0, column=3, sticky="ew", pady=(4, 0))

    venue_combo = ticket.grid_slaves(row=0, column=1)[0]
    venue_combo.bind("<<ComboboxSelected>>", lambda _event: self.on_trading_venue_changed())
    self.after_idle(self.on_trading_venue_changed)

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
        "Choose Schwab or Hyperliquid, create a ticket, then use Preview Venue.\n\n"
        "Schwab keeps the existing preview/order workflow. Hyperliquid currently supports local ticket readiness preview and a disabled live-submit safety review.\n\n"
        "Reminder: live actions remain behind explicit safety checks."
    )

    explainer = ttk.LabelFrame(explainer_shell, text="Order Type Cheat Sheet", style="Card.TLabelframe")
    explainer.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        explainer,
        text=(
            "Schwab: existing stock/ETF ticket. Hyperliquid: coin + side + size + limit price, "
            "with TIF Alo/Ioc/Gtc and optional reduce-only intent."
        ),
        wraplength=560,
        style="Subtle.TLabel",
    ).pack(anchor=tk.W)


def _on_trading_venue_changed(self: tk.Tk) -> None:
    venue = self.trade_venue_var.get()
    if venue == "Hyperliquid":
        try:
            self.hyperliquid_coin_var.set(normalize_hyperliquid_coin(self.symbol_var.get() or self.hyperliquid_coin_var.get()))
        except Exception:
            pass
        self.hyperliquid_status_var.set("Hyperliquid: selected")
    else:
        self.hyperliquid_status_var.set("Hyperliquid: preview only")


def _preview_selected_venue(self: tk.Tk) -> None:
    if self.trade_venue_var.get() == "Hyperliquid":
        self.preview_hyperliquid_ticket()
    else:
        self.preview_order()


def _show_selected_live_safety_review(self: tk.Tk) -> None:
    if self.trade_venue_var.get() == "Hyperliquid":
        self.show_hyperliquid_live_submit_safety_review()
    else:
        self.show_live_submit_safety_review()


def _parse_hyperliquid_ticket(self: tk.Tk) -> HyperliquidOrderTicket:
    coin_source = self.hyperliquid_coin_var.get().strip() or self.symbol_var.get().strip()
    coin = normalize_hyperliquid_coin(coin_source)
    side = self.side_var.get().strip().lower()
    if side not in {"buy", "sell"}:
        raise ValueError("Hyperliquid side must be buy or sell.")
    try:
        size = float(self.quantity_var.get().strip().replace(",", ""))
        limit_price = float(self.limit_price_var.get().strip().replace(",", ""))
    except ValueError as exc:
        raise ValueError("Hyperliquid size and limit price must be numbers.") from exc
    if size <= 0:
        raise ValueError("Hyperliquid size must be positive.")
    if limit_price <= 0:
        raise ValueError("Hyperliquid limit price must be positive.")
    tif = self.hyperliquid_tif_var.get().strip() or "Gtc"
    if tif not in HYPERLIQUID_TIFS:
        raise ValueError("Hyperliquid TIF must be Alo, Ioc, or Gtc.")
    return HyperliquidOrderTicket(
        coin=coin,
        is_buy=side == "buy",
        size=size,
        limit_price=limit_price,
        tif=tif,
        reduce_only=bool(self.hyperliquid_reduce_only_var.get()),
    )


def _preview_hyperliquid_ticket(self: tk.Tk) -> None:
    try:
        ticket = self.parse_hyperliquid_ticket()
        config = HyperliquidTradingConfig()
        self.hyperliquid_status_var.set("Hyperliquid: preview ready")
        self._set_preview_text(config.preview_text(ticket))
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: preview failed")
        messagebox.showerror("Hyperliquid preview failed", str(exc))


def _show_hyperliquid_live_submit_safety_review(self: tk.Tk) -> None:
    try:
        ticket = self.parse_hyperliquid_ticket()
        config = HyperliquidTradingConfig()
        self.hyperliquid_status_var.set("Hyperliquid: live disabled")
        self._set_preview_text(config.live_disabled_text(ticket, self.confirmation_var.get()))
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: safety failed")
        messagebox.showerror("Hyperliquid safety review failed", str(exc))
