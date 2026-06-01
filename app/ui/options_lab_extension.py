from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Type

from app.analytics.technical_analysis import (
    analyze_candles,
    candles_from_price_history,
    simple_moving_average,
)
from app.analytics.trade_setup import calculate_support_resistance
from app.brokers.hyperliquid.client import HyperliquidInfoClient
from app.brokers.hyperliquid.trading import normalize_hyperliquid_coin
from app.core.order_models import SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, OrderSide, OrderType, TimeInForce
from app.ui.options_lab import build_options_lab_tab, run_options_what_if
from app.ui.polished_theme import _make_paned


def install_options_lab_extension(app_cls: Type[tk.Tk]) -> None:
    """Add the Options What-If Lab and Schwab/Hyperliquid cockpit layout."""

    app_cls._build_layout = _build_layout_with_options_lab  # type: ignore[method-assign]
    app_cls.load_options_lab_technical_context = _load_options_lab_technical_context  # type: ignore[attr-defined]
    app_cls.use_current_cockpit_source_portfolio = _use_current_cockpit_source_portfolio  # type: ignore[attr-defined]
    app_cls.use_hyperliquid_mid_market = _use_hyperliquid_mid_market  # type: ignore[attr-defined]
    app_cls.run_hyperliquid_perp_what_if = _run_hyperliquid_perp_what_if  # type: ignore[attr-defined]
    app_cls.update_workspace_holdings_tables = _update_workspace_holdings_tables  # type: ignore[attr-defined]
    app_cls.set_hyperliquid_sync_status = _set_hyperliquid_sync_status  # type: ignore[attr-defined]


