from __future__ import annotations

import tkinter as tk
from typing import Type

from app.ui import trading_workspace

_ORIGINAL_PARSE_SCENARIO = trading_workspace._parse_scenario
_ORIGINAL_ANALYZE_SCENARIO = trading_workspace._analyze_scenario
_ORIGINAL_ESTIMATE_PRICE_PNL = trading_workspace._estimate_price_pnl
_ORIGINAL_FORMAT_ANALYSIS = trading_workspace._format_analysis


def install_options_what_if_enhancement_extension(app_cls: Type[tk.Tk]) -> None:
    """Enhance Schwab Options with thesis defaults and portfolio-combined scenarios."""

    trading_workspace._parse_scenario = _parse_scenario_with_safe_defaults  # type: ignore[method-assign]
    trading_workspace._estimate_price_pnl = _estimate_price_pnl_with_directional_verticals  # type: ignore[method-assign]
    trading_workspace._analyze_scenario = _analyze_scenario_with_directional_verticals  # type: ignore[method-assign]
    trading_workspace._format_analysis = _format_analysis_with_combined_portfolio  # type: ignore[method-assign]


def _parse_scenario_with_safe_defaults(app: tk.Tk):
    _fill_missing_what_if_defaults(app)
    return _ORIGINAL_PARSE_SCENARIO(app)


def _fill_missing_what_if_defaults(app: tk.Tk) -> None:
    underlying = _first_float(
        _get_var(app, "options_underlying_price_var"),
        _get_var(app, "limit_price_var"),
        _get_var(app, "estimated_price_var"),
    )
    symbol = _get_var(app, "options_symbol_var") or _get_var(app, "symbol_var")
    if underlying is None and symbol:
        try:
            position = app.broker.get_portfolio().get_position(symbol)
            if position is not None:
                underlying = position.last_price
        except Exception:
            pass
    if underlying is None or underlying <= 0:
        underlying = 1.0

    _set_if_blank(app, "options_symbol_var", symbol or "UNKNOWN")
    _set_if_blank(app, "options_underlying_price_var", _format_number(underlying))
    _set_if_blank(app, "options_quantity_var", "100")
    _set_if_blank(app, "options_contracts_var", "1")
    _set_if_blank(app, "options_order_type_var", "LIMIT")
    _set_if_blank(app, "options_tif_var", "Day")
    _set_if_blank(app, "options_credit_var", "0")
    _set_if_blank(app, "options_initial_margin_var", "50")
    _set_if_blank(app, "options_maintenance_margin_var", "30")
    _set_if_blank(app, "options_atr_var", "5")
    _set_if_blank(app, "options_rsi_var", "50")
    _set_if_blank(app, "options_sma_20_var", _format_number(underlying))
    _set_if_blank(app, "options_sma_50_var", _format_number(underlying))
    _set_if_blank(app, "options_sma_200_var", _format_number(underlying))
    _set_if_blank(app, "options_support_var", _format_number(underlying * 0.97))
    _set_if_blank(app, "options_resistance_var", _format_number(underlying * 1.03))

    strike = _first_float(_get_var(app, "options_strike_var")) or underlying
    _set_if_blank(app, "options_strike_var", _format_number(strike))
    _set_if_blank(app, "options_short_strike_var", _format_number(strike))

    premium = _first_float(
        _get_var(app, "options_premium_var"),
        _get_var(app, "options_ask_var"),
        _get_var(app, "options_mark_var"),
    )
    if premium is None:
        premium = 0.01
    _set_if_blank(app, "options_premium_var", _format_number(premium))

    try:
        portfolio = app.broker.get_portfolio()
        _set_if_blank(app, "options_cash_available_var", f"{portfolio.cash:.2f}")
        _set_if_blank(app, "options_portfolio_value_var", f"{portfolio.total_value:.2f}")
    except Exception:
        _set_if_blank(app, "options_cash_available_var", "0")
        _set_if_blank(app, "options_portfolio_value_var", "1")


def _analyze_scenario_with_directional_verticals(s, app: tk.Tk | None = None) -> dict:
    analysis = _ORIGINAL_ANALYZE_SCENARIO(s, app)
    if s.strategy not in {"Vertical Debit Spread", "Vertical Credit Spread"}:
        return analysis

    contracts = max(s.contracts, 1)
    multiplier = 100
    width = abs(s.short_strike - s.strike) * contracts * multiplier
    premium_paid = s.premium * contracts * multiplier
    credit_received = s.credit * contracts * multiplier

    if s.strategy == "Vertical Debit Spread":
        max_loss = premium_paid
        max_profit = max(width - premium_paid, 0)
        breakeven = _vertical_debit_breakeven(s)
        margin_required = max_loss
    else:
        max_loss = max(width - credit_received, 0)
        max_profit = credit_received
        breakeven = _vertical_credit_breakeven(s)
        margin_required = max_loss

    stop_loss = _estimate_price_pnl_with_directional_verticals(s, s.stop_price) if s.stop_price is not None else None
    target_profit = _estimate_price_pnl_with_directional_verticals(s, s.target_price) if s.target_price is not None else None
    reward_risk = None
    if stop_loss is not None and stop_loss < 0 and target_profit is not None and target_profit > 0:
        reward_risk = target_profit / abs(stop_loss)

    portfolio_context = trading_workspace._portfolio_context(s, app, margin_required, max_loss)
    portfolio_risk = max_loss / max(portfolio_context.total_value, 0.01)
    price_rows = []
    for move in [-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20]:
        price = s.underlying_price * (1 + move)
        pnl = _estimate_price_pnl_with_directional_verticals(s, price)
        price_rows.append((move, price, pnl, pnl / max(portfolio_context.total_value, 0.01)))

    technical = trading_workspace._technical_context(s)
    checklist = trading_workspace._safety_checklist(s, max_loss, margin_required, stop_loss, portfolio_context)
    analysis.update(
        {
            "max_loss": max_loss,
            "max_profit": max_profit,
            "breakeven": breakeven,
            "margin_required": margin_required,
            "portfolio_risk": portfolio_risk,
            "buying_power_after": s.cash_available - margin_required,
            "stop_loss": stop_loss,
            "target_profit": target_profit,
            "reward_risk": reward_risk,
            "price_rows": price_rows,
            "technical": technical,
            "checklist": checklist,
            "portfolio_context": portfolio_context,
        }
    )
    return analysis


