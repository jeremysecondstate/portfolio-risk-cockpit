from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Mapping, Type

from app.analytics.option_contract_inspector import (
    OptionContractInspectorModel,
    build_option_contract_inspector_model,
    is_schwab_option_holding,
    parse_occ_option_symbol,
)
from app.ui.polished_theme import BORDER, DANGER, MUTED, PANEL, PANEL_ALT, POSITIVE, SURFACE, TEXT, WARNING


_installed = False


def install_schwab_option_contract_inspector_extension(app_cls: Type[tk.Tk]) -> None:
    """Install the Schwab option holdings double-click inspector."""

    global _installed
    if _installed:
        return

    app_cls.open_schwab_option_contract_inspector = _open_schwab_option_contract_inspector  # type: ignore[attr-defined]

    original_build_layout = app_cls._build_layout

    def build_layout_with_option_contract_inspector(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _bind_schwab_option_contract_inspector(self))

    app_cls._build_layout = build_layout_with_option_contract_inspector  # type: ignore[method-assign]
    _installed = True


def _bind_schwab_option_contract_inspector(self: tk.Tk) -> None:
    table = getattr(self, "schwab_workspace_holdings_table", None)
    if table is None or getattr(table, "_schwab_option_contract_inspector_bound", False):
        return
    table.bind("<Double-1>", lambda event, app=self, source=table: _open_inspector_from_event(app, source, event), add="+")
    table._schwab_option_contract_inspector_bound = True  # type: ignore[attr-defined]


def _open_inspector_from_event(self: tk.Tk, table: ttk.Treeview, event: tk.Event) -> None:
    row_id = table.identify_row(event.y)
    if not row_id:
        return

    table.selection_set(row_id)
    table.focus(row_id)
    values = _tree_row_values(table, row_id)
    if not is_schwab_option_holding(values):
        return

    _open_schwab_option_contract_inspector(self, values)


def _open_schwab_option_contract_inspector(
    self: tk.Tk,
    holding_values: Mapping[str, Any] | None = None,
) -> None:
    values = dict(holding_values or _selected_holding_values(self))
    if not values:
        messagebox.showinfo("Option inspector", "Select a Schwab option holding first.")
        return
    if not is_schwab_option_holding(values):
        return

    model = _build_model_from_app_context(self, values)
    _show_option_contract_inspector(self, model)


def _selected_holding_values(self: tk.Tk) -> dict[str, str]:
    table = getattr(self, "schwab_workspace_holdings_table", None)
    if table is None:
        return {}
    selection = table.selection()
    row_id = selection[0] if selection else table.focus()
    if not row_id:
        return {}
    return _tree_row_values(table, row_id)


def _tree_row_values(table: ttk.Treeview, row_id: str) -> dict[str, str]:
    raw_values = table.item(row_id, "values")
    columns = tuple(table["columns"])
    return {str(column): str(raw_values[index]) for index, column in enumerate(columns) if index < len(raw_values)}


def _build_model_from_app_context(self: tk.Tk, values: Mapping[str, Any]) -> OptionContractInspectorModel:
    raw_symbol = str(values.get("symbol") or "").strip().upper()
    parsed = parse_occ_option_symbol(raw_symbol)
    portfolio = _portfolio_or_none(self)
    position = _position_for_symbol(portfolio, raw_symbol)
    underlying_price = _underlying_price_from_app(self, portfolio, parsed.underlying if parsed is not None else "")
    chain_rows = getattr(self, "schwab_option_chain_rows", {}) or {}
    portfolio_value = _to_float(getattr(portfolio, "total_value", None))
    return build_option_contract_inspector_model(
        values,
        position=position,
        portfolio_value=portfolio_value,
        underlying_last=underlying_price,
        chain_rows=chain_rows,
    )


