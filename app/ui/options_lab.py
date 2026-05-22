from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk, messagebox


STRATEGIES = [
    "Stock",
    "Long Call",
    "Long Put",
    "Covered Call",
    "Cash-Secured Put",
    "Vertical Debit Spread",
    "Vertical Credit Spread",
]


@dataclass(frozen=True)
class OptionsScenario:
    symbol: str
    strategy: str
    underlying_price: float
    quantity: float
    contracts: int
    strike: float
    short_strike: float
    premium: float
    credit: float
    portfolio_value: float
    cash_available: float
    initial_margin_rate: float
    maintenance_margin_rate: float
    stop_price: float | None
    target_price: float | None
    atr_percent: float
    rsi: float
    sma_20: float
    sma_50: float
    sma_200: float
    support: float
    resistance: float


def build_options_lab_tab(app: tk.Tk, parent: ttk.Frame) -> None:
    """Build a safe, hypothetical options/stock what-if tab.

    This tab deliberately does not place, preview, or recommend trades. It only models
    approximate risk, margin, technical context, and portfolio impact for a user-entered
    scenario.
    """

    _init_options_vars(app)

    parent.columnconfigure(0, weight=2)
    parent.columnconfigure(1, weight=3)
    parent.rowconfigure(1, weight=1)

    _build_options_disclaimer(parent)
    _build_scenario_builder(app, parent)
    _build_options_output(app, parent)
    run_options_what_if(app)


def _init_options_vars(app: tk.Tk) -> None:
    app.options_symbol_var = tk.StringVar(value="NVDA")
    app.options_strategy_var = tk.StringVar(value="Long Call")
    app.options_underlying_price_var = tk.StringVar(value="200.00")
    app.options_quantity_var = tk.StringVar(value="2")
    app.options_contracts_var = tk.StringVar(value="1")
    app.options_strike_var = tk.StringVar(value="205.00")
    app.options_short_strike_var = tk.StringVar(value="215.00")
    app.options_premium_var = tk.StringVar(value="8.20")
    app.options_credit_var = tk.StringVar(value="3.00")
    app.options_portfolio_value_var = tk.StringVar(value="25000.00")
    app.options_cash_available_var = tk.StringVar(value="10000.00")
    app.options_initial_margin_var = tk.StringVar(value="50")
    app.options_maintenance_margin_var = tk.StringVar(value="30")
    app.options_stop_price_var = tk.StringVar(value="190.00")
    app.options_target_price_var = tk.StringVar(value="230.00")
    app.options_atr_var = tk.StringVar(value="4.0")
    app.options_rsi_var = tk.StringVar(value="58")
    app.options_sma_20_var = tk.StringVar(value="198.00")
    app.options_sma_50_var = tk.StringVar(value="192.00")
    app.options_sma_200_var = tk.StringVar(value="175.00")
    app.options_support_var = tk.StringVar(value="190.00")
    app.options_resistance_var = tk.StringVar(value="220.00")


def _build_options_disclaimer(parent: ttk.Frame) -> None:
    banner = ttk.LabelFrame(parent, text="Options What-If Lab", style="Card.TLabelframe")
    banner.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
    ttk.Label(
        banner,
        text=(
            "Hypothetical scenario modeling only. This tab estimates risk, margin usage, "
            "technical context, and portfolio impact. It does not generate trade recommendations, "
            "submit orders, or replace broker margin requirements."
        ),
        wraplength=1020,
        style="Subtle.TLabel",
    ).pack(anchor=tk.W)


