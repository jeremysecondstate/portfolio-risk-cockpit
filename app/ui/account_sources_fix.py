from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.ui.options_lab import build_options_lab_tab, run_options_what_if
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
    options_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    tabs.add(cockpit_tab, text="Cockpit")
    tabs.add(options_tab, text="Options What-If Lab")

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
    self.after_idle(lambda: _capture_current_source_portfolio(self))

    build_options_lab_tab(self, options_tab)
    _build_options_lab_market_loader(self, options_tab)


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
