from __future__ import annotations

from typing import Any

from app.core.portfolio import Portfolio, Position


def portfolio_from_plaid_holdings(payload: dict[str, Any]) -> tuple[Portfolio, str]:
    accounts = payload.get("accounts") or []
    holdings = payload.get("holdings") or []
    securities = payload.get("securities") or []
    securities_by_id = {str(item.get("security_id")): item for item in securities if isinstance(item, dict)}
    positions: dict[str, Position] = {}

    for holding in holdings:
        if not isinstance(holding, dict):
            continue
        security = securities_by_id.get(str(holding.get("security_id")), {})
        symbol = _symbol(security)
        if not symbol:
            continue
        quantity = _num(holding.get("quantity"))
        last_price = _num(holding.get("institution_price"))
        value = _num(holding.get("institution_value"))
        cost_basis = _num(holding.get("cost_basis"))
        if last_price <= 0 and quantity:
            last_price = abs(value / quantity)
        average_cost = abs(cost_basis / quantity) if cost_basis and quantity else last_price
        positions[symbol] = Position(symbol, round(quantity, 8), round(average_cost, 4), round(last_price, 4))

    cash = _cash(accounts)
    source = _source_message(payload, accounts)
    return Portfolio(cash=round(cash, 2), positions=positions), source


def merge_portfolios(primary: Portfolio, secondary: Portfolio) -> Portfolio:
    merged = Portfolio(cash=round(primary.cash + secondary.cash, 2), positions={})
    for source in (primary, secondary):
        for position in source.positions.values():
            existing = merged.positions.get(position.symbol)
            if existing is None:
                merged.positions[position.symbol] = Position(
                    position.symbol,
                    position.quantity,
                    position.average_cost,
                    position.last_price,
                    position.day_profit_loss,
                    position.day_profit_loss_percent,
                    position.open_profit_loss,
                )
                continue
            new_quantity = existing.quantity + position.quantity
            if abs(new_quantity) <= 0.00000001:
                del merged.positions[position.symbol]
                continue
            new_cost = (existing.average_cost * existing.quantity) + (position.average_cost * position.quantity)
            existing.quantity = round(new_quantity, 8)
            existing.average_cost = round(abs(new_cost / new_quantity), 4)
            existing.last_price = position.last_price
    return merged


def _source_message(payload: dict[str, Any], accounts: list[Any]) -> str:
    item = payload.get("item") or {}
    institution_id = str(item.get("institution_id") or "").strip()
    account_names = [str(account.get("name") or "").strip() for account in accounts if isinstance(account, dict)]
    account_hint = ", ".join(name for name in account_names if name) or "investment account"
    if institution_id == "ins_109508":
        return f"Loaded Plaid Sandbox test holdings ({account_hint})"
    if institution_id:
        return f"Loaded Plaid Investments holdings ({institution_id}; {account_hint})"
    return f"Loaded Plaid Investments holdings ({account_hint})"


def _symbol(security: dict[str, Any]) -> str | None:
    symbol = str(security.get("ticker_symbol") or security.get("symbol") or "").strip().upper()
    return symbol or None


def _cash(accounts: list[Any]) -> float:
    total = 0.0
    for account in accounts:
        if not isinstance(account, dict):
            continue
        balances = account.get("balances") or {}
        available = _num(balances.get("available"))
        current = _num(balances.get("current"))
        name = str(account.get("name") or "").lower()
        if "cash" in name:
            total += available or current
    return total


def _num(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
