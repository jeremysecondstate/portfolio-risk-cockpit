from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Mapping, Type
import webbrowser

from app.analytics.option_contract_inspector import parse_occ_option_symbol
from app.storage.trade_memory_store import (
    TradeMemoryStore,
    compare_then_now,
    match_snapshot_to_order,
    normalize_order_side,
    order_identity,
)
from app.ui.polished_theme import BORDER, DANGER, MUTED, PANEL, PANEL_ALT, SURFACE, TEXT


_installed = False
_ORIGINAL_RUN_SCHWAB_PREVIEW = None
_ORIGINAL_SUBMIT_LIVE_SCHWAB = None
_ORIGINAL_FORMAT_RECENT_ORDERS = None
_ORIGINAL_FORMAT_OPEN_ORDERS_ONLY = None


def install_schwab_trade_memory_extension(app_cls: Type[tk.Tk]) -> None:
    """Install Schwab Trade Memory controls and snapshot hooks."""

    global _installed, _ORIGINAL_RUN_SCHWAB_PREVIEW, _ORIGINAL_SUBMIT_LIVE_SCHWAB
    global _ORIGINAL_FORMAT_RECENT_ORDERS, _ORIGINAL_FORMAT_OPEN_ORDERS_ONLY
    if _installed:
        return

    _ORIGINAL_RUN_SCHWAB_PREVIEW = getattr(app_cls, "run_schwab_preview", None)
    _ORIGINAL_SUBMIT_LIVE_SCHWAB = getattr(app_cls, "submit_live_schwab_order", None)
    _ORIGINAL_FORMAT_RECENT_ORDERS = getattr(app_cls, "format_schwab_open_orders_response", None)
    _ORIGINAL_FORMAT_OPEN_ORDERS_ONLY = getattr(app_cls, "format_schwab_open_orders_only_response", None)

    app_cls.run_schwab_preview = _run_schwab_preview_with_trade_memory  # type: ignore[method-assign]
    # app_cls.submit_live_schwab_order = _submit_live_schwab_with_trade_memory  # type: ignore[method-assign]
    app_cls.format_schwab_open_orders_response = _format_recent_orders_with_trade_memory  # type: ignore[method-assign]
    app_cls.format_schwab_open_orders_only_response = _format_open_orders_only_with_trade_memory  # type: ignore[method-assign]
    app_cls.show_schwab_trade_memory = _show_schwab_trade_memory  # type: ignore[attr-defined]
    app_cls.save_schwab_trade_thesis_now = _save_schwab_trade_thesis_now  # type: ignore[attr-defined]
    app_cls.open_trade_memory_for_symbol = _open_trade_memory_for_symbol  # type: ignore[attr-defined]
    app_cls.trade_memory_has_snapshot_for_symbol = _trade_memory_has_snapshot_for_symbol  # type: ignore[attr-defined]

    original_build_layout = app_cls._build_layout

    def build_layout_with_trade_memory(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _install_trade_memory_controls(self))

    app_cls._build_layout = build_layout_with_trade_memory  # type: ignore[method-assign]
    _installed = True


def _run_schwab_preview_with_trade_memory(self: tk.Tk) -> None:
    _ensure_trade_memory_vars(self)
    _sync_trade_memory_checkbox_default(self)
    raw_order = _try_build_order_json(self)
    if callable(_ORIGINAL_RUN_SCHWAB_PREVIEW):
        _ORIGINAL_RUN_SCHWAB_PREVIEW(self)
    if _save_with_order_enabled(self):
        _save_snapshot_safely(self, source="preview", raw_order_json=raw_order)


def _submit_live_schwab_with_trade_memory(self: tk.Tk) -> None:
    _ensure_trade_memory_vars(self)
    _sync_trade_memory_checkbox_default(self)
    raw_order = _try_build_order_json(self)
    if callable(_ORIGINAL_SUBMIT_LIVE_SCHWAB):
        _ORIGINAL_SUBMIT_LIVE_SCHWAB(self)
    if _save_with_order_enabled(self):
        _save_or_update_live_snapshot_safely(self, raw_order)


def _format_recent_orders_with_trade_memory(self: tk.Tk, status_code: int, payload: Any) -> str:
    if callable(_ORIGINAL_FORMAT_RECENT_ORDERS):
        base = _ORIGINAL_FORMAT_RECENT_ORDERS(self, status_code, payload)
    else:
        base = str(payload)
    return _append_trade_memory_order_matches(self, base, payload, title="Trade Memory matches")


def _format_open_orders_only_with_trade_memory(self: tk.Tk, status_code: int, payload: Any) -> str:
    if callable(_ORIGINAL_FORMAT_OPEN_ORDERS_ONLY):
        base = _ORIGINAL_FORMAT_OPEN_ORDERS_ONLY(self, status_code, payload)
    else:
        base = str(payload)
    if isinstance(payload, list):
        active = set()
        getter = getattr(self, "schwab_active_order_statuses", None)
        if callable(getter):
            try:
                active = set(getter())
            except Exception:
                active = set()
        if active:
            payload = [order for order in payload if str(order.get("status", "")).upper() in active]
    return _append_trade_memory_order_matches(self, base, payload, title="Open-order Trade Memory matches")


