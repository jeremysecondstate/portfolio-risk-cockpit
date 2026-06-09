from __future__ import annotations

import re
from dataclasses import dataclass
import tkinter as tk
from tkinter import ttk
from typing import Iterable

from app.analytics.research_scoring import BadgeReadout
from app.ui import polished_theme


STATUS_COLORS = {
    "good": {"bg": "#052e2b", "fg": polished_theme.POSITIVE, "bar": "#10b981"},
    "mixed": {"bg": "#3b2f08", "fg": polished_theme.WARNING, "bar": "#f59e0b"},
    "bad": {"bg": "#3b0a19", "fg": polished_theme.NEGATIVE, "bar": "#f43f5e"},
    "info": {"bg": "#0f2a4a", "fg": polished_theme.ACCENT_SOFT, "bar": polished_theme.ACCENT},
    "neutral": {"bg": polished_theme.PANEL_ALT, "fg": polished_theme.MUTED, "bar": polished_theme.MUTED},
}

PANEL_BG = polished_theme.PANEL
TEXT = polished_theme.TEXT
MUTED = polished_theme.MUTED
BORDER = polished_theme.BORDER
METRIC_CARD_PAD_X = 14
METRIC_CARD_PAD_TOP = 10
METRIC_CARD_PAD_BOTTOM = 12
METRIC_CARD_GAP = 10
DEFAULT_METRIC_CARD_HEIGHT = 104
DEFAULT_PROMINENT_CARD_HEIGHT = 116
COMPACT_CARD_LABEL_LIMIT = 44
COMPACT_CARD_WHY_LIMIT = 102
COMPACT_VALUE_LIMIT = 112


@dataclass(frozen=True)
class MarkdownPipeTable:
    title: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    start_line: int = 0
    end_line: int = 0


@dataclass(frozen=True)
class VisualReadoutBlock:
    kind: str
    title: str
    rows: tuple[str, ...] = ()
    headers: tuple[str, ...] = ()
    table_rows: tuple[tuple[str, ...], ...] = ()
    secondary: bool = False


@dataclass(frozen=True)
class VisualReadout:
    title: str
    hero: str
    key_values: tuple[tuple[str, str], ...]
    blocks: tuple[VisualReadoutBlock, ...]
    raw_text: str


@dataclass(frozen=True)
class TruncatedText:
    display: str
    detail: str
    truncated: bool


def _compact_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def truncate_with_detail(value: object, limit: int) -> TruncatedText:
    text = " ".join(str(value or "").split())
    if limit <= 0 or len(text) <= limit:
        return TruncatedText(text, "", False)
    return TruncatedText(text[: max(0, limit - 3)].rstrip() + "...", text, True)


def parse_markdown_pipe_table(lines: list[str], start: int = 0, *, title: str = "") -> tuple[MarkdownPipeTable | None, int]:
    if start >= len(lines) or not _is_markdown_table_line(lines[start].strip()):
        return None, start

    table_lines: list[str] = []
    index = start
    while index < len(lines) and _is_markdown_table_line(lines[index].strip()):
        table_lines.append(lines[index].strip())
        index += 1
    if not table_lines:
        return None, index

    headers = tuple(_split_table_row(table_lines[0]))
    rows: list[tuple[str, ...]] = []
    for line in table_lines[1:]:
        pieces = _split_table_row(line)
        if _is_markdown_separator_row(pieces):
            continue
        if pieces:
            rows.append(tuple(pieces))
    return MarkdownPipeTable(title=title, headers=headers, rows=tuple(rows), start_line=start, end_line=index), index


