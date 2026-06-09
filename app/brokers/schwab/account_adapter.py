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

_BALANCE_REPORT_KEYS = (
    ("liquidationValue", "Liquidation value"),
    ("cashBalance", "Cash balance"),
    ("cashAvailableForTrading", "Cash available for trading"),
    ("settledCash", "Settled cash"),
    ("availableFunds", "Available funds"),
    ("availableFundsNonMarginableTrade", "Available non-margin funds"),
    ("buyingPower", "Buying power"),
    ("stockBuyingPower", "Stock buying power"),
    ("optionBuyingPower", "Option buying power"),
    ("maintenanceRequirement", "Maintenance requirement"),
    ("regTCall", "Reg-T call"),
    ("dayTradingBuyingPower", "Day-trading buying power"),
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
    account = _securities_account(payload)
    balances = _current_balances(account)

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


def format_schwab_account_snapshot(payload: Any, portfolio: Portfolio) -> str:
    """Create a Schwab sync report similar to the Hyperliquid portfolio report."""

    account = _securities_account(payload)
    balances = _current_balances(account)
    raw_positions = account.get("positions") or []
    if not isinstance(raw_positions, list):
        raw_positions = []

    account_label = _account_label(account)
    account_type = str(account.get("type") or account.get("accountType") or "--")
    loaded_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_value = portfolio.total_value
    cash_weight = portfolio.cash / max(total_value, 0.01)
    long_value = sum(max(position.market_value, 0.0) for position in portfolio.positions.values())
    short_value = sum(abs(min(position.market_value, 0.0)) for position in portfolio.positions.values())
    day_pnl = portfolio.day_profit_loss

    lines = [
        "SCHWAB ACCOUNT SYNC",
        "===================",
        "",
        f"Account: {account_label}",
        f"Account type: {account_type}",
        f"Fetched: {loaded_at}",
        "",
        "Account summary:",
        f"- Cash loaded into cockpit: {_money(portfolio.cash)} ({cash_weight:.1%} of account)",
        f"- Positions value loaded: {_money(portfolio.positions_value)}",
        f"- Total value loaded into cockpit: {_money(total_value)}",
        f"- Unrealized P&L: {_money(portfolio.unrealized_profit_loss)} ({_optional_percent(portfolio.unrealized_profit_loss_percent)})",
        f"- Day P&L: {_optional_money(day_pnl)}",
        f"- Positions loaded: {len(portfolio.positions)}",
        "",
    ]

    balance_lines = _format_balance_lines(balances)
    if balance_lines:
        lines.extend(["Schwab balances:", *balance_lines, ""])

    lines.extend(
        [
            "Exposure snapshot:",
            f"- Long market value: {_money(long_value)}",
            f"- Short market value: {_money(short_value)}",
            f"- Net market exposure: {_money(long_value - short_value)}",
            f"- Gross market exposure: {_money(long_value + short_value)}",
            f"- Largest position: {_largest_position_line(portfolio)}",
            "",
            "Positions:",
        ]
    )

    if not portfolio.positions:
        lines.append("- None")
    else:
        for symbol in sorted(portfolio.positions):
            position = portfolio.positions[symbol]
            weight = position.market_value / max(total_value, 0.01)
            day_pnl_text = _optional_money(position.day_profit_loss)
            day_pnl_pct_text = _optional_percent(position.day_profit_loss_percent)
            lines.append(
                f"- {symbol}: qty {position.quantity:g}, "
                f"last {_money(position.last_price)}, "
                f"value {_money(position.market_value)} ({weight:.1%}), "
                f"cost {_money(position.cost_basis)}, "
                f"uPnL {_money(position.unrealized_profit_loss)} ({_optional_percent(position.unrealized_profit_loss_percent)}), "
                f"day {day_pnl_text} ({day_pnl_pct_text})"
            )

    lines.extend(
        [
            "",
            "Risk read-through:",
            f"- Concentration: {_concentration_note(portfolio)}",
            f"- Cash buffer: {_cash_buffer_note(portfolio)}",
            f"- Day move: {_day_move_note(portfolio)}",
            "",
            "Notes:",
            "- This is a read-only Schwab account sync report. No order was previewed, submitted, replaced, or canceled.",
            "- Schwab balances can vary by account type; missing fields are omitted from the balance section.",
        ]
    )
    return "\n".join(lines)


def _format_balance_lines(balances: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    seen_labels: set[str] = set()
    for key, label in _BALANCE_REPORT_KEYS:
        value = _to_float(balances.get(key))
        if value is None or label in seen_labels:
            continue
        seen_labels.add(label)
        lines.append(f"- {label}: {_money(value)}")
    return lines


def _largest_position_line(portfolio: Portfolio) -> str:
    if not portfolio.positions:
        return "none"
    total_value = max(portfolio.total_value, 0.01)
    largest = max(portfolio.positions.values(), key=lambda position: abs(position.market_value))
    weight = abs(largest.market_value) / total_value
    return f"{largest.symbol} at {_money(largest.market_value)} ({weight:.1%} of account)"


def _concentration_note(portfolio: Portfolio) -> str:
    if not portfolio.positions:
        return "no positions loaded."
    total_value = max(portfolio.total_value, 0.01)
    largest = max(portfolio.positions.values(), key=lambda position: abs(position.market_value))
    weight = abs(largest.market_value) / total_value
    if weight >= 0.35:
        return f"{largest.symbol} is concentrated at {weight:.1%} of account value."
    if weight >= 0.20:
        return f"{largest.symbol} is the largest holding at {weight:.1%}; worth monitoring."
    return f"largest holding is {largest.symbol} at {weight:.1%}; concentration looks moderate."


def _cash_buffer_note(portfolio: Portfolio) -> str:
    cash_weight = portfolio.cash / max(portfolio.total_value, 0.01)
    if cash_weight < 0.05:
        return f"cash is low at {cash_weight:.1%} of account value."
    if cash_weight > 0.25:
        return f"cash is high at {cash_weight:.1%} of account value."
    return f"cash is {cash_weight:.1%} of account value."


def _day_move_note(portfolio: Portfolio) -> str:
    if portfolio.day_profit_loss is None:
        return "no day P&L field was available in the Schwab positions payload."
    pct = portfolio.day_profit_loss / max(portfolio.total_value, 0.01)
    direction = "gain" if portfolio.day_profit_loss >= 0 else "loss"
    return f"account day {direction} is {_money(portfolio.day_profit_loss)} ({pct:+.2%} of account value)."


def _securities_account(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Unexpected Schwab account response; expected an object.")

    account = payload.get("securitiesAccount") or payload
    if not isinstance(account, dict):
        raise ValueError("Unexpected Schwab account response; missing securitiesAccount object.")
    return account


def _current_balances(account: dict[str, Any]) -> dict[str, Any]:
    balances = account.get("currentBalances") or account.get("initialBalances") or {}
    return balances if isinstance(balances, dict) else {}


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
    day_profit_loss_percent = _first_number(raw_position, _DAY_PNL_PERCENT_KEYS)

    position = Position(
        symbol=symbol,
        quantity=round(quantity, 8),
        average_cost=round(average_cost, 4),
        last_price=round(last_price or 0.0, 4),
        day_profit_loss=_day_profit_loss(raw_position, market_value, day_profit_loss_percent),
        day_profit_loss_percent=day_profit_loss_percent,
        open_profit_loss=_open_profit_loss(raw_position),
    )
    asset_type = _instrument_asset_type(instrument)
    if asset_type:
        setattr(position, "asset_type", asset_type)
    return position


def _instrument_asset_type(instrument: dict[str, Any]) -> str:
    pieces = [
        str(instrument.get("assetType") or "").strip(),
        str(instrument.get("assetSubType") or instrument.get("type") or "").strip(),
    ]
    return " ".join(piece for piece in pieces if piece)


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


def _day_profit_loss(
    raw_position: dict[str, Any],
    market_value: float | None,
    day_profit_loss_percent: float | None,
) -> float | None:
    raw_day_pnl = _first_number(raw_position, _DAY_PNL_KEYS)
    if raw_day_pnl is None:
        return _day_profit_loss_from_percent(market_value, day_profit_loss_percent)

    rounded_day_pnl = round(raw_day_pnl, 2)
    if _looks_like_market_value_copy(rounded_day_pnl, market_value):
        return _day_profit_loss_from_percent(market_value, day_profit_loss_percent)
    return rounded_day_pnl


def _day_profit_loss_from_percent(market_value: float | None, day_profit_loss_percent: float | None) -> float | None:
    if market_value is None or day_profit_loss_percent is None:
        return None
    if abs(day_profit_loss_percent) > 100:
        return None
    return round(market_value * (day_profit_loss_percent / 100.0), 2)


def _looks_like_market_value_copy(day_profit_loss: float, market_value: float | None) -> bool:
    if market_value is None:
        return False
    tolerance = max(abs(market_value) * 0.0001, 0.01)
    return abs(abs(day_profit_loss) - abs(market_value)) <= tolerance


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


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _optional_money(value: float | None) -> str:
    return "--" if value is None else _money(value)


def _optional_percent(value: float | None) -> str:
    return "--" if value is None else f"{value:+.2f}%"
