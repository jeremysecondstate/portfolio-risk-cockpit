from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Type

from app.core.portfolio import CashPosition, Portfolio, Position
from app.ui import polished_theme

_HYPERLIQUID_SPOT_ALIASES = {
    "UBTC": "BTC",
    "UBTC/USDC": "BTC",
}
_HIDDEN_CASH_SOURCES = {"HYPERLIQUID PERPS"}
_NEUTRAL_FOREGROUND = "#0f172a"
_CASH_FOREGROUND = "#334155"
_POSITIVE_FOREGROUND = "#047857"
_NEGATIVE_FOREGROUND = "#b91c1c"
_POSITION_TABLE_COLUMNS = [
    ("symbol", "Symbol", 90, tk.W),
    ("asset_type", "Type", 84, tk.W),
    ("qty", "Qty", 92, tk.E),
    ("avg_cost", "Avg Cost", 112, tk.E),
    ("last", "Last", 112, tk.E),
    ("cost_basis", "Cost Basis", 118, tk.E),
    ("value", "Value", 122, tk.E),
    ("weight", "Weight", 88, tk.E),
    ("pnl", "P&L $", 112, tk.E),
    ("pnl_pct", "P&L %", 86, tk.E),
    ("day_pnl", "Day P&L", 112, tk.E),
]
_LEFT_POSITION_COLUMNS = tuple(column for column, *_rest in _POSITION_TABLE_COLUMNS if column not in {"pnl", "pnl_pct", "day_pnl"})
_PNL_POSITION_COLUMNS = ("pnl", "pnl_pct", "day_pnl")


def install_cash_positions_extension(app_cls: Type[tk.Tk]) -> None:
    """Show cash-like balances as neutral rows in the Positions table."""

    app_cls.refresh_portfolio = _refresh_portfolio_with_cash_rows  # type: ignore[method-assign]
    app_cls._merge_hyperliquid_portfolio = _merge_hyperliquid_portfolio_with_cash_rows  # type: ignore[method-assign]


