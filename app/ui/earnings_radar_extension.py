from __future__ import annotations

import os
import math
import threading
import webbrowser
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Type

import tkinter as tk
from tkinter import messagebox, ttk

from app.analytics.earnings_ai import (
    EARNINGS_AI_ANALYZE_PROMPT,
    EARNINGS_AI_QUICK_PROMPTS,
    EarningsAiResponse,
    OpenAiEarningsRadarClient,
)
from app.analytics.earnings_pipeline import (
    EARNINGS_FORMS,
    EMPTY_VALUE,
    EarningsRadarSnapshot,
    EarningsRadarStore,
    FORMAL_REPORT_KIND,
    RecentEarningsRecord,
    display_money,
    display_optional_text,
    display_percent,
    fetch_recent_earnings_snapshot,
    filter_recent_earnings_records,
    filter_upcoming_earnings_records,
)
from app.analytics.market_screener import (
    DATA_COMPLETENESS_OPTIONS,
    EARNINGS_WINDOW_OPTIONS,
    EVENT_TYPE_OPTIONS,
    AskScreenerExecutionResult,
    AskScreenerEnrichmentDecision,
    AskScreenerPlan,
    AskScreenerPlannerError,
    ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT,
    ASK_SCREENER_PROVIDER_ACTION_ENRICH_THEN_EXECUTE,
    ASK_SCREENER_PROVIDER_ACTION_MISSING_CONFIG,
    MARKET_SCREENER_AI_ANALYZE_PROMPT,
    MARKET_SCREENER_AI_QUICK_PROMPTS,
    MARKET_SCREENER_ENRICH_SELECTED_ROW,
    MARKET_SCREENER_ENRICH_VISIBLE_PAGE,
    MarketScreenerAiResponse,
    MarketScreenerEnrichmentResult,
    MarketScreenerRecord,
    MarketScreenerSnapshot,
    OpenAiAskScreenerPlannerClient,
    OpenAiMarketScreenerClient,
    ask_screener_config_from_env,
    ask_screener_enrichment_decision,
    execute_ask_screener_plan,
    execute_provider_aware_ask_screener_plan,
    enrich_market_screener_records,
    fetch_market_screener_snapshot,
    filter_market_screener_records,
    market_screener_data_completeness,
    market_screener_data_completeness_label,
    market_screener_data_label,
    market_screener_diagnostics_detail_lines,
    market_screener_diagnostics_summary,
    market_screener_backfill_config_from_env,
    market_screener_is_my_holding,
    market_screener_record_missing_reason_lines,
    market_screener_record_has_fundamentals,
    market_screener_has_ai_signal,
    market_screener_major_cap_diagnostic_lines,
    market_screener_market_cap_rank,
    merge_market_data_records_into_screener_records,
    parse_ask_screener_fallback,
    sort_market_screener_records,
)
from app.analytics.symbol_chat import redact_symbol_chat_secrets
from app.data.earnings_calendar import ALPHA_VANTAGE_HORIZONS, UpcomingEarningsRecord, configured_upcoming_earnings_provider
from app.data.market_data_provider import MarketQuoteFundamentalsSnapshot, configured_market_data_provider, configured_market_data_symbol_limit
from app.data.sec_edgar import SecEdgarClient
from app.ui import polished_theme
from app.ui.symbol_chat_window import open_symbol_chat_window


RECENT_COLUMNS = (
    ("company", "Company", 220, tk.W),
    ("ticker", "Ticker", 78, tk.W),
    ("cik", "CIK", 92, tk.W),
    ("form", "Form", 74, tk.W),
    ("kind", "Type", 120, tk.W),
    ("item", "Item", 170, tk.W),
    ("filed_date", "Filed date", 96, tk.W),
    ("acceptance_time", "Acceptance time", 140, tk.W),
    ("fiscal_period", "Fiscal period", 150, tk.W),
    ("sector", "Sector / SIC", 150, tk.W),
    ("industry", "Industry", 210, tk.W),
    ("exchange", "Exchange", 120, tk.W),
    ("revenue", "Revenue", 116, tk.E),
    ("revenue_growth", "Revenue growth", 124, tk.E),
    ("eps", "EPS", 90, tk.E),
    ("net_income", "Net income", 116, tk.E),
    ("guidance", "Guidance", 92, tk.W),
    ("risk_flags", "Risk flags", 240, tk.W),
    ("filing_link", "Filing link", 360, tk.W),
    ("exhibit_link", "Exhibit link", 360, tk.W),
)
UPCOMING_COLUMNS = (
    ("symbol", "Symbol", 90, tk.W),
    ("company", "Company", 240, tk.W),
    ("report_date", "Report date", 110, tk.W),
    ("fiscal_date", "Fiscal date ending", 150, tk.W),
    ("estimate", "Estimate", 110, tk.E),
    ("currency", "Currency", 84, tk.W),
    ("source", "Source", 130, tk.W),
    ("source_link", "Source link", 420, tk.W),
)
SCREENER_COLUMNS = (
    ("symbol", "Symbol", 86, tk.W),
    ("data_status", "Data", 150, tk.W),
    ("data_completeness", "Complete", 98, tk.E),
    ("company", "Company", 230, tk.W),
    ("exchange", "Exchange", 110, tk.W),
    ("sector", "Sector", 150, tk.W),
    ("industry", "Industry", 190, tk.W),
    ("price", "Price", 92, tk.E),
    ("change_percent", "Change %", 96, tk.E),
    ("volume", "Volume", 110, tk.E),
    ("avg_volume", "Avg vol", 110, tk.E),
    ("market_cap", "Market cap", 120, tk.E),
    ("pe_ratio", "P/E", 82, tk.E),
    ("eps", "EPS", 82, tk.E),
    ("revenue_growth", "Rev growth", 104, tk.E),
    ("float_shares", "Float/Shares", 124, tk.E),
    ("next_earnings", "Next earnings", 112, tk.W),
    ("recent_filing", "Recent filing", 112, tk.W),
    ("recent_type", "Recent type", 160, tk.W),
    ("signals", "Signals", 260, tk.W),
    ("risk_flags", "Risk flags", 220, tk.W),
    ("sources", "Sources", 190, tk.W),
)
RECENT_NUMERIC_COLUMNS = {"revenue", "revenue_growth", "eps", "net_income"}
UPCOMING_NUMERIC_COLUMNS = {"estimate"}
SCREENER_NUMERIC_COLUMNS = {"price", "change_percent", "volume", "avg_volume", "market_cap", "pe_ratio", "eps", "revenue_growth", "float_shares", "data_completeness", "signals", "risk_flags", "sources"}
DEFAULT_MARKET_SCREENER_SORT_COLUMN = "market_cap"
DEFAULT_MARKET_SCREENER_SORT_DESC = True
MARKET_DATA_PAGE_ENRICHMENT_CAP = 100
MARKET_SCREENER_TABLE_STYLE = "MarketScreener.Treeview"
MARKET_SCREENER_TABLE_HEIGHT = 18
MARKET_SCREENER_TABLE_ROWHEIGHT = 38
MARKET_SCREENER_TEXT_FONT = ("Segoe UI", 11)
MARKET_SCREENER_SOURCE_FONT = ("Segoe UI", 10)
EARNINGS_RADAR_CHART_HEIGHT = 150
MARKET_SCREENER_SUMMARY_STRIP_HEIGHT = 68
MARKET_SCREENER_SUMMARY_MAX_ITEMS = 5
STATUS_CHIP_DISPLAY_LIMIT = 96
STATUS_CHIP_WIDTH = 96


def install_earnings_radar_extension(app_cls: Type[tk.Tk]) -> None:
    previous_build_layout = app_cls._build_layout

    def _build_layout_with_earnings_radar(self: tk.Tk) -> None:
        previous_build_layout(self)
        for delay_ms in (0, 100, 500, 1200):
            self.after(delay_ms, lambda app=self: _inject_earnings_button(app))

    app_cls._build_layout = _build_layout_with_earnings_radar  # type: ignore[method-assign]
    app_cls.show_earnings_radar = _open_earnings_radar_dashboard  # type: ignore[attr-defined]


def _inject_earnings_button(self: tk.Tk) -> None:
    actions = _find_labelframe(self, "Schwab Actions")
    if actions is None or getattr(actions, "_earnings_radar_button_installed", False):
        return
    for column in range(3):
        actions.columnconfigure(column, weight=1, uniform="schwab_actions")
    ttk.Button(actions, text="Market Screener", command=self.show_earnings_radar, style="Accent.TButton").grid(
        row=3,
        column=2,
        sticky="ew",
        padx=(4, 0),
        pady=(6, 6),
        ipady=1,
    )
    setattr(actions, "_earnings_radar_button_installed", True)


def _open_earnings_radar_dashboard(self: tk.Tk) -> None:
    existing = getattr(self, "earnings_radar_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                return
        except tk.TclError:
            pass

    _ensure_state(self)
    window = tk.Toplevel(self)
    window.title("Market Intelligence Screener")
    window.geometry("1420x900")
    window.minsize(1120, 700)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(0, weight=1)
    self.earnings_radar_window = window

    notebook = ttk.Notebook(window)
    notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
    screener_tab = ttk.Frame(notebook, style="Panel.TFrame")
    recent_tab = ttk.Frame(notebook, style="Panel.TFrame")
    upcoming_tab = ttk.Frame(notebook, style="Panel.TFrame")
    notebook.add(screener_tab, text="All Stocks / Screener")
    notebook.add(recent_tab, text="Recent EDGAR Drops")
    notebook.add(upcoming_tab, text="Upcoming Earnings")
    _build_screener_tab(self, screener_tab)
    _build_recent_tab(self, recent_tab)
    _build_upcoming_tab(self, upcoming_tab)

    cached = EarningsRadarStore().load(max_age=None)
    if cached is not None:
        _load_recent_snapshot(self, cached)
        self.earnings_recent_status_var.set(f"Loaded cached EDGAR earnings radar: {len(cached.recent)} filings.")
    else:
        self.earnings_recent_status_var.set("Ready. Refresh SEC data to load recent earnings drops.")

    window.after(150, lambda app=self: _refresh_screener(app, force_refresh=False))
    window.after(300, lambda app=self: _refresh_recent(app, force_refresh=False))
    window.after(600, lambda app=self: _refresh_upcoming(app, force_refresh=False))

    def _close() -> None:
        self.earnings_radar_window = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _close)


def _ensure_state(self: tk.Tk) -> None:
    self.market_screener_records: list[MarketScreenerRecord] = []
    self.market_screener_filtered_records: list[MarketScreenerRecord] = []
    self.market_screener_row_map: dict[str, MarketScreenerRecord] = {}
    self.market_screener_sort_column = DEFAULT_MARKET_SCREENER_SORT_COLUMN
    self.market_screener_sort_desc = DEFAULT_MARKET_SCREENER_SORT_DESC
    self.market_screener_page = 0
    self.market_screener_status_var = tk.StringVar(value="Ready.")
    self.market_screener_search_var = tk.StringVar(value="")
    self.market_screener_sector_var = tk.StringVar(value="All")
    self.market_screener_exchange_var = tk.StringVar(value="All")
    self.market_screener_event_type_var = tk.StringVar(value="All")
    self.market_screener_risk_flag_var = tk.StringVar(value="All")
    self.market_screener_earnings_window_var = tk.StringVar(value="All")
    self.market_screener_data_completeness_var = tk.StringVar(value="All")
    self.market_screener_has_ai_signal_var = tk.BooleanVar(value=False)
    self.market_screener_has_price_volume_data_var = tk.BooleanVar(value=False)
    self.market_screener_page_size_var = tk.StringVar(value="100")
    self.market_screener_ask_var = tk.StringVar(value="")
    self.market_screener_ask_summary_var = tk.StringVar(value="Ask Screener is ready.")
    self.market_screener_quick_preset_var = tk.StringVar(value="Quick filter")
    self.market_screener_ask_plan: AskScreenerPlan | None = None
    self.market_screener_ask_result: AskScreenerExecutionResult | None = None
    self.market_screener_selected_record: MarketScreenerRecord | None = None
    self.market_screener_selected_summary_var = tk.StringVar(value="No screener row selected.")
    self.market_screener_ai_status_var = tk.StringVar(value="Select a screener row for row-grounded AI.")
    self.market_screener_source_summary_var = tk.StringVar(value="Market data/source status will appear here after load.")
    self.market_screener_last_snapshot: MarketScreenerSnapshot | None = None
    self.market_screener_empty_state_text = ""
    self.market_screener_summary_visible_var = tk.BooleanVar(value=True)
    self.market_screener_summary_toggle_var = tk.StringVar(value="Hide Summary")
    self._market_screener_ai_running = False
    self._market_screener_refreshing = False
    self._market_screener_refresh_pending = False
    self._market_screener_refresh_pending_force = False
    self._market_screener_ask_running = False
    self.market_screener_source_status_base_text = "Source/status: Market Intelligence Screener has not loaded yet."
    self.market_screener_market_data_status_lines: list[str] = []
    self.market_screener_last_enrichment_symbols: tuple[str, ...] = ()
    self.market_screener_last_enrichment_reason = ""
    self.market_screener_market_data_attempted_symbols: set[str] = set()
    self.market_screener_market_data_running_symbols: set[str] = set()
    self.market_screener_open_source_button = None
    self.market_screener_open_symbol_chat_button = None
    self.market_screener_context_button = None

    self.earnings_recent_records: list[RecentEarningsRecord] = []
    self.earnings_recent_filtered_records: list[RecentEarningsRecord] = []
    self.earnings_recent_row_map: dict[str, RecentEarningsRecord] = {}
    self.earnings_recent_sort_column = "filed_date"
    self.earnings_recent_sort_desc = True
    self.earnings_recent_page = 0
    self.earnings_recent_status_var = tk.StringVar(value="Ready.")
    self.earnings_recent_search_var = tk.StringVar(value="")
    self.earnings_recent_form_var = tk.StringVar(value="All")
    self.earnings_recent_item_var = tk.StringVar(value="All")
    self.earnings_recent_sector_var = tk.StringVar(value="All")
    self.earnings_recent_exchange_var = tk.StringVar(value="All")
    self.earnings_recent_risk_flag_var = tk.StringVar(value="All")
    self.earnings_recent_guidance_var = tk.StringVar(value="All")
    self.earnings_recent_date_from_var = tk.StringVar(value="")
    self.earnings_recent_date_to_var = tk.StringVar(value="")
    self.earnings_recent_limit_var = tk.StringVar(value="100")
    self.earnings_recent_page_size_var = tk.StringVar(value="100")
    self.earnings_recent_parse_documents_var = tk.BooleanVar(value=True)
    self.earnings_recent_has_exhibit_var = tk.BooleanVar(value=False)
    self.earnings_recent_selected_record: RecentEarningsRecord | None = None
    self.earnings_ai_status_var = tk.StringVar(value="Select a recent earnings row for row-grounded AI.")
    self._earnings_ai_running = False

    self.earnings_upcoming_records: list[UpcomingEarningsRecord] = []
    self.earnings_upcoming_filtered_records: list[UpcomingEarningsRecord] = []
    self.earnings_upcoming_row_map: dict[str, UpcomingEarningsRecord] = {}
    self.earnings_upcoming_sort_column = "report_date"
    self.earnings_upcoming_sort_desc = False
    self.earnings_upcoming_page = 0
    self.earnings_upcoming_status_var = tk.StringVar(value="Ready.")
    self.earnings_upcoming_search_var = tk.StringVar(value="")
    self.earnings_upcoming_horizon_var = tk.StringVar(value="3month")
    self.earnings_upcoming_symbols_var = tk.StringVar(value="")
    self.earnings_upcoming_date_from_var = tk.StringVar(value="")
    self.earnings_upcoming_date_to_var = tk.StringVar(value="")
    self.earnings_upcoming_has_estimate_var = tk.BooleanVar(value=False)
    self.earnings_upcoming_page_size_var = tk.StringVar(value="100")


def _build_screener_tab(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(3, weight=1)
    _configure_market_screener_tree_style(parent)
    _header(
        parent,
        self.market_screener_status_var,
    ).grid(row=0, column=0, sticky="ew", pady=(0, 3))
    _build_screener_filters(self, parent)
    self.market_screener_chart = _screener_summary_panel(self, parent, row=2)
    self.market_screener_table = _table(
        parent,
        row=3,
        title="All Stocks / Screener",
        columns=SCREENER_COLUMNS,
        sort=lambda col: _sort_screener(self, col),
        style=MARKET_SCREENER_TABLE_STYLE,
        height=MARKET_SCREENER_TABLE_HEIGHT,
    )
    self.market_screener_table.bind("<Double-1>", lambda _event, app=self: _open_screener_source(app), add="+")
    self.market_screener_table.bind("<<TreeviewSelect>>", lambda _event, app=self: _on_screener_selection_changed(app), add="+")
    self.market_screener_table.tag_configure("holding", foreground=polished_theme.ACCENT)
    self.market_screener_table.tag_configure("signal", foreground=polished_theme.ACCENT_SOFT)
    self.market_screener_table.tag_configure("risk", foreground=polished_theme.NEGATIVE)
    _footer(
        self,
        parent,
        row=4,
        page_var_name="market_screener_page_var",
        size_var=self.market_screener_page_size_var,
        prev=lambda: _turn_screener_page(self, -1),
        next_=lambda: _turn_screener_page(self, 1),
        apply=lambda: _apply_screener_filters(self),
    )


def _build_screener_filters(self: tk.Tk, parent: ttk.Frame) -> None:
    box = ttk.LabelFrame(parent, text="Filters", style="Card.TLabelframe")
    box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    for column in range(8):
        box.columnconfigure(column, weight=1 if column in {1, 3, 5, 7} else 0)

    ask = ttk.Frame(box, style="Panel.TFrame")
    ask.grid(row=0, column=0, columnspan=8, sticky="ew", pady=(0, 8))
    ask.columnconfigure(1, weight=1)
    ttk.Label(ask, text="Ask Screener", style="Subtle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
    ask_entry = ttk.Entry(ask, textvariable=self.market_screener_ask_var)
    ask_entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
    ask_entry.bind("<Return>", lambda _event, app=self: _run_ask_screener_query(app), add="+")
    self.market_screener_ask_button = ttk.Button(ask, text="Ask", command=lambda app=self: _run_ask_screener_query(app), style="Accent.TButton")
    self.market_screener_ask_button.grid(row=0, column=2, sticky="e", padx=(0, 8))
    ttk.Button(ask, text="Clear Ask", command=lambda app=self: _clear_ask_screener(app)).grid(row=0, column=3, sticky="e")
    ttk.Label(ask, textvariable=self.market_screener_ask_summary_var, style="Chip.TLabel", wraplength=980).grid(row=1, column=1, columnspan=3, sticky="ew", pady=(6, 0))

    _entry_filter(box, "Search", self.market_screener_search_var, row=1, column=0, command=lambda: _apply_screener_filters(self))
    self.market_screener_sector_combo = _combo_filter(box, "Sector", self.market_screener_sector_var, ["All"], row=1, column=2, command=lambda: _apply_screener_filters(self))
    self.market_screener_exchange_combo = _combo_filter(box, "Exchange", self.market_screener_exchange_var, ["All"], row=1, column=4, command=lambda: _apply_screener_filters(self))
    _combo_filter(box, "Event", self.market_screener_event_type_var, list(EVENT_TYPE_OPTIONS), row=1, column=6, command=lambda: _apply_screener_filters(self))
    self.market_screener_risk_flag_combo = _combo_filter(box, "Risk flag", self.market_screener_risk_flag_var, ["All"], row=2, column=0, command=lambda: _apply_screener_filters(self))
    _combo_filter(box, "Earnings", self.market_screener_earnings_window_var, list(EARNINGS_WINDOW_OPTIONS), row=2, column=2, command=lambda: _apply_screener_filters(self))
    _combo_filter(box, "Completeness", self.market_screener_data_completeness_var, list(DATA_COMPLETENESS_OPTIONS), row=2, column=4, command=lambda: _apply_screener_filters(self))
    checks = ttk.Frame(box, style="Panel.TFrame")
    checks.grid(row=2, column=6, columnspan=2, sticky="ew", pady=4)
    ttk.Checkbutton(checks, text="Has AI-worthy signal", variable=self.market_screener_has_ai_signal_var, command=lambda: _apply_screener_filters(self)).pack(side=tk.LEFT)
    ttk.Checkbutton(checks, text="Has price/volume data", variable=self.market_screener_has_price_volume_data_var, command=lambda: _apply_screener_filters(self)).pack(side=tk.LEFT, padx=(12, 0))

    quick = ttk.Frame(box, style="Panel.TFrame")
    quick.grid(row=3, column=0, columnspan=8, sticky="ew", pady=(2, 0))
    ttk.Label(quick, text="Quick", style="Subtle.TLabel").pack(side=tk.LEFT, padx=(0, 6))
    self.market_screener_quick_preset_combo = ttk.Combobox(
        quick,
        textvariable=self.market_screener_quick_preset_var,
        values=[
            "Quick filter",
            "Earnings Soon",
            "Recent SEC Filing",
            "Guidance",
            "Risk Flags",
            "High Volume / Mover",
            "Quote-enriched",
            "Fundamentals",
            "My Holdings",
            "Complete Data",
        ],
        width=24,
        state="readonly",
    )
    self.market_screener_quick_preset_combo.pack(side=tk.LEFT, padx=(0, 6))
    self.market_screener_quick_preset_combo.bind("<<ComboboxSelected>>", lambda _event, app=self: _apply_selected_screener_quick_preset(app), add="+")

    actions = ttk.Frame(box, style="Panel.TFrame")
    actions.grid(row=4, column=0, columnspan=8, sticky="ew", pady=(8, 0))
    ttk.Button(actions, text="Refresh", command=lambda: _refresh_screener(self, force_refresh=True), style="Accent.TButton").pack(side=tk.LEFT)
    ttk.Button(actions, text="Enrich Current Page", command=lambda: _request_visible_page_market_data_enrichment(self, force_refresh=False)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text="Re-enrich Current Page", command=lambda: _request_visible_page_market_data_enrichment(self, force_refresh=True)).pack(side=tk.LEFT, padx=(8, 0))
    self.market_screener_open_source_button = ttk.Button(actions, text="Open Source", command=lambda: _open_screener_source(self))
    self.market_screener_open_source_button.pack(side=tk.LEFT, padx=(8, 0))
    self.market_screener_open_symbol_chat_button = ttk.Button(actions, text="Open Symbol Chat", command=lambda: _open_screener_symbol_chat(self))
    self.market_screener_open_symbol_chat_button.pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text="Loading / Source Diagnostics", command=lambda app=self: _open_screener_diagnostics_popout(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text="Clear Filters", command=lambda: _clear_screener_filters(self)).pack(side=tk.LEFT, padx=(8, 0))
    self.market_screener_context_button = ttk.Button(actions, text="Open Selected Context + AI", command=lambda app=self: _open_screener_context_popout(app))
    self.market_screener_context_button.pack(side=tk.LEFT, padx=(8, 0))
    ttk.Label(actions, textvariable=self.market_screener_selected_summary_var, style="Chip.TLabel", width=48).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))
    self.market_screener_ai_quick_buttons: list[ttk.Button] = []
    _set_screener_ai_actions_enabled(self, False)
    _update_screener_row_action_states(self, None)


