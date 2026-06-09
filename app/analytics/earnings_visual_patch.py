from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Any, Iterable, Type

from app.analytics.earnings_filing_summary import (
    EarningsFilingSummary,
    build_earnings_filing_summary,
    format_earnings_filing_summary,
    parse_earnings_filing_summary_from_readout,
)
from app.ui import polished_theme
from app.ui.research_widgets import (
    MUTED,
    PANEL_BG,
    STATUS_COLORS,
    TEXT,
    VisualReadout,
    VisualReadoutBlock,
    clear_children,
    parse_visual_readout,
    truncate_with_detail,
)

_INSTALLED = False
_ORIGINAL_ANALYZE: Any | None = None
_ORIGINAL_FORMAT: Any | None = None
_ORIGINAL_OPEN_READOUT: Any | None = None
_ORIGINAL_COMPACT_TEXT: Any | None = None


def install_schwab_earnings_visual_extension(app_cls: Type[tk.Tk]) -> None:
    """Add structured SEC filing summaries and low-noise visual research popouts."""

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
    polished_theme.configure_toplevel(window)
    window.title(title)
    window.geometry("1320x880")
    window.minsize(980, 680)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    toolbar = ttk.Frame(window, padding=(12, 9), style="Panel.TFrame")
    toolbar.grid(row=0, column=0, sticky="ew")
    toolbar.columnconfigure(0, weight=1)
    ttk.Label(toolbar, text=title, font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(toolbar, text="Readable view | native tables | order behavior unchanged", style="Subtle.TLabel").grid(row=0, column=1, sticky="e", padx=(12, 8))
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
    parent = getattr(source, "_readout_popout_text", None)
    if parent is None:
        return
    try:
        content = source.get("1.0", tk.END).strip() or "Run analysis first. The detailed readout will appear here."
        clear_children(parent)
        _build_visual_readout_body(parent, str(getattr(source, "_readout_title", "Detailed Readout") or "Detailed Readout"), content)
    except tk.TclError:
        return


def _build_visual_readout_body(parent: ttk.Frame, title: str, content: str) -> None:
    parsed = parse_visual_readout(content, title_hint=title)
    lower_title = title.lower()
    if "earnings release" in lower_title:
        summary = parse_earnings_filing_summary_from_readout(_symbol_from_readout(title, content), content)
        if summary.has_structured_data:
            _build_earnings_filing_body(parent, parsed, summary)
            return
    if "recommendation engine" in lower_title:
        _build_recommendation_body(parent, parsed)
        return
    if _is_evidence_detail_title(lower_title):
        _build_ranked_detail_body(parent, parsed)
        return
    _build_generic_body(parent, parsed)


def _build_generic_body(parent: ttk.Frame, parsed: VisualReadout) -> None:
    row = _hero(parent, parsed.title, parsed.hero, "info")
    primary_values = parsed.key_values[:8]
    if primary_values:
        _cards(parent, primary_values).grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1
    secondary_blocks: list[VisualReadoutBlock] = []
    for block in parsed.blocks:
        if block.secondary:
            secondary_blocks.append(block)
            continue
        _block_widget(parent, block).grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1
    detail_text = _serialize_blocks(secondary_blocks).strip()
    if detail_text:
        _collapsible_detail(parent, "Raw / Source Detail", detail_text).grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1
    _collapsible_detail(parent, "Full Generated Readout", parsed.raw_text).grid(row=row, column=0, sticky="ew", pady=(10, 0))


def _build_recommendation_body(parent: ttk.Frame, parsed: VisualReadout) -> None:
    operator = _operator_card_value(parsed)
    row = _hero(parent, parsed.title, _recommendation_hero(parsed), _status_for_text(_value_for(parsed, "recommendation")))
    summary_cards = [
        ("Recommendation", _value_for(parsed, "recommendation") or "No read"),
        ("Confidence", _value_for(parsed, "confidence") or "--"),
        ("Evidence Score", _value_for(parsed, "evidence score") or "--"),
        ("Data Confidence", _value_for(parsed, "data confidence") or "--"),
        ("Operator Verdict", operator or "--"),
    ]
    _cards(parent, summary_cards).grid(row=row, column=0, sticky="ew", pady=(10, 0))
    row += 1

    evidence_table = _first_block(parsed, "evidence components", kind="table")
    if evidence_table is not None:
        _table_panel(parent, "Evidence Component Table", evidence_table.headers, evidence_table.table_rows).grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1

    ordered_sections = (
        "Why",
        "Supporting Evidence",
        "Contradictions",
        "Expected Reward/Risk + Planning EV",
        "Reward/Risk + Planning EV",
        "Position Sizing Notes",
        "Data Confidence Gaps",
        "Warnings",
        "What Would Change",
        "Invalidation Lines",
        "Confirmation Lines",
        "Source Confidence Rows",
        "Empirical Recommendation Intelligence",
    )
    used: set[int] = set()
    for section in ordered_sections:
        block = _first_block(parsed, section, used=used)
        if block is None or block.kind == "table":
            continue
        used.add(id(block))
        _section_panel(parent, block.title, block.rows, status=_status_for_text(block.title + " " + " ".join(block.rows))).grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1

    _collapsible_detail(parent, "Raw Generated Recommendation Text", parsed.raw_text).grid(row=row, column=0, sticky="ew", pady=(10, 0))


def _build_ranked_detail_body(parent: ttk.Frame, parsed: VisualReadout) -> None:
    rows = _detail_rows(parsed)
    status = _status_for_text(parsed.title + " " + " ".join(rows))
    row = _hero(parent, parsed.title, _ranked_hero(parsed.title, rows), status)
    _ranked_rows_panel(parent, parsed.title, rows, status=status).grid(row=row, column=0, sticky="ew", pady=(10, 0))
    row += 1
    _collapsible_detail(parent, "Full Detail", parsed.raw_text).grid(row=row, column=0, sticky="ew", pady=(10, 0))


def _build_earnings_filing_body(parent: ttk.Frame, parsed: VisualReadout, summary: EarningsFilingSummary) -> None:
    row = _hero(parent, "Earnings Filing Dashboard", summary.headline or parsed.hero, "info")
    metric_cards = [(metric.label, f"{metric.latest_text} | {metric.change_text}") for metric in summary.metrics[:6]]
    if metric_cards:
        _cards(parent, metric_cards).grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1
    if summary.metrics:
        _table_panel(parent, "Financial Snapshot", ("Metric", "Latest", "Prior / Comparable", "Change", "Read"), _metric_rows(summary.metrics)).grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1
    if summary.platform_rows:
        _table_panel(parent, "Segment / Platform Revenue", ("Segment", "Latest", "Prior / Comparable", "Change", "Read"), _metric_rows(summary.platform_rows)).grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1
    for title, rows, status in (
        ("Growth Drivers", summary.growth_drivers, "good"),
        ("Quality Of Earnings", summary.quality_points, "info"),
        ("Risks To Watch", summary.risks, "bad"),
        ("Capital Return / Cash Use", summary.capital_return, "mixed"),
    ):
        if rows:
            _section_panel(parent, title, rows, status=status).grid(row=row, column=0, sticky="ew", pady=(10, 0))
            row += 1
    source_rows = _source_rows(summary)
    if source_rows:
        _section_panel(parent, "Source Freshness / Links", source_rows, status="info").grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1
    if summary.raw_excerpt:
        _collapsible_detail(parent, "Raw SEC Excerpt", summary.raw_excerpt).grid(row=row, column=0, sticky="ew", pady=(10, 0))
        row += 1
    _collapsible_detail(parent, "Full Generated Earnings Readout", parsed.raw_text).grid(row=row, column=0, sticky="ew", pady=(10, 0))


def _hero(parent: ttk.Frame, title: str, subtitle: str, status: str) -> int:
    colors = STATUS_COLORS.get(status, STATUS_COLORS["info"])
    hero = tk.Frame(parent, bg=colors["bg"], highlightbackground=colors["bar"], highlightthickness=1)
    hero.grid(row=0, column=0, sticky="ew")
    hero.columnconfigure(0, weight=1)
    tk.Label(hero, text=title, bg=colors["bg"], fg=colors["fg"], font=("Segoe UI", 18, "bold"), anchor="w", justify=tk.LEFT).grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))
    tk.Label(hero, text=subtitle, bg=colors["bg"], fg=TEXT, font=("Segoe UI", 10), anchor="w", justify=tk.LEFT, wraplength=1100).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 16))
    return 1


