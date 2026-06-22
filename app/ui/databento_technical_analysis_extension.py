from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Type

from app.analytics.capital_structure_pressure import (
    analyze_capital_structure_pressure,
    unknown_capital_structure_report,
)
from app.analytics.technical_analysis import build_technical_command_center_report
from app.data.market_intelligence import build_external_market_intelligence
from app.ui.trading_cockpit import (
    _external_fmp_filing_metadata,
    _quote_snapshot_from_external_intelligence,
    _technical_ticket_from_ui,
)


_installed = False


def install_databento_technical_analysis_extension(app_cls: Type[tk.Tk]) -> None:
    """Install Databento-only candle sourcing for the Technical Analysis button."""

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

    try:
        external_intelligence = None
        warnings: list[str] = []
        try:
            external_intelligence = build_external_market_intelligence(
                symbol,
                force_refresh=False,
            )
        except Exception as exc:
            warnings.append(f"Databento/FMP market intelligence unavailable: {exc}")

        if not _has_databento_technical_candles(external_intelligence):
            warnings.append(
                "Databento did not return technical candles; Schwab pricehistory fallback is disabled for Technical Analysis."
            )

        quote_snapshot = _quote_snapshot_from_external_intelligence(symbol, external_intelligence)

        try:
            fmp_filings = _external_fmp_filing_metadata(external_intelligence)
            capital_structure_pressure = analyze_capital_structure_pressure(
                symbol,
                fmp_profile=getattr(external_intelligence, "fmp_profile", None),
                fmp_filing_metadata=fmp_filings,
            )
        except Exception as exc:
            capital_structure_pressure = unknown_capital_structure_report(
                symbol,
                warnings=[f"Capital structure overlay unavailable: {exc}"],
            )

        report = build_technical_command_center_report(
            symbol,
            {},
            benchmark_candles={},
            quote_snapshot=quote_snapshot,
            ticket=_technical_ticket_from_ui(self),
            warnings=warnings,
            capital_structure_pressure=capital_structure_pressure,
            external_intelligence=external_intelligence,
        )
        self.schwab_status_var.set("Technical Analysis: Databento candles")
        self._set_preview_text(self.format_technical_analysis_report(report))
    except Exception as exc:
        messagebox.showerror("Technical analysis failed", str(exc))


def _has_databento_technical_candles(external_intelligence: Any | None) -> bool:
    candles_by_timeframe = getattr(external_intelligence, "databento_technical_candles", None)
    if not isinstance(candles_by_timeframe, dict):
        return False
    for candles in candles_by_timeframe.values():
        try:
            if len(candles or ()) > 0:
                return True
        except TypeError:
            continue
    return False
