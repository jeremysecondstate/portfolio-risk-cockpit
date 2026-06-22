from __future__ import annotations
import json
import re
import traceback

from datetime import datetime, timedelta, timezone
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Type

from app.core.order_models import (
    SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES,
    TimeInForce,
    normalize_time_in_force,
    schwab_equity_session_duration,
    schwab_equity_tif_from_session_duration,
    schwab_equity_tif_requires_limit_order,
)
from app.ui.trading_workspace import (
    OPTION_TYPES,
    ORDER_TYPES,
    STRATEGIES,
    _analyze_scenario,
    _format_analysis,
    _init_options_vars,
    _parse_scenario,
    use_mid_as_limit,
)
from app.ui.trading_workspace_extension import (
    _bind_side_combobox_style,
    _build_hyperliquid_trading_tab,
    _build_schwab_trading_tab,
    _bind_workspace_holdings_click,
    _configure_side_combobox_styles,
    _ensure_execution_workspace_vars,
    _first_available_command,
    _workspace_holdings_table,
)
from app.ui import polished_theme
from app.ui.polished_theme import _make_paned
from app.ui.venue_mid_extension import _extract_schwab_quote, _first_number, _format_optional_price, _format_price


def install_schwab_trading_tab(app_cls: Type[tk.Tk]) -> None:
    """Keep Account Sources state without rendering a large top strip."""
    app_cls._build_layout = _build_layout_without_account_strip  # type: ignore[method-assign]
    app_cls.build_schwab_order_json_from_ui = _build_schwab_stock_order_json_from_ui  # type: ignore[method-assign]
    app_cls.capture_current_portfolio_source = _capture_current_source_portfolio  # type: ignore[attr-defined]
    app_cls.sync_options_from_active_portfolio = _sync_options_values_from_active_portfolio  # type: ignore[attr-defined]
    app_cls.set_schwab_sync_status = _set_schwab_sync_status  # type: ignore[attr-defined]
    app_cls.refresh_schwab_open_orders_tab = _refresh_schwab_open_orders_tab  # type: ignore[attr-defined]
    app_cls.refresh_schwab_recent_orders_tab = _refresh_schwab_recent_orders_tab  # type: ignore[attr-defined]
    app_cls.open_selected_schwab_order_editor = _open_selected_schwab_order_editor  # type: ignore[attr-defined]
    app_cls.cancel_selected_schwab_open_order = _cancel_selected_schwab_open_order  # type: ignore[attr-defined]


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

    _install_schwab_account_tabs(self, schwab_tab)

    for button in _walk_buttons(schwab_tab):
        label = str(button.cget("text"))
        if label == "Open Trading Workspace" and _inside_labelframe(button, "Schwab Trading Workspace"):
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
    _ensure_schwab_stock_ticket_vars(self)

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
    stock.grid(row=0, column=0, sticky="new", padx=(0, 8))
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
        "TIF",
        ttk.Combobox(stock, textvariable=self.time_in_force_var, values=SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, state="readonly"),
    )
    self._grid_row(
        stock,
        2,
        "Position effect",
        ttk.Combobox(stock, textvariable=self.schwab_stock_position_effect_var, values=["AUTO", "OPENING", "CLOSING"], state="readonly"),
        "Quantity",
        ttk.Entry(stock, textvariable=self.quantity_var),
    )
    self._grid_row(
        stock,
        3,
        "Entry / Limit",
        ttk.Entry(stock, textvariable=self.limit_price_var),
        "Stop price",
        ttk.Entry(stock, textvariable=self.stop_price_var),
    )
    self._grid_row(
        stock,
        4,
        "Use Mid",
        ttk.Button(
            stock,
            text="Use Mid",
            command=lambda app=self: _run_schwab_workspace_action(app, "use_schwab_mid_market", "use_selected_venue_mid_market"),
            style="Accent.TButton",
        ),
        "Cancel order ID",
        ttk.Entry(stock, textvariable=self.cancel_order_id_var),
    )

    options = ttk.LabelFrame(ticket_fields, text="Options Ticket Fields", style="Card.TLabelframe")
    options.grid(row=0, column=1, sticky="new", padx=(8, 0))
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
    limit_box = ttk.Frame(options, style="Panel.TFrame")
    limit_box.columnconfigure(0, weight=1)
    ttk.Entry(limit_box, textvariable=self.options_premium_var).grid(row=0, column=0, sticky="ew")
    ttk.Button(limit_box, text="Use Mid", command=lambda app=self: use_mid_as_limit(app), style="Accent.TButton").grid(row=0, column=1, sticky="e", padx=(6, 0))
    _grid_pair(options, 4, "Limit / Debit", limit_box, "Short strike", ttk.Entry(options, textvariable=self.options_short_strike_var))
    _grid_pair(options, 5, "Credit", ttk.Entry(options, textvariable=self.options_credit_var), "Target price", ttk.Entry(options, textvariable=self.options_target_price_var))

    _build_schwab_action_grid(self, ticket)
    self._schwab_ticket_side_by_side_built = True
    self._schwab_options_fields_integrated = True


def _ensure_schwab_stock_ticket_vars(self: tk.Tk) -> None:
    if not hasattr(self, "schwab_stock_position_effect_var"):
        self.schwab_stock_position_effect_var = tk.StringVar(value="AUTO")
    if not hasattr(self, "schwab_stock_session_var"):
        self.schwab_stock_session_var = tk.StringVar(value="NORMAL")


