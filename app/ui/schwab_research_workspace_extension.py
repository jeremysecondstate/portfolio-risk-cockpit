from __future__ import annotations

import math
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timezone
from tkinter import messagebox, ttk
from typing import Any, Type

from app.analytics.earnings_release import analyze_earnings_release, format_earnings_release_digest
from app.analytics.fundamental_analysis import analyze_company_facts, format_fundamental_analysis
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
    build_earnings_workspace_summary,
    build_fundamental_verdict,
    build_macro_metric_cards,
    build_risk_plan,
    build_technical_narrative,
    combined_option_scenarios,
    inflation_read_from_metrics,
    macro_why_it_matters,
    option_timeline_text,
    selected_candidate_detail,
    suggest_option_candidates,
    ticket_fields_for_option_candidate,
)
from app.analytics.stock_research import (
    AdvancedIndicatorSnapshot,
    DataSourceStatus,
    PortfolioSymbolContext,
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
from app.analytics.technical_analysis import candles_from_price_history
from app.data.sec_edgar import SecEdgarClient, normalize_ticker
from app.macro.analysis import format_macro_report
from app.macro.models import MacroSnapshot
from app.macro.releases import fetch_macro_release_snapshot
from app.ui.research_widgets import Checklist, ScenarioImpactBars, ScoreMeter, ScrollableFrame, clear_children, freshness_badges, labeled_value_grid, metric_grid
from app.ui.schwab_output_popout_extension import _apply_report_tags, _open_external_url

REPORT_FORMS = ("10-K", "10-Q", "8-K")


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


def install_schwab_research_workspace_extension(app_cls: Type[tk.Tk]) -> None:
    app_cls.show_technical_analysis = _open_schwab_research_workspace  # type: ignore[method-assign]


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
    window.geometry("1280x820")
    window.minsize(1020, 640)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)
    self.schwab_research_window = window

    selected_symbol = _initial_research_symbol(self)
    self.schwab_research_symbol_var = tk.StringVar(value=selected_symbol)
    self.schwab_research_max_risk_var = tk.StringVar(value="Run analysis")
    self.schwab_research_scenario_basis_var = tk.StringVar(value="Scenario moves will be generated from technical levels.")
    self.schwab_research_status_var = tk.StringVar(value="Choose a holding or enter a symbol, then run analysis.")

    header = ttk.Frame(window, padding=(12, 10), style="Panel.TFrame")
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(header, text="Schwab Research + Risk Workspace", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(header, textvariable=self.schwab_research_status_var, style="Subtle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
    ttk.Button(header, text="Refresh Holdings", command=lambda app=self: _refresh_research_holdings(app)).grid(row=0, column=1, rowspan=2, sticky="e", padx=(8, 0))

    body = tk.PanedWindow(window, orient=tk.HORIZONTAL, bg="#0f172a", bd=0, sashwidth=8, sashpad=4, showhandle=True)
    body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

    left = ttk.Frame(body, style="Panel.TFrame", padding=10)
    right = ttk.Frame(body, style="Panel.TFrame", padding=10)
    body.add(left, minsize=330, stretch="never")
    body.add(right, minsize=680, stretch="always")
    window.after_idle(lambda: body.sash_place(0, 370, 0))

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
    tree = ttk.Treeview(holdings, columns=columns, show="headings", height=16, selectmode="browse")
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
    tree.bind("<ButtonRelease-1>", lambda event, app=self: _select_research_holding(app, event), add="+")
    tree.bind("<Double-1>", lambda _event, app=self: _run_research_analysis(app), add="+")
    self.schwab_research_holdings_tree = tree


def _build_research_right_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(2, weight=1)

    top = ttk.LabelFrame(parent, text="Selected Symbol", style="Card.TLabelframe")
    top.grid(row=0, column=0, sticky="ew")
    top.columnconfigure((0, 1, 2, 3), weight=1)
    self.schwab_research_quote_var = tk.StringVar(value="Quote --")
    self.schwab_research_held_var = tk.StringVar(value="Held --")
    self.schwab_research_weight_var = tk.StringVar(value="Weight --")
    self.schwab_research_risk_var = tk.StringVar(value="Risk --")
    for index, var in enumerate((self.schwab_research_quote_var, self.schwab_research_held_var, self.schwab_research_weight_var, self.schwab_research_risk_var)):
        ttk.Label(top, textvariable=var, style="Chip.TLabel").grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0))

    glance = ttk.LabelFrame(parent, text="At a Glance", style="Card.TLabelframe")
    glance.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    glance.columnconfigure(0, weight=1)
    self.schwab_research_glance_cards = ttk.Frame(glance, style="Panel.TFrame")
    self.schwab_research_glance_cards.grid(row=0, column=0, sticky="ew")
    self.schwab_research_top_strip = ttk.Frame(glance, style="Panel.TFrame")
    self.schwab_research_top_strip.grid(row=1, column=0, sticky="ew", pady=(6, 0))
    meters = ttk.Frame(glance, style="Panel.TFrame")
    meters.grid(row=2, column=0, sticky="ew", pady=(6, 0))
    meters.columnconfigure((0, 1), weight=1)
    self.schwab_research_bull_bear_meter = ScoreMeter(meters)
    self.schwab_research_bull_bear_meter.grid(row=0, column=0, sticky="ew", padx=(0, 8))
    self.schwab_research_risk_meter = ScoreMeter(meters)
    self.schwab_research_risk_meter.grid(row=0, column=1, sticky="ew")

    notebook = ttk.Notebook(parent)
    notebook.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
    self.schwab_research_tabs = notebook

    self.schwab_research_overview_frame = _overview_tab(notebook)
    self.schwab_research_overview_text = self.schwab_research_overview_frame.detail_text  # type: ignore[attr-defined]
    self.schwab_research_technicals_frame = _technicals_tab(notebook)
    self.schwab_research_scenarios_frame = _scenarios_tab(self, notebook)
    self.schwab_research_options_frame = _options_strategy_tab(self, notebook)
    self.schwab_research_earnings_frame = _earnings_tab(notebook)
    self.schwab_research_earnings_text = self.schwab_research_earnings_frame.detail_text  # type: ignore[attr-defined]
    self.schwab_research_fundamentals_frame = _section_summary_tab(notebook, "Fundamentals")
    self.schwab_research_fundamentals_text = self.schwab_research_fundamentals_frame.detail_text  # type: ignore[attr-defined]
    self.schwab_research_macro_frame = _macro_tab(notebook)
    self.schwab_research_macro_text = self.schwab_research_macro_frame.detail_text  # type: ignore[attr-defined]


