from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class Position:
    symbol: str
    quantity: float
    average_cost: float
    last_price: float
    day_profit_loss: float | None = None
    day_profit_loss_percent: float | None = None
    open_profit_loss: float | None = None
    unrealized_profit_loss_known: bool = True
    cost_basis_estimated: bool = False
    raw_profit_loss: float | None = None
    custom_profit_loss: float | None = None
    custom_realized_profit_loss: float | None = None
    custom_unrealized_profit_loss: float | None = None
    custom_pnl_status: str = ""
    basis_status: str = ""

    @property
    def cost_basis(self) -> float:
        return round(abs(self.quantity) * self.average_cost, 2)

    @property
    def market_value(self) -> float:
        return round(self.quantity * self.last_price, 2)

    @property
    def unrealized_profit_loss(self) -> float:
        if not self.unrealized_profit_loss_known:
            return 0.0
        if self.open_profit_loss is not None:
            return round(self.open_profit_loss, 2)
        return round(self.market_value - self.cost_basis, 2)

    @property
    def unrealized_profit_loss_percent(self) -> float | None:
        if not self.unrealized_profit_loss_known:
            return None
        cost_basis = self.cost_basis
        if cost_basis <= 0:
            return None
        return round((self.unrealized_profit_loss / cost_basis) * 100, 2)


@dataclass
class CashPosition:
    """Display-only cash-like holding such as Schwab USD or Hyperliquid USDC."""

    symbol: str
    amount: float
    source: str = ""

    @property
    def display_symbol(self) -> str:
        source = self.source.strip()
        return f"{self.symbol} ({source})" if source else self.symbol

    @property
    def quantity(self) -> float:
        return round(self.amount, 2)

    @property
    def average_cost(self) -> float:
        return 1.0

    @property
    def last_price(self) -> float:
        return 1.0

    @property
    def cost_basis(self) -> float:
        return round(self.amount, 2)

    @property
    def market_value(self) -> float:
        return round(self.amount, 2)


@dataclass
class Portfolio:
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)
    cash_positions: Dict[str, CashPosition] = field(default_factory=dict)

    @property
    def positions_value(self) -> float:
        return round(sum(position.market_value for position in self.positions.values()), 2)

    @property
    def total_value(self) -> float:
        return round(self.cash + self.positions_value, 2)

    @property
    def cost_basis(self) -> float:
        return round(sum(position.cost_basis for position in self.positions.values()), 2)

    @property
    def unrealized_profit_loss(self) -> float:
        return round(
            sum(
                position.unrealized_profit_loss
                for position in self.positions.values()
                if position.unrealized_profit_loss_known
            ),
            2,
        )

    @property
    def unrealized_profit_loss_percent(self) -> float | None:
        cost_basis = round(
            sum(
                position.cost_basis
                for position in self.positions.values()
                if position.unrealized_profit_loss_known
            ),
            2,
        )
        if cost_basis <= 0:
            return None
        return round((self.unrealized_profit_loss / cost_basis) * 100, 2)

    @property
    def day_profit_loss(self) -> float | None:
        values = [position.day_profit_loss for position in self.positions.values() if position.day_profit_loss is not None]
        if not values:
            return None
        return round(sum(values), 2)

    def display_cash_positions(self) -> list[CashPosition]:
        """Return cash rows for UI display without changing portfolio totals.

        `cash` remains the single source of truth for total cash. `cash_positions`
        is an optional source/currency breakdown used by the Positions table. If
        a source adapter does not provide a breakdown, show the aggregate as USD.
        """

        rows = [cash for cash in self.cash_positions.values() if abs(cash.amount) > 0.00000001]
        allocated_cash = round(sum(row.amount for row in rows), 2)
        residual_cash = round(self.cash - allocated_cash, 2)
        if abs(residual_cash) > 0.005:
            source = "Unallocated" if rows else "Cash"
            rows.append(CashPosition("USD", residual_cash, source))
        return sorted(rows, key=lambda cash: cash.display_symbol)

    def get_position(self, symbol: str) -> Position | None:
        return self.positions.get(symbol.strip().upper())

    def upsert_position(self, symbol: str, quantity_delta: float, fill_price: float) -> None:
        symbol = symbol.strip().upper()
        existing = self.positions.get(symbol)

        if existing is None:
            if quantity_delta <= 0:
                raise ValueError(f"Cannot create negative or zero position for {symbol}.")
            self.positions[symbol] = Position(
                symbol=symbol,
                quantity=round(quantity_delta, 8),
                average_cost=fill_price,
                last_price=fill_price,
            )
            return

        new_quantity = round(existing.quantity + quantity_delta, 8)
        if new_quantity < -0.00000001:
            raise ValueError(f"Cannot sell more {symbol} than the paper account owns.")

        existing.last_price = fill_price
        existing.day_profit_loss = None
        existing.day_profit_loss_percent = None
        existing.open_profit_loss = None
        if new_quantity <= 0.00000001:
            del self.positions[symbol]
            return

        if quantity_delta > 0:
            old_cost = existing.average_cost * existing.quantity
            added_cost = fill_price * quantity_delta
            existing.average_cost = round((old_cost + added_cost) / new_quantity, 4)

        existing.quantity = new_quantity


def current_foundation_portfolio() -> Portfolio:
    """Seed the paper broker from the latest portfolio screenshot/PDF."""
    return Portfolio(
        cash=0.00,
        positions={
        },
    )
