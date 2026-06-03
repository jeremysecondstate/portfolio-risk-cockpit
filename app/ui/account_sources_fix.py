from __future__ import annotations
import traceback

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Type

from app.core.order_models import SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, TimeInForce, normalize_time_in_force
from app.ui.options_lab import (
    OPTION_TYPES,
    ORDER_TYPES,
    STRATEGIES,
    _analyze_scenario,
    _format_analysis,
    _init_options_vars,
    _parse_scenario,
)
from app.ui.options_lab_extension import (
    _bind_side_combobox_style,
    _build_hyperliquid_trading_tab,
    _build_schwab_trading_tab,
    _configure_side_combobox_styles,
    _ensure_execution_workspace_vars,
    _first_available_command,
    _run_workspace_action,
)
from app.ui.polished_theme import _make_paned


def install_account_sources_fix(app_cls: Type[tk.Tk]) -> None:
    """Keep Account Sources state without rendering a large top strip."""
    app_cls._build_layout = _build_layout_without_account_strip  # type: ignore[method-assign]
    app_cls.capture_current_portfolio_source = _capture_current_source_portfolio  # type: ignore[attr-defined]
    app_cls.sync_options_from_active_portfolio = _sync_options_values_from_active_portfolio  # type: ignore[attr-defined]
    app_cls.set_schwab_sync_status = _set_schwab_sync_status  # type: ignore[attr-defined]


def _build_layout_without_account_strip(self: tk.Tk) -> None:
    root = ttk.Frame(self, style="Canvas.TFrame", padding=18)
    root.pack(fill=tk.BOTH, expand=True)
    self._build_header(root)

    tabs = ttk.Notebook(root)
    tabs.pack(fill=tk.BOTH, expand=True, pady=(16, 0))

    cockpit_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=0)
    schwab_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    hyperliquid_tab = ttk.Frame(tabs, style="Canvas.TFrame", padding=14)
    tabs.add(cockpit_tab, text="Cockpit")
    tabs.add(schwab_tab, text="Schwab Trading")
    tabs.add(hyperliquid_tab, text="Hyperliquid Trading")

    self.active_portfolio_source_var = tk.StringVar(value="Active portfolio: current cockpit source")
    self.cockpit_source_portfolio = None
    self.cockpit_source_message = "Current cockpit portfolio"

    body = _make_paned(cockpit_tab, tk.HORIZONTAL)
    body.pack(fill=tk.BOTH, expand=True)

    left = ttk.Frame(body, style="Canvas.TFrame")
    right = ttk.Frame(body, style="Canvas.TFrame")
    body.add(left, minsize=560, stretch="always")
    body.add(right, minsize=520, stretch="always")
    self.after_idle(lambda: body.sash_place(0, max(600, int(self.winfo_width() * 0.60)), 0))

    self._build_portfolio_panel(left)
    self._build_order_panel(right)
    _ensure_execution_workspace_vars(self)
    self.after_idle(lambda: _capture_current_source_portfolio(self))

    _build_schwab_trading_tab(self, schwab_tab, tabs, schwab_tab)
    _install_schwab_options_feature(self, schwab_tab)
    _build_hyperliquid_trading_tab(self, hyperliquid_tab)


def _install_schwab_options_feature(self: tk.Tk, schwab_tab: ttk.Frame) -> None:
    """Flatten Schwab stock/ETF and option planning into one compact ticket."""

    _init_options_vars(self)

    ticket = _find_labelframe(schwab_tab, "Schwab Stock / ETF Ticket")
    if ticket is not None:
        _rebuild_schwab_ticket_side_by_side(self, ticket)

    for button in _walk_buttons(schwab_tab):
        label = str(button.cget("text"))
        if label == "Open Options Lab" and _inside_labelframe(button, "Schwab Trading Workspace"):
            button.configure(
                text="Sync Schwab",
                command=lambda app=self: _run_schwab_workspace_action(app, "refresh_schwab_account", "connect_schwab"),
                style="Accent.TButton",
            )
            _install_schwab_sync_status_badge(self, button)

    _set_schwab_mode_text(
        self,
        "SCHWAB TRADING WORKSPACE\n"
        "========================\n\n"
        "Use this single tab for stocks, ETFs, Schwab previews, order history, guarded live Schwab actions, and options what-if planning.\n\n"
        "The Stock / ETF ticket and Options Ticket Fields now sit side by side above the Schwab action grid, so the option inputs and guarded Schwab buttons stay visible together.\n\n"
        "Sync Schwab refreshes account balances and positions. Options Strategy now lives inside the Schwab Research + Risk Workspace next to Risk Scenarios.",
    )


