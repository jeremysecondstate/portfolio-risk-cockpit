from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Type

from app.ui import options_lab_extension


def install_schwab_workspace_sync_extension(app_cls: Type[tk.Tk]) -> None:
    """Make the Schwab workspace actions mirror the Hyperliquid workspace pattern."""

    previous_build_layout = app_cls._build_layout

    def build_layout_with_schwab_sync_polish(self: tk.Tk) -> None:
        previous_build_layout(self)
        self.after_idle(lambda: _polish_schwab_workspace(self))

    app_cls._build_layout = build_layout_with_schwab_sync_polish  # type: ignore[method-assign]


def _polish_schwab_workspace(self: tk.Tk) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is None:
        return

    for button in _walk_buttons(self):
        label = str(button.cget("text"))
        if label == "Open Options Lab" and _inside_labelframe(button, "Schwab Trading Workspace"):
            button.configure(
                text="Sync Schwab",
                command=lambda: _run_schwab_workspace_action(self, "refresh_schwab_account", "connect_schwab"),
            )
        elif label == "Refresh Account" and _inside_labelframe(button, "Schwab Actions"):
            button.configure(
                text="Sync Account",
                command=lambda: _run_schwab_workspace_action(self, "refresh_schwab_account", "connect_schwab"),
            )
        elif label == "Preview Schwab Order" and _inside_labelframe(button, "Schwab Actions"):
            button.configure(text="Preview Stock Ticket")
        elif label == "Order Checklist" and _inside_labelframe(button, "Schwab Actions"):
            button.configure(
                text="Options Lab",
                command=lambda: _select_options_lab(self),
            )

    _set_schwab_workspace_intro(self)


def _run_schwab_workspace_action(self: tk.Tk, *command_names: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is None:
        command = options_lab_extension._first_available_command(self, *command_names)
        command()
        return

    options_lab_extension._run_workspace_action(
        self,
        venue="Schwab",
        preview_widget=output,
        command=options_lab_extension._first_available_command(self, *command_names),
    )


def _set_schwab_workspace_intro(self: tk.Tk) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is None:
        return
    current = output.get("1.0", tk.END).strip()
    if "SCHWAB TRADING WORKSPACE" not in current:
        return

    options_lab_extension._set_workspace_text(
        output,
        "SCHWAB TRADING WORKSPACE\n"
        "========================\n\n"
        "Use this tab for stocks, ETFs, Schwab previews, order history, and guarded live Schwab actions.\n\n"
        "Sync Schwab refreshes account balances and positions through the Trader API account snapshot flow.\n\n"
        "Options tickets still live in the Options What-If Lab; use Options Lab when the setup needs calls/puts instead of shares.",
    )


def _select_options_lab(self: tk.Tk) -> None:
    notebook = _find_notebook(self)
    if notebook is None:
        return
    for tab_id in notebook.tabs():
        if notebook.tab(tab_id, "text") == "Options What-If Lab":
            notebook.select(tab_id)
            return


def _find_notebook(root: tk.Widget) -> ttk.Notebook | None:
    if isinstance(root, ttk.Notebook):
        return root
    for child in root.winfo_children():
        found = _find_notebook(child)
        if found is not None:
            return found
    return None


def _inside_labelframe(widget: tk.Widget, title: str) -> bool:
    parent = widget.master
    while parent is not None:
        if isinstance(parent, ttk.LabelFrame):
            try:
                if str(parent.cget("text")) == title:
                    return True
            except Exception:
                pass
        parent = parent.master
    return False


def _walk_buttons(root: tk.Widget):
    for child in root.winfo_children():
        if isinstance(child, ttk.Button):
            yield child
        yield from _walk_buttons(child)
