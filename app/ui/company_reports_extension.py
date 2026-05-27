from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Type

from app.analytics.fundamental_analysis import (
    analyze_company_facts,
    format_earnings_snapshot,
    format_fundamental_analysis,
)
from app.data.sec_edgar import SecEdgarClient, SecFiling, cache_status_line, normalize_ticker

REPORT_FORMS = ("10-K", "10-Q", "8-K")


def install_company_reports_extension(app_cls: Type[tk.Tk]) -> None:
    """Add public-company filing and fundamentals actions to the cockpit."""

    original_build_order_panel = app_cls._build_order_panel

    def _build_order_panel_with_company_reports(self: tk.Tk, parent: ttk.Frame) -> None:
        original_build_order_panel(self, parent)
        _build_company_reports_panel(self, parent)

    app_cls._build_order_panel = _build_order_panel_with_company_reports  # type: ignore[method-assign]
    app_cls.show_company_filings = _show_company_filings  # type: ignore[method-assign]
    app_cls.show_earnings_snapshot = _show_earnings_snapshot  # type: ignore[method-assign]
    app_cls.show_fundamental_analysis = _show_fundamental_analysis  # type: ignore[method-assign]


def _build_company_reports_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    panel = ttk.LabelFrame(parent, text="Company Reports", style="Card.TLabelframe")
    panel.pack(fill=tk.X, pady=(12, 0))

    buttons = ttk.Frame(panel)
    buttons.pack(fill=tk.X)
    ttk.Button(buttons, text="Filings", command=self.show_company_filings).pack(side=tk.LEFT)
    ttk.Button(buttons, text="Earnings Snapshot", command=self.show_earnings_snapshot).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(buttons, text="Fundamental Analysis", command=self.show_fundamental_analysis).pack(side=tk.LEFT, padx=(8, 0))

    ttk.Label(
        panel,
        text="Uses public EDGAR/data.sec.gov filings and XBRL facts. No Schwab auth required.",
        wraplength=430,
        style="Subtle.TLabel",
    ).pack(anchor=tk.W, pady=(8, 0))


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
