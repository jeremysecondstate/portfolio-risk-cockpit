from __future__ import annotations

from datetime import datetime
import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Type

from app.brokers.hyperliquid.client import HyperliquidInfoClient
from app.brokers.hyperliquid.trading import (
    HyperliquidExecutionAdapter,
    HyperliquidOrderTicket,
    HyperliquidTradingConfig,
    normalize_hyperliquid_coin,
    normalize_hyperliquid_spot_market,
)
from app.core.order_models import OrderSide, OrderType, TimeInForce
from app.ui import polished_theme
from app.ui.polished_theme import _make_paned


TRADING_VENUES = ["Schwab", "Hyperliquid"]
HYPERLIQUID_TIFS = ["Alo", "Ioc", "Gtc"]
HYPERLIQUID_ADDRESS_ENV_KEYS = ("HYPE_WALLET_ADDRESS", "HYPERLIQUID_USER_ADDRESS")


def install_hyperliquid_trading_extension(app_cls: Type[tk.Tk]) -> None:
    """Add a venue selector and guarded Hyperliquid ticket workflow."""

    app_cls.submit_cockpit_selected_venue = _submit_cockpit_selected_venue  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_spot_live_submit_safety_review = _show_hyperliquid_spot_live_submit_safety_review  # type: ignore[attr-defined]
    app_cls.parse_hyperliquid_spot_ticket = _parse_hyperliquid_spot_ticket  # type: ignore[attr-defined]
    app_cls.submit_selected_venue = _submit_selected_venue  # type: ignore[attr-defined]
    app_cls.cancel_selected_order = _cancel_selected_order  # type: ignore[attr-defined]
    app_cls.cancel_hyperliquid_order_guarded = _cancel_hyperliquid_order_guarded  # type: ignore[attr-defined]
    app_cls.load_selected_recent_orders = _load_selected_recent_orders  # type: ignore[attr-defined]
    app_cls._build_order_panel = _build_order_panel_with_hyperliquid  # type: ignore[method-assign]
    app_cls.load_selected_open_orders_only = _load_selected_open_orders_only  # type: ignore[attr-defined]
    app_cls.load_hyperliquid_open_orders = _load_hyperliquid_open_orders  # type: ignore[attr-defined]
    app_cls.preview_hyperliquid_ticket = _preview_hyperliquid_ticket  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_live_submit_safety_review = _show_hyperliquid_live_submit_safety_review  # type: ignore[attr-defined]
    app_cls.parse_hyperliquid_ticket = _parse_hyperliquid_ticket  # type: ignore[attr-defined]
    app_cls.on_trading_venue_changed = _on_trading_venue_changed  # type: ignore[attr-defined]
    app_cls.apply_hyperliquid_quantity_percent = _apply_hyperliquid_quantity_percent  # type: ignore[attr-defined]


def _ensure_hyperliquid_vars(self: tk.Tk) -> None:
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
    if not hasattr(self, "hyperliquid_open_order_coin_by_oid"):
        self.hyperliquid_open_order_coin_by_oid = {}
    if not hasattr(self, "hyperliquid_size_percent_var"):
        self.hyperliquid_size_percent_var = tk.DoubleVar(value=0.0)
    if not hasattr(self, "hyperliquid_size_status_var"):
        self.hyperliquid_size_status_var = tk.StringVar(value="Sync Hyperliquid, then choose a size %")


def _configure_compact_ticket_styles(self: tk.Tk) -> None:
    style = ttk.Style(self)
    style.configure("Compact.Card.TLabelframe", background=polished_theme.PANEL, bordercolor=polished_theme.BORDER, relief="solid", padding=8)
    style.configure("Compact.Card.TLabelframe.Label", background=polished_theme.PANEL, foreground=polished_theme.TEXT, font=("Segoe UI", 10, "bold"))
    style.configure("Compact.TButton", padding=(6, 5), font=("Segoe UI", 9))
    style.configure("CompactAccent.TButton", background=polished_theme.ACCENT, foreground="#ffffff", padding=(6, 5), font=("Segoe UI", 9, "bold"))
    style.map("CompactAccent.TButton", background=[("active", polished_theme.ACCENT_DARK), ("pressed", polished_theme.ACCENT_DARK)], foreground=[("active", "#ffffff")])
    style.configure("CompactDanger.TButton", background="#fee2e2", foreground=polished_theme.DANGER, padding=(6, 5), font=("Segoe UI", 9, "bold"))
    style.map("CompactDanger.TButton", background=[("active", "#fecaca")])


