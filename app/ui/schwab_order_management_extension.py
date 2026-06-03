from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Mapping, Type

from app.brokers.schwab import order_management
from app.brokers.schwab.order_management import SchwabOrderRow
from app.core.order_models import SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES
from app.ui import options_lab_extension


def install_schwab_order_management_extension(app_cls: Type[tk.Tk]) -> None:
    """Install Schwab open/recent order tables and guarded edit/cancel flows."""

    app_cls.load_schwab_open_orders = _load_schwab_recent_orders  # type: ignore[method-assign]
    app_cls.load_schwab_open_orders_only = _load_schwab_open_orders_only  # type: ignore[method-assign]
    app_cls.show_cancel_order_placeholder = _show_schwab_cancel_from_selection  # type: ignore[method-assign]
    app_cls.show_schwab_working_order_dialog = _show_schwab_working_order_dialog  # type: ignore[attr-defined]
    app_cls.show_schwab_cancel_order_dialog = _show_schwab_cancel_order_dialog  # type: ignore[attr-defined]
    app_cls.fill_schwab_order_main_ticket_only = _fill_main_ticket_from_schwab_row  # type: ignore[attr-defined]


def _load_schwab_recent_orders(self: tk.Tk) -> None:
    try:
        days_back = _recent_days_back(self)
        session = self._authorize_schwab_session()
        if session is None:
            return
        to_time = datetime.now(timezone.utc)
        from_time = to_time - timedelta(days=days_back)
        account_hash = session.get_account_hash()
        status_code, payload = session.get_orders(from_entered_time=from_time, to_entered_time=to_time)
        rows = order_management.normalize_order_rows(payload, account_hash=account_hash) if status_code == 200 else []
        table = getattr(self, "schwab_recent_orders_table", None)
        if table is not None:
            options_lab_extension._populate_workspace_schwab_orders_table(table, rows)
        _select_schwab_order_tab(self, "recent")
        self.schwab_status_var.set("Schwab: connected")
        _set_schwab_output(
            self,
            _orders_loaded_text(
                "SCHWAB RECENT ORDERS",
                status_code=status_code,
                rows=rows,
                days_back=days_back,
                payload=payload,
            ),
        )
    except Exception as exc:
        self.schwab_status_var.set("Schwab: order load failed")
        messagebox.showerror("Load Schwab recent orders failed", str(exc))


def _load_schwab_open_orders_only(self: tk.Tk) -> None:
    try:
        session = self._authorize_schwab_session()
        if session is None:
            return
        to_time = datetime.now(timezone.utc)
        from_time = to_time - timedelta(days=60)
        account_hash = session.get_account_hash()
        status_code, payload = session.get_orders(from_entered_time=from_time, to_entered_time=to_time)
        rows = order_management.open_order_rows(payload, account_hash=account_hash) if status_code == 200 else []
        table = getattr(self, "schwab_open_orders_table", None)
        if table is not None:
            options_lab_extension._populate_workspace_schwab_orders_table(table, rows)
        _select_schwab_order_tab(self, "open")
        self.schwab_status_var.set("Schwab: connected")
        if status_code == 200:
            self.open_only_verified_this_session = True
            updater = getattr(self, "_update_verification_status", None)
            if callable(updater):
                updater()
        _set_schwab_output(
            self,
            _orders_loaded_text(
                "SCHWAB OPEN ORDERS",
                status_code=status_code,
                rows=rows,
                days_back=60,
                payload=payload,
                open_only=True,
            ),
        )
    except Exception as exc:
        self.schwab_status_var.set("Schwab: order load failed")
        messagebox.showerror("Load Schwab open orders failed", str(exc))


