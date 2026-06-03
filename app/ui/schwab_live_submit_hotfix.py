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


def install_schwab_live_submit_hotfix(app_cls: Type[tk.Tk]) -> None:
    """Force Schwab Trading tab LIVE Submit through the direct Schwab guarded submit."""

    ensure_hyperliquid_submit_alias()
    account_sources_fix._submit_live_schwab_from_workspace = _submit_live_schwab_from_workspace  # type: ignore[attr-defined]


def _submit_live_schwab_from_workspace(self: tk.Tk) -> None:
    account_sources_fix._run_schwab_workspace_action(self, "submit_live_schwab_order_guarded")
