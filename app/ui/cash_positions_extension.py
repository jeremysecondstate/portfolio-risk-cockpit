from __future__ import annotations

import tkinter as tk
from typing import Type

from app.core.portfolio import Portfolio, Position
from app.ui import polished_theme


def install_cash_positions_extension(app_cls: Type[tk.Tk]) -> None:
    """Show cash-like balances as neutral rows in the Positions table."""

    app_cls.refresh_portfolio = _refresh_portfolio_with_cash_rows  # type: ignore[method-assign]
    app_cls._merge_hyperliquid_portfolio = _merge_hyperliquid_portfolio_with_cash_rows  # type: ignore[method-assign]


def _merge_hyperliquid_portfolio_with_cash_rows(self: tk.Tk, hyperliquid_portfolio: Portfolio) -> Portfolio:
    """Merge Hyperliquid while preserving source-level cash display rows."""

    current = self.broker.get_portfolio()
    non_hyperliquid_cash = round(current.cash - self.last_hyperliquid_cash_adjustment, 2)
    positions = {
        symbol: position
        for symbol, position in current.positions.items()
        if not symbol.startswith("HL:")
    }
    cash_positions = {
        key: cash
        for key, cash in current.cash_positions.items()
        if "HYPERLIQUID" not in key.upper()
    }

    for symbol, position in hyperliquid_portfolio.positions.items():
        display_symbol = f"HL:{symbol}"
        positions[display_symbol] = Position(
            symbol=display_symbol,
            quantity=position.quantity,
            average_cost=position.average_cost,
            last_price=position.last_price,
            day_profit_loss=position.day_profit_loss,
            day_profit_loss_percent=position.day_profit_loss_percent,
            open_profit_loss=position.open_profit_loss,
        )

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

    for row_id in self.positions_table.get_children():
        self.positions_table.delete(row_id)

    self.positions_table.tag_configure("cash_position", foreground="#334155")
    total_value = max(portfolio.total_value, 0.01)

    for cash in portfolio.display_cash_positions():
        weight = (cash.market_value / total_value) * 100
        self.positions_table.insert(
            "",
            tk.END,
            values=(
                cash.display_symbol,
                f"{cash.quantity:g}",
                polished_theme._format_money(cash.average_cost),
                polished_theme._format_money(cash.last_price),
                polished_theme._format_money(cash.cost_basis),
                polished_theme._format_money(cash.market_value),
                f"{weight:.1f}%",
                "--",
                "--",
                "--",
            ),
            tags=("cash_position",),
        )

    for symbol in sorted(portfolio.positions):
        p = portfolio.positions[symbol]
        weight = (p.market_value / total_value) * 100
        row_tag = "pnl_positive" if p.unrealized_profit_loss >= 0 else "pnl_negative"
        self.positions_table.insert(
            "",
            tk.END,
            values=(
                p.symbol,
                f"{p.quantity:g}",
                polished_theme._format_money(p.average_cost),
                polished_theme._format_money(p.last_price),
                polished_theme._format_money(p.cost_basis),
                polished_theme._format_money(p.market_value),
                f"{weight:.1f}%",
                polished_theme._format_money(p.unrealized_profit_loss),
                polished_theme._format_percent(p.unrealized_profit_loss_percent),
                polished_theme._format_optional_money(p.day_profit_loss),
            ),
            tags=(row_tag,),
        )

    polished_theme._update_risk_alerts(self, portfolio)
