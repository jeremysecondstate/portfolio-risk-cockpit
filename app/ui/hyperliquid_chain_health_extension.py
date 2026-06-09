from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Type

from app.analytics.hyperliquid_chain_health import (
    HyperliquidChainHealthAssessment,
    HyperliquidMarketImpactRead,
    HyperliquidValidatorHealthSnapshot,
    assess_hyperliquid_chain_health,
    build_hyperliquid_market_impact_read,
    format_hyperliquid_chain_health_report,
    format_hyperliquid_chain_health_human_report,
    save_hyperliquid_chain_health_observation,
)
from app.brokers.hyperliquid.client import HyperliquidInfoClient
from app.ui import polished_theme


def install_hyperliquid_chain_health_extension(app_cls: Type[tk.Tk]) -> None:
    """Add a read-only Hyperliquid validator/chain health check."""

    app_cls.refresh_hyperliquid_chain_health = _refresh_hyperliquid_chain_health  # type: ignore[attr-defined]
    app_cls.refresh_hyperliquid_chain_health_workspace = _refresh_hyperliquid_chain_health_workspace  # type: ignore[attr-defined]
    app_cls.open_hyperliquid_chain_health_popup = _open_hyperliquid_chain_health_popup  # type: ignore[attr-defined]


def _refresh_hyperliquid_chain_health(self: tk.Tk) -> None:
    status = getattr(self, "hyperliquid_status_var", None)
    if hasattr(status, "set"):
        status.set("Hyperliquid chain health: checking...")

    try:
        client = HyperliquidInfoClient()
        snapshot = client.fetch_validator_health_snapshot()
        assessment = assess_hyperliquid_chain_health(snapshot)
        try:
            save_hyperliquid_chain_health_observation(snapshot, assessment)
        except Exception:
            pass
        human_report = format_hyperliquid_chain_health_human_report(snapshot, assessment)
        legacy_report = format_hyperliquid_chain_health_report(snapshot, assessment)
        _set_output_text(self, human_report)
        popup = getattr(self, "open_hyperliquid_chain_health_popup", None)
        if callable(popup):
            popup(snapshot, assessment, human_report, legacy_report)
        if hasattr(status, "set"):
            score = "--" if assessment.score is None else f"{assessment.score}/100"
            status.set(f"Hyperliquid chain health: {assessment.temperature} {score}")
    except Exception as exc:
        if hasattr(status, "set"):
            status.set("Hyperliquid chain health: failed")
        messagebox.showerror("Hyperliquid chain health failed", str(exc))


def _refresh_hyperliquid_chain_health_workspace(self: tk.Tk) -> None:
    workspace_output = getattr(self, "hyperliquid_trading_preview_text", None)
    if workspace_output is not None:
        self.preview_text = workspace_output
    _refresh_hyperliquid_chain_health(self)


