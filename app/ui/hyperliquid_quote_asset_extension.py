from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type


_installed = False
_original_build_layout = None


def install_hyperliquid_quote_asset_extension(app_cls: Type[tk.Tk]) -> None:
    """Replace the obsolete USDH/USDC quote selector with chain health access."""

    global _installed, _original_build_layout
    if _installed:
        return

    _original_build_layout = app_cls._build_layout
    app_cls._build_layout = _build_layout_with_hyperliquid_chain_health_shortcut  # type: ignore[method-assign]
    _installed = True


def _build_layout_with_hyperliquid_chain_health_shortcut(self: tk.Tk) -> None:
    if _original_build_layout is None:
        raise RuntimeError("Original layout builder was not captured.")
    _original_build_layout(self)
    self.after_idle(lambda: _install_chain_health_shortcut(self))


def _install_chain_health_shortcut(self: tk.Tk) -> None:
    if getattr(self, "_hyperliquid_chain_health_shortcut_built", False):
        return

    spot_ticket = _find_labelframe(self, "Hyperliquid Spot Ticket")
    if spot_ticket is None:
        return

    row = _next_grid_row(spot_ticket)
    ttk.Label(spot_ticket, text="Chain Health", style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
    ttk.Button(
        spot_ticket,
        text="Chain Vibe Check",
        command=lambda app=self: _run_chain_health(app),
        style="Accent.TButton",
    ).grid(row=row, column=1, columnspan=3, sticky="ew", pady=6)
    self._hyperliquid_chain_health_shortcut_built = True


def _run_chain_health(app: tk.Tk) -> None:
    command = getattr(app, "refresh_hyperliquid_chain_health_workspace", None)
    if not callable(command):
        command = getattr(app, "refresh_hyperliquid_chain_health", None)
    if callable(command):
        command()


def _find_labelframe(root: tk.Widget, title: str) -> ttk.LabelFrame | None:
    try:
        if str(root.winfo_class()) == "TLabelframe" and str(root.cget("text")) == title:
            return root  # type: ignore[return-value]
    except Exception:
        pass
    for child in root.winfo_children():
        found = _find_labelframe(child, title)
        if found is not None:
            return found
    return None


def _next_grid_row(parent: tk.Widget) -> int:
    rows: list[int] = []
    for child in parent.winfo_children():
        try:
            rows.append(int(child.grid_info().get("row", 0)))
        except Exception:
            continue
    return (max(rows) + 1) if rows else 0
