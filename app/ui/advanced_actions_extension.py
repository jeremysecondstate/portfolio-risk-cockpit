from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.core.order_models import OrderSide, OrderType, TimeInForce
from app.ui.polished_theme import CANVAS, DANGER, INPUT, _make_paned


def install_advanced_actions_extension(app_cls: Type[tk.Tk]) -> None:
    """Tuck dangerous/live controls into a dedicated advanced section."""
    app_cls._build_order_panel = _build_order_panel  # type: ignore[method-assign]


def _sync_single_ticket_price(self: tk.Tk, *_args) -> None:
    """Keep legacy estimated-price plumbing aligned with the visible entry price."""
    if getattr(self, "_syncing_ticket_price", False):
        return
    try:
        self._syncing_ticket_price = True
        self.estimated_price_var.set(self.limit_price_var.get())
    finally:
        self._syncing_ticket_price = False


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
    self._grid_row(ticket, 1, "Order type", ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"), "Time", ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=[t.value for t in TimeInForce], state="readonly"))
    self._grid_row(ticket, 2, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var), "Entry / Limit", ttk.Entry(ticket, textvariable=self.limit_price_var))
    self._grid_row(ticket, 3, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var), "Type CONFIRM", ttk.Entry(ticket, textvariable=self.confirmation_var))

    ttk.Label(ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=4, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    primary_actions = ttk.Frame(ticket, style="Panel.TFrame")
    primary_actions.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(16, 0))
    primary_actions.columnconfigure((0, 1, 2, 3), weight=1)
    ttk.Button(primary_actions, text="Preview Risk", command=self.preview_order, style="Accent.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Connect Schwab", command=self.connect_schwab).grid(row=0, column=1, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Refresh Account", command=self.refresh_schwab_account).grid(row=0, column=2, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Tech Analysis", command=self.show_technical_analysis).grid(row=0, column=3, sticky="ew")

    secondary_actions = ttk.Frame(ticket, style="Panel.TFrame")
    secondary_actions.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    for column in range(4):
        secondary_actions.columnconfigure(column, weight=1, uniform="actions")
    for index, (label, command) in enumerate([
        ("Trade Setup", self.show_position_size),
        ("Preview Order", self.run_schwab_preview),
        ("Recent Orders", self.load_schwab_open_orders),
        ("Open Only", self.load_schwab_open_orders_only),
        ("Reset Session", self.reset_schwab_session),
    ]):
        ttk.Button(secondary_actions, text=label, command=command).grid(
            row=index // 4,
            column=index % 4,
            sticky="ew",
            padx=(0 if index % 4 == 0 else 6, 0),
            pady=(0 if index < 4 else 6, 0),
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
    ttk.Button(advanced, text="LIVE Submit", command=self.submit_live_schwab_order_guarded, style="Danger.TButton").grid(row=0, column=2, sticky="ew", pady=(0, 6))

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
        "Advanced / Live Actions are intentionally separated from the normal analysis workflow."
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
