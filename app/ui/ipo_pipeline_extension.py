from __future__ import annotations

import math
import threading
import webbrowser
from datetime import datetime, timedelta
from typing import Any, Callable, Type

import tkinter as tk
from tkinter import messagebox, ttk

from app.analytics.ipo_pipeline import (
    EMPTY_VALUE,
    IPO_FORMS,
    IPO_STATUSES,
    IpoPipelineRecord,
    IpoPipelineSnapshot,
    IpoPipelineStore,
    display_money,
    display_optional_text,
    display_percent,
    display_price_range,
    fetch_ipo_pipeline_snapshot,
)
from app.data.sec_edgar import SecEdgarClient


TABLE_COLUMNS = (
    ("company", "Company", 220, tk.W),
    ("ticker", "Proposed ticker", 110, tk.W),
    ("cik", "CIK", 92, tk.W),
    ("form", "Form", 78, tk.W),
    ("filed_date", "Filed date", 98, tk.W),
    ("status", "IPO status", 160, tk.W),
    ("sector", "Sector / SIC", 150, tk.W),
    ("industry", "Industry", 220, tk.W),
    ("exchange", "Exchange", 135, tk.W),
    ("offering_amount", "Offering amount", 130, tk.E),
    ("price_range", "Price range", 142, tk.W),
    ("shares_offered", "Shares offered", 124, tk.E),
    ("market_cap", "Implied market cap", 142, tk.E),
    ("revenue", "Revenue", 118, tk.E),
    ("revenue_growth", "Revenue growth", 124, tk.E),
    ("net_income", "Net income / loss", 132, tk.E),
    ("gross_margin", "Gross margin", 112, tk.E),
    ("cash", "Cash", 110, tk.E),
    ("debt", "Debt", 110, tk.E),
    ("use_of_proceeds", "Use of proceeds", 220, tk.W),
    ("underwriters", "Underwriters", 220, tk.W),
    ("auditor", "Auditor", 170, tk.W),
    ("risk_flags", "Risk flags", 260, tk.W),
    ("filing_link", "Filing link", 420, tk.W),
)

NUMERIC_COLUMNS = {
    "offering_amount",
    "shares_offered",
    "market_cap",
    "revenue",
    "revenue_growth",
    "net_income",
    "gross_margin",
    "cash",
    "debt",
}


def install_ipo_pipeline_extension(app_cls: Type[tk.Tk]) -> None:
    previous_build_layout = app_cls._build_layout

    def _build_layout_with_ipo_pipeline(self: tk.Tk) -> None:
        previous_build_layout(self)
        _schedule_ipo_pipeline_button_injection(self)

    app_cls._build_layout = _build_layout_with_ipo_pipeline  # type: ignore[method-assign]
    app_cls.show_ipo_pipeline = _open_ipo_pipeline_dashboard  # type: ignore[attr-defined]


def _schedule_ipo_pipeline_button_injection(self: tk.Tk) -> None:
    for delay_ms in (0, 100, 500, 1200):
        self.after(delay_ms, lambda app=self: _inject_ipo_pipeline_buttons(app))


def _inject_ipo_pipeline_buttons(self: tk.Tk) -> None:
    actions = _find_labelframe(self, "Schwab Actions")
    if actions is not None and not getattr(actions, "_ipo_pipeline_button_installed", False):
        for column in range(3):
            actions.columnconfigure(column, weight=1, uniform="schwab_actions")
        _add_grid_button(actions, row=5, column=0, text="IPO Pipeline", command=self.show_ipo_pipeline, style="Accent.TButton")
        setattr(actions, "_ipo_pipeline_button_installed", True)

    planner = _find_labelframe(self, "Trade Planner")
    if planner is not None and not getattr(planner, "_ipo_pipeline_button_installed", False):
        actions_frame = _find_first_button_grid(planner)
        if actions_frame is not None:
            column = len([child for child in actions_frame.winfo_children() if _widget_class(child) == "TButton"])
            try:
                actions_frame.columnconfigure(column, weight=1, uniform="primary_actions")
                ttk.Button(actions_frame, text="IPO Pipeline", command=self.show_ipo_pipeline).grid(
                    row=1,
                    column=0,
                    columnspan=max(1, column),
                    sticky="ew",
                    pady=(8, 0),
                )
                setattr(planner, "_ipo_pipeline_button_installed", True)
            except tk.TclError:
                pass


