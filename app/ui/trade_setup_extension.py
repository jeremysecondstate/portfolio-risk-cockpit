from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Type

from app.analytics.technical_analysis import candles_from_price_history
from app.analytics.trade_setup import (
    RiskRewardPlan,
    SetupVerdict,
    SupportResistanceLevels,
    calculate_risk_reward_plan,
    calculate_support_resistance,
)
from app.core.order_models import OrderSide


def install_trade_setup_extension(app_cls: Type[tk.Tk]) -> None:
    """Upgrade the existing Position Size action into a full trade setup planner."""
    app_cls.show_position_size = _show_trade_setup  # type: ignore[method-assign]


def _show_trade_setup(self: tk.Tk) -> None:
    symbol = self.symbol_var.get().strip().upper()
    if not symbol:
        messagebox.showerror("Trade setup failed", "Enter a symbol first.")
        return

    try:
        side = OrderSide(self.side_var.get())
        quantity = float(self.quantity_var.get())
        entry_price = _entry_price_from_ticket(self)
        stop_price = _optional_float(self.stop_price_var.get())
        target_price = _target_price_from_limit(self, side, entry_price)

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
        levels = calculate_support_resistance(candles, lookback=50)
        plan = calculate_risk_reward_plan(
            portfolio=self.broker.get_portfolio(),
            side=side,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            quantity=quantity,
        )

        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(_format_trade_setup_report(symbol, levels, plan))
    except Exception as exc:
        messagebox.showerror("Trade setup failed", str(exc))


def _entry_price_from_ticket(self: tk.Tk) -> float:
    limit_price = _optional_float(self.limit_price_var.get())
    estimated_price = _optional_float(self.estimated_price_var.get())
    entry_price = limit_price or estimated_price
    if entry_price is None or entry_price <= 0:
        raise ValueError("Enter a positive limit price or estimated price for the setup entry.")
    return entry_price


def _target_price_from_limit(self: tk.Tk, side: OrderSide, entry_price: float) -> float | None:
    """Use the ticket limit as a target only when it is beyond the entry direction.

    Most limit orders use the limit field as the entry. If a user enters a limit
    away from the estimated price, this allows the setup planner to treat it as a
    target without adding another field yet.
    """
    estimated_price = _optional_float(self.estimated_price_var.get())
    limit_price = _optional_float(self.limit_price_var.get())
    if estimated_price is None or limit_price is None:
        return None

    if side == OrderSide.BUY and limit_price > estimated_price:
        return limit_price
    if side == OrderSide.SELL and limit_price < estimated_price:
        return limit_price
    return None


def _optional_float(value: str) -> float | None:
    value = value.strip()
    return float(value) if value else None


def _format_trade_setup_report(
    symbol: str,
    levels: SupportResistanceLevels,
    plan: RiskRewardPlan,
) -> str:
    lines = [
        f"TRADE SETUP PLANNER — {symbol}",
        "=" * (23 + len(symbol)),
        "",
        "Price context from Schwab daily candles:",
        f"- Latest close: {_format_money(levels.latest_close)}",
        f"- 50-candle recent low: {_format_money(levels.recent_low)}",
        f"- 50-candle recent high: {_format_money(levels.recent_high)}",
        f"- Nearby support: {_format_optional_money(levels.support)} ({_format_optional_percent(levels.support_distance_percent)})",
        f"- Nearby resistance: {_format_optional_money(levels.resistance)} ({_format_optional_percent(levels.resistance_distance_percent)})",
        "",
        "Ticket risk/reward:",
        f"- Side: {plan.side.value.upper()}",
        f"- Quantity: {plan.quantity:g}",
        f"- Entry: {_format_money(plan.entry_price)}",
        f"- Stop: {_format_optional_money(plan.stop_price)} ({_format_optional_percent(plan.stop_distance_percent)} from entry)",
        f"- Target: {_format_optional_money(plan.target_price)} ({_format_optional_percent(plan.target_distance_percent)} from entry)",
        f"- Dollars at risk: {_format_optional_money(plan.dollars_at_risk)}",
        f"- Portfolio risk: {_format_optional_percent(plan.portfolio_risk_percent)}",
        f"- Reward dollars: {_format_optional_money(plan.reward_dollars)}",
        f"- Risk/reward: {_format_optional_ratio(plan.risk_reward_ratio)}",
        f"- Target needed for 2R: {_format_optional_money(plan.target_for_2r)}",
        f"- Target needed for 3R: {_format_optional_money(plan.target_for_3r)}",
        "",
        f"Setup verdict: {plan.verdict.value.upper()}",
        f"Reason: {plan.verdict_reason}",
        "",
        "How to use this:",
        "- Support/resistance gives nearby map levels; it is not a guarantee.",
        "- A setup is incomplete without a stop because capital at risk cannot be measured.",
        "- For a weekly high-conviction move, favor clean risk, defined invalidation, and at least 2R reward potential.",
    ]

    if plan.verdict == SetupVerdict.INCOMPLETE:
        lines.extend(
            [
                "",
                "Next step: add a stop price to calculate capital at risk and portfolio risk.",
            ]
        )

    return "\n".join(lines)


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _format_optional_money(value: float | None) -> str:
    return "--" if value is None else _format_money(value)


def _format_optional_percent(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}%"


def _format_optional_ratio(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}R"
