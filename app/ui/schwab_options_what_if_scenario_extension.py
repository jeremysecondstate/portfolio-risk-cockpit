from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Callable, Type

from app.core.order_models import TimeInForce, normalize_time_in_force
from app.ui import schwab_trading_tab, trading_workspace

_installed = False
_original_analyze_scenario: Callable[..., dict] | None = None
_original_format_analysis: Callable[..., str] | None = None


def install_schwab_options_what_if_scenario_extension(app_cls: Type[tk.Tk]) -> None:
    """Make the integrated Schwab Options What-If button use the live option-analysis stack.

    The Schwab workspace flattens the option ticket into the Schwab tab, so this patch
    deliberately routes the blue Options What-If button through the runtime-patched
    trading_workspace functions instead of the early imported parser/analyzer references.
    """

    global _installed, _original_analyze_scenario, _original_format_analysis
    if _installed:
        return

    _original_analyze_scenario = trading_workspace._analyze_scenario
    _original_format_analysis = trading_workspace._format_analysis

    trading_workspace._analyze_scenario = _analyze_scenario_with_full_portfolio_paths  # type: ignore[method-assign]
    trading_workspace._format_analysis = _format_analysis_with_full_portfolio_paths  # type: ignore[method-assign]

    schwab_trading_tab._sync_integrated_options_values = _sync_integrated_options_values  # type: ignore[attr-defined]
    schwab_trading_tab._run_schwab_integrated_options_what_if = _run_schwab_integrated_options_what_if  # type: ignore[attr-defined]
    app_cls.run_schwab_integrated_options_what_if = _run_schwab_integrated_options_what_if  # type: ignore[attr-defined]

    _installed = True


def _run_schwab_integrated_options_what_if(self: tk.Tk) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is not None:
        self.preview_text = output

    try:
        _sync_integrated_options_values(self)
        _fill_missing_integrated_what_if_defaults(self)
        scenario = trading_workspace._parse_scenario(self)
        analysis = trading_workspace._analyze_scenario(scenario, self)
        schwab_trading_tab._set_schwab_mode_text(self, trading_workspace._format_analysis(scenario, analysis))
        if hasattr(self, "schwab_preview_status_var"):
            self.schwab_preview_status_var.set("Last Schwab preview: options what-if only")
    except Exception as exc:
        messagebox.showerror("Options what-if failed", str(exc))


def _sync_integrated_options_values(self: tk.Tk) -> None:
    if not hasattr(self, "options_symbol_var"):
        trading_workspace._init_options_vars(self)

    symbol = _get_var(self, "symbol_var").strip().upper() or _get_var(self, "options_symbol_var").strip().upper()
    if symbol:
        _set_var(self, "symbol_var", symbol)
        _set_var(self, "options_symbol_var", symbol)

    side = _get_var(self, "side_var").strip().lower()
    if side:
        _set_var(self, "options_action_var", "Sell" if side == "sell" else "Buy")

    order_type = _get_var(self, "order_type_var").strip().upper()
    _set_var(self, "options_order_type_var", order_type if order_type in trading_workspace.ORDER_TYPES else "LIMIT")

    tif = normalize_time_in_force(_get_var(self, "time_in_force_var"))
    _set_var(self, "options_tif_var", "GTC" if tif in {TimeInForce.GTC, TimeInForce.GTC_EXT} else "Day")

    quantity = _get_var(self, "quantity_var").strip()
    if quantity:
        _set_var(self, "options_quantity_var", quantity)

    stop_price = _get_var(self, "stop_price_var").strip()
    if stop_price:
        _set_var(self, "options_stop_price_var", stop_price)

    limit_price = _get_var(self, "limit_price_var").strip()
    if limit_price and not _get_var(self, "options_underlying_price_var").strip():
        _set_var(self, "options_underlying_price_var", limit_price)

    try:
        portfolio = self.broker.get_portfolio()
        _set_var(self, "options_cash_available_var", f"{portfolio.cash:.2f}")
        _set_var(self, "options_portfolio_value_var", f"{portfolio.total_value:.2f}")
        position = portfolio.get_position(symbol)
        if position is not None and getattr(position, "last_price", None):
            _set_var(self, "options_underlying_price_var", f"{position.last_price:.2f}")
    except Exception:
        # The What-If button should still run from the filled ticket even if the
        # live/portfolio refresh is temporarily unavailable.
        pass


