from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type


_BALANCE_ACTION_BUTTONS = {"Edit Selected Position", "TP/SL Orders", "Open Orders"}


def install_hyperliquid_workspace_layout_guard(app_cls: Type[tk.Tk]) -> None:
    """Keep the Hyperliquid balances action bar visible across DPI/resolution changes.

    The balances table can ask for more vertical space on high-DPI monitors. Packing
    the action bar at the bottom before the table lets the table shrink first instead
    of clipping the buttons.
    """

    from app.ui import trading_workspace_extension as workspace

    original = workspace._build_hyperliquid_trading_tab
    if getattr(original, "_hyperliquid_layout_guard_installed", False):
        return

    def guarded_build_hyperliquid_trading_tab(self: tk.Tk, parent: ttk.Frame) -> None:
        original(self, parent)
        _reserve_hyperliquid_balance_action_bar(self)
        try:
            self.after_idle(lambda: _reserve_hyperliquid_balance_action_bar(self))
        except tk.TclError:
            pass

    setattr(guarded_build_hyperliquid_trading_tab, "_hyperliquid_layout_guard_installed", True)
    workspace._build_hyperliquid_trading_tab = guarded_build_hyperliquid_trading_tab


def _reserve_hyperliquid_balance_action_bar(self: tk.Tk) -> None:
    table = getattr(self, "hyperliquid_workspace_holdings_table", None)
    if table is None:
        return

    try:
        if not table.winfo_exists():
            return
        parent = table.master
        action_bar = _find_balance_action_bar(parent, table)
        if action_bar is None:
            return

        try:
            action_bar.pack_forget()
        except tk.TclError:
            pass
        action_bar.pack(fill=tk.X, side=tk.BOTTOM, pady=(8, 0), before=table)
    except tk.TclError:
        return


def _find_balance_action_bar(parent: tk.Misc, table: ttk.Treeview) -> ttk.Frame | None:
    for child in parent.winfo_children():
        if child is table:
            continue
        if not isinstance(child, ttk.Frame):
            continue
        texts = _button_texts(child)
        if _BALANCE_ACTION_BUTTONS.issubset(texts):
            return child
    return None


def _button_texts(parent: tk.Misc) -> set[str]:
    texts: set[str] = set()
    for child in parent.winfo_children():
        if not isinstance(child, ttk.Button):
            continue
        try:
            texts.add(str(child.cget("text")))
        except tk.TclError:
            continue
    return texts