def _estimate_price_pnl_with_directional_verticals(s, underlying_price: float | None) -> float:
    if underlying_price is None or s.strategy not in {"Vertical Debit Spread", "Vertical Credit Spread"}:
        return _ORIGINAL_ESTIMATE_PRICE_PNL(s, underlying_price)

    contracts = max(s.contracts, 1)
    multiplier = 100
    low_strike = min(s.strike, s.short_strike)
    high_strike = max(s.strike, s.short_strike)
    width = high_strike - low_strike

    if str(s.option_type).lower().startswith("put"):
        intrinsic_spread = min(max(high_strike - underlying_price, 0), width) * contracts * multiplier
    else:
        intrinsic_spread = min(max(underlying_price - low_strike, 0), width) * contracts * multiplier

    if s.strategy == "Vertical Debit Spread":
        return intrinsic_spread - (s.premium * contracts * multiplier)
    return (s.credit * contracts * multiplier) - intrinsic_spread


def _format_analysis_with_combined_portfolio(s, analysis: dict) -> str:
    base = _ORIGINAL_FORMAT_ANALYSIS(s, analysis)
    insertion = _combined_portfolio_section(s, analysis)
    marker = "\nNotes:\n"
    if marker in base:
        return base.replace(marker, f"\n{insertion}\nNotes:\n", 1)
    return base + "\n\n" + insertion


def _combined_portfolio_section(s, analysis: dict) -> str:
    context = analysis["portfolio_context"]
    existing_qty = context.existing_quantity
    existing_last = context.existing_last_price or s.underlying_price
    total_value = max(context.total_value, 0.01)

    lines = [
        "Combined Position + Option Outcomes:",
        "Move      Price        Option P/L    Existing Pos P/L    Combined P/L    Portfolio Impact",
        "---------------------------------------------------------------------------------------",
    ]
    combined_rows = []
    for move, price, option_pnl, _impact in analysis["price_rows"]:
        existing_pnl = (price - existing_last) * existing_qty if existing_qty else 0.0
        combined_pnl = option_pnl + existing_pnl
        combined_rows.append((move, price, option_pnl, existing_pnl, combined_pnl, combined_pnl / total_value))
        lines.append(
            f"{move:>+5.0%}   {_money(price):>10}   {_money(option_pnl):>12}   "
            f"{_money(existing_pnl):>16}   {_money(combined_pnl):>12}   {combined_pnl / total_value:>8.1%}"
        )

    worst = min(combined_rows, key=lambda row: row[4]) if combined_rows else None
    best = max(combined_rows, key=lambda row: row[4]) if combined_rows else None
    flat = min(combined_rows, key=lambda row: abs(row[0])) if combined_rows else None

    lines.extend(["", "Outcome read-through:"])
    if existing_qty:
        lines.append(
            f"- Existing {s.symbol} position included: {existing_qty:g} shares using current/reference price {_money(existing_last)}."
        )
    else:
        lines.append(f"- No existing {s.symbol} holding was found in the cockpit snapshot, so combined P/L equals option-ticket P/L.")
    if best is not None:
        lines.append(f"- Better loaded path: {best[0]:+.0%} to {_money(best[1])} => combined {_money(best[4])}.")
    if flat is not None:
        lines.append(f"- Flat/chop path: {flat[0]:+.0%} to {_money(flat[1])} => combined {_money(flat[4])}.")
    if worst is not None:
        lines.append(f"- Worse loaded path: {worst[0]:+.0%} to {_money(worst[1])} => combined {_money(worst[4])}.")
    lines.append("- This table uses simplified expiration-style option payoff plus current spot/share exposure; it does not model IV, Greeks, early exits, or intraday fills.")
    return "\n".join(lines)


def _vertical_debit_breakeven(s) -> float:
    if str(s.option_type).lower().startswith("put"):
        return max(s.strike, s.short_strike) - s.premium
    return min(s.strike, s.short_strike) + s.premium


def _vertical_credit_breakeven(s) -> float:
    if str(s.option_type).lower().startswith("put"):
        return max(s.strike, s.short_strike) - s.credit
    return min(s.strike, s.short_strike) + s.credit


def _get_var(app: tk.Tk, name: str) -> str:
    var = getattr(app, name, None)
    try:
        return str(var.get())
    except Exception:
        return ""


def _set_if_blank(app: tk.Tk, name: str, value: str) -> None:
    var = getattr(app, name, None)
    if var is None:
        setattr(app, name, tk.StringVar(value=value))
        return
    try:
        if not str(var.get()).strip():
            var.set(value)
    except Exception:
        pass


def _first_float(*values: str) -> float | None:
    for value in values:
        try:
            cleaned = str(value).strip().replace(",", "")
            if cleaned:
                return float(cleaned)
        except ValueError:
            continue
    return None


def _format_number(value: float) -> str:
    formatted = f"{value:.2f}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _money(value: float) -> str:
    return f"${value:,.2f}"
