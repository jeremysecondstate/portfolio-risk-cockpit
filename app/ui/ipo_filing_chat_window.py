from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Callable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app.analytics.ipo_filing_chat import (
    QUICK_ACTION_PROMPTS,
    IpoFilingChatResponse,
    IpoFilingChatSession,
    create_ipo_filing_chat_session,
    ipo_filing_chat_transcript_path,
    redact_ipo_filing_chat_secrets,
    save_ipo_filing_chat_transcript,
)
from app.analytics.ipo_pipeline import IpoPipelineRecord
from app.data.sec_edgar import SecEdgarClient
from app.ui import polished_theme


def open_ipo_filing_chat_window(
    parent: tk.Misc,
    record: IpoPipelineRecord,
    *,
    session_factory: Callable[[IpoPipelineRecord], IpoFilingChatSession] | None = None,
) -> "IpoFilingChatWindow":
    window = IpoFilingChatWindow(parent, record, session_factory=session_factory)
    window.focus_set()
    return window


class IpoFilingChatWindow(tk.Toplevel):
    def __init__(
        self,
        parent: tk.Misc,
        record: IpoPipelineRecord,
        *,
        session_factory: Callable[[IpoPipelineRecord], IpoFilingChatSession] | None = None,
    ) -> None:
        super().__init__(parent)
        self.record = record
        self.session_factory = session_factory or _default_session_factory
        self.session: IpoFilingChatSession | None = None
        self._request_running = False

        title_company = record.company_name.strip() or "Selected filing"
        title_form = record.form.strip().upper() or "SEC filing"
        self.title(f"AI Filing Chat - {title_company} {title_form}")
        polished_theme.configure_toplevel(self)
        self.geometry("980x760")
        self.minsize(760, 560)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.status_var = tk.StringVar(value="Loading selected SEC filing context...")
        self._build_header()
        self._build_transcript()
        self._build_prompt_area()
        self._set_controls_enabled(False)
        self._append_system_line("Loading selected SEC filing context...")
        self._load_session_in_background()

    def _build_header(self) -> None:
        header = ttk.Frame(self, style="Panel.TFrame")
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 8))
        header.columnconfigure(0, weight=1)
        ttk.Label(
            header,
            text=f"{self.record.company_name or 'Selected filing'} - {self.record.form or 'SEC filing'}",
            style="TLabel",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var, style="Chip.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 0))

        actions = ttk.Frame(header, style="Panel.TFrame")
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.open_filing_button = ttk.Button(actions, text="Open SEC Filing", command=self._open_sec_filing)
        self.open_filing_button.pack(side=tk.LEFT)
        self.save_button = ttk.Button(actions, text="Save Transcript Markdown", command=self._save_transcript, state=tk.DISABLED)
        self.save_button.pack(side=tk.LEFT, padx=(8, 0))

        quick_actions = ttk.Frame(header, style="Panel.TFrame")
        quick_actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.quick_buttons: list[ttk.Button] = []
        for label, prompt in QUICK_ACTION_PROMPTS.items():
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

    def _load_session_in_background(self) -> None:
        def worker() -> None:
            try:
                session = self.session_factory(self.record)
            except Exception as exc:
                self.after(0, lambda error=exc: self._finish_session_error(error))
                return
            self.after(0, lambda loaded=session: self._finish_session_loaded(loaded))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_session_loaded(self, session: IpoFilingChatSession) -> None:
        self.session = session
        metadata = session.context.bundle.metadata
        source_label = " ".join(str(metadata.get(key) or "") for key in ("source_form", "source_document")).strip()
        self.status_var.set(f"Ready - {source_label or 'SEC filing'} loaded.")
        self._set_controls_enabled(True)
        self._append_system_line(
            "Filing context loaded. Ask a question or use a quick action. Answers are grounded in the selected SEC filing."
        )

    def _finish_session_error(self, error: Exception) -> None:
        self.status_var.set("Filing chat could not load context.")
        self._append_system_line(f"Context load failed: {error}")
        messagebox.showerror("AI Filing Chat", str(error))

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        self.prompt_box.configure(state=state)
        self.send_button.configure(state=state)
        self.save_button.configure(state=tk.NORMAL if enabled else tk.DISABLED)
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
            self.status_var.set("Still loading filing context...")
            return
        if self._request_running:
            self.status_var.set("Wait for the current filing chat response to finish.")
            return
        clean_prompt = prompt.strip()
        if not clean_prompt:
            return
        self._request_running = True
        self._set_controls_enabled(False)
        self._append_message("user", clean_prompt)
        self.status_var.set("Asking OpenAI against the selected filing...")

        def worker() -> None:
            try:
                response = self.session.ask(clean_prompt)
            except Exception as exc:
                self.after(0, lambda error=exc: self._finish_prompt_error(error))
                return
            self.after(0, lambda answer=response: self._finish_prompt_success(answer))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_prompt_success(self, response: IpoFilingChatResponse) -> None:
        self._request_running = False
        self._set_controls_enabled(True)
        self._append_message("assistant", response.answer)
        source_note = f" Source: {response.source_mode}." if response.source_mode else ""
        self.status_var.set(f"Ready.{source_note}")

    def _finish_prompt_error(self, error: Exception) -> None:
        self._request_running = False
        self._set_controls_enabled(True)
        self.status_var.set("OpenAI filing chat failed.")
        self._append_system_line(f"OpenAI request failed: {error}")
        messagebox.showerror("AI Filing Chat failed", str(error))

    def _append_message(self, role: str, content: str) -> None:
        label = "You" if role == "user" else "AI Filing Analyst"
        tag = "user" if role == "user" else "assistant"
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, f"\n{label}\n", tag)
        self.transcript.insert(tk.END, redact_ipo_filing_chat_secrets(content.strip()) + "\n")
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _append_system_line(self, content: str) -> None:
        self.transcript.configure(state=tk.NORMAL)
        self.transcript.insert(tk.END, content.strip() + "\n", "system")
        self.transcript.see(tk.END)
        self.transcript.configure(state=tk.DISABLED)

    def _open_sec_filing(self) -> None:
        url = self.record.filing_url
        if self.session is not None:
            url = self.session.context.source_url or url
        if not url:
            messagebox.showinfo("Open SEC Filing", "The selected row does not have an SEC filing URL.")
            return
        webbrowser.open_new_tab(url)

    def _save_transcript(self) -> None:
        if self.session is None:
            messagebox.showinfo("Save Transcript", "Filing context is still loading.")
            return
        default_path = ipo_filing_chat_transcript_path(self.record)
        default_path.parent.mkdir(parents=True, exist_ok=True)
        selected = filedialog.asksaveasfilename(
            parent=self,
            title="Save filing chat transcript",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Text files", "*.txt"), ("All files", "*.*")],
            initialdir=str(default_path.parent),
            initialfile=default_path.name,
        )
        if not selected:
            return
        try:
            path = save_ipo_filing_chat_transcript(self.session, Path(selected))
        except Exception as exc:
            self.status_var.set("Transcript save failed.")
            messagebox.showerror("Save Transcript failed", str(exc))
            return
        self.status_var.set(f"Transcript saved: {path}")


def _default_session_factory(record: IpoPipelineRecord) -> IpoFilingChatSession:
    return create_ipo_filing_chat_session(record, sec_client=SecEdgarClient())
