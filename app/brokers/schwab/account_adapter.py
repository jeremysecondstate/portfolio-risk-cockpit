from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.portfolio import CashPosition, Portfolio, Position


_NUMBER_KEYS_FOR_CASH = (
    "cashBalance",
    "cashAvailableForTrading",
    "settledCash",
    "availableFunds",
    "availableFundsNonMarginableTrade",
)

_NUMBER_KEYS_FOR_LIQUIDATION = (
    "liquidationValue",
    "currentLiquidationValue",
    "accountValue",
)

_NUMBER_KEYS_FOR_PRICE = (
    "marketPrice",
    "lastPrice",
    "currentPrice",
    "markPrice",
)

_PER_SHARE_COST_KEYS = (
    "averagePrice",
    "averageLongPrice",
    "averageShortPrice",
    "averageCost",
)

_TOTAL_COST_KEYS = (
    "averageCostBasis",
    "costBasis",
)

_OPEN_PNL_KEYS = (
    "longOpenProfitLoss",
    "shortOpenProfitLoss",
    "openProfitLoss",
)

_DAY_PNL_KEYS = (
    "currentDayProfitLoss",
    "dayProfitLoss",
)

_DAY_PNL_PERCENT_KEYS = (
    "currentDayProfitLossPercentage",
    "dayProfitLossPercentage",
)


def portfolio_from_schwab_account(payload: Any) -> tuple[Portfolio, str]:
    """Convert a Schwab account response into the cockpit's Portfolio model.

    Schwab account payloads are nested under `securitiesAccount` and may vary by
    account type. This adapter keeps the UI resilient by using several common
    balance/position fields and by deriving last price from market value when a
    direct price field is not present.
    """
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Schwab account response; expected an object.")

    account = payload.get("securitiesAccount") or payload
    if not isinstance(account, dict):
        raise ValueError("Unexpected Schwab account response; missing securitiesAccount object.")

    balances = account.get("currentBalances") or account.get("initialBalances") or {}
    if not isinstance(balances, dict):
        balances = {}

    positions: dict[str, Position] = {}
    raw_positions = account.get("positions") or []
    if not isinstance(raw_positions, list):
        raw_positions = []

    for raw_position in raw_positions:
        position = _position_from_schwab(raw_position)
        if position is not None:
            positions[position.symbol] = position

    positions_value = round(sum(position.market_value for position in positions.values()), 2)
    liquidation_value = _first_number(balances, _NUMBER_KEYS_FOR_LIQUIDATION)
    if liquidation_value is not None:
        cash = round(liquidation_value - positions_value, 2)
    else:
        cash = round(_first_number(balances, _NUMBER_KEYS_FOR_CASH) or 0.0, 2)

    account_label = _account_label(account)
    cash_positions = {"USD:SCHWAB": CashPosition("USD", cash, "Schwab")}
    loaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    source_message = f"Loaded Schwab account {account_label} at {loaded_at}"
    return Portfolio(cash=cash, positions=positions, cash_positions=cash_positions), source_message


def _position_from_schwab(raw_position: Any) -> Position | None:
    if not isinstance(raw_position, dict):
        return None

    instrument = raw_position.get("instrument") or {}
    if not isinstance(instrument, dict):
        instrument = {}

    symbol = str(instrument.get("symbol") or raw_position.get("symbol") or "").strip().upper()
    if not symbol:
        return None

    quantity = _net_quantity(raw_position)
    if abs(quantity) <= 0.00000001:
        return None

    market_value = _to_float(raw_position.get("marketValue"))
    last_price = _first_number(raw_position, _NUMBER_KEYS_FOR_PRICE)
    if last_price is None and market_value is not None:
        last_price = abs(market_value / quantity)

    average_cost = _average_cost(raw_position, quantity, last_price)

    return Position(
        symbol=symbol,
        quantity=round(quantity, 8),
        average_cost=round(average_cost, 4),
        last_price=round(last_price or 0.0, 4),
        day_profit_loss=_first_number(raw_position, _DAY_PNL_KEYS),
        day_profit_loss_percent=_first_number(raw_position, _DAY_PNL_PERCENT_KEYS),
        open_profit_loss=_open_profit_loss(raw_position),
    )


def _average_cost(raw_position: dict[str, Any], quantity: float, fallback_price: float | None) -> float:
    per_share_cost = _first_number(raw_position, _PER_SHARE_COST_KEYS)
    if per_share_cost is not None:
        return per_share_cost

    total_cost = _first_number(raw_position, _TOTAL_COST_KEYS)
    if total_cost is not None and abs(quantity) > 0.00000001:
        return abs(total_cost / quantity)

    return fallback_price or 0.0


def _open_profit_loss(raw_position: dict[str, Any]) -> float | None:
    long_pnl = _to_float(raw_position.get("longOpenProfitLoss"))
    short_pnl = _to_float(raw_position.get("shortOpenProfitLoss"))
    if long_pnl is not None or short_pnl is not None:
        return round((long_pnl or 0.0) + (short_pnl or 0.0), 2)
    return _first_number(raw_position, _OPEN_PNL_KEYS)


def _net_quantity(raw_position: dict[str, Any]) -> float:
    long_quantity = _to_float(raw_position.get("longQuantity"))
    short_quantity = _to_float(raw_position.get("shortQuantity"))
    if long_quantity is not None or short_quantity is not None:
        return (long_quantity or 0.0) - (short_quantity or 0.0)

    for key in ("quantity", "settledLongQuantity", "agedQuantity"):
        value = _to_float(raw_position.get(key))
        if value is not None:
            return value

    return 0.0


def _first_number(container: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _to_float(container.get(key))
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _account_label(account: dict[str, Any]) -> str:
    account_number = str(account.get("accountNumber") or "").strip()
    if account_number:
        return "••••" + account_number[-4:]
    return "snapshot"
