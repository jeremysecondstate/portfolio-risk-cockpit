from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Type

from app.brokers.hyperliquid.client import HyperliquidInfoClient
from app.brokers.hyperliquid.trading import (
    HyperliquidExecutionAdapter,
    HyperliquidOrderEditTicket,
    HyperliquidOrderTicket,
    HyperliquidTriggerTicket,
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


@dataclass(frozen=True)
class HyperliquidOpenOrder:
    raw: dict[str, Any]
    oid: str
    context: str
    order_kind: str
    coin: str
    side: str
    direction: str
    size: float
    size_label: str
    close_position: bool
    original_size_label: str
    order_value_label: str
    price_label: str
    limit_price: float
    reduce_only: bool
    is_trigger: bool
    trigger_condition: str
    trigger_price: float | None
    trigger_kind: str
    is_market_trigger: bool
    tif: str
    tpsl_label: str


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
    app_cls.show_hyperliquid_position_tpsl_dialog = _show_hyperliquid_position_tpsl_dialog  # type: ignore[attr-defined]
    app_cls.place_hyperliquid_position_tpsl_guarded = _place_hyperliquid_position_tpsl_guarded  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_perp_position_size = _show_hyperliquid_perp_position_size  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_perp_position_editor = _show_hyperliquid_perp_position_editor  # type: ignore[attr-defined]
    app_cls.use_hyperliquid_perp_position = _use_hyperliquid_perp_position  # type: ignore[attr-defined]
    app_cls.apply_hyperliquid_leverage_guarded = _apply_hyperliquid_leverage_guarded  # type: ignore[attr-defined]
    app_cls.load_selected_recent_orders = _load_selected_recent_orders  # type: ignore[attr-defined]
    app_cls._build_order_panel = _build_order_panel_with_hyperliquid  # type: ignore[method-assign]
    app_cls.load_selected_open_orders_only = _load_selected_open_orders_only  # type: ignore[attr-defined]
    app_cls.load_hyperliquid_open_orders = _load_hyperliquid_open_orders  # type: ignore[attr-defined]
    app_cls.preview_hyperliquid_ticket = _preview_hyperliquid_ticket  # type: ignore[attr-defined]
    app_cls.preview_hyperliquid_spot_ticket = _preview_hyperliquid_spot_ticket  # type: ignore[attr-defined]
    app_cls.run_hyperliquid_spot_what_if = _run_hyperliquid_spot_what_if  # type: ignore[attr-defined]
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
    if not hasattr(self, "hyperliquid_margin_mode_var"):
        self.hyperliquid_margin_mode_var = tk.StringVar(value="Cross")


def _configure_compact_ticket_styles(self: tk.Tk) -> None:
    style = ttk.Style(self)
    style.configure("Compact.Card.TLabelframe", background=polished_theme.PANEL, bordercolor=polished_theme.BORDER, relief="solid", padding=8)
    style.configure("Compact.Card.TLabelframe.Label", background=polished_theme.PANEL, foreground=polished_theme.TEXT, font=("Segoe UI", 10, "bold"))
    style.configure("Compact.TButton", padding=(6, 5), font=("Segoe UI", 9))
    style.configure("Body.TLabel", background=polished_theme.PANEL, foreground=polished_theme.TEXT)
    style.configure("Mono.TLabel", background=polished_theme.PANEL, foreground=polished_theme.TEXT, font=("Consolas", 9))
    style.configure("CompactAccent.TButton", background=polished_theme.ACCENT, foreground="#ffffff", padding=(6, 5), font=("Segoe UI", 9, "bold"))
    style.map("CompactAccent.TButton", background=[("active", polished_theme.ACCENT_DARK), ("pressed", polished_theme.ACCENT_DARK)], foreground=[("active", "#ffffff")])
    style.configure("CompactDanger.TButton", background="#fee2e2", foreground=polished_theme.DANGER, padding=(6, 5), font=("Segoe UI", 9, "bold"))
    style.map("CompactDanger.TButton", background=[("active", "#fecaca")])
    style.configure("ConnectionGood.TLabel", background=polished_theme.PANEL, foreground="#047857", font=("Segoe UI", 9, "bold"))
    style.configure("ConnectionMuted.TLabel", background=polished_theme.PANEL, foreground=polished_theme.MUTED, font=("Segoe UI", 9))


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
        padx=(0, 6) if columnspan == 1 and column < 2 else 0,
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
    stack.add(summary_shell, minsize=145, stretch="never")
    stack.add(exposure_shell, minsize=420, stretch="always")

    summary = ttk.LabelFrame(summary_shell, text="Portfolio Risk Console", style="Card.TLabelframe")
    summary.pack(fill=tk.BOTH, expand=True)
    summary.columnconfigure((0, 1, 2, 3), weight=1)
    self.cockpit_cash_weight_var = tk.StringVar(value="Cash weight: --")
    self.cockpit_spot_equity_var = tk.StringVar(value="Spot/equity: --")
    self.cockpit_perp_notional_var = tk.StringVar(value="Perp notional: --")
    self.cockpit_largest_risk_var = tk.StringVar(value="Largest risk: --")
    ttk.Label(summary, textvariable=self.cockpit_cash_weight_var, style="MetricValue.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
    ttk.Label(summary, textvariable=self.cockpit_spot_equity_var, style="MetricValue.TLabel").grid(row=0, column=1, sticky="w", padx=(0, 10))
    ttk.Label(summary, textvariable=self.cockpit_perp_notional_var, style="MetricValue.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 10))
    ttk.Label(summary, textvariable=self.cockpit_largest_risk_var, style="MetricValue.TLabel").grid(row=0, column=3, sticky="w")

    actions = ttk.Frame(summary, style="Panel.TFrame")
    actions.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(16, 0))
    actions.columnconfigure((0, 1, 2), weight=1, uniform="risk_console_actions")
    _grid_action_button(actions, 0, 0, "Refresh", self.refresh_portfolio, "CompactAccent.TButton")
    _grid_action_button(actions, 0, 1, "Sync Schwab", lambda: _run_then_update_risk_console(self, self.refresh_schwab_account, "schwab_trading_preview_text"))
    _grid_action_button(actions, 0, 2, "Sync Hyperliquid", lambda: _run_then_update_risk_console(self, self.sync_hyperliquid_account, "hyperliquid_trading_preview_text"))
    self.cockpit_schwab_connection_var = tk.StringVar(value="Schwab: not connected")
    self.cockpit_hyperliquid_connection_var = tk.StringVar(value="Hyperliquid: not synced")
    ttk.Label(actions, text="Live data controls", style="ConnectionMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
    self.cockpit_schwab_connection_label = ttk.Label(actions, textvariable=self.cockpit_schwab_connection_var, style="ConnectionMuted.TLabel")
    self.cockpit_schwab_connection_label.grid(row=1, column=1, sticky="w", pady=(2, 0))
    self.cockpit_hyperliquid_connection_label = ttk.Label(actions, textvariable=self.cockpit_hyperliquid_connection_var, style="ConnectionMuted.TLabel")
    self.cockpit_hyperliquid_connection_label.grid(row=1, column=2, sticky="w", pady=(2, 0))

    exposure = ttk.LabelFrame(exposure_shell, text="Spot / Perp / Equity Exposure Map", style="Card.TLabelframe")
    exposure.pack(fill=tk.BOTH, expand=True)
    columns = ("asset", "type", "spot_equity", "perp", "net", "weight", "pnl", "readout")
    self.cockpit_exposure_table = ttk.Treeview(exposure, columns=columns, show="headings", height=16)
    headings = {
        "asset": ("Asset", 82, tk.W),
        "type": ("Type", 88, tk.W),
        "spot_equity": ("Spot / Equity", 112, tk.E),
        "perp": ("Perp Notional", 112, tk.E),
        "net": ("Net Exposure", 112, tk.E),
        "weight": ("Weight", 76, tk.E),
        "pnl": ("P&L", 95, tk.E),
        "readout": ("Risk Readout", 210, tk.W),
    }
    for column, (label, width, anchor) in headings.items():
        self.cockpit_exposure_table.heading(column, text=label)
        self.cockpit_exposure_table.column(column, width=width, anchor=anchor, stretch=True)
    self.cockpit_exposure_table.tag_configure("equity", foreground=polished_theme.TEXT)
    self.cockpit_exposure_table.tag_configure("crypto", foreground="#075985")
    self.cockpit_exposure_table.tag_configure("cash", foreground=polished_theme.MUTED)
    self.cockpit_exposure_table.pack(fill=tk.BOTH, expand=True)
    _update_cockpit_risk_console(self, self.broker.get_portfolio())


def _run_then_update_risk_console(self: tk.Tk, command, output_attr: str | None = None) -> None:
    output = getattr(self, output_attr, None) if output_attr else None
    previous_output = getattr(self, "preview_text", None)
    if output is not None:
        self.preview_text = output
    command()
    if output is not None and previous_output is not None:
        self.preview_text = previous_output
    _update_cockpit_risk_console(self, self.broker.get_portfolio())


def _update_cockpit_risk_console(self: tk.Tk, portfolio: Any | None = None) -> None:
    portfolio = portfolio or self.broker.get_portfolio()
    total_value = max(float(getattr(portfolio, "total_value", 0.0) or 0.0), 0.01)
    cash = float(getattr(portfolio, "cash", 0.0) or 0.0)
    exposures = _portfolio_coin_exposures(portfolio)
    total_perp_notional = sum(abs(row["perp_notional"]) for row in exposures.values())
    total_spot_equity = sum(abs(row["spot_value"]) for row in exposures.values() if row.get("asset_class") in {"Crypto", "Equity"})
    largest = _largest_abs_position(portfolio)

    if hasattr(self, "cockpit_cash_weight_var"):
        self.cockpit_cash_weight_var.set(f"Cash weight: {cash / total_value:.1%}")
    if hasattr(self, "cockpit_spot_equity_var"):
        self.cockpit_spot_equity_var.set(f"Spot/equity: {_money(total_spot_equity)}")
    if hasattr(self, "cockpit_perp_notional_var"):
        self.cockpit_perp_notional_var.set(f"Perp notional: {_money(total_perp_notional)}")
    if hasattr(self, "cockpit_largest_risk_var"):
        if largest is None:
            self.cockpit_largest_risk_var.set("Largest risk: --")
        else:
            self.cockpit_largest_risk_var.set(f"Largest risk: {largest.symbol} {_money(abs(largest.market_value))}")
    _refresh_cockpit_connection_badges(self)

    _update_cockpit_exposure_table(self, exposures, total_value)
    console_text = getattr(self, "cockpit_risk_console_text", None)
    if console_text is None:
        return

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


def _update_cockpit_exposure_table(self: tk.Tk, exposures: dict[str, dict[str, Any]], total_value: float) -> None:
    table = getattr(self, "cockpit_exposure_table", None)
    if table is None:
        return
    for row_id in table.get_children():
        table.delete(row_id)
    for asset, row in sorted(exposures.items(), key=lambda item: (item[1].get("sort_group", 99), item[0])):
        table.insert(
            "",
            tk.END,
            values=(
                asset,
                row["asset_class"],
                _money(row["spot_value"]),
                _money(abs(row["perp_notional"])) if abs(row["perp_notional"]) > 0.01 else "--",
                _signed_money(row["net_delta"]),
                f"{abs(row['net_delta']) / total_value:.1%}",
                _signed_money(row["pnl"]) if row["pnl_known"] else "--",
                row["readout"],
            ),
            tags=(str(row.get("tag", "equity")),),
        )


def _refresh_cockpit_connection_badges(self: tk.Tk) -> None:
    schwab_text = str(getattr(getattr(self, "schwab_status_var", None), "get", lambda: "")()).lower()
    schwab_connected = getattr(self, "schwab_session", None) is not None or "connected" in schwab_text
    _set_cockpit_connection_badge(
        self,
        "cockpit_schwab_connection_var",
        "cockpit_schwab_connection_label",
        "Schwab: connected" if schwab_connected else "Schwab: not connected",
        schwab_connected,
    )

    hyper_text = str(getattr(getattr(self, "hyperliquid_status_var", None), "get", lambda: "")()).lower()
    source_text = str(getattr(getattr(self, "broker", None), "source_message", "")).lower()
    hyper_synced = "synced" in hyper_text or "loaded hyperliquid" in source_text or "hyperliquid account" in source_text
    _set_cockpit_connection_badge(
        self,
        "cockpit_hyperliquid_connection_var",
        "cockpit_hyperliquid_connection_label",
        "Hyperliquid: synced" if hyper_synced else "Hyperliquid: not synced",
        hyper_synced,
    )


def _set_cockpit_connection_badge(self: tk.Tk, var_name: str, label_name: str, text: str, active: bool) -> None:
    var = getattr(self, var_name, None)
    if hasattr(var, "set"):
        var.set(text)
    label = getattr(self, label_name, None)
    if label is not None:
        try:
            label.configure(style="ConnectionGood.TLabel" if active else "ConnectionMuted.TLabel")
        except tk.TclError:
            pass


def _portfolio_coin_exposures(portfolio: Any) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for position in getattr(portfolio, "positions", {}).values():
        symbol = str(getattr(position, "symbol", "")).upper()
        asset_type = str(getattr(position, "asset_type", "")).lower()
        is_perp = "-PERP" in symbol or asset_type.startswith("perp")
        is_spot = asset_type == "spot" or symbol.endswith("-SPOT")

        if is_perp or is_spot:
            asset = _coin_from_exposure_symbol(symbol)
            asset_class = "Crypto"
            sort_group = 0
            tag = "crypto"
        else:
            asset = symbol
            asset_class = "Equity"
            sort_group = 1
            tag = "equity"

        row = rows.setdefault(
            asset,
            {
                "coin": asset,
                "asset_class": asset_class,
                "sort_group": sort_group,
                "tag": tag,
                "spot_value": 0.0,
                "spot_quantity": 0.0,
                "perp_notional": 0.0,
                "perp_signed": 0.0,
                "perp_quantity": 0.0,
                "pnl": 0.0,
                "pnl_known": False,
            },
        )

        position_pnl = _position_display_pnl(position)
        if position_pnl is not None:
            row["pnl"] += position_pnl
            row["pnl_known"] = True

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


def _position_display_pnl(position: Any) -> float | None:
    raw_pnl = getattr(position, "raw_profit_loss", None)
    if isinstance(raw_pnl, (int, float)):
        return float(raw_pnl)
    custom_pnl = getattr(position, "custom_profit_loss", None)
    if isinstance(custom_pnl, (int, float)):
        return float(custom_pnl)
    if getattr(position, "unrealized_profit_loss_known", True):
        unrealized_pnl = getattr(position, "unrealized_profit_loss", None)
        if isinstance(unrealized_pnl, (int, float)):
            return float(unrealized_pnl)
    return None


def _coin_from_exposure_symbol(symbol: str) -> str:
    return _display_spot_base(symbol)


def _exposure_readout(row: dict[str, Any]) -> str:
    if row.get("asset_class") == "Equity":
        return "Equity exposure"
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
    if row.get("asset_class") == "Equity":
        pnl = f" P&L {_signed_money(row['pnl'])}." if row.get("pnl_known") else ""
        return f"equity exposure {_money(row['spot_value'])} ({abs(row['net_delta']) / total_value:.1%} of portfolio).{pnl} {row['readout']}."
    spot = float(row["spot_value"])
    perp = float(row["perp_signed"])
    net = float(row["net_delta"])
    direction = "short" if perp < 0 else "long"
    if abs(perp) <= 0.01:
        return f"spot {_money(spot)}, no perp notional. {row['readout']}."
    pnl = f" Combined P&L {_signed_money(row['pnl'])}." if row.get("pnl_known") else ""
    return (
        f"spot {_money(spot)} versus {_money(abs(perp))} {direction} perp notional; "
        f"net read {_signed_money(net)} ({abs(net) / total_value:.1%} of portfolio).{pnl} {row['readout']}."
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
    if cached_order is None:
        self.hyperliquid_status_var.set("Hyperliquid: no open order selected")
        self._set_preview_text(
            "HYPERLIQUID EDIT ORDER BLOCKED\n"
            "==============================\n\n"
            "Select an open order first.\n\n"
            "Edit Order modifies the selected loaded Hyperliquid open order. It does not guess from the first cached order when multiple orders are present.\n\n"
            "Next steps:\n"
            "- Open the Hyperliquid Open Orders tab.\n"
            "- Select the specific order row you want to change.\n"
            "- Click that row's Edit action or click Edit Order."
        )
        messagebox.showinfo(
            "Select an open order first",
            "Select an open order row first, then click Edit.",
        )
        return

    normalized_order = normalize_hyperliquid_open_order(cached_order) if cached_order else None
    raw_order_id = self.cancel_order_id_var.get().strip()
    if not raw_order_id and normalized_order is not None:
        raw_order_id = normalized_order.oid

    market = normalized_order.coin if normalized_order else _order_market_for_edit(self, cached_order)
    context = normalized_order.context if normalized_order else _order_edit_context(self, cached_order, market)
    side = normalized_order.side if normalized_order else _order_side_for_edit(cached_order, self.side_var.get())
    size = _format_hyperliquid_size(normalized_order.size) if normalized_order else str((cached_order or {}).get("sz") or (cached_order or {}).get("size") or self.quantity_var.get()).strip()
    price = (
        _format_hyperliquid_size(normalized_order.limit_price)
        if normalized_order
        else str((cached_order or {}).get("limitPx") or (cached_order or {}).get("price") or self.limit_price_var.get()).strip()
    )
    tif = normalized_order.tif if normalized_order else str((cached_order or {}).get("tif") or (cached_order or {}).get("timeInForce") or self.hyperliquid_tif_var.get() or "Gtc")
    reduce_only = normalized_order.reduce_only if normalized_order else bool((cached_order or {}).get("reduceOnly", (cached_order or {}).get("reduce_only", self.hyperliquid_reduce_only_var.get())))
    close_position = normalized_order.close_position if normalized_order else False
    is_trigger = normalized_order.is_trigger if normalized_order else False
    trigger_price = "" if normalized_order is None or normalized_order.trigger_price is None else _format_hyperliquid_size(normalized_order.trigger_price)
    trigger_kind = normalized_order.trigger_kind if normalized_order else "sl"
    is_market_trigger = normalized_order.is_market_trigger if normalized_order else False

    dialog = tk.Toplevel(self)
    dialog.title("Edit Hyperliquid Open Order")
    dialog.transient(self)
    dialog.resizable(False, False)

    shell = ttk.Frame(dialog, style="Panel.TFrame", padding=16)
    shell.pack(fill=tk.BOTH, expand=True)
    shell.columnconfigure(0, weight=1)

    order_id_var = tk.StringVar(value=raw_order_id)
    market_var = tk.StringVar(value=market)
    side_var = tk.StringVar(value=side)
    size_var = tk.StringVar(value=size)
    price_var = tk.StringVar(value=price)
    tif_var = tk.StringVar(value=tif if tif in HYPERLIQUID_TIFS else "Gtc")
    context_var = tk.StringVar(value=context)
    order_kind_var = tk.StringVar(value=normalized_order.order_kind if normalized_order else "Limit")
    reduce_only_var = tk.BooleanVar(value=reduce_only if context == "Perp" else False)
    size_mode_var = tk.StringVar(value="Close Position" if close_position else "Numeric size")
    trigger_enabled_var = tk.BooleanVar(value=is_trigger)
    trigger_price_var = tk.StringVar(value=trigger_price)
    trigger_kind_var = tk.StringVar(value=trigger_kind)
    market_trigger_var = tk.BooleanVar(value=is_market_trigger)
    mid_status_var = tk.StringVar(value="")

    summary = ttk.LabelFrame(shell, text="Order Summary", style="Card.TLabelframe")
    summary.grid(row=0, column=0, sticky="ew")
    for column in range(4):
        summary.columnconfigure(column, weight=1)
    _summary_cell(summary, 0, 0, "Coin", market or "--")
    _summary_cell(summary, 0, 1, "Type", order_kind_var.get())
    _summary_cell(summary, 0, 2, "Direction", normalized_order.direction if normalized_order else side)
    _summary_cell(summary, 0, 3, "OID", raw_order_id or "--")
    _summary_cell(summary, 1, 0, "Size", normalized_order.size_label if normalized_order else size)
    _summary_cell(summary, 1, 1, "Original size", normalized_order.original_size_label if normalized_order else "--")
    _summary_cell(summary, 1, 2, "Price", normalized_order.price_label if normalized_order else price)
    _summary_cell(summary, 1, 3, "Time", _order_time_label(cached_order or {}))
    _summary_cell(summary, 2, 0, "Reduce-only", "Yes" if reduce_only else "No")
    _summary_cell(summary, 2, 1, "Trigger", normalized_order.trigger_condition if normalized_order else "--")
    _summary_cell(summary, 2, 2, "TP/SL", normalized_order.tpsl_label if normalized_order else "--")
    _summary_cell(summary, 2, 3, "Venue", context)

    editable = ttk.LabelFrame(shell, text="Editable Fields", style="Card.TLabelframe")
    editable.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    editable.columnconfigure(1, weight=1)

    editable_fields = [
        ("Size mode", ttk.Combobox(editable, textvariable=size_mode_var, values=["Numeric size", "Close Position"], state="readonly")),
        ("Size", ttk.Entry(editable, textvariable=size_var)),
    ]
    for row, (label, widget) in enumerate(editable_fields):
        ttk.Label(editable, text=label, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        widget.grid(row=row, column=1, sticky="ew", pady=4)

    price_row = len(editable_fields)
    ttk.Label(editable, text="Price", style="Subtle.TLabel").grid(row=price_row, column=0, sticky="w", padx=(0, 10), pady=4)
    price_controls = ttk.Frame(editable, style="Panel.TFrame")
    price_controls.grid(row=price_row, column=1, sticky="ew", pady=4)
    price_controls.columnconfigure(0, weight=1)
    ttk.Entry(price_controls, textvariable=price_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(
        price_controls,
        text="Mid",
        command=lambda: _fill_edit_dialog_mid_price(context_var, market_var, price_var, mid_status_var),
        style="CompactAccent.TButton",
    ).grid(row=0, column=1, sticky="ew")
    ttk.Label(price_controls, textvariable=mid_status_var, style="Subtle.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))

    ttk.Label(editable, text="TIF", style="Subtle.TLabel").grid(row=price_row + 1, column=0, sticky="w", padx=(0, 10), pady=4)
    tif_combo = ttk.Combobox(editable, textvariable=tif_var, values=HYPERLIQUID_TIFS, state="readonly")
    tif_combo.grid(row=price_row + 1, column=1, sticky="ew", pady=4)

    trigger = ttk.LabelFrame(shell, text="Trigger / TP-SL", style="Card.TLabelframe")
    trigger.grid(row=2, column=0, sticky="ew", pady=(10, 0))
    trigger.columnconfigure(1, weight=1)
    ttk.Checkbutton(trigger, text="Trigger order", variable=trigger_enabled_var).grid(row=0, column=0, sticky="w", pady=4)
    ttk.Combobox(trigger, textvariable=trigger_kind_var, values=["sl", "tp"], state="readonly").grid(row=0, column=1, sticky="ew", pady=4)
    ttk.Label(trigger, text="Trigger price", style="Subtle.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
    ttk.Entry(trigger, textvariable=trigger_price_var).grid(row=1, column=1, sticky="ew", pady=4)
    ttk.Checkbutton(trigger, text="Market when triggered", variable=market_trigger_var).grid(row=2, column=1, sticky="w", pady=4)

    advanced = ttk.LabelFrame(shell, text="Advanced", style="Card.TLabelframe")
    advanced.grid(row=3, column=0, sticky="ew", pady=(10, 0))
    advanced.columnconfigure(0, weight=1)
    reduce_only_check = ttk.Checkbutton(advanced, text="Reduce-only", variable=reduce_only_var)
    reduce_only_check.grid(row=0, column=0, sticky="w", pady=4)

    def refresh_context_fields(*_args: Any) -> None:
        if context_var.get() == "Spot":
            reduce_only_var.set(False)
            reduce_only_check.configure(state="disabled")
            if size_mode_var.get() == "Close Position":
                size_mode_var.set("Numeric size")
        else:
            reduce_only_check.configure(state="normal")
        close_mode = size_mode_var.get() == "Close Position"
        trigger_state = "normal" if trigger_enabled_var.get() else "disabled"
        tif_combo.configure(state="disabled" if trigger_enabled_var.get() else "readonly")
        if close_mode:
            trigger_enabled_var.set(True)
            trigger_state = "normal"

    context_var.trace_add("write", refresh_context_fields)
    size_mode_var.trace_add("write", refresh_context_fields)
    trigger_enabled_var.trace_add("write", refresh_context_fields)
    refresh_context_fields()

    note = ttk.Label(
        shell,
        text="This uses Hyperliquid's modify order flow for the selected OID. Price and size are the primary edits; reduce-only and trigger fields are carried forward from the selected order unless changed below.",
        style="Subtle.TLabel",
        wraplength=520,
    )
    note.grid(row=4, column=0, sticky="w", pady=(10, 2))

    buttons = ttk.Frame(shell, style="Panel.TFrame")
    buttons.grid(row=5, column=0, sticky="ew", pady=(12, 0))
    buttons.columnconfigure((0, 1), weight=1)

    def submit_edit() -> None:
        self.edit_hyperliquid_order_guarded(
            order_id_var.get(),
            market_var.get(),
            side_var.get(),
            size_var.get(),
            price_var.get(),
            tif_var.get(),
            context_var.get(),
            reduce_only_var.get(),
            size_mode_var.get(),
            trigger_enabled_var.get(),
            trigger_price_var.get(),
            trigger_kind_var.get(),
            market_trigger_var.get(),
            dialog,
        )

    ttk.Button(buttons, text="Close", command=dialog.destroy).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(buttons, text="Submit Edit", command=submit_edit, style="CompactDanger.TButton").grid(row=0, column=1, sticky="ew")


def _summary_cell(parent: ttk.Frame, row: int, column: int, label: str, value: str) -> None:
    cell = ttk.Frame(parent, style="Panel.TFrame")
    cell.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0), pady=3)
    ttk.Label(cell, text=label.upper(), style="Subtle.TLabel").pack(anchor="w")
    ttk.Label(cell, text=value or "--", style="Mono.TLabel").pack(anchor="w")


def _fill_edit_dialog_mid_price(
    context_var: tk.StringVar, market_var: tk.StringVar, price_var: tk.StringVar, status_var: tk.StringVar
) -> None:
    try:
        from app.ui.hyperliquid_cockpit_spot_mid_extension import _format_price, _lookup_hyperliquid_spot_mid

        if context_var.get() == "Perp":
            from app.ui.options_lab_extension import _lookup_hyperliquid_mid

            coin = normalize_hyperliquid_coin(market_var.get())
            mid = _lookup_hyperliquid_mid(coin)
            basis = "allMids"
        else:
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


def normalize_hyperliquid_open_order(raw_order: dict[str, Any] | None) -> HyperliquidOpenOrder:
    order = raw_order or {}
    oid = str(order.get("oid") or "")
    coin = str(order.get("coin") or "").strip()
    context = "Spot" if coin.startswith("@") or "/" in coin else "Perp"
    side = _order_side_for_edit(order, "buy")
    reduce_only = _as_bool(order.get("reduceOnly", order.get("reduce_only", False)))
    is_trigger = _as_bool(order.get("isTrigger", False)) or _optional_order_float(order.get("triggerPx")) is not None
    order_type = str(order.get("orderType") or order.get("type") or ("Stop Market" if is_trigger else "Limit")).strip() or "Limit"
    trigger_price = _optional_order_float(order.get("triggerPx"))
    is_market_trigger = is_trigger and ("market" in order_type.lower() or _price_is_market(order))
    close_position = reduce_only and is_trigger and (_optional_order_float(order.get("sz")) or 0.0) <= 0
    size = 0.0 if close_position else (_optional_order_float(order.get("sz")) or _optional_order_float(order.get("size")) or 0.0)
    limit_price = _optional_order_float(order.get("limitPx")) or _optional_order_float(order.get("price")) or trigger_price or 0.0
    tif = str(order.get("tif") or order.get("timeInForce") or "Gtc")
    trigger_condition = _trigger_condition_label(order, trigger_price)
    trigger_kind = _trigger_kind_from_order(order, side, trigger_condition)
    direction = _direction_label(side, reduce_only, close_position)
    size_label = "Close Position" if close_position else _format_hyperliquid_size(size)
    price_label = "Market" if is_market_trigger else _format_order_price_label(limit_price)
    original_size = order.get("origSz", order.get("origSize"))
    original_size_label = "--" if original_size in (None, "") else str(original_size)
    order_value_label = _order_value_label(order, size, limit_price, close_position)
    tpsl_label = _tpsl_label(order, trigger_kind, is_trigger)

    return HyperliquidOpenOrder(
        raw=order,
        oid=oid,
        context=context,
        order_kind=order_type,
        coin=coin,
        side=side,
        direction=direction,
        size=size,
        size_label=size_label,
        close_position=close_position,
        original_size_label=original_size_label,
        order_value_label=order_value_label,
        price_label=price_label,
        limit_price=limit_price,
        reduce_only=reduce_only,
        is_trigger=is_trigger,
        trigger_condition=trigger_condition,
        trigger_price=trigger_price,
        trigger_kind=trigger_kind,
        is_market_trigger=is_market_trigger,
        tif=tif,
        tpsl_label=tpsl_label,
    )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def _optional_order_float(value: Any) -> float | None:
    if value in (None, "", "--"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


def _price_is_market(order: dict[str, Any]) -> bool:
    raw_price = str(order.get("price") or order.get("limitPx") or "").strip().lower()
    return raw_price == "market"


def _trigger_condition_label(order: dict[str, Any], trigger_price: float | None) -> str:
    existing = str(order.get("triggerCondition") or "").strip()
    if existing:
        return existing
    if trigger_price is None:
        return "--"
    return f"Price above {_format_hyperliquid_size(trigger_price)}" if _trigger_is_price_above(order) else f"Price below {_format_hyperliquid_size(trigger_price)}"


def _trigger_is_price_above(order: dict[str, Any]) -> bool:
    condition = str(order.get("triggerCondition") or "").lower()
    if "above" in condition:
        return True
    if "below" in condition:
        return False
    side = _order_side_for_edit(order, "buy")
    reduce_only = _as_bool(order.get("reduceOnly", order.get("reduce_only", False)))
    return side == "buy" and reduce_only


def _trigger_kind_from_order(order: dict[str, Any], side: str, trigger_condition: str) -> str:
    raw_type = str(order.get("orderType") or order.get("type") or "").lower()
    raw_tpsl = str(order.get("tpsl") or order.get("tpSl") or "").lower()
    if "take" in raw_type or raw_tpsl == "tp":
        return "tp"
    if "stop" in raw_type or raw_tpsl == "sl":
        return "sl"
    if "above" in trigger_condition.lower() and side == "buy":
        return "sl"
    if "below" in trigger_condition.lower() and side == "sell":
        return "sl"
    return "tp"


def _direction_label(side: str, reduce_only: bool, close_position: bool) -> str:
    if reduce_only and close_position:
        return "Close Short" if side == "buy" else "Close Long"
    if reduce_only:
        return "Reduce Short" if side == "buy" else "Reduce Long"
    return "Buy" if side == "buy" else "Sell"


def _format_order_price_label(value: float) -> str:
    return "--" if value <= 0 else _format_hyperliquid_size(value)


def _order_value_label(order: dict[str, Any], size: float, price: float, close_position: bool) -> str:
    for key in ("orderValue", "orderValueUsd", "value"):
        if order.get(key) not in (None, ""):
            return str(order[key])
    if close_position or size <= 0 or price <= 0:
        return "--"
    return f"{size * price:,.2f} USDC"


def _tpsl_label(order: dict[str, Any], trigger_kind: str, is_trigger: bool) -> str:
    if _as_bool(order.get("isPositionTpsl", False)):
        return "TP" if trigger_kind == "tp" else "SL"
    if is_trigger:
        return "TP" if trigger_kind == "tp" else "SL"
    return "--"


def _format_optional_trigger_price(value: float | None) -> str:
    return "--" if value is None else f"${value:,.4f}"


def _selected_hyperliquid_order(self: tk.Tk) -> dict[str, Any] | None:
    orders = getattr(self, "hyperliquid_open_order_by_oid", {})
    selected_order_id = _selected_workspace_hyperliquid_order_id(self)
    if selected_order_id == "":
        return None
    raw_order_id = selected_order_id or self.cancel_order_id_var.get().strip()
    if raw_order_id and raw_order_id in orders:
        self.cancel_order_id_var.set(raw_order_id)
        return orders[raw_order_id]
    return None


def _selected_workspace_hyperliquid_order_id(self: tk.Tk) -> str | None:
    table = getattr(self, "hyperliquid_workspace_open_orders_table", None)
    if table is None:
        return None
    try:
        row_ids = tuple(table.selection())
    except Exception:
        return None
    if not row_ids:
        try:
            return "" if table.get_children() else None
        except Exception:
            return ""
    row_id = row_ids[0]
    try:
        raw_values = table.item(row_id, "values")
        columns = tuple(table["columns"])
    except Exception:
        return ""
    values = {str(column): str(raw_values[index]) for index, column in enumerate(columns) if index < len(raw_values)}
    return values.get("oid", "").strip()


def _order_market_for_edit(self: tk.Tk, order: dict[str, Any] | None) -> str:
    raw_market = str((order or {}).get("coin") or "").strip()
    if raw_market:
        return raw_market
    symbol_source = self.symbol_var.get().strip() or self.hyperliquid_coin_var.get().strip()
    active_ticket = getattr(self, "hyperliquid_workspace_active_ticket_var", tk.StringVar(value="spot")).get()
    if active_ticket == "perp" and symbol_source:
        return normalize_hyperliquid_coin(symbol_source)
    return normalize_hyperliquid_spot_market(symbol_source) if symbol_source else ""


def _order_edit_context(self: tk.Tk, order: dict[str, Any] | None, market: str) -> str:
    raw_market = str((order or {}).get("coin") or market or "").strip().upper()
    if raw_market.startswith("@") or "/" in raw_market:
        return "Spot"
    active_ticket = getattr(self, "hyperliquid_workspace_active_ticket_var", tk.StringVar(value="spot")).get()
    if active_ticket == "perp":
        return "Perp"
    return "Spot"


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
    raw_context: str = "Spot",
    reduce_only: bool = False,
    raw_size_mode: str = "Numeric size",
    is_trigger: bool = False,
    raw_trigger_price: str = "",
    raw_trigger_kind: str = "sl",
    is_market_trigger: bool = False,
    dialog: tk.Toplevel | None = None,
) -> None:
    try:
        order_id = int(raw_order_id.strip())
    except ValueError:
        messagebox.showerror("Hyperliquid edit blocked", "Hyperliquid order ID must be a number.")
        return

    try:
        market = _normalize_edit_market(raw_market, raw_context)
        side = raw_side.strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("Side must be buy or sell.")
        close_position = raw_size_mode == "Close Position"
        size = 0.0 if close_position else float(raw_size.strip().replace(",", ""))
        trigger_price = float(raw_trigger_price.strip().replace(",", "")) if is_trigger else None
        limit_price = (
            trigger_price
            if is_trigger and is_market_trigger and trigger_price is not None
            else float(raw_limit_price.strip().replace(",", ""))
        )
        tif = raw_tif.strip() or "Gtc"
        if not is_trigger and tif not in HYPERLIQUID_TIFS:
            raise ValueError("TIF must be Alo, Ioc, or Gtc.")
        if close_position and not is_trigger:
            raise ValueError("Numeric size is not required for Close Position stop orders, but Close Position edits must remain trigger/TP-SL orders.")
        if is_trigger and trigger_price is None:
            raise ValueError("Trigger edits require a trigger price.")
        trigger_kind = raw_trigger_kind.strip().lower() or "sl"
        ticket = HyperliquidOrderEditTicket(
            coin=market,
            is_buy=side == "buy",
            size=size,
            limit_price=limit_price,
            tif=tif,
            reduce_only=bool(reduce_only) if raw_context == "Perp" else False,
            is_trigger=is_trigger,
            trigger_price=trigger_price,
            trigger_kind=trigger_kind,
            is_market_trigger=is_market_trigger,
            close_position=close_position,
        )
    except Exception as exc:
        messagebox.showerror("Hyperliquid edit blocked", str(exc))
        return

    config = HyperliquidTradingConfig()
    try:
        config.validate_edit_for_live(ticket)
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
        f"New size: {ticket.size_label}\n"
        f"New price: ${ticket.wire_limit_price:,.4f}\n"
        f"Trigger: {'yes' if ticket.is_trigger else 'no'}\n"
        f"TIF: {ticket.tif}\n"
        f"Reduce-only: {'yes' if ticket.reduce_only else 'no'}\n\n"
        "Continue?",
    )
    if not ok:
        return

    try:
        result = HyperliquidExecutionAdapter().modify_order_edit(order_id, ticket)
        self.cancel_order_id_var.set(str(order_id))
        self.symbol_var.set(_display_spot_base(ticket.coin))
        self.hyperliquid_coin_var.set(_display_spot_base(ticket.coin))
        self.side_var.set("buy" if ticket.is_buy else "sell")
        if not ticket.close_position:
            self.quantity_var.set(_format_hyperliquid_size(ticket.size))
        self.limit_price_var.set(_format_hyperliquid_size(ticket.wire_limit_price))
        self.hyperliquid_tif_var.set(ticket.tif)
        self.hyperliquid_status_var.set("Hyperliquid: edit attempted")
        self._set_preview_text(
            "HYPERLIQUID EDIT ORDER RESULT\n"
            "=============================\n\n"
            f"Order ID: {order_id}\n"
            f"Market: {ticket.coin}\n"
            f"Side: {ticket.side_label}\n"
            f"Size: {ticket.size_label}\n"
            f"Price: ${ticket.wire_limit_price:,.4f}\n"
            f"Trigger: {'yes' if ticket.is_trigger else 'no'}\n"
            f"Trigger price: {_format_optional_trigger_price(ticket.trigger_price)}\n\n"
            f"Reduce-only: {'yes' if ticket.reduce_only else 'no'}\n\n"
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


def _normalize_edit_market(raw_market: str, raw_context: str = "Spot") -> str:
    market = raw_market.strip().upper()
    if not market:
        raise ValueError("Enter a Hyperliquid market.")
    if market.startswith("@"):
        return market
    if raw_context.strip().lower() == "perp":
        return normalize_hyperliquid_coin(market)
    return normalize_hyperliquid_spot_market(market)


def _show_hyperliquid_position_tpsl_dialog(self: tk.Tk) -> None:
    _ensure_hyperliquid_vars(self)
    self.trade_venue_var.set("Hyperliquid")

    try:
        coin = normalize_hyperliquid_coin(self.hyperliquid_coin_var.get().strip() or self.symbol_var.get().strip())
        position, is_short = _current_hyperliquid_perp_position(self, coin)
    except Exception as exc:
        messagebox.showerror("Hyperliquid TP/SL blocked", str(exc))
        return

    close_side = "buy" if is_short else "sell"
    mark_price = position.last_price or position.average_cost
    try:
        mark_price = _lookup_hyperliquid_perp_mid(coin)
    except Exception:
        pass
    existing = _matching_tpsl_orders(self, coin)
    direction = "SHORT" if is_short else "LONG"

    dialog = tk.Toplevel(self)
    dialog.title(f"Hyperliquid Perp TP/SL - {coin} {direction}")
    dialog.transient(self)
    dialog.resizable(False, False)

    shell = ttk.Frame(dialog, style="Panel.TFrame", padding=14)
    shell.pack(fill=tk.BOTH, expand=True)
    shell.columnconfigure(1, weight=1)

    coin_var = tk.StringVar(value=coin)
    size_var = tk.StringVar(value=_format_hyperliquid_size(position.quantity))
    existing_tp = getattr(self, "hyperliquid_target_price_var", tk.StringVar(value="")).get().strip()
    existing_sl = (getattr(self, "hyperliquid_bad_price_var", tk.StringVar(value="")).get() or getattr(self, "stop_price_var", tk.StringVar(value="")).get()).strip()
    tp_var = tk.StringVar(value=existing_tp or _format_hyperliquid_size(mark_price * (0.95 if is_short else 1.05)))
    sl_var = tk.StringVar(value=existing_sl or _format_hyperliquid_size(mark_price * (1.03 if is_short else 0.97)))
    configure_amount_var = tk.BooleanVar(value=False)
    limit_trigger_var = tk.BooleanVar(value=False)
    limit_price_var = tk.StringVar(value=_format_hyperliquid_size(mark_price))

    target = ttk.LabelFrame(shell, text=f"Target Position: {coin} {direction}", style="Card.TLabelframe")
    target.grid(row=0, column=0, columnspan=2, sticky="ew")
    for column in range(3):
        target.columnconfigure(column, weight=1)
    _summary_cell(target, 0, 0, "Coin", coin)
    _summary_cell(target, 0, 1, "Position", f"{position.quantity:g} {coin} {direction.lower()}")
    _summary_cell(target, 0, 2, "Closing Side", close_side)
    _summary_cell(target, 1, 0, "Entry", f"${position.average_cost:,.4f}")
    _summary_cell(target, 1, 1, "Mark", f"${mark_price:,.4f}")
    _summary_cell(target, 1, 2, "Existing TP/SL", str(len(existing)))

    row = 1
    if existing:
        existing_frame = ttk.LabelFrame(shell, text="Existing TP/SL", style="Card.TLabelframe")
        existing_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        existing_frame.columnconfigure(0, weight=1)
        for existing_row, order in enumerate(existing):
            label = f"{order.tpsl_label}: {order.trigger_condition} · {order.size_label} · oid {order.oid}"
            ttk.Label(existing_frame, text=label, style="Subtle.TLabel").grid(row=existing_row, column=0, sticky="w", pady=3)
            ttk.Button(
                existing_frame,
                text="Cancel",
                command=lambda oid=order.oid: _cancel_tpsl_from_dialog(self, oid),
                style="CompactDanger.TButton",
            ).grid(row=existing_row, column=1, sticky="ew", padx=(8, 0), pady=3)
        row += 1
    else:
        ttk.Label(
            shell,
            text=f"No existing TP/SL orders were found for {coin}. Confirming below creates new reduce-only trigger order(s) for this {direction.lower()} position.",
            style="Subtle.TLabel",
            wraplength=500,
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 4))
        row += 1

    ttk.Label(shell, text="TP price", style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
    ttk.Entry(shell, textvariable=tp_var).grid(row=row, column=1, sticky="ew", pady=5)
    row += 1

    ttk.Label(shell, text="SL price", style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
    ttk.Entry(shell, textvariable=sl_var).grid(row=row, column=1, sticky="ew", pady=5)
    row += 1

    amount_check = ttk.Checkbutton(shell, text="Configure amount", variable=configure_amount_var)
    amount_check.grid(row=row, column=0, sticky="w", pady=5)
    amount_entry = ttk.Entry(shell, textvariable=size_var)
    amount_entry.grid(row=row, column=1, sticky="ew", pady=5)
    row += 1

    limit_check = ttk.Checkbutton(shell, text="Limit trigger", variable=limit_trigger_var)
    limit_check.grid(row=row, column=0, sticky="w", pady=5)
    limit_controls = ttk.Frame(shell, style="Panel.TFrame")
    limit_controls.grid(row=row, column=1, sticky="ew", pady=5)
    limit_controls.columnconfigure(0, weight=1)
    ttk.Entry(limit_controls, textvariable=limit_price_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(
        limit_controls,
        text="Mid",
        command=lambda: limit_price_var.set(_format_hyperliquid_size(_lookup_hyperliquid_perp_mid(coin_var.get()))),
        style="CompactAccent.TButton",
    ).grid(row=0, column=1, sticky="ew")
    row += 1

    note = ttk.Label(
        shell,
        text="Creates new reduce-only Hyperliquid position TP/SL trigger orders. Existing open orders are optional; leave TP or SL blank to skip that side.",
        style="Subtle.TLabel",
        wraplength=430,
    )
    note.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 2))
    row += 1

    buttons = ttk.Frame(shell, style="Panel.TFrame")
    buttons.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    buttons.columnconfigure((0, 1), weight=1)

    def submit_tpsl() -> None:
        amount = size_var.get() if configure_amount_var.get() else _format_hyperliquid_size(position.quantity)
        limit_price = limit_price_var.get() if limit_trigger_var.get() else ""
        self.place_hyperliquid_position_tpsl_guarded(
            coin_var.get(),
            close_side,
            amount,
            tp_var.get(),
            sl_var.get(),
            limit_price,
            not limit_trigger_var.get(),
            dialog,
        )

    ttk.Button(buttons, text="Close", command=dialog.destroy).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(buttons, text="Confirm TP/SL", command=submit_tpsl, style="CompactDanger.TButton").grid(row=0, column=1, sticky="ew")


def _place_hyperliquid_position_tpsl_guarded(
    self: tk.Tk,
    raw_coin: str,
    raw_close_side: str,
    raw_size: str,
    raw_tp_price: str,
    raw_sl_price: str,
    raw_limit_price: str = "",
    is_market: bool = True,
    dialog: tk.Toplevel | None = None,
) -> None:
    try:
        coin = normalize_hyperliquid_coin(raw_coin)
        close_side = raw_close_side.strip().lower()
        if close_side not in {"buy", "sell"}:
            raise ValueError("Closing side must be buy or sell.")
        size = float(raw_size.strip().replace(",", ""))
        limit_price = float(raw_limit_price.strip().replace(",", "")) if raw_limit_price.strip() else None
        tickets: list[HyperliquidTriggerTicket] = []
        for raw_price, kind in ((raw_tp_price, "tp"), (raw_sl_price, "sl")):
            if not raw_price.strip():
                continue
            tickets.append(
                HyperliquidTriggerTicket(
                    coin=coin,
                    is_buy=close_side == "buy",
                    size=size,
                    trigger_price=float(raw_price.strip().replace(",", "")),
                    tpsl=kind,
                    is_market=is_market,
                    limit_price=limit_price,
                )
            )
        if not tickets:
            raise ValueError("Enter a TP price, an SL price, or both.")
    except Exception as exc:
        messagebox.showerror("Hyperliquid TP/SL blocked", str(exc))
        return

    config = HyperliquidTradingConfig()
    try:
        for ticket in tickets:
            config.validate_trigger_for_live(ticket)
    except Exception as exc:
        self._set_preview_text(
            "HYPERLIQUID TP/SL BLOCKED\n"
            "=========================\n\n"
            f"{exc}\n\n"
            "Required local .env gates:\n"
            "- HYPE_WALLET_ADDRESS\n"
            "- HYPE_API_ADDRESS\n"
            "- HYPE_API_SECRET\n"
            "- HYPERLIQUID_ENABLE_LIVE_ORDERS=true\n\n"
        )
        messagebox.showerror("Hyperliquid TP/SL blocked", str(exc))
        return

    summary = "\n".join(
        f"- {ticket.kind_label}: {ticket.side_label} {ticket.size:g} {ticket.coin} at trigger ${ticket.trigger_price:,.4f}"
        for ticket in tickets
    )
    ok = messagebox.askyesno(
        "FINAL HYPERLIQUID TP/SL CONFIRMATION",
        "This will place LIVE reduce-only Hyperliquid TP/SL trigger order(s).\n\n"
        f"{summary}\n\n"
        f"Trigger style: {'market' if is_market else 'limit'}\n\n"
        "Continue?",
    )
    if not ok:
        return

    try:
        result = HyperliquidExecutionAdapter().place_position_tpsl(tickets)
        self.hyperliquid_status_var.set("Hyperliquid: TP/SL attempted")
        self._set_preview_text(
            "HYPERLIQUID POSITION TP/SL RESULT\n"
            "=================================\n\n"
            f"{summary}\n\n"
            f"Response:\n{result}\n\n"
            "Refreshing Hyperliquid open orders..."
        )
        if dialog is not None:
            dialog.destroy()
        try:
            self.load_hyperliquid_open_orders(title="HYPERLIQUID OPEN ORDERS AFTER TP/SL")
        except Exception:
            pass
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: TP/SL failed")
        messagebox.showerror("Hyperliquid TP/SL failed", str(exc))


def _show_hyperliquid_perp_position_size(self: tk.Tk) -> None:
    _ensure_hyperliquid_vars(self)
    self.trade_venue_var.set("Hyperliquid")
    try:
        coin = normalize_hyperliquid_coin(self.hyperliquid_coin_var.get().strip() or self.symbol_var.get().strip())
        position, is_short = _current_hyperliquid_perp_position(self, coin)
    except Exception as exc:
        messagebox.showerror("Hyperliquid position size failed", str(exc))
        return

    mark = position.last_price or position.average_cost
    entry = position.average_cost or mark
    qty = abs(float(getattr(position, "quantity", 0.0) or 0.0))
    notional = qty * mark
    direction = "SHORT" if is_short else "LONG"
    tp_price = _optional_order_float(getattr(self, "hyperliquid_target_price_var", tk.StringVar(value="")).get())
    sl_price = _optional_order_float(getattr(self, "hyperliquid_bad_price_var", tk.StringVar(value="")).get()) or _optional_order_float(getattr(self, "stop_price_var", tk.StringVar(value="")).get())
    if tp_price is None:
        tp_price = mark * (0.95 if is_short else 1.05)
    if sl_price is None:
        sl_price = mark * (1.03 if is_short else 0.97)
    tp_pnl = _perp_position_pnl(entry, tp_price, qty, is_short)
    sl_pnl = _perp_position_pnl(entry, sl_price, qty, is_short)
    mark_pnl = _perp_position_pnl(entry, mark, qty, is_short)
    rr = _risk_reward(tp_pnl - mark_pnl, sl_pnl - mark_pnl)

    self.hyperliquid_status_var.set("Hyperliquid: position size ready")
    self._set_preview_text(
        "HYPERLIQUID PERP POSITION SIZE\n"
        "==============================\n\n"
        f"Coin: {coin}\n"
        f"Current position: {direction} {qty:g} {coin}\n"
        f"Entry: ${entry:,.4f}\n"
        f"Mark: ${mark:,.4f}\n"
        f"Current notional: ${notional:,.2f}\n"
        f"Open P&L estimate from entry: {_signed_money(mark_pnl)}\n\n"
        "TP / SL planning\n"
        f"- Take-profit trigger: ${tp_price:,.4f} -> position P&L {_signed_money(tp_pnl)}\n"
        f"- Stop-loss trigger: ${sl_price:,.4f} -> position P&L {_signed_money(sl_pnl)}\n"
        f"- Reward/risk from current mark: {rr}\n\n"
        "How to use this\n"
        "- This uses the synced Hyperliquid perp position, not Schwab candles.\n"
        "- TP/SL can create new reduce-only trigger orders even when there are no existing open orders to edit.\n"
        "- Open Only shows existing open orders only; it will not show an order ID for a position that has no TP/SL order yet."
    )


def _show_hyperliquid_perp_position_editor(self: tk.Tk) -> None:
    _ensure_hyperliquid_vars(self)
    self.trade_venue_var.set("Hyperliquid")
    try:
        coin = normalize_hyperliquid_coin(self.hyperliquid_coin_var.get().strip() or self.symbol_var.get().strip())
        position, is_short = _current_hyperliquid_perp_position(self, coin)
    except Exception as exc:
        messagebox.showerror("Hyperliquid position edit blocked", str(exc))
        return

    mark = position.last_price or position.average_cost
    try:
        mark = _lookup_hyperliquid_perp_mid(coin)
    except Exception:
        pass

    entry = position.average_cost or mark
    qty = abs(float(getattr(position, "quantity", 0.0) or 0.0))
    direction = "SHORT" if is_short else "LONG"
    close_side = "buy" if is_short else "sell"
    reverse_side = close_side

    dialog = tk.Toplevel(self)
    dialog.title(f"Edit Hyperliquid Perp Position - {coin} {direction}")
    dialog.transient(self)
    dialog.resizable(False, False)

    shell = ttk.Frame(dialog, style="Panel.TFrame", padding=14)
    shell.pack(fill=tk.BOTH, expand=True)
    shell.columnconfigure(0, weight=1)

    summary = ttk.LabelFrame(shell, text="Position", style="Card.TLabelframe")
    summary.grid(row=0, column=0, sticky="ew")
    for column in range(4):
        summary.columnconfigure(column, weight=1)
    _summary_cell(summary, 0, 0, "Coin", coin)
    _summary_cell(summary, 0, 1, "Side", direction)
    _summary_cell(summary, 0, 2, "Size", f"{qty:g}")
    _summary_cell(summary, 0, 3, "Close Side", close_side.upper())
    _summary_cell(summary, 1, 0, "Entry", f"${entry:,.4f}")
    _summary_cell(summary, 1, 1, "Mark", f"${mark:,.4f}")
    _summary_cell(summary, 1, 2, "Notional", f"${qty * mark:,.2f}")
    _summary_cell(summary, 1, 3, "Open P&L", _signed_money(_perp_position_pnl(entry, mark, qty, is_short)))

    fields = ttk.LabelFrame(shell, text="Ticket Values", style="Card.TLabelframe")
    fields.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    fields.columnconfigure(1, weight=1)
    fields.columnconfigure(3, weight=1)

    amount_var = tk.StringVar(value=_format_hyperliquid_size(qty))
    limit_var = tk.StringVar(value=_format_hyperliquid_size(mark))
    tp_var = tk.StringVar(value=getattr(self, "hyperliquid_target_price_var", tk.StringVar(value="")).get())
    sl_var = tk.StringVar(value=(getattr(self, "hyperliquid_bad_price_var", tk.StringVar(value="")).get() or getattr(self, "stop_price_var", tk.StringVar(value="")).get()))

    ttk.Label(fields, text="Amount", style="Subtle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
    ttk.Entry(fields, textvariable=amount_var).grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=5)
    ttk.Label(fields, text="Limit / IOC price", style="Subtle.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8), pady=5)
    limit_controls = ttk.Frame(fields, style="Panel.TFrame")
    limit_controls.grid(row=0, column=3, sticky="ew", pady=5)
    limit_controls.columnconfigure(0, weight=1)
    ttk.Entry(limit_controls, textvariable=limit_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))

    def _use_perp_mid() -> None:
        try:
            mid = _set_hyperliquid_perp_mid_price(coin, limit_var)
            self.hyperliquid_status_var.set(f"Hyperliquid: {coin} perp mid ${mid:,.4f}")
        except Exception as exc:
            self.hyperliquid_status_var.set("Hyperliquid: perp mid failed")
            messagebox.showerror("Hyperliquid perp mid failed", str(exc))

    ttk.Button(limit_controls, text="Mid", command=_use_perp_mid, style="CompactAccent.TButton").grid(row=0, column=1, sticky="ew")
    ttk.Label(fields, text="TP price", style="Subtle.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
    ttk.Entry(fields, textvariable=tp_var).grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=5)
    ttk.Label(fields, text="SL price", style="Subtle.TLabel").grid(row=1, column=2, sticky="w", padx=(0, 8), pady=5)
    ttk.Entry(fields, textvariable=sl_var).grid(row=1, column=3, sticky="ew", pady=5)

    actions = ttk.LabelFrame(shell, text="Position Actions", style="Card.TLabelframe")
    actions.grid(row=2, column=0, sticky="ew", pady=(10, 0))
    actions.columnconfigure((0, 1, 2, 3), weight=1)

    def _open_limit_close() -> None:
        _show_hyperliquid_limit_close_dialog(
            self,
            parent=dialog,
            coin=coin,
            direction=direction,
            close_side=close_side,
            qty=qty,
            entry=entry,
            mark=mark,
            pnl=_perp_position_pnl(entry, mark, qty, is_short),
            amount_var=amount_var,
            limit_var=limit_var,
            tp_var=tp_var,
            sl_var=sl_var,
        )

    def _prepare_reverse() -> None:
        try:
            reverse_size = _reverse_order_size_for_same_opposite_position(_positive_dialog_float(amount_var.get(), "Amount"))
            reverse_price = _positive_dialog_float(limit_var.get(), "Limit / IOC price")
            _prepare_hyperliquid_perp_ticket(
                self,
                coin=coin,
                side=reverse_side,
                size=reverse_size,
                limit_price=reverse_price,
                tif="Gtc",
                reduce_only=False,
                tp_price=tp_var.get(),
                sl_price=sl_var.get(),
                note=(
                    f"Prepared {coin} reverse ticket to close the current position and open the same size in the opposite direction. "
                    "This is not reduce-only; review carefully before LIVE Submit."
                ),
            )
            dialog.destroy()
        except Exception as exc:
            messagebox.showerror("Hyperliquid position edit blocked", str(exc))

    def _market_close() -> None:
        try:
            close_size = _positive_dialog_float(amount_var.get(), "Amount")
            base_price = _positive_dialog_float(limit_var.get(), "Limit / IOC price")
            close_price = _market_close_limit_price(base_price, is_short)
            ticket = HyperliquidOrderTicket(
                coin=coin,
                is_buy=close_side == "buy",
                size=close_size,
                limit_price=close_price,
                tif="Ioc",
                reduce_only=True,
            )
            result = HyperliquidExecutionAdapter().submit(ticket)
            self.hyperliquid_status_var.set(f"Hyperliquid: {coin} market close sent")
            self._set_preview_text(
                "HYPERLIQUID MARKET CLOSE RESULT\n"
                "===============================\n\n"
                f"Coin: {coin}\n"
                f"Position: {direction} {qty:g}\n"
                f"Submitted: {ticket.side_label} reduce-only {ticket.size:g} {coin}\n"
                f"TIF: IOC\n"
                f"Aggressive limit used for market-style close: ${ticket.limit_price:,.4f}\n\n"
                f"Response:\n{result}\n\n"
                "No automatic portfolio sync was run. Use Sync Hyperliquid to refresh balances and positions."
            )
            dialog.destroy()
        except Exception as exc:
            self.hyperliquid_status_var.set("Hyperliquid: market close blocked")
            messagebox.showerror("Hyperliquid market close blocked", str(exc))

    def _open_tpsl() -> None:
        self.hyperliquid_coin_var.set(coin)
        self.symbol_var.set(coin)
        self.hyperliquid_target_price_var.set(tp_var.get())
        self.hyperliquid_bad_price_var.set(sl_var.get())
        self.stop_price_var.set(sl_var.get())
        dialog.destroy()
        self.show_hyperliquid_position_tpsl_dialog()

    ttk.Button(actions, text="Limit", command=_open_limit_close, style="CompactDanger.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(actions, text="Market", command=_market_close, style="CompactDanger.TButton").grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Button(actions, text="Reverse", command=_prepare_reverse, style="CompactDanger.TButton").grid(row=0, column=2, sticky="ew", padx=(0, 6))
    ttk.Button(actions, text="TP/SL", command=_open_tpsl, style="CompactAccent.TButton").grid(row=0, column=3, sticky="ew")

    note = ttk.Label(
        shell,
        text=(
            "Position actions use the selected Hyperliquid perp position.\n"
            "Market sends a reduce-only immediate close using the existing market-close logic. "
            "Limit opens a limit-close ticket where you can confirm price and size before preparing the reduce-only close for review. "
            "Reverse prepares the opposite-side ticket for review before LIVE Submit. "
            "TP/SL opens the reduce-only take-profit / stop-loss flow."
        ),
        style="Subtle.TLabel",
        wraplength=620,
    )
    note.grid(row=3, column=0, sticky="w", pady=(10, 0))


def _show_hyperliquid_limit_close_dialog(
    self: tk.Tk,
    *,
    parent: tk.Toplevel,
    coin: str,
    direction: str,
    close_side: str,
    qty: float,
    entry: float,
    mark: float,
    pnl: float,
    amount_var: tk.StringVar,
    limit_var: tk.StringVar,
    tp_var: tk.StringVar,
    sl_var: tk.StringVar,
) -> tk.Toplevel:
    dialog = tk.Toplevel(parent)
    dialog.title("Limit Close")
    dialog.transient(parent)
    dialog.resizable(False, False)

    shell = ttk.Frame(dialog, style="Panel.TFrame", padding=14)
    shell.pack(fill=tk.BOTH, expand=True)
    shell.columnconfigure(0, weight=1)

    summary = ttk.LabelFrame(shell, text="Position", style="Card.TLabelframe")
    summary.grid(row=0, column=0, sticky="ew")
    for column in range(4):
        summary.columnconfigure(column, weight=1)
    _summary_cell(summary, 0, 0, "Coin", coin)
    _summary_cell(summary, 0, 1, "Side", direction)
    _summary_cell(summary, 0, 2, "Size", f"{qty:g}")
    _summary_cell(summary, 0, 3, "Close Side", close_side.upper())
    _summary_cell(summary, 1, 0, "Entry", f"${entry:,.4f}")
    _summary_cell(summary, 1, 1, "Mark", f"${mark:,.4f}")
    _summary_cell(summary, 1, 2, "Notional", f"${qty * mark:,.2f}")
    _summary_cell(summary, 1, 3, "Open P&L", _signed_money(pnl))

    fields = ttk.LabelFrame(shell, text="Limit Close Ticket", style="Card.TLabelframe")
    fields.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    fields.columnconfigure(1, weight=1)
    price_var = tk.StringVar(value=(limit_var.get().strip() or _format_hyperliquid_size(mark)))
    size_var = tk.StringVar(value=(amount_var.get().strip() or _format_hyperliquid_size(qty)))

    ttk.Label(fields, text="Limit price", style="Subtle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
    ttk.Entry(fields, textvariable=price_var).grid(row=0, column=1, sticky="ew", pady=5)
    ttk.Label(fields, text="Amount", style="Subtle.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
    ttk.Entry(fields, textvariable=size_var).grid(row=1, column=1, sticky="ew", pady=5)

    note = ttk.Label(
        shell,
        text=(
            "Confirming prepares a reduce-only GTC limit close ticket for review. "
            "No live order is sent until you use the existing LIVE Submit flow."
        ),
        style="Subtle.TLabel",
        wraplength=520,
    )
    note.grid(row=2, column=0, sticky="w", pady=(10, 0))

    buttons = ttk.Frame(shell, style="Panel.TFrame")
    buttons.grid(row=3, column=0, sticky="ew", pady=(12, 0))
    buttons.columnconfigure((0, 1), weight=1)

    def confirm_limit_close() -> None:
        try:
            close_size = _positive_dialog_float(size_var.get(), "Amount")
            close_price = _positive_dialog_float(price_var.get(), "Limit price")
            amount_var.set(_format_hyperliquid_size(close_size))
            limit_var.set(_format_hyperliquid_size(close_price))
            _prepare_hyperliquid_perp_ticket(
                self,
                coin=coin,
                side=close_side,
                size=close_size,
                limit_price=close_price,
                tif="Gtc",
                reduce_only=True,
                tp_price=tp_var.get(),
                sl_price=sl_var.get(),
                note=f"Prepared reduce-only {coin} {direction.lower()} limit close ticket. Review it, then use LIVE Submit.",
            )
            dialog.destroy()
            parent.destroy()
        except Exception as exc:
            messagebox.showerror("Hyperliquid limit close blocked", str(exc))

    ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(buttons, text="Confirm Limit Close", command=confirm_limit_close, style="CompactDanger.TButton").grid(row=0, column=1, sticky="ew")
    return dialog


def _reverse_order_size_for_same_opposite_position(size: float) -> float:
    if size <= 0:
        raise ValueError("Amount must be positive.")
    return size * 2.0


def _positive_dialog_float(raw: object, label: str) -> float:
    try:
        value = float(str(raw).strip().replace(",", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if value <= 0:
        raise ValueError(f"{label} must be positive.")
    return value


def _market_close_limit_price(base_price: float, is_short: bool) -> float:
    if base_price <= 0:
        raise ValueError("Market close price basis must be positive.")
    return base_price * (1.01 if is_short else 0.99)


def _prepare_hyperliquid_perp_ticket(
    self: tk.Tk,
    *,
    coin: str,
    side: str,
    size: float,
    limit_price: float,
    tif: str,
    reduce_only: bool,
    tp_price: str,
    sl_price: str,
    note: str,
) -> None:
    _ensure_hyperliquid_vars(self)
    self.trade_venue_var.set("Hyperliquid")
    self.hyperliquid_coin_var.set(coin)
    self.symbol_var.set(coin)
    self.side_var.set(side)
    self.quantity_var.set(_format_hyperliquid_size(size))
    self.limit_price_var.set(_format_hyperliquid_size(limit_price))
    self.hyperliquid_tif_var.set(tif)
    self.hyperliquid_reduce_only_var.set(reduce_only)
    self.hyperliquid_target_price_var.set(str(tp_price).strip())
    self.hyperliquid_bad_price_var.set(str(sl_price).strip())
    self.stop_price_var.set(str(sl_price).strip())
    if hasattr(self, "hyperliquid_workspace_active_ticket_var"):
        self.hyperliquid_workspace_active_ticket_var.set("perp")
    if hasattr(self, "hyperliquid_perp_coin_var"):
        self.hyperliquid_perp_coin_var.set(coin)
    if hasattr(self, "hyperliquid_perp_symbol_var"):
        self.hyperliquid_perp_symbol_var.set(coin)
    if hasattr(self, "hyperliquid_perp_side_var"):
        self.hyperliquid_perp_side_var.set(side)
    if hasattr(self, "hyperliquid_perp_quantity_var"):
        self.hyperliquid_perp_quantity_var.set(_format_hyperliquid_size(size))
    if hasattr(self, "hyperliquid_perp_limit_price_var"):
        self.hyperliquid_perp_limit_price_var.set(_format_hyperliquid_size(limit_price))
    if hasattr(self, "hyperliquid_perp_tif_var"):
        self.hyperliquid_perp_tif_var.set(tif)
    if hasattr(self, "hyperliquid_perp_reduce_only_var"):
        self.hyperliquid_perp_reduce_only_var.set(reduce_only)
    if hasattr(self, "hyperliquid_perp_target_price_var"):
        self.hyperliquid_perp_target_price_var.set(str(tp_price).strip())
    if hasattr(self, "hyperliquid_perp_stop_price_var"):
        self.hyperliquid_perp_stop_price_var.set(str(sl_price).strip())

    self.hyperliquid_status_var.set(f"Hyperliquid: prepared {coin} position action")
    self._set_preview_text(
        "HYPERLIQUID PERP POSITION ACTION PREPARED\n"
        "=========================================\n\n"
        f"{note}\n\n"
        f"Coin: {coin}\n"
        f"Side: {side.upper()}\n"
        f"Size: {size:g}\n"
        f"Limit price: ${limit_price:,.4f}\n"
        f"TIF: {tif}\n"
        f"Reduce-only: {'yes' if reduce_only else 'no'}\n"
        f"TP price field: {str(tp_price).strip() or '--'}\n"
        f"SL price field: {str(sl_price).strip() or '--'}\n\n"
        "No order was submitted. Use Preview Perp Ticket or LIVE Submit after checking the ticket."
    )


def _use_hyperliquid_perp_position(self: tk.Tk, raw_coin: str | None = None) -> None:
    _ensure_hyperliquid_vars(self)
    self.trade_venue_var.set("Hyperliquid")
    coin = normalize_hyperliquid_coin(raw_coin or self.hyperliquid_coin_var.get().strip() or self.symbol_var.get().strip())
    position, is_short = _current_hyperliquid_perp_position(self, coin)
    mark = position.last_price or position.average_cost
    qty = abs(float(getattr(position, "quantity", 0.0) or 0.0))
    direction = "SHORT" if is_short else "LONG"
    close_side = "buy" if is_short else "sell"

    self.symbol_var.set(coin)
    self.hyperliquid_coin_var.set(coin)
    self.side_var.set(close_side)
    self.quantity_var.set(_format_hyperliquid_size(qty))
    self.limit_price_var.set(_format_hyperliquid_size(mark))
    self.hyperliquid_reduce_only_var.set(True)
    if hasattr(self, "hyperliquid_workspace_active_ticket_var"):
        self.hyperliquid_workspace_active_ticket_var.set("perp")
    if hasattr(self, "hyperliquid_perp_coin_var"):
        self.hyperliquid_perp_coin_var.set(coin)
    if hasattr(self, "hyperliquid_perp_symbol_var"):
        self.hyperliquid_perp_symbol_var.set(coin)
    if hasattr(self, "hyperliquid_perp_side_var"):
        self.hyperliquid_perp_side_var.set(close_side)
    if hasattr(self, "hyperliquid_perp_quantity_var"):
        self.hyperliquid_perp_quantity_var.set(_format_hyperliquid_size(qty))
    if hasattr(self, "hyperliquid_perp_limit_price_var"):
        self.hyperliquid_perp_limit_price_var.set(_format_hyperliquid_size(mark))
    if hasattr(self, "hyperliquid_perp_reduce_only_var"):
        self.hyperliquid_perp_reduce_only_var.set(True)

    existing = _matching_tpsl_orders(self, coin)
    self.hyperliquid_status_var.set(f"Hyperliquid: targeting {coin} {direction.lower()} position")
    self._set_preview_text(
        "HYPERLIQUID PERP POSITION TARGET\n"
        "================================\n\n"
        f"Target position: {coin} {direction} {qty:g}\n"
        f"Entry: ${position.average_cost:,.4f}\n"
        f"Mark: ${mark:,.4f}\n"
        f"Closing side for TP/SL: {close_side}\n"
        f"Existing loaded TP/SL orders for this coin: {len(existing)}\n\n"
        "What this means\n"
        "- The perp ticket is now scoped to this position.\n"
        "- TP/SL will create new reduce-only trigger orders for this position if no matching open TP/SL orders exist.\n"
        "- Edit Position works from the synced position row and does not need an open-order ID."
    )


def _apply_hyperliquid_leverage_guarded(self: tk.Tk) -> None:
    _ensure_hyperliquid_vars(self)
    self.trade_venue_var.set("Hyperliquid")
    try:
        coin = normalize_hyperliquid_coin(self.hyperliquid_coin_var.get().strip() or self.symbol_var.get().strip())
        raw_leverage = getattr(self, "hyperliquid_leverage_var", tk.StringVar(value="1")).get().strip().lower().replace("x", "")
        leverage = int(float(raw_leverage))
        if leverage < 1:
            raise ValueError("Leverage must be at least 1x.")
        margin_mode = getattr(self, "hyperliquid_margin_mode_var", tk.StringVar(value="Cross")).get().strip() or "Cross"
        is_cross = margin_mode.lower() != "isolated"
    except Exception as exc:
        messagebox.showerror("Hyperliquid leverage blocked", str(exc))
        return

    config = HyperliquidTradingConfig()
    try:
        config.validate_for_live_action()
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: leverage blocked")
        self._set_preview_text(
            "HYPERLIQUID LEVERAGE UPDATE BLOCKED\n"
            "===================================\n\n"
            f"{exc}\n\n"
            "This action changes the exchange-side perp leverage setting for the selected coin. Required local .env gates:\n"
            "- HYPE_WALLET_ADDRESS\n"
            "- HYPE_API_ADDRESS\n"
            "- HYPE_API_SECRET\n"
            "- HYPERLIQUID_ENABLE_LIVE_ORDERS=true"
        )
        messagebox.showerror("Hyperliquid leverage blocked", str(exc))
        return

    ok = messagebox.askyesno(
        "FINAL HYPERLIQUID LEVERAGE CONFIRMATION",
        "This will update LIVE Hyperliquid perp leverage settings.\n\n"
        f"Coin: {coin}\n"
        f"Leverage: {leverage}x\n"
        f"Margin mode: {'Cross' if is_cross else 'Isolated'}\n\n"
        "Continue?",
    )
    if not ok:
        return

    try:
        result = HyperliquidExecutionAdapter().update_leverage(coin, leverage, is_cross=is_cross)
        self.hyperliquid_status_var.set(f"Hyperliquid: {coin} leverage update attempted")
        self._set_preview_text(
            "HYPERLIQUID LEVERAGE UPDATE RESULT\n"
            "==================================\n\n"
            f"Coin: {coin}\n"
            f"Leverage: {leverage}x\n"
            f"Margin mode: {'Cross' if is_cross else 'Isolated'}\n\n"
            f"Response:\n{result}\n\n"
            "No order was submitted. Use Preview Perp Ticket or Perp What-If to review the ticket with this leverage setting."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: leverage update failed")
        messagebox.showerror("Hyperliquid leverage failed", str(exc))


def _current_hyperliquid_perp_position(self: tk.Tk, coin: str) -> tuple[Any, bool]:
    portfolio = getattr(self, "current_portfolio", None)
    if portfolio is None:
        try:
            portfolio = self.broker.get_portfolio()
        except Exception:
            portfolio = None
    if portfolio is None:
        raise ValueError("Sync Hyperliquid first so the app can see the current perp position.")
    long_position = portfolio.positions.get(f"{coin}-PERP")
    short_position = portfolio.positions.get(f"{coin}-PERP-SHORT")
    if short_position is not None:
        return short_position, True
    if long_position is not None:
        return long_position, False
    raise ValueError(f"No active Hyperliquid perp position found for {coin}.")


def _perp_position_pnl(entry: float, price: float, quantity: float, is_short: bool) -> float:
    if is_short:
        return (entry - price) * quantity
    return (price - entry) * quantity


def _risk_reward(reward_net: float, stop_net: float) -> str:
    risk = abs(min(stop_net, 0.0))
    reward = max(reward_net, 0.0)
    if risk <= 0 or reward <= 0:
        return "n/a - TP must be profitable and SL must be a loss"
    return f"{reward / risk:.2f} : 1"


def _matching_tpsl_orders(self: tk.Tk, coin: str) -> list[HyperliquidOpenOrder]:
    normalized_coin = normalize_hyperliquid_coin(coin)
    orders = getattr(self, "hyperliquid_open_order_by_oid", {})
    matches: list[HyperliquidOpenOrder] = []
    for order in orders.values():
        normalized = normalize_hyperliquid_open_order(order)
        if normalize_hyperliquid_coin(normalized.coin) == normalized_coin and normalized.is_trigger and normalized.reduce_only:
            matches.append(normalized)
    return matches


def _cancel_tpsl_from_dialog(self: tk.Tk, order_id: str) -> None:
    self.cancel_order_id_var.set(order_id)
    self.cancel_hyperliquid_order_guarded()


def _lookup_hyperliquid_perp_mid(coin: str) -> float:
    normalized_coin = normalize_hyperliquid_coin(coin)
    all_mids = HyperliquidInfoClient().post_info({"type": "allMids"})
    value = all_mids.get(normalized_coin)
    if value is None:
        raise ValueError(f"No Hyperliquid perp mid-market price found for {normalized_coin}.")
    return float(value)


def _set_hyperliquid_perp_mid_price(coin: str, target_var: tk.StringVar) -> float:
    mid = _lookup_hyperliquid_perp_mid(coin)
    target_var.set(_format_hyperliquid_size(mid))
    return mid


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
        orders_table = getattr(self, "hyperliquid_workspace_open_orders_table", None)
        if orders_table is not None:
            try:
                from app.ui.options_lab_extension import _populate_workspace_open_orders_table

                _populate_workspace_open_orders_table(orders_table, snapshot.open_orders)
            except Exception:
                pass
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
        rows = [normalize_hyperliquid_open_order(order) for order in open_orders]
        header = (
            f"{'Time':<17} {'Type':<13} {'Coin':<10} {'Direction':<13} {'Size':<15} "
            f"{'Original':<10} {'Order Value':<15} {'Price':<11} {'RO':<4} {'Trigger Conditions':<22} {'TP/SL':<6} {'OID':<12}"
        )
        lines.append(header)
        lines.append("-" * len(header))
        for row in rows:
            coin = _display_order_coin(row.coin or "UNKNOWN", selected_coin)
            lines.append(
                f"{_order_time_label(row.raw):<17} "
                f"{row.order_kind:<13.13} "
                f"{coin:<10.10} "
                f"{row.direction:<13.13} "
                f"{row.size_label:<15.15} "
                f"{row.original_size_label:<10.10} "
                f"{row.order_value_label:<15.15} "
                f"{row.price_label:<11.11} "
                f"{'Yes' if row.reduce_only else '--':<4} "
                f"{row.trigger_condition:<22.22} "
                f"{row.tpsl_label:<6.6} "
                f"{row.oid:<12.12}"
            )

    lines.extend(
        [
            "Note: In Hyperliquid mode, Recent Orders and Open Only are routed to Hyperliquid active open orders.",
            "Switch Venue back to Schwab to use the Schwab recent/open order lookups.",
        ]
    )
    return "\n".join(lines)


def _order_time_label(order: dict[str, Any]) -> str:
    raw_time = order.get("timestamp")
    try:
        timestamp = int(raw_time)
    except (TypeError, ValueError):
        return "--"
    if timestamp > 10_000_000_000:
        timestamp = timestamp // 1000
    return datetime.fromtimestamp(timestamp).strftime("%m/%d %H:%M:%S")


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


def _run_hyperliquid_spot_what_if(self: tk.Tk) -> None:
    try:
        ticket = self.parse_hyperliquid_spot_ticket()
        portfolio = self.broker.get_portfolio()
        total_value = max(portfolio.total_value, 0.01)
        coin = _spot_ticket_base_coin(ticket.coin)
        exposure = _portfolio_coin_exposures(portfolio).get(
            coin,
            {
                "spot_value": 0.0,
                "spot_quantity": 0.0,
                "perp_signed": 0.0,
                "perp_notional": 0.0,
                "perp_quantity": 0.0,
            },
        )

        signed_order_value = ticket.size * ticket.limit_price * (1 if ticket.is_buy else -1)
        current_spot_value = float(exposure["spot_value"])
        current_spot_quantity = float(exposure["spot_quantity"])
        current_perp_signed = float(exposure["perp_signed"])
        current_perp_quantity = float(exposure["perp_quantity"])
        projected_spot_value = max(0.0, current_spot_value + signed_order_value)
        projected_spot_quantity = max(0.0, current_spot_quantity + (ticket.size if ticket.is_buy else -ticket.size))
        projected_net = projected_spot_value + current_perp_signed
        hedge_gap = abs(current_perp_signed) - current_spot_value if current_perp_signed < 0 else 0.0
        remaining_gap = abs(current_perp_signed) - projected_spot_value if current_perp_signed < 0 else 0.0
        stop_price = _positive_float(getattr(self, "stop_price_var", tk.StringVar(value="")).get())
        stop_lines = _spot_stop_lines(ticket, projected_spot_quantity, stop_price)

        side_word = "BUY" if ticket.is_buy else "SELL"
        perp_direction = "short" if current_perp_signed < 0 else "long" if current_perp_signed > 0 else "flat"
        self.hyperliquid_status_var.set("Hyperliquid spot: what-if ready")
        self._set_preview_text(
            "HYPERLIQUID SPOT WHAT-IF\n"
            "========================\n\n"
            "No order was submitted. This models how the spot ticket changes current spot/perp exposure.\n\n"
            "Proposed spot order:\n"
            f"- {side_word} {_format_hyperliquid_size(ticket.size)} {coin} at ${ticket.limit_price:,.4f}\n"
            f"- Order value: {_money(abs(signed_order_value))}\n"
            f"- Time in force: {ticket.tif}\n\n"
            "Current exposure:\n"
            f"- Spot: {_format_hyperliquid_size(current_spot_quantity)} {coin}, value {_money(current_spot_value)}\n"
            f"- Perp: {_format_hyperliquid_size(abs(current_perp_quantity))} {coin}, {_money(abs(current_perp_signed))} {perp_direction} notional\n"
            f"- Current net read: {_signed_money(current_spot_value + current_perp_signed)} ({abs(current_spot_value + current_perp_signed) / total_value:.1%} of portfolio)\n\n"
            "After proposed spot order:\n"
            f"- Projected spot: {_format_hyperliquid_size(projected_spot_quantity)} {coin}, value {_money(projected_spot_value)}\n"
            f"- Projected net read: {_signed_money(projected_net)} ({abs(projected_net) / total_value:.1%} of portfolio)\n"
            f"- Hedge readout: {_spot_what_if_readout(current_perp_signed, current_spot_value, projected_spot_value)}\n"
            f"{_spot_gap_text(hedge_gap, remaining_gap, ticket.limit_price)}"
            f"{stop_lines}\n"
            "Interpretation:\n"
            f"- {_spot_what_if_interpretation(ticket.is_buy, current_perp_signed, current_spot_value, projected_spot_value)}\n"
            "- This is a hedge/exposure view only; confirm live liquidity, fees, open orders, and whether the perp is intentionally directional before trading."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid spot: what-if failed")
        messagebox.showerror("Hyperliquid spot what-if failed", str(exc))


def _spot_ticket_base_coin(market: str) -> str:
    return _display_spot_base(market)


def _spot_gap_text(hedge_gap: float, remaining_gap: float, price: float) -> str:
    if hedge_gap <= 0:
        return ""
    needed_now = hedge_gap / price if price > 0 else 0.0
    needed_after = max(0.0, remaining_gap) / price if price > 0 else 0.0
    return (
        f"- Spot needed to fully offset current short perp: {_money(hedge_gap)} "
        f"({_format_hyperliquid_size(needed_now)} coin at this price)\n"
        f"- Remaining short-perp gap after ticket: {_money(max(0.0, remaining_gap))} "
        f"({_format_hyperliquid_size(needed_after)} coin at this price)\n"
    )


def _spot_stop_lines(ticket: HyperliquidOrderTicket, projected_spot_quantity: float, stop_price: float | None) -> str:
    if stop_price is None or stop_price <= 0:
        return "\n"
    if stop_price >= ticket.limit_price and ticket.is_buy:
        note = "Stop is above/equal entry for a spot buy; check that this is intentional."
    else:
        order_risk = (ticket.limit_price - stop_price) * ticket.size if ticket.is_buy else (stop_price - ticket.limit_price) * ticket.size
        projected_risk = max(0.0, ticket.limit_price - stop_price) * projected_spot_quantity
        note = f"Approx spot downside to stop on this ticket: {_money(max(0.0, order_risk))}; projected spot downside: {_money(projected_risk)}."
    return f"- Stop reference: ${stop_price:,.4f}. {note}\n\n"


def _spot_what_if_readout(perp_signed: float, current_spot: float, projected_spot: float) -> str:
    if abs(perp_signed) <= 0.01:
        return "No active perp to hedge; spot ticket is directional spot exposure."
    if perp_signed > 0:
        return "Long perp plus spot is stacked long exposure." if projected_spot > current_spot else "Spot sale reduces spot, but long perp remains directional."
    coverage = projected_spot / abs(perp_signed) if abs(perp_signed) > 0 else 0.0
    if coverage >= 1.25:
        return "Spot would more than cover the short perp."
    if coverage >= 0.75:
        return "Spot would roughly hedge the short perp."
    if coverage >= 0.25:
        return "Spot would partially hedge the short perp."
    return "Short perp remains much larger than spot."


def _spot_what_if_interpretation(is_buy: bool, perp_signed: float, current_spot: float, projected_spot: float) -> str:
    if abs(perp_signed) <= 0.01:
        return "With no matching perp, this spot ticket mainly changes outright coin exposure."
    if perp_signed < 0:
        if is_buy:
            return "Buying spot moves the account toward a hedged short-perp posture."
        return "Selling spot removes hedge against the short perp and makes net exposure more short."
    if is_buy:
        return "Buying spot adds to an already long perp posture, so directional exposure increases."
    if projected_spot < current_spot:
        return "Selling spot reduces spot exposure, but the long perp remains the main directional position."
    return "The spot ticket does not materially change the long-perp posture."


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