def _configure_market_screener_tree_style(parent: tk.Widget) -> None:
    style = ttk.Style(parent)
    style.configure(
        MARKET_SCREENER_TABLE_STYLE,
        background=polished_theme.INPUT,
        fieldbackground=polished_theme.INPUT,
        foreground=polished_theme.TEXT,
        rowheight=MARKET_SCREENER_TABLE_ROWHEIGHT,
        font=("Segoe UI", 10),
        bordercolor=polished_theme.BORDER,
        borderwidth=0,
    )
    style.configure(
        f"{MARKET_SCREENER_TABLE_STYLE}.Heading",
        background=polished_theme.TREE_HEADING,
        foreground=polished_theme.TEXT,
        font=("Segoe UI", 10, "bold"),
        padding=10,
        relief=tk.FLAT,
    )
    style.map(MARKET_SCREENER_TABLE_STYLE, background=[("selected", polished_theme.SELECTED)], foreground=[("selected", polished_theme.SELECTED_TEXT)])
    style.map(f"{MARKET_SCREENER_TABLE_STYLE}.Heading", background=[("active", polished_theme.PANEL_ALT)], foreground=[("active", polished_theme.TEXT)])


def _open_screener_context_popout(self: tk.Tk) -> None:
    record = _selected_screener_record(self, show_message=False)
    action_state = _update_screener_row_action_states(self, record)
    if not action_state.get("context_enabled"):
        _set_var_if_present(self, "market_screener_selected_summary_var", str(action_state.get("context_reason") or "Selected context is unavailable."))
        messagebox.showinfo("Open Selected Context + AI", str(action_state.get("context_reason") or "Select a screener row first."))
        return
    window = getattr(self, "market_screener_context_window", None)
    if not _widget_exists(window):
        parent = getattr(self, "earnings_radar_window", None)
        window = tk.Toplevel(parent if _widget_exists(parent) else self)
        polished_theme.configure_toplevel(window)
        window.title("Selected Screener Context + AI")
        window.geometry("1080x780")
        window.minsize(800, 560)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(3, weight=3)
        window.rowconfigure(6, weight=2)
        self.market_screener_context_window = window

        ttk.Label(window, textvariable=self.market_screener_ai_status_var, style="Chip.TLabel", wraplength=1010).grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        actions = ttk.Frame(window, style="Panel.TFrame")
        actions.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        self.market_screener_ai_analyze_button = ttk.Button(
            actions,
            text="Analyze Selected",
            command=lambda app=self: _run_screener_ai_prompt(app, "Analyze Selected", MARKET_SCREENER_AI_ANALYZE_PROMPT),
            style="Accent.TButton",
        )
        self.market_screener_ai_analyze_button.pack(side=tk.LEFT, padx=(0, 8))
        self.market_screener_ai_why_button = ttk.Button(
            actions,
            text="Why Interesting?",
            command=lambda app=self: _run_screener_ai_prompt(app, "Why Interesting?", MARKET_SCREENER_AI_QUICK_PROMPTS["Why Interesting?"]),
        )
        self.market_screener_ai_why_button.pack(side=tk.LEFT, padx=(0, 8))
        self.market_screener_ai_risks_button = ttk.Button(
            actions,
            text="Risks + Diligence",
            command=lambda app=self: _run_screener_ai_prompt(app, "Risks + Diligence", MARKET_SCREENER_AI_QUICK_PROMPTS["Risks + Diligence"]),
        )
        self.market_screener_ai_risks_button.pack(side=tk.LEFT, padx=(0, 8))
        self.market_screener_ai_quick_buttons = []
        for label, prompt in MARKET_SCREENER_AI_QUICK_PROMPTS.items():
            if label in {"Why Interesting?", "Risks + Diligence"}:
                continue
            button = ttk.Button(actions, text=label, command=lambda prompt_label=label, value=prompt, app=self: _run_screener_ai_prompt(app, prompt_label, value))
            button.pack(side=tk.LEFT, padx=(0, 8))
            self.market_screener_ai_quick_buttons.append(button)
        self.market_screener_ai_symbol_chat_button = ttk.Button(actions, text="Open Symbol Chat", command=lambda app=self: _open_screener_symbol_chat(app))
        self.market_screener_ai_symbol_chat_button.pack(side=tk.LEFT)

        ttk.Label(window, text="Selected row context", style="Subtle.TLabel").grid(row=2, column=0, sticky="w", padx=14, pady=(0, 4))
        self.market_screener_detail_text = _readonly_scrolled_text(
            window,
            row=3,
            bg=polished_theme.PANEL,
            fg=polished_theme.TEXT,
            font=MARKET_SCREENER_TEXT_FONT,
            height=14,
        )
        ttk.Label(window, textvariable=self.market_screener_source_summary_var, style="Chip.TLabel", wraplength=1010).grid(row=4, column=0, sticky="ew", padx=14, pady=(12, 4))
        ttk.Label(window, text="Source/status", style="Subtle.TLabel").grid(row=5, column=0, sticky="w", padx=14, pady=(0, 4))
        self.market_screener_source_text = _readonly_scrolled_text(
            window,
            row=6,
            bg=polished_theme.INPUT,
            fg=polished_theme.MUTED,
            font=MARKET_SCREENER_SOURCE_FONT,
            height=8,
        )

        def _close() -> None:
            self.market_screener_context_window = None
            self.market_screener_detail_text = None
            self.market_screener_source_text = None
            self.market_screener_ai_analyze_button = None
            self.market_screener_ai_why_button = None
            self.market_screener_ai_risks_button = None
            self.market_screener_ai_symbol_chat_button = None
            self.market_screener_ai_quick_buttons = []
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", _close)

    record = _selected_screener_record(self, show_message=False)
    self.market_screener_selected_record = record
    _update_screener_detail_panel(self, record)
    _refresh_screener_source_text(self)
    _set_screener_ai_actions_enabled(self, record is not None)
    try:
        window.deiconify()
        window.lift()
        window.focus_force()
    except tk.TclError:
        pass


def _open_screener_diagnostics_popout(self: tk.Tk) -> None:
    window = getattr(self, "market_screener_diagnostics_window", None)
    if not _widget_exists(window):
        parent = getattr(self, "earnings_radar_window", None)
        window = tk.Toplevel(parent if _widget_exists(parent) else self)
        polished_theme.configure_toplevel(window)
        window.title("Loading / Source Diagnostics / Why blanks?")
        window.geometry("1040x760")
        window.minsize(780, 520)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        self.market_screener_diagnostics_window = window

        ttk.Label(window, textvariable=self.market_screener_source_summary_var, style="Chip.TLabel", wraplength=970).grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
        actions = ttk.Frame(window, style="Panel.TFrame")
        actions.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 10))
        ttk.Button(actions, text="Refresh Screener", command=lambda app=self: _refresh_screener(app, force_refresh=True), style="Accent.TButton").pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Enrich Current Page", command=lambda app=self: _request_visible_page_market_data_enrichment(app, force_refresh=False)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Re-enrich Current Page", command=lambda app=self: _request_visible_page_market_data_enrichment(app, force_refresh=True)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Open Selected Context + AI", command=lambda app=self: _open_screener_context_popout(app)).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(actions, text="Clear Filters", command=lambda app=self: _clear_screener_filters(app)).pack(side=tk.LEFT)
        self.market_screener_diagnostics_text = _readonly_scrolled_text(
            window,
            row=2,
            bg=polished_theme.INPUT,
            fg=polished_theme.TEXT,
            font=MARKET_SCREENER_SOURCE_FONT,
            height=26,
        )

        def _close() -> None:
            self.market_screener_diagnostics_window = None
            self.market_screener_diagnostics_text = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", _close)

    _refresh_screener_diagnostics_text(self)
    try:
        window.deiconify()
        window.lift()
        window.focus_force()
    except tk.TclError:
        pass


def _readonly_scrolled_text(
    parent: tk.Widget,
    *,
    row: int,
    bg: str,
    fg: str,
    font: tuple[str, int],
    height: int,
) -> tk.Text:
    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.grid(row=row, column=0, sticky="nsew", padx=14)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    text = tk.Text(
        frame,
        height=height,
        wrap=tk.WORD,
        bg=bg,
        fg=fg,
        insertbackground=polished_theme.TEXT,
        relief=tk.FLAT,
        padx=12,
        pady=10,
        font=font,
    )
    text.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=scroll.set, state=tk.DISABLED)
    return text


def _widget_exists(widget: Any) -> bool:
    if widget is None:
        return False
    try:
        return bool(widget.winfo_exists())
    except (AttributeError, tk.TclError):
        return False


def _build_recent_tab(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(3, weight=1)
    _header(
        parent,
        self.earnings_recent_status_var,
    ).grid(row=0, column=0, sticky="ew", pady=(0, 3))
    _build_recent_filters(self, parent)
    self.earnings_recent_chart = _chart(parent, row=2)
    self.earnings_recent_table = _table(parent, row=3, title="Recent EDGAR Drops", columns=RECENT_COLUMNS, sort=lambda col: _sort_recent(self, col))
    self.earnings_recent_table.bind("<Double-1>", lambda _event, app=self: _open_recent_filing(app), add="+")
    self.earnings_recent_table.bind("<<TreeviewSelect>>", lambda _event, app=self: _on_recent_selection_changed(app), add="+")
    self.earnings_recent_table.tag_configure("kind_drop", foreground=polished_theme.ACCENT_SOFT)
    self.earnings_recent_table.tag_configure("kind_formal", foreground=polished_theme.POSITIVE)
    _build_recent_detail_panel(self, parent, row=4)
    _footer(self, parent, row=5, page_var_name="earnings_recent_page_var", size_var=self.earnings_recent_page_size_var, prev=lambda: _turn_recent_page(self, -1), next_=lambda: _turn_recent_page(self, 1), apply=lambda: _apply_recent_filters(self))


def _build_upcoming_tab(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(3, weight=1)
    _header(
        parent,
        self.earnings_upcoming_status_var,
    ).grid(row=0, column=0, sticky="ew", pady=(0, 3))
    _build_upcoming_filters(self, parent)
    self.earnings_upcoming_chart = _chart(parent, row=2)
    self.earnings_upcoming_table = _table(parent, row=3, title="Upcoming Earnings", columns=UPCOMING_COLUMNS, sort=lambda col: _sort_upcoming(self, col))
    self.earnings_upcoming_table.bind("<Double-1>", lambda _event, app=self: _open_upcoming_source(app), add="+")
    _footer(self, parent, row=4, page_var_name="earnings_upcoming_page_var", size_var=self.earnings_upcoming_page_size_var, prev=lambda: _turn_upcoming_page(self, -1), next_=lambda: _turn_upcoming_page(self, 1), apply=lambda: _apply_upcoming_filters(self))


def _build_recent_filters(self: tk.Tk, parent: ttk.Frame) -> None:
    box = ttk.LabelFrame(parent, text="Filters", style="Card.TLabelframe")
    box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    for column in range(8):
        box.columnconfigure(column, weight=1 if column in {1, 3, 5, 7} else 0)
    _entry_filter(box, "Search", self.earnings_recent_search_var, row=0, column=0, command=lambda: _apply_recent_filters(self))
    _combo_filter(box, "Form", self.earnings_recent_form_var, ["All", *EARNINGS_FORMS], row=0, column=2, command=lambda: _apply_recent_filters(self))
    self.earnings_recent_item_combo = _combo_filter(box, "Item", self.earnings_recent_item_var, ["All"], row=0, column=4, command=lambda: _apply_recent_filters(self))
    _combo_filter(box, "Guidance", self.earnings_recent_guidance_var, ["All", "Mentioned", "Not mentioned"], row=0, column=6, command=lambda: _apply_recent_filters(self))
    self.earnings_recent_sector_combo = _combo_filter(box, "Sector", self.earnings_recent_sector_var, ["All"], row=1, column=0, command=lambda: _apply_recent_filters(self))
    self.earnings_recent_exchange_combo = _combo_filter(box, "Exchange", self.earnings_recent_exchange_var, ["All"], row=1, column=2, command=lambda: _apply_recent_filters(self))
    self.earnings_recent_risk_flag_combo = _combo_filter(box, "Risk flag", self.earnings_recent_risk_flag_var, ["All"], row=1, column=4, command=lambda: _apply_recent_filters(self))
    _combo_filter(box, "Per form", self.earnings_recent_limit_var, ["20", "40", "60", "100"], row=1, column=6, command=None)
    _date_filters(box, self.earnings_recent_date_from_var, self.earnings_recent_date_to_var, row=2, columnspan=4, command=lambda: _apply_recent_filters(self))
    checks = ttk.Frame(box, style="Panel.TFrame")
    checks.grid(row=2, column=4, columnspan=4, sticky="ew", pady=(6, 0))
    ttk.Checkbutton(checks, text="Has EX-99 / exhibit", variable=self.earnings_recent_has_exhibit_var, command=lambda: _apply_recent_filters(self)).pack(side=tk.LEFT)
    ttk.Checkbutton(checks, text="Parse optional release text", variable=self.earnings_recent_parse_documents_var).pack(side=tk.LEFT, padx=(12, 0))
    quick = ttk.Frame(box, style="Panel.TFrame")
    quick.grid(row=3, column=0, columnspan=8, sticky="ew", pady=(8, 0))
    ttk.Label(quick, text="Quick", style="Subtle.TLabel").pack(side=tk.LEFT, padx=(0, 6))
    for label, preset in (
        ("Last 7D", "last7"),
        ("8-K 2.02", "item202"),
        ("Guidance", "guidance"),
        ("Risk Flags", "risk"),
        ("Has Exhibit", "exhibit"),
        ("Formal Reports", "formal"),
    ):
        ttk.Button(quick, text=label, command=lambda value=preset, app=self: _apply_recent_quick_preset(app, value)).pack(side=tk.LEFT, padx=(0, 6))
    actions = ttk.Frame(box, style="Panel.TFrame")
    actions.grid(row=4, column=0, columnspan=8, sticky="ew", pady=(8, 0))
    ttk.Button(actions, text="Refresh SEC Data", command=lambda: _refresh_recent(self, force_refresh=True), style="Accent.TButton").pack(side=tk.LEFT)
    ttk.Button(actions, text="Open SEC Filing", command=lambda: _open_recent_filing(self)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text="Open Earnings Exhibit", command=lambda: _open_recent_exhibit(self)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text="Clear Filters", command=lambda: _clear_recent_filters(self)).pack(side=tk.LEFT, padx=(8, 0))


def _build_recent_detail_panel(self: tk.Tk, parent: ttk.Frame, *, row: int) -> None:
    detail = ttk.LabelFrame(parent, text="Selected Earnings Context + AI", style="Card.TLabelframe")
    detail.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    detail.columnconfigure(0, weight=1)
    detail.columnconfigure(1, weight=0)
    ttk.Label(detail, textvariable=self.earnings_ai_status_var, style="Chip.TLabel").grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 6))

    self.earnings_recent_detail_text = tk.Text(
        detail,
        height=5,
        wrap=tk.WORD,
        bg=polished_theme.PANEL,
        fg=polished_theme.TEXT,
        insertbackground=polished_theme.TEXT,
        relief=tk.FLAT,
        padx=10,
        pady=8,
        font=("Segoe UI", 9),
    )
    self.earnings_recent_detail_text.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
    self.earnings_recent_detail_text.configure(state=tk.DISABLED)

    actions = ttk.Frame(detail, style="Panel.TFrame")
    actions.grid(row=1, column=1, sticky="nsew", padx=(0, 8), pady=(0, 8))
    self.earnings_ai_analyze_button = ttk.Button(actions, text="Analyze Selected", command=lambda app=self: _run_earnings_ai_prompt(app, "Analyze Selected", EARNINGS_AI_ANALYZE_PROMPT), style="Accent.TButton")
    self.earnings_ai_analyze_button.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    self.earnings_ai_summarize_button = ttk.Button(actions, text="Summarize Filing", command=lambda app=self: _run_earnings_ai_prompt(app, "Summarize Filing", EARNINGS_AI_QUICK_PROMPTS["Summarize Drop"]))
    self.earnings_ai_summarize_button.grid(row=1, column=0, sticky="ew", pady=(0, 4))
    self.earnings_ai_symbol_chat_button = ttk.Button(actions, text="Open Symbol Chat", command=lambda app=self: _open_recent_symbol_chat(app))
    self.earnings_ai_symbol_chat_button.grid(row=2, column=0, sticky="ew", pady=(0, 4))

    quick = ttk.Frame(detail, style="Panel.TFrame")
    quick.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 8))
    ttk.Label(quick, text="AI", style="Subtle.TLabel").pack(side=tk.LEFT, padx=(0, 6))
    self.earnings_ai_quick_buttons: list[ttk.Button] = []
    for label, prompt in EARNINGS_AI_QUICK_PROMPTS.items():
        button = ttk.Button(quick, text=label, command=lambda prompt_label=label, value=prompt, app=self: _run_earnings_ai_prompt(app, prompt_label, value))
        button.pack(side=tk.LEFT, padx=(0, 6))
        self.earnings_ai_quick_buttons.append(button)
    _set_recent_ai_actions_enabled(self, False)


