from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.core.order_models import OrderSide, OrderType, TimeInForce


CANVAS = "#0f172a"
SURFACE = "#111827"
PANEL = "#f8fafc"
PANEL_ALT = "#eef2ff"
BORDER = "#cbd5e1"
TEXT = "#0f172a"
MUTED = "#64748b"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
DANGER = "#b91c1c"
INPUT = "#ffffff"


def install_polished_cockpit_theme(app_cls: Type[tk.Tk]) -> None:
    """Install a polished visual layer without touching trading behavior.

    The cockpit already has the workflows wired up in the base dashboard and
    Schwab extension. This module only replaces presentation methods on the app
    class so the existing order, preview, safety, and Schwab commands stay the
    same.
    """

    app_cls._configure_style = _configure_style  # type: ignore[method-assign]
    app_cls._build_layout = _build_layout  # type: ignore[method-assign]
    app_cls._build_header = _build_header  # type: ignore[method-assign]
    app_cls._build_portfolio_panel = _build_portfolio_panel  # type: ignore[method-assign]
    app_cls._build_order_panel = _build_order_panel  # type: ignore[method-assign]
    app_cls._metric = _metric  # type: ignore[method-assign]
    app_cls._grid_row = _grid_row  # type: ignore[method-assign]
    app_cls._set_preview_text = _set_preview_text  # type: ignore[method-assign]


def _configure_style(self: tk.Tk) -> None:
    self.option_add("*Font", "{Segoe UI} 10")
    self.configure(bg=CANVAS)

    style = ttk.Style(self)
    style.theme_use("clam")

    style.configure(".", font=("Segoe UI", 10), background=PANEL, foreground=TEXT)
    style.configure("TFrame", background=PANEL)
    style.configure("Canvas.TFrame", background=CANVAS)
    style.configure("Hero.TFrame", background=SURFACE)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Card.TLabelframe", background=PANEL, bordercolor=BORDER, relief="solid", padding=16)
    style.configure("Card.TLabelframe.Label", background=PANEL, foreground=TEXT, font=("Segoe UI", 11, "bold"))

    style.configure("Header.TLabel", background=SURFACE, foreground="#ffffff", font=("Segoe UI", 22, "bold"))
    style.configure("HeroSubtle.TLabel", background=SURFACE, foreground="#cbd5e1")
    style.configure("Subtle.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Mode.TLabel", background=SURFACE, foreground="#86efac", font=("Segoe UI", 10, "bold"))
    style.configure("MetricTitle.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9, "bold"))
    style.configure("MetricValue.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 18, "bold"))
    style.configure("Chip.TLabel", background=PANEL_ALT, foreground=ACCENT_DARK, font=("Segoe UI", 9, "bold"), padding=(8, 4))

    style.configure("TButton", padding=(10, 7), borderwidth=0)
    style.map("TButton", background=[("active", "#e0e7ff")])
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=(12, 8))
    style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)], foreground=[("active", "#ffffff")])
    style.configure("Danger.TButton", background="#fee2e2", foreground=DANGER, font=("Segoe UI", 10, "bold"), padding=(10, 7))
    style.map("Danger.TButton", background=[("active", "#fecaca")])

    style.configure("TEntry", fieldbackground=INPUT, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=6)
    style.configure("TCombobox", fieldbackground=INPUT, bordercolor=BORDER, padding=6)
    style.configure("Treeview", background=INPUT, fieldbackground=INPUT, foreground=TEXT, rowheight=30, bordercolor=BORDER, borderwidth=0)
    style.configure("Treeview.Heading", background="#e2e8f0", foreground=TEXT, font=("Segoe UI", 9, "bold"), padding=8)
    style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", TEXT)])


def _make_paned(parent: tk.Widget, orient: str) -> tk.PanedWindow:
    """Create a visible draggable splitter that still matches the cockpit theme."""
    return tk.PanedWindow(
        parent,
        orient=orient,
        bg=CANVAS,
        bd=0,
        sashwidth=8,
        sashrelief=tk.FLAT,
        sashpad=4,
        showhandle=True,
        opaqueresize=True,
    )