def _fill_missing_integrated_what_if_defaults(self: tk.Tk) -> None:
    symbol = _get_var(self, "options_symbol_var").strip().upper() or _get_var(self, "symbol_var").strip().upper() or "UNKNOWN"
    underlying = _first_float(
        _get_var(self, "options_underlying_price_var"),
        _get_var(self, "limit_price_var"),
        _get_var(self, "estimated_price_var"),
    )

    if (underlying is None or underlying <= 0) and symbol != "UNKNOWN":
        try:
            position = self.broker.get_portfolio().get_position(symbol)
            if position is not None and getattr(position, "last_price", None):
                underlying = float(position.last_price)
        except Exception:
            pass

    strike = _first_float(_get_var(self, "options_strike_var"))
    if underlying is None or underlying <= 0:
        underlying = strike if strike is not None and strike > 0 else 1.0

    _set_if_blank(self, "options_symbol_var", symbol)
    _set_if_blank(self, "options_underlying_price_var", _format_number(underlying))
    _set_if_blank(self, "options_quantity_var", "100")
    _set_if_blank(self, "options_contracts_var", "1")
    _set_if_blank(self, "options_action_var", "Buy")
    _set_if_blank(self, "options_type_var", "Call")
    _set_if_blank(self, "options_order_type_var", "LIMIT")
    _set_if_blank(self, "options_tif_var", "Day")

    if strike is None or strike <= 0:
        strike = underlying
    _set_if_blank(self, "options_strike_var", _format_number(strike))
    _set_if_blank(self, "options_short_strike_var", _format_number(strike))

    strategy = _get_var(self, "options_strategy_var").strip()
    short_strike = _first_float(_get_var(self, "options_short_strike_var"))
    if not strategy:
        if short_strike is not None and strike is not None and abs(short_strike - strike) > 0.0001:
            strategy = "Vertical Debit Spread" if _get_var(self, "options_action_var").strip().lower() != "sell" else "Vertical Credit Spread"
        else:
            strategy = "Long Put" if _get_var(self, "options_type_var").strip().lower().startswith("put") else "Long Call"
        _set_var(self, "options_strategy_var", strategy)

    premium = _first_float(
        _get_var(self, "options_premium_var"),
        _get_var(self, "options_ask_var"),
        _get_var(self, "options_mark_var"),
        _get_var(self, "options_bid_var"),
    )
    if premium is None:
        premium = 0.01
    _set_if_blank(self, "options_premium_var", _format_number(premium))

    credit = _first_float(_get_var(self, "options_credit_var"))
    if credit is None:
        fallback_credit = _first_float(_get_var(self, "options_bid_var"), _get_var(self, "options_mark_var"))
        _set_if_blank(self, "options_credit_var", _format_number(fallback_credit if strategy == "Vertical Credit Spread" and fallback_credit is not None else 0.0))

    _set_if_blank(self, "options_initial_margin_var", "50")
    _set_if_blank(self, "options_maintenance_margin_var", "30")
    _set_if_blank(self, "options_atr_var", "5")
    _set_if_blank(self, "options_rsi_var", "50")
    _set_if_blank(self, "options_sma_20_var", _format_number(underlying))
    _set_if_blank(self, "options_sma_50_var", _format_number(underlying))
    _set_if_blank(self, "options_sma_200_var", _format_number(underlying))
    _set_if_blank(self, "options_support_var", _format_number(underlying * 0.97))
    _set_if_blank(self, "options_resistance_var", _format_number(underlying * 1.03))

    try:
        portfolio = self.broker.get_portfolio()
        _set_if_blank(self, "options_cash_available_var", f"{portfolio.cash:.2f}")
        _set_if_blank(self, "options_portfolio_value_var", f"{portfolio.total_value:.2f}")
    except Exception:
        _set_if_blank(self, "options_cash_available_var", "0")
        _set_if_blank(self, "options_portfolio_value_var", "1")


def _analyze_scenario_with_full_portfolio_paths(s: trading_workspace.OptionsScenario, app: tk.Tk | None = None) -> dict:
    if _original_analyze_scenario is None:
        raise RuntimeError("Options What-If analyzer was not initialized.")

    analysis = _original_analyze_scenario(s, app)
    if s.strategy in {"Vertical Debit Spread", "Vertical Credit Spread"}:
        _apply_directional_vertical_math(s, app, analysis)

    analysis["combined_rows"] = _combined_scenario_rows(s, analysis)
    analysis["combined_read"] = _combined_read_summary(s, analysis)
    return analysis