def parse_visual_readout(content: str, *, title_hint: str = "") -> VisualReadout:
    raw_text = str(content or "").strip()
    lines = [line.rstrip() for line in raw_text.splitlines()]
    index = 0
    title = title_hint.strip()
    if len(lines) >= 2 and lines[0].strip() and _is_underline(lines[1].strip()):
        title = lines[0].strip()
        index = 2
    elif not title and lines and _looks_like_readout_heading(lines[0].strip()):
        title = lines[0].strip().rstrip(":")
        index = 1
    if not title:
        title = "Detailed Readout"

    blocks: list[VisualReadoutBlock] = []
    key_values: list[tuple[str, str]] = []
    current_title = "Summary"
    paragraph_rows: list[str] = []
    bullet_rows: list[str] = []

    def flush() -> None:
        nonlocal paragraph_rows, bullet_rows
        secondary = _is_secondary_readout_section(current_title)
        if bullet_rows:
            blocks.append(VisualReadoutBlock("bullets", current_title, tuple(bullet_rows), secondary=secondary))
            bullet_rows = []
        if paragraph_rows:
            blocks.append(VisualReadoutBlock("paragraphs", current_title, tuple(paragraph_rows), secondary=secondary))
            paragraph_rows = []

    while index < len(lines):
        line = lines[index].strip()
        next_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if not line:
            index += 1
            continue
        if _is_underline(next_line):
            flush()
            current_title = line.rstrip(":")
            index += 2
            continue
        if _looks_like_readout_heading(line):
            flush()
            current_title = line.rstrip(":")
            index += 1
            continue
        if _is_markdown_table_line(line):
            flush()
            table, next_index = parse_markdown_pipe_table(lines, index, title=current_title)
            if table is not None:
                blocks.append(
                    VisualReadoutBlock(
                        "table",
                        table.title or current_title,
                        headers=table.headers,
                        table_rows=table.rows,
                        secondary=_is_secondary_readout_section(current_title),
                    )
                )
            index = max(next_index, index + 1)
            continue

        clean = _strip_list_marker(line)
        label, value = _split_label_value(clean)
        if label and value:
            key_values.append((label, value))
        if line.startswith(("-", "*")):
            bullet_rows.append(clean)
        else:
            paragraph_rows.append(clean)
        index += 1

    flush()
    hero = _first_hero_line(blocks)
    return VisualReadout(
        title=title,
        hero=hero or "Readable research popout with sections, cards, and native tables.",
        key_values=tuple(_dedupe_key_values(key_values)),
        blocks=tuple(block for block in blocks if block.rows or block.table_rows),
        raw_text=raw_text,
    )


def markdown_tables_from_readout(content: str) -> list[MarkdownPipeTable]:
    lines = [line.rstrip() for line in str(content or "").splitlines()]
    tables: list[MarkdownPipeTable] = []
    title = ""
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if _looks_like_readout_heading(line):
            title = line.rstrip(":")
            index += 1
            continue
        if _is_markdown_table_line(line):
            table, next_index = parse_markdown_pipe_table(lines, index, title=title)
            if table is not None:
                tables.append(table)
            index = max(next_index, index + 1)
            continue
        index += 1
    return tables


def _first_hero_line(blocks: list[VisualReadoutBlock]) -> str:
    for block in blocks:
        if block.secondary:
            continue
        if block.kind == "table" and block.table_rows:
            return f"{block.title}: {len(block.table_rows)} row(s)."
        if block.rows:
            return block.rows[0]
    return ""


def _strip_list_marker(line: str) -> str:
    return line.lstrip("-* \t").strip()


def _split_label_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        return "", ""
    label, value = text.split(":", 1)
    label = label.strip(" -")
    value = value.strip()
    if not label or not value or len(label) > 72:
        return "", ""
    return label, value


