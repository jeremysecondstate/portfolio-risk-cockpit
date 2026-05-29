from __future__ import annotations

import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime, timezone
from tkinter import messagebox, ttk
from typing import Any, Type

from app.analytics.earnings_release import analyze_earnings_release, format_earnings_release_digest
from app.analytics.fundamental_analysis import analyze_company_facts, format_fundamental_analysis
from app.analytics.stock_research import (
    AdvancedIndicatorSnapshot,
    DataSourceStatus,
    PortfolioSymbolContext,
    build_portfolio_symbol_context,
    build_scenario_rows,
    calculate_advanced_indicators,
    distance_to_price,
    load_cached_price_history,
    save_cached_price_history,
    suggested_position_size,
)
from app.analytics.technical_analysis import candles_from_price_history
from app.data.sec_edgar import SecEdgarClient, normalize_ticker
from app.macro.releases import build_macro_report
from app.ui.schwab_output_popout_extension import _apply_report_tags

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
    window.geometry("1220x780")
    window.minsize(940, 580)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)
    self.schwab_research_window = window

    selected_symbol = _initial_research_symbol(self)
    self.schwab_research_symbol_var = tk.StringVar(value=selected_symbol)
    self.schwab_research_custom_move_var = tk.StringVar(value="3")
    self.schwab_research_max_risk_var = tk.StringVar(value="500")
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
    body.add(left, minsize=360, stretch="never")
    body.add(right, minsize=620, stretch="always")
    window.after_idle(lambda: body.sash_place(0, 390, 0))

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
    parent.rowconfigure(1, weight=1)

    top = ttk.LabelFrame(parent, text="Selected Symbol", style="Card.TLabelframe")
    top.grid(row=0, column=0, sticky="ew")
    top.columnconfigure((0, 1, 2, 3), weight=1)
    self.schwab_research_quote_var = tk.StringVar(value="Quote --")
    self.schwab_research_held_var = tk.StringVar(value="Held --")
    self.schwab_research_weight_var = tk.StringVar(value="Weight --")
    self.schwab_research_risk_var = tk.StringVar(value="Risk --")
    for index, var in enumerate((self.schwab_research_quote_var, self.schwab_research_held_var, self.schwab_research_weight_var, self.schwab_research_risk_var)):
        ttk.Label(top, textvariable=var, style="Chip.TLabel").grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 6, 0))

    notebook = ttk.Notebook(parent)
    notebook.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
    self.schwab_research_tabs = notebook

    self.schwab_research_overview_text = _report_tab(notebook, "Overview")
    self.schwab_research_technicals_frame = _technicals_tab(notebook)
    self.schwab_research_scenarios_frame = _scenarios_tab(self, notebook)
    self.schwab_research_earnings_text = _report_tab(notebook, "Earnings / News")
    self.schwab_research_fundamentals_text = _report_tab(notebook, "Fundamentals")
    self.schwab_research_macro_text = _report_tab(notebook, "Macro Context")


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
    frame = ttk.Frame(notebook, style="Panel.TFrame", padding=10)
    frame.rowconfigure(1, weight=1)
    frame.columnconfigure(0, weight=1)
    tree = ttk.Treeview(frame, columns=("metric", "value", "read"), show="headings", height=12)
    for column, label, width in (("metric", "Metric", 160), ("value", "Value", 140), ("read", "Readout", 360)):
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=tk.W if column != "value" else tk.E, stretch=True)
    tree.grid(row=0, column=0, sticky="ew")
    text = tk.Text(frame, height=8, wrap=tk.WORD, font=("Segoe UI", 10), padx=14, pady=10, relief=tk.FLAT, borderwidth=0, background="#f8fafc")
    text.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
    frame.indicator_tree = tree  # type: ignore[attr-defined]
    frame.technical_notes_text = text  # type: ignore[attr-defined]
    notebook.add(frame, text="Technicals")
    return frame