def _build_schwab_stock_order_json_from_ui(self: tk.Tk) -> dict[str, Any]:
    _ensure_schwab_stock_ticket_vars(self)

    symbol = _required_text_var(self, "symbol_var", "Symbol").upper()
    side = _required_text_var(self, "side_var", "Side").lower()
    if side not in {"buy", "sell"}:
        raise ValueError(f"Side must be buy or sell. Got: {side!r}")

    quantity = _required_positive_float(_get_string_var(self, "quantity_var"), "Quantity")
    order_type = _supported_stock_order_type(_get_string_var(self, "order_type_var"))
    limit_price = _optional_positive_float(_get_string_var(self, "limit_price_var"), "Entry / Limit")
    stop_price = _optional_positive_float(_get_string_var(self, "stop_price_var"), "Stop price")

    tif = _supported_duration(_get_string_var(self, "time_in_force_var"))
    _validate_schwab_equity_tif_order_type(tif, order_type)
    session, duration = schwab_equity_session_duration(tif)
    position_effect = _normalized_stock_position_effect(_get_string_var(self, "schwab_stock_position_effect_var"))

    if order_type in {"LIMIT", "STOP_LIMIT"} and limit_price is None:
        raise ValueError("Entry / Limit is required for LIMIT and STOP_LIMIT stock orders.")
    if order_type in {"STOP", "STOP_LIMIT"} and stop_price is None:
        raise ValueError("Stop price is required for STOP and STOP_LIMIT stock orders.")
    if order_type not in {"STOP", "STOP_LIMIT"} and stop_price is not None:
        raise ValueError("Stop price can only be used with STOP or STOP_LIMIT stock orders. Clear Stop price or change Order type.")

    leg: dict[str, Any] = {
        "instruction": "BUY" if side == "buy" else "SELL",
        "quantity": quantity,
        "instrument": {
            "symbol": symbol,
            "assetType": "EQUITY",
        },
    }
    if position_effect:
        leg["positionEffect"] = position_effect

    payload: dict[str, Any] = {
        "orderType": order_type,
        "session": session,
        "duration": duration,
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [leg],
    }
    if limit_price is not None and order_type in {"LIMIT", "STOP_LIMIT"}:
        payload["price"] = f"{limit_price:.2f}"
    if stop_price is not None and order_type in {"STOP", "STOP_LIMIT"}:
        payload["stopPrice"] = f"{stop_price:.2f}"

    return payload


def _get_string_var(self: tk.Tk, name: str) -> str:
    var = getattr(self, name, None)
    try:
        return str(var.get()).strip()
    except Exception:
        return ""


def _required_text_var(self: tk.Tk, name: str, label: str) -> str:
    value = _get_string_var(self, name)
    if not value:
        raise ValueError(f"{label} is required.")
    return value


def _required_positive_float(value: str, label: str) -> float:
    parsed = _optional_positive_float(value, label)
    if parsed is None:
        raise ValueError(f"{label} is required.")
    return parsed


def _optional_positive_float(value: str, label: str) -> float | None:
    clean = str(value or "").strip().replace("$", "").replace(",", "")
    if not clean:
        return None
    try:
        parsed = float(clean)
    except ValueError as exc:
        raise ValueError(f"{label} must be a number.") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be positive.")
    return parsed


