from __future__ import annotations

import threading
import tkinter as tk
from tkinter import messagebox
from typing import Any, Type

from app.analytics.capital_structure_pressure import analyze_capital_structure_pressure
from app.analytics.technical_analysis import (
    DEFAULT_COMMAND_CENTER_TIMEFRAMES,
    TechnicalCommandCenterReport,
    TechnicalTicket,
    build_technical_command_center_report,
)
from app.data.market_intelligence import build_external_market_intelligence
from app.ui.trading_cockpit import (
    _external_fmp_filing_metadata,
    _technical_ticket_from_ui,
)


_installed = False


class TechnicalAnalysisDataUnavailable(RuntimeError):
    """Raised when a required Technical Analysis source is incomplete or failed."""


def install_databento_technical_analysis_extension(app_cls: Type[tk.Tk]) -> None:
    """Install strict Databento candle sourcing for the Technical Analysis button."""

    global _installed
    if _installed:
        return

    app_cls.show_technical_analysis = _show_databento_technical_analysis  # type: ignore[method-assign]
    _installed = True


def _show_databento_technical_analysis(self: tk.Tk) -> None:
    symbol = self.symbol_var.get().strip().upper()
    if not symbol:
        messagebox.showerror("Technical analysis failed", "Enter a symbol first.")
        return

    if getattr(self, "_databento_technical_analysis_running", False):
        self.schwab_status_var.set("Technical Analysis: already running")
        _open_or_refresh_schwab_output(self)
        return

    ticket = _technical_ticket_from_ui(self)
    self._databento_technical_analysis_running = True
    self.schwab_status_var.set("Technical Analysis: fetching required Databento candles...")
    self._set_preview_text(
        "TECHNICAL ANALYSIS\n"
        "==================\n\n"
        f"Symbol: {symbol}\n"
        "Fetching required Databento candle timeframes. This run will fail instead of using fallbacks if required data is incomplete."
    )
    _open_or_refresh_schwab_output(self)

    worker = threading.Thread(
        target=_run_databento_technical_analysis_worker,
        args=(self, symbol, ticket),
        daemon=True,
        name=f"databento-technical-analysis-{symbol}",
    )
    worker.start()


def _run_databento_technical_analysis_worker(
    app: tk.Tk,
    symbol: str,
    ticket: TechnicalTicket,
) -> None:
    try:
        report = _build_databento_technical_analysis_report(symbol, ticket)
    except Exception as exc:
        message = str(exc)
        app.after(0, lambda: _finish_databento_technical_analysis_error(app, message))
        return

    app.after(0, lambda: _finish_databento_technical_analysis_success(app, report))


def _build_databento_technical_analysis_report(
    symbol: str,
    ticket: TechnicalTicket,
) -> TechnicalCommandCenterReport:
    try:
        external_intelligence = build_external_market_intelligence(
            symbol,
            force_refresh=True,
        )
    except Exception as exc:
        raise TechnicalAnalysisDataUnavailable(
            "Technical Analysis aborted: required market-intelligence fetch failed.\n\n"
            f"Source: Databento/FMP enrichment bundle\n"
            f"Error: {type(exc).__name__}: {exc}"
        ) from exc

    _assert_required_databento_candles(external_intelligence)

    try:
        fmp_filings = _external_fmp_filing_metadata(external_intelligence)
        capital_structure_pressure = analyze_capital_structure_pressure(
            symbol,
            fmp_profile=getattr(external_intelligence, "fmp_profile", None),
            fmp_filing_metadata=fmp_filings,
        )
    except Exception as exc:
        raise TechnicalAnalysisDataUnavailable(
            "Technical Analysis aborted: required capital-structure source failed.\n\n"
            f"Source: SEC/FMP filing context\n"
            f"Error: {type(exc).__name__}: {exc}"
        ) from exc

    return build_technical_command_center_report(
        symbol,
        {},
        benchmark_candles={},
        quote_snapshot=None,
        ticket=ticket,
        warnings=[],
        capital_structure_pressure=capital_structure_pressure,
        external_intelligence=external_intelligence,
    )


def _assert_required_databento_candles(external_intelligence: Any | None) -> None:
    candles_by_timeframe = getattr(external_intelligence, "databento_technical_candles", None)
    if not isinstance(candles_by_timeframe, dict):
        raise TechnicalAnalysisDataUnavailable(
            "Technical Analysis aborted: Databento technical candles were missing from the market-intelligence payload."
        )

    failures: list[str] = []
    for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES:
        candles = candles_by_timeframe.get(spec.key) or ()
        try:
            count = len(candles)
        except TypeError:
            count = 0
        required = 1 if spec.key == "timing_1m" else 35
        if count < required:
            failures.append(f"- {spec.label} ({spec.key}): got {count} candle(s), required at least {required}")

    provider_failures = _provider_failure_lines(external_intelligence)
    if failures or provider_failures:
        parts = ["Technical Analysis aborted: required Databento data is incomplete."]
        if failures:
            parts.extend(["", "Missing/incomplete required candle timeframes:", *failures])
        if provider_failures:
            parts.extend(["", "Provider failures:", *provider_failures])
        parts.append("")
        parts.append("No Schwab, FMP, cached, or partial fallback analysis was used.")
        raise TechnicalAnalysisDataUnavailable("\n".join(parts))


def _provider_failure_lines(external_intelligence: Any | None) -> list[str]:
    statuses = getattr(external_intelligence, "source_statuses", ()) or ()
    failures: list[str] = []
    for status in statuses:
        source = str(getattr(status, "source", "") or "provider").strip()
        state = str(getattr(status, "status", "") or "").strip().lower()
        message = str(getattr(status, "message", "") or "").strip()
        if "databento technical history" not in source.lower():
            continue
        if state not in {"available"}:
            failures.append(f"- {source}: {state or 'unknown'}; {message}")
    return failures


def _finish_databento_technical_analysis_success(app: tk.Tk, report: TechnicalCommandCenterReport) -> None:
    app._databento_technical_analysis_running = False
    app.schwab_status_var.set("Technical Analysis: Databento candles")
    app._set_preview_text(app.format_technical_analysis_report(report))
    _open_or_refresh_schwab_output(app)


def _finish_databento_technical_analysis_error(app: tk.Tk, message: str) -> None:
    app._databento_technical_analysis_running = False
    app.schwab_status_var.set("Technical Analysis: failed")
    app._set_preview_text(
        "TECHNICAL ANALYSIS FAILED\n"
        "=========================\n\n"
        f"{message}\n"
    )
    _open_or_refresh_schwab_output(app)
    messagebox.showerror("Technical analysis failed", message)


def _open_or_refresh_schwab_output(app: tk.Tk) -> None:
    opener = getattr(app, "open_schwab_output_popout", None)
    if callable(opener):
        try:
            opener()
            return
        except Exception:
            pass

    refresher = getattr(app, "refresh_schwab_output_popout", None)
    if callable(refresher):
        try:
            refresher(force=True)
        except Exception:
            pass
