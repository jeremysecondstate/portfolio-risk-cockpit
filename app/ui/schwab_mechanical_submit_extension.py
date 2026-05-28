from __future__ import annotations

import json
import os
import tkinter as tk
from typing import Any, Type


def install_schwab_mechanical_submit_extension(app_cls: Type[tk.Tk]) -> None:
    """Use mechanical checks instead of the removed DELETE ME confirmation field."""

    app_cls.submit_live_schwab_order_guarded = _submit_with_mechanical_checks  # type: ignore[method-assign]
    app_cls.show_live_submit_safety_review = _show_mechanical_submit_review  # type: ignore[method-assign]


def _submit_with_mechanical_checks(self: tk.Tk) -> None:
    try:
        payload = self.build_schwab_order_json_from_ui()
        summary = _validate_and_summarize_payload(self, payload)
    except Exception as exc:
        self._set_preview_text(_blocked_text(str(exc)))
        return

    if os.getenv("SCHWAB_ENABLE_LIVE_ORDERS", "").strip().lower() != "true":
        self._set_preview_text(_blocked_text("SCHWAB_ENABLE_LIVE_ORDERS=true is required in your local .env."))
        return

    max_dollars = float(os.getenv("SCHWAB_MAX_LIVE_ORDER_DOLLARS", "500"))
    if summary["estimated_dollars"] > max_dollars:
        self._set_preview_text(
            _blocked_text(
                f"Estimated order dollars ${summary['estimated_dollars']:,.2f} exceeds "
                f"SCHWAB_MAX_LIVE_ORDER_DOLLARS=${max_dollars:,.2f}."
            )
        )
        return

    try:
        session = self._authorize_schwab_session()
        if session is None:
            return

        preview_status_code, preview_payload = session.preview_order(payload)
        if isinstance(preview_payload, dict):
            self._record_schwab_preview_status(preview_payload)

        strategy = (preview_payload or {}).get("orderStrategy", {}) if isinstance(preview_payload, dict) else {}
        schwab_status = str(strategy.get("status") or "UNKNOWN").upper()
        if preview_status_code != 200 or schwab_status != "ACCEPTED":
            self._set_preview_text(
                _blocked_text(
                    f"Immediate Schwab preview was not accepted. HTTP {preview_status_code}, Schwab status {schwab_status}."
                )
                + "\n\nPreview response:\n"
                + str(preview_payload)
            )
            return

        submit_status_code, submit_payload, location = session.submit_live_order(payload)
        self.schwab_status_var.set("Schwab session: connected for this app run")
        self._set_preview_text(
            "SCHWAB ORDER SUBMIT RESULT\n"
            "==========================\n\n"
            f"Ticket: {summary['label']}\n"
            f"Estimated order dollars: ${summary['estimated_dollars']:,.2f}\n"
            f"HTTP Status: {submit_status_code}\n"
            f"Location: {location or '(none returned)'}\n"
            f"Response: {submit_payload if submit_payload is not None else '(empty response body)'}\n\n"
            "Submitted payload:\n"
            f"{json.dumps(payload, indent=2)}\n\n"
            "Use Recent Orders / Open Only to verify status. Use Cancel Order if needed."
        )
    except Exception as exc:
        self._set_preview_text(_blocked_text(f"Schwab submit failed: {exc}"))


def _show_mechanical_submit_review(self: tk.Tk) -> None:
    try:
        payload = self.build_schwab_order_json_from_ui()
        summary = _validate_and_summarize_payload(self, payload)
        max_dollars = float(os.getenv("SCHWAB_MAX_LIVE_ORDER_DOLLARS", "500"))
        env_status = "PASS" if os.getenv("SCHWAB_ENABLE_LIVE_ORDERS", "").strip().lower() == "true" else "BLOCKED"
        cap_status = "PASS" if summary["estimated_dollars"] <= max_dollars else "BLOCKED"
        self._set_preview_text(
            "LIVE SUBMIT MECHANICAL REVIEW\n"
            "=============================\n\n"
            f"Ticket: {summary['label']}\n"
            f"Estimated order dollars: ${summary['estimated_dollars']:,.2f}\n"
            f"Env gate SCHWAB_ENABLE_LIVE_ORDERS=true: {env_status}\n"
            f"Max-dollar gate ${max_dollars:,.2f}: {cap_status}\n"
            "Typed DELETE ME / PLACE checkpoint: removed from this flow.\n"
            "Submit flow: build validated payload -> immediate Schwab previewOrder -> submit only if preview is ACCEPTED.\n\n"
            "Payload that will be previewed/submitted:\n"
            f"{json.dumps(payload, indent=2)}"
        )
    except Exception as exc:
        self._set_preview_text(_blocked_text(str(exc)))


def _validate_and_summarize_payload(self: tk.Tk, payload: dict[str, Any]) -> dict[str, Any]:
    legs = payload.get("orderLegCollection") or []
    if not isinstance(legs, list) or not legs:
        raise ValueError("Order payload has no legs.")

    first_instrument = (legs[0].get("instrument") or {}) if isinstance(legs[0], dict) else {}
    asset_type = str(first_instrument.get("assetType") or "").upper()

    if asset_type == "OPTION":
        price = _require_float(str(payload.get("price") or ""), "Option order price")
        quantity = _require_float(str(legs[0].get("quantity") or ""), "Contracts")
        if price <= 0:
            raise ValueError("Option order price must be positive.")
        if quantity <= 0:
            raise ValueError("Contracts must be positive.")
        for leg in legs:
            instrument = leg.get("instrument") or {}
            if str(instrument.get("assetType") or "").upper() != "OPTION":
                raise ValueError("Every option-order leg must have assetType OPTION.")
            if not str(instrument.get("symbol") or "").strip():
                raise ValueError("Every option-order leg must include a Schwab option contract symbol.")
            instruction = str(leg.get("instruction") or "").upper()
            if instruction not in {"BUY_TO_OPEN", "SELL_TO_OPEN", "BUY_TO_CLOSE", "SELL_TO_CLOSE"}:
                raise ValueError(f"Unsupported option instruction: {instruction}")
        if payload.get("complexOrderStrategyType") == "VERTICAL" and len(legs) != 2:
            raise ValueError("A vertical option order must have exactly two legs.")
        return {
            "estimated_dollars": price * 100 * quantity,
            "label": f"{payload.get('orderType', 'OPTION')} option order, {quantity:g} contract(s), {len(legs)} leg(s)",
        }

    order = self._parse_order()
    if order.order_type.value.upper() != "LIMIT":
        raise ValueError("Only LIMIT stock/ETF orders are allowed for live submit.")
    if order.quantity <= 0:
        raise ValueError("Quantity must be positive.")
    if order.limit_price is None or order.limit_price <= 0:
        raise ValueError("A positive limit price is required.")
    return {
        "estimated_dollars": order.quantity * order.limit_price,
        "label": f"{order.side.value.upper()} {order.quantity:g} {order.symbol.strip().upper()} @ {order.limit_price}",
    }


def _blocked_text(reason: str) -> str:
    return (
        "SCHWAB SUBMIT BLOCKED\n"
        "=====================\n\n"
        f"{reason}\n\n"
        "No order was submitted."
    )


def _require_float(value: str, field: str) -> float:
    try:
        cleaned = str(value).strip().replace(",", "")
        if not cleaned:
            raise ValueError
        return float(cleaned)
    except ValueError as exc:
        raise ValueError(f"{field} must be a number.") from exc
