from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.ui import trading_workspace_extension


def install_schwab_workspace_sync_extension(app_cls: Type[tk.Tk]) -> None:
    """Make the Schwab workspace actions mirror the Hyperliquid workspace pattern."""

    previous_build_layout = app_cls._build_layout

    def build_layout_with_schwab_sync_polish(self: tk.Tk) -> None:
        previous_build_layout(self)
        _schedule_schwab_workspace_polish(self)

    app_cls._build_layout = build_layout_with_schwab_sync_polish  # type: ignore[method-assign]


def _schedule_schwab_workspace_polish(self: tk.Tk) -> None:
    """Run a few times because multiple UI extensions patch and build the tab lazily."""

    for delay_ms in (0, 100, 500, 1200):
        self.after(delay_ms, lambda app=self: _polish_schwab_workspace(app))


def _polish_schwab_workspace(self: tk.Tk) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is None:
        return

    _hide_top_level_options_tab(self)

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
                text="Options Strategy",
                command=lambda: getattr(self, "show_technical_analysis")(),
            )

    _set_schwab_workspace_intro(self)


def _embed_trading_workspace_under_schwab(self: tk.Tk) -> None:
    """Keep Schwab stock and option planning in one top-level Schwab tab.

    The base options extension still creates an Options What-If Lab tab for backward
    compatibility. This polish pass hides that separate top-level tab and builds the
    same lab at the bottom of the Schwab workspace so Schwab owns stocks, ETFs, and
    options while Hyperliquid stays perp-only.
    """

    if getattr(self, "_schwab_trading_workspace_embedded", False):
        _hide_top_level_options_tab(self)
        return

    notebook = _find_notebook(self)
    if notebook is None:
        return

    schwab_tab = None
    for tab_id in notebook.tabs():
        if notebook.tab(tab_id, "text") == "Schwab Trading":
            schwab_tab = notebook.nametowidget(tab_id)
            break
    if schwab_tab is None:
        return

    embedded = ttk.LabelFrame(
        schwab_tab,
        text="Schwab Options What-If Lab",
        style="Card.TLabelframe",
    )
    embedded.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
    embedded.columnconfigure(0, weight=1)
    embedded.columnconfigure(1, weight=1)
    embedded.rowconfigure(1, weight=1)

    trading_workspace_extension.build_trading_workspace_tab(self, embedded)
    trading_workspace_extension._build_trading_workspace_market_loader(self, embedded)

    schwab_tab.rowconfigure(2, weight=1)
    self._schwab_trading_workspace_embedded = True
    self._schwab_trading_workspace_frame = embedded
    _hide_top_level_options_tab(self)


def _hide_top_level_options_tab(self: tk.Tk) -> None:
    notebook = _find_notebook(self)
    if notebook is None:
        return
    for tab_id in notebook.tabs():
        if notebook.tab(tab_id, "text") == "Options What-If Lab":
            notebook.hide(tab_id)
            return


def _run_schwab_workspace_action(self: tk.Tk, *command_names: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is None:
        command = trading_workspace_extension._first_available_command(self, *command_names)
        command()
        return

    trading_workspace_extension._run_workspace_action(
        self,
        venue="Schwab",
        preview_widget=output,
        command=trading_workspace_extension._first_available_command(self, *command_names),
    )


def _set_schwab_workspace_intro(self: tk.Tk) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is None:
        return
    current = output.get("1.0", tk.END).strip()
    if "SCHWAB TRADING WORKSPACE" not in current:
        return

    trading_workspace_extension._set_workspace_text(
        output,
        "SCHWAB TRADING WORKSPACE\n"
        "========================\n\n"
        "Use this tab for stocks, ETFs, Schwab previews, order history, guarded live Schwab actions, and options what-if planning.\n\n"
        "Sync Schwab refreshes account balances and positions through the Trader API account snapshot flow.\n\n"
        "Options Strategy lives in the Schwab Research + Risk Workspace so option candidates sit beside technicals, macro, earnings, and risk scenarios.",
    )


def _select_trading_workspace(self: tk.Tk) -> None:
    embedded = getattr(self, "_schwab_trading_workspace_frame", None)
    if embedded is not None:
        try:
            embedded.focus_set()
            embedded.tkraise()
            return
        except Exception:
            pass

    notebook = _find_notebook(self)
    if notebook is None:
        return
    for tab_id in notebook.tabs():
        if notebook.tab(tab_id, "text") == "Options What-If Lab":
            notebook.select(tab_id)
            return


def _find_notebook(root: tk.Widget) -> ttk.Notebook | None:
    if _widget_class(root) == "TNotebook":
        return root  # type: ignore[return-value]
    for child in root.winfo_children():
        found = _find_notebook(child)
        if found is not None:
            return found
    return None


def _inside_labelframe(widget: tk.Widget, title: str) -> bool:
    parent = widget.master
    while parent is not None:
        if _widget_class(parent) == "TLabelframe":
            try:
                if str(parent.cget("text")) == title:
                    return True
            except Exception:
                pass
        parent = parent.master
    return False


def _walk_buttons(root: tk.Widget):
    for child in root.winfo_children():
        if _widget_class(child) == "TButton":
            yield child
        yield from _walk_buttons(child)


def _widget_class(widget: tk.Widget) -> str:
    try:
        return str(widget.winfo_class())
    except Exception:
        return ""
