from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.ui.schwab_option_chain_extension import _OPTION_CHAIN_COLUMNS, _use_selected_schwab_option


def install_schwab_option_chain_visible_fix(app_cls: Type[tk.Tk]) -> None:
    """Place the Schwab option-chain table in the visible right-hand output pane."""

    original_build_layout = app_cls._build_layout

    def build_layout_with_visible_option_chain(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _install_visible_option_chain(self))

    app_cls._build_layout = build_layout_with_visible_option_chain  # type: ignore[method-assign]


def _install_visible_option_chain(self: tk.Tk) -> None:
    if getattr(self, "_schwab_option_chain_visible_built", False):
        return

    output_text = getattr(self, "schwab_trading_preview_text", None)
    if output_text is None:
        return

    output_parent = output_text.master
    if output_parent is None:
        return

    chain = ttk.LabelFrame(output_parent, text="Schwab Option Chain", style="Card.TLabelframe")
    try:
        chain.pack(fill=tk.X, padx=0, pady=(0, 10), before=output_text)
    except Exception:
        chain.pack(fill=tk.X, padx=0, pady=(0, 10))
    chain.columnconfigure(0, weight=1)
    chain.rowconfigure(1, weight=1)

    controls = ttk.Frame(chain, style="Panel.TFrame")
    controls.grid(row=0, column=0, sticky="ew", pady=(0, 6))
    controls.columnconfigure((0, 1, 2), weight=1, uniform="visible_option_chain_buttons")
    ttk.Button(
        controls,
        text="Load Option Chain",
        command=self.load_schwab_option_chain,
        style="Accent.TButton",
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(
        controls,
        text="Use Call Ask",
        command=self.use_selected_schwab_option_call_ask,
    ).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Button(
        controls,
        text="Use Put Ask",
        command=self.use_selected_schwab_option_put_ask,
    ).grid(row=0, column=2, sticky="ew")

    table_frame = ttk.Frame(chain, style="Panel.TFrame")
    table_frame.grid(row=1, column=0, sticky="nsew")
    table_frame.columnconfigure(0, weight=1)
    table_frame.rowconfigure(0, weight=1)

    tree = ttk.Treeview(table_frame, columns=_OPTION_CHAIN_COLUMNS, show="headings", height=9)
    headings = {
        "expiration": "Expiration",
        "strike": "Strike",
        "call_bid": "Call Bid",
        "call_ask": "Call Ask",
        "put_bid": "Put Bid",
        "put_ask": "Put Ask",
        "call_volume": "Call Vol",
        "put_volume": "Put Vol",
    }
    widths = {
        "expiration": 125,
        "strike": 75,
        "call_bid": 80,
        "call_ask": 80,
        "put_bid": 80,
        "put_ask": 80,
        "call_volume": 75,
        "put_volume": 75,
    }
    for column in _OPTION_CHAIN_COLUMNS:
        tree.heading(column, text=headings[column])
        tree.column(column, width=widths[column], anchor=tk.E if column != "expiration" else tk.W)

    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=tree.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=scrollbar.set)
    tree.bind(
        "<Double-1>",
        lambda _event: _use_selected_schwab_option(
            self,
            self.options_type_var.get().strip().lower() or "call",
        ),
    )

    hint = ttk.Label(
        chain,
        text="Load a chain, select a row, then Use Call Ask or Use Put Ask. This fills the options fields only.",
        style="Subtle.TLabel",
    )
    hint.grid(row=2, column=0, sticky="w", pady=(6, 0))

    self.schwab_option_chain_tree = tree
    self._schwab_option_chain_visible_built = True