def _portfolio_or_none(self: tk.Tk) -> Any:
    broker = getattr(self, "broker", None)
    getter = getattr(broker, "get_portfolio", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _position_for_symbol(portfolio: Any, symbol: str) -> Any:
    if portfolio is None or not symbol:
        return None
    getter = getattr(portfolio, "get_position", None)
    if callable(getter):
        try:
            position = getter(symbol)
            if position is not None:
                return position
        except Exception:
            pass
    positions = getattr(portfolio, "positions", {}) or {}
    return positions.get(symbol.strip().upper()) if isinstance(positions, dict) else None


def _underlying_price_from_app(self: tk.Tk, portfolio: Any, underlying: str) -> float | None:
    if not underlying:
        return None

    underlying_position = _position_for_symbol(portfolio, underlying)
    price = _to_float(getattr(underlying_position, "last_price", None))
    if price is not None and price > 0:
        return price

    options_symbol = _get_var(self, "options_symbol_var").strip().upper()
    if options_symbol == underlying:
        price = _to_float(_get_var(self, "options_underlying_price_var"))
        if price is not None and price > 0:
            return price
    return None


def _show_option_contract_inspector(self: tk.Tk, model: OptionContractInspectorModel) -> None:
    _ensure_inspector_styles(self)

    window = tk.Toplevel(self)
    window.title("Option Contract Inspector")
    window.geometry("980x760")
    window.minsize(760, 560)
    window.configure(bg=PANEL)
    window.transient(self)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    header = tk.Frame(window, bg=SURFACE, padx=18, pady=16)
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    _build_header(header, model)

    shell = ttk.Frame(window, style="Panel.TFrame", padding=(14, 12, 14, 0))
    shell.grid(row=1, column=0, sticky="nsew")
    shell.columnconfigure(0, weight=1)
    shell.rowconfigure(0, weight=1)

    canvas = tk.Canvas(shell, background=PANEL, highlightthickness=0, borderwidth=0)
    scrollbar = ttk.Scrollbar(shell, orient=tk.VERTICAL, command=canvas.yview)
    content = ttk.Frame(canvas, style="Panel.TFrame")
    content_id = canvas.create_window((0, 0), window=content, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    content.columnconfigure(0, weight=1, uniform="inspector")
    content.columnconfigure(1, weight=1, uniform="inspector")
    content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_id, width=event.width))

    _build_contract_identity(content, model, row=0, column=0)
    _build_moneyness(content, model, row=0, column=1)
    _build_time_risk(content, model, row=1, column=0)
    _build_liquidity(content, model, row=1, column=1)
    _build_greeks(content, model, row=2, column=0)
    _build_position_risk(content, model, row=2, column=1)
    _build_posture(content, model, row=3, column=0, columnspan=2)

    footer = ttk.Frame(window, style="Panel.TFrame", padding=(14, 10, 14, 14))
    footer.grid(row=2, column=0, sticky="ew")
    for column in range(6):
        footer.columnconfigure(column, weight=1, uniform="inspector_buttons")
    ttk.Button(footer, text="Load Into Ticket", command=lambda: _load_model_into_ticket(self, model), style="Accent.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(footer, text="Load Option Chain", command=lambda: _load_option_chain_for_model(self, model)).grid(row=0, column=1, sticky="ew", padx=(0, 8))
    ttk.Button(footer, text="Run Risk Preview", command=lambda: _run_risk_preview_for_model(self, model)).grid(row=0, column=2, sticky="ew", padx=(0, 8))
    ttk.Button(footer, text="Copy Summary", command=lambda: _copy_summary(self, model)).grid(row=0, column=3, sticky="ew", padx=(0, 8))
    ttk.Button(footer, text=_trade_memory_button_text(self, model), command=lambda: _open_trade_memory_for_model(self, model)).grid(row=0, column=4, sticky="ew", padx=(0, 8))
    ttk.Button(footer, text="Close", command=window.destroy).grid(row=0, column=5, sticky="ew")

    window.focus_set()


def _ensure_inspector_styles(root: tk.Misc) -> None:
    style = ttk.Style(root)
    style.configure("InspectorCard.TLabelframe", background=PANEL, bordercolor=BORDER, relief="solid", padding=14)
    style.configure("InspectorCard.TLabelframe.Label", background=PANEL, foreground=TEXT, font=("Segoe UI", 10, "bold"))
    style.configure("InspectorMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("InspectorValue.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10, "bold"))
    style.configure("InspectorBullet.TLabel", background=PANEL, foreground=TEXT)
    style.configure("InspectorBadge.TLabel", background=PANEL_ALT, foreground=TEXT, font=("Segoe UI", 9, "bold"), padding=(8, 4))


