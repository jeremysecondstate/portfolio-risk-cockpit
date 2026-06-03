from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.core.order_models import schwab_equity_session_duration


OPEN_ORDER_STATUSES = frozenset(
    {
        "ACCEPTED",
        "AWAITING_CONDITION",
        "AWAITING_MANUAL_REVIEW",
        "AWAITING_PARENT_ORDER",
        "AWAITING_RELEASE_TIME",
        "AWAITING_STOP_CONDITION",
        "AWAITING_UR_OUT",
        "NEW",
        "PENDING_ACKNOWLEDGEMENT",
        "PENDING_ACTIVATION",
        "PENDING_CANCEL",
        "PENDING_REPLACE",
        "QUEUED",
        "WORKING",
    }
)

REPLACE_ALLOWED_STATUSES = frozenset(
    {
        "ACCEPTED",
        "AWAITING_CONDITION",
        "AWAITING_MANUAL_REVIEW",
        "AWAITING_PARENT_ORDER",
        "AWAITING_RELEASE_TIME",
        "AWAITING_STOP_CONDITION",
        "AWAITING_UR_OUT",
        "NEW",
        "PENDING_ACKNOWLEDGEMENT",
        "PENDING_ACTIVATION",
        "QUEUED",
        "WORKING",
    }
)

TERMINAL_ORDER_STATUSES = frozenset({"CANCELED", "EXPIRED", "FILLED", "REJECTED", "REPLACED"})


@dataclass(frozen=True)
class SchwabOrderRow:
    order_id: str
    entered_time: str
    status: str
    symbol: str
    asset_type: str
    instruction: str
    quantity: float | None
    filled_quantity: float | None
    order_type: str
    price: float | None
    stop_price: float | None
    duration: str
    session: str
    account_hash: str
    complex_order_strategy_type: str
    raw_status: str
    raw: Mapping[str, Any]

    @property
    def remaining_quantity(self) -> float | None:
        if self.quantity is None:
            return None
        return max(self.quantity - (self.filled_quantity or 0.0), 0.0)

    @property
    def masked_account_hash(self) -> str:
        return mask_account_hash(self.account_hash)


def normalize_order_rows(payload: Any, *, account_hash: str = "") -> list[SchwabOrderRow]:
    if not isinstance(payload, list):
        return []
    rows = []
    for raw_order in payload:
        if isinstance(raw_order, Mapping):
            rows.append(order_to_row(raw_order, account_hash=account_hash))
    return rows


def open_order_rows(payload: Any, *, account_hash: str = "") -> list[SchwabOrderRow]:
    return [row for row in normalize_order_rows(payload, account_hash=account_hash) if is_open_order_status(row.status)]


def order_to_row(order: Mapping[str, Any], *, account_hash: str = "") -> SchwabOrderRow:
    legs = _order_legs(order)
    first_leg = legs[0] if legs else {}
    instrument = first_leg.get("instrument") if isinstance(first_leg, Mapping) else {}
    if not isinstance(instrument, Mapping):
        instrument = {}

    order_quantity = _first_number(first_leg, ("quantity",)) if isinstance(first_leg, Mapping) else None
    if order_quantity is None:
        order_quantity = _first_number(order, ("quantity",))

    order_id = _clean_string(order.get("orderId") or order.get("order_id") or order.get("id"))
    status = _clean_string(order.get("status") or "UNKNOWN").upper()
    symbol = _clean_string(instrument.get("symbol") or first_leg.get("finalSymbol") if isinstance(first_leg, Mapping) else "")
    if not symbol:
        symbol = _clean_string(order.get("symbol") or "UNKNOWN")

    return SchwabOrderRow(
        order_id=order_id or "UNKNOWN",
        entered_time=_clean_string(order.get("enteredTime") or order.get("entered_time") or order.get("closeTime")),
        status=status,
        symbol=symbol.upper() or "UNKNOWN",
        asset_type=_clean_string(instrument.get("assetType") or order.get("assetType") or "UNKNOWN").upper(),
        instruction=_clean_string(first_leg.get("instruction") if isinstance(first_leg, Mapping) else "UNKNOWN").upper()
        or "UNKNOWN",
        quantity=order_quantity,
        filled_quantity=_first_number(order, ("filledQuantity", "filled_quantity"))
        or (_first_number(first_leg, ("filledQuantity", "filled_quantity")) if isinstance(first_leg, Mapping) else None),
        order_type=_clean_string(order.get("orderType") or "UNKNOWN").upper(),
        price=_first_number(order, ("price", "limitPrice")),
        stop_price=_first_number(order, ("stopPrice", "stop_price")),
        duration=_clean_string(order.get("duration") or order.get("timeInForce") or "DAY").upper(),
        session=_clean_string(order.get("session") or "NORMAL").upper(),
        account_hash=account_hash,
        complex_order_strategy_type=_clean_string(
            order.get("complexOrderStrategyType") or order.get("orderStrategyType") or ""
        ).upper(),
        raw_status=_clean_string(order.get("status") or "UNKNOWN"),
        raw=order,
    )