def _supported_stock_order_type(value: str) -> str:
    clean = str(value or "limit").strip().upper().replace(" ", "_")
    if clean in {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"}:
        return clean
    raise ValueError(f"Unsupported Schwab stock order type: {value!r}")


def _normalized_stock_position_effect(value: str) -> str:
    clean = str(value or "AUTO").strip().upper().replace(" ", "_")
    aliases = {
        "AUTO": "",
        "AUTOMATIC": "",
        "NONE": "",
        "": "",
        "OPEN": "OPENING",
        "TO_OPEN": "OPENING",
        "OPENING": "OPENING",
        "CLOSE": "CLOSING",
        "TO_CLOSE": "CLOSING",
        "CLOSING": "CLOSING",
    }
    if clean not in aliases:
        raise ValueError(f"Unsupported Schwab position effect: {value!r}")
    return aliases[clean]


def _display_stock_position_effect(value: str) -> str:
    normalized = _normalized_stock_position_effect(value)
    return normalized or "AUTO"


def _stock_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    legs = payload.get("orderLegCollection") or []
    first_leg = legs[0] if legs and isinstance(legs[0], dict) else {}
    instrument = first_leg.get("instrument") if isinstance(first_leg.get("instrument"), dict) else {}
    return {
        "symbol": str(instrument.get("symbol") or ""),
        "instruction": str(first_leg.get("instruction") or ""),
        "quantity": first_leg.get("quantity") or 0,
        "position_effect": str(first_leg.get("positionEffect") or ""),
    }


def _format_stock_order_summary(payload: dict[str, Any]) -> str:
    summary = _stock_payload_summary(payload)
    pieces = [
        str(summary["instruction"]),
        f"{float(summary['quantity']):g}",
        str(summary["symbol"]),
        str(payload.get("orderType") or ""),
    ]
    if payload.get("price") is not None:
        pieces.append(f"limit {payload['price']}")
    if payload.get("stopPrice") is not None:
        pieces.append(f"stop {payload['stopPrice']}")
    pieces.append(str(payload.get("duration") or ""))
    pieces.append(str(payload.get("session") or ""))
    return " ".join(piece for piece in pieces if piece)


def _estimated_stock_order_value(payload: dict[str, Any]) -> float | str:
    summary = _stock_payload_summary(payload)
    quantity = float(summary.get("quantity") or 0.0)
    for key in ("price", "stopPrice"):
        try:
            value = float(str(payload.get(key) or "").replace(",", ""))
        except ValueError:
            continue
        if value > 0:
            return quantity * value
    return "--"


def _submit_schwab_live_order(self: tk.Tk) -> None:
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

    def show_error(title: str, text: str) -> None:
        try:
            messagebox.showerror(title, text, parent=self)
        except Exception:
            messagebox.showerror(title, text)

    def show_info(title: str, text: str) -> None:
        try:
            messagebox.showinfo(title, text, parent=self)
        except Exception:
            messagebox.showinfo(title, text)

    def ask_submit(title: str, text: str) -> bool:
        try:
            return bool(messagebox.askokcancel(title, text, parent=self))
        except Exception:
            return bool(messagebox.askokcancel(title, text))

    def money(value: object) -> str:
        if isinstance(value, (int, float)):
            return f"${value:,.2f}"
        return str(value if value not in (None, "") else "--")

    try:
        payload = _build_schwab_stock_order_json_from_ui(self)
        order_summary = _stock_payload_summary(payload)
        symbol = order_summary["symbol"]
        instruction = order_summary["instruction"]
        quantity = float(order_summary["quantity"])

        start_text = (
            "SCHWAB LIVE SUBMIT PREVIEW\n"
            "==========================\n\n"
            f"UTC: {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n"
            f"Order: {_format_stock_order_summary(payload)}\n\n"
            "Calling Schwab previewOrder..."
        )
        terminal(start_text)
        pane(start_text)

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

        order_balance = strategy.get("orderBalance", {}) if isinstance(strategy, dict) else {}
        order_value = order_balance.get("orderValue", _estimated_stock_order_value(payload))
        buying_power = order_balance.get("projectedBuyingPower", "--")
        available_funds = order_balance.get("projectedAvailableFund", "--")
        commission = order_balance.get("projectedCommission", "--")

        preview_text = (
            "SCHWAB LIVE ORDER PREVIEW\n"
            "=========================\n\n"
            f"Action:        {instruction}\n"
            f"Symbol:        {symbol}\n"
            f"Quantity:      {quantity:g}\n"
            f"Order type:    {payload.get('orderType', '--')}\n"
            f"Limit price:   {money(payload.get('price'))}\n"
            f"Stop price:    {money(payload.get('stopPrice'))}\n"
            f"Duration:      {payload.get('duration', '--')}\n"
            f"Session:       {payload.get('session', '--')}\n"
            f"Position eff.: {order_summary.get('position_effect') or 'AUTO'}\n\n"
            "Schwab preview:\n"
            f"- HTTP status: {preview_status_code}\n"
            f"- Schwab status: {schwab_status}\n"
            f"- Order value: {money(order_value)}\n"
            f"- Commission: {money(commission)}\n"
            f"- Buying power after: {money(buying_power)}\n"
            f"- Available funds after: {money(available_funds)}\n\n"
            "Ready to submit. Confirm in the popup to place the live order."
        )
        terminal(
            "SCHWAB LIVE SUBMIT PREVIEW RAW RESPONSE\n"
            "=======================================\n\n"
            f"{json.dumps(preview_payload, indent=2) if isinstance(preview_payload, (dict, list)) else str(preview_payload)}"
        )
        pane(preview_text)

        if preview_status_code != 200 or schwab_status != "ACCEPTED":
            show_error(
                "Schwab preview blocked submit",
                f"Preview HTTP {preview_status_code}, Schwab status {schwab_status}.\n\nNo order was submitted.",
            )
            return

        confirm_text = (
            f"{_format_stock_order_summary(payload)}\n"
            f"Position effect: {order_summary.get('position_effect') or 'AUTO'}\n"
            f"Estimated value: {order_value}\n\n"
            "Submit this order?"
        )

        if not ask_submit("Submit Schwab order?", confirm_text):
            pane(preview_text + "\n\nSubmit canceled by user. No live order was placed.")
            terminal("Submit canceled by user. No live order was placed.")
            return

        submit_status_code, submit_payload, location = session.submit_live_order(payload)

        result_text = (
            "SCHWAB LIVE ORDER SUBMITTED\n"
            "===========================\n\n"
            f"Action:      {instruction}\n"
            f"Symbol:      {symbol}\n"
            f"Quantity:    {quantity:g}\n"
            f"Order:       {_format_stock_order_summary(payload)}\n\n"
            f"HTTP status: {submit_status_code}\n"
            f"Order URL:   {location or '(none returned)'}\n\n"
            "Next: use Recent Orders, Open Only, or thinkorswim to verify final order status."
        )
        terminal(
            "SCHWAB LIVE SUBMIT RAW RESULT\n"
            "============================\n\n"
            f"Vibe: {submit_status_code}\n"
            f"Location: {location or '(none returned)'}\n"
            f"Body: {json.dumps(submit_payload, indent=2) if isinstance(submit_payload, (dict, list)) else str(submit_payload)}"
        )
        pane(result_text)

        if 200 <= submit_status_code < 300:
            show_info(
                "Schwab order submitted",
                f"{_format_stock_order_summary(payload)}\n\n"
                f"Vibe: {submit_status_code}\n"
                "Check Recent Orders or thinkorswim for final status.",
            )
        else:
            show_error(
                "Schwab submit returned non-2xx",
                f"Vibe: {submit_status_code}\n\nCheck the Schwab output pane.",
            )

    except Exception as exc:
        error_text = (
            "SCHWAB LIVE SUBMIT ERROR\n"
            "========================\n\n"
            f"{type(exc).__name__}: {exc}\n\n"
            "Traceback:\n"
            f"{traceback.format_exc()}"
        )
        terminal(error_text)
        pane(error_text)
        show_error("Schwab live submit error", f"{type(exc).__name__}: {exc}")


def _build_schwab_action_grid(self: tk.Tk, ticket: ttk.LabelFrame) -> None:
    actions = ttk.LabelFrame(ticket, text="Schwab Actions", style="Card.TLabelframe")
    actions.grid(row=1, column=0, sticky="ew", pady=(8, 0))
    setattr(actions, "_company_reports_installed", True)
    for column in range(4):
        actions.columnconfigure(column, weight=1, uniform="schwab_actions")

    def schwab_action(*names: str) -> Callable[[], None]:
        return lambda: _run_schwab_workspace_action(self, *names)

    _add_action_button(actions, row=0, column=3, text="Tech Analysis", command=schwab_action("show_technical_analysis"))
    _add_action_button(actions, row=1, column=3, text="Preview Schwab", command=schwab_action("run_schwab_preview"))
    _add_action_button(actions, row=0, column=2, text="Recent Orders", command=lambda app=self: _refresh_schwab_recent_orders_tab(app))
    _add_action_button(actions, row=1, column=2, text="Open Only", command=schwab_action("load_selected_open_orders_only", "load_schwab_open_orders_only"))

    show_ipo_pipeline = getattr(self, "show_ipo_pipeline", None)
    if callable(show_ipo_pipeline):
        _add_action_button(actions, row=3, column=3, text="IPO Pipeline", command=show_ipo_pipeline, style="Accent.TButton")
        setattr(actions, "_ipo_pipeline_button_installed", True)

    _add_action_button(actions, row=2, column=2, text="Cancel Order", command=schwab_action("cancel_selected_order", "show_cancel_order_placeholder"), style="Danger.TButton")
    _add_action_button(actions, row=2, column=3, text="LIVE Submit", command=lambda app=self: _submit_schwab_live_order(app), style="Danger.TButton")


def _selected_schwab_position_symbol(self: tk.Tk) -> str:
    table = getattr(self, "schwab_workspace_holdings_table", None)
    if table is None:
        return ""
    try:
        selection = table.selection()
    except Exception:
        return ""
    if not selection:
        return ""
    try:
        raw_values = table.item(selection[0], "values")
        columns = tuple(table["columns"])
    except Exception:
        return ""
    values = {str(column): str(raw_values[index]) for index, column in enumerate(columns) if index < len(raw_values)}
    if str(values.get("type", "")).strip().lower() == "cash":
        return ""
    return values.get("symbol", "")


def _clean_symbol_chat_symbol(value: object) -> str:
    clean = str(value or "").strip().upper()
    if not clean:
        return ""
    if "(" in clean:
        clean = clean.split("(", 1)[0].strip()
    if clean.startswith("HL:"):
        clean = clean[3:]
    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if clean.endswith(suffix):
            clean = clean[: -len(suffix)]
    clean = re.sub(r"\s+", "", clean)
    clean = re.sub(r"[^A-Z0-9.\-_/]", "", clean)
    return clean


def _install_schwab_account_tabs(self: tk.Tk, schwab_tab: ttk.Frame) -> None:
    if getattr(self, "_schwab_account_tabs_built", False):
        return

    account_frame = _find_labelframe(schwab_tab, "Schwab Holdings")
    if account_frame is None:
        return

    try:
        account_frame.configure(text="Schwab Account")
    except tk.TclError:
        pass

    for child in list(account_frame.winfo_children()):
        child.destroy()

    notebook = ttk.Notebook(account_frame)
    notebook.pack(fill=tk.BOTH, expand=True)
    self.schwab_account_tabs = notebook

    holdings_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=8)
    open_orders_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=8)
    recent_orders_tab = ttk.Frame(notebook, style="Panel.TFrame", padding=8)
    notebook.add(holdings_tab, text="Holdings")
    notebook.add(open_orders_tab, text="Open Orders")
    notebook.add(recent_orders_tab, text="Recent Orders")

    self.schwab_workspace_holdings_table = _workspace_holdings_table(holdings_tab)
    _bind_workspace_holdings_click(self, self.schwab_workspace_holdings_table, "Schwab")

    self.schwab_open_orders_table = _build_schwab_orders_table(open_orders_tab)
    _build_schwab_open_orders_controls(self, open_orders_tab)

    self.schwab_recent_orders_table = _build_schwab_orders_table(recent_orders_tab)
    _build_schwab_recent_orders_controls(self, recent_orders_tab)

    self.schwab_open_orders_by_iid = {}
    self.schwab_recent_orders_by_iid = {}
    self._schwab_account_tabs_built = True