def _apply_directional_vertical_math(s: trading_workspace.OptionsScenario, app: tk.Tk | None, analysis: dict) -> None:
    contracts = max(s.contracts, 1)
    multiplier = 100
    width = abs(s.short_strike - s.strike) * contracts * multiplier
    premium_paid = s.premium * contracts * multiplier
    credit_received = s.credit * contracts * multiplier

    if s.strategy == "Vertical Debit Spread":
        max_loss = premium_paid
        max_profit = max(width - premium_paid, 0.0)
        breakeven = _vertical_debit_breakeven(s)
        margin_required = max_loss
    else:
        max_loss = max(width - credit_received, 0.0)
        max_profit = credit_received
        breakeven = _vertical_credit_breakeven(s)
        margin_required = max_loss

    stop_loss = _directional_vertical_pnl(s, s.stop_price) if s.stop_price is not None else None
    target_profit = _directional_vertical_pnl(s, s.target_price) if s.target_price is not None else None
    reward_risk = None
    if stop_loss is not None and stop_loss < 0 and target_profit is not None and target_profit > 0:
        reward_risk = target_profit / abs(stop_loss)

    portfolio_context = trading_workspace._portfolio_context(s, app, margin_required, max_loss)
    price_rows = []
    for price in _scenario_prices(s, analysis):
        move = (price / s.underlying_price) - 1 if s.underlying_price else 0.0
        pnl = _directional_vertical_pnl(s, price)
        price_rows.append((move, price, pnl, pnl / max(portfolio_context.total_value, 0.01)))

    analysis.update(
        {
            "max_loss": max_loss,
            "max_profit": max_profit,
            "breakeven": breakeven,
            "margin_required": margin_required,
            "portfolio_risk": max_loss / max(portfolio_context.total_value, 0.01),
            "buying_power_after": portfolio_context.cash - margin_required,
            "stop_loss": stop_loss,
            "target_profit": target_profit,
            "reward_risk": reward_risk,
            "price_rows": price_rows,
            "portfolio_context": portfolio_context,
            "checklist": trading_workspace._safety_checklist(s, max_loss, margin_required, stop_loss, portfolio_context),
        }
    )


def _combined_scenario_rows(s: trading_workspace.OptionsScenario, analysis: dict) -> list[dict[str, float | str]]:
    context: trading_workspace.PortfolioContext = analysis["portfolio_context"]
    existing_quantity = context.existing_quantity
    existing_last = context.existing_last_price or s.underlying_price
    total_value = max(context.total_value, 0.01)

    rows: list[dict[str, float | str]] = []
    for price in _scenario_prices(s, analysis):
        move = (price / s.underlying_price) - 1 if s.underlying_price else 0.0
        option_pnl = _estimate_ticket_pnl(s, price)
        existing_pnl = (price - existing_last) * existing_quantity if existing_quantity else 0.0
        combined_pnl = option_pnl + existing_pnl
        rows.append(
            {
                "label": _scenario_label(move, combined_pnl),
                "move": move,
                "price": price,
                "option_pnl": option_pnl,
                "existing_pnl": existing_pnl,
                "combined_pnl": combined_pnl,
                "portfolio_impact": combined_pnl / total_value,
            }
        )
    rows.sort(key=lambda row: float(row["price"]))
    return rows


def _combined_read_summary(s: trading_workspace.OptionsScenario, analysis: dict) -> dict[str, Any]:
    rows = analysis.get("combined_rows") or []
    if not rows:
        return {}
    context: trading_workspace.PortfolioContext = analysis["portfolio_context"]
    worst = min(rows, key=lambda row: float(row["combined_pnl"]))
    best = max(rows, key=lambda row: float(row["combined_pnl"]))
    flat = min(rows, key=lambda row: abs(float(row["move"])))
    down = min(rows, key=lambda row: abs(float(row["move"]) + 0.10))
    up = min(rows, key=lambda row: abs(float(row["move"]) - 0.10))

    exposure_note = "No existing spot/share position was found for this symbol, so combined P/L equals the option-ticket P/L."
    if context.existing_quantity:
        exposure_note = (
            f"Existing {s.symbol} spot/share exposure included: {context.existing_quantity:g} shares "
            f"using reference price {trading_workspace._money(context.existing_last_price or s.underlying_price)}."
        )

    return {"worst": worst, "best": best, "flat": flat, "down": down, "up": up, "exposure_note": exposure_note}


def _format_analysis_with_full_portfolio_paths(s: trading_workspace.OptionsScenario, analysis: dict) -> str:
    if _original_format_analysis is None:
        raise RuntimeError("Options What-If formatter was not initialized.")

    base = _original_format_analysis(s, analysis)
    section = _format_combined_paths_section(s, analysis)
    if not section or "FULL-PORTFOLIO WHAT-IF LADDER" in base:
        return base

    marker = "\nNotes:\n"
    if marker in base:
        return base.replace(marker, f"\n{section}\nNotes:\n", 1)
    return f"{base}\n\n{section}"