def _install_trade_memory_controls(self: tk.Tk) -> None:
    if getattr(self, "_schwab_trade_memory_controls_built", False):
        return
    _ensure_trade_memory_vars(self)
    actions = _find_labelframe(self, "Schwab Actions")
    if actions is None:
        return

    controls = ttk.Frame(actions, style="Panel.TFrame")
    controls.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
    controls.columnconfigure(0, weight=1)
    controls.columnconfigure(1, weight=0)
    controls.columnconfigure(2, weight=0)
    ttk.Checkbutton(
        controls,
        text="Save thesis snapshot with order",
        variable=self.trade_memory_save_with_order_var,
        command=lambda app=self: setattr(app, "_trade_memory_checkbox_touched", True),
    ).grid(row=0, column=0, sticky="w")
    ttk.Button(controls, text="Save Thesis Now", command=self.save_schwab_trade_thesis_now).grid(row=0, column=1, sticky="e", padx=(8, 0))
    ttk.Button(controls, text="Trade Memory", command=self.show_schwab_trade_memory, style="Accent.TButton").grid(row=0, column=2, sticky="e", padx=(8, 0))
    ttk.Label(actions, textvariable=self.trade_memory_status_var, style="Subtle.TLabel").grid(row=5, column=0, columnspan=3, sticky="w", pady=(4, 0))
    self._schwab_trade_memory_controls_built = True
    _sync_trade_memory_checkbox_default(self)


def _ensure_trade_memory_vars(self: tk.Tk) -> None:
    if not hasattr(self, "trade_memory_save_with_order_var"):
        self.trade_memory_save_with_order_var = tk.BooleanVar(value=_has_current_research_report(self))
    if not hasattr(self, "trade_memory_status_var"):
        self.trade_memory_status_var = tk.StringVar(value="Trade Memory: local snapshots")


def _sync_trade_memory_checkbox_default(self: tk.Tk) -> None:
    if getattr(self, "_trade_memory_checkbox_touched", False):
        return
    if _has_current_research_report(self):
        self.trade_memory_save_with_order_var.set(True)


def _save_with_order_enabled(self: tk.Tk) -> bool:
    _ensure_trade_memory_vars(self)
    try:
        return bool(self.trade_memory_save_with_order_var.get())
    except Exception:
        return False


def _save_schwab_trade_thesis_now(self: tk.Tk) -> None:
    record = _save_snapshot_safely(self, source="manual_snapshot", raw_order_json=_try_build_order_json(self))
    if record is not None:
        messagebox.showinfo("Thesis snapshot saved", f"Saved {record['snapshot_id']}")


