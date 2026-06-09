from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Type

from app.analytics.risk_alerts import AlertSeverity, evaluate_portfolio_risk
from app.core.order_models import SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, OrderSide, OrderType, TimeInForce
from app.core.portfolio import Portfolio


DARK_MODE = True

CANVAS = "#070d19"
SURFACE = "#0f172a"
PANEL = "#111827"
PANEL_ALT = "#1f2937"
BORDER = "#334155"
TEXT = "#e5e7eb"
MUTED = "#94a3b8"
ACCENT = "#2563eb"
ACCENT_DARK = "#1d4ed8"
ACCENT_SOFT = "#60a5fa"
DANGER = "#fb7185"
INPUT = "#0b1220"
OUTPUT = "#08111f"
TREE_HEADING = "#1e293b"
SELECTED = "#1d4ed8"
SELECTED_TEXT = "#f8fafc"
DISABLED_BG = "#172033"
DISABLED_TEXT = "#64748b"
POSITIVE = "#34d399"
NEGATIVE = DANGER
WARNING = "#fbbf24"
CASH = MUTED
LINK = "#93c5fd"


def dark_text_options(**overrides: object) -> dict[str, object]:
    """Return readable defaults for raw ``tk.Text`` widgets on dark panels."""

    options: dict[str, object] = {
        "relief": tk.FLAT,
        "borderwidth": 0,
        "background": OUTPUT,
        "foreground": TEXT,
        "insertbackground": TEXT,
        "selectbackground": SELECTED,
        "selectforeground": SELECTED_TEXT,
        "highlightthickness": 1,
        "highlightbackground": BORDER,
        "highlightcolor": ACCENT,
    }
    options.update(overrides)
    return options


def configure_text_widget(text: tk.Text, **overrides: object) -> None:
    """Apply the dark text palette to an already-created raw text widget."""

    try:
        text.configure(**dark_text_options(**overrides))
    except tk.TclError:
        return


def configure_toplevel(window: tk.Toplevel | tk.Tk) -> None:
    """Keep raw toplevel backgrounds aligned with the dark ttk palette."""

    try:
        window.configure(bg=CANVAS)
    except tk.TclError:
        return


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
    app_cls.refresh_portfolio = _refresh_portfolio  # type: ignore[method-assign]
    app_cls.connect_schwab = _connect_schwab  # type: ignore[method-assign]
    app_cls.refresh_schwab_account = _refresh_schwab_account  # type: ignore[method-assign]
    app_cls.run_schwab_preview = _run_schwab_preview  # type: ignore[method-assign]
    app_cls._grid_row = _grid_row  # type: ignore[method-assign]
    app_cls._set_preview_text = _set_preview_text  # type: ignore[method-assign]