def _show_schwab_working_order_dialog(self: tk.Tk, row: SchwabOrderRow) -> None:
    window = tk.Toplevel(self)
    window.title("Replace Schwab Order")
    window.geometry("820x680")
    window.minsize(720, 560)
    window.transient(self)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(2, weight=1)

    header = ttk.LabelFrame(window, text="Edit Schwab Working Order", style="Card.TLabelframe")
    header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
    header.columnconfigure((1, 3), weight=1)
    _readonly_pair(header, 0, "Order ID", row.order_id, "Status", row.status)
    _readonly_pair(header, 1, "Entered", row.entered_time or "--", "Filled", _number(row.filled_quantity))
    _readonly_pair(header, 2, "Remaining", _number(row.remaining_quantity), "Account", row.masked_account_hash)

    fields = ttk.LabelFrame(window, text="Replacement Fields", style="Card.TLabelframe")
    fields.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
    fields.columnconfigure((1, 3), weight=1)

    symbol_var = tk.StringVar(value=row.symbol)
    instruction_var = tk.StringVar(value=row.instruction)
    quantity_var = tk.StringVar(value=_number(row.remaining_quantity or row.quantity))
    order_type_var = tk.StringVar(value=row.order_type if row.order_type != "UNKNOWN" else "LIMIT")
    limit_price_var = tk.StringVar(value=_price(row.price))
    stop_price_var = tk.StringVar(value=_price(row.stop_price))
    tif_var = tk.StringVar(value=_display_tif(row))
    confirmation_var = tk.StringVar(value="")

    _field_pair(fields, 0, "Symbol", ttk.Entry(fields, textvariable=symbol_var), "Side", ttk.Combobox(fields, textvariable=instruction_var, values=_instruction_choices(row), state="readonly"))
    _field_pair(fields, 1, "Quantity", ttk.Entry(fields, textvariable=quantity_var), "Order type", ttk.Combobox(fields, textvariable=order_type_var, values=("MARKET", "LIMIT", "STOP", "STOP_LIMIT"), state="readonly"))
    _field_pair(fields, 2, "Limit price", ttk.Entry(fields, textvariable=limit_price_var), "Stop price", ttk.Entry(fields, textvariable=stop_price_var))
    _field_pair(fields, 3, "TIF / duration", ttk.Combobox(fields, textvariable=tif_var, values=SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES + ("DAY", "GOOD_TILL_CANCEL"), state="readonly"), "Confirm", ttk.Entry(fields, textvariable=confirmation_var))

    output = tk.Text(fields, height=9, wrap=tk.WORD, font=("Cascadia Mono", 9), padx=10, pady=8)
    output.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(10, 0))
    _set_dialog_text(
        output,
        "Double-click opened this dialog only. No Schwab API call was made.\n\n"
        f"To replace, type: {order_management.required_confirmation('REPLACE', row.order_id)}\n"
        f"To cancel, type: {order_management.required_confirmation('CANCEL', row.order_id)}",
    )

    actions = ttk.Frame(window, style="Panel.TFrame")
    actions.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 12))
    actions.columnconfigure((0, 1, 2, 3, 4), weight=1)

    def edits() -> dict[str, Any]:
        return {
            "symbol": symbol_var.get(),
            "instruction": instruction_var.get(),
            "quantity": quantity_var.get(),
            "order_type": order_type_var.get(),
            "limit_price": limit_price_var.get(),
            "stop_price": stop_price_var.get(),
            "time_in_force": tif_var.get(),
            "asset_type": row.asset_type,
            "session": row.session,
            "duration": row.duration,
        }

    ttk.Button(
        actions,
        text="Fill Main Ticket Only",
        command=lambda: _fill_ticket_from_edits(self, row, edits(), output),
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(
        actions,
        text="Preview Replacement",
        command=lambda: _preview_replacement(self, row, edits(), output),
    ).grid(row=0, column=1, sticky="ew", padx=(0, 6))
    ttk.Button(
        actions,
        text="Replace Order / Send Edit",
        command=lambda: _replace_order_guarded(self, row, edits(), confirmation_var.get(), output),
        style="Danger.TButton",
    ).grid(row=0, column=2, sticky="ew", padx=(0, 6))
    ttk.Button(
        actions,
        text="Cancel Order",
        command=lambda: _cancel_schwab_order_guarded(self, row, confirmation_var.get(), output),
        style="Danger.TButton",
    ).grid(row=0, column=3, sticky="ew", padx=(0, 6))
    ttk.Button(actions, text="Close", command=window.destroy).grid(row=0, column=4, sticky="ew")

    try:
        window.grab_set()
    except tk.TclError:
        pass


def _show_schwab_cancel_from_selection(self: tk.Tk) -> None:
    row = _selected_or_cached_open_order_row(self)
    if row is None:
        _set_schwab_output(
            self,
            "SCHWAB CANCEL BLOCKED\n"
            "=====================\n\n"
            "Load Open Orders and select the working order to cancel. The app blocks cancel-by-guess because it needs the current status and symbol before sending DELETE.",
        )
        messagebox.showinfo("Select an open order", "Load Open Orders and select the order to cancel.")
        return
    _show_schwab_cancel_order_dialog(self, row)


def _show_schwab_cancel_order_dialog(self: tk.Tk, row: SchwabOrderRow) -> None:
    window = tk.Toplevel(self)
    window.title("Cancel Schwab Order")
    window.geometry("560x300")
    window.transient(self)
    window.columnconfigure(0, weight=1)

    frame = ttk.LabelFrame(window, text="Confirm Schwab Cancel", style="Card.TLabelframe")
    frame.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
    frame.columnconfigure(1, weight=1)
    _readonly_pair(frame, 0, "Order ID", row.order_id, "Status", row.status)
    _readonly_pair(frame, 1, "Symbol", row.symbol, "Side", row.instruction)
    _readonly_pair(frame, 2, "Quantity", _number(row.quantity), "Limit", _price(row.price))
    confirm = tk.StringVar(value="")
    ttk.Label(frame, text=f"Type {order_management.required_confirmation('CANCEL', row.order_id)}").grid(row=3, column=0, sticky="w", pady=(12, 0), padx=(0, 8))
    ttk.Entry(frame, textvariable=confirm).grid(row=3, column=1, columnspan=3, sticky="ew", pady=(12, 0))
    buttons = ttk.Frame(frame, style="Panel.TFrame")
    buttons.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(14, 0))
    buttons.columnconfigure((0, 1), weight=1)
    ttk.Button(
        buttons,
        text="Cancel Schwab Order",
        command=lambda: _cancel_schwab_order_guarded(self, row, confirm.get(), None, close_window=window),
        style="Danger.TButton",
    ).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    ttk.Button(buttons, text="Close", command=window.destroy).grid(row=0, column=1, sticky="ew")