def _build_action_group(parent: ttk.Frame, title: str, column: int) -> ttk.LabelFrame:
    group = ttk.LabelFrame(parent, text=title, style="Compact.Card.TLabelframe")
    group.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
    group.columnconfigure(0, weight=1)
    group.columnconfigure(1, weight=1)
    return group


def _grid_action_button(
    parent: ttk.LabelFrame,
    row: int,
    column: int,
    text: str,
    command: object,
    style: str = "Compact.TButton",
    columnspan: int = 1,
) -> None:
    ttk.Button(parent, text=text, command=command, style=style).grid(
        row=row,
        column=column,
        columnspan=columnspan,
        sticky="ew",
        padx=(0, 6) if column == 0 and columnspan == 1 else 0,
        pady=(2, 3) if row == 0 else (0, 2),
    )


def _use_mid_from_cockpit(self: tk.Tk) -> None:
    """Route the Cockpit Trade Planner's inline Use Mid button to the Hyperliquid helper."""
    _ensure_hyperliquid_vars(self)
    self.trade_venue_var.set("Hyperliquid")
    try:
        self.on_trading_venue_changed()
    except Exception:
        pass

    command = getattr(self, "use_hyperliquid_mid_market", None)
    if callable(command):
        command()
        return
    messagebox.showinfo(
        "Use Mid unavailable",
        "The Hyperliquid mid-market helper is not installed yet. Restart the app after pulling the latest changes.",
    )


def _build_order_panel_with_hyperliquid(self: tk.Tk, parent: ttk.Frame) -> None:
    _ensure_hyperliquid_vars(self)
    _configure_compact_ticket_styles(self)

    stack = _make_paned(parent, tk.VERTICAL)
    stack.pack(fill=tk.BOTH, expand=True)

    ticket_shell = ttk.Frame(stack, style="Canvas.TFrame")
    preview_shell = ttk.Frame(stack, style="Canvas.TFrame")
    explainer_shell = ttk.Frame(stack, style="Canvas.TFrame")
    stack.add(ticket_shell, minsize=560, stretch="never")
    stack.add(preview_shell, minsize=150, stretch="always")
    stack.add(explainer_shell, minsize=58, stretch="never")

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
    _grid_hyperliquid_size_controls(ticket, self, row=4)
    self._grid_row(ticket, 5, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var), "Use Mid", ttk.Button(ticket, text="Use Mid", command=lambda: _use_mid_from_cockpit(self), style="Accent.TButton"))
    self._grid_row(
        ticket,
        6,
        "HL TIF",
        ttk.Combobox(ticket, textvariable=self.hyperliquid_tif_var, values=HYPERLIQUID_TIFS, state="readonly"),
        "HL Reduce-only",
        ttk.Checkbutton(ticket, variable=self.hyperliquid_reduce_only_var),
    )

    ttk.Label(ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=7, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=7, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    actions = ttk.Frame(ticket, style="Panel.TFrame")
    actions.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    actions.columnconfigure((0, 1, 2), weight=1, uniform="action_groups")

    connect_group = _build_action_group(actions, "Connections", 0)
    plan_group = _build_action_group(actions, "Planning", 1)
    live_group = _build_action_group(actions, "Guarded Live Actions", 2)

    _grid_action_button(connect_group, 0, 0, "Schwab", self.connect_schwab)
    _grid_action_button(connect_group, 0, 1, "Hyperliquid", self.sync_hyperliquid_account)
    _grid_action_button(connect_group, 1, 0, "Refresh", self.refresh_schwab_account)
    _grid_action_button(connect_group, 1, 1, "Reset", self.reset_schwab_session)
    _grid_action_button(plan_group, 1, 0, "Tech", self.show_technical_analysis, columnspan=2)

    _grid_action_button(live_group, 0, 0, "Recent", self.load_selected_recent_orders)
    _grid_action_button(live_group, 0, 1, "Open", self.load_selected_open_orders_only)
    _grid_action_button(live_group, 1, 0, "Cancel", self.cancel_selected_order, "CompactDanger.TButton")
    _grid_action_button(live_group, 1, 1, "Submit", self.submit_cockpit_selected_venue, "CompactDanger.TButton")

    status_bar = ttk.Frame(ticket, style="Panel.TFrame")
    status_bar.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(10, 0))
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
        "Choose Schwab or Hyperliquid, create a ticket, then use the grouped planning and guarded live actions.\n\n"
        "Recent Orders and Open Only now follow the selected venue. Schwab uses Schwab orders; Hyperliquid reads active open orders.\n\n"
        "For Hyperliquid, Use Mid pulls the current Hyperliquid allMids price into Entry / Limit. LIVE Submit checks env readiness + max notional, then calls the local hook."
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