def _scrollable_tab(notebook: ttk.Notebook, title: str) -> ttk.Frame:
    outer = ScrollableFrame(notebook, padding=10)
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
    tree = ttk.Treeview(tree_box, columns=("group", "metric", "latest", "prior", "change", "period", "source", "fresh", "read"), show="headings", height=7)
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
    frame.metric_tree = tree  # type: ignore[attr-defined]
    frame.interpretation = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.interpretation.grid(row=3, column=0, sticky="ew", pady=(8, 0))
    frame.interpretation.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    raw = ttk.LabelFrame(frame, text="Raw Details", style="Card.TLabelframe")
    raw.grid(row=4, column=0, sticky="ew", pady=(8, 0))
    raw.columnconfigure(0, weight=1)
    frame.detail_text = _readout_launcher(raw, title="Macro Snapshot Explanation", button_text="Open Macro Snapshot Explanation", row=0, pady=(0, 0))  # type: ignore[attr-defined]
    return frame


def _earnings_tab(notebook: ttk.Notebook) -> ttk.Frame:
    frame = _scrollable_tab(notebook, "Earnings / News")
    frame.columnconfigure(0, weight=1)
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=0, column=0, sticky="ew")
    frame.checks = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.checks.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    frame.checks.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    source_box = ttk.LabelFrame(frame, text="Source Links", style="Card.TLabelframe")
    source_box.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    source_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(source_box, columns=("source", "date", "url"), show="headings", height=5)
    for column, label, width in (("source", "Source", 250), ("date", "Date", 110), ("url", "URL", 520)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.W, stretch=column == "url")
    tree.grid(row=0, column=0, sticky="ew")
    y_scroll = ttk.Scrollbar(source_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    tree.bind("<Double-1>", lambda event, source=tree: _open_source_tree_url(source, event), add="+")
    frame.source_tree = tree  # type: ignore[attr-defined]
    frame.detail_text = _readout_launcher(frame, title="Earnings Release Explanation", button_text="Open Earnings Release Explanation", row=3)  # type: ignore[attr-defined]
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
    window.geometry("960x720")
    window.minsize(640, 420)
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
        font=("Segoe UI", 10),
        padx=18,
        pady=16,
        relief=tk.FLAT,
        borderwidth=0,
        background="#f8fafc",
        foreground="#111827",
        insertbackground="#111827",
        selectbackground="#bfdbfe",
        spacing1=3,
        spacing2=1,
        spacing3=6,
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
    frame.chart_readout = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.chart_readout.grid(row=2, column=0, sticky="ew", pady=(10, 0))  # type: ignore[attr-defined]
    frame.chart_readout.columnconfigure((0, 1), weight=1)  # type: ignore[attr-defined]
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=3, column=0, sticky="ew", pady=(10, 0))
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("metric", "value", "read"), show="headings", height=7)
    for column, label, width in (("metric", "Metric", 160), ("value", "Value", 140), ("read", "Readout", 360)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.W if column != "value" else tk.E, stretch=True)
    tree.grid(row=0, column=0, sticky="ew")
    y_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    text = _readout_launcher(frame, title="Technical Readout", button_text="Open Technical Readout", row=4, pady=(10, 0))
    frame.indicator_tree = tree  # type: ignore[attr-defined]
    frame.technical_notes_text = text  # type: ignore[attr-defined]
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
    frame.cards = ttk.Frame(frame, style="Panel.TFrame")  # type: ignore[attr-defined]
    frame.cards.grid(row=1, column=0, sticky="ew", pady=(0, 8))
    planner_box = ttk.LabelFrame(frame, text="Move Planner", style="Card.TLabelframe")
    planner_box.grid(row=2, column=0, sticky="ew", pady=(0, 8))
    planner_box.columnconfigure(0, weight=1)
    move_tree = ttk.Treeview(planner_box, columns=("move", "makes_sense", "protects", "gives_up", "effect"), show="headings", height=7)
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
    frame.move_planner_tree = move_tree  # type: ignore[attr-defined]
    frame.impact_bars = ScenarioImpactBars(frame, height=170)  # type: ignore[attr-defined]
    frame.impact_bars.grid(row=3, column=0, sticky="ew", pady=(0, 8))  # type: ignore[attr-defined]
    tree_box = ttk.Frame(frame, style="Panel.TFrame")
    tree_box.grid(row=4, column=0, sticky="ew")
    tree_box.columnconfigure(0, weight=1)
    tree = ttk.Treeview(tree_box, columns=("scenario", "price", "pnl", "impact", "portfolio"), show="headings", height=7)
    for column, label, width in (
        ("scenario", "Scenario", 100),
        ("price", "Symbol Price", 130),
        ("pnl", "Position P&L", 130),
        ("impact", "Portfolio Impact", 140),
        ("portfolio", "New Portfolio Value", 160),
    ):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.E if column != "scenario" else tk.W, stretch=True)
    tree.tag_configure("positive", foreground="#047857")
    tree.tag_configure("negative", foreground="#b91c1c")
    tree.grid(row=0, column=0, sticky="ew")
    y_scroll = ttk.Scrollbar(tree_box, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=y_scroll.set)
    note = _readout_launcher(frame, title="Risk Scenario Explanation", button_text="Open Risk Scenario Explanation", row=5, pady=(10, 0))
    frame.scenario_tree = tree  # type: ignore[attr-defined]
    frame.scenario_note_text = note  # type: ignore[attr-defined]
    option_box = ttk.LabelFrame(frame, text="Options Scenario Based On Suggested Contract", style="Card.TLabelframe")
    option_box.grid(row=6, column=0, sticky="ew", pady=(10, 0))
    option_box.columnconfigure(0, weight=1)
    option_controls = ttk.Frame(option_box, style="Panel.TFrame")
    option_controls.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    ttk.Button(option_controls, text="Run Option Scenario From Top Candidate", command=lambda app=self: _render_option_scenarios_from_top(app)).pack(side=tk.LEFT)
    ttk.Button(option_controls, text="Load Chain", command=lambda app=self: _load_chain_from_research_tab(app)).pack(side=tk.LEFT, padx=(8, 0))
    option_tree = ttk.Treeview(option_box, columns=("move", "stock", "option", "combined", "impact", "read"), show="headings", height=6)
    for column, label, width in (
        ("move", "Underlying Move", 130),
        ("stock", "Stock P&L", 120),
        ("option", "Option P&L", 120),
        ("combined", "Combined P&L", 130),
        ("impact", "Portfolio Impact", 140),
        ("read", "Read", 180),
    ):
        option_tree.heading(column, text=label)
        option_tree.column(column, width=width, anchor=tk.E if column not in {"move", "read"} else tk.W, stretch=True)
    option_tree.tag_configure("positive", foreground="#047857")
    option_tree.tag_configure("negative", foreground="#b91c1c")
    option_tree.grid(row=1, column=0, sticky="ew")
    option_scroll = ttk.Scrollbar(option_box, orient=tk.VERTICAL, command=option_tree.yview)
    option_scroll.grid(row=1, column=1, sticky="ns")
    option_tree.configure(yscrollcommand=option_scroll.set)
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
    tree = ttk.Treeview(tree_box, columns=("group", "strategy", "expiration", "strike", "type", "mid", "max_loss", "breakeven", "score", "confidence"), show="headings", height=6)
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
    frame.candidate_tree = tree  # type: ignore[attr-defined]
    frame.timeline = ttk.LabelFrame(frame, text="Selected Candidate Timeline", style="Card.TLabelframe")  # type: ignore[attr-defined]
    frame.timeline.grid(row=4, column=0, sticky="ew", pady=(8, 0))  # type: ignore[attr-defined]
    frame.timeline.columnconfigure(0, weight=1)  # type: ignore[attr-defined]
    frame.timeline_var = tk.StringVar(value="Select a candidate.")  # type: ignore[attr-defined]
    ttk.Label(frame.timeline, textvariable=frame.timeline_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=10, pady=8)  # type: ignore[attr-defined]
    scenario_box = ttk.LabelFrame(frame, text="Selected Candidate Combined Scenario", style="Card.TLabelframe")
    scenario_box.grid(row=5, column=0, sticky="ew", pady=(8, 0))
    scenario_box.columnconfigure(0, weight=1)
    frame.candidate_bars = ScenarioImpactBars(scenario_box, height=104)  # type: ignore[attr-defined]
    frame.candidate_bars.grid(row=0, column=0, sticky="ew", pady=(0, 6))  # type: ignore[attr-defined]
    scenario_tree = ttk.Treeview(scenario_box, columns=("move", "price", "stock", "value", "option", "combined", "impact", "read"), show="headings", height=7)
    for column, label, width in (
        ("move", "Move", 75),
        ("price", "Stock Price", 105),
        ("stock", "Stock P&L", 105),
        ("value", "Option Value", 110),
        ("option", "Option P&L", 105),
        ("combined", "Combined", 110),
        ("impact", "Portfolio", 95),
        ("read", "Plain-English Read", 280),
    ):
        scenario_tree.heading(column, text=label)
        scenario_tree.column(column, width=width, anchor=tk.E if column not in {"move", "read"} else tk.W, stretch=column == "read")
    scenario_tree.tag_configure("positive", foreground="#047857")
    scenario_tree.tag_configure("negative", foreground="#b91c1c")
    scenario_tree.grid(row=1, column=0, sticky="ew")
    scenario_scroll = ttk.Scrollbar(scenario_box, orient=tk.VERTICAL, command=scenario_tree.yview)
    scenario_scroll.grid(row=1, column=1, sticky="ns")
    scenario_tree.configure(yscrollcommand=scenario_scroll.set)
    frame.candidate_scenario_tree = scenario_tree  # type: ignore[attr-defined]
    help_box = ttk.LabelFrame(frame, text="How To Read This", style="Card.TLabelframe")
    help_box.grid(row=6, column=0, sticky="ew", pady=(8, 0))
    ttk.Label(
        help_box,
        text="Call: right to benefit from upside. Put: downside insurance/speculation. Premium: upfront option price. Strike: exercise reference price. Intrinsic value: value at expiration from stock versus strike. Expiration-style estimate: simple payoff math, not live option pricing.",
        style="Subtle.TLabel",
        wraplength=1120,
        justify=tk.LEFT,
    ).grid(row=0, column=0, sticky="ew", padx=10, pady=8)
    frame.detail_text = _readout_launcher(frame, title="Options Strategy Explanation", button_text="Open Options Strategy Explanation", row=7)  # type: ignore[attr-defined]
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
    _set_research_text(self.schwab_research_overview_text, f"Running Schwab research for {symbol}...\n\nFetching quote/history, SEC filings, fundamentals, earnings layer, and macro context.")
    self.schwab_research_status_var.set(f"Running analysis for {symbol}...")

    def worker() -> None:
        try:
            payload = _build_research_payload(session, portfolio, symbol)
        except Exception as exc:
            self.after(0, lambda error=exc: _show_research_error(self, symbol, error))
            return
        self.after(0, lambda result=payload: _render_research_payload(self, result))

    threading.Thread(target=worker, daemon=True).start()


