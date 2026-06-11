from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Callable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.analytics.symbol_chat import (
    SYMBOL_CHAT_QUICK_PROMPTS,
    SymbolChatResponse,
    SymbolChatSession,
    create_symbol_chat_session,
    redact_symbol_chat_secrets,
    save_symbol_chat_transcript,
    symbol_chat_transcript_path,
)
from app.ui import polished_theme


def open_symbol_chat_window(
    parent: tk.Misc,
    symbol: str,
    *,
    app_context: object | None = None,
    schwab_session: object | None = None,
    session_factory: Callable[[str], SymbolChatSession] | None = None,
) -> "SymbolChatWindow":
    window = SymbolChatWindow(
        parent,
        symbol,
        app_context=app_context,
        schwab_session=schwab_session,
        session_factory=session_factory,
    )
    window.focus_set()
    return window


class SymbolChatWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        symbol: str,
        *,
        app_context: object | None = None,
        schwab_session: object | None = None,
        session_factory: Callable[[str], SymbolChatSession] | None = None,
    ) -> None:
        super().__init__(parent)
        self.symbol = str(symbol or "").strip().upper()
        self.app_context = app_context
        self.schwab_session = schwab_session
        self.session_factory = session_factory or self._default_session_factory
        self.session: SymbolChatSession | None = None
        self._request_running = False
        self._context_loading = False
        self._cancel_requested = False
        self._closed = False
        self._request_generation = 0

        self.title(f"AI Symbol Chat - {self.symbol or 'Symbol'}")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        polished_theme.configure_toplevel(self)
        self.geometry("980x760")
        self.minsize(760, 560)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.status_var = tk.StringVar(value=f"Loading {self.symbol} context...")
        self.header_title_var = tk.StringVar(value=f"{self.symbol} Symbol Chat")
        self._build_header()
        self._build_transcript()
        self._build_prompt_area()
        self._set_controls_enabled(False)
        self._append_system_line(f"Loading {self.symbol} context...")
        self._load_session_in_background()

    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            textvariable=self.header_title_var,
            style="TLabel",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 0))

        actions = ttk.Frame(header, style="Panel.TFrame")
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.refresh_button = ttk.Button(actions, text="Refresh Context", command=self._refresh_context)
        self.refresh_button.pack(side=tk.LEFT)
        self.open_filings_button = ttk.Button(actions, text="Open Recent Filings", command=self._open_recent_filings, state=tk.DISABLED)
        self.open_filings_button.pack(side=tk.LEFT, padx=(8, 0))
        self.save_button = ttk.Button(actions, text="Save Transcript Markdown", command=self._save_transcript, state=tk.DISABLED)
        self.save_button.pack(side=tk.LEFT, padx=(8, 0))

        quick_actions = ttk.Frame(header, style="Panel.TFrame")
        quick_actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.quick_buttons: list[ttk.Button] = []
        for label, prompt in SYMBOL_CHAT_QUICK_PROMPTS.items():
            button = ttk.Button(quick_actions, text=label, command=lambda value=prompt: self._send_prompt(value))
            button.pack(side=tk.LEFT, padx=(8, 0))
            self.quick_buttons.append(button)

    def _build_transcript(self) -> None:
        frame = ttk.Frame(self, style="Panel.TFrame")
        frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.transcript = tk.Text(
            frame,
            wrap=tk.WORD,
            bg=polished_theme.PANEL,
            fg=polished_theme.TEXT,
            insertbackground=polished_theme.TEXT,
            relief=tk.FLAT,
            padx=12,
            pady=12,
            font=("Segoe UI", 10),
        )
        self.transcript.grid(row=0, column=0, sticky="nsew")
        self.transcript.configure(state=tk.DISABLED)
        self.transcript.tag_configure("user", foreground=polished_theme.ACCENT_SOFT, font=("Segoe UI", 10, "bold"))
        self.transcript.tag_configure("assistant", foreground=polished_theme.TEXT, font=("Segoe UI", 10, "bold"))
        self.transcript.tag_configure("system", foreground=polished_theme.MUTED, font=("Segoe UI", 9, "italic"))
        scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.transcript.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.transcript.configure(yscrollcommand=scroll.set)

    def _build_prompt_area(self) -> None:
        frame = ttk.Frame(self, style="Panel.TFrame")
        frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 12))
        frame.columnconfigure(0, weight=1)
        self.prompt_box = tk.Text(
            frame,
            height=4,
            wrap=tk.WORD,
            bg=polished_theme.INPUT,
            fg=polished_theme.TEXT,
            insertbackground=polished_theme.TEXT,
            relief=tk.FLAT,
            padx=10,
            pady=8,
            font=("Segoe UI", 10),
        )
        self.prompt_box.grid(row=0, column=0, sticky="ew")
        self.prompt_box.bind("<Control-Return>", lambda _event: self._send_current_prompt(), add="+")
        self.send_button = ttk.Button(frame, text="Send", command=self._send_current_prompt, style="Accent.TButton")
        self.send_button.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.cancel_button = ttk.Button(frame, text="Cancel", command=self._cancel_request, state=tk.DISABLED)
        self.cancel_button.grid(row=0, column=2, sticky="ns", padx=(8, 0))

    def _default_session_factory(self, symbol: str) -> SymbolChatSession:
        return create_symbol_chat_session(
            symbol,
            app_context=self.app_context,
            schwab_session=self.schwab_session,
        )

    def _load_session_in_background(self) -> None:
        if self._context_loading:
            return
        self._context_loading = True
        self._set_controls_enabled(False)

        def worker() -> None:
            try:
                session = self.session_factory(self.symbol)
            except Exception as exc:
                self._post_to_ui(lambda error=exc: self._finish_session_error(error))
                return
            self._post_to_ui(lambda loaded=session: self._finish_session_loaded(loaded))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_session_loaded(self, session: SymbolChatSession) -> None:
        if self._closed:
            return
        previous_session = self.session
        if previous_session is not None and previous_session.messages and not session.messages:
            session.messages = list(previous_session.messages)
            session.last_response_id = previous_session.last_response_id
        self._context_loading = False
        self.session = session
        self.symbol = session.context.symbol
        self.title(f"AI Symbol Chat - {self.symbol}")
        self.header_title_var.set(session.context.display_name)
        available_count = len(session.context.source_metadata.get("available", []) or [])
        unavailable_count = len(session.context.source_metadata.get("unavailable", []) or [])
        self.status_var.set(f"Ready - {available_count} context sources loaded, {unavailable_count} limited.")
        self._set_controls_enabled(True)
        filings_available = bool(session.context.recent_filings_summary)
        self.open_filings_button.configure(state=tk.NORMAL if filings_available else tk.DISABLED)
        self._append_system_line(
            f"{self.symbol} context loaded. Answers are analysis-only and grounded in the provided app context."
        )

    def _finish_session_error(self, error: Exception) -> None:
        if self._closed:
            return
        self._context_loading = False
        self.status_var.set("Symbol chat could not load context.")
        self._append_system_line(f"Context load failed: {error}")
        self.refresh_button.configure(state=tk.NORMAL)
        messagebox.showerror("AI Symbol Chat", str(error))

    def _refresh_context(self) -> None:
        if self._request_running:
            self.status_var.set("Wait for the current symbol chat response to finish.")
            return
        self.status_var.set(f"Refreshing {self.symbol} context...")
        self._append_system_line(f"Refreshing {self.symbol} context...")
        self._load_session_in_background()

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        self.prompt_box.configure(state=state)
        self.send_button.configure(state=state)
        self.save_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        self.refresh_button.configure(state=tk.NORMAL if not self._request_running else tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL if self._request_running and not enabled else tk.DISABLED)
        for button in self.quick_buttons:
            button.configure(state=state)

    def _send_current_prompt(self) -> str:
        prompt = self.prompt_box.get("1.0", tk.END).strip()
        if prompt:
            self.prompt_box.delete("1.0", tk.END)
            self._send_prompt(prompt)
        return "break"

    def _send_prompt(self, prompt: str) -> None:
        if self.session is None:
            self.status_var.set("Still loading symbol context...")
            return
        if self._request_running:
            self.status_var.set("Wait for the current symbol chat response to finish.")
            return
        clean_prompt = prompt.strip()
        if not clean_prompt:
            return
        self._request_running = True
        self._cancel_requested = False
        self._request_generation += 1
        request_generation = self._request_generation
        self._set_controls_enabled(False)
        self._append_message("user", clean_prompt)
        self.status_var.set("Preparing symbol context...")

        def progress(message: str) -> None:
            self._post_to_ui(lambda value=message: self._update_request_status(value, request_generation))

        def worker() -> None:
            try:
                response = self.session.ask(clean_prompt, progress_callback=progress)
            except Exception as exc:
                self._post_to_ui(lambda error=exc: self._finish_prompt_error(error, request_generation))
                return
            self._post_to_ui(lambda answer=response: self._finish_prompt_success(answer, request_generation))

        threading.Thread(target=worker, daemon=True).start()

    def _update_request_status(self, message: str, request_generation: int) -> None:
        if self._closed or request_generation != self._request_generation or self._cancel_requested:
            return
        self.status_var.set(message)

    def _finish_prompt_success(self, response: SymbolChatResponse, request_generation: int | None = None) -> None:
        if self._closed or (request_generation is not None and request_generation != self._request_generation):
            return
        self._request_running = False
        was_cancelled = self._cancel_requested
        self._cancel_requested = False
        self._set_controls_enabled(True)
        if was_cancelled:
            self.status_var.set("Ready.")
            self._append_system_line("Request canceled. The completed OpenAI response was not added to the transcript.")
            return
        self._append_message("assistant", response.answer)
        source_note = f" Source: {response.source_mode}." if response.source_mode else ""
        self.status_var.set(f"Ready.{source_note}")

    def _finish_prompt_error(self, error: Exception, request_generation: int | None = None) -> None:
        if self._closed or (request_generation is not None and request_generation != self._request_generation):
            return
        self._request_running = False
        was_cancelled = self._cancel_requested
        self._cancel_requested = False
        self._set_controls_enabled(True)
        if was_cancelled:
            self.status_var.set("Ready.")
            self._append_system_line(f"Canceled request ended with error: {error}")
            return
        self.status_var.set("OpenAI symbol chat failed.")
        self._append_system_line(f"OpenAI request failed: {error}")
        messagebox.showerror("AI Symbol Chat failed", str(error))

    def _cancel_request(self) -> None:
        if not self._request_running:
            return
        self._cancel_requested = True
        self.status_var.set("Cancel requested. Waiting for the current OpenAI call to finish or time out...")
        self.cancel_button.configure(state=tk.DISABLED)

    def _post_to_ui(self, callback: Callable[[], None]) -> None:
        if self._closed:
            return

        def run_if_open() -> None:
            if not self._closed:
                callback()

        try:
            self.after(0, run_if_open)
        except tk.TclError:
            self._closed = True

    def _on_close(self) -> None:
        self._closed = True
        try:
            self.destroy()
        except tk.TclError:
            pass

    def _append_message(self, role: str, content: str) -> None:
        label = "You" if role == "user" else "AI Symbol Analyst"
        tag = "user" if role == "user" else "assistant"
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, f"\n{label}\n", tag)
        self.transcript.insert(tk.END, redact_symbol_chat_secrets(content.strip()) + "\n")
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _append_system_line(self, content: str) -> None:
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, redact_symbol_chat_secrets(content.strip()) + "\n", "system")
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _open_recent_filings(self) -> None:
        if self.session is None:
            messagebox.showinfo("Open Recent Filings", "Symbol context is still loading.")
            return
        for filing in self.session.context.recent_filings_summary:
            url = str(filing.get("url") or "").strip()
            if url:
                webbrowser.open_new_tab(url)
                return
        messagebox.showinfo("Open Recent Filings", "No recent filing URL is available in the loaded context.")

    def _save_transcript(self) -> None:
        if self.session is None:
            messagebox.showinfo("Save Transcript", "Symbol context is still loading.")
            return
        default_path = symbol_chat_transcript_path(self.session.context.symbol)
        default_path.parent.mkdir(parents=True, exist_ok=True)
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Save symbol chat transcript",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text files", "*.txt"), ("All files", "*.*")],
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
        )
        if not selected:
            return
        try:
            path = save_symbol_chat_transcript(self.session, Path(selected))
        except Exception as exc:
            self.status_var.set("Transcript save failed.")
            messagebox.showerror("Save Transcript failed", str(exc))
            return
        self.status_var.set(f"Transcript saved: {path}")