def _build_header(parent: tk.Frame, model: OptionContractInspectorModel) -> None:
    posture_bg, posture_fg = _posture_colors(model.posture)
    title = _contract_title(model)
    subtitle = model.summary

    tk.Label(parent, text=title, background=SURFACE, foreground="#ffffff", font=("Segoe UI", 18, "bold"), anchor="w").grid(row=0, column=0, sticky="ew")
    tk.Label(parent, text=subtitle, background=SURFACE, foreground="#cbd5e1", font=("Segoe UI", 10), anchor="w", justify=tk.LEFT, wraplength=760).grid(row=1, column=0, sticky="ew", pady=(6, 0))
    tk.Label(parent, text=model.posture, background=posture_bg, foreground=posture_fg, font=("Segoe UI", 10, "bold"), padx=12, pady=6).grid(row=0, column=1, rowspan=2, sticky="ne", padx=(16, 0))

    metrics = tk.Frame(parent, bg=SURFACE)
    metrics.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
    for column in range(5):
        metrics.columnconfigure(column, weight=1, uniform="header_metric")
    _header_metric(metrics, 0, "Type", model.option_type)
    _header_metric(metrics, 1, "Strike", _price(model.strike))
    _header_metric(metrics, 2, "Expiration", model.expiration_text)
    _header_metric(metrics, 3, "DTE", _text(model.dte))
    _header_metric(metrics, 4, "P&L", _money(model.pnl))


def _header_metric(parent: tk.Frame, column: int, label: str, value: str) -> None:
    box = tk.Frame(parent, bg="#1f2937", padx=10, pady=8)
    box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0))
    tk.Label(box, text=label.upper(), bg="#1f2937", fg="#9ca3af", font=("Segoe UI", 8, "bold"), anchor="w").pack(anchor=tk.W)
    tk.Label(box, text=value, bg="#1f2937", fg="#ffffff", font=("Segoe UI", 11, "bold"), anchor="w").pack(anchor=tk.W, pady=(2, 0))


def _build_contract_identity(parent: ttk.Frame, model: OptionContractInspectorModel, *, row: int, column: int) -> None:
    card = _card(parent, "Contract Identity", row, column)
    _kv(card, 0, "Raw symbol", model.raw_symbol or "--")
    _kv(card, 1, "Underlying", model.underlying or "--")
    _kv(card, 2, "Expiration", model.expiration_text)
    _kv(card, 3, "DTE", _text(model.dte))
    _kv(card, 4, "Call / Put", model.option_type)
    _kv(card, 5, "Strike", _price(model.strike))
    _kv(card, 6, "Contracts", _contracts(model.quantity))
    _kv(card, 7, "Last / Mark", f"{_money(model.last_price)} / {_price(model.mark)}")
    _kv(card, 8, "Current value", _money(model.current_value))
    _kv(card, 9, "Current P&L", _money(model.pnl))
    if model.parse_warning:
        _note(card, 10, model.parse_warning, color=DANGER)


def _build_moneyness(parent: ttk.Frame, model: OptionContractInspectorModel, *, row: int, column: int) -> None:
    card = _card(parent, "Moneyness", row, column)
    _kv(card, 0, "Underlying last", _money(model.underlying_last))
    _kv(card, 1, "Status", model.moneyness)
    _kv(card, 2, "Distance", _signed_money(model.distance_to_strike))
    _kv(card, 3, "Distance %", _signed_percent(model.distance_percent))
    _note(card, 4, model.moneyness_explanation)


def _build_time_risk(parent: ttk.Frame, model: OptionContractInspectorModel, *, row: int, column: int) -> None:
    card = _card(parent, "Time Risk", row, column)
    _kv(card, 0, "DTE", _text(model.dte))
    _kv(card, 1, "Time bucket", model.time_bucket)
    _note(card, 2, model.time_warning)