def _save_snapshot_safely(
    self: tk.Tk,
    *,
    source: str,
    raw_order_json: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    _ensure_trade_memory_vars(self)
    try:
        record = _build_snapshot_record(self, source=source, raw_order_json=raw_order_json)
        saved = _store(self).save_snapshot(record)
        self.trade_memory_last_snapshot_id = saved["snapshot_id"]
        if hasattr(self, "trade_memory_status_var"):
            self.trade_memory_status_var.set(f"Trade Memory: saved {saved['snapshot_id']}")
        return saved
    except Exception as exc:
        if hasattr(self, "trade_memory_status_var"):
            self.trade_memory_status_var.set(f"Trade Memory: save failed ({exc})")
        return None


def _save_or_update_live_snapshot_safely(self: tk.Tk, raw_order_json: Mapping[str, Any] | None) -> None:
    _ensure_trade_memory_vars(self)
    try:
        record = _build_snapshot_record(self, source="live_submit", raw_order_json=raw_order_json)
        store = _store(self)
        snapshot_id = str(getattr(self, "trade_memory_last_snapshot_id", "") or "")
        updated = None
        if snapshot_id:
            updated = store.update_snapshot(
                snapshot_id,
                {
                    "source": "live_submit",
                    "order_status": record.get("order_status"),
                    "order_id": record.get("order_id"),
                    "order_location": record.get("order_location"),
                    "schwab_response_text": record.get("schwab_response_text"),
                    "raw_order_json": record.get("raw_order_json"),
                },
            )
        saved = updated or store.save_snapshot(record)
        self.trade_memory_last_snapshot_id = saved["snapshot_id"]
        if hasattr(self, "trade_memory_status_var"):
            self.trade_memory_status_var.set(f"Trade Memory: saved {saved['snapshot_id']}")
    except Exception as exc:
        if hasattr(self, "trade_memory_status_var"):
            self.trade_memory_status_var.set(f"Trade Memory: save failed ({exc})")


def _show_schwab_trade_memory(self: tk.Tk) -> None:
    existing = getattr(self, "schwab_trade_memory_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                _populate_trade_memory_table(self)
                return
        except tk.TclError:
            pass

    _ensure_trade_memory_styles(self)
    window = tk.Toplevel(self)
    window.title("Schwab Trade Memory")
    window.geometry("1120x650")
    window.minsize(780, 440)
    window.configure(bg=PANEL)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    header = ttk.Frame(window, style="Panel.TFrame", padding=(14, 12))
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(1, weight=1)
    ttk.Label(header, text="Schwab Trade Memory", style="MemoryTitle.TLabel").grid(row=0, column=0, sticky="w")
    ttk.Label(header, text="Evidence receipts for what the cockpit saw at order time.", style="Subtle.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
    self.trade_memory_filter_var = tk.StringVar(value="All")
    filters = ("All", "Open", "Filled", "Cancelled", "Has Thesis", "Missing Thesis", "Options Only", "Equities Only")
    ttk.Combobox(header, textvariable=self.trade_memory_filter_var, values=filters, state="readonly", width=18).grid(row=0, column=2, sticky="e", padx=(12, 0))
    ttk.Button(header, text="Refresh", command=lambda app=self: _populate_trade_memory_table(app)).grid(row=0, column=3, sticky="e", padx=(8, 0))
    ttk.Button(header, text="Save Thesis Now", command=self.save_schwab_trade_thesis_now).grid(row=0, column=4, sticky="e", padx=(8, 0))

    body = ttk.Frame(window, style="Panel.TFrame", padding=(14, 0, 14, 14))
    body.grid(row=1, column=0, sticky="nsew")
    body.rowconfigure(0, weight=1)
    body.columnconfigure(0, weight=1)
    columns = ("created", "symbol", "instrument", "side", "qty", "status", "thesis", "pnl", "order")
    tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
    headings = {
        "created": ("Created", 150, tk.W),
        "symbol": ("Symbol", 170, tk.W),
        "instrument": ("Instrument", 90, tk.W),
        "side": ("Side", 80, tk.W),
        "qty": ("Qty", 80, tk.E),
        "status": ("Status", 110, tk.W),
        "thesis": ("Thesis", 120, tk.W),
        "pnl": ("P&L", 90, tk.E),
        "order": ("Order", 160, tk.W),
    }
    for column, (label, width, anchor) in headings.items():
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=anchor, stretch=column in {"symbol", "order"})
    tree.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=tree.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=scrollbar.set)
    tree.bind("<Double-1>", lambda event, app=self, source=tree: _open_selected_trade_memory_snapshot(app, source, event), add="+")

    self.schwab_trade_memory_window = window
    self.schwab_trade_memory_tree = tree
    self.trade_memory_filter_var.trace_add("write", lambda *_args, app=self: _populate_trade_memory_table(app))
    _populate_trade_memory_table(self)


def _populate_trade_memory_table(self: tk.Tk) -> None:
    tree = getattr(self, "schwab_trade_memory_tree", None)
    if tree is None:
        return
    records = _filtered_snapshots(self, _store(self).load_snapshots())
    for row_id in tree.get_children():
        tree.delete(row_id)
    tree._trade_memory_records = {}  # type: ignore[attr-defined]
    for index, record in enumerate(records):
        iid = f"snapshot_{index}"
        tree._trade_memory_records[iid] = record  # type: ignore[attr-defined]
        ticket = record.get("order_ticket") if isinstance(record.get("order_ticket"), dict) else {}
        tree.insert(
            "",
            tk.END,
            iid=iid,
            values=(
                _short_created(record.get("created_at")),
                record.get("symbol") or "--",
                record.get("instrument_type") or "--",
                ticket.get("side") or "--",
                ticket.get("quantity") or "--",
                record.get("order_status") or "--",
                str(record.get("thesis_status") or "unknown").upper(),
                "--",
                record.get("order_id") or _short_location(record.get("order_location")) or "--",
            ),
        )


def _open_selected_trade_memory_snapshot(self: tk.Tk, tree: ttk.Treeview, event: tk.Event | None = None) -> None:
    row_id = tree.identify_row(event.y) if event is not None else tree.focus()
    if row_id:
        tree.selection_set(row_id)
        tree.focus(row_id)
    selected = tree.focus() or (tree.selection()[0] if tree.selection() else "")
    records = getattr(tree, "_trade_memory_records", {}) or {}
    record = records.get(selected)
    if record is None:
        return
    _open_trade_memory_inspector(self, record)


def _open_trade_memory_for_symbol(self: tk.Tk, symbol: str) -> None:
    records = _store(self).find_snapshots_for_symbol(symbol)
    if records:
        _open_trade_memory_inspector(self, records[0])
        return
    answer = messagebox.askyesno(
        "No original thesis snapshot",
        "No original thesis snapshot was saved for this symbol.\n\nCreate a local note snapshot now?",
    )
    if answer:
        _save_snapshot_safely(self, source="manual_snapshot", raw_order_json=_try_build_order_json(self))


def _trade_memory_has_snapshot_for_symbol(self: tk.Tk, symbol: str) -> bool:
    return bool(_store(self).find_snapshots_for_symbol(symbol))


def _open_trade_memory_inspector(self: tk.Tk, record: Mapping[str, Any]) -> None:
    _ensure_trade_memory_styles(self)
    window = tk.Toplevel(self)
    window.title("Trade Memory Inspector")
    window.geometry("980x760")
    window.minsize(760, 560)
    window.configure(bg=PANEL)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    header = tk.Frame(window, bg=SURFACE, padx=18, pady=16)
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(0, weight=1)
    _build_memory_header(header, record)

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
    content.columnconfigure(0, weight=1, uniform="memory")
    content.columnconfigure(1, weight=1, uniform="memory")
    content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_id, width=event.width))

    _memory_order_details(content, record, row=0, column=0)
    _memory_original_thesis(content, record, row=0, column=1)
    _memory_current_read(content, record, row=1, column=0)
    compare_var = tk.StringVar(value=_comparison_text(self, record))
    _memory_then_now(content, compare_var, row=1, column=1)
    notes_widgets = _memory_notes(content, record, row=2, column=0, columnspan=2)
    _memory_reports(content, record, row=3, column=0, columnspan=2)

    footer = ttk.Frame(window, style="Panel.TFrame", padding=(14, 10, 14, 14))
    footer.grid(row=2, column=0, sticky="ew")
    for column in range(5):
        footer.columnconfigure(column, weight=1, uniform="memory_buttons")
    ttk.Button(footer, text="Open Original Thesis Report", command=lambda rec=record: _open_report(rec), style="Accent.TButton").grid(row=0, column=0, sticky="ew", padx=(0, 8))
    ttk.Button(footer, text="Export PDF", command=_export_pdf_unavailable).grid(row=0, column=1, sticky="ew", padx=(0, 8))
    ttk.Button(footer, text="Compare Then vs Now", command=lambda app=self, rec=record, var=compare_var: var.set(_comparison_text(app, rec))).grid(row=0, column=2, sticky="ew", padx=(0, 8))
    ttk.Button(footer, text="Copy Summary", command=lambda app=self, rec=record: _copy_memory_summary(app, rec)).grid(row=0, column=3, sticky="ew", padx=(0, 8))
    ttk.Button(footer, text="Close", command=window.destroy).grid(row=0, column=4, sticky="ew")
    ttk.Button(footer, text="Save Notes", command=lambda app=self, rec=record, widgets=notes_widgets: _save_notes(app, rec, widgets)).grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))