def _grid_hyperliquid_size_controls(parent: ttk.LabelFrame, self: tk.Tk, row: int) -> None:
    ttk.Label(parent, text="Size %", style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)

    controls = ttk.Frame(parent, style="Panel.TFrame")
    controls.grid(row=row, column=1, columnspan=3, sticky="ew", pady=6)
    controls.columnconfigure(0, weight=1)

    scale = ttk.Scale(
        controls,
        from_=0,
        to=100,
        orient=tk.HORIZONTAL,
        variable=self.hyperliquid_size_percent_var,
        command=lambda _value: _apply_hyperliquid_quantity_percent(self),
    )
    scale.grid(row=0, column=0, sticky="ew", padx=(0, 8))

    for column, percent in enumerate((25, 50, 75), start=1):
        ttk.Button(
            controls,
            text=f"{percent}%",
            command=lambda value=percent: _apply_hyperliquid_quantity_percent(self, value),
            style="Compact.TButton",
        ).grid(row=0, column=column, sticky="ew", padx=(0, 6))

    ttk.Button(
        controls,
        text="Max",
        command=lambda: _apply_hyperliquid_quantity_percent(self, 100),
        style="CompactAccent.TButton",
    ).grid(row=0, column=4, sticky="ew")

    ttk.Label(controls, textvariable=self.hyperliquid_size_status_var, style="Subtle.TLabel").grid(
        row=1,
        column=0,
        columnspan=5,
        sticky="w",
        pady=(3, 0),
    )


def _show_hyperliquid_spot_live_submit_safety_review(self: tk.Tk) -> None:
    try:
        ticket = self.parse_hyperliquid_spot_ticket()
        config = HyperliquidTradingConfig()
        self._set_preview_text(config.live_review_text(ticket))
        result = HyperliquidExecutionAdapter().submit(ticket)
        self.hyperliquid_status_var.set("Hyperliquid spot: submit attempted")
        self._set_preview_text(
            "HYPERLIQUID SPOT LIVE SUBMIT RESULT\n"
            "===================================\n\n"
            f"{result}\n\n"
            "Refreshing Hyperliquid account snapshot..."
        )
        try:
            self.sync_hyperliquid_account()
        except Exception:
            pass
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid spot: live blocked")
        messagebox.showerror("Hyperliquid spot live submit blocked", str(exc))


def _submit_cockpit_selected_venue(self: tk.Tk) -> None:
    if _selected_venue_is_hyperliquid(self):
        self.show_hyperliquid_spot_live_submit_safety_review()
        return
    self.submit_live_schwab_order_guarded()


def _selected_venue_is_hyperliquid(self: tk.Tk) -> bool:
    return getattr(self, "trade_venue_var", tk.StringVar(value="Schwab")).get() == "Hyperliquid"


def _apply_hyperliquid_quantity_percent(self: tk.Tk, percent: float | None = None) -> None:
    _ensure_hyperliquid_vars(self)
    if percent is not None:
        self.hyperliquid_size_percent_var.set(float(percent))
    percent_value = max(0.0, min(100.0, float(self.hyperliquid_size_percent_var.get())))

    try:
        max_size, basis = _hyperliquid_max_spot_size(self)
    except Exception as exc:
        self.hyperliquid_size_status_var.set(f"Size helper: {exc}")
        return

    if max_size <= 0:
        self.hyperliquid_size_status_var.set(f"Size helper: no available {basis}")
        return

    size = max_size * (percent_value / 100.0)
    self.quantity_var.set(_format_hyperliquid_size(size))
    self.hyperliquid_size_status_var.set(
        f"Size helper: {percent_value:.0f}% of {basis} = {_format_hyperliquid_size(size)}"
    )


