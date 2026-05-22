from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Type

from app.analytics.technical_analysis import (
    analyze_candles,
    candles_from_price_history,
    simple_moving_average,
)
from app.analytics.trade_setup import calculate_support_resistance
from app.brokers.plaid.client import PlaidClient
from app.brokers.plaid.investments_adapter import merge_portfolios, portfolio_from_plaid_holdings
from app.brokers.plaid.token_store import clear_plaid_token, load_plaid_token, save_plaid_token
from app.ui.options_lab import build_options_lab_tab, run_options_what_if
from app.ui.polished_theme import _make_paned


def install_options_lab_extension(app_cls: Type[tk.Tk]) -> None:
    """Add the Options What-If Lab as a separate cockpit tab."""

    app_cls._build_layout = _build_layout_with_options_lab  # type: ignore[method-assign]
    app_cls.load_options_lab_technical_context = _load_options_lab_technical_context  # type: ignore[attr-defined]
    app_cls.create_plaid_sandbox_item = _create_plaid_sandbox_item  # type: ignore[attr-defined]
    app_cls.exchange_plaid_public_token = _exchange_plaid_public_token  # type: ignore[attr-defined]
    app_cls.refresh_plaid_holdings = _refresh_plaid_holdings  # type: ignore[attr-defined]
    app_cls.use_combined_schwab_plaid_portfolio = _use_combined_schwab_plaid_portfolio  # type: ignore[attr-defined]
    app_cls.clear_plaid_connection = _clear_plaid_connection  # type: ignore[attr-defined]


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

    body = _make_paned(cockpit_tab, tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True)

    left = ttk.Frame(body, style="Canvas.TFrame")
    right = ttk.Frame(body, style="Canvas.TFrame")
    body.add(left, minsize=560, stretch="always")
    body.add(right, minsize=520, stretch="always")
    self.after_idle(lambda: body.sash_place(0, max(600, int(self.winfo_width() * 0.60)), 0))

    self.plaid_portfolio = None
    self.plaid_source_message = "Plaid: not connected"
    self.plaid_status_var = tk.StringVar(value=self.plaid_source_message)

    self._build_portfolio_panel(left)
    self._build_order_panel(right)
    build_options_lab_tab(self, options_tab)
    _build_options_lab_market_loader(self, options_tab)
    _build_plaid_holdings_loader(self, options_tab)


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


def _build_plaid_holdings_loader(self: tk.Tk, parent: ttk.Frame) -> None:
    loader = ttk.LabelFrame(parent, text="Plaid / Robinhood Holdings", style="Card.TLabelframe")
    loader.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    loader.columnconfigure(0, weight=1)

    ttk.Label(
        loader,
        text=(
            "Read-only Plaid Investments flow for bringing Robinhood-style holdings into the cockpit. "
            "Sandbox creates test data; real accounts require a Plaid Link public token and approved product access."
        ),
        style="Subtle.TLabel",
        wraplength=860,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))

    buttons = ttk.Frame(loader, style="Panel.TFrame")
    buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
    for column in range(5):
        buttons.columnconfigure(column, weight=1)

    ttk.Button(buttons, text="Create Sandbox Item", command=self.create_plaid_sandbox_item).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Paste Public Token", command=self.exchange_plaid_public_token).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Refresh Plaid Holdings", command=self.refresh_plaid_holdings).grid(row=0, column=2, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Use Combined Portfolio", command=self.use_combined_schwab_plaid_portfolio, style="Accent.TButton").grid(row=0, column=3, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Clear Plaid", command=self.clear_plaid_connection).grid(row=0, column=4, sticky="ew")

    ttk.Label(loader, textvariable=self.plaid_status_var, style="Subtle.TLabel").grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))


def _create_plaid_sandbox_item(self: tk.Tk) -> None:
    try:
        client = PlaidClient()
        public_payload = client.create_sandbox_public_token()
        exchange_payload = client.exchange_public_token(public_payload["public_token"])
        save_plaid_token(exchange_payload)
        self.plaid_status_var.set("Plaid sandbox item connected. Click Refresh Plaid Holdings.")
        self.refresh_plaid_holdings()
    except Exception as exc:
        messagebox.showerror("Plaid sandbox failed", str(exc))


def _exchange_plaid_public_token(self: tk.Tk) -> None:
    public_token = simpledialog.askstring(
        "Plaid Public Token",
        "Paste the public_token returned by Plaid Link. Do not paste a Plaid access token here.",
    )
    if not public_token:
        return

    try:
        client = PlaidClient()
        exchange_payload = client.exchange_public_token(public_token)
        save_plaid_token(exchange_payload)
        self.plaid_status_var.set("Plaid public token exchanged. Click Refresh Plaid Holdings.")
        self.refresh_plaid_holdings()
    except Exception as exc:
        messagebox.showerror("Plaid token exchange failed", str(exc))


def _refresh_plaid_holdings(self: tk.Tk) -> None:
    try:
        cached = load_plaid_token()
        if not cached or not cached.get("access_token"):
            raise RuntimeError("No Plaid token found. Create a sandbox item or paste a Plaid public token first.")

        client = PlaidClient()
        payload = client.get_investment_holdings(str(cached["access_token"]))
        portfolio, source_message = portfolio_from_plaid_holdings(payload)
        self.plaid_portfolio = portfolio
        self.plaid_source_message = source_message
        self.plaid_status_var.set(
            f"{source_message}: {len(portfolio.positions)} positions, total value ${portfolio.total_value:,.2f}"
        )
        self._set_preview_text(_format_plaid_report(portfolio, source_message))
    except Exception as exc:
        messagebox.showerror("Plaid holdings refresh failed", str(exc))


def _use_combined_schwab_plaid_portfolio(self: tk.Tk) -> None:
    if self.plaid_portfolio is None:
        self.refresh_plaid_holdings()
    if self.plaid_portfolio is None:
        return

    try:
        current = self.broker.get_portfolio()
        combined = merge_portfolios(current, self.plaid_portfolio)
        self.broker.set_portfolio(combined, f"Combined current cockpit portfolio + {self.plaid_source_message}")
        self.refresh_portfolio()
        if hasattr(self, "options_cash_available_var"):
            self.options_cash_available_var.set(f"{combined.cash:.2f}")
            self.options_portfolio_value_var.set(f"{combined.total_value:.2f}")
            run_options_what_if(self)
        self.plaid_status_var.set(f"Combined portfolio active: ${combined.total_value:,.2f}")
    except Exception as exc:
        messagebox.showerror("Combined portfolio failed", str(exc))


def _clear_plaid_connection(self: tk.Tk) -> None:
    clear_plaid_token()
    self.plaid_portfolio = None
    self.plaid_source_message = "Plaid: not connected"
    self.plaid_status_var.set(self.plaid_source_message)


def _format_plaid_report(portfolio, source_message: str) -> str:
    lines = [
        "PLAID INVESTMENTS HOLDINGS",
        "=========================",
        "",
        source_message,
        f"Cash estimate: ${portfolio.cash:,.2f}",
        f"Positions value: ${portfolio.positions_value:,.2f}",
        f"Total value: ${portfolio.total_value:,.2f}",
        "",
        "Positions:",
    ]
    if not portfolio.positions:
        lines.append("- None returned.")
    else:
        for symbol in sorted(portfolio.positions):
            position = portfolio.positions[symbol]
            lines.append(
                f"- {symbol}: {position.quantity:g} @ ${position.last_price:,.2f} = ${position.market_value:,.2f}"
            )
    lines.extend([
        "",
        "No Plaid order placement exists here. This is read-only holdings import only.",
    ])
    return "\n".join(lines)


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
