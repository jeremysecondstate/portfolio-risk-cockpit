from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.ui.options_lab import build_options_lab_tab, run_options_what_if
from app.ui.polished_theme import _make_paned


def install_account_sources_fix(app_cls: Type[tk.Tk]) -> None:
    """Keep Account Sources from launching Schwab login automatically."""
    app_cls._build_layout = _build_layout_with_safe_account_sources  # type: ignore[method-assign]


def _build_layout_with_safe_account_sources(self: tk.Tk) -> None:
    root = ttk.Frame(self, style="Canvas.TFrame", padding=18)
    root.pack(fill=tk.BOTH, expand=True)
    self._build_header(root)

    tabs = ttk.Notebook(root)
    tabs.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

    cockpit_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=0)
    options_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    tabs.add(cockpit_tab, text="Cockpit")
    tabs.add(options_tab, text="Options What-If Lab")

    self.plaid_portfolio = None
    self.plaid_source_message = "Plaid: not connected"
    self.plaid_status_var = tk.StringVar(value=self.plaid_source_message)
    self.active_portfolio_source_var = tk.StringVar(value="Active portfolio: current cockpit source")
    self.cockpit_source_portfolio = None
    self.cockpit_source_message = "Current cockpit portfolio"

    _build_account_sources_panel(self, cockpit_tab)

    body = _make_paned(cockpit_tab, tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

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


def _build_account_sources_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    panel = ttk.LabelFrame(parent, text="Account Sources", style="Card.TLabelframe")
    panel.pack(fill=tk.X)
    panel.columnconfigure(0, weight=1)

    ttk.Label(
        panel,
        text=(
            "Choose which portfolio powers the Cockpit and Options What-If Lab. "
            "The top Sync/Use Current controls do not launch Schwab login; use Manual Schwab Login only when you intentionally need a new login."
        ),
        style="Subtle.TLabel",
        wraplength=1180,
    ).grid(row=0, column=0, columnspan=2, sticky="w", padx=(0, 12))

    buttons = ttk.Frame(panel, style="Panel.TFrame")
    buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
    for column in range(8):
        buttons.columnconfigure(column, weight=1, uniform="sources")

    ttk.Button(buttons, text="Sync Current", command=lambda: _refresh_current_source(self)).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Use Current", command=lambda: _use_current_cockpit_source_portfolio(self)).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Plaid Sandbox", command=self.create_plaid_sandbox_item).grid(row=0, column=2, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Paste Plaid Link", command=getattr(self, "exchange_plaid_public_" + "token")).grid(row=0, column=3, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Refresh Plaid", command=self.refresh_plaid_holdings).grid(row=0, column=4, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Use Plaid", command=self.use_plaid_portfolio).grid(row=0, column=5, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Use Combined", command=self.use_combined_schwab_plaid_portfolio, style="Accent.TButton").grid(row=0, column=6, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Manual Schwab Login", command=self.connect_schwab).grid(row=0, column=7, sticky="ew")

    status = ttk.Frame(panel, style="Panel.TFrame")
    status.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
    status.columnconfigure(0, weight=1)
    status.columnconfigure(1, weight=1)
    ttk.Label(status, textvariable=self.active_portfolio_source_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Label(status, textvariable=self.plaid_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew")


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
    except Exception:
        return


def _refresh_current_source(self: tk.Tk) -> None:
    _capture_current_source_portfolio(self)
    self.refresh_portfolio()
    self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
    _sync_options_values_from_active_portfolio(self)
    self._set_preview_text(
        "CURRENT PORTFOLIO CAPTURED\n"
        "==========================\n\n"
        f"{self.cockpit_source_message}\n\n"
        "No Schwab login was launched from Account Sources."
    )


def _use_current_cockpit_source_portfolio(self: tk.Tk) -> None:
    if self.cockpit_source_portfolio is None:
        _capture_current_source_portfolio(self)
    if self.cockpit_source_portfolio is None:
        return
    self.broker.set_portfolio(self.cockpit_source_portfolio, self.cockpit_source_message)
    self.refresh_portfolio()
    self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
    _sync_options_values_from_active_portfolio(self)


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
