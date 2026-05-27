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
REFRESH_DUE_AFTER_MS = 5 * 60 * 1000
AUTO_REFRESH_AFTER_MS = 1 * 60 * 1000
AUTO_REFRESH_ENV_KEY = "COCKPIT_AUTO_REFRESH_MS"
_AUTO_REFRESH_OUTPUT_PREFIXES = (
    "PORTFOLIO REFRESH",
)


def install_unified_refresh_extension(app_cls: Type[tk.Tk]) -> None:
    """Collapse Schwab/Hyperliquid refresh controls into one portfolio refresh."""

    previous_build_order_panel = app_cls._build_order_panel
    app_cls._build_order_panel = _wrap_build_order_panel(previous_build_order_panel)  # type: ignore[method-assign]
    app_cls.refresh_connected_portfolio = _refresh_connected_portfolio  # type: ignore[attr-defined]
    app_cls.refresh_connected_portfolio_silent = _refresh_connected_portfolio_silent  # type: ignore[attr-defined]


def _wrap_build_order_panel(previous_build_order_panel: Callable[[tk.Tk, ttk.Frame], None]) -> Callable[[tk.Tk, ttk.Frame], None]:
    def build_order_panel_with_unified_refresh(self: tk.Tk, parent: ttk.Frame) -> None:
        previous_build_order_panel(self, parent)
        _replace_connection_refresh_controls(self)

    return build_order_panel_with_unified_refresh


def _replace_connection_refresh_controls(self: tk.Tk) -> None:
    connections_group = _find_label_frame_by_text(self, "Connections")
    if connections_group is None:
        return

    _configure_refresh_status_styles(self)

    for child in list(connections_group.winfo_children()):
        if not isinstance(child, ttk.Button):
            continue
        label = str(child.cget("text"))
        if label == "Refresh Schwab":
            child.configure(text="Refresh Portfolio", command=self.refresh_connected_portfolio)
            child.grid_configure(row=1, column=0, columnspan=1, sticky="ew", padx=(0, 6))
            _ensure_refresh_status_label(self, connections_group)
        elif label == "Reset Session":
            child.destroy()

    _schedule_auto_refresh(self)


def _configure_refresh_status_styles(self: tk.Tk) -> None:
    style = ttk.Style(self)
    style.configure("RefreshSuccess.TLabel", background="#f8fafc", foreground="#047857", font=("Segoe UI", 9, "bold"))
    style.configure("RefreshDue.TLabel", background="#f8fafc", foreground="#b91c1c", font=("Segoe UI", 9, "bold"))
    style.configure("RefreshWorking.TLabel", background="#f8fafc", foreground="#2563eb", font=("Segoe UI", 9, "bold"))
    style.configure("RefreshIdle.TLabel", background="#f8fafc", foreground="#64748b", font=("Segoe UI", 9, "bold"))


def _ensure_refresh_status_label(self: tk.Tk, parent: ttk.LabelFrame) -> None:
    existing = getattr(self, "refresh_portfolio_status_label", None)
    if existing is not None and existing.winfo_exists():
        return

    self.refresh_portfolio_status_var = tk.StringVar(value="")
    self.refresh_portfolio_status_label = ttk.Label(
        parent,
        textvariable=self.refresh_portfolio_status_var,
        style="RefreshIdle.TLabel",
        anchor=tk.W,
    )
    self.refresh_portfolio_status_label.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(0, 4))
    self.refresh_portfolio_due_after_id = None
    self.refresh_portfolio_auto_after_id = None
    self.refresh_portfolio_auto_enabled = True
    self.refresh_portfolio_auto_running = False


def _set_refresh_status(self: tk.Tk, text: str, style_name: str) -> None:
    label = getattr(self, "refresh_portfolio_status_label", None)
    var = getattr(self, "refresh_portfolio_status_var", None)
    if label is None or var is None:
        return
    var.set(text)
    label.configure(style=style_name)


def _cancel_refresh_due_timer(self: tk.Tk) -> None:
    after_id = getattr(self, "refresh_portfolio_due_after_id", None)
    if after_id is None:
        return
    try:
        self.after_cancel(after_id)
    except Exception:
        pass
    self.refresh_portfolio_due_after_id = None


def _schedule_refresh_due_status(self: tk.Tk) -> None:
    _cancel_refresh_due_timer(self)
    self.refresh_portfolio_due_after_id = self.after(
        REFRESH_DUE_AFTER_MS,
        lambda: _set_refresh_status(self, "● refresh due", "RefreshDue.TLabel"),
    )


def _auto_refresh_interval_ms() -> int:
    raw_value = os.getenv(AUTO_REFRESH_ENV_KEY, "").strip()
    if not raw_value:
        return AUTO_REFRESH_AFTER_MS
    try:
        interval = int(raw_value)
    except ValueError:
        return AUTO_REFRESH_AFTER_MS
    return max(interval, 60_000)


