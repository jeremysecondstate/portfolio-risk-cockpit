from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Type

from app.analytics.hyperliquid_assessment import format_hyperliquid_position_assessment
from app.brokers.hyperliquid.client import (
    HyperliquidInfoClient,
    format_hyperliquid_snapshot,
    portfolio_from_hyperliquid_snapshot,
)


def install_hyperliquid_assessment_extension(app_cls: Type[tk.Tk]) -> None:
    """Append a position/risk assessment to every Hyperliquid account sync."""

    app_cls.sync_hyperliquid_account = _sync_hyperliquid_account_with_assessment  # type: ignore[method-assign]


def _sync_hyperliquid_account_with_assessment(self: tk.Tk) -> None:
    """Read Hyperliquid balances/positions, merge them, and show an exposure assessment."""

    default_address = __import__("os").getenv("HYPERLIQUID_USER_ADDRESS", "").strip()
    address = default_address or simpledialog.askstring(
        "Hyperliquid Sync",
        "Enter your Hyperliquid master/sub-account wallet address.\n\n"
        "Use the account address, not the API/agent wallet address.",
    )
    if not address:
        return

    try:
        client = HyperliquidInfoClient()
        snapshot = client.fetch_snapshot(address)
        hyperliquid_portfolio, hyperliquid_source_message = portfolio_from_hyperliquid_snapshot(snapshot)
        merged_portfolio = self._merge_hyperliquid_portfolio(hyperliquid_portfolio)

        base_source_message = self.broker.source_message.split(" + Loaded Hyperliquid account ")[0]
        source_message = f"{base_source_message} + {hyperliquid_source_message}"
        self.broker.set_portfolio(merged_portfolio, source_message)
        self.last_hyperliquid_cash_adjustment = hyperliquid_portfolio.cash
        self.refresh_portfolio()

        raw_report = format_hyperliquid_snapshot(snapshot, hyperliquid_portfolio)
        assessment = format_hyperliquid_position_assessment(snapshot, hyperliquid_portfolio)
        self._set_preview_text(f"{raw_report}\n\n{assessment}")
        messagebox.showinfo("Hyperliquid synced", hyperliquid_source_message)
    except Exception as exc:
        messagebox.showerror("Hyperliquid sync failed", str(exc))