def _clean_hyperliquid_symbol(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean.startswith("HL:"):
        clean = clean[3:]

    # Keep perp symbols explicit so a derivative row cannot be confused with
    # spot HYPE/BTC or a Schwab equity position.
    if clean.endswith("-PERP-SHORT") or clean.endswith("-PERP"):
        return clean

    if clean.endswith("-SPOT"):
        clean = clean[: -len("-SPOT")]

    return _HYPERLIQUID_SPOT_ALIASES.get(clean, clean)


def _hyperliquid_position_type(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean.endswith("-PERP-SHORT"):
        return "Perp Short"
    if clean.endswith("-PERP"):
        return "Perp Long"
    if clean.endswith("-SPOT"):
        return "Spot"
    return "Hyperliquid"


def _position_type(position: Position) -> str:
    explicit_type = getattr(position, "asset_type", None)
    if explicit_type:
        return str(explicit_type)

    symbol = position.symbol.upper()
    if symbol.endswith("-PERP-SHORT"):
        return "Perp Short"
    if symbol.endswith("-PERP"):
        return "Perp Long"
    if symbol.endswith("-SPOT"):
        return "Spot"
    return "Equity"


def _is_hidden_cash_position(cash: CashPosition) -> bool:
    return cash.source.strip().upper() in _HIDDEN_CASH_SOURCES


def _ensure_position_type_column(table: ttk.Treeview | Any) -> None:
    columns = list(table["columns"])
    if "asset_type" not in columns:
        insert_at = columns.index("symbol") + 1 if "symbol" in columns else 1
        columns.insert(insert_at, "asset_type")
        table.configure(columns=tuple(columns))

    _configure_position_table_headings(table)


def _configure_position_table_headings(table: ttk.Treeview | Any) -> None:
    """Re-apply every heading after adding the Type column."""

    active_columns = set(table["columns"])
    for column, label, width, anchor in _POSITION_TABLE_COLUMNS:
        if column not in active_columns:
            continue
        table.heading(column, text=label)
        table.column(column, width=width, anchor=anchor, stretch=True)


def _table_values(table: ttk.Treeview | Any, values_by_column: dict[str, str]) -> tuple[str, ...]:
    return tuple(values_by_column.get(column, "") for column in table["columns"])


def _merge_hyperliquid_portfolio_with_cash_rows(self: tk.Tk, hyperliquid_portfolio: Portfolio) -> Portfolio:
    """Merge Hyperliquid while preserving source-level cash display rows."""

    current = self.broker.get_portfolio()
    non_hyperliquid_cash = round(current.cash - self.last_hyperliquid_cash_adjustment, 2)
    previous_hyperliquid_symbols = set(getattr(self, "last_hyperliquid_display_symbols", set()))
    positions = {
        symbol: position
        for symbol, position in current.positions.items()
        if not symbol.startswith("HL:") and symbol not in previous_hyperliquid_symbols
    }
    cash_positions = {
        key: cash
        for key, cash in current.cash_positions.items()
        if "HYPERLIQUID" not in key.upper()
    }

    display_symbols: set[str] = set()
    for symbol, position in hyperliquid_portfolio.positions.items():
        display_symbol = _clean_hyperliquid_symbol(symbol)
        display_symbols.add(display_symbol)
        display_position = Position(
            symbol=display_symbol,
            quantity=position.quantity,
            average_cost=position.average_cost,
            last_price=position.last_price,
            day_profit_loss=position.day_profit_loss,
            day_profit_loss_percent=position.day_profit_loss_percent,
            open_profit_loss=position.open_profit_loss,
        )
        setattr(display_position, "asset_type", _hyperliquid_position_type(symbol))
        positions[display_symbol] = display_position

    self.last_hyperliquid_display_symbols = display_symbols
    for key, cash in hyperliquid_portfolio.cash_positions.items():
        cash_positions[f"HL:{key}"] = cash

    return Portfolio(
        cash=round(non_hyperliquid_cash + hyperliquid_portfolio.cash, 2),
        positions=positions,
        cash_positions=cash_positions,
    )


def _refresh_portfolio_with_cash_rows(self: tk.Tk) -> None:
    portfolio = self.broker.get_portfolio()
    self.cash_value_label.configure(text=polished_theme._format_money(portfolio.cash))
    self.positions_value_label.configure(text=polished_theme._format_money(portfolio.positions_value))
    self.total_value_label.configure(text=polished_theme._format_money(portfolio.total_value))
    self.unrealized_pnl_value_label.configure(
        text=(
            f"{polished_theme._format_money(portfolio.unrealized_profit_loss)} "
            f"({polished_theme._format_percent(portfolio.unrealized_profit_loss_percent)})"
        )
    )
    self.day_pnl_value_label.configure(text=polished_theme._format_optional_money(portfolio.day_profit_loss))
    self.snapshot_source_label.configure(text=f"Snapshot: {self.broker.source_message}")

    _ensure_split_positions_table(self)
    _ensure_position_type_column(self.positions_table)
    _clear_positions_tables(self)
    _configure_position_tags(self)

    total_value = max(portfolio.total_value, 0.01)
    row_index = 0

    for cash in portfolio.display_cash_positions():
        if _is_hidden_cash_position(cash):
            continue

        weight = (cash.market_value / total_value) * 100
        row_id = f"row_{row_index}"
        row_index += 1
        _insert_position_row(
            self,
            row_id=row_id,
            values={
                "symbol": cash.display_symbol,
                "asset_type": "Cash",
                "qty": f"{cash.quantity:g}",
                "avg_cost": polished_theme._format_money(cash.average_cost),
                "last": polished_theme._format_money(cash.last_price),
                "cost_basis": polished_theme._format_money(cash.cost_basis),
                "value": polished_theme._format_money(cash.market_value),
                "weight": f"{weight:.1f}%",
                "pnl": "--",
                "pnl_pct": "--",
                "day_pnl": "--",
            },
            pnl_values={"pnl": None, "pnl_pct": None, "day_pnl": None},
            main_tag="cash_position",
        )

    for symbol in sorted(portfolio.positions):
        p = portfolio.positions[symbol]
        position_type = _position_type(p)
        weight = (p.market_value / total_value) * 100
        row_id = f"row_{row_index}"
        row_index += 1
        _insert_position_row(
            self,
            row_id=row_id,
            values={
                "symbol": p.symbol,
                "asset_type": position_type,
                "qty": f"{p.quantity:g}",
                "avg_cost": polished_theme._format_money(p.average_cost),
                "last": polished_theme._format_money(p.last_price),
                "cost_basis": polished_theme._format_money(p.cost_basis),
                "value": polished_theme._format_money(p.market_value),
                "weight": f"{weight:.1f}%",
                "pnl": polished_theme._format_money(p.unrealized_profit_loss),
                "pnl_pct": polished_theme._format_percent(p.unrealized_profit_loss_percent),
                "day_pnl": polished_theme._format_optional_money(p.day_profit_loss),
            },
            pnl_values={
                "pnl": p.unrealized_profit_loss,
                "pnl_pct": p.unrealized_profit_loss_percent,
                "day_pnl": p.day_profit_loss,
            },
            main_tag="data_neutral",
        )

    polished_theme._update_risk_alerts(self, portfolio)


def _ensure_split_positions_table(self: tk.Tk) -> None:
    """Split the visual table so only P&L cells receive red/green color.

    ttk.Treeview applies foreground color to an entire row. To color P&L cells
    independently while keeping the symbol/quantity/value columns neutral, keep
    the normal columns in the main Treeview and render each P&L column as its own
    one-column Treeview that scrolls with the main table.
    """

    existing = getattr(self, "positions_pnl_tables", None)
    if isinstance(existing, dict) and all(_widget_alive(widget) for widget in existing.values()):
        return

    parent = self.positions_table.master
    for child in list(parent.winfo_children()):
        child.destroy()

    parent.rowconfigure(0, weight=1)
    parent.rowconfigure(1, weight=0)
    parent.columnconfigure(0, weight=1)
    for column in range(1, 5):
        parent.columnconfigure(column, weight=0)

    main_table = ttk.Treeview(parent, columns=_LEFT_POSITION_COLUMNS, show="headings", height=14)
    main_table.grid(row=0, column=0, sticky="nsew")
    self.positions_table = main_table

    pnl_tables: dict[str, ttk.Treeview] = {}
    for grid_column, column in enumerate(_PNL_POSITION_COLUMNS, start=1):
        table = ttk.Treeview(parent, columns=(column,), show="headings", height=14, selectmode="browse")
        table.grid(row=0, column=grid_column, sticky="ns")
        pnl_tables[column] = table
    self.positions_pnl_tables = pnl_tables

    y_scrollbar = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=lambda *args: _yview_all_position_tables(self, *args))
    y_scrollbar.grid(row=0, column=4, sticky="ns")
    main_table.configure(yscrollcommand=y_scrollbar.set)
    for table in pnl_tables.values():
        table.configure(yscrollcommand=y_scrollbar.set)

    x_scrollbar = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=main_table.xview)
    x_scrollbar.grid(row=1, column=0, sticky="ew")
    main_table.configure(xscrollcommand=x_scrollbar.set)

    _bind_synced_table_scroll(self, main_table)
    for table in pnl_tables.values():
        _bind_synced_table_scroll(self, table)

    _configure_position_table_headings(main_table)
    for table in pnl_tables.values():
        _configure_position_table_headings(table)

    _bind_position_ticket_shortcuts(self)