def _fill_main_ticket_from_schwab_row(self: tk.Tk, row: SchwabOrderRow) -> None:
    _set_main_ticket_fields(
        self,
        {
            "symbol": row.symbol,
            "instruction": row.instruction,
            "quantity": row.remaining_quantity or row.quantity or "",
            "order_type": row.order_type,
            "limit_price": row.price or "",
            "stop_price": row.stop_price or "",
            "time_in_force": _display_tif(row),
        },
    )


def _fill_ticket_from_edits(self: tk.Tk, row: SchwabOrderRow, edits: Mapping[str, Any], output: tk.Text) -> None:
    _set_main_ticket_fields(self, edits)
    text = (
        "MAIN TICKET FILLED ONLY\n"
        "=======================\n\n"
        f"Copied order {row.order_id} fields into the main Schwab ticket.\n"
        "No Schwab API call was made. No order was previewed, replaced, canceled, or submitted."
    )
    _set_dialog_text(output, text)
    _set_schwab_output(self, text)


def _preview_replacement(self: tk.Tk, row: SchwabOrderRow, edits: Mapping[str, Any], output: tk.Text) -> None:
    try:
        replacement = order_management.build_replacement_order_json(row, edits)
        session = self._authorize_schwab_session()
        if session is None:
            return
        status_code, payload = session.preview_order(replacement)
        formatted = _format_replacement_preview(row, replacement, status_code=status_code, payload=payload)
        _set_dialog_text(output, formatted)
        _set_schwab_output(self, formatted)
    except Exception as exc:
        try:
            replacement = order_management.build_replacement_order_json(row, edits)
            generated = json.dumps(replacement, indent=2)
        except Exception:
            generated = "<replacement JSON could not be built>"
        text = (
            "SCHWAB REPLACEMENT PREVIEW FAILED\n"
            "=================================\n\n"
            f"{exc}\n\n"
            "Generated replacement JSON:\n"
            f"{generated}\n\n"
            "No replace request was sent."
        )
        _set_dialog_text(output, text)
        _set_schwab_output(self, text)