def _build_liquidity(parent: ttk.Frame, model: OptionContractInspectorModel, *, row: int, column: int) -> None:
    card = _card(parent, "Liquidity / Execution", row, column)
    _kv(card, 0, "Bid", _price(model.bid))
    _kv(card, 1, "Ask", _price(model.ask))
    _kv(card, 2, "Mark / mid", _price(model.mark))
    _kv(card, 3, "Bid/ask width", _price(model.spread))
    _kv(card, 4, "Spread % of mark", _decimal_percent(model.spread_percent))
    _kv(card, 5, "Volume", _int_text(model.volume))
    _kv(card, 6, "Open interest", _int_text(model.open_interest))
    _kv(card, 7, "Liquidity grade", model.liquidity_grade)
    _note(card, 8, model.liquidity_warning)


def _build_greeks(parent: ttk.Frame, model: OptionContractInspectorModel, *, row: int, column: int) -> None:
    card = _card(parent, "Greeks", row, column)
    if model.greeks_source == "Unavailable":
        _note(card, 0, "Greeks unavailable from current data source.")
        return
    _kv(card, 0, "Delta", _number(model.delta, digits=3, signed=True))
    _kv(card, 1, "Gamma", _number(model.gamma, digits=4, signed=True))
    _kv(card, 2, "Theta", _number(model.theta, digits=3, signed=True))
    _kv(card, 3, "Vega", _number(model.vega, digits=3, signed=True))
    _kv(card, 4, "IV", _decimal_percent(model.implied_volatility))
    _note(card, 5, _greek_translation(model))


def _build_position_risk(parent: ttk.Frame, model: OptionContractInspectorModel, *, row: int, column: int) -> None:
    card = _card(parent, "Position Risk", row, column)
    for index, line in enumerate(model.position_risk_lines):
        _bullet(card, index, line)


def _build_posture(parent: ttk.Frame, model: OptionContractInspectorModel, *, row: int, column: int, columnspan: int) -> None:
    card = _card(parent, "Action Posture", row, column, columnspan=columnspan)
    _kv(card, 0, "Posture", model.posture)
    _note(card, 1, "This is a read-only contract explanation, not financial advice.")
    review_lines = [
        "Review exit liquidity.",
        "Review thesis.",
        "Review roll/close/hold alternatives.",
        "Check whether the original reason for the trade still holds.",
        "Confirm expiration risk.",
    ]
    for offset, line in enumerate(review_lines, start=2):
        _bullet(card, offset, line)
    if model.what_can_go_wrong:
        _note(card, len(review_lines) + 3, "What can go wrong:", bold=True)
        for offset, line in enumerate(model.what_can_go_wrong, start=len(review_lines) + 4):
            _bullet(card, offset, line)
    if model.flags:
        _note(card, len(review_lines) + len(model.what_can_go_wrong) + 5, "Flags: " + ", ".join(model.flags), color=DANGER if model.posture == "DEFENSIVE" else TEXT)


def _card(parent: ttk.Frame, title: str, row: int, column: int, *, columnspan: int = 1) -> ttk.LabelFrame:
    card = ttk.LabelFrame(parent, text=title, style="InspectorCard.TLabelframe")
    card.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=(0 if column == 0 else 10, 0), pady=(0, 10))
    card.columnconfigure(1, weight=1)
    return card


def _kv(parent: ttk.Frame, row: int, label: str, value: str) -> None:
    ttk.Label(parent, text=label, style="InspectorMuted.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=3)
    ttk.Label(parent, text=value or "--", style="InspectorValue.TLabel", wraplength=330, justify=tk.LEFT).grid(row=row, column=1, sticky="ew", pady=3)


def _note(parent: ttk.Frame, row: int, text: str, *, color: str = TEXT, bold: bool = False) -> None:
    label = tk.Label(
        parent,
        text=text or "--",
        bg=PANEL,
        fg=color,
        font=("Segoe UI", 10, "bold" if bold else "normal"),
        justify=tk.LEFT,
        anchor="w",
        wraplength=420,
    )
    label.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 2))


def _bullet(parent: ttk.Frame, row: int, text: str) -> None:
    tk.Label(parent, text="- " + text, bg=PANEL, fg=TEXT, font=("Segoe UI", 10), justify=tk.LEFT, anchor="w", wraplength=420).grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)


