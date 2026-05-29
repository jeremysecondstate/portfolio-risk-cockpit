from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import messagebox
from typing import Type

from app.analytics.earnings_release import analyze_earnings_release
from app.analytics.fundamental_analysis import analyze_company_facts, FundamentalReport
from app.analytics.technical_analysis import analyze_candles, candles_from_price_history, compare_timeframes
from app.analytics.thesis_option_ticket import ThesisOptionTicket, build_thesis_option_ticket
from app.analytics.trade_thesis import OptionChainCandidate, OptionChainContext, format_unified_trade_thesis, option_context_from_rows
from app.data.sec_edgar import SecEdgarClient, SecFiling, normalize_ticker
from app.macro.releases import build_macro_report

REPORT_FORMS = ("10-K", "10-Q", "8-K")


@dataclass(frozen=True)
class _LegPair:
    long_leg: OptionChainCandidate | None
    short_leg: OptionChainCandidate | None


def install_unified_trade_thesis_next_checks_extension(app_cls: Type[tk.Tk]) -> None:
    """Make Tech Analysis execute and fold the old suggested checks into the thesis."""

    app_cls.show_technical_analysis = _show_unified_trade_thesis_with_next_checks  # type: ignore[method-assign]
    app_cls.refresh_macro_data = _refresh_macro_data  # type: ignore[attr-defined]


def _refresh_macro_data(self: tk.Tk) -> None:
    self._set_preview_text(_macro_report_or_error(force_refresh=True))
    if hasattr(self, "schwab_preview_status_var"):
        self.schwab_preview_status_var.set("Last Schwab preview: macro refreshed")


def _show_unified_trade_thesis_with_next_checks(self: tk.Tk) -> None:
    symbol = self.symbol_var.get().strip().upper()
    if not symbol:
        self._set_preview_text(_macro_report_or_error())
        if hasattr(self, "schwab_preview_status_var"):
            self.schwab_preview_status_var.set("Last Schwab preview: macro only")
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
        directional_line = report.split("Overall directional read:", 1)[1].splitlines()[0].strip().lower() if "Overall directional read:" in report else "mixed"
        ticket = build_thesis_option_ticket(
            symbol=normalized_symbol,
            directional_bias=directional_line,
            option_context=option_context,
            spot_price=technical_report.daily.latest_close,
        )
        self.current_thesis_option_ticket = ticket

        portfolio_value = _current_portfolio_value(self)
        report = _replace_suggested_next_checks(
            report,
            option_context=option_context,
            ticket=ticket,
            release_digest_available=release_digest is not None,
            fundamental_report=fundamental_report,
            filings=filings,
            portfolio_value=portfolio_value,
        )

        self.schwab_status_var.set("Schwab session: connected")
        self._set_preview_text(_append_macro_report(report))
    except Exception as exc:
        self.current_thesis_option_ticket = None
        try:
            self._set_preview_text(
                "Symbol / technical readout unavailable\n"
                f"- {exc}\n\n"
                f"{_macro_report_or_error()}"
            )
        except Exception:
            messagebox.showerror("Unified thesis failed", str(exc))


def _append_macro_report(symbol_report: str) -> str:
    return f"{symbol_report.rstrip()}\n\n{_macro_report_or_error()}"


def _macro_report_or_error(*, force_refresh: bool = False) -> str:
    try:
        return build_macro_report(force_refresh=force_refresh)
    except Exception as exc:
        return (
            "Official Macro Snapshot\n"
            f"Fetched: unavailable\n\n"
            "Macro source status:\n"
            f"- unavailable/error: {exc}\n"
            "- Symbol-level technical analysis, if available above, remains valid independently of this macro fetch."
        )


def _replace_suggested_next_checks(
    report: str,
    *,
    option_context: OptionChainContext | None,
    ticket: ThesisOptionTicket | None,
    release_digest_available: bool,
    fundamental_report: FundamentalReport | None,
    filings: list[SecFiling],
    portfolio_value: float | None,
) -> str:
    replacement = "\n".join(
        [
            "Automated next-check results:",
            *_option_chain_freshness_lines(option_context),
            *_liquidity_and_spread_lines(option_context, ticket),
            *_filing_reconciliation_lines(release_digest_available, fundamental_report, filings),
            *_position_sizing_lines(ticket, portfolio_value),
            "",
            "Options thesis after checks:",
            *_options_thesis_after_checks(option_context, ticket, portfolio_value),
        ]
    )

    marker = "Suggested next checks:"
    if marker not in report:
        return f"{report}\n\n{replacement}"
    prefix = report.split(marker, 1)[0].rstrip()
    return f"{prefix}\n\n{replacement}"