def _build_schwab_orders_table(parent: ttk.Frame) -> ttk.Treeview:
    parent.rowconfigure(1, weight=1)
    parent.columnconfigure(0, weight=1)

    columns = ("time", "order_id", "symbol", "side", "qty", "effect", "type", "limit", "stop", "tif", "session", "status", "account")
    table = ttk.Treeview(parent, columns=columns, show="headings", height=7, selectmode="browse")
    headings = {
        "time": ("Time", 138, tk.W),
        "order_id": ("Order ID", 118, tk.W),
        "symbol": ("Symbol", 82, tk.W),
        "side": ("Side", 86, tk.W),
        "qty": ("Qty", 72, tk.E),
        "effect": ("Pos Effect", 92, tk.W),
        "type": ("Order", 92, tk.W),
        "limit": ("Limit", 78, tk.E),
        "stop": ("Stop", 78, tk.E),
        "tif": ("TIF", 78, tk.W),
        "session": ("Session", 86, tk.W),
        "status": ("Status", 108, tk.W),
        "account": ("Account", 110, tk.W),
    }
    for column, (label, width, anchor) in headings.items():
        table.heading(column, text=label)
        table.column(column, width=width, anchor=anchor, stretch=column in {"time", "order_id", "symbol", "status"})
    table.grid(row=1, column=0, sticky="nsew")
    y_scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=table.yview)
    y_scroll.grid(row=1, column=1, sticky="ns")
    x_scroll = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=table.xview)
    x_scroll.grid(row=2, column=0, sticky="ew")
    table.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
    table.tag_configure("active", foreground=polished_theme.POSITIVE)
    table.tag_configure("terminal", foreground=polished_theme.MUTED)
    table.tag_configure("error", foreground=polished_theme.NEGATIVE)
    return table


def _build_schwab_open_orders_controls(self: tk.Tk, parent: ttk.Frame) -> None:
    controls = ttk.Frame(parent, style="Panel.TFrame")
    controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    controls.columnconfigure(3, weight=1)
    ttk.Button(controls, text="Refresh Open Orders", command=lambda app=self: _refresh_schwab_open_orders_tab(app), style="Accent.TButton").grid(row=0, column=0, sticky="w")
    ttk.Button(controls, text="Edit Selected", command=lambda app=self: _open_selected_schwab_order_editor(app, source="open")).grid(row=0, column=1, sticky="w", padx=(8, 0))
    ttk.Button(controls, text="Cancel Order", command=lambda app=self: _cancel_selected_schwab_open_order(app), style="Danger.TButton").grid(row=0, column=2, sticky="w", padx=(8, 0))
    ttk.Label(controls, text="Double-click a working order to edit/replace.", style="Subtle.TLabel").grid(row=0, column=3, sticky="e")

    table = self.schwab_open_orders_table
    table.bind("<Double-1>", lambda _event, app=self: _open_selected_schwab_order_editor(app, source="open"), add="+")
    table.bind("<ButtonRelease-1>", lambda _event, app=self: _load_ticket_from_selected_schwab_order(app, source="open"), add="+")


def _build_schwab_recent_orders_controls(self: tk.Tk, parent: ttk.Frame) -> None:
    controls = ttk.Frame(parent, style="Panel.TFrame")
    controls.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
    controls.columnconfigure(2, weight=1)
    ttk.Button(controls, text="Refresh Recent Orders", command=lambda app=self: _refresh_schwab_recent_orders_tab(app), style="Accent.TButton").grid(row=0, column=0, sticky="w")
    ttk.Button(controls, text="Details / Edit", command=lambda app=self: _open_selected_schwab_order_editor(app, source="recent")).grid(row=0, column=1, sticky="w", padx=(8, 0))
    ttk.Label(controls, text="Double-click working rows to edit; terminal rows open read-only details.", style="Subtle.TLabel").grid(row=0, column=2, sticky="e")

    table = self.schwab_recent_orders_table
    table.bind("<Double-1>", lambda _event, app=self: _open_selected_schwab_order_editor(app, source="recent"), add="+")
    table.bind("<ButtonRelease-1>", lambda _event, app=self: _load_ticket_from_selected_schwab_order(app, source="recent"), add="+")


def _refresh_schwab_open_orders_tab(self: tk.Tk) -> None:
    _select_schwab_account_tab(self, "Open Orders")
    try:
        status_code, payload = _fetch_schwab_orders(self)
        active_statuses = _active_order_statuses(self)
        orders = [order for order in payload if isinstance(order, dict) and str(order.get("status", "")).upper() in active_statuses] if isinstance(payload, list) else []
        _populate_schwab_orders_table(self.schwab_open_orders_table, orders, cache_attr="schwab_open_orders_by_iid", active_statuses=active_statuses)
        if status_code == 200:
            self.open_only_verified_this_session = True
            updater = getattr(self, "_update_verification_status", None)
            if callable(updater):
                updater()
        _set_schwab_mode_text(
            self,
            "SCHWAB OPEN ORDERS\n"
            "==================\n\n"
            f"Vibe: {status_code}\n"
            f"Active orders loaded: {len(orders)}\n\n"
            "Select an open order to populate the ticket. Double-click to edit/replace, or use Cancel Order in the Open Orders tab.",
        )
    except Exception as exc:
        _write_schwab_order_error(self, "Refresh Schwab open orders failed", exc)
        messagebox.showerror("Refresh open orders failed", str(exc))