def _load_model_into_ticket(self: tk.Tk, model: OptionContractInspectorModel) -> None:
    parsed = model.parsed
    if parsed is None:
        _set_preview_text_if_available(self, model.summary + "\n\nCould not load option-specific ticket fields because the symbol did not parse.")
        return

    contracts = abs(model.quantity) if model.quantity is not None else 1.0
    contracts_text = f"{contracts:g}"
    option_type = "Call" if parsed.option_type == "call" else "Put"
    strategy = "Long Call" if parsed.option_type == "call" else "Long Put"
    ticket_price = model.mark if model.mark is not None and model.mark > 0 else model.ask

    _set_var(self, "trade_venue_var", "Schwab")
    _set_var(self, "symbol_var", parsed.underlying)
    _set_var(self, "quantity_var", contracts_text)
    _set_var(self, "options_symbol_var", parsed.underlying)
    _set_var(self, "options_strategy_var", strategy)
    _set_var(self, "options_action_var", "Sell" if (model.quantity or 0) < 0 else "Buy")
    _set_var(self, "options_expiration_var", model.chain_expiration_label or model.expiration_text)
    _set_var(self, "options_type_var", option_type)
    _set_var(self, "options_order_type_var", "LIMIT")
    _set_var(self, "options_tif_var", "Day")
    _set_var(self, "options_quantity_var", f"{int(max(1.0, contracts) * 100)}")
    _set_var(self, "options_contracts_var", contracts_text)
    _set_var(self, "options_strike_var", _number(model.strike, digits=2))
    _set_var(self, "options_short_strike_var", _number(model.strike, digits=2))
    _set_var(self, "options_bid_var", _number(model.bid, digits=2))
    _set_var(self, "options_ask_var", _number(model.ask, digits=2))
    _set_var(self, "options_mark_var", _number(model.mark, digits=2))
    _set_var(self, "options_credit_var", "0")
    if ticket_price is not None and ticket_price > 0:
        _set_var(self, "options_premium_var", _number(ticket_price, digits=2))
        _set_var(self, "limit_price_var", _number(ticket_price, digits=2))
    if model.underlying_last is not None:
        _set_var(self, "options_underlying_price_var", _number(model.underlying_last, digits=2))

    self.schwab_research_selected_contract_symbol = model.raw_symbol
    if hasattr(self, "schwab_preview_status_var"):
        _set_var(self, "schwab_preview_status_var", "Last Schwab preview: option loaded only")
    _set_preview_text_if_available(
        self,
        "OPTION CONTRACT LOADED INTO TICKET\n"
        "==================================\n\n"
        f"{model.summary}\n\n"
        "The shared option ticket fields were populated for review. No order was submitted or previewed.",
    )


def _load_option_chain_for_model(self: tk.Tk, model: OptionContractInspectorModel) -> None:
    parsed = model.parsed
    if parsed is None:
        messagebox.showinfo("Load option chain", "Could not resolve the underlying symbol from this option contract.")
        return
    _set_var(self, "symbol_var", parsed.underlying)
    _set_var(self, "options_symbol_var", parsed.underlying)
    command = getattr(self, "load_schwab_option_chain", None)
    if callable(command):
        command()
    else:
        messagebox.showinfo("Load option chain", "Schwab option-chain loader is not installed.")


def _run_risk_preview_for_model(self: tk.Tk, model: OptionContractInspectorModel) -> None:
    _load_model_into_ticket(self, model)
    command = getattr(self, "preview_order", None)
    if callable(command):
        command()
    else:
        messagebox.showinfo("Run risk preview", "Local risk preview is not installed.")


def _copy_summary(self: tk.Tk, model: OptionContractInspectorModel) -> None:
    lines = [
        "Option Contract Inspector",
        f"Symbol: {model.raw_symbol}",
        f"Posture: {model.posture}",
        model.summary,
        "",
        f"Underlying: {model.underlying}",
        f"Expiration: {model.expiration_text}",
        f"DTE: {_text(model.dte)}",
        f"Type: {model.option_type}",
        f"Strike: {_price(model.strike)}",
        f"Contracts: {_contracts(model.quantity)}",
        f"Value: {_money(model.current_value)}",
        f"P&L: {_money(model.pnl)}",
        f"Moneyness: {model.moneyness}",
        f"Liquidity: {model.liquidity_grade}",
    ]
    self.clipboard_clear()
    self.clipboard_append("\n".join(lines))