def _build_research_payload(session: Any, portfolio, symbol: str) -> _ResearchPayload:
    statuses: list[DataSourceStatus] = []
    quote: dict[str, Any] | None = None
    daily_payload: dict[str, Any] | None = None

    quote, quote_status = _fetch_quote(session, symbol)
    statuses.append(quote_status)

    daily_payload, history_status = _fetch_daily_history(session, symbol)
    statuses.append(history_status)
    candles = candles_from_price_history(daily_payload) if daily_payload else []
    indicators = calculate_advanced_indicators(symbol, candles)

    fallback_price = _last_price_from_quote(quote) or indicators.latest_close
    context = build_portfolio_symbol_context(portfolio, symbol, fallback_price)
    moves = technical_scenario_moves(context, indicators)
    scenario_rows = build_scenario_rows(context, moves)

    earnings_text, fundamentals_text, filings_lines, sec_statuses = _fetch_sec_layers(symbol)
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

    return _ResearchPayload(symbol, quote, indicators, context, scenario_rows, earnings_text, fundamentals_text, filings_lines, macro_text, statuses, decision, macro_snapshot)


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


def _fetch_sec_layers(symbol: str) -> tuple[str, str, list[str], list[DataSourceStatus]]:
    statuses: list[DataSourceStatus] = []
    earnings_text = "Earnings / News\n\nOfficial SEC earnings-release layer unavailable."
    fundamentals_text = "Fundamentals\n\nSEC companyfacts unavailable."
    filings_lines: list[str] = []
    try:
        client = SecEdgarClient(timeout_seconds=12)
        filings = client.recent_filings(symbol, forms=REPORT_FORMS, limit=10)
        release = client.latest_earnings_release(symbol)
        digest = analyze_earnings_release(release)
        earnings_text = format_earnings_release_digest(digest)
        filings_lines = [f"{filing.form} filed {filing.filing_date} period {filing.report_date or '--'}: {filing.filing_url}" for filing in filings[:8]]
        statuses.append(DataSourceStatus("SEC filings/earnings", "fresh/cache", _now(), f"{len(filings)} recent filings scanned."))
    except Exception as exc:
        statuses.append(DataSourceStatus("SEC filings/earnings", "error", _now(), str(exc)))
        filings_lines = [f"SEC filings unavailable/error: {exc}"]

    try:
        client = SecEdgarClient(timeout_seconds=12)
        company, payload = client.get_companyfacts(symbol)
        fundamentals_text = format_fundamental_analysis(analyze_company_facts(company, payload))
        statuses.append(DataSourceStatus("SEC companyfacts", "fresh/cache", _now(), "Standardized XBRL fundamentals loaded."))
    except Exception as exc:
        statuses.append(DataSourceStatus("SEC companyfacts", "error", _now(), str(exc)))
        fundamentals_text = f"Fundamentals\n\nSEC companyfacts unavailable/error: {exc}\n\nFor ETFs, issuer holdings, expense ratio, AUM, and sector exposure remain a future provider hook."
    return earnings_text, fundamentals_text, filings_lines, statuses


