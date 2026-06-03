from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Type

from app.brokers.hyperliquid.client import HyperliquidInfoClient
from app.brokers.hyperliquid.trading import (
    HyperliquidSpotMarketLookupError,
    HyperliquidSpotMarketResolution,
    resolve_hyperliquid_spot_market,
)
from app.ui import hyperliquid_trading_extension as hyperliquid_ui


def install_hyperliquid_cockpit_spot_mid_extension(app_cls: Type[tk.Tk]) -> None:
    """Make the Cockpit Hyperliquid Use Mid button fetch spot mids, not perp mids."""

    app_cls.use_hyperliquid_cockpit_spot_mid_market = _use_hyperliquid_cockpit_spot_mid_market  # type: ignore[attr-defined]
    hyperliquid_ui._use_mid_from_cockpit = _use_hyperliquid_cockpit_spot_mid_market  # type: ignore[attr-defined]


def _use_hyperliquid_cockpit_spot_mid_market(self: tk.Tk) -> None:
    symbol_source = _spot_mid_symbol_source(self)
    if not symbol_source:
        messagebox.showerror("Hyperliquid spot mid failed", "Enter a Hyperliquid spot symbol first, for example XAUT, BTC, ZEC, HYPE, or XAUT/USDC.")
        return

    try:
        resolution = _lookup_hyperliquid_spot_market(symbol_source)
        if resolution.mid_price is None:
            raise HyperliquidSpotMarketLookupError(resolution)
        mid = resolution.mid_price
        basis = resolution.mid_basis
        self.limit_price_var.set(_format_price(mid))
        self.symbol_var.set(resolution.display_market)
        self.hyperliquid_coin_var.set(resolution.execution_coin)
        _set_spot_ticket_mid_fields(self, resolution)
        self.hyperliquid_status_var.set(f"Hyperliquid spot: {resolution.display_market} mid ${mid:,.4f}")
        self._set_preview_text(
            "HYPERLIQUID SPOT MID-MARKET PRICE\n"
            "==================================\n\n"
            f"Display market: {resolution.display_market}\n"
            f"Execution/API coin: {resolution.execution_coin}\n"
            f"Mid-market price: ${mid:,.4f}\n"
            f"Basis: {basis}\n\n"
            "Entry / Limit was updated from Hyperliquid spot market data. No order was submitted.\n\n"
            "Note: this Use Mid path is read-only and only fills the local Entry / Limit price."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid spot: mid failed")
        messagebox.showerror("Hyperliquid spot mid failed", str(exc))


def _spot_mid_symbol_source(self: tk.Tk) -> str:
    active_ticket = str(getattr(getattr(self, "hyperliquid_workspace_active_ticket_var", None), "get", lambda: "")()).lower()
    if active_ticket == "spot":
        for attr in ("hyperliquid_spot_symbol_var", "hyperliquid_spot_coin_var"):
            value = str(getattr(getattr(self, attr, None), "get", lambda: "")()).strip()
            if value:
                return _market_with_selected_quote(self, value)
    for attr in ("symbol_var", "hyperliquid_coin_var", "hyperliquid_spot_symbol_var", "hyperliquid_spot_coin_var"):
        value = str(getattr(getattr(self, attr, None), "get", lambda: "")()).strip()
        if value:
            return _market_with_selected_quote(self, value)
    return ""


def _market_with_selected_quote(self: tk.Tk, market: str) -> str:
    clean = market.strip()
    if not clean or clean.startswith("@") or "/" in clean:
        return clean
    return f"{clean}/{_selected_spot_quote_asset(self)}"


def _selected_spot_quote_asset(self: tk.Tk) -> str:
    for attr in ("symbol_var", "hyperliquid_spot_symbol_var"):
        market = str(getattr(getattr(self, attr, None), "get", lambda: "")()).strip().upper()
        if "/" in market:
            quote = market.split("/", 1)[1].strip()
            if quote in {"USDC", "USDT"}:
                return quote
    unit = str(getattr(getattr(self, "hyperliquid_size_unit_var", None), "get", lambda: "")()).strip().upper()
    if unit in {"USDC", "USDT"}:
        return unit
    for attr in ("hyperliquid_spot_quote_asset_var", "hyperliquid_quote_asset_var"):
        quote = str(getattr(getattr(self, attr, None), "get", lambda: "")()).strip().upper()
        if quote in {"USDC", "USDT"}:
            return quote
    return "USDC"


def _set_spot_ticket_mid_fields(self: tk.Tk, resolution: HyperliquidSpotMarketResolution) -> None:
    self.hyperliquid_spot_resolved_display_market = resolution.display_market
    self.hyperliquid_spot_resolved_execution_coin = resolution.execution_coin
    for attr, value in (
        ("hyperliquid_spot_symbol_var", resolution.display_market),
        ("hyperliquid_spot_coin_var", resolution.execution_coin),
        ("hyperliquid_spot_limit_price_var", _format_price(resolution.mid_price or 0.0)),
        ("hyperliquid_spot_quote_asset_var", resolution.quote_symbol),
        ("hyperliquid_quote_asset_var", resolution.quote_symbol),
    ):
        var = getattr(self, attr, None)
        if hasattr(var, "set"):
            var.set(value)


def _lookup_hyperliquid_spot_market(market: str) -> HyperliquidSpotMarketResolution:
    client = HyperliquidInfoClient(timeout_seconds=10)
    all_mids = client.post_info({"type": "allMids"})
    spot_meta_and_asset_ctxs = client._safe_post_info({"type": "spotMetaAndAssetCtxs"}, default=None)
    spot_meta = client._safe_post_info({"type": "spotMeta"}, default=None)

    if not isinstance(all_mids, dict):
        raise RuntimeError("Hyperliquid allMids returned an unexpected response.")

    resolution = resolve_hyperliquid_spot_market(
        market,
        all_mids=all_mids,
        spot_meta=spot_meta,
        spot_meta_and_asset_ctxs=spot_meta_and_asset_ctxs,
    )
    if resolution.mid_price is None:
        raise HyperliquidSpotMarketLookupError(resolution)
    return resolution


def _lookup_hyperliquid_spot_mid(market: str) -> tuple[float, str]:
    resolution = _lookup_hyperliquid_spot_market(market)
    if resolution.mid_price is None:
        raise HyperliquidSpotMarketLookupError(resolution)
    return resolution.mid_price, resolution.mid_basis


def _format_price(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")
