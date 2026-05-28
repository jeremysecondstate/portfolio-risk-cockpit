from __future__ import annotations

import tkinter as tk

from app.ui import options_lab
from app.ui.options_core_math import (
    OptionCoreMetrics,
    calculate_core_option_metrics,
    format_core_option_math_lines,
)

_installed = False
_original_analyze_scenario = options_lab._analyze_scenario
_original_format_analysis = options_lab._format_analysis
_VERTICAL_SPREADS = {"Vertical Debit Spread", "Vertical Credit Spread"}


def install_options_core_math_extension() -> None:
    """Make simple option fundamentals the core of the What-If Options Lab."""

    global _installed
    if _installed:
        return

    options_lab._analyze_scenario = _analyze_scenario_with_core_math
    options_lab._format_analysis = _format_analysis_with_core_math
    options_lab._update_order_summary = _update_order_summary_with_core_math
    _installed = True


def _analyze_scenario_with_core_math(s: options_lab.OptionsScenario, app: tk.Tk | None = None) -> dict:
    analysis = _original_analyze_scenario(s, app)
    core = calculate_core_option_metrics(
        stock_price=s.underlying_price,
        strike=s.strike,
        premium=s.premium,
        contracts=s.contracts,
        option_type=s.option_type,
    )
    analysis["core"] = core

    if s.strategy == "Long Call":
        analysis["max_loss"] = core.max_loss_long_option
        analysis["max_profit"] = core.max_profit_long_call
        analysis["breakeven"] = core.call_breakeven
        analysis["margin_required"] = core.contract_cost
    elif s.strategy == "Long Put":
        analysis["max_loss"] = core.max_loss_long_option
        analysis["max_profit"] = core.max_profit_long_put
        analysis["breakeven"] = core.put_breakeven
        analysis["margin_required"] = core.contract_cost

    context = analysis["portfolio_context"]
    analysis["portfolio_risk"] = analysis["max_loss"] / max(context.total_value, 0.01)
    analysis["buying_power_after"] = s.cash_available - analysis["margin_required"]
    return analysis


def _format_analysis_with_core_math(s: options_lab.OptionsScenario, analysis: dict) -> str:
    formatted = _original_format_analysis(s, analysis)
    if s.strategy in _VERTICAL_SPREADS:
        core_block = "\n".join(_format_vertical_spread_math_lines(s, analysis))
    else:
        core = _get_core_metrics(s, analysis)
        core_block = "\n".join(
            format_core_option_math_lines(
                stock_price=s.underlying_price,
                strike=s.strike,
                premium=s.premium,
                contracts=s.contracts,
                metrics=core,
                money_formatter=options_lab._money,
            )
        )
    insertion = f"\n{core_block}\n"
    marker = "\nStrategy math:"
    if marker in formatted:
        return formatted.replace(marker, f"{insertion}{marker}", 1)
    return f"{formatted}\n{insertion}"


def _format_vertical_spread_math_lines(s: options_lab.OptionsScenario, analysis: dict) -> list[str]:
    contracts = max(s.contracts, 1)
    multiplier = 100
    width = abs(s.short_strike - s.strike)
    width_dollars = width * multiplier * contracts
    max_loss = float(analysis.get("max_loss") or 0.0)
    max_profit = analysis.get("max_profit")
    max_profit_text = "--" if max_profit is None else options_lab._money(float(max_profit))
    breakeven = analysis.get("breakeven")
    debit_or_credit = s.premium if s.strategy == "Vertical Debit Spread" else s.credit
    debit_or_credit_label = "Net debit" if s.strategy == "Vertical Debit Spread" else "Net credit"
    breakeven_text = "--" if not isinstance(breakeven, (int, float)) else options_lab._money(float(breakeven))

    lines = [
        "Vertical Spread Math:",
        f"- Structure: buy {s.strike:g} {s.option_type.upper()} / sell {s.short_strike:g} {s.option_type.upper()} {s.expiration}.",
        f"- Long-leg quote: bid/ask/mark {options_lab._format_optional_price(s.bid)} / {options_lab._format_optional_price(s.ask)} / {options_lab._format_optional_price(s.mark)}.",
        f"- Short-leg credit input: {s.credit:.2f} per share.",
        f"- {debit_or_credit_label}: {debit_or_credit:.2f} × {multiplier} × {contracts} = {options_lab._money(debit_or_credit * multiplier * contracts)}.",
        f"- Spread width: {width:.2f} × {multiplier} × {contracts} = {options_lab._money(width_dollars)}.",
        f"- Max loss: {options_lab._money(max_loss)}.",
        f"- Max profit: {max_profit_text}.",
        f"- Breakeven: {breakeven_text}.",
        "- Note: this section uses spread net debit/credit. Long-option intrinsic/time-value math is intentionally not shown for spreads because it would mix one-leg option math with two-leg spread pricing.",
    ]
    return lines


def _update_order_summary_with_core_math(app: tk.Tk, s: options_lab.OptionsScenario, analysis: dict) -> None:
    if not hasattr(app, "options_order_summary_label"):
        return

    if s.strategy in _VERTICAL_SPREADS:
        max_profit = analysis.get("max_profit")
        max_profit_text = "--" if max_profit is None else options_lab._money(float(max_profit))
        summary = (
            f"{s.action.upper()} {s.contracts} {s.symbol} {s.expiration} {s.strike:g}/{s.short_strike:g} "
            f"{s.option_type.upper()} {s.strategy} @ net {s.premium:.2f} {s.order_type} {s.time_in_force} · "
            f"Max loss {options_lab._money(float(analysis['max_loss']))} · "
            f"Max profit {max_profit_text} · "
            f"Breakeven {options_lab._money(float(analysis['breakeven']))}"
        )
        if s.bid is not None or s.ask is not None or s.mark is not None:
            summary += (
                f" · Long bid/ask/mark {options_lab._format_optional_price(s.bid)} / "
                f"{options_lab._format_optional_price(s.ask)} / "
                f"{options_lab._format_optional_price(s.mark)} · Short credit {s.credit:.2f}"
            )
        app.options_order_summary_label.configure(text=summary)
        return

    core = _get_core_metrics(s, analysis)
    summary = (
        f"{s.action.upper()} {s.contracts} {s.symbol} {s.expiration} {s.strike:g} {s.option_type.upper()} "
        f"@ {s.premium:.2f} {s.order_type} {s.time_in_force} · "
        f"Contract cost {options_lab._money(core.contract_cost)} · "
        f"Breakeven {options_lab._money(analysis['breakeven'])} · "
        f"Intrinsic {options_lab._money(core.selected_intrinsic_value)} / Time value {options_lab._money(core.time_value)}"
    )
    if s.bid is not None or s.ask is not None or s.mark is not None:
        summary += (
            f" · Bid {options_lab._format_optional_price(s.bid)} / "
            f"Ask {options_lab._format_optional_price(s.ask)} / "
            f"Mark {options_lab._format_optional_price(s.mark)}"
        )
    app.options_order_summary_label.configure(text=summary)


def _get_core_metrics(s: options_lab.OptionsScenario, analysis: dict) -> OptionCoreMetrics:
    core = analysis.get("core")
    if isinstance(core, OptionCoreMetrics):
        return core
    return calculate_core_option_metrics(
        stock_price=s.underlying_price,
        strike=s.strike,
        premium=s.premium,
        contracts=s.contracts,
        option_type=s.option_type,
    )
