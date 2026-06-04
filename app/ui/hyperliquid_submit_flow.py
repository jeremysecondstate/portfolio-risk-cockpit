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
    normalize_hyperliquid_ticket_for_wire,
    normalize_hyperliquid_ticket_limit_price,
)


def install_hyperliquid_submit_flow(app_cls: Type[tk.Tk]) -> None:
    """Keep Hyperliquid live-submit responses visible until the user replaces them."""

    app_cls.show_hyperliquid_spot_live_submit_safety_review = _show_hyperliquid_spot_live_submit_no_autosync  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_perp_live_submit_safety_review = _show_hyperliquid_perp_live_submit_safety_review  # type: ignore[attr-defined]
    app_cls.show_hyperliquid_live_submit_safety_review = _show_hyperliquid_perp_live_submit_safety_review  # type: ignore[attr-defined]


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


def _show_hyperliquid_perp_live_submit_safety_review(self: tk.Tk) -> None:
    try:
        _sync_perp_ticket_fields_to_shared(self)
        ticket = self.parse_hyperliquid_ticket()
        normalized_ticket = normalize_hyperliquid_ticket_for_wire(ticket)
        config = HyperliquidTradingConfig()
        self._set_preview_text(config.live_review_text(normalized_ticket))
        adapter = HyperliquidExecutionAdapter()
        leverage_result = _apply_ticket_leverage_if_needed(self, adapter, normalized_ticket)
        result = adapter.submit(normalized_ticket)
        self.hyperliquid_status_var.set("Hyperliquid perp: submit attempted")
        _update_ticket_fields_if_needed(self, ticket, normalized_ticket)
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
        sync_result = _sync_hyperliquid_account_best_effort(self)
        self._set_preview_text(
            "HYPERLIQUID PERP LIVE SUBMIT RESULT\n"
            "===================================\n\n"
            "HYPERLIQUID PERP LIVE SUBMIT\n"
            "This was submitted through the PERP order path, not the spot ticket flow.\n\n"
            f"{_perp_ticket_summary_lines(self, normalized_ticket)}"
            f"{_ticket_adjustment_lines(ticket, normalized_ticket)}"
            f"{_leverage_result_lines(leverage_result)}"
            "Parent submit result:\n"
            f"{result}\n\n"
            f"{_child_tpsl_result_lines(child_tickets, child_result, child_error)}"
            f"{sync_result}\n"
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
        self.hyperliquid_status_var.set("Hyperliquid perp: live blocked")
        messagebox.showerror("Hyperliquid perp live submit blocked", str(exc))


def _sync_perp_ticket_fields_to_shared(self: tk.Tk) -> None:
    mappings = (
        ("hyperliquid_perp_coin_var", "hyperliquid_coin_var"),
        ("hyperliquid_perp_symbol_var", "symbol_var"),
        ("hyperliquid_perp_side_var", "side_var"),
        ("hyperliquid_perp_order_type_var", "order_type_var"),
        ("hyperliquid_perp_quantity_var", "quantity_var"),
        ("hyperliquid_perp_limit_price_var", "limit_price_var"),
        ("hyperliquid_perp_target_price_var", "hyperliquid_target_price_var"),
        ("hyperliquid_perp_stop_price_var", "hyperliquid_bad_price_var"),
        ("hyperliquid_perp_stop_price_var", "stop_price_var"),
        ("hyperliquid_perp_tif_var", "hyperliquid_tif_var"),
        ("hyperliquid_perp_cancel_order_id_var", "cancel_order_id_var"),
        ("hyperliquid_perp_leverage_var", "hyperliquid_leverage_var"),
        ("hyperliquid_perp_margin_mode_var", "hyperliquid_margin_mode_var"),
        ("hyperliquid_perp_fee_rate_var", "hyperliquid_fee_rate_var"),
        ("hyperliquid_perp_reduce_only_var", "hyperliquid_reduce_only_var"),
        ("hyperliquid_perp_attach_tpsl_var", "hyperliquid_attach_tpsl_var"),
    )
    for source_name, target_name in mappings:
        source = getattr(self, source_name, None)
        target = getattr(self, target_name, None)
        if source is None or target is None:
            continue
        try:
            target.set(source.get())
        except Exception:
            continue


def _update_limit_price_if_needed(self: tk.Tk, ticket: HyperliquidOrderTicket, normalized_ticket: HyperliquidOrderTicket) -> None:
    if normalized_ticket.limit_price == ticket.limit_price:
        return
    try:
        self.limit_price_var.set(format_hyperliquid_limit_price(normalized_ticket.limit_price))
    except Exception:
        return


def _price_adjustment_lines(ticket: HyperliquidOrderTicket, normalized_ticket: HyperliquidOrderTicket) -> str:
    if normalized_ticket.limit_price == ticket.limit_price:
        return ""
    return (
        "Limit price was adjusted to Hyperliquid's accepted price grid.\n"
        f"- Original limit: {format_hyperliquid_limit_price(ticket.limit_price)}\n"
        f"- Submitted limit: {format_hyperliquid_limit_price(normalized_ticket.limit_price)}\n\n"
    )


def _update_ticket_fields_if_needed(self: tk.Tk, ticket: HyperliquidOrderTicket, normalized_ticket: HyperliquidOrderTicket) -> None:
    if normalized_ticket.limit_price != ticket.limit_price:
        _set_var_if_present(self, "limit_price_var", format_hyperliquid_limit_price(normalized_ticket.limit_price))
        _set_var_if_present(self, "hyperliquid_perp_limit_price_var", format_hyperliquid_limit_price(normalized_ticket.limit_price))
        _set_var_if_present(self, "hyperliquid_spot_limit_price_var", format_hyperliquid_limit_price(normalized_ticket.limit_price))
    if normalized_ticket.size != ticket.size:
        _set_var_if_present(self, "quantity_var", f"{normalized_ticket.size:g}")
        _set_var_if_present(self, "hyperliquid_perp_quantity_var", f"{normalized_ticket.size:g}")
        _set_var_if_present(self, "hyperliquid_spot_quantity_var", f"{normalized_ticket.size:g}")


def _set_var_if_present(self: tk.Tk, name: str, value: str) -> None:
    var = getattr(self, name, None)
    if var is None:
        return
    try:
        var.set(value)
    except Exception:
        return


def _ticket_adjustment_lines(ticket: HyperliquidOrderTicket, normalized_ticket: HyperliquidOrderTicket) -> str:
    lines: list[str] = []
    if normalized_ticket.limit_price != ticket.limit_price:
        lines.extend(
            [
                "Limit price was adjusted to Hyperliquid's accepted price grid.",
                f"- Original limit: {format_hyperliquid_limit_price(ticket.limit_price)}",
                f"- Submitted limit: {format_hyperliquid_limit_price(normalized_ticket.limit_price)}",
            ]
        )
    if normalized_ticket.size != ticket.size:
        lines.extend(
            [
                "Size was adjusted to Hyperliquid's accepted size precision.",
                f"- Original size: {ticket.size:g}",
                f"- Submitted size: {normalized_ticket.size:g}",
            ]
        )
    return "\n".join(lines) + ("\n\n" if lines else "")


def _perp_ticket_summary_lines(self: tk.Tk, ticket: HyperliquidOrderTicket) -> str:
    direction = "LONG" if ticket.is_buy else "SHORT"
    leverage = _var_text(self, "hyperliquid_leverage_var") or _var_text(self, "hyperliquid_perp_leverage_var") or "1"
    margin_mode = _var_text(self, "hyperliquid_margin_mode_var") or _var_text(self, "hyperliquid_perp_margin_mode_var") or "Cross"
    attach = "on" if _attach_tpsl_enabled(self) else "off"
    return "\n".join(
        [
            f"Coin / market: {ticket.coin}-PERP",
            f"Direction: {direction}",
            f"Size: {ticket.size:g}",
            f"Limit price: ${ticket.limit_price:,.4f}",
            f"Time in force: {ticket.tif}",
            f"Reduce only: {'yes' if ticket.reduce_only else 'no'}",
            f"Leverage shown for planning: {leverage}x",
            f"Margin mode shown for planning: {margin_mode}",
            f"Attach TP/SL: {attach}",
            "Leverage/margin mode may require a separate exchange update. Use Apply Leverage if needed before submit.",
            "",
        ]
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
    var = getattr(self, "hyperliquid_attach_tpsl_var", None) or getattr(self, "hyperliquid_perp_attach_tpsl_var", None)
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
    tp_price = _optional_price(
        _var_text(self, "hyperliquid_target_price_var")
        or _var_text(self, "hyperliquid_perp_target_price_var")
    )
    sl_price = _optional_price(
        _var_text(self, "hyperliquid_bad_price_var")
        or _var_text(self, "hyperliquid_perp_stop_price_var")
        or _var_text(self, "stop_price_var")
    )
    close_is_buy = not ticket.is_buy
    triggers: list[HyperliquidTriggerTicket] = []
    if tp_price is not None:
        _validate_tpsl_price("TP", ticket, tp_price)
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
        _validate_tpsl_price("SL", ticket, sl_price)
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


def _validate_tpsl_price(field: str, ticket: HyperliquidOrderTicket, price: float) -> None:
    try:
        from app.ui.hyperliquid_perp_ticket import _tpsl_scenario_readout

        readout = _tpsl_scenario_readout(field, ticket.limit_price, price, ticket.is_buy)
    except Exception:
        return
    if not readout.valid:
        raise ValueError(readout.warning or f"{field} trigger direction is invalid for this perp ticket.")


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


def _sync_hyperliquid_account_best_effort(self: tk.Tk) -> str:
    sync = getattr(self, "sync_hyperliquid_account", None)
    if not callable(sync):
        return "Hyperliquid account snapshot refresh: unavailable."
    try:
        sync()
    except Exception as exc:
        return f"Hyperliquid account snapshot refresh: failed ({exc})."
    return "Hyperliquid account snapshot refresh: attempted."