def _replace_order_guarded(self: tk.Tk, row: SchwabOrderRow, edits: Mapping[str, Any], typed: str, output: tk.Text) -> None:
    try:
        order_management.validate_replace_allowed(row, typed)
        replacement = order_management.build_replacement_order_json(row, edits)
        max_dollars = float(os.getenv("SCHWAB_MAX_LIVE_ORDER_DOLLARS", "500"))
        notional = order_management.replacement_notional(replacement)
        if notional is not None and notional > max_dollars:
            raise ValueError(f"Replacement notional ${notional:,.2f} exceeds SCHWAB_MAX_LIVE_ORDER_DOLLARS=${max_dollars:,.2f}.")
        if os.getenv("SCHWAB_ENABLE_LIVE_ORDERS", "").strip().lower() != "true":
            raise ValueError("SCHWAB_ENABLE_LIVE_ORDERS=true is required before sending a live replace.")

        diff = order_management.replacement_diff(row, replacement)
        ok = messagebox.askyesno(
            "Final Schwab Replace Confirmation",
            "This will send a LIVE Schwab replace request.\n\n"
            + "\n".join(f"{label}: {old or '--'} -> {new or '--'}" for label, old, new in diff)
            + "\n\nContinue?",
        )
        if not ok:
            return

        session = self._authorize_schwab_session()
        if session is None:
            return
        preview_status_code, preview_payload = session.preview_order(replacement)
        strategy = (preview_payload or {}).get("orderStrategy", {}) if isinstance(preview_payload, dict) else {}
        schwab_status = str(strategy.get("status") or "UNKNOWN").upper()
        if preview_status_code != 200 or schwab_status != "ACCEPTED":
            text = (
                "SCHWAB REPLACE BLOCKED\n"
                "======================\n\n"
                f"Immediate preview HTTP status: {preview_status_code}\n"
                f"Immediate preview Schwab status: {schwab_status}\n\n"
                "No replace request was sent because Schwab previewOrder did not return ACCEPTED."
            )
            _set_dialog_text(output, text)
            _set_schwab_output(self, text)
            return

        status_code, payload, location = session.replace_order(row.order_id, replacement)
        text = (
            "SCHWAB REPLACE RESULT\n"
            "=====================\n\n"
            "Flow: official Trader API PUT replace.\n"
            f"Original order ID: {row.order_id}\n"
            f"Symbol: {row.symbol}\n"
            f"HTTP Status: {status_code}\n"
            f"Location: {location or '(none returned)'}\n"
            f"Response: {payload if payload is not None else '(empty response body)'}\n\n"
            "Replacement payload:\n"
            f"{json.dumps(replacement, indent=2)}\n\n"
            "Refreshing Open Orders now."
        )
        _set_dialog_text(output, text)
        _set_schwab_output(self, text)
        _append_order_action_audit("REPLACE", row, status_code, replacement)
        self.load_schwab_open_orders_only()
        _set_schwab_output(self, text + "\n\nOpen Orders table refreshed.")
    except Exception as exc:
        text = (
            "SCHWAB REPLACE BLOCKED\n"
            "======================\n\n"
            f"{exc}\n\n"
            "No replace request was sent."
        )
        _set_dialog_text(output, text)
        _set_schwab_output(self, text)


def _cancel_schwab_order_guarded(
    self: tk.Tk,
    row: SchwabOrderRow,
    typed: str,
    output: tk.Text | None,
    *,
    close_window: tk.Toplevel | None = None,
) -> None:
    try:
        order_management.validate_cancel_allowed(row, typed)
        ok = messagebox.askyesno(
            "Final Schwab Cancel Confirmation",
            "This will send a LIVE Schwab cancel request.\n\n"
            f"Order ID: {row.order_id}\n"
            f"Symbol: {row.symbol}\n"
            f"Status: {row.status}\n\n"
            "Continue?",
        )
        if not ok:
            return
        session = self._authorize_schwab_session()
        if session is None:
            return
        status_code, payload = session.cancel_order(row.order_id)
        text = (
            "SCHWAB CANCEL ORDER RESULT\n"
            "==========================\n\n"
            f"Order ID: {row.order_id}\n"
            f"Symbol: {row.symbol}\n"
            f"HTTP Status: {status_code}\n"
            f"Response: {payload if payload is not None else '(empty response body)'}\n\n"
            "Refreshing Open Orders now. No replacement or submit request was sent."
        )
        if output is not None:
            _set_dialog_text(output, text)
        _set_schwab_output(self, text)
        _append_order_action_audit("CANCEL", row, status_code, None)
        if close_window is not None:
            close_window.destroy()
        self.load_schwab_open_orders_only()
        _set_schwab_output(self, text + "\n\nOpen Orders table refreshed.")
    except Exception as exc:
        text = (
            "SCHWAB CANCEL BLOCKED\n"
            "=====================\n\n"
            f"{exc}\n\n"
            "No cancel request was sent."
        )
        if output is not None:
            _set_dialog_text(output, text)
        _set_schwab_output(self, text)