def _configure_style(self: tk.Tk) -> None:
    self.option_add("*Font", "{Segoe UI} 10")
    self.option_add("*Text.background", OUTPUT)
    self.option_add("*Text.foreground", TEXT)
    self.option_add("*Text.insertBackground", TEXT)
    self.option_add("*Text.selectBackground", SELECTED)
    self.option_add("*Text.selectForeground", SELECTED_TEXT)
    self.option_add("*Canvas.background", PANEL)
    self.option_add("*Toplevel.background", CANVAS)
    self.configure(bg=CANVAS)

    style = ttk.Style(self)
    style.theme_use("clam")

    style.configure(
        ".",
        font=("Segoe UI", 10),
        background=PANEL,
        foreground=TEXT,
        fieldbackground=INPUT,
        bordercolor=BORDER,
        darkcolor=BORDER,
        lightcolor=BORDER,
        troughcolor=CANVAS,
        selectbackground=SELECTED,
        selectforeground=SELECTED_TEXT,
    )
    style.configure("TFrame", background=PANEL)
    style.configure("Canvas.TFrame", background=CANVAS)
    style.configure("Hero.TFrame", background=SURFACE)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("TLabel", background=PANEL, foreground=TEXT)
    style.configure("Card.TLabelframe", background=PANEL, bordercolor=BORDER, relief="solid", padding=16)
    style.configure("Card.TLabelframe.Label", background=PANEL, foreground=TEXT, font=("Segoe UI", 11, "bold"))

    style.configure("Header.TLabel", background=SURFACE, foreground="#ffffff", font=("Segoe UI", 22, "bold"))
    style.configure("HeroSubtle.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Subtle.TLabel", background=PANEL, foreground=MUTED)
    style.configure("Danger.TLabel", background=PANEL, foreground=DANGER, font=("Segoe UI", 10, "bold"))
    style.configure("Mode.TLabel", background=SURFACE, foreground=POSITIVE, font=("Segoe UI", 10, "bold"))
    style.configure("MetricTitle.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9, "bold"))
    style.configure("MetricValue.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 18, "bold"))
    style.configure("Chip.TLabel", background=PANEL_ALT, foreground=ACCENT_SOFT, font=("Segoe UI", 9, "bold"), padding=(8, 4))

    style.configure("TButton", background=PANEL_ALT, foreground=TEXT, padding=(10, 7), borderwidth=0, focusthickness=1, focuscolor=BORDER)
    style.map(
        "TButton",
        background=[("disabled", DISABLED_BG), ("pressed", "#263449"), ("active", "#243044")],
        foreground=[("disabled", DISABLED_TEXT), ("active", TEXT)],
    )
    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", font=("Segoe UI", 10, "bold"), padding=(12, 8))
    style.map("Accent.TButton", background=[("disabled", DISABLED_BG), ("active", ACCENT_DARK), ("pressed", ACCENT_DARK)], foreground=[("disabled", DISABLED_TEXT), ("active", "#ffffff")])
    style.configure("Danger.TButton", background="#3b0a19", foreground=DANGER, font=("Segoe UI", 10, "bold"), padding=(10, 7))
    style.map("Danger.TButton", background=[("disabled", DISABLED_BG), ("active", "#4a1020"), ("pressed", "#4a1020")], foreground=[("disabled", DISABLED_TEXT), ("active", "#fecdd3")])

    style.configure("TCheckbutton", background=PANEL, foreground=TEXT)
    style.map("TCheckbutton", background=[("active", PANEL), ("disabled", PANEL)], foreground=[("disabled", DISABLED_TEXT)])
    style.configure("TRadiobutton", background=PANEL, foreground=TEXT)
    style.map("TRadiobutton", background=[("active", PANEL), ("disabled", PANEL)], foreground=[("disabled", DISABLED_TEXT)])

    style.configure("TNotebook", background=CANVAS, borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure("TNotebook.Tab", background=SURFACE, foreground=MUTED, padding=(14, 8), borderwidth=0)
    style.map(
        "TNotebook.Tab",
        background=[("selected", PANEL), ("active", PANEL_ALT)],
        foreground=[("selected", TEXT), ("active", TEXT), ("disabled", DISABLED_TEXT)],
    )

    style.configure("TEntry", fieldbackground=INPUT, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=6)
    style.map(
        "TEntry",
        fieldbackground=[("disabled", DISABLED_BG), ("readonly", INPUT), ("focus", INPUT)],
        foreground=[("disabled", DISABLED_TEXT), ("readonly", TEXT)],
        bordercolor=[("focus", ACCENT), ("disabled", BORDER)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=INPUT,
        background=INPUT,
        foreground=TEXT,
        arrowcolor=TEXT,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        padding=6,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", INPUT), ("focus", INPUT), ("disabled", DISABLED_BG)],
        background=[("readonly", INPUT), ("active", PANEL_ALT), ("disabled", DISABLED_BG)],
        foreground=[("readonly", TEXT), ("focus", TEXT), ("disabled", DISABLED_TEXT)],
        selectbackground=[("readonly", INPUT), ("focus", SELECTED)],
        selectforeground=[("readonly", TEXT), ("focus", SELECTED_TEXT)],
        arrowcolor=[("disabled", DISABLED_TEXT), ("active", TEXT), ("readonly", TEXT)],
        bordercolor=[("focus", ACCENT), ("disabled", BORDER)],
    )

    for scrollbar_style in ("TScrollbar", "Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(
            scrollbar_style,
            background=PANEL_ALT,
            troughcolor=CANVAS,
            bordercolor=BORDER,
            arrowcolor=MUTED,
            darkcolor=PANEL_ALT,
            lightcolor=PANEL_ALT,
            relief=tk.FLAT,
        )
        style.map(
            scrollbar_style,
            background=[("active", "#263449"), ("pressed", "#263449"), ("disabled", DISABLED_BG)],
            arrowcolor=[("active", TEXT), ("disabled", DISABLED_TEXT)],
        )

    style.configure("Treeview", background=INPUT, fieldbackground=INPUT, foreground=TEXT, rowheight=30, bordercolor=BORDER, borderwidth=0)
    style.configure("Treeview.Heading", background=TREE_HEADING, foreground=TEXT, font=("Segoe UI", 9, "bold"), padding=8, relief=tk.FLAT)
    style.map("Treeview", background=[("selected", SELECTED)], foreground=[("selected", SELECTED_TEXT)])
    style.map("Treeview.Heading", background=[("active", PANEL_ALT)], foreground=[("active", TEXT)])


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
    ttk.Label(status_stack, text="TRADE CENTER", style="Mode.TLabel").pack(anchor=tk.E)
    ttk.Label(status_stack, text="Lock In", style="HeroSubtle.TLabel").pack(anchor=tk.E, pady=(4, 0))


def _build_portfolio_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    stack = _make_paned(parent, tk.VERTICAL)
    stack.pack(fill=tk.BOTH, expand=True)

    summary_shell = ttk.Frame(stack, style="Canvas.TFrame")
    positions_shell = ttk.Frame(stack, style="Canvas.TFrame")
    alerts_shell = ttk.Frame(stack, style="Canvas.TFrame")
    stack.add(summary_shell, minsize=150, stretch="never")
    stack.add(positions_shell, minsize=260, stretch="always")
    stack.add(alerts_shell, minsize=120, stretch="never")

    summary = ttk.LabelFrame(summary_shell, text="Account Snapshot", style="Card.TLabelframe")
    summary.pack(fill=tk.BOTH, expand=True)
    summary.columnconfigure((0, 1, 2, 3, 4), weight=1)

    self.cash_value_label = self._metric(summary, "Cash", 0)
    self.positions_value_label = self._metric(summary, "Positions", 1)
    self.total_value_label = self._metric(summary, "Total Value", 2)
    self.unrealized_pnl_value_label = self._metric(summary, "Unrealized P&L", 3)
    self.day_pnl_value_label = self._metric(summary, "Day P&L", 4)

    self.snapshot_source_label = ttk.Label(summary, text="Snapshot: --", style="Subtle.TLabel")
    self.snapshot_source_label.grid(row=2, column=0, columnspan=5, sticky="w", pady=(14, 0))

    snapshot_buttons = ttk.Frame(summary, style="Panel.TFrame")
    snapshot_buttons.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(12, 0))
    ttk.Button(snapshot_buttons, text="Reload Snapshot", command=self.reload_snapshot).pack(side=tk.LEFT)
    ttk.Button(snapshot_buttons, text="Refresh View", command=self.refresh_portfolio).pack(side=tk.LEFT, padx=(8, 0))

    positions_frame = ttk.LabelFrame(positions_shell, text="Positions", style="Card.TLabelframe")
    positions_frame.pack(fill=tk.BOTH, expand=True)

    table_wrap = ttk.Frame(positions_frame, style="Panel.TFrame")
    table_wrap.pack(fill=tk.BOTH, expand=True)
    table_wrap.rowconfigure(0, weight=1)
    table_wrap.columnconfigure(0, weight=1)

    columns = (
        "symbol",
        "qty",
        "avg_cost",
        "last",
        "cost_basis",
        "value",
        "weight",
        "pnl",
        "pnl_pct",
        "day_pnl",
    )
    self.positions_table = ttk.Treeview(table_wrap, columns=columns, show="headings", height=14)
    for column, label, width in [
        ("symbol", "Symbol", 90),
        ("qty", "Qty", 92),
        ("avg_cost", "Avg Cost", 112),
        ("last", "Last", 112),
        ("cost_basis", "Cost Basis", 118),
        ("value", "Value", 122),
        ("weight", "Weight", 88),
        ("pnl", "Unrlzd $", 112),
        ("pnl_pct", "Unrlzd %", 86),
        ("day_pnl", "Day P&L", 112),
    ]:
        self.positions_table.heading(column, text=label)
        self.positions_table.column(column, width=width, anchor=tk.E, stretch=True)
    self.positions_table.column("symbol", anchor=tk.W)
    self.positions_table.tag_configure("pnl_positive", foreground=POSITIVE)
    self.positions_table.tag_configure("pnl_negative", foreground=NEGATIVE)
    self.positions_table.grid(row=0, column=0, sticky="nsew")

    y_scrollbar = ttk.Scrollbar(table_wrap, orient=tk.VERTICAL, command=self.positions_table.yview)
    y_scrollbar.grid(row=0, column=1, sticky="ns")
    x_scrollbar = ttk.Scrollbar(table_wrap, orient=tk.HORIZONTAL, command=self.positions_table.xview)
    x_scrollbar.grid(row=1, column=0, sticky="ew")
    self.positions_table.configure(yscrollcommand=y_scrollbar.set, xscrollcommand=x_scrollbar.set)

    alerts_box = ttk.LabelFrame(alerts_shell, text="Risk Alerts", style="Card.TLabelframe")
    alerts_box.pack(fill=tk.BOTH, expand=True)
    self.risk_alerts_text = tk.Text(
        alerts_box,
        **dark_text_options(height=5, wrap=tk.WORD, font=("Segoe UI", 10), padx=10, pady=8),
    )
    self.risk_alerts_text.pack(fill=tk.BOTH, expand=True)
    self.risk_alerts_text.configure(state=tk.DISABLED)


def _metric(self: tk.Tk, parent: ttk.Frame, title: str, column: int) -> ttk.Label:
    card = ttk.Frame(parent, style="Panel.TFrame", padding=(10, 6))
    card.grid(row=0, column=column, rowspan=2, sticky="ew", padx=(0 if column == 0 else 8, 0))
    ttk.Label(card, text=title.upper(), style="MetricTitle.TLabel").pack(anchor=tk.W)
    value_label = ttk.Label(card, text="--", style="MetricValue.TLabel")
    value_label.pack(anchor=tk.W, pady=(3, 0))
    return value_label


def _refresh_portfolio(self: tk.Tk) -> None:
    portfolio = self.broker.get_portfolio()
    self.cash_value_label.configure(text=_format_money(portfolio.cash))
    self.positions_value_label.configure(text=_format_money(portfolio.positions_value))
    self.total_value_label.configure(text=_format_money(portfolio.total_value))
    self.unrealized_pnl_value_label.configure(
        text=f"{_format_money(portfolio.unrealized_profit_loss)} ({_format_percent(portfolio.unrealized_profit_loss_percent)})"
    )
    self.day_pnl_value_label.configure(text=_format_optional_money(portfolio.day_profit_loss))
    self.snapshot_source_label.configure(text=f"Snapshot: {self.broker.source_message}")

    for row_id in self.positions_table.get_children():
        self.positions_table.delete(row_id)

    total_value = max(portfolio.total_value, 0.01)
    for symbol in sorted(portfolio.positions):
        p = portfolio.positions[symbol]
        weight = (p.market_value / total_value) * 100
        row_tag = "pnl_positive" if p.unrealized_profit_loss >= 0 else "pnl_negative"
        pnl_text = _format_money(p.unrealized_profit_loss) if p.unrealized_profit_loss_known else "--"
        pnl_percent_text = _format_percent(p.unrealized_profit_loss_percent) if p.unrealized_profit_loss_known else "--"
        self.positions_table.insert(
            "",
            tk.END,
            values=(
                p.symbol,
                f"{p.quantity:g}",
                _format_money(p.average_cost),
                _format_money(p.last_price),
                _format_money(p.cost_basis),
                _format_money(p.market_value),
                f"{weight:.1f}%",
                pnl_text,
                pnl_percent_text,
                _format_optional_money(p.day_profit_loss),
            ),
            tags=(row_tag,),
        )

    _update_risk_alerts(self, portfolio)


def _update_risk_alerts(self: tk.Tk, portfolio: Portfolio) -> None:
    if not hasattr(self, "risk_alerts_text"):
        return

    alerts = evaluate_portfolio_risk(portfolio)
    lines = []
    for alert in alerts:
        symbol = f" [{alert.symbol}]" if alert.symbol else ""
        lines.append(f"{_severity_prefix(alert.severity)}{symbol} {alert.title}: {alert.message}")

    self.risk_alerts_text.configure(state=tk.NORMAL)
    self.risk_alerts_text.delete("1.0", tk.END)
    self.risk_alerts_text.insert(tk.END, "\n".join(lines))
    self.risk_alerts_text.configure(state=tk.DISABLED)


def _severity_prefix(severity: AlertSeverity) -> str:
    if severity == AlertSeverity.CRITICAL:
        return "CRITICAL"
    if severity == AlertSeverity.WARNING:
        return "WATCH"
    return "INFO"


def _connect_schwab(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        self.schwab_session = session
        source_message = self._sync_schwab_account_snapshot(session)
        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(
            "SCHWAB CONNECTED + ACCOUNT REFRESHED\n"
            "====================================\n\n"
            f"{source_message}\n\n"
            "The cockpit is connected to Schwab, and the left-side account snapshot, positions, P&L, and risk alerts were refreshed.\n\n"
            "Next step: click Preview Order only when you want Schwab to validate the current ticket.\n\n"
            "No order was previewed, submitted, replaced, or canceled."
        )
    except Exception as exc:
        self.schwab_session = None
        self.schwab_status_var.set("Schwab session: not connected")
        messagebox.showerror("Schwab connect failed", str(exc))


def _refresh_schwab_account(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        source_message = self._sync_schwab_account_snapshot(session)
        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(
            "SCHWAB ACCOUNT REFRESHED\n"
            "========================\n\n"
            f"{source_message}\n\n"
            "Balances, positions, P&L, and risk alerts were refreshed from Schwab.\n\n"
            "No order was previewed, submitted, replaced, or canceled."
        )
    except Exception as exc:
        messagebox.showerror("Schwab account refresh failed", str(exc))


def _run_schwab_preview(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return

        status_code, preview_payload = session.preview_order(self.build_schwab_order_json_from_ui())
        self.schwab_status_var.set("Schwab session: connected")
        if isinstance(preview_payload, dict):
            self._record_schwab_preview_status(preview_payload)
        else:
            self.last_schwab_preview_status = "UNKNOWN"
            self.schwab_preview_status_var.set("Last Schwab preview: UNKNOWN")
        self._set_preview_text(self.format_schwab_preview_response(status_code, preview_payload))
    except Exception as exc:
        self.last_schwab_preview_status = None
        self.schwab_preview_status_var.set("Last Schwab preview: none")
        messagebox.showerror("Schwab order preview failed", str(exc))


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _format_optional_money(value: float | None) -> str:
    return "--" if value is None else _format_money(value)


def _format_percent(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}%"


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
    stack.add(ticket_shell, minsize=245, stretch="never")
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
    primary_actions.columnconfigure((0, 1, 2, 3), weight=1)
    ttk.Button(primary_actions, text="Preview Risk", command=self.preview_order, style="Accent.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Connect Schwab", command=self.connect_schwab).grid(row=0, column=1, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Refresh Account", command=self.refresh_schwab_account).grid(row=0, column=2, sticky="ew", padx=(0, 8))
    ttk.Button(primary_actions, text="Tech Analysis", command=self.show_technical_analysis).grid(row=0, column=3, sticky="ew")

    secondary_actions = ttk.Frame(ticket, style="Panel.TFrame")
    secondary_actions.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    for column in range(4):
        secondary_actions.columnconfigure(column, weight=1, uniform="actions")
    for index, (label, command, style_name) in enumerate([
        ("Preview Order", self.run_schwab_preview, "TButton"),
        ("Recent Orders", self.load_schwab_open_orders, "TButton"),
        ("Open Only", self.load_schwab_open_orders_only, "TButton"),
        ("Reset Session", self.reset_schwab_session, "TButton"),
        ("Cancel Order", self.show_cancel_order_placeholder, "Danger.TButton"),
        ("LIVE Submit", self.submit_live_schwab_order, "Danger.TButton"),
    ]):
        ttk.Button(secondary_actions, text=label, command=command, style=style_name).grid(
            row=index // 4,
            column=index % 4,
            sticky="ew",
            padx=(0 if index % 4 == 0 else 6, 0),
            pady=(0 if index < 4 else 6, 0),
        )

    status_bar = ttk.Frame(ticket, style="Panel.TFrame")
    status_bar.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    status_bar.columnconfigure((0, 1, 2), weight=1)
    ttk.Label(status_bar, textvariable=self.schwab_status_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.schwab_preview_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.schwab_verification_status_var, style="Chip.TLabel").grid(row=0, column=2, sticky="ew", pady=(4, 0))

    results = ttk.LabelFrame(preview_shell, text="Analysis + Instructions", style="Card.TLabelframe")
    results.pack(fill=tk.BOTH, expand=True)

    self.preview_text = tk.Text(
        results,
        **dark_text_options(height=18, wrap=tk.WORD, font=("Cascadia Mono", 10), padx=14, pady=12),
    )
    self.preview_text.pack(fill=tk.BOTH, expand=True)
    self._set_preview_text(
        "Create a ticket, then use Preview Risk, Tech Analysis, or Trade Setup.\n\n"
        "Entry / Limit is the single planning price used for local risk, trade setup, and Schwab limit-order preview.\n\n"
        "Reminder: live Schwab actions remain behind explicit safety checks."
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
    styler = getattr(self.preview_text, "_apply_report_style", None)
    if callable(styler):
        styler(content)
    self.preview_text.configure(state=tk.DISABLED)
