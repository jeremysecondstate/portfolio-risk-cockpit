from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Iterable

from app.analytics.research_scoring import BadgeReadout


STATUS_COLORS = {
    "good": {"bg": "#dcfce7", "fg": "#047857", "bar": "#16a34a"},
    "mixed": {"bg": "#fef3c7", "fg": "#92400e", "bar": "#d97706"},
    "bad": {"bg": "#fee2e2", "fg": "#b91c1c", "bar": "#dc2626"},
    "info": {"bg": "#dbeafe", "fg": "#1d4ed8", "bar": "#2563eb"},
    "neutral": {"bg": "#f1f5f9", "fg": "#475569", "bar": "#64748b"},
}

PANEL_BG = "#f8fafc"
TEXT = "#111827"
MUTED = "#64748b"
BORDER = "#cbd5e1"


def clear_children(parent: tk.Widget) -> None:
    for child in parent.winfo_children():
        child.destroy()


class ScrollableFrame(ttk.Frame):
    """A native scrollable container for dashboard tabs that can grow tall."""

    def __init__(self, parent: tk.Widget, *, padding: int | tuple[int, ...] = 0, style: str = "Panel.TFrame") -> None:
        super().__init__(parent, style=style)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=PANEL_BG)
        self.scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")

        self.body = ttk.Frame(self.canvas, style=style, padding=padding)
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body_configure, add="+")
        self.canvas.bind("<Configure>", self._on_canvas_configure, add="+")
        self.canvas.bind("<Enter>", self._bind_mousewheel, add="+")
        self.canvas.bind("<Leave>", self._unbind_mousewheel, add="+")
        self.body.bind("<Enter>", self._bind_mousewheel, add="+")
        self.body.bind("<Leave>", self._unbind_mousewheel, add="+")

    def _on_body_configure(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all") or (0, 0, 0, 0))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _bind_mousewheel(self, _event: tk.Event | None = None) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")

    def _unbind_mousewheel(self, _event: tk.Event | None = None) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class MetricCard(tk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        readout: BadgeReadout,
        *,
        width: int = 150,
        height: int = 92,
        prominent: bool = False,
    ) -> None:
        colors = STATUS_COLORS.get(readout.status, STATUS_COLORS["neutral"])
        super().__init__(parent, bg=colors["bg"], highlightbackground=colors["bar"], highlightthickness=2 if prominent else 1, width=width, height=height)
        self.grid_propagate(False)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1)
        tk.Frame(self, bg=colors["bar"], width=5).grid(row=0, column=0, rowspan=3, sticky="nsw")
        title_font = ("Segoe UI", 8, "bold")
        label_font = ("Segoe UI", 16 if prominent else 13, "bold")
        tk.Label(self, text=readout.title.upper(), bg=colors["bg"], fg=MUTED, font=title_font, anchor="w").grid(row=0, column=1, sticky="ew", padx=12, pady=(9, 0))
        tk.Label(self, text=readout.label, bg=colors["bg"], fg=colors["fg"], font=label_font, anchor="w").grid(row=1, column=1, sticky="ew", padx=12, pady=(2, 0))
        tk.Label(self, text=readout.why, bg=colors["bg"], fg=TEXT, font=("Segoe UI", 8), wraplength=width - 28, justify=tk.LEFT, anchor="nw").grid(row=2, column=1, sticky="nsew", padx=12, pady=(4, 10))


class ScoreBadge(tk.Frame):
    def __init__(self, parent: tk.Widget, readout: BadgeReadout) -> None:
        colors = STATUS_COLORS.get(readout.status, STATUS_COLORS["neutral"])
        super().__init__(parent, bg=colors["bg"], highlightbackground=BORDER, highlightthickness=1)
        tk.Label(self, text=f"{readout.title}: ", bg=colors["bg"], fg=MUTED, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(8, 0), pady=4)
        tk.Label(self, text=readout.label, bg=colors["bg"], fg=colors["fg"], font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8), pady=4)


class ScoreMeter(tk.Canvas):
    def __init__(self, parent: tk.Widget, *, height: int = 36, background: str = PANEL_BG) -> None:
        super().__init__(parent, height=height, bg=background, highlightthickness=0)
        self._score = 0.0
        self._mode = "direction"
        self._label = ""
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_score(self, score: float, *, mode: str = "direction", label: str = "") -> None:
        self._score = max(-100.0, min(100.0, score))
        self._mode = mode
        self._label = label
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 160)
        height = max(self.winfo_height(), 30)
        x0, x1 = 10, width - 10
        y = height // 2
        self.create_rectangle(x0, y - 5, x1, y + 5, fill="#e5e7eb", outline="")
        if self._mode == "risk":
            fill_width = (self._score / 100.0) * (x1 - x0)
            color = _bar_color_for_risk(self._score)
            self.create_rectangle(x0, y - 5, x0 + fill_width, y + 5, fill=color, outline="")
            pointer = x0 + fill_width
        else:
            center = (x0 + x1) / 2
            pointer = center + (self._score / 100.0) * ((x1 - x0) / 2)
            color = _bar_color_for_direction(self._score)
            self.create_rectangle(min(center, pointer), y - 5, max(center, pointer), y + 5, fill=color, outline="")
            self.create_line(center, y - 9, center, y + 9, fill="#94a3b8")
        self.create_oval(pointer - 5, y - 9, pointer + 5, y + 9, fill=color, outline="#ffffff")
        if self._label:
            self.create_text(x0, height - 6, text=self._label, anchor="sw", fill=MUTED, font=("Segoe UI", 8))


