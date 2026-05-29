from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timezone
from tkinter import messagebox, ttk
from typing import Any, Type

from app.analytics.crypto_market_data import CryptoCandleResult, fetch_crypto_candles, normalize_crypto_symbol, normalize_timeframe
from app.analytics.crypto_research import (
    CryptoDecisionReadout,
    CryptoExposure,
    build_crypto_decision,
    build_crypto_exposure,
    build_crypto_scenarios,
)
from app.analytics.research_scoring import direction_strength_label, risk_heat_label
from app.analytics.stock_research import AdvancedIndicatorSnapshot, DataSourceStatus, calculate_advanced_indicators
from app.ui.research_widgets import Checklist, ScenarioImpactBars, ScoreMeter, ScrollableFrame, clear_children, freshness_badges, labeled_value_grid, metric_grid


class _ScrollableDetailText:
    def __init__(self, parent: ttk.Frame) -> None:
        self.container = ttk.Frame(parent, style="Panel.TFrame")
        self.text = tk.Text(
            self.container,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            padx=16,
            pady=14,
            relief=tk.FLAT,
            borderwidth=0,
            background="#f8fafc",
            foreground="#111827",
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(self.container, orient=tk.VERTICAL, command=self.text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scroll.set)
        self.container.columnconfigure(0, weight=1)
        self.container.rowconfigure(0, weight=1)

    def grid(self, *args: Any, **kwargs: Any) -> None:
        self.container.grid(*args, **kwargs)


@dataclass(frozen=True)
class _CryptoResearchPayload:
    coin: str
    candles: CryptoCandleResult
    indicators: AdvancedIndicatorSnapshot
    exposure: CryptoExposure
    scenarios: list
    statuses: list[DataSourceStatus]
    decision: CryptoDecisionReadout


def install_hyperliquid_research_workspace_extension(app_cls: Type[tk.Tk]) -> None:
    app_cls.show_hyperliquid_crypto_research_workspace = _open_hyperliquid_research_workspace  # type: ignore[attr-defined]


def _open_hyperliquid_research_workspace(self: tk.Tk) -> None:
    existing = getattr(self, "hyperliquid_research_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                _refresh_left_tables(self)
                return
        except tk.TclError:
            pass

    window = tk.Toplevel(self)
    window.title("Hyperliquid Crypto Research + Risk Workspace")
    window.geometry("1360x840")
    window.minsize(1080, 660)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)
    self.hyperliquid_research_window = window

    self.hyperliquid_research_coin_var = tk.StringVar(value=_initial_coin(self))
    self.hyperliquid_research_status_var = tk.StringVar(value="Choose a synced crypto asset or enter a coin, then run analysis.")
    self.hyperliquid_research_timeframe_var = tk.StringVar(value="4h")

    header = ttk.Frame(window, padding=(12, 10), style="Panel.TFrame")
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(header, text="Hyperliquid Crypto Research + Risk Workspace", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(header, textvariable=self.hyperliquid_research_status_var, style="Subtle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
    ttk.Button(header, text="Refresh Account", command=lambda app=self: _refresh_hyperliquid_research(app)).grid(row=0, column=1, rowspan=2, sticky="e")

    body = tk.PanedWindow(window, orient=tk.HORIZONTAL, bg="#0f172a", bd=0, sashwidth=8, sashpad=4, showhandle=True)
    body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
    left = ttk.Frame(body, style="Panel.TFrame", padding=10)
    right = ttk.Frame(body, style="Panel.TFrame", padding=10)
    body.add(left, minsize=330, stretch="never")
    body.add(right, minsize=640, stretch="always")
    window.after_idle(lambda: body.sash_place(0, 370, 0))

    _build_left_panel(self, left)
    _build_right_panel(self, right)
    _refresh_left_tables(self)
    if self.hyperliquid_research_coin_var.get().strip():
        _run_crypto_research(self)

    def _close() -> None:
        self.hyperliquid_research_window = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _close)


def _build_left_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(2, weight=1)
    parent.rowconfigure(3, weight=1)

    summary = ttk.LabelFrame(parent, text="Synced Hyperliquid Account", style="Card.TLabelframe")
    summary.grid(row=0, column=0, sticky="ew")
    summary.columnconfigure((0, 1), weight=1)
    self.hyperliquid_research_value_var = tk.StringVar(value="Total --")
    self.hyperliquid_research_spot_var = tk.StringVar(value="Spot --")
    self.hyperliquid_research_perp_var = tk.StringVar(value="Perps --")
    self.hyperliquid_research_orders_var = tk.StringVar(value="Orders --")
    for index, var in enumerate((self.hyperliquid_research_value_var, self.hyperliquid_research_spot_var, self.hyperliquid_research_perp_var, self.hyperliquid_research_orders_var)):
        ttk.Label(summary, textvariable=var, style="Chip.TLabel").grid(row=index // 2, column=index % 2, sticky="ew", padx=(0 if index % 2 == 0 else 6, 0), pady=(0 if index < 2 else 6, 6))

    selector = ttk.LabelFrame(parent, text="Select Coin", style="Card.TLabelframe")
    selector.grid(row=1, column=0, sticky="ew", pady=(10, 10))
    selector.columnconfigure(1, weight=1)
    ttk.Label(selector, text="Coin", style="Subtle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
    ttk.Entry(selector, textvariable=self.hyperliquid_research_coin_var).grid(row=0, column=1, sticky="ew")
    ttk.Label(selector, text="Timeframe", style="Subtle.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Combobox(selector, textvariable=self.hyperliquid_research_timeframe_var, values=("15m", "1h", "4h", "1d"), state="readonly", width=8).grid(row=1, column=1, sticky="w", pady=(8, 0))
    ttk.Button(selector, text="Run Analysis", command=lambda app=self: _run_crypto_research(app), style="Accent.TButton").grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    balances = ttk.LabelFrame(parent, text="Spot + Perp Exposure", style="Card.TLabelframe")
    balances.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
    balances.columnconfigure(0, weight=1)
    balances.rowconfigure(0, weight=1)
    tree = ttk.Treeview(balances, columns=("symbol", "type", "qty", "value", "pnl"), show="headings", height=9, selectmode="browse")
    for column, label, width, anchor in (
        ("symbol", "Symbol", 120, tk.W),
        ("type", "Type", 84, tk.W),
        ("qty", "Qty", 80, tk.E),
        ("value", "Value", 94, tk.E),
        ("pnl", "P&L", 86, tk.E),
    ):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=anchor, stretch=column == "symbol")
    tree.tag_configure("positive", foreground="#047857")
    tree.tag_configure("negative", foreground="#b91c1c")
    tree.grid(row=0, column=0, sticky="nsew")
    y_scroll = ttk.Scrollbar(balances, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    tree.bind("<ButtonRelease-1>", lambda event, app=self: _select_balance_row(app, event), add="+")
    tree.bind("<Double-1>", lambda _event, app=self: _run_crypto_research(app), add="+")
    self.hyperliquid_research_balances_tree = tree

    orders = ttk.LabelFrame(parent, text="Open Orders", style="Card.TLabelframe")
    orders.grid(row=3, column=0, sticky="nsew")
    orders.columnconfigure(0, weight=1)
    orders.rowconfigure(0, weight=1)
    order_tree = ttk.Treeview(orders, columns=("oid", "coin", "type", "side", "size", "price"), show="headings", height=8, selectmode="browse")
    for column, label, width in (("oid", "OID", 92), ("coin", "Coin", 70), ("type", "Type", 92), ("side", "Side", 88), ("size", "Size", 100), ("price", "Price", 90)):
        order_tree.heading(column, text=label)
        order_tree.column(column, width=width, anchor=tk.W, stretch=column in {"oid", "size"})
    order_tree.grid(row=0, column=0, sticky="nsew")
    order_scroll = ttk.Scrollbar(orders, orient=tk.VERTICAL, command=order_tree.yview)
    order_scroll.grid(row=0, column=1, sticky="ns")
    order_tree.configure(yscrollcommand=order_scroll.set)
    order_tree.bind("<ButtonRelease-1>", lambda event, app=self: _select_order_row(app, event), add="+")
    self.hyperliquid_research_orders_tree = order_tree


def _build_right_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(3, weight=1)

    top = ttk.LabelFrame(parent, text="Selected Coin", style="Card.TLabelframe")
    top.grid(row=0, column=0, sticky="ew")
    top.columnconfigure((0, 1, 2, 3, 4), weight=1)
    self.hyperliquid_research_quote_var = tk.StringVar(value="Price --")
    self.hyperliquid_research_spot_read_var = tk.StringVar(value="Spot --")
    self.hyperliquid_research_perp_read_var = tk.StringVar(value="Perp --")
    self.hyperliquid_research_net_var = tk.StringVar(value="Net --")
    self.hyperliquid_research_funding_var = tk.StringVar(value="Funding --")
    for index, var in enumerate((self.hyperliquid_research_quote_var, self.hyperliquid_research_spot_read_var, self.hyperliquid_research_perp_read_var, self.hyperliquid_research_net_var, self.hyperliquid_research_funding_var)):
        ttk.Label(top, textvariable=var, style="Chip.TLabel").grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0))

    glance = ttk.LabelFrame(parent, text="At a Glance", style="Card.TLabelframe")
    glance.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    glance.columnconfigure(0, weight=1)
    self.hyperliquid_research_glance_cards = ttk.Frame(glance, style="Panel.TFrame")
    self.hyperliquid_research_glance_cards.grid(row=0, column=0, sticky="ew")
    self.hyperliquid_research_top_strip = ttk.Frame(glance, style="Panel.TFrame")
    self.hyperliquid_research_top_strip.grid(row=1, column=0, sticky="ew", pady=(6, 0))
    meters = ttk.Frame(glance, style="Panel.TFrame")
    meters.grid(row=2, column=0, sticky="ew", pady=(6, 0))
    meters.columnconfigure((0, 1), weight=1)
    self.hyperliquid_research_bull_meter = ScoreMeter(meters)
    self.hyperliquid_research_bull_meter.grid(row=0, column=0, sticky="ew", padx=(0, 8))
    self.hyperliquid_research_risk_meter = ScoreMeter(meters)
    self.hyperliquid_research_risk_meter.grid(row=0, column=1, sticky="ew")

    notebook = ttk.Notebook(parent)
    notebook.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
    self.hyperliquid_research_tabs = notebook
    self.hyperliquid_crypto_overview_frame = _summary_tab(notebook, "Overview")
    self.hyperliquid_crypto_technicals_frame = _technicals_tab(notebook)
    self.hyperliquid_crypto_exposure_frame = _exposure_tab(notebook)
    self.hyperliquid_crypto_scenarios_frame = _scenarios_tab(notebook)
    self.hyperliquid_crypto_orders_frame = _orders_tab(notebook)
    self.hyperliquid_crypto_news_frame = _summary_tab(notebook, "News / Sentiment")
    self.hyperliquid_crypto_sources_frame = _sources_tab(notebook)


def _scrollable_tab(notebook: ttk.Notebook, title: str) -> ttk.Frame:
    outer = ScrollableFrame(notebook, padding=10)
    frame = outer.body
    frame.columnconfigure(0, weight=1)
    frame._scrollable_outer = outer  # type: ignore[attr-defined]
    notebook.add(outer, text=title)
    return frame


def _summary_tab(notebook: ttk.Notebook, title: str) -> ttk.Frame:
    frame = _scrollable_tab(notebook, title)
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    frame.checks = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.checks.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    frame.freshness = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.freshness.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    frame.detail_text = _detail_text(frame)  # type: ignore[attr-defined]
    frame.detail_text.grid(row=3, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    return frame


def _technicals_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Technicals")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("metric", "value", "read"), show="headings", height=9)
    for column, label, width in (("metric", "Metric", 150), ("value", "Value", 130), ("read", "Read", 360)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.W if column != "value" else tk.E, stretch=True)
    tree.grid(row=0, column=0, sticky="ew")
    tree_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    tree_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=tree_scroll.set)
    frame.detail_text = _detail_text(frame)  # type: ignore[attr-defined]
    frame.detail_text.grid(row=2, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    frame.indicator_tree = tree  # type: ignore[attr-defined]
    return frame


def _exposure_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Spot / Perp Exposure")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("metric", "value", "read"), show="headings", height=9)
    for column, label, width in (("metric", "Metric", 180), ("value", "Value", 160), ("read", "Read", 420)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.W if column != "value" else tk.E, stretch=True)
    tree.grid(row=0, column=0, sticky="ew")
    tree_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    tree_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=tree_scroll.set)
    frame.detail_text = _detail_text(frame)  # type: ignore[attr-defined]
    frame.detail_text.grid(row=2, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    frame.exposure_tree = tree  # type: ignore[attr-defined]
    return frame


def _scenarios_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "What-If Scenarios")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    frame.impact_bars = ScenarioImpactBars(frame, height=148)  # type: ignore[attr-defined]
    frame.impact_bars.grid(row=1, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("scenario", "price", "spot", "perp", "net", "impact", "read"), show="headings", height=9)
    for column, label, width in (("scenario", "Scenario", 90), ("price", "Price", 120), ("spot", "Spot P&L", 120), ("perp", "Perp P&L", 120), ("net", "Net P&L", 120), ("impact", "Portfolio", 100), ("read", "After Move", 170)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.E if column not in {"scenario", "read"} else tk.W, stretch=True)
    tree.tag_configure("positive", foreground="#047857")
    tree.tag_configure("negative", foreground="#b91c1c")
    tree.grid(row=0, column=0, sticky="ew")
    tree_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    tree_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=tree_scroll.set)
    frame.scenario_tree = tree  # type: ignore[attr-defined]
    return frame


def _orders_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Funding / Orders")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("oid", "type", "coin", "direction", "size", "price", "trigger"), show="headings", height=9)
    for column, label, width in (("oid", "OID", 110), ("type", "Type", 100), ("coin", "Coin", 70), ("direction", "Direction", 110), ("size", "Size", 120), ("price", "Price", 90), ("trigger", "Trigger", 160)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.W, stretch=column in {"oid", "trigger"})
    tree.grid(row=0, column=0, sticky="ew")
    tree_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    tree_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=tree_scroll.set)
    frame.orders_tree = tree  # type: ignore[attr-defined]
    frame.detail_text = _detail_text(frame)  # type: ignore[attr-defined]
    frame.detail_text.grid(row=2, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    return frame


def _sources_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Market Data Sources")
    frame.columnconfigure(0, weight=1)
    top = ttk.Frame(frame, style="Panel.TFrame")
    top.grid(row=0, column=0, sticky="ew")
    top.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(top, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    frame.refresh_button = ttk.Button(top, text="Refresh Candles", command=lambda: None)  # type: ignore[attr-defined]
    frame.refresh_button.grid(row=0, column=1, sticky="ne", padx=(8, 0))  # type: ignore[attr-defined]
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("provider", "status", "timeframe", "fetched", "candles", "message"), show="headings", height=8)
    for column, label, width in (("provider", "Provider", 170), ("status", "Status", 100), ("timeframe", "TF", 70), ("fetched", "Last fetched", 170), ("candles", "Candles", 80), ("message", "Message", 360)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.W, stretch=column == "message")
    tree.grid(row=0, column=0, sticky="ew")
    tree_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    tree_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=tree_scroll.set)
    frame.provider_tree = tree  # type: ignore[attr-defined]
    frame.freshness = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.freshness.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    frame.detail_text = _detail_text(frame)  # type: ignore[attr-defined]
    frame.detail_text.grid(row=3, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    return frame


def _detail_text(parent: ttk.Frame) -> _ScrollableDetailText:
    return _ScrollableDetailText(parent)


def _run_crypto_research(self: tk.Tk) -> None:
    coin = normalize_crypto_symbol(self.hyperliquid_research_coin_var.get())
    if not coin:
        messagebox.showinfo("Choose coin", "Select a synced Hyperliquid asset or enter a crypto coin first.")
        return
    self.hyperliquid_research_coin_var.set(coin)
    _sync_ticket_coin_fields(self, coin)
    self.hyperliquid_research_status_var.set(f"Running crypto research for {coin}...")

    portfolio = self.broker.get_portfolio()
    orders = _normalized_orders(self)
    timeframe = _selected_timeframe(self)
    days = _days_for_timeframe(timeframe)

    def worker() -> None:
        try:
            candles = fetch_crypto_candles(coin, days=days, timeframe=timeframe)
            indicators = calculate_advanced_indicators(coin, candles.candles)
            exposure = build_crypto_exposure(portfolio, coin, orders)
            statuses = [
                DataSourceStatus(candles.source, candles.status, candles.fetched_at, candles.message),
                DataSourceStatus("Hyperliquid synced account", "fresh/cache", _now(), "Exposure loaded from current cockpit portfolio."),
                DataSourceStatus("Hyperliquid funding", "unknown", _now(), "Funding data unavailable from current sync."),
                DataSourceStatus("Crypto sentiment", "unknown", _now(), "Optional sentiment provider not configured."),
            ]
            scenarios = build_crypto_scenarios(exposure, indicators.latest_close)
            decision = build_crypto_decision(indicators=indicators, exposure=exposure, candle_result=candles, funding_rate=None, sentiment_status="unknown")
            payload = _CryptoResearchPayload(coin, candles, indicators, exposure, scenarios, statuses, decision)
        except Exception as exc:
            self.after(0, lambda error=exc: _show_crypto_error(self, coin, error))
            return
        self.after(0, lambda result=payload: _render_crypto_payload(self, result))

    threading.Thread(target=worker, daemon=True).start()


def _selected_timeframe(self: tk.Tk) -> str:
    var = getattr(self, "hyperliquid_research_timeframe_var", None)
    value = var.get() if var is not None else "4h"
    return normalize_timeframe(value)


def _days_for_timeframe(timeframe: str) -> int:
    return {"15m": 10, "1h": 30, "4h": 120, "1d": 365}.get(normalize_timeframe(timeframe), 120)


def _render_crypto_payload(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    self.hyperliquid_research_last_payload = payload
    exposure = payload.exposure
    decision = payload.decision
    price = payload.indicators.latest_close or exposure.perp_mark or (exposure.spot_value / exposure.spot_quantity if exposure.spot_quantity else None)
    self.hyperliquid_research_quote_var.set(f"{payload.coin}: {_money(price)}")
    self.hyperliquid_research_spot_read_var.set(f"Spot {_money(exposure.spot_value)}")
    self.hyperliquid_research_perp_read_var.set(f"Perp {exposure.perp_direction} {_money(exposure.perp_notional)}")
    self.hyperliquid_research_net_var.set(f"Net {_signed_money(exposure.net_exposure)}")
    self.hyperliquid_research_funding_var.set("Funding unknown")
    self.hyperliquid_research_status_var.set(f"{payload.coin} crypto research updated at {_now()}")

    metric_grid(
        self.hyperliquid_research_glance_cards,
        [
            decision.overall,
            decision.risk_level,
            decision.trend,
            decision.momentum,
            decision.volatility,
            decision.funding_bias,
            decision.exposure,
            decision.sentiment,
            decision.action_bias,
        ],
        columns=3,
        prominent_indexes={0, 8},
    )
    labeled_value_grid(
        self.hyperliquid_research_top_strip,
        {"Setup": decision.top_things[0], "Risk heat": decision.top_things[1], "Key trigger": decision.top_things[2]},
        columns=3,
    )
    self.hyperliquid_research_bull_meter.set_score(decision.technical_score, mode="direction", label=f"Bullishness: {direction_strength_label(decision.technical_score)} ({decision.technical_score:.0f})")
    self.hyperliquid_research_risk_meter.set_score(decision.risk_score, mode="risk", label=f"Risk Heat: {risk_heat_label(decision.risk_score)} ({decision.risk_score:.0f}/100)")
    _render_overview(self, payload)
    _render_technicals(self, payload)
    _render_exposure(self, payload)
    _render_scenarios(self, payload)
    _render_orders(self, payload)
    _render_news(self, payload)
    _render_sources(self, payload)


def _render_overview(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_overview_frame
    decision = payload.decision
    metric_grid(frame.cards, [decision.overall, decision.exposure, decision.risk_level, decision.action_bias], columns=4, prominent_indexes={0, 3})  # type: ignore[attr-defined]
    clear_children(frame.checks)  # type: ignore[attr-defined]
    labeled_value_grid(frame.checks, decision.operator_view, columns=4)  # type: ignore[attr-defined]
    freshness_badges(frame.freshness, payload.statuses)  # type: ignore[attr-defined]
    _set_text(frame.detail_text, "\n".join(["Plain-English summary:", *[f"- {line}" for line in decision.summary]]))  # type: ignore[attr-defined]


def _render_technicals(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_technicals_frame
    indicators = payload.indicators
    decision = payload.decision
    metric_grid(frame.cards, [decision.trend, decision.momentum, decision.volatility, _rsi_badge(indicators)], columns=4)  # type: ignore[attr-defined]
    tree = frame.indicator_tree  # type: ignore[attr-defined]
    _clear_tree(tree)
    for metric, value, read in [
        ("SMA 20", indicators.sma_20, "short trend"),
        ("SMA 50", indicators.sma_50, "intermediate trend"),
        ("SMA 100", indicators.sma_100, "intermediate/long trend"),
        ("SMA 200", indicators.sma_200, "long trend"),
        ("EMA 12", indicators.ema_12, "fast momentum"),
        ("EMA 26", indicators.ema_26, "slow momentum"),
        ("MACD", indicators.macd, "12/26 line"),
        ("MACD signal", indicators.macd_signal, "signal line"),
        ("RSI 14", indicators.rsi_14, "momentum oscillator"),
        ("ATR 14", indicators.atr_14, "volatility"),
        ("Support", indicators.support, "nearby downside level"),
        ("Resistance", indicators.resistance, "nearby upside level"),
        ("52w high", indicators.week_52_high, "range high"),
        ("52w low", indicators.week_52_low, "range low"),
    ]:
        tree.insert("", tk.END, values=(metric, _number(value), read))
    for label, value in indicators.fibonacci_levels.items():
        tree.insert("", tk.END, values=(f"Fib {label}", _money(value), "recent swing retracement"))
    _set_text(frame.detail_text, "\n".join(["Technical takeaways:", f"- Trend: {indicators.trend}.", f"- Momentum: {indicators.momentum}.", f"- Volatility: {indicators.volatility}.", *[f"- {note}" for note in indicators.notes]]))  # type: ignore[attr-defined]


def _render_exposure(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_exposure_frame
    exposure = payload.exposure
    metric_grid(frame.cards, [payload.decision.exposure, payload.decision.risk_level, payload.decision.funding_bias, payload.decision.action_bias], columns=4)  # type: ignore[attr-defined]
    tree = frame.exposure_tree  # type: ignore[attr-defined]
    _clear_tree(tree)
    rows = [
        ("Spot quantity", f"{exposure.spot_quantity:g}", "synced spot balance"),
        ("Spot value", _money(exposure.spot_value), "current spot USD value"),
        ("Spot P&L", _money(exposure.spot_pnl), "neutral if unknown basis"),
        ("Perp size", f"{exposure.perp_quantity:g}", exposure.perp_direction),
        ("Perp notional", _money(exposure.perp_notional), "absolute perp exposure"),
        ("Perp entry", _money(exposure.perp_entry), "entry from synced position"),
        ("Perp mark", _money(exposure.perp_mark), "mark from synced position"),
        ("Perp P&L", _money(exposure.perp_unrealized_pnl), "Hyperliquid unrealized P&L"),
        ("Net exposure", _signed_money(exposure.net_exposure), "spot plus signed perp notional"),
        ("Hedge ratio", "--" if exposure.hedge_ratio is None else f"{exposure.hedge_ratio:.2f}x", "perp notional / spot value"),
        ("Portfolio share", f"{exposure.portfolio_share:.2%}", "spot + perp notional versus total portfolio"),
        ("Open orders", str(exposure.open_orders), "active orders for this coin"),
    ]
    for row in rows:
        tree.insert("", tk.END, values=row)
    _set_text(frame.detail_text, "\n".join(["Exposure readout:", *[f"- {line}" for line in payload.decision.summary], "", "Operator view:", *[f"- {key}: {value}" for key, value in payload.decision.operator_view.items()]]))  # type: ignore[attr-defined]


def _render_scenarios(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_scenarios_frame
    rows = payload.scenarios
    worst = min(rows, key=lambda row: row.net_pnl, default=None)
    best = max(rows, key=lambda row: row.net_pnl, default=None)
    cards = []
    if worst:
        cards.append(_badge("Downside Pain", _money(worst.net_pnl), "bad" if worst.net_pnl < 0 else "info", f"{worst.scenario} move; {worst.portfolio_impact:+.2%} portfolio."))
    if best:
        cards.append(_badge("Upside Reward", _money(best.net_pnl), "good" if best.net_pnl > 0 else "info", f"{best.scenario} move; {best.portfolio_impact:+.2%} portfolio."))
    cards.extend([payload.decision.exposure, payload.decision.action_bias])
    metric_grid(frame.cards, cards, columns=4)  # type: ignore[attr-defined]
    max_abs = max((abs(row.portfolio_impact) for row in rows), default=0.0001)
    frame.impact_bars.set_rows([(row.scenario, (row.portfolio_impact / max_abs) * 100, _money(row.net_pnl)) for row in rows])  # type: ignore[attr-defined]
    tree = frame.scenario_tree  # type: ignore[attr-defined]
    _clear_tree(tree)
    for row in rows:
        tag = "positive" if row.net_pnl > 0 else "negative" if row.net_pnl < 0 else ""
        tree.insert("", tk.END, values=(row.scenario, _money(row.price), _money(row.spot_pnl), _money(row.perp_pnl), _money(row.net_pnl), f"{row.portfolio_impact:+.2%}", row.hedge_read), tags=(tag,) if tag else ())


def _render_orders(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_orders_frame
    metric_grid(frame.cards, [payload.decision.funding_bias, _badge("Open Orders", str(payload.exposure.open_orders), "mixed" if payload.exposure.open_orders else "good", "active orders can change risk quickly.")], columns=4)  # type: ignore[attr-defined]
    tree = frame.orders_tree  # type: ignore[attr-defined]
    _clear_tree(tree)
    matched = False
    for order in _normalized_orders(self):
        if normalize_crypto_symbol(order.coin) != payload.coin:
            continue
        matched = True
        tree.insert("", tk.END, values=(order.oid, order.order_kind, normalize_crypto_symbol(order.coin), order.direction, order.size_label, order.price_label, order.trigger_condition))
    if not matched:
        tree.insert("", tk.END, values=("--", "No open orders", payload.coin, "--", "--", "--", "No open orders for selected coin."))
    _set_text(frame.detail_text, "\n".join([
        "Funding / Orders:",
        "- Funding data is unavailable from the current sync." if payload.decision.funding_bias.label == "Unknown" else f"- Funding read: {payload.decision.funding_bias.label}.",
        "- No open orders for this selected coin." if not matched else "- Selected-coin open orders are shown above.",
        "- Use Open Orders in the trading tab to refresh active order details.",
    ]))  # type: ignore[attr-defined]


def _render_news(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_news_frame
    sentiment = _badge("Sentiment", "Not Configured", "info", "optional provider not configured")
    metric_grid(frame.cards, [sentiment], columns=4)  # type: ignore[attr-defined]
    clear_children(frame.checks)  # type: ignore[attr-defined]
    Checklist(frame.checks, "Sentiment Notes", ["INFO: optional crypto sentiment/news provider is not configured.", "SOURCE: this is not an error; candles and synced exposure still drive the read.", "WATCH: add CryptoPanic, CoinGecko, or a project-news hook later if desired."]).grid(row=0, column=0, sticky="ew")  # type: ignore[attr-defined]
    _set_text(frame.detail_text, "Sentiment/news is intentionally marked Not Configured. The workspace remains usable with price candles, spot/perp exposure, open orders, and what-if scenarios.")  # type: ignore[attr-defined]


def _render_sources(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_sources_frame
    candle_status = "good" if payload.candles.status.startswith("fresh") else "mixed" if payload.candles.status == "stale" else "bad"
    metric_grid(frame.cards, [_badge("Candles", payload.candles.status.title(), candle_status, f"{payload.candles.source}; {payload.candles.timeframe}"), _badge("Exposure", "Synced", "good", "current cockpit portfolio snapshot"), _badge("Sentiment", "Not Configured", "info", "optional provider not configured")], columns=3)  # type: ignore[attr-defined]
    tree = frame.provider_tree  # type: ignore[attr-defined]
    _clear_tree(tree)
    for row in build_crypto_provider_status_rows(payload.candles):
        tree.insert("", tk.END, values=row)
    freshness_badges(frame.freshness, payload.statuses)  # type: ignore[attr-defined]
    frame.refresh_button.configure(command=lambda app=self: _run_crypto_research(app))  # type: ignore[attr-defined]
    _set_text(frame.detail_text, "\n".join([
        "Market data sources:",
        "- Active candle provider is listed first in the table above.",
        "- Fallback order: Hyperliquid hook, Coinbase, Kraken, KuCoin hook, Binance hook, CoinGecko, optional CoinMarketCap.",
        "- Missing optional providers are shown as planned/future hooks instead of errors.",
        "",
        *[f"- {status.source}: {status.status} at {status.fetched_at}. {status.message}" for status in payload.statuses],
    ]))  # type: ignore[attr-defined]


def _refresh_hyperliquid_research(self: tk.Tk) -> None:
    sync = getattr(self, "sync_hyperliquid_account", None)
    if callable(sync):
        try:
            sync()
        except Exception as exc:
            messagebox.showerror("Hyperliquid sync failed", str(exc))
    _refresh_left_tables(self)


def build_crypto_provider_status_rows(candles: CryptoCandleResult) -> list[tuple[str, str, str, str, str, str]]:
    active = candles.source
    providers = [
        ("Hyperliquid candles", "Planned hook"),
        ("Coinbase public candles", "Fallback"),
        ("Kraken public OHLC", "Fallback"),
        ("KuCoin public candles", "Planned hook"),
        ("Binance public klines", "Planned hook"),
        ("CoinGecko market chart", "Fallback"),
        ("CoinMarketCap", "Optional API key"),
    ]
    rows: list[tuple[str, str, str, str, str, str]] = [
        (active, candles.status.title(), candles.timeframe, candles.fetched_at, str(len(candles.candles)), candles.message)
    ]
    for provider, note in providers:
        if provider == active:
            continue
        rows.append((provider, note, candles.timeframe, "--", "--", "Not used for this run."))
    return rows


def hyperliquid_cash_display_rows(cash_positions: Any) -> list[tuple[str, str, str, str, str, tuple[str, ...]]]:
    positive_usdc = 0.0
    negative_usdc = 0.0
    other_rows: list[tuple[str, str, str, str, str, tuple[str, ...]]] = []
    for cash in getattr(cash_positions, "values", lambda: [])():
        source = str(getattr(cash, "source", "")).lower()
        if "hyper" not in source:
            continue
        symbol = str(getattr(cash, "display_symbol", None) or getattr(cash, "symbol", "USDC")).upper()
        amount = float(getattr(cash, "amount", 0.0) or 0.0)
        if symbol in {"USDC", "USD"}:
            if amount >= 0:
                positive_usdc += amount
            else:
                negative_usdc += amount
            continue
        tag = ("positive",) if amount > 0 else ("negative",) if amount < 0 else ()
        other_rows.append((symbol, "Hyperliquid Cash", _number(amount), _money(amount), "--", tag))

    rows: list[tuple[str, str, str, str, str, tuple[str, ...]]] = []
    if positive_usdc:
        rows.append(("USDC", "Spot USDC", _number(positive_usdc), _money(positive_usdc), "--", ()))
    if negative_usdc:
        rows.append(("USDC", "Perp USDC / margin adj", _number(negative_usdc), _money(negative_usdc), "--", ("negative",)))
    if positive_usdc and negative_usdc:
        net = positive_usdc + negative_usdc
        tag = ("positive",) if net > 0 else ("negative",) if net < 0 else ()
        rows.append(("USDC", "Net Hyperliquid USDC", _number(net), _money(net), "--", tag))
    return rows + other_rows


def _refresh_left_tables(self: tk.Tk) -> None:
    portfolio = self.broker.get_portfolio()
    tree = getattr(self, "hyperliquid_research_balances_tree", None)
    spot_total = 0.0
    perp_total = 0.0
    if tree is not None:
        _clear_tree(tree)
        for position in sorted(getattr(portfolio, "positions", {}).values(), key=lambda p: str(getattr(p, "symbol", ""))):
            symbol = str(getattr(position, "symbol", "")).upper()
            asset_type = str(getattr(position, "asset_type", "Equity"))
            lower_type = asset_type.lower()
            if "perp" not in lower_type and lower_type != "spot":
                continue
            value = abs(float(getattr(position, "market_value", 0.0) or 0.0))
            if "perp" in lower_type:
                perp_total += value
            else:
                spot_total += value
            pnl = getattr(position, "unrealized_profit_loss", None)
            tag = "positive" if pnl is not None and pnl > 0 else "negative" if pnl is not None and pnl < 0 else ""
            tree.insert("", tk.END, values=(symbol, asset_type, _number(getattr(position, "quantity", None)), _money(value), _money(pnl)), tags=(tag,) if tag else ())
        for row in hyperliquid_cash_display_rows(getattr(portfolio, "cash_positions", {})):
            symbol, label, qty, value, pnl, tags = row
            tree.insert("", tk.END, values=(symbol, label, qty, value, pnl), tags=tags)
    orders = _normalized_orders(self)
    order_tree = getattr(self, "hyperliquid_research_orders_tree", None)
    if order_tree is not None:
        _clear_tree(order_tree)
        if orders:
            for order in orders:
                order_tree.insert("", tk.END, values=(order.oid, normalize_crypto_symbol(order.coin), order.order_kind, order.direction, order.size_label, order.price_label))
        else:
            order_tree.insert("", tk.END, values=("--", "--", "No open orders", "--", "--", "--"))
    total = float(getattr(portfolio, "total_value", 0.0) or 0.0)
    if hasattr(self, "hyperliquid_research_value_var"):
        self.hyperliquid_research_value_var.set(f"Total {_money(total)}")
        self.hyperliquid_research_spot_var.set(f"Spot {_money(spot_total)}")
        self.hyperliquid_research_perp_var.set(f"Perps {_money(perp_total)}")
        self.hyperliquid_research_orders_var.set(f"Orders {len(orders)}")


def _normalized_orders(self: tk.Tk) -> list[Any]:
    from app.ui.hyperliquid_trading_extension import normalize_hyperliquid_open_order

    raw_orders = getattr(self, "hyperliquid_open_order_by_oid", {}) or {}
    result = []
    for raw in raw_orders.values():
        try:
            result.append(normalize_hyperliquid_open_order(raw))
        except Exception:
            continue
    return result


def _select_balance_row(self: tk.Tk, _event: tk.Event) -> None:
    tree = self.hyperliquid_research_balances_tree
    selection = tree.selection()
    if not selection:
        return
    values = tree.item(selection[0], "values")
    if not values:
        return
    row_type = str(values[1]).lower() if len(values) > 1 else ""
    if "cash" in row_type or "usdc" in str(values[0]).lower():
        return
    coin = normalize_crypto_symbol(str(values[0]))
    if coin:
        self.hyperliquid_research_coin_var.set(coin)
        _sync_ticket_coin_fields(self, coin)


def _select_order_row(self: tk.Tk, _event: tk.Event) -> None:
    tree = self.hyperliquid_research_orders_tree
    selection = tree.selection()
    if not selection:
        return
    values = tree.item(selection[0], "values")
    if len(values) >= 2 and str(values[0]) != "--":
        self.hyperliquid_research_coin_var.set(normalize_crypto_symbol(str(values[1])))


def _initial_coin(self: tk.Tk) -> str:
    for attr in ("hyperliquid_spot_coin_var", "hyperliquid_perp_coin_var", "hyperliquid_coin_var", "symbol_var"):
        var = getattr(self, attr, None)
        if var is not None:
            value = normalize_crypto_symbol(var.get())
            if value:
                return value
    return ""


def _sync_ticket_coin_fields(self: tk.Tk, coin: str) -> None:
    for attr in ("hyperliquid_spot_coin_var", "hyperliquid_spot_symbol_var", "hyperliquid_perp_coin_var", "hyperliquid_perp_symbol_var", "hyperliquid_coin_var", "symbol_var"):
        var = getattr(self, attr, None)
        if var is not None:
            try:
                var.set(coin)
            except Exception:
                pass


def _show_crypto_error(self: tk.Tk, coin: str, error: Exception) -> None:
    self.hyperliquid_research_status_var.set(f"{coin} crypto research failed: {error}")
    messagebox.showerror("Hyperliquid research failed", str(error))


def _set_text(widget: tk.Text | _ScrollableDetailText, value: str) -> None:
    text = widget.text if isinstance(widget, _ScrollableDetailText) else widget
    text.configure(state=tk.NORMAL)
    text.delete("1.0", tk.END)
    text.insert(tk.END, value)
    text.configure(state=tk.DISABLED)


def _clear_tree(tree: ttk.Treeview) -> None:
    for row_id in tree.get_children():
        tree.delete(row_id)


def _badge(title: str, label: str, status: str, why: str) -> Any:
    from app.analytics.research_scoring import BadgeReadout

    return BadgeReadout(title, label, status, 0, why)


def _rsi_badge(indicators: AdvancedIndicatorSnapshot) -> Any:
    if indicators.rsi_14 is None:
        return _badge("RSI", "Unknown", "info", "RSI unavailable.")
    if indicators.rsi_14 >= 70:
        return _badge("RSI", "Overbought", "bad", f"RSI {indicators.rsi_14:.1f}.")
    if indicators.rsi_14 <= 30:
        return _badge("RSI", "Oversold", "mixed", f"RSI {indicators.rsi_14:.1f}.")
    return _badge("RSI", "Normal", "good", f"RSI {indicators.rsi_14:.1f}.")


def _money(value: Any) -> str:
    try:
        if value is None:
            return "--"
        return f"${float(value):,.2f}"
    except Exception:
        return "--"


def _signed_money(value: Any) -> str:
    try:
        amount = float(value)
        return f"+${amount:,.2f}" if amount >= 0 else f"-${abs(amount):,.2f}"
    except Exception:
        return "--"


def _number(value: Any) -> str:
    try:
        if value is None:
            return "--"
        return f"{float(value):,.6g}"
    except Exception:
        return "--"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