def _selected_or_cached_open_order_row(self: tk.Tk) -> SchwabOrderRow | None:
    row = options_lab_extension._selected_schwab_order_row(self)
    if row is not None:
        return row
    order_id = str(getattr(getattr(self, "cancel_order_id_var", None), "get", lambda: "")()).strip()
    if not order_id:
        return None
    table = getattr(self, "schwab_open_orders_table", None)
    rows_by_iid = getattr(table, "_schwab_order_rows_by_iid", {}) if table is not None else {}
    if isinstance(rows_by_iid, dict):
        for cached in rows_by_iid.values():
            if getattr(cached, "order_id", "") == order_id:
                return cached
    return None


def _orders_loaded_text(
    title: str,
    *,
    status_code: int,
    rows: list[SchwabOrderRow],
    days_back: int,
    payload: Any,
    open_only: bool = False,
) -> str:
    lines = [
        title,
        "=" * len(title),
        "",
        f"HTTP Status: {status_code}",
        f"Date window: last {days_back} day(s)",
        f"Loaded order count: {len(rows)}",
        "",
    ]
    if status_code != 200:
        lines.extend(["Schwab returned an error payload:", str(payload)])
        return "\n".join(lines)
    if not rows:
        lines.append("No active working Schwab orders found." if open_only else "No recent Schwab orders returned for this window.")
        lines.append("")
        lines.append("No live order action was taken.")
        return "\n".join(lines)
    for row in rows[:20]:
        lines.append(
            f"- {row.order_id}: {row.status} {row.instruction} {_number(row.quantity)} {row.symbol} "
            f"{row.order_type} limit {_price(row.price) or '--'} stop {_price(row.stop_price) or '--'}"
        )
    if len(rows) > 20:
        lines.append(f"- ... {len(rows) - 20} more row(s) in the table")
    lines.extend(["", "Double-clicking an Open Orders row opens the guarded edit dialog. It does not submit, replace, or cancel."])
    return "\n".join(lines)


def _format_replacement_preview(row: SchwabOrderRow, replacement: Mapping[str, Any], *, status_code: int, payload: Any) -> str:
    return (
        "SCHWAB REPLACEMENT PREVIEW\n"
        "==========================\n\n"
        "Schwab previewOrder was called for the generated replacement payload. No PUT replace request was sent.\n\n"
        f"Original order ID: {row.order_id}\n"
        f"HTTP Status: {status_code}\n\n"
        "Old vs new:\n"
        + "\n".join(f"- {label}: {old or '--'} -> {new or '--'}" for label, old, new in order_management.replacement_diff(row, replacement))
        + "\n\nReplacement JSON:\n"
        + json.dumps(replacement, indent=2)
        + "\n\nPreview payload:\n"
        + (json.dumps(payload, indent=2) if isinstance(payload, (dict, list)) else str(payload))
    )


