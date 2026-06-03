from __future__ import annotations

import tkinter as tk
from typing import Type

from app.ui import account_sources_fix, hyperliquid_trading_extension


def ensure_hyperliquid_submit_alias() -> None:
    """Keep the shared venue submit method installable after symbol renames."""

    if not hasattr(hyperliquid_trading_extension, "_submit_selected_venue"):
        hyperliquid_trading_extension._submit_selected_venue = (  # type: ignore[attr-defined]
            hyperliquid_trading_extension._submit_cockpit_selected_venue
        )