def _option_chain_freshness_lines(option_context: OptionChainContext | None) -> list[str]:
    if option_context is None or not option_context.has_rows:
        return ["- Option-chain refresh check: no loaded option chain was available, so contract-specific checks could not run."]
    dtes = sorted({candidate.dte for candidate in option_context.candidates if candidate.dte is not None})
    dte_text = f"DTEs loaded: {', '.join(str(dte) for dte in dtes[:8])}" if dtes else "DTE unavailable in loaded rows"
    return [
        f"- Option-chain refresh check: {len(option_context.candidates)} loaded contracts for {option_context.underlying or 'selected symbol'} are present in the current cockpit snapshot; {dte_text}.",
        "  Refresh again before live planning because bid/ask, volume, and DTE can change quickly intraday.",
    ]


def _liquidity_and_spread_lines(option_context: OptionChainContext | None, ticket: ThesisOptionTicket | None) -> list[str]:
    if option_context is None or not option_context.has_rows:
        return ["- Spread / liquidity check: skipped because no option-chain rows were loaded."]
    if ticket is None:
        return ["- Spread / liquidity check: no thesis option ticket was built from the loaded chain."]

    legs = _find_ticket_legs(option_context, ticket)
    lines = ["- Spread / liquidity check:"]
    if legs.long_leg is not None:
        lines.append(f"  Long leg: {_leg_liquidity_text(legs.long_leg)}")
    else:
        lines.append("  Long leg: not found in loaded option-chain rows.")
    if ticket.strategy == "Vertical Debit Spread":
        if legs.short_leg is not None:
            lines.append(f"  Short leg: {_leg_liquidity_text(legs.short_leg)}")
        else:
            lines.append("  Short leg: not found in loaded option-chain rows.")

    width = abs(ticket.short_strike - ticket.strike)
    max_loss = ticket.max_loss
    max_reward = ticket.max_reward
    if ticket.strategy == "Vertical Debit Spread" and max_reward is not None:
        reward_risk = max_reward / max(max_loss, 0.01)
        lines.append(
            f"  Spread economics: width ${width:,.2f}, net debit ${ticket.premium:,.2f}, max loss about ${max_loss:,.0f}/contract, "
            f"max reward about ${max_reward:,.0f}/contract, reward/risk {reward_risk:.2f}x."
        )
        if max_reward <= 0:
            lines.append("  Result: FAIL — this spread has no modeled reward after the loaded debit/credit, so do not treat it as an attractive options structure.")
        elif reward_risk < 0.50:
            lines.append("  Result: CAUTION — reward/risk is thin; the directional thesis may be fine, but this exact spread is not compelling without a very high conviction move.")
        else:
            lines.append("  Result: PASS/REVIEW — defined risk is clear; still confirm live quotes, open interest, and slippage before sizing.")
    else:
        lines.append(f"  Long-option economics: max loss about ${max_loss:,.0f}/contract; upside/downside reward remains path-dependent.")

    if not _open_interest_available(option_context):
        lines.append("  Open interest: not present in the current chain row model, so this run used bid/ask spread and volume. Add OI to the chain payload if the Schwab response includes it.")
    return lines


def _filing_reconciliation_lines(
    release_digest_available: bool,
    fundamental_report: FundamentalReport | None,
    filings: list[SecFiling],
) -> list[str]:
    latest_10q_10k = next((filing for filing in filings if filing.form in {"10-Q", "10-K"}), None)
    latest_8k = next((filing for filing in filings if filing.form == "8-K"), None)
    lines = ["- SEC / earnings reconciliation check:"]
    if release_digest_available:
        lines.append("  Fast 8-K earnings-release exhibit was parsed and folded into the directional read.")
    else:
        lines.append("  No recent 8-K earnings-release exhibit was found, so the report is not relying on a stale fast-release layer.")
    if fundamental_report is not None and latest_10q_10k is not None:
        lines.append(
            f"  Formal XBRL/companyfacts layer was parsed from SEC data; latest formal filing in this scan: {latest_10q_10k.form} filed {latest_10q_10k.filing_date or '--'} "
            f"for period {latest_10q_10k.report_date or '--'}."
        )
    elif latest_10q_10k is not None:
        lines.append(f"  Formal filing found ({latest_10q_10k.form} filed {latest_10q_10k.filing_date or '--'}), but XBRL/companyfacts parsing was unavailable.")
    else:
        lines.append("  No 10-Q/10-K appeared in the current filing scan window.")
    if latest_8k is not None:
        lines.append(f"  Latest 8-K in scan: filed {latest_8k.filing_date or '--'}; items {latest_8k.items or '--'}.")
    return lines