def _cards(parent: ttk.Frame, pairs: Iterable[tuple[str, str]], *, columns: int = 5) -> ttk.Frame:
    frame = ttk.Frame(parent, style="Panel.TFrame")
    for column in range(columns):
        frame.columnconfigure(column, weight=1, uniform="visual_cards")
    for index, (label, value) in enumerate(pairs):
        status = _status_for_text(str(value))
        colors = STATUS_COLORS.get(status, STATUS_COLORS["neutral"])
        card = tk.Frame(frame, bg=PANEL_BG, highlightbackground=colors["bar"], highlightthickness=1)
        card.grid(row=index // columns, column=index % columns, sticky="nsew", padx=(0 if index % columns == 0 else 8, 0), pady=(0 if index < columns else 8, 0))
        card.columnconfigure(0, weight=1)
        tk.Label(card, text=str(label).upper(), bg=PANEL_BG, fg=MUTED, font=("Segoe UI", 8, "bold"), anchor="w").grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        tk.Label(card, text=str(value), bg=PANEL_BG, fg=colors["fg"], font=("Segoe UI", 11, "bold"), anchor="w", justify=tk.LEFT, wraplength=210).grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 11))
    return frame


def _block_widget(parent: ttk.Frame, block: VisualReadoutBlock) -> tk.Widget:
    if block.kind == "table":
        return _table_panel(parent, block.title, block.headers, block.table_rows)
    if block.kind == "bullets":
        return _section_panel(parent, block.title, block.rows, status=_status_for_text(block.title + " " + " ".join(block.rows)))
    return _paragraph_panel(parent, block.title, block.rows)


