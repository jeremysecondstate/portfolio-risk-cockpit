from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Type

from app.core.order_models import normalize_time_in_force
from app.ui import schwab_trading_tab


SCHWAB_EQUITY_SESSION_CHOICES = ("NORMAL", "AM", "PM", "SEAMLESS", "EXTO")
_LEGACY_SESSION_CHOICES = ("NORMAL", "AM", "PM", "SEAMLESS")


def install_schwab_exto_time_in_force_extension(app_cls: Type[tk.Tk]) -> None:
    """Expose the full thinkorswim-style equity TIF/session set in Schwab tickets."""

    if getattr(app_cls, "_schwab_exto_time_in_force_extension_installed", False):
        return

    _patch_supported_duration()
    _patch_supported_session()
    _patch_replace_dialog()

    original_build_layout = app_cls._build_layout

    def _build_layout_with_exto_session_choices(self: tk.Tk, *args: Any, **kwargs: Any) -> Any:
        result = original_build_layout(self, *args, **kwargs)
        _patch_session_comboboxes(self)
        return result

    app_cls._build_layout = _build_layout_with_exto_session_choices  # type: ignore[method-assign]
    app_cls._schwab_exto_time_in_force_extension_installed = True  # type: ignore[attr-defined]


def _patch_supported_duration() -> None:
    if getattr(schwab_trading_tab, "_schwab_exto_supported_duration_patched", False):
        return

    def _supported_duration_with_tos_codes(value: str) -> str:
        try:
            return normalize_time_in_force(value).value
        except ValueError:
            return "DAY"

    schwab_trading_tab._supported_duration = _supported_duration_with_tos_codes  # type: ignore[attr-defined]
    schwab_trading_tab._schwab_exto_supported_duration_patched = True  # type: ignore[attr-defined]


def _patch_supported_session() -> None:
    if getattr(schwab_trading_tab, "_schwab_exto_supported_session_patched", False):
        return

    def _supported_session_with_exto(value: str) -> str:
        clean = str(value or "NORMAL").strip().upper().replace(" ", "_")
        aliases = {
            "OVERNIGHT": "EXTO",
            "EXT_OVERNIGHT": "EXTO",
            "EXTENDED_OVERNIGHT": "EXTO",
            "24H": "EXTO",
            "24_5": "EXTO",
            "24/5": "EXTO",
        }
        clean = aliases.get(clean, clean)
        return clean if clean in SCHWAB_EQUITY_SESSION_CHOICES else "NORMAL"

    schwab_trading_tab._supported_session = _supported_session_with_exto  # type: ignore[attr-defined]
    schwab_trading_tab._schwab_exto_supported_session_patched = True  # type: ignore[attr-defined]


def _patch_replace_dialog() -> None:
    if getattr(schwab_trading_tab, "_schwab_exto_replace_dialog_patched", False):
        return

    original_show_replace_dialog = schwab_trading_tab._show_schwab_replace_dialog

    def _show_replace_dialog_with_exto_choices(self: tk.Tk, order: dict[str, Any], parsed: dict[str, str]) -> Any:
        existing_children = set(self.winfo_children())
        result = original_show_replace_dialog(self, order, parsed)
        for child in self.winfo_children():
            if child not in existing_children:
                _patch_session_comboboxes(child)
        return result

    schwab_trading_tab._show_schwab_replace_dialog = _show_replace_dialog_with_exto_choices  # type: ignore[assignment]
    schwab_trading_tab._schwab_exto_replace_dialog_patched = True  # type: ignore[attr-defined]


def _patch_session_comboboxes(root: tk.Misc) -> None:
    for child in root.winfo_children():
        if isinstance(child, ttk.Combobox):
            values = _combobox_values(child)
            if values == _LEGACY_SESSION_CHOICES:
                child.configure(values=SCHWAB_EQUITY_SESSION_CHOICES)
        _patch_session_comboboxes(child)


def _combobox_values(combo: ttk.Combobox) -> tuple[str, ...]:
    try:
        raw_values = combo["values"]
    except tk.TclError:
        return ()
    if isinstance(raw_values, str):
        try:
            return tuple(str(value) for value in combo.tk.splitlist(raw_values))
        except tk.TclError:
            return ()
    return tuple(str(value) for value in raw_values)
