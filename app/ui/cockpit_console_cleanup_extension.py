from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

from app.ui import polished_theme


_UNUSED_STATUS_MARKERS = (
    "DELETE ME",
    "I AM USELESS",
    "WHY AM I HERE",
    "SERVE NO PURPOSE",
    "THIS IS USELESS",
)


def install_cockpit_console_cleanup_extension(app_cls: Type[tk.Tk]) -> None:
    """Clean up the Cockpit risk console status strip.

    The Hyperliquid cockpit extension creates the console layout. This small
    follow-up patch removes the old blue status chips and replaces them with
    compact connection checks beside the Schwab / Hyperliquid sync controls.
    """

    original_build_order_panel = app_cls._build_order_panel

    def build_order_panel_with_clean_console(self: tk.Tk, parent: ttk.Frame) -> None:
        original_build_order_panel(self, parent)
        self.after_idle(lambda app=self: _cleanup_cockpit_risk_console(app))

    app_cls._build_order_panel = build_order_panel_with_clean_console  # type: ignore[method-assign]

    original_reset = app_cls.reset_schwab_session

    def reset_schwab_session_clean(self: tk.Tk) -> None:
        original_reset(self)
        _clear_useless_preview_status(self)
        _refresh_connection_badges(self)

    app_cls.reset_schwab_session = reset_schwab_session_clean  # type: ignore[method-assign]

    original_preview = app_cls.run_schwab_preview

    def run_schwab_preview_clean(self: tk.Tk) -> None:
        original_preview(self)
        _clear_useless_preview_status(self)
        _refresh_connection_badges(self)

    app_cls.run_schwab_preview = run_schwab_preview_clean  # type: ignore[method-assign]



def _cleanup_cockpit_risk_console(app: tk.Tk) -> None:
    summary = _find_labelframe(app, "Portfolio Risk Console")
    if summary is None:
        return

    _configure_connection_styles(app)
    _clear_useless_preview_status(app)

    # Remove the old full-width blue chip row: Schwab status / preview status /
    # Hyperliquid status. Connection state now lives next to the relevant sync
    # controls instead of taking an entire row.
    for child in list(summary.winfo_children()):
        try:
            info = child.grid_info()
        except tk.TclError:
            continue
        if str(info.get("row")) == "2":
            child.destroy()

    actions = _action_frame(summary)
    if actions is not None:
        _install_connection_badges(app, actions)

    _refresh_connection_badges(app)
    _attach_status_traces(app)



def _configure_connection_styles(app: tk.Tk) -> None:
    style = ttk.Style(app)
    style.configure("ConnectionGood.TLabel", background=polished_theme.PANEL, foreground="#047857", font=("Segoe UI", 9, "bold"))
    style.configure("ConnectionMuted.TLabel", background=polished_theme.PANEL, foreground=polished_theme.MUTED, font=("Segoe UI", 9))



def _install_connection_badges(app: tk.Tk, actions: ttk.Frame) -> None:
    if not hasattr(app, "cockpit_schwab_connection_var"):
        app.cockpit_schwab_connection_var = tk.StringVar(value="Schwab: not connected")
    if not hasattr(app, "cockpit_hyperliquid_connection_var"):
        app.cockpit_hyperliquid_connection_var = tk.StringVar(value="Hyperliquid: not synced")

    # Avoid duplicate labels if the panel is rebuilt.
    for child in list(actions.winfo_children()):
        if getattr(child, "_cockpit_connection_badge", False):
            child.destroy()

    schwab = ttk.Label(actions, textvariable=app.cockpit_schwab_connection_var, style="ConnectionMuted.TLabel")
    schwab._cockpit_connection_badge = True  # type: ignore[attr-defined]
    schwab.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(1, 0))
    app.cockpit_schwab_connection_label = schwab

    hyper = ttk.Label(actions, textvariable=app.cockpit_hyperliquid_connection_var, style="ConnectionMuted.TLabel")
    hyper._cockpit_connection_badge = True  # type: ignore[attr-defined]
    hyper.grid(row=1, column=2, sticky="w", padx=(6, 0), pady=(1, 0))
    app.cockpit_hyperliquid_connection_label = hyper



def _attach_status_traces(app: tk.Tk) -> None:
    if getattr(app, "_cockpit_connection_traces_installed", False):
        return
    for attr in ("schwab_status_var", "hyperliquid_status_var"):
        var = getattr(app, attr, None)
        if hasattr(var, "trace_add"):
            var.trace_add("write", lambda *_args, current_app=app: _refresh_connection_badges(current_app))
    app._cockpit_connection_traces_installed = True



def _refresh_connection_badges(app: tk.Tk) -> None:
    schwab_text = str(getattr(getattr(app, "schwab_status_var", None), "get", lambda: "")()).lower()
    schwab_connected = getattr(app, "schwab_session", None) is not None or "connected" in schwab_text
    schwab_value = "✓ Schwab connected" if schwab_connected else "Schwab: not connected"
    _set_badge(app, "cockpit_schwab_connection_var", "cockpit_schwab_connection_label", schwab_value, schwab_connected)

    hyper_text = str(getattr(getattr(app, "hyperliquid_status_var", None), "get", lambda: "")()).lower()
    source_text = str(getattr(getattr(app, "broker", None), "source_message", "")).lower()
    hyper_synced = "synced" in hyper_text or "loaded hyperliquid" in source_text or "hyperliquid account" in source_text
    hyper_value = "✓ Hyperliquid synced" if hyper_synced else "Hyperliquid: not synced"
    _set_badge(app, "cockpit_hyperliquid_connection_var", "cockpit_hyperliquid_connection_label", hyper_value, hyper_synced)



def _set_badge(app: tk.Tk, var_name: str, label_name: str, value: str, is_good: bool) -> None:
    var = getattr(app, var_name, None)
    if hasattr(var, "set"):
        var.set(value)
    label = getattr(app, label_name, None)
    if label is not None:
        try:
            label.configure(style="ConnectionGood.TLabel" if is_good else "ConnectionMuted.TLabel")
        except tk.TclError:
            pass



def _clear_useless_preview_status(app: tk.Tk) -> None:
    var = getattr(app, "schwab_preview_status_var", None)
    if not hasattr(var, "get") or not hasattr(var, "set"):
        return
    value = str(var.get())
    if any(marker in value.upper() for marker in _UNUSED_STATUS_MARKERS):
        var.set("")



def _find_labelframe(root: tk.Misc, title: str) -> ttk.LabelFrame | None:
    for child in root.winfo_children():
        try:
            if isinstance(child, ttk.LabelFrame) and str(child.cget("text")) == title:
                return child
        except tk.TclError:
            pass
        found = _find_labelframe(child, title)
        if found is not None:
            return found
    return None



def _action_frame(summary: ttk.LabelFrame) -> ttk.Frame | None:
    for child in summary.winfo_children():
        try:
            info = child.grid_info()
        except tk.TclError:
            continue
        if str(info.get("row")) == "1" and isinstance(child, ttk.Frame):
            return child
    return None