def _table_panel(parent: ttk.Frame, title: str, headers: Iterable[str], rows: Iterable[Iterable[str]]) -> ttk.LabelFrame:
    from app.ui import schwab_research_workspace_extension as research

    row_values = [tuple(str(value) for value in row) for row in rows]
    header_values = tuple(str(header) for header in headers) or ("Detail",)
    box = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    box.columnconfigure(0, weight=1)
    columns = tuple(f"c{index}" for index in range(len(header_values)))
    tree = ttk.Treeview(box, columns=columns, show="headings", height=max(4, min(12, len(row_values) + 1)))
    try:
        research._style_research_tree(tree)  # type: ignore[attr-defined]
    except Exception:
        pass
    for index, column in enumerate(columns):
        label = header_values[index]
        width = 230 if index == 0 else 160
        anchor = tk.W if index == 0 or "read" in label.lower() or "reason" in label.lower() else tk.CENTER
        tree.heading(column, text=label)
        tree.column(column, width=width, anchor=anchor, stretch=index == len(columns) - 1 or "read" in label.lower() or "reason" in label.lower())
    tree.grid(row=0, column=0, sticky="ew")
    scrollbar = ttk.Scrollbar(box, orient=tk.VERTICAL, command=tree.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    tree.configure(yscrollcommand=scrollbar.set)
    for row in row_values:
        padded = row + ("",) * max(0, len(columns) - len(row))
        tree.insert("", tk.END, values=padded[: len(columns)])
    return box


def _section_panel(parent: ttk.Frame, title: str, rows: Iterable[str], *, status: str = "info") -> ttk.LabelFrame:
    box = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    box.columnconfigure(1, weight=1)
    colors = STATUS_COLORS.get(status, STATUS_COLORS["info"])
    clean_rows = [str(row or "").strip() for row in rows if str(row or "").strip()]
    if not clean_rows:
        clean_rows = ["No rows are available yet."]
    for index, row in enumerate(clean_rows):
        tk.Label(box, text=str(index + 1), bg=colors["bg"], fg=colors["fg"], font=("Segoe UI", 8, "bold"), width=3).grid(row=index, column=0, sticky="nw", padx=(10, 8), pady=(8 if index == 0 else 3, 4))
        ttk.Label(box, text=row, style="Subtle.TLabel", wraplength=1080, justify=tk.LEFT).grid(row=index, column=1, sticky="ew", padx=(0, 12), pady=(8 if index == 0 else 3, 4))
    return box


def _paragraph_panel(parent: ttk.Frame, title: str, rows: Iterable[str]) -> ttk.LabelFrame:
    box = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    box.columnconfigure(0, weight=1)
    for index, row in enumerate(rows):
        ttk.Label(box, text=str(row), style="Subtle.TLabel", wraplength=1120, justify=tk.LEFT).grid(row=index, column=0, sticky="ew", padx=12, pady=(8 if index == 0 else 4, 4))
    return box


def _ranked_rows_panel(parent: ttk.Frame, title: str, rows: list[str], *, status: str) -> ttk.LabelFrame:
    box = ttk.LabelFrame(parent, text=f"{title} - Ranked Detail", style="Card.TLabelframe")
    box.columnconfigure(2, weight=1)
    colors = STATUS_COLORS.get(status, STATUS_COLORS["info"])
    full_detail: list[str] = []
    for index, row in enumerate(rows or ["No rows are available yet."], start=1):
        text = truncate_with_detail(row, 320)
        if text.truncated:
            full_detail.append(text.detail)
        fields = _evidence_detail_fields(row)
        tk.Label(box, text=str(index), bg=colors["bg"], fg=colors["fg"], font=("Segoe UI", 8, "bold"), width=3).grid(row=index - 1, column=0, sticky="nw", padx=(10, 8), pady=(8 if index == 1 else 4, 4))
        tk.Frame(box, bg=colors["bar"], width=4).grid(row=index - 1, column=1, sticky="nsw", pady=(8 if index == 1 else 4, 4))
        if fields:
            _evidence_field_grid(box, fields).grid(row=index - 1, column=2, sticky="ew", padx=(10, 12), pady=(8 if index == 1 else 4, 4))
        else:
            ttk.Label(box, text=text.display, style="Subtle.TLabel", wraplength=1060, justify=tk.LEFT).grid(row=index - 1, column=2, sticky="ew", padx=(10, 12), pady=(8 if index == 1 else 4, 4))
    if full_detail:
        _collapsible_detail(box, "Untruncated Row Detail", "\n\n".join(full_detail)).grid(row=len(rows) + 1, column=0, columnspan=3, sticky="ew", padx=10, pady=(6, 10))
    return box


def _evidence_field_grid(parent: ttk.Frame, fields: list[tuple[str, str]]) -> ttk.Frame:
    frame = ttk.Frame(parent, style="Panel.TFrame")
    frame.columnconfigure(1, weight=1)
    for row_index, (label, value) in enumerate(fields):
        clean_value = " ".join(str(value or "").split())
        clipped = truncate_with_detail(clean_value, 260)
        tk.Label(
            frame,
            text=label.upper(),
            bg=PANEL_BG,
            fg=MUTED,
            font=("Segoe UI", 8, "bold"),
            anchor="w",
            width=16,
        ).grid(row=row_index, column=0, sticky="nw", padx=(0, 10), pady=(0 if row_index == 0 else 3, 3))
        ttk.Label(
            frame,
            text=clipped.display,
            style="Subtle.TLabel",
            wraplength=900,
            justify=tk.LEFT,
        ).grid(row=row_index, column=1, sticky="ew", pady=(0 if row_index == 0 else 3, 3))
    return frame


def _evidence_detail_fields(row: str) -> list[tuple[str, str]]:
    text = " ".join(str(row or "").split())
    if not text:
        return []

    pipe_parts = [part.strip() for part in text.split(" | ") if part.strip()]
    if len(pipe_parts) >= 5:
        return _trim_evidence_fields(
            [
                ("Component", pipe_parts[0]),
                ("Vote", pipe_parts[1]),
                ("Confidence", pipe_parts[2]),
                ("Read", pipe_parts[3]),
                ("Finding", " | ".join(pipe_parts[4:])),
            ]
        )
    if len(pipe_parts) == 4:
        return _trim_evidence_fields(
            [
                ("Component", pipe_parts[0]),
                ("Vote", pipe_parts[1]),
                ("Read", pipe_parts[2]),
                ("Finding", pipe_parts[3]),
            ]
        )
    if len(pipe_parts) == 3 and re.match(r"^[+\-]?\d", pipe_parts[1]):
        return _trim_evidence_fields([("Component", pipe_parts[0]), ("Vote", pipe_parts[1]), ("Finding", pipe_parts[2])])

    component_vote = re.match(
        r"^(?P<component>[^:\n]{2,90}):\s*(?P<vote>[+\-]?\d+(?:\.\d+)?(?:\s*/\s*100|%)?)\s*(?:[-:]\s*)?(?P<tail>.*)$",
        text,
    )
    if component_vote:
        fields = [
            ("Component", component_vote.group("component").strip()),
            ("Vote", component_vote.group("vote").strip()),
        ]
        tail = component_vote.group("tail").strip()
        fields.extend(_keyed_evidence_fields(tail) or _heuristic_evidence_fields(tail))
        return _trim_evidence_fields(fields)

    keyed = _keyed_evidence_fields(text)
    if keyed:
        return _trim_evidence_fields(keyed)

    if len(text) >= 140 and ":" in text:
        component, finding = text.split(":", 1)
        return _trim_evidence_fields([("Component", component), *_heuristic_evidence_fields(finding)])
    return []


def _keyed_evidence_fields(text: str) -> list[tuple[str, str]]:
    labels = (
        "Component",
        "Vote",
        "Finding",
        "Signals",
        "Signal",
        "Missing",
        "Read",
        "Action / Verify next",
        "Action",
        "Verify next",
        "Status",
        "Confidence",
        "Reason",
    )
    pattern = re.compile(r"\b(" + "|".join(re.escape(label) for label in labels) + r")\s*:\s*", re.IGNORECASE)
    matches = list(pattern.finditer(text))
    if not matches:
        return []
    fields: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        label = _normalize_evidence_label(match.group(1))
        value = text[start:end].strip(" .;-")
        if value:
            fields.append((label, value))
    return fields


def _heuristic_evidence_fields(text: str) -> list[tuple[str, str]]:
    clean = text.strip(" .;-")
    if not clean:
        return []
    fields: list[tuple[str, str]] = []
    finding: list[str] = []
    signals: list[str] = []
    missing: list[str] = []
    actions: list[str] = []
    for sentence in re.split(r"(?<=[.;])\s+", clean):
        part = sentence.strip(" .;")
        if not part:
            continue
        lower = part.lower()
        if lower.startswith("missing") or " no " in lower or "unavailable" in lower or "not loaded" in lower:
            missing.append(part)
        elif lower.startswith("action") or lower.startswith("verify") or "refresh" in lower or "verify" in lower:
            actions.append(part)
        elif "signal" in lower or "volume" in lower or "vwap" in lower or "price" in lower or "trend" in lower:
            signals.append(part)
        else:
            finding.append(part)
    if finding:
        fields.append(("Finding", " ".join(finding)))
    if signals:
        fields.append(("Signals", " ".join(signals)))
    if missing:
        fields.append(("Missing", " ".join(missing)))
    if actions:
        fields.append(("Action / Verify next", " ".join(actions)))
    return fields or [("Finding", clean)]


def _normalize_evidence_label(label: str) -> str:
    lower = label.lower()
    if lower == "signal":
        return "Signals"
    if lower in {"action", "verify next", "action / verify next"}:
        return "Action / Verify next"
    if lower == "reason":
        return "Finding"
    return label[:1].upper() + label[1:].lower()


def _trim_evidence_fields(fields: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for label, value in fields:
        clean_label = _normalize_evidence_label(str(label or "").strip())
        clean_value = " ".join(str(value or "").split())
        if not clean_label or not clean_value:
            continue
        key = f"{clean_label}:{clean_value}"
        if key in seen:
            continue
        seen.add(key)
        result.append((clean_label, clean_value))
    return result[:7]


def _collapsible_detail(parent: ttk.Frame, title: str, text: str) -> ttk.LabelFrame:
    box = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
    box.columnconfigure(0, weight=1)
    expanded = tk.BooleanVar(value=False)
    button = ttk.Button(box, text="Show Detail")
    button.grid(row=0, column=0, sticky="w", padx=10, pady=8)
    body = ttk.Frame(box, style="Panel.TFrame")
    body.columnconfigure(0, weight=1)
    line_count = max(5, min(18, len(str(text or "").splitlines()) + 2))
    target = tk.Text(
        body,
        **polished_theme.dark_text_options(
            wrap=tk.WORD,
            height=line_count,
            font=("Segoe UI", 9),
            padx=12,
            pady=10,
            background=PANEL_BG,
            foreground=TEXT,
        ),
    )
    target.insert(tk.END, str(text or "").strip())
    target.configure(state=tk.DISABLED)
    target.grid(row=0, column=0, sticky="ew")

    def toggle() -> None:
        if expanded.get():
            body.grid_remove()
            expanded.set(False)
            button.configure(text="Show Detail")
            return
        body.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        expanded.set(True)
        button.configure(text="Hide Detail")

    button.configure(command=toggle)
    return box


def _first_block(parsed: VisualReadout, title: str, *, kind: str | None = None, used: set[int] | None = None) -> VisualReadoutBlock | None:
    target = title.lower()
    for block in parsed.blocks:
        if used is not None and id(block) in used:
            continue
        if kind is not None and block.kind != kind:
            continue
        if block.title.lower() == target:
            return block
    for block in parsed.blocks:
        if used is not None and id(block) in used:
            continue
        if kind is not None and block.kind != kind:
            continue
        if target in block.title.lower():
            return block
    return None


def _value_for(parsed: VisualReadout, label: str) -> str:
    target = label.lower()
    for key, value in parsed.key_values:
        if key.lower() == target:
            return value
    for key, value in parsed.key_values:
        if target in key.lower():
            return value
    return ""


def _operator_card_value(parsed: VisualReadout) -> str:
    block = _first_block(parsed, "Operator Verdict")
    if block is None:
        return ""
    for row in block.rows:
        if row.lower().startswith("primary action:"):
            return row.split(":", 1)[1].strip()
    return block.rows[0] if block.rows else ""


def _recommendation_hero(parsed: VisualReadout) -> str:
    recommendation = _value_for(parsed, "recommendation") or "No recommendation label found."
    confidence = _value_for(parsed, "confidence") or "--"
    evidence = _value_for(parsed, "evidence score") or "--"
    data = _value_for(parsed, "data confidence") or "--"
    return f"{recommendation}. Confidence {confidence}; evidence {evidence}; data confidence {data}."


def _ranked_hero(title: str, rows: list[str]) -> str:
    if not rows:
        return f"{title} has no rows yet. Full generated text remains available below."
    return f"{len(rows)} ranked row(s). Start with the highest-priority detail, then expand the full text if needed."


def _detail_rows(parsed: VisualReadout) -> list[str]:
    rows: list[str] = []
    for block in parsed.blocks:
        if block.kind == "table":
            rows.extend(" | ".join(row) for row in block.table_rows)
        else:
            rows.extend(block.rows)
    return [row for row in rows if row]


def _serialize_blocks(blocks: Iterable[VisualReadoutBlock]) -> str:
    lines: list[str] = []
    for block in blocks:
        lines.extend(["", block.title])
        if block.kind == "table":
            lines.append(" | ".join(block.headers))
            lines.extend(" | ".join(row) for row in block.table_rows)
        else:
            lines.extend(block.rows)
    return "\n".join(lines).strip()


def _metric_rows(metrics: Iterable[Any]) -> tuple[tuple[str, str, str, str, str], ...]:
    rows: list[tuple[str, str, str, str, str]] = []
    for metric in metrics:
        rows.append(
            (
                str(getattr(metric, "label", "") or ""),
                str(getattr(metric, "latest_text", "") or "--"),
                str(getattr(metric, "prior_text", "") or "--"),
                str(getattr(metric, "change_text", "") or "--"),
                str(getattr(metric, "read", "") or ""),
            )
        )
    return tuple(rows)


def _source_rows(summary: EarningsFilingSummary) -> list[str]:
    rows = list(summary.source_notes)
    if summary.source_label and not any("Source:" in row for row in rows):
        rows.append(f"Source: {summary.source_label}.")
    if summary.source_date and not any("date" in row.lower() for row in rows):
        rows.append(f"Filed / loaded date: {summary.source_date}.")
    if summary.source_url and not any(summary.source_url in row for row in rows):
        rows.append(f"URL: {summary.source_url}.")
    return rows


def _symbol_from_readout(title: str, content: str) -> str:
    for text in (title, content[:500]):
        match = re.search(r"\b(?:Readout|Explanation|Dashboard)\s*-\s*([A-Z][A-Z0-9.\-]{0,9})\b", text)
        if match:
            return match.group(1).upper()
    return "SYMBOL"


def _status_for_text(text: str) -> str:
    lower = str(text or "").lower()
    if any(term in lower for term in ("avoid", "warning", "risk", "contradiction", "gap", "missing", "error", "bad", "unfavorable", "reduce")):
        return "bad"
    if any(term in lower for term in ("watch", "wait", "mixed", "limited", "stale", "caution", "confirmation", "invalidation")):
        return "mixed"
    if any(term in lower for term in ("constructive", "support", "favorable", "fresh", "high", "good", "loaded", "positive")):
        return "good"
    return "info"


def _is_evidence_detail_title(lower_title: str) -> bool:
    return any(
        term in lower_title
        for term in (
            "supporting evidence",
            "contradictions",
            "warnings",
            "data confidence gaps",
            "reward/risk",
            "position sizing",
            "invalidation lines",
            "confirmation lines",
            "what would change",
        )
    )