def _build_upcoming_filters(self: tk.Tk, parent: ttk.Frame) -> None:
    box = ttk.LabelFrame(parent, text="Filters", style="Card.TLabelframe")
    box.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    for column in range(6):
        box.columnconfigure(column, weight=1 if column in {1, 3, 5} else 0)
    _entry_filter(box, "Search", self.earnings_upcoming_search_var, row=0, column=0, command=lambda: _apply_upcoming_filters(self))
    _combo_filter(box, "Horizon", self.earnings_upcoming_horizon_var, list(ALPHA_VANTAGE_HORIZONS), row=0, column=2, command=lambda: _refresh_upcoming(self, force_refresh=False))
    _entry_filter(box, "Symbols", self.earnings_upcoming_symbols_var, row=0, column=4, command=lambda: _apply_upcoming_filters(self))
    _date_filters(box, self.earnings_upcoming_date_from_var, self.earnings_upcoming_date_to_var, row=1, columnspan=4, command=lambda: _apply_upcoming_filters(self))
    checks = ttk.Frame(box, style="Panel.TFrame")
    checks.grid(row=1, column=4, columnspan=2, sticky="ew", pady=(6, 0))
    ttk.Checkbutton(checks, text="Has estimate", variable=self.earnings_upcoming_has_estimate_var, command=lambda: _apply_upcoming_filters(self)).pack(side=tk.LEFT)
    actions = ttk.Frame(box, style="Panel.TFrame")
    actions.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(8, 0))
    ttk.Button(actions, text="Refresh Calendar", command=lambda: _refresh_upcoming(self, force_refresh=True), style="Accent.TButton").pack(side=tk.LEFT)
    ttk.Button(actions, text="Open Source", command=lambda: _open_upcoming_source(self)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text="Clear Filters", command=lambda: _clear_upcoming_filters(self)).pack(side=tk.LEFT, padx=(8, 0))


def _market_screener_holdings_records(self: tk.Tk) -> list[MarketScreenerRecord]:
    broker = getattr(self, "broker", None)
    getter = getattr(broker, "get_portfolio", None)
    if not callable(getter):
        return []
    try:
        portfolio = getter()
    except Exception:
        return []
    positions = getattr(portfolio, "positions", None)
    if not isinstance(positions, dict):
        return []
    fetched_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    total_value = max(_safe_float(getattr(portfolio, "total_value", None)) or 0.0, 0.01)
    records: list[MarketScreenerRecord] = []
    for symbol, position in sorted(positions.items()):
        clean_symbol = str(getattr(position, "symbol", symbol) or symbol).strip().upper()
        if not clean_symbol or clean_symbol.startswith("HL:") or clean_symbol == "USD":
            continue
        quantity = _safe_float(getattr(position, "quantity", None))
        average_cost = _safe_float(getattr(position, "average_cost", None))
        market_value = _safe_float(getattr(position, "market_value", None))
        unrealized_pnl = _safe_float(getattr(position, "unrealized_profit_loss", None))
        records.append(
            MarketScreenerRecord(
                symbol=clean_symbol,
                company_name=None,
                price=_safe_float(getattr(position, "last_price", None)),
                signals=("Schwab holding",),
                sources=("Local app holdings",),
                fetched_at=fetched_at,
                portfolio_quantity=quantity,
                portfolio_average_cost=average_cost,
                portfolio_market_value=market_value,
                portfolio_unrealized_pnl=unrealized_pnl,
                portfolio_weight=(market_value / total_value) if market_value is not None else None,
                source_excerpt=(
                    f"Local holdings context: quantity={quantity}; "
                    f"average_cost={average_cost}; "
                    f"market_value={market_value}; "
                    f"unrealized_pnl={unrealized_pnl}."
                ),
            )
        )
    return records


def _refresh_screener(self: tk.Tk, *, force_refresh: bool) -> None:
    if getattr(self, "_market_screener_refreshing", False):
        self._market_screener_refresh_pending = True
        self._market_screener_refresh_pending_force = bool(getattr(self, "_market_screener_refresh_pending_force", False) or force_refresh)
        return
    if force_refresh:
        _reset_screener_market_data_enrichment_state(self)
    self._market_screener_refreshing = True
    self._market_screener_refresh_pending = False
    self._market_screener_refresh_pending_force = False
    self.market_screener_status_var.set("Loading market intelligence screener...")
    _append_screener_market_data_status(
        self,
        (
            f"Screener refresh started: force_refresh={bool(force_refresh)}; "
            f"initial provider symbol cap={configured_market_data_symbol_limit()}."
        ),
    )
    recent_records = list(getattr(self, "earnings_recent_records", []) or [])
    upcoming_records = list(getattr(self, "earnings_upcoming_records", []) or [])
    supplemental_records = _market_screener_holdings_records(self)
    market_data_provider = configured_market_data_provider(schwab_session=getattr(self, "schwab_session", None))
    market_data_symbol_limit = configured_market_data_symbol_limit()

    def worker() -> None:
        try:
            snapshot = fetch_market_screener_snapshot(
                recent_records=recent_records,
                upcoming_records=upcoming_records,
                supplemental_records=supplemental_records,
                market_data_provider=market_data_provider,
                market_data_symbol_limit=market_data_symbol_limit,
                force_refresh=force_refresh,
            )
        except Exception as exc:
            self.after(0, lambda error=exc: _finish_screener_error(self, error))
            return
        self.after(0, lambda loaded=snapshot: _finish_screener_success(self, loaded))

    threading.Thread(target=worker, daemon=True).start()


def _finish_screener_success(self: tk.Tk, snapshot: MarketScreenerSnapshot) -> None:
    self._market_screener_refreshing = False
    _load_screener_snapshot(self, snapshot)
    signals = sum(1 for record in snapshot.records if market_screener_has_ai_signal(record))
    holdings = sum(1 for record in snapshot.records if market_screener_is_my_holding(record))
    warnings = f" ({len(snapshot.errors)} nonblocking warning(s))" if snapshot.errors else ""
    source_status = market_screener_diagnostics_summary(snapshot.diagnostics)
    self.market_screener_status_var.set(f"Loaded {len(snapshot.records)} screener rows; {holdings} My Holdings; {signals} with AI-worthy signals. {source_status} Fetched {snapshot.fetched_at}.{warnings}")
    _append_screener_market_data_status(
        self,
        f"Screener refresh finished: rows={len(snapshot.records)}; statuses={len(snapshot.statuses)}; warnings={len(snapshot.errors)}; fetched_at={snapshot.fetched_at}.",
    )
    _run_pending_screener_refresh(self)


def _finish_screener_error(self: tk.Tk, error: Exception) -> None:
    self._market_screener_refreshing = False
    self.market_screener_status_var.set(f"Screener refresh failed: {error}")
    _append_screener_market_data_status(self, f"Screener refresh failed: {redact_symbol_chat_secrets(str(error))}")
    _run_pending_screener_refresh(self)


def _run_pending_screener_refresh(self: tk.Tk) -> None:
    if not getattr(self, "_market_screener_refresh_pending", False):
        return
    force_refresh = bool(getattr(self, "_market_screener_refresh_pending_force", False))
    self._market_screener_refresh_pending = False
    self._market_screener_refresh_pending_force = False
    try:
        self.after(50, lambda app=self, force=force_refresh: _refresh_screener(app, force_refresh=force))
    except tk.TclError:
        return


def _load_screener_snapshot(self: tk.Tk, snapshot: MarketScreenerSnapshot) -> None:
    self.market_screener_last_snapshot = snapshot
    self.market_screener_records = list(snapshot.records)
    attempted_symbols = getattr(self, "market_screener_market_data_attempted_symbols", set())
    if isinstance(attempted_symbols, set):
        attempted_symbols.update(_market_data_symbols_from_records(self.market_screener_records))
    _set_combo_values(self.market_screener_sector_combo, ["All", *sorted({record.sector or EMPTY_VALUE for record in self.market_screener_records})], self.market_screener_sector_var)
    _set_combo_values(self.market_screener_exchange_combo, ["All", *sorted({record.exchange or EMPTY_VALUE for record in self.market_screener_records})], self.market_screener_exchange_var)
    _set_combo_values(self.market_screener_risk_flag_combo, ["All", "Any risk flag", *sorted({flag for record in self.market_screener_records for flag in record.risk_flags})], self.market_screener_risk_flag_var)
    self.market_screener_source_status_base_text = _screener_source_status_text(snapshot)
    self.market_screener_source_summary_var.set(_screener_source_summary_text(snapshot))
    _refresh_screener_source_text(self)
    self.market_screener_page = 0
    _apply_screener_filters(self)


def _run_ask_screener_query(self: tk.Tk) -> None:
    query = self.market_screener_ask_var.get().strip()
    if not query:
        self.market_screener_ask_summary_var.set("Enter an Ask Screener question first.")
        return
    if getattr(self, "_market_screener_ask_running", False):
        self.market_screener_ask_summary_var.set("Ask Screener is already planning a query.")
        return
    local_plan = parse_ask_screener_fallback(query, records=getattr(self, "market_screener_records", ()) or ())
    if local_plan is not None:
        _apply_ask_screener_plan(self, local_plan, source_label="local")
        return

    self._market_screener_ask_running = True
    _set_ask_screener_actions_enabled(self, False)
    self.market_screener_ask_summary_var.set("Ask Screener is planning with compact schema metadata...")

    def progress(message: str) -> None:
        _post_to_earnings_ui(self, lambda value=message: self.market_screener_ask_summary_var.set(f"Ask Screener: {value}"))

    def worker() -> None:
        try:
            client = _ask_screener_planner_client(self)
            plan = client.plan(query, getattr(self, "market_screener_records", ()) or (), progress_callback=progress)
        except Exception as exc:
            _post_to_earnings_ui(self, lambda error=exc: _finish_ask_screener_error(self, error))
            return
        _post_to_earnings_ui(self, lambda loaded=plan: _finish_ask_screener_success(self, loaded))

    threading.Thread(target=worker, daemon=True).start()


def _ask_screener_planner_client(self: tk.Tk) -> OpenAiAskScreenerPlannerClient:
    factory = getattr(self, "ask_screener_planner_client_factory", None)
    if callable(factory):
        return factory()
    return OpenAiAskScreenerPlannerClient()


def _finish_ask_screener_success(self: tk.Tk, plan: AskScreenerPlan) -> None:
    self._market_screener_ask_running = False
    _set_ask_screener_actions_enabled(self, True)
    _apply_ask_screener_plan(self, plan, source_label="OpenAI JSON plan")


def _finish_ask_screener_error(self: tk.Tk, error: Exception) -> None:
    self._market_screener_ask_running = False
    _set_ask_screener_actions_enabled(self, True)
    message = redact_symbol_chat_secrets(str(error))
    if isinstance(error, AskScreenerPlannerError):
        self.market_screener_ask_summary_var.set(message)
    else:
        self.market_screener_ask_summary_var.set(f"Ask Screener could not plan this query: {message}")


def _apply_ask_screener_plan(self: tk.Tk, plan: AskScreenerPlan, *, source_label: str) -> None:
    if plan.clear_filters:
        _clear_screener_filters(self)
        self.market_screener_ask_summary_var.set("Ask Screener cleared filters.")
        self.market_screener_status_var.set("Ask Screener cleared filters.")
        return
    records = list(getattr(self, "market_screener_records", ()) or ())
    config = ask_screener_config_from_env(schwab_quote_configured=callable(getattr(getattr(self, "schwab_session", None), "get_quote", None)))
    decision = ask_screener_enrichment_decision(records, plan, config=config)
    if decision.action == ASK_SCREENER_PROVIDER_ACTION_MISSING_CONFIG:
        result = execute_provider_aware_ask_screener_plan(records, plan, config=config)
        _apply_ask_screener_plan_local(self, result.plan, source_label=source_label, precomputed_result=result)
        return
    if decision.action == ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT:
        if not _confirm_ask_screener_large_enrichment(decision):
            result = execute_provider_aware_ask_screener_plan(records, plan, config=config)
            _apply_ask_screener_plan_local(self, result.plan, source_label=source_label, precomputed_result=result)
            return
    if decision.action == ASK_SCREENER_PROVIDER_ACTION_ENRICH_THEN_EXECUTE or (
        decision.action == ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT
    ):
        _request_ask_screener_provider_enrichment(self, plan, source_label=source_label, config=config, allow_large_enrichment=True)
        return
    _apply_ask_screener_plan_local(self, plan, source_label=source_label)


def _apply_ask_screener_plan_local(
    self: tk.Tk,
    plan: AskScreenerPlan,
    *,
    source_label: str,
    precomputed_result: AskScreenerExecutionResult | None = None,
) -> None:
    self.market_screener_ask_plan = plan
    self.market_screener_ask_result = precomputed_result
    if plan.sort is not None:
        self.market_screener_sort_column = _ask_sort_field_to_screener_column(plan.sort.field)
        self.market_screener_sort_desc = plan.sort.descending
    self.market_screener_page = 0
    if precomputed_result is None:
        _apply_screener_filters(self)
        result = getattr(self, "market_screener_ask_result", None)
    else:
        filtered = list(precomputed_result.records)
        self.market_screener_filtered_records = sort_market_screener_records(filtered, self.market_screener_sort_column, descending=self.market_screener_sort_desc)
        self.market_screener_page = min(self.market_screener_page, _max_page(self.market_screener_filtered_records, _screener_page_size(self)))
        _populate_screener_table(self)
        _draw_screener_chart(self)
        result = precomputed_result
    summary = result.summary if result is not None else "Ask Screener applied the validated plan."
    message = f"{summary} Source: {source_label}."
    self.market_screener_ask_summary_var.set(message)
    self.market_screener_status_var.set(message)


def _confirm_ask_screener_large_enrichment(decision: AskScreenerEnrichmentDecision) -> bool:
    message = (
        f"Ask Screener needs provider enrichment for {decision.candidate_symbol_count} symbol(s), "
        f"capped to {len(decision.symbols_to_enrich)} this run.\n\nContinue?"
    )
    try:
        return bool(messagebox.askyesno("Ask Screener provider enrichment", message))
    except tk.TclError:
        return False


def _request_ask_screener_provider_enrichment(
    self: tk.Tk,
    plan: AskScreenerPlan,
    *,
    source_label: str,
    config: Any,
    allow_large_enrichment: bool,
) -> None:
    if getattr(self, "_market_screener_ask_running", False):
        self.market_screener_ask_summary_var.set("Ask Screener is already enriching a query.")
        return
    self._market_screener_ask_running = True
    _set_ask_screener_actions_enabled(self, False)
    self.market_screener_ask_summary_var.set("Ask Screener is enriching required provider fields, then filtering locally...")
    backfill_config = market_screener_backfill_config_from_env()
    provider = configured_market_data_provider(
        schwab_session=getattr(self, "schwab_session", None),
        include_fallback_provider=True,
        fmp_symbol_limit=max(backfill_config.profile_limit, backfill_config.quote_limit, backfill_config.fundamental_limit),
        databento_symbol_limit=backfill_config.databento_limit,
        cache_ttl_seconds=backfill_config.cache_ttl_seconds,
        batch_size=backfill_config.batch_size,
    )
    records = list(getattr(self, "market_screener_records", ()) or ())

    def worker() -> None:
        try:
            result = execute_provider_aware_ask_screener_plan(
                records,
                plan,
                provider=provider,
                config=config,
                force_refresh=False,
                allow_large_enrichment=allow_large_enrichment,
            )
        except Exception as exc:
            _post_to_earnings_ui(self, lambda error=exc: _finish_ask_screener_error(self, error))
            return
        _post_to_earnings_ui(self, lambda loaded=result: _finish_ask_screener_enrichment_success(self, loaded, source_label=source_label))

    threading.Thread(target=worker, daemon=True).start()


def _finish_ask_screener_enrichment_success(self: tk.Tk, result: AskScreenerExecutionResult, *, source_label: str) -> None:
    self._market_screener_ask_running = False
    _set_ask_screener_actions_enabled(self, True)
    if result.all_records:
        self.market_screener_records = list(result.all_records)
    _apply_ask_screener_plan_local(self, result.plan, source_label=source_label, precomputed_result=result)


def _clear_ask_screener(self: tk.Tk) -> None:
    self.market_screener_ask_var.set("")
    self.market_screener_ask_plan = None
    self.market_screener_ask_result = None
    self.market_screener_ask_summary_var.set("Ask Screener cleared.")
    self.market_screener_page = 0
    _apply_screener_filters(self)


def _set_ask_screener_actions_enabled(self: tk.Tk, enabled: bool) -> None:
    button = getattr(self, "market_screener_ask_button", None)
    if button is None:
        return
    try:
        button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
    except tk.TclError:
        pass


def _ask_sort_field_to_screener_column(field: str) -> str:
    return {
        "company_name": "company",
        "data_completeness_score": "data_completeness",
        "next_earnings_date": "next_earnings",
        "recent_filing_date": "recent_filing",
        "shares_float": "float_shares",
        "shares_outstanding": "float_shares",
    }.get(field, field)


def _apply_screener_filters(self: tk.Tk) -> None:
    filtered = filter_market_screener_records(
        self.market_screener_records,
        search=self.market_screener_search_var.get(),
        sector=self.market_screener_sector_var.get(),
        exchange=self.market_screener_exchange_var.get(),
        event_type=self.market_screener_event_type_var.get(),
        risk_flag=self.market_screener_risk_flag_var.get(),
        earnings_date_window=self.market_screener_earnings_window_var.get(),
        data_completeness=self.market_screener_data_completeness_var.get(),
        has_ai_signal=self.market_screener_has_ai_signal_var.get(),
        has_price_volume_data=self.market_screener_has_price_volume_data_var.get(),
    )
    ask_plan = getattr(self, "market_screener_ask_plan", None)
    if ask_plan is not None:
        ask_result = execute_ask_screener_plan(filtered, ask_plan)
        self.market_screener_ask_result = ask_result
        filtered = list(ask_result.records)
        if ask_result.total_matched_rows == 0:
            self.market_screener_empty_state_text = ask_result.summary
        else:
            self.market_screener_empty_state_text = ""
    else:
        self.market_screener_ask_result = None
        self.market_screener_empty_state_text = _screener_filter_empty_state_text(self, filtered)
    self.market_screener_filtered_records = sort_market_screener_records(filtered, self.market_screener_sort_column, descending=self.market_screener_sort_desc)
    self.market_screener_page = min(self.market_screener_page, _max_page(self.market_screener_filtered_records, _screener_page_size(self)))
    _populate_screener_table(self)
    _draw_screener_chart(self)


def _screener_filter_empty_state_text(self: tk.Tk, filtered: Iterable[MarketScreenerRecord]) -> str:
    if list(filtered) or self.market_screener_event_type_var.get() != "High volume / mover":
        return ""
    candidate_records = filter_market_screener_records(
        self.market_screener_records,
        search=self.market_screener_search_var.get(),
        sector=self.market_screener_sector_var.get(),
        exchange=self.market_screener_exchange_var.get(),
        event_type="All",
        risk_flag=self.market_screener_risk_flag_var.get(),
        earnings_date_window=self.market_screener_earnings_window_var.get(),
        data_completeness=self.market_screener_data_completeness_var.get(),
        has_ai_signal=self.market_screener_has_ai_signal_var.get(),
        has_price_volume_data=self.market_screener_has_price_volume_data_var.get(),
    )
    return _high_volume_mover_empty_state_text(candidate_records, diagnostics=getattr(getattr(self, "market_screener_last_snapshot", None), "diagnostics", None))


def _high_volume_mover_empty_state_text(records: Iterable[MarketScreenerRecord], *, diagnostics: Any | None = None) -> str:
    rows = list(records)
    if not rows:
        return "High Volume / Mover has no candidates after the active non-mover filters. Clear filters or refresh the screener before applying the mover filter."
    missing_change = sum(1 for record in rows if record.change_percent is None)
    missing_volume = sum(1 for record in rows if record.volume is None)
    missing_avg_volume = sum(1 for record in rows if record.avg_volume is None)
    evaluable_change = len(rows) - missing_change
    evaluable_volume = sum(1 for record in rows if record.volume is not None and record.avg_volume not in (None, 0))
    if missing_change or missing_volume or missing_avg_volume:
        return (
            "High Volume / Mover returned 0 rows because mover fields are sparse: "
            f"{missing_change} of {len(rows)} candidate row(s) are missing change %, "
            f"{missing_volume} are missing current volume, and {missing_avg_volume} are missing avg volume. "
            f"Only {evaluable_change} row(s) can be checked for +/-5% moves and {evaluable_volume} row(s) can be checked for volume >= 1.5x average. "
            "Missing values are not treated as zero. Clear the mover filter, click Enrich Current Page, click Re-enrich Current Page after fixing an API key/plan, or use Refresh Screener to force provider retry. "
            f"{_high_volume_mover_cap_guidance(diagnostics)}"
        )
    return (
        f"High Volume / Mover found 0 rows among {len(rows)} candidate row(s): no change % reached +/-5% and no volume reached 1.5x average volume."
    )