class Checklist(tk.Frame):
    def __init__(self, parent: tk.Widget, title: str, rows: Iterable[str]) -> None:
        super().__init__(parent, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
        self.columnconfigure(0, weight=1)
        tk.Label(self, text=title, bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 10, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        for index, row in enumerate(rows, start=1):
            status = "info"
            upper = row.upper()
            if upper.startswith("GOOD") or upper.startswith("POSITION"):
                status = "good"
            elif upper.startswith("BAD"):
                status = "bad"
            elif upper.startswith("WATCH") or upper.startswith("EARNINGS") or upper.startswith("MACRO"):
                status = "mixed"
            color = STATUS_COLORS[status]["fg"]
            tk.Label(self, text=row, bg=PANEL_BG, fg=color, font=("Segoe UI", 9), anchor="w", justify=tk.LEFT, wraplength=420).grid(row=index, column=0, sticky="ew", padx=10, pady=2)


class ScenarioImpactBars(tk.Canvas):
    def __init__(self, parent: tk.Widget, *, height: int = 150, background: str = PANEL_BG) -> None:
        super().__init__(parent, height=height, bg=background, highlightthickness=0)
        self._rows: list[tuple[str, float, str]] = []
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_rows(self, rows: list[tuple[str, float, str]]) -> None:
        self._rows = rows[:8]
        self.redraw()

    def redraw(self) -> None:
        self.delete("all")
        if not self._rows:
            self.create_text(10, 10, text="Run analysis to see scenario impact bars.", anchor="nw", fill=MUTED, font=("Segoe UI", 9))
            return
        width = max(self.winfo_width(), 260)
        center = width / 2
        max_abs = max(abs(value) for _label, value, _display in self._rows) or 0.0001
        row_height = 18
        start_y = 12
        self.create_line(center, 6, center, start_y + len(self._rows) * row_height + 4, fill="#94a3b8")
        for index, (label, value, display) in enumerate(self._rows):
            y = start_y + index * row_height
            bar = (value / max_abs) * (width * 0.38)
            color = "#16a34a" if value >= 0 else "#dc2626"
            self.create_text(8, y, text=label, anchor="nw", fill=TEXT, font=("Segoe UI", 8, "bold"))
            self.create_rectangle(min(center, center + bar), y + 2, max(center, center + bar), y + 12, fill=color, outline="")
            self.create_text(width - 8, y, text=display, anchor="ne", fill=color, font=("Segoe UI", 8, "bold"))


def metric_grid(parent: tk.Widget, readouts: list[BadgeReadout], *, columns: int = 4, prominent_indexes: set[int] | None = None) -> None:
    clear_children(parent)
    prominent_indexes = prominent_indexes or set()
    for column in range(columns):
        parent.columnconfigure(column, weight=1, uniform="metric_cards")
    for index, readout in enumerate(readouts):
        card = MetricCard(parent, readout, prominent=index in prominent_indexes, height=102 if index in prominent_indexes else 92)
        card.grid(row=index // columns, column=index % columns, sticky="nsew", padx=(0 if index % columns == 0 else 8, 0), pady=(0, 8))


def labeled_value_grid(parent: tk.Widget, rows: dict[str, str], *, columns: int = 3) -> None:
    clear_children(parent)
    for column in range(columns):
        parent.columnconfigure(column, weight=1, uniform="label_value")
    for index, (label, value) in enumerate(rows.items()):
        colors = STATUS_COLORS["neutral"]
        cell = tk.Frame(parent, bg="#ffffff", highlightbackground=BORDER, highlightthickness=1)
        cell.grid(row=index // columns, column=index % columns, sticky="nsew", padx=(0 if index % columns == 0 else 8, 0), pady=(0, 8))
        cell.columnconfigure(0, weight=1)
        tk.Label(cell, text=label.upper(), bg="#ffffff", fg=MUTED, font=("Segoe UI", 8, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=(7, 0))
        tk.Label(cell, text=value, bg="#ffffff", fg=colors["fg"], font=("Segoe UI", 10, "bold"), anchor="w", wraplength=260, justify=tk.LEFT).grid(row=1, column=0, sticky="ew", padx=10, pady=(3, 8))


def freshness_badges(parent: tk.Widget, statuses: list) -> None:
    clear_children(parent)
    for index, status in enumerate(statuses[:6]):
        badge_status = "good" if status.status in {"fresh", "fresh/cache"} else "mixed" if status.status in {"cached", "stale"} else "bad" if status.status == "error" else "info"
        readout = BadgeReadout(status.source, status.status.title(), badge_status, 0, status.fetched_at)
        ScoreBadge(parent, readout).grid(row=0, column=index, sticky="w", padx=(0, 6), pady=(0, 6))


def _bar_color_for_direction(score: float) -> str:
    if score >= 25:
        return STATUS_COLORS["good"]["bar"]
    if score <= -25:
        return STATUS_COLORS["bad"]["bar"]
    return STATUS_COLORS["mixed"]["bar"]


def _bar_color_for_risk(score: float) -> str:
    if score >= 70:
        return STATUS_COLORS["bad"]["bar"]
    if score >= 40:
        return STATUS_COLORS["mixed"]["bar"]
    return STATUS_COLORS["good"]["bar"]