def _build_layout(self: tk.Tk) -> None:
    root = ttk.Frame(self, style="Canvas.TFrame", padding=18)
    root.pack(fill=tk.BOTH, expand=True)

    self._build_header(root)

    body = _make_paned(root, tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

    left = ttk.Frame(body, style="Canvas.TFrame")
    right = ttk.Frame(body, style="Canvas.TFrame")
    body.add(left, minsize=560, stretch="always")
    body.add(right, minsize=520, stretch="always")
    self.after_idle(lambda: body.sash_place(0, max(600, int(self.winfo_width() * 0.60)), 0))

    self._build_portfolio_panel(left)
    self._build_order_panel(right)


def _build_header(self: tk.Tk, parent: ttk.Frame) -> None:
    header = ttk.Frame(parent, style="Hero.TFrame", padding=(20, 18))
    header.pack(fill=tk.X)
    header.columnconfigure(0, weight=1)

    title_stack = ttk.Frame(header, style="Hero.TFrame")
    title_stack.grid(row=0, column=0, sticky="w")
    ttk.Label(title_stack, text="Portfolio Risk Cockpit", style="Header.TLabel").pack(anchor=tk.W)
    ttk.Label(
        title_stack,
        text="A safer, cleaner control surface for paper planning and Schwab previews.",
        style="HeroSubtle.TLabel",
    ).pack(anchor=tk.W, pady=(4, 0))

    status_stack = ttk.Frame(header, style="Hero.TFrame")
    status_stack.grid(row=0, column=1, sticky="e")
    ttk.Label(status_stack, text="PAPER-FIRST MODE", style="Mode.TLabel").pack(anchor=tk.E)
    ttk.Label(status_stack, text="Live actions require explicit safety checks", style="HeroSubtle.TLabel").pack(anchor=tk.E, pady=(4, 0))


def _build_portfolio_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    stack = _make_paned(parent, tk.VERTICAL)
    stack.pack(fill=tk.BOTH, expand=True)

    summary_shell = ttk.Frame(stack, style="Canvas.TFrame")
    positions_shell = ttk.Frame(stack, style="Canvas.TFrame")
    safety_shell = ttk.Frame(stack, style="Canvas.TFrame")
    stack.add(summary_shell, minsize=150, stretch="never")
    stack.add(positions_shell, minsize=260, stretch="always")
    stack.add(safety_shell, minsize=72, stretch="never")

    summary = ttk.LabelFrame(summary_shell, text="Account Snapshot", style="Card.TLabelframe")
    summary.pack(fill=tk.BOTH, expand=True)
    summary.columnconfigure((0, 1, 2), weight=1)

    self.cash_value_label = self._metric(summary, "Cash", 0)
    self.positions_value_label = self._metric(summary, "Positions", 1)
    self.total_value_label = self._metric(summary, "Total Value", 2)

    self.snapshot_source_label = ttk.Label(summary, text="Snapshot: --", style="Subtle.TLabel")
    self.snapshot_source_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(14, 0))

    snapshot_buttons = ttk.Frame(summary, style="Panel.TFrame")
    snapshot_buttons.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(12, 0))
    ttk.Button(snapshot_buttons, text="Reload Snapshot", command=self.reload_snapshot).pack(side=tk.LEFT)
    ttk.Button(snapshot_buttons, text="Refresh View", command=self.refresh_portfolio).pack(side=tk.LEFT, padx=(8, 0))

    positions_frame = ttk.LabelFrame(positions_shell, text="Positions", style="Card.TLabelframe")
    positions_frame.pack(fill=tk.BOTH, expand=True)

    table_wrap = ttk.Frame(positions_frame, style="Panel.TFrame")
    table_wrap.pack(fill=tk.BOTH, expand=True)

    columns = ("symbol", "qty", "avg_cost", "last", "value", "weight")
    self.positions_table = ttk.Treeview(table_wrap, columns=columns, show="headings", height=14)
    for column, label, width in [
        ("symbol", "Symbol", 90),
        ("qty", "Qty", 92),
        ("avg_cost", "Avg Cost", 112),
        ("last", "Last", 112),
        ("value", "Value", 122),
        ("weight", "Weight", 88),
    ]:
        self.positions_table.heading(column, text=label)
        self.positions_table.column(column, width=width, anchor=tk.E)
    self.positions_table.column("symbol", anchor=tk.W)
    self.positions_table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    scrollbar = ttk.Scrollbar(table_wrap, orient=tk.VERTICAL, command=self.positions_table.yview)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
    self.positions_table.configure(yscrollcommand=scrollbar.set)

    help_box = ttk.LabelFrame(safety_shell, text="Safety Rules", style="Card.TLabelframe")
    help_box.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        help_box,
        text=(
            "Schwab sync is read-only until preview checks, typed confirmation, max-size checks, "
            "margin checks, and audit logging are fully verified."
        ),
        wraplength=680,
        style="Subtle.TLabel",
    ).pack(anchor=tk.W)


def _metric(self: tk.Tk, parent: ttk.Frame, title: str, column: int) -> ttk.Label:
    card = ttk.Frame(parent, style="Panel.TFrame", padding=(10, 6))
    card.grid(row=0, column=column, rowspan=2, sticky="ew", padx=(0 if column == 0 else 8, 0))
    ttk.Label(card, text=title.upper(), style="MetricTitle.TLabel").pack(anchor=tk.W)
    value_label = ttk.Label(card, text="--", style="MetricValue.TLabel")
    value_label.pack(anchor=tk.W, pady=(3, 0))
    return value_label


