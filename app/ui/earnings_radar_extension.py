from __future__ import annotations

import math
import threading
import webbrowser
from datetime import datetime
from typing import Any, Callable, Iterable, Type

import tkinter as tk
from tkinter import messagebox, ttk

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
from app.data.earnings_calendar import ALPHA_VANTAGE_HORIZONS, AlphaVantageEarningsCalendarClient, UpcomingEarningsRecord
from app.data.sec_edgar import SecEdgarClient
from app.ui import polished_theme


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
RECENT_NUMERIC_COLUMNS = {"revenue", "revenue_growth", "eps", "net_income"}
UPCOMING_NUMERIC_COLUMNS = {"estimate"}


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
    ttk.Button(actions, text="Earnings Radar", command=self.show_earnings_radar, style="Accent.TButton").grid(
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
    window.title("Earnings Radar / SEC + Calendar")
    window.geometry("1360x860")
    window.minsize(1120, 700)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(0, weight=1)
    self.earnings_radar_window = window

    notebook = ttk.Notebook(window)
    notebook.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
    recent_tab = ttk.Frame(notebook, style="Panel.TFrame")
    upcoming_tab = ttk.Frame(notebook, style="Panel.TFrame")
    notebook.add(recent_tab, text="Recent EDGAR Drops")
    notebook.add(upcoming_tab, text="Upcoming Earnings")
    _build_recent_tab(self, recent_tab)
    _build_upcoming_tab(self, upcoming_tab)

    cached = EarningsRadarStore().load(max_age=None)
    if cached is not None:
        _load_recent_snapshot(self, cached)
        self.earnings_recent_status_var.set(f"Loaded cached EDGAR earnings radar: {len(cached.recent)} filings.")
    else:
        self.earnings_recent_status_var.set("Ready. Refresh SEC data to load recent earnings drops.")

    window.after(250, lambda app=self: _refresh_recent(app, force_refresh=False))
    window.after(500, lambda app=self: _refresh_upcoming(app, force_refresh=False))

    def _close() -> None:
        self.earnings_radar_window = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _close)


def _ensure_state(self: tk.Tk) -> None:
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


def _build_recent_tab(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(3, weight=1)
    _header(
        parent,
        "Recent EDGAR Earnings Drops",
        "Companies listed here recently filed earnings-related SEC documents. 8-K Item 2.02 and earnings-looking EX-99 exhibits are treated as earnings drops; formal 10-Q/10-K/20-F/40-F filings are shown as report filings.",
        self.earnings_recent_status_var,
    ).grid(row=0, column=0, sticky="ew", pady=(0, 8))
    _build_recent_filters(self, parent)
    self.earnings_recent_chart = _chart(parent, row=2)
    self.earnings_recent_table = _table(parent, row=3, title="Recent EDGAR Drops", columns=RECENT_COLUMNS, sort=lambda col: _sort_recent(self, col))
    self.earnings_recent_table.bind("<Double-1>", lambda _event, app=self: _open_recent_filing(app), add="+")
    self.earnings_recent_table.tag_configure("kind_drop", foreground=polished_theme.ACCENT_SOFT)
    self.earnings_recent_table.tag_configure("kind_formal", foreground=polished_theme.POSITIVE)
    _footer(self, parent, row=4, page_var_name="earnings_recent_page_var", size_var=self.earnings_recent_page_size_var, prev=lambda: _turn_recent_page(self, -1), next_=lambda: _turn_recent_page(self, 1), apply=lambda: _apply_recent_filters(self))


def _build_upcoming_tab(self: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(3, weight=1)
    _header(
        parent,
        "Upcoming Earnings Calendar",
        "Upcoming dates come from the configured earnings-calendar provider, not EDGAR. Dates can change and should be treated as planning inputs, not official SEC filings.",
        self.earnings_upcoming_status_var,
    ).grid(row=0, column=0, sticky="ew", pady=(0, 8))
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
    actions = ttk.Frame(box, style="Panel.TFrame")
    actions.grid(row=3, column=0, columnspan=8, sticky="ew", pady=(8, 0))
    ttk.Button(actions, text="Refresh SEC Data", command=lambda: _refresh_recent(self, force_refresh=True), style="Accent.TButton").pack(side=tk.LEFT)
    ttk.Button(actions, text="Open SEC Filing", command=lambda: _open_recent_filing(self)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text="Open Earnings Exhibit", command=lambda: _open_recent_exhibit(self)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text="Clear Filters", command=lambda: _clear_recent_filters(self)).pack(side=tk.LEFT, padx=(8, 0))


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
        client = AlphaVantageEarningsCalendarClient()
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


def _finish_upcoming_error(self: tk.Tk, error: Exception) -> None:
    self._earnings_upcoming_refreshing = False
    self.earnings_upcoming_status_var.set(f"Calendar refresh failed: {error}")


def _load_recent_snapshot(self: tk.Tk, snapshot: EarningsRadarSnapshot) -> None:
    self.earnings_recent_records = list(snapshot.recent)
    _set_combo_values(self.earnings_recent_item_combo, ["All", *sorted({record.items for record in self.earnings_recent_records if record.items})], self.earnings_recent_item_var)
    _set_combo_values(self.earnings_recent_sector_combo, ["All", *sorted({record.sector or EMPTY_VALUE for record in self.earnings_recent_records})], self.earnings_recent_sector_var)
    _set_combo_values(self.earnings_recent_exchange_combo, ["All", *sorted({record.exchange or EMPTY_VALUE for record in self.earnings_recent_records})], self.earnings_recent_exchange_var)
    _set_combo_values(self.earnings_recent_risk_flag_combo, ["All", *sorted({flag for record in self.earnings_recent_records for flag in record.risk_flags})], self.earnings_recent_risk_flag_var)
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


def _open_upcoming_source(self: tk.Tk) -> None:
    record = _selected(self.earnings_upcoming_table, self.earnings_upcoming_row_map, "Open Source", "Select an upcoming earnings row first.")
    if record is not None:
        _open_url(record.source_url, "Open Source", "The selected row does not have a source URL.")


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


def _header(parent: ttk.Frame, title: str, body: str, status_var: tk.StringVar) -> ttk.LabelFrame:
    header = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    header.columnconfigure(0, weight=1)
    ttk.Label(header, text=body, style="Subtle.TLabel", wraplength=1120).grid(row=0, column=0, sticky="w")
    ttk.Label(header, textvariable=status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 0))
    return header


def _chart(parent: ttk.Frame, *, row: int) -> tk.Canvas:
    canvas = tk.Canvas(parent, height=150, bg=polished_theme.PANEL, highlightthickness=1, highlightbackground=polished_theme.BORDER)
    canvas.grid(row=row, column=0, sticky="ew", pady=(0, 8))
    return canvas


def _table(parent: ttk.Frame, *, row: int, title: str, columns: tuple[tuple[str, str, int, str], ...], sort: Callable[[str], None]) -> ttk.Treeview:
    frame = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    frame.grid(row=row, column=0, sticky="nsew", pady=(0, 8))
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)
    tree = ttk.Treeview(frame, columns=tuple(column_id for column_id, _label, _width, _anchor in columns), show="headings", height=14, selectmode="browse")
    for column_id, label, width, anchor in columns:
        tree.heading(column_id, text=label, command=lambda col=column_id: sort(col))
        tree.column(column_id, width=width, minwidth=min(width, 90), anchor=anchor, stretch=column_id in {"company", "industry", "risk_flags", "filing_link", "exhibit_link", "source_link"})
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