def _scenarios_tab(self: tk.Tk, notebook: ttk.Notebook) -> ttk.Frame:
    frame = ttk.Frame(notebook, style="Panel.TFrame", padding=10)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(1, weight=1)
    controls = ttk.Frame(frame, style="Panel.TFrame")
    controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    ttk.Label(controls, text="Custom move %", style="Subtle.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Entry(controls, textvariable=self.schwab_research_custom_move_var, width=8).grid(row=0, column=1, sticky="w", padx=(6, 18))
    ttk.Label(controls, text="Max risk $", style="Subtle.TLabel").grid(row=0, column=2, sticky="w")
    ttk.Entry(controls, textvariable=self.schwab_research_max_risk_var, width=10).grid(row=0, column=3, sticky="w", padx=(6, 18))
    ttk.Button(controls, text="Recalculate", command=lambda app=self: _recalculate_research_scenarios(app)).grid(row=0, column=4, sticky="w")
    tree = ttk.Treeview(frame, columns=("scenario", "price", "pnl", "impact", "portfolio"), show="headings", height=10)
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
    tree.grid(row=1, column=0, sticky="nsew")
    note = tk.Text(frame, height=7, wrap=tk.WORD, font=("Segoe UI", 10), padx=14, pady=10, relief=tk.FLAT, borderwidth=0, background="#f8fafc")
    note.grid(row=2, column=0, sticky="ew", pady=(10, 0))
    frame.scenario_tree = tree  # type: ignore[attr-defined]
    frame.scenario_note_text = note  # type: ignore[attr-defined]
    notebook.add(frame, text="Risk Scenarios")
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
    moves = _scenario_moves(self)
    _set_research_text(self.schwab_research_overview_text, f"Running Schwab research for {symbol}...\n\nFetching quote/history, SEC filings, fundamentals, earnings layer, and macro context.")
    self.schwab_research_status_var.set(f"Running analysis for {symbol}...")

    def worker() -> None:
        try:
            payload = _build_research_payload(session, portfolio, symbol, moves)
        except Exception as exc:
            self.after(0, lambda error=exc: _show_research_error(self, symbol, error))
            return
        self.after(0, lambda result=payload: _render_research_payload(self, result))

    threading.Thread(target=worker, daemon=True).start()


def _build_research_payload(session: Any, portfolio, symbol: str, moves: tuple[float, ...]) -> _ResearchPayload:
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
    scenario_rows = build_scenario_rows(context, moves)

    earnings_text, fundamentals_text, filings_lines, sec_statuses = _fetch_sec_layers(symbol)
    statuses.extend(sec_statuses)

    try:
        macro_text = build_macro_report(timeout_seconds=8)
        statuses.append(DataSourceStatus("Official macro", "fresh/cache", _now(), "BLS/BEA/Treasury macro context loaded."))
    except Exception as exc:
        macro_text = f"Official Macro Snapshot\nFetched: unavailable\n\nMacro data unavailable/error: {exc}"
        statuses.append(DataSourceStatus("Official macro", "error", _now(), str(exc)))

    return _ResearchPayload(symbol, quote, indicators, context, scenario_rows, earnings_text, fundamentals_text, filings_lines, macro_text, statuses)


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
    self.schwab_research_risk_var.set(f"Trend {payload.indicators.trend}; vol {payload.indicators.volatility}")
    self.schwab_research_status_var.set(f"{payload.symbol} research updated at {_now()}")

    _set_research_text(self.schwab_research_overview_text, _overview_text(payload))
    _render_technicals(self, payload.indicators)
    _render_scenarios(self, payload)
    _set_research_text(self.schwab_research_earnings_text, _earnings_news_text(payload))
    _set_research_text(self.schwab_research_fundamentals_text, payload.fundamentals_text)
    _set_research_text(self.schwab_research_macro_text, payload.macro_text)

    output = getattr(self, "schwab_trading_preview_text", None)
    if output is not None:
        _set_research_text(output, _overview_text(payload) + "\n\n" + _source_status_text(payload.statuses))


def _overview_text(payload: _ResearchPayload) -> str:
    context = payload.context
    indicators = payload.indicators
    lines = [
        f"Schwab Research Workspace - {payload.symbol}",
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


def _render_technicals(self: tk.Tk, indicators: AdvancedIndicatorSnapshot) -> None:
    frame = self.schwab_research_technicals_frame
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
        ("MACD", indicators.macd, "12/26 line"),
        ("MACD signal", indicators.macd_signal, "9-period MACD signal"),
        ("RSI 14", indicators.rsi_14, "Momentum oscillator"),
        ("Bollinger upper", indicators.bollinger_upper, "Upper 2 stdev band"),
        ("Bollinger middle", indicators.bollinger_middle, "20 SMA band middle"),
        ("Bollinger lower", indicators.bollinger_lower, "Lower 2 stdev band"),
        ("ATR 14", indicators.atr_14, "Average true range"),
        ("Volume avg 20", indicators.volume_average_20, "Average daily volume"),
        ("Swing high", indicators.swing_high, "Recent 60-candle high"),
        ("Swing low", indicators.swing_low, "Recent 60-candle low"),
    ]
    for metric, value, read in rows:
        tree.insert("", tk.END, values=(metric, _number(value), read))
    for label, value in indicators.fibonacci_levels.items():
        tree.insert("", tk.END, values=(f"Fib {label}", _money(value), "Recent swing retracement"))
    notes = "\n".join(["Technicals summary:", f"- Trend classification: {indicators.trend}.", f"- Momentum classification: {indicators.momentum}.", f"- Volatility classification: {indicators.volatility}.", *[f"- {note}" for note in indicators.notes]])
    _set_research_text(frame.technical_notes_text, notes)  # type: ignore[attr-defined]


def _render_scenarios(self: tk.Tk, payload: _ResearchPayload) -> None:
    frame = self.schwab_research_scenarios_frame
    tree = frame.scenario_tree  # type: ignore[attr-defined]
    for row_id in tree.get_children():
        tree.delete(row_id)
    for row in payload.scenario_rows:
        tag = "positive" if row.position_pnl > 0 else "negative" if row.position_pnl < 0 else ""
        tree.insert("", tk.END, values=(row.scenario, _money(row.symbol_price), _money(row.position_pnl), f"{row.portfolio_pnl_impact:+.2%}", _money(row.new_portfolio_value)), tags=(tag,) if tag else ())
    stop = _float_from_var(getattr(self, "stop_price_var", None))
    target = _float_from_var(getattr(self, "options_target_price_var", None)) or _float_from_var(getattr(self, "limit_price_var", None))
    max_risk = _to_float(self.schwab_research_max_risk_var.get())
    size = suggested_position_size(entry_price=payload.context.last_price, stop_price=stop, max_risk_dollars=max_risk)
    lines = [
        "Scenario notes:",
        "- Scenarios model direct share P&L impact for the currently held quantity.",
        f"- Stop distance: {_percent(distance_to_price(payload.context.last_price, stop))}.",
        f"- Target distance: {_percent(distance_to_price(payload.context.last_price, target))}.",
        f"- Suggested size at {_money(max_risk)} max risk and stop {_money(stop)}: {_shares(size)}.",
        "- Options context: check loaded option chain, implied volatility, and earnings timing before using option structures.",
    ]
    _set_research_text(frame.scenario_note_text, "\n".join(lines))  # type: ignore[attr-defined]


def _recalculate_research_scenarios(self: tk.Tk) -> None:
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is None:
        return
    moves = _scenario_moves(self)
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
    )
    self.schwab_research_last_payload = updated
    _render_scenarios(self, updated)


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


def _scenario_moves(self: tk.Tk) -> tuple[float, ...]:
    moves = [-0.10, -0.05, -0.02, 0.02, 0.05, 0.10]
    custom = _to_float(self.schwab_research_custom_move_var.get())
    if custom is not None and custom != 0:
        move = abs(custom) / 100
        moves.extend([-move, move])
    return tuple(sorted(set(moves)))


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
    return "--" if value is None else f"${value:,.2f}"


def _number(value: float | None) -> str:
    return "--" if value is None else f"{value:,.2f}"


def _percent(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2%}"


def _shares(value: float | None) -> str:
    return "--" if value is None else f"{value:,.2f} shares"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
