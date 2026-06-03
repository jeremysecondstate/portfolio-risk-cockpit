from __future__ import annotations

import tkinter as tk
from typing import Any, Type

from app.brokers.hyperliquid.client import DEFAULT_SPOT_MID_KEYS, HyperliquidInfoClient
from app.brokers.hyperliquid.trading import resolve_hyperliquid_spot_market
from app.ui import hyperliquid_trading_extension as hyperliquid_ui

_installed = False
_RAW_DISPLAY_ORDER_COIN = hyperliquid_ui._display_order_coin
_SPOT_METADATA_CACHE: tuple[Any, Any] | None = None


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
        if selected and not selected.startswith("@"):
            return f"{_clean_display_symbol(selected)} ({coin})"
        mapped = _spot_symbol_for_market_id(coin)
        if mapped:
            return f"{mapped} ({coin})"
        if selected:
            return f"{_clean_display_symbol(selected)} ({coin})"

    return _RAW_DISPLAY_ORDER_COIN(raw_coin, selected_coin)


def _spot_symbol_for_market_id(market_id: str) -> str:
    normalized_market_id = market_id.strip().upper()
    for symbol, keys in DEFAULT_SPOT_MID_KEYS.items():
        if normalized_market_id in {str(key).strip().upper() for key in keys}:
            return _clean_display_symbol(symbol)

    spot_meta, spot_meta_and_asset_ctxs = _cached_spot_metadata()
    if spot_meta is None and spot_meta_and_asset_ctxs is None:
        return ""
    try:
        resolution = resolve_hyperliquid_spot_market(
            normalized_market_id,
            spot_meta=spot_meta,
            spot_meta_and_asset_ctxs=spot_meta_and_asset_ctxs,
        )
    except Exception:
        return ""
    if resolution.display_market.startswith("@"):
        return ""
    return resolution.display_market


def _cached_spot_metadata() -> tuple[Any, Any]:
    global _SPOT_METADATA_CACHE
    if _SPOT_METADATA_CACHE is not None:
        return _SPOT_METADATA_CACHE
    client = HyperliquidInfoClient(timeout_seconds=10)
    spot_meta_and_asset_ctxs = client._safe_post_info({"type": "spotMetaAndAssetCtxs"}, default=None)
    spot_meta = client._safe_post_info({"type": "spotMeta"}, default=None)
    _SPOT_METADATA_CACHE = (spot_meta, spot_meta_and_asset_ctxs)
    return _SPOT_METADATA_CACHE
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
    if clean in {"USDT", "USDT0"}:
        return "USDT"
    if clean.startswith("U") and len(clean) > 1:
        clean = clean[1:]
    if clean.endswith("0") and len(clean) > 1:
        clean = clean[:-1]
    return clean