def _build_snapshot_record(
    self: tk.Tk,
    *,
    source: str,
    raw_order_json: Mapping[str, Any] | None,
) -> dict[str, Any]:
    response_text = _current_preview_text(self)
    order_details = _order_details_from_app(self, raw_order_json)
    thesis_text, tab_summaries = _current_thesis_text(self)
    payload = getattr(self, "schwab_research_last_payload", None)
    option_details = order_details["option_details"]
    symbol = order_details["symbol"]
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "broker": "Schwab",
        "symbol": symbol,
        "underlying_symbol": order_details.get("underlying_symbol"),
        "instrument_type": order_details["instrument_type"],
        "option_details": option_details,
        "order_ticket": order_details["order_ticket"],
        "preview_status": str(getattr(self, "last_schwab_preview_status", "") or _extract_line_value(response_text, "Schwab Status") or ""),
        "order_location": _extract_line_value(response_text, "Location"),
        "order_id": _extract_order_id(response_text),
        "order_status": _extract_order_status(response_text),
        "source": source,
        "thesis_status": "saved" if thesis_text.strip() else "missing_analysis",
        "plain_english_summary": _plain_summary_from_payload(payload, thesis_text),
        "research_report_text": thesis_text,
        "tab_summaries": tab_summaries,
        "raw_order_json": raw_order_json or {},
        "raw_preview_response": {},
        "schwab_response_text": response_text,
        "notes": {},
        "tags": [],
        "original_posture": _original_posture(payload),
        "market_snapshot": _market_snapshot(payload),
    }
    return record


