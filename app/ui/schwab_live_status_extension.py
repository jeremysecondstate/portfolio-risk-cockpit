from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import messagebox
from typing import Type


def install_schwab_live_status_extension(app_cls: Type[tk.Tk]) -> None:
    """Replace the noisy Schwab live-status screen with concise readiness text."""

    app_cls.show_live_submit_safety_review = _show_streamlined_schwab_live_status  # type: ignore[method-assign]


def _show_streamlined_schwab_live_status(self: tk.Tk) -> None:
    try:
        order = self._parse_order()
        schwab_order = self.build_schwab_order_json_from_ui()
    except Exception as exc:
        messagebox.showerror("Live status failed", str(exc))
        return

    enable_live = os.getenv("SCHWAB_ENABLE_LIVE_ORDERS", "").strip().lower() == "true"
    max_notional = _float_env("SCHWAB_MAX_LIVE_ORDER_DOLLARS", 500.0)
    order_type = order.order_type.value.upper()
    symbol = order.symbol.strip().upper()
    side = order.side.value.upper()
    tif = order.time_in_force.value.upper()
    limit_price = order.limit_price or 0.0
    estimated_notional = order.quantity * limit_price

    checks = [
        ("SCHWAB_ENABLE_LIVE_ORDERS=true", enable_live),
        ("LIMIT order", order_type == "LIMIT"),
        ("Positive quantity", order.quantity > 0),
        ("Positive limit price", limit_price > 0),
        (f"Notional <= ${max_notional:,.2f}", estimated_notional <= max_notional),
    ]
    ready = all(passed for _label, passed in checks)

    self._set_preview_text(
        "SCHWAB LIVE STATUS\n"
        "==================\n\n"
        f"Status: {'READY FOR LIVE SUBMIT BUTTON' if ready else 'BLOCKED — fix items below'}\n\n"
        "Current ticket:\n"
        f"- Symbol: {symbol}\n"
        f"- Side: {side}\n"
        f"- Type: {order_type}\n"
        f"- Quantity: {order.quantity:g}\n"
        f"- Limit price: {limit_price}\n"
        f"- Time in force: {tif}\n"
        f"- Estimated notional: ${estimated_notional:,.2f}\n\n"
        "Fast checks:\n"
        + "\n".join(f"- {label}: {'PASS' if passed else 'REQUIRED'}" for label, passed in checks)
        + "\n\n"
        "Schwab LIVE Submit still runs Schwab previewOrder immediately before submit and only continues if Schwab returns ACCEPTED.\n"
        "Use LIVE Submit when the ticket is correct. Use Cancel Order if you need to cancel an active order.\n\n"
        "Schwab order JSON:\n"
        f"{json.dumps(schwab_order, indent=2)}"
    )


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).strip())
    except ValueError:
        return default
