from __future__ import annotations

import tkinter as tk
from typing import Any, Type

from app.brokers.hyperliquid.client import DEFAULT_SPOT_MID_KEYS
from app.ui import hyperliquid_trading_extension as hyperliquid_ui

_installed = False
_RAW_DISPLAY_ORDER_COIN = hyperliquid_ui._display_order_coin


def install_hyperliquid_spot_symbol_display_extension(app_cls: Type[tk.Tk]) -> None:
    """Show friendly spot tickers like HYPE instead of Hyperliquid @ market IDs."""

    global _installed
    if _installed:
        return

    hyperliquid_ui._display_order_coin = _display_order_coin_with_spot_symbol  # type: ignore[attr-defined]
    _installed = True


def _display_order_coin_with_spot_symbol(raw_coin: Any, selected_coin: str) -> str:
    coin = str(raw_coin or "UNKNOWN").strip()
    selected = str(selected_coin or "").strip().upper()

    if coin.startswith("@"):
        if selected:
            return f"{_clean_display_symbol(selected)} ({coin})"
        mapped = _spot_symbol_for_market_id(coin)
        if mapped:
            return f"{mapped} ({coin})"

    return _RAW_DISPLAY_ORDER_COIN(raw_coin, selected_coin)


def _spot_symbol_for_market_id(market_id: str) -> str:
    normalized_market_id = market_id.strip().upper()
    for symbol, keys in DEFAULT_SPOT_MID_KEYS.items():
        if normalized_market_id in {str(key).strip().upper() for key in keys}:
            return _clean_display_symbol(symbol)
    return ""


def _clean_display_symbol(symbol: str) -> str:
    clean = str(symbol or "").strip().upper()
    if clean.startswith("HL:"):
        clean = clean[3:]
    if "/" in clean:
        clean = clean.split("/", 1)[0]
    for suffix in ("-SPOT", "-PERP-SHORT", "-PERP"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
    if clean.startswith("U") and len(clean) > 1:
        clean = clean[1:]
    return clean
