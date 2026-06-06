from __future__ import annotations

import math
import threading
import tkinter as tk
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from tkinter import messagebox, ttk
from typing import Any, Type

from app.analytics.earnings_release import (
    analyze_earnings_release,
    analyze_earnings_sources,
    fetch_earnings_calendar_event,
    fetch_official_company_earnings_release,
    format_earnings_release_digest,
)
from app.analytics.etf_analysis import (
    ETF_SEC_FORMS,
    ETF_SECURITY_KINDS,
    ETFCard,
    ETFResearchSnapshot,
    build_etf_readout,
    build_etf_research_snapshot,
    detect_security_kind,
    format_etf_documents_text,
    format_etf_structure_text,
)
from app.analytics.foreign_issuer_analysis import (
    ALL_COMPANY_REPORT_FORMS,
    FOREIGN_RESULTS_FORMS,
    REPORTING_PROFILE_ETF_OR_FUND,
    REPORTING_PROFILE_FOREIGN_ISSUER,
    REPORTING_PROFILE_UNKNOWN,
    REPORTING_PROFILE_US_DOMESTIC_EQUITY,
    ForeignIssuerSnapshot,
    build_foreign_issuer_snapshot,
    detect_reporting_profile,
    fetch_known_official_ir_texts,
    foreign_issuer_earnings_cards,
    foreign_issuer_fundamental_cards,
    foreign_issuer_interpretation,
    foreign_issuer_risks,
    foreign_issuer_source_links,
    format_foreign_issuer_earnings_text,
    format_foreign_issuer_fundamentals_text,
    format_foreign_issuer_results_explanation,
)
from app.analytics.fundamental_analysis import analyze_company_facts, format_fundamental_analysis
from app.analytics.options_greeks import (
    GreekSummary,
    GreekValue,
    OptionGreekSnapshot,
    build_greek_summary,
    build_greek_decision_section,
    classify_greek_contract,
    greek_approximation_rows,
    greek_dollar_meanings,
    rank_greek_contracts,
    theta_offset_moves,
)
from app.analytics.research_scoring import (
    BadgeReadout,
    ResearchDecisionReadout,
    build_decision_readout,
    direction_strength_label,
    risk_heat_label,
    scenario_impact_bar_value,
)
from app.analytics.research_workspace_insights import (
    TERM_HELPERS,
    OptionCandidate,
    build_cross_read_conflict_badge,
    build_earnings_workspace_summary,
    build_fundamental_verdict,
    build_fundamental_metric_cards,
    build_macro_metric_cards,
    build_risk_plan,
    build_technical_narrative,
    build_technical_at_glance_read,
    combined_current_model_option_scenarios,
    combined_option_scenarios,
    inflation_read_from_metrics,
    macro_why_it_matters,
    option_position_readout,
    option_strategy_scenario_move_note,
    option_strategy_scenario_moves,
    option_timeline_text,
    selected_candidate_detail,
    suggest_option_candidates,
    ticket_fields_for_option_candidate,
)
from app.analytics.stock_research import (
    AdvancedIndicatorSnapshot,
    DataSourceStatus,
    PortfolioSymbolContext,
    build_current_model_scenario_rows,
    build_planned_stock_context,
    build_portfolio_symbol_context,
    build_scenario_rows,
    calculate_advanced_indicators,
    distance_to_price,
    generated_risk_budget,
    load_cached_price_history,
    save_cached_price_history,
    suggested_position_size,
    technical_scenario_basis,
    technical_scenario_moves,
)
from app.analytics.capital_structure_pressure import (
    analyze_capital_structure_pressure,
    unknown_capital_structure_report,
)
from app.core.order_models import OrderSide, OrderType, TimeInForce
from app.analytics.technical_analysis import (
    CapitalStructureIndicatorRead,
    DEFAULT_COMMAND_CENTER_TIMEFRAMES,
    Candle,
    TechnicalCommandCenterReport,
    TechnicalTicket,
    build_technical_command_center_report,
    candles_from_price_history,
    format_technical_command_center_report,
    parse_quote_snapshot,
)
from app.analytics.trade_evidence import (
    TradeEvidenceReport,
    append_trade_evidence_snapshot,
    build_trade_evidence_report,
    evidence_scorecards,
    format_trade_evidence_report,
)
from app.data.sec_edgar import SecEdgarClient, normalize_ticker
from app.macro.analysis import format_macro_report
from app.macro.models import MacroSnapshot
from app.macro.releases import fetch_macro_release_snapshot
from app.ui.research_widgets import Checklist, ScenarioImpactBars, ScoreMeter, ScrollableFrame, clear_children, freshness_badges, labeled_value_grid, metric_grid
from app.ui.schwab_option_chain_extension import _option_chain_rows, _populate_option_chain_tree, _request_option_chain, _underlying_price
from app.ui.schwab_output_popout_extension import _apply_report_tags, _open_external_url

REPORT_FORMS = ("10-K", "10-Q", "8-K")
GREEK_VISUAL_BG = "#ffffff"
GREEK_SURFACE = "#f8fafc"
GREEK_BORDER = "#cbd5e1"
GREEK_TEXT = "#0f172a"
GREEK_MUTED = "#64748b"
GREEK_BLUE = "#2563eb"
GREEK_GREEN = "#16a34a"
GREEK_RED = "#dc2626"
GREEK_AMBER = "#d97706"
GREEK_TEAL = "#0f766e"
GREEK_PURPLE = "#7c3aed"

WORKSPACE_GEOMETRY = "1550x980"
WORKSPACE_MIN_SIZE = (1180, 760)
WORKSPACE_PAD = 14
PANE_PAD = 12
LEFT_PANE_WIDTH = 370
LEFT_PANE_MIN = 330
RIGHT_PANE_MIN = 760
TAB_PADDING = 16
SECTION_GAP = 12
READABLE_TREE_ROW_HEIGHT = 28
FOCUS_WINDOW_GEOMETRY = "1220x840"
FOCUS_WINDOW_MIN_SIZE = (860, 600)
SUMMARY_PANE_MIN = 130
SUMMARY_COLLAPSED_HEIGHT = 158
SUMMARY_EXPANDED_HEIGHT = 286
TAB_PANE_MIN = 440
DETACH_DRAG_PIXELS = 42
PRC_PRESSURE_LINE_NOTICE = "PRC Pressure Line is a synthetic internal indicator, not an official price target or exchange price."
CAPITAL_STRUCTURE_LEVEL_DISCLAIMER = "Filing-derived levels are risk/context modifiers, not support, resistance, targets, or price predictions."


@dataclass(frozen=True)
class _ResearchPayload:
    symbol: str
    quote: dict[str, Any] | None
    indicators: AdvancedIndicatorSnapshot
    context: PortfolioSymbolContext
    scenario_rows: list
    earnings_text: str
    fundamentals_text: str
    filings_lines: list[str]
    macro_text: str
    statuses: list[DataSourceStatus]
    decision: ResearchDecisionReadout
    macro_snapshot: MacroSnapshot | None = None
    option_chain_rows: list[dict[str, Any]] | None = None
    option_chain_underlying_price: float | None = None
    greek_summary: GreekSummary | None = None
    security_kind: str = "equity"
    reporting_profile: str = REPORTING_PROFILE_UNKNOWN
    etf_snapshot: ETFResearchSnapshot | None = None
    foreign_issuer_snapshot: ForeignIssuerSnapshot | None = None
    daily_candles: list[Candle] | None = None
    market_indicators: dict[str, AdvancedIndicatorSnapshot] | None = None
    market_candles: dict[str, list[Candle]] | None = None
    trade_evidence_report: TradeEvidenceReport | None = None
    command_center_report: TechnicalCommandCenterReport | None = None


def install_schwab_research_workspace_extension(app_cls: Type[tk.Tk]) -> None:
    app_cls.show_technical_analysis = _open_schwab_research_workspace  # type: ignore[method-assign]
    app_cls.render_schwab_research_greeks = _render_greeks  # type: ignore[attr-defined]


def _open_schwab_research_workspace(self: tk.Tk) -> None:
    existing = getattr(self, "schwab_research_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                _refresh_research_holdings(self)
                return
        except tk.TclError:
            pass

    window = tk.Toplevel(self)
    window.title("Schwab Research + Risk Workspace")
    window.geometry(WORKSPACE_GEOMETRY)
    window.minsize(*WORKSPACE_MIN_SIZE)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)
    _configure_research_workspace_styles(window)
    self.schwab_research_window = window

    selected_symbol = _initial_research_symbol(self)
    self.schwab_research_symbol_var = tk.StringVar(value=selected_symbol)
    self.schwab_research_max_risk_var = tk.StringVar(value="Run analysis")
    self.schwab_research_scenario_basis_var = tk.StringVar(value="Scenario moves will be generated from technical levels.")
    self.schwab_research_status_var = tk.StringVar(value="Choose a holding or enter a symbol, then run analysis.")
    self.schwab_research_sidebar_visible = tk.BooleanVar(value=True)
    self.schwab_research_summary_expanded = tk.BooleanVar(value=False)

    header = ttk.Frame(window, padding=(WORKSPACE_PAD, 10), style="Panel.TFrame")
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(header, text="Schwab Research + Risk Workspace", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(header, textvariable=self.schwab_research_status_var, style="Subtle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
    ttk.Button(header, text="Refresh Holdings", command=lambda app=self: _refresh_research_holdings(app)).grid(row=0, column=1, rowspan=2, sticky="e", padx=(8, 0))
    self.schwab_research_sidebar_button = ttk.Button(header, text="Hide Sidebar", command=lambda app=self: _toggle_research_sidebar(app))
    self.schwab_research_sidebar_button.grid(row=0, column=2, rowspan=2, sticky="e", padx=(8, 0))

    body = tk.PanedWindow(window, orient=tk.HORIZONTAL, bg="#0f172a", bd=0, sashwidth=8, sashpad=4, showhandle=True)
    body.grid(row=1, column=0, sticky="nsew", padx=WORKSPACE_PAD, pady=(0, WORKSPACE_PAD))

    left = ttk.Frame(body, style="Panel.TFrame", padding=PANE_PAD)
    right = ttk.Frame(body, style="Panel.TFrame", padding=PANE_PAD)
    body.add(left, minsize=LEFT_PANE_MIN, stretch="never")
    body.add(right, minsize=RIGHT_PANE_MIN, stretch="always")
    self.schwab_research_paned = body
    self.schwab_research_left_panel = left
    self.schwab_research_right_panel = right
    window.after_idle(lambda: body.sash_place(0, LEFT_PANE_WIDTH, 0))

    _build_research_left_panel(self, left)
    _build_research_right_panel(self, right)
    _refresh_research_holdings(self)

    def _close() -> None:
        self.schwab_research_window = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _close)


def _build_research_left_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(2, weight=1)

    summary = ttk.LabelFrame(parent, text="Synced Schwab Portfolio", style="Card.TLabelframe")
    summary.grid(row=0, column=0, sticky="ew")
    summary.columnconfigure((0, 1), weight=1)
    self.schwab_research_total_var = tk.StringVar(value="Total --")
    self.schwab_research_cash_var = tk.StringVar(value="Cash --")
    self.schwab_research_positions_var = tk.StringVar(value="Positions --")
    self.schwab_research_pnl_var = tk.StringVar(value="P&L --")
    ttk.Label(summary, textvariable=self.schwab_research_total_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 6))
    ttk.Label(summary, textvariable=self.schwab_research_cash_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", pady=(0, 6))
    ttk.Label(summary, textvariable=self.schwab_research_positions_var, style="Chip.TLabel").grid(row=1, column=0, sticky="ew", padx=(0, 6))
    ttk.Label(summary, textvariable=self.schwab_research_pnl_var, style="Chip.TLabel").grid(row=1, column=1, sticky="ew")

    selector = ttk.LabelFrame(parent, text="Select Symbol", style="Card.TLabelframe")
    selector.grid(row=1, column=0, sticky="ew", pady=(10, 10))
    selector.columnconfigure(1, weight=1)
    ttk.Label(selector, text="Symbol", style="Subtle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
    ttk.Entry(selector, textvariable=self.schwab_research_symbol_var).grid(row=0, column=1, sticky="ew")
    ttk.Button(selector, text="Run Analysis", command=lambda app=self: _run_research_analysis(app), style="Accent.TButton").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    ttk.Button(selector, text="Pop Out Output", command=self.open_schwab_output_popout).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(6, 0))

    holdings = ttk.LabelFrame(parent, text="Schwab Holdings", style="Card.TLabelframe")
    holdings.grid(row=2, column=0, sticky="nsew")
    holdings.rowconfigure(0, weight=1)
    holdings.columnconfigure(0, weight=1)
    columns = ("symbol", "type", "qty", "avg", "last", "value", "weight", "pnl")
    tree = ttk.Treeview(holdings, columns=columns, show="headings", height=18, selectmode="browse")
    _style_research_tree(tree)
    specs = {
        "symbol": ("Symbol", 82, tk.W),
        "type": ("Type", 70, tk.W),
        "qty": ("Qty", 72, tk.E),
        "avg": ("Avg", 78, tk.E),
        "last": ("Last", 78, tk.E),
        "value": ("Value", 92, tk.E),
        "weight": ("Weight", 74, tk.E),
        "pnl": ("P&L", 86, tk.E),
    }
    for column, (label, width, anchor) in specs.items():
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=anchor, stretch=column in {"symbol", "value", "pnl"})
    tree.tag_configure("positive", foreground="#047857")
    tree.tag_configure("negative", foreground="#b91c1c")
    tree.grid(row=0, column=0, sticky="nsew")
    y_scroll = ttk.Scrollbar(holdings, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    _add_horizontal_tree_scrollbar(holdings, tree, row=1)
    tree.bind("<ButtonRelease-1>", lambda event, app=self: _select_research_holding(app, event), add="+")
    tree.bind("<Double-1>", lambda _event, app=self: _run_research_analysis(app), add="+")
    self.schwab_research_holdings_tree = tree


def _configure_research_workspace_styles(window: tk.Widget) -> None:
    style = ttk.Style(window)
    style.configure("Research.Treeview", rowheight=READABLE_TREE_ROW_HEIGHT, font=("Segoe UI", 9))
    style.configure("Research.Treeview.Heading", font=("Segoe UI", 9, "bold"))


def _style_research_tree(tree: ttk.Treeview, *, height: int | None = None) -> None:
    tree.configure(style="Research.Treeview")
    if height is not None:
        tree.configure(height=height)


def _add_horizontal_tree_scrollbar(parent: tk.Widget, tree: ttk.Treeview, *, row: int, column: int = 0) -> None:
    x_scroll = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=tree.xview)
    x_scroll.grid(row=row, column=column, sticky="ew")
    tree.configure(xscrollcommand=x_scroll.set)


def _toggle_research_sidebar(self: tk.Tk) -> None:
    body = getattr(self, "schwab_research_paned", None)
    left = getattr(self, "schwab_research_left_panel", None)
    right = getattr(self, "schwab_research_right_panel", None)
    if body is None or left is None or right is None:
        return
    visible = bool(getattr(self, "schwab_research_sidebar_visible", tk.BooleanVar(value=True)).get())
    if visible:
        try:
            body.forget(left)
        except tk.TclError:
            return
        self.schwab_research_sidebar_visible.set(False)
        _set_button_text(getattr(self, "schwab_research_sidebar_button", None), "Show Sidebar")
        return
    try:
        body.add(left, before=right, minsize=LEFT_PANE_MIN, stretch="never")
        body.after_idle(lambda: body.sash_place(0, LEFT_PANE_WIDTH, 0))
    except tk.TclError:
        return
    self.schwab_research_sidebar_visible.set(True)
    _set_button_text(getattr(self, "schwab_research_sidebar_button", None), "Hide Sidebar")


def _toggle_research_summary(self: tk.Tk) -> None:
    expanded_var = getattr(self, "schwab_research_summary_expanded", None)
    if expanded_var is None:
        return
    expanded_var.set(not bool(expanded_var.get()))
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is not None:
        _render_at_glance(self, payload)
    else:
        _apply_research_summary_visibility(self)


def _apply_research_summary_visibility(self: tk.Tk) -> None:
    expanded = bool(getattr(self, "schwab_research_summary_expanded", tk.BooleanVar(value=False)).get())
    _set_button_text(
        getattr(self, "schwab_research_summary_button", None),
        "Collapse Summary" if expanded else "Expand Summary",
    )

    # Keep the top scorecard a clean summary band. The old lower strip/meters
    # are the source of the half-hidden content under the notebook boundary.
    # Full details remain available in Overview / popouts.
    for widget_name in ("schwab_research_top_strip", "schwab_research_summary_meters"):
        widget = getattr(self, widget_name, None)
        if widget is None:
            continue
        try:
            widget.grid_remove()
        except tk.TclError:
            continue

    _schedule_research_summary_resize(self, expanded=expanded)


def _schedule_research_summary_resize(self: tk.Tk, *, expanded: bool) -> None:
    window = getattr(self, "schwab_research_window", None) or self
    try:
        window.after_idle(lambda: _resize_research_summary_pane(self, expanded=expanded))
    except tk.TclError:
        pass


def _resize_research_summary_pane(self: tk.Tk, *, expanded: bool) -> None:
    vertical = getattr(self, "schwab_research_vertical_paned", None)
    if vertical is None:
        return
    try:
        vertical.update_idletasks()
        panes = vertical.panes()
        summary_widget = vertical.nametowidget(panes[0]) if panes else None
        requested = summary_widget.winfo_reqheight() + 6 if summary_widget is not None else 0

        target = SUMMARY_EXPANDED_HEIGHT if expanded else SUMMARY_COLLAPSED_HEIGHT
        desired = max(target, requested if expanded else min(requested, target))
        desired = min(desired, 330 if expanded else 176)

        vertical.sash_place(0, 0, int(desired))
    except (tk.TclError, IndexError, ValueError):
        return


def _set_button_text(button: Any, text: str) -> None:
    try:
        if button is not None:
            button.configure(text=text)
    except tk.TclError:
        pass


def _open_current_research_tab_focus(self: tk.Tk) -> None:
    detail = _current_research_tab_detail(self)
    if detail is None:
        messagebox.showinfo("Focus tab", "Run analysis first, then focus the selected tab.")
        return
    _open_readout_popout(detail)


def _current_research_tab_detail(self: tk.Tk) -> tk.Text | None:
    notebook = getattr(self, "schwab_research_tabs", None)
    if notebook is None:
        return None
    try:
        tab_title = notebook.tab(notebook.select(), "text")
    except tk.TclError:
        return None
    frame_by_title = {
        "Overview": getattr(self, "schwab_research_overview_frame", None),
        "Evidence Desk": getattr(self, "schwab_trade_evidence_frame", None),
        "Technicals": getattr(self, "schwab_research_technicals_frame", None),
        "Risk Scenarios": getattr(self, "schwab_research_scenarios_frame", None),
        "Options Strategy": getattr(self, "schwab_research_options_frame", None),
        "Greeks": getattr(self, "schwab_research_greeks_frame", None),
        "Earnings / News": getattr(self, "schwab_research_earnings_frame", None),
        "Fundamentals": getattr(self, "schwab_research_fundamentals_frame", None),
        "Macro Context": getattr(self, "schwab_research_macro_frame", None),
    }
    frame = frame_by_title.get(str(tab_title))
    if frame is None:
        return None
    detail = getattr(frame, "detail_text", None) or getattr(frame, "technical_notes_text", None) or getattr(frame, "scenario_note_text", None)
    return detail if isinstance(detail, tk.Text) else None


def _bind_summary_detach_drag(self: tk.Tk, handle: tk.Widget) -> None:
    state: dict[str, Any] = {"start": None, "detached": False}

    def press(event: tk.Event) -> None:
        state["start"] = (event.x_root, event.y_root)
        state["detached"] = False

    def motion(event: tk.Event) -> None:
        start = state.get("start")
        if start is None or state.get("detached"):
            return
        if abs(event.x_root - start[0]) + abs(event.y_root - start[1]) < DETACH_DRAG_PIXELS:
            return
        state["detached"] = True
        _open_summary_tearout(self)

    def release(_event: tk.Event) -> None:
        state["start"] = None
        state["detached"] = False

    _bind_drag_handlers(handle, press, motion, release)


def _bind_notebook_tab_detach_drag(self: tk.Tk, notebook: ttk.Notebook) -> None:
    state: dict[str, Any] = {"start": None, "tab_index": None, "detached": False}

    def press(event: tk.Event) -> None:
        try:
            tab_index = notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            tab_index = None
        state["start"] = (event.x_root, event.y_root) if tab_index is not None else None
        state["tab_index"] = tab_index
        state["detached"] = False

    def motion(event: tk.Event) -> None:
        start = state.get("start")
        tab_index = state.get("tab_index")
        if start is None or tab_index is None or state.get("detached"):
            return
        if abs(event.x_root - start[0]) + abs(event.y_root - start[1]) < DETACH_DRAG_PIXELS:
            return
        state["detached"] = True
        try:
            notebook.select(tab_index)
        except tk.TclError:
            return
        _open_current_research_tab_focus(self)

    def release(_event: tk.Event) -> None:
        state["start"] = None
        state["tab_index"] = None
        state["detached"] = False

    notebook.bind("<ButtonPress-1>", press, add="+")
    notebook.bind("<B1-Motion>", motion, add="+")
    notebook.bind("<ButtonRelease-1>", release, add="+")


def _bind_drag_handlers(widget: tk.Widget, press: Any, motion: Any, release: Any) -> None:
    widget.bind("<ButtonPress-1>", press, add="+")
    widget.bind("<B1-Motion>", motion, add="+")
    widget.bind("<ButtonRelease-1>", release, add="+")
    for child in widget.winfo_children():
        _bind_drag_handlers(child, press, motion, release)


