from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Type

from app.analytics.fundamental_analysis import (
    analyze_company_facts,
    format_earnings_snapshot,
    format_fundamental_analysis,
)
from app.data.sec_edgar import SecEdgarClient, SecFiling, cache_status_line, normalize_ticker

REPORT_FORMS = ("10-K", "10-Q", "8-K")


def install_company_reports_extension(app_cls: Type[tk.Tk]) -> None:
    """Add public-company filing and fundamentals actions to the visible cockpit workspaces."""

    previous_build_layout = app_cls._build_layout

    def _build_layout_with_company_reports(self: tk.Tk) -> None:
        previous_build_layout(self)
        _schedule_company_report_button_injection(self)

    app_cls._build_layout = _build_layout_with_company_reports  # type: ignore[method-assign]
    app_cls.show_company_filings = _show_company_filings  # type: ignore[method-assign]
    app_cls.show_earnings_snapshot = _show_earnings_snapshot  # type: ignore[method-assign]
    app_cls.show_fundamental_analysis = _show_fundamental_analysis  # type: ignore[method-assign]


def _schedule_company_report_button_injection(self: tk.Tk) -> None:
    # Several cockpit extensions polish the tabs after idle. Run a few times and
    # make injection idempotent so the buttons land in whichever layout is active.
    for delay_ms in (0, 100, 500, 1200):
        self.after(delay_ms, lambda app=self: _inject_company_report_buttons(app))


def _inject_company_report_buttons(self: tk.Tk) -> None:
    _inject_cockpit_trade_planner_buttons(self)
    _inject_schwab_workspace_buttons(self)


def _inject_cockpit_trade_planner_buttons(self: tk.Tk) -> None:
    planning = _find_labelframe(self, "Planning", inside="Trade Planner")
    if planning is None or getattr(planning, "_company_reports_installed", False):
        return

    planning.columnconfigure(0, weight=1)
    planning.columnconfigure(1, weight=1)
    _grid_report_button(planning, 0, 0, "Filings", self.show_company_filings)
    _grid_report_button(planning, 0, 1, "Earnings", self.show_earnings_snapshot)
    _grid_report_button(planning, 2, 0, "Fundamentals", self.show_fundamental_analysis, columnspan=2)
    setattr(planning, "_company_reports_installed", True)


def _inject_schwab_workspace_buttons(self: tk.Tk) -> None:
    actions = _find_labelframe(self, "Schwab Actions", inside="Schwab Stock / ETF Ticket")
    if actions is None or getattr(actions, "_company_reports_installed", False):
        return

    actions.columnconfigure(0, weight=1)
    actions.columnconfigure(1, weight=1)
    actions.columnconfigure(2, weight=1)
    _grid_report_button(
        actions,
        4,
        0,
        "Filings",
        lambda: _run_in_schwab_workspace_output(self, self.show_company_filings),
    )
    _grid_report_button(
        actions,
        4,
        1,
        "Earnings",
        lambda: _run_in_schwab_workspace_output(self, self.show_earnings_snapshot),
    )
    _grid_report_button(
        actions,
        4,
        2,
        "Fundamentals",
        lambda: _run_in_schwab_workspace_output(self, self.show_fundamental_analysis),
    )
    setattr(actions, "_company_reports_installed", True)


def _run_in_schwab_workspace_output(self: tk.Tk, command: Callable[[], None]) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is not None:
        self.preview_text = output
    command()


def _grid_report_button(
    parent: ttk.LabelFrame,
    row: int,
    column: int,
    text: str,
    command: Callable[[], None],
    *,
    columnspan: int = 1,
) -> None:
    ttk.Button(parent, text=text, command=command).grid(
        row=row,
        column=column,
        columnspan=columnspan,
        sticky="ew",
        padx=(0, 6) if column == 0 and columnspan == 1 else 0,
        pady=(4, 6) if row == 0 else (0, 4),
    )


def _show_company_filings(self: tk.Tk) -> None:
    try:
        symbol = _symbol_from_ticket(self)
        client = SecEdgarClient()
        filings = client.recent_filings(symbol, forms=REPORT_FORMS, limit=18)
        self._set_preview_text(_format_filings_report(symbol, filings))
    except Exception as exc:
        messagebox.showerror("Company filings failed", str(exc))


def _show_earnings_snapshot(self: tk.Tk) -> None:
    try:
        symbol = _symbol_from_ticket(self)
        client = SecEdgarClient()
        company, payload = client.get_companyfacts(symbol)
        report = analyze_company_facts(company, payload)
        self._set_preview_text(format_earnings_snapshot(report) + "\n\n" + cache_status_line())
    except Exception as exc:
        messagebox.showerror("Earnings snapshot failed", str(exc))


def _show_fundamental_analysis(self: tk.Tk) -> None:
    try:
        symbol = _symbol_from_ticket(self)
        client = SecEdgarClient()
        company, payload = client.get_companyfacts(symbol)
        report = analyze_company_facts(company, payload)
        self._set_preview_text(format_fundamental_analysis(report) + "\n\n" + cache_status_line())
    except Exception as exc:
        messagebox.showerror("Fundamental analysis failed", str(exc))


def _symbol_from_ticket(self: tk.Tk) -> str:
    return normalize_ticker(self.symbol_var.get())


def _format_filings_report(symbol: str, filings: list[SecFiling]) -> str:
    if not filings:
        return (
            f"COMPANY FILINGS — {symbol}\n"
            f"{'=' * (19 + len(symbol))}\n\n"
            "No recent 10-K, 10-Q, or 8-K filings were found for this symbol.\n\n"
            + cache_status_line()
        )

    company = filings[0].company
    lines = [
        f"COMPANY FILINGS — {company.ticker}",
        "=" * (19 + len(company.ticker)),
        "",
        f"Company: {company.title}",
        f"CIK: {company.cik}",
        "Forms: 10-K, 10-Q, 8-K",
        "",
        "Recent filings:",
    ]
    for filing in filings:
        description = f" — {filing.description}" if filing.description else ""
        lines.extend(
            [
                f"- {filing.form} | filed {filing.filing_date} | period {filing.report_date or '--'}{description}",
                f"  Accession: {filing.accession_number}",
                f"  URL: {filing.filing_url}",
            ]
        )

    lines.extend(
        [
            "",
            "Tip: 8-K filings often contain earnings-release exhibits; 10-Q/10-K filings carry the full financial statements and notes.",
            cache_status_line(),
        ]
    )
    return "\n".join(lines)


def _find_labelframe(root: tk.Widget, title: str, *, inside: str | None = None) -> ttk.LabelFrame | None:
    for child in _walk_widgets(root):
        if _widget_class(child) != "TLabelframe":
            continue
        try:
            if str(child.cget("text")) != title:
                continue
        except Exception:
            continue
        if inside is not None and not _inside_labelframe(child, inside):
            continue
        return child  # type: ignore[return-value]
    return None


def _inside_labelframe(widget: tk.Widget, title: str) -> bool:
    parent = widget.master
    while parent is not None:
        if _widget_class(parent) == "TLabelframe":
            try:
                if str(parent.cget("text")) == title:
                    return True
            except Exception:
                pass
        parent = parent.master
    return False


def _walk_widgets(root: tk.Widget):
    for child in root.winfo_children():
        yield child
        yield from _walk_widgets(child)


def _widget_class(widget: tk.Widget) -> str:
    try:
        return str(widget.winfo_class())
    except Exception:
        return ""
