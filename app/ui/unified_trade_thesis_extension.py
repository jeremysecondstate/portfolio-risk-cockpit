from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Type

from app.analytics.earnings_release import analyze_earnings_release
from app.analytics.fundamental_analysis import analyze_company_facts
from app.analytics.technical_analysis import analyze_candles, candles_from_price_history, compare_timeframes
from app.analytics.trade_thesis import format_unified_trade_thesis, option_context_from_rows
from app.data.sec_edgar import SecEdgarClient, normalize_ticker

REPORT_FORMS = ("10-K", "10-Q", "8-K")


def install_unified_trade_thesis_extension(app_cls: Type[tk.Tk]) -> None:
    """Turn Tech Analysis into a full cross-source trade-thesis report."""

    app_cls.show_technical_analysis = _show_unified_trade_thesis  # type: ignore[method-assign]


def _show_unified_trade_thesis(self: tk.Tk) -> None:
    symbol = self.symbol_var.get().strip().upper()
    if not symbol:
        messagebox.showerror("Unified thesis failed", "Enter a symbol first.")
        return

    try:
        normalized_symbol = normalize_ticker(symbol)
        session = self._authorize_schwab_session()
        if session is None:
            return

        intraday_status_code, intraday_payload = session.get_price_history(
            normalized_symbol,
            period_type="day",
            period=10,
            frequency_type="minute",
            frequency=5,
            need_extended_hours_data=False,
        )
        if intraday_status_code != 200:
            raise RuntimeError(f"Schwab intraday price history returned HTTP {intraday_status_code}: {intraday_payload}")

        daily_status_code, daily_payload = session.get_price_history(
            normalized_symbol,
            period_type="year",
            period=1,
            frequency_type="daily",
            frequency=1,
            need_extended_hours_data=False,
        )
        if daily_status_code != 200:
            raise RuntimeError(f"Schwab daily price history returned HTTP {daily_status_code}: {daily_payload}")

        intraday_report = analyze_candles(normalized_symbol, candles_from_price_history(intraday_payload))
        daily_report = analyze_candles(normalized_symbol, candles_from_price_history(daily_payload))
        technical_report = compare_timeframes(normalized_symbol, intraday_report, daily_report)

        sec_client = SecEdgarClient()
        filings = sec_client.recent_filings(normalized_symbol, forms=REPORT_FORMS, limit=18)
        release_digest = analyze_earnings_release(sec_client.latest_earnings_release(normalized_symbol))

        fundamental_report = None
        try:
            company, payload = sec_client.get_companyfacts(normalized_symbol)
            fundamental_report = analyze_company_facts(company, payload)
        except Exception:
            fundamental_report = None

        option_context = option_context_from_rows(getattr(self, "schwab_option_chain_rows", None))
        report = format_unified_trade_thesis(
            symbol=normalized_symbol,
            technical_report=technical_report,
            fundamental_report=fundamental_report,
            filings=filings,
            release_digest=release_digest,
            option_context=option_context,
        )

        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(report)
    except Exception as exc:
        messagebox.showerror("Unified thesis failed", str(exc))
