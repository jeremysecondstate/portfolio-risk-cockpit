from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Type

_ORIGINAL_BUILD_ORDER_JSON: Callable[[tk.Tk], dict[str, Any]] | None = None
_installed = False


def install_schwab_option_order_payload_extension(app_cls: Type[tk.Tk]) -> None:
    """Build Schwab OPTION order payloads when the integrated option ticket is active."""

    global _ORIGINAL_BUILD_ORDER_JSON, _installed
    if _installed:
        return

    _ORIGINAL_BUILD_ORDER_JSON = getattr(app_cls, "build_schwab_order_json_from_ui", None)
    app_cls.build_schwab_order_json_from_ui = _build_schwab_order_json_from_ui  # type: ignore[method-assign]
    _installed = True


def _build_schwab_order_json_from_ui(self: tk.Tk) -> dict[str, Any]:
    if _option_ticket_is_active(self):
        return _build_option_order_payload(self)
    if _ORIGINAL_BUILD_ORDER_JSON is None:
        raise RuntimeError("Original Schwab order builder is unavailable.")
    return _ORIGINAL_BUILD_ORDER_JSON(self)


def _option_ticket_is_active(self: tk.Tk) -> bool:
    strategy = _get_var(self, "options_strategy_var").strip()
    expiration = _get_var(self, "options_expiration_var").strip()
    strike = _to_float(_get_var(self, "options_strike_var"))
    debit = _to_float(_get_var(self, "options_premium_var"))
    return bool(strategy and expiration and strike is not None and debit is not None)


def _build_option_order_payload(self: tk.Tk) -> dict[str, Any]:
    strategy = _get_var(self, "options_strategy_var").strip()
    action = _get_var(self, "options_action_var").strip().lower() or "buy"
    option_type = "put" if _get_var(self, "options_type_var").strip().lower().startswith("put") else "call"
    expiration = _get_var(self, "options_expiration_var").strip()
    contracts = int(max(_require_float(_get_var(self, "options_contracts_var"), "Contracts"), 0))
    strike = _require_float(_get_var(self, "options_strike_var"), "Strike")
    short_strike = _to_float(_get_var(self, "options_short_strike_var"))
    debit = _require_float(_get_var(self, "options_premium_var"), "Limit / Debit")
    duration = "GOOD_TILL_CANCEL" if _get_var(self, "options_tif_var").strip().upper() == "GTC" else "DAY"

    if action != "buy":
        raise ValueError("Integrated options submit currently supports BUY-to-open tickets only.")
    if contracts <= 0:
        raise ValueError("Contracts must be positive.")
    if debit <= 0:
        raise ValueError("Limit / Debit must be positive.")

    long_symbol = _find_loaded_contract_symbol(self, expiration=expiration, strike=strike, option_type=option_type)
    if not long_symbol:
        raise ValueError("Could not resolve the long-leg Schwab option symbol from the loaded option chain.")

    if strategy == "Vertical Debit Spread":
        if short_strike is None or short_strike <= 0:
            raise ValueError("Short strike is required for a vertical debit spread.")
        short_symbol = _find_loaded_contract_symbol(self, expiration=expiration, strike=short_strike, option_type=option_type)
        if not short_symbol:
            raise ValueError("Could not resolve the short-leg Schwab option symbol from the loaded option chain.")
        return {
            "orderType": "NET_DEBIT",
            "session": "NORMAL",
            "duration": duration,
            "price": f"{debit:.2f}",
            "orderStrategyType": "SINGLE",
            "complexOrderStrategyType": "VERTICAL",
            "orderLegCollection": [
                {
                    "instruction": "BUY_TO_OPEN",
                    "quantity": contracts,
                    "instrument": {"symbol": long_symbol, "assetType": "OPTION"},
                },
                {
                    "instruction": "SELL_TO_OPEN",
                    "quantity": contracts,
                    "instrument": {"symbol": short_symbol, "assetType": "OPTION"},
                },
            ],
        }

    if strategy in {"Long Call", "Long Put"}:
        return {
            "orderType": "LIMIT",
            "session": "NORMAL",
            "duration": duration,
            "price": f"{debit:.2f}",
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": "BUY_TO_OPEN",
                    "quantity": contracts,
                    "instrument": {"symbol": long_symbol, "assetType": "OPTION"},
                }
            ],
        }

    raise ValueError(f"Integrated options submit is not wired for strategy: {strategy}")


def _find_loaded_contract_symbol(self: tk.Tk, *, expiration: str, strike: float, option_type: str) -> str:
    rows = getattr(self, "schwab_option_chain_rows", {}) or {}
    for row in rows.values():
        if not isinstance(row, dict):
            continue
        if str(row.get("expiration_label") or "") != expiration:
            continue
        try:
            row_strike = float(str(row.get("strike") or "").replace(",", ""))
        except ValueError:
            continue
        if abs(row_strike - strike) > 0.0001:
            continue
        contract = row.get(option_type)
        if isinstance(contract, dict):
            return str(contract.get("symbol") or "").strip()
    return ""


def _get_var(app: tk.Tk, name: str) -> str:
    var = getattr(app, name, None)
    try:
        return str(var.get())
    except Exception:
        return ""


def _to_float(value: str) -> float | None:
    try:
        cleaned = str(value).strip().replace(",", "")
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _require_float(value: str, field: str) -> float:
    parsed = _to_float(value)
    if parsed is None:
        raise ValueError(f"{field} must be a number.")
    return parsed