def _cancel_auto_refresh(self: tk.Tk) -> None:
    after_id = getattr(self, "refresh_portfolio_auto_after_id", None)
    if after_id is None:
        return
    try:
        self.after_cancel(after_id)
    except Exception:
        pass
    self.refresh_portfolio_auto_after_id = None


def _schedule_auto_refresh(self: tk.Tk) -> None:
    if not getattr(self, "refresh_portfolio_auto_enabled", True):
        return
    _cancel_auto_refresh(self)
    self.refresh_portfolio_auto_after_id = self.after(
        _auto_refresh_interval_ms(),
        lambda app=self: _run_auto_refresh(app),
    )


def _run_auto_refresh(self: tk.Tk) -> None:
    if getattr(self, "refresh_portfolio_auto_running", False):
        _schedule_auto_refresh(self)
        return
    try:
        if not self.winfo_exists():
            return
    except Exception:
        return

    self.refresh_portfolio_auto_running = True
    try:
        _refresh_connected_portfolio(self, automated=True)
    finally:
        self.refresh_portfolio_auto_running = False
        _schedule_auto_refresh(self)


def _find_label_frame_by_text(root: tk.Widget, text: str) -> ttk.LabelFrame | None:
    for child in root.winfo_children():
        if isinstance(child, ttk.LabelFrame) and str(child.cget("text")) == text:
            return child
        nested = _find_label_frame_by_text(child, text)
        if nested is not None:
            return nested
    return None


def _active_output_allows_auto_refresh_update(output: tk.Text) -> bool:
    """Only overwrite the output if the user is already viewing auto/manual portfolio refresh output.

    Auto-refresh should keep the portfolio table live without stealing the output
    panel from a manual Hyperliquid sync assessment, Perp What-If, Tech Analysis,
    Position Size, or any other report the user explicitly opened. Manual refreshes
    still replace the output as before.
    """

    try:
        content = output.get("1.0", "end-1c").lstrip()
    except Exception:
        return True
    if not content:
        return True
    return any(content.startswith(prefix) for prefix in _AUTO_REFRESH_OUTPUT_PREFIXES)


def _set_refresh_output_text(self: tk.Tk, content: str, preserve_scroll: bool) -> None:
    if not preserve_scroll:
        self._set_preview_text(content)
        return

    output = getattr(self, "preview_text", None)
    if output is None:
        self._set_preview_text(content)
        return

    if not _active_output_allows_auto_refresh_update(output):
        return

    try:
        previous_top, previous_bottom = output.yview()
        was_at_bottom = previous_bottom >= 0.995
    except Exception:
        previous_top = 0.0
        was_at_bottom = False

    self._set_preview_text(content)

    def restore_scroll() -> None:
        try:
            if was_at_bottom:
                output.yview_moveto(1.0)
            else:
                output.yview_moveto(previous_top)
        except Exception:
            return

    restore_scroll()
    try:
        output.after_idle(restore_scroll)
    except Exception:
        pass


def _refresh_connected_portfolio_silent(self: tk.Tk) -> None:
    _refresh_connected_portfolio(self, automated=True)


def _refresh_connected_portfolio(self: tk.Tk, automated: bool = False) -> None:
    """Refresh Schwab, then Hyperliquid, through one user-facing action."""

    _cancel_refresh_due_timer(self)
    _set_refresh_status(self, "↻ auto-refreshing..." if automated else "↻ refreshing...", "RefreshWorking.TLabel")

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
        hyperliquid_source_message, hyperliquid_preview = _sync_hyperliquid_account_silent(self, automated=automated)
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

    _set_refresh_output_text(self, "\n".join(results), preserve_scroll=automated)

    if schwab_error or hyperliquid_error:
        failed = []
        if schwab_error:
            failed.append("Schwab")
        if hyperliquid_error:
            failed.append("Hyperliquid")
        _set_refresh_status(self, "● auto-refresh failed" if automated else "● refresh failed", "RefreshDue.TLabel")
        if not automated:
            messagebox.showerror("Portfolio refresh incomplete", f"Could not refresh: {', '.join(failed)}")
        return

    _set_refresh_status(self, "✓ auto-refreshed" if automated else "✓ refreshed", "RefreshSuccess.TLabel")
    _schedule_refresh_due_status(self)


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


def _sync_hyperliquid_account_silent(self: tk.Tk, automated: bool = False) -> tuple[str, str | None]:
    default_address = _hyperliquid_address_from_env()
    address = default_address
    if not address and not automated:
        address = simpledialog.askstring(
            "Hyperliquid Sync",
            "Enter your Hyperliquid master/sub-account wallet address.\n\n"
            "Tip: save HYPE_WALLET_ADDRESS=0x... in .env to skip this prompt.\n\n"
            "Use the account address, not the API/agent wallet address.",
        )
    if not address:
        raise RuntimeError("Hyperliquid refresh skipped; save HYPE_WALLET_ADDRESS=0x... in .env to enable automatic Hyperliquid sync.")

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