def _dedupe_key_values(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for label, value in pairs:
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append((label, value))
    return result


def _is_markdown_table_line(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 2


def _split_table_row(line: str) -> list[str]:
    clean = line.strip()
    if clean.startswith("|"):
        clean = clean[1:]
    if clean.endswith("|"):
        clean = clean[:-1]
    pieces: list[str] = []
    current: list[str] = []
    escaped = False
    for char in clean:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            pieces.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    pieces.append("".join(current).strip())
    return pieces


def _is_markdown_separator_row(pieces: Iterable[str]) -> bool:
    cleaned = ["".join(piece.split()) for piece in pieces]
    return bool(cleaned) and all(piece and set(piece) <= {"-", ":"} for piece in cleaned)


def _is_underline(line: str) -> bool:
    clean = line.strip()
    return len(clean) >= 3 and set(clean) <= {"=", "-"}


def _looks_like_readout_heading(line: str) -> bool:
    clean = line.strip().rstrip(":")
    if not clean or len(clean) > 90 or clean.startswith(("-", "*", "|")):
        return False
    if ":" in line and line.split(":", 1)[1].strip():
        return False
    lower = clean.lower()
    known = {
        "headline",
        "source freshness",
        "key financial snapshot",
        "platform / segment revenue",
        "financial snapshot",
        "growth drivers",
        "what is driving the quarter",
        "quality of earnings",
        "risks to watch",
        "capital return / cash use",
        "source excerpt",
        "original / raw generated readout",
        "original / detailed readout",
        "operator verdict",
        "why",
        "evidence components",
        "empirical recommendation intelligence",
        "supporting evidence",
        "contradictions",
        "expected reward/risk + planning ev",
        "reward/risk + planning ev",
        "position sizing notes",
        "position sizing",
        "invalidation lines",
        "confirmation lines",
        "data confidence gaps",
        "source confidence rows",
        "warnings",
        "what would change",
        "what would change the view",
        "raw details",
        "source details",
        "good",
        "bad / missing",
        "watch",
        "key points",
        "what this means",
        "why it matters",
    }
    if lower in known:
        return True
    words = clean.split()
    return len(words) <= 8 and clean[:1].isupper() and not clean.endswith(".")


def _is_secondary_readout_section(title: str) -> bool:
    lower = title.lower()
    return any(term in lower for term in ("raw", "source excerpt", "source details", "original / detailed", "original / raw"))


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
        height: int = DEFAULT_METRIC_CARD_HEIGHT,
        prominent: bool = False,
        adaptive_height: bool = False,
    ) -> None:
        colors = STATUS_COLORS.get(readout.status, STATUS_COLORS["neutral"])
        super().__init__(parent, bg=colors["bg"], highlightbackground=colors["bar"], highlightthickness=2 if prominent else 1, width=width, height=height)
        # These cards appear inside resizable and detached dashboards. Do not
        # freeze the frame height: fixed-height cards made wrapped text show
        # ellipses or clip even when the surrounding window had plenty of room.
        # Treat the configured height as a minimum, then let Tk grow the card to
        # the full wrapped label/body text.
        self.grid_propagate(True)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(2, weight=1, minsize=max(38, height - 70))
        tk.Frame(self, bg=colors["bar"], width=5).grid(row=0, column=0, rowspan=3, sticky="nsw")
        title_font = ("Segoe UI", 8, "bold")
        label_font = ("Segoe UI", 15 if prominent else 12, "bold")
        body_font = ("Segoe UI", 9 if adaptive_height else 8)
        label_text = str(readout.label or "")
        why_text = str(readout.why or "")
        tk.Label(self, text=readout.title.upper(), bg=colors["bg"], fg=MUTED, font=title_font, anchor="w").grid(row=0, column=1, sticky="ew", padx=METRIC_CARD_PAD_X, pady=(METRIC_CARD_PAD_TOP, 0))
        self._label = tk.Label(self, text=label_text, bg=colors["bg"], fg=colors["fg"], font=label_font, anchor="w", justify=tk.LEFT, wraplength=width - (METRIC_CARD_PAD_X * 2))
        self._label.grid(row=1, column=1, sticky="ew", padx=METRIC_CARD_PAD_X, pady=(2, 0))
        self._why_label = tk.Label(self, text=why_text, bg=colors["bg"], fg=TEXT, font=body_font, wraplength=width - (METRIC_CARD_PAD_X * 2), justify=tk.LEFT, anchor="nw")
        self._why_label.grid(row=2, column=1, sticky="nsew", padx=METRIC_CARD_PAD_X, pady=(4, METRIC_CARD_PAD_BOTTOM))
        self.bind("<Configure>", self._on_resize, add="+")

    def _on_resize(self, event: tk.Event) -> None:
        wrap = max(140, int(event.width) - (METRIC_CARD_PAD_X * 2) - 8)
        self._label.configure(wraplength=wrap)
        self._why_label.configure(wraplength=wrap)


class ScoreBadge(tk.Frame):
    def __init__(self, parent: tk.Widget, readout: BadgeReadout) -> None:
        colors = STATUS_COLORS.get(readout.status, STATUS_COLORS["neutral"])
        super().__init__(parent, bg=colors["bg"], highlightbackground=BORDER, highlightthickness=1)
        tk.Label(self, text=f"{readout.title}: ", bg=colors["bg"], fg=MUTED, font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(8, 0), pady=4)
        tk.Label(self, text=readout.label, bg=colors["bg"], fg=colors["fg"], font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8), pady=4)


