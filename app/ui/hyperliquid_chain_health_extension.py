from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
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
