from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.ui import options_lab_extension

_DELETE_ME = "DELETE ME"


def install_hyperliquid_perp_ticket_use_mid_fix(app_cls: type[tk.Tk] | None = None) -> None:
    """Remove placeholder DELETE ME controls from the dedicated trading tabs."""
    _patch_hyperliquid_tab_builder()
    _patch_options_layout_builder()
    if app_cls is not None:
        _patch_grid_row(app_cls)
        _patch_layout_after_build(app_cls)


def _patch_grid_row(app_cls: type[tk.Tk]) -> None:
    """Block DELETE ME controls at the shared row builder before widgets hit the UI."""
    original_grid_row = app_cls._grid_row

    def grid_row_without_delete_me(
        self: tk.Tk,
        parent: ttk.Frame,
        row: int,
        left_label: str,
        left_widget: tk.Widget,
        right_label: str | None = None,
        right_widget: tk.Widget | None = None,
    ) -> None:
        if right_label == _DELETE_ME:
            if _labelframe_title(parent) == "Hyperliquid Perp Ticket":
                right_label = "Use Mid"
                right_widget = _make_use_mid_button(self, parent)
            else:
                right_label = None
                right_widget = None
        return original_grid_row(
            self,
            parent,
            row,
            left_label,
            left_widget,
            right_label,
            right_widget,
        )

    app_cls._grid_row = grid_row_without_delete_me


def _patch_hyperliquid_tab_builder() -> None:
    original_build_hyperliquid_tab = options_lab_extension._build_hyperliquid_trading_tab

    def build_hyperliquid_tab_with_inline_use_mid(self: tk.Tk, parent: ttk.Frame) -> None:
        original_grid_row = self._grid_row

        def grid_row_with_inline_use_mid(
            container: ttk.Frame,
            row: int,
            left_label: str,
            left_widget: tk.Widget,
            right_label: str | None = None,
            right_widget: tk.Widget | None = None,
        ) -> None:
            if left_label == "Stop price" and right_label == _DELETE_ME:
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


def _patch_options_layout_builder() -> None:
    original_build_layout = options_lab_extension._build_layout_with_options_lab

    def build_layout_then_remove_delete_me(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _remove_delete_me_controls(self))

    options_lab_extension._build_layout_with_options_lab = build_layout_then_remove_delete_me


def _patch_layout_after_build(app_cls: type[tk.Tk]) -> None:
    original_build_layout = app_cls._build_layout

    def build_layout_then_remove_delete_me(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _remove_delete_me_controls(self))

    app_cls._build_layout = build_layout_then_remove_delete_me


def _remove_delete_me_controls(self: tk.Tk) -> None:
    for widget in list(_walk_widgets(self)):
        if not _is_delete_me_label(widget):
            continue

        parent = widget.master
        if parent is None:
            continue

        grid_info = widget.grid_info()
        row = int(grid_info.get("row", 0))
        title = _labelframe_title(parent)

        widget.destroy()
        for existing in parent.grid_slaves(row=row, column=3):
            existing.destroy()

        if title == "Hyperliquid Perp Ticket":
            ttk.Label(parent, text="Use Mid", style="Subtle.TLabel").grid(
                row=row,
                column=2,
                sticky="w",
                padx=(0, 8),
                pady=grid_info.get("pady", 0),
            )
            _make_use_mid_button(self, parent).grid(
                row=row,
                column=3,
                sticky="ew",
                pady=grid_info.get("pady", 0),
            )


def _is_delete_me_label(widget: tk.Widget) -> bool:
    try:
        return widget.winfo_class() in {"TLabel", "Label"} and widget.cget("text") == _DELETE_ME
    except Exception:
        return False


def _labelframe_title(widget: tk.Widget) -> str:
    try:
        return str(widget.cget("text"))
    except Exception:
        return ""


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
