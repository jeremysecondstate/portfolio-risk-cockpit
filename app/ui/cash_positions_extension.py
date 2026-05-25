from __future__ import annotations

import tkinter as tk
from typing import Any, Type

from app.core.portfolio import CashPosition, Portfolio, Position
from app.ui import polished_theme

_HYPERLIQUID_SPOT_ALIASES = {
    "UBTC": "BTC",
    "UBTC/USDC": "BTC",
}
_HIDDEN_CASH_SOURCES = {"HYPERLIQUID PERPS"}


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
    if "asset_type" in columns:
        return

    insert_at = columns.index("symbol") + 1 if "symbol" in columns else 1
    columns.insert(insert_at, "asset_type")
    table.configure(columns=tuple(columns))
    table.heading("asset_type", text="Type")
    table.column("asset_type", width=84, anchor=tk.W, stretch=True)
    if "symbol" in columns:
        table.column("symbol", anchor=tk.W)


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
    self.positions_table.tag_configure("perp_position", foreground="#7c2d12")
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
        row_tag = "perp_position" if position_type.startswith("Perp") else "pnl_positive" if p.unrealized_profit_loss >= 0 else "pnl_negative"
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
            tags=(row_tag,),
        )

    polished_theme._update_risk_alerts(self, portfolio)
