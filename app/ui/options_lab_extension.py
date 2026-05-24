from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Type

from app.analytics.technical_analysis import (
    analyze_candles,
    candles_from_price_history,
    simple_moving_average,
)
from app.analytics.trade_setup import calculate_support_resistance
from app.ui.options_lab import build_options_lab_tab, run_options_what_if
from app.ui.polished_theme import _make_paned


def install_options_lab_extension(app_cls: Type[tk.Tk]) -> None:
    """Add the Options What-If Lab and Schwab/Hyperliquid cockpit layout."""

    app_cls._build_layout = _build_layout_with_options_lab  # type: ignore[method-assign]
    app_cls.load_options_lab_technical_context = _load_options_lab_technical_context  # type: ignore[attr-defined]
    app_cls.use_current_cockpit_source_portfolio = _use_current_cockpit_source_portfolio  # type: ignore[attr-defined]


def _build_layout_with_options_lab(self: tk.Tk) -> None:
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
            "Schwab/current portfolio powers the Cockpit and Options What-If Lab. "
            "Hyperliquid can be synced from the Trade Planner."
        ),
        style="Subtle.TLabel",
        wraplength=1180,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))

    buttons = ttk.Frame(panel, style="Panel.TFrame")
    buttons.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    for column in range(3):
        buttons.columnconfigure(column, weight=1, uniform="sources")

    ttk.Button(buttons, text="Connect Schwab", command=self.connect_schwab).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Refresh Schwab", command=lambda: _refresh_current_source(self)).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Use Schwab/Current", command=self.use_current_cockpit_source_portfolio, style="Accent.TButton").grid(row=0, column=2, sticky="ew")

    status = ttk.Frame(panel, style="Panel.TFrame")
    status.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    status.columnconfigure(0, weight=1)
    ttk.Label(status, textvariable=self.active_portfolio_source_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew")


def _build_options_lab_market_loader(self: tk.Tk, parent: ttk.Frame) -> None:
    loader = ttk.LabelFrame(parent, text="Optional Schwab Technical Context Loader", style="Card.TLabelframe")
    loader.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    loader.columnconfigure(0, weight=1)

    ttk.Label(
        loader,
        text=(
            "Pulls recent daily Schwab candles for the sandbox symbol and fills underlying price, RSI, "
            "20/50/200 SMA, ATR %, support, and resistance. No order preview or order submission is made."
        ),
        style="Subtle.TLabel",
        wraplength=860,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Button(
        loader,
        text="Load Schwab Technicals",
        command=self.load_options_lab_technical_context,
        style="Accent.TButton",
    ).grid(row=0, column=1, sticky="e")


def _capture_current_source_portfolio(self: tk.Tk) -> None:
    try:
        self.cockpit_source_portfolio = self.broker.get_portfolio()
        self.cockpit_source_message = getattr(self.broker, "source_message", "Current cockpit portfolio")
    except Exception:
        return


def _refresh_current_source(self: tk.Tk) -> None:
    try:
        self.refresh_schwab_account()
    except Exception:
        self.refresh_portfolio()
    _capture_current_source_portfolio(self)
    self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
    _sync_options_values_from_active_portfolio(self)


def _use_current_cockpit_source_portfolio(self: tk.Tk) -> None:
    try:
        if self.cockpit_source_portfolio is None:
            _capture_current_source_portfolio(self)
        if self.cockpit_source_portfolio is None:
            raise RuntimeError("No current cockpit source portfolio is available yet.")

        self.broker.set_portfolio(self.cockpit_source_portfolio, self.cockpit_source_message)
        self.refresh_portfolio()
        self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
        _sync_options_values_from_active_portfolio(self)
    except Exception as exc:
        messagebox.showerror("Use current portfolio failed", str(exc))


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


def _load_options_lab_technical_context(self: tk.Tk) -> None:
    symbol = self.options_symbol_var.get().strip().upper()
    if not symbol:
        messagebox.showerror("Options lab technicals failed", "Enter a symbol first.")
        return

    try:
        session = self._authorize_schwab_session()
        if session is None:
            return

        status_code, payload = session.get_price_history(
            symbol,
            period_type="year",
            period=1,
            frequency_type="daily",
            frequency=1,
            need_extended_hours_data=False,
        )
        if status_code != 200:
            raise RuntimeError(f"Schwab daily price history returned HTTP {status_code}: {payload}")

        candles = candles_from_price_history(payload)
        report = analyze_candles(symbol, candles)
        levels = calculate_support_resistance(candles, lookback=50)
        closes = [candle.close for candle in candles]
        sma_200 = simple_moving_average(closes, 200)
        atr_percent = _average_true_range_percent(candles, period=14)

        self.options_underlying_price_var.set(f"{report.latest_close:.2f}")
        if report.rsi is not None:
            self.options_rsi_var.set(f"{report.rsi:.1f}")
        if report.sma_fast is not None:
            self.options_sma_20_var.set(f"{report.sma_fast:.2f}")
        if report.sma_slow is not None:
            self.options_sma_50_var.set(f"{report.sma_slow:.2f}")
        if sma_200 is not None:
            self.options_sma_200_var.set(f"{sma_200:.2f}")
        if levels.support is not None:
            self.options_support_var.set(f"{levels.support:.2f}")
        if levels.resistance is not None:
            self.options_resistance_var.set(f"{levels.resistance:.2f}")
        if atr_percent is not None:
            self.options_atr_var.set(f"{atr_percent:.2f}")

        self.schwab_status_var.set("Schwab session: connected")
        run_options_what_if(self)
    except Exception as exc:
        messagebox.showerror("Options lab technicals failed", str(exc))


def _average_true_range_percent(candles, *, period: int) -> float | None:
    if len(candles) <= period:
        return None

    true_ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        true_range = max(
            candle.high - candle.low,
            abs(candle.high - previous_close),
            abs(candle.low - previous_close),
        )
        true_ranges.append(true_range)
        previous_close = candle.close

    recent_ranges = true_ranges[-period:]
    latest_close = candles[-1].close
    if not recent_ranges or latest_close <= 0:
        return None
    return (sum(recent_ranges) / len(recent_ranges) / latest_close) * 100