def _high_volume_mover_cap_guidance(diagnostics: Any | None) -> str:
    skipped = int(getattr(diagnostics, "rows_skipped_by_configured_symbol_cap", 0) or 0) if diagnostics is not None else 0
    if skipped:
        return (
            f"Source Diagnostics shows {skipped} row(s) skipped by cap; paid FMP users can raise "
            "MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT up to 1000 for Databento batching and FMP_MARKET_DATA_SYMBOL_LIMIT up to 100 for FMP, then Re-enrich Current Page or Refresh Screener."
        )
    return (
        "If Source Diagnostics shows skipped rows, increase MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT for page scope, DATABENTO_EQUITIES_SYMBOL_LIMIT for Databento batches, and FMP_MARKET_DATA_SYMBOL_LIMIT up to 100 for paid FMP; "
        "otherwise configure Schwab, Databento US Equities, FMP quote/profile, local market data, or fallback quote data before reapplying."
    )


def _populate_screener_table(self: tk.Tk) -> None:
    tree = self.market_screener_table
    tree.delete(*tree.get_children())
    self.market_screener_row_map = {}
    page_size = _screener_page_size(self)
    total, page_count, start = _page_window(self.market_screener_filtered_records, page_size, self.market_screener_page)
    self.market_screener_page = min(self.market_screener_page, page_count - 1)
    for index, record in enumerate(self.market_screener_filtered_records[start : start + page_size]):
        iid = f"market_screener_{start + index}"
        self.market_screener_row_map[iid] = record
        tag = "holding" if market_screener_is_my_holding(record) else "risk" if record.risk_flags else "signal" if market_screener_has_ai_signal(record) else ""
        tags = (tag,) if tag else ()
        tree.insert("", tk.END, iid=iid, values=_screener_values(record), tags=tags)
    self.market_screener_page_var.set(f"Page {self.market_screener_page + 1} / {page_count} - {total} records")
    _on_screener_selection_changed(self)
    empty_state = str(getattr(self, "market_screener_empty_state_text", "") or "").strip()
    if total == 0 and empty_state:
        self.market_screener_page_var.set(f"Page {self.market_screener_page + 1} / {page_count} - 0 records - {empty_state}")
        self.market_screener_status_var.set(empty_state)
        self.market_screener_ai_status_var.set(empty_state)
        _set_var_if_present(self, "market_screener_selected_summary_var", empty_state)
        _set_screener_detail_text(self, empty_state)
    _request_visible_page_market_data_enrichment(self, force_refresh=False)


def _screener_values(record: MarketScreenerRecord) -> tuple[str, ...]:
    return (
        record.symbol or EMPTY_VALUE,
        market_screener_data_label(record),
        market_screener_data_completeness_label(record),
        display_optional_text(record.company_name),
        display_optional_text(record.exchange),
        display_optional_text(record.sector),
        display_optional_text(record.industry),
        _display_market_money(record.price),
        _display_market_percent(record.change_percent),
        _display_market_large_number(record.volume),
        _display_market_large_number(record.avg_volume),
        _display_market_money(record.market_cap),
        _display_market_decimal(record.pe_ratio),
        _display_market_money(record.eps),
        _display_market_percent(record.revenue_growth),
        _display_float_or_shares(record),
        display_optional_text(record.next_earnings_date),
        display_optional_text(record.recent_filing_date),
        display_optional_text(record.recent_filing_type),
        ", ".join(record.signals) if record.signals else EMPTY_VALUE,
        ", ".join(record.risk_flags) if record.risk_flags else EMPTY_VALUE,
        ", ".join(record.sources) if record.sources else EMPTY_VALUE,
    )


def _draw_screener_chart(self: tk.Tk) -> None:
    records = self.market_screener_filtered_records
    _draw_screener_summary_strip(self.market_screener_chart, _screener_summary_groups(records))


def _screener_summary_groups(records: Iterable[MarketScreenerRecord]) -> tuple[tuple[str, dict[str, int]], ...]:
    return (
        ("Portfolio", _counts(records, lambda record: "My Holdings" if market_screener_is_my_holding(record) else "Not held")),
        ("Data", _counts(records, market_screener_data_label)),
        ("Completeness", _counts(records, lambda record: str(market_screener_data_completeness(record)["label"]))),
    )


def _sort_screener(self: tk.Tk, column: str) -> None:
    self.market_screener_sort_desc = not self.market_screener_sort_desc if self.market_screener_sort_column == column else column in SCREENER_NUMERIC_COLUMNS | {"next_earnings", "recent_filing"}
    self.market_screener_sort_column = column
    self.market_screener_page = 0
    _apply_screener_filters(self)


def _turn_screener_page(self: tk.Tk, delta: int) -> None:
    self.market_screener_page = min(max(self.market_screener_page + delta, 0), _max_page(self.market_screener_filtered_records, _screener_page_size(self)))
    _populate_screener_table(self)


def _open_screener_source(self: tk.Tk) -> None:
    record = _selected_screener_record(self, show_message=True, title="Open Source")
    if record is None:
        return
    action_state = _update_screener_row_action_states(self, record)
    if not action_state.get("open_source_enabled"):
        reason = str(action_state.get("source_reason") or "The selected screener row does not have a source URL.")
        _set_var_if_present(self, "market_screener_selected_summary_var", reason)
        messagebox.showinfo("Open Source", reason)
        return
    _open_url(record.source_links[0] if record.source_links else None, "Open Source", "The selected screener row does not have a source URL.")


def _request_visible_page_market_data_enrichment(self: tk.Tk, *, force_refresh: bool = False) -> None:
    rows = list(getattr(self, "market_screener_row_map", {}).values())
    page_symbols = _dedupe_market_data_symbols(record.symbol for record in rows)
    self.market_screener_last_enrichment_symbols = page_symbols
    self.market_screener_last_enrichment_reason = "current page"
    if force_refresh:
        _clear_market_data_attempted_symbols(self, page_symbols)
    symbols = _symbols_needing_market_data_enrichment(self, rows, require_price_or_volume=False)
    if not symbols:
        if page_symbols:
            _append_screener_market_data_status(
                self,
                (
                    f"Market data current page: no provider request needed for {_symbol_list_text(page_symbols)}; "
                    "rows were already complete, cached/attempted this session, running, or lacked provider-usable symbols."
                ),
            )
        return
    cap = _visible_page_market_data_enrichment_cap(self)
    requested = symbols[:cap]
    skipped = tuple(symbol for symbol in page_symbols if symbol not in set(requested))
    _append_screener_market_data_status(
        self,
        (
            f"Market data current page snapshot: page_symbols={_symbol_list_text(page_symbols)}; "
            f"requesting={_symbol_list_text(requested)}; skipped_or_deferred={_symbol_list_text(skipped)}. "
            "Rows may move after enrichment because trusted market-cap ranking and the active sort are reapplied."
        ),
    )
    if len(symbols) > cap:
        _append_screener_market_data_status(
            self,
            (
                f"Market data current page: requesting {cap} of {len(symbols)} current-page symbol(s); "
                "remaining rows can be enriched after increasing MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT or changing pages."
            ),
        )
    _request_market_data_enrichment(
        self,
        requested,
        reason="current page",
        force_refresh=force_refresh,
        max_symbols=cap,
    )


def _request_selected_row_market_data_enrichment(self: tk.Tk, record: MarketScreenerRecord) -> None:
    if not _market_screener_record_needs_market_data_enrichment(record):
        return
    symbols = _symbols_needing_market_data_enrichment(self, [record], require_price_or_volume=True)
    if not symbols:
        return
    _request_market_data_enrichment(
        self,
        symbols[:1],
        reason="selected row",
        force_refresh=False,
        max_symbols=1,
    )


def _request_market_data_enrichment(
    self: tk.Tk,
    symbols: Iterable[str],
    *,
    reason: str,
    force_refresh: bool,
    max_symbols: int,
    mode: str | None = None,
    scope_records: Iterable[MarketScreenerRecord] | None = None,
) -> None:
    requested = _dedupe_market_data_symbols(symbols)[:max_symbols]
    if not requested:
        return
    running_symbols = getattr(self, "market_screener_market_data_running_symbols", set())
    attempted_symbols = getattr(self, "market_screener_market_data_attempted_symbols", set())
    if not isinstance(running_symbols, set) or not isinstance(attempted_symbols, set):
        return
    if force_refresh:
        attempted_symbols.difference_update(requested)
        running_symbols.difference_update(requested)
    running_symbols.update(requested)
    backfill_config = market_screener_backfill_config_from_env()
    provider = configured_market_data_provider(
        schwab_session=getattr(self, "schwab_session", None),
        include_fallback_provider=True,
        fmp_symbol_limit=max(backfill_config.profile_limit, backfill_config.quote_limit, backfill_config.fundamental_limit),
        databento_symbol_limit=backfill_config.databento_limit,
        cache_ttl_seconds=backfill_config.cache_ttl_seconds,
        batch_size=backfill_config.batch_size,
    )
    effective_mode = mode or (MARKET_SCREENER_ENRICH_SELECTED_ROW if reason == "selected row" else MARKET_SCREENER_ENRICH_VISIBLE_PAGE)
    effective_scope_records = tuple(scope_records) if scope_records is not None else _market_data_scope_records_for_symbols(self, requested)
    self.market_screener_status_var.set(f"Enriching {reason} market data for {len(requested)} symbol(s)...")

    def worker() -> None:
        try:
            result = enrich_market_screener_records(
                getattr(self, "market_screener_records", ()) or (),
                effective_mode,
                provider=provider,
                scope_records=effective_scope_records,
                symbols=requested,
                force_refresh=force_refresh,
            )
        except Exception as exc:
            self.after(0, lambda error=exc, symbols=requested, why=reason: _finish_screener_market_data_enrichment_error(self, symbols, why, error))
            return
        self.after(0, lambda loaded=result, symbols=requested, why=reason: _finish_screener_market_data_enrichment(self, symbols, why, loaded))

    threading.Thread(target=worker, daemon=True).start()


def _finish_screener_market_data_enrichment(
    self: tk.Tk,
    requested_symbols: tuple[str, ...],
    reason: str,
    result: MarketScreenerEnrichmentResult | MarketQuoteFundamentalsSnapshot,
) -> None:
    _mark_market_data_enrichment_finished(self, requested_symbols)
    selected_symbol = _selected_screener_symbol(self)
    before_order = _screener_symbol_order(getattr(self, "market_screener_filtered_records", ()) or ())
    if isinstance(result, MarketQuoteFundamentalsSnapshot):
        if result.records:
            self.market_screener_records = merge_market_data_records_into_screener_records(
                self.market_screener_records,
                result.records,
                fetched_at=result.fetched_at,
            )
            attempted_symbols = getattr(self, "market_screener_market_data_attempted_symbols", set())
            if isinstance(attempted_symbols, set):
                attempted_symbols.update(_dedupe_market_data_symbols(record.symbol for record in result.records))
            _apply_screener_filters(self)
            if selected_symbol:
                _select_screener_symbol(self, selected_symbol)
                _update_screener_detail_panel(self, _record_by_symbol(self.market_screener_records, selected_symbol))
        resort_note = _post_enrichment_resort_note(self, before_order, requested_symbols)
        _append_screener_market_data_status(
            self,
            (
                f"Market data {reason}: requested {len(requested_symbols)} symbol(s) {_symbol_list_text(requested_symbols)}; "
                f"enriched {len(result.records)}. {_market_data_provider_status_summary(result)} {resort_note} Missing fields are not inferred."
            ),
        )
        signals = sum(1 for record in self.market_screener_records if market_screener_has_ai_signal(record))
        holdings = sum(1 for record in self.market_screener_records if market_screener_is_my_holding(record))
        self.market_screener_status_var.set(f"Loaded {len(self.market_screener_records)} screener rows; {holdings} My Holdings; {signals} with AI-worthy signals. Market data {reason} updated.")
        return
    if result.records:
        self.market_screener_records = list(result.records)
        attempted_symbols = getattr(self, "market_screener_market_data_attempted_symbols", set())
        if isinstance(attempted_symbols, set):
            attempted_symbols.update(result.requested_symbols)
        _apply_screener_filters(self)
        if selected_symbol:
            _select_screener_symbol(self, selected_symbol)
            _update_screener_detail_panel(self, _record_by_symbol(self.market_screener_records, selected_symbol))
    resort_note = _post_enrichment_resort_note(self, before_order, requested_symbols)
    _append_screener_market_data_status(
        self,
        (
            f"Market data {reason}: requested {len(result.requested_symbols)} symbol(s) {_symbol_list_text(result.requested_symbols)}; "
            f"updated {result.report.rows_updated} row(s). {_market_data_enrichment_status_summary(result)} {resort_note} Missing fields are not inferred."
        ),
    )
    signals = sum(1 for record in self.market_screener_records if market_screener_has_ai_signal(record))
    holdings = sum(1 for record in self.market_screener_records if market_screener_is_my_holding(record))
    self.market_screener_status_var.set(f"Loaded {len(self.market_screener_records)} screener rows; {holdings} My Holdings; {signals} with AI-worthy signals. Market data {reason} updated.")


def _finish_screener_market_data_enrichment_error(self: tk.Tk, requested_symbols: tuple[str, ...], reason: str, error: Exception) -> None:
    _mark_market_data_enrichment_finished(self, requested_symbols)
    _append_screener_market_data_status(self, f"Market data {reason}: provider error for {len(requested_symbols)} symbol(s): {error}")
    self.market_screener_status_var.set(f"Market data {reason} enrichment failed: {error}")


def _mark_market_data_enrichment_finished(self: tk.Tk, requested_symbols: Iterable[str]) -> None:
    requested = _dedupe_market_data_symbols(requested_symbols)
    running_symbols = getattr(self, "market_screener_market_data_running_symbols", set())
    attempted_symbols = getattr(self, "market_screener_market_data_attempted_symbols", set())
    if isinstance(running_symbols, set):
        running_symbols.difference_update(requested)
    if isinstance(attempted_symbols, set):
        attempted_symbols.update(requested)


def _symbols_needing_market_data_enrichment(
    self: tk.Tk,
    records: Iterable[MarketScreenerRecord],
    *,
    require_price_or_volume: bool,
) -> tuple[str, ...]:
    attempted_symbols = getattr(self, "market_screener_market_data_attempted_symbols", set())
    running_symbols = getattr(self, "market_screener_market_data_running_symbols", set())
    attempted = attempted_symbols if isinstance(attempted_symbols, set) else set()
    running = running_symbols if isinstance(running_symbols, set) else set()
    symbols: list[str] = []
    for record in records:
        symbol = _normalize_screener_symbol(record.symbol)
        if not symbol or symbol in attempted or symbol in running:
            continue
        needs_data = _market_screener_record_needs_market_data_enrichment(record)
        if require_price_or_volume:
            needs_data = needs_data or record.price is None or record.volume is None
        if needs_data:
            symbols.append(symbol)
    return _dedupe_market_data_symbols(symbols)


def _market_screener_record_needs_market_data_enrichment(record: MarketScreenerRecord) -> bool:
    rank = market_screener_market_cap_rank(record)
    cap_needs_trust_enrichment = record.market_cap is not None and rank.category in {
        "untrusted_non_usd",
        "untrusted_non_primary",
        "untrusted_explicit",
    }
    return bool(_market_screener_enrichment_missing_fields(record) or cap_needs_trust_enrichment)


def _market_screener_enrichment_missing_fields(record: MarketScreenerRecord) -> tuple[str, ...]:
    fields = (
        "price",
        "change_percent",
        "volume",
        "avg_volume",
        "market_cap",
        "pe_ratio",
        "eps",
        "revenue_growth",
        "shares_float",
        "shares_outstanding",
        "sector",
        "industry",
    )
    return tuple(field for field in fields if getattr(record, field, None) in (None, ""))


def _clear_market_data_attempted_symbols(self: tk.Tk, symbols: Iterable[str]) -> None:
    attempted_symbols = getattr(self, "market_screener_market_data_attempted_symbols", set())
    if isinstance(attempted_symbols, set):
        attempted_symbols.difference_update(_dedupe_market_data_symbols(symbols))


def _visible_page_market_data_enrichment_cap(self: tk.Tk) -> int:
    try:
        page_size = _screener_page_size(self)
    except Exception:
        page_size = MARKET_DATA_PAGE_ENRICHMENT_CAP
    configured_limit = configured_market_data_symbol_limit(default=page_size)
    return max(1, min(1000, max(page_size, configured_limit, MARKET_DATA_PAGE_ENRICHMENT_CAP)))


def _reset_screener_market_data_enrichment_state(self: tk.Tk) -> None:
    self.market_screener_market_data_status_lines = []
    self.market_screener_last_enrichment_symbols = ()
    self.market_screener_last_enrichment_reason = ""
    self.market_screener_market_data_attempted_symbols = set()
    self.market_screener_market_data_running_symbols = set()


def _market_data_symbols_from_records(records: Iterable[MarketScreenerRecord]) -> tuple[str, ...]:
    return _dedupe_market_data_symbols(record.symbol for record in records if not _market_screener_record_needs_market_data_enrichment(record))


def _market_data_scope_records_for_symbols(self: tk.Tk, symbols: Iterable[str]) -> tuple[MarketScreenerRecord, ...]:
    wanted = set(_dedupe_market_data_symbols(symbols))
    rows = list(getattr(self, "market_screener_row_map", {}).values()) or list(getattr(self, "market_screener_records", ()) or ())
    return tuple(record for record in rows if _normalize_screener_symbol(record.symbol) in wanted)


def _screener_symbol_order(records: Iterable[MarketScreenerRecord]) -> tuple[str, ...]:
    return _dedupe_market_data_symbols(record.symbol for record in records)


def _post_enrichment_resort_note(self: tk.Tk, before_order: Iterable[str], requested_symbols: Iterable[str]) -> str:
    before = list(_dedupe_market_data_symbols(before_order))
    after = list(_screener_symbol_order(getattr(self, "market_screener_filtered_records", ()) or ()))
    requested = set(_dedupe_market_data_symbols(requested_symbols))
    if not requested or not before or not after:
        return "Rows may move after enrichment because the active sort is reapplied."
    before_index = {symbol: index for index, symbol in enumerate(before)}
    after_index = {symbol: index for index, symbol in enumerate(after)}
    moved: list[str] = []
    for symbol in sorted(requested):
        if symbol in before_index and symbol in after_index and before_index[symbol] != after_index[symbol]:
            moved.append(f"{symbol} {before_index[symbol] + 1}->{after_index[symbol] + 1}")
    if moved:
        return (
            "Post-enrichment resort moved requested row(s): "
            f"{', '.join(moved[:8])}. "
            "Reason: trusted market-cap ranking and the active sort were reapplied."
        )
    return "Post-enrichment resort reapplied; requested rows kept their relative positions in the filtered list."


def _symbol_list_text(symbols: Iterable[str], *, limit: int = 24) -> str:
    rows = list(_dedupe_market_data_symbols(symbols))
    if not rows:
        return "[]"
    suffix = f", ... +{len(rows) - limit}" if len(rows) > limit else ""
    return "[" + ", ".join(rows[:limit]) + suffix + "]"


def _dedupe_market_data_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        clean = _normalize_screener_symbol(symbol)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return tuple(result)


def _normalize_screener_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper().replace("/", ".")
    return symbol if symbol and len(symbol) <= 16 else ""


def _selected_screener_symbol(self: tk.Tk) -> str:
    record = _selected_screener_record(self, show_message=False)
    return _normalize_screener_symbol(record.symbol if record is not None else "")


def _record_by_symbol(records: Iterable[MarketScreenerRecord], symbol: str) -> MarketScreenerRecord | None:
    clean = _normalize_screener_symbol(symbol)
    for record in records:
        if _normalize_screener_symbol(record.symbol) == clean:
            return record
    return None