def _hyperliquid_max_spot_size(self: tk.Tk) -> tuple[float, str]:
    if not _selected_venue_is_hyperliquid(self):
        raise ValueError("switch Venue to Hyperliquid")

    market = normalize_hyperliquid_spot_market(
        self.symbol_var.get().strip() or self.hyperliquid_coin_var.get().strip()
    )
    base = _display_spot_base(market)
    side = self.side_var.get().strip().lower()

    if side == "sell":
        quantity = _hyperliquid_spot_balance(self, base)
        return quantity, f"{base} spot balance"

    if side == "buy":
        limit_price = _positive_float(self.limit_price_var.get())
        if limit_price is None:
            raise ValueError("enter a positive limit price first")
        usdc = _hyperliquid_usdc_balance(self)
        return usdc / limit_price, f"USDC balance at ${limit_price:,.4f}"

    raise ValueError("choose buy or sell")


def _hyperliquid_spot_balance(self: tk.Tk, base: str) -> float:
    portfolio = self.broker.get_portfolio()
    for position in portfolio.positions.values():
        symbol = _display_spot_base(position.symbol)
        position_type = str(getattr(position, "asset_type", "")).strip().lower()
        if symbol == base and (position_type == "spot" or position.symbol.upper().endswith("-SPOT")):
            return max(float(position.quantity), 0.0)
    return 0.0


def _hyperliquid_usdc_balance(self: tk.Tk) -> float:
    portfolio = self.broker.get_portfolio()
    for cash in portfolio.cash_positions.values():
        if cash.symbol.strip().upper() == "USDC" and "HYPERLIQUID" in cash.source.strip().upper():
            return max(float(cash.amount), 0.0)
    return 0.0


