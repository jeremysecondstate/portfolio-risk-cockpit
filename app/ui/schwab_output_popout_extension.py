from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Type

REPORT_BG = "#f8fafc"
REPORT_FG = "#111827"
REPORT_SELECT_BG = "#bfdbfe"
REPORT_FONT = ("Segoe UI", 10)
REPORT_MONO_FONT = ("Cascadia Mono", 10)


def install_schwab_output_popout_extension(app_cls: Type[tk.Tk]) -> None:
    """Add a resizable pop-out window for Schwab analysis/order output."""

    app_cls.open_schwab_output_popout = _open_schwab_output_popout  # type: ignore[attr-defined]
    app_cls.refresh_schwab_output_popout = _refresh_schwab_output_popout  # type: ignore[attr-defined]

    original_build_layout = app_cls._build_layout

    def build_layout_with_schwab_output_popout(self: tk.Tk) -> None:
        original_build_layout(self)
        self.after_idle(lambda: _install_schwab_output_popout_button(self))

    app_cls._build_layout = build_layout_with_schwab_output_popout  # type: ignore[method-assign]


def _install_schwab_output_popout_button(self: tk.Tk) -> None:
    if getattr(self, "_schwab_output_popout_button_built", False):
        return

    output_text = getattr(self, "schwab_trading_preview_text", None)
    if output_text is None:
        return
    _style_report_text(output_text)

    output_parent = output_text.master
    if output_parent is None:
        return

    controls = ttk.Frame(output_parent, style="Panel.TFrame")
    try:
        controls.pack(fill=tk.X, padx=0, pady=(0, 8), before=output_text)
    except Exception:
        controls.pack(fill=tk.X, padx=0, pady=(0, 8))
    controls.columnconfigure(0, weight=1)
    controls.columnconfigure(1, weight=0)
    controls.columnconfigure(2, weight=0)

    ttk.Label(
        controls,
        text="Readable report view. Use Thesis Option fills the options ticket from the latest unified thesis; no order is submitted.",
        style="Subtle.TLabel",
    ).grid(row=0, column=0, sticky="w")
    ttk.Button(
        controls,
        text="Use Thesis Option",
        command=self.use_current_thesis_option_ticket,
    ).grid(row=0, column=1, sticky="e", padx=(8, 0))
    ttk.Button(
        controls,
        text="Pop Out Output",
        command=self.open_schwab_output_popout,
        style="Accent.TButton",
    ).grid(row=0, column=2, sticky="e", padx=(8, 0))

    self._schwab_output_popout_button_built = True


def _open_schwab_output_popout(self: tk.Tk) -> None:
    existing = getattr(self, "schwab_output_popout_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                self.refresh_schwab_output_popout(force=True)
                return
        except tk.TclError:
            pass

    window = tk.Toplevel(self)
    window.title("Schwab Analysis + Order Output")
    window.geometry("980x720")
    window.minsize(620, 420)
    window.rowconfigure(1, weight=1)
    window.columnconfigure(0, weight=1)

    toolbar = ttk.Frame(window, padding=(10, 8))
    toolbar.grid(row=0, column=0, sticky="ew")
    toolbar.columnconfigure(0, weight=1)
    ttk.Label(
        toolbar,
        text="Resizable Schwab output mirror. Use Thesis Option fills the options ticket only; no order is submitted.",
        style="Subtle.TLabel",
    ).grid(row=0, column=0, sticky="w")
    ttk.Button(toolbar, text="Use Thesis Option", command=self.use_current_thesis_option_ticket).grid(row=0, column=1, sticky="e", padx=(8, 0))
    ttk.Button(toolbar, text="Refresh", command=lambda: self.refresh_schwab_output_popout(force=True)).grid(row=0, column=2, sticky="e", padx=(8, 0))

    body = ttk.Frame(window, padding=(10, 0, 10, 10))
    body.grid(row=1, column=0, sticky="nsew")
    body.rowconfigure(0, weight=1)
    body.columnconfigure(0, weight=1)

    text = tk.Text(
        body,
        wrap=tk.WORD,
        font=REPORT_MONO_FONT,
        padx=18,
        pady=16,
        relief=tk.FLAT,
        borderwidth=0,
        background=REPORT_BG,
        foreground=REPORT_FG,
        insertbackground=REPORT_FG,
        selectbackground=REPORT_SELECT_BG,
        spacing1=3,
        spacing2=1,
        spacing3=6,
    )
    text.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=text.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=scrollbar.set)

    self.schwab_output_popout_window = window
    self.schwab_output_popout_text = text
    self.schwab_output_popout_last_content = None

    def _on_close() -> None:
        self.schwab_output_popout_window = None
        self.schwab_output_popout_text = None
        self.schwab_output_popout_last_content = None
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", _on_close)
    self.refresh_schwab_output_popout(force=True)
    _schedule_popout_refresh(self)


def _refresh_schwab_output_popout(self: tk.Tk, *, force: bool = False) -> None:
    source = getattr(self, "schwab_trading_preview_text", None)
    target = getattr(self, "schwab_output_popout_text", None)
    if source is None or target is None:
        return

    try:
        _style_report_text(source)
        content = source.get("1.0", tk.END)
        last_content = getattr(self, "schwab_output_popout_last_content", None)
        if not force and content == last_content:
            return

        current_yview = target.yview()
        top_fraction = current_yview[0] if current_yview else 0.0

        target.configure(state=tk.NORMAL)
        target.delete("1.0", tk.END)
        target.insert(tk.END, content)
        target.configure(state=tk.DISABLED)
        target.yview_moveto(top_fraction)
        self.schwab_output_popout_last_content = content
    except tk.TclError:
        return


def _schedule_popout_refresh(self: tk.Tk) -> None:
    window = getattr(self, "schwab_output_popout_window", None)
    if window is None:
        return
    try:
        if not window.winfo_exists():
            return
    except tk.TclError:
        return

    self.refresh_schwab_output_popout()
    try:
        window.after(750, lambda: _schedule_popout_refresh(self))
    except tk.TclError:
        return


def _style_report_text(text: tk.Text) -> None:
    try:
        text.configure(
            font=REPORT_MONO_FONT,
            padx=18,
            pady=16,
            relief=tk.FLAT,
            borderwidth=0,
            background=REPORT_BG,
            foreground=REPORT_FG,
            insertbackground=REPORT_FG,
            selectbackground=REPORT_SELECT_BG,
            spacing1=3,
            spacing2=1,
            spacing3=6,
        )
    except tk.TclError:
        return
