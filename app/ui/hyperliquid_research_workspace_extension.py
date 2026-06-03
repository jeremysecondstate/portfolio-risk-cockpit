from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timezone
from tkinter import messagebox, ttk
from typing import Any, Type

from app.analytics.crypto_sentiment import CryptoSentimentSnapshot, build_crypto_sentiment_snapshot
from app.analytics.crypto_token_health import TokenHealthSnapshot, fetch_token_health_snapshot
from app.analytics.crypto_market_data import CryptoCandleResult, fetch_crypto_candles, normalize_crypto_symbol, normalize_timeframe
from app.analytics.crypto_research import (
    CryptoDecisionReadout,
    CryptoExposure,
    build_crypto_decision,
    build_crypto_exposure,
    build_crypto_scenarios,
)
from app.analytics.hyperliquid_market_data import (
    DEFAULT_MATRIX_TIMEFRAMES,
    HYPERLIQUID_INTERVALS,
    LiquiditySnapshot,
    MarketDataSourceStatus,
    MultiTimeframeCryptoSnapshot,
    PerpStructureSnapshot,
    fetch_liquidity_snapshot,
    fetch_multi_timeframe_crypto_snapshot,
    fetch_perp_structure_snapshot,
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
    source_statuses: list[MarketDataSourceStatus]
    decision: CryptoDecisionReadout
    multi_timeframe: MultiTimeframeCryptoSnapshot
    liquidity: LiquiditySnapshot
    perp_structure: PerpStructureSnapshot
    sentiment: CryptoSentimentSnapshot
    token_health: TokenHealthSnapshot


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
    window.title("Hyperliquid Market + Position Intelligence")
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
    ttk.Label(header, text="Hyperliquid Market + Position Intelligence", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
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
    ttk.Combobox(selector, textvariable=self.hyperliquid_research_timeframe_var, values=HYPERLIQUID_INTERVALS, state="readonly", width=8).grid(row=1, column=1, sticky="w", pady=(8, 0))
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
    self.hyperliquid_crypto_timeframe_frame = _timeframe_matrix_tab(notebook)
    self.hyperliquid_crypto_liquidity_frame = _liquidity_tab(notebook)
    self.hyperliquid_crypto_perp_frame = _perp_structure_tab(notebook)
    self.hyperliquid_crypto_exposure_frame = _exposure_tab(notebook)
    self.hyperliquid_crypto_scenarios_frame = _scenarios_tab(notebook)
    self.hyperliquid_crypto_news_frame = _summary_tab(notebook, "Sentiment / News")
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


def _timeframe_matrix_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Timeframe Matrix")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    columns = ("timeframe", "group", "trend", "rsi", "macd", "atr", "volume", "support", "resistance", "read")
    tree = ttk.Treeview(tree_box, columns=columns, show="headings", height=12)
    headings = (
        ("timeframe", "TF", 70, tk.W),
        ("group", "Group", 95, tk.W),
        ("trend", "Trend", 90, tk.W),
        ("rsi", "RSI", 80, tk.E),
        ("macd", "MACD", 90, tk.W),
        ("atr", "ATR%", 80, tk.E),
        ("volume", "Volume", 100, tk.W),
        ("support", "Support", 100, tk.E),
        ("resistance", "Resistance", 110, tk.E),
        ("read", "Read", 230, tk.W),
    )
    for column, label, width, anchor in headings:
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=anchor, stretch=column == "read")
    tree.tag_configure("good", foreground="#047857")
    tree.tag_configure("bad", foreground="#b91c1c")
    tree.tag_configure("mixed", foreground="#92400e")
    tree.grid(row=0, column=0, sticky="ew")
    tree_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    tree_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=tree_scroll.set)
    frame.matrix_tree = tree  # type: ignore[attr-defined]
    frame.detail_text = _detail_text(frame)  # type: ignore[attr-defined]
    frame.detail_text.grid(row=2, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    return frame


def _liquidity_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Liquidity / Order Book")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    depth_box = ttk.Frame(frame, style="Panel.TFrame")
    depth_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    depth_box.columnconfigure(0, weight=1)
    depth_tree = ttk.Treeview(depth_box, columns=("bucket", "bid", "ask", "imbalance"), show="headings", height=5)
    for column, label, width, anchor in (("bucket", "Depth", 120, tk.W), ("bid", "Bid Depth", 140, tk.E), ("ask", "Ask Depth", 140, tk.E), ("imbalance", "Imbalance", 140, tk.E)):
        depth_tree.heading(column, text=label)
        depth_tree.column(column, width=width, anchor=anchor, stretch=True)
    depth_tree.grid(row=0, column=0, sticky="ew")
    frame.depth_tree = depth_tree  # type: ignore[attr-defined]

    slippage_box = ttk.Frame(frame, style="Panel.TFrame")
    slippage_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    slippage_box.columnconfigure(0, weight=1)
    slippage_tree = ttk.Treeview(slippage_box, columns=("size", "buy", "sell", "read"), show="headings", height=5)
    for column, label, width, anchor in (("size", "Order Size", 120, tk.E), ("buy", "Buy Slip", 120, tk.E), ("sell", "Sell Slip", 120, tk.E), ("read", "Read", 260, tk.W)):
        slippage_tree.heading(column, text=label)
        slippage_tree.column(column, width=width, anchor=anchor, stretch=column == "read")
    slippage_tree.grid(row=0, column=0, sticky="ew")
    frame.slippage_tree = slippage_tree  # type: ignore[attr-defined]
    frame.detail_text = _detail_text(frame)  # type: ignore[attr-defined]
    frame.detail_text.grid(row=3, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    return frame


def _perp_structure_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Perp Structure")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("metric", "value", "read"), show="headings", height=10)
    for column, label, width, anchor in (("metric", "Metric", 190, tk.W), ("value", "Value", 170, tk.E), ("read", "Read", 420, tk.W)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=anchor, stretch=column == "read")
    tree.grid(row=0, column=0, sticky="ew")
    tree_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    tree_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=tree_scroll.set)
    frame.perp_tree = tree  # type: ignore[attr-defined]
    frame.detail_text = _detail_text(frame)  # type: ignore[attr-defined]
    frame.detail_text.grid(row=2, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
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
            enrichment_timeout = 4
            matrix_timeframes = tuple(dict.fromkeys((*DEFAULT_MATRIX_TIMEFRAMES, timeframe)))
            multi_timeframe = fetch_multi_timeframe_crypto_snapshot(coin, timeframes=matrix_timeframes, timeout_seconds=enrichment_timeout)
            liquidity = fetch_liquidity_snapshot(
                coin,
                exposure_notional=abs(exposure.net_exposure) or exposure.perp_notional or exposure.spot_value,
                order_sizes_usd=_liquidity_order_sizes(exposure),
                timeout_seconds=enrichment_timeout,
            )
            perp_structure = fetch_perp_structure_snapshot(
                coin,
                perp_notional=exposure.perp_notional,
                perp_direction=exposure.perp_direction,
                timeout_seconds=enrichment_timeout,
            )
            sentiment = build_crypto_sentiment_snapshot(coin)
            token_health = fetch_token_health_snapshot(coin, timeout_seconds=enrichment_timeout)
            source_statuses = [
                MarketDataSourceStatus("Crypto fallback chain", candles.source, candles.status, candles.fetched_at, candles.timeframe, len(candles.candles), candles.message),
                *multi_timeframe.source_statuses,
            ]
            if liquidity.source_status is not None:
                source_statuses.append(liquidity.source_status)
            source_statuses.extend(perp_structure.source_statuses)
            source_statuses.extend(token_health.source_statuses)
            source_statuses.append(MarketDataSourceStatus(sentiment.provider, "sentiment", sentiment.status, sentiment.fetched_at, "", sentiment.headline_count, sentiment.message))
            statuses = [
                DataSourceStatus(candles.source, candles.status, candles.fetched_at, candles.message),
                DataSourceStatus("Hyperliquid synced account", "fresh/cache", _now(), "Exposure loaded from current cockpit portfolio."),
                *_data_statuses_from_market_statuses(source_statuses[:4]),
                DataSourceStatus("Crypto sentiment", sentiment.status, sentiment.fetched_at, sentiment.message),
            ]
            scenarios = build_crypto_scenarios(exposure, indicators.latest_close)
            decision = build_crypto_decision(
                indicators=indicators,
                exposure=exposure,
                candle_result=candles,
                sentiment_status=sentiment.status,
                multi_timeframe=multi_timeframe,
                liquidity=liquidity,
                perp_structure=perp_structure,
                sentiment=sentiment,
            )
            payload = _CryptoResearchPayload(
                coin,
                candles,
                indicators,
                exposure,
                scenarios,
                statuses,
                source_statuses,
                decision,
                multi_timeframe,
                liquidity,
                perp_structure,
                sentiment,
                token_health,
            )
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
    return {
        "1m": 4,
        "3m": 7,
        "5m": 10,
        "15m": 21,
        "30m": 45,
        "1h": 90,
        "2h": 120,
        "4h": 180,
        "8h": 365,
        "12h": 365,
        "1d": 730,
        "3d": 1_200,
        "1w": 2_000,
        "1M": 3_000,
    }.get(normalize_timeframe(timeframe), 180)


def _render_crypto_payload(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    self.hyperliquid_research_last_payload = payload
    exposure = payload.exposure
    decision = payload.decision
    price = payload.indicators.latest_close or exposure.perp_mark or (exposure.spot_value / exposure.spot_quantity if exposure.spot_quantity else None)
    self.hyperliquid_research_quote_var.set(f"{payload.coin}: {_money(price)}")
    self.hyperliquid_research_spot_read_var.set(f"Spot {_money(exposure.spot_value)}")
    self.hyperliquid_research_perp_read_var.set(f"Perp {exposure.perp_direction} {_money(exposure.perp_notional)}")
    self.hyperliquid_research_net_var.set(f"Net {_signed_money(exposure.net_exposure)}")
    self.hyperliquid_research_funding_var.set(f"Perp carry {decision.funding_bias.label}")
    self.hyperliquid_research_status_var.set(f"{payload.coin} crypto research updated at {_now()}")

    metric_grid(
        self.hyperliquid_research_glance_cards,
        [
            decision.overall,
            decision.risk_level,
            decision.trend_alignment,
            decision.liquidity,
            decision.funding_bias,
            decision.exposure,
            decision.sentiment,
            decision.action_bias,
        ],
        columns=4,
        prominent_indexes={0, 7},
    )
    labeled_value_grid(
        self.hyperliquid_research_top_strip,
        {"Setup": decision.top_things[0], "Risk / liquidity": decision.top_things[1], "Invalidation": (decision.invalidations[0] if decision.invalidations else decision.top_things[2])},
        columns=3,
    )
    self.hyperliquid_research_bull_meter.set_score(decision.composite_score, mode="direction", label=f"Market Score: {direction_strength_label(decision.composite_score)} ({decision.composite_score:.0f})")
    self.hyperliquid_research_risk_meter.set_score(decision.risk_score, mode="risk", label=f"Risk Heat: {risk_heat_label(decision.risk_score)} ({decision.risk_score:.0f}/100)")
    _render_overview(self, payload)
    _render_timeframe_matrix(self, payload)
    _render_liquidity(self, payload)
    _render_perp_structure(self, payload)
    _render_exposure(self, payload)
    _render_scenarios(self, payload)
    _render_news(self, payload)
    _render_sources(self, payload)


def _render_overview(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_overview_frame
    decision = payload.decision
    metric_grid(frame.cards, [decision.overall, decision.risk_level, decision.trend_alignment, decision.liquidity, decision.funding_bias, decision.exposure, decision.sentiment, decision.action_bias], columns=4, prominent_indexes={0, 7})  # type: ignore[attr-defined]
    clear_children(frame.checks)  # type: ignore[attr-defined]
    labeled_value_grid(frame.checks, decision.operator_view, columns=4)  # type: ignore[attr-defined]
    freshness_badges(frame.freshness, payload.statuses)  # type: ignore[attr-defined]
    _set_text(
        frame.detail_text,
        "\n".join(
            [
                "Plain-English summary:",
                *[f"- {line}" for line in decision.summary],
                "",
                "Why:",
                *[f"- {line}" for line in decision.why_bullets],
                "",
                "Invalidations:",
                *[f"- {line}" for line in decision.invalidations],
                "",
                "Composite score:",
                *[f"- {name}: {value:.0f}" for name, value in decision.score_components.items()],
            ]
        ),
    )  # type: ignore[attr-defined]


def _render_timeframe_matrix(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_timeframe_frame
    alignment = payload.multi_timeframe.alignment
    group_cards = [
        _badge("Short-Term", alignment.group_reads.get("short-term", "Unavailable"), _group_status(alignment.group_reads.get("short-term", "")), "1m / 5m / 15m"),
        _badge("Intraday", alignment.group_reads.get("intraday", "Unavailable"), _group_status(alignment.group_reads.get("intraday", "")), "30m / 1h / 4h"),
        _badge("Swing", alignment.group_reads.get("swing", "Unavailable"), _group_status(alignment.group_reads.get("swing", "")), "8h / 12h / 1d"),
        _badge("Macro", alignment.group_reads.get("macro", "Unavailable"), _group_status(alignment.group_reads.get("macro", "")), "3d / 1w / 1M"),
    ]
    metric_grid(frame.cards, [payload.decision.trend_alignment, *group_cards], columns=5, prominent_indexes={0})  # type: ignore[attr-defined]
    tree = frame.matrix_tree  # type: ignore[attr-defined]
    _clear_tree(tree)
    for read in payload.multi_timeframe.timeframe_reads:
        tag = "good" if read.score >= 35 else "bad" if read.score <= -35 else "mixed" if read.candle_count else ""
        tree.insert(
            "",
            tk.END,
            values=(
                read.timeframe,
                read.group,
                read.trend,
                _number(read.rsi),
                read.macd,
                "--" if read.atr_percent is None else f"{read.atr_percent:.2f}%",
                read.volume_regime,
                _money(read.support),
                _money(read.resistance),
                read.read,
            ),
            tags=(tag,) if tag else (),
        )
    _set_text(
        frame.detail_text,
        "\n".join(
            [
                "Timeframe matrix:",
                f"- Alignment: {alignment.label}. {alignment.why}",
                f"- Trend score: {alignment.trend_score:.0f}. Momentum agreement: {alignment.momentum_score:.0f}.",
                f"- Volatility regime score: {alignment.volatility_score:.0f}. Volume expansion score: {alignment.volume_score:.0f}.",
                f"- Breakout/breakdown confirmation: {alignment.breakout_score:.0f}.",
            ]
        ),
    )  # type: ignore[attr-defined]


def _render_liquidity(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_liquidity_frame
    liquidity = payload.liquidity
    spread = _badge("Spread", _bps(liquidity.spread_bps), _spread_status(liquidity.spread_bps), "best bid/ask spread")
    top_depth = _badge("Top Depth", f"{_money(liquidity.top_bid_depth_usd)} / {_money(liquidity.top_ask_depth_usd)}", "mixed", "top bid / top ask notional")
    imbalance = _badge("Book Imbalance", _signed_percent(liquidity.imbalance), _imbalance_status(liquidity.imbalance), "positive means bid-heavy visible book")
    metric_grid(frame.cards, [payload.decision.liquidity, spread, top_depth, imbalance], columns=4, prominent_indexes={0})  # type: ignore[attr-defined]
    depth_tree = frame.depth_tree  # type: ignore[attr-defined]
    _clear_tree(depth_tree)
    for bucket in liquidity.depth_buckets:
        depth_tree.insert("", tk.END, values=(f"Within {bucket.bps} bps", _money(bucket.bid_depth_usd), _money(bucket.ask_depth_usd), _signed_percent(_depth_imbalance(bucket.bid_depth_usd, bucket.ask_depth_usd))))
    slippage_tree = frame.slippage_tree  # type: ignore[attr-defined]
    _clear_tree(slippage_tree)
    for estimate in liquidity.slippage:
        slippage_tree.insert("", tk.END, values=(_money(estimate.order_size_usd), _bps(estimate.buy_slippage_bps), _bps(estimate.sell_slippage_bps), estimate.read))
    _set_text(
        frame.detail_text,
        "\n".join(
            [
                "Liquidity health:",
                f"- {liquidity.health}: {liquidity.reason}",
                f"- Mid: {_money(liquidity.mid_price)}; best bid {_money(liquidity.best_bid)} / best ask {_money(liquidity.best_ask)}.",
                *[f"- WARNING: {warning}" for warning in liquidity.warnings],
            ]
        ),
    )  # type: ignore[attr-defined]


def _render_perp_structure(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_perp_frame
    perp = payload.perp_structure
    token = payload.token_health
    cards = [
        payload.decision.funding_bias,
        _badge("Open Interest Cap", perp.oi_cap_status, "bad" if perp.oi_cap_status == "At/near cap" else "good" if perp.is_perp_enabled else "info", "Hyperliquid cap flag"),
        _badge("Premium", _bps(perp.premium_bps), _premium_status(perp.premium_bps), "mark/oracle premium"),
        _badge("Token Health", token.label, "good" if token.status == "fresh" else "info", token.message),
    ]
    metric_grid(frame.cards, cards, columns=4, prominent_indexes={0})  # type: ignore[attr-defined]
    tree = frame.perp_tree  # type: ignore[attr-defined]
    _clear_tree(tree)
    rows = [
        ("Perp enabled", "Yes" if perp.is_perp_enabled else "No", perp.status),
        ("Mark price", _money(perp.mark_price), "Hyperliquid mark"),
        ("Oracle price", _money(perp.oracle_price), "oracle reference"),
        ("Mid price", _money(perp.mid_price), "mid/reference price"),
        ("Premium", _bps(perp.premium_bps), "mark versus oracle"),
        ("Current funding", _rate(perp.current_funding), "perp carry per funding interval"),
        ("Predicted funding", _rate(perp.predicted_funding), "predicted carry when available"),
        ("Funding trend", perp.historical_funding_trend, "last 7d funding history"),
        ("Open interest", _number(perp.open_interest), "contracts / native size if provided"),
        ("Day notional volume", _money(perp.day_notional_volume), "24h notional volume"),
        ("Max leverage", "--" if perp.max_leverage is None else f"{perp.max_leverage:g}x", "metadata max leverage"),
        ("Carry cost 8h", _signed_money(perp.carry_cost_8h), "estimated cost/credit for synced perp"),
        ("Carry cost daily", _signed_money(perp.carry_cost_daily), "estimated 3x funding intervals"),
    ]
    for row in rows:
        tree.insert("", tk.END, values=row)
    for label, value in token.metrics.items():
        tree.insert("", tk.END, values=(f"Token: {label}", value, "Hyperliquid spot metadata"))
    _set_text(
        frame.detail_text,
        "\n".join(
            [
                "Perp structure:",
                f"- {perp.carry_read}",
                f"- OI cap: {perp.oi_cap_status}.",
                f"- Token health: {token.message}",
            ]
        ),
    )  # type: ignore[attr-defined]


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


def _render_news(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_news_frame
    sentiment = payload.sentiment
    metric_grid(
        frame.cards,
        [
            payload.decision.sentiment,
            _badge("Headlines", str(sentiment.headline_count), "good" if sentiment.headline_count else "info", sentiment.provider),
            _badge("Provider", sentiment.provider_status.title(), _source_status_style(sentiment.provider_status), sentiment.message),
            _badge("Freshness", sentiment.source_freshness.title(), _source_status_style(sentiment.source_freshness), sentiment.fetched_at),
        ],
        columns=4,
    )  # type: ignore[attr-defined]
    clear_children(frame.checks)  # type: ignore[attr-defined]
    Checklist(frame.checks, "Narrative Notes", sentiment.narratives).grid(row=0, column=0, sticky="ew")  # type: ignore[attr-defined]
    _set_text(
        frame.detail_text,
        "\n".join(
            [
                "Sentiment / news:",
                f"- Label: {sentiment.label}. Score {sentiment.score:.0f}.",
                f"- Provider: {sentiment.provider}; status {sentiment.provider_status}.",
                f"- {sentiment.message}",
                "- Optional paid providers are not required; absent providers are reported as Not Configured.",
            ]
        ),
    )  # type: ignore[attr-defined]


def _render_sources(self: tk.Tk, payload: _CryptoResearchPayload) -> None:
    frame = self.hyperliquid_crypto_sources_frame
    candle_status = "good" if payload.candles.status.startswith("fresh") else "mixed" if payload.candles.status == "stale" else "bad"
    metric_grid(
        frame.cards,
        [
            _badge("Candles", payload.candles.status.title(), candle_status, f"{payload.candles.source}; {payload.candles.timeframe}"),
            _badge("Timeframes", payload.multi_timeframe.alignment.label, payload.multi_timeframe.alignment.status, payload.multi_timeframe.alignment.why),
            payload.decision.liquidity,
            payload.decision.sentiment,
        ],
        columns=4,
    )  # type: ignore[attr-defined]
    tree = frame.provider_tree  # type: ignore[attr-defined]
    _clear_tree(tree)
    for row in build_crypto_provider_status_rows(payload.candles, payload.source_statuses):
        tree.insert("", tk.END, values=row)
    freshness_badges(frame.freshness, payload.statuses)  # type: ignore[attr-defined]
    frame.refresh_button.configure(command=lambda app=self: _run_crypto_research(app))  # type: ignore[attr-defined]
    _set_text(frame.detail_text, "\n".join([
        "Market data sources:",
        "- Active candle provider is listed first in the table above.",
        "- Fallback order: Hyperliquid candleSnapshot, Coinbase, Kraken, CoinGecko.",
        "- Optional sentiment/token providers degrade to Not Configured or Unavailable instead of Unknown.",
        "",
        *[f"- {status.provider} {status.endpoint}: {status.status} at {status.fetched_at}. {status.message}" for status in payload.source_statuses],
    ]))  # type: ignore[attr-defined]


def _refresh_hyperliquid_research(self: tk.Tk) -> None:
    sync = getattr(self, "sync_hyperliquid_account", None)
    if callable(sync):
        try:
            sync()
        except Exception as exc:
            messagebox.showerror("Hyperliquid sync failed", str(exc))
    _refresh_left_tables(self)


def build_crypto_provider_status_rows(candles: CryptoCandleResult, extra_statuses: list[MarketDataSourceStatus] | None = None) -> list[tuple[str, str, str, str, str, str]]:
    active = candles.source
    providers = [
        ("Hyperliquid candleSnapshot", "Primary"),
        ("Coinbase public candles", "Fallback"),
        ("Kraken public OHLC", "Fallback"),
        ("CoinGecko market chart", "Fallback"),
        ("Hyperliquid l2Book", "Liquidity"),
        ("Hyperliquid metaAndAssetCtxs", "Perp structure"),
        ("Local keyword scanner", "Optional sentiment"),
    ]
    rows: list[tuple[str, str, str, str, str, str]] = [
        (active, candles.status.title(), candles.timeframe, candles.fetched_at, str(len(candles.candles)), candles.message)
    ]
    for status in extra_statuses or []:
        provider = f"{status.provider} {status.endpoint}".strip()
        if provider == active:
            continue
        rows.append(
            (
                provider,
                status.status.title(),
                status.timeframe or "--",
                status.fetched_at,
                "--" if status.candle_count is None else str(status.candle_count),
                status.message,
            )
        )
    for provider, note in providers:
        if provider == active or any(row[0] == provider for row in rows):
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


def _liquidity_order_sizes(exposure: CryptoExposure) -> tuple[float, ...]:
    base = [1_000.0, 5_000.0, 25_000.0]
    exposure_size = abs(exposure.net_exposure) or exposure.perp_notional or exposure.spot_value
    if exposure_size > 0:
        base.append(max(1_000.0, exposure_size))
    return tuple(dict.fromkeys(base))


def _data_statuses_from_market_statuses(statuses: list[MarketDataSourceStatus]) -> list[DataSourceStatus]:
    return [DataSourceStatus(f"{status.provider} {status.endpoint}".strip(), status.status, status.fetched_at, status.message) for status in statuses]


def _group_status(label: str) -> str:
    text = label.lower()
    if "bullish" in text:
        return "good"
    if "bearish" in text:
        return "bad"
    if "unavailable" in text:
        return "info"
    return "mixed"


def _source_status_style(status: str) -> str:
    text = status.lower()
    if text in {"fresh", "fresh/cache", "configured"}:
        return "good"
    if text in {"stale", "cached", "mixed"}:
        return "mixed"
    if text in {"error"}:
        return "bad"
    return "info"


def _spread_status(spread_bps: float | None) -> str:
    if spread_bps is None:
        return "info"
    if spread_bps <= 8:
        return "good"
    if spread_bps <= 35:
        return "mixed"
    return "bad"


def _premium_status(premium_bps: float | None) -> str:
    if premium_bps is None:
        return "info"
    if abs(premium_bps) <= 5:
        return "good"
    if abs(premium_bps) <= 25:
        return "mixed"
    return "bad"


def _imbalance_status(imbalance: float | None) -> str:
    if imbalance is None:
        return "info"
    if abs(imbalance) <= 0.20:
        return "good"
    if abs(imbalance) <= 0.45:
        return "mixed"
    return "bad"


def _depth_imbalance(bid_depth: float, ask_depth: float) -> float | None:
    total = bid_depth + ask_depth
    if total <= 0:
        return None
    return (bid_depth - ask_depth) / total


def _bps(value: Any) -> str:
    try:
        if value is None:
            return "--"
        return f"{float(value):,.1f} bps"
    except Exception:
        return "--"


def _rate(value: Any) -> str:
    try:
        if value is None:
            return "--"
        return f"{float(value):+.4%}"
    except Exception:
        return "--"


def _signed_percent(value: Any) -> str:
    try:
        if value is None:
            return "--"
        return f"{float(value):+.1%}"
    except Exception:
        return "--"


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
