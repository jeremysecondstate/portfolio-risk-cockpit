from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Type

from app.ui import options_lab_extension as workspace

_ORIGINAL_WORKSPACE_HOLDING_ROWS = workspace._workspace_holding_rows
_NEUTRAL_FOREGROUND = "#0f172a"
_CASH_FOREGROUND = "#334155"
_POSITIVE_FOREGROUND = "#047857"
_NEGATIVE_FOREGROUND = "#b91c1c"


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
    """Build a holdings table with an independently colored Day P&L lane.

    ttk.Treeview applies row tags to every visible cell, so a single tree cannot
    color total P&L and Day P&L independently. Keep the normal holdings table as
    the returned/clickable widget, and render Day P&L as a narrow synced tree on
    the right so its red/green state can follow the daily value only.
    """

    shell = ttk.Frame(parent, style="Panel.TFrame")
    shell.pack(fill=tk.BOTH, expand=True)
    shell.columnconfigure(0, weight=1)
    shell.columnconfigure(1, weight=0)
    shell.rowconfigure(0, weight=1)

    columns = ("symbol", "type", "qty", "last", "value", "pnl")
    table = ttk.Treeview(shell, columns=columns, show="headings", height=6, selectmode="browse")
    headings = {
        "symbol": ("Symbol", 86, tk.W),
        "type": ("Type", 78, tk.W),
        "qty": ("Qty", 84, tk.E),
        "last": ("Last", 86, tk.E),
        "value": ("Value", 96, tk.E),
        "pnl": ("P&L", 104 if include_custom_pnl else 96, tk.E),
    }
    for column in tuple(str(column) for column in table["columns"]):
        label, width, anchor = headings[column]
        table.heading(column, text=label)
        table.column(column, width=width, anchor=anchor, stretch=True)
    table.grid(row=0, column=0, sticky="nsew")
    table.tag_configure("positive", foreground=_POSITIVE_FOREGROUND)
    table.tag_configure("negative", foreground=_NEGATIVE_FOREGROUND)
    table.tag_configure("cash", foreground=_CASH_FOREGROUND)

    day_table = ttk.Treeview(shell, columns=("day_pnl",), show="headings", height=6, selectmode="browse")
    day_table.heading("day_pnl", text="Day P&L")
    day_table.column("day_pnl", width=96, minwidth=82, anchor=tk.E, stretch=False)
    day_table.grid(row=0, column=1, sticky="ns")
    day_table.tag_configure("positive", foreground=_POSITIVE_FOREGROUND)
    day_table.tag_configure("negative", foreground=_NEGATIVE_FOREGROUND)
    day_table.tag_configure("neutral", foreground=_NEUTRAL_FOREGROUND)
    day_table.tag_configure("cash", foreground=_CASH_FOREGROUND)

    table._day_pnl_table = day_table  # type: ignore[attr-defined]
    table._split_day_pnl_syncing_selection = False  # type: ignore[attr-defined]
    table._split_day_pnl_syncing_scroll = False  # type: ignore[attr-defined]
    _bind_split_day_pnl_table(table, day_table)
    return table


def _bind_split_day_pnl_table(table: ttk.Treeview, day_table: ttk.Treeview) -> None:
    def sync_selection(source: ttk.Treeview, target: ttk.Treeview) -> None:
        if getattr(table, "_split_day_pnl_syncing_selection", False):
            return
        table._split_day_pnl_syncing_selection = True  # type: ignore[attr-defined]
        try:
            selection = source.selection()
            if target.selection() != selection:
                target.selection_set(selection)
            if selection and target.focus() != selection[0]:
                target.focus(selection[0])
        except tk.TclError:
            return
        finally:
            table._split_day_pnl_syncing_selection = False  # type: ignore[attr-defined]

    def sync_scroll_from_table(first: str, _last: str) -> None:
        _sync_scroll(table, day_table, first)

    def sync_scroll_from_day_table(first: str, _last: str) -> None:
        _sync_scroll(table, table, first)

    def on_mousewheel(event: tk.Event) -> str:
        if getattr(event, "num", None) == 4:
            direction = -1
        elif getattr(event, "num", None) == 5:
            direction = 1
        else:
            direction = -1 if getattr(event, "delta", 0) > 0 else 1
        try:
            table.yview_scroll(direction, "units")
            day_table.yview_scroll(direction, "units")
        except tk.TclError:
            return "break"
        return "break"

    table.configure(yscrollcommand=sync_scroll_from_table)
    day_table.configure(yscrollcommand=sync_scroll_from_day_table)
    table.bind("<<TreeviewSelect>>", lambda _event: sync_selection(table, day_table), add="+")
    day_table.bind("<<TreeviewSelect>>", lambda _event: sync_selection(day_table, table), add="+")
    for widget in (table, day_table):
        widget.bind("<MouseWheel>", on_mousewheel, add="+")
        widget.bind("<Button-4>", on_mousewheel, add="+")
        widget.bind("<Button-5>", on_mousewheel, add="+")


def _sync_scroll(owner: ttk.Treeview, target: ttk.Treeview, first: str) -> None:
    if getattr(owner, "_split_day_pnl_syncing_scroll", False):
        return
    owner._split_day_pnl_syncing_scroll = True  # type: ignore[attr-defined]
    try:
        target.yview_moveto(float(first))
    except (tk.TclError, ValueError):
        return
    finally:
        owner._split_day_pnl_syncing_scroll = False  # type: ignore[attr-defined]


def _populate_workspace_holdings_table_with_day_pnl(table: ttk.Treeview, rows: list[dict[str, object]]) -> None:
    day_table = getattr(table, "_day_pnl_table", None)
    for row_id in table.get_children():
        table.delete(row_id)
    if day_table is not None:
        for row_id in day_table.get_children():
            day_table.delete(row_id)

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
            "raw_pnl": row.get("raw_pnl_text", ""),
            "custom_pnl": row.get("custom_pnl_text", ""),
            "basis_status": row.get("basis_status", ""),
        }
        row_id = f"holding_{index}"
        table.insert(
            "",
            tk.END,
            iid=row_id,
            values=tuple(values_by_column.get(str(column), "") for column in table["columns"]),
            tags=(tag,) if tag else (),
        )
        if day_table is not None:
            day_table.insert(
                "",
                tk.END,
                iid=row_id,
                values=(row.get("day_pnl_text", ""),),
                tags=(_day_pnl_tag(row),),
            )


def _day_pnl_tag(row: dict[str, object]) -> str:
    if str(row.get("type", "")).lower() == "cash":
        return "cash"
    day_pnl = row.get("day_pnl")
    if isinstance(day_pnl, (int, float)):
        if day_pnl > 0:
            return "positive"
        if day_pnl < 0:
            return "negative"
    return "neutral"


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