def _build_scenario_builder(app: tk.Tk, parent: ttk.Frame) -> None:
    left = ttk.Frame(parent, padding=(0, 0, 10, 0))
    left.grid(row=1, column=0, sticky="nsew")
    left.columnconfigure(0, weight=1)

    scenario = ttk.LabelFrame(left, text="Scenario Builder", style="Card.TLabelframe")
    scenario.grid(row=0, column=0, sticky="ew")
    scenario.columnconfigure(1, weight=1)
    scenario.columnconfigure(3, weight=1)

    _grid_pair(scenario, 0, "Symbol", ttk.Entry(scenario, textvariable=app.options_symbol_var), "Strategy", ttk.Combobox(scenario, textvariable=app.options_strategy_var, values=STRATEGIES, state="readonly"))
    _grid_pair(scenario, 1, "Underlying", ttk.Entry(scenario, textvariable=app.options_underlying_price_var), "Shares", ttk.Entry(scenario, textvariable=app.options_quantity_var))
    _grid_pair(scenario, 2, "Contracts", ttk.Entry(scenario, textvariable=app.options_contracts_var), "Long strike", ttk.Entry(scenario, textvariable=app.options_strike_var))
    _grid_pair(scenario, 3, "Short strike", ttk.Entry(scenario, textvariable=app.options_short_strike_var), "Premium/debit", ttk.Entry(scenario, textvariable=app.options_premium_var))
    _grid_pair(scenario, 4, "Credit", ttk.Entry(scenario, textvariable=app.options_credit_var), "Cash available", ttk.Entry(scenario, textvariable=app.options_cash_available_var))
    _grid_pair(scenario, 5, "Portfolio value", ttk.Entry(scenario, textvariable=app.options_portfolio_value_var), "Initial margin %", ttk.Entry(scenario, textvariable=app.options_initial_margin_var))
    _grid_pair(scenario, 6, "Maintenance %", ttk.Entry(scenario, textvariable=app.options_maintenance_margin_var), "Stop price", ttk.Entry(scenario, textvariable=app.options_stop_price_var))
    _grid_pair(scenario, 7, "Target price", ttk.Entry(scenario, textvariable=app.options_target_price_var), "ATR %", ttk.Entry(scenario, textvariable=app.options_atr_var))

    technical = ttk.LabelFrame(left, text="Manual Technical Context", style="Card.TLabelframe")
    technical.grid(row=1, column=0, sticky="ew", pady=(12, 0))
    technical.columnconfigure(1, weight=1)
    technical.columnconfigure(3, weight=1)

    _grid_pair(technical, 0, "RSI", ttk.Entry(technical, textvariable=app.options_rsi_var), "20 SMA", ttk.Entry(technical, textvariable=app.options_sma_20_var))
    _grid_pair(technical, 1, "50 SMA", ttk.Entry(technical, textvariable=app.options_sma_50_var), "200 SMA", ttk.Entry(technical, textvariable=app.options_sma_200_var))
    _grid_pair(technical, 2, "Support", ttk.Entry(technical, textvariable=app.options_support_var), "Resistance", ttk.Entry(technical, textvariable=app.options_resistance_var))

    buttons = ttk.Frame(left)
    buttons.grid(row=2, column=0, sticky="ew", pady=(12, 0))
    ttk.Button(buttons, text="Run What-If", command=lambda: run_options_what_if(app), style="Accent.TButton").pack(side=tk.LEFT)
    ttk.Button(buttons, text="Load Portfolio Values", command=lambda: load_options_portfolio_values(app)).pack(side=tk.LEFT, padx=(8, 0))

    notes = ttk.LabelFrame(left, text="Safety Protocols", style="Card.TLabelframe")
    notes.grid(row=3, column=0, sticky="ew", pady=(12, 0))
    ttk.Label(
        notes,
        text=(
            "The checklist flags oversized portfolio risk, buying-power pressure, stops inside normal ATR noise, "
            "and undefined-risk structures. Keep this as a sandbox before using broker previewOrder."
        ),
        wraplength=460,
        style="Subtle.TLabel",
    ).pack(anchor=tk.W)