def _select_screener_symbol(self: tk.Tk, symbol: str) -> None:
    clean = _normalize_screener_symbol(symbol)
    tree = getattr(self, "market_screener_table", None)
    if tree is None or not clean:
        return
    for iid, record in getattr(self, "market_screener_row_map", {}).items():
        if _normalize_screener_symbol(record.symbol) != clean:
            continue
        try:
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)
        except tk.TclError:
            pass
        return


def _market_data_enrichment_status_summary(result: MarketScreenerEnrichmentResult) -> str:
    if not result.statuses:
        return ""
    sources = ", ".join(dict.fromkeys(status.source for status in result.statuses if status.source))
    statuses = ", ".join(dict.fromkeys(status.status for status in result.statuses if status.status))
    provider_message = next((status.message for status in result.statuses if "FMP" in status.source and status.message), "")
    note = f" {_truncate(provider_message, 240)}" if provider_message else ""
    coverage = (
        f" Coverage: price {result.report.rows_with_price}/{result.report.total_rows}, "
        f"volume {result.report.rows_with_volume}/{result.report.total_rows}, "
        f"fundamentals {result.report.rows_with_fundamentals}/{result.report.total_rows}."
    )
    if sources and statuses:
        return f"Provider status: {sources} ({statuses}).{note}{coverage}"
    if sources:
        return f"Provider status: {sources}.{note}{coverage}"
    return coverage


def _market_data_provider_status_summary(snapshot: MarketQuoteFundamentalsSnapshot) -> str:
    if not snapshot.statuses:
        return ""
    sources = ", ".join(dict.fromkeys(status.source for status in snapshot.statuses if status.source))
    statuses = ", ".join(dict.fromkeys(status.status for status in snapshot.statuses if status.status))
    fmp_message = next((status.message for status in snapshot.statuses if status.source == "FMP quote/fundamentals" and status.message), "")
    note = f" {_truncate(fmp_message, 240)}" if fmp_message else ""
    if sources and statuses:
        return f"Provider status: {sources} ({statuses}).{note}"
    if sources:
        return f"Provider status: {sources}.{note}"
    return ""


def _refresh_recent(self: tk.Tk, *, force_refresh: bool) -> None:
    if getattr(self, "_earnings_recent_refreshing", False):
        return
    self._earnings_recent_refreshing = True
    self.earnings_recent_status_var.set("Loading recent SEC earnings filings...")
    parse_documents = bool(self.earnings_recent_parse_documents_var.get())
    per_form_limit = _safe_int(self.earnings_recent_limit_var.get(), default=100, minimum=20, maximum=100)

    def worker() -> None:
        try:
            snapshot = fetch_recent_earnings_snapshot(SecEdgarClient(), per_form_limit=per_form_limit, force_refresh=force_refresh, parse_documents=parse_documents)
        except Exception as exc:
            self.after(0, lambda error=exc: _finish_recent_error(self, error))
            return
        self.after(0, lambda loaded=snapshot: _finish_recent_success(self, loaded))

    threading.Thread(target=worker, daemon=True).start()


def _finish_recent_success(self: tk.Tk, snapshot: EarningsRadarSnapshot) -> None:
    self._earnings_recent_refreshing = False
    _load_recent_snapshot(self, snapshot)
    source = "cache" if snapshot.used_cache else "SEC"
    warnings = f" ({len(snapshot.errors)} nonblocking warnings)" if snapshot.errors else ""
    self.earnings_recent_status_var.set(f"Loaded {len(snapshot.recent)} earnings filings from {source}. Fetched {snapshot.fetched_at}.{warnings}")
    if hasattr(self, "market_screener_table"):
        _refresh_screener(self, force_refresh=False)


def _finish_recent_error(self: tk.Tk, error: Exception) -> None:
    self._earnings_recent_refreshing = False
    self.earnings_recent_status_var.set(f"SEC refresh failed: {error}")


def _refresh_upcoming(self: tk.Tk, *, force_refresh: bool) -> None:
    if getattr(self, "_earnings_upcoming_refreshing", False):
        return
    self._earnings_upcoming_refreshing = True
    self.earnings_upcoming_status_var.set("Loading upcoming earnings calendar...")
    horizon = self.earnings_upcoming_horizon_var.get()
    symbols = _symbol_list(self.earnings_upcoming_symbols_var.get())

    def worker() -> None:
        client = configured_upcoming_earnings_provider()
        try:
            records = client.upcoming_earnings(horizon=horizon, symbols=symbols, force_refresh=force_refresh)
        except Exception as exc:
            self.after(0, lambda error=exc: _finish_upcoming_error(self, error))
            return
        self.after(0, lambda loaded=records, status=client.last_status: _finish_upcoming_success(self, loaded, status))

    threading.Thread(target=worker, daemon=True).start()


def _finish_upcoming_success(self: tk.Tk, records: list[UpcomingEarningsRecord], status: str) -> None:
    self._earnings_upcoming_refreshing = False
    self.earnings_upcoming_records = list(records)
    self.earnings_upcoming_status_var.set(status)
    self.earnings_upcoming_page = 0
    _apply_upcoming_filters(self)
    if hasattr(self, "market_screener_table"):
        _refresh_screener(self, force_refresh=False)


def _finish_upcoming_error(self: tk.Tk, error: Exception) -> None:
    self._earnings_upcoming_refreshing = False
    self.earnings_upcoming_status_var.set(f"Calendar refresh failed: {error}")


def _load_recent_snapshot(self: tk.Tk, snapshot: EarningsRadarSnapshot) -> None:
    self.earnings_recent_records = list(snapshot.recent)
    _set_combo_values(self.earnings_recent_item_combo, ["All", *sorted({record.items for record in self.earnings_recent_records if record.items})], self.earnings_recent_item_var)
    _set_combo_values(self.earnings_recent_sector_combo, ["All", *sorted({record.sector or EMPTY_VALUE for record in self.earnings_recent_records})], self.earnings_recent_sector_var)
    _set_combo_values(self.earnings_recent_exchange_combo, ["All", *sorted({record.exchange or EMPTY_VALUE for record in self.earnings_recent_records})], self.earnings_recent_exchange_var)
    _set_combo_values(self.earnings_recent_risk_flag_combo, ["All", "Any risk flag", *sorted({flag for record in self.earnings_recent_records for flag in record.risk_flags})], self.earnings_recent_risk_flag_var)
    self.earnings_recent_page = 0
    _apply_recent_filters(self)


def _apply_recent_filters(self: tk.Tk) -> None:
    guidance_text = self.earnings_recent_guidance_var.get()
    guidance = True if guidance_text == "Mentioned" else False if guidance_text == "Not mentioned" else None
    filtered = filter_recent_earnings_records(
        self.earnings_recent_records,
        search=self.earnings_recent_search_var.get(),
        form=self.earnings_recent_form_var.get(),
        item=self.earnings_recent_item_var.get(),
        sector=self.earnings_recent_sector_var.get(),
        exchange=self.earnings_recent_exchange_var.get(),
        risk_flag=self.earnings_recent_risk_flag_var.get(),
        date_from=self.earnings_recent_date_from_var.get(),
        date_to=self.earnings_recent_date_to_var.get(),
        has_exhibit=self.earnings_recent_has_exhibit_var.get(),
        guidance=guidance,
    )
    self.earnings_recent_filtered_records = _sorted(filtered, self.earnings_recent_sort_column, self.earnings_recent_sort_desc, _recent_sort_value)
    self.earnings_recent_page = min(self.earnings_recent_page, _max_page(self.earnings_recent_filtered_records, _recent_page_size(self)))
    _populate_recent_table(self)
    _draw_recent_chart(self)


def _apply_upcoming_filters(self: tk.Tk) -> None:
    filtered = filter_upcoming_earnings_records(
        self.earnings_upcoming_records,
        search=self.earnings_upcoming_search_var.get(),
        date_from=self.earnings_upcoming_date_from_var.get(),
        date_to=self.earnings_upcoming_date_to_var.get(),
        has_estimate=self.earnings_upcoming_has_estimate_var.get(),
        symbols=_symbol_list(self.earnings_upcoming_symbols_var.get()),
    )
    self.earnings_upcoming_filtered_records = _sorted(filtered, self.earnings_upcoming_sort_column, self.earnings_upcoming_sort_desc, _upcoming_sort_value)
    self.earnings_upcoming_page = min(self.earnings_upcoming_page, _max_page(self.earnings_upcoming_filtered_records, _upcoming_page_size(self)))
    _populate_upcoming_table(self)
    _draw_upcoming_chart(self)


def _populate_recent_table(self: tk.Tk) -> None:
    tree = self.earnings_recent_table
    tree.delete(*tree.get_children())
    self.earnings_recent_row_map = {}
    page_size = _recent_page_size(self)
    total, page_count, start = _page_window(self.earnings_recent_filtered_records, page_size, self.earnings_recent_page)
    self.earnings_recent_page = min(self.earnings_recent_page, page_count - 1)
    for index, record in enumerate(self.earnings_recent_filtered_records[start : start + page_size]):
        iid = f"earnings_recent_{start + index}"
        self.earnings_recent_row_map[iid] = record
        tree.insert("", tk.END, iid=iid, values=_recent_values(record), tags=("kind_formal" if record.filing_type == FORMAL_REPORT_KIND else "kind_drop",))
    self.earnings_recent_page_var.set(f"Page {self.earnings_recent_page + 1} / {page_count} - {total} records")
    if hasattr(self, "earnings_recent_detail_text"):
        _on_recent_selection_changed(self)


def _populate_upcoming_table(self: tk.Tk) -> None:
    tree = self.earnings_upcoming_table
    tree.delete(*tree.get_children())
    self.earnings_upcoming_row_map = {}
    page_size = _upcoming_page_size(self)
    total, page_count, start = _page_window(self.earnings_upcoming_filtered_records, page_size, self.earnings_upcoming_page)
    self.earnings_upcoming_page = min(self.earnings_upcoming_page, page_count - 1)
    for index, record in enumerate(self.earnings_upcoming_filtered_records[start : start + page_size]):
        iid = f"earnings_upcoming_{start + index}"
        self.earnings_upcoming_row_map[iid] = record
        tree.insert("", tk.END, iid=iid, values=_upcoming_values(record))
    self.earnings_upcoming_page_var.set(f"Page {self.earnings_upcoming_page + 1} / {page_count} - {total} records")


def _recent_values(record: RecentEarningsRecord) -> tuple[str, ...]:
    return (
        record.company_name,
        display_optional_text(record.ticker),
        record.cik,
        record.form,
        record.filing_type,
        record.items or EMPTY_VALUE,
        record.filed_date or EMPTY_VALUE,
        record.acceptance_datetime or EMPTY_VALUE,
        display_optional_text(record.fiscal_period),
        f"{display_optional_text(record.sector)} / {display_optional_text(record.sic)}",
        display_optional_text(record.industry),
        display_optional_text(record.exchange),
        display_money(record.revenue),
        display_percent(record.revenue_growth),
        display_money(record.eps),
        display_money(record.net_income),
        "Yes" if record.guidance_flag else EMPTY_VALUE,
        ", ".join(record.risk_flags) if record.risk_flags else EMPTY_VALUE,
        record.filing_url,
        record.exhibit_url or EMPTY_VALUE,
    )


def _upcoming_values(record: UpcomingEarningsRecord) -> tuple[str, ...]:
    return (
        record.symbol,
        display_optional_text(record.company_name),
        record.report_date or EMPTY_VALUE,
        display_optional_text(record.fiscal_date_ending),
        display_money(record.estimate),
        display_optional_text(record.currency),
        record.source,
        record.source_url or EMPTY_VALUE,
    )


def _draw_recent_chart(self: tk.Tk) -> None:
    records = self.earnings_recent_filtered_records
    _draw_grouped_chart(
        self.earnings_recent_chart,
        (
            ("Form / item", _counts(records, lambda record: f"{record.form} / {record.items[:18]}")),
            ("Sector", _counts(records, lambda record: record.sector or "Unknown")),
            ("Date", _counts(records, lambda record: (record.filed_date or "0000-00-00")[:10])),
        ),
    )


def _draw_upcoming_chart(self: tk.Tk) -> None:
    records = self.earnings_upcoming_filtered_records
    _draw_grouped_chart(
        self.earnings_upcoming_chart,
        (
            ("Week", _counts(records, lambda record: _week_label(record.report_date))),
            ("Estimate", _counts(records, lambda record: "Has estimate" if record.estimate is not None else "No estimate")),
            ("Source", _counts(records, lambda record: record.source or "Unknown")),
        ),
    )


def _sort_recent(self: tk.Tk, column: str) -> None:
    self.earnings_recent_sort_desc = not self.earnings_recent_sort_desc if self.earnings_recent_sort_column == column else column in {"filed_date", "acceptance_time", *RECENT_NUMERIC_COLUMNS}
    self.earnings_recent_sort_column = column
    self.earnings_recent_page = 0
    _apply_recent_filters(self)


def _sort_upcoming(self: tk.Tk, column: str) -> None:
    self.earnings_upcoming_sort_desc = not self.earnings_upcoming_sort_desc if self.earnings_upcoming_sort_column == column else column in UPCOMING_NUMERIC_COLUMNS
    self.earnings_upcoming_sort_column = column
    self.earnings_upcoming_page = 0
    _apply_upcoming_filters(self)


def _turn_recent_page(self: tk.Tk, delta: int) -> None:
    self.earnings_recent_page = min(max(self.earnings_recent_page + delta, 0), _max_page(self.earnings_recent_filtered_records, _recent_page_size(self)))
    _populate_recent_table(self)


def _turn_upcoming_page(self: tk.Tk, delta: int) -> None:
    self.earnings_upcoming_page = min(max(self.earnings_upcoming_page + delta, 0), _max_page(self.earnings_upcoming_filtered_records, _upcoming_page_size(self)))
    _populate_upcoming_table(self)


def _open_recent_filing(self: tk.Tk) -> None:
    record = _selected(self.earnings_recent_table, self.earnings_recent_row_map, "Open SEC Filing", "Select an earnings row first.")
    if record is not None:
        _open_url(record.filing_url, "Open SEC Filing", "The selected row does not have an SEC filing URL.")


def _open_recent_exhibit(self: tk.Tk) -> None:
    record = _selected(self.earnings_recent_table, self.earnings_recent_row_map, "Open Earnings Exhibit", "Select an earnings row first.")
    if record is not None:
        _open_url(record.exhibit_url, "Open Earnings Exhibit", "The selected row does not have an earnings exhibit URL.")


def _screener_row_action_state(record: MarketScreenerRecord | None) -> dict[str, Any]:
    if record is None:
        return {
            "open_source_enabled": False,
            "open_symbol_chat_enabled": False,
            "context_enabled": False,
            "source_reason": "Select a screener row before opening a source URL.",
            "symbol_chat_reason": "Select a screener row with a usable ticker before opening Symbol Chat.",
            "context_reason": "Select a screener row before opening selected context and AI actions.",
            "summary": "No screener row selected. Row actions are disabled.",
        }
    symbol = _normalize_screener_symbol(record.symbol)
    has_source = bool(record.source_links and record.source_links[0])
    has_symbol = bool(symbol and not symbol.startswith("CIK:"))
    has_context = bool(symbol or record.cik or record.company_name or record.field_provenance or record.sources)
    reasons: list[str] = []
    if not has_source:
        reasons.append("source URL unavailable")
    if not has_symbol:
        reasons.append("Symbol Chat unavailable: no usable ticker")
    if not has_context:
        reasons.append("selected context unavailable")
    return {
        "open_source_enabled": has_source,
        "open_symbol_chat_enabled": has_symbol,
        "context_enabled": has_context,
        "source_reason": "The selected screener row does not have a source URL." if not has_source else "Source URL is available.",
        "symbol_chat_reason": "The selected screener row does not have a usable stock ticker symbol." if not has_symbol else "Symbol Chat is available.",
        "context_reason": "The selected screener row has no usable context fields." if not has_context else "Selected context is available.",
        "summary": "; ".join(reasons) if reasons else "Row actions available.",
    }


def _update_screener_row_action_states(self: tk.Tk, record: MarketScreenerRecord | None) -> dict[str, Any]:
    state = _screener_row_action_state(record)
    for name, enabled_key in (
        ("market_screener_open_source_button", "open_source_enabled"),
        ("market_screener_open_symbol_chat_button", "open_symbol_chat_enabled"),
        ("market_screener_context_button", "context_enabled"),
    ):
        button = getattr(self, name, None)
        if button is None:
            continue
        try:
            button.configure(state=tk.NORMAL if state[enabled_key] else tk.DISABLED)
        except tk.TclError:
            pass
    return state


def _selected_screener_record(self: tk.Tk, *, show_message: bool = False, title: str = "Market Screener") -> MarketScreenerRecord | None:
    table = getattr(self, "market_screener_table", None)
    row_map = getattr(self, "market_screener_row_map", {}) or {}
    if table is None:
        return None
    try:
        selection = table.selection()
    except Exception:
        selection = ()
    if not selection:
        if show_message:
            messagebox.showinfo(title, "Select a screener row first.")
        return None
    record = row_map.get(selection[0]) if isinstance(row_map, dict) else None
    if record is None and show_message:
        messagebox.showinfo(title, "The selected screener row is no longer available. Refresh or select another row.")
    return record


def _on_screener_selection_changed(self: tk.Tk) -> None:
    record = _selected_screener_record(self, show_message=False)
    self.market_screener_selected_record = record
    _update_screener_detail_panel(self, record)
    _refresh_screener_diagnostics_text(self)
    action_state = _update_screener_row_action_states(self, record)
    _set_screener_ai_actions_enabled(self, bool(action_state.get("context_enabled")))
    if record is not None:
        _request_selected_row_market_data_enrichment(self, record)


def _update_screener_detail_panel(self: tk.Tk, record: MarketScreenerRecord | None) -> None:
    if record is None:
        self.market_screener_ai_status_var.set("Select a screener row for row-grounded AI.")
        _set_var_if_present(self, "market_screener_selected_summary_var", "No screener row selected.")
        _set_screener_detail_text(self, "No screener row selected.")
        return
    action_state = _screener_row_action_state(record)
    symbol = record.symbol or record.cik or "selected row"
    signal = ", ".join(record.signals[:3]) if record.signals else "no screener signals"
    risk = f"{len(record.risk_flags)} risk flag(s)" if record.risk_flags else "no risk flags"
    self.market_screener_ai_status_var.set(f"Selected {symbol} - {market_screener_data_label(record)}; {market_screener_data_completeness_label(record)}; {signal}; {risk}.")
    _set_var_if_present(self, "market_screener_selected_summary_var", f"{symbol} - {market_screener_data_label(record)}; {market_screener_data_completeness_label(record)}; {risk}; {action_state['summary']}.")
    _set_screener_detail_text(
        self,
        _screener_detail_text(
            record,
            snapshot=getattr(self, "market_screener_last_snapshot", None),
            session_lines=getattr(self, "market_screener_market_data_status_lines", ()) or (),
        ),
    )


def _set_screener_detail_text(self: tk.Tk, text: str) -> None:
    widget = getattr(self, "market_screener_detail_text", None)
    if widget is None:
        return
    try:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, redact_symbol_chat_secrets(text))
        widget.configure(state=tk.DISABLED)
    except tk.TclError:
        return


def _set_var_if_present(self: tk.Tk, name: str, value: str) -> None:
    variable = getattr(self, name, None)
    if variable is None:
        return
    try:
        variable.set(value)
    except tk.TclError:
        return


def _set_screener_source_text(self: tk.Tk, text: str) -> None:
    widget = getattr(self, "market_screener_source_text", None)
    if widget is None:
        return
    try:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, redact_symbol_chat_secrets(text))
        widget.configure(state=tk.DISABLED)
    except tk.TclError:
        return


def _set_screener_diagnostics_text(self: tk.Tk, text: str) -> None:
    widget = getattr(self, "market_screener_diagnostics_text", None)
    if widget is None:
        return
    try:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, redact_symbol_chat_secrets(text))
        widget.configure(state=tk.DISABLED)
    except tk.TclError:
        return


def _append_screener_market_data_status(self: tk.Tk, line: str) -> None:
    lines = list(getattr(self, "market_screener_market_data_status_lines", []) or [])
    clean = str(line or "").strip()
    if clean:
        lines.append(clean)
        summary_var = getattr(self, "market_screener_source_summary_var", None)
        if summary_var is not None:
            try:
                summary_var.set(_truncate(clean, 240))
            except tk.TclError:
                pass
    self.market_screener_market_data_status_lines = lines[-80:]
    _refresh_screener_source_text(self)