def _open_summary_tearout(self: tk.Tk) -> None:
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is None:
        messagebox.showinfo("Detach At a Glance", "Run analysis first, then drag At a Glance out into its own window.")
        return
    existing = getattr(self, "schwab_research_summary_tearout_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                _refresh_summary_tearout(self, payload)
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass

    window = tk.Toplevel(getattr(self, "schwab_research_window", self))
    window.title(f"At a Glance - {payload.symbol}")
    window.geometry("1120x440")
    window.minsize(760, 320)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    toolbar = ttk.Frame(window, padding=(12, 9), style="Panel.TFrame")
    toolbar.grid(row=0, column=0, sticky="ew")
    toolbar.columnconfigure(0, weight=1)
    ttk.Label(toolbar, text=f"At a Glance - {payload.symbol}", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(toolbar, text="Resizable detached scorecard window", style="Subtle.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 8))
    ttk.Button(toolbar, text="Close", command=window.destroy).grid(row=0, column=2, sticky="e")

    body = ScrollableFrame(window, padding=16)
    body.grid(row=1, column=0, sticky="nsew")
    body.body.columnconfigure(0, weight=1)
    cards = ttk.Frame(body.body, style="Panel.TFrame")
    cards.grid(row=0, column=0, sticky="ew")
    strip = ttk.Frame(body.body, style="Panel.TFrame")
    strip.grid(row=1, column=0, sticky="ew", pady=(SECTION_GAP, 0))
    meters = ttk.Frame(body.body, style="Panel.TFrame")
    meters.grid(row=2, column=0, sticky="ew", pady=(SECTION_GAP, 0))
    meters.columnconfigure((0, 1), weight=1)
    bull_meter = ScoreMeter(meters)
    risk_meter = ScoreMeter(meters)
    bull_meter.grid(row=0, column=0, sticky="ew", padx=(0, 8))
    risk_meter.grid(row=0, column=1, sticky="ew")

    self.schwab_research_summary_tearout_window = window
    self.schwab_research_summary_tearout_cards = cards
    self.schwab_research_summary_tearout_strip = strip
    self.schwab_research_summary_tearout_bull_meter = bull_meter
    self.schwab_research_summary_tearout_risk_meter = risk_meter

    def on_close() -> None:
        self.schwab_research_summary_tearout_window = None
        self.schwab_research_summary_tearout_cards = None
        self.schwab_research_summary_tearout_strip = None
        self.schwab_research_summary_tearout_bull_meter = None
        self.schwab_research_summary_tearout_risk_meter = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", on_close)
    for child in toolbar.winfo_children():
        if _widget_class(child) == "TButton":
            try:
                child.configure(command=on_close)
            except tk.TclError:
                pass
    _refresh_summary_tearout(self, payload)


def _refresh_summary_tearout(self: tk.Tk, payload: _ResearchPayload) -> None:
    window = getattr(self, "schwab_research_summary_tearout_window", None)
    cards = getattr(self, "schwab_research_summary_tearout_cards", None)
    strip = getattr(self, "schwab_research_summary_tearout_strip", None)
    bull_meter = getattr(self, "schwab_research_summary_tearout_bull_meter", None)
    risk_meter = getattr(self, "schwab_research_summary_tearout_risk_meter", None)
    if window is None or cards is None or strip is None or bull_meter is None or risk_meter is None:
        return
    try:
        if not window.winfo_exists():
            return
    except tk.TclError:
        return
    decision = payload.decision
    technical_read = build_technical_at_glance_read(decision, payload.command_center_report)
    fundamental_verdict = build_fundamental_verdict(payload.fundamentals_text, payload.indicators, decision.macro_backdrop.label)
    conflict_read = build_cross_read_conflict_badge(fundamental_verdict.verdict, decision.macro_backdrop.label, technical_read)
    metric_grid(
        cards,
        [decision.overall, technical_read, _thesis_read_badge(decision), _preferred_vehicle_badge(decision), decision.risk_level, conflict_read, decision.macro_backdrop, decision.action_bias],
        columns=3,
        card_height=132,
        prominent_height=142,
        prominent_indexes={0, 1, 5},
    )
    labeled_value_grid(
        strip,
        {
            "Best thing": decision.top_things[0],
            "Biggest risk": decision.top_things[1],
            "Key trigger": decision.top_things[2],
        },
        columns=3,
    )
    bull_meter.set_score(technical_read.score, mode="direction", label=f"Technical: {technical_read.label} ({technical_read.score:.0f})")
    risk_meter.set_score(decision.risk_score, mode="risk", label=f"Risk Heat: {risk_heat_label(decision.risk_score)} ({decision.risk_score:.0f}/100)")


def _build_research_right_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(1, weight=1)

    top = ttk.Frame(parent, style="Panel.TFrame")
    top.grid(row=0, column=0, sticky="ew")
    top.columnconfigure((1, 2, 3, 4), weight=1)
    self.schwab_research_quote_var = tk.StringVar(value="Quote --")
    self.schwab_research_held_var = tk.StringVar(value="Held --")
    self.schwab_research_weight_var = tk.StringVar(value="Weight --")
    self.schwab_research_risk_var = tk.StringVar(value="Risk --")
    ttk.Label(top, text="Selected", style="Subtle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
    for index, var in enumerate((self.schwab_research_quote_var, self.schwab_research_held_var, self.schwab_research_weight_var, self.schwab_research_risk_var)):
        ttk.Label(top, textvariable=var, style="Chip.TLabel").grid(row=0, column=index + 1, sticky="ew", padx=(0 if index == 0 else 6, 0))
    self.schwab_research_summary_button = ttk.Button(top, text="Expand Summary", command=lambda app=self: _toggle_research_summary(app))
    self.schwab_research_summary_button.grid(row=0, column=5, sticky="e", padx=(8, 0))

    vertical = tk.PanedWindow(parent, orient=tk.VERTICAL, bg="#cbd5e1", bd=0, sashwidth=8, sashpad=4, showhandle=True)
    vertical.grid(row=1, column=0, sticky="nsew", pady=(SECTION_GAP, 0))
    self.schwab_research_vertical_paned = vertical

    summary_pane = ttk.Frame(vertical, style="Panel.TFrame", padding=(0, 0, 0, PANE_PAD))
    summary_pane.columnconfigure(0, weight=1)
    tabs_pane = ttk.Frame(vertical, style="Panel.TFrame")
    tabs_pane.columnconfigure(0, weight=1)
    tabs_pane.rowconfigure(1, weight=1)
    vertical.add(summary_pane, minsize=SUMMARY_PANE_MIN, stretch="never")
    vertical.add(tabs_pane, minsize=TAB_PANE_MIN, stretch="always")
    parent.after_idle(lambda: vertical.sash_place(0, 0, 190))

    summary_handle = ttk.Frame(summary_pane, style="Panel.TFrame")
    summary_handle.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    summary_handle.columnconfigure(0, weight=1)
    ttk.Label(summary_handle, text="At a Glance", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(summary_handle, text="Drag this title out to detach", style="Subtle.TLabel").grid(row=0, column=1, sticky="e")
    _bind_summary_detach_drag(self, summary_handle)

    glance = ttk.LabelFrame(summary_pane, text="", style="Card.TLabelframe")
    glance.grid(row=1, column=0, sticky="ew")
    glance.columnconfigure(0, weight=1)
    self.schwab_research_glance_cards = ttk.Frame(glance, style="Panel.TFrame")
    self.schwab_research_glance_cards.grid(row=0, column=0, sticky="ew")
    self.schwab_research_top_strip = ttk.Frame(glance, style="Panel.TFrame")
    self.schwab_research_top_strip.grid(row=1, column=0, sticky="ew", pady=(6, 0))
    meters = ttk.Frame(glance, style="Panel.TFrame")
    self.schwab_research_summary_meters = meters
    meters.grid(row=2, column=0, sticky="ew", pady=(6, 0))
    meters.columnconfigure((0, 1), weight=1)
    self.schwab_research_bull_bear_meter = ScoreMeter(meters)
    self.schwab_research_bull_bear_meter.grid(row=0, column=0, sticky="ew", padx=(0, 8))
    self.schwab_research_risk_meter = ScoreMeter(meters)
    self.schwab_research_risk_meter.grid(row=0, column=1, sticky="ew")
    self.schwab_research_top_strip.grid_remove()
    self.schwab_research_summary_meters.grid_remove()

    tab_toolbar = ttk.Frame(tabs_pane, style="Panel.TFrame")
    tab_toolbar.grid(row=0, column=0, sticky="ew")
    tab_toolbar.columnconfigure(0, weight=1)
    ttk.Label(tab_toolbar, text="Tabs - drag a tab label out to detach", style="Subtle.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Button(tab_toolbar, text="Focus Current Tab", command=lambda app=self: _open_current_research_tab_focus(app), style="Accent.TButton").grid(row=0, column=1, sticky="e")
    notebook = ttk.Notebook(tabs_pane)
    notebook.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
    self.schwab_research_tabs = notebook
    _bind_notebook_tab_detach_drag(self, notebook)

    self.schwab_research_overview_frame = _overview_tab(notebook)
    self.schwab_research_overview_text = self.schwab_research_overview_frame.detail_text  # type: ignore[attr-defined]
    self.schwab_trade_evidence_frame = _evidence_tab(self, notebook)
    self.schwab_research_technicals_frame = _technicals_tab(notebook)
    self.schwab_research_scenarios_frame = _scenarios_tab(self, notebook)
    self.schwab_research_options_frame = _options_strategy_tab(self, notebook)
    self.schwab_research_greeks_frame = _greeks_tab(self, notebook)
    self.schwab_research_earnings_frame = _earnings_tab(self, notebook)
    self.schwab_research_earnings_text = self.schwab_research_earnings_frame.detail_text  # type: ignore[attr-defined]
    self.schwab_research_fundamentals_frame = _section_summary_tab(notebook, "Fundamentals")
    self.schwab_research_fundamentals_text = self.schwab_research_fundamentals_frame.detail_text  # type: ignore[attr-defined]
    self.schwab_research_macro_frame = _macro_tab(notebook)
    self.schwab_research_macro_text = self.schwab_research_macro_frame.detail_text  # type: ignore[attr-defined]


def _scrollable_tab(notebook: ttk.Notebook, title: str) -> ttk.Frame:
    outer = ScrollableFrame(notebook, padding=TAB_PADDING)
    frame = outer.body
    frame.columnconfigure(0, weight=1)
    frame._scrollable_outer = outer  # type: ignore[attr-defined]
    notebook.add(outer, text=title)
    return frame


def _overview_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Overview")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    frame.operator = ttk.LabelFrame(frame, text="Operator View", style="Card.TLabelframe")  # type: ignore[attr-defined]
    frame.operator.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    frame.operator.columnconfigure(0, weight=1)  # type: ignore[attr-defined]
    frame.summary = ttk.LabelFrame(frame, text="Plain-English Summary", style="Card.TLabelframe")  # type: ignore[attr-defined]
    frame.summary.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    frame.summary.columnconfigure(0, weight=1)  # type: ignore[attr-defined]
    frame.checks = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.checks.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    frame.checks.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    frame.freshness = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.freshness.grid(row=4, column=0, sticky="ew", pady=(8, 0))
    frame.detail_text = _readout_launcher(frame, title="Overview Explanation", button_text="Open Overview Explanation", row=5)  # type: ignore[attr-defined]
    return frame


def _evidence_tab(self: tk.Tk, notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Evidence Desk")
    frame.columnconfigure(0, weight=1)
    controls = ttk.Frame(frame, style="Panel.TFrame")
    controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    controls.columnconfigure(1, weight=1)
    ttk.Button(controls, text="Save Snapshot", command=lambda app=self: _save_trade_evidence_snapshot(app)).grid(row=0, column=0, sticky="w")
    frame.snapshot_status_var = tk.StringVar(value="Snapshot --")  # type: ignore[attr-defined]
    ttk.Label(controls, textvariable=frame.snapshot_status_var, style="Subtle.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 0))  # type: ignore[attr-defined]
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=1, column=0, sticky="ew")
    frame.verdict = ttk.LabelFrame(frame, text="Verdict", style="Card.TLabelframe")  # type: ignore[attr-defined]
    frame.verdict.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    frame.verdict.columnconfigure(0, weight=1)  # type: ignore[attr-defined]
    score_box = ttk.LabelFrame(frame, text="Courtroom Scorecard", style="Card.TLabelframe")
    score_box.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    score_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(score_box, columns=("category", "grade", "read"), show="headings", height=12)
    _style_research_tree(tree)
    for column, label, width, anchor in (
        ("category", "Evidence", 190, tk.W),
        ("grade", "Grade", 80, tk.CENTER),
        ("read", "Read", 620, tk.W),
    ):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=anchor, stretch=column == "read")
    tree.grid(row=0, column=0, sticky="ew")
    y_scroll = ttk.Scrollbar(score_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    _add_horizontal_tree_scrollbar(score_box, tree, row=1)
    frame.score_tree = tree  # type: ignore[attr-defined]
    frame.evidence_columns = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.evidence_columns.grid(row=4, column=0, sticky="ew", pady=(8, 0))
    frame.evidence_columns.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    frame.risk_columns = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.risk_columns.grid(row=5, column=0, sticky="ew", pady=(8, 0))
    frame.risk_columns.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    frame.decision_columns = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.decision_columns.grid(row=6, column=0, sticky="ew", pady=(8, 0))
    frame.decision_columns.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    frame.detail_text = _readout_launcher(frame, title="Trade Evidence Report", button_text="Open Full Evidence Report", row=7, pady=(10, 0))  # type: ignore[attr-defined]
    return frame


def _section_summary_tab(notebook: ttk.Notebook, title: str) -> ttk.Frame:
    frame = _scrollable_tab(notebook, title)
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    frame.checks = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.checks.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    frame.detail_text = _readout_launcher(frame, title=f"{title} Explanation", button_text=f"Open {title} Explanation", row=2)  # type: ignore[attr-defined]
    return frame


def _macro_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Macro Context")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    frame.why = ttk.LabelFrame(frame, text="Why This Matters For This Symbol", style="Card.TLabelframe")  # type: ignore[attr-defined]
    frame.why.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    frame.why.columnconfigure(0, weight=1)  # type: ignore[attr-defined]
    tree_box = ttk.LabelFrame(frame, text="Official Macro Dashboard", style="Card.TLabelframe")
    tree_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("group", "metric", "latest", "prior", "change", "period", "source", "fresh", "read"), show="headings", height=11)
    _style_research_tree(tree)
    specs = (
        ("group", "Card", 130, tk.W),
        ("metric", "Metric", 150, tk.W),
        ("latest", "Latest", 85, tk.E),
        ("prior", "Prior", 85, tk.E),
        ("change", "Change", 80, tk.E),
        ("period", "Period", 95, tk.W),
        ("source", "Source", 100, tk.W),
        ("fresh", "Freshness", 90, tk.W),
        ("read", "Read", 95, tk.W),
    )
    for column, label, width, anchor in specs:
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=anchor, stretch=column in {"metric", "source"})
    tree.grid(row=0, column=0, sticky="ew")
    y_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    _add_horizontal_tree_scrollbar(tree_box, tree, row=1)
    frame.metric_tree = tree  # type: ignore[attr-defined]
    frame.interpretation = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.interpretation.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    frame.interpretation.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    raw = ttk.LabelFrame(frame, text="Raw Details", style="Card.TLabelframe")
    raw.grid(row=4, column=0, sticky="ew", pady=(8, 0))
    raw.columnconfigure(0, weight=1)
    frame.detail_text = _readout_launcher(raw, title="Macro Snapshot Explanation", button_text="Open Macro Snapshot Explanation", row=0, pady=(0, 0))  # type: ignore[attr-defined]
    return frame


def _earnings_tab(self: tk.Tk, notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Earnings / News")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    frame.checks = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.checks.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    frame.checks.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    source_box = ttk.LabelFrame(frame, text="Confirmed Sources / Search Helpers", style="Card.TLabelframe")
    source_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    source_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(source_box, columns=("source", "date", "url"), show="headings", height=8)
    _style_research_tree(tree)
    for column, label, width in (("source", "Source / Helper", 290), ("date", "Date", 110), ("url", "URL", 520)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.W, stretch=column == "url")
    tree.grid(row=0, column=0, sticky="ew")
    y_scroll = ttk.Scrollbar(source_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    _add_horizontal_tree_scrollbar(source_box, tree, row=1)
    tree.bind("<Double-1>", lambda event, source=tree: _open_source_tree_url(source, event), add="+")
    frame.source_tree = tree  # type: ignore[attr-defined]
    frame.detail_text = _readout_launcher(frame, title="Earnings Release Explanation", button_text="Open Earnings Release Explanation", row=3)  # type: ignore[attr-defined]
    launcher = frame.detail_text._readout_launcher  # type: ignore[attr-defined]
    ttk.Button(launcher, text="Refresh Earnings", command=lambda app=self: _refresh_earnings_sources(app)).grid(row=0, column=1, sticky="w", padx=(8, 0))
    return frame


def _open_source_tree_url(tree: ttk.Treeview, event: tk.Event | None = None) -> None:
    row_id = tree.identify_row(event.y) if event is not None else tree.focus()
    if row_id:
        tree.selection_set(row_id)
        tree.focus(row_id)
    selected = tree.focus() or (tree.selection()[0] if tree.selection() else "")
    if not selected:
        return
    values = tree.item(selected, "values")
    if len(values) < 3:
        return
    url = str(values[2]).strip()
    if url.startswith(("http://", "https://")):
        _open_external_url(url)


def _detail_text(parent: ttk.Frame) -> tk.Text:
    text = tk.Text(parent, wrap=tk.WORD, font=("Segoe UI", 10), padx=16, pady=14, relief=tk.FLAT, borderwidth=0, background="#f8fafc", foreground="#111827")
    scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=text.yview)
    text.configure(yscrollcommand=scroll.set)
    text._paired_scrollbar = scroll  # type: ignore[attr-defined]
    text.bind("<Map>", lambda _event, widget=text: widget._paired_scrollbar.grid(row=widget.grid_info().get("row", 0), column=1, sticky="ns"), add="+")  # type: ignore[attr-defined]
    return text


def _readout_launcher(parent: ttk.Frame, *, title: str, button_text: str, row: int, column: int = 0, sticky: str = "ew", pady: tuple[int, int] = (8, 0)) -> tk.Text:
    text = _readout_storage_text(parent)
    text._readout_title = title  # type: ignore[attr-defined]
    launcher = ttk.Frame(parent, style="Panel.TFrame")
    launcher.grid(row=row, column=column, sticky=sticky, pady=pady)
    launcher.columnconfigure(1, weight=1)
    ttk.Button(launcher, text=button_text, command=lambda widget=text: _open_readout_popout(widget), style="Accent.TButton").grid(row=0, column=0, sticky="w")
    text._readout_launcher = launcher  # type: ignore[attr-defined]
    return text


def _readout_storage_text(parent: ttk.Frame) -> tk.Text:
    text = tk.Text(
        parent,
        wrap=tk.WORD,
        font=("Segoe UI", 10),
        padx=16,
        pady=14,
        relief=tk.FLAT,
        borderwidth=0,
        background="#f8fafc",
        foreground="#111827",
    )
    return text


def _open_readout_popout(source: tk.Text) -> None:
    if _is_greek_readout_source(source):
        _open_greek_visual_popout(source)
        return

    existing = getattr(source, "_readout_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                _refresh_readout_popout(source)
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass

    window = tk.Toplevel(source.winfo_toplevel())
    title = str(getattr(source, "_readout_title", "Detailed Readout"))
    window.title(title)
    window.geometry(FOCUS_WINDOW_GEOMETRY)
    window.minsize(*FOCUS_WINDOW_MIN_SIZE)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    toolbar = ttk.Frame(window, padding=(10, 8), style="Panel.TFrame")
    toolbar.grid(row=0, column=0, sticky="ew")
    toolbar.columnconfigure(0, weight=1)
    ttk.Label(toolbar, text=title, font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
    close_button = ttk.Button(toolbar, text="Close", command=window.destroy)
    close_button.grid(row=0, column=1, sticky="e", padx=(8, 0))

    body = ttk.Frame(window, padding=(10, 0, 10, 10), style="Panel.TFrame")
    body.grid(row=1, column=0, sticky="nsew")
    body.columnconfigure(0, weight=1)
    body.rowconfigure(0, weight=1)
    target = tk.Text(
        body,
        wrap=tk.WORD,
        font=("Segoe UI", 11),
        padx=24,
        pady=22,
        relief=tk.FLAT,
        borderwidth=0,
        background="#f8fafc",
        foreground="#111827",
        insertbackground="#111827",
        selectbackground="#bfdbfe",
        spacing1=5,
        spacing2=2,
        spacing3=8,
    )
    target.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=target.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    target.configure(yscrollcommand=scrollbar.set)

    source._readout_window = window  # type: ignore[attr-defined]
    source._readout_popout_text = target  # type: ignore[attr-defined]

    def _on_close() -> None:
        source._readout_window = None  # type: ignore[attr-defined]
        source._readout_popout_text = None  # type: ignore[attr-defined]
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _on_close)
    close_button.configure(command=_on_close)
    _refresh_readout_popout(source)


def _refresh_readout_popout(source: tk.Text) -> None:
    visual_refresh = getattr(source, "_readout_popout_refresh", None)
    if callable(visual_refresh):
        visual_refresh()
        return

    target = getattr(source, "_readout_popout_text", None)
    if target is None:
        return
    try:
        content = source.get("1.0", tk.END).strip()
        if not content:
            content = "Run analysis first. The detailed readout will appear here."
        target.configure(state=tk.NORMAL)
        target.delete("1.0", tk.END)
        target.insert(tk.END, content)
        _apply_report_tags(target, content)
        target.configure(state=tk.DISABLED)
    except tk.TclError:
        return


def _is_greek_readout_source(source: tk.Text) -> bool:
    title = str(getattr(source, "_readout_title", ""))
    return title == "Option Sensitivities Explanation" or hasattr(source, "_greek_summary")


def _open_greek_visual_popout(source: tk.Text) -> None:
    existing = getattr(source, "_readout_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                _refresh_readout_popout(source)
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass

    window = tk.Toplevel(source.winfo_toplevel())
    title = "Option Sensitivities Visual Guide"
    window.title(title)
    window.geometry("1320x880")
    window.minsize(1060, 720)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    toolbar = ttk.Frame(window, padding=(12, 9), style="Panel.TFrame")
    toolbar.grid(row=0, column=0, sticky="ew")
    toolbar.columnconfigure(0, weight=1)
    ttk.Label(toolbar, text=title, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(
        toolbar,
        text="Visual Greek readout only. No order is previewed or submitted.",
        style="Subtle.TLabel",
    ).grid(row=0, column=1, sticky="e", padx=(12, 8))
    ttk.Button(toolbar, text="Close", command=window.destroy).grid(row=0, column=2, sticky="e")

    scroll = ScrollableFrame(window, padding=14)
    scroll.grid(row=1, column=0, sticky="nsew")
    scroll.body.columnconfigure(0, weight=1)

    def refresh() -> None:
        try:
            clear_children(scroll.body)
            _build_greek_visual_popout_body(scroll.body, source)
        except tk.TclError:
            return

    source._readout_window = window  # type: ignore[attr-defined]
    source._readout_popout_text = scroll.body  # type: ignore[attr-defined]
    source._readout_popout_refresh = refresh  # type: ignore[attr-defined]

    def _on_close() -> None:
        source._readout_window = None  # type: ignore[attr-defined]
        source._readout_popout_text = None  # type: ignore[attr-defined]
        source._readout_popout_refresh = None  # type: ignore[attr-defined]
        window.destroy()

    for child in toolbar.winfo_children():
        if _widget_class(child) == "TButton":
            try:
                child.configure(command=_on_close)
            except tk.TclError:
                pass
    window.protocol("WM_DELETE_WINDOW", _on_close)
    refresh()


def _build_greek_visual_popout_body(parent: ttk.Frame, source: tk.Text) -> None:
    summary = getattr(source, "_greek_summary", None)
    payload = getattr(source, "_greek_payload", None)
    if summary is None or not isinstance(summary, GreekSummary) or not summary.rows:
        empty = tk.Frame(parent, bg=GREEK_VISUAL_BG, highlightbackground=GREEK_BORDER, highlightthickness=1)
        empty.grid(row=0, column=0, sticky="ew")
        empty.columnconfigure(0, weight=1)
        tk.Label(empty, text="Load / Refresh Greeks to build the visual guide.", bg=GREEK_VISUAL_BG, fg=GREEK_TEXT, font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(18, 6))
        tk.Label(empty, text="The explanation will switch from plain text to charts once a Schwab option chain is available.", bg=GREEK_VISUAL_BG, fg=GREEK_MUTED, font=("Segoe UI", 10), wraplength=760, justify=tk.LEFT).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 18))
        return

    active = _active_greek_snapshot(summary)
    symbol = (getattr(payload, "symbol", None) or summary.underlying or getattr(active, "underlying", "") or "Symbol").upper()
    classification = classify_greek_contract(active, summary.underlying_price) if active is not None else "No active contract"

    _build_greek_visual_hero(parent, symbol, summary, active, classification)

    metrics = ttk.Frame(parent, style="Panel.TFrame")
    metrics.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    for column in range(5):
        metrics.columnconfigure(column, weight=1, uniform="greek_metric_cards")
    if active is not None:
        for column, spec in enumerate(_greek_visual_metric_specs(active)):
            _greek_metric_visual_card(metrics, spec).grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))

    chart_grid = ttk.Frame(parent, style="Panel.TFrame")
    chart_grid.grid(row=2, column=0, sticky="ew", pady=(10, 0))
    chart_grid.columnconfigure((0, 1), weight=1, uniform="greek_charts")
    sensitivity_box = ttk.LabelFrame(chart_grid, text="Dollar Sensitivity Map", style="Card.TLabelframe")
    pnl_box = ttk.LabelFrame(chart_grid, text="Move vs Greek P/L Estimate", style="Card.TLabelframe")
    sensitivity_box.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    pnl_box.grid(row=0, column=1, sticky="nsew")
    sensitivity_box.columnconfigure(0, weight=1)
    pnl_box.columnconfigure(0, weight=1)
    sensitivity_canvas = tk.Canvas(sensitivity_box, height=300, bg=GREEK_VISUAL_BG, highlightthickness=0)
    pnl_canvas = tk.Canvas(pnl_box, height=300, bg=GREEK_VISUAL_BG, highlightthickness=0)
    sensitivity_canvas.grid(row=0, column=0, sticky="ew")
    pnl_canvas.grid(row=0, column=0, sticky="ew")
    sensitivity_canvas._greek_summary = summary  # type: ignore[attr-defined]
    pnl_canvas._greek_summary = summary  # type: ignore[attr-defined]
    sensitivity_canvas.bind("<Configure>", lambda event: _draw_greek_sensitivity_canvas(event.widget), add="+")
    pnl_canvas.bind("<Configure>", lambda event: _draw_greek_pnl_canvas(event.widget), add="+")
    _draw_greek_sensitivity_canvas(sensitivity_canvas)
    _draw_greek_pnl_canvas(pnl_canvas)

    insight_grid = ttk.Frame(parent, style="Panel.TFrame")
    insight_grid.grid(row=3, column=0, sticky="ew", pady=(10, 0))
    insight_grid.columnconfigure((0, 1, 2), weight=1, uniform="greek_insights")
    _greek_text_panel(insight_grid, "Plain-English Read", summary.plain_english or ["No plain-English read is available yet."], 0)
    _greek_text_panel(insight_grid, "Theta Offset", _theta_offset_visual_lines(active, symbol), 1)
    _greek_text_panel(insight_grid, "Sources / Caveats", _greek_source_lines(summary), 2)

    ranks = rank_greek_contracts(summary, active.option_type if active is not None else None)
    if ranks:
        rank_box = ttk.LabelFrame(parent, text="Best Nearby Contracts By Greek Efficiency", style="Card.TLabelframe")
        rank_box.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        rank_box.columnconfigure(0, weight=1)
        for index, rank in enumerate(ranks[:5], start=1):
            _rank_row(rank_box, index, rank.label, rank.reason, rank.score).grid(row=index - 1, column=0, sticky="ew", pady=(0 if index == 1 else 6, 0))


def _build_greek_visual_hero(parent: ttk.Frame, symbol: str, summary: GreekSummary, active: OptionGreekSnapshot | None, classification: str) -> None:
    hero = tk.Frame(parent, bg="#eff6ff", highlightbackground="#bfdbfe", highlightthickness=1)
    hero.grid(row=0, column=0, sticky="ew")
    hero.columnconfigure(0, weight=1)
    hero.columnconfigure(1, weight=0)
    contract = _greek_contract_short_label(active)
    title = f"{symbol} Greeks: {contract}" if active is not None else f"{symbol} Greeks"
    tk.Label(hero, text=title, bg="#eff6ff", fg="#1e3a8a", font=("Segoe UI", 18, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))
    subtitle = f"Underlying {_money(summary.underlying_price)}. {classification}. Source mix: {active.source_summary if active is not None else 'Unavailable'}."
    tk.Label(hero, text=subtitle, bg="#eff6ff", fg=GREEK_TEXT, font=("Segoe UI", 10), anchor="w", justify=tk.LEFT, wraplength=820).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
    badge = tk.Frame(hero, bg="#dbeafe", highlightbackground=GREEK_BLUE, highlightthickness=1)
    badge.grid(row=0, column=1, rowspan=2, sticky="e", padx=18, pady=16)
    tk.Label(badge, text="Decision Support", bg="#dbeafe", fg="#1d4ed8", font=("Segoe UI", 9, "bold")).pack(padx=12, pady=(8, 2))
    tk.Label(badge, text="No order action", bg="#dbeafe", fg="#1e3a8a", font=("Segoe UI", 11, "bold")).pack(padx=12, pady=(0, 8))


def _greek_visual_metric_specs(active: OptionGreekSnapshot) -> list[dict[str, Any]]:
    return [
        {
            "title": "Delta",
            "value": _signed_number(active.delta.value, digits=3),
            "caption": _greek_dollar_caption(active, "Delta"),
            "color": GREEK_BLUE,
            "source": active.delta.source,
        },
        {
            "title": "Theta",
            "value": _signed_number(active.theta.value, digits=3),
            "caption": _greek_dollar_caption(active, "Theta"),
            "color": GREEK_RED if (active.theta.value or 0) < 0 else GREEK_AMBER,
            "source": active.theta.source,
        },
        {
            "title": "Vega",
            "value": _signed_number(active.vega.value, digits=3),
            "caption": _greek_dollar_caption(active, "Vega"),
            "color": GREEK_TEAL,
            "source": active.vega.source,
        },
        {
            "title": "Gamma",
            "value": _signed_number(active.gamma.value, digits=4),
            "caption": _greek_dollar_caption(active, "Gamma"),
            "color": GREEK_PURPLE,
            "source": active.gamma.source,
        },
        {
            "title": "Rho",
            "value": _signed_number(active.rho.value, digits=3),
            "caption": _greek_dollar_caption(active, "Rho"),
            "color": GREEK_AMBER,
            "source": active.rho.source,
        },
    ]


def _greek_metric_visual_card(parent: tk.Widget, spec: dict[str, Any]) -> tk.Frame:
    frame = tk.Frame(parent, bg=GREEK_VISUAL_BG, highlightbackground=GREEK_BORDER, highlightthickness=1, height=126)
    frame.grid_propagate(False)
    frame.columnconfigure(0, weight=1)
    tk.Frame(frame, bg=str(spec["color"]), height=5).grid(row=0, column=0, sticky="ew")
    tk.Label(frame, text=str(spec["title"]).upper(), bg=GREEK_VISUAL_BG, fg=GREEK_MUTED, font=("Segoe UI", 8, "bold"), anchor="w").grid(row=1, column=0, sticky="ew", padx=12, pady=(9, 0))
    tk.Label(frame, text=str(spec["value"]), bg=GREEK_VISUAL_BG, fg=str(spec["color"]), font=("Segoe UI", 18, "bold"), anchor="w").grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 0))
    tk.Label(frame, text=str(spec["caption"]), bg=GREEK_VISUAL_BG, fg=GREEK_TEXT, font=("Segoe UI", 8), anchor="nw", justify=tk.LEFT, wraplength=180).grid(row=3, column=0, sticky="nsew", padx=12, pady=(3, 0))
    tk.Label(frame, text=str(spec["source"]), bg=GREEK_VISUAL_BG, fg=GREEK_MUTED, font=("Segoe UI", 8, "italic"), anchor="w").grid(row=4, column=0, sticky="ew", padx=12, pady=(4, 8))
    return frame


def _greek_text_panel(parent: ttk.Frame, title: str, rows: list[str], column: int) -> None:
    box = tk.Frame(parent, bg=GREEK_VISUAL_BG, highlightbackground=GREEK_BORDER, highlightthickness=1)
    box.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
    box.columnconfigure(0, weight=1)
    tk.Label(box, text=title.upper(), bg=GREEK_VISUAL_BG, fg=GREEK_MUTED, font=("Segoe UI", 8, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))
    for index, row in enumerate(rows[:7], start=1):
        tk.Label(box, text=row, bg=GREEK_VISUAL_BG, fg=GREEK_TEXT, font=("Segoe UI", 9), anchor="w", justify=tk.LEFT, wraplength=320).grid(row=index, column=0, sticky="ew", padx=12, pady=(0, 4))


def _rank_row(parent: ttk.Frame, index: int, label: str, reason: str, score: float) -> tk.Frame:
    frame = tk.Frame(parent, bg=GREEK_VISUAL_BG, highlightbackground=GREEK_BORDER, highlightthickness=1)
    frame.columnconfigure(1, weight=1)
    color = GREEK_GREEN if index == 1 else GREEK_BLUE if index <= 3 else GREEK_MUTED
    tk.Label(frame, text=str(index), bg=color, fg="#ffffff", font=("Segoe UI", 11, "bold"), width=3).grid(row=0, column=0, rowspan=2, sticky="nsw")
    tk.Label(frame, text=label, bg=GREEK_VISUAL_BG, fg=GREEK_TEXT, font=("Segoe UI", 10, "bold"), anchor="w").grid(row=0, column=1, sticky="ew", padx=10, pady=(7, 0))
    tk.Label(frame, text=reason, bg=GREEK_VISUAL_BG, fg=GREEK_MUTED, font=("Segoe UI", 9), anchor="w", justify=tk.LEFT, wraplength=920).grid(row=1, column=1, sticky="ew", padx=10, pady=(2, 7))
    tk.Label(frame, text=f"Score {score:.1f}", bg=GREEK_VISUAL_BG, fg=color, font=("Segoe UI", 9, "bold"), anchor="e").grid(row=0, column=2, rowspan=2, sticky="e", padx=10)
    return frame


def _active_greek_snapshot(summary: GreekSummary) -> OptionGreekSnapshot | None:
    return summary.selected or summary.nearest_call or summary.nearest_put


def _greek_dollar_caption(active: OptionGreekSnapshot, greek: str) -> str:
    meanings = dict(greek_dollar_meanings(active))
    for label, meaning in meanings.items():
        if label.lower().startswith(greek.lower()):
            return meaning
    return "--"


def _theta_offset_visual_lines(active: OptionGreekSnapshot | None, symbol: str) -> list[str]:
    if active is None:
        return ["No active contract is loaded."]
    offset = theta_offset_moves(active)
    if offset.one_day_move is None or offset.full_window_move is None:
        return ["Theta offset cannot be calculated because delta or theta is missing."]
    direction = "rise" if active.option_type == "call" else "fall"
    sign = "+" if active.option_type == "call" else "-"
    fallback = " DTE was missing, so the multi-day estimate uses 5 days." if offset.used_dte_fallback else ""
    return [
        f"After 1 day, {symbol} needs to {direction} about {sign}${offset.one_day_move:,.2f} to offset theta.",
        f"Over {offset.full_window_days} days, it needs about {sign}${offset.full_window_move:,.2f} before premium and IV changes.{fallback}",
    ]


def _draw_greek_sensitivity_canvas(canvas: tk.Canvas) -> None:
    summary = getattr(canvas, "_greek_summary", None)
    canvas.delete("all")
    width = max(canvas.winfo_width(), 360)
    height = max(canvas.winfo_height(), 220)
    active = _active_greek_snapshot(summary) if isinstance(summary, GreekSummary) else None
    if active is None:
        canvas.create_text(12, 16, text="Load Greeks to see sensitivity bars.", anchor="nw", fill=GREEK_MUTED, font=("Segoe UI", 10))
        return

    rows = [
        ("Delta", _scaled_greek_value(active.delta.value), "$ / +$1 stock move", GREEK_BLUE),
        ("Theta", _scaled_greek_value(active.theta.value), "$ / day", GREEK_RED if (active.theta.value or 0) < 0 else GREEK_AMBER),
        ("Vega", _scaled_greek_value(active.vega.value), "$ / +1 IV point", GREEK_TEAL),
        ("Gamma", _scaled_greek_value(active.gamma.value), "delta points / $1", GREEK_PURPLE),
        ("Rho", _scaled_greek_value(active.rho.value), "$ / +1 rate point", GREEK_AMBER),
    ]
    usable = [abs(value) for _label, value, _unit, _color in rows if value is not None]
    max_abs = max(usable) if usable else 1.0
    center = int(width * 0.52)
    left = 145
    right = width - 106
    available = max(height - 76, 160)
    row_step = max(35, min(46, available // max(len(rows), 1)))
    start_y = 50
    canvas.create_line(center, 34, center, height - 24, fill="#cbd5e1")
    canvas.create_text(14, 12, text="Positive values help; negative values hurt for that unit move.", anchor="nw", fill=GREEK_MUTED, font=("Segoe UI", 9))
    for index, (label, value, unit, color) in enumerate(rows):
        y = start_y + index * row_step
        canvas.create_text(14, y, text=label, anchor="nw", fill=GREEK_TEXT, font=("Segoe UI", 10, "bold"))
        canvas.create_text(14, y + 17, text=unit, anchor="nw", fill=GREEK_MUTED, font=("Segoe UI", 8))
        if value is None:
            canvas.create_text(center + 8, y + 4, text="Unavailable", anchor="nw", fill=GREEK_MUTED, font=("Segoe UI", 9))
            continue
        span = max(center - left, right - center)
        bar = int((abs(value) / max_abs) * span)
        x0, x1 = (center - bar, center) if value < 0 else (center, center + bar)
        canvas.create_rectangle(x0, y + 8, x1, y + 23, fill=color, outline="")
        canvas.create_text(width - 14, y + 6, text=_greek_signed_money_or_points(value, unit), anchor="ne", fill=color, font=("Segoe UI", 10, "bold"))


def _draw_greek_pnl_canvas(canvas: tk.Canvas) -> None:
    summary = getattr(canvas, "_greek_summary", None)
    canvas.delete("all")
    width = max(canvas.winfo_width(), 360)
    height = max(canvas.winfo_height(), 220)
    active = _active_greek_snapshot(summary) if isinstance(summary, GreekSummary) else None
    if active is None:
        canvas.create_text(12, 16, text="No active contract is loaded.", anchor="nw", fill=GREEK_MUTED, font=("Segoe UI", 10))
        return
    rows = greek_approximation_rows(active)
    if not rows:
        canvas.create_text(12, 16, text="P/L approximation needs delta, gamma, and theta.", anchor="nw", fill=GREEK_MUTED, font=("Segoe UI", 10))
        return

    max_abs = max(max(abs(row.one_day_pnl), abs(row.full_window_pnl)) for row in rows) or 1.0
    center = int(width * 0.52)
    left = 145
    right = width - 96
    row_height = max(28, int((height - 54) / max(len(rows), 1)))
    canvas.create_text(14, 12, text="Light bar = 1 day. Dark bar = full DTE window.", anchor="nw", fill=GREEK_MUTED, font=("Segoe UI", 9))
    canvas.create_line(center, 34, center, height - 18, fill="#cbd5e1")
    for index, row in enumerate(rows):
        y = 46 + index * row_height
        canvas.create_text(14, y, text=_move_label_for_visual(row.move), anchor="nw", fill=GREEK_TEXT, font=("Segoe UI", 9, "bold"))
        _draw_signed_bar(canvas, center, left, right, y + 1, row.one_day_pnl, max_abs, light=True)
        _draw_signed_bar(canvas, center, left, right, y + 15, row.full_window_pnl, max_abs, light=False)
        canvas.create_text(width - 14, y + 4, text=_greek_signed_money(row.full_window_pnl), anchor="ne", fill=GREEK_GREEN if row.full_window_pnl >= 0 else GREEK_RED, font=("Segoe UI", 9, "bold"))


def _draw_signed_bar(canvas: tk.Canvas, center: int, left: int, right: int, y: int, value: float, max_abs: float, *, light: bool) -> None:
    span = max(center - left, right - center)
    bar = int((abs(value) / max_abs) * span)
    x0, x1 = (center - bar, center) if value < 0 else (center, center + bar)
    color = "#86efac" if value >= 0 and light else GREEK_GREEN if value >= 0 else "#fca5a5" if light else GREEK_RED
    canvas.create_rectangle(x0, y, x1, y + 10, fill=color, outline="")


def _scaled_greek_value(value: float | None) -> float | None:
    return None if value is None else value * 100.0


def _greek_signed_money_or_points(value: float, unit: str) -> str:
    if "delta points" in unit:
        return f"{value:+.1f} pts"
    return _greek_signed_money(value)


def _greek_signed_money(value: float) -> str:
    prefix = "-$" if value < 0 else "+$"
    return f"{prefix}{abs(value):,.0f}"


def _move_label_for_visual(move: float) -> str:
    if abs(move) < 0.001:
        return "$0 move"
    prefix = "+$" if move > 0 else "-$"
    return f"{prefix}{abs(move):g}"


def _widget_class(widget: tk.Widget) -> str:
    try:
        return str(widget.winfo_class())
    except Exception:
        return ""


def _sync_readout_window_title(source: tk.Text) -> None:
    window = getattr(source, "_readout_window", None)
    if window is None:
        return
    try:
        if window.winfo_exists():
            window.title(str(getattr(source, "_readout_title", "Detailed Readout")))
    except tk.TclError:
        return


def _report_tab(notebook: ttk.Notebook, title: str) -> tk.Text:
    frame = ttk.Frame(notebook, style="Panel.TFrame", padding=10)
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    text = tk.Text(frame, wrap=tk.WORD, font=("Segoe UI", 10), padx=16, pady=14, relief=tk.FLAT, borderwidth=0, background="#f8fafc", foreground="#111827")
    text.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=scroll.set)
    notebook.add(frame, text=title)
    return text


def _technicals_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Technicals")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    frame.meters = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.meters.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    frame.meters.columnconfigure((0, 1, 2), weight=1)  # type: ignore[attr-defined]
    frame.bull_meter = ScoreMeter(frame.meters)  # type: ignore[attr-defined]
    frame.bull_meter.grid(row=0, column=0, sticky="ew", padx=(0, 8))  # type: ignore[attr-defined]
    frame.momentum_meter = ScoreMeter(frame.meters)  # type: ignore[attr-defined]
    frame.momentum_meter.grid(row=0, column=1, sticky="ew", padx=(0, 8))  # type: ignore[attr-defined]
    frame.risk_meter = ScoreMeter(frame.meters)  # type: ignore[attr-defined]
    frame.risk_meter.grid(row=0, column=2, sticky="ew")  # type: ignore[attr-defined]

    capital_box = ttk.LabelFrame(frame, text="Capital Structure / Supply", style="Card.TLabelframe")
    capital_box.grid(row=2, column=0, sticky="ew", pady=(10, 0))
    capital_box.columnconfigure(0, weight=1)
    ttk.Label(capital_box, text=CAPITAL_STRUCTURE_LEVEL_DISCLAIMER, style="Subtle.TLabel", wraplength=1120, justify=tk.LEFT).grid(row=0, column=0, sticky="ew", pady=(0, 6))
    capital_cards = ttk.Frame(capital_box, style="Panel.TFrame")
    capital_cards.grid(row=1, column=0, sticky="ew")
    capital_grid = ttk.Frame(capital_box, style="Panel.TFrame")
    capital_grid.grid(row=2, column=0, sticky="ew", pady=(4, 0))
    capital_grid.columnconfigure((0, 1), weight=1)

    supply_box = ttk.LabelFrame(capital_grid, text="Supply Level / Recommendation", style="Card.TLabelframe")
    supply_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    supply_box.columnconfigure(0, weight=1)
    supply_tree = ttk.Treeview(
        supply_box,
        columns=("label", "price", "distance", "read"),
        show="headings",
        height=4,
    )
    _style_research_tree(supply_tree)
    for column, label, width, anchor in (
        ("label", "Level Label", 170, tk.W),
        ("price", "Price", 95, tk.E),
        ("distance", "Distance From Latest", 145, tk.E),
        ("read", "Read / Recommendation", 440, tk.W),
    ):
        supply_tree.heading(column, text=label)
        supply_tree.column(column, width=width, anchor=anchor, stretch=column == "read")
    supply_tree.grid(row=0, column=0, sticky="ew")
    supply_scroll = ttk.Scrollbar(supply_box, orient=tk.VERTICAL, command=supply_tree.yview)
    supply_scroll.grid(row=0, column=1, sticky="ns")
    supply_tree.configure(yscrollcommand=supply_scroll.set)
    _add_horizontal_tree_scrollbar(supply_box, supply_tree, row=1)

    capital_note_box = ttk.LabelFrame(capital_grid, text="Explanation / Warnings", style="Card.TLabelframe")
    capital_note_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
    capital_note_box.columnconfigure(0, weight=1)
    capital_note_tree = ttk.Treeview(
        capital_note_box,
        columns=("kind", "detail"),
        show="headings",
        height=4,
    )
    _style_research_tree(capital_note_tree)
    for column, label, width, anchor in (
        ("kind", "Type", 135, tk.W),
        ("detail", "Detail", 650, tk.W),
    ):
        capital_note_tree.heading(column, text=label)
        capital_note_tree.column(column, width=width, anchor=anchor, stretch=column == "detail")
    capital_note_tree.grid(row=0, column=0, sticky="ew")
    capital_note_scroll = ttk.Scrollbar(capital_note_box, orient=tk.VERTICAL, command=capital_note_tree.yview)
    capital_note_scroll.grid(row=0, column=1, sticky="ns")
    capital_note_tree.configure(yscrollcommand=capital_note_scroll.set)
    _add_horizontal_tree_scrollbar(capital_note_box, capital_note_tree, row=1)

    timeframe_box = ttk.LabelFrame(frame, text="Timeframe Stack", style="Card.TLabelframe")
    timeframe_box.grid(row=3, column=0, sticky="ew", pady=(10, 0))
    timeframe_box.columnconfigure(0, weight=1)
    timeframe_tree = ttk.Treeview(
        timeframe_box,
        columns=("timeframe", "role", "trend", "momentum", "volume", "vwap", "atr", "range", "read"),
        show="headings",
        height=6,
    )
    _style_research_tree(timeframe_tree)
    for column, label, width, anchor in (
        ("timeframe", "Timeframe", 110, tk.W),
        ("role", "Role", 84, tk.W),
        ("trend", "Trend", 150, tk.W),
        ("momentum", "Momentum", 170, tk.W),
        ("volume", "Volume", 170, tk.W),
        ("vwap", "VWAP Dist", 84, tk.E),
        ("atr", "ATR%", 70, tk.E),
        ("range", "Range", 108, tk.W),
        ("read", "Key Read", 390, tk.W),
    ):
        timeframe_tree.heading(column, text=label)
        timeframe_tree.column(column, width=width, anchor=anchor, stretch=column in {"trend", "momentum", "volume", "read"})
    timeframe_tree.grid(row=0, column=0, sticky="ew")
    timeframe_scroll = ttk.Scrollbar(timeframe_box, orient=tk.VERTICAL, command=timeframe_tree.yview)
    timeframe_scroll.grid(row=0, column=1, sticky="ns")
    timeframe_tree.configure(yscrollcommand=timeframe_scroll.set)
    _add_horizontal_tree_scrollbar(timeframe_box, timeframe_tree, row=1)

    prc_box = ttk.LabelFrame(frame, text="PRC Pressure Line - Synthetic Internal Indicator", style="Card.TLabelframe")
    prc_box.grid(row=4, column=0, sticky="ew", pady=(10, 0))
    prc_box.columnconfigure(0, weight=1)
    ttk.Label(prc_box, text=PRC_PRESSURE_LINE_NOTICE, style="Subtle.TLabel", wraplength=1120, justify=tk.LEFT).grid(row=0, column=0, sticky="ew", pady=(0, 6))
    prc_tree = ttk.Treeview(
        prc_box,
        columns=("timeframe", "price", "line", "distance", "slope", "read", "confidence"),
        show="headings",
        height=5,
    )
    _style_research_tree(prc_tree)
    for column, label, width, anchor in (
        ("timeframe", "Timeframe", 120, tk.W),
        ("price", "Price", 94, tk.E),
        ("line", "PRC Line", 104, tk.E),
        ("distance", "Distance", 92, tk.E),
        ("slope", "Slope", 82, tk.E),
        ("read", "Read", 320, tk.W),
        ("confidence", "Confidence", 95, tk.W),
    ):
        prc_tree.heading(column, text=label)
        prc_tree.column(column, width=width, anchor=anchor, stretch=column == "read")
    prc_tree.grid(row=1, column=0, sticky="ew")
    prc_scroll = ttk.Scrollbar(prc_box, orient=tk.VERTICAL, command=prc_tree.yview)
    prc_scroll.grid(row=1, column=1, sticky="ns")
    prc_tree.configure(yscrollcommand=prc_scroll.set)
    _add_horizontal_tree_scrollbar(prc_box, prc_tree, row=2)

    command_grid = ttk.Frame(frame, style="Panel.TFrame")
    command_grid.grid(row=5, column=0, sticky="ew", pady=(10, 0))
    command_grid.columnconfigure((0, 1), weight=1)

    score_box = ttk.LabelFrame(command_grid, text="Score Breakdown", style="Card.TLabelframe")
    score_box.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    score_box.columnconfigure(0, weight=1)
    score_tree = ttk.Treeview(score_box, columns=("component", "score", "why"), show="headings", height=8)
    _style_research_tree(score_tree)
    for column, label, width, anchor in (
        ("component", "Component", 150, tk.W),
        ("score", "Score", 72, tk.E),
        ("why", "Why", 430, tk.W),
    ):
        score_tree.heading(column, text=label)
        score_tree.column(column, width=width, anchor=anchor, stretch=column == "why")
    score_tree.grid(row=0, column=0, sticky="ew")
    score_scroll = ttk.Scrollbar(score_box, orient=tk.VERTICAL, command=score_tree.yview)
    score_scroll.grid(row=0, column=1, sticky="ns")
    score_tree.configure(yscrollcommand=score_scroll.set)
    _add_horizontal_tree_scrollbar(score_box, score_tree, row=1)

    ticket_box = ttk.LabelFrame(command_grid, text="Execution / Ticket Check", style="Card.TLabelframe")
    ticket_box.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
    ticket_box.columnconfigure(0, weight=1)
    ticket_tree = ttk.Treeview(ticket_box, columns=("field", "read", "detail"), show="headings", height=8)
    _style_research_tree(ticket_tree)
    for column, label, width, anchor in (
        ("field", "Field", 140, tk.W),
        ("read", "Read", 180, tk.W),
        ("detail", "Detail", 430, tk.W),
    ):
        ticket_tree.heading(column, text=label)
        ticket_tree.column(column, width=width, anchor=anchor, stretch=column == "detail")
    ticket_tree.grid(row=0, column=0, sticky="ew")
    ticket_scroll = ttk.Scrollbar(ticket_box, orient=tk.VERTICAL, command=ticket_tree.yview)
    ticket_scroll.grid(row=0, column=1, sticky="ns")
    ticket_tree.configure(yscrollcommand=ticket_scroll.set)
    _add_horizontal_tree_scrollbar(ticket_box, ticket_tree, row=1)

    warning_box = ttk.LabelFrame(frame, text="Warnings / Data Quality", style="Card.TLabelframe")
    warning_box.grid(row=6, column=0, sticky="ew", pady=(10, 0))
    warning_box.columnconfigure(0, weight=1)
    warning_tree = ttk.Treeview(warning_box, columns=("source", "warning"), show="headings", height=4)
    _style_research_tree(warning_tree)
    for column, label, width in (("source", "Source", 180), ("warning", "Warning", 780)):
        warning_tree.heading(column, text=label)
        warning_tree.column(column, width=width, anchor=tk.W, stretch=column == "warning")
    warning_tree.grid(row=0, column=0, sticky="ew")
    warning_scroll = ttk.Scrollbar(warning_box, orient=tk.VERTICAL, command=warning_tree.yview)
    warning_scroll.grid(row=0, column=1, sticky="ns")
    warning_tree.configure(yscrollcommand=warning_scroll.set)
    _add_horizontal_tree_scrollbar(warning_box, warning_tree, row=1)
    warning_box.grid_remove()

    frame.chart_readout = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.chart_readout.grid(row=7, column=0, sticky="ew", pady=(10, 0))  # type: ignore[attr-defined]
    frame.chart_readout.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]

    tree_box = ttk.LabelFrame(frame, text="Existing Indicator Readout", style="Card.TLabelframe")
    tree_box.grid(row=8, column=0, sticky="ew", pady=(10, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("metric", "value", "read"), show="headings", height=12)
    _style_research_tree(tree)
    for column, label, width in (("metric", "Metric", 160), ("value", "Value", 140), ("read", "Readout", 360)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.W if column != "value" else tk.E, stretch=True)
    tree.grid(row=0, column=0, sticky="ew")
    y_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    _add_horizontal_tree_scrollbar(tree_box, tree, row=1)
    text = _readout_launcher(frame, title="Technical Readout", button_text="Open Technical Readout", row=9, pady=(10, 0))
    frame.capital_cards = capital_cards  # type: ignore[attr-defined]
    frame.capital_supply_tree = supply_tree  # type: ignore[attr-defined]
    frame.capital_note_tree = capital_note_tree  # type: ignore[attr-defined]
    frame.timeframe_tree = timeframe_tree  # type: ignore[attr-defined]
    frame.prc_tree = prc_tree  # type: ignore[attr-defined]
    frame.score_tree = score_tree  # type: ignore[attr-defined]
    frame.ticket_tree = ticket_tree  # type: ignore[attr-defined]
    frame.warning_box = warning_box  # type: ignore[attr-defined]
    frame.warning_tree = warning_tree  # type: ignore[attr-defined]
    frame.indicator_tree = tree  # type: ignore[attr-defined]
    frame.technical_notes_text = text  # type: ignore[attr-defined]
    frame.detail_text = text  # type: ignore[attr-defined]
    return frame


def _scenarios_tab(self: tk.Tk, notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Risk Scenarios")
    frame.columnconfigure(0, weight=1)
    controls = ttk.Frame(frame, style="Panel.TFrame")
    controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    controls.columnconfigure(1, weight=1)
    ttk.Label(controls, text="Scenario basis", style="Subtle.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(controls, textvariable=self.schwab_research_scenario_basis_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(8, 18))
    ttk.Label(controls, text="Generated risk $", style="Subtle.TLabel").grid(row=0, column=2, sticky="w")
    ttk.Entry(controls, textvariable=self.schwab_research_max_risk_var, width=13, state="readonly").grid(row=0, column=3, sticky="w", padx=(6, 18))
    ttk.Button(controls, text="Refresh Risk", command=lambda app=self: _recalculate_research_scenarios(app)).grid(row=0, column=4, sticky="w")
    ttk.Button(controls, text="Use Model Stock Plan", command=lambda app=self: _use_model_stock_plan(app)).grid(row=0, column=5, sticky="w", padx=(8, 0))
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    planner_box = ttk.LabelFrame(frame, text="Move Planner", style="Card.TLabelframe")
    planner_box.grid(row=2, column=0, sticky="ew", pady=(0, 8))
    planner_box.columnconfigure(0, weight=1)
    move_tree = ttk.Treeview(planner_box, columns=("move", "makes_sense", "protects", "gives_up", "effect"), show="headings", height=9)
    _style_research_tree(move_tree)
    for column, label, width in (
        ("move", "Move", 150),
        ("makes_sense", "When It Makes Sense", 260),
        ("protects", "Protects Against", 210),
        ("gives_up", "Gives Up", 210),
        ("effect", "Estimated Effect", 260),
    ):
        move_tree.heading(column, text=label)
        move_tree.column(column, width=width, anchor=tk.W, stretch=column in {"makes_sense", "effect"})
    move_tree.grid(row=0, column=0, sticky="ew")
    move_scroll = ttk.Scrollbar(planner_box, orient=tk.VERTICAL, command=move_tree.yview)
    move_scroll.grid(row=0, column=1, sticky="ns")
    move_tree.configure(yscrollcommand=move_scroll.set)
    _add_horizontal_tree_scrollbar(planner_box, move_tree, row=1)
    frame.move_planner_tree = move_tree  # type: ignore[attr-defined]
    frame.impact_bars = ScenarioImpactBars(frame, height=170)  # type: ignore[attr-defined]
    frame.impact_bars.grid(row=3, column=0, sticky="ew", pady=(0, 8))  # type: ignore[attr-defined]
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=4, column=0, sticky="ew")
    tree_box.columnconfigure(0, weight=1)
    ttk.Label(
        tree_box,
        text="Current columns use shares already held in the portfolio. Model columns use the generated stock scenario position from the card above.",
        style="Subtle.TLabel",
        wraplength=1120,
        justify=tk.LEFT,
    ).grid(row=0, column=0, sticky="ew", pady=(0, 6))
    tree = ttk.Treeview(
        tree_box,
        columns=("scenario", "price", "current_shares", "current_pnl", "model_shares", "model_pnl", "model_impact", "model_portfolio"),
        show="headings",
        height=10,
    )
    _style_research_tree(tree)
    for column, label, width in (
        ("scenario", "Move", 72),
        ("price", "Stock Price", 105),
        ("current_shares", "Current Sh", 88),
        ("current_pnl", "Current P&L", 112),
        ("model_shares", "Model Sh", 88),
        ("model_pnl", "Model P&L", 112),
        ("model_impact", "Model Impact", 112),
        ("model_portfolio", "Model Portfolio", 130),
    ):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.E if column != "scenario" else tk.W, stretch=True)
    tree.tag_configure("positive", foreground="#047857")
    tree.tag_configure("negative", foreground="#b91c1c")
    tree.grid(row=1, column=0, sticky="ew")
    y_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=1, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    _add_horizontal_tree_scrollbar(tree_box, tree, row=2)
    note = _readout_launcher(frame, title="Risk Scenario Explanation", button_text="Open Risk Scenario Explanation", row=5, pady=(10, 0))
    frame.scenario_tree = tree  # type: ignore[attr-defined]
    frame.scenario_note_text = note  # type: ignore[attr-defined]
    option_box = ttk.LabelFrame(frame, text="Options Scenario - Expiration Payoff Estimate", style="Card.TLabelframe")
    option_box.grid(row=6, column=0, sticky="ew", pady=(10, 0))
    option_box.columnconfigure(0, weight=1)
    option_controls = ttk.Frame(option_box, style="Panel.TFrame")
    option_controls.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    ttk.Button(option_controls, text="Run Option Scenario From Selected/Best Contract", command=lambda app=self: _render_option_scenarios_from_top(app)).pack(side=tk.LEFT)
    ttk.Button(option_controls, text="Load Chain", command=lambda app=self: _load_chain_from_research_tab(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Label(
        option_box,
        text="Expiration payoff estimate: current columns use actual shares held now; model columns use the generated stock scenario position. This is not live option-value/path pricing.",
        style="Subtle.TLabel",
        wraplength=1120,
        justify=tk.LEFT,
    ).grid(row=1, column=0, sticky="ew", pady=(0, 6))
    option_tree = ttk.Treeview(
        option_box,
        columns=("move", "price", "current_stock", "model_stock", "option", "current_combined", "model_combined", "read"),
        show="headings",
        height=9,
    )
    _style_research_tree(option_tree)
    for column, label, width in (
        ("move", "Move", 70),
        ("price", "Stock Price", 100),
        ("current_stock", "Current Stock", 110),
        ("model_stock", "Model Stock", 110),
        ("option", "Option P&L", 105),
        ("current_combined", "Current Combo", 118),
        ("model_combined", "Model Combo", 118),
        ("read", "Read", 310),
    ):
        option_tree.heading(column, text=label)
        option_tree.column(column, width=width, anchor=tk.E if column not in {"move", "read"} else tk.W, stretch=True)
    option_tree.tag_configure("positive", foreground="#047857")
    option_tree.tag_configure("negative", foreground="#b91c1c")
    option_tree.grid(row=2, column=0, sticky="ew")
    option_scroll = ttk.Scrollbar(option_box, orient=tk.VERTICAL, command=option_tree.yview)
    option_scroll.grid(row=2, column=1, sticky="ns")
    option_tree.configure(yscrollcommand=option_scroll.set)
    _add_horizontal_tree_scrollbar(option_box, option_tree, row=3)
    option_tree.bind("<Double-1>", lambda _event, app=self: _load_chain_from_option_scenario_row(app), add="+")
    option_tree.bind("<Return>", lambda _event, app=self: _load_chain_from_option_scenario_row(app), add="+")
    frame.option_scenario_tree = option_tree  # type: ignore[attr-defined]
    return frame


def _options_strategy_tab(self: tk.Tk, notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Options Strategy")
    frame.columnconfigure(0, weight=1)
    controls = ttk.Frame(frame, style="Panel.TFrame")
    controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    ttk.Button(controls, text="Generate Candidates", command=lambda app=self: _render_options_strategy(app)).pack(side=tk.LEFT)
    ttk.Button(controls, text="Load Chain", command=lambda app=self: _load_chain_from_research_tab(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(controls, text="Use This Option", command=lambda app=self: _use_selected_research_option(app), style="Accent.TButton").pack(side=tk.LEFT, padx=(8, 0))
    frame.status_var = tk.StringVar(value="Run symbol analysis and load an option chain to generate candidates.")  # type: ignore[attr-defined]
    ttk.Label(frame, textvariable=frame.status_var, style="Subtle.TLabel").grid(row=1, column=0, sticky="w", pady=(0, 8))  # type: ignore[attr-defined]
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=2, column=0, sticky="ew")
    tree_box = ttk.LabelFrame(frame, text="Candidate Options", style="Card.TLabelframe")
    tree_box.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("group", "strategy", "expiration", "strike", "type", "mid", "max_loss", "breakeven", "score", "confidence"), show="headings", height=10)
    _style_research_tree(tree)
    for column, label, width in (
        ("group", "Group", 110),
        ("strategy", "Strategy", 210),
        ("expiration", "Expiration", 130),
        ("strike", "Strike", 95),
        ("type", "Call/Put", 90),
        ("mid", "Mid/Debit", 95),
        ("max_loss", "Max Loss", 105),
        ("breakeven", "Breakeven", 105),
        ("score", "Score", 70),
        ("confidence", "Read", 110),
    ):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.E if column in {"strike", "mid", "max_loss", "breakeven", "score"} else tk.W, stretch=column == "strategy")
    tree.grid(row=0, column=0, sticky="ew")
    tree.bind("<<TreeviewSelect>>", lambda _event, app=self: _show_selected_option_candidate(app), add="+")
    y_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    _add_horizontal_tree_scrollbar(tree_box, tree, row=1)
    frame.candidate_tree = tree  # type: ignore[attr-defined]
    frame.score_breakdown = ttk.LabelFrame(frame, text="Score Breakdown / Why Ranked Here", style="Card.TLabelframe")  # type: ignore[attr-defined]
    frame.score_breakdown.grid(row=4, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    frame.score_breakdown.columnconfigure(0, weight=1)  # type: ignore[attr-defined]
    frame.timeline = ttk.LabelFrame(frame, text="Selected Candidate Timeline", style="Card.TLabelframe")  # type: ignore[attr-defined]
    frame.timeline.grid(row=5, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    frame.timeline.columnconfigure(0, weight=1)  # type: ignore[attr-defined]
    frame.timeline_var = tk.StringVar(value="Select a candidate.")  # type: ignore[attr-defined]
    ttk.Label(frame.timeline, textvariable=frame.timeline_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=10, pady=8)  # type: ignore[attr-defined]
    scenario_box = ttk.LabelFrame(frame, text="Expiration Payoff Estimate - Selected Candidate Scenario", style="Card.TLabelframe")
    scenario_box.grid(row=6, column=0, sticky="ew", pady=(8, 0))
    scenario_box.columnconfigure(0, weight=1)
    ttk.Label(
        scenario_box,
        text="Expiration payoff estimate: current columns use actual shares held now; model columns use the generated stock scenario position when available. This is not live option-value/path pricing.",
        style="Subtle.TLabel",
        wraplength=1120,
        justify=tk.LEFT,
    ).grid(row=0, column=0, sticky="ew", pady=(0, 6))
    frame.candidate_bars = ScenarioImpactBars(scenario_box, height=104)  # type: ignore[attr-defined]
    frame.candidate_bars.grid(row=1, column=0, sticky="ew", pady=(0, 6))  # type: ignore[attr-defined]
    scenario_tree = ttk.Treeview(
        scenario_box,
        columns=("move", "price", "contracts", "current_stock", "model_stock", "option", "current_combined", "model_combined", "read"),
        show="headings",
        height=9,
    )
    _style_research_tree(scenario_tree)
    for column, label, width in (
        ("move", "Move", 75),
        ("price", "Stock Price", 105),
        ("contracts", "Contracts", 85),
        ("current_stock", "Current Stock", 105),
        ("model_stock", "Model Stock", 105),
        ("option", "Option Payoff", 105),
        ("current_combined", "Current Combo", 112),
        ("model_combined", "Model Combo", 112),
        ("read", "Plain-English Read", 280),
    ):
        scenario_tree.heading(column, text=label)
        scenario_tree.column(column, width=width, anchor=tk.E if column not in {"move", "read"} else tk.W, stretch=column == "read")
    scenario_tree.tag_configure("positive", foreground="#047857")
    scenario_tree.tag_configure("negative", foreground="#b91c1c")
    scenario_tree.grid(row=2, column=0, sticky="ew")
    scenario_scroll = ttk.Scrollbar(scenario_box, orient=tk.VERTICAL, command=scenario_tree.yview)
    scenario_scroll.grid(row=2, column=1, sticky="ns")
    scenario_tree.configure(yscrollcommand=scenario_scroll.set)
    _add_horizontal_tree_scrollbar(scenario_box, scenario_tree, row=3)
    frame.candidate_scenario_tree = scenario_tree  # type: ignore[attr-defined]
    help_box = ttk.LabelFrame(frame, text="How To Read This", style="Card.TLabelframe")
    help_box.grid(row=7, column=0, sticky="ew", pady=(8, 0))
    ttk.Label(
        help_box,
        text="Call: right to benefit from upside. Put: downside insurance/speculation. Premium: upfront option price. Strike: exercise reference price. Intrinsic value: value at expiration from stock versus strike. Expiration-style estimate: simple payoff math, not live option pricing.",
        style="Subtle.TLabel",
        wraplength=1120,
        justify=tk.LEFT,
    ).grid(row=0, column=0, sticky="ew", padx=10, pady=8)
    frame.detail_text = _readout_launcher(frame, title="Options Strategy Explanation", button_text="Open Options Strategy Explanation", row=8)  # type: ignore[attr-defined]
    return frame


def _greeks_tab(self: tk.Tk, notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Greeks")
    frame.columnconfigure(0, weight=1)
    controls = ttk.LabelFrame(frame, text="Option Sensitivities", style="Card.TLabelframe")
    controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    controls.columnconfigure(1, weight=1)
    ttk.Button(controls, text="Load / Refresh Greeks", command=lambda app=self: _load_greeks_from_research_tab(app), style="Accent.TButton").grid(row=0, column=0, sticky="w", padx=(10, 8), pady=8)
    frame.status_var = tk.StringVar(value="Run analysis or load an option chain to populate option sensitivities.")  # type: ignore[attr-defined]
    ttk.Label(controls, textvariable=frame.status_var, style="Subtle.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=8)  # type: ignore[attr-defined]

    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=1, column=0, sticky="ew")

    tree_box = ttk.LabelFrame(frame, text="Greek Chain Table", style="Card.TLabelframe")
    tree_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    tree_box.columnconfigure(0, weight=1)
    columns = ("expiration", "dte", "strike", "type", "bid", "ask", "mark", "iv", "delta", "gamma", "theta", "vega", "rho", "source")
    tree = ttk.Treeview(tree_box, columns=columns, show="headings", height=12, selectmode="browse")
    _style_research_tree(tree)
    specs = (
        ("expiration", "Expiration", 130, tk.W),
        ("dte", "DTE", 56, tk.E),
        ("strike", "Strike", 78, tk.E),
        ("type", "Type", 58, tk.W),
        ("bid", "Bid", 68, tk.E),
        ("ask", "Ask", 68, tk.E),
        ("mark", "Mark", 68, tk.E),
        ("iv", "IV", 72, tk.E),
        ("delta", "Delta", 76, tk.E),
        ("gamma", "Gamma", 76, tk.E),
        ("theta", "Theta", 76, tk.E),
        ("vega", "Vega", 76, tk.E),
        ("rho", "Rho", 76, tk.E),
        ("source", "Source", 150, tk.W),
    )
    for column, label, width, anchor in specs:
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=anchor, stretch=column in {"expiration", "source"})
    tree.tag_configure("selected", background="#dbeafe", foreground="#0f172a")
    tree.grid(row=0, column=0, sticky="ew")
    y_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    x_scroll = ttk.Scrollbar(tree_box, orient=tk.HORIZONTAL, command=tree.xview)
    x_scroll.grid(row=1, column=0, sticky="ew")
    tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
    frame.greek_tree = tree  # type: ignore[attr-defined]

    frame.interpretation = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.interpretation.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    frame.interpretation.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    frame.detail_text = _readout_launcher(frame, title="Option Sensitivities Explanation", button_text="Open Greeks Visual Guide", row=4)  # type: ignore[attr-defined]
    return frame


def _run_research_analysis(self: tk.Tk) -> None:
    symbol = self.schwab_research_symbol_var.get().strip().upper()
    if not symbol:
        messagebox.showinfo("Choose symbol", "Select a Schwab holding or enter a stock/ETF symbol first.")
        return
    try:
        symbol = normalize_ticker(symbol)
    except Exception as exc:
        messagebox.showerror("Symbol blocked", str(exc))
        return

    self.schwab_research_symbol_var.set(symbol)
    self.symbol_var.set(symbol)
    if hasattr(self, "options_symbol_var"):
        self.options_symbol_var.set(symbol)

    try:
        session = self._authorize_schwab_session()
    except Exception as exc:
        messagebox.showerror("Schwab session failed", str(exc))
        return
    if session is None:
        return

    portfolio = self.broker.get_portfolio()
    ticket = _technical_ticket_from_research_ui(self, portfolio)
    _set_research_text(self.schwab_research_overview_text, f"Running Schwab research for {symbol}...\n\nFetching quote/history, SEC filings, fundamentals, earnings layer, and macro context.")
    self.schwab_research_status_var.set(f"Running analysis for {symbol}...")

    def worker() -> None:
        try:
            payload = _build_research_payload(session, portfolio, symbol, ticket=ticket)
        except Exception as exc:
            self.after(0, lambda error=exc: _show_research_error(self, symbol, error))
            return
        self.after(0, lambda result=payload: _render_research_payload(self, result))

    threading.Thread(target=worker, daemon=True).start()


def _build_research_payload(session: Any, portfolio, symbol: str, *, ticket: TechnicalTicket | None = None) -> _ResearchPayload:
    statuses: list[DataSourceStatus] = []
    quote: dict[str, Any] | None = None
    daily_payload: dict[str, Any] | None = None

    quote, quote_status = _fetch_quote(session, symbol)
    statuses.append(quote_status)
    quote_snapshot = parse_quote_snapshot(symbol, quote) if quote else None

    daily_payload, history_status = _fetch_daily_history(session, symbol)
    statuses.append(history_status)
    candles = candles_from_price_history(daily_payload) if daily_payload else []
    indicators = calculate_advanced_indicators(symbol, candles)
    command_timeframes, command_warnings, command_statuses = _fetch_command_center_timeframes(session, symbol, candles)
    statuses.extend(command_statuses)
    market_indicators, market_candles, market_statuses = _fetch_market_evidence_context(session, symbol, candles)
    statuses.extend(market_statuses)
    try:
        capital_structure_pressure = analyze_capital_structure_pressure(symbol)
    except Exception as exc:
        capital_structure_pressure = unknown_capital_structure_report(
            symbol,
            warnings=[f"Capital structure overlay unavailable: {exc}"],
        )
    capital_status = "fresh/cache" if capital_structure_pressure.read != "Unknown" else "limited"
    capital_message = (
        f"{capital_structure_pressure.read} pressure; score {capital_structure_pressure.supply_overhang_score}/100."
        if capital_structure_pressure.read != "Unknown"
        else "; ".join(capital_structure_pressure.warnings[:2]) or "Capital structure overlay unavailable."
    )
    statuses.append(DataSourceStatus("SEC capital structure pressure", capital_status, _now(), capital_message))
    command_center_report = build_technical_command_center_report(
        symbol,
        command_timeframes,
        benchmark_candles=market_candles,
        quote_snapshot=quote_snapshot,
        ticket=ticket,
        warnings=command_warnings,
        capital_structure_pressure=capital_structure_pressure,
    )

    fallback_price = _last_price_from_quote(quote) or indicators.latest_close
    context = build_portfolio_symbol_context(portfolio, symbol, fallback_price)
    moves = technical_scenario_moves(context, indicators)
    scenario_rows = build_scenario_rows(context, moves)

    security_kind = detect_security_kind(symbol, quote, _portfolio_position_asset_type(portfolio, symbol))
    reporting_profile = REPORTING_PROFILE_ETF_OR_FUND if security_kind in ETF_SECURITY_KINDS else REPORTING_PROFILE_UNKNOWN
    etf_snapshot: ETFResearchSnapshot | None = None
    foreign_issuer_snapshot: ForeignIssuerSnapshot | None = None
    if security_kind in ETF_SECURITY_KINDS:
        earnings_text, fundamentals_text, filings_lines, sec_statuses, etf_snapshot = _fetch_etf_layers(symbol, quote, security_kind)
    else:
        (
            earnings_text,
            fundamentals_text,
            filings_lines,
            sec_statuses,
            reporting_profile,
            foreign_issuer_snapshot,
        ) = _fetch_company_layers(symbol, quote, _portfolio_position_asset_type(portfolio, symbol))
    statuses.extend(sec_statuses)

    macro_snapshot: MacroSnapshot | None = None
    try:
        macro_snapshot = fetch_macro_release_snapshot(timeout_seconds=8)
        macro_text = format_macro_report(macro_snapshot)
        statuses.append(DataSourceStatus("Official macro", "fresh/cache", _now(), "BLS/BEA/Treasury macro context loaded."))
    except Exception as exc:
        macro_text = f"Official Macro Snapshot\nFetched: unavailable\n\nMacro data unavailable/error: {exc}"
        statuses.append(DataSourceStatus("Official macro", "error", _now(), str(exc)))

    decision = build_decision_readout(
        indicators=indicators,
        context=context,
        scenario_rows=scenario_rows,
        earnings_text=earnings_text,
        fundamentals_text=fundamentals_text,
        macro_text=macro_text,
        statuses=statuses,
    )

    option_chain_rows, option_chain_underlying_price, option_chain_status = _fetch_research_option_chain(session, symbol)
    statuses.append(option_chain_status)
    greek_underlying_price = option_chain_underlying_price or context.last_price
    greek_summary = build_greek_summary(option_chain_rows, greek_underlying_price)
    trade_evidence_report = build_trade_evidence_report(
        symbol=symbol,
        indicators=indicators,
        context=context,
        decision=decision,
        scenario_rows=scenario_rows,
        earnings_text=earnings_text,
        macro_text=macro_text,
        statuses=statuses,
        quote=quote,
        option_chain_rows=option_chain_rows,
        symbol_candles=candles,
        command_center_snapshots=command_center_report.snapshots,
        market_indicators=market_indicators,
        market_candles=market_candles,
    )

    return _ResearchPayload(
        symbol=symbol,
        quote=quote,
        indicators=indicators,
        context=context,
        scenario_rows=scenario_rows,
        earnings_text=earnings_text,
        fundamentals_text=fundamentals_text,
        filings_lines=filings_lines,
        macro_text=macro_text,
        statuses=statuses,
        decision=decision,
        macro_snapshot=macro_snapshot,
        option_chain_rows=option_chain_rows,
        option_chain_underlying_price=greek_underlying_price,
        greek_summary=greek_summary,
        security_kind=security_kind,
        reporting_profile=reporting_profile,
        etf_snapshot=etf_snapshot,
        foreign_issuer_snapshot=foreign_issuer_snapshot,
        daily_candles=candles,
        market_indicators=market_indicators,
        market_candles=market_candles,
        trade_evidence_report=trade_evidence_report,
        command_center_report=command_center_report,
    )


def _fetch_quote(session: Any, symbol: str) -> tuple[dict[str, Any] | None, DataSourceStatus]:
    try:
        status_code, payload = session.get_quote(symbol)
        if status_code != 200:
            return None, DataSourceStatus("Schwab quote", "error", _now(), f"HTTP {status_code}: {payload}")
        quote = _quote_for_symbol(payload, symbol)
        return quote, DataSourceStatus("Schwab quote", "fresh", _now(), "Quote loaded from Schwab market data.")
    except Exception as exc:
        return None, DataSourceStatus("Schwab quote", "error", _now(), str(exc))


def _fetch_daily_history(session: Any, symbol: str) -> tuple[dict[str, Any] | None, DataSourceStatus]:
    try:
        status_code, payload = session.get_price_history(
            symbol,
            period_type="year",
            period=2,
            frequency_type="daily",
            frequency=1,
            need_extended_hours_data=False,
        )
        if status_code != 200:
            cached = load_cached_price_history(symbol)
            if cached:
                return cached, DataSourceStatus("Schwab price history", "cached", _now(), f"Live history HTTP {status_code}; using cached candles.")
            return None, DataSourceStatus("Schwab price history", "error", _now(), f"HTTP {status_code}: {payload}")
        if isinstance(payload, dict):
            save_cached_price_history(symbol, payload)
        return payload, DataSourceStatus("Schwab price history", "fresh", _now(), "Two-year daily candles loaded.")
    except Exception as exc:
        cached = load_cached_price_history(symbol)
        if cached:
            return cached, DataSourceStatus("Schwab price history", "cached", _now(), f"{exc}; using cached candles.")
        return None, DataSourceStatus("Schwab price history", "error", _now(), str(exc))


def _fetch_command_center_timeframes(
    session: Any,
    symbol: str,
    daily_candles: list[Candle],
) -> tuple[dict[str, list[Candle]], list[str], list[DataSourceStatus]]:
    timeframe_candles: dict[str, list[Candle]] = {}
    warnings: list[str] = []
    statuses: list[DataSourceStatus] = []

    for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES:
        if spec.frequency_type == "daily" and daily_candles:
            timeframe_candles[spec.key] = daily_candles[-260:]
            statuses.append(DataSourceStatus(f"Command Center {spec.label}", "fresh/cache", _now(), "Using already loaded daily Schwab candles."))
            continue
        try:
            status_code, payload = session.get_price_history(
                symbol,
                period_type=spec.period_type,
                period=spec.period,
                frequency_type=spec.frequency_type,
                frequency=spec.frequency,
                need_extended_hours_data=False,
            )
            if status_code != 200:
                message = f"{spec.label} returned HTTP {status_code}: {payload}"
                warnings.append(message)
                statuses.append(DataSourceStatus(f"Command Center {spec.label}", "error", _now(), message))
                timeframe_candles[spec.key] = []
                continue
            timeframe_candles[spec.key] = candles_from_price_history(payload)
            statuses.append(DataSourceStatus(f"Command Center {spec.label}", "fresh", _now(), "Schwab price-history candles loaded."))
        except Exception as exc:
            message = f"{spec.label} fetch failed: {exc}"
            warnings.append(message)
            statuses.append(DataSourceStatus(f"Command Center {spec.label}", "error", _now(), str(exc)))
            timeframe_candles[spec.key] = []

    return timeframe_candles, warnings, statuses


def _fetch_market_evidence_context(
    session: Any,
    symbol: str,
    symbol_candles: list[Candle],
) -> tuple[dict[str, AdvancedIndicatorSnapshot], dict[str, list[Candle]], list[DataSourceStatus]]:
    indicators: dict[str, AdvancedIndicatorSnapshot] = {}
    candles_by_symbol: dict[str, list[Candle]] = {}
    statuses: list[DataSourceStatus] = []
    selected = symbol.strip().upper()
    for benchmark in ("SPY", "QQQ", "IWM"):
        if benchmark == selected:
            candles = list(symbol_candles)
            indicators[benchmark] = calculate_advanced_indicators(benchmark, candles)
            candles_by_symbol[benchmark] = candles
            statuses.append(DataSourceStatus(f"Market regime {benchmark}", "fresh", _now(), "Using selected-symbol daily candles because it is a benchmark."))
            continue
        payload, status = _fetch_daily_history(session, benchmark)
        candles = candles_from_price_history(payload) if payload else []
        if candles:
            indicators[benchmark] = calculate_advanced_indicators(benchmark, candles)
            candles_by_symbol[benchmark] = candles
        statuses.append(DataSourceStatus(f"Market regime {benchmark}", status.status, status.fetched_at, status.message))
    return indicators, candles_by_symbol, statuses


def _portfolio_position_asset_type(portfolio: Any, symbol: str) -> str:
    try:
        position = portfolio.get_position(symbol) if hasattr(portfolio, "get_position") else None
        if position is None:
            position = getattr(portfolio, "positions", {}).get(symbol.strip().upper())
        return str(getattr(position, "asset_type", "") or "")
    except Exception:
        return ""


def _fetch_sec_layers(symbol: str) -> tuple[str, str, list[str], list[DataSourceStatus]]:
    earnings_text, fundamentals_text, filings_lines, statuses, _profile, _snapshot = _fetch_company_layers(symbol)
    return earnings_text, fundamentals_text, filings_lines, statuses


def _fetch_company_layers(
    symbol: str,
    quote: dict[str, Any] | None = None,
    position_asset_type: str | None = None,
) -> tuple[str, str, list[str], list[DataSourceStatus], str, ForeignIssuerSnapshot | None]:
    preliminary_profile = detect_reporting_profile(symbol, quote, position_asset_type)
    try:
        client = SecEdgarClient(timeout_seconds=12)
        profile_filings = client.recent_filings(symbol, forms=ALL_COMPANY_REPORT_FORMS, limit=24)
    except Exception as exc:
        if preliminary_profile == REPORTING_PROFILE_FOREIGN_ISSUER:
            snapshot = build_foreign_issuer_snapshot(
                symbol,
                source_links=foreign_issuer_source_links(symbol),
                warnings=[f"SEC foreign issuer filings could not be loaded automatically: {exc}"],
            )
            statuses = [
                DataSourceStatus("Reporting profile", REPORTING_PROFILE_FOREIGN_ISSUER, _now(), "Detected from quote/metadata fallback."),
                DataSourceStatus("SEC foreign issuer filings", "not found", _now(), str(exc)),
                DataSourceStatus("Foreign issuer document sources", "pending", _now(), "Official IR, 6-K, and 20-F source links prepared."),
                DataSourceStatus("SEC companyfacts", "limited", _now(), "Companyfacts is not the primary source for foreign issuer mode."),
            ]
            return (
                format_foreign_issuer_earnings_text(snapshot),
                format_foreign_issuer_fundamentals_text(snapshot),
                snapshot.filings_lines or [f"SEC foreign issuer filings unavailable: {exc}"],
                statuses,
                REPORTING_PROFILE_FOREIGN_ISSUER,
                snapshot,
            )

        statuses = [
            DataSourceStatus("SEC filings/earnings", "error", _now(), str(exc)),
            DataSourceStatus("SEC companyfacts", "error", _now(), str(exc)),
        ]
        return (
            "Earnings / News\n\nOfficial SEC earnings-release layer unavailable.",
            f"Fundamentals\n\nSEC companyfacts unavailable/error: {exc}\n\nFor ETFs, issuer holdings, expense ratio, AUM, and sector exposure remain a future provider hook.",
            [f"SEC filings unavailable/error: {exc}"],
            statuses,
            preliminary_profile,
            None,
        )

    forms = [filing.form for filing in profile_filings]
    company_title = profile_filings[0].company.title if profile_filings else ""
    reporting_profile = detect_reporting_profile(
        symbol,
        quote,
        position_asset_type,
        sec_forms=forms,
        company_title=company_title,
    )
    if reporting_profile == REPORTING_PROFILE_FOREIGN_ISSUER:
        return _fetch_foreign_issuer_layers(symbol, client, profile_filings, company_title)
    return (*_fetch_us_domestic_sec_layers(symbol, client), reporting_profile or REPORTING_PROFILE_US_DOMESTIC_EQUITY, None)


def _fetch_us_domestic_sec_layers(
    symbol: str,
    client: SecEdgarClient | None = None,
) -> tuple[str, str, list[str], list[DataSourceStatus]]:
    statuses: list[DataSourceStatus] = []
    earnings_text = "Earnings / News\n\nOfficial SEC earnings-release layer unavailable."
    fundamentals_text = "Fundamentals\n\nSEC companyfacts unavailable."
    filings_lines: list[str] = []
    try:
        active_client = client or SecEdgarClient(timeout_seconds=12)
        filings = active_client.recent_filings(symbol, forms=REPORT_FORMS, limit=10)
        release = active_client.latest_earnings_release(symbol)
        company_name = release.company.title if release else filings[0].company.title if filings else symbol
        calendar_event = fetch_earnings_calendar_event(symbol)
        if calendar_event is not None:
            statuses.append(
                DataSourceStatus(
                    "Earnings calendar",
                    "fresh/cache",
                    _now(),
                    f"{calendar_event.symbol} event {calendar_event.event_date} {calendar_event.timing or ''}".strip(),
                )
            )
        else:
            statuses.append(DataSourceStatus("Earnings calendar", "not found", _now(), "No same-day or next-trading-day earnings event found."))
        company_release = (
            fetch_official_company_earnings_release(
                symbol,
                company_name=company_name,
                event_date=calendar_event.event_date,
            )
            if calendar_event is not None
            else None
        )
        if company_release is not None:
            statuses.append(DataSourceStatus("Company IR earnings", "fresh", _now(), f"{company_release.label} ({company_release.date})"))
        elif calendar_event is not None:
            statuses.append(DataSourceStatus("Company IR earnings", "pending", _now(), "Near-term event found; no fresh company IR release was found yet."))
        digest = analyze_earnings_sources(
            symbol,
            release,
            calendar_event=calendar_event,
            company_release=company_release,
            company_name=company_name,
            latest_sec_filing_date=filings[0].filing_date if filings else "",
        )
        earnings_text = format_earnings_release_digest(digest)
        filings_lines = [f"{filing.form} filed {filing.filing_date} period {filing.report_date or '--'}: {filing.filing_url}" for filing in filings[:8]]
        statuses.append(DataSourceStatus("SEC filings/earnings", "fresh/cache", _now(), f"{len(filings)} recent filings scanned."))
    except Exception as exc:
        statuses.append(DataSourceStatus("SEC filings/earnings", "error", _now(), str(exc)))
        filings_lines = [f"SEC filings unavailable/error: {exc}"]

    try:
        active_client = client or SecEdgarClient(timeout_seconds=12)
        company, payload = active_client.get_companyfacts(symbol)
        fundamentals_text = format_fundamental_analysis(analyze_company_facts(company, payload))
        statuses.append(DataSourceStatus("SEC companyfacts", "fresh/cache", _now(), "Standardized XBRL fundamentals loaded."))
    except Exception as exc:
        statuses.append(DataSourceStatus("SEC companyfacts", "error", _now(), str(exc)))
        fundamentals_text = f"Fundamentals\n\nSEC companyfacts unavailable/error: {exc}\n\nFor ETFs, issuer holdings, expense ratio, AUM, and sector exposure remain a future provider hook."
    return earnings_text, fundamentals_text, filings_lines, statuses


def _fetch_foreign_issuer_layers(
    symbol: str,
    client: SecEdgarClient,
    profile_filings: list[Any],
    company_title: str = "",
) -> tuple[str, str, list[str], list[DataSourceStatus], str, ForeignIssuerSnapshot]:
    statuses: list[DataSourceStatus] = [
        DataSourceStatus("Reporting profile", REPORTING_PROFILE_FOREIGN_ISSUER, _now(), "Foreign issuer / ADR-style SEC filing profile detected."),
    ]
    foreign_filings = [filing for filing in profile_filings if filing.form in FOREIGN_RESULTS_FORMS]
    filings_lines = [f"{filing.form} filed {filing.filing_date} period {filing.report_date or '--'}: {filing.filing_url}" for filing in foreign_filings[:10]]

    sec_text = ""
    try:
        release = client.latest_foreign_issuer_release(symbol)
        if release is not None:
            sec_text = release.text
            statuses.append(DataSourceStatus("SEC 6-K / 20-F scan", "fresh/cache", _now(), f"Loaded {release.filing.form} foreign issuer source text."))
            release_line = f"{release.filing.form} filed {release.filing.filing_date} period {release.filing.report_date or '--'}: {release.source_url}"
            if release_line not in filings_lines:
                filings_lines.insert(0, release_line)
        elif filings_lines:
            statuses.append(DataSourceStatus("SEC 6-K / 20-F scan", "not found", _now(), "Foreign issuer filings were listed, but no results document text was selected automatically."))
        else:
            statuses.append(DataSourceStatus("SEC 6-K / 20-F scan", "not found", _now(), "No recent 6-K / 20-F / 40-F filings found in this scan."))
    except Exception as exc:
        statuses.append(DataSourceStatus("SEC 6-K / 20-F scan", "not found", _now(), f"Foreign issuer SEC document scan did not load: {exc}"))

    official_text, discovered_links, ir_warnings = fetch_known_official_ir_texts(symbol)
    if official_text.strip():
        statuses.append(DataSourceStatus("Official IR results", "fresh/cache", _now(), "Official investor relations result text loaded."))
    else:
        statuses.append(DataSourceStatus("Official IR results", "pending", _now(), "Official IR result links prepared; parsed text not loaded automatically."))

    companyfacts_text = ""
    companyfacts_available = False
    try:
        company, payload = client.get_companyfacts(symbol)
        companyfacts_text = format_fundamental_analysis(analyze_company_facts(company, payload))
        companyfacts_available = True
        company_title = company_title or company.title
        statuses.append(DataSourceStatus("SEC companyfacts", "supplemental", _now(), "Companyfacts loaded as supplemental context for foreign issuer mode."))
    except Exception as exc:
        statuses.append(DataSourceStatus("SEC companyfacts", "limited", _now(), f"Companyfacts not primary for foreign issuer mode: {exc}"))

    source_links = foreign_issuer_source_links(symbol, company_title, filings_lines, discovered_links)
    if companyfacts_available:
        source_links.append(("SEC companyfacts / XBRL", "latest loaded", "https://data.sec.gov/api/xbrl/companyfacts/"))
    snapshot = build_foreign_issuer_snapshot(
        symbol,
        company_name=company_title,
        filings_lines=filings_lines,
        sec_text=sec_text,
        official_text=official_text,
        companyfacts_text=companyfacts_text,
        companyfacts_available=companyfacts_available,
        source_links=source_links,
        warnings=ir_warnings,
    )
    statuses.append(DataSourceStatus("Foreign issuer document sources", "fresh/cache", _now(), f"{len(snapshot.source_links)} IR / SEC source links prepared."))
    return (
        format_foreign_issuer_earnings_text(snapshot),
        format_foreign_issuer_fundamentals_text(snapshot),
        filings_lines,
        statuses,
        REPORTING_PROFILE_FOREIGN_ISSUER,
        snapshot,
    )


def _fetch_etf_layers(
    symbol: str,
    quote: dict[str, Any] | None = None,
    security_kind: str = "etf",
) -> tuple[str, str, list[str], list[DataSourceStatus], ETFResearchSnapshot]:
    statuses: list[DataSourceStatus] = []
    filings_lines: list[str] = []
    sec_error = ""
    try:
        client = SecEdgarClient(timeout_seconds=12)
        filings = client.recent_filings(symbol, forms=ETF_SEC_FORMS, limit=12)
        filings_lines = [f"{filing.form} filed {filing.filing_date} period {filing.report_date or '--'}: {filing.filing_url}" for filing in filings[:10]]
        if filings_lines:
            statuses.append(DataSourceStatus("SEC fund filings", "fresh/cache", _now(), f"{len(filings_lines)} ETF/fund filings scanned."))
        else:
            statuses.append(DataSourceStatus("SEC fund filings", "not found", _now(), "No ETF/fund filings resolved by ticker in this scan."))
            sec_error = "No ETF/fund filings resolved by ticker in this scan."
    except Exception as exc:
        sec_error = str(exc)
        statuses.append(DataSourceStatus("SEC fund filings", "not found", _now(), f"ETF/fund SEC lookup did not resolve by ticker: {exc}"))

    snapshot = build_etf_research_snapshot(
        symbol,
        quote=quote,
        security_kind=security_kind,
        sec_filing_lines=filings_lines,
        sec_error=sec_error,
    )
    statuses.append(DataSourceStatus("ETF document sources", "pending", _now(), "Issuer factsheet, prospectus, holdings, SAI, shareholder report, and distribution links prepared."))
    statuses.append(DataSourceStatus("SEC companyfacts", "not-applicable", _now(), "ETF companyfacts are not applicable; using ETF/fund document sources."))
    return format_etf_documents_text(snapshot), format_etf_structure_text(snapshot), filings_lines, statuses, snapshot


def _fetch_research_option_chain(session: Any, symbol: str) -> tuple[list[dict[str, Any]], float | None, DataSourceStatus]:
    try:
        status_code, payload = _request_option_chain(session, symbol, strike_count=20)
        if status_code != 200:
            return [], None, DataSourceStatus("Schwab option chain", "error", _now(), f"HTTP {status_code}: {payload}")
        if not isinstance(payload, dict):
            return [], None, DataSourceStatus("Schwab option chain", "error", _now(), "Unexpected option-chain payload.")
        rows = _option_chain_rows(payload)
        underlying_price = _underlying_price(payload)
        return rows, underlying_price, DataSourceStatus("Schwab option chain", "fresh", _now(), f"{len(rows)} option rows loaded for Greeks and strategy candidates.")
    except Exception as exc:
        return [], None, DataSourceStatus("Schwab option chain", "error", _now(), str(exc))


def _render_research_payload(self: tk.Tk, payload: _ResearchPayload) -> None:
    self.schwab_research_last_payload = payload
    context = payload.context
    quote_price = context.last_price
    if payload.security_kind in ETF_SECURITY_KINDS:
        kind_label = f" ({payload.security_kind.upper()})"
    elif payload.reporting_profile == REPORTING_PROFILE_FOREIGN_ISSUER:
        kind_label = " (FOREIGN ISSUER)"
    else:
        kind_label = ""
    self.schwab_research_quote_var.set(f"{payload.symbol}{kind_label}: {_money(quote_price)}")
    self.schwab_research_held_var.set("Held" if context.is_held else "Not held")
    self.schwab_research_weight_var.set(f"Weight {context.portfolio_weight:.2%}")
    self.schwab_research_risk_var.set(f"{payload.decision.overall.label}; risk {payload.decision.risk_level.label}")
    if payload.security_kind in ETF_SECURITY_KINDS:
        mode_text = " ETF/fund research"
    elif payload.reporting_profile == REPORTING_PROFILE_FOREIGN_ISSUER:
        mode_text = " foreign issuer research"
    else:
        mode_text = " research"
    self.schwab_research_status_var.set(f"{payload.symbol}{mode_text} updated at {_now()}")
    _sync_research_option_chain(self, payload)

    _render_at_glance(self, payload)
    _render_overview(self, payload)
    _render_trade_evidence(self, payload)
    _render_technicals(self, payload)
    _render_scenarios(self, payload)
    _render_options_strategy(self)
    _render_greeks(self, payload)
    _render_earnings_news(self, payload)
    _render_fundamentals(self, payload)
    _render_macro(self, payload)

    output = getattr(self, "schwab_trading_preview_text", None)
    if output is not None:
        evidence_text = _trade_evidence_text(payload)
        _set_research_text(output, evidence_text + "\n\n" + _overview_text(payload) + "\n\n" + _source_status_text(payload.statuses))


def _sync_research_option_chain(self: tk.Tk, payload: _ResearchPayload) -> None:
    rows = payload.option_chain_rows if payload.option_chain_rows is not None else []
    if getattr(self, "schwab_option_chain_tree", None) is not None:
        _populate_option_chain_tree(self, rows)
    else:
        self.schwab_option_chain_rows = {f"research_option_{index}": row for index, row in enumerate(rows)}
    if hasattr(self, "schwab_option_chain_status_var"):
        self.schwab_option_chain_status_var.set(f"Option chain: {len(rows)} rows loaded for {payload.symbol}" if rows else "Option chain: not loaded")
    if payload.option_chain_underlying_price is not None and hasattr(self, "options_underlying_price_var"):
        self.options_underlying_price_var.set(_format_number(payload.option_chain_underlying_price))


def _overview_text(payload: _ResearchPayload) -> str:
    context = payload.context
    indicators = payload.indicators
    decision = payload.decision
    evidence = _trade_evidence_report(payload)
    lines = [
        f"Schwab Research Workspace - {payload.symbol}",
        "",
        "At a glance:",
        f"- Technical read: {build_technical_at_glance_read(decision, payload.command_center_report).label}",
        f"- Thesis read: {decision.thesis.trade_judgment}",
        f"- Preferred vehicle: {decision.thesis.preferred_vehicle}; confidence {decision.thesis.confidence}",
        f"- What proves it wrong: {decision.thesis.invalidation}",
        f"- Overall: {decision.overall.label} ({decision.overall.why})",
        f"- Risk: {decision.risk_level.label} ({decision.risk_level.why})",
        f"- Action bias: {decision.action_bias.label} ({decision.action_bias.why})",
        f"- Evidence posture: {evidence.posture}; setup type {evidence.setup_type}.",
        "",
        "Scenario forecast (model estimate, not a guarantee):",
        *[f"- {row.scenario}: {row.probability:.1f}% / {row.likelihood}. {row.reference}. {row.why}" for row in decision.thesis.forecast],
        "",
        "Plain-English summary:",
        *[f"- {line}" for line in decision.summary],
        "",
        "Symbol / portfolio readout:",
        f"- Current quote: {_money(context.last_price)}",
        f"- Held: {'yes' if context.is_held else 'no'}",
        f"- Position: {context.quantity:g} shares, value {_money(context.market_value)}, weight {context.portfolio_weight:.2%}",
        f"- Unrealized P&L: {_money(context.unrealized_pnl)}; day P&L {_money(context.day_pnl)}",
        f"- Security kind: {payload.security_kind}.",
        f"- Reporting profile: {payload.reporting_profile}.",
        "",
        "Technical read:",
        f"- Trend: {indicators.trend}; momentum: {indicators.momentum}; volatility: {indicators.volatility}.",
        f"- Support / resistance: {_money(indicators.support)} / {_money(indicators.resistance)}.",
        f"- 52-week range: {_money(indicators.week_52_low)} to {_money(indicators.week_52_high)}.",
        "",
        "Risk read:",
        *_risk_lines(payload),
        "",
        "Source freshness:",
        *_source_status_lines(payload.statuses),
    ]
    return "\n".join(lines)


def _overview_popout_text(payload: _ResearchPayload) -> str:
    decision = payload.decision
    evidence = _trade_evidence_report(payload)
    return _format_beginner_readout(
        title=f"Overview Explanation - {payload.symbol}",
        what_this_means=(
            "This is the complete symbol readout. It combines the Schwab quote and portfolio position with "
            "the technical setup, current risk level, action bias, and data freshness."
        ),
        key_points=[
            f"Evidence verdict: {evidence.verdict}",
            f"Evidence posture: {evidence.posture}; setup type: {evidence.setup_type}.",
            f"Technical read: {build_technical_at_glance_read(decision, payload.command_center_report).label}",
            f"Thesis read: {decision.thesis.trade_judgment}",
            f"Preferred vehicle: {decision.thesis.preferred_vehicle}; confidence {decision.thesis.confidence}.",
            f"What proves it wrong: {decision.thesis.invalidation}",
            f"Overall read: {decision.overall.label} ({decision.overall.why})",
            f"Risk level: {decision.risk_level.label} ({decision.risk_level.why})",
            f"Action bias: {decision.action_bias.label} ({decision.action_bias.why})",
            *[f"Forecast estimate: {row.scenario} {row.probability:.1f}% ({row.likelihood})" for row in decision.thesis.forecast],
            *decision.summary,
        ],
        why_it_matters="Use this view to decide whether the symbol belongs on watch, needs a smaller position, needs a hedge, or has enough confirmation for a planned trade.",
        original_text=_overview_text(payload),
    )


def _format_beginner_readout(
    *,
    title: str,
    what_this_means: str,
    key_points: list[str] | tuple[str, ...],
    why_it_matters: str,
    original_text: str,
    original_title: str = "Original / detailed readout",
) -> str:
    lines = [
        title,
        "=" * min(len(title), 80),
        "",
        "What this means:",
        what_this_means.strip(),
        "",
        "Key points:",
    ]
    lines.extend(_bullet_line(point) for point in key_points if str(point).strip())
    lines.extend(
        [
            "",
            "Why it matters:",
            why_it_matters.strip(),
            "",
            f"{original_title}:",
            original_text.strip(),
        ]
    )
    return "\n".join(lines)


def _bullet_line(text: str) -> str:
    clean = str(text).strip()
    if clean.startswith(("- ", "* ")):
        return f"- {clean[2:].strip()}"
    return f"- {clean}"


def _render_at_glance(self: tk.Tk, payload: _ResearchPayload) -> None:
    decision = payload.decision
    technical_read = build_technical_at_glance_read(decision, payload.command_center_report)
    fundamental_verdict = build_fundamental_verdict(payload.fundamentals_text, payload.indicators, decision.macro_backdrop.label)
    conflict_read = build_cross_read_conflict_badge(fundamental_verdict.verdict, decision.macro_backdrop.label, technical_read)
    expanded = bool(getattr(self, "schwab_research_summary_expanded", tk.BooleanVar(value=False)).get())
    if expanded:
        cards = [
            decision.overall,
            technical_read,
            _thesis_read_badge(decision),
            _preferred_vehicle_badge(decision),
            decision.risk_level,
            conflict_read,
            decision.macro_backdrop,
            decision.action_bias,
        ]
        columns = 4
        prominent = {0, 2, 7}
        card_height = 104
        prominent_height = 112
    else:
        cards = [
            technical_read,
            _thesis_read_badge(decision),
            _preferred_vehicle_badge(decision),
            decision.action_bias,
        ]
        columns = 4
        prominent = {1, 3}
        card_height = 96
        prominent_height = 104
    metric_grid(
        self.schwab_research_glance_cards,
        cards,
        columns=columns,
        card_height=card_height,
        prominent_height=prominent_height,
        prominent_indexes=prominent,
    )
    labeled_value_grid(
        self.schwab_research_top_strip,
        {
            "Technical Read": technical_read.label,
            "Thesis Read": decision.thesis.trade_judgment,
            "Preferred Vehicle": decision.thesis.preferred_vehicle,
            "Confidence": decision.thesis.confidence,
            "What proves it wrong": decision.thesis.invalidation,
        },
        columns=5,
    )
    self.schwab_research_bull_bear_meter.set_score(technical_read.score, mode="direction", label=f"Technical: {technical_read.label} ({technical_read.score:.0f})")
    self.schwab_research_risk_meter.set_score(decision.risk_score, mode="risk", label=f"Risk Heat: {risk_heat_label(decision.risk_score)} ({decision.risk_score:.0f}/100)")
    _apply_research_summary_visibility(self)
    _schedule_research_summary_resize(self, expanded=expanded)
    _refresh_summary_tearout(self, payload)


def _render_overview(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_overview_frame
    decision = payload.decision
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [
            build_technical_at_glance_read(decision, payload.command_center_report),
            _thesis_read_badge(decision),
            _preferred_vehicle_badge(decision),
            _synthetic_badge("What Proves It Wrong", decision.thesis.invalidation, "info", "Invalidation line from the thesis-aware decision layer."),
            decision.overall,
            decision.risk_level,
            decision.position_impact,
            decision.action_bias,
            decision.trend,
            decision.momentum,
            decision.volatility,
            decision.macro_backdrop,
        ],
        columns=4,
        card_height=132,
        prominent_height=146,
        prominent_indexes={1, 3, 7},
        adaptive_height=True,
    )
    labeled_value_grid(frame.operator, decision.operator_view, columns=3)  # type: ignore[attr-defined]
    clear_children(frame.summary)  # type: ignore[attr-defined]
    for index, sentence in enumerate(decision.summary):
        ttk.Label(frame.summary, text=sentence, style="Subtle.TLabel", wraplength=980, justify=tk.LEFT).grid(row=index, column=0, sticky="ew", padx=10, pady=(6 if index == 0 else 2, 2))  # type: ignore[attr-defined]
    clear_children(frame.checks)  # type: ignore[attr-defined]
    Checklist(frame.checks, "What Matters Most", decision.matters).grid(row=0, column=0, sticky="nsew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.checks, "What Would Change The View", decision.changes_view).grid(row=0, column=1, sticky="nsew")  # type: ignore[attr-defined]
    freshness_badges(frame.freshness, payload.statuses)  # type: ignore[attr-defined]
    _set_research_text(self.schwab_research_overview_text, _overview_popout_text(payload))


def _render_trade_evidence(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = getattr(self, "schwab_trade_evidence_frame", None)
    if frame is None:
        return
    report = _trade_evidence_report(payload)
    metric_grid(
        frame.cards,
        evidence_scorecards(report),
        columns=4,
        card_height=144,
        prominent_height=156,
        prominent_indexes={0},
        adaptive_height=True,
    )  # type: ignore[attr-defined]
    clear_children(frame.verdict)  # type: ignore[attr-defined]
    ttk.Label(
        frame.verdict,
        text=report.verdict,
        style="Chip.TLabel",
        wraplength=1050,
        justify=tk.LEFT,
    ).grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))  # type: ignore[attr-defined]
    ttk.Label(
        frame.verdict,
        text=f"Posture {report.posture} | Setup type {report.setup_type} | Confidence {report.confidence}",
        style="Subtle.TLabel",
        wraplength=1050,
        justify=tk.LEFT,
    ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))  # type: ignore[attr-defined]

    tree = frame.score_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    for grade in report.grades:
        tree.insert("", tk.END, values=(grade.category, grade.grade, grade.why))

    clear_children(frame.evidence_columns)  # type: ignore[attr-defined]
    Checklist(frame.evidence_columns, "Supporting Evidence", report.supporting_evidence).grid(row=0, column=0, sticky="nsew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.evidence_columns, "Contradictions", report.contradictory_evidence).grid(row=0, column=1, sticky="nsew")  # type: ignore[attr-defined]
    clear_children(frame.risk_columns)  # type: ignore[attr-defined]
    Checklist(frame.risk_columns, "Event + Execution Risk", [*report.event_risk[:4], *report.liquidity_execution[:4]]).grid(row=0, column=0, sticky="nsew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.risk_columns, "Portfolio + Options Impact", [*report.portfolio_impact[:4], *report.options_iv[:4]]).grid(row=0, column=1, sticky="nsew")  # type: ignore[attr-defined]
    clear_children(frame.decision_columns)  # type: ignore[attr-defined]
    Checklist(frame.decision_columns, "What Would Make This Dumb", report.dumb_if).grid(row=0, column=0, sticky="nsew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.decision_columns, "What Would Change The View", report.changes_mind).grid(row=0, column=1, sticky="nsew")  # type: ignore[attr-defined]
    _set_research_text(frame.detail_text, format_trade_evidence_report(report))  # type: ignore[attr-defined]


def _trade_evidence_report(payload: _ResearchPayload) -> TradeEvidenceReport:
    if payload.trade_evidence_report is not None:
        return payload.trade_evidence_report
    return build_trade_evidence_report(
        symbol=payload.symbol,
        indicators=payload.indicators,
        context=payload.context,
        decision=payload.decision,
        scenario_rows=payload.scenario_rows,
        earnings_text=payload.earnings_text,
        macro_text=payload.macro_text,
        statuses=payload.statuses,
        quote=payload.quote,
        option_chain_rows=payload.option_chain_rows or [],
        symbol_candles=payload.daily_candles or [],
        command_center_snapshots=payload.command_center_report.snapshots if payload.command_center_report else None,
        market_indicators=payload.market_indicators or {},
        market_candles=payload.market_candles or {},
    )


def _trade_evidence_text(payload: _ResearchPayload) -> str:
    return format_trade_evidence_report(_trade_evidence_report(payload))


def _save_trade_evidence_snapshot(self: tk.Tk) -> None:
    payload = getattr(self, "schwab_research_last_payload", None)
    frame = getattr(self, "schwab_trade_evidence_frame", None)
    if payload is None:
        messagebox.showinfo("Run analysis first", "Run analysis once before saving a trade evidence snapshot.")
        return
    try:
        path = append_trade_evidence_snapshot(_trade_evidence_report(payload))
        if frame is not None and hasattr(frame, "snapshot_status_var"):
            frame.snapshot_status_var.set(f"Saved {path.name} at {_now()}")  # type: ignore[attr-defined]
    except Exception as exc:
        messagebox.showerror("Save snapshot failed", str(exc))


def _risk_lines(payload: _ResearchPayload) -> list[str]:
    context = payload.context
    lines: list[str] = []
    if not context.is_held:
        lines.append("- This symbol is not currently held; scenarios show watchlist exposure until quantity is added.")
    elif context.portfolio_weight >= 0.10:
        lines.append("- Concentration warning: this position is above 10% of portfolio value.")
    elif context.portfolio_weight >= 0.05:
        lines.append("- Concentration watch: this position is above 5% of portfolio value.")
    else:
        lines.append("- Concentration is modest based on current portfolio weight.")
    if payload.indicators.support and context.last_price:
        lines.append(f"- Distance to technical support: {distance_to_price(context.last_price, payload.indicators.support):+.1%}.")
    if payload.indicators.resistance and context.last_price:
        lines.append(f"- Distance to nearby resistance: {distance_to_price(context.last_price, payload.indicators.resistance):+.1%}.")
    return lines


def _technical_setup_cards(report: TechnicalCommandCenterReport | None) -> list[BadgeReadout]:
    if report is None:
        return [
            _synthetic_badge(
                "Command Center",
                "Unavailable",
                "info",
                "The command-center report was not built; legacy indicators remain available below.",
            )
        ]
    classification = report.setup_classification
    reason = classification.main_reason or "Setup classification was built from the command-center timeframe stack."
    return [
        _synthetic_badge("Regime", _humanize_command_value(classification.regime), _technical_status(classification.regime), reason),
        _synthetic_badge("Setup", _humanize_command_value(classification.setup), _technical_status(classification.setup), reason),
        _synthetic_badge("Timing", _humanize_command_value(classification.timing), _technical_status(classification.timing), reason),
        _synthetic_badge("Action Quality", _humanize_command_value(classification.action_quality), _technical_status(classification.action_quality), f"Confidence: {classification.confidence}. {reason}"),
        _synthetic_badge("Confirmation", _money(classification.confirmation_level), "info" if classification.confirmation_level is None else "mixed", "Price level the setup needs to confirm."),
        _synthetic_badge("Invalidation", _money(classification.invalidation_level), "info" if classification.invalidation_level is None else "bad", "Price level that weakens or invalidates the visible setup."),
    ]


def _technical_capital_structure_cards(report: TechnicalCommandCenterReport | None) -> list[BadgeReadout]:
    indicator = _technical_capital_structure_indicator(report)
    if report is None:
        return [
            _synthetic_badge(
                "Capital Read",
                "Unavailable",
                "info",
                "The command-center report was not built; no filing-derived supply indicator is available.",
            )
        ]
    if indicator is None:
        return [
            _synthetic_badge(
                "Capital Read",
                "No Parsed Supply",
                "info",
                "No capital-structure / supply indicator was returned for this command-center payload.",
            )
        ]

    cards = [
        _synthetic_badge(
            "Capital Read",
            _humanize_command_value(getattr(indicator, "read", "")),
            _capital_structure_read_status(indicator),
            _capital_structure_read_why(indicator),
            _safe_float(getattr(indicator, "technical_score", None)),
        ),
        _synthetic_badge(
            "Technical Score",
            _score_label(getattr(indicator, "technical_score", None)),
            _capital_structure_direction_status(getattr(indicator, "technical_score", None)),
            "Higher is cleaner for technical confirmation after filing-derived supply context.",
        ),
        _synthetic_badge(
            "Supply Overhang",
            _score_label(getattr(indicator, "supply_overhang_score", None)),
            _capital_structure_risk_status(getattr(indicator, "supply_overhang_score", None)),
            "Risk score from the filing pressure scan.",
        ),
        _synthetic_badge(
            "Dilution Pressure",
            _score_label(getattr(indicator, "dilution_pressure_score", None)),
            _capital_structure_risk_status(getattr(indicator, "dilution_pressure_score", None)),
            "Preferred, convertible, offering, and dilution-warning terms can weaken clean breakouts.",
        ),
        _synthetic_badge(
            "Level Proximity",
            _score_label(getattr(indicator, "warrant_conversion_proximity_score", None)),
            _capital_structure_risk_status(getattr(indicator, "warrant_conversion_proximity_score", None)),
            _capital_structure_level_why(indicator),
        ),
        _synthetic_badge(
            "Offering / ATM",
            _score_label(getattr(indicator, "offering_activity_score", None)),
            _capital_structure_risk_status(getattr(indicator, "offering_activity_score", None)),
            "ATM, shelf, resale, or offering language raises rally-fade context.",
        ),
        _synthetic_badge(
            "Float Quality",
            _score_label(getattr(indicator, "float_quality_score", None)),
            _capital_structure_direction_status(getattr(indicator, "float_quality_score", None)),
            "Higher score means fewer parsed float, share-class, or ADS/ADR complexity flags.",
        ),
        _synthetic_badge(
            "ADS Confidence",
            _signed_score_label(getattr(indicator, "foreign_issuer_confidence_modifier", None)),
            _capital_structure_modifier_status(getattr(indicator, "foreign_issuer_confidence_modifier", None)),
            "Negative modifier means ADS/ADR or foreign issuer terms need source verification.",
        ),
    ]
    if _safe_float(getattr(indicator, "option_exposure_mismatch_score", None)) > 0:
        cards.append(
            _synthetic_badge(
                "Option Mismatch",
                _score_label(getattr(indicator, "option_exposure_mismatch_score", None)),
                _capital_structure_risk_status(getattr(indicator, "option_exposure_mismatch_score", None)),
                "Modeled option contracts control meaningfully different exposure than modeled shares.",
            )
        )
    cards.append(
        _synthetic_badge(
            "Chase Risk",
            _score_label(getattr(indicator, "chase_risk_score", None)),
            _capital_structure_risk_status(getattr(indicator, "chase_risk_score", None)),
            "Composite supply, dilution, proximity, offering, and option-exposure chase risk.",
        )
    )
    return cards


def _technical_capital_structure_supply_rows(report: TechnicalCommandCenterReport | None) -> list[tuple[str, str, str, str]]:
    indicator = _technical_capital_structure_indicator(report)
    if report is None:
        return [("Unavailable", "--", "--", "Technical Command Center report was not built.")]
    if indicator is None:
        return [("No parsed supply", "--", "--", "No capital-structure / supply indicator was returned.")]

    recommendation_lines = _bounded_clean_lines(getattr(indicator, "recommendation_lines", None), limit=4)
    read = recommendation_lines[0] if recommendation_lines else _humanize_command_value(getattr(indicator, "read", ""))
    level_label = str(getattr(indicator, "nearest_supply_level_label", None) or "No nearest filing level")
    rows = [
        (
            level_label,
            _money(getattr(indicator, "nearest_supply_level", None)),
            _format_command_percent(getattr(indicator, "nearest_supply_level_distance_percent", None)),
            read or "Supply-level read unavailable.",
        )
    ]
    for line in recommendation_lines[1:]:
        rows.append(("Recommendation", "--", "--", line))
    return rows


def _technical_capital_structure_note_rows(report: TechnicalCommandCenterReport | None) -> list[tuple[str, str]]:
    indicator = _technical_capital_structure_indicator(report)
    if report is None:
        return [
            ("Status", "Technical Command Center report was not built."),
            ("Disclaimer", CAPITAL_STRUCTURE_LEVEL_DISCLAIMER),
        ]
    if indicator is None:
        return [
            ("Status", "No parsed capital-structure / supply indicator was returned."),
            ("Disclaimer", CAPITAL_STRUCTURE_LEVEL_DISCLAIMER),
        ]

    rows: list[tuple[str, str]] = []
    rows.extend(("Recommendation", line) for line in _bounded_clean_lines(getattr(indicator, "recommendation_lines", None), limit=4))
    rows.extend(("Explanation", line) for line in _bounded_clean_lines(getattr(indicator, "explanation_lines", None), limit=5))
    rows.extend(("Warning", line) for line in _bounded_clean_lines(getattr(indicator, "warnings", None), limit=4))
    if not rows:
        rows.append(("Status", "Indicator available; no explanation, warning, or recommendation lines were returned."))
    rows.append(("Disclaimer", CAPITAL_STRUCTURE_LEVEL_DISCLAIMER))
    return rows


def _technical_capital_structure_indicator(report: TechnicalCommandCenterReport | None) -> CapitalStructureIndicatorRead | None:
    if report is None:
        return None
    indicator = getattr(report, "capital_structure_indicator", None)
    return indicator if indicator is not None else None


def _technical_timeframe_stack_rows(report: TechnicalCommandCenterReport | None) -> list[tuple[str, str, str, str, str, str, str, str, str]]:
    if report is None:
        return [("Unavailable", "--", "--", "--", "--", "--", "--", "--", "Technical Command Center report was not built.")]
    snapshots = _ordered_timeframe_snapshots(report)
    if not snapshots:
        return [("No timeframes", "--", "--", "--", "--", "--", "--", "--", "No command-center snapshots are available.")]
    rows: list[tuple[str, str, str, str, str, str, str, str, str]] = []
    for snapshot in snapshots:
        rows.append(
            (
                snapshot.label,
                _humanize_command_value(snapshot.role),
                _timeframe_trend_read(snapshot),
                _timeframe_momentum_read(snapshot),
                _timeframe_volume_read(snapshot),
                _format_command_percent(snapshot.vwap_distance_percent),
                _format_command_percent(snapshot.atr_percent),
                _humanize_command_value(snapshot.range_state),
                _timeframe_key_read(snapshot),
            )
        )
    return rows


def _technical_prc_rows(report: TechnicalCommandCenterReport | None) -> list[tuple[str, str, str, str, str, str, str]]:
    if report is None:
        return [("Unavailable", "--", "--", "--", "--", "Command-center PRC Pressure Line was not built.", "--")]
    prc_indexes = _ordered_prc_indexes(report)
    if not prc_indexes:
        return [("Unavailable", "--", "--", "--", "--", PRC_PRESSURE_LINE_NOTICE, "--")]
    if not any(prc.latest_price is not None or prc.index_price is not None for prc in prc_indexes):
        return [("Unavailable", "--", "--", "--", "--", PRC_PRESSURE_LINE_NOTICE, "--")]
    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for prc in prc_indexes:
        read = prc.read or "PRC read unavailable."
        if prc.warnings:
            read = f"{read} Data note: {prc.warnings[0]}"
        rows.append(
            (
                prc.timeframe_name,
                _money(prc.latest_price),
                _money(prc.index_price),
                _format_command_percent(prc.index_distance_percent),
                _format_command_percent(prc.index_slope),
                read,
                prc.confidence or "--",
            )
        )
    return rows


def _technical_score_breakdown_rows(report: TechnicalCommandCenterReport | None) -> list[tuple[str, str, str]]:
    if report is None:
        return [("Command Center", "--", "Technical Command Center report was not built.")]
    rows = [("Overall", f"{report.overall_score:.0f}/100", f"{report.overall_read}; confidence {report.confidence}; best action: {report.best_action}.")]
    if not report.scores:
        rows.append(("Components", "--", "No score components were returned."))
        return rows
    for name, component in report.scores.items():
        rows.append((name, f"{component.score:.0f}/100", component.reason))
    return rows


def _technical_ticket_check_rows(report: TechnicalCommandCenterReport | None) -> list[tuple[str, str, str]]:
    if report is None:
        return [("Ticket check", "Unavailable", "Technical Command Center report was not built.")]
    check = getattr(report, "ticket_check", None)
    if check is None:
        return [("Ticket check", "Unavailable", "No execution/ticket check was returned.")]
    lines = list(getattr(check, "lines", ()) or ())
    return [
        ("Entry location", _humanize_command_value(getattr(check, "entry_quality", "")), _first_line_containing(lines, ("Entry location", "Entry:")) or "Entry read unavailable."),
        ("Stop quality", _humanize_command_value(getattr(check, "stop_quality", "")), _first_line_containing(lines, ("Stop logic", "Stop:")) or "Stop read unavailable."),
        ("Reward/risk", _humanize_command_value(getattr(check, "risk_reward_read", "")), getattr(check, "risk_reward", "") or "Reward/risk unavailable."),
        ("Defined risk", getattr(check, "risk_note", "") or "Risk dollars unavailable.", _first_line_containing(lines, ("Estimated defined risk", "Risk dollars")) or "Add entry, quantity, and stop for dollar risk."),
        ("Target", _money(getattr(check, "target_price", None)), "Nearest visible technical target from the support/resistance map."),
        ("Verdict", getattr(check, "verdict", "") or "Unavailable", f"Ticket Quality score {getattr(getattr(check, 'score', None), 'score', 0.0):.0f}/100."),
    ]


def _technical_warning_rows(report: TechnicalCommandCenterReport | None) -> list[tuple[str, str]]:
    if report is None:
        return []
    rows: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(source: str, warning: str) -> None:
        clean = " ".join(str(warning or "").split())
        if not clean:
            return
        key = (source, clean)
        if key in seen:
            return
        seen.add(key)
        rows.append(key)

    for warning in report.warnings:
        add("Command Center", warning)
    for warning in report.setup_classification.warnings:
        add("Setup Classification", warning)
    for prc in _ordered_prc_indexes(report):
        for warning in prc.warnings:
            add(f"PRC {prc.timeframe_name}", warning)
    return rows


def _legacy_indicator_rows(indicators: AdvancedIndicatorSnapshot) -> list[tuple[str, str, str]]:
    rows = [
        ("SMA 20", _number(indicators.sma_20), "Short trend average"),
        ("SMA 50", _number(indicators.sma_50), "Intermediate trend average"),
        ("SMA 100", _number(indicators.sma_100), "Intermediate/long trend"),
        ("SMA 200", _number(indicators.sma_200), "Long trend reference"),
        ("EMA 12", _number(indicators.ema_12), "Fast momentum average"),
        ("EMA 26", _number(indicators.ema_26), "Slow momentum average"),
        ("MACD", _number(indicators.macd), TERM_HELPERS["MACD"]),
        ("MACD signal", _number(indicators.macd_signal), "9-period MACD signal; compare it with MACD for momentum turns."),
        ("RSI 14", _number(indicators.rsi_14), TERM_HELPERS["RSI"]),
        ("Bollinger upper", _number(indicators.bollinger_upper), TERM_HELPERS["Bollinger Bands"]),
        ("Bollinger middle", _number(indicators.bollinger_middle), "20 SMA band middle."),
        ("Bollinger lower", _number(indicators.bollinger_lower), TERM_HELPERS["Bollinger Bands"]),
        ("ATR 14", _number(indicators.atr_14), TERM_HELPERS["ATR"]),
        ("Volume avg 20", _number(indicators.volume_average_20), "Average daily volume"),
        ("Swing high", _number(indicators.swing_high), TERM_HELPERS["Swing high / swing low"]),
        ("Swing low", _number(indicators.swing_low), TERM_HELPERS["Swing high / swing low"]),
    ]
    rows.extend((f"Fib {label}", _money(value), TERM_HELPERS["Fibonacci retracement"]) for label, value in indicators.fibonacci_levels.items())
    return rows


def _ordered_timeframe_snapshots(report: TechnicalCommandCenterReport) -> list[Any]:
    ordered: list[Any] = []
    seen: set[str] = set()
    for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES:
        snapshot = report.snapshots.get(spec.key)
        if snapshot is not None:
            ordered.append(snapshot)
            seen.add(spec.key)
    for key, snapshot in report.snapshots.items():
        if key not in seen:
            ordered.append(snapshot)
    return ordered


def _ordered_prc_indexes(report: TechnicalCommandCenterReport) -> list[Any]:
    ordered: list[Any] = []
    seen: set[str] = set()
    for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES:
        prc = report.prc_indexes.get(spec.key)
        if prc is not None:
            ordered.append(prc)
            seen.add(spec.key)
    for key, prc in report.prc_indexes.items():
        if key not in seen:
            ordered.append(prc)
    return ordered


def _timeframe_trend_read(snapshot: Any) -> str:
    component = snapshot.scores.get("Trend") if getattr(snapshot, "scores", None) else None
    score_text = f"{component.score:.0f}/100" if component is not None else "--"
    return f"{_humanize_command_value(snapshot.trend_structure)}; {score_text}"


def _timeframe_momentum_read(snapshot: Any) -> str:
    parts: list[str] = []
    if snapshot.rsi_14 is not None:
        parts.append(f"RSI {snapshot.rsi_14:.1f}")
    if snapshot.macd_histogram is not None:
        parts.append(f"MACD hist {snapshot.macd_histogram:+.2f}")
    if snapshot.macd_histogram_change is not None:
        parts.append(f"change {snapshot.macd_histogram_change:+.2f}")
    return "; ".join(parts) if parts else "Momentum unavailable"


def _timeframe_volume_read(snapshot: Any) -> str:
    volume_read = snapshot.volume_read
    parts: list[str] = []
    if volume_read.relative_volume is not None:
        parts.append(f"RVOL {volume_read.relative_volume:.2f}x")
    if volume_read.up_down_volume_ratio is not None:
        parts.append(f"Up/down {volume_read.up_down_volume_ratio:.2f}")
    parts.append(_humanize_command_value(volume_read.accumulation_read))
    return "; ".join(parts)


def _timeframe_key_read(snapshot: Any) -> str:
    if snapshot.candle_count <= 0:
        return "No candles available for this timeframe."
    if snapshot.lines:
        return snapshot.lines[0]
    return f"Close {_money(snapshot.latest_close)}; support/resistance map is limited."


def _technical_status(value: Any) -> str:
    text = str(value or "").lower()
    if any(term in text for term in ("bullish", "breakout", "confirmed", "good_entry", "coherent", "constructive")):
        return "good"
    if any(term in text for term in ("bearish", "breakdown", "failed", "protect", "avoid", "no_edge", "weak")):
        return "bad"
    if any(term in text for term in ("range", "pullback", "early", "wait", "chop", "extended")):
        return "mixed"
    return "info"


def _capital_structure_read_status(indicator: CapitalStructureIndicatorRead) -> str:
    text = str(getattr(indicator, "read", "") or "").lower()
    if any(term in text for term in ("rally", "fade", "dilution", "mismatch", "offering_pressure")):
        return "bad"
    if any(term in text for term in ("verification", "watch")):
        return "mixed"
    if any(term in text for term in ("clean", "absorption", "low")):
        return "good"
    if any(term in text for term in ("context", "supply")):
        return "info"
    return _capital_structure_direction_status(getattr(indicator, "technical_score", None))


def _capital_structure_read_why(indicator: CapitalStructureIndicatorRead) -> str:
    recommendation = _bounded_clean_lines(getattr(indicator, "recommendation_lines", None), limit=1)
    if recommendation:
        return recommendation[0]
    return "Filing-derived supply terms modify chart confidence and chase risk."


def _capital_structure_level_why(indicator: CapitalStructureIndicatorRead) -> str:
    level = getattr(indicator, "nearest_supply_level", None)
    if level is None:
        return "No nearest filing-derived supply level was returned."
    label = getattr(indicator, "nearest_supply_level_label", None) or "parsed supply level"
    distance = _format_command_percent(getattr(indicator, "nearest_supply_level_distance_percent", None))
    return f"Nearest {label} is {_money(level)} ({distance} from latest); context only."


def _capital_structure_risk_status(value: Any) -> str:
    score = _optional_float(value)
    if score is None:
        return "info"
    if score >= 60:
        return "bad"
    if score >= 30:
        return "mixed"
    return "good"


def _capital_structure_direction_status(value: Any) -> str:
    score = _optional_float(value)
    if score is None:
        return "info"
    if score >= 70:
        return "good"
    if score >= 45:
        return "mixed"
    return "bad"


def _capital_structure_modifier_status(value: Any) -> str:
    score = _optional_float(value)
    if score is None:
        return "info"
    if score >= 0:
        return "good"
    if score <= -20:
        return "bad"
    return "mixed"


def _score_label(value: Any) -> str:
    score = _optional_float(value)
    return "--" if score is None else f"{score:.0f}/100"


def _signed_score_label(value: Any) -> str:
    score = _optional_float(value)
    return "--" if score is None else f"{score:+.0f}"


def _optional_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _safe_float(value: Any) -> float:
    number = _optional_float(value)
    return 0.0 if number is None else number


def _bounded_clean_lines(lines: Any, *, limit: int) -> list[str]:
    if not lines or limit <= 0:
        return []
    if isinstance(lines, str):
        iterable = [lines]
    else:
        try:
            iterable = list(lines)
        except TypeError:
            return []
    clean_lines: list[str] = []
    seen: set[str] = set()
    for line in iterable:
        clean = " ".join(str(line or "").split())
        if not clean or clean in seen:
            continue
        seen.add(clean)
        clean_lines.append(clean)
        if len(clean_lines) >= limit:
            break
    return clean_lines


def _humanize_command_value(value: Any) -> str:
    text = " ".join(str(value or "--").replace("_", " ").replace("-", " ").split())
    if text == "--":
        return text
    return text[:1].upper() + text[1:]


def _format_command_percent(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2f}%"


def _first_line_containing(lines: list[str], needles: tuple[str, ...]) -> str:
    for line in lines:
        if any(needle.lower() in line.lower() for needle in needles):
            return line
    return ""


def _replace_tree_rows(tree: ttk.Treeview, rows: list[tuple[Any, ...]]) -> None:
    for row_id in tree.get_children():
        tree.delete(row_id)
    for row in rows:
        tree.insert("", tk.END, values=row)


def _render_warning_panel(frame: ttk.Frame, rows: list[tuple[str, str]]) -> None:
    warning_box = frame.warning_box  # type: ignore[attr-defined]
    warning_tree = frame.warning_tree  # type: ignore[attr-defined]
    if not rows:
        _replace_tree_rows(warning_tree, [])
        warning_box.grid_remove()
        return
    warning_box.grid()
    _replace_tree_rows(warning_tree, rows)


def _render_technicals(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_technicals_frame
    indicators = payload.indicators
    decision = payload.decision
    command_report = payload.command_center_report
    narrative = build_technical_narrative(indicators, payload.context, decision.macro_backdrop.label)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        _technical_setup_cards(command_report),
        columns=3,
        card_height=132,
        prominent_height=142,
        adaptive_height=True,
    )
    if command_report is not None:
        momentum_component = command_report.scores.get("Momentum")
        risk_component = command_report.scores.get("Volatility/Risk")
        momentum_score = momentum_component.score if momentum_component is not None else decision.momentum_score
        risk_score = risk_component.score if risk_component is not None else decision.risk_score
        frame.bull_meter.set_score(command_report.overall_score, mode="direction", label=f"Command read: {command_report.overall_read} ({command_report.overall_score:.0f}/100)")  # type: ignore[attr-defined]
        frame.momentum_meter.set_score(momentum_score, mode="direction", label=f"Momentum component: {momentum_score:.0f}/100")  # type: ignore[attr-defined]
        frame.risk_meter.set_score(risk_score, mode="risk", label=f"Risk component: {risk_score:.0f}/100")  # type: ignore[attr-defined]
    else:
        frame.bull_meter.set_score(decision.technical_score, mode="direction", label=f"Bullishness: {direction_strength_label(decision.technical_score)} ({decision.technical_score:.0f})")  # type: ignore[attr-defined]
        frame.momentum_meter.set_score(decision.momentum_score, mode="direction", label=f"Momentum: {direction_strength_label(decision.momentum_score)} ({decision.momentum_score:.0f})")  # type: ignore[attr-defined]
        frame.risk_meter.set_score(decision.risk_score, mode="risk", label=f"Risk Heat: {risk_heat_label(decision.risk_score)} ({decision.risk_score:.0f})")  # type: ignore[attr-defined]

    metric_grid(
        frame.capital_cards,  # type: ignore[attr-defined]
        _technical_capital_structure_cards(command_report),
        columns=4,
        prominent_indexes={0},
        card_height=124,
        prominent_height=134,
        adaptive_height=True,
    )
    _replace_tree_rows(frame.capital_supply_tree, _technical_capital_structure_supply_rows(command_report))  # type: ignore[attr-defined]
    _replace_tree_rows(frame.capital_note_tree, _technical_capital_structure_note_rows(command_report))  # type: ignore[attr-defined]
    _replace_tree_rows(frame.timeframe_tree, _technical_timeframe_stack_rows(command_report))  # type: ignore[attr-defined]
    _replace_tree_rows(frame.prc_tree, _technical_prc_rows(command_report))  # type: ignore[attr-defined]
    _replace_tree_rows(frame.score_tree, _technical_score_breakdown_rows(command_report))  # type: ignore[attr-defined]
    _replace_tree_rows(frame.ticket_tree, _technical_ticket_check_rows(command_report))  # type: ignore[attr-defined]
    _render_warning_panel(frame, _technical_warning_rows(command_report))

    clear_children(frame.chart_readout)  # type: ignore[attr-defined]
    chart_rows = [f"{label}: {text}" for label, text in narrative.rows.items()]
    Checklist(frame.chart_readout, "What The Chart Is Saying", chart_rows).grid(row=0, column=0, sticky="nsew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(
        frame.chart_readout,
        "Position + Key Terms",
        [
            narrative.position_meaning,
            TERM_HELPERS["Confirmation"],
            TERM_HELPERS["Risk line"],
            TERM_HELPERS["Fibonacci retracement"],
        ],
    ).grid(row=0, column=1, sticky="nsew")  # type: ignore[attr-defined]
    tree = frame.indicator_tree  # type: ignore[attr-defined]
    _replace_tree_rows(tree, _legacy_indicator_rows(indicators))
    command_text = (
        format_technical_command_center_report(command_report)
        if command_report is not None
        else "Technical Command Center unavailable."
    )
    notes = "\n".join(
        [
            command_text,
            "",
            "What the chart is saying:",
            *[f"- {label}: {text}" for label, text in narrative.rows.items()],
            "",
            "Current position meaning:",
            f"- {narrative.position_meaning}",
            "",
            "Term helpers:",
            *[f"- {term}: {explanation}" for term, explanation in TERM_HELPERS.items()],
            "",
            "Raw technical notes:",
            *[f"- {note}" for note in indicators.notes],
        ]
    )
    _set_research_text(frame.technical_notes_text, _technical_popout_text(payload, narrative, notes))  # type: ignore[attr-defined]


def _technical_popout_text(payload: _ResearchPayload, narrative: Any, original_text: str) -> str:
    indicators = payload.indicators
    return _format_beginner_readout(
        title=f"Technical Readout - {payload.symbol}",
        what_this_means=(
            "This readout explains the chart signals behind the setup. It separates trend, momentum, volatility, "
            "support/resistance, and the terms used in the table."
        ),
        key_points=[
            f"Trend: {indicators.trend}.",
            f"Momentum: {indicators.momentum}.",
            f"Volatility: {indicators.volatility}.",
            f"Support / resistance: {_money(indicators.support)} / {_money(indicators.resistance)}.",
            f"Indicator agreement: {narrative.indicator_agreement}. {narrative.agreement_explanation}",
            narrative.position_meaning,
        ],
        why_it_matters="The technical read helps separate a setup that is improving from one that is only cheap, stretched, or risky. It also gives nearby levels for confirmation and invalidation.",
        original_text=original_text,
    )


def _render_scenarios(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_scenarios_frame
    risk_budget = generated_risk_budget(
        payload.context,
        payload.indicators,
        macro_label=payload.decision.macro_backdrop.label,
        risk_level_label=payload.decision.risk_level.label,
        action_bias_label=payload.decision.action_bias.label,
        earnings_text=payload.earnings_text,
        fundamentals_text=payload.fundamentals_text,
    )
    max_risk = risk_budget.amount
    self.schwab_research_max_risk_var.set(_money(max_risk))
    scenario_context, stock_plan = build_planned_stock_context(payload.context, payload.indicators, risk_budget)
    self.schwab_research_scenario_context = scenario_context
    self.schwab_research_stock_plan = stock_plan
    scenario_basis = technical_scenario_basis(payload.context, payload.indicators)
    self.schwab_research_scenario_basis_var.set(scenario_basis)
    scenario_moves = technical_scenario_moves(payload.context, payload.indicators)
    scenario_rows = build_scenario_rows(scenario_context, scenario_moves)
    comparison_rows = build_current_model_scenario_rows(payload.context, stock_plan, scenario_moves)
    candidates = getattr(self, "schwab_research_option_candidates", []) or []
    if not candidates:
        rows_map = getattr(self, "schwab_option_chain_rows", {}) or {}
        chain_rows = [row for row in rows_map.values() if isinstance(row, dict)]
        if chain_rows:
            candidates = suggest_option_candidates(
                chain_rows,
                payload.indicators,
                payload.context,
                macro_label=payload.decision.macro_backdrop.label,
                earnings_text=payload.earnings_text,
                risk_budget=max_risk,
                stock_plan=stock_plan,
            )
            setattr(self, "schwab_research_option_candidates", candidates)
    selected_candidate = _selected_option_candidate(self)
    top_candidate = selected_candidate if selected_candidate is not None and selected_candidate.option_type in {"call", "put"} else next((item for item in candidates if item.option_type in {"call", "put"}), None)
    fundamental_verdict = build_fundamental_verdict(payload.fundamentals_text, payload.indicators, payload.decision.macro_backdrop.label)
    risk_plan = build_risk_plan(payload.indicators, payload.context, payload.decision.macro_backdrop.label, fundamental_verdict.verdict, top_candidate, max_risk)
    negative_rows = [row for row in scenario_rows if row.position_pnl < 0]
    positive_rows = [row for row in scenario_rows if row.position_pnl > 0]
    worst = min(negative_rows or scenario_rows, key=lambda row: row.position_pnl, default=None)
    best = max(positive_rows or scenario_rows, key=lambda row: row.position_pnl, default=None)
    cards = [
        _synthetic_badge("Recommended Move", risk_plan.recommendation, risk_plan.status, risk_plan.reason),
        _synthetic_badge("Current Actual Exposure", _money(payload.context.market_value), "mixed" if payload.context.is_held else "info", f"{payload.context.quantity:g} shares; {payload.context.portfolio_weight:.2%} of portfolio."),
        _synthetic_badge("Model Stock Scenario Position", _shares(stock_plan.quantity), "info", _stock_plan_card_text(stock_plan)),
        _synthetic_badge("Generated Risk Budget", _money(max_risk), "info", _risk_budget_card_text(risk_budget)),
        _synthetic_badge("Paired Option", risk_plan.paired_option, "info", "Best loaded option candidate for this risk plan."),
    ]
    if worst is not None:
        cards.append(_synthetic_badge("Downside Pain", _money(worst.position_pnl), "bad" if worst.position_pnl < 0 else "info", f"{worst.scenario} move, {worst.portfolio_pnl_impact:+.2%} portfolio impact."))
    if best is not None:
        cards.append(_synthetic_badge("Upside Reward", _money(best.position_pnl), "good" if best.position_pnl > 0 else "info", f"{best.scenario} move, {best.portfolio_pnl_impact:+.2%} portfolio impact."))
    cards.extend([payload.decision.position_impact, payload.decision.risk_level])
    metric_grid(frame.cards, cards, columns=4, card_height=144, prominent_height=154, adaptive_height=True)  # type: ignore[attr-defined]
    move_tree = frame.move_planner_tree  # type: ignore[attr-defined]
    for row_id in move_tree.get_children():
        move_tree.delete(row_id)
    for move, makes_sense, protects, gives_up, effect in risk_plan.move_planner:
        move_tree.insert("", tk.END, values=(move, makes_sense, protects, gives_up, effect))
    max_abs = max((abs(row.portfolio_pnl_impact) for row in scenario_rows), default=0.0001)
    frame.impact_bars.set_rows([(row.scenario, scenario_impact_bar_value(row, max_abs), _money(row.position_pnl)) for row in scenario_rows])  # type: ignore[attr-defined]
    tree = frame.scenario_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    for row in comparison_rows:
        model_pnl = row.model_position_pnl
        tag_basis = model_pnl if model_pnl is not None else row.current_position_pnl
        tag = "positive" if tag_basis > 0 else "negative" if tag_basis < 0 else ""
        tree.insert(
            "",
            tk.END,
            values=(
                row.scenario,
                _money(row.symbol_price),
                _number(row.current_shares),
                _money(row.current_position_pnl),
                _number(row.model_shares),
                _money(row.model_position_pnl),
                _percent(row.model_portfolio_pnl_impact),
                _money(row.model_new_portfolio_value),
            ),
            tags=(tag,) if tag else (),
        )
    stop = _float_from_var(getattr(self, "stop_price_var", None))
    target = _float_from_var(getattr(self, "options_target_price_var", None)) or _float_from_var(getattr(self, "limit_price_var", None))
    size = suggested_position_size(entry_price=payload.context.last_price, stop_price=stop, max_risk_dollars=max_risk)
    decision_difference_lines = _decision_difference_lines(top_candidate, payload.context, stock_plan)
    lines = [
        "Recommended move:",
        f"- {risk_plan.recommendation}: {risk_plan.reason}",
        f"- {risk_plan.confirmation}",
        f"- {risk_plan.risk_line}",
        "",
        "Decision difference:",
        *[f"- {line}" for line in decision_difference_lines],
        "",
        "How to read this:",
        "- Current columns use actual shares already held in the portfolio.",
        "- Model columns use the generated stock scenario position from the card above.",
        "- Combined rows below use expiration-style option payoff, not live option pricing.",
        "- Protective put: insurance that can gain value if your shares fall, but the premium is paid upfront.",
        "- Covered call: income against held shares, but upside can be capped above the strike.",
        "",
        "Generated risk budget:",
        f"- Amount: {_money(max_risk)}.",
        f"- Base: {_money(risk_budget.base_amount)}; technical line: {_money(risk_budget.technical_amount)}; portfolio cap: {_money(risk_budget.portfolio_cap)}; cash cap: {_money(risk_budget.cash_cap)}.",
        *[f"- {factor}." for factor in risk_budget.factors[:6]],
        "",
        "Generated stock scenario position:",
        f"- Shares: {_shares(stock_plan.quantity)}; notional: {_money(stock_plan.notional)}; portfolio weight: {stock_plan.portfolio_weight:.2%}.",
        f"- Entry: {_money(stock_plan.entry_price)}; stop: {_money(stock_plan.stop_price)}; per-share risk: {_money(stock_plan.per_share_risk)}.",
        f"- {stock_plan.basis}",
        "",
        "Stock-only scenario notes:",
        f"- Stop distance: {_percent(distance_to_price(payload.context.last_price, stop))}.",
        f"- Target distance: {_percent(distance_to_price(payload.context.last_price, target))}.",
        f"- Suggested size at {_money(max_risk)} max risk and ticket stop {_money(stop)}: {_shares(size)}.",
        f"- Scenario basis: {scenario_basis}",
    ]
    _set_research_text(frame.scenario_note_text, _risk_scenario_popout_text(payload, risk_plan, decision_difference_lines, "\n".join(lines)))  # type: ignore[attr-defined]
    _render_option_scenarios_from_top(self)


def _risk_scenario_popout_text(payload: _ResearchPayload, risk_plan: Any, decision_difference_lines: list[str], original_text: str) -> str:
    title = f"Risk Scenario Explanation - {payload.symbol}"
    key_points = [
        f"Recommended move: {risk_plan.recommendation}. {risk_plan.reason}",
        risk_plan.confirmation,
        risk_plan.risk_line,
        *decision_difference_lines,
    ]
    lines = [
        title,
        "=" * min(len(title), 80),
        "",
        "What this means:",
        (
            "This section explains the suggested risk move, what would confirm it, what would invalidate it, "
            "and how the generated stock scenario size was calculated."
        ),
        "",
        "Key points:",
    ]
    lines.extend(_bullet_line(point) for point in key_points if str(point).strip())
    lines.extend(
        [
            "",
            "Why it matters:",
            "The scenario table shows possible P&L paths, but this readout explains how to use those paths for sizing, hedging, waiting, or rejecting the setup.",
            "",
            "Original / detailed readout:",
            original_text.strip(),
        ]
    )
    return "\n".join(lines)


def _render_options_strategy(self: tk.Tk) -> None:
    frame = getattr(self, "schwab_research_options_frame", None)
    payload = getattr(self, "schwab_research_last_payload", None)
    if frame is None:
        return
    tree = frame.candidate_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    rows_map = getattr(self, "schwab_option_chain_rows", {}) or {}
    chain_rows = [row for row in rows_map.values() if isinstance(row, dict)]
    if payload is None:
        frame.status_var.set("Run technical analysis first; candidates need the symbol read, macro context, and position context.")  # type: ignore[attr-defined]
        _set_research_text(frame.detail_text, _basic_popout_text("Options Strategy Explanation", "Run analysis first, then load the option chain to generate candidates."))  # type: ignore[attr-defined]
        metric_grid(frame.cards, [_synthetic_badge("Options Strategy", "Waiting", "info", "Needs analysis and option chain.")], columns=1)  # type: ignore[attr-defined]
        return
    if not chain_rows:
        frame.status_var.set("Load the option chain to generate option candidates.")  # type: ignore[attr-defined]
        _set_research_text(frame.detail_text, _basic_popout_text("Options Strategy Explanation", "Load the option chain to generate option candidates.\n\nUse the Load Chain button inside this tab. No order will be submitted."))  # type: ignore[attr-defined]
        metric_grid(frame.cards, [_synthetic_badge("Chain", "Not Loaded", "info", "Load the option chain to generate candidates.")], columns=1)  # type: ignore[attr-defined]
        return
    candidates = suggest_option_candidates(
        chain_rows,
        payload.indicators,
        payload.context,
        macro_label=payload.decision.macro_backdrop.label,
        earnings_text=payload.earnings_text,
        risk_budget=_float_from_var(getattr(self, "schwab_research_max_risk_var", None)),
        stock_plan=getattr(self, "schwab_research_stock_plan", None),
    )
    self.schwab_research_option_candidates = candidates
    if not candidates:
        frame.status_var.set("No usable option candidates found in the loaded chain.")  # type: ignore[attr-defined]
        _set_research_text(frame.detail_text, _basic_popout_text("Options Strategy Explanation", "The loaded chain did not include usable bid/ask/mark data for calls or puts."))  # type: ignore[attr-defined]
        return
    for index, candidate in enumerate(candidates):
        tree.insert(
            "",
            tk.END,
            iid=f"candidate_{index}",
            values=(
                candidate.group,
                candidate.strategy,
                candidate.expiration,
                _money(candidate.strike),
                candidate.option_type.upper(),
                _money(candidate.midpoint),
                "Unlimited/stock" if candidate.max_loss is None else _money(candidate.max_loss),
                _money(candidate.breakeven),
                f"{candidate.score:.0f}",
                candidate.confidence,
            ),
        )
    tree.selection_set("candidate_0")
    _render_option_strategy_cards(frame, _selected_option_candidate(self) or candidates[0], candidates, payload.context, getattr(self, "schwab_research_stock_plan", None))
    frame.status_var.set(f"{len(candidates)} candidates generated from loaded {payload.symbol} chain. Select one and click Use This Option to fill fields only.")  # type: ignore[attr-defined]
    _show_selected_option_candidate(self)
    _render_scenarios(self, payload)


def _render_option_strategy_cards(
    frame: ttk.Frame,
    selected: OptionCandidate,
    candidates: list[OptionCandidate],
    context: PortfolioSymbolContext,
    model_position: Any | None = None,
) -> None:
    actionable = _best_actionable_contract(candidates)
    selected_is_contract = _is_actionable_contract(selected)
    position_candidate = selected if selected_is_contract else actionable
    cards = [
        _synthetic_badge("Selected Candidate", selected.strategy, _candidate_status(selected.confidence), selected.why),
    ]
    if not selected_is_contract and actionable is not None:
        entry = _candidate_entry_price(actionable)
        action_text = f"{actionable.strategy}; score {actionable.score:.0f}/100"
        action_why = (
            f"Best actionable contract: {actionable.option_type.upper()} expiring {actionable.expiration}, "
            f"strike {_money(actionable.strike)}, entry {_money(entry)}, {actionable.contract_count} contract(s)."
        )
        cards.append(_synthetic_badge("Best Actionable Contract", action_text, _candidate_status(actionable.confidence), action_why))

    entry = _candidate_entry_price(selected) if selected_is_contract else None
    entry_why = _candidate_entry_basis(selected) if selected_is_contract else "No contract is selected for wait/no-trade."
    contracts = max(int(selected.contract_count or 0), 0) if selected_is_contract else 0
    contracts_label = str(contracts) if selected_is_contract else "--"
    multiplier_text = (
        f"Option multiplier: {contracts * 100} shares equivalent."
        if selected_is_contract
        else "No option multiplier because no contract is selected."
    )
    cards.extend(
        [
            _synthetic_badge("Entry", _money(entry), "info", entry_why),
            _synthetic_badge("Contracts", contracts_label, "info", multiplier_text),
            _synthetic_badge("Risk Read", selected.confidence, _candidate_status(selected.confidence), selected.goes_wrong_if),
        ]
    )
    position_readout = option_position_readout(position_candidate, context, model_position)
    if position_readout is not None:
        cards.append(_synthetic_badge(position_readout.title, position_readout.label, position_readout.status, position_readout.detail))
    if selected.practical_warnings:
        warning_text = " ".join(selected.practical_warnings[:2])
        cards.append(_synthetic_badge("Option Warning", "Check Fit", "bad", warning_text))
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        cards,
        columns=4,
        prominent_indexes={0},
        card_height=156,
        prominent_height=172,
        adaptive_height=True,
    )


def _best_actionable_contract(candidates: list[OptionCandidate]) -> OptionCandidate | None:
    return next((candidate for candidate in candidates if _is_actionable_contract(candidate)), None)


def _is_actionable_contract(candidate: OptionCandidate | None) -> bool:
    return candidate is not None and candidate.option_type in {"call", "put"}


def _candidate_entry_price(candidate: OptionCandidate) -> float | None:
    if candidate.midpoint is not None:
        return candidate.midpoint
    if candidate.mark is not None:
        return candidate.mark
    return candidate.ask


def _candidate_entry_basis(candidate: OptionCandidate) -> str:
    if candidate.midpoint is not None:
        return "Estimated entry uses the selected contract midpoint."
    if candidate.mark is not None:
        return "Midpoint unavailable; estimated entry falls back to selected contract mark."
    if candidate.ask is not None:
        return "Midpoint and mark unavailable; estimated entry falls back to selected contract ask."
    return "Selected contract has no usable midpoint, mark, or ask."


def _option_scenario_read(read: str, move_note: str = "") -> str:
    pieces = [read]
    if move_note:
        pieces.append(move_note)
    pieces.append("expiration payoff estimate")
    return "; ".join(piece for piece in pieces if piece)


def _load_chain_from_research_tab(self: tk.Tk) -> None:
    payload = getattr(self, "schwab_research_last_payload", None)
    symbol = getattr(self, "schwab_research_symbol_var", tk.StringVar(value="")).get().strip().upper()
    if payload is not None:
        symbol = payload.symbol
    if symbol and hasattr(self, "symbol_var"):
        self.symbol_var.set(symbol)
    command = getattr(self, "load_schwab_option_chain", None)
    if command is None:
        messagebox.showerror("Option chain unavailable", "The Schwab option-chain loader is not installed.")
        return
    command()
    _refresh_payload_option_chain_evidence(self)
    _render_options_strategy(self)
    _render_greeks(self)


def _load_greeks_from_research_tab(self: tk.Tk) -> None:
    _load_chain_from_research_tab(self)


def _refresh_payload_option_chain_evidence(self: tk.Tk) -> None:
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is None:
        return
    rows_map = getattr(self, "schwab_option_chain_rows", {}) or {}
    chain_rows = [row for row in rows_map.values() if isinstance(row, dict)]
    if not chain_rows:
        return
    underlying_price = _float_from_var(getattr(self, "options_underlying_price_var", None)) or payload.option_chain_underlying_price or payload.context.last_price
    greek_summary = build_greek_summary(chain_rows, underlying_price)
    trade_evidence_report = build_trade_evidence_report(
        symbol=payload.symbol,
        indicators=payload.indicators,
        context=payload.context,
        decision=payload.decision,
        scenario_rows=payload.scenario_rows,
        earnings_text=payload.earnings_text,
        macro_text=payload.macro_text,
        statuses=payload.statuses,
        quote=payload.quote,
        option_chain_rows=chain_rows,
        symbol_candles=payload.daily_candles or [],
        command_center_snapshots=payload.command_center_report.snapshots if payload.command_center_report else None,
        market_indicators=payload.market_indicators or {},
        market_candles=payload.market_candles or {},
    )
    updated = replace(
        payload,
        option_chain_rows=chain_rows,
        option_chain_underlying_price=underlying_price,
        greek_summary=greek_summary,
        trade_evidence_report=trade_evidence_report,
    )
    self.schwab_research_last_payload = updated
    _render_trade_evidence(self, updated)


def _render_greeks(self: tk.Tk, payload: _ResearchPayload | None = None) -> None:
    frame = getattr(self, "schwab_research_greeks_frame", None)
    if frame is None:
        return
    payload = payload or getattr(self, "schwab_research_last_payload", None)
    rows_map = getattr(self, "schwab_option_chain_rows", {}) or {}
    chain_rows = [row for row in rows_map.values() if isinstance(row, dict)]
    selected_candidate = _selected_option_candidate(self)
    selected_contract_symbol = str(getattr(self, "schwab_research_selected_contract_symbol", "") or "")
    underlying_price = _greek_underlying_price(self, payload)
    if not chain_rows and payload is not None and payload.greek_summary is not None:
        summary = payload.greek_summary
    else:
        summary = build_greek_summary(
            chain_rows,
            underlying_price,
            selected_candidate=selected_candidate,
            selected_contract_symbol=selected_contract_symbol,
        )
    self.schwab_research_greek_summary = summary
    active = summary.selected or summary.nearest_call or summary.nearest_put
    if not summary.rows:
        frame.status_var.set("Greeks unavailable until a Schwab option chain is loaded.")  # type: ignore[attr-defined]
        metric_grid(frame.cards, [_synthetic_badge("Option Sensitivities", "Waiting", "info", "Run analysis or load/refresh Greeks to fetch the option chain.")], columns=1)  # type: ignore[attr-defined]
    else:
        source = active.source_summary if active is not None else "Mixed"
        active_label = _greek_contract_short_label(active) if active is not None else "ATM contract"
        frame.status_var.set(f"{len(summary.rows)} contracts loaded. Active Greeks: {active_label}. Source: {source}.")  # type: ignore[attr-defined]
        metric_grid(
            frame.cards,
            _greek_metric_cards(active),
            columns=4,
            prominent_indexes={0},
            card_height=136,
            prominent_height=144,
            adaptive_height=True,
        )  # type: ignore[attr-defined]
    _render_greek_table(frame, summary)
    _render_greek_interpretation(frame, summary)
    frame.detail_text._greek_summary = summary  # type: ignore[attr-defined]
    frame.detail_text._greek_payload = payload  # type: ignore[attr-defined]
    _set_research_text(frame.detail_text, _greeks_popout_text(payload, summary))  # type: ignore[attr-defined]


def _greek_underlying_price(self: tk.Tk, payload: _ResearchPayload | None) -> float | None:
    if payload is not None:
        return payload.option_chain_underlying_price or payload.context.last_price
    value = _float_from_var(getattr(self, "options_underlying_price_var", None))
    if value is not None:
        return value
    last_payload = getattr(self, "schwab_research_last_payload", None)
    return getattr(getattr(last_payload, "context", None), "last_price", None)


def _render_greek_table(frame: ttk.Frame, summary: GreekSummary) -> None:
    tree = frame.greek_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    selected_iid = ""
    for index, snapshot in enumerate(summary.rows):
        iid = f"greek_{index}"
        values = (
            snapshot.expiration,
            "--" if snapshot.dte is None else str(snapshot.dte),
            _plain_number(snapshot.strike, digits=2),
            snapshot.option_type.upper(),
            _plain_number(snapshot.bid, digits=2),
            _plain_number(snapshot.ask, digits=2),
            _plain_number(snapshot.mark, digits=2),
            _iv_label(snapshot.implied_volatility.value),
            _signed_number(snapshot.delta.value, digits=3),
            _signed_number(snapshot.gamma.value, digits=4),
            _signed_number(snapshot.theta.value, digits=3),
            _signed_number(snapshot.vega.value, digits=3),
            _signed_number(snapshot.rho.value, digits=3),
            snapshot.source_summary,
        )
        tags = ("selected",) if snapshot.selected else ()
        tree.insert("", tk.END, iid=iid, values=values, tags=tags)
        if snapshot.selected:
            selected_iid = iid
    if selected_iid:
        tree.selection_set(selected_iid)
        tree.focus(selected_iid)
        tree.see(selected_iid)


def _render_greek_interpretation(frame: ttk.Frame, summary: GreekSummary) -> None:
    clear_children(frame.interpretation)  # type: ignore[attr-defined]
    Checklist(frame.interpretation, "Plain-English Interpretation", summary.plain_english).grid(row=0, column=0, sticky="nsew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.interpretation, "Sources / Caveats", _greek_source_lines(summary)).grid(row=0, column=1, sticky="nsew")  # type: ignore[attr-defined]


def _greek_metric_cards(active: OptionGreekSnapshot | None) -> list[BadgeReadout]:
    if active is None:
        return [_synthetic_badge("Option Sensitivities", "Waiting", "info", "No active contract is available yet.")]
    return [
        _synthetic_badge("Stock Move Sensitivity / Delta", _signed_number(active.delta.value, digits=3), _greek_status(active.delta), _greek_card_text(active.delta, "Approximate contract impact for a $1 stock move.")),
        _synthetic_badge("Time Decay / Theta", _signed_number(active.theta.value, digits=3), _theta_status(active.theta.value), _greek_card_text(active.theta, "Per-day time decay, all else equal.")),
        _synthetic_badge("Volatility Sensitivity / Vega", _signed_number(active.vega.value, digits=3), _greek_status(active.vega), _greek_card_text(active.vega, "Per one-point implied-volatility move.")),
        _synthetic_badge("Delta Acceleration / Gamma", _signed_number(active.gamma.value, digits=4), _greek_status(active.gamma), _greek_card_text(active.gamma, "How much delta changes after a $1 stock move.")),
        _synthetic_badge("Rate Sensitivity / Rho", _signed_number(active.rho.value, digits=3), _greek_status(active.rho), _greek_card_text(active.rho, "Per one-point interest-rate move.")),
    ]


def _greek_card_text(value: GreekValue, available_text: str) -> str:
    if value.value is None or value.source == "Unavailable":
        return "Unavailable for the active contract. Missing from Schwab chain and not enough inputs to estimate."
    return f"{value.source}. {available_text}"


def _greek_source_lines(summary: GreekSummary) -> list[str]:
    active = summary.selected or summary.nearest_call or summary.nearest_put
    lines: list[str] = []
    if active is None:
        lines.append("No active contract is loaded.")
    else:
        lines.extend(
            [
                f"Active contract: {_greek_contract_short_label(active)}.",
                f"IV: {active.implied_volatility.source}.",
                f"Delta: {active.delta.source}.",
                f"Gamma: {active.gamma.source}.",
                f"Theta: {active.theta.source}.",
                f"Vega: {active.vega.source}.",
                f"Rho: {active.rho.source}.",
            ]
        )
        lines.extend(active.warnings)
    lines.extend(summary.warnings)
    if not lines:
        lines.append("All displayed active values have explicit source labels.")
    return lines


def _greeks_popout_text(payload: _ResearchPayload | None, summary: GreekSummary) -> str:
    symbol = payload.symbol if payload is not None else summary.underlying or "Symbol"
    active = summary.selected or summary.nearest_call or summary.nearest_put
    key_points = summary.plain_english or ["Load an option chain to see option sensitivities."]
    if active is not None:
        key_points.extend(
            [
                f"Active source mix: {active.source_summary}.",
                f"Delta source: {active.delta.source}; gamma source: {active.gamma.source}; theta source: {active.theta.source}.",
                f"Vega source: {active.vega.source}; rho source: {active.rho.source}; IV source: {active.implied_volatility.source}.",
            ]
        )
    original_lines = [
        f"Underlying price: {_money(summary.underlying_price)}",
        f"Rows loaded: {len(summary.rows)}",
        "",
        "Warnings / caveats:",
        *[f"- {line}" for line in (summary.warnings or ["None."])],
        "",
        "Contract rows:",
    ]
    for snapshot in summary.rows[:30]:
        original_lines.append(
            f"- {_greek_contract_short_label(snapshot)} | IV {_iv_label(snapshot.implied_volatility.value)} ({snapshot.implied_volatility.source}) | "
            f"Delta {_signed_number(snapshot.delta.value, digits=3)} ({snapshot.delta.source}) | "
            f"Gamma {_signed_number(snapshot.gamma.value, digits=4)} ({snapshot.gamma.source}) | "
            f"Theta {_signed_number(snapshot.theta.value, digits=3)} ({snapshot.theta.source}) | "
            f"Vega {_signed_number(snapshot.vega.value, digits=3)} ({snapshot.vega.source}) | "
            f"Rho {_signed_number(snapshot.rho.value, digits=3)} ({snapshot.rho.source})"
        )
    base_text = _format_beginner_readout(
        title=f"Option Sensitivities - {symbol}",
        what_this_means=(
            "This tab shows option Greeks for the loaded Schwab chain. Schwab-provided values are used first; "
            "missing sensitivities are estimated locally when the contract has enough inputs."
        ),
        key_points=key_points,
        why_it_matters="Greeks translate an option into stock-move, time-decay, volatility, and rate exposures before any order is previewed or submitted.",
        original_text="\n".join(original_lines),
    )
    decision = build_greek_decision_section(summary, atr=getattr(getattr(payload, "indicators", None), "atr_14", None))
    return base_text.replace("\n\nOriginal / detailed readout:", f"\n\n{decision}\n\nOriginal / detailed readout:", 1)


def _greek_contract_short_label(snapshot: OptionGreekSnapshot | None) -> str:
    if snapshot is None:
        return "--"
    strike = _plain_number(snapshot.strike, digits=2)
    return f"{snapshot.expiration} {strike} {snapshot.option_type.upper()}"


def _greek_status(value: GreekValue) -> str:
    if value.source == "Unavailable" or value.value is None:
        return "info"
    if value.source == "Schwab provided":
        return "good"
    return "mixed"


def _theta_status(value: float | None) -> str:
    if value is None:
        return "info"
    return "bad" if value < 0 else "mixed"


def _plain_number(value: float | None, *, digits: int = 2) -> str:
    if value is None:
        return "--"
    return _format_number(value, digits=digits)


def _signed_number(value: float | None, *, digits: int = 3) -> str:
    if value is None:
        return "--"
    return f"{value:+.{digits}f}"


def _iv_label(value: float | None) -> str:
    if value is None:
        return "--"
    return f"{value * 100:.1f}%"


def _load_chain_from_option_scenario_row(self: tk.Tk) -> None:
    frame = getattr(self, "schwab_research_scenarios_frame", None)
    if frame is None or not hasattr(frame, "option_scenario_tree"):
        return
    tree = frame.option_scenario_tree  # type: ignore[attr-defined]
    row_id = tree.focus() or (tree.selection()[0] if tree.selection() else "")
    values = tree.item(row_id, "values") if row_id else ()
    if values and str(values[0]).lower().startswith("load chain"):
        _load_chain_from_research_tab(self)


def _selected_option_candidate(self: tk.Tk) -> OptionCandidate | None:
    frame = getattr(self, "schwab_research_options_frame", None)
    if frame is None:
        return None
    tree = frame.candidate_tree  # type: ignore[attr-defined]
    selection = tree.selection()
    if not selection:
        return None
    try:
        index = int(str(selection[0]).replace("candidate_", ""))
    except ValueError:
        return None
    candidates = getattr(self, "schwab_research_option_candidates", []) or []
    return candidates[index] if 0 <= index < len(candidates) else None


def _show_selected_option_candidate(self: tk.Tk) -> None:
    frame = getattr(self, "schwab_research_options_frame", None)
    candidate = _selected_option_candidate(self)
    if frame is None or candidate is None:
        return
    payload = getattr(self, "schwab_research_last_payload", None)
    earnings_text = payload.earnings_text if payload is not None else ""
    context = _active_stock_scenario_context(self, payload) if payload is not None else None
    candidates = getattr(self, "schwab_research_option_candidates", []) or []
    if payload is not None:
        _render_option_strategy_cards(frame, candidate, candidates, payload.context, getattr(self, "schwab_research_stock_plan", None))
    frame.timeline_var.set(option_timeline_text(candidate, earnings_text))  # type: ignore[attr-defined]
    _render_candidate_score_breakdown(frame, candidate)
    scenario_tree = frame.candidate_scenario_tree  # type: ignore[attr-defined]
    for row_id in scenario_tree.get_children():
        scenario_tree.delete(row_id)
    if payload is not None and _is_actionable_contract(candidate):
        stock_plan = getattr(self, "schwab_research_stock_plan", None)
        moves = option_strategy_scenario_moves(candidate, payload.indicators)
        scenario_rows = combined_current_model_option_scenarios(candidate, payload.context, stock_plan, moves=moves)
        frame.candidate_bars.set_rows(_normalized_current_model_option_bar_rows(scenario_rows))  # type: ignore[attr-defined]
        for move, row in zip(moves, scenario_rows):
            move_note = option_strategy_scenario_move_note(candidate, payload.indicators, move)
            tag_basis = row.model_combined_pnl if row.model_combined_pnl is not None else row.current_combined_pnl
            tag = "positive" if tag_basis > 0 else "negative" if tag_basis < 0 else ""
            scenario_tree.insert(
                "",
                tk.END,
                values=(
                    row.move_label,
                    _money(row.underlying_price),
                    candidate.contract_count,
                    _money(row.current_stock_pnl),
                    _money(row.model_stock_pnl),
                    _money(row.option_pnl),
                    _money(row.current_combined_pnl),
                    _money(row.model_combined_pnl),
                    _option_scenario_read(row.read, move_note),
                ),
                tags=(tag,) if tag else (),
            )
        lines = selected_candidate_detail(candidate, context or payload.context, earnings_text)
    elif context is not None and _is_actionable_contract(candidate):
        moves = option_strategy_scenario_moves(candidate, None)
        scenario_rows = combined_option_scenarios(candidate, context, moves=moves)
        frame.candidate_bars.set_rows(_normalized_candidate_bar_rows(scenario_rows))  # type: ignore[attr-defined]
        for move, row in zip(moves, scenario_rows):
            move_note = option_strategy_scenario_move_note(candidate, None, move)
            tag = "positive" if row.combined_pnl > 0 else "negative" if row.combined_pnl < 0 else ""
            scenario_tree.insert(
                "",
                tk.END,
                values=(row.move_label, _money(row.underlying_price), candidate.contract_count, _money(row.stock_pnl), "--", _money(row.option_pnl), _money(row.combined_pnl), "--", _option_scenario_read(row.read, move_note)),
                tags=(tag,) if tag else (),
            )
        lines = selected_candidate_detail(candidate, context, earnings_text)
    elif context is not None:
        frame.candidate_bars.set_rows([])  # type: ignore[attr-defined]
        scenario_tree.insert(
            "",
            tk.END,
            values=("No option", _money(candidate.underlying_price), "--", "--", "--", "--", "--", "--", "Wait/no-trade has no contract, so no expiration payoff estimate is calculated."),
        )
        lines = selected_candidate_detail(candidate, context, earnings_text)
    else:
        frame.candidate_bars.set_rows([])  # type: ignore[attr-defined]
        lines = [f"{candidate.group}: {candidate.strategy}", "Run analysis to see combined stock + option scenarios."]
    lines.extend(["", "Use This Option fills the existing options ticket only. It does not submit, preview, or stage an order."])
    _set_research_text(frame.detail_text, _options_strategy_popout_text(payload, candidate, context, "\n".join(lines), alternatives=candidates))  # type: ignore[attr-defined]
    self.schwab_research_selected_contract_symbol = candidate.contract_symbol
    _render_greeks(self, payload)
    if payload is not None:
        _render_scenarios(self, payload)


def _render_candidate_score_breakdown(frame: ttk.Frame, candidate: OptionCandidate) -> None:
    move = _percent(candidate.expected_move_required) if candidate.expected_move_required is not None else "--"
    if _is_actionable_contract(candidate):
        rows = {
            "Technical Fit": f"{candidate.technical_fit_score:.0f}/100",
            "Liquidity Fit": f"{candidate.liquidity_score:.0f}/100; spread {_percent(candidate.spread_pct) if candidate.spread_pct is not None else '--'}",
            "Greek Fit": f"{candidate.greek_score:.0f}/100; delta {_number(candidate.delta)}; IV {_percent(candidate.iv) if candidate.iv is not None else '--'}",
            "Risk-Budget Fit": f"{candidate.risk_budget_score:.0f}/100; max loss {_money(candidate.max_loss)}",
            "Move To Breakeven": move,
            "Stock Comparison": candidate.better_than_stock or "No stock-only comparison was available.",
        }
    else:
        rows = {
            "No-Trade Action Score": f"{candidate.score:.0f}/100",
            "Liquidity Fit": "Not scored; no contract selected.",
            "Greek Fit": "Not scored; no delta/theta/IV exposure.",
            "Risk-Budget Fit": "Not scored; no option capital committed.",
            "Move To Breakeven": "Not applicable.",
            "Stock Comparison": candidate.better_than_stock or "Waiting keeps the stock plan optional.",
        }
    if candidate.avoid_reason:
        rows["Avoid / Low Rank Reason"] = candidate.avoid_reason
    if candidate.practical_warnings:
        rows["Option Warning"] = "; ".join(candidate.practical_warnings)
    breakdown = getattr(frame, "score_breakdown", None)
    if breakdown is not None:
        labeled_value_grid(breakdown, rows, columns=3)


def _basic_popout_text(title: str, original_text: str) -> str:
    return _format_beginner_readout(
        title=title,
        what_this_means="This readout will update after the required data is available.",
        key_points=[line for line in original_text.splitlines() if line.strip()],
        why_it_matters="The main tab stays compact while keeping the full generated explanation available when there is enough data to show it.",
        original_text=original_text,
    )


def _options_strategy_popout_text(
    payload: _ResearchPayload | None,
    candidate: OptionCandidate,
    context: PortfolioSymbolContext | None,
    original_text: str,
    *,
    alternatives: list[OptionCandidate] | None = None,
) -> str:
    symbol = payload.symbol if payload is not None else candidate.underlying
    contracts = max(candidate.contract_count, 0)
    multiplier = contracts * 100
    cost = (candidate.midpoint or 0.0) * multiplier
    key_points = [
        f"Candidate: {candidate.group}: {candidate.strategy}",
        f"Contract: {candidate.option_type.upper()} expiring {candidate.expiration}; strike {_money(candidate.strike)}; DTE {candidate.dte if candidate.dte is not None else '--'}.",
        f"Bid/ask/mid: {_money(candidate.bid)} / {_money(candidate.ask)} / {_money(candidate.midpoint)}; {contracts} contract estimate {_money(cost)}.",
        f"Option multiplier: {multiplier} shares equivalent.",
        f"Works if: {candidate.works_if}",
        f"Goes wrong if: {candidate.goes_wrong_if}",
        f"Position interaction: {candidate.relation_to_position}",
        f"Score: {candidate.score:.0f}/100. {candidate.score_reason}",
        f"Better/worse than stock: {candidate.better_than_stock or 'No stock-only comparison was available.'}",
    ]
    key_points.extend(candidate.score_breakdown)
    if candidate.avoid_reason:
        key_points.append(candidate.avoid_reason)
    if candidate.coverage_note:
        key_points.append(candidate.coverage_note)
    key_points.extend(f"Warning: {warning}" for warning in candidate.practical_warnings)
    key_points.extend(_candidate_alternative_lines(candidate, alternatives or []))
    if context is not None:
        key_points.append(f"Stock context: {context.quantity:g} shares, weight {context.portfolio_weight:.2%}, quote {_money(context.last_price)}.")
    return _format_beginner_readout(
        title=f"Options Strategy Explanation - {symbol}",
        what_this_means=(
            "This explains the selected option candidate and how it interacts with the stock scenario. "
            "The estimates are expiration-style payoff math, not live option pricing."
        ),
        key_points=key_points,
        why_it_matters="Options can define risk, add leverage, or hedge a stock position, but the premium, expiration, strike, and required move decide whether the trade is worth considering.",
        original_text=original_text,
    )


def _candidate_alternative_lines(candidate: OptionCandidate, alternatives: list[OptionCandidate]) -> list[str]:
    lines: list[str] = []
    rejected = [item for item in alternatives if item.key != candidate.key][:3]
    if not rejected:
        return lines
    for item in rejected:
        reason = item.avoid_reason or item.score_reason or item.goes_wrong_if
        lines.append(f"Alternative ranked lower: {item.group}: {item.strategy} scored {item.score:.0f}/100 because {reason}")
    return lines


def _normalized_candidate_bar_rows(scenario_rows: list[Any]) -> list[tuple[str, float, str]]:
    max_abs = max(max((abs(float(getattr(row, "combined_pnl", 0.0) or 0.0)) for row in scenario_rows), default=0.0), 0.0001)
    return [
        (
            str(getattr(row, "move_label", "")),
            (float(getattr(row, "combined_pnl", 0.0) or 0.0) / max_abs) * 100,
            _money(float(getattr(row, "combined_pnl", 0.0) or 0.0)),
        )
        for row in scenario_rows
    ]


def _normalized_current_model_option_bar_rows(scenario_rows: list[Any]) -> list[tuple[str, float, str]]:
    values = [
        float(
            getattr(row, "model_combined_pnl", None)
            if getattr(row, "model_combined_pnl", None) is not None
            else getattr(row, "current_combined_pnl", 0.0)
            or 0.0
        )
        for row in scenario_rows
    ]
    max_abs = max(max((abs(value) for value in values), default=0.0), 0.0001)
    return [
        (
            str(getattr(row, "move_label", "")),
            (value / max_abs) * 100,
            _money(value),
        )
        for row, value in zip(scenario_rows, values)
    ]


def _use_selected_research_option(self: tk.Tk) -> None:
    candidate = _selected_option_candidate(self)
    if candidate is None:
        messagebox.showinfo("Choose candidate", "Select an option candidate first.")
        return
    if candidate.option_type not in {"call", "put"}:
        messagebox.showinfo("No option to use", "The selected candidate is a wait/no-trade read, so there is no contract to fill.")
        return
    _ensure_option_ticket_vars(self)
    fields = ticket_fields_for_option_candidate(candidate)
    self.symbol_var.set(fields["symbol"])
    self.options_symbol_var.set(fields["symbol"])
    if candidate.underlying_price is not None:
        self.options_underlying_price_var.set(_format_number(candidate.underlying_price))
    self.options_strategy_var.set(fields["strategy"])
    self.options_action_var.set(fields["action"])
    self.options_expiration_var.set(fields["expiration"])
    self.options_type_var.set(fields["option_type"])
    self.options_order_type_var.set(fields["order_type"])
    self.options_tif_var.set(fields["time_in_force"])
    self.options_contracts_var.set(fields["contracts"])
    self.options_strike_var.set(fields["strike"])
    self.options_short_strike_var.set(fields["short_strike"])
    self.options_bid_var.set(fields["bid"])
    self.options_ask_var.set(fields["ask"])
    self.options_mark_var.set(fields["mark"])
    self.options_premium_var.set(fields["premium"])
    self.options_credit_var.set(fields["credit"])
    if hasattr(self, "schwab_preview_status_var"):
        self.schwab_preview_status_var.set("Last Schwab preview: research option filled only")
    frame = getattr(self, "schwab_research_options_frame", None)
    if frame is not None:
        frame.status_var.set("Option candidate filled into ticket only. No order was submitted.")  # type: ignore[attr-defined]
    self.schwab_research_selected_contract_symbol = candidate.contract_symbol
    _render_greeks(self)
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is not None:
        _render_scenarios(self, payload)


def _use_model_stock_plan(self: tk.Tk) -> None:
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is None:
        messagebox.showinfo("Run analysis first", "Run analysis first.")
        return
    stock_plan = getattr(self, "schwab_research_stock_plan", None)
    quantity = math.floor(float(getattr(stock_plan, "quantity", 0.0) or 0.0)) if stock_plan is not None else 0
    entry = _to_float(getattr(stock_plan, "entry_price", None)) if stock_plan is not None else None
    if stock_plan is None or quantity <= 0 or entry is None or entry <= 0:
        messagebox.showinfo("No usable model stock plan", "No usable model stock plan.")
        return

    _ensure_stock_ticket_vars(self)
    self.symbol_var.set(payload.symbol)
    self.side_var.set(OrderSide.BUY.value)
    self.order_type_var.set(OrderType.LIMIT.value)
    self.time_in_force_var.set(TimeInForce.DAY.value)
    self.quantity_var.set(str(quantity))
    self.limit_price_var.set(_format_number(entry))
    self.estimated_price_var.set(_format_number(entry))
    stop = _to_float(getattr(stock_plan, "stop_price", None))
    self.stop_price_var.set(_format_optional_number(stop))
    if hasattr(self, "schwab_preview_status_var"):
        self.schwab_preview_status_var.set("Last Schwab preview: model stock plan filled only")
    if hasattr(self, "last_schwab_preview_status"):
        self.last_schwab_preview_status = None
    if hasattr(self, "last_preview"):
        self.last_preview = None

    note = _model_stock_plan_fill_note(payload.symbol, quantity, entry, stop, stock_plan)
    _append_schwab_output_note(self, note)
    frame = getattr(self, "schwab_research_scenarios_frame", None)
    if frame is not None and hasattr(frame, "scenario_note_text"):
        current = frame.scenario_note_text.get("1.0", tk.END).strip() if hasattr(frame.scenario_note_text, "get") else ""  # type: ignore[attr-defined]
        _set_research_text(frame.scenario_note_text, (current + "\n\n" + note).strip())  # type: ignore[attr-defined]
    messagebox.showinfo("Model stock plan filled", "Model stock plan filled into ticket fields only. No order was submitted, previewed, or staged.")


def _model_stock_plan_fill_note(symbol: str, quantity: int, entry: float, stop: float | None, stock_plan: Any) -> str:
    return "\n".join(
        [
            "MODEL STOCK PLAN FILLED ONLY",
            "============================",
            f"Symbol: {symbol}",
            f"Side: Buy",
            f"Order type: LIMIT",
            f"Quantity: {quantity}",
            f"Limit / estimated price: {_money(entry)}",
            f"Stop reference: {_money(stop)}",
            f"Basis: {getattr(stock_plan, 'basis', '')}",
            "",
            "No order was submitted, previewed, or staged. Review the ticket manually before using any Schwab preview or live action.",
        ]
    )


def _append_schwab_output_note(self: tk.Tk, note: str) -> None:
    widget = getattr(self, "schwab_trading_preview_text", None) or getattr(self, "preview_text", None)
    if widget is None:
        return
    try:
        widget.configure(state=tk.NORMAL)
        existing = widget.get("1.0", tk.END).strip()
        if existing:
            widget.insert(tk.END, "\n\n")
        widget.insert(tk.END, note)
        content = widget.get("1.0", tk.END)
        _apply_report_tags(widget, content)
        widget.configure(state=tk.DISABLED)
    except Exception:
        setter = getattr(self, "_set_preview_text", None)
        if callable(setter):
            setter(note)


def _render_option_scenarios_from_top(self: tk.Tk) -> None:
    frame = getattr(self, "schwab_research_scenarios_frame", None)
    if frame is None or not hasattr(frame, "option_scenario_tree"):
        return
    tree = frame.option_scenario_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    payload = getattr(self, "schwab_research_last_payload", None)
    candidates = getattr(self, "schwab_research_option_candidates", []) or []
    selected_candidate = _selected_option_candidate(self)
    candidate = selected_candidate if selected_candidate is not None and selected_candidate.option_type in {"call", "put"} else next((item for item in candidates if item.option_type in {"call", "put"}), None)
    if payload is None or candidate is None:
        tree.insert(
            "",
            tk.END,
            iid="load_chain_row",
            values=("Load chain", "--", "--", "--", "--", "--", "--", "Double-click this row or use Load Chain above to generate option scenarios."),
        )
        return
    stock_plan = getattr(self, "schwab_research_stock_plan", None)
    moves = option_strategy_scenario_moves(candidate, payload.indicators)
    for move, row in zip(moves, combined_current_model_option_scenarios(candidate, payload.context, stock_plan, moves=moves)):
        move_note = option_strategy_scenario_move_note(candidate, payload.indicators, move)
        tag_basis = row.model_combined_pnl if row.model_combined_pnl is not None else row.current_combined_pnl
        tag = "positive" if tag_basis > 0 else "negative" if tag_basis < 0 else ""
        tree.insert(
            "",
            tk.END,
            values=(
                row.move_label,
                _money(row.underlying_price),
                _money(row.current_stock_pnl),
                _money(row.model_stock_pnl),
                _money(row.option_pnl),
                _money(row.current_combined_pnl),
                _money(row.model_combined_pnl),
                _option_scenario_read(row.read, move_note),
            ),
            tags=(tag,) if tag else (),
        )


def _active_stock_scenario_context(self: tk.Tk, payload: _ResearchPayload) -> PortfolioSymbolContext:
    context = getattr(self, "schwab_research_scenario_context", None)
    if isinstance(context, PortfolioSymbolContext) and context.symbol == payload.context.symbol:
        return context
    return payload.context


def _decision_difference_lines(candidate: OptionCandidate | None, context: PortfolioSymbolContext, stock_plan: Any | None = None) -> list[str]:
    lines = _stock_plan_look_lines(context, stock_plan)
    if candidate is None or candidate.option_type not in {"call", "put"}:
        lines.append("No loaded option candidate yet. Load the chain to compare those stock looks against option structures.")
        return lines
    rows = combined_current_model_option_scenarios(candidate, context, stock_plan, moves=(-0.05,))
    if not rows:
        lines.append("Combined option math is unavailable for the selected candidate.")
        return lines
    row = rows[0]
    if row.model_stock_pnl is None or row.model_combined_pnl is None:
        lines.append("Model stock comparison is unavailable, so the option scenario can only show current-position math.")
        return lines
    difference = row.model_combined_pnl - row.model_stock_pnl
    premium = (candidate.midpoint or 0.0) * 100
    if candidate.option_type == "put":
        worth = "This hedge may not be worth the premium." if difference < premium * 0.5 else "This hedge provides visible downside offset in the -5% case."
        lines.append(f"At the model stock look, if {context.symbol} falls -5%, stock-only is {_money(row.model_stock_pnl)}; with {candidate.strategy}, combined is {_money(row.model_combined_pnl)}. Net protection benefit: {_money(difference)}. Trade-off: upfront debit about {_money(premium)}. {worth}")
        return lines
    worth = "This call needs too much move before expiration." if row.option_pnl < 0 else "This call adds upside leverage in the + path, but premium is still the defined risk."
    lines.append(f"At the model stock look, if {context.symbol} falls -5%, stock-only is {_money(row.model_stock_pnl)}; with {candidate.strategy}, combined is {_money(row.model_combined_pnl)}. Option difference: {_money(difference)}. Trade-off: upfront debit about {_money(premium)}. {worth}")
    return lines


def _stock_plan_look_lines(context: PortfolioSymbolContext, stock_plan: Any | None = None) -> list[str]:
    quantity = float(getattr(stock_plan, "quantity", context.quantity) or context.quantity or 0.0)
    entry = float(getattr(stock_plan, "entry_price", context.last_price) or context.last_price or 0.0)
    if quantity <= 0 or entry <= 0:
        return ["No stock scenario size is available yet, so stock-only comparison remains at zero exposure."]
    whole_quantity = max(1, int(quantity))
    look_quantities = sorted({max(1, int(math.floor(whole_quantity * fraction))) for fraction in (0.33, 0.66, 1.0)})
    lines: list[str] = []
    for qty in look_quantities:
        notional = qty * entry
        down_5 = notional * -0.05
        up_5 = notional * 0.05
        label = "Model" if qty == whole_quantity else "Starter"
        lines.append(f"{label} stock look: {qty:g} shares, {_money(notional)} notional; -5% is {_money(down_5)}, +5% is {_money(up_5)}.")
    return lines


def _refresh_earnings_sources(self: tk.Tk) -> None:
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is None:
        messagebox.showinfo("Run analysis first", "Run analysis once before refreshing earnings sources.")
        return
    if payload.security_kind in ETF_SECURITY_KINDS or payload.reporting_profile == REPORTING_PROFILE_FOREIGN_ISSUER:
        _render_earnings_news(self, payload)
        self.schwab_research_status_var.set(f"{payload.symbol} earnings-style tab refreshed at {_now()}")
        return

    symbol = payload.symbol
    self.schwab_research_status_var.set(f"Refreshing earnings sources for {symbol}...")
    _set_research_text(
        self.schwab_research_earnings_text,
        f"Refreshing earnings sources for {symbol}...\n\nChecking earnings calendar, company IR / press releases, recent SEC 8-K filings, and companyfacts.",
    )

    def worker() -> None:
        try:
            earnings_text, fundamentals_text, filings_lines, sec_statuses = _fetch_us_domestic_sec_layers(symbol)
            statuses = _merge_statuses(payload.statuses, sec_statuses)
            decision = build_decision_readout(
                indicators=payload.indicators,
                context=payload.context,
                scenario_rows=payload.scenario_rows,
                earnings_text=earnings_text,
                fundamentals_text=fundamentals_text,
                macro_text=payload.macro_text,
                statuses=statuses,
            )
            trade_evidence_report = build_trade_evidence_report(
                symbol=payload.symbol,
                indicators=payload.indicators,
                context=payload.context,
                decision=decision,
                scenario_rows=payload.scenario_rows,
                earnings_text=earnings_text,
                macro_text=payload.macro_text,
                statuses=statuses,
                quote=payload.quote,
                option_chain_rows=payload.option_chain_rows or [],
                symbol_candles=payload.daily_candles or [],
                command_center_snapshots=payload.command_center_report.snapshots if payload.command_center_report else None,
                market_indicators=payload.market_indicators or {},
                market_candles=payload.market_candles or {},
            )
            updated = replace(
                payload,
                earnings_text=earnings_text,
                fundamentals_text=fundamentals_text,
                filings_lines=filings_lines,
                statuses=statuses,
                decision=decision,
                trade_evidence_report=trade_evidence_report,
            )
        except Exception as exc:
            self.after(0, lambda error=exc: messagebox.showerror("Refresh Earnings failed", str(error)))
            return
        self.after(0, lambda result=updated: _finish_earnings_refresh(self, result))

    threading.Thread(target=worker, daemon=True).start()


def _finish_earnings_refresh(self: tk.Tk, payload: _ResearchPayload) -> None:
    self.schwab_research_last_payload = payload
    _render_at_glance(self, payload)
    _render_overview(self, payload)
    _render_trade_evidence(self, payload)
    _render_earnings_news(self, payload)
    _render_fundamentals(self, payload)
    self.schwab_research_status_var.set(f"{payload.symbol} earnings refreshed at {_now()}")
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is not None:
        _set_research_text(output, _trade_evidence_text(payload) + "\n\n" + _overview_text(payload) + "\n\n" + _source_status_text(payload.statuses))


def _merge_statuses(existing: list[DataSourceStatus], updates: list[DataSourceStatus]) -> list[DataSourceStatus]:
    update_sources = {status.source for status in updates}
    return [status for status in existing if status.source not in update_sources] + updates


def _recalculate_research_scenarios(self: tk.Tk) -> None:
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is None:
        return
    moves = technical_scenario_moves(payload.context, payload.indicators)
    scenario_rows = build_scenario_rows(payload.context, moves)
    trade_evidence_report = build_trade_evidence_report(
        symbol=payload.symbol,
        indicators=payload.indicators,
        context=payload.context,
        decision=payload.decision,
        scenario_rows=scenario_rows,
        earnings_text=payload.earnings_text,
        macro_text=payload.macro_text,
        statuses=payload.statuses,
        quote=payload.quote,
        option_chain_rows=payload.option_chain_rows or [],
        symbol_candles=payload.daily_candles or [],
        command_center_snapshots=payload.command_center_report.snapshots if payload.command_center_report else None,
        market_indicators=payload.market_indicators or {},
        market_candles=payload.market_candles or {},
    )
    updated = replace(payload, scenario_rows=scenario_rows, trade_evidence_report=trade_evidence_report)
    self.schwab_research_last_payload = updated
    _render_trade_evidence(self, updated)
    _render_scenarios(self, updated)
    _render_greeks(self, updated)


def _render_earnings_news(self: tk.Tk, payload: _ResearchPayload) -> None:
    if payload.security_kind in ETF_SECURITY_KINDS:
        _render_etf_documents(self, payload)
        return
    if payload.reporting_profile == REPORTING_PROFILE_FOREIGN_ISSUER:
        _render_foreign_issuer_documents(self, payload)
        return

    frame = self.schwab_research_earnings_frame
    self.schwab_research_earnings_text._readout_title = "Earnings Release Explanation"  # type: ignore[attr-defined]
    _sync_readout_window_title(self.schwab_research_earnings_text)
    decision = payload.decision
    summary = build_earnings_workspace_summary(payload.symbol, payload.earnings_text, payload.fundamentals_text, payload.filings_lines)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [
            decision.earnings_risk,
            _synthetic_badge("Latest Earnings", summary.earnings_card_label, summary.earnings_card_status, summary.earnings_card_why),
            _synthetic_badge("Source Freshness", summary.freshness_label.title(), summary.freshness_status, summary.freshness_verdict),
            _synthetic_badge("Guidance Tone", summary.guidance_tone, _tone_status(summary.guidance_tone), "Read from SEC earnings text when available."),
            _synthetic_badge("Revenue Trend", summary.revenue_trend, _trend_status(summary.revenue_trend), "Interpreted from standardized companyfacts where available."),
            _synthetic_badge("Profitability", summary.profitability_trend, _trend_status(summary.profitability_trend), "Net income/EPS/margin read from loaded facts and snippets."),
        ],
        columns=3,
        card_height=144,
        prominent_height=156,
        prominent_indexes={0, 1},
        adaptive_height=True,
    )
    clear_children(frame.checks)  # type: ignore[attr-defined]
    Checklist(frame.checks, "Plain-English Interpretation", summary.interpretation).grid(row=0, column=0, sticky="ew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.checks, "Risks To Watch", summary.risks).grid(row=0, column=1, sticky="ew")  # type: ignore[attr-defined]
    tree = frame.source_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    for label, date, url in summary.source_links:
        tree.insert("", tk.END, values=(label, date, url or "--"))
    _set_research_text(self.schwab_research_earnings_text, _earnings_popout_text(payload, summary, _earnings_news_text(payload)))


def _render_etf_documents(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_earnings_frame
    snapshot = payload.etf_snapshot or build_etf_research_snapshot(payload.symbol, quote=payload.quote, security_kind=payload.security_kind)
    readout = build_etf_readout(snapshot)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [_badge_from_etf_card(card) for card in readout.document_cards],
        columns=3,
        card_height=136,
        prominent_height=146,
        prominent_indexes={0},
        adaptive_height=True,
    )
    clear_children(frame.checks)  # type: ignore[attr-defined]
    frame.checks.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    Checklist(frame.checks, "ETF Read", readout.interpretation).grid(row=0, column=0, sticky="ew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.checks, "ETF Risks To Watch", readout.risks).grid(row=0, column=1, sticky="ew")  # type: ignore[attr-defined]
    tree = frame.source_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    for label, date, url in readout.source_links:
        tree.insert("", tk.END, values=(label, date, url or "--"))
    _set_research_text(self.schwab_research_earnings_text, _etf_documents_popout_text(payload, snapshot, readout))


def _render_foreign_issuer_documents(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_earnings_frame
    snapshot = payload.foreign_issuer_snapshot or build_foreign_issuer_snapshot(payload.symbol)
    self.schwab_research_earnings_text._readout_title = "Foreign Issuer Results Explanation"  # type: ignore[attr-defined]
    _sync_readout_window_title(self.schwab_research_earnings_text)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [_badge_from_foreign_card(card) for card in foreign_issuer_earnings_cards(snapshot)],
        columns=2,
        card_height=136,
        prominent_height=146,
        prominent_indexes={0, 1},
        adaptive_height=True,
    )
    clear_children(frame.checks)  # type: ignore[attr-defined]
    frame.checks.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    Checklist(frame.checks, "Foreign Issuer Read", foreign_issuer_interpretation(snapshot)).grid(row=0, column=0, sticky="ew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.checks, "Risks To Watch", foreign_issuer_risks(snapshot)).grid(row=0, column=1, sticky="ew")  # type: ignore[attr-defined]
    tree = frame.source_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    for label, date, url in snapshot.source_links:
        tree.insert("", tk.END, values=(label, date, url or "--"))
    _set_research_text(self.schwab_research_earnings_text, _foreign_issuer_documents_popout_text(payload, snapshot))


def _render_fundamentals(self: tk.Tk, payload: _ResearchPayload) -> None:
    if payload.security_kind in ETF_SECURITY_KINDS:
        _render_etf_fundamentals(self, payload)
        return
    if payload.reporting_profile == REPORTING_PROFILE_FOREIGN_ISSUER:
        _render_foreign_issuer_fundamentals(self, payload)
        return

    frame = self.schwab_research_fundamentals_frame
    decision = payload.decision
    verdict = build_fundamental_verdict(payload.fundamentals_text, payload.indicators, decision.macro_backdrop.label)
    structured_metric_cards = build_fundamental_metric_cards(payload.fundamentals_text)
    factor_cards = structured_metric_cards or [decision.growth, decision.profitability, decision.balance_sheet, decision.cash_flow]
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [
            _synthetic_badge("Fundamental Verdict", verdict.verdict, _fundamental_status(verdict.verdict), verdict.investment_read),
            _synthetic_badge("Action Bias", verdict.action_bias, _fundamental_status(verdict.verdict), "Fundamentals translated into portfolio action bias."),
            _synthetic_badge("Confidence", verdict.confidence, "good" if verdict.confidence == "High" else "mixed" if verdict.confidence == "Medium" else "info", "Based on data quantity, consistency, and recency language."),
            decision.valuation,
            *factor_cards,
        ],
        columns=3,
        card_height=150,
        prominent_height=164,
        prominent_indexes={0, 1},
        adaptive_height=True,
    )
    clear_children(frame.checks)  # type: ignore[attr-defined]
    frame.checks.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    Checklist(frame.checks, "Investment vs Trade", [verdict.investment_read, verdict.trade_read, verdict.combined_read]).grid(row=0, column=0, sticky="ew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.checks, "What Would Change This View", verdict.what_changes).grid(row=0, column=1, sticky="ew")  # type: ignore[attr-defined]
    details = "\n".join(
        [
            "Fundamental recommendation:",
            f"- Verdict: {verdict.verdict}.",
            f"- Action bias: {verdict.action_bias}.",
            f"- Confidence: {verdict.confidence}.",
            f"- {verdict.investment_read}",
            f"- {verdict.trade_read}",
            f"- {verdict.combined_read}",
            "",
            "Key standardized data below:",
            payload.fundamentals_text,
        ]
    )
    _set_research_text(self.schwab_research_fundamentals_text, _fundamentals_popout_text(payload, verdict, details))


def _render_foreign_issuer_fundamentals(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_fundamentals_frame
    snapshot = payload.foreign_issuer_snapshot or build_foreign_issuer_snapshot(payload.symbol)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [_badge_from_foreign_card(card) for card in foreign_issuer_fundamental_cards(snapshot)],
        columns=2,
        card_height=136,
        prominent_height=146,
        prominent_indexes={0, 1},
        adaptive_height=True,
    )
    clear_children(frame.checks)  # type: ignore[attr-defined]
    frame.checks.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    Checklist(frame.checks, "Investment vs Trade", foreign_issuer_interpretation(snapshot)).grid(row=0, column=0, sticky="ew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.checks, "What To Verify Next", [
        "Open the official IR result package and annual report before treating companyfacts as complete.",
        "Review 6-K interim updates for current quarter results, guidance, orders, and management commentary.",
        "Review 20-F / 40-F annual filings for risk factors, segments, currency, R&D, capex, and concentration.",
    ]).grid(row=0, column=1, sticky="ew")  # type: ignore[attr-defined]
    _set_research_text(self.schwab_research_fundamentals_text, _foreign_issuer_fundamentals_popout_text(payload, snapshot))


def _render_etf_fundamentals(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_fundamentals_frame
    snapshot = payload.etf_snapshot or build_etf_research_snapshot(payload.symbol, quote=payload.quote, security_kind=payload.security_kind)
    readout = build_etf_readout(snapshot)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [_badge_from_etf_card(card) for card in readout.structure_cards],
        columns=3,
        card_height=136,
        prominent_height=146,
        prominent_indexes={0, 7},
        adaptive_height=True,
    )
    clear_children(frame.checks)  # type: ignore[attr-defined]
    frame.checks.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    Checklist(frame.checks, "ETF Structure Read", readout.interpretation).grid(row=0, column=0, sticky="ew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(
        frame.checks,
        "What To Verify Next",
        [
            "Open the issuer fund page and factsheet for objective, fee, AUM, yield, holdings, and sector/country exposures.",
            "Open the prospectus and SAI for principal strategy, fees, derivatives/leverage rules, securities lending, and creation/redemption mechanics.",
            "Open shareholder reports or N-PORT for holdings, concentration, derivatives exposure, and portfolio discussion.",
        ],
    ).grid(row=0, column=1, sticky="ew")  # type: ignore[attr-defined]
    _set_research_text(self.schwab_research_fundamentals_text, _etf_fundamentals_popout_text(payload, snapshot, readout))


def _render_macro(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_macro_frame
    decision = payload.decision
    readouts = build_macro_metric_cards(payload.macro_snapshot)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [
            _synthetic_badge(readout.group, readout.simple_read, readout.status, readout.interpretation)
            for readout in readouts
        ],
        columns=3,
        card_height=136,
        prominent_height=144,
        adaptive_height=True,
    )
    clear_children(frame.why)  # type: ignore[attr-defined]
    why_text = macro_why_it_matters(payload.symbol, None, decision.macro_backdrop.label)
    ttk.Label(frame.why, text=why_text, style="Subtle.TLabel", wraplength=980, justify=tk.LEFT).grid(row=0, column=0, sticky="ew", padx=10, pady=8)  # type: ignore[attr-defined]
    tree = frame.metric_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    for readout in readouts:
        tree.insert("", tk.END, values=(readout.group, readout.metric, readout.latest_value, readout.prior_value, readout.change, readout.period, readout.source, readout.freshness, readout.simple_read))
    clear_children(frame.interpretation)  # type: ignore[attr-defined]
    Checklist(frame.interpretation, "Plain-English Interpretations", [readout.interpretation for readout in readouts[:5]]).grid(row=0, column=0, sticky="ew", padx=(0, 8))  # type: ignore[attr-defined]
    Checklist(frame.interpretation, "Good / Bad / Watch", [*decision.macro_good, *decision.macro_bad, *decision.macro_watch]).grid(row=0, column=1, sticky="ew")  # type: ignore[attr-defined]
    _set_research_text(self.schwab_research_macro_text, _macro_popout_text(payload, readouts))


def _earnings_news_text(payload: _ResearchPayload) -> str:
    lines = [
        payload.earnings_text,
        "",
        "Company news / official filings:",
    ]
    if payload.filings_lines:
        lines.extend(f"- {line}" for line in payload.filings_lines)
    else:
        lines.append("- No SEC filing headlines were available.")
    lines.extend(["", "News provider note:", "- Same-day events check the earnings calendar first, then company IR / press releases, then SEC 8-K exhibits. Market/news sources remain fallback context only."])
    return "\n".join(lines)


def _earnings_popout_text(payload: _ResearchPayload, summary: Any, original_text: str) -> str:
    return original_text


def _etf_documents_popout_text(payload: _ResearchPayload, snapshot: ETFResearchSnapshot, readout: Any) -> str:
    return _format_beginner_readout(
        title=f"ETF Documents / Updates - {payload.symbol}",
        what_this_means=(
            "This is ETF research mode. Company earnings, revenue, EPS, margins, and guidance do not apply; "
            "the useful sources are issuer documents, holdings, fund filings, distributions, and index methodology."
        ),
        key_points=[
            f"Fund type: {snapshot.fund_type}.",
            f"Issuer: {snapshot.issuer}.",
            f"Strategy: {snapshot.strategy}.",
            "ETF companyfacts are not applicable. Using ETF/fund document sources instead.",
            *readout.interpretation,
            *readout.risks,
        ],
        why_it_matters="ETF risk comes from what the fund owns, how it is built, how liquid it is, what it costs, and whether distributions or structure add hidden risk.",
        original_text=payload.earnings_text,
        original_title="Original / detailed ETF document readout",
    )


def _etf_fundamentals_popout_text(payload: _ResearchPayload, snapshot: ETFResearchSnapshot, readout: Any) -> str:
    return _format_beginner_readout(
        title=f"ETF Structure / Holdings - {payload.symbol}",
        what_this_means=(
            "This replaces operating-company fundamentals with ETF structure and holdings checks: expense ratio, AUM, "
            "top holdings, concentration, sector/theme exposure, distribution profile, liquidity, and index/strategy."
        ),
        key_points=[
            f"Expense ratio: {snapshot.expense_ratio}.",
            f"AUM / assets: {snapshot.aum}.",
            f"Top 10 weight: {snapshot.top_10_weight}.",
            f"Liquidity: {snapshot.liquidity}.",
            f"Index / strategy: {snapshot.index_name or snapshot.strategy}.",
            *readout.interpretation,
            *readout.risks,
        ],
        why_it_matters="An ETF can look diversified by ticker count but still be concentrated by top holdings, sector, theme, duration, commodity, crypto, leverage, or option structure.",
        original_text=payload.fundamentals_text,
        original_title="Original / detailed ETF structure readout",
    )


def _foreign_issuer_documents_popout_text(payload: _ResearchPayload, snapshot: ForeignIssuerSnapshot) -> str:
    return format_foreign_issuer_results_explanation(snapshot)


def _foreign_issuer_fundamentals_popout_text(payload: _ResearchPayload, snapshot: ForeignIssuerSnapshot) -> str:
    return _format_beginner_readout(
        title=f"Foreign Issuer Fundamentals - {payload.symbol}",
        what_this_means=(
            "This replaces a pure companyfacts read with a foreign issuer fundamentals stack: annual reports, 20-F / 40-F, "
            "6-K interim reports, official IR result packages, and supplemental XBRL when available."
        ),
        key_points=[
            f"Revenue trend: {snapshot.revenue_trend}.",
            f"Net income / margin: {snapshot.profitability_trend}.",
            f"Reporting basis: {snapshot.reporting_basis_label}.",
            f"Source freshness: {snapshot.source_freshness}.",
            snapshot.companyfacts_note,
            *foreign_issuer_interpretation(snapshot),
        ],
        why_it_matters="The fundamentals tab should not look broken just because a foreign issuer uses 6-K, 20-F, IFRS, local annual reports, or official IR result packages.",
        original_text=format_foreign_issuer_fundamentals_text(snapshot),
        original_title="Original / detailed foreign issuer fundamentals readout",
    )


def _fundamentals_popout_text(payload: _ResearchPayload, verdict: Any, original_text: str) -> str:
    return _format_beginner_readout(
        title=f"Fundamentals Explanation - {payload.symbol}",
        what_this_means=(
            "This translates standardized SEC fundamental data into an investment read, a trading read, "
            "and a combined view that also considers the current technical and macro setup."
        ),
        key_points=[
            f"Verdict: {verdict.verdict}.",
            f"Action bias: {verdict.action_bias}.",
            f"Confidence: {verdict.confidence}.",
            verdict.investment_read,
            verdict.trade_read,
            verdict.combined_read,
            *verdict.what_changes,
        ],
        why_it_matters="Fundamentals can support owning a name, but the trade timing still depends on price confirmation, risk lines, and the macro backdrop.",
        original_text=original_text,
        original_title="Original / detailed fundamentals readout",
    )


def _macro_popout_text(payload: _ResearchPayload, readouts: list[Any]) -> str:
    decision = payload.decision
    key_points = [
        f"Macro backdrop: {decision.macro_backdrop.label} ({decision.macro_backdrop.why})",
        *[readout.interpretation for readout in readouts],
        *decision.macro_good,
        *decision.macro_bad,
        *decision.macro_watch,
    ]
    return _format_beginner_readout(
        title=f"Macro Snapshot Explanation - {payload.symbol}",
        what_this_means=(
            "This is the official macro context layer. It keeps CPI, jobs, rates, energy, and other macro feed items "
            "separate from company-specific data while showing how the backdrop affects this symbol's risk read."
        ),
        key_points=key_points,
        why_it_matters="Macro data can turn an otherwise good company setup into a wait, hedge, or smaller-size trade when rates, inflation, or growth data are working against the position.",
        original_text=payload.macro_text,
        original_title="Original / detailed macro snapshot",
    )


def _synthetic_badge(title: str, label: str, status: str, why: str, score: float = 0.0) -> BadgeReadout:
    return BadgeReadout(title=title, label=label, status=status, score=score, why=why)


def _thesis_read_badge(decision: ResearchDecisionReadout) -> BadgeReadout:
    thesis = decision.thesis
    status = _thesis_status(thesis.recommendation)
    label = _thesis_short_label(thesis)
    why = f"{thesis.trade_judgment} {thesis.why} Confidence: {thesis.confidence}."
    return _synthetic_badge("Thesis Read", label, status, why, decision.technical_score)


def _preferred_vehicle_badge(decision: ResearchDecisionReadout) -> BadgeReadout:
    thesis = decision.thesis
    warning = f" Warning: {thesis.warnings[0]}" if thesis.warnings else ""
    return _synthetic_badge(
        "Preferred Vehicle",
        thesis.preferred_vehicle,
        _vehicle_status(thesis.preferred_vehicle, thesis.recommendation),
        f"{thesis.recommendation}. {thesis.invalidation}{warning}",
    )


def _thesis_short_label(thesis: Any) -> str:
    if thesis.setup_type == "pullback":
        return "Pullback Candidate"
    if thesis.setup_type == "breakdown":
        return "Breakdown Risk"
    if thesis.setup_type == "hedge":
        return "Hedge"
    if thesis.setup_type == "no-trade":
        return "Wait"
    if thesis.recommendation in {"Avoid", "Trim"}:
        return thesis.recommendation
    return thesis.recommendation


def _thesis_status(recommendation: str) -> str:
    if recommendation in {"Accumulate Pullback", "Add Carefully"}:
        return "good"
    if recommendation in {"Avoid", "Trim"}:
        return "bad"
    return "mixed"


def _vehicle_status(vehicle: str, recommendation: str) -> str:
    if recommendation in {"Avoid", "Trim"} or vehicle in {"Cash", "No Trade"}:
        return "mixed" if recommendation not in {"Avoid", "Trim"} else "bad"
    if vehicle in {"Shares", "Starter Shares"}:
        return "good"
    return "mixed"


def _badge_from_etf_card(card: ETFCard) -> BadgeReadout:
    return _synthetic_badge(card.title, card.label, card.status, card.why)


def _badge_from_foreign_card(card: tuple[str, str, str, str]) -> BadgeReadout:
    title, label, status, why = card
    return _synthetic_badge(title, label, status, why)


def _rsi_badge(indicators: AdvancedIndicatorSnapshot) -> BadgeReadout:
    if indicators.rsi_14 is None:
        return _synthetic_badge("RSI", "Unknown", "info", "RSI is unavailable.")
    if indicators.rsi_14 >= 70:
        return _synthetic_badge("RSI", "Overbought", "bad", f"RSI is {indicators.rsi_14:.1f}.")
    if indicators.rsi_14 <= 30:
        return _synthetic_badge("RSI", "Oversold", "mixed", f"RSI is {indicators.rsi_14:.1f}.")
    if indicators.rsi_14 >= 55:
        return _synthetic_badge("RSI", "Firm", "good", f"RSI is {indicators.rsi_14:.1f}.")
    if indicators.rsi_14 <= 45:
        return _synthetic_badge("RSI", "Soft", "bad", f"RSI is {indicators.rsi_14:.1f}.")
    return _synthetic_badge("RSI", "Neutral", "mixed", f"RSI is {indicators.rsi_14:.1f}.")


def _tone_from_text(text: str) -> str:
    lower = text.lower()
    positive = sum(1 for term in ("increase", "increased", "growth", "reaffirm", "beat", "higher") if term in lower)
    negative = sum(1 for term in ("decrease", "decline", "lower", "miss", "pressure", "risk") if term in lower)
    if positive > negative:
        return "Positive"
    if negative > positive:
        return "Negative"
    if "unavailable" in lower or not lower.strip():
        return "Unknown"
    return "Mixed"


def _tone_status(text: str) -> str:
    tone = text if text in {"Positive", "Negative", "Unknown", "Unavailable", "Mixed"} else _tone_from_text(text)
    if tone == "Positive":
        return "good"
    if tone == "Negative":
        return "bad"
    if tone in {"Unknown", "Unavailable"}:
        return "info"
    return "mixed"


def _trend_status(text: str) -> str:
    if text == "Improving":
        return "good"
    if text == "Weak":
        return "bad"
    if text == "Unavailable":
        return "info"
    return "mixed"


def _fundamental_status(verdict: str) -> str:
    if verdict in {"Strong", "Good"}:
        return "good"
    if verdict in {"Weak", "Avoid"}:
        return "bad"
    if verdict == "Unknown":
        return "info"
    return "mixed"


def _short_text_bullets(text: str, *, limit: int) -> list[str]:
    raw_lines = []
    for line in text.splitlines():
        stripped = line.strip(" -\t")
        if len(stripped) >= 28 and not set(stripped) <= {"="}:
            raw_lines.append(stripped)
    if not raw_lines:
        return ["No clean source bullet is available yet."]
    bullets = []
    for line in raw_lines[:limit]:
        bullets.append(line[:165] + ("..." if len(line) > 165 else ""))
    return bullets


def _risk_text_bullets(text: str, filings_lines: list[str], *, limit: int) -> list[str]:
    lower_lines = [line.strip(" -\t") for line in text.splitlines() if any(term in line.lower() for term in ("risk", "pressure", "decline", "decrease", "uncertain", "loss"))]
    if not lower_lines:
        lower_lines = filings_lines[:limit]
    if not lower_lines:
        return ["No obvious earnings/news risk bullet was found in the loaded sources."]
    return [line[:165] + ("..." if len(line) > 165 else "") for line in lower_lines[:limit]]


def _macro_metric_label(text: str, *, hot_terms: tuple[str, ...], cool_terms: tuple[str, ...]) -> str:
    lower = text.lower()
    if any(term in lower for term in hot_terms):
        return "Hot"
    if any(term in lower for term in cool_terms):
        return "Cool"
    return "Normal/Mixed"


def _macro_metric_status(text: str, *, hot_terms: tuple[str, ...], cool_terms: tuple[str, ...]) -> str:
    label = _macro_metric_label(text, hot_terms=hot_terms, cool_terms=cool_terms)
    if label == "Hot":
        return "bad"
    if label == "Cool":
        return "good"
    return "mixed"


def _source_status_text(statuses: list[DataSourceStatus]) -> str:
    return "Data freshness\n\n" + "\n".join(_source_status_lines(statuses))


def _source_status_lines(statuses: list[DataSourceStatus]) -> list[str]:
    return [f"- {status.source}: {status.status}; fetched {status.fetched_at}; {status.message}" for status in statuses]


def _show_research_error(self: tk.Tk, symbol: str, error: Exception) -> None:
    self.schwab_research_status_var.set(f"{symbol} research failed")
    _set_research_text(self.schwab_research_overview_text, f"Schwab research failed for {symbol}\n\n{error}")


def _refresh_research_holdings(self: tk.Tk) -> None:
    tree = getattr(self, "schwab_research_holdings_tree", None)
    if tree is None:
        return
    portfolio = self.broker.get_portfolio()
    total_value = max(portfolio.total_value, 0.01)
    self.schwab_research_total_var.set(f"Total {_money(portfolio.total_value)}")
    self.schwab_research_cash_var.set(f"Cash {_money(portfolio.cash)}")
    self.schwab_research_positions_var.set(f"Positions {_money(portfolio.positions_value)}")
    self.schwab_research_pnl_var.set(f"Unrealized {_money(portfolio.unrealized_profit_loss)}")
    for row_id in tree.get_children():
        tree.delete(row_id)
    index = 0
    for symbol, position in sorted(portfolio.positions.items(), key=lambda item: -abs(item[1].market_value)):
        asset_type = str(getattr(position, "asset_type", "") or "Equity")
        if _is_hyperliquid(asset_type, symbol):
            continue
        tag = "positive" if position.unrealized_profit_loss > 0 else "negative" if position.unrealized_profit_loss < 0 else ""
        tree.insert(
            "",
            tk.END,
            iid=f"research_holding_{index}",
            values=(
                position.symbol,
                asset_type,
                f"{position.quantity:g}",
                _money(position.average_cost),
                _money(position.last_price),
                _money(position.market_value),
                f"{position.market_value / total_value:.1%}",
                _money(position.unrealized_profit_loss),
            ),
            tags=(tag,) if tag else (),
        )
        index += 1


def _select_research_holding(self: tk.Tk, event: tk.Event) -> None:
    tree = self.schwab_research_holdings_tree
    row_id = tree.identify_row(event.y)
    if not row_id:
        return
    values = tree.item(row_id, "values")
    symbol = selected_holding_symbol_from_values(values)
    if not symbol:
        return
    self.schwab_research_symbol_var.set(symbol)
    self.symbol_var.set(symbol)
    if hasattr(self, "options_symbol_var"):
        self.options_symbol_var.set(symbol)
    self.schwab_research_status_var.set(f"Selected {symbol}. Run analysis when ready.")


def selected_holding_symbol_from_values(values: Any) -> str:
    if not values:
        return ""
    try:
        return str(values[0]).strip().upper()
    except Exception:
        return ""


def _initial_research_symbol(self: tk.Tk) -> str:
    symbol = getattr(self, "symbol_var", tk.StringVar(value="")).get().strip().upper()
    if symbol:
        return symbol
    try:
        portfolio = self.broker.get_portfolio()
        for key, position in sorted(portfolio.positions.items(), key=lambda item: -abs(item[1].market_value)):
            asset_type = str(getattr(position, "asset_type", "") or "Equity")
            if not _is_hyperliquid(asset_type, key):
                return position.symbol
    except Exception:
        pass
    return ""


def _quote_for_symbol(payload: Any, symbol: str) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    direct = payload.get(symbol.upper()) or payload.get(symbol.lower())
    if isinstance(direct, dict):
        return direct
    for key, value in payload.items():
        if str(key).upper() == symbol.upper() and isinstance(value, dict):
            return value
    return None


def _last_price_from_quote(quote: dict[str, Any] | None) -> float | None:
    if not quote:
        return None
    quote_body = quote.get("quote") if isinstance(quote.get("quote"), dict) else quote
    for key in ("lastPrice", "mark", "regularMarketLastPrice", "closePrice", "bidPrice", "askPrice"):
        value = _to_float(quote_body.get(key))
        if value is not None and value > 0:
            return value
    return None


def _set_research_text(widget: tk.Text, content: str) -> None:
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, content)
    _apply_report_tags(widget, content)
    widget.configure(state=tk.DISABLED)
    if getattr(widget, "_readout_popout_text", None) is not None:
        _refresh_readout_popout(widget)


def _risk_budget_card_text(risk_budget: Any) -> str:
    factors = list(getattr(risk_budget, "factors", ()) or ())
    if not factors:
        return "Generated from portfolio value, cash, exposure, technicals, macro, and event risk."
    return "Portfolio/cash/technical base adjusted by " + "; ".join(factors[:3]) + "."


def _stock_plan_card_text(stock_plan: Any) -> str:
    quantity = float(getattr(stock_plan, "quantity", 0.0) or 0.0)
    if quantity <= 0:
        return getattr(stock_plan, "basis", "No stock scenario position could be generated.")
    return (
        f"{_money(getattr(stock_plan, 'notional', None))} notional; "
        f"entry {_money(getattr(stock_plan, 'entry_price', None))}; "
        f"stop {_money(getattr(stock_plan, 'stop_price', None))}."
    )


def _is_hyperliquid(asset_type: str, symbol: str) -> bool:
    clean_type = asset_type.lower()
    clean_symbol = symbol.upper()
    return clean_type == "spot" or clean_type.startswith("perp") or clean_symbol.endswith("-SPOT") or "-PERP" in clean_symbol


def _float_from_var(var: Any) -> float | None:
    try:
        return _to_float(var.get()) if var is not None else None
    except Exception:
        return None


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(str(value).replace("$", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _money(value: float | None) -> str:
    if value is None:
        return "--"
    prefix = "-$" if value < 0 else "$"
    return f"{prefix}{abs(value):,.2f}"


def _number(value: float | None) -> str:
    return "--" if value is None else f"{value:,.2f}"


def _percent(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2%}"


def _shares(value: float | None) -> str:
    return "--" if value is None else f"{value:,.2f} shares"


def _format_number(value: float, *, digits: int = 2) -> str:
    formatted = f"{value:.{digits}f}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _format_optional_number(value: float | None) -> str:
    return "" if value is None else _format_number(value)


def _candidate_status(label: str) -> str:
    if label == "Good":
        return "good"
    if label == "Avoid":
        return "bad"
    if label == "Speculative":
        return "mixed"
    return "info"


def _ensure_option_ticket_vars(self: tk.Tk) -> None:
    defaults = {
        "options_symbol_var": "",
        "options_strategy_var": "",
        "options_action_var": "",
        "options_expiration_var": "",
        "options_type_var": "",
        "options_order_type_var": "LIMIT",
        "options_tif_var": "Day",
        "options_underlying_price_var": "",
        "options_quantity_var": "100",
        "options_contracts_var": "1",
        "options_strike_var": "",
        "options_short_strike_var": "",
        "options_bid_var": "",
        "options_ask_var": "",
        "options_mark_var": "",
        "options_premium_var": "",
        "options_credit_var": "0",
    }
    for name, value in defaults.items():
        if not hasattr(self, name):
            setattr(self, name, tk.StringVar(value=value))


def _ensure_stock_ticket_vars(self: tk.Tk) -> None:
    defaults = {
        "symbol_var": "",
        "side_var": OrderSide.BUY.value,
        "order_type_var": OrderType.LIMIT.value,
        "time_in_force_var": TimeInForce.DAY.value,
        "quantity_var": "1",
        "limit_price_var": "",
        "estimated_price_var": "",
        "stop_price_var": "",
        "schwab_preview_status_var": "",
    }
    for name, value in defaults.items():
        if not hasattr(self, name):
            setattr(self, name, _new_ticket_string_var(self, value))


def _technical_ticket_from_research_ui(self: tk.Tk, portfolio: Any) -> TechnicalTicket:
    side = "buy"
    try:
        side = str(self.side_var.get()).strip().lower() or "buy"
    except Exception:
        pass
    entry = _float_from_var(getattr(self, "limit_price_var", None)) or _float_from_var(getattr(self, "estimated_price_var", None))
    portfolio_value = None
    try:
        value = float(getattr(portfolio, "total_value", 0.0) or 0.0)
        portfolio_value = value if value > 0 else None
    except Exception:
        portfolio_value = None
    return TechnicalTicket(
        side=side,
        quantity=_float_from_var(getattr(self, "quantity_var", None)),
        entry_price=entry,
        stop_price=_float_from_var(getattr(self, "stop_price_var", None)),
        portfolio_value=portfolio_value,
    )


def _new_ticket_string_var(self: tk.Tk, value: str) -> tk.StringVar:
    master = self if isinstance(self, tk.Misc) else getattr(self, "_ticket_tcl_master", None)
    if master is None:
        master = tk.Tcl()
        try:
            setattr(self, "_ticket_tcl_master", master)
        except Exception:
            pass
    return tk.StringVar(master=master, value=value)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