def is_open_order_status(status: str) -> bool:
    return str(status or "").strip().upper() in OPEN_ORDER_STATUSES


def is_replace_allowed_status(status: str) -> bool:
    return str(status or "").strip().upper() in REPLACE_ALLOWED_STATUSES


def is_terminal_order_status(status: str) -> bool:
    return str(status or "").strip().upper() in TERMINAL_ORDER_STATUSES


def required_confirmation(action: str, order_id: str) -> str:
    action_label = str(action or "").strip().upper()
    if action_label not in {"CANCEL", "REPLACE"}:
        raise ValueError("Confirmation action must be CANCEL or REPLACE.")
    return f"{action_label} {str(order_id).strip()}"


def confirmation_matches(action: str, order_id: str, typed: str) -> bool:
    return str(typed or "").strip() == required_confirmation(action, order_id)


def validate_replace_allowed(row: SchwabOrderRow, typed_confirmation: str) -> None:
    if not is_replace_allowed_status(row.status):
        raise ValueError(f"Order status {row.status or 'UNKNOWN'} is not eligible for replace.")
    if not confirmation_matches("REPLACE", row.order_id, typed_confirmation):
        raise ValueError(f"Type exactly: {required_confirmation('REPLACE', row.order_id)}")


def validate_cancel_allowed(row: SchwabOrderRow, typed_confirmation: str) -> None:
    if not is_open_order_status(row.status):
        raise ValueError(f"Order status {row.status or 'UNKNOWN'} is not eligible for cancel.")
    if not confirmation_matches("CANCEL", row.order_id, typed_confirmation):
        raise ValueError(f"Type exactly: {required_confirmation('CANCEL', row.order_id)}")


def build_replacement_order_json(row: SchwabOrderRow, edits: Mapping[str, Any]) -> dict[str, Any]:
    if len(_order_legs(row.raw)) > 1:
        raise ValueError("This editor only builds replacements for single-leg Schwab orders.")

    symbol = _required_text(edits.get("symbol") or row.symbol, "Symbol").upper()
    instruction = _required_text(edits.get("instruction") or row.instruction, "Side / instruction").upper()
    order_type = _required_text(edits.get("order_type") or row.order_type, "Order type").upper()
    quantity = _required_positive_float(edits.get("quantity") if edits.get("quantity") not in (None, "") else row.quantity, "Quantity")
    asset_type = _required_text(edits.get("asset_type") or row.asset_type or "EQUITY", "Asset type").upper()
    session, duration = _session_duration_from_edit(edits, row)

    replacement = {
        "orderType": order_type,
        "session": session,
        "duration": duration,
        "orderStrategyType": _clean_string(row.raw.get("orderStrategyType") or "SINGLE").upper() or "SINGLE",
        "orderLegCollection": [
            {
                "instruction": instruction,
                "quantity": quantity,
                "instrument": {
                    "symbol": symbol,
                    "assetType": asset_type,
                },
            }
        ],
    }

    limit_price = _optional_positive_float(edits.get("limit_price"))
    stop_price = _optional_positive_float(edits.get("stop_price"))
    if order_type in {"LIMIT", "STOP_LIMIT"}:
        if limit_price is None:
            raise ValueError("Limit price is required for LIMIT and STOP_LIMIT replacements.")
        replacement["price"] = _format_order_number(limit_price)
    elif limit_price is not None:
        replacement["price"] = _format_order_number(limit_price)

    if order_type in {"STOP", "STOP_LIMIT"}:
        if stop_price is None:
            raise ValueError("Stop price is required for STOP and STOP_LIMIT replacements.")
        replacement["stopPrice"] = _format_order_number(stop_price)
    elif stop_price is not None:
        replacement["stopPrice"] = _format_order_number(stop_price)

    return replacement


