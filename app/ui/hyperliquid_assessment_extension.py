from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Type

from app.analytics.hyperliquid_assessment import format_hyperliquid_position_assessment
from app.brokers.hyperliquid.client import (
    HyperliquidInfoClient,
    format_hyperliquid_snapshot,
    portfolio_from_hyperliquid_snapshot,
)

HYPERLIQUID_ADDRESS_ENV_KEYS = ("HYPE_WALLET_ADDRESS", "HYPERLIQUID_USER_ADDRESS")


def install_hyperliquid_assessment_extension(app_cls: Type[tk.Tk]) -> None:
    """Append a position/risk assessment to every Hyperliquid account sync."""

    app_cls.sync_hyperliquid_account = _sync_hyperliquid_account_with_assessment  # type: ignore[method-assign]


def _hyperliquid_address_from_env() -> str:
    for key in HYPERLIQUID_ADDRESS_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _sync_hyperliquid_account_with_assessment(self: tk.Tk) -> None:
    """Read Hyperliquid balances/positions, merge them, and show an exposure assessment.

    Preserve the existing one-click flow: if HYPE_WALLET_ADDRESS or
    HYPERLIQUID_USER_ADDRESS is present in .env, do not prompt.
    """

    address = _hyperliquid_address_from_env()
    if not address:
        address = simpledialog.askstring(
            "Hyperliquid Sync",
            "Enter your Hyperliquid master/sub-account wallet address.\n\n"
            "Tip: save HYPE_WALLET_ADDRESS=0x... in .env to skip this prompt.\n\n"
            "Use the account address, not the API/agent wallet address.",
        )
    if not address:
        return

    try:
        client = HyperliquidInfoClient()
        snapshot = client.fetch_snapshot(address)
        orders_table = getattr(self, "hyperliquid_workspace_open_orders_table", None)
        if orders_table is not None:
            try:
                from app.ui.options_lab_extension import _populate_workspace_open_orders_table

                _populate_workspace_open_orders_table(orders_table, snapshot.open_orders)
            except Exception:
                pass
        hyperliquid_portfolio, hyperliquid_source_message = portfolio_from_hyperliquid_snapshot(snapshot)
        merged_portfolio = self._merge_hyperliquid_portfolio(hyperliquid_portfolio)

        base_source_message = self.broker.source_message.split(" + Loaded Hyperliquid account ")[0]
        source_message = f"{base_source_message} + {hyperliquid_source_message}"
        self.broker.set_portfolio(merged_portfolio, source_message)
        self.last_hyperliquid_cash_adjustment = hyperliquid_portfolio.cash
        self.refresh_portfolio()

        if hasattr(self, "hyperliquid_status_var"):
            self.hyperliquid_status_var.set("Hyperliquid: synced")

        raw_report = format_hyperliquid_snapshot(snapshot, hyperliquid_portfolio)
        assessment = format_hyperliquid_position_assessment(snapshot, hyperliquid_portfolio)
        self._set_preview_text(f"{raw_report}\n\n{assessment}")
    except Exception as exc:
        if hasattr(self, "hyperliquid_status_var"):
            self.hyperliquid_status_var.set("Hyperliquid: sync failed")
        messagebox.showerror("Hyperliquid sync failed", str(exc))
