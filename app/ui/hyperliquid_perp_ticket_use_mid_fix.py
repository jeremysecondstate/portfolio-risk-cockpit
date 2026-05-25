from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.ui import options_lab_extension


def install_hyperliquid_perp_ticket_use_mid_fix(app_cls: type[tk.Tk] | None = None) -> None:
    """Replace the unused Hyperliquid Type CONFIRM field with a Use Mid button."""
    _patch_hyperliquid_tab_builder()
    if app_cls is not None:
        _patch_layout_after_build(app_cls)


def _patch_hyperliquid_tab_builder() -> None:
    original_build_hyperliquid_tab = options_lab_extension._build_hyperliquid_trading_tab

    def build_hyperliquid_tab_with_inline_use_mid(self: tk.Tk, parent: ttk.Frame) -> None:
        original_grid_row = self._grid_row

        def grid_row_with_inline_use_mid(
            container: ttk.Frame,
            row: int,
            left_label: str,
            left_widget: tk.Widget,
            right_label: str,
            right_widget: tk.Widget,
        ) -> None:
            if left_label == "Stop price" and right_label == "Type CONFIRM":
                right_label = "Use Mid"
                right_widget = _make_use_mid_button(self, container)
            return original_grid_row(
                container,
                row,
                left_label,
                left_widget,
                right_label,
                right_widget,
            )

        self._grid_row = grid_row_with_inline_use_mid
        try:
            original_build_hyperliquid_tab(self, parent)
        finally:
            self._grid_row = original_grid_row

    options_lab_extension._build_hyperliquid_trading_tab = build_hyperliquid_tab_with_inline_use_mid


def _patch_layout_after_build(app_cls: type[tk.Tk]) -> None:
    original_build_layout = app_cls._build_layout

    def build_layout_then_replace_type_confirm(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _replace_existing_type_confirm_field(self))

    app_cls._build_layout = build_layout_then_replace_type_confirm


def _replace_existing_type_confirm_field(self: tk.Tk) -> None:
    for widget in _walk_widgets(self):
        if not _is_hyperliquid_type_confirm_label(widget):
            continue

        parent = widget.master
        grid_info = widget.grid_info()
        row = int(grid_info.get("row", 0))
        widget.configure(text="Use Mid")

        for existing in parent.grid_slaves(row=row, column=3):
            existing.destroy()

        _make_use_mid_button(self, parent).grid(
            row=row,
            column=3,
            sticky="ew",
            pady=grid_info.get("pady", 0),
        )


def _is_hyperliquid_type_confirm_label(widget: tk.Widget) -> bool:
    try:
        if widget.winfo_class() not in {"TLabel", "Label"}:
            return False
        if widget.cget("text") != "Type CONFIRM":
            return False
        parent = widget.master
        return parent is not None and parent.cget("text") == "Hyperliquid Perp Ticket"
    except Exception:
        return False


def _make_use_mid_button(self: tk.Tk, parent: tk.Widget) -> ttk.Button:
    return ttk.Button(
        parent,
        text="Use Mid",
        command=lambda: options_lab_extension._run_workspace_action(
            self,
            venue="Hyperliquid",
            preview_widget=self.hyperliquid_trading_preview_text,
            command=options_lab_extension._first_available_command(
                self,
                "use_hyperliquid_mid_market",
            ),
        ),
        style="Accent.TButton",
    )


def _walk_widgets(root: tk.Widget):
    for child in root.winfo_children():
        yield child
        yield from _walk_widgets(child)