def _open_ipo_pipeline_dashboard(self: tk.Tk) -> None:
    existing = getattr(self, "ipo_pipeline_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                return
        except tk.TclError:
            pass

    window = tk.Toplevel(self)
    window.title("IPO Pipeline / SEC IPO Radar")
    window.geometry("1360x860")
    window.minsize(1120, 700)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(3, weight=1)
    self.ipo_pipeline_window = window

    _ensure_ipo_state(self)
    _build_ipo_header(self, window)
    _build_ipo_filters(self, window)
    _build_ipo_charts(self, window)
    _build_ipo_table(self, window)
    _build_ipo_footer(self, window)

    cached = IpoPipelineStore().load(max_age=None)
    if cached is not None:
        _load_ipo_snapshot(self, cached)
        self.ipo_pipeline_status_var.set(f"Loaded cached IPO pipeline: {len(cached.records)} companies.")
    else:
        self.ipo_pipeline_status_var.set("Ready. Refresh SEC data to load the IPO pipeline.")

    window.after(250, lambda app=self: _refresh_ipo_pipeline(app, force_refresh=False))

    def _close() -> None:
        self.ipo_pipeline_window = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _close)


def _ensure_ipo_state(self: tk.Tk) -> None:
    self.ipo_pipeline_records: list[IpoPipelineRecord] = []
    self.ipo_pipeline_filtered_records: list[IpoPipelineRecord] = []
    self.ipo_pipeline_row_map: dict[str, IpoPipelineRecord] = {}
    self.ipo_pipeline_sort_column = "filed_date"
    self.ipo_pipeline_sort_desc = True
    self.ipo_pipeline_page = 0
    self.ipo_pipeline_status_var = tk.StringVar(value="Ready.")
    self.ipo_pipeline_search_var = tk.StringVar(value="")
    self.ipo_pipeline_form_var = tk.StringVar(value="All")
    self.ipo_pipeline_status_filter_var = tk.StringVar(value="All")
    self.ipo_pipeline_sector_var = tk.StringVar(value="All")
    self.ipo_pipeline_exchange_var = tk.StringVar(value="All")
    self.ipo_pipeline_profitability_var = tk.StringVar(value="All")
    self.ipo_pipeline_risk_flag_var = tk.StringVar(value="All")
    self.ipo_pipeline_date_from_var = tk.StringVar(value="")
    self.ipo_pipeline_date_to_var = tk.StringVar(value="")
    self.ipo_pipeline_limit_var = tk.StringVar(value="60")
    self.ipo_pipeline_page_size_var = tk.StringVar(value="100")
    self.ipo_pipeline_parse_documents_var = tk.BooleanVar(value=True)
    self.ipo_pipeline_price_range_var = tk.BooleanVar(value=False)
    self.ipo_pipeline_has_424b4_var = tk.BooleanVar(value=False)
    self.ipo_pipeline_has_effect_var = tk.BooleanVar(value=False)
    self.ipo_pipeline_foreign_var = tk.BooleanVar(value=False)


def _build_ipo_header(self: tk.Tk, parent: ttk.Frame) -> None:
    header = ttk.LabelFrame(parent, text="SEC IPO Radar", style="Card.TLabelframe")
    header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
    header.columnconfigure(0, weight=1)
    ttk.Label(
        header,
        text="Companies listed here have filed IPO-related documents with the SEC. A public S-1/F-1 filing means the company has publicly filed to go public; it does not necessarily mean the company is already trading.",
        style="Subtle.TLabel",
        wraplength=1180,
    ).grid(row=0, column=0, sticky="w")
    ttk.Label(header, textvariable=self.ipo_pipeline_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 0))


