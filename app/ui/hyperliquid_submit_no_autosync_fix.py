from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Type

from app.brokers.hyperliquid.trading import (
    HyperliquidExecutionAdapter,
    HyperliquidTradingConfig,
    format_hyperliquid_limit_price,
    normalize_hyperliquid_ticket_limit_price,
)


def install_hyperliquid_submit_no_autosync_fix(app_cls: Type[tk.Tk]) -> None:
    """Keep Hyperliquid live-submit responses visible until the user replaces them."""

    app_cls.show_hyperliquid_spot_live_submit_safety_review = _show_hyperliquid_spot_live_submit_no_autosync  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_live_submit_safety_review = _show_hyperliquid_perp_live_submit_no_autosync  # type: ignore[attr-defined]


def _show_hyperliquid_spot_live_submit_no_autosync(self: tk.Tk) -> None:
    try:
        ticket = self.parse_hyperliquid_spot_ticket()
        normalized_ticket = normalize_hyperliquid_ticket_limit_price(ticket)
        config = HyperliquidTradingConfig()
        self._set_preview_text(config.live_review_text(normalized_ticket))
        result = HyperliquidExecutionAdapter().submit(normalized_ticket)
        self.hyperliquid_status_var.set("Hyperliquid spot: submit attempted")
        _update_limit_price_if_needed(self, ticket, normalized_ticket)
        self._set_preview_text(
            "HYPERLIQUID SPOT LIVE SUBMIT RESULT\n"
            "===================================\n\n"
            f"{_price_adjustment_lines(ticket, normalized_ticket)}"
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
        normalized_ticket = normalize_hyperliquid_ticket_limit_price(ticket)
        config = HyperliquidTradingConfig()
        self._set_preview_text(config.live_review_text(normalized_ticket))
        result = HyperliquidExecutionAdapter().submit(normalized_ticket)
        self.hyperliquid_status_var.set("Hyperliquid: submit attempted")
        _update_limit_price_if_needed(self, ticket, normalized_ticket)
        self._set_preview_text(
            "HYPERLIQUID LIVE SUBMIT RESULT\n"
            "==============================\n\n"
            f"{_price_adjustment_lines(ticket, normalized_ticket)}"
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


def _update_limit_price_if_needed(self: tk.Tk, ticket, normalized_ticket) -> None:
    if normalized_ticket.limit_price == ticket.limit_price:
        return
    try:
        self.limit_price_var.set(format_hyperliquid_limit_price(normalized_ticket.limit_price))
    except Exception:
        return


def _price_adjustment_lines(ticket, normalized_ticket) -> str:
    if normalized_ticket.limit_price == ticket.limit_price:
        return ""
    return (
        "Limit price was adjusted to Hyperliquid's accepted price grid.\n"
        f"- Original limit: {format_hyperliquid_limit_price(ticket.limit_price)}\n"
        f"- Submitted limit: {format_hyperliquid_limit_price(normalized_ticket.limit_price)}\n\n"
    )
