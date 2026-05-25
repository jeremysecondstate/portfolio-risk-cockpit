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
    """Re-apply every heading after adding the Type column.

    Tk can drop existing heading labels when a Treeview's columns are reconfigured.
    The cash/asset-type extension mutates the columns at refresh time, so restore
    the full header set every time instead of only setting the new Type header.
    """

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

    _ensure_position_type_column(self.positions_table)

    for row_id in self.positions_table.get_children():
        self.positions_table.delete(row_id)

    self.positions_table.tag_configure("cash_position", foreground="#334155")
    self.positions_table.tag_configure("pnl_positive", foreground="#047857")
    self.positions_table.tag_configure("pnl_negative", foreground="#b91c1c")
    self.positions_table.tag_configure("pnl_neutral", foreground="#334155")
    total_value = max(portfolio.total_value, 0.01)

    for cash in portfolio.display_cash_positions():
        if _is_hidden_cash_position(cash):
            continue

        weight = (cash.market_value / total_value) * 100
        self.positions_table.insert(
            "",
            tk.END,
            values=_table_values(
                self.positions_table,
                {
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
            ),
            tags=("cash_position",),
        )

    for symbol in sorted(portfolio.positions):
        p = portfolio.positions[symbol]
        position_type = _position_type(p)
        weight = (p.market_value / total_value) * 100
        self.positions_table.insert(
            "",
            tk.END,
            values=_table_values(
                self.positions_table,
                {
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
            ),
            tags=(_position_row_tag(p),),
        )

    polished_theme._update_risk_alerts(self, portfolio)


def _position_row_tag(position: Position) -> str:
    """Choose a live red/green row color from the most active P&L signal.

    Tk's built-in Treeview supports row foreground colors, not independent
    per-cell foreground colors. Use Day P&L when it exists, because that is the
    number most likely to flip during live refreshes; otherwise fall back to the
    open/unrealized P&L. This removes the old fixed red Perp color and makes
    positive rows green, negative rows red, and flat rows neutral.
    """

    driver = position.day_profit_loss
    if driver is None or abs(driver) <= 0.005:
        driver = position.unrealized_profit_loss

    if driver > 0.005:
        return "pnl_positive"
    if driver < -0.005:
        return "pnl_negative"
    return "pnl_neutral"
