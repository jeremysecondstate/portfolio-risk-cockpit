from __future__ import annotations

from datetime import datetime
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Type

import requests

from app.brokers.schwab.session import MARKETDATA_BASE_URL
from app.ui.schwab_trading_tab import _find_labelframe, _set_schwab_mode_text


_OPTION_CHAIN_COLUMNS = (
    "expiration",
    "strike",
    "call_bid",
    "call_ask",
    "put_bid",
    "put_ask",
    "call_volume",
    "put_volume",
)


def install_schwab_option_chain_extension(app_cls: Type[tk.Tk]) -> None:
    """Add a Schwab option-chain selector to the integrated Schwab workspace."""

    app_cls.load_schwab_option_chain = _load_schwab_option_chain  # type: ignore[attr-defined]
    app_cls.use_selected_schwab_option_call_ask = lambda self: _use_selected_schwab_option(self, "call")  # type: ignore[attr-defined]
    app_cls.use_selected_schwab_option_put_ask = lambda self: _use_selected_schwab_option(self, "put")  # type: ignore[attr-defined]

    original_build_layout = app_cls._build_layout

    def build_layout_with_option_chain(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _install_option_chain_widgets(self))

    app_cls._build_layout = build_layout_with_option_chain  # type: ignore[method-assign]


def _ensure_option_chain_vars(self: tk.Tk) -> None:
    if not hasattr(self, "schwab_option_chain_strike_count_var"):
        self.schwab_option_chain_strike_count_var = tk.StringVar(value="10")
    if not hasattr(self, "schwab_option_chain_status_var"):
        self.schwab_option_chain_status_var = tk.StringVar(value="Option chain: not loaded")
    if not hasattr(self, "schwab_option_chain_rows"):
        self.schwab_option_chain_rows: dict[str, dict[str, Any]] = {}