def _format_combined_paths_section(s: trading_workspace.OptionsScenario, analysis: dict) -> str:
    rows = analysis.get("combined_rows") or []
    if not rows:
        return ""

    lines = [
        "FULL-PORTFOLIO WHAT-IF LADDER",
        "================================",
        "Move      Price        Option P/L    Existing Pos P/L    Combined P/L    Portfolio Impact    Path",
        "------------------------------------------------------------------------------------------------",
    ]
    for row in rows:
        lines.append(
            f"{float(row['move']):>+5.0%}   "
            f"{trading_workspace._money(float(row['price'])):>10}   "
            f"{trading_workspace._money(float(row['option_pnl'])):>12}   "
            f"{trading_workspace._money(float(row['existing_pnl'])):>16}   "
            f"{trading_workspace._money(float(row['combined_pnl'])):>12}   "
            f"{float(row['portfolio_impact']):>8.1%}          "
            f"{row['label']}"
        )

    read = analysis.get("combined_read") or {}
    lines.extend(["", "Good / bad read-through:"])
    exposure_note = read.get("exposure_note")
    if exposure_note:
        lines.append(f"- {exposure_note}")
    for title, key in (
        ("Better loaded path", "best"),
        ("Flat/chop path", "flat"),
        ("Downside stress", "down"),
        ("Upside stress", "up"),
        ("Worst loaded path", "worst"),
    ):
        row = read.get(key)
        if row:
            lines.append(
                f"- {title}: {float(row['move']):+.0%} to {trading_workspace._money(float(row['price']))} "
                f"=> option {trading_workspace._money(float(row['option_pnl']))}, "
                f"spot {trading_workspace._money(float(row['existing_pnl']))}, "
                f"combined {trading_workspace._money(float(row['combined_pnl']))}."
            )

    lines.append(
        "- This is an expiration-style scenario lens using the filled ticket plus current spot/share exposure; "
        "it does not model IV crush/expansion, Greeks, early exits, partial fills, or broker-specific margin changes."
    )
    return "\n".join(lines)


def _scenario_prices(s: trading_workspace.OptionsScenario, analysis: dict) -> list[float]:
    prices = {max(s.underlying_price * (1 + move), 0.01) for move in (-0.30, -0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20, 0.30)}
    for key in ("breakeven",):
        value = analysis.get(key)
        if isinstance(value, (int, float)) and value > 0:
            prices.add(float(value))
    for value in (s.support, s.resistance, s.stop_price, s.target_price, s.strike, s.short_strike):
        if isinstance(value, (int, float)) and value and value > 0:
            prices.add(float(value))
    return sorted(prices)


def _estimate_ticket_pnl(s: trading_workspace.OptionsScenario, price: float) -> float:
    if s.strategy in {"Vertical Debit Spread", "Vertical Credit Spread"}:
        return _directional_vertical_pnl(s, price)
    return trading_workspace._estimate_price_pnl(s, price)


def _directional_vertical_pnl(s: trading_workspace.OptionsScenario, underlying_price: float | None) -> float:
    if underlying_price is None:
        return 0.0
    contracts = max(s.contracts, 1)
    multiplier = 100
    low_strike = min(s.strike, s.short_strike)
    high_strike = max(s.strike, s.short_strike)
    width = high_strike - low_strike

    if str(s.option_type).lower().startswith("put"):
        intrinsic_spread = min(max(high_strike - underlying_price, 0.0), width) * contracts * multiplier
    else:
        intrinsic_spread = min(max(underlying_price - low_strike, 0.0), width) * contracts * multiplier

    if s.strategy == "Vertical Debit Spread":
        return intrinsic_spread - (s.premium * contracts * multiplier)
    return (s.credit * contracts * multiplier) - intrinsic_spread


def _vertical_debit_breakeven(s: trading_workspace.OptionsScenario) -> float:
    if str(s.option_type).lower().startswith("put"):
        return max(s.strike, s.short_strike) - s.premium
    return min(s.strike, s.short_strike) + s.premium


def _vertical_credit_breakeven(s: trading_workspace.OptionsScenario) -> float:
    if str(s.option_type).lower().startswith("put"):
        return max(s.strike, s.short_strike) - s.credit
    return min(s.strike, s.short_strike) + s.credit


def _scenario_label(move: float, combined_pnl: float) -> str:
    if combined_pnl < 0 and move <= -0.20:
        return "really bad downside"
    if combined_pnl < 0 and move >= 0.20:
        return "really bad upside"
    if combined_pnl < 0:
        return "bad"
    if abs(move) <= 0.02:
        return "flat / chop"
    if combined_pnl > 0 and abs(move) >= 0.20:
        return "really good"
    if combined_pnl > 0:
        return "good"
    return "neutral"


def _get_var(app: tk.Tk, name: str) -> str:
    var = getattr(app, name, None)
    try:
        return str(var.get())
    except Exception:
        return ""


def _set_var(app: tk.Tk, name: str, value: str) -> None:
    var = getattr(app, name, None)
    if var is None:
        setattr(app, name, tk.StringVar(value=value))
        return
    try:
        var.set(value)
    except Exception:
        pass


def _set_if_blank(app: tk.Tk, name: str, value: str) -> None:
    if not _get_var(app, name).strip():
        _set_var(app, name, value)


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