def _refresh_screener_source_text(self: tk.Tk) -> None:
    base = str(getattr(self, "market_screener_source_status_base_text", "") or "").strip()
    lines = list(getattr(self, "market_screener_market_data_status_lines", []) or [])
    if lines:
        session_text = "\n".join(f"- {line}" for line in lines)
        text = f"{base}\nSession enrichment:\n{session_text}" if base else f"Session enrichment:\n{session_text}"
    else:
        text = base
    _set_screener_source_text(self, text or "Source/status: Market Intelligence Screener has not loaded yet.")
    _refresh_screener_diagnostics_text(self)


def _refresh_screener_diagnostics_text(self: tk.Tk) -> None:
    if not _widget_exists(getattr(self, "market_screener_diagnostics_window", None)):
        return
    snapshot = getattr(self, "market_screener_last_snapshot", None)
    record = getattr(self, "market_screener_selected_record", None)
    session_lines = getattr(self, "market_screener_market_data_status_lines", ()) or ()
    _set_screener_diagnostics_text(self, _screener_diagnostics_popout_text(snapshot, selected_record=record, session_lines=session_lines))


def _set_screener_ai_actions_enabled(self: tk.Tk, enabled: bool) -> None:
    running = bool(getattr(self, "_market_screener_ai_running", False))
    action_state = _screener_row_action_state(getattr(self, "market_screener_selected_record", None))
    base_enabled = bool(enabled and not running)
    state = tk.NORMAL if base_enabled else tk.DISABLED
    for name in (
        "market_screener_ai_analyze_button",
        "market_screener_ai_why_button",
        "market_screener_ai_risks_button",
        "market_screener_ai_symbol_chat_button",
    ):
        button = getattr(self, name, None)
        if button is not None:
            try:
                button_state = tk.DISABLED if name == "market_screener_ai_symbol_chat_button" and not action_state.get("open_symbol_chat_enabled") else state
                button.configure(state=button_state)
            except tk.TclError:
                pass
    for button in getattr(self, "market_screener_ai_quick_buttons", []) or []:
        try:
            button.configure(state=state)
        except tk.TclError:
            pass


def _run_screener_ai_prompt(self: tk.Tk, label: str, prompt: str) -> None:
    record = _selected_screener_record(self, show_message=True, title=label)
    if record is None:
        return
    if getattr(self, "_market_screener_ai_running", False):
        self.market_screener_ai_status_var.set("Wait for the current screener AI response to finish.")
        return
    self._market_screener_ai_running = True
    _set_screener_ai_actions_enabled(self, False)
    symbol = record.symbol or record.cik or "selected row"
    self.market_screener_ai_status_var.set(f"{label}: preparing selected {symbol} context...")

    def progress(message: str) -> None:
        _post_to_earnings_ui(self, lambda value=message: self.market_screener_ai_status_var.set(f"{label}: {value}"))

    def worker() -> None:
        try:
            client = _market_screener_ai_client(self)
            response = client.analyze(record, prompt, source_snippets=_source_snippets_for_screener_record(record), progress_callback=progress)
        except Exception as exc:
            _post_to_earnings_ui(self, lambda error=exc: _finish_screener_ai_error(self, label, error))
            return
        _post_to_earnings_ui(self, lambda loaded=response: _finish_screener_ai_success(self, record, label, prompt, loaded))

    threading.Thread(target=worker, daemon=True).start()


def _market_screener_ai_client(self: tk.Tk) -> OpenAiMarketScreenerClient:
    factory = getattr(self, "market_screener_ai_client_factory", None)
    if callable(factory):
        return factory()
    return OpenAiMarketScreenerClient()


def _source_snippets_for_screener_record(record: MarketScreenerRecord) -> tuple[str, ...]:
    source_excerpt = getattr(record, "source_excerpt", None)
    return (str(source_excerpt),) if source_excerpt else ()


def _finish_screener_ai_success(self: tk.Tk, record: MarketScreenerRecord, label: str, prompt: str, response: MarketScreenerAiResponse) -> None:
    self._market_screener_ai_running = False
    _set_screener_ai_actions_enabled(self, _selected_screener_record(self, show_message=False) is not None)
    self.market_screener_ai_status_var.set(f"{label}: response ready for {record.symbol or record.cik or 'selected row'}.")
    _show_screener_ai_result(self, record, label, prompt, response)


def _finish_screener_ai_error(self: tk.Tk, label: str, error: Exception) -> None:
    self._market_screener_ai_running = False
    _set_screener_ai_actions_enabled(self, _selected_screener_record(self, show_message=False) is not None)
    message = redact_symbol_chat_secrets(str(error))
    self.market_screener_ai_status_var.set(f"{label}: OpenAI request failed.")
    messagebox.showerror(f"{label} failed", message)


def _show_screener_ai_result(self: tk.Tk, record: MarketScreenerRecord, label: str, prompt: str, response: MarketScreenerAiResponse) -> None:
    window = getattr(self, "market_screener_ai_result_window", None)
    text_widget = getattr(self, "market_screener_ai_result_text", None)
    if window is None or text_widget is None:
        window = tk.Toplevel(self)
        polished_theme.configure_toplevel(window)
        window.title("Market Screener AI - Selected Row")
        window.geometry("980x720")
        window.minsize(760, 520)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        text_widget = tk.Text(
            window,
            wrap=tk.WORD,
            bg=polished_theme.PANEL,
            fg=polished_theme.TEXT,
            insertbackground=polished_theme.TEXT,
            relief=tk.FLAT,
            padx=12,
            pady=12,
            font=("Segoe UI", 10),
        )
        text_widget.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        scroll = ttk.Scrollbar(window, orient=tk.VERTICAL, command=text_widget.yview)
        scroll.grid(row=0, column=1, sticky="ns", pady=12)
        text_widget.configure(yscrollcommand=scroll.set)

        def _close() -> None:
            self.market_screener_ai_result_window = None
            self.market_screener_ai_result_text = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", _close)
        self.market_screener_ai_result_window = window
        self.market_screener_ai_result_text = text_widget
    text_widget.configure(state=tk.NORMAL)
    text_widget.delete("1.0", tk.END)
    text_widget.insert(tk.END, _format_screener_ai_result(record, label, prompt, response))
    text_widget.configure(state=tk.DISABLED)
    try:
        window.deiconify()
        window.lift()
    except tk.TclError:
        pass


def _open_screener_symbol_chat(self: tk.Tk) -> None:
    record = _selected_screener_record(self, show_message=True, title="Open Symbol Chat")
    if record is None:
        return
    action_state = _update_screener_row_action_states(self, record)
    if not action_state.get("open_symbol_chat_enabled"):
        reason = str(action_state.get("symbol_chat_reason") or "The selected screener row does not have a usable stock ticker symbol.")
        _set_var_if_present(self, "market_screener_selected_summary_var", reason)
        messagebox.showinfo("Open Symbol Chat", reason)
        return
    symbol = str(record.symbol or "").strip().upper()
    if not symbol or symbol.startswith("CIK:"):
        messagebox.showinfo("Open Symbol Chat", "The selected screener row does not have a stock ticker symbol.")
        return
    try:
        open_symbol_chat_window(
            self,
            symbol,
            app_context=self,
            schwab_session=getattr(self, "schwab_session", None),
        )
        self.market_screener_ai_status_var.set(f"Opened Symbol Chat for {symbol}.")
    except Exception as exc:
        messagebox.showerror("Open Symbol Chat failed", redact_symbol_chat_secrets(str(exc)))


def _selected_recent_record(self: tk.Tk, *, show_message: bool = False, title: str = "Earnings Radar") -> RecentEarningsRecord | None:
    table = getattr(self, "earnings_recent_table", None)
    row_map = getattr(self, "earnings_recent_row_map", {}) or {}
    if table is None:
        return None
    try:
        selection = table.selection()
    except Exception:
        selection = ()
    if not selection:
        if show_message:
            messagebox.showinfo(title, "Select a recent earnings row first.")
        return None
    record = row_map.get(selection[0]) if isinstance(row_map, dict) else None
    if record is None and show_message:
        messagebox.showinfo(title, "The selected earnings row is no longer available. Refresh or select another row.")
    return record


def _on_recent_selection_changed(self: tk.Tk) -> None:
    record = _selected_recent_record(self, show_message=False)
    self.earnings_recent_selected_record = record
    _update_recent_detail_panel(self, record)
    _set_recent_ai_actions_enabled(self, record is not None)


def _update_recent_detail_panel(self: tk.Tk, record: RecentEarningsRecord | None) -> None:
    if record is None:
        self.earnings_ai_status_var.set("Select a recent earnings row for row-grounded AI.")
        _set_detail_text(self, "No recent earnings row selected.")
        return
    symbol = record.ticker or record.cik
    risk = f"{len(record.risk_flags)} risk flag(s)" if record.risk_flags else "no parsed risk flags"
    guidance = "guidance mentioned" if record.guidance_flag else "guidance not detected"
    self.earnings_ai_status_var.set(f"Selected {symbol} - {record.form} {record.items or EMPTY_VALUE}; {guidance}; {risk}.")
    _set_detail_text(self, _recent_detail_text(record))


def _set_detail_text(self: tk.Tk, text: str) -> None:
    widget = getattr(self, "earnings_recent_detail_text", None)
    if widget is None:
        return
    try:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, redact_symbol_chat_secrets(text))
        widget.configure(state=tk.DISABLED)
    except tk.TclError:
        return


def _set_recent_ai_actions_enabled(self: tk.Tk, enabled: bool) -> None:
    running = bool(getattr(self, "_earnings_ai_running", False))
    state = tk.NORMAL if enabled and not running else tk.DISABLED
    for name in ("earnings_ai_analyze_button", "earnings_ai_summarize_button", "earnings_ai_symbol_chat_button"):
        button = getattr(self, name, None)
        if button is not None:
            try:
                button.configure(state=state)
            except tk.TclError:
                pass
    for button in getattr(self, "earnings_ai_quick_buttons", []) or []:
        try:
            button.configure(state=state)
        except tk.TclError:
            pass


def _run_earnings_ai_prompt(self: tk.Tk, label: str, prompt: str) -> None:
    record = _selected_recent_record(self, show_message=True, title=label)
    if record is None:
        return
    if getattr(self, "_earnings_ai_running", False):
        self.earnings_ai_status_var.set("Wait for the current Earnings AI response to finish.")
        return
    self._earnings_ai_running = True
    _set_recent_ai_actions_enabled(self, False)
    symbol = record.ticker or record.cik
    self.earnings_ai_status_var.set(f"{label}: preparing selected {symbol} context...")

    def progress(message: str) -> None:
        _post_to_earnings_ui(self, lambda value=message: self.earnings_ai_status_var.set(f"{label}: {value}"))

    def worker() -> None:
        try:
            client = _earnings_ai_client(self)
            response = client.analyze(record, prompt, source_snippets=_source_snippets_for_record(record), progress_callback=progress)
        except Exception as exc:
            _post_to_earnings_ui(self, lambda error=exc: _finish_earnings_ai_error(self, label, error))
            return
        _post_to_earnings_ui(self, lambda loaded=response: _finish_earnings_ai_success(self, record, label, prompt, loaded))

    threading.Thread(target=worker, daemon=True).start()


def _earnings_ai_client(self: tk.Tk) -> OpenAiEarningsRadarClient:
    factory = getattr(self, "earnings_ai_client_factory", None)
    if callable(factory):
        return factory()
    return OpenAiEarningsRadarClient()


def _source_snippets_for_record(record: RecentEarningsRecord) -> tuple[str, ...]:
    source_excerpt = getattr(record, "source_excerpt", None)
    return (str(source_excerpt),) if source_excerpt else ()


def _post_to_earnings_ui(self: tk.Tk, callback: Callable[[], None]) -> None:
    try:
        self.after(0, callback)
    except tk.TclError:
        return


def _finish_earnings_ai_success(self: tk.Tk, record: RecentEarningsRecord, label: str, prompt: str, response: EarningsAiResponse) -> None:
    self._earnings_ai_running = False
    _set_recent_ai_actions_enabled(self, _selected_recent_record(self, show_message=False) is not None)
    self.earnings_ai_status_var.set(f"{label}: response ready for {record.ticker or record.cik}.")
    _show_earnings_ai_result(self, record, label, prompt, response)


def _finish_earnings_ai_error(self: tk.Tk, label: str, error: Exception) -> None:
    self._earnings_ai_running = False
    _set_recent_ai_actions_enabled(self, _selected_recent_record(self, show_message=False) is not None)
    message = redact_symbol_chat_secrets(str(error))
    self.earnings_ai_status_var.set(f"{label}: OpenAI request failed.")
    messagebox.showerror(f"{label} failed", message)


def _show_earnings_ai_result(self: tk.Tk, record: RecentEarningsRecord, label: str, prompt: str, response: EarningsAiResponse) -> None:
    window = getattr(self, "earnings_ai_result_window", None)
    text_widget = getattr(self, "earnings_ai_result_text", None)
    if window is None or text_widget is None:
        window = tk.Toplevel(self)
        polished_theme.configure_toplevel(window)
        window.title("Earnings AI - Selected Row")
        window.geometry("980x720")
        window.minsize(760, 520)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        text_widget = tk.Text(
            window,
            wrap=tk.WORD,
            bg=polished_theme.PANEL,
            fg=polished_theme.TEXT,
            insertbackground=polished_theme.TEXT,
            relief=tk.FLAT,
            padx=12,
            pady=12,
            font=("Segoe UI", 10),
        )
        text_widget.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        scroll = ttk.Scrollbar(window, orient=tk.VERTICAL, command=text_widget.yview)
        scroll.grid(row=0, column=1, sticky="ns", pady=12)
        text_widget.configure(yscrollcommand=scroll.set)

        def _close() -> None:
            self.earnings_ai_result_window = None
            self.earnings_ai_result_text = None
            window.destroy()

        window.protocol("WM_DELETE_WINDOW", _close)
        self.earnings_ai_result_window = window
        self.earnings_ai_result_text = text_widget
    text_widget.configure(state=tk.NORMAL)
    text_widget.delete("1.0", tk.END)
    text_widget.insert(tk.END, _format_earnings_ai_result(record, label, prompt, response))
    text_widget.configure(state=tk.DISABLED)
    try:
        window.deiconify()
        window.lift()
    except tk.TclError:
        pass


def _open_recent_symbol_chat(self: tk.Tk) -> None:
    record = _selected_recent_record(self, show_message=True, title="Open Symbol Chat")
    if record is None:
        return
    symbol = str(record.ticker or "").strip().upper()
    if not symbol:
        messagebox.showinfo("Open Symbol Chat", "The selected earnings row does not have a ticker symbol.")
        return
    try:
        open_symbol_chat_window(
            self,
            symbol,
            app_context=self,
            schwab_session=getattr(self, "schwab_session", None),
        )
        self.earnings_ai_status_var.set(f"Opened Symbol Chat for {symbol}.")
    except Exception as exc:
        messagebox.showerror("Open Symbol Chat failed", redact_symbol_chat_secrets(str(exc)))


def _open_upcoming_source(self: tk.Tk) -> None:
    record = _selected(self.earnings_upcoming_table, self.earnings_upcoming_row_map, "Open Source", "Select an upcoming earnings row first.")
    if record is not None:
        _open_url(record.source_url, "Open Source", "The selected row does not have a source URL.")


def _clear_screener_filters(self: tk.Tk) -> None:
    self.market_screener_search_var.set("")
    self.market_screener_ask_var.set("")
    self.market_screener_ask_plan = None
    self.market_screener_ask_result = None
    self.market_screener_ask_summary_var.set("Ask Screener cleared.")
    self.market_screener_quick_preset_var.set("Quick filter")
    for variable in (
        self.market_screener_sector_var,
        self.market_screener_exchange_var,
        self.market_screener_event_type_var,
        self.market_screener_risk_flag_var,
        self.market_screener_earnings_window_var,
        self.market_screener_data_completeness_var,
    ):
        variable.set("All")
    self.market_screener_has_ai_signal_var.set(False)
    self.market_screener_has_price_volume_data_var.set(False)
    self.market_screener_page = 0
    _apply_screener_filters(self)


def _apply_selected_screener_quick_preset(self: tk.Tk) -> None:
    mapping = {
        "Earnings Soon": "earnings_soon",
        "Recent SEC Filing": "recent_filing",
        "Guidance": "guidance",
        "Risk Flags": "risk",
        "High Volume / Mover": "mover",
        "Quote-enriched": "quote_enriched",
        "Fundamentals": "fundamentals",
        "My Holdings": "holdings",
        "Complete Data": "complete_data",
    }
    preset = mapping.get(self.market_screener_quick_preset_var.get())
    if preset:
        _apply_screener_quick_preset(self, preset)


def _apply_screener_quick_preset(self: tk.Tk, preset: str) -> None:
    self.market_screener_ask_plan = None
    self.market_screener_ask_result = None
    self.market_screener_ask_summary_var.set("Ask Screener cleared for quick filter.")
    if preset == "earnings_soon":
        self.market_screener_event_type_var.set("Upcoming earnings")
        self.market_screener_earnings_window_var.set("Next 30 days")
    elif preset == "recent_filing":
        self.market_screener_event_type_var.set("Recent SEC filing")
        self.market_screener_earnings_window_var.set("All")
    elif preset == "guidance":
        self.market_screener_event_type_var.set("Guidance mentioned")
    elif preset == "risk":
        self.market_screener_event_type_var.set("Risk flags")
        self.market_screener_risk_flag_var.set("Any risk flag")
    elif preset == "mover":
        self.market_screener_event_type_var.set("High volume / mover")
    elif preset == "quote_enriched":
        self.market_screener_event_type_var.set("Quote-enriched")
        self.market_screener_earnings_window_var.set("All")
        self.market_screener_has_price_volume_data_var.set(False)
    elif preset == "fundamentals":
        self.market_screener_event_type_var.set("Fundamentals available")
        self.market_screener_earnings_window_var.set("All")
        self.market_screener_has_price_volume_data_var.set(False)
    elif preset == "holdings":
        self.market_screener_event_type_var.set("My Holdings")
        self.market_screener_data_completeness_var.set("All")
    elif preset == "complete_data":
        self.market_screener_event_type_var.set("All")
        self.market_screener_data_completeness_var.set("High completeness (>=75%)")
    self.market_screener_page = 0
    _apply_screener_filters(self)


def _clear_recent_filters(self: tk.Tk) -> None:
    for variable in (
        self.earnings_recent_search_var,
        self.earnings_recent_date_from_var,
        self.earnings_recent_date_to_var,
    ):
        variable.set("")
    for variable in (
        self.earnings_recent_form_var,
        self.earnings_recent_item_var,
        self.earnings_recent_sector_var,
        self.earnings_recent_exchange_var,
        self.earnings_recent_risk_flag_var,
        self.earnings_recent_guidance_var,
    ):
        variable.set("All")
    self.earnings_recent_has_exhibit_var.set(False)
    self.earnings_recent_page = 0
    _apply_recent_filters(self)