def _refresh_schwab_recent_orders_tab(self: tk.Tk) -> None:
    _select_schwab_account_tab(self, "Recent Orders")
    try:
        status_code, payload = _fetch_schwab_orders(self)
        orders = payload if isinstance(payload, list) else []
        _populate_schwab_orders_table(self.schwab_recent_orders_table, orders, cache_attr="schwab_recent_orders_by_iid", active_statuses=_active_order_statuses(self))
        _set_schwab_mode_text(
            self,
            "SCHWAB RECENT ORDERS\n"
            "====================\n\n"
            f"Vibe: {status_code}\n"
            f"Recent orders loaded: {len(orders)}\n\n"
            "Working/open orders can be edited from this tab. Filled, canceled, rejected, and expired orders open read-only details.",
        )
    except Exception as exc:
        _write_schwab_order_error(self, "Refresh Schwab recent orders failed", exc)
        messagebox.showerror("Refresh recent orders failed", str(exc))


def _fetch_schwab_orders(self: tk.Tk) -> tuple[int, Any]:
    session = self._authorize_schwab_session()
    if session is None:
        raise RuntimeError("Schwab authorization returned no session.")
    to_time = datetime.now(timezone.utc)
    from_time = to_time - timedelta(days=7)
    status_code, payload = session.get_orders(from_entered_time=from_time, to_entered_time=to_time)
    if hasattr(self, "schwab_status_var"):
        self.schwab_status_var.set("Schwab: connected")
    return status_code, payload


def _populate_schwab_orders_table(table: ttk.Treeview, orders: list[Any], *, cache_attr: str, active_statuses: set[str]) -> None:
    for row_id in table.get_children():
        table.delete(row_id)

    cache: dict[str, dict[str, Any]] = {}
    for index, raw_order in enumerate(orders):
        if not isinstance(raw_order, dict):
            continue
        parsed = _parse_schwab_order_row(raw_order)
        iid = f"schwab_order_{index}"
        cache[iid] = raw_order
        status = str(parsed["status"]).upper()
        tag = "active" if status in active_statuses else "error" if status in {"REJECTED", "CANCELED", "EXPIRED"} else "terminal"
        table.insert(
            "",
            tk.END,
            iid=iid,
            values=(
                parsed["time"],
                parsed["order_id"],
                parsed["symbol"],
                parsed["side"],
                parsed["quantity"],
                parsed["position_effect"],
                parsed["order_type"],
                parsed["limit_price"],
                parsed["stop_price"],
                parsed["duration"],
                parsed["session"],
                parsed["status"],
                parsed["account"],
            ),
            tags=(tag,),
        )
    setattr(table, cache_attr, cache)


def _parse_schwab_order_row(order: dict[str, Any]) -> dict[str, str]:
    legs = order.get("orderLegCollection") or order.get("orderLegs") or []
    first_leg = legs[0] if legs and isinstance(legs[0], dict) else {}
    instrument = first_leg.get("instrument") if isinstance(first_leg.get("instrument"), dict) else {}
    return {
        "time": _first_text(order, "enteredTime", "enteredDateTime", "closeTime", "cancelTime"),
        "order_id": _first_text(order, "orderId", "orderID"),
        "symbol": str(instrument.get("symbol") or first_leg.get("finalSymbol") or order.get("symbol") or "").upper(),
        "side": _side_from_instruction(str(first_leg.get("instruction") or order.get("instruction") or "")),
        "quantity": _quantity_text(first_leg.get("quantity", order.get("quantity"))),
        "position_effect": str(first_leg.get("positionEffect") or first_leg.get("positionEffectType") or "").upper(),
        "order_type": str(order.get("orderType") or "").upper(),
        "limit_price": _price_text(order.get("price")),
        "stop_price": _price_text(order.get("stopPrice")),
        "duration": str(order.get("duration") or order.get("timeInForce") or "").upper(),
        "session": str(order.get("session") or "").upper(),
        "status": str(order.get("status") or "").upper(),
        "account": _first_text(order, "accountNumber", "accountId", "accountHash"),
    }


def _open_selected_schwab_order_editor(self: tk.Tk, source: str = "open") -> None:
    selected = _selected_schwab_order(self, source)
    if selected is None:
        messagebox.showerror("No Schwab order selected", "Select a Schwab order first.")
        return

    order, parsed = selected
    if str(parsed["status"]).upper() not in _active_order_statuses(self):
        _show_schwab_order_details(self, order, parsed)
        return

    _load_schwab_order_into_ticket(self, order, parsed)
    _show_schwab_replace_dialog(self, order, parsed)


