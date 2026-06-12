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
    DAY = "DAY"
    GTC = "GTC"
    EXT = "EXT"
    GTC_EXT = "GTC_EXT"
    EXTO = "EXTO"
    GTC_EXTO = "GTC_EXTO"
    AM = "AM"
    PM = "PM"


SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES = tuple(option.value for option in TimeInForce)


def normalize_time_in_force(value: str | TimeInForce) -> TimeInForce:
    if isinstance(value, TimeInForce):
        return value

    raw = str(value or "").strip()
    aliases = {
        "day": TimeInForce.DAY,
        "gtc": TimeInForce.GTC,
        "good_till_cancel": TimeInForce.GTC,
        "good till cancel": TimeInForce.GTC,
        "ext": TimeInForce.EXT,
        "day_ext": TimeInForce.EXT,
        "day ext": TimeInForce.EXT,
        "day (ext 13h)": TimeInForce.EXT,
        "gtc_ext": TimeInForce.GTC_EXT,
        "gtc ext": TimeInForce.GTC_EXT,
        "gtc (ext 13h)": TimeInForce.GTC_EXT,
        "exto": TimeInForce.EXTO,
        "day_exto": TimeInForce.EXTO,
        "day exto": TimeInForce.EXTO,
        "day (exto)": TimeInForce.EXTO,
        "overnight": TimeInForce.EXTO,
        "24h": TimeInForce.EXTO,
        "24_5": TimeInForce.EXTO,
        "gtc_exto": TimeInForce.GTC_EXTO,
        "gtc exto": TimeInForce.GTC_EXTO,
        "gtc (exto)": TimeInForce.GTC_EXTO,
        "day_ext_am": TimeInForce.AM,
        "day ext am": TimeInForce.AM,
        "day (ext am)": TimeInForce.AM,
        "am": TimeInForce.AM,
        "day_ext_pm": TimeInForce.PM,
        "day ext pm": TimeInForce.PM,
        "day (ext pm)": TimeInForce.PM,
        "pm": TimeInForce.PM,
    }
    normalized = aliases.get(raw.lower())
    if normalized is not None:
        return normalized
    return TimeInForce(raw)


def schwab_equity_session_duration(time_in_force: str | TimeInForce) -> tuple[str, str]:
    tif = normalize_time_in_force(time_in_force)
    mapping = {
        TimeInForce.DAY: ("NORMAL", "DAY"),
        TimeInForce.GTC: ("NORMAL", "GOOD_TILL_CANCEL"),
        TimeInForce.EXT: ("SEAMLESS", "DAY"),
        TimeInForce.GTC_EXT: ("SEAMLESS", "GOOD_TILL_CANCEL"),
        TimeInForce.EXTO: ("EXTO", "DAY"),
        TimeInForce.GTC_EXTO: ("EXTO", "GOOD_TILL_CANCEL"),
        TimeInForce.AM: ("AM", "DAY"),
        TimeInForce.PM: ("PM", "DAY"),
    }
    return mapping[tif]


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