def _build_order_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    stack = _make_paned(parent, tk.VERTICAL)
    stack.pack(fill=tk.BOTH, expand=True)

    ticket_shell = ttk.Frame(stack, style="Canvas.TFrame")
    preview_shell = ttk.Frame(stack, style="Canvas.TFrame")
    explainer_shell = ttk.Frame(stack, style="Canvas.TFrame")
    stack.add(ticket_shell, minsize=285, stretch="never")
    stack.add(preview_shell, minsize=220, stretch="always")
    stack.add(explainer_shell, minsize=78, stretch="never")

    ticket = ttk.LabelFrame(ticket_shell, text="Order Planner", style="Card.TLabelframe")
    ticket.pack(fill=tk.BOTH, expand=True)
    ticket.columnconfigure(1, weight=1)
    ticket.columnconfigure(3, weight=1)

    self._grid_row(ticket, 0, "Symbol", ttk.Entry(ticket, textvariable=self.symbol_var), "Side", ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"))
    self._grid_row(ticket, 1, "Order type", ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"), "Time", ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=[t.value for t in TimeInForce], state="readonly"))
    self._grid_row(ticket, 2, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var), "Est. price", ttk.Entry(ticket, textvariable=self.estimated_price_var))
    self._grid_row(ticket, 3, "Limit price", ttk.Entry(ticket, textvariable=self.limit_price_var), "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var))
    self._grid_row(ticket, 4, "Risk % cash", ttk.Entry(ticket, textvariable=self.risk_percent_var), "Type CONFIRM", ttk.Entry(ticket, textvariable=self.confirmation_var))

    ttk.Label(ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=5, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    primary_actions = ttk.Frame(ticket, style="Panel.TFrame")
    primary_actions.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(16, 0))
    primary_actions.columnconfigure((0, 1, 2), weight=1)
    ttk.Button(primary_actions, text="Preview Risk", command=self.preview_order, style="Accent.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Schwab Preview", command=self.run_schwab_preview).grid(row=0, column=1, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Submit Paper Order", command=self.submit_order).grid(row=0, column=2, sticky="ew")

    secondary_actions = ttk.Frame(ticket, style="Panel.TFrame")
    secondary_actions.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    for column in range(4):
        secondary_actions.columnconfigure(column, weight=1, uniform="actions")
    for index, (label, command, style_name) in enumerate([
        ("Recent Orders", self.load_schwab_open_orders, "TButton"),
        ("Open Only", self.load_schwab_open_orders_only, "TButton"),
        ("Reset Session", self.reset_schwab_session, "TButton"),
        ("Live Safety", self.show_live_submit_safety_review, "TButton"),
        ("Position Size", self.show_position_size, "TButton"),
        ("Checklist", self.show_manual_checklist, "TButton"),
        ("Cancel Order", self.show_cancel_order_placeholder, "Danger.TButton"),
        ("LIVE Submit", self.submit_live_schwab_order_guarded, "Danger.TButton"),
    ]):
        ttk.Button(secondary_actions, text=label, command=command, style=style_name).grid(
            row=index // 4,
            column=index % 4,
            sticky="ew",
            padx=(0 if index % 4 == 0 else 6, 0),
            pady=(0 if index < 4 else 6, 0),
        )

    status_bar = ttk.Frame(ticket, style="Panel.TFrame")
    status_bar.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    status_bar.columnconfigure((0, 1, 2), weight=1)
    ttk.Label(status_bar, textvariable=self.schwab_status_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.schwab_preview_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.schwab_verification_status_var, style="Chip.TLabel").grid(row=0, column=2, sticky="ew", pady=(4, 0))

    results = ttk.LabelFrame(preview_shell, text="Risk Preview + Instructions", style="Card.TLabelframe")
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
        "Create an order and click Preview Risk.\n\n"
        "Tip: drag the splitters between panels to resize the cockpit for your workflow.\n\n"
        "Reminder: live Schwab orders require staged safety checks before anything can be submitted."
    )

    explainer = ttk.LabelFrame(explainer_shell, text="Order Type Cheat Sheet", style="Card.TLabelframe")
    explainer.pack(fill=tk.BOTH, expand=True)
    ttk.Label(
        explainer,
        text=(
            "Limit buy = maximum price. Limit sell = minimum price. Stop = trigger order. "
            "Stop-limit = trigger plus limit, but may not fill."
        ),
        wraplength=460,
        style="Subtle.TLabel",
    ).pack(anchor=tk.W)


def _grid_row(
    self: tk.Tk,
    parent: ttk.Frame,
    row: int,
    label_a: str,
    widget_a: tk.Widget,
    label_b: str,
    widget_b: tk.Widget,
) -> None:
    ttk.Label(parent, text=label_a, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=7)
    widget_a.grid(row=row, column=1, sticky="ew", padx=(0, 16), pady=7)
    ttk.Label(parent, text=label_b, style="Subtle.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 8), pady=7)
    widget_b.grid(row=row, column=3, sticky="ew", pady=7)


def _set_preview_text(self: tk.Tk, content: str) -> None:
    self.preview_text.configure(state=tk.NORMAL)
    self.preview_text.delete("1.0", tk.END)
    self.preview_text.insert(tk.END, content)
    self.preview_text.configure(state=tk.DISABLED)
