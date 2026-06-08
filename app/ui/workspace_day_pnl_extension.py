from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Type

from app.ui import options_lab_extension as workspace

_ORIGINAL_WORKSPACE_HOLDING_ROWS = workspace._workspace_holding_rows


def install_workspace_day_pnl_extension(app_cls: Type[tk.Tk]) -> None:
    """Show per-position day P&L in the Schwab and Hyperliquid trading tabs."""

    workspace._workspace_holdings_table = _workspace_holdings_table_with_day_pnl
    workspace._workspace_holding_rows = _workspace_holding_rows_with_day_pnl
    workspace._populate_workspace_holdings_table = _populate_workspace_holdings_table_with_day_pnl

    # schwab_trading_tab imports the holdings-table builder directly, then rebuilds
    # the Schwab account area into Holdings/Open Orders/Recent Orders tabs. Patch
    # that imported reference too so the Schwab Account > Holdings table gets the
    # same Day P&L column as the Hyperliquid balances table.
    try:
        from app.ui import schwab_trading_tab as schwab_workspace
    except Exception:
        return
    schwab_workspace._workspace_holdings_table = _workspace_holdings_table_with_day_pnl


def _workspace_holdings_table_with_day_pnl(parent: ttk.Frame, include_custom_pnl: bool = False) -> ttk.Treeview:
    columns = ("symbol", "type", "qty", "last", "value", "pnl", "day_pnl")
    table = ttk.Treeview(parent, columns=columns, show="headings", height=6, selectmode="browse")
    headings = {
        "symbol": ("Symbol", 86, tk.W),
        "type": ("Type", 78, tk.W),
        "qty": ("Qty", 84, tk.E),
        "last": ("Last", 86, tk.E),
        "value": ("Value", 96, tk.E),
        "pnl": ("P&L", 104 if include_custom_pnl else 96, tk.E),
        "day_pnl": ("Day P&L", 96, tk.E),
    }
    for column in tuple(str(column) for column in table["columns"]):
        label, width, anchor = headings[column]
        table.heading(column, text=label)
        table.column(column, width=width, anchor=anchor, stretch=True)
    table.pack(fill=tk.BOTH, expand=True)
    table.tag_configure("positive", foreground="#047857")
    table.tag_configure("negative", foreground="#b91c1c")
    table.tag_configure("cash", foreground="#334155")
    return table


def _populate_workspace_holdings_table_with_day_pnl(table: ttk.Treeview, rows: list[dict[str, object]]) -> None:
    for row_id in table.get_children():
        table.delete(row_id)
    for index, row in enumerate(rows):
        pnl = row.get("pnl")
        tag = (
            "cash"
            if str(row.get("type", "")).lower() == "cash"
            else "positive"
            if isinstance(pnl, (int, float)) and pnl > 0
            else "negative"
            if isinstance(pnl, (int, float)) and pnl < 0
            else ""
        )
        values_by_column = {
            "symbol": row.get("symbol", ""),
            "type": row.get("type", ""),
            "qty": row.get("qty", ""),
            "last": row.get("last", ""),
            "value": row.get("value", ""),
            "pnl": row.get("pnl_text", ""),
            "day_pnl": row.get("day_pnl_text", ""),
            "raw_pnl": row.get("raw_pnl_text", ""),
            "custom_pnl": row.get("custom_pnl_text", ""),
            "basis_status": row.get("basis_status", ""),
        }
        table.insert(
            "",
            tk.END,
            iid=f"holding_{index}",
            values=tuple(values_by_column.get(str(column), "") for column in table["columns"]),
            tags=(tag,) if tag else (),
        )


def _workspace_holding_rows_with_day_pnl(portfolio: Any, venue: str) -> list[dict[str, object]]:
    rows = _ORIGINAL_WORKSPACE_HOLDING_ROWS(portfolio, venue)
    day_pnl_by_symbol = _workspace_day_pnl_by_display_symbol(portfolio)
    for row in rows:
        symbol = str(row.get("symbol", "")).strip()
        day_pnl = day_pnl_by_symbol.get(symbol.upper())
        row["day_pnl"] = day_pnl
        row["day_pnl_text"] = workspace._fmt_money(day_pnl) if day_pnl is not None else "--"
    return rows


def _workspace_day_pnl_by_display_symbol(portfolio: Any) -> dict[str, float | None]:
    by_symbol: dict[str, float | None] = {}

    for cash in getattr(portfolio, "display_cash_positions", lambda: [])():
        display_symbol = str(getattr(cash, "display_symbol", "")).strip().upper()
        if display_symbol:
            by_symbol[display_symbol] = None

    for symbol, position in getattr(portfolio, "positions", {}).items():
        display_symbol = str(getattr(position, "symbol", symbol)).strip().upper()
        if display_symbol:
            by_symbol[display_symbol] = getattr(position, "day_profit_loss", None)

    return by_symbol