def _display_spot_base(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean.startswith("HL:"):
        clean = clean[3:]
    if "/" in clean:
        clean = clean.split("/", 1)[0]
    for suffix in ("-SPOT", "-PERP-SHORT", "-PERP"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
    if clean.startswith("U") and len(clean) > 1:
        clean = clean[1:]
    return clean


def _positive_float(value: str) -> float | None:
    try:
        number = float(value.strip().replace(",", ""))
    except ValueError:
        return None
    return number if number > 0 else None


def _format_hyperliquid_size(value: float) -> str:
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def _load_selected_recent_orders(self: tk.Tk) -> None:
    if _selected_venue_is_hyperliquid(self):
        self.load_hyperliquid_open_orders(title="HYPERLIQUID ACTIVE ORDERS")
        return
    self.load_schwab_open_orders()


def _load_selected_open_orders_only(self: tk.Tk) -> None:
    if _selected_venue_is_hyperliquid(self):
        self.load_hyperliquid_open_orders(title="HYPERLIQUID OPEN ORDERS ONLY")
        return
    self.load_schwab_open_orders_only()


def _cancel_selected_order(self: tk.Tk) -> None:
    if _selected_venue_is_hyperliquid(self):
        self.cancel_hyperliquid_order_guarded()
        return
    self.show_cancel_order_placeholder()


def _hyperliquid_cancel_coin_for_order(self: tk.Tk, order_id: str) -> str:
    cached_orders = getattr(self, "hyperliquid_open_order_coin_by_oid", {})
    cached_coin = cached_orders.get(order_id)
    if cached_coin:
        return cached_coin

    coin_source = getattr(self, "hyperliquid_coin_var", tk.StringVar(value="")).get().strip()
    if coin_source:
        return normalize_hyperliquid_coin(coin_source)

    raise ValueError(
        "Could not determine the Hyperliquid market for this order. "
        "Click Open Only first, then try Cancel Order again."
    )


def _cancel_hyperliquid_order_guarded(self: tk.Tk) -> None:
    raw_order_id = self.cancel_order_id_var.get().strip()
    if not raw_order_id:
        messagebox.showerror("Hyperliquid cancel blocked", "Enter an active Hyperliquid order ID first.")
        return

    try:
        order_id = int(raw_order_id)
    except ValueError:
        messagebox.showerror("Hyperliquid cancel blocked", "Hyperliquid order ID must be a number.")
        return

    try:
        coin = _hyperliquid_cancel_coin_for_order(self, raw_order_id)
    except Exception as exc:
        messagebox.showerror("Hyperliquid cancel blocked", str(exc))
        return

    config = HyperliquidTradingConfig()
    try:
        config.validate_for_live_action()
    except Exception as exc:
        self._set_preview_text(
            "HYPERLIQUID CANCEL BLOCKED\n"
            "==========================\n\n"
            f"{exc}\n\n"
            "Required local .env gates:\n"
            "- HYPE_WALLET_ADDRESS\n"
            "- HYPE_API_ADDRESS\n"
            "- HYPE_API_SECRET\n"
            "- HYPERLIQUID_ENABLE_LIVE_ORDERS=true\n\n"
        )
        messagebox.showerror("Hyperliquid cancel blocked", str(exc))
        return

    ok = messagebox.askyesno(
        "FINAL HYPERLIQUID CANCEL CONFIRMATION",
        "This will send a LIVE Hyperliquid cancel request.\n\n"
        f"Market: {coin}\n"
        f"Order ID: {order_id}\n\n"
        "Continue?",
    )
    if not ok:
        return

    try:
        result = HyperliquidExecutionAdapter().cancel(coin, order_id)
        self.hyperliquid_status_var.set("Hyperliquid: cancel attempted")
        self._set_preview_text(
            "HYPERLIQUID CANCEL ORDER RESULT\n"
            "===============================\n\n"
            f"Market: {coin}\n"
            f"Order ID: {order_id}\n\n"
            f"Response:\n{result}\n\n"
            "Refreshing Hyperliquid open orders..."
        )

        try:
            self.load_hyperliquid_open_orders(title="HYPERLIQUID OPEN ORDERS AFTER CANCEL")
        except Exception:
            pass

    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: cancel failed")
        messagebox.showerror("Hyperliquid cancel failed", str(exc))


def _hyperliquid_env_address() -> tuple[str, str] | tuple[None, None]:
    for key in HYPERLIQUID_ADDRESS_ENV_KEYS:
        address = os.getenv(key, "").strip()
        if address:
            return address, key
    return None, None


def _hyperliquid_user_address(self: tk.Tk) -> str | None:
    address, _source_key = _hyperliquid_env_address()
    if not address:
        address = simpledialog.askstring(
            "Hyperliquid Open Orders",
            "Enter your Hyperliquid master/sub-account wallet address.\n\n"
            "Tip: set HYPE_WALLET_ADDRESS in .env to skip this prompt.",
        ) or ""
    return address.strip() or None


def _load_hyperliquid_open_orders(self: tk.Tk, title: str = "HYPERLIQUID OPEN ORDERS") -> None:
    address = _hyperliquid_user_address(self)
    if not address:
        return

    try:
        client = HyperliquidInfoClient()
        snapshot = client.fetch_snapshot(address, include_open_orders=True)
        self.hyperliquid_open_order_coin_by_oid = {
            str(order.get("oid")): str(order.get("coin"))
            for order in snapshot.open_orders
            if order.get("oid") is not None and order.get("coin")
        }
        selected_coin = getattr(self, "hyperliquid_coin_var", tk.StringVar(value="")).get().strip()
        _address, source_key = _hyperliquid_env_address()
        self.hyperliquid_status_var.set(f"Hyperliquid: {len(snapshot.open_orders)} open orders")
        self._set_preview_text(
            _format_hyperliquid_open_orders(
                title,
                snapshot.user,
                snapshot.open_orders,
                snapshot.fetched_at,
                selected_coin=selected_coin,
                address_source=source_key or "manual entry",
            )
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: open orders failed")
        messagebox.showerror("Load Hyperliquid open orders failed", str(exc))


def _format_hyperliquid_open_orders(
    title: str,
    user: str,
    open_orders: list[dict[str, Any]],
    fetched_at: datetime,
    *,
    selected_coin: str = "",
    address_source: str = "manual entry",
) -> str:
    lines = [
        title,
        "=" * len(title),
        "",
        f"Wallet: {_short_address(user)}",
        f"Address source: {address_source}",
        f"Fetched: {fetched_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"Open orders: {len(open_orders)}",
        "",
    ]

    if not open_orders:
        lines.append("- None")
    else:
        for index, order in enumerate(open_orders, start=1):
            coin = _display_order_coin(order.get("coin", "UNKNOWN"), selected_coin)
            side = _display_order_side(order.get("side", "UNKNOWN"))
            size = order.get("sz", order.get("size", "?"))
            price = order.get("limitPx", order.get("price", "?"))
            oid = order.get("oid", "?")
            tif = order.get("tif") or order.get("timeInForce") or "--"
            reduce_only = order.get("reduceOnly", order.get("reduce_only", False))
            lines.extend(
                [
                    f"Order {index}",
                    f"- Market: {coin}",
                    f"- Side: {side}",
                    f"- Size: {size}",
                    f"- Limit price: {price}",
                    f"- Order ID: {oid}",
                    f"- TIF: {tif}",
                    f"- Reduce-only: {reduce_only}",
                    "",
                ]
            )

    lines.extend(
        [
            "Note: In Hyperliquid mode, Recent Orders and Open Only are routed to Hyperliquid active open orders.",
            "Switch Venue back to Schwab to use the Schwab recent/open order lookups.",
        ]
    )
    return "\n".join(lines)


def _display_order_coin(raw_coin: Any, selected_coin: str) -> str:
    coin = str(raw_coin or "UNKNOWN").strip()
    selected = selected_coin.strip().upper()
    if coin.startswith("@") and selected:
        return f"{selected} ({coin})"
    return coin


def _display_order_side(raw_side: Any) -> str:
    side = str(raw_side or "UNKNOWN").strip().upper()
    if side == "B":
        return "BUY"
    if side == "A":
        return "SELL"
    return side


def _short_address(address: str) -> str:
    if len(address) < 12:
        return address
    return f"{address[:6]}…{address[-4:]}"


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


def _submit_selected_venue(self: tk.Tk) -> None:
    if self.trade_venue_var.get() == "Hyperliquid":
        self.show_hyperliquid_live_submit_safety_review()
    else:
        self.submit_live_schwab_order_guarded()


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


def _parse_hyperliquid_spot_ticket(self: tk.Tk) -> HyperliquidOrderTicket:
    # Prefer Symbol because Cockpit spot UI shows ZEC/USDC there.
    # Fall back to HL Coin for quick typing like "zec".
    coin_source = self.symbol_var.get().strip() or self.hyperliquid_coin_var.get().strip()
    coin = normalize_hyperliquid_spot_market(coin_source)

    side = self.side_var.get().strip().lower()
    if side not in {"buy", "sell"}:
        raise ValueError("Hyperliquid spot side must be buy or sell.")

    try:
        size = float(self.quantity_var.get().strip().replace(",", ""))
        limit_price = float(self.limit_price_var.get().strip().replace(",", ""))
    except ValueError as exc:
        raise ValueError("Hyperliquid spot size and limit price must be numbers.") from exc

    if size <= 0:
        raise ValueError("Hyperliquid spot size must be positive.")
    if limit_price <= 0:
        raise ValueError("Hyperliquid spot limit price must be positive.")

    tif = self.hyperliquid_tif_var.get().strip() or "Gtc"
    if tif not in HYPERLIQUID_TIFS:
        raise ValueError("Hyperliquid TIF must be Alo, Ioc, or Gtc.")

    return HyperliquidOrderTicket(
        coin=coin,
        is_buy=side == "buy",
        size=size,
        limit_price=limit_price,
        tif=tif,
        reduce_only=False,  # spot should not be reduce-only
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
        self._set_preview_text(config.live_review_text(ticket))
        result = HyperliquidExecutionAdapter().submit(ticket)
        self.hyperliquid_status_var.set("Hyperliquid: submit attempted")
        self._set_preview_text(
            "HYPERLIQUID LIVE SUBMIT RESULT\n"
            "==============================\n\n"
            f"{result}\n\n"
            "Refreshing Hyperliquid account snapshot..."
        )
        try:
            self.sync_hyperliquid_account()
        except Exception:
            pass
    except NotImplementedError as exc:
        self.hyperliquid_status_var.set("Hyperliquid: hook missing")
        self._set_preview_text(
            "HYPERLIQUID LOCAL SUBMIT HOOK MISSING\n"
            "=====================================\n\n"
            f"{exc}\n\n"
            "Wire HyperliquidExecutionAdapter._local_signed_submit() locally."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: live blocked")
        messagebox.showerror("Hyperliquid live submit blocked", str(exc))
