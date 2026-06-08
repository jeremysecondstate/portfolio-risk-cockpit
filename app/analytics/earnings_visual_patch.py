from __future__ import annotations

from typing import Any, Type
import re
import tkinter as tk
from tkinter import ttk

from app.analytics.earnings_filing_summary import (
    EarningsFilingSummary,
    build_earnings_filing_summary,
    format_earnings_filing_summary,
)

_INSTALLED = False
_ORIGINAL_ANALYZE: Any | None = None
_ORIGINAL_FORMAT: Any | None = None
_ORIGINAL_OPEN_READOUT: Any | None = None
_ORIGINAL_COMPACT_TEXT: Any | None = None


def install_schwab_earnings_visual_extension(app_cls: Type[tk.Tk]) -> None:
    """Add structured SEC filing summaries and richer research popouts."""

    global _INSTALLED, _ORIGINAL_ANALYZE, _ORIGINAL_FORMAT, _ORIGINAL_OPEN_READOUT, _ORIGINAL_COMPACT_TEXT
    if _INSTALLED:
        return
    from app.ui import schwab_research_workspace_extension as research

    _ORIGINAL_ANALYZE = getattr(research, "analyze_earnings_sources")
    _ORIGINAL_FORMAT = getattr(research, "format_earnings_release_digest")
    _ORIGINAL_OPEN_READOUT = getattr(research, "_open_readout_popout")
    _ORIGINAL_COMPACT_TEXT = getattr(research, "_recommendation_compact_text", None)

    research.analyze_earnings_sources = _analyze_earnings_sources_with_summary  # type: ignore[attr-defined]
    research.format_earnings_release_digest = _format_digest_with_summary  # type: ignore[attr-defined]
    research._open_readout_popout = _open_readout_popout_visual  # type: ignore[attr-defined]
    if callable(_ORIGINAL_COMPACT_TEXT):
        research._recommendation_compact_text = _less_aggressive_compact_text  # type: ignore[attr-defined]
    _INSTALLED = True


def _analyze_earnings_sources_with_summary(*args: Any, **kwargs: Any) -> Any:
    digest = _ORIGINAL_ANALYZE(*args, **kwargs) if callable(_ORIGINAL_ANALYZE) else None
    if digest is None:
        return digest
    source_text = _best_source_text(args, kwargs)
    if not source_text.strip():
        return digest
    symbol = str(args[0] if args else getattr(digest, "symbol", "") or "").strip().upper()
    summary = build_earnings_filing_summary(
        symbol,
        source_text,
        company_name=str(getattr(digest, "company_name", "") or ""),
        source_label=str(getattr(digest, "source_label", "") or ""),
        source_date=str(getattr(digest, "source_date", "") or getattr(digest, "filing_date", "") or ""),
        source_url=str(getattr(digest, "source_url", "") or ""),
    )
    if summary.has_structured_data:
        try:
            object.__setattr__(digest, "_structured_filing_summary", summary)
        except Exception:
            pass
    return digest


def _format_digest_with_summary(digest: Any | None) -> str:
    original = _ORIGINAL_FORMAT(digest) if callable(_ORIGINAL_FORMAT) else ""
    summary = getattr(digest, "_structured_filing_summary", None)
    if isinstance(summary, EarningsFilingSummary) and summary.has_structured_data:
        return format_earnings_filing_summary(summary, original_text=original)
    return original