def _install_option_chain_widgets(self: tk.Tk) -> None:
    if getattr(self, "_schwab_option_chain_widgets_built", False):
        return

    _ensure_option_chain_vars(self)

    options_fields = _find_labelframe(self, "Options Ticket Fields")
    if options_fields is not None:
        if getattr(self, "schwab_trading_preview_text", None) is not None:
            self._schwab_option_chain_widgets_built = True
            return
        ttk.Label(options_fields, text="Chain strikes", style="Subtle.TLabel").grid(
            row=6, column=0, sticky="w", padx=(0, 6), pady=(8, 4)
        )
        ttk.Entry(options_fields, textvariable=self.schwab_option_chain_strike_count_var).grid(
            row=6, column=1, sticky="ew", padx=(0, 10), pady=(8, 4)
        )
        ttk.Button(
            options_fields,
            text="Load Chain",
            command=self.load_schwab_option_chain,
            style="Accent.TButton",
        ).grid(row=6, column=2, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Label(options_fields, textvariable=self.schwab_option_chain_status_var, style="Subtle.TLabel").grid(
            row=7, column=0, columnspan=4, sticky="w", pady=(2, 0)
        )

    self._schwab_option_chain_widgets_built = True


def _load_schwab_option_chain(self: tk.Tk) -> None:
    _ensure_option_chain_vars(self)
    _install_option_chain_widgets(self)

    symbol = self.symbol_var.get().strip().upper()
    if not symbol and hasattr(self, "options_symbol_var"):
        symbol = self.options_symbol_var.get().strip().upper()
    if not symbol:
        messagebox.showerror("Option chain blocked", "Enter a stock symbol first, for example NVDA.")
        return

    try:
        strike_count = int(self.schwab_option_chain_strike_count_var.get().strip() or "10")
    except ValueError:
        messagebox.showerror("Option chain blocked", "Chain strikes must be a whole number.")
        return
    if strike_count <= 0:
        messagebox.showerror("Option chain blocked", "Chain strikes must be positive.")
        return

    already_loaded_symbol = str(getattr(self, "schwab_option_chain_loaded_symbol", "") or "").upper()
    already_loaded_count = getattr(self, "schwab_option_chain_loaded_strike_count", None)
    if (
        already_loaded_symbol == symbol
        and already_loaded_count == strike_count
        and getattr(self, "schwab_option_chain_rows", {})
        and _resolve_option_chain_tree(self) is not None
    ):
        row_count = len(getattr(self, "schwab_option_chain_rows", {}) or {})
        self.schwab_option_chain_status_var.set(f"✓ Option chain already loaded for {symbol} ({row_count} rows)")
        return

    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        status_code, payload = _request_option_chain(session, symbol, strike_count=strike_count)
        if status_code != 200:
            raise RuntimeError(f"Schwab option chain returned HTTP {status_code}: {payload}")
        if not isinstance(payload, dict):
            raise RuntimeError("Schwab option chain returned an unexpected response.")

        rows = _option_chain_rows(payload)
        _populate_option_chain_tree(self, rows)
        self.schwab_option_chain_loaded_symbol = symbol
        self.schwab_option_chain_loaded_strike_count = strike_count

        underlying_price = _underlying_price(payload)
        if underlying_price is not None and hasattr(self, "options_underlying_price_var"):
            self.options_underlying_price_var.set(_format_number(underlying_price, digits=2))
        if hasattr(self, "options_symbol_var"):
            self.options_symbol_var.set(symbol)

        self.schwab_status_var.set("Schwab session: connected")
        self.schwab_option_chain_status_var.set(f"✓ Option chain loaded for {symbol} ({len(rows)} rows)")
        _set_schwab_mode_text(
            self,
            "SCHWAB OPTION CHAIN\n"
            "===================\n\n"
            f"Symbol: {symbol}\n"
            f"Underlying price: {_format_optional_price(underlying_price)}\n"
            f"Rows loaded: {len(rows)}\n"
            f"Strike count request: {strike_count}\n\n"
            "Select a row in the Schwab Option Chain table, then click Use Call Ask or Use Put Ask.\n\n"
            "This only fills the options what-if fields. It does not submit, preview, or stage a live Schwab order.",
        )
        render_greeks = getattr(self, "render_schwab_research_greeks", None)
        if callable(render_greeks):
            render_greeks()
    except Exception as exc:
        self.schwab_option_chain_status_var.set("Option chain: load failed")
        messagebox.showerror("Load Schwab option chain failed", str(exc))


def _request_option_chain(session: Any, symbol: str, *, strike_count: int) -> tuple[int, Any]:
    response = requests.get(
        f"{MARKETDATA_BASE_URL}/chains",
        headers=session._headers(),
        params={
            "symbol": symbol,
            "contractType": "ALL",
            "strategy": "SINGLE",
            "strikeCount": strike_count,
            "includeUnderlyingQuote": "true",
        },
        timeout=5,
    )
    try:
        payload: Any = response.json()
    except ValueError:
        payload = response.text
    return response.status_code, payload


def _widget_exists(widget: Any) -> bool:
    try:
        return bool(widget is not None and widget.winfo_exists())
    except tk.TclError:
        return False


def _find_option_chain_tree(root: tk.Misc) -> ttk.Treeview | None:
    for child in root.winfo_children():
        if isinstance(child, ttk.Treeview):
            try:
                if tuple(child.cget("columns")) == _OPTION_CHAIN_COLUMNS:
                    return child
            except tk.TclError:
                pass
        found = _find_option_chain_tree(child)
        if found is not None:
            return found
    return None


def _resolve_option_chain_tree(self: tk.Tk) -> ttk.Treeview | None:
    tree = getattr(self, "schwab_option_chain_tree", None)
    if _widget_exists(tree):
        return tree
    tree = _find_option_chain_tree(self)
    if tree is not None:
        self.schwab_option_chain_tree = tree
        return tree
    return None


def _populate_option_chain_tree(self: tk.Tk, rows: list[dict[str, Any]]) -> None:
    tree = _resolve_option_chain_tree(self)
    if tree is None:
        self.schwab_option_chain_rows = {}
        return

    try:
        for iid in tree.get_children():
            tree.delete(iid)
        self.schwab_option_chain_rows = {}

        for index, row in enumerate(rows):
            iid = f"option_{index}"
            self.schwab_option_chain_rows[iid] = row
            tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    row["expiration_label"],
                    _format_number(row["strike"], digits=2),
                    _contract_price(row.get("call"), "bid"),
                    _contract_price(row.get("call"), "ask"),
                    _contract_price(row.get("put"), "bid"),
                    _contract_price(row.get("put"), "ask"),
                    _contract_int(row.get("call"), "totalVolume", "volume"),
                    _contract_int(row.get("put"), "totalVolume", "volume"),
                ),
            )
    except tk.TclError:
        self.schwab_option_chain_tree = None
        self.schwab_option_chain_rows = {}
        return


