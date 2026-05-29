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
    normalize_hyperliquid_size,
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
    app_cls.show_hyperliquid_order_edit_dialog = _show_hyperliquid_order_edit_dialog  # type: ignore[attr-defined]
    app_cls.edit_hyperliquid_order_guarded = _edit_hyperliquid_order_guarded  # type: ignore[attr-defined]
    app_cls.load_selected_recent_orders = _load_selected_recent_orders  # type: ignore[attr-defined]
    app_cls._build_order_panel = _build_order_panel_with_hyperliquid  # type: ignore[method-assign]
    app_cls.load_selected_open_orders_only = _load_selected_open_orders_only  # type: ignore[attr-defined]
    app_cls.load_hyperliquid_open_orders = _load_hyperliquid_open_orders  # type: ignore[attr-defined]
    app_cls.preview_hyperliquid_ticket = _preview_hyperliquid_ticket  # type: ignore[attr-defined]
    app_cls.preview_hyperliquid_spot_ticket = _preview_hyperliquid_spot_ticket  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_live_submit_safety_review = _show_hyperliquid_live_submit_safety_review  # type: ignore[attr-defined]
    app_cls.parse_hyperliquid_ticket = _parse_hyperliquid_ticket  # type: ignore[attr-defined]
    app_cls.on_trading_venue_changed = _on_trading_venue_changed  # type: ignore[attr-defined]
    app_cls.apply_hyperliquid_quantity_percent = _apply_hyperliquid_quantity_percent  # type: ignore[attr-defined]
    app_cls.update_cockpit_risk_console = _update_cockpit_risk_console  # type: ignore[attr-defined]


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
    if not hasattr(self, "hyperliquid_open_order_by_oid"):
        self.hyperliquid_open_order_by_oid = {}
    if not hasattr(self, "hyperliquid_size_percent_var"):
        self.hyperliquid_size_percent_var = tk.DoubleVar(value=0.0)
    if not hasattr(self, "hyperliquid_size_status_var"):
        self.hyperliquid_size_status_var = tk.StringVar(value="Sync Hyperliquid, then choose a size %")
    if not hasattr(self, "hyperliquid_size_unit_var"):
        self.hyperliquid_size_unit_var = tk.StringVar(value="")


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

    summary_shell = ttk.Frame(stack, style="Canvas.TFrame")
    exposure_shell = ttk.Frame(stack, style="Canvas.TFrame")
    console_shell = ttk.Frame(stack, style="Canvas.TFrame")
    stack.add(summary_shell, minsize=150, stretch="never")
    stack.add(exposure_shell, minsize=220, stretch="never")
    stack.add(console_shell, minsize=260, stretch="always")

    summary = ttk.LabelFrame(summary_shell, text="Portfolio Risk Console", style="Card.TLabelframe")
    summary.pack(fill=tk.BOTH, expand=True)
    summary.columnconfigure((0, 1, 2), weight=1)
    self.cockpit_cash_weight_var = tk.StringVar(value="Cash weight: --")
    self.cockpit_perp_notional_var = tk.StringVar(value="Perp notional: --")
    self.cockpit_largest_risk_var = tk.StringVar(value="Largest risk: --")
    ttk.Label(summary, textvariable=self.cockpit_cash_weight_var, style="MetricValue.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
    ttk.Label(summary, textvariable=self.cockpit_perp_notional_var, style="MetricValue.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 10))
    ttk.Label(summary, textvariable=self.cockpit_largest_risk_var, style="MetricValue.TLabel").grid(row=0, column=2, sticky="w")

    actions = ttk.Frame(summary, style="Panel.TFrame")
    actions.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(18, 0))
    actions.columnconfigure((0, 1, 2), weight=1, uniform="risk_console_actions")
    _grid_action_button(actions, 0, 0, "Refresh View", self.refresh_portfolio, "CompactAccent.TButton")
    _grid_action_button(actions, 0, 1, "Sync Schwab", self.refresh_schwab_account)
    _grid_action_button(actions, 0, 2, "Sync Hyperliquid", self.sync_hyperliquid_account)

    status_bar = ttk.Frame(summary, style="Panel.TFrame")
    status_bar.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
    status_bar.columnconfigure((0, 1, 2), weight=1)
    ttk.Label(status_bar, textvariable=self.schwab_status_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.schwab_preview_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=(4, 0))
    ttk.Label(status_bar, textvariable=self.hyperliquid_status_var, style="Chip.TLabel").grid(row=0, column=2, sticky="ew", pady=(4, 0))

    exposure = ttk.LabelFrame(exposure_shell, text="Spot / Perp Exposure Map", style="Card.TLabelframe")
    exposure.pack(fill=tk.BOTH, expand=True)
    columns = ("coin", "spot", "perp", "net", "readout")
    self.cockpit_exposure_table = ttk.Treeview(exposure, columns=columns, show="headings", height=7)
    headings = {
        "coin": ("Coin", 80, tk.W),
        "spot": ("Spot", 110, tk.E),
        "perp": ("Perp Notional", 120, tk.E),
        "net": ("Net Read", 110, tk.E),
        "readout": ("Risk Readout", 230, tk.W),
    }
    for column, (label, width, anchor) in headings.items():
        self.cockpit_exposure_table.heading(column, text=label)
        self.cockpit_exposure_table.column(column, width=width, anchor=anchor, stretch=True)
    self.cockpit_exposure_table.pack(fill=tk.BOTH, expand=True)

    results = ttk.LabelFrame(console_shell, text="Portfolio Analysis + Next Checks", style="Card.TLabelframe")
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
    self.cockpit_risk_console_text = self.preview_text
    _update_cockpit_risk_console(self, self.broker.get_portfolio())


def _update_cockpit_risk_console(self: tk.Tk, portfolio: Any | None = None) -> None:
    if not hasattr(self, "cockpit_risk_console_text"):
        return
    portfolio = portfolio or self.broker.get_portfolio()
    total_value = max(float(getattr(portfolio, "total_value", 0.0) or 0.0), 0.01)
    cash = float(getattr(portfolio, "cash", 0.0) or 0.0)
    exposures = _portfolio_coin_exposures(portfolio)
    total_perp_notional = sum(abs(row["perp_notional"]) for row in exposures.values())
    largest = _largest_abs_position(portfolio)

    if hasattr(self, "cockpit_cash_weight_var"):
        self.cockpit_cash_weight_var.set(f"Cash weight: {cash / total_value:.1%}")
    if hasattr(self, "cockpit_perp_notional_var"):
        self.cockpit_perp_notional_var.set(f"Perp notional: {_money(total_perp_notional)}")
    if hasattr(self, "cockpit_largest_risk_var"):
        if largest is None:
            self.cockpit_largest_risk_var.set("Largest risk: --")
        else:
            self.cockpit_largest_risk_var.set(f"Largest risk: {largest.symbol} {_money(abs(largest.market_value))}")

    _update_cockpit_exposure_table(self, exposures)

    lines = [
        "PORTFOLIO RISK CONSOLE",
        "======================",
        "",
        "Portfolio posture:",
        f"- Total value: {_money(total_value)}",
        f"- Cash: {_money(cash)} ({cash / total_value:.1%})",
        f"- Positions value: {_money(getattr(portfolio, 'positions_value', 0.0))}",
        f"- Perp notional: {_money(total_perp_notional)} ({total_perp_notional / total_value:.1%} of total value)",
        "",
        "Spot / perp pairing:",
    ]
    if exposures:
        for coin, row in sorted(exposures.items()):
            lines.append(f"- {coin}: {_exposure_sentence(row, total_value)}")
    else:
        lines.append("- No Hyperliquid spot/perp exposure found in the current cockpit snapshot.")

    lines.extend(["", "Next checks:"])
    lines.extend(f"- {line}" for line in _cockpit_next_checks(portfolio, exposures, total_value))
    lines.extend(
        [
            "",
            "Execution lives in the Schwab Trading and Hyperliquid Trading tabs. This Cockpit view is read-only risk context.",
        ]
    )

    self.cockpit_risk_console_text.configure(state=tk.NORMAL)
    self.cockpit_risk_console_text.delete("1.0", tk.END)
    self.cockpit_risk_console_text.insert(tk.END, "\n".join(lines))
    self.cockpit_risk_console_text.configure(state=tk.DISABLED)


def _update_cockpit_exposure_table(self: tk.Tk, exposures: dict[str, dict[str, Any]]) -> None:
    table = getattr(self, "cockpit_exposure_table", None)
    if table is None:
        return
    for row_id in table.get_children():
        table.delete(row_id)
    for coin, row in sorted(exposures.items()):
        table.insert(
            "",
            tk.END,
            values=(
                coin,
                _money(row["spot_value"]),
                _money(abs(row["perp_notional"])),
                _signed_money(row["net_delta"]),
                row["readout"],
            ),
        )


def _portfolio_coin_exposures(portfolio: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for position in getattr(portfolio, "positions", {}).values():
        symbol = str(getattr(position, "symbol", "")).upper()
        asset_type = str(getattr(position, "asset_type", "")).lower()
        is_perp = "-PERP" in symbol or asset_type.startswith("perp")
        is_spot = asset_type == "spot" or symbol.endswith("-SPOT")
        if not is_perp and not is_spot:
            continue

        coin = _coin_from_exposure_symbol(symbol)
        row = rows.setdefault(
            coin,
            {
                "coin": coin,
                "spot_value": 0.0,
                "spot_quantity": 0.0,
                "perp_notional": 0.0,
                "perp_signed": 0.0,
                "perp_quantity": 0.0,
            },
        )

        if is_perp:
            signed_notional = abs(position.market_value)
            signed_quantity = abs(float(getattr(position, "quantity", 0.0) or 0.0))
            if symbol.endswith("-SHORT") or "short" in asset_type:
                signed_notional *= -1
                signed_quantity *= -1
            row["perp_notional"] += signed_notional
            row["perp_signed"] += signed_notional
            row["perp_quantity"] += signed_quantity
        else:
            row["spot_value"] += abs(position.market_value)
            row["spot_quantity"] += abs(float(getattr(position, "quantity", 0.0) or 0.0))

    for row in rows.values():
        row["net_delta"] = row["spot_value"] + row["perp_signed"]
        row["readout"] = _exposure_readout(row)
    return rows


def _coin_from_exposure_symbol(symbol: str) -> str:
    clean = symbol.upper().replace("-SPOT", "")
    if "-PERP" in clean:
        clean = clean.split("-PERP", 1)[0]
    if "/" in clean:
        clean = clean.split("/", 1)[0]
    return clean


def _exposure_readout(row: dict[str, Any]) -> str:
    spot = float(row["spot_value"])
    perp = float(row["perp_signed"])
    abs_perp = abs(perp)
    if abs_perp <= 0.01 and spot > 0.01:
        return "Spot only"
    if spot <= 0.01 and abs_perp > 0.01:
        return "Directional perp, no spot hedge"
    if abs_perp <= 0.01:
        return "No active exposure"
    if perp < 0:
        ratio = spot / abs_perp if abs_perp else 0.0
        if 0.75 <= ratio <= 1.25:
            return "Spot roughly hedges short perp"
        if ratio < 0.25:
            return "Short perp much larger than spot"
        if ratio < 0.75:
            return "Partial spot hedge"
        return "Spot larger than short perp"
    return "Spot plus long perp stacks direction"


def _exposure_sentence(row: dict[str, Any], total_value: float) -> str:
    spot = float(row["spot_value"])
    perp = float(row["perp_signed"])
    net = float(row["net_delta"])
    direction = "short" if perp < 0 else "long"
    if abs(perp) <= 0.01:
        return f"spot {_money(spot)}, no perp notional. {row['readout']}."
    return (
        f"spot {_money(spot)} versus {_money(abs(perp))} {direction} perp notional; "
        f"net read {_signed_money(net)} ({abs(net) / total_value:.1%} of portfolio). {row['readout']}."
    )


def _cockpit_next_checks(portfolio: Any, exposures: dict[str, dict[str, Any]], total_value: float) -> list[str]:
    checks: list[str] = []
    for coin, row in sorted(exposures.items()):
        spot = float(row["spot_value"])
        perp = abs(float(row["perp_signed"]))
        if perp > 0 and spot <= max(25.0, perp * 0.05):
            checks.append(f"{coin}: perp exposure is large relative to spot; decide whether that is intentional directional risk or needs spot hedge/reduction.")
        elif perp > 0 and spot < perp * 0.75:
            checks.append(f"{coin}: spot only partially offsets perp notional; review whether the hedge ratio matches the thesis.")
        elif perp > 0 and spot > perp * 1.25:
            checks.append(f"{coin}: spot value is larger than perp notional; confirm you still want net spot exposure.")

    largest = _largest_abs_position(portfolio)
    if largest is not None and abs(largest.market_value) / total_value >= 0.20:
        checks.append(f"{largest.symbol}: largest position is {abs(largest.market_value) / total_value:.1%} of portfolio value.")

    cash_ratio = float(getattr(portfolio, "cash", 0.0) or 0.0) / total_value
    if cash_ratio >= 0.75:
        checks.append("Cash is very high; this is defensive, but new trades should have clear priority versus staying liquid.")
    elif cash_ratio <= 0.05:
        checks.append("Cash buffer is thin; avoid adding risk before checking liquidity and open orders.")

    if not checks:
        checks.append("No major spot/perp mismatch stands out from the current snapshot.")
    checks.append("Use the dedicated trading tabs for any add, reduce, hedge, cancel, or edit action.")
    return checks


def _largest_abs_position(portfolio: Any) -> Any | None:
    positions = [position for position in getattr(portfolio, "positions", {}).values() if abs(position.market_value) > 0.01]
    if not positions:
        return None
    return max(positions, key=lambda position: abs(position.market_value))


def _money(value: float) -> str:
    return polished_theme._format_money(float(value or 0.0))


def _signed_money(value: float) -> str:
    value = float(value or 0.0)
    return f"{'+' if value > 0 else ''}{_money(value)}"


def _grid_hyperliquid_quantity_row(parent: ttk.LabelFrame, self: tk.Tk, row: int) -> None:
    ttk.Label(parent, text="Quantity", style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
    quantity_controls = ttk.Frame(parent, style="Panel.TFrame")
    quantity_controls.grid(row=row, column=1, sticky="ew", pady=6)
    quantity_controls.columnconfigure(0, weight=1)

    ttk.Entry(quantity_controls, textvariable=self.quantity_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    unit_combo = ttk.Combobox(
        quantity_controls,
        textvariable=self.hyperliquid_size_unit_var,
        values=_hyperliquid_size_unit_values(self),
        state="readonly",
        width=8,
    )
    unit_combo.configure(postcommand=lambda: _refresh_hyperliquid_size_unit_combo(self, unit_combo))
    unit_combo.grid(row=0, column=1, sticky="ew")

    ttk.Label(parent, text="Entry / Limit", style="Subtle.TLabel").grid(row=row, column=2, sticky="w", padx=(16, 8), pady=6)
    ttk.Entry(parent, textvariable=self.limit_price_var).grid(row=row, column=3, sticky="ew", pady=6)
    _sync_hyperliquid_size_unit(self)


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
    displayed_size, unit = _display_size_for_selected_unit(self, size)
    self.quantity_var.set(_format_hyperliquid_size(displayed_size))
    self.hyperliquid_size_status_var.set(
        f"Size helper: {percent_value:.0f}% of {basis} = {_format_hyperliquid_size(displayed_size)} {unit}"
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


def _hyperliquid_size_unit_values(self: tk.Tk) -> list[str]:
    base = _current_hyperliquid_base_symbol(self)
    return [base, "USDC"] if base else ["Coin", "USDC"]


def _refresh_hyperliquid_size_unit_combo(self: tk.Tk, combo: ttk.Combobox) -> None:
    values = _hyperliquid_size_unit_values(self)
    combo.configure(values=values)
    _sync_hyperliquid_size_unit(self)


def _sync_hyperliquid_size_unit(self: tk.Tk) -> None:
    if not hasattr(self, "hyperliquid_size_unit_var"):
        return
    values = _hyperliquid_size_unit_values(self)
    current = self.hyperliquid_size_unit_var.get().strip().upper()
    if current not in values:
        self.hyperliquid_size_unit_var.set(values[0])


def _selected_size_unit(self: tk.Tk) -> str:
    _sync_hyperliquid_size_unit(self)
    return self.hyperliquid_size_unit_var.get().strip().upper()


def _display_size_for_selected_unit(self: tk.Tk, coin_size: float) -> tuple[float, str]:
    unit = _selected_size_unit(self)
    if unit == "USDC":
        limit_price = _positive_float(self.limit_price_var.get()) or 0.0
        return coin_size * limit_price, unit
    return coin_size, unit


def _spot_size_from_quantity_input(self: tk.Tk, raw_quantity: float, limit_price: float) -> float:
    unit = _selected_size_unit(self)
    if unit != "USDC":
        return raw_quantity
    if limit_price <= 0:
        raise ValueError("A positive limit price is required when Quantity is in USDC.")
    return normalize_hyperliquid_size(raw_quantity / limit_price)


def _current_hyperliquid_base_symbol(self: tk.Tk) -> str:
    symbol_source = ""
    if hasattr(self, "symbol_var"):
        symbol_source = self.symbol_var.get().strip()
    if not symbol_source and hasattr(self, "hyperliquid_coin_var"):
        symbol_source = self.hyperliquid_coin_var.get().strip()
    if not symbol_source:
        return ""
    try:
        return _display_spot_base(normalize_hyperliquid_spot_market(symbol_source))
    except Exception:
        return _display_spot_base(symbol_source)


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


def _show_hyperliquid_order_edit_dialog(self: tk.Tk) -> None:
    _ensure_hyperliquid_vars(self)
    if not _selected_venue_is_hyperliquid(self):
        self.trade_venue_var.set("Hyperliquid")

    cached_order = _selected_hyperliquid_order(self)
    raw_order_id = self.cancel_order_id_var.get().strip()
    if not raw_order_id and cached_order is not None:
        raw_order_id = str(cached_order.get("oid") or "")

    market = _order_market_for_edit(self, cached_order)
    side = _order_side_for_edit(cached_order, self.side_var.get())
    size = str((cached_order or {}).get("sz") or (cached_order or {}).get("size") or self.quantity_var.get()).strip()
    price = str((cached_order or {}).get("limitPx") or (cached_order or {}).get("price") or self.limit_price_var.get()).strip()
    tif = str((cached_order or {}).get("tif") or (cached_order or {}).get("timeInForce") or self.hyperliquid_tif_var.get() or "Gtc")

    dialog = tk.Toplevel(self)
    dialog.title("Edit Hyperliquid Order")
    dialog.transient(self)
    dialog.resizable(False, False)

    shell = ttk.Frame(dialog, style="Panel.TFrame", padding=14)
    shell.pack(fill=tk.BOTH, expand=True)
    shell.columnconfigure(1, weight=1)

    order_id_var = tk.StringVar(value=raw_order_id)
    market_var = tk.StringVar(value=market)
    side_var = tk.StringVar(value=side)
    size_var = tk.StringVar(value=size)
    price_var = tk.StringVar(value=price)
    tif_var = tk.StringVar(value=tif if tif in HYPERLIQUID_TIFS else "Gtc")
    mid_status_var = tk.StringVar(value="")

    fields = [
        ("Order ID", ttk.Entry(shell, textvariable=order_id_var)),
        ("Market", ttk.Entry(shell, textvariable=market_var)),
        ("Side", ttk.Combobox(shell, textvariable=side_var, values=["buy", "sell"], state="readonly")),
        ("Size", ttk.Entry(shell, textvariable=size_var)),
        ("TIF", ttk.Combobox(shell, textvariable=tif_var, values=HYPERLIQUID_TIFS, state="readonly")),
    ]
    for row, (label, widget) in enumerate(fields[:4]):
        ttk.Label(shell, text=label, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        widget.grid(row=row, column=1, sticky="ew", pady=5)

    price_row = 4
    ttk.Label(shell, text="Limit price", style="Subtle.TLabel").grid(row=price_row, column=0, sticky="w", padx=(0, 10), pady=5)
    price_controls = ttk.Frame(shell, style="Panel.TFrame")
    price_controls.grid(row=price_row, column=1, sticky="ew", pady=5)
    price_controls.columnconfigure(0, weight=1)
    ttk.Entry(price_controls, textvariable=price_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(
        price_controls,
        text="Mid",
        command=lambda: _fill_edit_dialog_mid_price(market_var, price_var, mid_status_var),
        style="CompactAccent.TButton",
    ).grid(row=0, column=1, sticky="ew")
    ttk.Label(price_controls, textvariable=mid_status_var, style="Subtle.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))

    for row, (label, widget) in enumerate(fields[4:], start=5):
        ttk.Label(shell, text=label, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
        widget.grid(row=row, column=1, sticky="ew", pady=5)

    note = ttk.Label(
        shell,
        text="Edits are live Hyperliquid modify-order requests. Use Open first to preload active order details.",
        style="Subtle.TLabel",
        wraplength=420,
    )
    note.grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(8, 2))

    buttons = ttk.Frame(shell, style="Panel.TFrame")
    buttons.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    buttons.columnconfigure((0, 1), weight=1)

    def submit_edit() -> None:
        self.edit_hyperliquid_order_guarded(
            order_id_var.get(),
            market_var.get(),
            side_var.get(),
            size_var.get(),
            price_var.get(),
            tif_var.get(),
            dialog,
        )

    ttk.Button(buttons, text="Close", command=dialog.destroy).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(buttons, text="Confirm Edit", command=submit_edit, style="CompactDanger.TButton").grid(row=0, column=1, sticky="ew")


def _fill_edit_dialog_mid_price(market_var: tk.StringVar, price_var: tk.StringVar, status_var: tk.StringVar) -> None:
    try:
        from app.ui.hyperliquid_cockpit_spot_mid_extension import _format_price, _lookup_hyperliquid_spot_mid

        market = _normalize_mid_lookup_market(market_var.get())
        mid, basis = _lookup_hyperliquid_spot_mid(market)
        price_var.set(_format_price(mid))
        status_var.set(f"Mid ${mid:,.4f} from {basis}")
    except Exception as exc:
        status_var.set(f"Mid failed: {exc}")


def _normalize_mid_lookup_market(raw_market: str) -> str:
    market = raw_market.strip().upper()
    if not market:
        raise ValueError("enter a market first")
    if market.startswith("@"):
        return market
    return normalize_hyperliquid_spot_market(market)


def _selected_hyperliquid_order(self: tk.Tk) -> dict[str, Any] | None:
    orders = getattr(self, "hyperliquid_open_order_by_oid", {})
    raw_order_id = self.cancel_order_id_var.get().strip()
    if raw_order_id and raw_order_id in orders:
        return orders[raw_order_id]
    if orders:
        first_order_id = sorted(orders)[0]
        self.cancel_order_id_var.set(first_order_id)
        return orders[first_order_id]
    return None


def _order_market_for_edit(self: tk.Tk, order: dict[str, Any] | None) -> str:
    raw_market = str((order or {}).get("coin") or "").strip()
    if raw_market:
        return raw_market
    symbol_source = self.symbol_var.get().strip() or self.hyperliquid_coin_var.get().strip()
    return normalize_hyperliquid_spot_market(symbol_source) if symbol_source else ""


def _order_side_for_edit(order: dict[str, Any] | None, fallback: str) -> str:
    raw_side = str((order or {}).get("side") or fallback or "").strip().upper()
    if raw_side in {"B", "BUY"}:
        return "buy"
    if raw_side in {"A", "S", "SELL"}:
        return "sell"
    return "buy"


def _edit_hyperliquid_order_guarded(
    self: tk.Tk,
    raw_order_id: str,
    raw_market: str,
    raw_side: str,
    raw_size: str,
    raw_limit_price: str,
    raw_tif: str,
    dialog: tk.Toplevel | None = None,
) -> None:
    try:
        order_id = int(raw_order_id.strip())
    except ValueError:
        messagebox.showerror("Hyperliquid edit blocked", "Hyperliquid order ID must be a number.")
        return

    try:
        market = _normalize_edit_market(raw_market)
        side = raw_side.strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("Side must be buy or sell.")
        size = float(raw_size.strip().replace(",", ""))
        limit_price = float(raw_limit_price.strip().replace(",", ""))
        tif = raw_tif.strip() or "Gtc"
        if tif not in HYPERLIQUID_TIFS:
            raise ValueError("TIF must be Alo, Ioc, or Gtc.")
        ticket = HyperliquidOrderTicket(
            coin=market,
            is_buy=side == "buy",
            size=size,
            limit_price=limit_price,
            tif=tif,
            reduce_only=False,
        )
    except Exception as exc:
        messagebox.showerror("Hyperliquid edit blocked", str(exc))
        return

    config = HyperliquidTradingConfig()
    try:
        config.validate_for_live(ticket)
    except Exception as exc:
        self._set_preview_text(
            "HYPERLIQUID EDIT BLOCKED\n"
            "========================\n\n"
            f"{exc}\n\n"
            "Required local .env gates:\n"
            "- HYPE_WALLET_ADDRESS\n"
            "- HYPE_API_ADDRESS\n"
            "- HYPE_API_SECRET\n"
            "- HYPERLIQUID_ENABLE_LIVE_ORDERS=true\n\n"
        )
        messagebox.showerror("Hyperliquid edit blocked", str(exc))
        return

    ok = messagebox.askyesno(
        "FINAL HYPERLIQUID ORDER EDIT CONFIRMATION",
        "This will modify a LIVE Hyperliquid order.\n\n"
        f"Order ID: {order_id}\n"
        f"Market: {ticket.coin}\n"
        f"Side: {ticket.side_label}\n"
        f"New size: {ticket.size:g}\n"
        f"New limit price: ${ticket.limit_price:,.4f}\n"
        f"TIF: {ticket.tif}\n\n"
        "Continue?",
    )
    if not ok:
        return

    try:
        result = HyperliquidExecutionAdapter().modify_order(order_id, ticket)
        self.cancel_order_id_var.set(str(order_id))
        self.symbol_var.set(_display_spot_base(ticket.coin))
        self.hyperliquid_coin_var.set(_display_spot_base(ticket.coin))
        self.side_var.set("buy" if ticket.is_buy else "sell")
        self.quantity_var.set(_format_hyperliquid_size(ticket.size))
        self.limit_price_var.set(_format_hyperliquid_size(ticket.limit_price))
        self.hyperliquid_tif_var.set(ticket.tif)
        self.hyperliquid_status_var.set("Hyperliquid: edit attempted")
        self._set_preview_text(
            "HYPERLIQUID EDIT ORDER RESULT\n"
            "=============================\n\n"
            f"Order ID: {order_id}\n"
            f"Market: {ticket.coin}\n"
            f"Side: {ticket.side_label}\n"
            f"Size: {ticket.size:g}\n"
            f"Limit price: ${ticket.limit_price:,.4f}\n\n"
            f"Response:\n{result}\n\n"
            "Refreshing Hyperliquid open orders..."
        )
        if dialog is not None:
            dialog.destroy()
        try:
            self.load_hyperliquid_open_orders(title="HYPERLIQUID OPEN ORDERS AFTER EDIT")
        except Exception:
            pass
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: edit failed")
        messagebox.showerror("Hyperliquid edit failed", str(exc))


def _normalize_edit_market(raw_market: str) -> str:
    market = raw_market.strip().upper()
    if not market:
        raise ValueError("Enter a Hyperliquid market.")
    if market.startswith("@"):
        return market
    return normalize_hyperliquid_spot_market(market)


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
        self.hyperliquid_open_order_by_oid = {
            str(order.get("oid")): order
            for order in snapshot.open_orders
            if order.get("oid") is not None
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
        raw_size = float(self.quantity_var.get().strip().replace(",", ""))
        limit_price = float(self.limit_price_var.get().strip().replace(",", ""))
    except ValueError as exc:
        raise ValueError("Hyperliquid spot size and limit price must be numbers.") from exc

    size = _spot_size_from_quantity_input(self, raw_size, limit_price)
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


def _preview_hyperliquid_spot_ticket(self: tk.Tk) -> None:
    try:
        ticket = self.parse_hyperliquid_spot_ticket()
        config = HyperliquidTradingConfig()
        self.hyperliquid_status_var.set("Hyperliquid spot: preview ready")
        self._set_preview_text(config.preview_text(ticket))
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid spot: preview failed")
        messagebox.showerror("Hyperliquid spot preview failed", str(exc))


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
