from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    app_mode: str = os.getenv("APP_MODE", "paper")
    max_order_dollars: float = float(os.getenv("MAX_ORDER_DOLLARS", "50000"))
    max_position_percent: float = float(os.getenv("MAX_POSITION_PERCENT", "15"))
    require_confirmation_text: str = os.getenv("REQUIRE_CONFIRMATION_TEXT", "CONFIRM")
    starting_cash: float = float(os.getenv("STARTING_CASH", "116838.39"))


settings = Settings()
