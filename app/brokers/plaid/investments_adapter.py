from __future__ import annotations

from typing import Any

from app.core.portfolio import Portfolio, Position


def portfolio_from_plaid_holdings(payload: dict[str, Any]) -> tuple[Portfolio, str]:
    return Portfolio(cash=0.0, positions={}), "Plaid adapter placeholder"


def merge_portfolios(primary: Portfolio, secondary: Portfolio) -> Portfolio:
    return Portfolio(cash=round(primary.cash + secondary.cash, 2), positions={**primary.positions, **secondary.positions})
