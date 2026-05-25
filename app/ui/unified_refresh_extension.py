from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Callable, Type

from app.brokers.hyperliquid.client import (
    HyperliquidInfoClient,
    format_hyperliquid_snapshot,
    portfolio_from_hyperliquid_snapshot,
)


HYPERLIQUID_ADDRESS_ENV_KEYS = ("HYPE_WALLET_ADDRESS", "HYPERLIQUID_USER_ADDRESS")


def install_unified_refresh_extension(app_cls: Type[tk.Tk]) -> None:
    """Collapse Schwab/Hyperliquid refresh controls into one portfolio refresh."""

    previous_build_order_panel = app_cls._build_order_panel
    app_cls._build_order_panel = _wrap_build_order_panel(previous_build_order_panel)  # type: ignore[method-assign]
    app_cls.refresh_connected_portfolio = _refresh_connected_portfolio  # type: ignore[attr-defined]


def _wrap_build_order_panel(previous_build_order_panel: Callable[[tk.Tk, ttk.Frame], None]) -> Callable[[tk.Tk, ttk.Frame], None]:
    def build_order_panel_with_unified_refresh(self: tk.Tk, parent: ttk.Frame) -> None:
        previous_build_order_panel(self, parent)
        _replace_connection_refresh_controls(self)

    return build_order_panel_with_unified_refresh


def _replace_connection_refresh_controls(self: tk.Tk) -> None:
    connections_group = _find_label_frame_by_text(self, "Connections")
    if connections_group is None:
        return

    for child in list(connections_group.winfo_children()):
        if not isinstance(child, ttk.Button):
            continue
        label = str(child.cget("text"))
        if label == "Refresh Schwab":
            child.configure(text="Refresh Portfolio", command=self.refresh_connected_portfolio)
            child.grid_configure(row=1, column=0, columnspan=2, sticky="ew", padx=0)
        elif label == "Reset Session":
            child.destroy()


def _find_label_frame_by_text(root: tk.Widget, text: str) -> ttk.LabelFrame | None:
    for child in root.winfo_children():
        if isinstance(child, ttk.LabelFrame) and str(child.cget("text")) == text:
            return child
        nested = _find_label_frame_by_text(child, text)
        if nested is not None:
            return nested
    return None


def _refresh_connected_portfolio(self: tk.Tk) -> None:
    """Refresh Schwab, then Hyperliquid, through one user-facing action."""

    results: list[str] = [
        "PORTFOLIO REFRESH",
        "=================",
        "",
        "Refreshing Schwab and Hyperliquid account data into one cockpit snapshot.",
        "",
    ]

    schwab_error: Exception | None = None
    hyperliquid_error: Exception | None = None
    hyperliquid_preview: str | None = None

    try:
        schwab_source_message = _sync_schwab_account_silent(self)
        results.append(f"- {schwab_source_message}")
    except Exception as exc:
        schwab_error = exc
        results.append(f"- Schwab refresh failed: {exc}")

    try:
        hyperliquid_source_message, hyperliquid_preview = _sync_hyperliquid_account_silent(self)
        results.append(f"- {hyperliquid_source_message}")
    except Exception as exc:
        hyperliquid_error = exc
        results.append(f"- Hyperliquid refresh failed: {exc}")

    try:
        self.refresh_portfolio()
    except Exception:
        pass

    results.extend(
        [
            "",
            f"Snapshot: {getattr(self.broker, 'source_message', '--')}",
        ]
    )
    if hyperliquid_preview:
        results.extend(["", hyperliquid_preview])

    self._set_preview_text("\n".join(results))

    if schwab_error or hyperliquid_error:
        failed = []
        if schwab_error:
            failed.append("Schwab")
        if hyperliquid_error:
            failed.append("Hyperliquid")
        messagebox.showerror("Portfolio refresh incomplete", f"Could not refresh: {', '.join(failed)}")
        return

    messagebox.showinfo("Portfolio refreshed", "Schwab and Hyperliquid refresh completed.")


def _sync_schwab_account_silent(self: tk.Tk) -> str:
    session = self._authorize_schwab_session()
    if session is None:
        raise RuntimeError("Schwab refresh canceled; no authorization was provided.")

    source_message = self._sync_schwab_account_snapshot(session)
    self.schwab_status_var.set("Schwab session: connected")
    return source_message


def _hyperliquid_address_from_env() -> str:
    for key in HYPERLIQUID_ADDRESS_ENV_KEYS:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def _sync_hyperliquid_account_silent(self: tk.Tk) -> tuple[str, str | None]:
    default_address = _hyperliquid_address_from_env()
    address = default_address or simpledialog.askstring(
        "Hyperliquid Sync",
        "Enter your Hyperliquid master/sub-account wallet address.\n\n"
        "Tip: save HYPE_WALLET_ADDRESS=0x... in .env to skip this prompt.\n\n"
        "Use the account address, not the API/agent wallet address.",
    )
    if not address:
        raise RuntimeError("Hyperliquid refresh canceled; no wallet address was provided.")

    client = HyperliquidInfoClient()
    snapshot = client.fetch_snapshot(address)
    hyperliquid_portfolio, hyperliquid_source_message = portfolio_from_hyperliquid_snapshot(snapshot)
    merged_portfolio = self._merge_hyperliquid_portfolio(hyperliquid_portfolio)

    base_source_message = self.broker.source_message.split(" + Loaded Hyperliquid account ")[0]
    source_message = f"{base_source_message} + {hyperliquid_source_message}"
    self.broker.set_portfolio(merged_portfolio, source_message)
    self.last_hyperliquid_cash_adjustment = hyperliquid_portfolio.cash

    if hasattr(self, "hyperliquid_status_var"):
        self.hyperliquid_status_var.set("Hyperliquid: synced")

    return hyperliquid_source_message, format_hyperliquid_snapshot(snapshot, hyperliquid_portfolio)
