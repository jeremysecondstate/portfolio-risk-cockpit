from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Type

from app.brokers.hyperliquid.trading import HyperliquidExecutionAdapter, HyperliquidTradingConfig


def install_hyperliquid_submit_no_autosync_fix(app_cls: Type[tk.Tk]) -> None:
    """Keep Hyperliquid live-submit responses visible until the user replaces them."""

    app_cls.show_hyperliquid_spot_live_submit_safety_review = _show_hyperliquid_spot_live_submit_no_autosync  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_live_submit_safety_review = _show_hyperliquid_perp_live_submit_no_autosync  # type: ignore[attr-defined]


def _show_hyperliquid_spot_live_submit_no_autosync(self: tk.Tk) -> None:
    try:
        ticket = self.parse_hyperliquid_spot_ticket()
        config = HyperliquidTradingConfig()
        self._set_preview_text(config.live_review_text(ticket))
        result = HyperliquidExecutionAdapter().submit(ticket)
        self.hyperliquid_status_var.set("Hyperliquid spot: submit attempted")
        self._set_preview_text(
            "HYPERLIQUID SPOT LIVE SUBMIT RESULT\n"
            "===================================\n\n"
            f"{result}\n\n"
            "No automatic portfolio sync was run.\n"
            "Use Open Only to verify active orders, or Connect Hyperliquid to refresh the account snapshot."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid spot: live blocked")
        messagebox.showerror("Hyperliquid spot live submit blocked", str(exc))


def _show_hyperliquid_perp_live_submit_no_autosync(self: tk.Tk) -> None:
    try:
        ticket = self.parse_hyperliquid_ticket()
        config = HyperliquidTradingConfig()
        self._set_preview_text(config.live_review_text(ticket))
        result = HyperliquidExecutionAdapter().submit(ticket)
        self.hyperliquid_status_var.set("Hyperliquid: submit attempted")
        self._set_preview_text(
            "HYPERLIQUID LIVE SUBMIT RESULT\n"
            "==============================\n\n"
            f"{result}\n\n"
            "No automatic portfolio sync was run.\n"
            "Use Open Only to verify active orders, or Connect Hyperliquid to refresh the account snapshot."
        )
    except NotImplementedError as exc:
        self.hyperliquid_status_var.set("Hyperliquid: hook missing")
        self._set_preview_text(
            "HYPERLIQUID LOCAL SUBMIT HOOK MISSING\n"
            "=====================================\n\n"
            f"{exc}\n\n"
            "Wire HyperliquidExecutionAdapter._local_signed_submit() locally."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: live blocked")
        messagebox.showerror("Hyperliquid live submit blocked", str(exc))