def _render_research_payload(self: tk.Tk, payload: _ResearchPayload) -> None:
    self.schwab_research_last_payload = payload
    context = payload.context
    quote_price = context.last_price
    self.schwab_research_quote_var.set(f"{payload.symbol}: {_money(quote_price)}")
    self.schwab_research_held_var.set("Held" if context.is_held else "Not held")
    self.schwab_research_weight_var.set(f"Weight {context.portfolio_weight:.2%}")
    self.schwab_research_risk_var.set(f"{payload.decision.overall.label}; risk {payload.decision.risk_level.label}")
    self.schwab_research_status_var.set(f"{payload.symbol} research updated at {_now()}")

    _render_at_glance(self, payload)
    _render_overview(self, payload)
    _render_technicals(self, payload)
    _render_scenarios(self, payload)
    _render_options_strategy(self)
    _render_earnings_news(self, payload)
    _render_fundamentals(self, payload)
    _render_macro(self, payload)

    output = getattr(self, "schwab_trading_preview_text", None)
    if output is not None:
        _set_research_text(output, _overview_text(payload) + "\n\n" + _source_status_text(payload.statuses))


def _overview_text(payload: _ResearchPayload) -> str:
    context = payload.context
    indicators = payload.indicators
    decision = payload.decision
    lines = [
        f"Schwab Research Workspace - {payload.symbol}",
        "",
        "At a glance:",
        f"- Overall: {decision.overall.label} ({decision.overall.why})",
        f"- Risk: {decision.risk_level.label} ({decision.risk_level.why})",
        f"- Action bias: {decision.action_bias.label} ({decision.action_bias.why})",
        "",
        "Plain-English summary:",
        *[f"- {line}" for line in decision.summary],
        "",
        "Symbol / portfolio readout:",
        f"- Current quote: {_money(context.last_price)}",
        f"- Held: {'yes' if context.is_held else 'no'}",
        f"- Position: {context.quantity:g} shares, value {_money(context.market_value)}, weight {context.portfolio_weight:.2%}",
        f"- Unrealized P&L: {_money(context.unrealized_pnl)}; day P&L {_money(context.day_pnl)}",
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
    return _format_beginner_readout(
        title=f"Overview Explanation - {payload.symbol}",
        what_this_means=(
            "This is the complete symbol readout. It combines the Schwab quote and portfolio position with "
            "the technical setup, current risk level, action bias, and data freshness."
        ),
        key_points=[
            f"Overall read: {decision.overall.label} ({decision.overall.why})",
            f"Risk level: {decision.risk_level.label} ({decision.risk_level.why})",
            f"Action bias: {decision.action_bias.label} ({decision.action_bias.why})",
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
    metric_grid(
        self.schwab_research_glance_cards,
        [
            decision.overall,
            decision.risk_level,
            decision.macro_backdrop,
            decision.action_bias,
        ],
        columns=4,
        prominent_indexes={0, 3},
    )
    labeled_value_grid(
        self.schwab_research_top_strip,
        {
            "Best thing": decision.top_things[0],
            "Biggest risk": decision.top_things[1],
            "Key trigger": decision.top_things[2],
        },
        columns=3,
    )
    self.schwab_research_bull_bear_meter.set_score(decision.technical_score, mode="direction", label=f"Bullishness: {direction_strength_label(decision.technical_score)} ({decision.technical_score:.0f})")
    self.schwab_research_risk_meter.set_score(decision.risk_score, mode="risk", label=f"Risk Heat: {risk_heat_label(decision.risk_score)} ({decision.risk_score:.0f}/100)")


def _render_overview(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_overview_frame
    decision = payload.decision
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [
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
        prominent_indexes={0, 3},
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


def _render_technicals(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_technicals_frame
    indicators = payload.indicators
    decision = payload.decision
    narrative = build_technical_narrative(indicators, payload.context, decision.macro_backdrop.label)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [
            decision.trend,
            decision.momentum,
            decision.volatility,
            _rsi_badge(indicators),
            _synthetic_badge("Indicator Agreement", narrative.indicator_agreement, narrative.agreement_status, narrative.agreement_explanation),
        ],
        columns=5,
        card_height=128,
        prominent_height=128,
    )
    frame.bull_meter.set_score(decision.technical_score, mode="direction", label=f"Bullishness: {direction_strength_label(decision.technical_score)} ({decision.technical_score:.0f})")  # type: ignore[attr-defined]
    frame.momentum_meter.set_score(decision.momentum_score, mode="direction", label=f"Momentum: {direction_strength_label(decision.momentum_score)} ({decision.momentum_score:.0f})")  # type: ignore[attr-defined]
    frame.risk_meter.set_score(decision.risk_score, mode="risk", label=f"Risk Heat: {risk_heat_label(decision.risk_score)} ({decision.risk_score:.0f})")  # type: ignore[attr-defined]
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
    for row_id in tree.get_children():
        tree.delete(row_id)
    rows = [
        ("SMA 20", indicators.sma_20, "Short trend average"),
        ("SMA 50", indicators.sma_50, "Intermediate trend average"),
        ("SMA 100", indicators.sma_100, "Intermediate/long trend"),
        ("SMA 200", indicators.sma_200, "Long trend reference"),
        ("EMA 12", indicators.ema_12, "Fast momentum average"),
        ("EMA 26", indicators.ema_26, "Slow momentum average"),
        ("MACD", indicators.macd, TERM_HELPERS["MACD"]),
        ("MACD signal", indicators.macd_signal, "9-period MACD signal; compare it with MACD for momentum turns."),
        ("RSI 14", indicators.rsi_14, TERM_HELPERS["RSI"]),
        ("Bollinger upper", indicators.bollinger_upper, TERM_HELPERS["Bollinger Bands"]),
        ("Bollinger middle", indicators.bollinger_middle, "20 SMA band middle."),
        ("Bollinger lower", indicators.bollinger_lower, TERM_HELPERS["Bollinger Bands"]),
        ("ATR 14", indicators.atr_14, TERM_HELPERS["ATR"]),
        ("Volume avg 20", indicators.volume_average_20, "Average daily volume"),
        ("Swing high", indicators.swing_high, TERM_HELPERS["Swing high / swing low"]),
        ("Swing low", indicators.swing_low, TERM_HELPERS["Swing high / swing low"]),
    ]
    for metric, value, read in rows:
        tree.insert("", tk.END, values=(metric, _number(value), read))
    for label, value in indicators.fibonacci_levels.items():
        tree.insert("", tk.END, values=(f"Fib {label}", _money(value), TERM_HELPERS["Fibonacci retracement"]))
    notes = "\n".join(
        [
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
    scenario_rows = build_scenario_rows(scenario_context, technical_scenario_moves(payload.context, payload.indicators))
    candidates = getattr(self, "schwab_research_option_candidates", []) or []
    if not candidates:
        rows_map = getattr(self, "schwab_option_chain_rows", {}) or {}
        chain_rows = [row for row in rows_map.values() if isinstance(row, dict)]
        if chain_rows:
            candidates = suggest_option_candidates(chain_rows, payload.indicators, payload.context, macro_label=payload.decision.macro_backdrop.label, earnings_text=payload.earnings_text)
            setattr(self, "schwab_research_option_candidates", candidates)
    top_candidate = next((item for item in candidates if item.option_type in {"call", "put"}), None)
    fundamental_verdict = build_fundamental_verdict(payload.fundamentals_text, payload.indicators, payload.decision.macro_backdrop.label)
    risk_plan = build_risk_plan(payload.indicators, payload.context, payload.decision.macro_backdrop.label, fundamental_verdict.verdict, top_candidate, max_risk)
    negative_rows = [row for row in scenario_rows if row.position_pnl < 0]
    positive_rows = [row for row in scenario_rows if row.position_pnl > 0]
    worst = min(negative_rows or scenario_rows, key=lambda row: row.position_pnl, default=None)
    best = max(positive_rows or scenario_rows, key=lambda row: row.position_pnl, default=None)
    cards = [
        _synthetic_badge("Recommended Move", risk_plan.recommendation, risk_plan.status, risk_plan.reason),
        _synthetic_badge("Current Exposure", _money(payload.context.market_value), "mixed" if payload.context.is_held else "info", f"{payload.context.quantity:g} shares; {payload.context.portfolio_weight:.2%} of portfolio."),
        _synthetic_badge("Stock Scenario Position", _shares(stock_plan.quantity), "info", _stock_plan_card_text(stock_plan)),
        _synthetic_badge("Generated Risk Budget", _money(max_risk), "info", _risk_budget_card_text(risk_budget)),
        _synthetic_badge("Paired Option", risk_plan.paired_option, "info", "Best loaded option candidate for this risk plan."),
    ]
    if worst is not None:
        cards.append(_synthetic_badge("Downside Pain", _money(worst.position_pnl), "bad" if worst.position_pnl < 0 else "info", f"{worst.scenario} move, {worst.portfolio_pnl_impact:+.2%} portfolio impact."))
    if best is not None:
        cards.append(_synthetic_badge("Upside Reward", _money(best.position_pnl), "good" if best.position_pnl > 0 else "info", f"{best.scenario} move, {best.portfolio_pnl_impact:+.2%} portfolio impact."))
    cards.extend([payload.decision.position_impact, payload.decision.risk_level])
    metric_grid(frame.cards, cards, columns=4, card_height=128, prominent_height=128)  # type: ignore[attr-defined]
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
    for row in scenario_rows:
        tag = "positive" if row.position_pnl > 0 else "negative" if row.position_pnl < 0 else ""
        tree.insert("", tk.END, values=(row.scenario, _money(row.symbol_price), _money(row.position_pnl), f"{row.portfolio_pnl_impact:+.2%}", _money(row.new_portfolio_value)), tags=(tag,) if tag else ())
    stop = _float_from_var(getattr(self, "stop_price_var", None))
    target = _float_from_var(getattr(self, "options_target_price_var", None)) or _float_from_var(getattr(self, "limit_price_var", None))
    size = suggested_position_size(entry_price=payload.context.last_price, stop_price=stop, max_risk_dollars=max_risk)
    decision_difference_lines = _decision_difference_lines(top_candidate, scenario_context, stock_plan)
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
        "- Stock-only rows show direct share P&L. Combined rows below use expiration-style option payoff, not live option pricing.",
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
    return _format_beginner_readout(
        title=f"Risk Scenario Explanation - {payload.symbol}",
        what_this_means=(
            "This section explains the suggested risk move, what would confirm it, what would invalidate it, "
            "and how the generated stock scenario size was calculated."
        ),
        key_points=[
            f"Recommended move: {risk_plan.recommendation}. {risk_plan.reason}",
            risk_plan.confirmation,
            risk_plan.risk_line,
            *decision_difference_lines,
        ],
        why_it_matters="The scenario table shows possible P&L paths, but this readout explains how to use those paths for sizing, hedging, waiting, or rejecting the setup.",
        original_text=original_text,
    )


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
    candidates = suggest_option_candidates(chain_rows, payload.indicators, payload.context, macro_label=payload.decision.macro_backdrop.label, earnings_text=payload.earnings_text)
    self.schwab_research_option_candidates = candidates
    if not candidates:
        frame.status_var.set("No usable option candidates found in the loaded chain.")  # type: ignore[attr-defined]
        _set_research_text(frame.detail_text, _basic_popout_text("Options Strategy Explanation", "The loaded chain did not include usable bid/ask/mark data for calls or puts."))  # type: ignore[attr-defined]
        return
    top = candidates[0]
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [
            _synthetic_badge("Top Suggestion", top.strategy, _candidate_status(top.confidence), top.why),
            _synthetic_badge("Entry", _money(top.midpoint), "info", "Estimated entry uses bid/ask midpoint when available."),
            _synthetic_badge("Breakeven", _money(top.breakeven), "info", "Expiration-style breakeven for a simple long option."),
            _synthetic_badge("Risk Read", top.confidence, _candidate_status(top.confidence), top.goes_wrong_if),
        ],
        columns=4,
        prominent_indexes={0},
    )
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
    frame.status_var.set(f"{len(candidates)} candidates generated from loaded {payload.symbol} chain. Select one and click Use This Option to fill fields only.")  # type: ignore[attr-defined]
    _show_selected_option_candidate(self)
    _render_scenarios(self, payload)


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
    _render_options_strategy(self)


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
    frame.timeline_var.set(option_timeline_text(candidate, earnings_text))  # type: ignore[attr-defined]
    scenario_tree = frame.candidate_scenario_tree  # type: ignore[attr-defined]
    for row_id in scenario_tree.get_children():
        scenario_tree.delete(row_id)
    if context is not None:
        scenario_rows = combined_option_scenarios(candidate, context, moves=(-0.10, -0.05, -0.03, -0.02, 0.0, 0.02, 0.03, 0.05, 0.10))
        frame.candidate_bars.set_rows(_normalized_candidate_bar_rows(scenario_rows))  # type: ignore[attr-defined]
        for row in scenario_rows:
            tag = "positive" if row.combined_pnl > 0 else "negative" if row.combined_pnl < 0 else ""
            scenario_tree.insert(
                "",
                tk.END,
                values=(row.move_label, _money(row.underlying_price), _money(row.stock_pnl), _money(row.option_value), _money(row.option_pnl), _money(row.combined_pnl), f"{row.portfolio_impact:+.2%}", row.read),
                tags=(tag,) if tag else (),
            )
        lines = selected_candidate_detail(candidate, context, earnings_text)
    else:
        frame.candidate_bars.set_rows([])  # type: ignore[attr-defined]
        lines = [f"{candidate.group}: {candidate.strategy}", "Run analysis to see combined stock + option scenarios."]
    lines.extend(["", "Use This Option fills the existing options ticket only. It does not submit, preview, or stage an order."])
    _set_research_text(frame.detail_text, _options_strategy_popout_text(payload, candidate, context, "\n".join(lines)))  # type: ignore[attr-defined]


def _basic_popout_text(title: str, original_text: str) -> str:
    return _format_beginner_readout(
        title=title,
        what_this_means="This readout will update after the required data is available.",
        key_points=[line for line in original_text.splitlines() if line.strip()],
        why_it_matters="The main tab stays compact while keeping the full generated explanation available when there is enough data to show it.",
        original_text=original_text,
    )


def _options_strategy_popout_text(payload: _ResearchPayload | None, candidate: OptionCandidate, context: PortfolioSymbolContext | None, original_text: str) -> str:
    symbol = payload.symbol if payload is not None else candidate.underlying
    cost = (candidate.midpoint or 0.0) * 100
    key_points = [
        f"Candidate: {candidate.group}: {candidate.strategy}",
        f"Contract: {candidate.option_type.upper()} expiring {candidate.expiration}; strike {_money(candidate.strike)}; DTE {candidate.dte if candidate.dte is not None else '--'}.",
        f"Bid/ask/mid: {_money(candidate.bid)} / {_money(candidate.ask)} / {_money(candidate.midpoint)}; one-contract estimate {_money(cost)}.",
        f"Works if: {candidate.works_if}",
        f"Goes wrong if: {candidate.goes_wrong_if}",
        f"Position interaction: {candidate.relation_to_position}",
        f"Score: {candidate.score:.0f}/100. {candidate.score_reason}",
    ]
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


def _render_option_scenarios_from_top(self: tk.Tk) -> None:
    frame = getattr(self, "schwab_research_scenarios_frame", None)
    if frame is None or not hasattr(frame, "option_scenario_tree"):
        return
    tree = frame.option_scenario_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    payload = getattr(self, "schwab_research_last_payload", None)
    candidates = getattr(self, "schwab_research_option_candidates", []) or []
    candidate = next((item for item in candidates if item.option_type in {"call", "put"}), None)
    if payload is None or candidate is None:
        tree.insert("", tk.END, iid="load_chain_row", values=("Load chain", "--", "--", "--", "--", "Double-click this row or use Load Chain above to generate option scenarios."))
        return
    context = _active_stock_scenario_context(self, payload)
    for row in combined_option_scenarios(candidate, context):
        tag = "positive" if row.combined_pnl > 0 else "negative" if row.combined_pnl < 0 else ""
        tree.insert("", tk.END, values=(row.move_label, _money(row.stock_pnl), _money(row.option_pnl), _money(row.combined_pnl), f"{row.portfolio_impact:+.2%}", f"{row.read}; expiration-style estimate"), tags=(tag,) if tag else ())


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
    rows = combined_option_scenarios(candidate, context, moves=(-0.05,))
    if not rows:
        lines.append("Combined option math is unavailable for the selected candidate.")
        return lines
    row = rows[0]
    difference = row.combined_pnl - row.stock_pnl
    premium = (candidate.midpoint or 0.0) * 100
    if candidate.option_type == "put":
        worth = "This hedge may not be worth the premium." if difference < premium * 0.5 else "This hedge provides visible downside offset in the -5% case."
        lines.append(f"At the model stock look, if {context.symbol} falls -5%, stock-only is {_money(row.stock_pnl)}; with {candidate.strategy}, combined is {_money(row.combined_pnl)}. Net protection benefit: {_money(difference)}. Trade-off: upfront debit about {_money(premium)}. {worth}")
        return lines
    worth = "This call needs too much move before expiration." if row.option_pnl < 0 else "This call adds upside leverage in the + path, but premium is still the defined risk."
    lines.append(f"At the model stock look, if {context.symbol} falls -5%, stock-only is {_money(row.stock_pnl)}; with {candidate.strategy}, combined is {_money(row.combined_pnl)}. Option difference: {_money(difference)}. Trade-off: upfront debit about {_money(premium)}. {worth}")
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


def _recalculate_research_scenarios(self: tk.Tk) -> None:
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is None:
        return
    moves = technical_scenario_moves(payload.context, payload.indicators)
    updated = _ResearchPayload(
        payload.symbol,
        payload.quote,
        payload.indicators,
        payload.context,
        build_scenario_rows(payload.context, moves),
        payload.earnings_text,
        payload.fundamentals_text,
        payload.filings_lines,
        payload.macro_text,
        payload.statuses,
        payload.decision,
        payload.macro_snapshot,
    )
    self.schwab_research_last_payload = updated
    _render_scenarios(self, updated)


def _render_earnings_news(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_earnings_frame
    decision = payload.decision
    summary = build_earnings_workspace_summary(payload.symbol, payload.earnings_text, payload.fundamentals_text, payload.filings_lines)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [
            decision.earnings_risk,
            _synthetic_badge("Latest Earnings", "SEC Scan", "info", summary.snapshot["Latest earnings release"]),
            _synthetic_badge("Guidance Tone", summary.guidance_tone, _tone_status(summary.guidance_tone), "Read from SEC earnings text when available."),
            _synthetic_badge("Revenue Trend", summary.revenue_trend, _trend_status(summary.revenue_trend), "Interpreted from standardized companyfacts where available."),
            _synthetic_badge("Profitability", summary.profitability_trend, _trend_status(summary.profitability_trend), "Net income/EPS/margin read from loaded facts and snippets."),
        ],
        columns=5,
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


def _render_fundamentals(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_fundamentals_frame
    decision = payload.decision
    verdict = build_fundamental_verdict(payload.fundamentals_text, payload.indicators, decision.macro_backdrop.label)
    metric_grid(
        frame.cards,  # type: ignore[attr-defined]
        [
            _synthetic_badge("Fundamental Verdict", verdict.verdict, _fundamental_status(verdict.verdict), verdict.investment_read),
            _synthetic_badge("Action Bias", verdict.action_bias, _fundamental_status(verdict.verdict), "Fundamentals translated into portfolio action bias."),
            _synthetic_badge("Confidence", verdict.confidence, "good" if verdict.confidence == "High" else "mixed" if verdict.confidence == "Medium" else "info", "Based on data quantity, consistency, and recency language."),
            decision.valuation,
            decision.growth,
            decision.profitability,
            decision.balance_sheet,
            decision.cash_flow,
        ],
        columns=4,
        prominent_indexes={0, 1},
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
    lines.extend(["", "News provider note:", "- First version uses official SEC filings and earnings exhibits as the source-labeled company news layer. Investor-relations and broader news feeds can plug into this tab later."])
    return "\n".join(lines)


def _earnings_popout_text(payload: _ResearchPayload, summary: Any, original_text: str) -> str:
    return _format_beginner_readout(
        title=f"Fast Earnings Release Layer - {payload.symbol}",
        what_this_means=(
            "This is a quick official-filings layer for the latest earnings/news context. It uses SEC filings, "
            "earnings exhibits, and standardized companyfacts when available."
        ),
        key_points=[
            f"Latest earnings release: {summary.snapshot['Latest earnings release']}",
            f"Latest 10-Q / 10-K: {summary.snapshot['Latest 10-Q / 10-K']}",
            f"Guidance tone: {summary.guidance_tone}.",
            f"Revenue trend: {summary.revenue_trend}.",
            f"Profitability: {summary.profitability_trend}.",
            *summary.interpretation,
            *summary.risks,
        ],
        why_it_matters="Earnings and filing language can change risk quickly, especially around guidance, backlog, margins, or event risk before option expiration.",
        original_text=original_text,
        original_title="Original / detailed earnings and source readout",
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


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