def _widget_alive(widget: tk.Widget) -> bool:
    try:
        return bool(widget.winfo_exists())
    except Exception:
        return False


def _all_position_tables(self: tk.Tk) -> list[ttk.Treeview]:
    tables = [self.positions_table]
    pnl_tables = getattr(self, "positions_pnl_tables", {})
    if isinstance(pnl_tables, dict):
        tables.extend(pnl_tables[column] for column in _PNL_POSITION_COLUMNS if column in pnl_tables)
    return tables


def _yview_all_position_tables(self: tk.Tk, *args: str) -> None:
    for table in _all_position_tables(self):
        table.yview(*args)


def _bind_synced_table_scroll(self: tk.Tk, table: ttk.Treeview) -> None:
    def _on_mousewheel(event: tk.Event) -> str:
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = -1 if getattr(event, "delta", 0) > 0 else 1
        for synced_table in _all_position_tables(self):
            synced_table.yview_scroll(delta, "units")
        return "break"

    for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
        table.bind(sequence, _on_mousewheel, add="+")


def _bind_position_ticket_shortcuts(self: tk.Tk) -> None:
    for table in _all_position_tables(self):
        table.bind("<ButtonRelease-1>", lambda event, app=self, source=table: _load_ticket_from_position_click(app, source, event), add="+")
        table.bind("<Motion>", lambda event, source=table: _update_position_cursor(source, event), add="+")
        table.bind("<Leave>", lambda _event, source=table: source.configure(cursor=""), add="+")


def _update_position_cursor(table: ttk.Treeview, event: tk.Event) -> None:
    row_id = table.identify_row(event.y)
    table.configure(cursor="hand2" if row_id else "")


