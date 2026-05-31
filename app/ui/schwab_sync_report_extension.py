from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Type

from app.brokers.schwab.account_adapter import (
    format_schwab_account_snapshot,
    portfolio_from_schwab_account,
)


def install_schwab_sync_report_extension(app_cls: Type[tk.Tk]) -> None:
    """Give Schwab sync the same rich report treatment as Hyperliquid sync."""

    app_cls.connect_schwab = _connect_schwab_with_report  # type: ignore[method-assign]
    app_cls.refresh_schwab_account = _refresh_schwab_account_with_report  # type: ignore[method-assign]


def _connect_schwab_with_report(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        self.schwab_session = session
        report = _sync_schwab_account_report(self, session)
        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(report)
    except Exception as exc:
        if _is_temporary_schwab_provider_error(exc):
            self.schwab_status_var.set("Schwab session: connected; account refresh unavailable")
            self._set_preview_text(_schwab_account_refresh_failure_report(exc))
            return
        self.schwab_session = None
        self.schwab_status_var.set("Schwab session: not connected")
        messagebox.showerror("Schwab connect failed", str(exc))


def _refresh_schwab_account_with_report(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        report = _sync_schwab_account_report(self, session)
        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(report)
    except Exception as exc:
        if _is_temporary_schwab_provider_error(exc):
            self.schwab_status_var.set("Schwab session: connected; account refresh unavailable")
            self._set_preview_text(_schwab_account_refresh_failure_report(exc))
            return
        messagebox.showerror("Schwab account refresh failed", str(exc))


def _sync_schwab_account_report(self: tk.Tk, session) -> str:
    """Fetch Schwab once, update the cockpit, and return a detailed report."""

    status_code, account_payload = session.get_account(fields="positions")
    if status_code != 200:
        raise RuntimeError(f"Schwab account fetch returned HTTP {status_code}: {account_payload}")

    portfolio, source_message = portfolio_from_schwab_account(account_payload)
    self.broker.set_portfolio(portfolio, source_message)
    self.last_hyperliquid_cash_adjustment = 0.0
    self.refresh_portfolio()
    return format_schwab_account_snapshot(account_payload, portfolio)


def _is_temporary_schwab_provider_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "http 500" in text or "unexpected error" in text or "temporarily unavailable" in text


def _schwab_account_refresh_failure_report(exc: Exception) -> str:
    return (
        "SCHWAB ACCOUNT REFRESH UNAVAILABLE\n"
        "==================================\n\n"
        "Schwab returned a temporary server-side error while the app tried to fetch balances and positions.\n\n"
        f"Provider response: {exc}\n\n"
        "What the app did:\n"
        "- Kept the current local/cached portfolio visible.\n"
        "- Did not submit, preview, replace, or cancel any order.\n"
        "- Left the Schwab session connected so you can retry without resetting login.\n\n"
        "Next step: wait a moment and use Sync Schwab / Refresh Portfolio again. If Schwab keeps returning HTTP 500, this is usually a provider-side account endpoint outage rather than a ticket problem."
    )