class ScoreMeter(tk.Canvas):
    def __init__(self, parent: tk.Widget, *, height: int = 48, background: str = PANEL_BG) -> None:
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
        height = max(self.winfo_height(), 44)
        x0, x1 = 10, width - 10
        y = 16
        self.create_rectangle(x0, y - 5, x1, y + 5, fill=polished_theme.PANEL_ALT, outline="")
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
            self.create_line(center, y - 9, center, y + 9, fill=polished_theme.MUTED)
        self.create_oval(pointer - 5, y - 9, pointer + 5, y + 9, fill=color, outline=polished_theme.TEXT)
        if self._label:
            self.create_text(x0, height - 7, text=self._label, anchor="sw", fill=MUTED, font=("Segoe UI", 8))


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


class RankedRows(tk.Frame):
    def __init__(self, parent: tk.Widget, title: str, rows: Iterable[str], *, status: str = "info", limit: int = 8) -> None:
        super().__init__(parent, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
        self.columnconfigure(1, weight=1)
        colors = STATUS_COLORS.get(status, STATUS_COLORS["info"])
        tk.Label(self, text=title, bg=PANEL_BG, fg=TEXT, font=("Segoe UI", 10, "bold"), anchor="w").grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 5))
        clean_rows = [str(row or "").strip() for row in rows if str(row or "").strip()]
        if not clean_rows:
            clean_rows = ["No rows are available yet."]
        for index, row in enumerate(clean_rows[:limit], start=1):
            badge = tk.Label(
                self,
                text=str(index),
                bg=colors["bg"],
                fg=colors["fg"],
                font=("Segoe UI", 8, "bold"),
                width=3,
                anchor="center",
            )
            badge.grid(row=index, column=0, sticky="nw", padx=(10, 7), pady=(2 if index > 1 else 4, 3))
            tk.Label(
                self,
                text=row,
                bg=PANEL_BG,
                fg=TEXT,
                font=("Segoe UI", 9),
                wraplength=520,
                justify=tk.LEFT,
                anchor="nw",
            ).grid(row=index, column=1, sticky="ew", padx=(0, 10), pady=(2 if index > 1 else 4, 3))
        if len(clean_rows) > limit:
            tk.Label(
                self,
                text=f"+ {len(clean_rows) - limit} more in the full popout.",
                bg=PANEL_BG,
                fg=MUTED,
                font=("Segoe UI", 8),
                anchor="w",
            ).grid(row=limit + 1, column=1, sticky="ew", padx=(0, 10), pady=(2, 8))