def _rebuild_schwab_ticket_side_by_side(self: tk.Tk, ticket: ttk.LabelFrame) -> None:
    if getattr(self, "_schwab_ticket_side_by_side_built", False):
        return

    _configure_side_combobox_styles(self)

    for child in list(ticket.winfo_children()):
        child.destroy()

    ticket.columnconfigure(0, weight=1)
    ticket.rowconfigure(0, weight=0)
    ticket.rowconfigure(1, weight=0)
    ticket.rowconfigure(2, weight=0)

    ticket_fields = ttk.Frame(ticket, style="Panel.TFrame")
    ticket_fields.grid(row=0, column=0, sticky="ew")
    ticket_fields.columnconfigure(0, weight=1, uniform="schwab_ticket_columns")
    ticket_fields.columnconfigure(1, weight=1, uniform="schwab_ticket_columns")

    stock = ttk.LabelFrame(ticket_fields, text="Stock / ETF Ticket", style="Card.TLabelframe")
    stock.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
    stock.columnconfigure(1, weight=1)
    stock.columnconfigure(3, weight=1)

    side_combo = ttk.Combobox(stock, textvariable=self.side_var, values=["buy", "sell"], state="readonly")
    _bind_side_combobox_style(self.side_var, side_combo)
    self._grid_row(
        stock,
        0,
        "Symbol",
        ttk.Entry(stock, textvariable=self.symbol_var),
        "Side",
        side_combo,
    )
    self._grid_row(
        stock,
        1,
        "Order type",
        ttk.Combobox(stock, textvariable=self.order_type_var, values=["market", "limit", "stop", "stop_limit"], state="readonly"),
        "Time",
        ttk.Combobox(stock, textvariable=self.time_in_force_var, values=SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, state="readonly"),
    )
    self._grid_row(stock, 2, "Quantity", ttk.Entry(stock, textvariable=self.quantity_var), "Entry / Limit", ttk.Entry(stock, textvariable=self.limit_price_var))
    self._grid_row(
        stock,
        3,
        "Stop price",
        ttk.Entry(stock, textvariable=self.stop_price_var),
        "Use Mid",
        ttk.Button(
            stock,
            text="Use Mid",
            command=lambda app=self: _run_schwab_workspace_action(app, "use_schwab_mid_market", "use_selected_venue_mid_market"),
            style="Accent.TButton",
        ),
    )
    ttk.Label(stock, text="Cancel order ID", style="Subtle.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=5)
    ttk.Entry(stock, textvariable=self.cancel_order_id_var).grid(row=4, column=1, columnspan=3, sticky="ew", pady=5)

    options = ttk.LabelFrame(ticket_fields, text="Options Ticket Fields", style="Card.TLabelframe")
    options.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
    options.columnconfigure(1, weight=1)
    options.columnconfigure(3, weight=1)

    _grid_pair(
        options,
        0,
        "Strategy",
        ttk.Combobox(options, textvariable=self.options_strategy_var, values=STRATEGIES, state="readonly"),
        "Contracts",
        ttk.Entry(options, textvariable=self.options_contracts_var),
    )
    _grid_pair(options, 1, "Expiration", ttk.Entry(options, textvariable=self.options_expiration_var), "Strike", ttk.Entry(options, textvariable=self.options_strike_var))
    _grid_pair(
        options,
        2,
        "Call / Put",
        ttk.Combobox(options, textvariable=self.options_type_var, values=OPTION_TYPES, state="readonly"),
        "Bid",
        ttk.Entry(options, textvariable=self.options_bid_var),
    )
    _grid_pair(options, 3, "Ask", ttk.Entry(options, textvariable=self.options_ask_var), "Mark", ttk.Entry(options, textvariable=self.options_mark_var))
    _grid_pair(options, 4, "Limit / Debit", ttk.Entry(options, textvariable=self.options_premium_var), "Short strike", ttk.Entry(options, textvariable=self.options_short_strike_var))
    _grid_pair(options, 5, "Credit", ttk.Entry(options, textvariable=self.options_credit_var), "Target price", ttk.Entry(options, textvariable=self.options_target_price_var))

    _build_schwab_action_grid(self, ticket)
    self._schwab_ticket_side_by_side_built = True
    self._schwab_options_fields_integrated = True


def _direct_schwab_stock_live_submit_(self: tk.Tk) -> None:
    import json
    import sys
    import traceback
    from datetime import datetime, timezone

    def terminal(text: str) -> None:
        print("\n" + "=" * 80, file=sys.stderr, flush=True)
        print(text, file=sys.stderr, flush=True)
        print("=" * 80 + "\n", file=sys.stderr, flush=True)

    def pane(text: str) -> None:
        output = getattr(self, "schwab_trading_preview_text", None)
        if output is not None:
            try:
                _set_schwab_mode_text(self, text)
                return
            except Exception:
                pass
        setter = getattr(self, "_set_preview_text", None)
        if callable(setter):
            try:
                setter(text)
                return
            except Exception:
                pass
        terminal(text)

    def popup(title: str, text: str) -> None:
        try:
            self.bell()
        except Exception:
            pass
        try:
            messagebox.showinfo(title, text, parent=self)
        except Exception:
            messagebox.showinfo(title, text)

    try:
        symbol = self.symbol_var.get().strip().upper()
        side = self.side_var.get().strip().lower()
        quantity = int(float(self.quantity_var.get().strip()))
        limit_price = float(self.limit_price_var.get().strip())

        if not symbol:
            raise ValueError("Symbol is blank.")
        if side not in {"buy", "sell"}:
            raise ValueError(f"Side must be buy or sell. Got: {side!r}")
        if quantity <= 0:
            raise ValueError("Quantity must be positive.")
        if limit_price <= 0:
            raise ValueError("Limit price must be positive.")

        instruction = "BUY" if side == "buy" else "SELL"

        payload = {
            "orderType": "LIMIT",
            "session": "NORMAL",
            "duration": "DAY",
            "orderStrategyType": "SINGLE",
            "price": f"{limit_price:.2f}",
            "orderLegCollection": [
                {
                    "instruction": instruction,
                    "quantity": quantity,
                    "instrument": {
                        "symbol": symbol,
                        "assetType": "EQUITY",
                    },
                }
            ],
        }

        start_text = (
            "DIRECT SCHWAB LIVE SUBMIT DEBUG STARTED\n"
            "======================================\n\n"
            f"UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
            "This will call Schwab previewOrder first.\n"
            "If preview returns ACCEPTED, it will call Schwab submit_live_order.\n\n"
            "Payload:\n"
            f"{json.dumps(payload, indent=2)}"
        )
        terminal(start_text)
        pane(start_text)
        popup("Direct Schwab live submit debug", "Click received. Preview first, then submit only if Schwab returns ACCEPTED.")

        try:
            self.update_idletasks()
        except Exception:
            pass

        session = self._authorize_schwab_session()
        if session is None:
            raise RuntimeError("Schwab authorization returned no session.")

        preview_status_code, preview_payload = session.preview_order(payload)
        strategy = preview_payload.get("orderStrategy", {}) if isinstance(preview_payload, dict) else {}
        schwab_status = str(strategy.get("status") or "UNKNOWN").upper()

        preview_text = (
            "DIRECT SCHWAB LIVE SUBMIT PREVIEW RESULT\n"
            "========================================\n\n"
            f"HTTP Status: {preview_status_code}\n"
            f"Schwab Status: {schwab_status}\n\n"
            "Preview response:\n"
            f"{json.dumps(preview_payload, indent=2) if isinstance(preview_payload, (dict, list)) else str(preview_payload)}"
        )
        terminal(preview_text)
        pane(preview_text)

        if preview_status_code != 200 or schwab_status != "ACCEPTED":
            popup("Schwab preview blocked submit", f"Preview HTTP {preview_status_code}, Schwab status {schwab_status}. No order submitted.")
            return

        popup(
            "Preview ACCEPTED",
            "Schwab preview returned ACCEPTED.\n\n"
            "The app will now call submit_live_order(payload)."
        )

        submit_status_code, submit_payload, location = session.submit_live_order(payload)

        result_text = (
            "DIRECT SCHWAB LIVE SUBMIT RESULT\n"
            "================================\n\n"
            f"HTTP Status: {submit_status_code}\n"
            f"Location: {location or '(none returned)'}\n\n"
            "Submit response body:\n"
            f"{json.dumps(submit_payload, indent=2) if isinstance(submit_payload, (dict, list)) else str(submit_payload)}\n\n"
            "Now check Recent Orders / thinkorswim."
        )
        terminal(result_text)
        pane(result_text)
        popup("Schwab submit_live_order returned", f"HTTP Status: {submit_status_code}\nLocation: {location or '(none returned)'}")

    except Exception as exc:
        error_text = (
            "DIRECT SCHWAB LIVE SUBMIT DEBUG ERROR\n"
            "=====================================\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Traceback:\n"
            f"{traceback.format_exc()}"
        )
        terminal(error_text)
        pane(error_text)
        try:
            messagebox.showerror("Direct Schwab live submit error", f"{type(exc).__name__}: {exc}", parent=self)
        except Exception:
            messagebox.showerror("Direct Schwab live submit error", f"{type(exc).__name__}: {exc}")


def _build_schwab_action_grid(self: tk.Tk, ticket: ttk.LabelFrame) -> None:
    actions = ttk.LabelFrame(ticket, text="Schwab Actions", style="Card.TLabelframe")
    actions.grid(row=1, column=0, sticky="ew", pady=(12, 0))
    for column in range(3):
        actions.columnconfigure(column, weight=1, uniform="schwab_actions")

    def schwab_action(*names: str) -> Callable[[], None]:
        return lambda: _run_schwab_workspace_action(self, *names)

    _add_action_button(actions, row=0, column=0, text="Connect Schwab", command=schwab_action("connect_schwab", "run_schwab_preview"))
    _add_action_button(actions, row=0, column=1, text="Refresh Account", command=schwab_action("refresh_schwab_account", "refresh_portfolio"))
    _add_action_button(actions, row=0, column=2, text="Tech Analysis", command=schwab_action("show_technical_analysis"))
    _add_action_button(actions, row=1, column=0, text="Macro Refresh", command=schwab_action("refresh_macro_data"))
    _add_action_button(actions, row=1, column=1, text="Preview Schwab Order", command=schwab_action("run_schwab_preview"))
    _add_action_button(actions, row=1, column=2, text="Position Size", command=schwab_action("show_position_size"))
    _add_action_button(actions, row=2, column=0, text="Preview Risk", command=schwab_action("preview_order"), style="Accent.TButton")
    _add_action_button(actions, row=2, column=1, text="Open Only", command=schwab_action("load_selected_open_orders_only", "load_schwab_open_orders_only"))
    _add_action_button(actions, row=2, column=2, text="Options Strategy", command=schwab_action("show_technical_analysis"))
    _add_action_button(actions, row=3, column=0, text="Recent Orders", command=schwab_action("load_selected_recent_orders", "load_schwab_open_orders"))
    # _add_action_button(actions, row=3, column=2, text="LIVE Submit", command=lambda app=self: _run_schwab_workspace_action(app, "submit_live_schwab_order_guarded"),                       style="Danger.TButton")
    _add_action_button(actions, row=3, column=2, text="LIVE Submit", command=lambda app=self: _direct_schwab_stock_live_submit_(app), style="Danger.TButton")
    _add_action_button(actions, row=4, column=0, text="Cancel Order", command=schwab_action("cancel_selected_order", "show_cancel_order_placeholder"), style="Danger.TButton")


def _install_schwab_sync_status_badge(self: tk.Tk, sync_button: ttk.Button) -> None:
    parent = sync_button.master
    if parent is None:
        return

    _ensure_schwab_sync_status_vars(self)
    badge = getattr(self, "schwab_sync_status_badge", None)
    if badge is None:
        badge = tk.Label(
            parent,
            textvariable=self.schwab_sync_status_var,
            bg="#f1f5f9",
            fg="#475569",
            font=("Segoe UI", 9, "bold"),
            padx=8,
            pady=4,
            bd=0,
        )
        self.schwab_sync_status_badge = badge

    _apply_schwab_sync_status_colors(self)
    try:
        info = sync_button.grid_info()
        row = int(info.get("row", 0))
        column = int(info.get("column", 1))
        sticky = info.get("sticky", "e") or "e"
        parent.columnconfigure(column, weight=0)
        parent.columnconfigure(column + 1, weight=0)
        badge.grid(row=row, column=column, sticky="e", padx=(0, 8))
        sync_button.grid(row=row, column=column + 1, sticky=sticky)
    except (tk.TclError, ValueError):
        return


def _ensure_schwab_sync_status_vars(self: tk.Tk) -> None:
    if not hasattr(self, "schwab_sync_status_var"):
        self.schwab_sync_status_var = tk.StringVar(value="Sync status --")
    if not hasattr(self, "schwab_sync_status_state"):
        self.schwab_sync_status_state = "neutral"


def _set_schwab_sync_status(self: tk.Tk, status: str, message: str | None = None) -> None:
    _ensure_schwab_sync_status_vars(self)
    clean = status if status in {"success", "failure", "neutral"} else "neutral"
    self.schwab_sync_status_state = clean
    label = {
        "success": "✓ Synced",
        "failure": "✕ Sync failed",
        "neutral": "Sync status --",
    }[clean]
    self.schwab_sync_status_var.set(message or label)
    _apply_schwab_sync_status_colors(self)


def _apply_schwab_sync_status_colors(self: tk.Tk) -> None:
    badge = getattr(self, "schwab_sync_status_badge", None)
    if badge is None:
        return
    state = getattr(self, "schwab_sync_status_state", "neutral")
    colors = {
        "success": ("#dcfce7", "#047857"),
        "failure": ("#fee2e2", "#b91c1c"),
        "neutral": ("#f1f5f9", "#475569"),
    }.get(state, ("#f1f5f9", "#475569"))
    try:
        badge.configure(bg=colors[0], fg=colors[1])
    except tk.TclError:
        return


def _add_action_button(parent: ttk.Frame, *, row: int, column: int, text: str, command: Callable[[], None], style: str = "TButton") -> None:
    ttk.Button(parent, text=text, command=command, style=style).grid(
        row=row,
        column=column,
        sticky="ew",
        padx=(0 if column == 0 else 4, 0),
        pady=(0 if row == 0 else 6, 6),
        ipady=1,
    )


def _run_schwab_workspace_action(self: tk.Tk, *command_names: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    command = _first_available_command(self, *command_names)
    command_label = ", ".join(command_names) or "(none)"
    is_live_submit = "submit_live_schwab_order_guarded" in command_names

    def emit(text: str) -> None:
        if output is not None:
            _set_schwab_mode_text(self, text)
            return
        setter = getattr(self, "_set_preview_text", None)
        if callable(setter):
            setter(text)

    try:
        _ensure_execution_workspace_vars(self)

        if hasattr(self, "trade_venue_var"):
            self.trade_venue_var.set("Schwab")

        if hasattr(self, "on_trading_venue_changed"):
            try:
                self.on_trading_venue_changed()
            except Exception:
                # Venue repaint must not kill the actual Schwab action.
                pass

        if output is not None:
            self.preview_text = output

        if is_live_submit:
            emit(
                "SCHWAB LIVE SUBMIT CLICK RECEIVED\n"
                "================================\n\n"
                "The Schwab Trading tab button click reached Python.\n"
                "Command: submit_live_schwab_order_guarded\n\n"
                "Next step: running the guarded Schwab submit handler now.\n"
                "If this text stays here, the submit handler is hanging or blocking before it writes its own result."
            )
            try:
                self.update_idletasks()
            except Exception:
                pass

        command()

    except Exception as exc:
        details = traceback.format_exc()
        message = (
            "SCHWAB ACTION ERROR\n"
            "===================\n\n"
            f"Command(s): {command_label}\n"
            f"Exception: {type(exc).__name__}: {exc}\n\n"
            "Traceback:\n"
            f"{details}"
        )
        emit(message)
        messagebox.showerror("Schwab action failed", f"{type(exc).__name__}: {exc}")


def _run_schwab_integrated_options_what_if(self: tk.Tk) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is not None:
        self.preview_text = output

    try:
        _sync_integrated_options_values(self)
        scenario = _parse_scenario(self)
        analysis = _analyze_scenario(scenario, self)
        _set_schwab_mode_text(self, _format_analysis(scenario, analysis))
        if hasattr(self, "schwab_preview_status_var"):
            self.schwab_preview_status_var.set("Last Schwab preview: options what-if only")
    except Exception as exc:
        messagebox.showerror("Options what-if failed", str(exc))


def _sync_integrated_options_values(self: tk.Tk) -> None:
    if not hasattr(self, "options_symbol_var"):
        _init_options_vars(self)

    symbol = self.symbol_var.get().strip().upper()
    if symbol:
        self.options_symbol_var.set(symbol)

    side = self.side_var.get().strip().lower()
    self.options_action_var.set("Sell" if side == "sell" else "Buy")

    order_type = self.order_type_var.get().strip().upper()
    self.options_order_type_var.set(order_type if order_type in ORDER_TYPES else "LIMIT")

    tif = normalize_time_in_force(self.time_in_force_var.get())
    self.options_tif_var.set("GTC" if tif in {TimeInForce.GTC, TimeInForce.GTC_EXT} else "Day")

    quantity = self.quantity_var.get().strip()
    if quantity:
        self.options_quantity_var.set(quantity)

    stop_price = self.stop_price_var.get().strip()
    if stop_price:
        self.options_stop_price_var.set(stop_price)

    try:
        portfolio = self.broker.get_portfolio()
        self.options_cash_available_var.set(f"{portfolio.cash:.2f}")
        self.options_portfolio_value_var.set(f"{portfolio.total_value:.2f}")
        position = portfolio.get_position(symbol)
        if position is not None:
            self.options_underlying_price_var.set(f"{position.last_price:.2f}")
    except Exception:
        return


def _set_schwab_mode_text(self: tk.Tk, content: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is None:
        return
    try:
        output.configure(state=tk.NORMAL)
        output.delete("1.0", tk.END)
        output.insert(tk.END, content)
        output.configure(state=tk.DISABLED)
    except Exception:
        return


def _grid_pair(parent: ttk.Frame, row: int, label_a: str, widget_a: tk.Widget, label_b: str, widget_b: tk.Widget) -> None:
    ttk.Label(parent, text=label_a, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 6), pady=4)
    widget_a.grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=4)
    ttk.Label(parent, text=label_b, style="Subtle.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 6), pady=4)
    widget_b.grid(row=row, column=3, sticky="ew", pady=4)


def _find_labelframe(root: tk.Widget, title: str) -> ttk.LabelFrame | None:
    if _widget_class(root) == "TLabelframe":
        try:
            if str(root.cget("text")) == title:
                return root  # type: ignore[return-value]
        except Exception:
            pass
    for child in root.winfo_children():
        found = _find_labelframe(child, title)
        if found is not None:
            return found
    return None


def _walk_buttons(root: tk.Widget):
    for child in root.winfo_children():
        if _widget_class(child) == "TButton":
            yield child
        yield from _walk_buttons(child)


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


def _widget_class(widget: tk.Widget) -> str:
    try:
        return str(widget.winfo_class())
    except Exception:
        return ""


def _capture_current_source_portfolio(self: tk.Tk) -> None:
    try:
        self.cockpit_source_portfolio = self.broker.get_portfolio()
        self.cockpit_source_message = getattr(self.broker, "source_message", "Current cockpit portfolio")
        if hasattr(self, "active_portfolio_source_var"):
            self.active_portfolio_source_var.set(f"Active portfolio: {self.cockpit_source_message}")
    except Exception:
        return


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
        _run_schwab_integrated_options_what_if(self)
    except Exception:
        return