def _show_schwab_replace_dialog(self: tk.Tk, order: dict[str, Any], parsed: dict[str, str]) -> None:
    window = tk.Toplevel(self)
    polished_theme.configure_toplevel(window)
    window.title(f"Edit Schwab Order {parsed['order_id']}")
    window.geometry("760x640")
    window.minsize(640, 520)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(2, weight=1)

    form = ttk.LabelFrame(window, text="Replacement Ticket", style="Card.TLabelframe", padding=10)
    form.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
    for column in (1, 3):
        form.columnconfigure(column, weight=1)

    order_id_var = tk.StringVar(value=parsed["order_id"])
    symbol_var = tk.StringVar(value=parsed["symbol"])
    side_var = tk.StringVar(value="sell" if "SELL" in parsed["side"].upper() else "buy")
    quantity_var = tk.StringVar(value=parsed["quantity"])
    effect_var = tk.StringVar(value=_display_stock_position_effect(parsed["position_effect"]))
    type_var = tk.StringVar(value=_supported_order_type(parsed["order_type"]))
    limit_var = tk.StringVar(value=parsed["limit_price"].replace("$", "").replace(",", "") if parsed["limit_price"] != "--" else "")
    stop_var = tk.StringVar(value=parsed["stop_price"].replace("$", "").replace(",", "") if parsed["stop_price"] != "--" else "")
    tif_var = tk.StringVar(value=_display_tif_from_order_fields(parsed["session"], parsed["duration"]))

    _grid_pair(form, 0, "Original order ID", ttk.Entry(form, textvariable=order_id_var, state="readonly"), "Symbol", ttk.Entry(form, textvariable=symbol_var))
    _grid_pair(form, 1, "Side", ttk.Combobox(form, textvariable=side_var, values=["buy", "sell"], state="readonly"), "Quantity", ttk.Entry(form, textvariable=quantity_var))
    _grid_pair(form, 2, "Position effect", ttk.Combobox(form, textvariable=effect_var, values=["AUTO", "OPENING", "CLOSING"], state="readonly"), "Order type", ttk.Combobox(form, textvariable=type_var, values=["MARKET", "LIMIT", "STOP", "STOP_LIMIT"], state="readonly"))
    limit_box = ttk.Frame(form, style="Panel.TFrame")
    limit_box.columnconfigure(0, weight=1)
    ttk.Entry(limit_box, textvariable=limit_var).grid(row=0, column=0, sticky="ew")
    ttk.Button(limit_box, text="Use Mid", command=lambda: use_mid_for_replace(), style="Accent.TButton").grid(row=0, column=1, sticky="e", padx=(6, 0))
    _grid_pair(form, 3, "Limit price", limit_box, "Stop price", ttk.Entry(form, textvariable=stop_var))
    ttk.Label(form, text="TIF", style="Subtle.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 6), pady=4)
    ttk.Combobox(form, textvariable=tif_var, values=SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, state="readonly").grid(
        row=4,
        column=1,
        sticky="ew",
        padx=(0, 10),
        pady=4,
    )

    preview_frame = ttk.LabelFrame(window, text="Replacement Payload Preview", style="Card.TLabelframe", padding=10)
    preview_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 8))
    preview_frame.rowconfigure(0, weight=1)
    preview_frame.columnconfigure(0, weight=1)
    preview = tk.Text(
        preview_frame,
        **polished_theme.dark_text_options(height=14, wrap=tk.WORD, font=("Consolas", 10), padx=8, pady=8),
    )
    preview.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=preview.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    preview.configure(yscrollcommand=scrollbar.set)

    def payload_from_form() -> dict[str, Any]:
        return _replacement_payload_from_fields(
            original_order=order,
            symbol=symbol_var.get(),
            side=side_var.get(),
            quantity=quantity_var.get(),
            order_type=type_var.get(),
            limit_price=limit_var.get(),
            stop_price=stop_var.get(),
            tif=tif_var.get(),
            position_effect=effect_var.get(),
        )

    def render_preview() -> dict[str, Any] | None:
        try:
            payload = payload_from_form()
            text = json.dumps(payload, indent=2)
        except Exception as exc:
            payload = None
            text = f"Replacement payload error:\n\n{type(exc).__name__}: {exc}"
        preview.configure(state=tk.NORMAL)
        preview.delete("1.0", tk.END)
        preview.insert(tk.END, text)
        preview.configure(state=tk.DISABLED)
        return payload

    def use_mid_for_replace() -> None:
        symbol = symbol_var.get().strip().upper()
        if not symbol:
            messagebox.showerror("Schwab mid-market lookup failed", "Enter a Schwab symbol first.", parent=window)
            return

        try:
            session = self._authorize_schwab_session()
            if session is None:
                return
            status_code, payload = session.get_quote(symbol)
            if status_code != 200:
                raise RuntimeError(f"Schwab quote returned HTTP {status_code}: {payload}")

            quote, source_key = _extract_schwab_quote(payload, symbol)
            bid = _first_number(quote, "bidPrice", "bid", "bid_price")
            ask = _first_number(quote, "askPrice", "ask", "ask_price")
            mark = _first_number(quote, "mark", "markPrice", "mark_price")
            last = _first_number(quote, "lastPrice", "last", "last_price", "closePrice", "regularMarketLastPrice")

            if bid is not None and ask is not None and bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                basis = "bid/ask midpoint"
            elif mark is not None and mark > 0:
                mid = mark
                basis = "mark price"
            elif last is not None and last > 0:
                mid = last
                basis = "last price fallback"
            else:
                raise RuntimeError(f"No usable bid/ask, mark, or last price found in Schwab quote for {symbol}.")

            limit_var.set(_format_price(mid))
            render_preview()
            _set_schwab_mode_text(
                self,
                "SCHWAB REPLACE MID-MARKET PRICE\n"
                "===============================\n\n"
                f"Symbol: {symbol}\n"
                f"Quote key: {source_key}\n"
                f"Bid: {_format_optional_price(bid)}\n"
                f"Ask: {_format_optional_price(ask)}\n"
                f"Mark: {_format_optional_price(mark)}\n"
                f"Last: {_format_optional_price(last)}\n\n"
                f"Replacement limit price updated to: ${mid:,.4f}\n"
                f"Basis: {basis}\n\n"
                "No order was submitted, replaced, or canceled.",
            )
        except Exception as exc:
            _write_schwab_order_error(self, "Schwab replace mid-market lookup failed", exc)
            messagebox.showerror("Schwab mid-market lookup failed", str(exc), parent=window)

    def confirm_replace() -> None:
        payload = render_preview()
        if payload is None:
            return
        try:
            session = self._authorize_schwab_session()
            if session is None:
                return
            status_code, response_payload, location = session.replace_order(parsed["order_id"], payload)
            receipt = (
                "SCHWAB REPLACE ORDER RESULT\n"
                "===========================\n\n"
                f"Original order ID: {parsed['order_id']}\n"
                f"Vibe: {status_code}\n"
                f"Location: {location or '(none returned)'}\n\n"
                "Replacement payload:\n"
                f"{json.dumps(payload, indent=2)}\n\n"
                "Response:\n"
                f"{json.dumps(response_payload, indent=2) if isinstance(response_payload, (dict, list)) else response_payload if response_payload is not None else '(empty response body)'}"
            )
            _set_schwab_mode_text(self, receipt)
            print(receipt, flush=True)
            _refresh_schwab_open_orders_tab(self)
            _refresh_schwab_recent_orders_tab(self)
            if 200 <= status_code < 300:
                messagebox.showinfo("Schwab replace sent", f"Order {parsed['order_id']} replace returned HTTP {status_code}.", parent=window)
                window.destroy()
            else:
                messagebox.showerror("Schwab replace returned non-2xx", f"Vibe: {status_code}\n\nCheck the Schwab output pane.", parent=window)
        except Exception as exc:
            _write_schwab_order_error(self, "Schwab replace order failed", exc)
            messagebox.showerror("Schwab replace failed", str(exc), parent=window)

    buttons = ttk.Frame(window, style="Panel.TFrame", padding=(12, 0, 12, 12))
    buttons.grid(row=3, column=0, sticky="ew")
    buttons.columnconfigure(0, weight=1)
    ttk.Button(buttons, text="Preview Replace", command=render_preview, style="Accent.TButton").grid(row=0, column=0, sticky="w")
    ttk.Button(buttons, text="Confirm Replace", command=confirm_replace, style="Danger.TButton").grid(row=0, column=1, sticky="e", padx=(8, 0))
    ttk.Button(buttons, text="Cancel Order", command=lambda app=self, win=window: _cancel_selected_schwab_open_order(app, parent=win)).grid(row=0, column=2, sticky="e", padx=(8, 0))
    ttk.Button(buttons, text="Close", command=window.destroy).grid(row=0, column=3, sticky="e", padx=(8, 0))

    for variable in (symbol_var, side_var, quantity_var, effect_var, type_var, limit_var, stop_var, tif_var):
        variable.trace_add("write", lambda *_args: render_preview())
    render_preview()


