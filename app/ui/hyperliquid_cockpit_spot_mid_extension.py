from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Type

from app.brokers.hyperliquid.client import HyperliquidInfoClient
from app.brokers.hyperliquid.trading import normalize_hyperliquid_spot_market
from app.ui import hyperliquid_trading_extension as hyperliquid_ui


def install_hyperliquid_cockpit_spot_mid_extension(app_cls: Type[tk.Tk]) -> None:
    """Make the Cockpit Hyperliquid Use Mid button fetch spot mids, not perp mids."""

    app_cls.use_hyperliquid_cockpit_spot_mid_market = _use_hyperliquid_cockpit_spot_mid_market  # type: ignore[attr-defined]
    hyperliquid_ui._use_mid_from_cockpit = _use_hyperliquid_cockpit_spot_mid_market  # type: ignore[attr-defined]


def _use_hyperliquid_cockpit_spot_mid_market(self: tk.Tk) -> None:
    symbol_source = self.symbol_var.get().strip() or self.hyperliquid_coin_var.get().strip()
    if not symbol_source:
        messagebox.showerror("Hyperliquid spot mid failed", "Enter a Hyperliquid spot symbol first, for example BTC, ZEC, HYPE, or BTC/USDC.")
        return

    try:
        market = normalize_hyperliquid_spot_market(symbol_source)
        mid, basis = _lookup_hyperliquid_spot_mid(market)
        self.limit_price_var.set(_format_price(mid))
        self.symbol_var.set(_display_spot_symbol(market))
        self.hyperliquid_status_var.set(f"Hyperliquid spot: {market} mid ${mid:,.4f}")
        self._set_preview_text(
            "HYPERLIQUID SPOT MID-MARKET PRICE\n"
            "==================================\n\n"
            f"Spot market: {market}\n"
            f"Mid-market price: ${mid:,.4f}\n"
            f"Basis: {basis}\n\n"
            "Entry / Limit was updated from Hyperliquid spot market data. No order was submitted.\n\n"
            "Note: this Cockpit Use Mid path is spot-only. The dedicated Hyperliquid Trading tab still uses perp mids."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid spot: mid failed")
        messagebox.showerror("Hyperliquid spot mid failed", str(exc))


def _lookup_hyperliquid_spot_mid(market: str) -> tuple[float, str]:
    client = HyperliquidInfoClient(timeout_seconds=10)
    all_mids = client.post_info({"type": "allMids"})
    spot_meta_and_asset_ctxs = client._safe_post_info({"type": "spotMetaAndAssetCtxs"}, default=None)

    if not isinstance(all_mids, dict):
        raise RuntimeError("Hyperliquid allMids returned an unexpected response.")

    market = market.strip().upper()
    base = market.split("/", 1)[0] if "/" in market else market
    candidates = [market, market.replace("/", "-"), base]

    for key in candidates:
        price = _all_mids_price(all_mids, key)
        if price is not None:
            return price, f"allMids[{key}]"

    meta_price = _spot_meta_mid_price(market, all_mids, spot_meta_and_asset_ctxs)
    if meta_price is not None:
        return meta_price

    raise RuntimeError(f"No Hyperliquid spot mid-market price found for {market}.")


def _spot_meta_mid_price(market: str, all_mids: dict[str, Any], payload: Any) -> tuple[float, str] | None:
    if not isinstance(payload, list) or len(payload) < 2:
        return None

    meta, asset_ctxs = payload[0], payload[1]
    universe = meta.get("universe") if isinstance(meta, dict) else None
    tokens = meta.get("tokens") if isinstance(meta, dict) else None
    if not isinstance(universe, list):
        return None

    market = market.strip().upper()
    base = market.split("/", 1)[0] if "/" in market else market
    token_names_by_index = _token_names_by_index(tokens)

    for market_index, asset in enumerate(universe):
        if not isinstance(asset, dict):
            continue
        names = _spot_market_names(asset, token_names_by_index)
        if market not in names and base not in names:
            continue

        asset_index = _to_int(asset.get("index"))
        candidate_keys: list[str] = []
        for name in sorted(names):
            candidate_keys.extend([name, name.replace("/", "-")])
        if asset_index is not None:
            candidate_keys.extend([f"@{asset_index}", f"@{10000 + asset_index}"])
        candidate_keys.extend([f"@{market_index}", f"@{10000 + market_index}"])

        for key in _unique(candidate_keys):
            price = _all_mids_price(all_mids, key)
            if price is not None:
                return price, f"spotMetaAndAssetCtxs/allMids[{key}]"

        if isinstance(asset_ctxs, list):
            for ctx_index in _ctx_indexes(asset, market_index, len(asset_ctxs)):
                ctx = asset_ctxs[ctx_index] if isinstance(asset_ctxs[ctx_index], dict) else {}
                price = _first_number(ctx, "midPx", "markPx", "oraclePx", "price", "prevDayPx")
                if price is not None and price > 0:
                    return price, f"spotMetaAndAssetCtxs ctx {ctx_index}"
    return None


def _spot_market_names(asset: dict[str, Any], token_names_by_index: dict[int, set[str]]) -> set[str]:
    names = {
        str(value).strip().upper()
        for key in ("name", "coin", "token", "symbol")
        for value in (asset.get(key),)
        if value not in (None, "")
    }
    token_indices = asset.get("tokens")
    if isinstance(token_indices, list) and token_indices:
        base_index = _to_int(token_indices[0])
        quote_index = _to_int(token_indices[1]) if len(token_indices) > 1 else None
        base_names = token_names_by_index.get(base_index, set()) if base_index is not None else set()
        quote_names = token_names_by_index.get(quote_index, set()) if quote_index is not None else {"USDC"}
        names.update(base_names)
        for base in base_names:
            if quote_names:
                for quote in quote_names:
                    names.add(f"{base}/{quote}")
                    names.add(f"{base}-{quote}")
            else:
                names.add(f"{base}/USDC")
                names.add(f"{base}-USDC")
    return {name for name in names if name}


def _token_names_by_index(tokens: Any) -> dict[int, set[str]]:
    names_by_index: dict[int, set[str]] = {}
    if not isinstance(tokens, list):
        return names_by_index
    for fallback_index, token in enumerate(tokens):
        if not isinstance(token, dict):
            continue
        token_names = {
            str(value).strip().upper()
            for key in ("name", "coin", "token", "fullName")
            for value in (token.get(key),)
            if value not in (None, "")
        }
        if not token_names:
            continue
        names_by_index.setdefault(fallback_index, set()).update(token_names)
        explicit_index = _to_int(token.get("index"))
        if explicit_index is not None:
            names_by_index.setdefault(explicit_index, set()).update(token_names)
    return names_by_index


def _ctx_indexes(asset: dict[str, Any], market_index: int, asset_ctxs_length: int) -> list[int]:
    indexes: list[int] = []
    asset_index = _to_int(asset.get("index"))
    if asset_index is not None and 0 <= asset_index < asset_ctxs_length:
        indexes.append(asset_index)
    if 0 <= market_index < asset_ctxs_length and market_index not in indexes:
        indexes.append(market_index)
    return indexes


def _all_mids_price(all_mids: dict[str, Any], key: str) -> float | None:
    upper_mids = {str(raw_key).upper(): value for raw_key, value in all_mids.items()}
    return _to_positive_float(upper_mids.get(key.upper()))


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _to_positive_float(source.get(key))
        if value is not None:
            return value
    return None


def _to_positive_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        return None
    return number if number > 0 else None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(",", ""))
    except ValueError:
        return None


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


def _display_spot_symbol(market: str) -> str:
    base = market.split("/", 1)[0]
    if base.startswith("U") and len(base) > 1:
        return base[1:]
    return base


def _format_price(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".")
