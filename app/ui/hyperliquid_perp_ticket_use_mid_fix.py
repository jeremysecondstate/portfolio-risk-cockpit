from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.ui import options_lab_extension


def install_hyperliquid_perp_ticket_use_mid_fix() -> None:
    """Replace the unused Hyperliquid Type CONFIRM field with a Use Mid button."""
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
                right_widget = ttk.Button(
                    container,
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