def _build_options_output(app: tk.Tk, parent: ttk.Frame) -> None:
    right = ttk.Frame(parent, padding=(10, 0, 0, 0))
    right.grid(row=1, column=1, sticky="nsew")
    right.rowconfigure(1, weight=1)
    right.columnconfigure(0, weight=1)

    metrics = ttk.LabelFrame(right, text="Risk + Margin Snapshot", style="Card.TLabelframe")
    metrics.grid(row=0, column=0, sticky="ew")
    metrics.columnconfigure((0, 1, 2), weight=1)

    app.options_max_loss_label = _metric(metrics, "Max Loss", 0, 0)
    app.options_max_profit_label = _metric(metrics, "Max Profit", 0, 1)
    app.options_breakeven_label = _metric(metrics, "Breakeven", 0, 2)
    app.options_margin_label = _metric(metrics, "Buying Power Used", 2, 0)
    app.options_portfolio_risk_label = _metric(metrics, "Portfolio Risk", 2, 1)
    app.options_reward_risk_label = _metric(metrics, "Reward/Risk", 2, 2)

    output = ttk.LabelFrame(right, text="Scenario Analysis", style="Card.TLabelframe")
    output.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
    output.rowconfigure(0, weight=1)
    output.columnconfigure(0, weight=1)

    app.options_output_text = tk.Text(output, height=28, wrap=tk.WORD, font=("Consolas", 10), padx=10, pady=10)
    app.options_output_text.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(output, orient=tk.VERTICAL, command=app.options_output_text.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    app.options_output_text.configure(yscrollcommand=scrollbar.set)


def _grid_pair(parent: ttk.Frame, row: int, label_a: str, widget_a: tk.Widget, label_b: str, widget_b: tk.Widget) -> None:
    ttk.Label(parent, text=label_a).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
    widget_a.grid(row=row, column=1, sticky="ew", padx=(0, 14), pady=5)
    ttk.Label(parent, text=label_b).grid(row=row, column=2, sticky="w", padx=(0, 8), pady=5)
    widget_b.grid(row=row, column=3, sticky="ew", pady=5)


def _metric(parent: ttk.Frame, title: str, row: int, column: int) -> ttk.Label:
    ttk.Label(parent, text=title, style="Subtle.TLabel").grid(row=row, column=column, sticky="w")
    label = ttk.Label(parent, text="--", font=("Segoe UI", 14, "bold"))
    label.grid(row=row + 1, column=column, sticky="w", pady=(2, 10))
    return label


def load_options_portfolio_values(app: tk.Tk) -> None:
    try:
        portfolio = app.broker.get_portfolio()
    except Exception as exc:
        messagebox.showerror("Portfolio load failed", str(exc))
        return

    app.options_cash_available_var.set(f"{portfolio.cash:.2f}")
    app.options_portfolio_value_var.set(f"{portfolio.total_value:.2f}")
    run_options_what_if(app)


def run_options_what_if(app: tk.Tk) -> None:
    try:
        scenario = _parse_scenario(app)
        analysis = _analyze_scenario(scenario)
    except Exception as exc:
        messagebox.showerror("Options what-if failed", str(exc))
        return

    _update_metric_labels(app, analysis)
    _set_options_text(app, _format_analysis(scenario, analysis))


def _parse_scenario(app: tk.Tk) -> OptionsScenario:
    def required_float(value: str, field: str) -> float:
        try:
            return float(value.strip().replace(",", ""))
        except ValueError as exc:
            raise ValueError(f"{field} must be a number.") from exc

    def optional_float(value: str) -> float | None:
        value = value.strip().replace(",", "")
        return float(value) if value else None

    return OptionsScenario(
        symbol=app.options_symbol_var.get().strip().upper() or "UNKNOWN",
        strategy=app.options_strategy_var.get(),
        underlying_price=required_float(app.options_underlying_price_var.get(), "Underlying"),
        quantity=required_float(app.options_quantity_var.get(), "Shares"),
        contracts=max(0, int(required_float(app.options_contracts_var.get(), "Contracts"))),
        strike=required_float(app.options_strike_var.get(), "Long strike"),
        short_strike=required_float(app.options_short_strike_var.get(), "Short strike"),
        premium=required_float(app.options_premium_var.get(), "Premium/debit"),
        credit=required_float(app.options_credit_var.get(), "Credit"),
        portfolio_value=max(required_float(app.options_portfolio_value_var.get(), "Portfolio value"), 0.01),
        cash_available=required_float(app.options_cash_available_var.get(), "Cash available"),
        initial_margin_rate=required_float(app.options_initial_margin_var.get(), "Initial margin %") / 100,
        maintenance_margin_rate=required_float(app.options_maintenance_margin_var.get(), "Maintenance %") / 100,
        stop_price=optional_float(app.options_stop_price_var.get()),
        target_price=optional_float(app.options_target_price_var.get()),
        atr_percent=max(required_float(app.options_atr_var.get(), "ATR %"), 0) / 100,
        rsi=required_float(app.options_rsi_var.get(), "RSI"),
        sma_20=required_float(app.options_sma_20_var.get(), "20 SMA"),
        sma_50=required_float(app.options_sma_50_var.get(), "50 SMA"),
        sma_200=required_float(app.options_sma_200_var.get(), "200 SMA"),
        support=required_float(app.options_support_var.get(), "Support"),
        resistance=required_float(app.options_resistance_var.get(), "Resistance"),
    )


def _analyze_scenario(s: OptionsScenario) -> dict:
    strategy = s.strategy
    contract_multiplier = 100
    contracts = max(s.contracts, 1)
    spread_width = abs(s.short_strike - s.strike) * contracts * contract_multiplier
    premium_paid = s.premium * contracts * contract_multiplier
    credit_received = s.credit * contracts * contract_multiplier
    share_notional = s.quantity * s.underlying_price

    if strategy == "Stock":
        max_loss = share_notional
        max_profit = None
        breakeven = s.underlying_price
        margin_required = share_notional * s.initial_margin_rate
    elif strategy == "Long Call":
        max_loss = premium_paid
        max_profit = None
        breakeven = s.strike + s.premium
        margin_required = premium_paid
    elif strategy == "Long Put":
        max_loss = premium_paid
        max_profit = max((s.strike - s.premium) * contracts * contract_multiplier, 0)
        breakeven = s.strike - s.premium
        margin_required = premium_paid
    elif strategy == "Covered Call":
        max_loss = max(share_notional - credit_received, 0)
        max_profit = max((s.strike - s.underlying_price) * s.quantity + credit_received, credit_received)
        breakeven = s.underlying_price - (credit_received / max(s.quantity, 1))
        margin_required = share_notional * s.initial_margin_rate
    elif strategy == "Cash-Secured Put":
        max_loss = max((s.strike * contracts * contract_multiplier) - credit_received, 0)
        max_profit = credit_received
        breakeven = s.strike - s.credit
        margin_required = s.strike * contracts * contract_multiplier
    elif strategy == "Vertical Debit Spread":
        max_loss = premium_paid
        max_profit = max(spread_width - premium_paid, 0)
        breakeven = min(s.strike, s.short_strike) + s.premium
        margin_required = max_loss
    else:
        max_loss = max(spread_width - credit_received, 0)
        max_profit = credit_received
        breakeven = min(s.strike, s.short_strike) + s.credit
        margin_required = max_loss

    stop_loss = _estimate_price_pnl(s, s.stop_price) if s.stop_price is not None else None
    target_profit = _estimate_price_pnl(s, s.target_price) if s.target_price is not None else None
    reward_risk = None
    if stop_loss is not None and stop_loss < 0 and target_profit is not None and target_profit > 0:
        reward_risk = target_profit / abs(stop_loss)

    price_rows = []
    for move in [-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20]:
        price = s.underlying_price * (1 + move)
        pnl = _estimate_price_pnl(s, price)
        price_rows.append((move, price, pnl, pnl / s.portfolio_value))

    technical = _technical_context(s)
    checklist = _safety_checklist(s, max_loss, margin_required, stop_loss)

    return {
        "max_loss": max_loss,
        "max_profit": max_profit,
        "breakeven": breakeven,
        "margin_required": margin_required,
        "portfolio_risk": max_loss / s.portfolio_value,
        "buying_power_after": s.cash_available - margin_required,
        "stop_loss": stop_loss,
        "target_profit": target_profit,
        "reward_risk": reward_risk,
        "price_rows": price_rows,
        "technical": technical,
        "checklist": checklist,
    }


def _estimate_price_pnl(s: OptionsScenario, underlying_price: float | None) -> float:
    if underlying_price is None:
        return 0.0

    contracts = max(s.contracts, 1)
    multiplier = 100
    strategy = s.strategy

    if strategy == "Stock":
        return (underlying_price - s.underlying_price) * s.quantity
    if strategy == "Long Call":
        value = max(underlying_price - s.strike, 0) * contracts * multiplier
        return value - (s.premium * contracts * multiplier)
    if strategy == "Long Put":
        value = max(s.strike - underlying_price, 0) * contracts * multiplier
        return value - (s.premium * contracts * multiplier)
    if strategy == "Covered Call":
        stock_pnl = (underlying_price - s.underlying_price) * s.quantity
        short_call_pnl = s.credit * contracts * multiplier - max(underlying_price - s.strike, 0) * contracts * multiplier
        return stock_pnl + short_call_pnl
    if strategy == "Cash-Secured Put":
        return s.credit * contracts * multiplier - max(s.strike - underlying_price, 0) * contracts * multiplier

    long_strike = min(s.strike, s.short_strike)
    short_strike = max(s.strike, s.short_strike)
    intrinsic_spread = min(max(underlying_price - long_strike, 0), short_strike - long_strike) * contracts * multiplier

    if strategy == "Vertical Debit Spread":
        return intrinsic_spread - (s.premium * contracts * multiplier)
    return (s.credit * contracts * multiplier) - intrinsic_spread


def _technical_context(s: OptionsScenario) -> list[str]:
    notes: list[str] = []
    if s.underlying_price > s.sma_20 > s.sma_50 > s.sma_200:
        notes.append("Trend: bullish stack — price above 20/50/200 SMA.")
    elif s.underlying_price < s.sma_20 < s.sma_50 < s.sma_200:
        notes.append("Trend: bearish stack — price below 20/50/200 SMA.")
    else:
        notes.append("Trend: mixed — moving averages are not cleanly stacked.")

    if s.rsi >= 70:
        notes.append("Momentum: RSI is elevated; scenario may be vulnerable to pullback/chop.")
    elif s.rsi <= 30:
        notes.append("Momentum: RSI is depressed; bearish follow-through may be stretched.")
    else:
        notes.append("Momentum: RSI is in a neutral operating zone.")

    atr_dollars = s.underlying_price * s.atr_percent
    notes.append(f"Volatility: ATR input implies roughly ${atr_dollars:,.2f} of normal price movement.")
    notes.append(f"Levels: support near ${s.support:,.2f}, resistance near ${s.resistance:,.2f}.")
    return notes


def _safety_checklist(s: OptionsScenario, max_loss: float, margin_required: float, stop_loss: float | None) -> list[tuple[str, str]]:
    checks: list[tuple[str, str]] = []
    risk_pct = max_loss / s.portfolio_value
    buying_power_pct = margin_required / max(s.cash_available, 0.01)
    atr_dollars = s.underlying_price * s.atr_percent

    checks.append(("OK" if risk_pct <= 0.02 else "WARN", f"Max loss equals {risk_pct:.1%} of portfolio value."))
    checks.append(("OK" if buying_power_pct <= 0.25 else "WARN", f"Buying-power usage equals {buying_power_pct:.1%} of available cash."))

    if stop_loss is None:
        checks.append(("WARN", "No stop-loss price entered for path-risk modeling."))
    else:
        stop_distance = abs(s.underlying_price - s.stop_price) if s.stop_price is not None else 0
        checks.append(("OK" if stop_distance >= atr_dollars else "WARN", "Stop is outside normal ATR noise." if stop_distance >= atr_dollars else "Stop is inside one ATR; normal noise may trigger it."))

    defined_risk = s.strategy not in {"Stock", "Covered Call"}
    checks.append(("OK" if defined_risk else "INFO", "Defined-risk options structure." if defined_risk else "Equity/covered stock exposure can remain large."))
    return checks


def _update_metric_labels(app: tk.Tk, analysis: dict) -> None:
    app.options_max_loss_label.configure(text=_money(analysis["max_loss"]))
    app.options_max_profit_label.configure(text="Unlimited/variable" if analysis["max_profit"] is None else _money(analysis["max_profit"]))
    app.options_breakeven_label.configure(text=_money(analysis["breakeven"]))
    app.options_margin_label.configure(text=_money(analysis["margin_required"]))
    app.options_portfolio_risk_label.configure(text=f"{analysis['portfolio_risk']:.1%}")
    reward_risk = analysis["reward_risk"]
    app.options_reward_risk_label.configure(text="--" if reward_risk is None else f"{reward_risk:.2f}x")


def _format_analysis(s: OptionsScenario, analysis: dict) -> str:
    lines = [
        "OPTIONS WHAT-IF ANALYSIS",
        "========================",
        "",
        f"Symbol: {s.symbol}",
        f"Strategy: {s.strategy}",
        f"Underlying price: {_money(s.underlying_price)}",
        f"Contracts: {s.contracts}",
        f"Shares: {s.quantity:g}",
        "",
        "Risk + Margin:",
        f"- Max loss: {_money(analysis['max_loss'])}",
        f"- Max profit: {'Unlimited/variable' if analysis['max_profit'] is None else _money(analysis['max_profit'])}",
        f"- Breakeven: {_money(analysis['breakeven'])}",
        f"- Estimated buying power used: {_money(analysis['margin_required'])}",
        f"- Buying power after scenario: {_money(analysis['buying_power_after'])}",
        f"- Portfolio risk: {analysis['portfolio_risk']:.1%}",
        "",
        "Stop / Target Path:",
        f"- Stop-loss P/L: {'--' if analysis['stop_loss'] is None else _money(analysis['stop_loss'])}",
        f"- Target P/L: {'--' if analysis['target_profit'] is None else _money(analysis['target_profit'])}",
        f"- Reward/Risk: {'--' if analysis['reward_risk'] is None else f'{analysis['reward_risk']:.2f}x'}",
        "",
        "Technical Context:",
    ]
    lines.extend(f"- {note}" for note in analysis["technical"])

    lines.extend(["", "Safety Checklist:"])
    for status, message in analysis["checklist"]:
        icon = {"OK": "✓", "WARN": "⚠", "INFO": "i"}.get(status, "-")
        lines.append(f"{icon} {message}")

    lines.extend(["", "Scenario Table:", "Move      Price        Est. P/L      Portfolio Impact", "----------------------------------------------------"])
    for move, price, pnl, impact in analysis["price_rows"]:
        lines.append(f"{move:>+5.0%}   {_money(price):>10}   {_money(pnl):>12}   {impact:>8.1%}")

    lines.extend([
        "",
        "Notes:",
        "- Option values are simplified expiration-style estimates, not live Greeks/IV marks.",
        "- Margin is an approximation for planning only; broker requirements can differ.",
        "- This is a what-if sandbox, not a trade recommendation.",
    ])
    return "\n".join(lines)


def _set_options_text(app: tk.Tk, content: str) -> None:
    app.options_output_text.configure(state=tk.NORMAL)
    app.options_output_text.delete("1.0", tk.END)
    app.options_output_text.insert(tk.END, content)
    app.options_output_text.configure(state=tk.DISABLED)


def _money(value: float) -> str:
    return f"${value:,.2f}"