def _use_selected_schwab_option(self: tk.Tk, option_type: str) -> None:
    tree = _resolve_option_chain_tree(self)
    rows = getattr(self, "schwab_option_chain_rows", {})
    if tree is None:
        messagebox.showerror("Option selection blocked", "Load the Schwab option chain first.")
        return

    selection = tree.selection()
    if not selection:
        messagebox.showerror("Option selection blocked", "Select an option-chain row first.")
        return

    option_type = "put" if option_type.startswith("put") else "call"
    row = rows.get(selection[0])
    if row is None:
        messagebox.showerror("Option selection blocked", "Could not find the selected option-chain row.")
        return

    contract = row.get(option_type)
    if not isinstance(contract, dict):
        messagebox.showerror("Option selection blocked", f"The selected row does not include a {option_type.upper()} contract.")
        return

    bid = _first_number(contract, "bid")
    ask = _first_number(contract, "ask")
    mark = _first_number(contract, "mark")
    limit_price = ask if ask is not None and ask > 0 else mark
    if limit_price is None or limit_price <= 0:
        messagebox.showerror("Option selection blocked", f"The selected {option_type.upper()} has no usable ask or mark price.")
        return

    symbol = str(row.get("underlying") or self.symbol_var.get().strip().upper())
    self.symbol_var.set(symbol)
    if hasattr(self, "options_symbol_var"):
        self.options_symbol_var.set(symbol)
    self.side_var.set("buy")
    self.order_type_var.set("limit")
    self.time_in_force_var.set("Day")
    self.options_action_var.set("Buy")
    self.options_strategy_var.set("Long Put" if option_type == "put" else "Long Call")
    self.options_type_var.set("Put" if option_type == "put" else "Call")
    self.options_expiration_var.set(row["expiration_label"])
    self.options_strike_var.set(_format_number(row["strike"], digits=2))
    self.options_bid_var.set(_format_optional_number(bid))
    self.options_ask_var.set(_format_optional_number(ask))
    self.options_mark_var.set(_format_optional_number(mark))
    self.options_premium_var.set(_format_number(limit_price, digits=2))
    self.options_credit_var.set("")

    contract_symbol = str(contract.get("symbol") or "")
    description = str(contract.get("description") or "")
    self.schwab_research_selected_contract_symbol = contract_symbol
    self.schwab_option_chain_status_var.set(
        f"Selected {symbol} {row['expiration_label']} {row['strike']:g} {option_type.upper()} @ {_format_number(limit_price, digits=2)}"
    )
    if hasattr(self, "schwab_preview_status_var"):
        self.schwab_preview_status_var.set("Last Schwab preview: option selected only")

    _set_schwab_mode_text(
        self,
        "SCHWAB OPTION SELECTED\n"
        "======================\n\n"
        f"Underlying: {symbol}\n"
        f"Expiration: {row['expiration_label']}\n"
        f"Strike: {row['strike']:g}\n"
        f"Type: {option_type.upper()}\n"
        f"Bid: {_format_optional_number(bid)}\n"
        f"Ask: {_format_optional_number(ask)}\n"
        f"Mark: {_format_optional_number(mark)}\n"
        f"Limit / debit filled from: {_format_number(limit_price, digits=2)}\n"
        f"Schwab contract symbol: {contract_symbol or '--'}\n"
        f"Description: {description or '--'}\n\n"
        "The options what-if fields were populated from the chain.\n"
        "No order was submitted or previewed. The current Schwab live-submit path remains the stock/ETF path.",
    )
    render_greeks = getattr(self, "render_schwab_research_greeks", None)
    if callable(render_greeks):
        render_greeks()


