from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionSizePlan:
    risk_budget: float
    risk_per_share: float
    suggested_quantity: int
    estimated_notional: float


def calculate_position_size(
    cash_available: float,
    entry_price: float,
    stop_price: float,
    risk_percent_of_cash: float = 1.0,
    max_notional: float = 5_000.0,
) -> PositionSizePlan:
    """Calculate a simple stop-based position size.

    Example: if cash is $100,000 and risk_percent_of_cash is 1%, the risk
    budget is $1,000. If entry is $100 and stop is $95, risk/share is $5, so
    the stop-based max is 200 shares, then capped by max_notional.
    """
    if cash_available <= 0:
        raise ValueError("Cash available must be greater than zero.")
    if entry_price <= 0:
        raise ValueError("Entry price must be greater than zero.")
    if stop_price <= 0:
        raise ValueError("Stop price must be greater than zero.")
    if stop_price >= entry_price:
        raise ValueError("For a long position, stop price must be below entry price.")
    if risk_percent_of_cash <= 0:
        raise ValueError("Risk percent must be greater than zero.")

    risk_budget = cash_available * (risk_percent_of_cash / 100)
    risk_per_share = entry_price - stop_price
    stop_based_quantity = int(risk_budget // risk_per_share)
    notional_based_quantity = int(max_notional // entry_price)
    suggested_quantity = max(0, min(stop_based_quantity, notional_based_quantity))

    return PositionSizePlan(
        risk_budget=round(risk_budget, 2),
        risk_per_share=round(risk_per_share, 2),
        suggested_quantity=suggested_quantity,
        estimated_notional=round(suggested_quantity * entry_price, 2),
    )
