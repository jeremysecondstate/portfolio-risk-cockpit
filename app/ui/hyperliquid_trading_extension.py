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
)
from app.core.order_models import OrderSide, OrderType, TimeInForce
from app.ui import polished_theme
from app.ui.polished_theme import _make_paned


TRADING_VENUES = ["Schwab", "Hyperliquid"]
HYPERLIQUID_TIFS = ["Alo", "Ioc", "Gtc"]


def install_hyperliquid_trading_extension(app_cls: Type[tk.Tk]) -> None:
    """Add a venue selector and guarded Hyperliquid ticket workflow."""

    app_cls._build_order_panel = _build_order_panel_with_hyperliquid  # type: ignore[method-assign]
    app_cls.submit_selected_venue = _submit_selected_venue  # type: ignore[attr-defined]
    app_cls.load_selected_recent_orders = _load_selected_recent_orders  # type: ignore[attr-defined]
    app_cls.load_selected_open_orders_only = _load_selected_open_orders_only  # type: ignore[attr-defined]
    app_cls.load_hyperliquid_open_orders = _load_hyperliquid_open_orders  # type: ignore[attr-defined]
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


def _build_action_group(parent: ttk.Frame, title: str, column: int) -> ttk.LabelFrame:
    group = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
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
    style: str = "TButton",
    columnspan: int = 1,
) -> None:
    ttk.Button(parent, text=text, command=command, style=style).grid(
        row=row,
        column=column,
        columnspan=columnspan,
        sticky="ew",
        padx=(0, 6) if column == 0 and columnspan == 1 else 0,
        pady=(4, 6) if row == 0 else (0, 4),
    )


def _build_order_panel_with_hyperliquid(self: tk.Tk, parent: ttk.Frame) -> None:
    _ensure_hyperliquid_vars(self)

    stack = _make_paned(parent, tk.VERTICAL)
    stack.pack(fill=tk.BOTH, expand=True)

    ticket_shell = ttk.Frame(stack, style="Canvas.TFrame")
    preview_shell = ttk.Frame(stack, style="Canvas.TFrame")
    explainer_shell = ttk.Frame(stack, style="Canvas.TFrame")
    stack.add(ticket_shell, minsize=330, stretch="never")
    stack.add(preview_shell, minsize=200, stretch="always")
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

    actions = ttk.Frame(ticket, style="Panel.TFrame")
    actions.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    actions.columnconfigure((0, 1, 2), weight=1, uniform="action_groups")

    connect_group = _build_action_group(actions, "Connections", 0)
    plan_group = _build_action_group(actions, "Planning", 1)
    live_group = _build_action_group(actions, "Guarded Live Actions", 2)

    _grid_action_button(connect_group, 0, 0, "Connect Schwab", self.connect_schwab)
    _grid_action_button(connect_group, 0, 1, "Connect Hyperliquid", self.sync_hyperliquid_account)
    _grid_action_button(connect_group, 1, 0, "Refresh Schwab", self.refresh_schwab_account)
    _grid_action_button(connect_group, 1, 1, "Reset Session", self.reset_schwab_session)

    _grid_action_button(plan_group, 1, 0, "Tech Analysis", self.show_technical_analysis, columnspan=2)

    _grid_action_button(live_group, 0, 0, "Recent Orders", self.load_selected_recent_orders)
    _grid_action_button(live_group, 0, 1, "Open Only", self.load_selected_open_orders_only)
    _grid_action_button(live_group, 1, 0, "Cancel Order", self.show_cancel_order_placeholder, "Danger.TButton")
    _grid_action_button(live_group, 1, 1, "LIVE Submit", self.submit_selected_venue, "Danger.TButton")

    status_bar = ttk.Frame(ticket, style="Panel.TFrame")
    status_bar.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(10, 0))
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
        "For Hyperliquid, LIVE Submit checks env readiness + max notional, then calls the local hook."
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


def _selected_venue_is_hyperliquid(self: tk.Tk) -> bool:
    return getattr(self, "trade_venue_var", tk.StringVar(value="Schwab")).get() == "Hyperliquid"


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


def _hyperliquid_user_address(self: tk.Tk) -> str | None:
    address = os.getenv("HYPERLIQUID_USER_ADDRESS", "").strip()
    if not address:
        address = simpledialog.askstring(
            "Hyperliquid Open Orders",
            "Enter your Hyperliquid master/sub-account wallet address.\n\n"
            "Use the account address, not the API/agent wallet address.",
        ) or ""
    return address.strip() or None


def _load_hyperliquid_open_orders(self: tk.Tk, title: str = "HYPERLIQUID OPEN ORDERS") -> None:
    address = _hyperliquid_user_address(self)
    if not address:
        return

    try:
        client = HyperliquidInfoClient()
        snapshot = client.fetch_snapshot(address, include_open_orders=True)
        self.hyperliquid_status_var.set(f"Hyperliquid: {len(snapshot.open_orders)} open orders")
        self._set_preview_text(_format_hyperliquid_open_orders(title, snapshot.user, snapshot.open_orders, snapshot.fetched_at))
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: open orders failed")
        messagebox.showerror("Load Hyperliquid open orders failed", str(exc))


def _format_hyperliquid_open_orders(
    title: str,
    user: str,
    open_orders: list[dict[str, Any]],
    fetched_at: datetime,
) -> str:
    lines = [
        title,
        "=" * len(title),
        "",
        f"Wallet: {_short_address(user)}",
        f"Fetched: {fetched_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "Mode: read-only public info API; no order was submitted, replaced, or canceled.",
        "",
        f"Open orders: {len(open_orders)}",
        "",
    ]

    if not open_orders:
        lines.append("- None")
    else:
        for index, order in enumerate(open_orders, start=1):
            coin = order.get("coin", "UNKNOWN")
            side = order.get("side", "UNKNOWN")
            size = order.get("sz", order.get("size", "?"))
            price = order.get("limitPx", order.get("price", "?"))
            oid = order.get("oid", "?")
            tif = order.get("tif") or order.get("timeInForce") or "--"
            reduce_only = order.get("reduceOnly", order.get("reduce_only", False))
            lines.append(
                f"{index}. {coin} {side} {size} @ {price} · oid {oid} · TIF {tif} · reduce-only {reduce_only}"
            )

    lines.extend(
        [
            "",
            "Note: In Hyperliquid mode, Recent Orders and Open Only are routed to Hyperliquid active open orders.",
            "Switch Venue back to Schwab to use the Schwab recent/open order lookups.",
        ]
    )
    return "\n".join(lines)


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