def _best_source_text(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    release = args[1] if len(args) > 1 else kwargs.get("release")
    for source in (kwargs.get("sec_report"), kwargs.get("company_release"), release):
        text = str(getattr(source, "text", "") or "")
        if text.strip():
            return text
    return ""


def _less_aggressive_compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    soft_limit = max(limit, 900)
    if soft_limit > 0 and len(text) > soft_limit:
        return text[: max(0, soft_limit - 3)].rstrip() + "..."
    return text


def _open_readout_popout_visual(source: tk.Text) -> None:
    from app.ui import schwab_research_workspace_extension as research

    if research._is_greek_readout_source(source):  # type: ignore[attr-defined]
        if callable(_ORIGINAL_OPEN_READOUT):
            _ORIGINAL_OPEN_READOUT(source)
        return
    existing = getattr(source, "_readout_window", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                _refresh_visual_readout(source)
                existing.deiconify()
                existing.lift()
                existing.focus_force()
                return
        except tk.TclError:
            pass

    title = str(getattr(source, "_readout_title", "Detailed Readout") or "Detailed Readout")
    window = tk.Toplevel(source.winfo_toplevel())
    window.title(title)
    window.geometry("1320x880")
    window.minsize(980, 680)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    toolbar = ttk.Frame(window, padding=(12, 9), style="Panel.TFrame")
    toolbar.grid(row=0, column=0, sticky="ew")
    toolbar.columnconfigure(0, weight=1)
    ttk.Label(toolbar, text=title, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(toolbar, text="Readable view • tables render as tables • no order behavior changes", style="Subtle.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 8))
    close_button = ttk.Button(toolbar, text="Close")
    close_button.grid(row=0, column=2, sticky="e")

    scroll = research.ScrollableFrame(window, padding=14)  # type: ignore[attr-defined]
    scroll.grid(row=1, column=0, sticky="nsew")
    scroll.body.columnconfigure(0, weight=1)

    source._readout_window = window  # type: ignore[attr-defined]
    source._readout_popout_text = scroll.body  # type: ignore[attr-defined]
    source._readout_popout_refresh = lambda: _refresh_visual_readout(source)  # type: ignore[attr-defined]

    def _on_close() -> None:
        source._readout_window = None  # type: ignore[attr-defined]
        source._readout_popout_text = None  # type: ignore[attr-defined]
        source._readout_popout_refresh = None  # type: ignore[attr-defined]
        window.destroy()

    close_button.configure(command=_on_close)
    window.protocol("WM_DELETE_WINDOW", _on_close)
    _refresh_visual_readout(source)


def _refresh_visual_readout(source: tk.Text) -> None:
    from app.ui import schwab_research_workspace_extension as research

    parent = getattr(source, "_readout_popout_text", None)
    if parent is None:
        return
    try:
        content = source.get("1.0", tk.END).strip() or "Run analysis first. The detailed readout will appear here."
        research.clear_children(parent)  # type: ignore[attr-defined]
        _build_visual_readout_body(parent, str(getattr(source, "_readout_title", "Detailed Readout") or "Detailed Readout"), content)
    except tk.TclError:
        return


def _build_visual_readout_body(parent: ttk.Frame, title: str, content: str) -> None:
    parsed = _parse_readout(content)
    row = 0
    hero = tk.Frame(parent, bg="#eff6ff", highlightbackground="#bfdbfe", highlightthickness=1)
    hero.grid(row=row, column=0, sticky="ew")
    hero.columnconfigure(0, weight=1)
    tk.Label(hero, text=title, bg="#eff6ff", fg="#1e3a8a", font=("Segoe UI", 18, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))
    tk.Label(hero, text=_hero_subtitle(parsed), bg="#eff6ff", fg="#0f172a", font=("Segoe UI", 10), anchor="w", justify=tk.LEFT, wraplength=1040).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
    row += 1

    if parsed["key_values"]:
        cards = ttk.Frame(parent, style="Panel.TFrame")
        cards.grid(row=row, column=0, sticky="ew", pady=(10, 0))
        _key_value_grid(cards, parsed["key_values"][:8])
        row += 1

    for block in parsed["blocks"]:
        kind = block["kind"]
        if kind == "table":
            _table_panel(parent, block["title"], block["headers"], block["rows"]).grid(row=row, column=0, sticky="ew", pady=(10, 0))
            row += 1
        elif kind == "bullets":
            _bullets_panel(parent, block["title"], block["rows"]).grid(row=row, column=0, sticky="ew", pady=(10, 0))
            row += 1
        elif kind == "paragraphs":
            _paragraph_panel(parent, block["title"], block["rows"]).grid(row=row, column=0, sticky="ew", pady=(10, 0))
            row += 1


def _parse_readout(content: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in content.splitlines()]
    blocks: list[dict[str, Any]] = []
    key_values: list[tuple[str, str]] = []
    current_title = "Summary"
    paragraph_rows: list[str] = []
    bullet_rows: list[str] = []
    i = 0

    def flush() -> None:
        nonlocal paragraph_rows, bullet_rows
        if bullet_rows:
            blocks.append({"kind": "bullets", "title": current_title, "rows": bullet_rows})
            bullet_rows = []
        if paragraph_rows:
            blocks.append({"kind": "paragraphs", "title": current_title, "rows": paragraph_rows})
            paragraph_rows = []

    while i < len(lines):
        line = lines[i].strip()
        next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not line:
            i += 1
            continue
        if _is_underline(next_line):
            flush()
            current_title = line
            i += 2
            continue
        if _looks_like_heading(line):
            flush()
            current_title = line.rstrip(":")
            i += 1
            continue
        if _is_markdown_table_line(line):
            flush()
            headers, rows, consumed = _consume_markdown_table(lines, i)
            blocks.append({"kind": "table", "title": current_title, "headers": headers, "rows": rows})
            i = consumed
            continue
        if line.startswith("-"):
            clean = line.lstrip("- ").strip()
            label, value = _split_label_value(clean)
            if label and value:
                key_values.append((label, value))
            bullet_rows.append(clean)
            i += 1
            continue
        label, value = _split_label_value(line)
        if label and value and len(value) <= 180:
            key_values.append((label, value))
        paragraph_rows.append(line)
        i += 1
    flush()
    return {"blocks": _merge_short_blocks(blocks), "key_values": _dedupe_key_values(key_values)}


def _consume_markdown_table(lines: list[str], start: int) -> tuple[list[str], list[list[str]], int]:
    table_lines = []
    i = start
    while i < len(lines) and _is_markdown_table_line(lines[i].strip()):
        table_lines.append(lines[i].strip())
        i += 1
    if not table_lines:
        return [], [], i
    headers = _split_table_row(table_lines[0])
    rows = []
    for line in table_lines[1:]:
        if set(line.replace("|", "").replace(":", "").replace(" ", "")) <= {"-"}:
            continue
        pieces = _split_table_row(line)
        if pieces:
            rows.append(pieces)
    return headers, rows, i


def _table_panel(parent: ttk.Frame, title: str, headers: list[str], rows: list[list[str]]) -> ttk.LabelFrame:
    from app.ui import schwab_research_workspace_extension as research

    box = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    box.columnconfigure(0, weight=1)
    columns = [f"c{index}" for index in range(max(len(headers), 1))]
    tree = ttk.Treeview(box, columns=columns, show="headings", height=max(4, min(12, len(rows) + 1)))
    research._style_research_tree(tree)  # type: ignore[attr-defined]
    for index, column in enumerate(columns):
        label = headers[index] if index < len(headers) else f"Column {index + 1}"
        tree.heading(column, text=label)
        tree.column(column, width=180 if index else 220, anchor=tk.W, stretch=True)
    tree.grid(row=0, column=0, sticky="ew")
    scrollbar = ttk.Scrollbar(box, orient=tk.VERTICAL, command=tree.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=scrollbar.set)
    for row in rows:
        padded = row + [""] * max(0, len(columns) - len(row))
        tree.insert("", tk.END, values=tuple(padded[: len(columns)]))
    return box


def _bullets_panel(parent: ttk.Frame, title: str, rows: list[str]) -> ttk.LabelFrame:
    box = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    box.columnconfigure(0, weight=1)
    for index, row in enumerate(rows):
        ttk.Label(box, text=f"• {row}", style="Subtle.TLabel", wraplength=1120, justify=tk.LEFT).grid(row=index, column=0, sticky="ew", padx=12, pady=(8 if index == 0 else 2, 4))
    return box


def _paragraph_panel(parent: ttk.Frame, title: str, rows: list[str]) -> ttk.LabelFrame:
    box = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    box.columnconfigure(0, weight=1)
    for index, row in enumerate(rows):
        ttk.Label(box, text=row, style="Subtle.TLabel", wraplength=1120, justify=tk.LEFT).grid(row=index, column=0, sticky="ew", padx=12, pady=(8 if index == 0 else 4, 4))
    return box


def _key_value_grid(parent: ttk.Frame, pairs: list[tuple[str, str]]) -> None:
    for column in range(4):
        parent.columnconfigure(column, weight=1, uniform="readout_cards")
    for index, (label, value) in enumerate(pairs):
        card = tk.Frame(parent, bg="#f8fafc", highlightbackground="#cbd5e1", highlightthickness=1)
        card.grid(row=index // 4, column=index % 4, sticky="nsew", padx=(0 if index % 4 == 0 else 8, 0), pady=(0 if index < 4 else 8, 0))
        card.columnconfigure(0, weight=1)
        tk.Label(card, text=label.upper(), bg="#f8fafc", fg="#64748b", font=("Segoe UI", 8, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        tk.Label(card, text=value, bg="#f8fafc", fg="#0f172a", font=("Segoe UI", 11, "bold"), anchor="w", justify=tk.LEFT, wraplength=250).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))


def _hero_subtitle(parsed: dict[str, Any]) -> str:
    for block in parsed.get("blocks", []):
        rows = block.get("rows") or []
        if rows:
            return str(rows[0])[:420]
    return "Readable research popout with native sections and table rendering."


def _looks_like_heading(line: str) -> bool:
    if len(line) > 80 or line.startswith(("-", "|")):
        return False
    lower = line.lower().strip(":")
    known = (
        "headline", "source freshness", "key financial snapshot", "platform / segment revenue", "good", "bad / missing", "watch",
        "supporting evidence", "contradictions", "reward/risk", "position sizing", "warnings", "data confidence gaps",
        "latest quarter snapshot", "quality of earnings", "risks to watch", "what is driving the quarter", "source details",
    )
    return lower in known or (line[:1].isupper() and not line.endswith(".") and len(line.split()) <= 7)


def _is_underline(line: str) -> bool:
    clean = line.strip()
    return len(clean) >= 3 and set(clean) <= {"=", "-"}


def _is_markdown_table_line(line: str) -> bool:
    return line.startswith("|") and line.endswith("|") and line.count("|") >= 2


def _split_table_row(line: str) -> list[str]:
    return [piece.strip() for piece in line.strip().strip("|").split("|")]


def _split_label_value(text: str) -> tuple[str, str]:
    if ":" not in text:
        return "", ""
    label, value = text.split(":", 1)
    label = label.strip(" -")
    value = value.strip()
    if not label or not value or len(label) > 55:
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


def _merge_short_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [block for block in blocks if block.get("rows")]
