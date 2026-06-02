from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Type

from app.analytics.hyperliquid_chain_health import (
    assess_hyperliquid_chain_health,
    format_hyperliquid_chain_health_report,
)
from app.brokers.hyperliquid.client import HyperliquidInfoClient


def install_hyperliquid_chain_health_extension(app_cls: Type[tk.Tk]) -> None:
    """Add a read-only Hyperliquid validator/chain health check."""

    app_cls.refresh_hyperliquid_chain_health = _refresh_hyperliquid_chain_health  # type: ignore[attr-defined]
    app_cls.refresh_hyperliquid_chain_health_workspace = _refresh_hyperliquid_chain_health_workspace  # type: ignore[attr-defined]

    previous_build_order_panel = app_cls._build_order_panel

    def build_order_panel_with_chain_health(self: tk.Tk, parent: ttk.Frame) -> None:
        previous_build_order_panel(self, parent)
        self.after_idle(lambda app=self: _install_cockpit_chain_health_button(app))

    app_cls._build_order_panel = build_order_panel_with_chain_health  # type: ignore[method-assign]
    _wrap_hyperliquid_workspace_builder()


def _refresh_hyperliquid_chain_health(self: tk.Tk) -> None:
    status = getattr(self, "hyperliquid_status_var", None)
    if hasattr(status, "set"):
        status.set("Hyperliquid chain health: checking...")

    try:
        client = HyperliquidInfoClient()
        snapshot = client.fetch_validator_health_snapshot()
        assessment = assess_hyperliquid_chain_health(snapshot)
        report = format_hyperliquid_chain_health_report(snapshot, assessment)
        _set_output_text(self, report)
        if hasattr(status, "set"):
            score = "--" if assessment.score is None else f"{assessment.score}/100"
            status.set(f"Hyperliquid chain health: {assessment.temperature} {score}")
    except Exception as exc:
        if hasattr(status, "set"):
            status.set("Hyperliquid chain health: failed")
        messagebox.showerror("Hyperliquid chain health failed", str(exc))


def _refresh_hyperliquid_chain_health_workspace(self: tk.Tk) -> None:
    workspace_output = getattr(self, "hyperliquid_trading_preview_text", None)
    if workspace_output is not None:
        self.preview_text = workspace_output
    _refresh_hyperliquid_chain_health(self)


def _set_output_text(self: tk.Tk, report: str) -> None:
    setter = getattr(self, "_set_preview_text", None)
    if callable(setter):
        setter(report)
        return

    output = getattr(self, "preview_text", None)
    if output is None:
        return
    output.configure(state=tk.NORMAL)
    output.delete("1.0", tk.END)
    output.insert(tk.END, report)
    output.configure(state=tk.DISABLED)


def _install_cockpit_chain_health_button(app: tk.Tk) -> None:
    summary = _find_labelframe(app, "Portfolio Risk Console")
    if summary is None:
        return
    actions = _action_frame(summary)
    if actions is None:
        return
    if _has_chain_health_button(actions):
        return

    actions.columnconfigure(3, weight=1, uniform="risk_console_actions")
    button = ttk.Button(
        actions,
        text="Chain Vibe Check",
        command=getattr(app, "refresh_hyperliquid_chain_health"),
        style="Compact.TButton",
    )
    button._hyperliquid_chain_health_button = True  # type: ignore[attr-defined]
    button.grid(row=0, column=3, sticky="ew", padx=(6, 0), pady=(2, 3))


def _wrap_hyperliquid_workspace_builder() -> None:
    try:
        from app.ui import options_lab_extension
    except Exception:
        return

    if getattr(options_lab_extension, "_hyperliquid_chain_health_wrapped", False):
        return

    original = options_lab_extension._build_hyperliquid_trading_tab

    def build_hyperliquid_trading_tab_with_chain_health(self: tk.Tk, parent: ttk.Frame) -> None:
        original(self, parent)
        self.after_idle(lambda app=self, root=parent: _install_workspace_chain_health_button(app, root))

    options_lab_extension._build_hyperliquid_trading_tab = build_hyperliquid_trading_tab_with_chain_health  # type: ignore[attr-defined]
    options_lab_extension._hyperliquid_chain_health_wrapped = True  # type: ignore[attr-defined]


def _install_workspace_chain_health_button(app: tk.Tk, root: tk.Misc) -> None:
    header = _find_labelframe(root, "Hyperliquid Trading Workspace")
    if header is None or _has_chain_health_button(header):
        return
    header.columnconfigure(3, weight=0)
    button = ttk.Button(
        header,
        text="Chain Vibe Check",
        command=getattr(app, "refresh_hyperliquid_chain_health_workspace"),
    )
    button._hyperliquid_chain_health_button = True  # type: ignore[attr-defined]
    button.grid(row=0, column=3, sticky="e")


def _has_chain_health_button(parent: tk.Misc) -> bool:
    for child in parent.winfo_children():
        if getattr(child, "_hyperliquid_chain_health_button", False):
            return True
    return False


def _find_labelframe(root: tk.Misc, title: str) -> ttk.LabelFrame | None:
    for child in root.winfo_children():
        try:
            if isinstance(child, ttk.LabelFrame) and str(child.cget("text")) == title:
                return child
        except tk.TclError:
            pass
        found = _find_labelframe(child, title)
        if found is not None:
            return found
    return None


def _action_frame(summary: ttk.LabelFrame) -> ttk.Frame | None:
    for child in summary.winfo_children():
        try:
            info = child.grid_info()
        except tk.TclError:
            continue
        if str(info.get("row")) == "1" and isinstance(child, ttk.Frame):
            return child
    return None