def _position_sizing_lines(ticket: ThesisOptionTicket | None, portfolio_value: float | None) -> list[str]:
    if ticket is None:
        return ["- Position-sizing check: skipped because no thesis option ticket was built."]
    if portfolio_value is None or portfolio_value <= 0:
        return [f"- Position-sizing check: ticket max loss is about ${ticket.max_loss:,.0f}/contract; portfolio value was unavailable for percentage sizing."]
    risk_pct = ticket.max_loss / portfolio_value
    status = "PASS" if risk_pct <= 0.02 else "CAUTION" if risk_pct <= 0.05 else "WARN"
    return [
        f"- Position-sizing check: {status} — ticket max loss is about ${ticket.max_loss:,.0f}/contract, or {risk_pct:.2%} of current portfolio value ${portfolio_value:,.2f}.",
        "  This keeps thesis quality separate from trade sizing; a good thesis can still be a bad trade if the debit, slippage, or concentration is too large.",
    ]


def _options_thesis_after_checks(option_context: OptionChainContext | None, ticket: ThesisOptionTicket | None, portfolio_value: float | None) -> list[str]:
    if ticket is None:
        return ["- No ticket was filled because the loaded chain did not produce a usable thesis contract."]
    risk_text = ""
    if portfolio_value and portfolio_value > 0:
        risk_text = f" ({ticket.max_loss / portfolio_value:.2%} of portfolio)"
    lines = [
        f"- Filled thesis ticket: {ticket.summary}",
        f"- Defined max loss / debit: about ${ticket.max_loss:,.0f}/contract{risk_text}.",
    ]
    if ticket.max_reward is not None:
        lines.append(f"- Modeled max reward: about ${ticket.max_reward:,.0f}/contract before fees/slippage.")
    if option_context is not None and option_context.has_rows:
        lines.append("- The options thesis is now based on the same loaded-chain contract set used to fill the ticket, so the planning layer and ticket should agree on expiration/strike.")
    return lines


def _find_ticket_legs(option_context: OptionChainContext, ticket: ThesisOptionTicket) -> _LegPair:
    side = ticket.option_type.lower()
    long_leg = _find_leg(option_context, side=side, expiration=ticket.expiration, strike=ticket.strike)
    short_leg = None
    if ticket.strategy == "Vertical Debit Spread":
        short_leg = _find_leg(option_context, side=side, expiration=ticket.expiration, strike=ticket.short_strike)
    return _LegPair(long_leg=long_leg, short_leg=short_leg)


def _find_leg(option_context: OptionChainContext, *, side: str, expiration: str, strike: float) -> OptionChainCandidate | None:
    for candidate in option_context.candidates:
        if candidate.side != side:
            continue
        if candidate.expiration != expiration:
            continue
        if abs(candidate.strike - strike) <= 0.0001:
            return candidate
    return None


def _leg_liquidity_text(candidate: OptionChainCandidate) -> str:
    spread = _spread(candidate)
    mid = _mid(candidate)
    spread_pct = spread / mid if mid else None
    spread_text = "--" if spread_pct is None else f"{spread_pct:.1%} of mid"
    verdict = "PASS" if spread_pct is not None and spread_pct <= 0.15 and (candidate.volume or 0) >= 100 else "REVIEW"
    return (
        f"{candidate.expiration} {candidate.strike:g} {candidate.side.upper()} bid/ask {_fmt(candidate.bid)}/{_fmt(candidate.ask)}, "
        f"spread ${spread:,.2f} ({spread_text}), volume {candidate.volume if candidate.volume is not None else '--'} => {verdict}."
    )


def _spread(candidate: OptionChainCandidate) -> float:
    if candidate.bid is None or candidate.ask is None:
        return 0.0
    return max(candidate.ask - candidate.bid, 0.0)


def _mid(candidate: OptionChainCandidate) -> float | None:
    if candidate.bid is not None and candidate.ask is not None and candidate.ask > 0:
        return (candidate.bid + candidate.ask) / 2
    return candidate.mark


def _fmt(value: float | None) -> str:
    return "--" if value is None else f"{value:,.2f}"


def _open_interest_available(option_context: OptionChainContext) -> bool:
    return any(hasattr(candidate, "open_interest") and getattr(candidate, "open_interest") is not None for candidate in option_context.candidates)


def _current_portfolio_value(self: tk.Tk) -> float | None:
    try:
        portfolio = self.broker.get_portfolio()
        return float(portfolio.total_value)
    except Exception:
        return None
