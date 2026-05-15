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

    @property
    def cost_basis(self) -> float:
        return round(abs(self.quantity) * self.average_cost, 2)

    @property
    def market_value(self) -> float:
        return round(self.quantity * self.last_price, 2)

    @property
    def unrealized_profit_loss(self) -> float:
        if self.open_profit_loss is not None:
            return round(self.open_profit_loss, 2)
        return round(self.market_value - self.cost_basis, 2)

    @property
    def unrealized_profit_loss_percent(self) -> float | None:
        cost_basis = self.cost_basis
        if cost_basis <= 0:
            return None
        return round((self.unrealized_profit_loss / cost_basis) * 100, 2)


@dataclass
class Portfolio:
    cash: float
    positions: Dict[str, Position] = field(default_factory=dict)

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
        return round(sum(position.unrealized_profit_loss for position in self.positions.values()), 2)

    @property
    def unrealized_profit_loss_percent(self) -> float | None:
        cost_basis = self.cost_basis
        if cost_basis <= 0:
            return None
        return round((self.unrealized_profit_loss / cost_basis) * 100, 2)

    @property
    def day_profit_loss(self) -> float | None:
        values = [position.day_profit_loss for position in self.positions.values() if position.day_profit_loss is not None]
        if not values:
            return None
        return round(sum(values), 2)

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
        cash=116_838.39,
        positions={
            "AMD": Position("AMD", 3, 323.89, 450.45),
            "RKLB": Position("RKLB", 5, 86.49, 112.99),
            "MU": Position("MU", 1, 661.71, 774.52),
            "SNDK": Position("SNDK", 1, 1398.99, 1491.65),
            "SPY": Position("SPY", 1.003, 711.05, 738.18),
            "PL": Position("PL", 10, 38.47, 40.96),
            "SWMR": Position("SWMR", 5, 29.18, 33.14),
            "IBRX": Position("IBRX", 10, 7.04, 8.10),
            "AVGO": Position("AVGO", 0.099, 346.06, 427.01),
            "NVDA": Position("NVDA", 1.01, 213.41, 218.22),
            "SEI": Position("SEI", 0.705, 73.34, 75.56),
            "JPM": Position("JPM", 0.011, 166.28, 300.00),
            "MSFT": Position("MSFT", 0.049, 402.54, 412.50),
            "AMZN": Position("AMZN", 0.01, 222.00, 268.15),
            "AAPL": Position("AAPL", 0.01, 272.00, 293.47),
            "VOO": Position("VOO", 0.1, 679.40, 678.56),
            "VPU": Position("VPU", 1.007, 199.92, 197.57),
            "IREN": Position("IREN", 1, 60.59, 55.05),
            "TSM": Position("TSM", 0.515, 419.50, 403.69),
            "MP": Position("MP", 5, 72.32, 67.07),
            "EPAM": Position("EPAM", 2, 219.15, 97.35),
        },
    )