def _option_chain_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    underlying = str(payload.get("symbol") or "").upper()
    call_map = payload.get("callExpDateMap") or {}
    put_map = payload.get("putExpDateMap") or {}
    if not isinstance(call_map, dict):
        call_map = {}
    if not isinstance(put_map, dict):
        put_map = {}

    merged: dict[tuple[str, float], dict[str, Any]] = {}
    _merge_side(merged, call_map, "call", underlying)
    _merge_side(merged, put_map, "put", underlying)

    return sorted(
        merged.values(),
        key=lambda row: (row.get("expiration_date") or "9999-12-31", row.get("dte", 99999), row["strike"]),
    )


def _merge_side(
    merged: dict[tuple[str, float], dict[str, Any]],
    exp_map: dict[str, Any],
    side: str,
    underlying: str,
) -> None:
    for exp_key, strike_map in exp_map.items():
        if not isinstance(strike_map, dict):
            continue
        expiration_date, dte = _expiration_parts(str(exp_key))
        for strike_key, contracts in strike_map.items():
            try:
                strike = float(str(strike_key).replace(",", ""))
            except ValueError:
                continue
            contract = contracts[0] if isinstance(contracts, list) and contracts else contracts
            if not isinstance(contract, dict):
                continue
            expiration_label = _expiration_label(expiration_date, dte, contract)
            key = (str(exp_key), strike)
            row = merged.setdefault(
                key,
                {
                    "underlying": underlying,
                    "expiration_key": str(exp_key),
                    "expiration_date": expiration_date,
                    "expiration_label": expiration_label,
                    "dte": dte,
                    "strike": strike,
                    "call": None,
                    "put": None,
                },
            )
            row[side] = contract


def _expiration_parts(exp_key: str) -> tuple[str, int | None]:
    date_part, _, dte_part = exp_key.partition(":")
    try:
        dte = int(dte_part) if dte_part else None
    except ValueError:
        dte = None
    return date_part, dte


def _expiration_label(expiration_date: str, dte: int | None, contract: dict[str, Any]) -> str:
    raw_date = str(contract.get("expirationDate") or expiration_date)
    date_text = raw_date[:10]
    try:
        parsed = datetime.strptime(date_text, "%Y-%m-%d")
        label = parsed.strftime("%b %d %Y")
    except ValueError:
        label = date_text or expiration_date
    if dte is None:
        dte = _first_int(contract, "daysToExpiration")
    return f"{label} ({dte}d)" if dte is not None else label


def _underlying_price(payload: dict[str, Any]) -> float | None:
    direct = _first_number(payload, "underlyingPrice", "lastPrice")
    if direct is not None:
        return direct
    underlying = payload.get("underlying")
    if isinstance(underlying, dict):
        return _first_number(underlying, "last", "lastPrice", "mark", "markPrice", "close")
    return None


def _contract_price(contract: Any, key: str) -> str:
    if not isinstance(contract, dict):
        return "--"
    value = _first_number(contract, key)
    return _format_optional_number(value)


def _contract_int(contract: Any, *keys: str) -> str:
    if not isinstance(contract, dict):
        return "--"
    value = _first_int(contract, *keys)
    return "--" if value is None else f"{value:,}"


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


def _first_int(source: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = source.get(key)
        if value in (None, ""):
            continue
        try:
            return int(float(str(value).replace(",", "")))
        except ValueError:
            continue
    return None


def _format_number(value: float, *, digits: int = 2) -> str:
    formatted = f"{value:.{digits}f}"
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted


def _format_optional_number(value: float | None) -> str:
    return "" if value is None else _format_number(value, digits=2)


def _format_optional_price(value: float | None) -> str:
    return "--" if value is None else f"${value:,.2f}"