def _load_ticket_from_position_click(self: tk.Tk, table: ttk.Treeview, event: tk.Event) -> None:
    row_id = table.identify_row(event.y)
    if not row_id:
        return

    values = _row_values_by_column(self.positions_table, row_id)
    symbol = values.get("symbol", "").strip()
    asset_type = values.get("asset_type", "").strip()
    if not symbol or asset_type.lower() == "cash":
        return

    ticket_symbol = _ticket_symbol_from_position(symbol)
    if not ticket_symbol:
        return

    self.symbol_var.set(ticket_symbol)

    if _position_is_hyperliquid(asset_type, symbol):
        if hasattr(self, "trade_venue_var"):
            self.trade_venue_var.set("Hyperliquid")
        if hasattr(self, "hyperliquid_coin_var"):
            self.hyperliquid_coin_var.set(ticket_symbol)
        _fill_hyperliquid_workspace_ticket(self, asset_type, symbol, ticket_symbol)
        if hasattr(self, "on_trading_venue_changed"):
            try:
                self.on_trading_venue_changed()
            except Exception:
                pass
    else:
        if hasattr(self, "trade_venue_var"):
            self.trade_venue_var.set("Schwab")
        if hasattr(self, "hyperliquid_coin_var"):
            self.hyperliquid_coin_var.set("")


def _fill_hyperliquid_workspace_ticket(self: tk.Tk, asset_type: str, symbol: str, ticket_symbol: str) -> None:
    normalized_type = asset_type.strip().lower()
    normalized_symbol = symbol.strip().upper()
    is_perp = normalized_type.startswith("perp") or "-PERP" in normalized_symbol

    if is_perp:
        if hasattr(self, "hyperliquid_workspace_active_ticket_var"):
            self.hyperliquid_workspace_active_ticket_var.set("perp")
        if hasattr(self, "hyperliquid_perp_coin_var"):
            self.hyperliquid_perp_coin_var.set(ticket_symbol)
        if hasattr(self, "hyperliquid_perp_symbol_var"):
            self.hyperliquid_perp_symbol_var.set(ticket_symbol)
        return

    if hasattr(self, "hyperliquid_workspace_active_ticket_var"):
        self.hyperliquid_workspace_active_ticket_var.set("spot")
    if hasattr(self, "hyperliquid_spot_symbol_var"):
        self.hyperliquid_spot_symbol_var.set(ticket_symbol)
    if hasattr(self, "hyperliquid_spot_coin_var"):
        self.hyperliquid_spot_coin_var.set(ticket_symbol)


def _row_values_by_column(table: ttk.Treeview, row_id: str) -> dict[str, str]:
    raw_values = table.item(row_id, "values")
    return {
        str(column): str(raw_values[index])
        for index, column in enumerate(table["columns"])
        if index < len(raw_values)
    }


def _position_is_hyperliquid(asset_type: str, symbol: str) -> bool:
    normalized_type = asset_type.strip().lower()
    normalized_symbol = symbol.strip().upper()
    return (
        normalized_type.startswith("perp")
        or normalized_type == "spot"
        or normalized_type == "hyperliquid"
        or normalized_symbol.endswith("-SPOT")
        or "-PERP" in normalized_symbol
    )


def _ticket_symbol_from_position(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean.startswith("HL:"):
        clean = clean[3:]
    if "(" in clean:
        clean = clean.split("(", 1)[0].strip()
    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
    return clean


def _clear_positions_tables(self: tk.Tk) -> None:
    for table in _all_position_tables(self):
        for row_id in table.get_children():
            table.delete(row_id)


def _configure_position_tags(self: tk.Tk) -> None:
    self.positions_table.tag_configure("cash_position", foreground=_CASH_FOREGROUND)
    self.positions_table.tag_configure("data_neutral", foreground=_NEUTRAL_FOREGROUND)

    pnl_tables = getattr(self, "positions_pnl_tables", {})
    if not isinstance(pnl_tables, dict):
        return
    for table in pnl_tables.values():
        table.tag_configure("pnl_positive", foreground=_POSITIVE_FOREGROUND)
        table.tag_configure("pnl_negative", foreground=_NEGATIVE_FOREGROUND)
        table.tag_configure("pnl_neutral", foreground=_CASH_FOREGROUND)


def _insert_position_row(
    self: tk.Tk,
    *,
    row_id: str,
    values: dict[str, str],
    pnl_values: dict[str, float | None],
    main_tag: str,
) -> None:
    self.positions_table.insert(
        "",
        tk.END,
        iid=row_id,
        values=_table_values(self.positions_table, values),
        tags=(main_tag,),
    )

    pnl_tables = getattr(self, "positions_pnl_tables", {})
    if not isinstance(pnl_tables, dict):
        return
    for column in _PNL_POSITION_COLUMNS:
        table = pnl_tables.get(column)
        if table is None:
            continue
        table.insert(
            "",
            tk.END,
            iid=row_id,
            values=(values.get(column, ""),),
            tags=(_pnl_value_tag(pnl_values.get(column)),),
        )


def _pnl_value_tag(value: float | None) -> str:
    if value is None or abs(value) <= 0.005:
        return "pnl_neutral"
    if value > 0:
        return "pnl_positive"
    return "pnl_negative"