def _trade_memory_button_text(self: tk.Tk, model: OptionContractInspectorModel) -> str:
    checker = getattr(self, "trade_memory_has_snapshot_for_symbol", None)
    symbol = model.raw_symbol or model.underlying
    if callable(checker) and symbol:
        try:
            if checker(symbol):
                return "Open Original Thesis"
        except Exception:
            pass
    return "Find Trade Memory"


def _open_trade_memory_for_model(self: tk.Tk, model: OptionContractInspectorModel) -> None:
    opener = getattr(self, "open_trade_memory_for_symbol", None)
    if not callable(opener):
        messagebox.showinfo("Trade Memory unavailable", "The Schwab Trade Memory extension is not installed.")
        return
    opener(model.raw_symbol or model.underlying)


def _set_preview_text_if_available(self: tk.Tk, content: str) -> None:
    writer = getattr(self, "_set_preview_text", None)
    preview = getattr(self, "schwab_trading_preview_text", None)
    if preview is not None:
        self.preview_text = preview
    if callable(writer):
        writer(content)


def _greek_translation(model: OptionContractInspectorModel) -> str:
    parts: list[str] = []
    if model.delta is not None:
        parts.append(f"Delta: about {_signed_money(model.delta * 100)} per +$1 stock move per contract.")
    if model.theta is not None:
        parts.append(f"Theta: about {_signed_money(model.theta * 100)} per day per contract.")
    if model.vega is not None:
        parts.append(f"Vega: about {_signed_money(model.vega * 100)} per +1 vol point per contract.")
    return " ".join(parts) or "Greeks unavailable from current data source."


def _contract_title(model: OptionContractInspectorModel) -> str:
    if model.parsed is None:
        return f"{model.raw_symbol or 'Option'} Contract"
    return f"{model.parsed.underlying} {_price(model.parsed.strike)} {model.parsed.option_type_label}"


def _posture_colors(posture: str) -> tuple[str, str]:
    if posture == "NORMAL":
        return "#052e2b", POSITIVE
    if posture == "CAUTIOUS":
        return "#3b2f08", WARNING
    if posture == "DEFENSIVE":
        return "#3b0a19", DANGER
    return PANEL_ALT, MUTED


def _set_var(self: tk.Tk, name: str, value: Any) -> None:
    var = getattr(self, name, None)
    if var is None:
        return
    try:
        var.set(str(value))
    except Exception:
        return


def _get_var(self: tk.Tk, name: str) -> str:
    var = getattr(self, name, None)
    try:
        return str(var.get())
    except Exception:
        return ""


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _text(value: Any) -> str:
    return "--" if value is None or value == "" else str(value)


def _money(value: float | None) -> str:
    return "--" if value is None else f"${value:,.2f}"


def _signed_money(value: float | None) -> str:
    if value is None:
        return "--"
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):,.2f}"


def _price(value: float | None) -> str:
    if value is None:
        return "--"
    formatted = f"{value:,.2f}"
    clean = formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
    return f"${clean}"


def _number(value: float | None, *, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return ""
    prefix = "+" if signed and value > 0 else ""
    return f"{prefix}{value:.{digits}f}".rstrip("0").rstrip(".")


def _int_text(value: int | None) -> str:
    return "--" if value is None else f"{value:,}"


def _contracts(value: float | None) -> str:
    if value is None:
        return "--"
    abs_value = abs(value)
    side = "short" if value < 0 else "long"
    plural = "contract" if abs_value == 1 else "contracts"
    return f"{abs_value:g} {plural} ({side})"


def _signed_percent(value: float | None) -> str:
    return "--" if value is None else f"{value:+.1f}%"


def _decimal_percent(value: float | None) -> str:
    return "--" if value is None else f"{value * 100:.1f}%"