def replacement_notional(order_payload: Mapping[str, Any]) -> float | None:
    legs = order_payload.get("orderLegCollection")
    if not isinstance(legs, list) or not legs:
        return None
    first_leg = legs[0]
    if not isinstance(first_leg, Mapping):
        return None
    quantity = _to_float(first_leg.get("quantity"))
    price = _to_float(order_payload.get("price"))
    if quantity is None or price is None:
        return None
    multiplier = 100.0 if str((first_leg.get("instrument") or {}).get("assetType") if isinstance(first_leg.get("instrument"), Mapping) else "").upper() == "OPTION" else 1.0
    return quantity * price * multiplier


def replacement_diff(row: SchwabOrderRow, replacement: Mapping[str, Any]) -> list[tuple[str, str, str]]:
    legs = replacement.get("orderLegCollection") if isinstance(replacement, Mapping) else None
    first_leg = legs[0] if isinstance(legs, list) and legs and isinstance(legs[0], Mapping) else {}
    instrument = first_leg.get("instrument") if isinstance(first_leg, Mapping) else {}
    if not isinstance(instrument, Mapping):
        instrument = {}
    fields = [
        ("Symbol", row.symbol, _clean_string(instrument.get("symbol"))),
        ("Side / instruction", row.instruction, _clean_string(first_leg.get("instruction"))),
        ("Quantity", _format_optional_number(row.quantity), _format_optional_number(_to_float(first_leg.get("quantity")))),
        ("Order type", row.order_type, _clean_string(replacement.get("orderType"))),
        ("Limit price", _format_optional_number(row.price), _clean_string(replacement.get("price") or "")),
        ("Stop price", _format_optional_number(row.stop_price), _clean_string(replacement.get("stopPrice") or "")),
        ("Duration", row.duration, _clean_string(replacement.get("duration"))),
    ]
    return fields


def mask_account_hash(account_hash: str) -> str:
    clean = str(account_hash or "").strip()
    if not clean:
        return "--"
    if len(clean) <= 8:
        return "..." + clean[-4:]
    return f"{clean[:4]}...{clean[-4:]}"


def _order_legs(order: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    legs = order.get("orderLegCollection") or order.get("orderLegs") or []
    if not isinstance(legs, list):
        return []
    return [leg for leg in legs if isinstance(leg, Mapping)]


def _first_number(container: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _to_float(container.get(key))
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def _optional_positive_float(value: Any) -> float | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    if parsed <= 0:
        raise ValueError("Price values must be positive when provided.")
    return parsed


def _required_positive_float(value: Any, label: str) -> float:
    parsed = _to_float(value)
    if parsed is None or parsed <= 0:
        raise ValueError(f"{label} must be a positive number.")
    return parsed


def _required_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required.")
    return text


def _clean_string(value: Any) -> str:
    return str(value or "").strip()


def _format_order_number(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_optional_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:g}"


def _session_duration_from_edit(edits: Mapping[str, Any], row: SchwabOrderRow) -> tuple[str, str]:
    tif = str(edits.get("time_in_force") or "").strip()
    if tif:
        try:
            return schwab_equity_session_duration(tif)
        except Exception:
            pass
    session = _clean_string(edits.get("session") or row.session or "NORMAL").upper()
    duration = _clean_string(edits.get("duration") or row.duration or "DAY").upper()
    return session, duration
