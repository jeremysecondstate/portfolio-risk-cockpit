from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.ui.options_lab import build_options_lab_tab, run_options_what_if
from app.ui.options_lab_extension import (
    _build_hyperliquid_trading_tab,
    _build_schwab_trading_tab,
    _ensure_execution_workspace_vars,
)
from app.ui.polished_theme import _make_paned


def install_account_sources_fix(app_cls: Type[tk.Tk]) -> None:
    """Keep Account Sources state without rendering a large top strip."""
    app_cls._build_layout = _build_layout_without_account_strip  # type: ignore[method-assign]
    app_cls.capture_current_portfolio_source = _capture_current_source_portfolio  # type: ignore[attr-defined]
    app_cls.sync_options_from_active_portfolio = _sync_options_values_from_active_portfolio  # type: ignore[attr-defined]


def _build_layout_without_account_strip(self: tk.Tk) -> None:
    root = ttk.Frame(self, style="Canvas.TFrame", padding=18)
    root.pack(fill=tk.BOTH, expand=True)
    self._build_header(root)

    tabs = ttk.Notebook(root)
    tabs.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

    cockpit_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=0)
    schwab_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    hyperliquid_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    tabs.add(cockpit_tab, text="Cockpit")
    tabs.add(schwab_tab, text="Schwab Trading")
    tabs.add(hyperliquid_tab, text="Hyperliquid Trading")

    self.active_portfolio_source_var = tk.StringVar(value="Active portfolio: current cockpit source")
    self.cockpit_source_portfolio = None
    self.cockpit_source_message = "Current cockpit portfolio"

    body = _make_paned(cockpit_tab, tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True)

    left = ttk.Frame(body, style="Canvas.TFrame")
    right = ttk.Frame(body, style="Canvas.TFrame")
    body.add(left, minsize=560, stretch="always")
    body.add(right, minsize=520, stretch="always")
    self.after_idle(lambda: body.sash_place(0, max(600, int(self.winfo_width() * 0.60)), 0))

    self._build_portfolio_panel(left)
    self._build_order_panel(right)
    _ensure_execution_workspace_vars(self)
    self.after_idle(lambda: _capture_current_source_portfolio(self))

    _build_schwab_trading_tab(self, schwab_tab, tabs, schwab_tab)
    _install_schwab_options_feature(self, schwab_tab)
    _build_hyperliquid_trading_tab(self, hyperliquid_tab)


def _install_schwab_options_feature(self: tk.Tk, schwab_tab: ttk.Frame) -> None:
    """Expose the options lab as an in-tab Schwab feature instead of a top-level tab."""

    stock_widgets = [widget for widget in schwab_tab.grid_slaves(row=1, column=0)]

    options_feature = ttk.Frame(schwab_tab, style="Canvas.TFrame")
    options_feature.columnconfigure(0, weight=1)
    options_feature.rowconfigure(1, weight=1)

    switcher = ttk.LabelFrame(options_feature, text="Schwab Options What-If", style="Card.TLabelframe")
    switcher.grid(row=0, column=0, sticky="ew", pady=(0, 12))
    switcher.columnconfigure(0, weight=1)
    ttk.Label(
        switcher,
        text=(
            "Options are a Schwab-only workflow here. This opens the same risk, margin, "
            "technical context, and portfolio-impact lab without creating a redundant top-level tab."
        ),
        style="Subtle.TLabel",
        wraplength=1080,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))

    options_body = ttk.Frame(options_feature, style="Canvas.TFrame")
    options_body.grid(row=1, column=0, sticky="nsew")
    build_options_lab_tab(self, options_body)
    _build_options_lab_market_loader(self, options_body)

    def show_stock_ticket() -> None:
        options_feature.grid_remove()
        for widget in stock_widgets:
            widget.grid()
        _set_schwab_mode_text(
            self,
            "SCHWAB TRADING WORKSPACE\n"
            "========================\n\n"
            "Use this tab for stocks, ETFs, Schwab previews, order history, and guarded live Schwab actions.\n\n"
            "Use Options What-If when the weekly setup needs calls/puts instead of shares."
        )

    def show_options_feature() -> None:
        for widget in stock_widgets:
            widget.grid_remove()
        options_feature.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        run_options_what_if(self)

    ttk.Button(
        switcher,
        text="Back to Stock / ETF Ticket",
        command=show_stock_ticket,
    ).grid(row=0, column=1, sticky="e")

    self.show_schwab_stock_ticket = show_stock_ticket  # type: ignore[attr-defined]
    self.show_schwab_options_what_if = show_options_feature  # type: ignore[attr-defined]
    options_feature.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
    options_feature.grid_remove()

    for button in _walk_buttons(schwab_tab):
        label = str(button.cget("text"))
        if label == "Open Options Lab" and _inside_labelframe(button, "Schwab Trading Workspace"):
            button.configure(text="Options What-If", command=show_options_feature, style="Accent.TButton")
        elif label == "Order Checklist" and _inside_labelframe(button, "Schwab Actions"):
            button.configure(text="Options What-If", command=show_options_feature, style="Accent.TButton")

    _set_schwab_mode_text(
        self,
        "SCHWAB TRADING WORKSPACE\n"
        "========================\n\n"
        "Use this tab for stocks, ETFs, Schwab previews, order history, and guarded live Schwab actions.\n\n"
        "Use Options What-If when the weekly setup needs calls/puts instead of shares."
    )


def _set_schwab_mode_text(self: tk.Tk, content: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is None:
        return
    try:
        output.configure(state=tk.NORMAL)
        output.delete("1.0", tk.END)
        output.insert(tk.END, content)
        output.configure(state=tk.DISABLED)
    except Exception:
        return


def _walk_buttons(root: tk.Widget):
    for child in root.winfo_children():
        if _widget_class(child) == "TButton":
            yield child
        yield from _walk_buttons(child)


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


def _widget_class(widget: tk.Widget) -> str:
    try:
        return str(widget.winfo_class())
    except Exception:
        return ""


def _build_options_lab_market_loader(self: tk.Tk, parent: ttk.Frame) -> None:
    loader = ttk.LabelFrame(parent, text="Optional Schwab Technical Context Loader", style="Card.TLabelframe")
    loader.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    loader.columnconfigure(0, weight=1)
    ttk.Label(
        loader,
        text="Pulls recent daily Schwab candles for the sandbox symbol and fills technical context. No order action is made.",
        style="Subtle.TLabel",
        wraplength=860,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Button(loader, text="Load Schwab Technicals", command=self.load_options_lab_technical_context, style="Accent.TButton").grid(row=0, column=1, sticky="e")


def _capture_current_source_portfolio(self: tk.Tk) -> None:
    try:
        self.cockpit_source_portfolio = self.broker.get_portfolio()
        self.cockpit_source_message = getattr(self.broker, "source_message", "Current cockpit portfolio")
        if hasattr(self, "active_portfolio_source_var"):
            self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
    except Exception:
        return


def _sync_options_values_from_active_portfolio(self: tk.Tk) -> None:
    if not hasattr(self, "options_cash_available_var"):
        return
    try:
        portfolio = self.broker.get_portfolio()
        self.options_cash_available_var.set(f"{portfolio.cash:.2f}")
        self.options_portfolio_value_var.set(f"{portfolio.total_value:.2f}")
        position = portfolio.get_position(self.options_symbol_var.get())
        if position is not None:
            self.options_underlying_price_var.set(f"{position.last_price:.2f}")
        run_options_what_if(self)
    except Exception:
        return
