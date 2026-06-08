from __future__ import annotations

from typing import Any, Type
import tkinter as tk

from app.analytics.earnings_filing_summary import (
    EarningsFilingSummary,
    build_earnings_filing_summary,
    format_earnings_filing_summary,
)

_INSTALLED = False
_ORIGINAL_ANALYZE: Any | None = None
_ORIGINAL_FORMAT: Any | None = None


def install_schwab_earnings_visual_extension(app_cls: Type[tk.Tk]) -> None:
    """Add structured SEC 10-Q/10-K filing summaries to Schwab earnings readouts."""

    global _INSTALLED, _ORIGINAL_ANALYZE, _ORIGINAL_FORMAT
    if _INSTALLED:
        return
    from app.ui import schwab_research_workspace_extension as research

    _ORIGINAL_ANALYZE = getattr(research, "analyze_earnings_sources")
    _ORIGINAL_FORMAT = getattr(research, "format_earnings_release_digest")
    research.analyze_earnings_sources = _analyze_earnings_sources_with_summary  # type: ignore[attr-defined]
    research.format_earnings_release_digest = _format_digest_with_summary  # type: ignore[attr-defined]
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