def _order_details_from_app(self: tk.Tk, raw_order_json: Mapping[str, Any] | None) -> dict[str, Any]:
    raw_order_json = raw_order_json or {}
    first_leg = _first_order_leg(raw_order_json)
    instrument = first_leg.get("instrument") if isinstance(first_leg.get("instrument"), Mapping) else {}
    raw_symbol = str(instrument.get("symbol") or _get_var(self, "symbol_var") or "UNKNOWN").strip().upper()
    asset_type = str(instrument.get("assetType") or "").upper()
    parsed = parse_occ_option_symbol(raw_symbol)
    instrument_type = "option" if parsed is not None or asset_type == "OPTION" else "equity" if raw_symbol != "UNKNOWN" else "unknown"
    symbol = raw_symbol if instrument_type == "option" else str(_get_var(self, "symbol_var") or raw_symbol).strip().upper()
    side = normalize_order_side(first_leg.get("instruction") or _get_var(self, "side_var"))
    quantity = _first_number(first_leg.get("quantity"), _get_var(self, "quantity_var"))
    price = _first_number(raw_order_json.get("price"), _get_var(self, "limit_price_var"))
    estimated = _estimated_notional(raw_order_json, quantity, price, instrument_type)
    option_details = {}
    underlying_symbol = ""
    if parsed is not None:
        underlying_symbol = parsed.underlying
        option_details = {
            "expiration": parsed.expiration.isoformat(),
            "call_put": parsed.option_type,
            "strike": parsed.strike,
            "occ_symbol": parsed.raw_symbol,
            "dte": parsed.dte,
        }

    return {
        "symbol": symbol,
        "underlying_symbol": underlying_symbol,
        "instrument_type": instrument_type,
        "option_details": option_details,
        "order_ticket": {
            "side": side or _get_var(self, "side_var"),
            "quantity": quantity,
            "order_type": raw_order_json.get("orderType") or _get_var(self, "order_type_var"),
            "limit_price": price,
            "stop_price": _get_var(self, "stop_price_var"),
            "time_in_force": raw_order_json.get("duration") or _get_var(self, "time_in_force_var"),
            "estimated_notional": estimated,
        },
    }


def _current_thesis_text(self: tk.Tk) -> tuple[str, dict[str, str]]:
    tab_attrs = {
        "Overview": "schwab_research_overview_text",
        "Evidence Desk": ("schwab_trade_evidence_frame", "detail_text"),
        "Technicals": ("schwab_research_technicals_frame", "technical_notes_text"),
        "Risk Scenarios": ("schwab_research_scenarios_frame", "scenario_note_text"),
        "Options Strategy": ("schwab_research_options_frame", "detail_text"),
        "Greeks": ("schwab_research_greeks_frame", "detail_text"),
        "Earnings / News": "schwab_research_earnings_text",
        "Fundamentals": "schwab_research_fundamentals_text",
        "Macro Context": "schwab_research_macro_text",
    }
    tabs: dict[str, str] = {}
    for name, attr in tab_attrs.items():
        widget = _resolve_widget_attr(self, attr)
        text = _widget_text(widget)
        if text.strip():
            tabs[name] = text.strip()

    joined = "\n\n".join(f"{name}\n{'=' * len(name)}\n{text}" for name, text in tabs.items())
    if joined.strip():
        return joined, tabs

    output = _current_preview_text(self)
    if "Schwab Research Workspace" in output or "Trade Evidence" in output or "Evidence posture" in output:
        return output, {"Schwab Trading Output": output}
    return "", {}


def _current_preview_text(self: tk.Tk) -> str:
    widget = getattr(self, "schwab_trading_preview_text", None) or getattr(self, "preview_text", None)
    return _widget_text(widget)


def _try_build_order_json(self: tk.Tk) -> Mapping[str, Any] | None:
    builder = getattr(self, "build_schwab_order_json_from_ui", None)
    if callable(builder):
        try:
            payload = builder()
            return payload if isinstance(payload, Mapping) else None
        except Exception:
            return None
    return None


def _store(self: tk.Tk) -> TradeMemoryStore:
    store = getattr(self, "schwab_trade_memory_store", None)
    if store is None:
        store = TradeMemoryStore()
        self.schwab_trade_memory_store = store
    return store


