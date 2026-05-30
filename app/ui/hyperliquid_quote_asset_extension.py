from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.brokers.hyperliquid.trading import HyperliquidOrderTicket
from app.ui import options_lab_extension as workspace_ui

QUOTE_ASSETS = ("USDC", "USDH")
BASE_ALIASES = {
    "SDH": "USDH",  # defensive repair for the previous UI bug that stripped USDH's leading U
    "USDH": "USDH",
}
_installed = False
_original_build_layout = None


def install_hyperliquid_quote_asset_extension(app_cls: Type[tk.Tk]) -> None:
    """Add a USDC/USDH quote selector to Hyperliquid spot tickets."""

    global _installed, _original_build_layout
    if _installed:
        return

    _original_build_layout = app_cls._build_layout
    app_cls._build_layout = _build_layout_with_hyperliquid_quote_selector  # type: ignore[method-assign]
    app_cls.parse_hyperliquid_spot_ticket = _parse_hyperliquid_spot_ticket_with_quote  # type: ignore[attr-defined]

    # The spot size-unit combo is built in options_lab_extension and uses this helper
    # at dropdown time. Patch it so the unit can follow the selected quote asset.
    workspace_ui._hyperliquid_workspace_spot_size_unit_values = _hyperliquid_workspace_spot_size_unit_values_with_quote  # type: ignore[attr-defined]
    _installed = True


def _build_layout_with_hyperliquid_quote_selector(self: tk.Tk) -> None:
    if _original_build_layout is None:
        raise RuntimeError("Original layout builder was not captured.")
    _ensure_quote_asset_var(self)
    _original_build_layout(self)
    self.after_idle(lambda: _install_quote_selector_widgets(self))


def _ensure_quote_asset_var(self: tk.Tk) -> None:
    if not hasattr(self, "hyperliquid_quote_asset_var"):
        self.hyperliquid_quote_asset_var = tk.StringVar(value="USDC")
    if not hasattr(self, "hyperliquid_spot_quote_asset_var"):
        self.hyperliquid_spot_quote_asset_var = tk.StringVar(value=self.hyperliquid_quote_asset_var.get() or "USDC")


def _install_quote_selector_widgets(self: tk.Tk) -> None:
    if getattr(self, "_hyperliquid_quote_selector_built", False):
        return
    _ensure_quote_asset_var(self)

    spot_ticket = _find_labelframe(self, "Hyperliquid Spot Ticket")
    if spot_ticket is not None:
        _add_quote_selector_to_spot_ticket(self, spot_ticket)

    perp_ticket = _find_labelframe(self, "Hyperliquid Perp Ticket")
    if perp_ticket is not None:
        _add_quote_note_to_perp_ticket(self, perp_ticket)

    _sync_quote_vars(self)
    self._hyperliquid_quote_selector_built = True