def _build_layout_with_options_lab(self: tk.Tk) -> None:
    root = ttk.Frame(self, style="Canvas.TFrame", padding=18)
    root.pack(fill=tk.BOTH, expand=True)

    self._build_header(root)

    tabs = ttk.Notebook(root)
    tabs.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

    cockpit_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=0)
    schwab_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    hyperliquid_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    options_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    tabs.add(cockpit_tab, text="Cockpit")
    tabs.add(schwab_tab, text="Schwab Trading")
    tabs.add(hyperliquid_tab, text="Hyperliquid Trading")
    tabs.add(options_tab, text="Options What-If Lab")

    self.active_portfolio_source_var = tk.StringVar(value="Active portfolio: current cockpit source")
    self.cockpit_source_portfolio = None
    self.cockpit_source_message = "Current cockpit portfolio"

    _build_account_sources_panel(self, cockpit_tab)

    body = _make_paned(cockpit_tab, tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

    left = ttk.Frame(body, style="Canvas.TFrame")
    right = ttk.Frame(body, style="Canvas.TFrame")
    body.add(left, minsize=560, stretch="always")
    body.add(right, minsize=520, stretch="always")
    self.after_idle(lambda: body.sash_place(0, max(600, int(self.winfo_width() * 0.60)), 0))

    self._build_portfolio_panel(left)
    self._build_order_panel(right)
    _ensure_execution_workspace_vars(self)
    self.after_idle(lambda: _capture_current_source_portfolio(self))

    _build_schwab_trading_tab(self, schwab_tab, tabs, options_tab)
    _build_hyperliquid_trading_tab(self, hyperliquid_tab)

    build_options_lab_tab(self, options_tab)
    _build_options_lab_market_loader(self, options_tab)


def _ensure_execution_workspace_vars(self: tk.Tk) -> None:
    """Keep the dedicated venue tabs safe even if extensions load in a different order."""
    if not hasattr(self, "trade_venue_var"):
        self.trade_venue_var = tk.StringVar(value="Schwab")
    if not hasattr(self, "hyperliquid_coin_var"):
        self.hyperliquid_coin_var = tk.StringVar(value="")
    if not hasattr(self, "hyperliquid_tif_var"):
        self.hyperliquid_tif_var = tk.StringVar(value="Gtc")
    if not hasattr(self, "hyperliquid_reduce_only_var"):
        self.hyperliquid_reduce_only_var = tk.BooleanVar(value=False)
    if not hasattr(self, "hyperliquid_status_var"):
        self.hyperliquid_status_var = tk.StringVar(value="Hyperliquid: preview only")
    if not hasattr(self, "hyperliquid_target_price_var"):
        self.hyperliquid_target_price_var = tk.StringVar(value="")
    if not hasattr(self, "hyperliquid_bad_price_var"):
        self.hyperliquid_bad_price_var = tk.StringVar(value="")
    if not hasattr(self, "hyperliquid_leverage_var"):
        self.hyperliquid_leverage_var = tk.StringVar(value="1")
    if not hasattr(self, "hyperliquid_margin_mode_var"):
        self.hyperliquid_margin_mode_var = tk.StringVar(value="Cross")
    if not hasattr(self, "hyperliquid_fee_rate_var"):
        self.hyperliquid_fee_rate_var = tk.StringVar(value="0.045")
    if not hasattr(self, "hyperliquid_workspace_active_ticket_var"):
        self.hyperliquid_workspace_active_ticket_var = tk.StringVar(value="spot")
    if not hasattr(self, "hyperliquid_sync_status_var"):
        self.hyperliquid_sync_status_var = tk.StringVar(value="\u2715 Not synced")
    if not hasattr(self, "hyperliquid_sync_status_state"):
        self.hyperliquid_sync_status_state = "failure"

    _ensure_string_var(self, "hyperliquid_spot_symbol_var", getattr(self, "symbol_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_spot_coin_var", getattr(self, "hyperliquid_coin_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_spot_side_var", getattr(self, "side_var", tk.StringVar(value=OrderSide.BUY.value)).get())
    _ensure_string_var(self, "hyperliquid_spot_order_type_var", getattr(self, "order_type_var", tk.StringVar(value=OrderType.LIMIT.value)).get())
    _ensure_string_var(self, "hyperliquid_spot_quantity_var", getattr(self, "quantity_var", tk.StringVar(value="1")).get())
    _ensure_string_var(self, "hyperliquid_spot_limit_price_var", getattr(self, "limit_price_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_spot_stop_price_var", getattr(self, "stop_price_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_spot_tif_var", getattr(self, "hyperliquid_tif_var", tk.StringVar(value="Gtc")).get())
    _ensure_string_var(self, "hyperliquid_spot_cancel_order_id_var", getattr(self, "cancel_order_id_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_spot_size_unit_var", getattr(self, "hyperliquid_size_unit_var", tk.StringVar(value="")).get())
    _ensure_double_var(self, "hyperliquid_spot_size_percent_var", getattr(self, "hyperliquid_size_percent_var", tk.DoubleVar(value=0.0)).get())
    _ensure_string_var(self, "hyperliquid_spot_size_status_var", "Sync Hyperliquid, then choose a size %")

    _ensure_string_var(self, "hyperliquid_perp_coin_var", getattr(self, "hyperliquid_coin_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_perp_symbol_var", getattr(self, "symbol_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_perp_side_var", getattr(self, "side_var", tk.StringVar(value=OrderSide.BUY.value)).get())
    _ensure_string_var(self, "hyperliquid_perp_order_type_var", getattr(self, "order_type_var", tk.StringVar(value=OrderType.LIMIT.value)).get())
    _ensure_string_var(self, "hyperliquid_perp_quantity_var", getattr(self, "quantity_var", tk.StringVar(value="1")).get())
    _ensure_string_var(self, "hyperliquid_perp_limit_price_var", getattr(self, "limit_price_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_perp_target_price_var", getattr(self, "hyperliquid_target_price_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_perp_stop_price_var", getattr(self, "stop_price_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_perp_tif_var", getattr(self, "hyperliquid_tif_var", tk.StringVar(value="Gtc")).get())
    _ensure_string_var(self, "hyperliquid_perp_cancel_order_id_var", getattr(self, "cancel_order_id_var", tk.StringVar(value="")).get())
    _ensure_string_var(self, "hyperliquid_perp_leverage_var", getattr(self, "hyperliquid_leverage_var", tk.StringVar(value="1")).get())
    _ensure_string_var(self, "hyperliquid_perp_margin_mode_var", getattr(self, "hyperliquid_margin_mode_var", tk.StringVar(value="Cross")).get())
    _ensure_string_var(self, "hyperliquid_perp_fee_rate_var", getattr(self, "hyperliquid_fee_rate_var", tk.StringVar(value="0.045")).get())
    _ensure_bool_var(self, "hyperliquid_perp_reduce_only_var", getattr(self, "hyperliquid_reduce_only_var", tk.BooleanVar(value=False)).get())
    _ensure_bool_var(self, "hyperliquid_perp_attach_tpsl_var", getattr(self, "hyperliquid_attach_tpsl_var", tk.BooleanVar(value=False)).get())


def _ensure_string_var(self: tk.Tk, name: str, default: str) -> None:
    if not hasattr(self, name):
        setattr(self, name, tk.StringVar(value=default))


def _ensure_double_var(self: tk.Tk, name: str, default: float) -> None:
    if not hasattr(self, name):
        setattr(self, name, tk.DoubleVar(value=float(default or 0.0)))


def _ensure_bool_var(self: tk.Tk, name: str, default: bool) -> None:
    if not hasattr(self, name):
        setattr(self, name, tk.BooleanVar(value=bool(default)))


def _workspace_text(parent: ttk.Frame) -> tk.Text:
    text = tk.Text(
        parent,
        height=18,
        wrap=tk.WORD,
        font=("Segoe UI", 10),
        padx=18,
        pady=16,
        relief=tk.FLAT,
        borderwidth=0,
        background="#f8fafc",
        foreground="#0f172a",
        insertbackground="#0f172a",
        selectbackground="#bfdbfe",
        spacing1=3,
        spacing2=1,
        spacing3=6,
    )
    _configure_workspace_report_tags(text)
    text._apply_report_style = lambda content, widget=text: _apply_workspace_report_tags(widget, content)  # type: ignore[attr-defined]
    text.pack(fill=tk.BOTH, expand=True)
    return text


def _configure_workspace_report_tags(text: tk.Text) -> None:
    text.tag_configure("report_title", font=("Segoe UI", 13, "bold"), foreground="#0f172a", spacing1=2, spacing3=8)
    text.tag_configure("section_title", font=("Segoe UI", 10, "bold"), foreground="#1d4ed8", spacing1=6, spacing3=3)
    text.tag_configure("body", font=("Segoe UI", 10), foreground="#0f172a")
    text.tag_configure("bullet", lmargin1=18, lmargin2=34, foreground="#1f2937")
    text.tag_configure("muted", foreground="#64748b")
    text.tag_configure("separator", foreground="#cbd5e1", font=("Segoe UI", 7))
    text.tag_configure("mono", font=("Cascadia Mono", 9), foreground="#1f2937")


def _apply_workspace_report_tags(text: tk.Text, content: str) -> None:
    try:
        _configure_workspace_report_tags(text)
        for tag in ("report_title", "section_title", "body", "bullet", "muted", "separator", "mono"):
            text.tag_remove(tag, "1.0", tk.END)

        lines = content.splitlines()
        table_mode = False
        for index, line in enumerate(lines, start=1):
            start = f"{index}.0"
            end = f"{index}.end"
            stripped = line.strip()
            if not stripped:
                continue
            text.tag_add("body", start, end)
            if re.fullmatch(r"[=\-]{5,}", stripped):
                text.tag_add("separator", start, end)
                table_mode = False
                continue
            if index == 1 and stripped:
                text.tag_add("report_title", start, end)
                continue
            if _looks_like_table_line(stripped):
                text.tag_add("mono", start, end)
                table_mode = True
                continue
            if stripped.startswith(("- ", "* ")):
                text.tag_add("bullet", start, end)
                continue
            if table_mode and re.search(r"\s{2,}", line):
                text.tag_add("mono", start, end)
                continue
            if stripped.endswith(":") or (_looks_like_section_heading(stripped) and len(stripped) <= 64):
                text.tag_add("section_title", start, end)
                table_mode = False
                continue
            if ":" in stripped and len(stripped.split(":", 1)[0]) <= 22:
                text.tag_add("muted", start, f"{index}.{len(line.split(':', 1)[0]) + 1}")
    except tk.TclError:
        return


def _looks_like_section_heading(value: str) -> bool:
    if not value or value.startswith(("-", "{", "[")):
        return False
    letters = [char for char in value if char.isalpha()]
    return bool(letters) and sum(char.isupper() for char in letters) >= max(4, int(len(letters) * 0.65))


def _looks_like_table_line(value: str) -> bool:
    if value.startswith(("{", "[", "'")):
        return True
    return len(value) > 45 and bool(re.search(r"\s{3,}", value))


def _workspace_holdings_table(parent: ttk.Frame) -> ttk.Treeview:
    columns = ("symbol", "type", "qty", "last", "value", "pnl")
    table = ttk.Treeview(parent, columns=columns, show="headings", height=6, selectmode="browse")
    headings = {
        "symbol": ("Symbol", 90, tk.W),
        "type": ("Type", 80, tk.W),
        "qty": ("Qty", 90, tk.E),
        "last": ("Last", 90, tk.E),
        "value": ("Value", 100, tk.E),
        "pnl": ("P&L", 100, tk.E),
    }
    for column, (label, width, anchor) in headings.items():
        table.heading(column, text=label)
        table.column(column, width=width, anchor=anchor, stretch=True)
    table.pack(fill=tk.BOTH, expand=True)
    table.tag_configure("positive", foreground="#047857")
    table.tag_configure("negative", foreground="#b91c1c")
    table.tag_configure("cash", foreground="#334155")
    return table


def _workspace_open_orders_table(parent: ttk.Frame) -> ttk.Treeview:
    columns = ("time", "type", "coin", "direction", "size", "price", "edit", "ro", "trigger", "tpsl", "oid")
    table = ttk.Treeview(parent, columns=columns, show="headings", height=7, selectmode="browse")
    headings = {
        "time": ("Time", 100, tk.W),
        "type": ("Type", 72, tk.W),
        "coin": ("Coin", 64, tk.W),
        "direction": ("Dir", 92, tk.W),
        "size": ("Size", 78, tk.E),
        "price": ("Price", 78, tk.E),
        "edit": ("Edit", 56, tk.CENTER),
        "ro": ("RO", 38, tk.CENTER),
        "trigger": ("Trigger", 110, tk.W),
        "tpsl": ("TP/SL", 48, tk.CENTER),
        "oid": ("OID", 78, tk.E),
    }
    for column, (label, width, anchor) in headings.items():
        table.heading(column, text=label)
        table.column(column, width=width, anchor=anchor, stretch=column in {"trigger", "direction"})
    table.pack(fill=tk.BOTH, expand=True)
    table.tag_configure("buy", foreground="#047857")
    table.tag_configure("sell", foreground="#b91c1c")
    table.tag_configure("trigger", foreground="#7c3aed")
    return table


def _set_workspace_text(widget: tk.Text, content: str) -> None:
    widget.configure(state=tk.NORMAL)
    widget.delete("1.0", tk.END)
    widget.insert(tk.END, content)
    styler = getattr(widget, "_apply_report_style", None)
    if callable(styler):
        styler(content)
    widget.configure(state=tk.DISABLED)


def _bind_workspace_holdings_click(self: tk.Tk, table: ttk.Treeview, venue: str) -> None:
    table.bind("<ButtonRelease-1>", lambda event, app=self, source=table, selected_venue=venue: _load_workspace_ticket_from_holding(app, source, event, selected_venue), add="+")
    if venue == "Hyperliquid":
        table.bind("<Double-1>", lambda event, app=self, source=table: _open_editor_for_clicked_hyperliquid_perp_position(app, source, event), add="+")
    table.bind("<Motion>", lambda event, source=table: source.configure(cursor="hand2" if source.identify_row(event.y) else ""), add="+")
    table.bind("<Leave>", lambda _event, source=table: source.configure(cursor=""), add="+")


def _bind_workspace_open_orders_click(self: tk.Tk, table: ttk.Treeview) -> None:
    table.bind("<ButtonRelease-1>", lambda event, app=self, source=table: _handle_workspace_open_order_click(app, source, event), add="+")
    table.bind(
        "<Motion>",
        lambda event, source=table: source.configure(
            cursor="hand2" if source.identify_row(event.y) and _workspace_open_order_column_name(source, event) == "edit" else ""
        ),
        add="+",
    )
    table.bind("<Leave>", lambda _event, source=table: source.configure(cursor=""), add="+")


def _workspace_open_order_column_name(table: ttk.Treeview, event: tk.Event) -> str:
    raw_column = table.identify_column(event.x)
    if not raw_column.startswith("#"):
        return ""
    try:
        column_index = int(raw_column[1:]) - 1
    except ValueError:
        return ""
    columns = tuple(table["columns"])
    if column_index < 0 or column_index >= len(columns):
        return ""
    return str(columns[column_index])


def _handle_workspace_open_order_click(self: tk.Tk, table: ttk.Treeview, event: tk.Event) -> None:
    order_id = _load_workspace_ticket_from_open_order(self, table, event)
    if not order_id or _workspace_open_order_column_name(table, event) != "edit":
        return
    opener = getattr(self, "show_hyperliquid_order_edit_dialog", None)
    if callable(opener):
        opener()


def _load_workspace_ticket_from_open_order(self: tk.Tk, table: ttk.Treeview, event: tk.Event) -> str | None:
    row_id = table.identify_row(event.y)
    if not row_id:
        return None
    table.selection_set(row_id)
    table.focus(row_id)
    raw_values = table.item(row_id, "values")
    columns = tuple(table["columns"])
    values = {str(column): str(raw_values[index]) for index, column in enumerate(columns) if index < len(raw_values)}
    order_id = values.get("oid", "").strip()
    coin = _workspace_ticket_symbol(values.get("coin", ""))
    direction = values.get("direction", "").strip().lower()
    if order_id:
        if hasattr(self, "cancel_order_id_var"):
            self.cancel_order_id_var.set(order_id)
        if hasattr(self, "hyperliquid_spot_cancel_order_id_var"):
            self.hyperliquid_spot_cancel_order_id_var.set(order_id)
        if hasattr(self, "hyperliquid_perp_cancel_order_id_var"):
            self.hyperliquid_perp_cancel_order_id_var.set(order_id)
    if not coin:
        return
    self.trade_venue_var.set("Hyperliquid")
    if hasattr(self, "hyperliquid_workspace_active_ticket_var"):
        self.hyperliquid_workspace_active_ticket_var.set("perp" if "close" in direction else "spot")
    for var_name in ("symbol_var", "hyperliquid_coin_var", "hyperliquid_spot_symbol_var", "hyperliquid_spot_coin_var", "hyperliquid_perp_symbol_var", "hyperliquid_perp_coin_var"):
        var = getattr(self, var_name, None)
        if var is not None:
            var.set(coin)
    return order_id or None


def _load_workspace_ticket_from_holding(self: tk.Tk, table: ttk.Treeview, event: tk.Event, venue: str) -> None:
    row_id = table.identify_row(event.y)
    if not row_id:
        return

    raw_values = table.item(row_id, "values")
    columns = tuple(table["columns"])
    values = {str(column): str(raw_values[index]) for index, column in enumerate(columns) if index < len(raw_values)}
    symbol = values.get("symbol", "").strip()
    asset_type = values.get("type", "").strip()
    if not symbol or asset_type.lower() == "cash":
        return

    ticket_symbol = _workspace_ticket_symbol(symbol)
    if not ticket_symbol:
        return

    if venue == "Hyperliquid":
        self.trade_venue_var.set("Hyperliquid")
        preview = getattr(self, "hyperliquid_trading_preview_text", None)
        if preview is not None:
            self.preview_text = preview
        self.symbol_var.set(ticket_symbol)
        self.hyperliquid_coin_var.set(ticket_symbol)
        is_perp = asset_type.lower().startswith("perp") or "-PERP" in symbol.upper()
        if is_perp:
            if hasattr(self, "hyperliquid_workspace_active_ticket_var"):
                self.hyperliquid_workspace_active_ticket_var.set("perp")
            if hasattr(self, "hyperliquid_perp_coin_var"):
                self.hyperliquid_perp_coin_var.set(ticket_symbol)
            if hasattr(self, "hyperliquid_perp_symbol_var"):
                self.hyperliquid_perp_symbol_var.set(ticket_symbol)
            selector = getattr(self, "use_hyperliquid_perp_position", None)
            if callable(selector):
                try:
                    selector(ticket_symbol)
                except Exception:
                    pass
        else:
            if hasattr(self, "hyperliquid_workspace_active_ticket_var"):
                self.hyperliquid_workspace_active_ticket_var.set("spot")
            if hasattr(self, "hyperliquid_spot_symbol_var"):
                self.hyperliquid_spot_symbol_var.set(ticket_symbol)
            if hasattr(self, "hyperliquid_spot_coin_var"):
                self.hyperliquid_spot_coin_var.set(ticket_symbol)
        return

    self.trade_venue_var.set("Schwab")
    self.symbol_var.set(ticket_symbol)
    if hasattr(self, "hyperliquid_coin_var"):
        self.hyperliquid_coin_var.set("")
    if hasattr(self, "options_symbol_var"):
        self.options_symbol_var.set(ticket_symbol)


def _selected_hyperliquid_perp_coin_from_workspace(self: tk.Tk) -> str:
    table = getattr(self, "hyperliquid_workspace_holdings_table", None)
    if table is not None:
        selection = table.selection()
        if selection:
            raw_values = table.item(selection[0], "values")
            columns = tuple(table["columns"])
            values = {str(column): str(raw_values[index]) for index, column in enumerate(columns) if index < len(raw_values)}
            symbol = values.get("symbol", "").strip()
            asset_type = values.get("type", "").strip().lower()
            if asset_type.startswith("perp") or "-PERP" in symbol.upper():
                return _workspace_ticket_symbol(symbol)
            raise ValueError("Select a Hyperliquid perp position row first. Spot and cash rows do not have perp TP/SL.")
    fallback = getattr(self, "hyperliquid_perp_coin_var", tk.StringVar(value="")).get().strip() or getattr(self, "hyperliquid_coin_var", tk.StringVar(value="")).get().strip()
    if fallback:
        return normalize_hyperliquid_coin(fallback)
    raise ValueError("Select a Hyperliquid perp position row first.")


def _target_selected_hyperliquid_perp_position(self: tk.Tk) -> bool:
    try:
        coin = _selected_hyperliquid_perp_coin_from_workspace(self)
        preview = getattr(self, "hyperliquid_trading_preview_text", None)
        if preview is not None:
            self.preview_text = preview
        selector = getattr(self, "use_hyperliquid_perp_position", None)
        if not callable(selector):
            raise RuntimeError("Perp position targeting is not installed.")
        selector(coin)
        return True
    except Exception as exc:
        messagebox.showinfo("Select perp position", str(exc))
        return False


def _open_tpsl_for_selected_hyperliquid_perp_position(self: tk.Tk) -> None:
    try:
        if not _target_selected_hyperliquid_perp_position(self):
            return
        command = _first_available_command(self, "show_hyperliquid_position_tpsl_dialog")
        _run_hyperliquid_ticket_action(
            self,
            ticket_kind="perp",
            preview_widget=self.hyperliquid_trading_preview_text,
            command=command,
        )
    except Exception as exc:
        messagebox.showinfo("TP/SL unavailable", str(exc))


def _open_editor_for_selected_hyperliquid_perp_position(self: tk.Tk) -> None:
    try:
        if not _target_selected_hyperliquid_perp_position(self):
            return
        command = _first_available_command(self, "show_hyperliquid_perp_position_editor")
        _run_hyperliquid_ticket_action(
            self,
            ticket_kind="perp",
            preview_widget=self.hyperliquid_trading_preview_text,
            command=command,
        )
    except Exception as exc:
        messagebox.showinfo("Position editor unavailable", str(exc))


def _open_editor_for_clicked_hyperliquid_perp_position(self: tk.Tk, table: ttk.Treeview, event: tk.Event) -> None:
    row_id = table.identify_row(event.y)
    if not row_id:
        return
    table.selection_set(row_id)
    _load_workspace_ticket_from_holding(self, table, event, "Hyperliquid")
    _open_editor_for_selected_hyperliquid_perp_position(self)


def _workspace_ticket_symbol(symbol: str) -> str:
    clean = symbol.strip().upper()
    if clean.startswith("HL:"):
        clean = clean[3:]
    if "(" in clean:
        clean = clean.split("(", 1)[0].strip()
    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
    return clean


def _update_workspace_holdings_tables(self: tk.Tk, portfolio=None) -> None:
    portfolio = portfolio or self.broker.get_portfolio()
    schwab_table = getattr(self, "schwab_workspace_holdings_table", None)
    hyperliquid_table = getattr(self, "hyperliquid_workspace_holdings_table", None)
    if schwab_table is not None:
        _populate_workspace_holdings_table(schwab_table, _workspace_holding_rows(portfolio, "Schwab"))
    if hyperliquid_table is not None:
        _populate_workspace_holdings_table(hyperliquid_table, _workspace_holding_rows(portfolio, "Hyperliquid"))


def _populate_workspace_holdings_table(table: ttk.Treeview, rows: list[dict[str, object]]) -> None:
    for row_id in table.get_children():
        table.delete(row_id)
    for index, row in enumerate(rows):
        pnl = row.get("pnl")
        tag = "cash" if str(row.get("type", "")).lower() == "cash" else "positive" if isinstance(pnl, (int, float)) and pnl > 0 else "negative" if isinstance(pnl, (int, float)) and pnl < 0 else ""
        table.insert(
            "",
            tk.END,
            iid=f"holding_{index}",
            values=(
                row.get("symbol", ""),
                row.get("type", ""),
                row.get("qty", ""),
                row.get("last", ""),
                row.get("value", ""),
                row.get("pnl_text", ""),
            ),
            tags=(tag,) if tag else (),
        )


def _populate_workspace_open_orders_table(table: ttk.Treeview, open_orders: list[dict[str, Any]]) -> None:
    from app.ui import hyperliquid_trading_extension as hyperliquid_ui

    for row_id in table.get_children():
        table.delete(row_id)
    for index, raw_order in enumerate(open_orders):
        order = hyperliquid_ui.normalize_hyperliquid_open_order(raw_order)
        direction = order.direction or order.side
        tag = "trigger" if order.is_trigger else "buy" if "buy" in direction.lower() else "sell" if "sell" in direction.lower() or "short" in direction.lower() else ""
        table.insert(
            "",
            tk.END,
            iid=f"open_order_{index}",
            values=(
                hyperliquid_ui._order_time_label(order.raw),
                order.order_kind,
                _workspace_ticket_symbol(hyperliquid_ui._display_order_coin(order.coin, "")),
                direction,
                order.size_label,
                order.price_label,
                "Edit",
                "Yes" if order.reduce_only else "--",
                order.trigger_condition or "N/A",
                order.tpsl_label or "--",
                order.oid,
            ),
            tags=(tag,) if tag else (),
        )


def _workspace_holding_rows(portfolio, venue: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    total_value = max(portfolio.total_value, 0.01)

    if venue == "Hyperliquid":
        for cash in portfolio.display_cash_positions():
            if "HYPERLIQUID" not in cash.source.upper():
                continue
            rows.append(
                {
                    "symbol": cash.display_symbol,
                    "type": "Cash",
                    "qty": f"{cash.quantity:g}",
                    "last": _fmt_money(cash.last_price),
                    "value": _fmt_money(cash.market_value),
                    "pnl": None,
                    "pnl_text": "--",
                }
            )

    for symbol, position in sorted(portfolio.positions.items()):
        asset_type = str(getattr(position, "asset_type", "") or _workspace_asset_type(symbol))
        is_hyperliquid = _workspace_is_hyperliquid(asset_type, symbol)
        if venue == "Hyperliquid" and not is_hyperliquid:
            continue
        if venue == "Schwab" and is_hyperliquid:
            continue
        rows.append(
            {
                "symbol": position.symbol,
                "type": asset_type,
                "qty": f"{position.quantity:g}",
                "last": _fmt_money(position.last_price),
                "value": _fmt_money(position.market_value),
                "pnl": position.unrealized_profit_loss,
                "pnl_text": _fmt_money(position.unrealized_profit_loss),
                "weight": position.market_value / total_value,
            }
        )

    return sorted(rows, key=lambda row: (str(row["type"]) == "Cash", -abs(_money_value(str(row["value"]))), str(row["symbol"])))


def _workspace_asset_type(symbol: str) -> str:
    clean = symbol.upper()
    if clean.endswith("-PERP-SHORT"):
        return "Perp Short"
    if clean.endswith("-PERP"):
        return "Perp Long"
    if clean.endswith("-SPOT"):
        return "Spot"
    return "Equity"


def _workspace_is_hyperliquid(asset_type: str, symbol: str) -> bool:
    normalized_type = asset_type.strip().lower()
    normalized_symbol = symbol.strip().upper()
    return (
        normalized_type == "spot"
        or normalized_type.startswith("perp")
        or normalized_type == "hyperliquid"
        or normalized_symbol.endswith("-SPOT")
        or "-PERP" in normalized_symbol
    )


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _money_value(value: str) -> float:
    try:
        return float(value.replace("$", "").replace(",", ""))
    except ValueError:
        return 0.0


def _first_available_command(self: tk.Tk, *names: str) -> Callable[[], None]:
    for name in names:
        command = getattr(self, name, None)
        if callable(command):
            return command

    def _missing() -> None:
        messagebox.showinfo(
            "Action unavailable",
            f"None of these actions are installed yet: {', '.join(names)}",
        )

    return _missing


def _run_workspace_action(
    self: tk.Tk,
    *,
    venue: str,
    preview_widget: tk.Text,
    command: Callable[[], None],
) -> None:
    _ensure_execution_workspace_vars(self)
    self.trade_venue_var.set(venue)
    if hasattr(self, "on_trading_venue_changed"):
        try:
            self.on_trading_venue_changed()
        except Exception:
            pass
    self.preview_text = preview_widget
    command()


def _run_hyperliquid_sync_action(self: tk.Tk) -> None:
    previous_source = getattr(getattr(self, "broker", None), "source_message", "")
    _set_hyperliquid_sync_status(self, "working")
    try:
        _run_workspace_action(
            self,
            venue="Hyperliquid",
            preview_widget=self.hyperliquid_trading_preview_text,
            command=_first_available_command(self, "sync_hyperliquid_account"),
        )
    except Exception:
        _set_hyperliquid_sync_status(self, "failure")
        raise

    _sync_hyperliquid_badge_from_status_text(self)
    if getattr(self, "hyperliquid_sync_status_state", "failure") != "working":
        return

    current_source = getattr(getattr(self, "broker", None), "source_message", "")
    if current_source != previous_source and "Hyperliquid" in current_source:
        _set_hyperliquid_sync_status(self, "success")
    else:
        _set_hyperliquid_sync_status(self, "failure")


def _install_hyperliquid_sync_status_badge(parent: ttk.Frame, self: tk.Tk, *, row: int, column: int) -> None:
    _ensure_execution_workspace_vars(self)
    _install_hyperliquid_sync_status_trace(self)

    badge = getattr(self, "hyperliquid_sync_status_badge", None)
    if badge is None:
        badge = tk.Label(
            parent,
            textvariable=self.hyperliquid_sync_status_var,
            bg="#fee2e2",
            fg="#b91c1c",
            font=("Segoe UI", 9, "bold"),
            padx=8,
            pady=4,
            bd=0,
        )
        self.hyperliquid_sync_status_badge = badge
    _apply_hyperliquid_sync_status_colors(self)
    badge.grid(row=row, column=column, sticky="e", padx=(0, 8))


def _install_hyperliquid_sync_status_trace(self: tk.Tk) -> None:
    if getattr(self, "_hyperliquid_sync_status_trace_installed", False):
        return
    self.hyperliquid_status_var.trace_add("write", lambda *_args, app=self: _sync_hyperliquid_badge_from_status_text(app))
    self._hyperliquid_sync_status_trace_installed = True
    _sync_hyperliquid_badge_from_status_text(self)


def _sync_hyperliquid_badge_from_status_text(self: tk.Tk) -> None:
    status = str(self.hyperliquid_status_var.get()).strip().lower()
    if status == "hyperliquid: synced":
        _set_hyperliquid_sync_status(self, "success")
    elif "sync failed" in status or "not synced" in status:
        _set_hyperliquid_sync_status(self, "failure")


def _set_hyperliquid_sync_status(self: tk.Tk, status: str, message: str | None = None) -> None:
    _ensure_execution_workspace_vars(self)
    clean = status if status in {"success", "failure", "working"} else "failure"
    self.hyperliquid_sync_status_state = clean
    label = {
        "success": "\u2713 Synced",
        "failure": "\u2715 Not synced",
        "working": "\u21bb Syncing",
    }[clean]
    self.hyperliquid_sync_status_var.set(message or label)
    _apply_hyperliquid_sync_status_colors(self)


def _apply_hyperliquid_sync_status_colors(self: tk.Tk) -> None:
    badge = getattr(self, "hyperliquid_sync_status_badge", None)
    if badge is None:
        return
    state = getattr(self, "hyperliquid_sync_status_state", "failure")
    colors = {
        "success": ("#dcfce7", "#047857"),
        "failure": ("#fee2e2", "#b91c1c"),
        "working": ("#dbeafe", "#1d4ed8"),
    }.get(state, ("#fee2e2", "#b91c1c"))
    try:
        badge.configure(bg=colors[0], fg=colors[1])
    except tk.TclError:
        return


def _run_hyperliquid_ticket_action(
    self: tk.Tk,
    *,
    ticket_kind: str,
    preview_widget: tk.Text,
    command: Callable[[], None],
) -> None:
    _sync_hyperliquid_ticket_to_shared(self, ticket_kind)
    try:
        _run_workspace_action(
            self,
            venue="Hyperliquid",
            preview_widget=preview_widget,
            command=command,
        )
    finally:
        _sync_hyperliquid_ticket_from_shared(self, ticket_kind)


def _sync_hyperliquid_ticket_to_shared(self: tk.Tk, ticket_kind: str) -> None:
    _ensure_execution_workspace_vars(self)
    self.hyperliquid_workspace_active_ticket_var.set(ticket_kind)
    if ticket_kind == "spot":
        self.symbol_var.set(self.hyperliquid_spot_symbol_var.get())
        self.hyperliquid_coin_var.set(self.hyperliquid_spot_coin_var.get())
        self.side_var.set(self.hyperliquid_spot_side_var.get())
        self.order_type_var.set(self.hyperliquid_spot_order_type_var.get())
        self.quantity_var.set(self.hyperliquid_spot_quantity_var.get())
        self.limit_price_var.set(self.hyperliquid_spot_limit_price_var.get())
        self.stop_price_var.set(self.hyperliquid_spot_stop_price_var.get())
        self.hyperliquid_tif_var.set(self.hyperliquid_spot_tif_var.get())
        self.cancel_order_id_var.set(self.hyperliquid_spot_cancel_order_id_var.get())
        self.hyperliquid_size_unit_var.set(self.hyperliquid_spot_size_unit_var.get())
        self.hyperliquid_size_percent_var.set(self.hyperliquid_spot_size_percent_var.get())
        self.hyperliquid_size_status_var.set(self.hyperliquid_spot_size_status_var.get())
        return

    self.hyperliquid_coin_var.set(self.hyperliquid_perp_coin_var.get())
    self.symbol_var.set(self.hyperliquid_perp_symbol_var.get())
    self.side_var.set(self.hyperliquid_perp_side_var.get())
    self.order_type_var.set(self.hyperliquid_perp_order_type_var.get())
    self.quantity_var.set(self.hyperliquid_perp_quantity_var.get())
    self.limit_price_var.set(self.hyperliquid_perp_limit_price_var.get())
    self.hyperliquid_target_price_var.set(self.hyperliquid_perp_target_price_var.get())
    self.hyperliquid_bad_price_var.set(self.hyperliquid_perp_stop_price_var.get())
    self.stop_price_var.set(self.hyperliquid_perp_stop_price_var.get())
    self.hyperliquid_tif_var.set(self.hyperliquid_perp_tif_var.get())
    self.cancel_order_id_var.set(self.hyperliquid_perp_cancel_order_id_var.get())
    self.hyperliquid_leverage_var.set(self.hyperliquid_perp_leverage_var.get())
    self.hyperliquid_margin_mode_var.set(self.hyperliquid_perp_margin_mode_var.get())
    self.hyperliquid_fee_rate_var.set(self.hyperliquid_perp_fee_rate_var.get())
    self.hyperliquid_reduce_only_var.set(self.hyperliquid_perp_reduce_only_var.get())
    if hasattr(self, "hyperliquid_attach_tpsl_var"):
        self.hyperliquid_attach_tpsl_var.set(self.hyperliquid_perp_attach_tpsl_var.get())


def _sync_hyperliquid_ticket_from_shared(self: tk.Tk, ticket_kind: str) -> None:
    if ticket_kind == "spot":
        self.hyperliquid_spot_symbol_var.set(self.symbol_var.get())
        self.hyperliquid_spot_coin_var.set(self.hyperliquid_coin_var.get())
        self.hyperliquid_spot_side_var.set(self.side_var.get())
        self.hyperliquid_spot_order_type_var.set(self.order_type_var.get())
        self.hyperliquid_spot_quantity_var.set(self.quantity_var.get())
        self.hyperliquid_spot_limit_price_var.set(self.limit_price_var.get())
        self.hyperliquid_spot_stop_price_var.set(self.stop_price_var.get())
        self.hyperliquid_spot_tif_var.set(self.hyperliquid_tif_var.get())
        self.hyperliquid_spot_cancel_order_id_var.set(self.cancel_order_id_var.get())
        self.hyperliquid_spot_size_unit_var.set(self.hyperliquid_size_unit_var.get())
        self.hyperliquid_spot_size_percent_var.set(self.hyperliquid_size_percent_var.get())
        self.hyperliquid_spot_size_status_var.set(self.hyperliquid_size_status_var.get())
        return

    self.hyperliquid_perp_coin_var.set(self.hyperliquid_coin_var.get())
    self.hyperliquid_perp_symbol_var.set(self.symbol_var.get())
    self.hyperliquid_perp_side_var.set(self.side_var.get())
    self.hyperliquid_perp_order_type_var.set(self.order_type_var.get())
    self.hyperliquid_perp_quantity_var.set(self.quantity_var.get())
    self.hyperliquid_perp_limit_price_var.set(self.limit_price_var.get())
    self.hyperliquid_perp_target_price_var.set(self.hyperliquid_target_price_var.get())
    self.hyperliquid_perp_stop_price_var.set(self.stop_price_var.get() or self.hyperliquid_bad_price_var.get())
    self.hyperliquid_perp_tif_var.set(self.hyperliquid_tif_var.get())
    self.hyperliquid_perp_cancel_order_id_var.set(self.cancel_order_id_var.get())
    self.hyperliquid_perp_leverage_var.set(self.hyperliquid_leverage_var.get())
    self.hyperliquid_perp_margin_mode_var.set(self.hyperliquid_margin_mode_var.get())
    self.hyperliquid_perp_fee_rate_var.set(self.hyperliquid_fee_rate_var.get())
    self.hyperliquid_perp_reduce_only_var.set(self.hyperliquid_reduce_only_var.get())
    if hasattr(self, "hyperliquid_attach_tpsl_var"):
        self.hyperliquid_perp_attach_tpsl_var.set(self.hyperliquid_attach_tpsl_var.get())


def _add_workspace_button(
    parent: ttk.Frame,
    *,
    row: int,
    column: int,
    text: str,
    command: Callable[[], None],
    style: str = "TButton",
    columnspan: int = 1,
) -> None:
    ttk.Button(parent, text=text, command=command, style=style).grid(
        row=row,
        column=column,
        columnspan=columnspan,
        sticky="ew",
        padx=(0 if column == 0 else 4, 0),
        pady=(0 if row == 0 else 8, 8),
        ipady=2,
    )


def _grid_hyperliquid_workspace_spot_quantity_row(parent: ttk.LabelFrame, self: tk.Tk, row: int) -> None:
    ttk.Label(parent, text="Quantity", style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
    quantity_controls = ttk.Frame(parent, style="Panel.TFrame")
    quantity_controls.grid(row=row, column=1, sticky="ew", pady=6)
    quantity_controls.columnconfigure(0, weight=1)

    ttk.Entry(quantity_controls, textvariable=self.hyperliquid_spot_quantity_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
    unit_combo = ttk.Combobox(
        quantity_controls,
        textvariable=self.hyperliquid_spot_size_unit_var,
        values=_hyperliquid_workspace_spot_size_unit_values(self),
        state="readonly",
        width=8,
    )
    unit_combo.configure(postcommand=lambda: _refresh_hyperliquid_workspace_spot_size_unit_combo(self, unit_combo))
    unit_combo.grid(row=0, column=1, sticky="ew")

    ttk.Label(parent, text="Entry / Limit", style="Subtle.TLabel").grid(row=row, column=2, sticky="w", padx=(16, 8), pady=6)
    ttk.Entry(parent, textvariable=self.hyperliquid_spot_limit_price_var).grid(row=row, column=3, sticky="ew", pady=6)
    _sync_hyperliquid_workspace_spot_size_unit(self)


def _grid_hyperliquid_workspace_spot_size_controls(parent: ttk.LabelFrame, self: tk.Tk, row: int) -> None:
    ttk.Label(parent, text="Size %", style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
    controls = ttk.Frame(parent, style="Panel.TFrame")
    controls.grid(row=row, column=1, columnspan=3, sticky="ew", pady=6)
    controls.columnconfigure(0, weight=1)

    ttk.Scale(
        controls,
        from_=0,
        to=100,
        orient=tk.HORIZONTAL,
        variable=self.hyperliquid_spot_size_percent_var,
        command=lambda _value: _apply_hyperliquid_workspace_spot_percent(self),
    ).grid(row=0, column=0, sticky="ew", padx=(0, 8))

    for column, percent in enumerate((25, 50, 75), start=1):
        ttk.Button(
            controls,
            text=f"{percent}%",
            command=lambda value=percent: _apply_hyperliquid_workspace_spot_percent(self, value),
            style="Compact.TButton",
        ).grid(row=0, column=column, sticky="ew", padx=(0, 6))

    ttk.Button(
        controls,
        text="Max",
        command=lambda: _apply_hyperliquid_workspace_spot_percent(self, 100),
        style="CompactAccent.TButton",
    ).grid(row=0, column=4, sticky="ew")
    ttk.Label(controls, textvariable=self.hyperliquid_spot_size_status_var, style="Subtle.TLabel").grid(
        row=1,
        column=0,
        columnspan=5,
        sticky="w",
        pady=(3, 0),
    )


def _apply_hyperliquid_workspace_spot_percent(self: tk.Tk, percent: float | None = None) -> None:
    _sync_hyperliquid_ticket_to_shared(self, "spot")
    try:
        if hasattr(self, "apply_hyperliquid_quantity_percent"):
            self.apply_hyperliquid_quantity_percent(percent)
    finally:
        _sync_hyperliquid_ticket_from_shared(self, "spot")


def _hyperliquid_workspace_spot_size_unit_values(self: tk.Tk) -> list[str]:
    base = _hyperliquid_workspace_spot_base(self)
    return ["USDC", base] if base and base != "USDC" else ["USDC"]


def _refresh_hyperliquid_workspace_spot_size_unit_combo(self: tk.Tk, combo: ttk.Combobox) -> None:
    values = _hyperliquid_workspace_spot_size_unit_values(self)
    combo.configure(values=values)
    _sync_hyperliquid_workspace_spot_size_unit(self)


def _sync_hyperliquid_workspace_spot_size_unit(self: tk.Tk) -> None:
    values = _hyperliquid_workspace_spot_size_unit_values(self)
    current = self.hyperliquid_spot_size_unit_var.get().strip().upper()
    if current not in {value.upper() for value in values}:
        self.hyperliquid_spot_size_unit_var.set(values[0])


def _hyperliquid_workspace_spot_base(self: tk.Tk) -> str:
    raw = self.hyperliquid_spot_symbol_var.get().strip() or self.hyperliquid_spot_coin_var.get().strip()
    base = raw.upper().replace("-SPOT", "")
    if "/" in base:
        base = base.split("/", 1)[0]
    return base


def _build_schwab_trading_tab(
    self: tk.Tk,
    parent: ttk.Frame,
    tabs: ttk.Notebook,
    options_tab: ttk.Frame,
) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(1, weight=1)

    header = ttk.LabelFrame(parent, text="Schwab Trading Workspace", style="Card.TLabelframe")
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(
        header,
        text=(
            "Dedicated Schwab execution surface for stocks, ETFs, and option planning. "
            "The original Cockpit tab is unchanged as a fallback."
        ),
        style="Subtle.TLabel",
        wraplength=1120,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Button(
        header,
        text="Open Options Lab",
        command=lambda: tabs.select(options_tab),
        style="Accent.TButton",
    ).grid(row=0, column=1, sticky="e")

    workspace = _make_paned(parent, tk.HORIZONTAL)
    workspace.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

    ticket_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    output_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    workspace.add(ticket_shell, minsize=540, stretch="never")
    workspace.add(output_shell, minsize=520, stretch="always")

    ticket = ttk.LabelFrame(ticket_shell, text="Schwab Stock / ETF Ticket", style="Card.TLabelframe")
    ticket.pack(fill=tk.BOTH, expand=True)
    ticket.columnconfigure(1, weight=1)
    ticket.columnconfigure(3, weight=1)

    self._grid_row(
        ticket,
        0,
        "Symbol",
        ttk.Entry(ticket, textvariable=self.symbol_var),
        "Side",
        ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"),
    )
    self._grid_row(
        ticket,
        1,
        "Order type",
        ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"),
        "Time",
        ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, state="readonly"),
    )
    self._grid_row(ticket, 2, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var), "Entry / Limit", ttk.Entry(ticket, textvariable=self.limit_price_var))
    self._grid_row(ticket, 3, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var), "DELETE ME", ttk.Entry(ticket, textvariable=self.confirmation_var))
    ttk.Label(ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=4, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    output_stack = _make_paned(output_shell, tk.VERTICAL)
    output_stack.pack(fill=tk.BOTH, expand=True)
    holdings_shell = ttk.Frame(output_stack, style="Canvas.TFrame")
    analysis_shell = ttk.Frame(output_stack, style="Canvas.TFrame")
    output_stack.add(holdings_shell, minsize=150, stretch="never")
    output_stack.add(analysis_shell, minsize=360, stretch="always")

    schwab_holdings_frame = ttk.LabelFrame(holdings_shell, text="Schwab Holdings", style="Card.TLabelframe")
    schwab_holdings_frame.pack(fill=tk.BOTH, expand=True)
    self.schwab_workspace_holdings_table = _workspace_holdings_table(schwab_holdings_frame)
    _bind_workspace_holdings_click(self, self.schwab_workspace_holdings_table, "Schwab")

    schwab_output_frame = ttk.LabelFrame(analysis_shell, text="Schwab Analysis + Order Output", style="Card.TLabelframe")
    schwab_output_frame.pack(fill=tk.BOTH, expand=True)
    self.schwab_trading_preview_text = _workspace_text(schwab_output_frame)

    actions = ttk.LabelFrame(ticket, text="Schwab Actions", style="Card.TLabelframe")
    actions.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    for column in range(3):
        actions.columnconfigure(column, weight=1, uniform="schwab_actions")

    def schwab_action(*names: str) -> Callable[[], None]:
        return lambda: _run_workspace_action(
            self,
            venue="Schwab",
            preview_widget=self.schwab_trading_preview_text,
            command=_first_available_command(self, *names),
        )

    _add_workspace_button(actions, row=0, column=0, text="Connect Schwab", command=schwab_action("connect_schwab", "run_schwab_preview"))
    _add_workspace_button(actions, row=0, column=1, text="Refresh Account", command=schwab_action("refresh_schwab_account", "refresh_portfolio"))
    _add_workspace_button(actions, row=0, column=2, text="Tech Analysis", command=schwab_action("show_technical_analysis"))
    _add_workspace_button(actions, row=1, column=0, text="Preview Risk", command=schwab_action("preview_order"), style="Accent.TButton")
    _add_workspace_button(actions, row=1, column=1, text="Preview Schwab Order", command=schwab_action("run_schwab_preview"))
    _add_workspace_button(actions, row=1, column=2, text="Position Size", command=schwab_action("show_position_size"))
    _add_workspace_button(actions, row=2, column=0, text="Recent Orders", command=schwab_action("load_selected_recent_orders", "load_schwab_open_orders"))
    _add_workspace_button(actions, row=2, column=1, text="Open Only", command=schwab_action("load_selected_open_orders_only", "load_schwab_open_orders_only"))
    _add_workspace_button(actions, row=2, column=2, text="Order Checklist", command=schwab_action("show_manual_checklist"))
    _add_workspace_button(actions, row=3, column=0, text="Cancel Order", command=schwab_action("cancel_selected_order", "show_cancel_order_placeholder"), style="Danger.TButton")
    _add_workspace_button(actions, row=3, column=1, text="Live Safety", command=schwab_action("show_live_submit_safety_review"))
    _add_workspace_button(actions, row=3, column=2, text="LIVE Submit", command=schwab_action("submit_selected_venue", "submit_live_schwab_order_guarded"), style="Danger.TButton")

    status = ttk.Frame(ticket, style="Panel.TFrame")
    status.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(8, 0))
    status.columnconfigure((0, 1, 2), weight=1)
    ttk.Label(status, textvariable=self.schwab_status_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Label(status, textvariable=self.schwab_preview_status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Label(status, textvariable=self.schwab_verification_status_var, style="Chip.TLabel").grid(row=0, column=2, sticky="ew")

    _set_workspace_text(
        self.schwab_trading_preview_text,
        "SCHWAB TRADING WORKSPACE\n"
        "========================\n\n"
        "Use this tab for stocks, ETFs, Schwab previews, order history, and guarded live Schwab actions.\n\n"
        "Options still live in the Options What-If Lab; use the button above when the weekly setup needs calls/puts instead of shares.",
    )


def _build_hyperliquid_trading_tab(self: tk.Tk, parent: ttk.Frame) -> None:
    _ensure_execution_workspace_vars(self)
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(1, weight=1)

    header = ttk.LabelFrame(parent, text="Hyperliquid Trading Workspace", style="Card.TLabelframe")
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    ttk.Label(
        header,
        text=(
            "Dedicated Hyperliquid execution surface for spot and perp tickets. "
            "This keeps crypto controls away from Schwab stock and option workflows."
        ),
        style="Subtle.TLabel",
        wraplength=1120,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))
    _install_hyperliquid_sync_status_badge(header, self, row=0, column=1)
    ttk.Button(
        header,
        text="Sync Hyperliquid",
        command=lambda: _run_hyperliquid_sync_action(self),
        style="Accent.TButton",
    ).grid(row=0, column=2, sticky="e")

    workspace = _make_paned(parent, tk.HORIZONTAL)
    workspace.grid(row=1, column=0, sticky="nsew", pady=(12, 0))

    ticket_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    output_shell = ttk.Frame(workspace, style="Canvas.TFrame")
    workspace.add(ticket_shell, minsize=760, stretch="always")
    workspace.add(output_shell, minsize=520, stretch="always")

    output_stack = _make_paned(output_shell, tk.VERTICAL)
    output_stack.pack(fill=tk.BOTH, expand=True)
    account_shell = ttk.Frame(output_stack, style="Canvas.TFrame")
    analysis_shell = ttk.Frame(output_stack, style="Canvas.TFrame")
    output_stack.add(account_shell, minsize=250, stretch="never")
    output_stack.add(analysis_shell, minsize=320, stretch="always")

    account_tabs = ttk.Notebook(account_shell)
    account_tabs.pack(fill=tk.BOTH, expand=True)
    holdings_shell = ttk.Frame(account_tabs, style="Panel.TFrame", padding=10)
    orders_shell = ttk.Frame(account_tabs, style="Panel.TFrame", padding=10)
    account_tabs.add(holdings_shell, text="Balances")
    account_tabs.add(orders_shell, text="Open Orders")

    hyperliquid_holdings_frame = ttk.LabelFrame(holdings_shell, text="Hyperliquid Balances", style="Card.TLabelframe")
    hyperliquid_holdings_frame.pack(fill=tk.BOTH, expand=True)
    self.hyperliquid_workspace_holdings_table = _workspace_holdings_table(hyperliquid_holdings_frame)
    _bind_workspace_holdings_click(self, self.hyperliquid_workspace_holdings_table, "Hyperliquid")
    hyperliquid_position_actions = ttk.Frame(hyperliquid_holdings_frame, style="Panel.TFrame")
    hyperliquid_position_actions.pack(fill=tk.X, pady=(8, 0))
    hyperliquid_position_actions.columnconfigure((0, 1, 2), weight=1)
    ttk.Button(
        hyperliquid_position_actions,
        text="Edit Selected Position",
        command=lambda: _open_editor_for_selected_hyperliquid_perp_position(self),
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(
        hyperliquid_position_actions,
        text="TP/SL Selected",
        command=lambda: _open_tpsl_for_selected_hyperliquid_perp_position(self),
        style="Accent.TButton",
    ).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Button(
        hyperliquid_position_actions,
        text="Open Orders",
        command=lambda: _run_hyperliquid_ticket_action(
            self,
            ticket_kind="perp",
            preview_widget=self.hyperliquid_trading_preview_text,
            command=_first_available_command(self, "load_hyperliquid_open_orders"),
        ),
    ).grid(row=0, column=2, sticky="ew")

    hyperliquid_orders_frame = ttk.LabelFrame(orders_shell, text="Hyperliquid Open Orders", style="Card.TLabelframe")
    hyperliquid_orders_frame.pack(fill=tk.BOTH, expand=True)
    self.hyperliquid_workspace_open_orders_table = _workspace_open_orders_table(hyperliquid_orders_frame)
    _bind_workspace_open_orders_click(self, self.hyperliquid_workspace_open_orders_table)

    hyperliquid_output_frame = ttk.LabelFrame(analysis_shell, text="Hyperliquid Analysis + Order Output", style="Card.TLabelframe")
    hyperliquid_output_frame.pack(fill=tk.BOTH, expand=True)
    self.hyperliquid_trading_preview_text = _workspace_text(hyperliquid_output_frame)

    def hyperliquid_action(ticket_kind: str, *names: str) -> Callable[[], None]:
        return lambda: _run_hyperliquid_ticket_action(
            self,
            ticket_kind=ticket_kind,
            preview_widget=self.hyperliquid_trading_preview_text,
            command=_first_available_command(self, *names),
        )

    def hyperliquid_workspace_action(ticket_kind: str, tab_attr: str, *names: str) -> Callable[[], None]:
        def run() -> None:
            _run_hyperliquid_ticket_action(
                self,
                ticket_kind=ticket_kind,
                preview_widget=self.hyperliquid_trading_preview_text,
                command=_first_available_command(self, *names),
            )
            opener = getattr(self, "show_hyperliquid_crypto_research_workspace", None)
            if not callable(opener):
                return
            opener()

            def select_tab() -> None:
                notebook = getattr(self, "hyperliquid_research_tabs", None)
                frame = getattr(self, tab_attr, None)
                target = getattr(frame, "_scrollable_outer", frame)
                if notebook is None or target is None:
                    return
                try:
                    notebook.select(target)
                except tk.TclError:
                    pass

            self.after(250, select_tab)

        return run

    from app.ui import hyperliquid_trading_extension as hyperliquid_ui

    hyperliquid_ui._ensure_hyperliquid_vars(self)
    hyperliquid_ui._configure_compact_ticket_styles(self)

    tickets = ttk.Frame(ticket_shell, style="Canvas.TFrame")
    tickets.pack(fill=tk.BOTH, expand=True)
    for column in range(2):
        tickets.columnconfigure(column, weight=1, uniform="hyperliquid_tickets")
    tickets.rowconfigure(0, weight=1)

    spot_ticket = ttk.LabelFrame(tickets, text="Hyperliquid Spot Ticket", style="Card.TLabelframe")
    spot_ticket.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    spot_ticket.columnconfigure(1, weight=1)
    spot_ticket.columnconfigure(3, weight=1)

    perp_ticket = ttk.LabelFrame(tickets, text="Hyperliquid Perp Ticket", style="Card.TLabelframe")
    perp_ticket.grid(row=0, column=1, sticky="nsew")
    perp_ticket.columnconfigure(1, weight=1)
    perp_ticket.columnconfigure(3, weight=1)

    self._grid_row(spot_ticket, 0, "Market", ttk.Entry(spot_ticket, textvariable=self.hyperliquid_spot_symbol_var), "HL Coin", ttk.Entry(spot_ticket, textvariable=self.hyperliquid_spot_coin_var))
    self._grid_row(
        spot_ticket,
        1,
        "Side",
        ttk.Combobox(spot_ticket, textvariable=self.hyperliquid_spot_side_var, values=[s.value for s in OrderSide], state="readonly"),
        "Order type",
        ttk.Combobox(spot_ticket, textvariable=self.hyperliquid_spot_order_type_var, values=[o.value for o in OrderType], state="readonly"),
    )
    _grid_hyperliquid_workspace_spot_quantity_row(spot_ticket, self, 2)
    _grid_hyperliquid_workspace_spot_size_controls(spot_ticket, self, 3)
    self._grid_row(
        spot_ticket,
        4,
        "Stop price",
        ttk.Entry(spot_ticket, textvariable=self.hyperliquid_spot_stop_price_var),
        "Use Mid",
        ttk.Button(spot_ticket, text="Use Mid", command=hyperliquid_action("spot", "use_hyperliquid_cockpit_spot_mid_market"), style="Accent.TButton"),
    )
    self._grid_row(
        spot_ticket,
        5,
        "HL TIF",
        ttk.Combobox(spot_ticket, textvariable=self.hyperliquid_spot_tif_var, values=["Alo", "Ioc", "Gtc"], state="readonly"),
        "",
        ttk.Frame(spot_ticket, style="Canvas.TFrame"),
    )
    ttk.Label(spot_ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=6, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(spot_ticket, textvariable=self.hyperliquid_spot_cancel_order_id_var).grid(row=6, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    spot_actions = ttk.LabelFrame(spot_ticket, text="Spot Actions", style="Card.TLabelframe")
    spot_actions.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    for column in range(3):
        spot_actions.columnconfigure(column, weight=1, uniform="hyperliquid_spot_actions")

    _add_workspace_button(spot_actions, row=0, column=0, text="Use Mid", command=hyperliquid_action("spot", "use_hyperliquid_cockpit_spot_mid_market"), style="Accent.TButton")
    _add_workspace_button(spot_actions, row=0, column=1, text="Spot What-If", command=hyperliquid_workspace_action("spot", "hyperliquid_crypto_scenarios_frame", "run_hyperliquid_spot_what_if"), style="Accent.TButton")
    _add_workspace_button(spot_actions, row=0, column=2, text="Preview Spot", command=hyperliquid_action("spot", "preview_hyperliquid_spot_ticket"))
    _add_workspace_button(spot_actions, row=1, column=0, text="Open Orders", command=hyperliquid_action("spot", "load_hyperliquid_open_orders"))
    _add_workspace_button(spot_actions, row=1, column=1, text="Edit Order", command=hyperliquid_action("spot", "show_hyperliquid_order_edit_dialog"))
    _add_workspace_button(spot_actions, row=1, column=2, text="Cancel Order", command=hyperliquid_action("spot", "cancel_hyperliquid_order_guarded"), style="Danger.TButton")
    _add_workspace_button(spot_actions, row=2, column=0, text="LIVE Submit", command=hyperliquid_action("spot", "show_hyperliquid_spot_live_submit_safety_review"), style="Danger.TButton", columnspan=3)

    ticket = perp_ticket
    self._grid_row(ticket, 0, "Coin", ttk.Entry(ticket, textvariable=self.hyperliquid_perp_coin_var), "Symbol", ttk.Entry(ticket, textvariable=self.hyperliquid_perp_symbol_var))
    self._grid_row(
        ticket,
        1,
        "Direction",
        ttk.Combobox(ticket, textvariable=self.hyperliquid_perp_side_var, values=[s.value for s in OrderSide], state="readonly"),
        "Order type",
        ttk.Combobox(ticket, textvariable=self.hyperliquid_perp_order_type_var, values=[o.value for o in OrderType], state="readonly"),
    )
    self._grid_row(ticket, 2, "Size", ttk.Entry(ticket, textvariable=self.hyperliquid_perp_quantity_var), "Entry / Limit", ttk.Entry(ticket, textvariable=self.hyperliquid_perp_limit_price_var))
    self._grid_row(ticket, 3, "TP price", ttk.Entry(ticket, textvariable=self.hyperliquid_perp_target_price_var), "SL price", ttk.Entry(ticket, textvariable=self.hyperliquid_perp_stop_price_var))
    self._grid_row(ticket, 4, "HL TIF", ttk.Combobox(ticket, textvariable=self.hyperliquid_perp_tif_var, values=["Alo", "Ioc", "Gtc"], state="readonly"), "Reduce-only", ttk.Checkbutton(ticket, variable=self.hyperliquid_perp_reduce_only_var),
    )
    leverage_combo = ttk.Combobox(ticket, textvariable=self.hyperliquid_perp_leverage_var, values=["1", "2", "3", "5", "10", "20", "50"], width=8)
    margin_combo = ttk.Combobox(ticket, textvariable=self.hyperliquid_perp_margin_mode_var, values=["Cross", "Isolated"], state="readonly")
    self._grid_row(ticket, 5, "Leverage x", leverage_combo, "Margin mode", margin_combo)
    self._grid_row(
        ticket,
        6,
        "Attach TP/SL",
        ttk.Checkbutton(ticket, variable=self.hyperliquid_perp_attach_tpsl_var),
        "Fee % / side",
        ttk.Entry(ticket, textvariable=self.hyperliquid_perp_fee_rate_var),
    )
    ttk.Label(ticket, text="Cancel order ID", style="Subtle.TLabel").grid(row=7, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
    ttk.Entry(ticket, textvariable=self.hyperliquid_perp_cancel_order_id_var).grid(row=7, column=1, columnspan=3, sticky="ew", pady=(8, 0))

    actions = ttk.LabelFrame(ticket, text="Perp Actions", style="Card.TLabelframe")
    actions.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    for column in range(3):
        actions.columnconfigure(column, weight=1, uniform="hyperliquid_actions")

    _add_workspace_button(actions, row=0, column=0, text="Use Mid", command=hyperliquid_action("perp", "use_hyperliquid_mid_market"), style="Accent.TButton")
    _add_workspace_button(actions, row=0, column=1, text="Perp What-If", command=hyperliquid_workspace_action("perp", "hyperliquid_crypto_scenarios_frame", "run_hyperliquid_perp_what_if"), style="Accent.TButton")
    _add_workspace_button(actions, row=0, column=2, text="Preview Perp Ticket", command=hyperliquid_action("perp", "preview_hyperliquid_ticket", "preview_order"))
    _add_workspace_button(actions, row=1, column=0, text="TP/SL", command=hyperliquid_action("perp", "show_hyperliquid_position_tpsl_dialog"))
    _add_workspace_button(actions, row=1, column=1, text="Edit Position", command=hyperliquid_action("perp", "show_hyperliquid_perp_position_editor"))
    _add_workspace_button(actions, row=1, column=2, text="Position Size", command=hyperliquid_action("perp", "show_hyperliquid_perp_position_size", "show_position_size"))
    _add_workspace_button(actions, row=2, column=0, text="Open Orders", command=hyperliquid_action("perp", "load_selected_open_orders_only", "load_hyperliquid_open_orders"))
    _add_workspace_button(actions, row=2, column=1, text="Edit Order", command=hyperliquid_action("perp", "show_hyperliquid_order_edit_dialog"))
    _add_workspace_button(actions, row=2, column=2, text="Cancel Order", command=hyperliquid_action("perp", "cancel_selected_order", "cancel_hyperliquid_order_guarded"), style="Danger.TButton")
    _add_workspace_button(actions, row=3, column=0, text="LIVE Submit", command=hyperliquid_action("perp", "submit_selected_venue"), style="Danger.TButton", columnspan=3)

    _set_workspace_text(
        self.hyperliquid_trading_preview_text,
        "HYPERLIQUID TRADING WORKSPACE\n"
        "=============================\n\n"
        "Use Mid pulls the current Hyperliquid allMids price into Entry / Limit.\n\n"
        "Spot What-If compares the spot ticket against the matching perp exposure so you can see whether a spot add/sell hedges or stacks the current position.\n\n"
        "Perp What-If compares your target and pain prices against the entry. It estimates gross P&L, fees, net P&L, account-risk-style ROI on margin, and a rough liquidation line.\n\n"
        "The rough liquidation line ignores maintenance margin, funding, slippage, partial fills, and account-wide margin. Treat it as a planning warning, not an exchange quote.",
    )


def _hyperliquid_selected_coin(self: tk.Tk) -> str:
    _ensure_execution_workspace_vars(self)
    raw = self.hyperliquid_coin_var.get().strip() or self.symbol_var.get().strip()
    coin = normalize_hyperliquid_coin(raw)
    self.hyperliquid_coin_var.set(coin)
    self.symbol_var.set(coin)
    return coin


def _lookup_hyperliquid_mid(coin: str) -> float:
    all_mids = HyperliquidInfoClient(timeout_seconds=10).post_info({"type": "allMids"})
    if not isinstance(all_mids, dict):
        raise RuntimeError("Hyperliquid allMids returned an unexpected response.")
    candidates = (coin, f"{coin}-PERP", f"{coin}/USDC")
    upper_mids = {str(key).upper(): value for key, value in all_mids.items()}
    for candidate in candidates:
        raw = upper_mids.get(candidate.upper())
        if raw is None:
            continue
        price = _to_float(raw)
        if price is not None and price > 0:
            return price
    raise RuntimeError(f"No Hyperliquid mid-market price found for {coin}. Try HYPE, BTC, ETH, or another listed coin.")


def _use_hyperliquid_mid_market(self: tk.Tk) -> None:
    try:
        coin = _hyperliquid_selected_coin(self)
        mid = _lookup_hyperliquid_mid(coin)
        self.limit_price_var.set(f"{mid:.4f}".rstrip("0").rstrip("."))
        self.hyperliquid_status_var.set(f"Hyperliquid: {coin} mid ${mid:,.4f}")
        self._set_preview_text(
            "HYPERLIQUID MID-MARKET PRICE\n"
            "============================\n\n"
            f"Coin: {coin}\n"
            f"Mid-market price: ${mid:,.4f}\n\n"
            "Entry / Limit was updated from Hyperliquid allMids. No order was submitted."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: mid failed")
        messagebox.showerror("Hyperliquid mid-market lookup failed", str(exc))


def _run_hyperliquid_perp_what_if(self: tk.Tk) -> None:
    try:
        coin = _hyperliquid_selected_coin(self)
        side = self.side_var.get().strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError("Direction must be buy or sell.")
        size = _required_float(self.quantity_var.get(), "Size")
        entry = _required_float(self.limit_price_var.get(), "Entry / Limit")
        leverage = _optional_float(self.hyperliquid_leverage_var.get(), default=1.0)
        fee_rate_percent = _optional_float(self.hyperliquid_fee_rate_var.get(), default=0.045)
        if size <= 0 or entry <= 0:
            raise ValueError("Size and Entry / Limit must be positive.")
        if leverage <= 0:
            raise ValueError("Leverage must be positive.")
        if fee_rate_percent < 0:
            raise ValueError("Fee % / side cannot be negative.")

        is_long = side == "buy"
        default_target = entry * (1.05 if is_long else 0.95)
        default_pain = entry * (0.97 if is_long else 1.03)
        target = _optional_float(self.hyperliquid_target_price_var.get(), default=default_target)
        pain = _optional_float(self.hyperliquid_bad_price_var.get() or self.stop_price_var.get(), default=default_pain)
        self.hyperliquid_target_price_var.set(_format_price(target))
        self.hyperliquid_bad_price_var.set(_format_price(pain))

        target_case = _perp_case(entry, target, size, is_long, leverage, fee_rate_percent)
        pain_case = _perp_case(entry, pain, size, is_long, leverage, fee_rate_percent)
        breakeven = _breakeven_price(entry, is_long, fee_rate_percent)
        notional = entry * size
        margin = notional / leverage
        rough_liq = _rough_liquidation_price(entry, is_long, leverage)
        rr = _risk_reward(target_case["net_pnl"], pain_case["net_pnl"])
        direction = "LONG" if is_long else "SHORT"
        favorable_word = "above" if is_long else "below"
        pain_word = "below" if is_long else "above"

        self.hyperliquid_status_var.set("Hyperliquid: what-if ready")
        self._set_preview_text(
            "HYPERLIQUID PERP WHAT-IF\n"
            "========================\n\n"
            "No order was submitted. This is a local scenario model for deciding whether the setup is worth taking.\n\n"
            f"Market: {coin}-PERP\n"
            f"Direction: {direction}\n"
            f"Size: {size:g} {coin}\n"
            f"Entry: ${entry:,.4f}\n"
            f"Notional: ${notional:,.2f}\n"
            f"Leverage used for margin math: {leverage:g}x\n"
            f"Estimated initial margin: ${margin:,.2f}\n"
            f"Fee estimate: {fee_rate_percent:g}% per side, entry + exit included\n\n"
            "Decision map:\n"
            f"- Good if price moves {favorable_word} entry toward target.\n"
            f"- Bad if price moves {pain_word} entry toward pain/stop.\n"
            f"- Fee-adjusted breakeven exit: ${breakeven:,.4f}\n"
            f"- Rough liquidation warning line: ${rough_liq:,.4f}\n\n"
            "Target scenario:\n"
            f"- Exit price: ${target:,.4f}\n"
            f"- Price move: {target_case['move_percent']:+.2f}%\n"
            f"- Gross P&L: ${target_case['gross_pnl']:+,.2f}\n"
            f"- Estimated fees: ${target_case['fees']:,.2f}\n"
            f"- Net P&L: ${target_case['net_pnl']:+,.2f}\n"
            f"- ROI on estimated margin: {target_case['margin_roi_percent']:+.2f}%\n\n"
            "Pain / stop scenario:\n"
            f"- Exit price: ${pain:,.4f}\n"
            f"- Price move: {pain_case['move_percent']:+.2f}%\n"
            f"- Gross P&L: ${pain_case['gross_pnl']:+,.2f}\n"
            f"- Estimated fees: ${pain_case['fees']:,.2f}\n"
            f"- Net P&L: ${pain_case['net_pnl']:+,.2f}\n"
            f"- ROI on estimated margin: {pain_case['margin_roi_percent']:+.2f}%\n\n"
            "Setup quality:\n"
            f"- Reward/risk using net P&L: {rr}\n"
            f"- Max modeled loss at pain/stop: ${min(pain_case['net_pnl'], 0):+,.2f}\n\n"
            "Formula notes:\n"
            "- Long P&L = (exit - entry) × size.\n"
            "- Short P&L = (entry - exit) × size.\n"
            "- Net P&L subtracts estimated entry and exit fees.\n"
            "- Rough liquidation ignores maintenance margin, funding, slippage, partial fills, and account-wide margin."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: what-if failed")
        messagebox.showerror("Hyperliquid perp what-if failed", str(exc))


def _perp_case(entry: float, exit_price: float, size: float, is_long: bool, leverage: float, fee_rate_percent: float) -> dict[str, float]:
    signed_move = (exit_price - entry) / entry
    gross_pnl = (exit_price - entry) * size if is_long else (entry - exit_price) * size
    fees = (entry * size + exit_price * size) * (fee_rate_percent / 100.0)
    net_pnl = gross_pnl - fees
    margin = (entry * size) / leverage
    margin_roi_percent = (net_pnl / margin * 100.0) if margin > 0 else 0.0
    return {
        "move_percent": signed_move * 100.0,
        "gross_pnl": gross_pnl,
        "fees": fees,
        "net_pnl": net_pnl,
        "margin_roi_percent": margin_roi_percent,
    }


def _breakeven_price(entry: float, is_long: bool, fee_rate_percent: float) -> float:
    fee = fee_rate_percent / 100.0
    if is_long:
        return entry * (1.0 + fee) / max(1.0 - fee, 0.000001)
    return entry * (1.0 - fee) / (1.0 + fee)


def _rough_liquidation_price(entry: float, is_long: bool, leverage: float) -> float:
    if leverage <= 1:
        return 0.0 if is_long else entry * 2.0
    return entry * (1.0 - 1.0 / leverage) if is_long else entry * (1.0 + 1.0 / leverage)


def _risk_reward(reward_net: float, pain_net: float) -> str:
    risk = abs(min(pain_net, 0.0))
    reward = max(reward_net, 0.0)
    if risk <= 0 or reward <= 0:
        return "n/a — target must be profitable and pain/stop must be a loss"
    return f"{reward / risk:.2f} : 1"


def _required_float(raw: str, label: str) -> float:
    value = _to_float(raw)
    if value is None:
        raise ValueError(f"{label} must be a number.")
    return value


def _optional_float(raw: str, *, default: float) -> float:
    value = _to_float(raw)
    return default if value is None else value


def _to_float(raw: object) -> float | None:
    try:
        text = str(raw).strip().replace(",", "")
        if text == "":
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _format_price(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _build_account_sources_panel(self: tk.Tk, parent: ttk.Frame) -> None:
    panel = ttk.LabelFrame(parent, text="Account Sources", style="Card.TLabelframe")
    panel.pack(fill=tk.X)
    panel.columnconfigure(0, weight=1)

    ttk.Label(
        panel,
        text=(
            "Schwab/current portfolio powers the Cockpit and Options What-If Lab. "
            "Hyperliquid can be synced from the Trade Planner."
        ),
        style="Subtle.TLabel",
        wraplength=1180,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))

    buttons = ttk.Frame(panel, style="Panel.TFrame")
    buttons.grid(row=1, column=0, sticky="ew", pady=(10, 0))
    for column in range(3):
        buttons.columnconfigure(column, weight=1, uniform="sources")

    ttk.Button(buttons, text="Connect Schwab", command=self.connect_schwab).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Refresh Schwab", command=lambda: _refresh_current_source(self)).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Use Schwab/Current", command=self.use_current_cockpit_source_portfolio, style="Accent.TButton").grid(row=0, column=2, sticky="ew")

    status = ttk.Frame(panel, style="Panel.TFrame")
    status.grid(row=2, column=0, sticky="ew", pady=(8, 0))
    status.columnconfigure(0, weight=1)
    ttk.Label(status, textvariable=self.active_portfolio_source_var, style="Chip.TLabel").grid(row=0, column=0, sticky="ew")


def _build_options_lab_market_loader(self: tk.Tk, parent: ttk.Frame) -> None:
    loader = ttk.LabelFrame(parent, text="Optional Schwab Technical Context Loader", style="Card.TLabelframe")
    loader.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
    loader.columnconfigure(0, weight=1)

    ttk.Label(
        loader,
        text=(
            "Pulls recent daily Schwab candles for the sandbox symbol and fills underlying price, RSI, "
            "20/50/200 SMA, ATR %, support, and resistance. No order preview or order submission is made."
        ),
        style="Subtle.TLabel",
        wraplength=860,
    ).grid(row=0, column=0, sticky="w", padx=(0, 12))
    ttk.Button(
        loader,
        text="Load Schwab Technicals",
        command=self.load_options_lab_technical_context,
        style="Accent.TButton",
    ).grid(row=0, column=1, sticky="e")


def _capture_current_source_portfolio(self: tk.Tk) -> None:
    try:
        self.cockpit_source_portfolio = self.broker.get_portfolio()
        self.cockpit_source_message = getattr(self.broker, "source_message", "Current cockpit portfolio")
    except Exception:
        return


def _refresh_current_source(self: tk.Tk) -> None:
    try:
        self.refresh_schwab_account()
    except Exception:
        self.refresh_portfolio()
    _capture_current_source_portfolio(self)
    self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
    _sync_options_values_from_active_portfolio(self)


def _use_current_cockpit_source_portfolio(self: tk.Tk) -> None:
    try:
        if self.cockpit_source_portfolio is None:
            _capture_current_source_portfolio(self)
        if self.cockpit_source_portfolio is None:
            raise RuntimeError("No current cockpit source portfolio is available yet.")

        self.broker.set_portfolio(self.cockpit_source_portfolio, self.cockpit_source_message)
        self.refresh_portfolio()
        self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
        _sync_options_values_from_active_portfolio(self)
    except Exception as exc:
        messagebox.showerror("Use current portfolio failed", str(exc))


def _sync_options_values_from_active_portfolio(self: tk.Tk) -> None:
    if not hasattr(self, "options_cash_available_var"):
        return
    try:
        portfolio = self.broker.get_portfolio()
        self.options_cash_available_var.set(f"{portfolio.cash:.2f}")
        self.options_portfolio_value_var.set(f"{portfolio.total_value:.2f}")
        position = portfolio.get_position(self.options_symbol_var.get())
        if position is not None:
            self.options_underlying_price_var.set(f"{position.last_price:.2f}")
        run_options_what_if(self)
    except Exception:
        return


def _load_options_lab_technical_context(self: tk.Tk) -> None:
    symbol = self.options_symbol_var.get().strip().upper()
    if not symbol:
        messagebox.showerror("Options lab technicals failed", "Enter a symbol first.")
        return

    try:
        session = self._authorize_schwab_session()
        if session is None:
            return

        status_code, payload = session.get_price_history(
            symbol,
            period_type="year",
            period=1,
            frequency_type="daily",
            frequency=1,
            need_extended_hours_data=False,
        )
        if status_code != 200:
            raise RuntimeError(f"Schwab daily price history returned HTTP {status_code}: {payload}")

        candles = candles_from_price_history(payload)
        report = analyze_candles(symbol, candles)
        levels = calculate_support_resistance(candles, lookback=50)
        closes = [candle.close for candle in candles]
        sma_200 = simple_moving_average(closes, 200)
        atr_percent = _average_true_range_percent(candles, period=14)

        self.options_underlying_price_var.set(f"{report.latest_close:.2f}")
        if report.rsi is not None:
            self.options_rsi_var.set(f"{report.rsi:.1f}")
        if report.sma_fast is not None:
            self.options_sma_20_var.set(f"{report.sma_fast:.2f}")
        if report.sma_slow is not None:
            self.options_sma_50_var.set(f"{report.sma_slow:.2f}")
        if sma_200 is not None:
            self.options_sma_200_var.set(f"{sma_200:.2f}")
        if levels.support is not None:
            self.options_support_var.set(f"{levels.support:.2f}")
        if levels.resistance is not None:
            self.options_resistance_var.set(f"{levels.resistance:.2f}")
        if atr_percent is not None:
            self.options_atr_var.set(f"{atr_percent:.2f}")

        self.schwab_status_var.set("Schwab session: connected")
        run_options_what_if(self)
    except Exception as exc:
        messagebox.showerror("Options lab technicals failed", str(exc))


def _average_true_range_percent(candles, *, period: int) -> float | None:
    if len(candles) <= period:
        return None

    true_ranges: list[float] = []
    previous_close = candles[0].close
    for candle in candles[1:]:
        true_range = max(
            candle.high - candle.low,
            abs(candle.high - previous_close),
            abs(candle.low - previous_close),
        )
        true_ranges.append(true_range)
        previous_close = candle.close

    recent_ranges = true_ranges[-period:]
    latest_close = candles[-1].close
    if not recent_ranges or latest_close <= 0:
        return None
    return (sum(recent_ranges) / len(recent_ranges) / latest_close) * 100