def _filtered_snapshots(self: tk.Tk, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = _get_var(self, "trade_memory_filter_var") or "All"
    selected = selected.lower()
    output: list[dict[str, Any]] = []
    for record in records:
        status = str(record.get("order_status") or "").upper()
        thesis = str(record.get("thesis_status") or "").lower()
        instrument = str(record.get("instrument_type") or "").lower()
        include = True
        if selected == "open":
            include = status in {"WORKING", "QUEUED", "PENDING_ACTIVATION", "ACCEPTED", "OPEN"}
        elif selected == "filled":
            include = status in {"FILLED", "EXECUTED"}
        elif selected == "cancelled":
            include = status in {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}
        elif selected == "has thesis":
            include = thesis == "saved"
        elif selected == "missing thesis":
            include = thesis == "missing_analysis"
        elif selected == "options only":
            include = instrument == "option"
        elif selected == "equities only":
            include = instrument == "equity"
        if include:
            output.append(record)
    return sorted(output, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _append_trade_memory_order_matches(self: tk.Tk, base: str, payload: Any, *, title: str) -> str:
    if not isinstance(payload, list) or not payload:
        return base
    snapshots = _store(self).load_snapshots()
    lines = [base.rstrip(), "", title + ":", "-" * len(title)]
    for index, order in enumerate(payload, start=1):
        identity = order_identity(order)
        match = match_snapshot_to_order(order, snapshots)
        label = identity.get("symbol") or f"Order {index}"
        if match is None:
            lines.append(f"- {label}: Missing thesis snapshot")
            continue
        thesis = str(match.get("thesis_status") or "unknown").upper()
        lines.append(f"- {label}: {thesis} ({match.get('snapshot_id')})")
    return "\n".join(lines)


def _memory_order_details(parent: ttk.Frame, record: Mapping[str, Any], *, row: int, column: int) -> None:
    card = _card(parent, "Order Details", row, column)
    ticket = record.get("order_ticket") if isinstance(record.get("order_ticket"), Mapping) else {}
    _kv(card, 0, "Symbol", str(record.get("symbol") or "--"))
    _kv(card, 1, "Instrument", str(record.get("instrument_type") or "--"))
    _kv(card, 2, "Side / qty", f"{ticket.get('side') or '--'} / {ticket.get('quantity') or '--'}")
    _kv(card, 3, "Order type", str(ticket.get("order_type") or "--"))
    _kv(card, 4, "Limit / stop", f"{ticket.get('limit_price') or '--'} / {ticket.get('stop_price') or '--'}")
    _kv(card, 5, "Status", str(record.get("order_status") or "--"))
    _kv(card, 6, "Order link", str(record.get("order_id") or record.get("order_location") or "--"))


def _memory_original_thesis(parent: ttk.Frame, record: Mapping[str, Any], *, row: int, column: int) -> None:
    card = _card(parent, "Original Thesis", row, column)
    _kv(card, 0, "Thesis", str(record.get("thesis_status") or "unknown").upper())
    _kv(card, 1, "Original posture", str(record.get("original_posture") or "--"))
    _note(card, 2, str(record.get("plain_english_summary") or "No research thesis was available at snapshot time."))


def _memory_current_read(parent: ttk.Frame, record: Mapping[str, Any], *, row: int, column: int) -> None:
    card = _card(parent, "Current Read", row, column)
    _note(card, 0, "Current comparison uses the latest loaded Schwab research payload when it matches the saved symbol. Refresh Schwab/research data to update this read.")


def _memory_then_now(parent: ttk.Frame, compare_var: tk.StringVar, *, row: int, column: int) -> None:
    card = _card(parent, "Then vs Now", row, column)
    ttk.Label(card, textvariable=compare_var, style="MemoryValue.TLabel", wraplength=420, justify=tk.LEFT).grid(row=0, column=0, columnspan=2, sticky="ew")


def _memory_notes(parent: ttk.Frame, record: Mapping[str, Any], *, row: int, column: int, columnspan: int) -> dict[str, Any]:
    card = _card(parent, "Notes", row, column, columnspan=columnspan)
    notes = record.get("notes") if isinstance(record.get("notes"), Mapping) else {}
    fields = {
        "original_reason": "Original reason",
        "invalidation_level": "Invalidation level",
        "target": "Target",
        "close_reduce": "Close/reduce if",
        "add": "Add if",
        "review_date": "Review date",
    }
    widgets: dict[str, Any] = {}
    for index, (key, label) in enumerate(fields.items()):
        ttk.Label(card, text=label, style="MemoryMuted.TLabel").grid(row=index, column=0, sticky="w", padx=(0, 10), pady=3)
        var = tk.StringVar(value=str(notes.get(key) or ""))
        ttk.Entry(card, textvariable=var).grid(row=index, column=1, sticky="ew", pady=3)
        widgets[key] = var
    ttk.Label(card, text="Freeform notes", style="MemoryMuted.TLabel").grid(row=len(fields), column=0, sticky="nw", padx=(0, 10), pady=3)
    text = tk.Text(card, height=4, wrap=tk.WORD, background="#ffffff", foreground=TEXT, relief=tk.FLAT, borderwidth=1)
    text.insert("1.0", str(notes.get("freeform") or ""))
    text.grid(row=len(fields), column=1, sticky="ew", pady=3)
    widgets["freeform"] = text
    return widgets


def _memory_reports(parent: ttk.Frame, record: Mapping[str, Any], *, row: int, column: int, columnspan: int) -> None:
    card = _card(parent, "Attachments / Reports", row, column, columnspan=columnspan)
    _kv(card, 0, "HTML report", str(record.get("report_path") or "--"))
    _note(card, 1, "Open Report uses the saved local HTML evidence receipt. PDF export is unavailable in v1 unless a PDF renderer is added.")


def _build_memory_header(parent: tk.Frame, record: Mapping[str, Any]) -> None:
    ticket = record.get("order_ticket") if isinstance(record.get("order_ticket"), Mapping) else {}
    title = str(record.get("symbol") or "Trade Memory")
    subtitle = f"{ticket.get('side') or '--'} {ticket.get('quantity') or '--'} | status {record.get('order_status') or '--'}"
    thesis = str(record.get("thesis_status") or "unknown").upper().replace("MISSING_ANALYSIS", "MISSING")
    badge_bg = "#dcfce7" if thesis == "SAVED" else "#fee2e2" if thesis == "MISSING" else "#e5e7eb"
    badge_fg = "#166534" if thesis == "SAVED" else DANGER if thesis == "MISSING" else "#374151"
    tk.Label(parent, text=title, bg=SURFACE, fg="#ffffff", font=("Segoe UI", 18, "bold"), anchor="w").grid(row=0, column=0, sticky="ew")
    tk.Label(parent, text=subtitle, bg=SURFACE, fg="#cbd5e1", font=("Segoe UI", 10), anchor="w").grid(row=1, column=0, sticky="ew", pady=(4, 0))
    tk.Label(parent, text=thesis, bg=badge_bg, fg=badge_fg, font=("Segoe UI", 10, "bold"), padx=12, pady=6).grid(row=0, column=1, rowspan=2, sticky="ne", padx=(16, 0))


def _comparison_text(self: tk.Tk, record: Mapping[str, Any]) -> str:
    current = _current_comparison_payload(self, record)
    comparison = compare_then_now(record, current)
    if not comparison["available"]:
        return str(comparison["summary"])
    return "\n".join(comparison["changes"])


def _current_comparison_payload(self: tk.Tk, record: Mapping[str, Any]) -> dict[str, Any] | None:
    payload = getattr(self, "schwab_research_last_payload", None)
    if payload is None:
        return None
    snapshot_symbol = str(record.get("underlying_symbol") or record.get("symbol") or "").strip().upper()
    payload_symbol = str(getattr(payload, "symbol", "") or "").strip().upper()
    if snapshot_symbol and payload_symbol and snapshot_symbol != payload_symbol:
        return None
    decision = getattr(payload, "decision", None)
    context = getattr(payload, "context", None)
    return {
        "symbol_price": getattr(context, "last_price", None),
        "posture": getattr(getattr(decision, "overall", None), "label", ""),
    }


def _save_notes(self: tk.Tk, record: Mapping[str, Any], widgets: Mapping[str, Any]) -> None:
    notes: dict[str, str] = {}
    for key, widget in widgets.items():
        if isinstance(widget, tk.Text):
            notes[key] = widget.get("1.0", tk.END).strip()
        else:
            try:
                notes[key] = str(widget.get()).strip()
            except Exception:
                notes[key] = ""
    updated = _store(self).update_snapshot(str(record.get("snapshot_id") or ""), {"notes": notes})
    if updated is None:
        messagebox.showerror("Save notes failed", "Could not find the snapshot to update.")
    else:
        messagebox.showinfo("Notes saved", "Trade Memory notes were updated.")
        _populate_trade_memory_table(self)


def _open_report(record: Mapping[str, Any]) -> None:
    path = Path(str(record.get("report_path") or ""))
    if not path.exists():
        messagebox.showinfo("Open report", "The saved HTML report file is unavailable.")
        return
    webbrowser.open(path.resolve().as_uri())


def _export_pdf_unavailable() -> None:
    messagebox.showinfo("Export PDF", "PDF export is unavailable in this v1. Open the HTML report instead.")


def _copy_memory_summary(self: tk.Tk, record: Mapping[str, Any]) -> None:
    ticket = record.get("order_ticket") if isinstance(record.get("order_ticket"), Mapping) else {}
    text = (
        "Trade Memory Inspector\n"
        f"Snapshot: {record.get('snapshot_id')}\n"
        f"Symbol: {record.get('symbol')}\n"
        f"Side/qty: {ticket.get('side') or '--'} / {ticket.get('quantity') or '--'}\n"
        f"Thesis: {record.get('thesis_status')}\n"
        f"Summary: {record.get('plain_english_summary')}\n"
    )
    self.clipboard_clear()
    self.clipboard_append(text)


def _find_labelframe(root: tk.Misc, text: str) -> ttk.LabelFrame | None:
    for child in root.winfo_children():
        if isinstance(child, ttk.LabelFrame):
            try:
                if str(child.cget("text")) == text:
                    return child
            except tk.TclError:
                pass
        found = _find_labelframe(child, text)
        if found is not None:
            return found
    return None


def _ensure_trade_memory_styles(root: tk.Misc) -> None:
    style = ttk.Style(root)
    style.configure("MemoryTitle.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 14, "bold"))
    style.configure("MemoryCard.TLabelframe", background=PANEL, bordercolor=BORDER, relief="solid", padding=14)
    style.configure("MemoryCard.TLabelframe.Label", background=PANEL, foreground=TEXT, font=("Segoe UI", 10, "bold"))
    style.configure("MemoryMuted.TLabel", background=PANEL, foreground=MUTED)
    style.configure("MemoryValue.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10, "bold"))


def _card(parent: ttk.Frame, title: str, row: int, column: int, *, columnspan: int = 1) -> ttk.LabelFrame:
    card = ttk.LabelFrame(parent, text=title, style="MemoryCard.TLabelframe")
    card.grid(row=row, column=column, columnspan=columnspan, sticky="nsew", padx=(0 if column == 0 else 10, 0), pady=(0, 10))
    card.columnconfigure(1, weight=1)
    return card


def _kv(parent: ttk.Frame, row: int, label: str, value: str) -> None:
    ttk.Label(parent, text=label, style="MemoryMuted.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, 12), pady=3)
    ttk.Label(parent, text=value or "--", style="MemoryValue.TLabel", wraplength=380, justify=tk.LEFT).grid(row=row, column=1, sticky="ew", pady=3)


def _note(parent: ttk.Frame, row: int, text: str) -> None:
    ttk.Label(parent, text=text or "--", style="MemoryValue.TLabel", wraplength=420, justify=tk.LEFT).grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 2))


def _first_order_leg(raw_order_json: Mapping[str, Any]) -> Mapping[str, Any]:
    legs = raw_order_json.get("orderLegCollection") or raw_order_json.get("orderLegs") or []
    if isinstance(legs, list) and legs and isinstance(legs[0], Mapping):
        return legs[0]
    return {}


def _estimated_notional(raw_order_json: Mapping[str, Any], quantity: float | None, price: float | None, instrument_type: str) -> float | None:
    if quantity is None or price is None:
        return None
    multiplier = 100 if instrument_type == "option" else 1
    return round(quantity * price * multiplier, 2)


def _plain_summary_from_payload(payload: Any, thesis_text: str) -> str:
    decision = getattr(payload, "decision", None)
    summary = getattr(decision, "summary", None)
    if summary:
        return " ".join(str(line) for line in summary if str(line).strip())[:800]
    for line in thesis_text.splitlines():
        clean = line.strip(" -")
        if len(clean) >= 30 and not set(clean) <= {"=", "-"}:
            return clean[:800]
    return "No research thesis was available at snapshot time."


def _original_posture(payload: Any) -> str:
    evidence = getattr(payload, "trade_evidence_report", None)
    if evidence is not None and getattr(evidence, "posture", None):
        return str(evidence.posture)
    decision = getattr(payload, "decision", None)
    if decision is not None and getattr(decision, "overall", None) is not None:
        return str(getattr(decision.overall, "label", ""))
    return "NO-READ"


def _market_snapshot(payload: Any) -> dict[str, Any]:
    context = getattr(payload, "context", None)
    if context is None:
        return {}
    return {
        "symbol_price": getattr(context, "last_price", None),
        "market_value": getattr(context, "market_value", None),
        "unrealized_pnl": getattr(context, "unrealized_pnl", None),
    }


def _extract_line_value(text: str, label: str) -> str:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def _extract_order_id(text: str) -> str:
    direct = _extract_line_value(text, "Order ID")
    if direct:
        return direct
    location = _extract_line_value(text, "Location")
    match = re.search(r"/orders/([^/?#\s]+)", location)
    return match.group(1) if match else ""


def _extract_order_status(text: str) -> str:
    status = _extract_line_value(text, "Schwab Status") or _extract_line_value(text, "Status")
    if status:
        return status.upper()
    if "SUBMIT BLOCKED" in text.upper():
        return "BLOCKED"
    if "ORDER SUBMIT RESULT" in text.upper():
        return "SUBMITTED"
    return ""


def _resolve_widget_attr(self: tk.Tk, attr: str | tuple[str, str]) -> Any:
    if isinstance(attr, str):
        return getattr(self, attr, None)
    parent = getattr(self, attr[0], None)
    return getattr(parent, attr[1], None) if parent is not None else None


def _widget_text(widget: Any) -> str:
    if widget is None:
        return ""
    try:
        return str(widget.get("1.0", tk.END)).strip()
    except Exception:
        return ""


def _get_var(self: tk.Tk, name: str) -> str:
    var = getattr(self, name, None)
    try:
        return str(var.get())
    except Exception:
        return ""


def _has_current_research_report(self: tk.Tk) -> bool:
    return bool(getattr(self, "schwab_research_last_payload", None) is not None or _current_thesis_text(self)[0].strip())


def _first_number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            return float(value)
        try:
            text = str(value).replace("$", "").replace(",", "").strip()
            if text:
                return float(text)
        except ValueError:
            continue
    return None


def _short_created(value: Any) -> str:
    text = str(value or "")
    return text.replace("T", " ")[:19]


def _short_location(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"/orders/([^/?#\s]+)", text)
    return match.group(1) if match else text[-18:]
