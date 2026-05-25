from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Type

from app.ui import hyperliquid_trading_extension as hyperliquid_ui


def install_venue_mid_extension(app_cls: Type[tk.Tk]) -> None:
    """Make the shared Cockpit Use Mid button follow the selected venue."""

    app_cls.use_schwab_mid_market = _use_schwab_mid_market  # type: ignore[attr-defined]
    app_cls.use_selected_venue_mid_market = _use_mid_from_cockpit  # type: ignore[attr-defined]
    original_build_layout = getattr(app_cls, "_build_layout", None)

    if callable(original_build_layout):
        def _build_layout_with_venue_mid_hotkeys(self: tk.Tk) -> None:
            original_build_layout(self)
            _install_mid_hotkeys(self)

        app_cls._build_layout = _build_layout_with_venue_mid_hotkeys  # type: ignore[method-assign]

    hyperliquid_ui._use_mid_from_cockpit = _use_mid_from_cockpit  # type: ignore[attr-defined]


def _install_mid_hotkeys(self: tk.Tk) -> None:
    """Install app-wide keyboard shortcuts for fast planning."""

    def _trigger_use_mid(_event: tk.Event | None = None) -> str:
        _use_mid_from_cockpit(self)
        return "break"

    # Windows/Linux use Control. Command-style bindings are harmless on Tk builds
    # that support them and make the shortcut friendlier on macOS later.
    for sequence in ("<Control-m>", "<Control-M>", "<Command-m>", "<Command-M>"):
        try:
            self.bind_all(sequence, _trigger_use_mid, add="+")
        except tk.TclError:
            continue


def _use_mid_from_cockpit(self: tk.Tk) -> None:
    venue_var = getattr(self, "trade_venue_var", tk.StringVar(value="Schwab"))
    if venue_var.get() == "Hyperliquid":
        command = getattr(self, "use_hyperliquid_mid_market", None)
        if callable(command):
            command()
            return
        messagebox.showinfo(
            "Use Mid unavailable",
            "The Hyperliquid mid-market helper is not installed yet. Restart the app after pulling the latest changes.",
        )
        return

    _use_schwab_mid_market(self)


def _use_schwab_mid_market(self: tk.Tk) -> None:
    symbol = self.symbol_var.get().strip().upper()
    if not symbol:
        messagebox.showerror("Schwab mid-market lookup failed", "Enter a Schwab symbol first.")
        return

    try:
        session = self._authorize_schwab_session()
        if session is None:
            return

        status_code, payload = session.get_quote(symbol)
        if status_code != 200:
            raise RuntimeError(f"Schwab quote returned HTTP {status_code}: {payload}")

        quote, source_key = _extract_schwab_quote(payload, symbol)
        bid = _first_number(quote, "bidPrice", "bid", "bid_price")
        ask = _first_number(quote, "askPrice", "ask", "ask_price")
        mark = _first_number(quote, "mark", "markPrice", "mark_price")
        last = _first_number(quote, "lastPrice", "last", "last_price", "closePrice", "regularMarketLastPrice")

        if bid is not None and ask is not None and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            basis = "bid/ask midpoint"
        elif mark is not None and mark > 0:
            mid = mark
            basis = "mark price"
        elif last is not None and last > 0:
            mid = last
            basis = "last price fallback"
        else:
            raise RuntimeError(f"No usable bid/ask, mark, or last price found in Schwab quote for {symbol}.")

        self.limit_price_var.set(_format_price(mid))
        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(
            "SCHWAB MID-MARKET PRICE\n"
            "=======================\n\n"
            f"Symbol: {symbol}\n"
            f"Quote key: {source_key}\n"
            f"Bid: {_format_optional_price(bid)}\n"
            f"Ask: {_format_optional_price(ask)}\n"
            f"Mark: {_format_optional_price(mark)}\n"
            f"Last: {_format_optional_price(last)}\n\n"
            f"Entry / Limit updated to: ${mid:,.4f}\n"
            f"Basis: {basis}\n\n"
            "No order was submitted."
        )
    except Exception as exc:
        messagebox.showerror("Schwab mid-market lookup failed", str(exc))


def _extract_schwab_quote(payload: Any, symbol: str) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, dict):
        raise RuntimeError("Schwab quote response was not a JSON object.")

    for key in (symbol, symbol.upper(), symbol.lower()):
        quote = _quote_dict_from_schwab_entry(payload.get(key))
        if quote is not None:
            return quote, str(key)

    for key, value in payload.items():
        quote = _quote_dict_from_schwab_entry(value)
        if quote is not None:
            return quote, str(key)

    quote = _quote_dict_from_schwab_entry(payload)
    if quote is not None:
        return quote, symbol

    raise RuntimeError(f"No quote object found in Schwab response for {symbol}.")


def _quote_dict_from_schwab_entry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    nested = value.get("quote")
    if isinstance(nested, dict):
        return nested
    if any(key in value for key in ("bidPrice", "askPrice", "lastPrice", "mark", "markPrice")):
        return value
    return None


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        if value in (None, ""):
            continue
        try:
            return float(str(value).replace(",", ""))
        except ValueError:
            continue
    return None


def _format_price(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _format_optional_price(value: float | None) -> str:
    return "--" if value is None else f"${value:,.4f}"