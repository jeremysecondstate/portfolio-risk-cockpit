from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.ui.options_lab import build_options_lab_tab
from app.ui.polished_theme import _make_paned


def install_options_lab_extension(app_cls: Type[tk.Tk]) -> None:
    """Add the Options What-If Lab as a separate cockpit tab.

    The existing account, paper-order, Schwab preview, and live-safety controls stay
    in the primary cockpit tab. The new tab is intentionally sandbox-only: no order
    JSON, no broker API calls, and no trade recommendation workflow.
    """

    app_cls._build_layout = _build_layout_with_options_lab  # type: ignore[method-assign]


def _build_layout_with_options_lab(self: tk.Tk) -> None:
    root = ttk.Frame(self, style="Canvas.TFrame", padding=18)
    root.pack(fill=tk.BOTH, expand=True)

    self._build_header(root)

    tabs = ttk.Notebook(root)
    tabs.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

    cockpit_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=0)
    options_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    tabs.add(cockpit_tab, text="Cockpit")
    tabs.add(options_tab, text="Options What-If Lab")

    body = _make_paned(cockpit_tab, tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True)

    left = ttk.Frame(body, style="Canvas.TFrame")
    right = ttk.Frame(body, style="Canvas.TFrame")
    body.add(left, minsize=560, stretch="always")
    body.add(right, minsize=520, stretch="always")
    self.after_idle(lambda: body.sash_place(0, max(600, int(self.winfo_width() * 0.60)), 0))

    self._build_portfolio_panel(left)
    self._build_order_panel(right)
    build_options_lab_tab(self, options_tab)