def _replacement_payload_from_fields(
    *,
    original_order: dict[str, Any],
    symbol: str,
    side: str,
    quantity: str,
    order_type: str,
    limit_price: str,
    stop_price: str,
    tif: str,
    position_effect: str,
) -> dict[str, Any]:
    clean_symbol = symbol.strip().upper()
    if not clean_symbol:
        raise ValueError("Symbol is required.")
    qty = float(quantity.strip().replace(",", ""))
    if qty <= 0:
        raise ValueError("Quantity must be positive.")

    clean_type = _supported_order_type(order_type)
    clean_tif = _supported_duration(tif)
    _validate_schwab_equity_tif_order_type(clean_tif, clean_type)
    clean_session, clean_duration = schwab_equity_session_duration(clean_tif)
    instruction = "BUY" if side.strip().lower() == "buy" else "SELL"
    asset_type = _asset_type_from_order(original_order)

    payload: dict[str, Any] = {
        "orderType": clean_type,
        "session": clean_session,
        "duration": clean_duration,
        "orderStrategyType": str(original_order.get("orderStrategyType") or "SINGLE").upper(),
        "orderLegCollection": [
            {
                "instruction": instruction,
                "quantity": qty,
                "instrument": {
                    "symbol": clean_symbol,
                    "assetType": asset_type,
                },
            }
        ],
    }
    normalized_effect = _normalized_stock_position_effect(position_effect)
    if normalized_effect:
        payload["orderLegCollection"][0]["positionEffect"] = normalized_effect

    if clean_type in {"LIMIT", "STOP_LIMIT"}:
        payload["price"] = _decimal_text(limit_price, field="Limit price")
    if clean_type in {"STOP", "STOP_LIMIT"}:
        payload["stopPrice"] = _decimal_text(stop_price, field="Stop price")
    if clean_type not in {"STOP", "STOP_LIMIT"} and str(stop_price or "").strip():
        raise ValueError("Stop price can only be used with STOP or STOP_LIMIT stock orders. Clear Stop price or change Order type.")

    return payload


def _cancel_selected_schwab_open_order(self: tk.Tk, parent: tk.Widget | None = None) -> None:
    selected = _selected_schwab_order(self, "open") or _selected_schwab_order(self, "recent")
    if selected is None:
        messagebox.showerror("No Schwab order selected", "Select an open Schwab order first.", parent=parent)
        return

    order, parsed = selected
    order_id = parsed["order_id"]
    if not order_id:
        messagebox.showerror("Cancel blocked", "Selected order has no Schwab order ID.", parent=parent)
        return
    if str(parsed["status"]).upper() not in _active_order_statuses(self):
        messagebox.showerror("Cancel blocked", f"Order {order_id} is {parsed['status']}, not open/working.", parent=parent)
        return

    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        status_code, payload = session.cancel_order(order_id)
        if 200 <= status_code < 300:
            self.cancel_verified_this_session = True
            updater = getattr(self, "_update_verification_status", None)
            if callable(updater):
                updater()
        receipt = (
            "SCHWAB CANCEL ORDER RESULT\n"
            "==========================\n\n"
            f"Vibe: {status_code}\n"
            f"Order ID: {order_id}\n"
            f"Symbol: {parsed['symbol']}\n"
            f"Status before cancel: {parsed['status']}\n\n"
            f"Response: {payload if payload is not None else '(empty response body)'}\n\n"
            "Open Orders and Recent Orders were refreshed after the cancel attempt."
        )
        _set_schwab_mode_text(self, receipt)
        print(receipt, flush=True)
        _refresh_schwab_open_orders_tab(self)
        _refresh_schwab_recent_orders_tab(self)
        if 200 <= status_code < 300:
            messagebox.showinfo("Schwab cancel sent", f"Order {order_id} cancel returned HTTP {status_code}.", parent=parent)
        else:
            messagebox.showerror("Schwab cancel returned non-2xx", f"Vibe: {status_code}\n\nCheck the Schwab output pane.", parent=parent)
    except Exception as exc:
        _write_schwab_order_error(self, "Schwab cancel order failed", exc)
        messagebox.showerror("Schwab cancel failed", str(exc), parent=parent)


def _selected_schwab_order(self: tk.Tk, source: str) -> tuple[dict[str, Any], dict[str, str]] | None:
    table = getattr(self, "schwab_open_orders_table" if source == "open" else "schwab_recent_orders_table", None)
    if table is None:
        return None
    selection = table.selection()
    if not selection:
        return None
    iid = str(selection[0])
    cache = getattr(table, "schwab_open_orders_by_iid" if source == "open" else "schwab_recent_orders_by_iid", {}) or {}
    order = cache.get(iid) if isinstance(cache, dict) else None
    if not isinstance(order, dict):
        return None
    return order, _parse_schwab_order_row(order)


def _load_ticket_from_selected_schwab_order(self: tk.Tk, source: str) -> None:
    selected = _selected_schwab_order(self, source)
    if selected is None:
        return
    order, parsed = selected
    _load_schwab_order_into_ticket(self, order, parsed)