class ScenarioImpactBars(tk.Canvas):
    def __init__(self, parent: tk.Widget, *, height: int = 150, background: str = PANEL_BG) -> None:
        super().__init__(parent, height=height, bg=background, highlightthickness=0)
        self._base_height = height
        self._rows: list[tuple[str, float, str]] = []
        self.bind("<Configure>", lambda _event: self.redraw())

    def set_rows(self, rows: list[tuple[str, float, str]]) -> None:
        self._rows = rows
        desired_height = max(self._base_height, 34 + len(self._rows) * 18)
        try:
            if int(float(str(self.cget("height")))) != desired_height:
                self.configure(height=desired_height)
        except (tk.TclError, ValueError):
            pass
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
        self.create_line(center, 6, center, start_y + len(self._rows) * row_height + 4, fill=polished_theme.MUTED)
        self.create_text(center + 4, 6, text="0", anchor="nw", fill=MUTED, font=("Segoe UI", 8))
        for index, (label, value, display) in enumerate(self._rows):
            y = start_y + index * row_height
            bar = (value / max_abs) * (width * 0.38)
            color = "#16a34a" if value >= 0 else "#dc2626"
            self.create_text(8, y, text=label, anchor="nw", fill=TEXT, font=("Segoe UI", 8, "bold"))
            self.create_rectangle(min(center, center + bar), y + 2, max(center, center + bar), y + 12, fill=color, outline="")
            self.create_text(width - 8, y, text=display, anchor="ne", fill=color, font=("Segoe UI", 8, "bold"))


def metric_grid(
    parent: tk.Widget,
    readouts: list[BadgeReadout],
    *,
    columns: int = 4,
    prominent_indexes: set[int] | None = None,
    card_height: int = DEFAULT_METRIC_CARD_HEIGHT,
    prominent_height: int = DEFAULT_PROMINENT_CARD_HEIGHT,
    adaptive_height: bool = False,
) -> None:
    clear_children(parent)
    prominent_indexes = prominent_indexes or set()
    if not adaptive_height and columns >= 4:
        card_height = min(card_height, DEFAULT_METRIC_CARD_HEIGHT)
        prominent_height = min(prominent_height, DEFAULT_PROMINENT_CARD_HEIGHT)
    for column in range(columns):
        parent.columnconfigure(column, weight=1, uniform="metric_cards")
    row_count = max(1, (len(readouts) + max(columns, 1) - 1) // max(columns, 1))
    for row in range(row_count):
        parent.rowconfigure(row, weight=0)
    for index, readout in enumerate(readouts):
        card = MetricCard(
            parent,
            readout,
            prominent=index in prominent_indexes,
            height=prominent_height if index in prominent_indexes else card_height,
            adaptive_height=adaptive_height,
        )
        card.grid(row=index // columns, column=index % columns, sticky="nsew", padx=(0 if index % columns == 0 else METRIC_CARD_GAP, 0), pady=(0, METRIC_CARD_GAP))


def labeled_value_grid(parent: tk.Widget, rows: dict[str, str], *, columns: int = 3) -> None:
    clear_children(parent)
    for column in range(columns):
        parent.columnconfigure(column, weight=1, uniform="label_value")
    for index, (label, value) in enumerate(rows.items()):
        colors = STATUS_COLORS["neutral"]
        cell = tk.Frame(parent, bg=PANEL_BG, highlightbackground=BORDER, highlightthickness=1)
        cell.grid(row=index // columns, column=index % columns, sticky="nsew", padx=(0 if index % columns == 0 else 8, 0), pady=(0, 8))
        cell.columnconfigure(0, weight=1)
        tk.Label(cell, text=label.upper(), bg=PANEL_BG, fg=MUTED, font=("Segoe UI", 8, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=10, pady=(7, 0))
        tk.Label(cell, text=_compact_text(value, COMPACT_VALUE_LIMIT), bg=PANEL_BG, fg=colors["fg"], font=("Segoe UI", 9, "bold"), anchor="w", wraplength=260, justify=tk.LEFT).grid(row=1, column=0, sticky="ew", padx=10, pady=(3, 8))


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