def _build_ipo_filters(self: tk.Tk, parent: ttk.Frame) -> None:
    filters = ttk.LabelFrame(parent, text="Filters", style="Card.TLabelframe")
    filters.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
    for column in range(8):
        filters.columnconfigure(column, weight=1 if column in {1, 3, 5, 7} else 0)

    _label(filters, "Search", 0, 0)
    search = ttk.Entry(filters, textvariable=self.ipo_pipeline_search_var)
    search.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=4)
    search.bind("<KeyRelease>", lambda _event, app=self: _apply_ipo_filters(app), add="+")

    _combo_filter(filters, "Form", self.ipo_pipeline_form_var, ["All", *IPO_FORMS], row=0, column=2, command=lambda app=self: _apply_ipo_filters(app))
    _combo_filter(filters, "Status", self.ipo_pipeline_status_filter_var, ["All", *IPO_STATUSES], row=0, column=4, command=lambda app=self: _apply_ipo_filters(app))
    _combo_filter(filters, "Profit", self.ipo_pipeline_profitability_var, ["All", "Profitable", "Unprofitable", "Not extracted"], row=0, column=6, command=lambda app=self: _apply_ipo_filters(app))

    self.ipo_pipeline_sector_combo = _combo_filter(filters, "Sector", self.ipo_pipeline_sector_var, ["All"], row=1, column=0, command=lambda app=self: _apply_ipo_filters(app))
    self.ipo_pipeline_exchange_combo = _combo_filter(filters, "Exchange", self.ipo_pipeline_exchange_var, ["All"], row=1, column=2, command=lambda app=self: _apply_ipo_filters(app))
    self.ipo_pipeline_risk_flag_combo = _combo_filter(filters, "Risk flag", self.ipo_pipeline_risk_flag_var, ["All"], row=1, column=4, command=lambda app=self: _apply_ipo_filters(app))
    _combo_filter(filters, "Per form", self.ipo_pipeline_limit_var, ["20", "40", "60", "100"], row=1, column=6, command=None)

    date_frame = ttk.Frame(filters, style="Panel.TFrame")
    date_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))
    date_frame.columnconfigure(1, weight=1)
    date_frame.columnconfigure(3, weight=1)
    ttk.Label(date_frame, text="From", style="Subtle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
    from_entry = ttk.Entry(date_frame, textvariable=self.ipo_pipeline_date_from_var, width=12)
    from_entry.grid(row=0, column=1, sticky="ew", padx=(0, 10))
    ttk.Label(date_frame, text="To", style="Subtle.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 6))
    to_entry = ttk.Entry(date_frame, textvariable=self.ipo_pipeline_date_to_var, width=12)
    to_entry.grid(row=0, column=3, sticky="ew")
    for entry in (from_entry, to_entry):
        entry.bind("<KeyRelease>", lambda _event, app=self: _apply_ipo_filters(app), add="+")

    check_frame = ttk.Frame(filters, style="Panel.TFrame")
    check_frame.grid(row=2, column=4, columnspan=4, sticky="ew", pady=(6, 0))
    for index, (label, var) in enumerate(
        [
            ("Price range", self.ipo_pipeline_price_range_var),
            ("424B4 filed", self.ipo_pipeline_has_424b4_var),
            ("EFFECT filed", self.ipo_pipeline_has_effect_var),
            ("Foreign issuer", self.ipo_pipeline_foreign_var),
            ("Parse optional S-1 fields", self.ipo_pipeline_parse_documents_var),
        ]
    ):
        ttk.Checkbutton(check_frame, text=label, variable=var, command=lambda app=self: _apply_ipo_filters(app)).grid(
            row=0,
            column=index,
            sticky="w",
            padx=(0 if index == 0 else 8, 0),
        )

    actions = ttk.Frame(filters, style="Panel.TFrame")
    actions.grid(row=3, column=0, columnspan=8, sticky="ew", pady=(8, 0))
    ttk.Button(actions, text="Refresh SEC Data", command=lambda app=self: _refresh_ipo_pipeline(app, force_refresh=True), style="Accent.TButton").pack(side=tk.LEFT)
    ttk.Button(actions, text="Open SEC Filing", command=lambda app=self: _open_selected_filing(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(actions, text="Clear Filters", command=lambda app=self: _clear_ipo_filters(app)).pack(side=tk.LEFT, padx=(8, 0))


def _build_ipo_charts(self: tk.Tk, parent: ttk.Frame) -> None:
    charts = ttk.Frame(parent, style="Panel.TFrame")
    charts.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 8))
    charts.columnconfigure((0, 1, 2), weight=1, uniform="ipo_charts")
    self.ipo_pipeline_status_chart = tk.Canvas(charts, height=150, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
    self.ipo_pipeline_sector_chart = tk.Canvas(charts, height=150, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
    self.ipo_pipeline_date_chart = tk.Canvas(charts, height=150, bg="#ffffff", highlightthickness=1, highlightbackground="#cbd5e1")
    for column, canvas in enumerate((self.ipo_pipeline_status_chart, self.ipo_pipeline_sector_chart, self.ipo_pipeline_date_chart)):
        canvas.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
        canvas.bind("<Configure>", lambda _event, app=self: _draw_ipo_charts(app), add="+")


def _build_ipo_table(self: tk.Tk, parent: ttk.Frame) -> None:
    table_frame = ttk.LabelFrame(parent, text="IPO Pipeline", style="Card.TLabelframe")
    table_frame.grid(row=3, column=0, sticky="nsew", padx=12, pady=(0, 8))
    table_frame.rowconfigure(0, weight=1)
    table_frame.columnconfigure(0, weight=1)

    columns = tuple(column_id for column_id, _label, _width, _anchor in TABLE_COLUMNS)
    tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=16, selectmode="browse")
    for column_id, label, width, anchor in TABLE_COLUMNS:
        tree.heading(column_id, text=label, command=lambda col=column_id, app=self: _sort_ipo_table(app, col))
        tree.column(column_id, width=width, minwidth=min(width, 90), anchor=anchor, stretch=column_id in {"company", "industry", "risk_flags", "filing_link"})
    tree.tag_configure("status_filed", foreground="#1d4ed8")
    tree.tag_configure("status_amended", foreground="#92400e")
    tree.tag_configure("status_effective", foreground="#047857")
    tree.tag_configure("status_priced", foreground="#0f766e")
    tree.tag_configure("status_trading_candidate", foreground="#b91c1c")
    tree.grid(row=0, column=0, sticky="nsew")
    tree.bind("<Double-1>", lambda _event, app=self: _open_selected_filing(app), add="+")
    y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
    y_scroll.grid(row=0, column=1, sticky="ns")
    x_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=tree.xview)
    x_scroll.grid(row=1, column=0, sticky="ew")
    tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
    self.ipo_pipeline_table = tree


def _build_ipo_footer(self: tk.Tk, parent: ttk.Frame) -> None:
    footer = ttk.Frame(parent, style="Panel.TFrame")
    footer.grid(row=4, column=0, sticky="ew", padx=12, pady=(0, 12))
    footer.columnconfigure(1, weight=1)
    ttk.Button(footer, text="Prev", command=lambda app=self: _turn_ipo_page(app, -1)).grid(row=0, column=0, sticky="w")
    self.ipo_pipeline_page_var = tk.StringVar(value="Page 1 / 1")
    ttk.Label(footer, textvariable=self.ipo_pipeline_page_var, style="Subtle.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
    ttk.Button(footer, text="Next", command=lambda app=self: _turn_ipo_page(app, 1)).grid(row=0, column=2, sticky="e")
    ttk.Label(footer, text="Rows/page", style="Subtle.TLabel").grid(row=0, column=3, sticky="e", padx=(12, 6))
    page_size = ttk.Combobox(footer, textvariable=self.ipo_pipeline_page_size_var, values=["50", "100", "200"], width=6, state="readonly")
    page_size.grid(row=0, column=4, sticky="e")
    page_size.bind("<<ComboboxSelected>>", lambda _event, app=self: _apply_ipo_filters(app), add="+")


def _refresh_ipo_pipeline(self: tk.Tk, *, force_refresh: bool) -> None:
    if getattr(self, "_ipo_pipeline_refreshing", False):
        return
    self._ipo_pipeline_refreshing = True
    self.ipo_pipeline_status_var.set("Loading SEC IPO filings...")
    parse_documents = bool(self.ipo_pipeline_parse_documents_var.get())
    per_form_limit = _safe_int(self.ipo_pipeline_limit_var.get(), default=60, minimum=20, maximum=100)

    def worker() -> None:
        try:
            snapshot = fetch_ipo_pipeline_snapshot(
                SecEdgarClient(),
                per_form_limit=per_form_limit,
                force_refresh=force_refresh,
                parse_documents=parse_documents,
            )
        except Exception as exc:
            self.after(0, lambda error=exc: _finish_ipo_refresh_error(self, error))
            return
        self.after(0, lambda loaded=snapshot: _finish_ipo_refresh_success(self, loaded))

    threading.Thread(target=worker, daemon=True).start()


def _finish_ipo_refresh_success(self: tk.Tk, snapshot: IpoPipelineSnapshot) -> None:
    self._ipo_pipeline_refreshing = False
    _load_ipo_snapshot(self, snapshot)
    source = "cache" if snapshot.used_cache else "SEC"
    error_suffix = f" ({len(snapshot.errors)} nonblocking warnings)" if snapshot.errors else ""
    self.ipo_pipeline_status_var.set(f"Loaded {len(snapshot.records)} IPO companies from {source}. Fetched {snapshot.fetched_at}.{error_suffix}")


def _finish_ipo_refresh_error(self: tk.Tk, error: Exception) -> None:
    self._ipo_pipeline_refreshing = False
    self.ipo_pipeline_status_var.set("IPO pipeline refresh failed.")
    messagebox.showerror("IPO Pipeline refresh failed", str(error))


def _load_ipo_snapshot(self: tk.Tk, snapshot: IpoPipelineSnapshot) -> None:
    self.ipo_pipeline_records = list(snapshot.records)
    _refresh_filter_values(self)
    self.ipo_pipeline_page = 0
    _apply_ipo_filters(self)


def _refresh_filter_values(self: tk.Tk) -> None:
    sectors = ["All", *sorted({record.sector or EMPTY_VALUE for record in self.ipo_pipeline_records})]
    exchanges = ["All", *sorted({record.exchange or EMPTY_VALUE for record in self.ipo_pipeline_records})]
    risk_flags = ["All", *sorted({flag for record in self.ipo_pipeline_records for flag in record.risk_flags})]
    _set_combo_values(self.ipo_pipeline_sector_combo, sectors, self.ipo_pipeline_sector_var)
    _set_combo_values(self.ipo_pipeline_exchange_combo, exchanges, self.ipo_pipeline_exchange_var)
    _set_combo_values(self.ipo_pipeline_risk_flag_combo, risk_flags, self.ipo_pipeline_risk_flag_var)


def _apply_ipo_filters(self: tk.Tk) -> None:
    records = list(getattr(self, "ipo_pipeline_records", []))
    search = self.ipo_pipeline_search_var.get().strip().lower()
    form_filter = self.ipo_pipeline_form_var.get()
    status_filter = self.ipo_pipeline_status_filter_var.get()
    sector_filter = self.ipo_pipeline_sector_var.get()
    exchange_filter = self.ipo_pipeline_exchange_var.get()
    profit_filter = self.ipo_pipeline_profitability_var.get()
    risk_flag_filter = self.ipo_pipeline_risk_flag_var.get()
    start_date = _parse_date(self.ipo_pipeline_date_from_var.get())
    end_date = _parse_date(self.ipo_pipeline_date_to_var.get())

    filtered: list[IpoPipelineRecord] = []
    for record in records:
        if search and search not in _record_search_text(record):
            continue
        if form_filter != "All" and record.form != form_filter:
            continue
        if status_filter != "All" and record.ipo_status != status_filter:
            continue
        if sector_filter != "All" and (record.sector or EMPTY_VALUE) != sector_filter:
            continue
        if exchange_filter != "All" and (record.exchange or EMPTY_VALUE) != exchange_filter:
            continue
        if risk_flag_filter != "All" and risk_flag_filter not in record.risk_flags:
            continue
        record_date = _parse_date(record.filed_date)
        if start_date is not None and record_date is not None and record_date < start_date:
            continue
        if end_date is not None and record_date is not None and record_date > end_date:
            continue
        if profit_filter == "Profitable" and (record.net_income is None or record.net_income <= 0):
            continue
        if profit_filter == "Unprofitable" and (record.net_income is None or record.net_income >= 0):
            continue
        if profit_filter == "Not extracted" and record.net_income is not None:
            continue
        if self.ipo_pipeline_price_range_var.get() and (record.price_range_low is None or record.price_range_high is None):
            continue
        if self.ipo_pipeline_has_424b4_var.get() and not record.has_final_prospectus:
            continue
        if self.ipo_pipeline_has_effect_var.get() and not record.has_effect:
            continue
        if self.ipo_pipeline_foreign_var.get() and not record.is_foreign_issuer:
            continue
        filtered.append(record)

    self.ipo_pipeline_filtered_records = _sorted_records(self, filtered)
    max_page = _max_page(self)
    self.ipo_pipeline_page = min(getattr(self, "ipo_pipeline_page", 0), max_page)
    _populate_ipo_table(self)
    _draw_ipo_charts(self)


def _sorted_records(self: tk.Tk, records: list[IpoPipelineRecord]) -> list[IpoPipelineRecord]:
    column = getattr(self, "ipo_pipeline_sort_column", "filed_date")
    desc = bool(getattr(self, "ipo_pipeline_sort_desc", True))
    present = [record for record in records if _sort_value(record, column) is not None]
    missing = [record for record in records if _sort_value(record, column) is None]
    present.sort(key=lambda record: _sort_value(record, column), reverse=desc)
    return present + missing


def _populate_ipo_table(self: tk.Tk) -> None:
    tree = self.ipo_pipeline_table
    for row_id in tree.get_children():
        tree.delete(row_id)
    self.ipo_pipeline_row_map = {}

    page_size = _page_size(self)
    total = len(self.ipo_pipeline_filtered_records)
    page_count = max(1, math.ceil(total / page_size))
    self.ipo_pipeline_page = min(max(self.ipo_pipeline_page, 0), page_count - 1)
    start = self.ipo_pipeline_page * page_size
    end = start + page_size
    page_records = self.ipo_pipeline_filtered_records[start:end]

    for index, record in enumerate(page_records):
        iid = f"ipo_{start + index}"
        self.ipo_pipeline_row_map[iid] = record
        tree.insert("", tk.END, iid=iid, values=_record_values(record), tags=(_status_tag(record.ipo_status),))

    self.ipo_pipeline_page_var.set(f"Page {self.ipo_pipeline_page + 1} / {page_count} - {total} records")


def _record_values(record: IpoPipelineRecord) -> tuple[str, ...]:
    return (
        record.company_name,
        display_optional_text(record.proposed_ticker),
        record.cik,
        record.form,
        record.filed_date or EMPTY_VALUE,
        record.ipo_status,
        f"{display_optional_text(record.sector)} / {display_optional_text(record.sic)}",
        display_optional_text(record.industry),
        display_optional_text(record.exchange),
        display_money(record.offering_amount),
        display_price_range(record),
        display_money(record.shares_offered),
        display_money(record.implied_market_cap),
        display_money(record.revenue),
        display_percent(record.revenue_growth),
        display_money(record.net_income),
        display_percent(record.gross_margin),
        display_money(record.cash),
        display_money(record.debt),
        display_optional_text(record.use_of_proceeds, missing="Not extracted yet"),
        ", ".join(record.underwriters) if record.underwriters else "Not extracted yet",
        display_optional_text(record.auditor, missing="Not extracted yet"),
        ", ".join(record.risk_flags) if record.risk_flags else EMPTY_VALUE,
        record.filing_url,
    )


def _draw_ipo_charts(self: tk.Tk) -> None:
    records = getattr(self, "ipo_pipeline_filtered_records", [])
    _draw_bar_chart(self.ipo_pipeline_status_chart, "Filed vs amended vs effective vs priced", _counts(records, lambda record: record.ipo_status))
    _draw_bar_chart(self.ipo_pipeline_sector_chart, "IPO count by sector", _counts(records, lambda record: record.sector or "Unknown"), limit=8)
    _draw_bar_chart(self.ipo_pipeline_date_chart, "IPO filings by month", _counts(records, lambda record: (record.filed_date or "0000-00")[:7]), limit=8)


def _draw_bar_chart(canvas: tk.Canvas, title: str, counts: dict[str, int], *, limit: int = 6) -> None:
    canvas.delete("all")
    width = max(canvas.winfo_width(), 260)
    height = max(canvas.winfo_height(), 140)
    canvas.create_text(10, 10, text=title, anchor="nw", fill="#0f172a", font=("Segoe UI", 9, "bold"))
    if not counts:
        canvas.create_text(10, 42, text="No matching IPO filings.", anchor="nw", fill="#64748b", font=("Segoe UI", 9))
        return
    items = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    max_value = max(count for _label, count in items) or 1
    row_height = max(16, min(22, (height - 38) // max(len(items), 1)))
    for index, (label, count) in enumerate(items):
        y = 38 + index * row_height
        bar_width = int((width - 150) * (count / max_value))
        canvas.create_text(10, y, text=_truncate(label, 18), anchor="nw", fill="#334155", font=("Segoe UI", 8))
        canvas.create_rectangle(128, y + 2, 128 + bar_width, y + 13, fill="#2563eb", outline="")
        canvas.create_text(width - 10, y, text=str(count), anchor="ne", fill="#0f172a", font=("Segoe UI", 8, "bold"))


def _sort_ipo_table(self: tk.Tk, column: str) -> None:
    if self.ipo_pipeline_sort_column == column:
        self.ipo_pipeline_sort_desc = not self.ipo_pipeline_sort_desc
    else:
        self.ipo_pipeline_sort_column = column
        self.ipo_pipeline_sort_desc = column in {"filed_date", *NUMERIC_COLUMNS}
    self.ipo_pipeline_page = 0
    _apply_ipo_filters(self)


def _turn_ipo_page(self: tk.Tk, delta: int) -> None:
    self.ipo_pipeline_page = min(max(self.ipo_pipeline_page + delta, 0), _max_page(self))
    _populate_ipo_table(self)


def _open_selected_filing(self: tk.Tk) -> None:
    tree = getattr(self, "ipo_pipeline_table", None)
    if tree is None:
        return
    selection = tree.selection()
    if not selection:
        messagebox.showinfo("Open SEC Filing", "Select an IPO pipeline row first.")
        return
    record = self.ipo_pipeline_row_map.get(selection[0])
    if record is None or not record.filing_url:
        messagebox.showinfo("Open SEC Filing", "The selected row does not have an SEC filing URL.")
        return
    webbrowser.open_new_tab(record.filing_url)


def _clear_ipo_filters(self: tk.Tk) -> None:
    self.ipo_pipeline_search_var.set("")
    self.ipo_pipeline_form_var.set("All")
    self.ipo_pipeline_status_filter_var.set("All")
    self.ipo_pipeline_sector_var.set("All")
    self.ipo_pipeline_exchange_var.set("All")
    self.ipo_pipeline_profitability_var.set("All")
    self.ipo_pipeline_risk_flag_var.set("All")
    self.ipo_pipeline_date_from_var.set("")
    self.ipo_pipeline_date_to_var.set("")
    self.ipo_pipeline_price_range_var.set(False)
    self.ipo_pipeline_has_424b4_var.set(False)
    self.ipo_pipeline_has_effect_var.set(False)
    self.ipo_pipeline_foreign_var.set(False)
    self.ipo_pipeline_page = 0
    _apply_ipo_filters(self)


def _record_search_text(record: IpoPipelineRecord) -> str:
    return " ".join(
        [
            record.company_name,
            record.proposed_ticker or "",
            record.cik,
            record.industry or "",
            " ".join(record.underwriters),
            record.auditor or "",
        ]
    ).lower()


def _sort_value(record: IpoPipelineRecord, column: str) -> Any:
    mapping: dict[str, Any] = {
        "company": record.company_name.lower(),
        "ticker": (record.proposed_ticker or "").lower() or None,
        "cik": record.cik,
        "form": record.form,
        "filed_date": record.filed_date or None,
        "status": record.ipo_status,
        "sector": record.sector or None,
        "industry": record.industry or None,
        "exchange": record.exchange or None,
        "offering_amount": record.offering_amount,
        "price_range": record.price_range_low,
        "shares_offered": record.shares_offered,
        "market_cap": record.implied_market_cap,
        "revenue": record.revenue,
        "revenue_growth": record.revenue_growth,
        "net_income": record.net_income,
        "gross_margin": record.gross_margin,
        "cash": record.cash,
        "debt": record.debt,
        "use_of_proceeds": record.use_of_proceeds,
        "underwriters": ", ".join(record.underwriters) if record.underwriters else None,
        "auditor": record.auditor,
        "risk_flags": len(record.risk_flags),
        "filing_link": record.filing_url,
    }
    return mapping.get(column)


def _status_tag(status: str) -> str:
    return {
        "Filed": "status_filed",
        "Amended": "status_amended",
        "Effective": "status_effective",
        "Priced / Final Prospectus": "status_priced",
        "Trading Candidate": "status_trading_candidate",
    }.get(status, "")


def _counts(records: list[IpoPipelineRecord], selector: Callable[[IpoPipelineRecord], str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        label = selector(record) or "Unknown"
        counts[label] = counts.get(label, 0) + 1
    return counts


def _parse_date(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _page_size(self: tk.Tk) -> int:
    return _safe_int(self.ipo_pipeline_page_size_var.get(), default=100, minimum=25, maximum=500)


def _max_page(self: tk.Tk) -> int:
    return max(0, math.ceil(len(getattr(self, "ipo_pipeline_filtered_records", [])) / _page_size(self)) - 1)


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


def _combo_filter(
    parent: ttk.Frame,
    label: str,
    variable: tk.StringVar,
    values: list[str],
    *,
    row: int,
    column: int,
    command: Callable[[], None] | None,
) -> ttk.Combobox:
    _label(parent, label, row, column)
    combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
    combo.grid(row=row, column=column + 1, sticky="ew", padx=(0, 10), pady=4)
    if command is not None:
        combo.bind("<<ComboboxSelected>>", lambda _event: command(), add="+")
    return combo


def _set_combo_values(combo: ttk.Combobox, values: list[str], variable: tk.StringVar) -> None:
    combo.configure(values=values)
    if variable.get() not in values:
        variable.set("All")


def _add_grid_button(parent: ttk.Frame, *, row: int, column: int, text: str, command: Callable[[], None], style: str = "TButton") -> None:
    ttk.Button(parent, text=text, command=command, style=style).grid(
        row=row,
        column=column,
        sticky="ew",
        padx=(0 if column == 0 else 4, 0),
        pady=(0 if row == 0 else 6, 6),
        ipady=1,
    )


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


def _find_first_button_grid(root: tk.Widget) -> ttk.Frame | None:
    for child in root.winfo_children():
        if _widget_class(child) == "TFrame" and any(_widget_class(grandchild) == "TButton" for grandchild in child.winfo_children()):
            return child  # type: ignore[return-value]
        found = _find_first_button_grid(child)
        if found is not None:
            return found
    return None


def _widget_class(widget: tk.Widget) -> str:
    try:
        return str(widget.winfo_class())
    except Exception:
        return ""
