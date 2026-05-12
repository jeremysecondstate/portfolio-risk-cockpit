from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import uuid4


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(str, Enum):
    DAY = "day"
    GTC = "gtc"


@dataclass(frozen=True)
class OrderRequest:
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    estimated_price: float
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    confirmation_text: str = ""

    def __post_init__(self) -> None:
        normalized = self.symbol.strip().upper()
        object.__setattr__(self, "symbol", normalized)

    @property
    def estimated_notional(self) -> float:
        return round(self.quantity * self.estimated_price, 2)


@dataclass(frozen=True)
class OrderPreview:
    order: OrderRequest
    warnings: list[str]
    blocked: bool
    estimated_cash_after: float
    estimated_position_value_after: float


@dataclass(frozen=True)
class SubmittedOrder:
    id: str
    order: OrderRequest
    submitted_at: datetime
    broker_mode: str
    status: str = "submitted"

    @classmethod
    def create(cls, order: OrderRequest, broker_mode: str) -> "SubmittedOrder":
        return cls(
            id=str(uuid4()),
            order=order,
            submitted_at=datetime.now(timezone.utc),
            broker_mode=broker_mode,
        )
