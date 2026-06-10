from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_mode: str = os.getenv("APP_MODE", "paper")
    max_order_dollars: float = _float_env("MAX_ORDER_DOLLARS", 50000)
    max_position_percent: float = _float_env("MAX_POSITION_PERCENT", 15)
    require_confirmation_text: str = os.getenv("REQUIRE_CONFIRMATION_TEXT", "CONFIRM")
    starting_cash: float = _float_env("STARTING_CASH", 116838.39)
    openai_ipo_report_model: str = os.getenv("OPENAI_IPO_REPORT_MODEL", "gpt-5.5")


settings = Settings()
