from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox
from typing import Callable, Type

from app.brokers.schwab.account_adapter import (
    format_schwab_account_snapshot,
    portfolio_from_schwab_account,
)


# Only true authorization failures should clear cached Schwab tokens.
# Provider/account-service 5xx responses are transient sync failures, not proof
# that the saved OAuth grant is invalid.
_REAUTH_STATUS_CODES = {401, 403}
_TEMPORARY_PROVIDER_STATUS_CODES = {500, 502, 503, 504}


def install_schwab_sync_report_extension(app_cls: Type[tk.Tk]) -> None:
    """Give Schwab sync the same rich report treatment as Hyperliquid sync."""

    previous_sync_snapshot = getattr(app_cls, "_sync_schwab_account_snapshot", None)
    if callable(previous_sync_snapshot):
        app_cls._sync_schwab_account_snapshot = _wrap_schwab_snapshot_sync(previous_sync_snapshot)  # type: ignore[method-assign]

    app_cls.connect_schwab = _connect_schwab_with_report  # type: ignore[method-assign]
    app_cls.refresh_schwab_account = _refresh_schwab_account_with_report  # type: ignore[method-assign]


def _connect_schwab_with_report(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        self.schwab_session = session
        report = _sync_schwab_account_report_with_reauth_fallback(self, session)
        if report is None:
            return
        _mark_schwab_sync_status(self, "success")
        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(report)
    except Exception as exc:
        if _is_temporary_schwab_provider_error(exc):
            _mark_schwab_sync_status(self, "failure")
            self.schwab_status_var.set("Schwab session: connected; retry account sync")
            self._set_preview_text(_schwab_account_refresh_failure_report(exc))
            return
        _mark_schwab_sync_status(self, "failure")
        self.schwab_session = None
        self.schwab_status_var.set("Schwab session: not connected")
        messagebox.showerror("Schwab connect failed", str(exc))


def _refresh_schwab_account_with_report(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        report = _sync_schwab_account_report_with_reauth_fallback(self, session)
        if report is None:
            return
        _mark_schwab_sync_status(self, "success")
        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(report)
    except Exception as exc:
        if _is_temporary_schwab_provider_error(exc):
            _mark_schwab_sync_status(self, "failure")
            self.schwab_status_var.set("Schwab session: connected; retry account sync")
            self._set_preview_text(_schwab_account_refresh_failure_report(exc))
            return
        _mark_schwab_sync_status(self, "failure")
        messagebox.showerror("Schwab account refresh failed", str(exc))


def _wrap_schwab_snapshot_sync(previous_sync_snapshot: Callable[[tk.Tk, object], str]) -> Callable[[tk.Tk, object], str]:
    def sync_snapshot_with_reauth_fallback(self: tk.Tk, session: object) -> str:
        try:
            result = previous_sync_snapshot(self, session)
            _mark_schwab_sync_status(self, "success")
            return result
        except Exception as exc:
            if not _should_force_schwab_reauthorization(exc):
                _mark_schwab_sync_status(self, "failure")
                raise

            retry_session = _force_schwab_reauthorization(self, session, exc)
            if retry_session is None:
                _mark_schwab_sync_status(self, "failure")
                raise RuntimeError("Schwab reauthorization canceled; no authorization was provided.") from exc
            try:
                result = previous_sync_snapshot(self, retry_session)
                _mark_schwab_sync_status(self, "success")
                return result
            except Exception:
                _mark_schwab_sync_status(self, "failure")
                raise

    return sync_snapshot_with_reauth_fallback


def _sync_schwab_account_report_with_reauth_fallback(self: tk.Tk, session) -> str | None:
    """Sync once, but force a fresh browser authorization only for auth failures."""

    try:
        return _sync_schwab_account_report(self, session)
    except Exception as exc:
        if not _should_force_schwab_reauthorization(exc):
            raise

        retry_session = _force_schwab_reauthorization(self, session, exc)
        if retry_session is None:
            return None

        return _sync_schwab_account_report(self, retry_session)


def _force_schwab_reauthorization(self: tk.Tk, session, exc: Exception):
    # Clear cached auth only when Schwab explicitly rejects authorization. Do not
    # clear tokens for provider-side 5xx/account-sync outages.
    clear_cached_authorization = getattr(session, "clear_cached_authorization", None)
    if callable(clear_cached_authorization):
        clear_cached_authorization()
    self.schwab_session = None
    self.schwab_status_var.set("Schwab session: saved authorization rejected; login required")
    self._set_preview_text(_schwab_reauthorization_required_report(exc))

    retry_session = self._authorize_schwab_session()
    if retry_session is None:
        return None

    self.schwab_session = retry_session
    return retry_session


def _sync_schwab_account_report(self: tk.Tk, session) -> str:
    """Fetch Schwab once, update the cockpit, and return a detailed report."""

    status_code, account_payload = session.get_account(fields="positions")
    if status_code != 200:
        raise RuntimeError(f"Schwab account fetch returned HTTP {status_code}: {account_payload}")

    portfolio, source_message = portfolio_from_schwab_account(account_payload)
    self.broker.set_portfolio(portfolio, source_message)
    self.last_hyperliquid_cash_adjustment = 0.0
    self.refresh_portfolio()
    _mark_schwab_sync_status(self, "success")
    return format_schwab_account_snapshot(account_payload, portfolio)


def _mark_schwab_sync_status(self: tk.Tk, status: str) -> None:
    setter = getattr(self, "set_schwab_sync_status", None)
    if callable(setter):
        try:
            setter(status)
            return
        except Exception:
            pass
    var = getattr(self, "schwab_sync_status_var", None)
    if var is not None:
        try:
            var.set("✓ Synced" if status == "success" else "✕ Sync failed")
        except Exception:
            pass


def _should_force_schwab_reauthorization(exc: Exception) -> bool:
    status_code = _extract_http_status_code(exc)
    if status_code in _REAUTH_STATUS_CODES:
        return True
    if status_code in _TEMPORARY_PROVIDER_STATUS_CODES:
        return False

    text = str(exc).lower()
    auth_markers = (
        "invalid_grant",
        "invalid token",
        "unauthorized",
        "forbidden",
        "token expired",
        "refresh token",
    )
    return any(marker in text for marker in auth_markers)


def _extract_http_status_code(exc: Exception) -> int | None:
    match = re.search(r"http\s+(\d{3})", str(exc), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _is_temporary_schwab_provider_error(exc: Exception) -> bool:
    status_code = _extract_http_status_code(exc)
    if status_code in _TEMPORARY_PROVIDER_STATUS_CODES:
        return True
    text = str(exc).lower()
    return "temporarily unavailable" in text or "server error" in text


def _schwab_reauthorization_required_report(exc: Exception) -> str:
    return (
        "SCHWAB REAUTHORIZATION REQUIRED\n"
        "===============================\n\n"
        "Schwab rejected the saved authorization while the app tried to fetch balances and positions.\n\n"
        f"Provider response: {exc}\n\n"
        "What the app is doing now:\n"
        "- Cleared the in-memory Schwab session and saved local token cache.\n"
        "- Opened the Schwab authorization page so you can sign in again.\n"
        "- Will retry Sync Schwab with the new authorization code after you paste it.\n\n"
        "No order was previewed, submitted, replaced, or canceled."
    )


def _schwab_account_refresh_failure_report(exc: Exception) -> str:
    return (
        "SCHWAB ACCOUNT SYNC TEMPORARILY FAILED\n"
        "=====================================\n\n"
        "The saved Schwab authorization was kept because this looks like a provider/account-sync failure, not a rejected OAuth token.\n\n"
        f"Provider response: {exc}\n\n"
        "What the app did:\n"
        "- Kept the current local/cached portfolio visible.\n"
        "- Kept the saved Schwab authorization instead of forcing a browser login.\n"
        "- Did not submit, preview, replace, or cancel any order.\n\n"
        "Next step: click Sync Schwab again in a moment, or use Reset Session only if Schwab explicitly rejects authorization."
    )