def _load_schwab_order_into_ticket(self: tk.Tk, order: dict[str, Any], parsed: dict[str, str]) -> None:
    if parsed["symbol"] and hasattr(self, "symbol_var"):
        self.symbol_var.set(parsed["symbol"])
    if parsed["side"] and hasattr(self, "side_var"):
        self.side_var.set("sell" if "SELL" in parsed["side"].upper() else "buy")
    if parsed["quantity"] and hasattr(self, "quantity_var"):
        self.quantity_var.set(parsed["quantity"])
    if parsed["limit_price"] != "--" and hasattr(self, "limit_price_var"):
        self.limit_price_var.set(parsed["limit_price"].replace("$", "").replace(",", ""))
    if parsed["stop_price"] != "--" and hasattr(self, "stop_price_var"):
        self.stop_price_var.set(parsed["stop_price"].replace("$", "").replace(",", ""))
    if (parsed["duration"] or parsed["session"]) and hasattr(self, "time_in_force_var"):
        self.time_in_force_var.set(_display_tif_from_order_fields(parsed["session"], parsed["duration"]))
    if parsed["session"] and hasattr(self, "schwab_stock_session_var"):
        self.schwab_stock_session_var.set(_supported_session(parsed["session"]))
    if parsed["position_effect"] and hasattr(self, "schwab_stock_position_effect_var"):
        self.schwab_stock_position_effect_var.set(_display_stock_position_effect(parsed["position_effect"]))
    if parsed["order_type"] and hasattr(self, "order_type_var"):
        self.order_type_var.set(_supported_order_type(parsed["order_type"]).lower())
    if parsed["order_id"] and hasattr(self, "cancel_order_id_var"):
        self.cancel_order_id_var.set(parsed["order_id"])


def _show_schwab_order_details(self: tk.Tk, order: dict[str, Any], parsed: dict[str, str]) -> None:
    text = (
        "SCHWAB ORDER DETAILS\n"
        "====================\n\n"
        f"Order ID: {parsed['order_id']}\n"
        f"Status: {parsed['status']}\n"
        f"Symbol: {parsed['symbol']}\n"
        f"Side: {parsed['side']}\n"
        f"Quantity: {parsed['quantity']}\n\n"
        "This order is not open/working, so replace and cancel actions are disabled.\n\n"
        "Raw order:\n"
        f"{json.dumps(order, indent=2)}"
    )
    _set_schwab_mode_text(self, text)


def _select_schwab_account_tab(self: tk.Tk, label: str) -> None:
    notebook = getattr(self, "schwab_account_tabs", None)
    if notebook is None:
        return
    try:
        for tab_id in notebook.tabs():
            if notebook.tab(tab_id, "text") == label:
                notebook.select(tab_id)
                return
    except tk.TclError:
        return


def _active_order_statuses(self: tk.Tk) -> set[str]:
    getter = getattr(self, "schwab_active_order_statuses", None)
    if callable(getter):
        try:
            return {str(status).upper() for status in getter()}
        except Exception:
            pass
    return {"WORKING", "QUEUED", "PENDING_ACTIVATION", "ACCEPTED", "AWAITING_PARENT_ORDER", "PENDING_REPLACE", "PENDING_CANCEL"}


def _write_schwab_order_error(self: tk.Tk, title: str, exc: Exception) -> None:
    text = (
        f"{title.upper()}\n"
        f"{'=' * len(title)}\n\n"
        f"{type(exc).__name__}: {exc}\n\n"
        "Traceback:\n"
        f"{traceback.format_exc()}"
    )
    print(text, flush=True)
    _set_schwab_mode_text(self, text)


def _first_text(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _side_from_instruction(value: str) -> str:
    clean = value.strip().upper()
    if clean.startswith("BUY"):
        return "BUY"
    if clean.startswith("SELL"):
        return "SELL"
    return clean


def _quantity_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(str(value).replace(",", ""))
        return f"{number:g}"
    except (TypeError, ValueError):
        return str(value)


def _price_text(value: Any) -> str:
    if value in (None, ""):
        return "--"
    try:
        return f"${float(str(value).replace(',', '')):,.2f}"
    except (TypeError, ValueError):
        return str(value)


def _decimal_text(value: str, *, field: str) -> str:
    try:
        number = float(value.strip().replace("$", "").replace(",", ""))
    except ValueError as exc:
        raise ValueError(f"{field} must be a positive number.") from exc
    if number <= 0:
        raise ValueError(f"{field} must be positive.")
    return f"{number:.2f}"


def _supported_order_type(value: str) -> str:
    clean = str(value or "LIMIT").strip().upper().replace(" ", "_")
    return clean if clean in {"MARKET", "LIMIT", "STOP", "STOP_LIMIT"} else "LIMIT"


def _supported_duration(value: str) -> str:
    try:
        return normalize_time_in_force(value).value
    except ValueError as exc:
        raise ValueError(f"Unsupported Schwab stock TIF: {value!r}") from exc


def _display_tif_from_order_fields(session: str, duration: str) -> str:
    return schwab_equity_tif_from_session_duration(session, duration).value


def _validate_schwab_equity_tif_order_type(tif: str, order_type: str) -> None:
    normalized_tif = normalize_time_in_force(tif)
    schwab_equity_session_duration(normalized_tif)
    if schwab_equity_tif_requires_limit_order(normalized_tif) and order_type != "LIMIT":
        raise ValueError(
            f"TIF {normalized_tif.value} is an extended-hours equity selection. Schwab extended-hours "
            f"stock/ETF orders must use Order type LIMIT; current order type is {order_type}."
        )


def _supported_session(value: str) -> str:
    clean = str(value or "NORMAL").strip().upper().replace(" ", "_")
    return clean if clean in {"NORMAL", "AM", "PM", "SEAMLESS"} else "NORMAL"


def _asset_type_from_order(order: dict[str, Any]) -> str:
    legs = order.get("orderLegCollection") or order.get("orderLegs") or []
    first_leg = legs[0] if legs and isinstance(legs[0], dict) else {}
    instrument = first_leg.get("instrument") if isinstance(first_leg.get("instrument"), dict) else {}
    return str(instrument.get("assetType") or "EQUITY").upper()


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
            bg=polished_theme.PANEL_ALT,
            fg=polished_theme.MUTED,
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
        "success": ("#052e2b", polished_theme.POSITIVE),
        "failure": ("#3b0a19", polished_theme.NEGATIVE),
        "neutral": (polished_theme.PANEL_ALT, polished_theme.MUTED),
    }.get(state, (polished_theme.PANEL_ALT, polished_theme.MUTED))
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
        pady=(0 if row == 0 else 4, 4),
        ipady=0,
    )


def _run_schwab_workspace_action(self: tk.Tk, *command_names: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    command = _first_available_command(self, *command_names)
    command_label = ", ".join(command_names) or "(none)"
    is_live_submit = "submit_live_schwab_order" in command_names

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
                "Command: submit_live_schwab_order\n\n"
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