def _set_main_ticket_fields(self: tk.Tk, values: Mapping[str, Any]) -> None:
    symbol = str(values.get("symbol") or "").strip().upper()
    instruction = str(values.get("instruction") or "").strip().upper()
    order_type = str(values.get("order_type") or "").strip().lower()
    order_type = "stop_limit" if order_type == "stop_limit" else order_type
    if symbol:
        self.symbol_var.set(symbol)
    if instruction in {"BUY", "BUY_TO_OPEN", "BUY_TO_CLOSE"}:
        self.side_var.set("buy")
    elif instruction in {"SELL", "SELL_TO_OPEN", "SELL_TO_CLOSE"}:
        self.side_var.set("sell")
    if order_type:
        self.order_type_var.set(order_type)
    quantity = values.get("quantity")
    if quantity not in (None, ""):
        self.quantity_var.set(_number(quantity))
    limit_price = values.get("limit_price")
    if limit_price not in (None, ""):
        self.limit_price_var.set(_price(limit_price))
        if hasattr(self, "estimated_price_var"):
            self.estimated_price_var.set(_price(limit_price))
    stop_price = values.get("stop_price")
    if stop_price not in (None, ""):
        self.stop_price_var.set(_price(stop_price))
    tif = str(values.get("time_in_force") or "").strip()
    if tif:
        self.time_in_force_var.set(tif)


def _select_schwab_order_tab(self: tk.Tk, tab_name: str) -> None:
    notebook = getattr(self, "schwab_workspace_orders_notebook", None)
    tab = getattr(self, f"schwab_{tab_name}_orders_tab", None)
    if notebook is not None and tab is not None:
        try:
            notebook.select(tab)
        except tk.TclError:
            pass


def _set_schwab_output(self: tk.Tk, content: str) -> None:
    output = getattr(self, "schwab_trading_preview_text", None)
    if output is not None:
        options_lab_extension._set_workspace_text(output, content)
        return
    setter = getattr(self, "_set_preview_text", None)
    if callable(setter):
        setter(content)


def _set_dialog_text(output: tk.Text, content: str) -> None:
    output.configure(state=tk.NORMAL)
    output.delete("1.0", tk.END)
    output.insert(tk.END, content)
    output.configure(state=tk.DISABLED)


def _recent_days_back(self: tk.Tk) -> int:
    raw = str(getattr(getattr(self, "schwab_recent_order_days_var", None), "get", lambda: "7")()).strip()
    try:
        days = int(raw)
    except ValueError:
        days = 7
    return min(max(days, 1), 60)


def _append_order_action_audit(action: str, row: SchwabOrderRow, status_code: int, payload: Mapping[str, Any] | None) -> None:
    path = os.path.join("data", "schwab_order_actions.csv")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", encoding="utf-8", newline="") as handle:
        if not exists:
            handle.write("timestamp,action,order_id,symbol,status,http_status,payload\n")
        safe_payload = json.dumps(payload or {}, sort_keys=True).replace('"', '""')
        handle.write(
            f"{datetime.now(timezone.utc).isoformat()},{action},{row.order_id},{row.symbol},{row.status},{status_code},\"{safe_payload}\"\n"
        )


def _readonly_pair(parent: ttk.Frame, row: int, label_a: str, value_a: str, label_b: str, value_b: str) -> None:
    ttk.Label(parent, text=label_a, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
    ttk.Label(parent, text=value_a).grid(row=row, column=1, sticky="w", padx=(0, 16), pady=4)
    ttk.Label(parent, text=label_b, style="Subtle.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 8), pady=4)
    ttk.Label(parent, text=value_b).grid(row=row, column=3, sticky="w", pady=4)


def _field_pair(parent: ttk.Frame, row: int, label_a: str, widget_a: tk.Widget, label_b: str, widget_b: tk.Widget) -> None:
    ttk.Label(parent, text=label_a, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 8), pady=5)
    widget_a.grid(row=row, column=1, sticky="ew", padx=(0, 16), pady=5)
    ttk.Label(parent, text=label_b, style="Subtle.TLabel").grid(row=row, column=2, sticky="w", padx=(0, 8), pady=5)
    widget_b.grid(row=row, column=3, sticky="ew", pady=5)


def _display_tif(row: SchwabOrderRow) -> str:
    if row.duration == "GOOD_TILL_CANCEL":
        return "GTC"
    return "Day"


def _instruction_choices(row: SchwabOrderRow) -> tuple[str, ...]:
    if row.asset_type == "OPTION" or "_TO_" in row.instruction:
        return ("BUY_TO_OPEN", "SELL_TO_OPEN", "BUY_TO_CLOSE", "SELL_TO_CLOSE")
    return ("BUY", "SELL")


def _number(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return "" if value in (None, "") else str(value)


def _price(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return "" if value in (None, "") else str(value)