def _open_hyperliquid_chain_health_popup(
    self: tk.Tk,
    snapshot: HyperliquidValidatorHealthSnapshot,
    assessment: HyperliquidChainHealthAssessment,
    human_report: str,
    legacy_report: str | None = None,
) -> tk.Toplevel:
    market = build_hyperliquid_market_impact_read(snapshot, assessment)
    window = tk.Toplevel(self)
    window.title("Hyperliquid Chain Vibe Check")
    window.geometry("900x700")
    window.minsize(760, 560)
    polished_theme.configure_toplevel(window)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(2, weight=1)

    header = tk.Frame(window, bg=_vibe_color(assessment.temperature), padx=18, pady=14)
    header.grid(row=0, column=0, sticky="ew", padx=14, pady=(14, 8))
    header.columnconfigure(1, weight=1)
    tk.Label(
        header,
        text=assessment.temperature,
        bg=_vibe_color(assessment.temperature),
        fg="#ffffff",
        font=("Segoe UI", 28, "bold"),
    ).grid(row=0, column=0, sticky="w", padx=(0, 18))
    headline = _plain_headline(assessment, market)
    tk.Label(
        header,
        text=headline,
        bg=_vibe_color(assessment.temperature),
        fg="#ffffff",
        font=("Segoe UI", 12, "bold"),
        wraplength=640,
        justify=tk.LEFT,
    ).grid(row=0, column=1, sticky="w")
    score_box = tk.Frame(header, bg=_vibe_color(assessment.temperature))
    score_box.grid(row=0, column=2, sticky="e")
    tk.Label(score_box, text="Score", bg=_vibe_color(assessment.temperature), fg="#ffffff", font=("Segoe UI", 9, "bold")).pack(anchor=tk.E)
    tk.Label(score_box, text=_score_text(assessment.score), bg=_vibe_color(assessment.temperature), fg="#ffffff", font=("Segoe UI", 18, "bold")).pack(anchor=tk.E)

    score_frame = ttk.Frame(window, style="Panel.TFrame", padding=(14, 0))
    score_frame.grid(row=1, column=0, sticky="ew")
    score_frame.columnconfigure(0, weight=1)
    progress = ttk.Progressbar(score_frame, maximum=100, value=max(0, assessment.score or 0))
    progress.grid(row=0, column=0, sticky="ew")

    body = ttk.Frame(window, style="Panel.TFrame", padding=14)
    body.grid(row=2, column=0, sticky="nsew", padx=14, pady=(8, 0))
    body.columnconfigure(0, weight=1)
    body.rowconfigure(2, weight=1)

    cards = ttk.Frame(body, style="Panel.TFrame")
    cards.grid(row=0, column=0, sticky="ew")
    for column in range(3):
        cards.columnconfigure(column, weight=1, uniform="chain_health_cards")
    _metric_card(cards, 0, 0, "Chain status", _card_value_chain(assessment), _card_detail_chain(assessment), _card_status_chain(assessment))
    _metric_card(cards, 0, 1, "Validator set", _validator_set_card_value(assessment), "Top-24 main squad check", _validator_set_status(assessment))
    _metric_card(cards, 0, 2, "Jailed validators", _jailed_card_value(assessment), _jailed_card_detail(assessment), _jailed_status(assessment))
    _metric_card(cards, 1, 0, "Stake concentration", _concentration_card_value(assessment), "A few operators control a lot of stake", _concentration_status(assessment))
    _metric_card(cards, 1, 1, "Trading impact", market.trading_posture.replace("_", " ").title(), f"Execution risk: {market.execution_risk.title()}", _impact_status(market))
    _metric_card(cards, 1, 2, "Data confidence", _score_text(assessment.key_metrics.get("data_confidence_score")), _data_confidence_detail(assessment), _data_confidence_status(assessment))

    bar_box = ttk.LabelFrame(body, text="Validator concentration", style="Card.TLabelframe")
    bar_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
    bar_box.columnconfigure(1, weight=1)
    for index, key in enumerate(("top1_pct", "top3_pct", "top5_pct", "top10_pct")):
        _concentration_bar(bar_box, index, key.replace("_pct", "").upper(), assessment.key_metrics.get(key))

    detail = ttk.LabelFrame(body, text="Plain-English Detail", style="Card.TLabelframe")
    detail.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
    detail.columnconfigure(0, weight=1)
    detail.rowconfigure(0, weight=1)
    text = tk.Text(
        detail,
        **polished_theme.dark_text_options(wrap=tk.WORD, font=("Segoe UI", 10), padx=14, pady=12),
    )
    text.grid(row=0, column=0, sticky="nsew")
    scroll = ttk.Scrollbar(detail, orient=tk.VERTICAL, command=text.yview)
    scroll.grid(row=0, column=1, sticky="ns")
    text.configure(yscrollcommand=scroll.set)
    text.insert(tk.END, human_report)
    text.configure(state=tk.DISABLED)

    footer = ttk.Frame(window, style="Panel.TFrame", padding=14)
    footer.grid(row=3, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    ttk.Label(
        footer,
        text="Infrastructure/risk context only. Not a buy/sell recommendation.",
        style="Subtle.TLabel",
    ).grid(row=0, column=0, sticky="w")
    ttk.Button(footer, text="Show Raw Data Notes", command=lambda: _show_raw_notes(window, assessment, legacy_report)).grid(row=0, column=1, padx=(8, 0))
    ttk.Button(footer, text="Copy Report", command=lambda: _copy_report(window, human_report)).grid(row=0, column=2, padx=(8, 0))
    ttk.Button(footer, text="Close", command=window.destroy, style="Accent.TButton").grid(row=0, column=3, padx=(8, 0))

    window.transient(self)
    window.lift()
    return window


def _set_output_text(self: tk.Tk, report: str) -> None:
    setter = getattr(self, "_set_preview_text", None)
    if callable(setter):
        setter(report)
        return

    output = getattr(self, "preview_text", None)
    if output is None:
        return
    output.configure(state=tk.NORMAL)
    output.delete("1.0", tk.END)
    output.insert(tk.END, report)
    output.configure(state=tk.DISABLED)


def _metric_card(parent: ttk.Frame, row: int, column: int, title: str, value: str, detail: str, status: str) -> None:
    card = tk.Frame(parent, bg=polished_theme.PANEL, bd=1, relief=tk.SOLID, padx=12, pady=10)
    card.grid(row=row, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0), pady=(0 if row == 0 else 8, 0))
    card.columnconfigure(0, weight=1)
    tk.Label(card, text=title.upper(), bg=polished_theme.PANEL, fg=polished_theme.MUTED, font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w")
    tk.Label(card, text=value, bg=polished_theme.PANEL, fg=polished_theme.TEXT, font=("Segoe UI", 14, "bold"), wraplength=230, justify=tk.LEFT).grid(row=1, column=0, sticky="w", pady=(3, 0))
    tk.Label(card, text=detail, bg=polished_theme.PANEL, fg=polished_theme.MUTED, font=("Segoe UI", 9), wraplength=230, justify=tk.LEFT).grid(row=2, column=0, sticky="w", pady=(3, 0))
    tk.Label(card, text=status, bg=_status_color(status), fg="#ffffff", font=("Segoe UI", 8, "bold"), padx=8, pady=3).grid(row=3, column=0, sticky="w", pady=(8, 0))


def _concentration_bar(parent: ttk.LabelFrame, row: int, label: str, value: Any) -> None:
    percent = _float(value) or 0.0
    ttk.Label(parent, text=label, style="Subtle.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=(0 if row == 0 else 8, 0))
    bar = ttk.Progressbar(parent, maximum=100, value=max(0.0, min(100.0, percent)))
    bar.grid(row=row, column=1, sticky="ew", pady=(0 if row == 0 else 8, 0))
    ttk.Label(parent, text=_percent_text(value), style="Subtle.TLabel").grid(row=row, column=2, sticky="e", padx=(10, 0), pady=(0 if row == 0 else 8, 0))


def _show_raw_notes(parent: tk.Toplevel, assessment: HyperliquidChainHealthAssessment, legacy_report: str | None) -> None:
    notes = "\n".join(assessment.raw_data_notes) if assessment.raw_data_notes else "No raw data notes."
    if legacy_report:
        notes = f"{notes}\n\nLegacy technical report:\n{legacy_report}"
    messagebox.showinfo("Raw Data Notes", notes, parent=parent)


def _copy_report(window: tk.Toplevel, report: str) -> None:
    window.clipboard_clear()
    window.clipboard_append(report)


def _vibe_color(temperature: str) -> str:
    return {
        "GREEN": polished_theme.POSITIVE,
        "YELLOW": polished_theme.WARNING,
        "ORANGE": "#ea580c",
        "RED": polished_theme.NEGATIVE,
        "UNKNOWN": polished_theme.MUTED,
    }.get(temperature, polished_theme.MUTED)


def _status_color(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"ok", "normal", "high"}:
        return polished_theme.POSITIVE
    if normalized in {"warning", "caution", "medium"}:
        return polished_theme.WARNING
    if normalized in {"bad", "defensive", "low"}:
        return polished_theme.NEGATIVE
    return polished_theme.MUTED


def _plain_headline(assessment: HyperliquidChainHealthAssessment, market: HyperliquidMarketImpactRead) -> str:
    if market.trading_posture == "NO_READ":
        return "Not enough validator data to make a real chain-health call."
    if assessment.key_metrics.get("jailed_top24", 0) == 0 and _concentration_status(assessment) in {"Warning", "Bad"}:
        return "Main squad looks intact; concentration is the caution flag."
    return assessment.headline


def _card_value_chain(assessment: HyperliquidChainHealthAssessment) -> str:
    if assessment.temperature == "UNKNOWN":
        return "No-read"
    if assessment.key_metrics.get("jailed_top24", 0) or assessment.key_metrics.get("inactive_top24", 0):
        return "Degraded"
    return "Operating"


def _card_detail_chain(assessment: HyperliquidChainHealthAssessment) -> str:
    score = assessment.key_metrics.get("chain_operating_health_score")
    return f"Operating health {_score_text(score)}"


def _card_status_chain(assessment: HyperliquidChainHealthAssessment) -> str:
    score = _float(assessment.key_metrics.get("chain_operating_health_score")) or 0
    if score >= 85:
        return "OK"
    if score >= 60:
        return "Warning"
    return "Bad"


def _validator_set_card_value(assessment: HyperliquidChainHealthAssessment) -> str:
    metrics = assessment.key_metrics
    return f"{metrics.get('top24_active_approximation', 0)}/{metrics.get('active_set_target', 24)}"


def _jailed_card_value(assessment: HyperliquidChainHealthAssessment) -> str:
    return f"{assessment.key_metrics.get('jailed_total', 0)} total"


def _jailed_card_detail(assessment: HyperliquidChainHealthAssessment) -> str:
    return f"{assessment.key_metrics.get('jailed_top24', 0)} in the top 24"


def _concentration_card_value(assessment: HyperliquidChainHealthAssessment) -> str:
    return f"Top 5 {_percent_text(assessment.key_metrics.get('top5_pct'))}"


def _data_confidence_detail(assessment: HyperliquidChainHealthAssessment) -> str:
    missing = assessment.key_metrics.get("missing_metrics") or []
    return "Missing: " + (", ".join(missing) if missing else "none")


def _validator_set_status(assessment: HyperliquidChainHealthAssessment) -> str:
    metrics = assessment.key_metrics
    if metrics.get("jailed_top24", 0) or metrics.get("inactive_top24", 0) or metrics.get("top24_active_approximation", 0) < metrics.get("active_set_target", 24):
        return "Bad"
    return "OK"


def _jailed_status(assessment: HyperliquidChainHealthAssessment) -> str:
    if assessment.key_metrics.get("jailed_top24", 0):
        return "Bad"
    if assessment.key_metrics.get("jailed_total", 0):
        return "Warning"
    return "OK"


def _concentration_status(assessment: HyperliquidChainHealthAssessment) -> str:
    metrics = assessment.key_metrics
    top1 = _float(metrics.get("top1_pct"))
    top3 = _float(metrics.get("top3_pct"))
    top5 = _float(metrics.get("top5_pct"))
    if (top1 is not None and top1 > 25) or (top5 is not None and top5 > 66):
        return "Bad"
    if (top1 is not None and top1 > 15) or (top3 is not None and top3 > 33) or (top5 is not None and top5 > 50):
        return "Warning"
    return "OK"


def _impact_status(market: HyperliquidMarketImpactRead) -> str:
    if market.trading_posture == "DEFENSIVE":
        return "Defensive"
    if market.trading_posture == "CAUTIOUS":
        return "Caution"
    if market.trading_posture == "NORMAL":
        return "OK"
    return "No-read"


def _data_confidence_status(assessment: HyperliquidChainHealthAssessment) -> str:
    score = _float(assessment.key_metrics.get("data_confidence_score")) or 0
    if score >= 85:
        return "OK"
    if score >= 50:
        return "Warning"
    return "Bad"


def _score_text(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{int(value)}/100"
    except (TypeError, ValueError):
        return "--"


def _percent_text(value: Any) -> str:
    number = _float(value)
    return "--" if number is None else f"{number:.1f}%"


def _float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