def _apply_recent_quick_preset(self: tk.Tk, preset: str) -> None:
    if preset == "last7":
        self.earnings_recent_date_from_var.set((datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
        self.earnings_recent_date_to_var.set("")
    elif preset == "item202":
        self.earnings_recent_form_var.set("8-K")
        self.earnings_recent_item_var.set("2.02")
    elif preset == "guidance":
        self.earnings_recent_guidance_var.set("Mentioned")
    elif preset == "risk":
        self.earnings_recent_risk_flag_var.set("Any risk flag")
    elif preset == "exhibit":
        self.earnings_recent_has_exhibit_var.set(True)
    elif preset == "formal":
        self.earnings_recent_form_var.set("All")
        self.earnings_recent_item_var.set("Formal report")
    self.earnings_recent_page = 0
    _apply_recent_filters(self)


def _clear_upcoming_filters(self: tk.Tk) -> None:
    for variable in (
        self.earnings_upcoming_search_var,
        self.earnings_upcoming_symbols_var,
        self.earnings_upcoming_date_from_var,
        self.earnings_upcoming_date_to_var,
    ):
        variable.set("")
    self.earnings_upcoming_has_estimate_var.set(False)
    self.earnings_upcoming_page = 0
    _apply_upcoming_filters(self)


def _screener_detail_text(
    record: MarketScreenerRecord,
    *,
    snapshot: MarketScreenerSnapshot | None = None,
    session_lines: Iterable[str] = (),
) -> str:
    completeness = market_screener_data_completeness(record)
    market_cap_rank = market_screener_market_cap_rank(record)
    lines = [
        f"{record.symbol or EMPTY_VALUE} | {display_optional_text(record.company_name)} | CIK {display_optional_text(record.cik)}",
        f"Data: {market_screener_data_label(record)} | Completeness: {completeness['score']}% {str(completeness['label']).lower()} | Provenance fields: {len(record.field_provenance)}",
        f"Exchange: {display_optional_text(record.exchange)} | Sector: {display_optional_text(record.sector)} | Industry: {display_optional_text(record.industry)}",
        (
            "Market fields: "
            f"Price {_display_market_money(record.price)} | "
            f"Change {_display_market_percent(record.change_percent)} | "
            f"Volume {_display_market_large_number(record.volume)} / Avg {_display_market_large_number(record.avg_volume)} | "
            f"Market cap {_display_market_money(record.market_cap)} | P/E {_display_market_decimal(record.pe_ratio)}"
        ),
        (
            "Fundamentals/events: "
            f"EPS {_display_market_money(record.eps)} | "
            f"Revenue growth {_display_market_percent(record.revenue_growth)} | "
            f"Float/Shares {_display_float_or_shares(record)} | "
            f"Next earnings {display_optional_text(record.next_earnings_date)} | "
            f"Recent filing {display_optional_text(record.recent_filing_date)} {display_optional_text(record.recent_filing_type)}"
        ),
        f"Signals: {', '.join(record.signals) if record.signals else EMPTY_VALUE}",
        f"Risk flags: {', '.join(record.risk_flags) if record.risk_flags else EMPTY_VALUE}",
        f"Sources: {', '.join(record.sources) if record.sources else EMPTY_VALUE}",
        (
            "Market cap ranking: "
            f"display {_display_market_money(market_cap_rank.display_market_cap)} | "
            f"ranking {_display_market_money(market_cap_rank.ranking_market_cap)} {market_cap_rank.currency} | "
            f"{'trusted' if market_cap_rank.trusted else 'untrusted'} | {market_cap_rank.reason}"
        ),
    ]
    missing_families = tuple(completeness.get("missing_source_families", ()) or ())
    if missing_families:
        lines.insert(2, "Missing source families: " + ", ".join(str(family) for family in missing_families))
    if market_screener_is_my_holding(record):
        lines.append(
            "Portfolio: "
            f"Qty {_display_market_decimal(record.portfolio_quantity)} | "
            f"Avg cost {_display_market_money(record.portfolio_average_cost)} | "
            f"Value {_display_market_money(record.portfolio_market_value)} | "
            f"uPnL {_display_market_money(record.portfolio_unrealized_pnl)} | "
            f"Weight {_display_market_percent((record.portfolio_weight or 0) * 100 if record.portfolio_weight is not None else None)}"
        )
    if record.source_links:
        lines.append("Source links: " + " | ".join(record.source_links[:4]))
    missing_reasons = _selected_screener_missing_reason_lines(record, snapshot=snapshot, session_lines=session_lines)
    if missing_reasons:
        lines.append("Source-aware missing reasons:")
        lines.extend(f"- {reason}" for reason in missing_reasons)
    if record.field_provenance:
        lines.append("Field provenance:")
        ordered_provenance = _ordered_screener_field_provenance(record)
        for item in ordered_provenance[:12]:
            detail = f" ({item.source_detail})" if item.source_detail else ""
            link = f" | {item.source_link}" if item.source_link else ""
            lines.append(f"- {item.field}: {item.source}{detail}{link}")
        if len(ordered_provenance) > 12:
            lines.append(f"- ... {len(ordered_provenance) - 12} more field provenance row(s).")
    source_excerpt = str(getattr(record, "source_excerpt", "") or "").strip()
    if source_excerpt:
        lines.append(f"Source excerpt: {_truncate(source_excerpt, 520)}")
    return "\n".join(lines)


def _selected_screener_missing_reason_lines(
    record: MarketScreenerRecord,
    *,
    snapshot: MarketScreenerSnapshot | None = None,
    session_lines: Iterable[str] = (),
) -> list[str]:
    reasons = list(market_screener_record_missing_reason_lines(record))
    record_has_market_blanks = (
        record.price is None
        or record.volume is None
        or record.avg_volume is None
        or record.change_percent is None
        or record.market_cap is None
        or record.pe_ratio is None
        or record.eps is None
        or record.revenue_growth is None
        or record.shares_float is None
        or record.shares_outstanding is None
        or not record.sector
        or not record.industry
        or not market_screener_record_has_fundamentals(record)
    )
    if snapshot is not None:
        diagnostics = snapshot.diagnostics
        if diagnostics.rows_skipped_by_configured_symbol_cap:
            reasons.append(
                "skipped by cap: "
                f"{diagnostics.rows_skipped_by_configured_symbol_cap} row(s) were outside the configured provider symbol cap; "
                "this selected row may require selected-row enrichment, a higher MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT, a higher DATABENTO_EQUITIES_SYMBOL_LIMIT, or the next current-page batch."
            )
        if diagnostics.provider_unavailable:
            reasons.append(
                "provider unavailable: "
                f"{diagnostics.provider_unavailable} provider status row(s) were unavailable or errored; see Source Diagnostics for the exact provider."
            )
        if diagnostics.rows_provider_returned_no_usable_data:
            reasons.append(
                "provider returned no usable data: "
                f"{diagnostics.rows_provider_returned_no_usable_data} requested row(s) came back without fields the screener can merge."
            )
        if diagnostics.rows_blocked_by_provider_plan_rate_auth_limit:
            reasons.append(
                "provider auth/plan/rate limit: "
                f"{diagnostics.rows_blocked_by_provider_plan_rate_auth_limit} provider attempt(s) were blocked; paid FMP helps FMP quote/profile/profile-by-CIK limits, while Databento limits require Databento dataset/schema/entitlement fixes."
            )
        for status in snapshot.statuses:
            if _screener_status_mentions_missing_config(status):
                reasons.append(f"provider disabled/missing config detail: {status.source} {status.status} - {status.message}")
            elif _screener_status_mentions_auth_plan_rate(status):
                reasons.append(f"FMP/provider auth-plan-rate detail: {status.source} {status.status} - {status.message}")
            elif _screener_status_is_provider_unavailable(status):
                reasons.append(f"provider unavailable detail: {status.source} {status.status} - {status.message}")
            elif _screener_status_mentions_unsupported_field(status):
                reasons.append(f"unsupported source field detail: {status.source} {status.status} - {status.message}")
            elif _screener_status_is_provider_empty(status):
                reasons.append(f"provider returned no usable data detail: {status.source} {status.status} - {status.message}")
        if record_has_market_blanks and not _snapshot_mentions_text(snapshot, "fallback"):
            reasons.append(
                "fallback disabled/not attempted: optional fallback data only runs when MARKET_SCREENER_FALLBACK_PROVIDER=alpha_vantage is configured, and only during visible-page or selected-row enrichment."
            )
    for line in session_lines:
        clean = str(line or "").strip()
        lower = clean.lower()
        if clean and ("provider error" in lower or "enriched 0" in lower or "0 of" in lower):
            reasons.append(f"session enrichment detail: {clean}")
    return list(dict.fromkeys(reasons))


def _screener_status_mentions_missing_config(status: Any) -> bool:
    text = f"{getattr(status, 'source', '')} {getattr(status, 'status', '')} {getattr(status, 'message', '')}".lower()
    return any(
        token in text
        for token in (
            " disabled",
            "missing ",
            "not configured",
            "no fmp_api_key",
            "no databento_api_key",
            "no alpha_vantage_api_key",
            "no market_screener",
            "not installed",
        )
    )


def _screener_status_mentions_auth_plan_rate(status: Any) -> bool:
    text = f"{getattr(status, 'source', '')} {getattr(status, 'status', '')} {getattr(status, 'message', '')}".lower()
    return any(token in text for token in ("auth", "401", "403", "api key", "apikey", "plan", "rate", "limit", "quota", "upgrade"))


def _screener_status_mentions_unsupported_field(status: Any) -> bool:
    text = f"{getattr(status, 'source', '')} {getattr(status, 'status', '')} {getattr(status, 'message', '')}".lower()
    return any(token in text for token in ("unsupported", "not used to fill", "not merged", "schema", "futures/options", "cme context"))


def _screener_status_is_provider_unavailable(status: Any) -> bool:
    source = str(getattr(status, "source", "") or "").lower()
    state = str(getattr(status, "status", "") or "").lower()
    return state in {"unavailable", "error"} and _source_name_is_market_data_provider(source)


def _screener_status_is_provider_empty(status: Any) -> bool:
    source = str(getattr(status, "source", "") or "").lower()
    state = str(getattr(status, "status", "") or "").lower()
    return state == "empty" and _source_name_is_market_data_provider(source)


def _source_name_is_market_data_provider(source: str) -> bool:
    return any(token in source for token in ("market data", "schwab", "fmp", "fallback", "alpha vantage", "local market", "databento"))


def _snapshot_mentions_text(snapshot: MarketScreenerSnapshot, needle: str) -> bool:
    target = needle.lower()
    if any(target in str(source or "").lower() for source in snapshot.sources):
        return True
    for status in snapshot.statuses:
        text = f"{getattr(status, 'source', '')} {getattr(status, 'status', '')} {getattr(status, 'message', '')}".lower()
        if target in text:
            return True
    return False


def _ordered_screener_field_provenance(record: MarketScreenerRecord) -> tuple[Any, ...]:
    priority = {
        field: index
        for index, field in enumerate(
            (
                "portfolio_quantity",
                "portfolio_average_cost",
                "portfolio_market_value",
                "portfolio_unrealized_pnl",
                "portfolio_weight",
                "price",
                "change_percent",
                "volume",
                "avg_volume",
                "market_cap",
                "pe_ratio",
                "eps",
                "revenue_growth",
                "shares_float",
                "shares_outstanding",
                "next_earnings_date",
                "recent_filing_date",
                "recent_filing_type",
                "signals",
                "risk_flags",
            )
        )
    }
    return tuple(sorted(record.field_provenance, key=lambda row: (priority.get(getattr(row, "field", ""), 999), getattr(row, "field", ""), getattr(row, "source", ""))))


def _format_screener_ai_result(record: MarketScreenerRecord, label: str, prompt: str, response: MarketScreenerAiResponse) -> str:
    header = [
        f"MARKET SCREENER AI - {label}",
        "=" * (21 + len(label)),
        "",
        f"Selected: {record.symbol or record.cik or EMPTY_VALUE} - {record.company_name or EMPTY_VALUE}",
        f"Data: {market_screener_data_label(record)} | Completeness: {market_screener_data_completeness_label(record)} | Provenance fields: {len(record.field_provenance)}",
        f"Signals: {', '.join(record.signals) if record.signals else EMPTY_VALUE}",
        f"Risk flags: {', '.join(record.risk_flags) if record.risk_flags else EMPTY_VALUE}",
        f"Model: {response.model}",
        f"Source mode: {response.source_mode}",
        "",
        "Prompt:",
        prompt.strip(),
        "",
        "Answer:",
        response.answer.strip(),
    ]
    if response.source_debug:
        header.extend(["", "Source Debug:"])
        header.extend(f"- {line}" for line in response.source_debug)
    return redact_symbol_chat_secrets("\n".join(header).strip() + "\n")


def _screener_source_status_text(snapshot: MarketScreenerSnapshot) -> str:
    return _screener_diagnostics_popout_text(snapshot)


def _screener_diagnostics_popout_text(
    snapshot: MarketScreenerSnapshot | None,
    *,
    selected_record: MarketScreenerRecord | None = None,
    session_lines: Iterable[str] = (),
) -> str:
    lines: list[str] = []
    lines.append("LOADING / SOURCE DIAGNOSTICS / WHY BLANKS?")
    lines.append("")
    if snapshot is None:
        lines.append("No Market Screener snapshot has loaded yet. Refresh the screener to load source statuses and diagnostics counters.")
        lines.append("")
        lines.extend(_screener_provider_config_lines())
        lines.append("")
        lines.extend(_screener_source_ladder_lines())
        lines.append("")
        lines.extend(_screener_blank_explanation_lines())
        return "\n".join(lines)

    lines.append("Summary")
    lines.append(f"- {market_screener_diagnostics_summary(snapshot.diagnostics)}")
    lines.append(f"- Rows loaded: {len(snapshot.records)}")
    lines.append(f"- Fetched at: {snapshot.fetched_at}")
    if getattr(snapshot.diagnostics, "major_us_large_caps_absent", 0):
        lines.append(f"- Major U.S. large-cap symbols absent: {snapshot.diagnostics.major_us_large_caps_absent}")
    lines.append("")
    lines.extend(_screener_provider_config_lines())
    lines.append("")
    lines.append("Market cap ranking diagnostics")
    rows_with_market_cap = snapshot.diagnostics.rows_with_market_cap or max(0, snapshot.diagnostics.total_rows - snapshot.diagnostics.rows_missing_market_cap)
    lines.extend(
        [
            f"- Rows with market cap: {rows_with_market_cap}/{snapshot.diagnostics.total_rows}",
            f"- Trusted USD market caps: {snapshot.diagnostics.rows_with_trusted_usd_market_cap}",
            f"- Trusted U.S. primary/common rank rows: {snapshot.diagnostics.rows_with_trusted_primary_market_cap}",
            f"- Trusted non-primary rank rows: {snapshot.diagnostics.rows_with_trusted_non_primary_market_cap}",
            f"- Untrusted market caps: {snapshot.diagnostics.rows_with_untrusted_market_cap}",
            f"- Non-USD market caps: {snapshot.diagnostics.rows_with_non_usd_market_cap}",
            f"- Ambiguous market caps: {snapshot.diagnostics.rows_with_ambiguous_market_cap}",
            f"- Missing market caps: {snapshot.diagnostics.rows_missing_market_cap}",
        ]
    )
    if snapshot.diagnostics.market_cap_coverage_incomplete or snapshot.diagnostics.rows_missing_market_cap:
        lines.append("- Global market-cap ranking coverage incomplete before page-1 ranking; rows without market caps sort below rows with numeric market caps.")
    lines.append("")
    lines.append("Major-cap diagnostics")
    major_lines = market_screener_major_cap_diagnostic_lines(snapshot.records, snapshot.diagnostics)
    lines.extend(f"- {line}" for line in major_lines[:16])
    if len(major_lines) > 16:
        lines.append(f"- ... {len(major_lines) - 16} more major-cap diagnostic row(s).")
    lines.append("")
    lines.extend(_screener_source_ladder_lines())
    lines.append("")
    lines.extend(_screener_blank_explanation_lines())
    lines.append("")
    lines.append("Counters")
    lines.extend(f"- {line}" for line in market_screener_diagnostics_detail_lines(snapshot.diagnostics))
    lines.append("")
    lines.append("Provider/source statuses")
    if snapshot.statuses:
        lines.extend(f"- {status.source}: {status.status} - {status.message}" for status in snapshot.statuses)
    else:
        lines.append("- No provider/source status rows were reported.")
    lines.append("")
    lines.append("Would paid FMP help?")
    lines.extend(
        [
            "- Yes, if FMP quote, profile, or profile-by-CIK is configured but blocked by auth, plan, quota, or rate limit, or if the free plan does not expose needed profile/fundamental fields.",
            "- No, if the row lacks a trusted CIK/symbol, a provider returned no usable data for that symbol, Schwab/Databento/local data is the desired tape source, or rows were skipped by this app's configured cap.",
            "- Paid FMP can improve key-metrics, ratios, income-growth, and shares-float coverage, but it still will not justify guessing tickers from company names; identity must come from SEC CIK/ticker data, SEC submissions, local seed data, Schwab, FMP, Databento US Equities, or a configured fallback.",
            "- Databento CME Globex context is futures/options macro context only; it is not used to fill selected-equity quote or fundamental fields.",
        ]
    )
    lines.append("")
    if selected_record is not None:
        lines.append("Selected row why blanks?")
        lines.append(f"- Selected: {selected_record.symbol or selected_record.cik or EMPTY_VALUE} - {display_optional_text(selected_record.company_name)}")
        selected_reasons = _selected_screener_missing_reason_lines(selected_record, snapshot=snapshot, session_lines=session_lines)
        if selected_reasons:
            lines.extend(f"- {reason}" for reason in selected_reasons)
        else:
            lines.append("- No blank-field reasons were detected for the selected row.")
        if selected_record.field_provenance:
            lines.append("")
            lines.append("Selected row field provenance")
            for item in _ordered_screener_field_provenance(selected_record):
                detail = f" ({item.source_detail})" if item.source_detail else ""
                link = f" | {item.source_link}" if item.source_link else ""
                lines.append(f"- {item.field}: {item.source}{detail}{link}")
    else:
        lines.append("Selected row why blanks?")
        lines.append("- No screener row selected. Select a row to see row-specific missing-field reasons and field provenance.")
    session = [str(line or "").strip() for line in session_lines if str(line or "").strip()]
    if session:
        if lines:
            lines.append("")
        lines.append("Session enrichment")
        lines.extend(f"- {line}" for line in session)
    if snapshot.errors:
        if lines:
            lines.append("")
        lines.append("Warnings:")
        lines.extend(f"- {error}" for error in snapshot.errors[:6])
    return "\n".join(lines)


def _screener_source_ladder_lines() -> list[str]:
    return [
        "Provider/source ladder",
        "- 1. Local market-data file/cache",
        "- 2. SEC EDGAR and FMP SEC-filings metadata supply filing date/type only; SEC company_tickers/submissions are not used for screener identity.",
        "- 3. FMP profile / Databento reference data for symbol, company, exchange, sector, and industry",
        "- 4. Schwab quote",
        "- 5. Databento US Equities, when enabled/configured, for selected-equity price/volume/change/avg-volume from supported OHLCV/trade rows",
        "- 6. FMP quote/profile/profile-by-CIK/market-cap/key-metrics/historical-EOD/ratios/income-growth/financial-growth/income-statement/shares-float for equity quote, profile, and fundamentals",
        "- 7. FMP SEC filings by symbol supplies recent filing date/type for the chart.",
        "- 8. Alpha Vantage upcoming earnings calendar, then FMP stable earnings-calendar fallback.",
        "- 9. Optional fallback provider, only when configured, and only for visible-page or selected-row enrichment",
        "- Separate. Databento CME context, when enabled/configured, is cross-asset futures/options context and is not merged into equity rows.",
    ]


def _screener_provider_config_lines() -> list[str]:
    def _env_line(name: str, default: str = "") -> str:
        raw = str(os.getenv(name, "") or "").strip()
        value = raw if raw else f"<unset; default {default}>" if default else "<unset>"
        return f"- {name}: {value}"

    return [
        "Provider config/caps",
        _env_line("MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT", str(configured_market_data_symbol_limit())),
        _env_line("FMP_MARKET_DATA_SYMBOL_LIMIT", "100"),
        _env_line("MARKET_SCREENER_PROFILE_BACKFILL_LIMIT", "2000"),
        _env_line("MARKET_SCREENER_QUOTE_BACKFILL_LIMIT", "2000"),
        _env_line("MARKET_SCREENER_FUNDAMENTAL_BACKFILL_LIMIT", "2000"),
        _env_line("MARKET_SCREENER_DATABENTO_BACKFILL_LIMIT", "1000"),
        _env_line("DATABENTO_EQUITIES_SYMBOL_LIMIT", "5000"),
        _env_line("FMP_CACHE_TTL_SECONDS", "900"),
        _env_line("MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS", "900"),
        "- Secret status: API keys are intentionally not displayed; provider statuses below report configured/unavailable/auth/rate/plan outcomes without secrets.",
    ]


def _screener_blank_explanation_lines() -> list[str]:
    return [
        "Why blanks happen",
        "- SEC company_tickers/submissions are not used for Market Screener symbol, company, exchange, sector, or industry. SEC is limited to filing date/type context.",
        "- Exchange, sector, and industry require FMP profile/profile-by-CIK, Databento reference/security-master fields when configured, local seed data, a local market-data file, or a configured fallback profile.",
        "- Price and volume require a local market-data file/cache, Schwab quote, Databento US Equities, FMP quote, or configured fallback quote.",
        "- Change % and avg volume require explicit provider fields, enough Databento selected-equity OHLCV/trade history to compute them, or FMP historical EOD. Unsupported schemas leave these blank; missing values are not treated as zero.",
        "- Market cap requires local seed data, FMP profile/key-metrics/market-cap endpoints, or a configured fallback. Trusted USD/common-equity caps sort ahead of ambiguous caps; missing caps sort last.",
        "- Fundamentals require local market-data/fundamental seed data, FMP quote/profile/key-metrics/ratios/income-growth/financial-growth/income-statement/shares-float fields, Databento shares outstanding where available, or a configured fallback profile. SEC filing rows are not chart-field sources; Databento CME context is not selected-equity fundamentals. Missing values are left blank and are not inferred.",
        "- Earnings and recent filing columns require the upcoming earnings calendar or FMP earnings-calendar / SEC-filings-by-symbol fallback metadata.",
    ]


def _screener_source_summary_text(snapshot: MarketScreenerSnapshot) -> str:
    if snapshot.diagnostics.total_rows:
        return _truncate(f"Source-aware coverage: {market_screener_diagnostics_summary(snapshot.diagnostics)} Open Source Diagnostics / Why blanks? for full counters.", 240)
    market_data_status = next((status for status in snapshot.statuses if status.source == "Market data enrichment"), None)
    if market_data_status is not None:
        return _truncate(market_data_status.message, 240)
    if snapshot.statuses:
        status = snapshot.statuses[0]
        return _truncate(f"{status.source}: {status.status} - {status.message}", 240)
    return "Market data/source status loaded."


def _recent_detail_text(record: RecentEarningsRecord) -> str:
    lines = [
        f"{record.company_name} ({display_optional_text(record.ticker)}) | CIK {record.cik}",
        f"{record.filing_type} | {record.form} | Item: {record.items or EMPTY_VALUE} | Filed: {record.filed_date or EMPTY_VALUE} | Accepted: {record.acceptance_datetime or EMPTY_VALUE}",
        f"Fiscal period: {display_optional_text(record.fiscal_period)} | Report date: {display_optional_text(record.report_date)}",
        (
            "Parsed: "
            f"Revenue {display_money(record.revenue)} | "
            f"Growth {display_percent(record.revenue_growth)} | "
            f"EPS {display_money(record.eps)} | "
            f"Net income {display_money(record.net_income)}"
        ),
        f"Guidance: {'Mentioned' if record.guidance_flag else 'Not detected'} | Risk flags: {', '.join(record.risk_flags) if record.risk_flags else EMPTY_VALUE}",
        f"Industry: {display_optional_text(record.sector)} / {display_optional_text(record.sic)} | {display_optional_text(record.industry)} | {display_optional_text(record.exchange)}",
        f"Filing: {record.filing_url}",
        f"Exhibit: {record.exhibit_url or EMPTY_VALUE}",
    ]
    source_excerpt = str(getattr(record, "source_excerpt", "") or "").strip()
    if source_excerpt:
        lines.append(f"Source excerpt: {_truncate(source_excerpt, 520)}")
    return "\n".join(lines)


def _format_earnings_ai_result(record: RecentEarningsRecord, label: str, prompt: str, response: EarningsAiResponse) -> str:
    header = [
        f"EARNINGS AI - {label}",
        "=" * (14 + len(label)),
        "",
        f"Selected: {record.company_name} ({record.ticker or record.cik})",
        f"Filing: {record.form} {record.items or EMPTY_VALUE} | {record.filing_type} | filed {record.filed_date or EMPTY_VALUE}",
        f"Accession: {record.accession_number}",
        f"Model: {response.model}",
        f"Source mode: {response.source_mode}",
        "",
        "Prompt:",
        prompt.strip(),
        "",
        "Answer:",
        response.answer.strip(),
    ]
    if response.source_debug:
        header.extend(["", "Source Debug:"])
        header.extend(f"- {line}" for line in response.source_debug)
    return redact_symbol_chat_secrets("\n".join(header).strip() + "\n")


def _recent_sort_value(record: RecentEarningsRecord, column: str) -> Any:
    return {
        "company": record.company_name.lower(),
        "ticker": (record.ticker or "").lower() or None,
        "cik": record.cik,
        "form": record.form,
        "kind": record.filing_type,
        "item": record.items,
        "filed_date": record.filed_date or None,
        "acceptance_time": record.acceptance_datetime or None,
        "fiscal_period": record.fiscal_period,
        "sector": record.sector,
        "industry": record.industry,
        "exchange": record.exchange,
        "revenue": record.revenue,
        "revenue_growth": record.revenue_growth,
        "eps": record.eps,
        "net_income": record.net_income,
        "guidance": record.guidance_flag,
        "risk_flags": len(record.risk_flags),
        "filing_link": record.filing_url,
        "exhibit_link": record.exhibit_url,
    }.get(column)


def _upcoming_sort_value(record: UpcomingEarningsRecord, column: str) -> Any:
    return {
        "symbol": record.symbol,
        "company": (record.company_name or "").lower() or None,
        "report_date": record.report_date or None,
        "fiscal_date": record.fiscal_date_ending,
        "estimate": record.estimate,
        "currency": record.currency,
        "source": record.source,
        "source_link": record.source_url,
    }.get(column)


def _header(
        parent: ttk.Frame,
        status_var: tk.StringVar) -> ttk.Frame:
    header = ttk.Frame(parent, style="Panel.TFrame")
    header.columnconfigure(0, weight=1)
    display_var = tk.StringVar(master=parent, value=_truncate(status_var.get(), STATUS_CHIP_DISPLAY_LIMIT))

    def _sync_status(*_args: str) -> None:
        display_var.set(_truncate(status_var.get(), STATUS_CHIP_DISPLAY_LIMIT))

    trace_name = status_var.trace_add("write", _sync_status)

    def _remove_trace(event: tk.Event) -> None:
        if event.widget is not header:
            return
        try:
            status_var.trace_remove("write", trace_name)
        except tk.TclError:
            pass

    header.bind("<Destroy>", _remove_trace, add="+")
    header._status_display_var = display_var  # type: ignore[attr-defined]
    header._status_trace_name = trace_name  # type: ignore[attr-defined]
    label = ttk.Label(
        header,
        textvariable=display_var,
        style="Chip.TLabel",
        width=STATUS_CHIP_WIDTH,
        anchor=tk.E,
    )
    label.grid(row=0, column=1, sticky="e", padx=(4, 0), pady=(0, 1))
    return header


def _chart(parent: ttk.Frame, *, row: int) -> tk.Canvas:
    canvas = tk.Canvas(parent, height=EARNINGS_RADAR_CHART_HEIGHT, bg=polished_theme.PANEL, highlightthickness=1, highlightbackground=polished_theme.BORDER)
    canvas.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    return canvas


def _screener_summary_panel(owner: tk.Tk, parent: ttk.Frame, *, row: int) -> tk.Canvas:
    shell = ttk.Frame(parent, style="Panel.TFrame")
    shell.grid(row=row, column=0, sticky="ew", pady=(0, 6))
    shell.columnconfigure(0, weight=1)
    header = ttk.Frame(shell, style="Panel.TFrame")
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(1, weight=1)
    ttk.Label(header, text="Summary", style="Subtle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
    ttk.Label(header, text="Portfolio / Data / Completeness", style="Subtle.TLabel").grid(row=0, column=1, sticky="w")
    ttk.Button(header, textvariable=owner.market_screener_summary_toggle_var, command=lambda app=owner: _toggle_screener_summary(app)).grid(row=0, column=2, sticky="e")
    canvas = tk.Canvas(shell, height=MARKET_SCREENER_SUMMARY_STRIP_HEIGHT, bg=polished_theme.PANEL, highlightthickness=1, highlightbackground=polished_theme.BORDER)
    canvas.grid(row=1, column=0, sticky="ew", pady=(4, 0))
    owner.market_screener_summary_panel = shell
    return canvas


def _toggle_screener_summary(self: tk.Tk) -> None:
    visible_var = getattr(self, "market_screener_summary_visible_var", None)
    if visible_var is None:
        return
    visible_var.set(not bool(visible_var.get()))
    _apply_screener_summary_visibility(self)
    if bool(visible_var.get()):
        _draw_screener_chart(self)


def _apply_screener_summary_visibility(self: tk.Tk) -> None:
    visible_var = getattr(self, "market_screener_summary_visible_var", None)
    if visible_var is None:
        return
    visible = bool(visible_var.get())
    toggle_var = getattr(self, "market_screener_summary_toggle_var", None)
    if toggle_var is not None:
        toggle_var.set("Hide Summary" if visible else "Show Summary")
    chart = getattr(self, "market_screener_chart", None)
    if chart is None:
        return
    try:
        if visible:
            chart.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        else:
            chart.grid_remove()
    except tk.TclError:
        return


def _table(
    parent: ttk.Frame,
    *,
    row: int,
    title: str,
    columns: tuple[tuple[str, str, int, str], ...],
    sort: Callable[[str], None],
    style: str | None = None,
    height: int = 14,
) -> ttk.Treeview:
    frame = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    frame.grid(row=row, column=0, sticky="nsew", pady=(0, 8))
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    tree_kwargs: dict[str, Any] = {
        "columns": tuple(column_id for column_id, _label, _width, _anchor in columns),
        "show": "headings",
        "height": height,
        "selectmode": "browse",
    }
    if style is not None:
        tree_kwargs["style"] = style
    tree = ttk.Treeview(frame, **tree_kwargs)
    for column_id, label, width, anchor in columns:
        tree.heading(column_id, text=label, command=lambda col=column_id: sort(col))
        tree.column(column_id, width=width, minwidth=min(width, 90), anchor=anchor, stretch=column_id in {"company", "industry", "data_status", "signals", "risk_flags", "sources", "filing_link", "exhibit_link", "source_link"})
    tree.grid(row=0, column=0, sticky="nsew")
    y_scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    x_scroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
    x_scroll.grid(row=1, column=0, sticky="ew")
    tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
    return tree


def _footer(owner: tk.Tk, parent: ttk.Frame, *, row: int, page_var_name: str, size_var: tk.StringVar, prev: Callable[[], None], next_: Callable[[], None], apply: Callable[[], None]) -> None:
    footer = ttk.Frame(parent, style="Panel.TFrame")
    footer.grid(row=row, column=0, sticky="ew")
    footer.columnconfigure(1, weight=1)
    ttk.Button(footer, text="Prev", command=prev).grid(row=0, column=0, sticky="w")
    page_var = tk.StringVar(value="Page 1 / 1")
    setattr(owner, page_var_name, page_var)
    ttk.Label(footer, textvariable=page_var, style="Subtle.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
    ttk.Button(footer, text="Next", command=next_).grid(row=0, column=2, sticky="e")
    ttk.Label(footer, text="Rows/page", style="Subtle.TLabel").grid(row=0, column=3, sticky="e", padx=(12, 6))
    combo = ttk.Combobox(footer, textvariable=size_var, values=["50", "100", "200"], width=6, state="readonly")
    combo.grid(row=0, column=4, sticky="e")
    combo.bind("<<ComboboxSelected>>", lambda _event: apply(), add="+")


def _entry_filter(parent: ttk.Frame, label: str, variable: tk.StringVar, *, row: int, column: int, command: Callable[[], None]) -> None:
    _label(parent, label, row, column)
    entry = ttk.Entry(parent, textvariable=variable)
    entry.grid(row=row, column=column + 1, sticky="ew", padx=(0, 10), pady=4)
    entry.bind("<KeyRelease>", lambda _event: command(), add="+")


def _combo_filter(parent: ttk.Frame, label: str, variable: tk.StringVar, values: list[str], *, row: int, column: int, command: Callable[[], None] | None) -> ttk.Combobox:
    _label(parent, label, row, column)
    combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
    combo.grid(row=row, column=column + 1, sticky="ew", padx=(0, 10), pady=4)
    if command is not None:
        combo.bind("<<ComboboxSelected>>", lambda _event: command(), add="+")
    return combo


def _date_filters(parent: ttk.Frame, from_var: tk.StringVar, to_var: tk.StringVar, *, row: int, columnspan: int, command: Callable[[], None]) -> None:
    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.grid(row=row, column=0, columnspan=columnspan, sticky="ew", pady=(6, 0))
    frame.columnconfigure((1, 3), weight=1)
    ttk.Label(frame, text="From", style="Subtle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
    from_entry = ttk.Entry(frame, textvariable=from_var, width=12)
    from_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
    ttk.Label(frame, text="To", style="Subtle.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 6))
    to_entry = ttk.Entry(frame, textvariable=to_var, width=12)
    to_entry.grid(row=0, column=3, sticky="ew")
    for entry in (from_entry, to_entry):
        entry.bind("<KeyRelease>", lambda _event: command(), add="+")


def _draw_grouped_chart(canvas: tk.Canvas, groups: tuple[tuple[str, dict[str, int]], ...]) -> None:
    canvas.delete("all")
    width = max(canvas.winfo_width(), 720)
    panel_width = max(220, width // max(len(groups), 1))
    for group_index, (title, counts) in enumerate(groups):
        x0 = group_index * panel_width + 10
        canvas.create_text(x0, 10, text=title, anchor="nw", fill=polished_theme.TEXT, font=("Segoe UI", 9, "bold"))
        if not counts:
            canvas.create_text(x0, 42, text="No matches.", anchor="nw", fill=polished_theme.MUTED, font=("Segoe UI", 9))
            continue
        items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]
        max_value = max(count for _label, count in items) or 1
        for index, (label, count) in enumerate(items):
            y = 38 + index * 20
            bar_width = int((panel_width - 125) * (count / max_value))
            canvas.create_text(x0, y, text=_truncate(label, 16), anchor="nw", fill=polished_theme.MUTED, font=("Segoe UI", 8))
            canvas.create_rectangle(x0 + 105, y + 2, x0 + 105 + bar_width, y + 13, fill=polished_theme.ACCENT, outline="")
            canvas.create_text(x0 + panel_width - 16, y, text=str(count), anchor="ne", fill=polished_theme.TEXT, font=("Segoe UI", 8, "bold"))


def _draw_screener_summary_strip(canvas: tk.Canvas, groups: tuple[tuple[str, dict[str, int]], ...]) -> None:
    canvas.delete("all")
    width = max(canvas.winfo_width(), 720)
    panel_width = max(220, width // max(len(groups), 1))
    for group_index, (title, counts) in enumerate(groups):
        x0 = group_index * panel_width + 10
        x1 = min(width - 12, (group_index + 1) * panel_width - 16)
        canvas.create_text(x0, 7, text=title, anchor="nw", fill=polished_theme.TEXT, font=("Segoe UI", 8, "bold"))
        if not counts:
            canvas.create_text(x0, 29, text="No matches.", anchor="nw", fill=polished_theme.MUTED, font=("Segoe UI", 8))
            continue
        items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:MARKET_SCREENER_SUMMARY_MAX_ITEMS]
        max_value = max(count for _label, count in items) or 1
        label_width = 102 if panel_width >= 300 else 82
        label_limit = 18 if panel_width >= 300 else 14
        count_x = max(x0 + 138, x1)
        bar_x0 = x0 + label_width
        bar_max_width = max(18, count_x - bar_x0 - 32)
        for index, (label, count) in enumerate(items):
            y = 22 + index * 9
            bar_width = max(3, int(bar_max_width * (count / max_value)))
            canvas.create_text(x0, y, text=_truncate(label, label_limit), anchor="nw", fill=polished_theme.MUTED, font=("Segoe UI", 7))
            canvas.create_rectangle(bar_x0, y + 4, bar_x0 + bar_width, y + 7, fill=polished_theme.ACCENT, outline="")
            canvas.create_text(count_x, y - 1, text=str(count), anchor="ne", fill=polished_theme.TEXT, font=("Segoe UI", 7, "bold"))


def _sorted(records: list[Any], column: str, desc: bool, sort_value: Callable[[Any, str], Any]) -> list[Any]:
    present = [record for record in records if sort_value(record, column) is not None]
    missing = [record for record in records if sort_value(record, column) is None]
    present.sort(key=lambda record: sort_value(record, column), reverse=desc)
    return present + missing


def _counts(records: Iterable[Any], selector: Callable[[Any], str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        label = selector(record) or "Unknown"
        counts[label] = counts.get(label, 0) + 1
    return counts


def _primary_screener_signal(record: MarketScreenerRecord) -> str:
    if record.risk_flags:
        return "Risk flags"
    if record.signals:
        return record.signals[0]
    if record.next_earnings_date:
        return "Upcoming earnings"
    if record.recent_filing_date:
        return "Recent SEC filing"
    return "Universe only"


def _page_window(records: list[Any], page_size: int, page: int) -> tuple[int, int, int]:
    total = len(records)
    page_count = max(1, math.ceil(total / page_size))
    safe_page = min(max(page, 0), page_count - 1)
    return total, page_count, safe_page * page_size


def _max_page(records: list[Any], page_size: int) -> int:
    return max(0, math.ceil(len(records) / page_size) - 1)


def _recent_page_size(self: tk.Tk) -> int:
    return _safe_int(self.earnings_recent_page_size_var.get(), default=100, minimum=25, maximum=500)


def _upcoming_page_size(self: tk.Tk) -> int:
    return _safe_int(self.earnings_upcoming_page_size_var.get(), default=100, minimum=25, maximum=500)


def _screener_page_size(self: tk.Tk) -> int:
    return _safe_int(self.market_screener_page_size_var.get(), default=100, minimum=25, maximum=500)


def _selected(tree: ttk.Treeview, row_map: dict[str, Any], title: str, missing_selection: str) -> Any | None:
    selection = tree.selection()
    if not selection:
        messagebox.showinfo(title, missing_selection)
        return None
    return row_map.get(selection[0])


def _open_url(url: str | None, title: str, missing_url: str) -> None:
    if not url:
        messagebox.showinfo(title, missing_url)
        return
    webbrowser.open_new_tab(url)


def _week_label(value: str) -> str:
    try:
        date = datetime.strptime(value[:10], "%Y-%m-%d")
        year, week, _day = date.isocalendar()
        return f"{year}-W{week:02d}"
    except (TypeError, ValueError):
        return "Unknown"


def _symbol_list(value: str) -> list[str]:
    return [part.strip().upper() for part in value.replace(";", ",").split(",") if part.strip()]


def _safe_int(value: str, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _display_market_money(value: float | None) -> str:
    if value is None:
        return EMPTY_VALUE
    return f"${value:,.0f}" if abs(value) >= 100 else f"${value:,.2f}"


def _display_market_percent(value: float | None) -> str:
    return EMPTY_VALUE if value is None else f"{value:.1f}%"


def _display_market_large_number(value: float | None) -> str:
    if value is None:
        return EMPTY_VALUE
    abs_value = abs(value)
    if abs_value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs_value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs_value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,.0f}"


def _display_float_or_shares(record: MarketScreenerRecord) -> str:
    shares_float = _display_market_large_number(record.shares_float)
    shares_outstanding = _display_market_large_number(record.shares_outstanding)
    if record.shares_float is not None and record.shares_outstanding is not None:
        return f"{shares_float} / {shares_outstanding}"
    if record.shares_float is not None:
        return shares_float
    if record.shares_outstanding is not None:
        return shares_outstanding
    return EMPTY_VALUE


def _display_market_decimal(value: float | None) -> str:
    return EMPTY_VALUE if value is None else f"{value:.2f}"


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _label(parent: ttk.Frame, text: str, row: int, column: int) -> None:
    ttk.Label(parent, text=text, style="Subtle.TLabel").grid(row=row, column=column, sticky="w", padx=(0, 6), pady=4)


def _set_combo_values(combo: ttk.Combobox, values: list[str], variable: tk.StringVar) -> None:
    combo.configure(values=values)
    if variable.get() not in values:
        variable.set("All")


def _find_labelframe(root: tk.Widget, title: str) -> ttk.LabelFrame | None:
    if _widget_class(root) == "TLabelframe":
        try:
            if str(root.cget("text")) == title:
                return root  # type: ignore[return-value]
        except Exception:
            pass
    for child in root.winfo_children():
        found = _find_labelframe(child, title)
        if found is not None:
            return found
    return None


def _widget_class(widget: tk.Widget) -> str:
    try:
        return str(widget.winfo_class())
    except Exception:
        return ""