def _add_quote_selector_to_spot_ticket(self: tk.Tk, spot_ticket: ttk.LabelFrame) -> None:
    row = _next_grid_row(spot_ticket)
    ttk.Label(spot_ticket, text="Pay / Quote", style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
    combo = ttk.Combobox(
        spot_ticket,
        textvariable=self.hyperliquid_spot_quote_asset_var,
        values=list(QUOTE_ASSETS),
        state="readonly",
        width=8,
    )
    combo.grid(row=row, column=1, sticky="ew", pady=6)
    ttk.Label(
        spot_ticket,
        text="Example: buy USDH with USDC = Market USDH, Pay/Quote USDC",
        style="Subtle.TLabel",
    ).grid(row=row, column=2, columnspan=2, sticky="w", padx=(16, 0), pady=6)
    self.hyperliquid_spot_quote_asset_var.trace_add("write", lambda *_args: _on_quote_changed(self))


def _add_quote_note_to_perp_ticket(self: tk.Tk, perp_ticket: ttk.LabelFrame) -> None:
    row = _next_grid_row(perp_ticket)
    ttk.Label(perp_ticket, text="Settlement", style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
    ttk.Label(
        perp_ticket,
        text="Perps remain exchange-native; Pay/Quote selector applies to spot tickets.",
        style="Subtle.TLabel",
    ).grid(row=row, column=1, columnspan=3, sticky="w", pady=6)


def _on_quote_changed(self: tk.Tk) -> None:
    _sync_quote_vars(self)
    try:
        workspace_ui._sync_hyperliquid_workspace_spot_size_unit(self)
    except Exception:
        pass


def _sync_quote_vars(self: tk.Tk) -> None:
    quote = _selected_quote_asset(self)
    self.hyperliquid_quote_asset_var.set(quote)
    self.hyperliquid_spot_quote_asset_var.set(quote)
    unit_var = getattr(self, "hyperliquid_spot_size_unit_var", None)
    if unit_var is not None:
        current = str(unit_var.get()).strip().upper()
        if current in {"USDC", "USDH", ""}:
            unit_var.set(quote)
    shared_unit_var = getattr(self, "hyperliquid_size_unit_var", None)
    if shared_unit_var is not None:
        current = str(shared_unit_var.get()).strip().upper()
        if current in {"USDC", "USDH", ""}:
            shared_unit_var.set(quote)


def _parse_hyperliquid_spot_ticket_with_quote(self: tk.Tk) -> HyperliquidOrderTicket:
    _ensure_quote_asset_var(self)
    quote = _selected_quote_asset(self)

    # Prefer Symbol because the visible spot ticket uses it as the market/base field.
    coin_source = self.symbol_var.get().strip() or self.hyperliquid_coin_var.get().strip()
    coin = _normalize_spot_market_for_quote(coin_source, quote)

    side = self.side_var.get().strip().lower()
    if side not in {"buy", "sell"}:
        raise ValueError("Hyperliquid spot side must be buy or sell.")

    try:
        raw_size = float(self.quantity_var.get().strip().replace(",", ""))
        limit_price = float(self.limit_price_var.get().strip().replace(",", ""))
    except ValueError as exc:
        raise ValueError("Hyperliquid spot size and limit price must be numbers.") from exc

    size = _spot_size_from_quantity_input_with_quote(self, raw_size, limit_price, quote)
    if size <= 0:
        raise ValueError("Hyperliquid spot size must be positive.")
    if limit_price <= 0:
        raise ValueError("Hyperliquid spot limit price must be positive.")

    tif = self.hyperliquid_tif_var.get().strip() or "Gtc"
    if tif not in {"Alo", "Ioc", "Gtc"}:
        raise ValueError("Hyperliquid TIF must be Alo, Ioc, or Gtc.")

    return HyperliquidOrderTicket(
        coin=coin,
        is_buy=side == "buy",
        size=size,
        limit_price=limit_price,
        tif=tif,
        reduce_only=False,
    )


def _spot_size_from_quantity_input_with_quote(self: tk.Tk, raw_size: float, limit_price: float, quote: str) -> float:
    unit = ""
    for name in ("hyperliquid_spot_size_unit_var", "hyperliquid_size_unit_var"):
        var = getattr(self, name, None)
        if var is not None and str(var.get()).strip():
            unit = str(var.get()).strip().upper()
            break
    if unit in {"USDC", "USDH"}:
        if limit_price <= 0:
            raise ValueError(f"Limit price is required when sizing in {unit}.")
        return raw_size / limit_price
    return raw_size


def _normalize_spot_market_for_quote(symbol: str, quote: str) -> str:
    market = str(symbol or "").strip().upper()
    if market.startswith("HL:"):
        market = market[3:]
    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if market.endswith(suffix):
            market = market[: -len(suffix)]
    if market.startswith("@"):
        return market
    if "/" in market:
        base, raw_quote = market.split("/", 1)
        raw_quote = BASE_ALIASES.get(raw_quote, raw_quote)
        if raw_quote not in QUOTE_ASSETS:
            raise ValueError("Hyperliquid spot quote must be USDC or USDH.")
        quote = raw_quote
    else:
        base = market
    base = _normalize_spot_base_for_quote(base, quote)
    if not base:
        raise ValueError("Enter a Hyperliquid spot market, for example HYPE, USDH, or BTC/USDH.")
    return f"{base}/{quote}"


def _normalize_spot_base_for_quote(base: str, quote: str) -> str:
    clean = BASE_ALIASES.get(base.strip().upper(), base.strip().upper())
    if quote == "USDC":
        aliases = {
            "BTC": "UBTC",
            "UBTC": "UBTC",
            "ETH": "UETH",
            "UETH": "UETH",
            "ZEC": "UZEC",
            "UZEC": "UZEC",
            "USDH": "USDH",
        }
        return aliases.get(clean, clean)
    # Hyperliquid USDH markets display familiar base names such as BTC/USDH.
    if clean.startswith("U") and clean in {"UBTC", "UETH", "UZEC"}:
        return clean[1:]
    return clean


def _hyperliquid_workspace_spot_size_unit_values_with_quote(self: tk.Tk) -> list[str]:
    quote = _selected_quote_asset(self)
    base = ""
    try:
        base = workspace_ui._hyperliquid_workspace_spot_base(self)
    except Exception:
        base = ""
    base = BASE_ALIASES.get(str(base).strip().upper(), str(base).strip().upper())
    return [quote, base] if base and base != quote else [quote]


def _selected_quote_asset(self: tk.Tk) -> str:
    for name in ("hyperliquid_spot_quote_asset_var", "hyperliquid_quote_asset_var"):
        var = getattr(self, name, None)
        try:
            value = str(var.get()).strip().upper()
        except Exception:
            value = ""
        value = BASE_ALIASES.get(value, value)
        if value in QUOTE_ASSETS:
            return value
    return "USDC"


def _find_labelframe(root: tk.Widget, title: str) -> ttk.LabelFrame | None:
    try:
        if str(root.winfo_class()) == "TLabelframe" and str(root.cget("text")) == title:
            return root  # type: ignore[return-value]
    except Exception:
        pass
    for child in root.winfo_children():
        found = _find_labelframe(child, title)
        if found is not None:
            return found
    return None


def _next_grid_row(parent: tk.Widget) -> int:
    rows: list[int] = []
    for child in parent.winfo_children():
        try:
            rows.append(int(child.grid_info().get("row", 0)))
        except Exception:
            continue
    return (max(rows) + 1) if rows else 0
