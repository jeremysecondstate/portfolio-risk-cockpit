from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Type

from app.brokers.hyperliquid.trading import (
    HyperliquidExecutionAdapter,
    HyperliquidOrderTicket,
    HyperliquidTradingConfig,
    HyperliquidTriggerTicket,
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
        adapter = HyperliquidExecutionAdapter()
        leverage_result = _apply_ticket_leverage_if_needed(self, adapter, normalized_ticket)
        result = adapter.submit(normalized_ticket)
        self.hyperliquid_status_var.set("Hyperliquid: submit attempted")
        _update_limit_price_if_needed(self, ticket, normalized_ticket)
        child_tickets = _attached_tpsl_tickets(self, normalized_ticket)
        child_result: object | None = None
        child_error: Exception | None = None
        if child_tickets:
            try:
                child_result = adapter.place_position_tpsl(child_tickets)
            except Exception as exc:
                child_error = exc
                self.hyperliquid_status_var.set("Hyperliquid: parent sent, TP/SL failed")
        elif _attach_tpsl_enabled(self):
            self.hyperliquid_status_var.set("Hyperliquid: parent sent, no TP/SL price entered")
        self._set_preview_text(
            "HYPERLIQUID LIVE SUBMIT RESULT\n"
            "==============================\n\n"
            f"{_price_adjustment_lines(ticket, normalized_ticket)}"
            f"{_leverage_result_lines(leverage_result)}"
            "Parent order response:\n"
            f"{result}\n\n"
            f"{_child_tpsl_result_lines(child_tickets, child_result, child_error)}"
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


def _apply_ticket_leverage_if_needed(self: tk.Tk, adapter: HyperliquidExecutionAdapter, ticket: HyperliquidOrderTicket) -> object | None:
    if ticket.reduce_only:
        return None
    leverage = _optional_int(_var_text(self, "hyperliquid_leverage_var"))
    if leverage is None:
        return None
    margin_mode = (_var_text(self, "hyperliquid_margin_mode_var") or "Cross").strip().lower()
    return adapter.update_leverage(ticket.coin, leverage, is_cross=margin_mode != "isolated")


def _optional_int(raw: str) -> int | None:
    text = str(raw or "").strip().lower().replace("x", "")
    if not text:
        return None
    try:
        value = int(float(text))
    except ValueError:
        return None
    return value if value >= 1 else None


def _leverage_result_lines(result: object | None) -> str:
    if result is None:
        return ""
    return f"Leverage update before parent order:\n{result}\n\n"


def _attach_tpsl_enabled(self: tk.Tk) -> bool:
    var = getattr(self, "hyperliquid_attach_tpsl_var", None)
    try:
        return bool(var.get()) if var is not None else False
    except Exception:
        return False


def _optional_price(raw: str) -> float | None:
    try:
        text = str(raw or "").strip().replace(",", "")
        if not text:
            return None
        value = float(text)
    except ValueError:
        return None
    return value if value > 0 else None


def _var_text(self: tk.Tk, name: str) -> str:
    var = getattr(self, name, None)
    if var is None:
        return ""
    try:
        return str(var.get())
    except Exception:
        return ""


def _attached_tpsl_tickets(self: tk.Tk, ticket: HyperliquidOrderTicket) -> list[HyperliquidTriggerTicket]:
    if not _attach_tpsl_enabled(self):
        return []
    tp_price = _optional_price(_var_text(self, "hyperliquid_target_price_var"))
    sl_price = _optional_price(
        _var_text(self, "hyperliquid_bad_price_var")
        or _var_text(self, "stop_price_var")
    )
    close_is_buy = not ticket.is_buy
    triggers: list[HyperliquidTriggerTicket] = []
    if tp_price is not None:
        triggers.append(
            HyperliquidTriggerTicket(
                coin=ticket.coin,
                is_buy=close_is_buy,
                size=ticket.size,
                trigger_price=tp_price,
                tpsl="tp",
            )
        )
    if sl_price is not None:
        triggers.append(
            HyperliquidTriggerTicket(
                coin=ticket.coin,
                is_buy=close_is_buy,
                size=ticket.size,
                trigger_price=sl_price,
                tpsl="sl",
            )
        )
    return triggers


def _child_tpsl_result_lines(
    child_tickets: list[HyperliquidTriggerTicket],
    child_result: object | None,
    child_error: Exception | None,
) -> str:
    if not child_tickets:
        return "Attached TP/SL: no child trigger order submitted.\n\n"
    lines = ["Attached TP/SL child orders:"]
    for trigger in child_tickets:
        side = "BUY" if trigger.is_buy else "SELL"
        label = "take-profit" if trigger.tpsl == "tp" else "stop-loss"
        lines.append(f"- {label}: {side} reduce-only {trigger.size:g} {trigger.coin} at trigger ${trigger.trigger_price:,.4f}")
    if child_error is not None:
        lines.extend(
            [
                "",
                "Child TP/SL result: FAILED after parent order was sent.",
                f"Reason: {child_error}",
                "Use TP/SL Selected after the parent fill is visible to create the missing protection order.",
                "",
            ]
        )
        return "\n".join(lines)
    lines.extend(["", "Child TP/SL result:", str(child_result), ""])
    return "\n".join(lines)
