from __future__ import annotations

import tkinter as tk
from typing import Any, Type

from app.analytics.trade_thesis import OptionChainCandidate, OptionChainContext, format_unified_trade_thesis

_installed = False
_original_format_unified_trade_thesis = format_unified_trade_thesis


def install_uncovered_options_risk_lane_extension(app_cls: Type[tk.Tk]) -> None:
    """Show uncovered/naked option alternatives as analysis lanes, not default tickets."""

    global _installed
    if _installed:
        return

    import app.analytics.trade_thesis as trade_thesis
    import app.ui.unified_trade_thesis_extension as unified_thesis
    import app.ui.unified_trade_thesis_next_checks_extension as next_checks

    trade_thesis.format_unified_trade_thesis = _format_unified_trade_thesis_with_uncovered_lane  # type: ignore[assignment]
    unified_thesis.format_unified_trade_thesis = _format_unified_trade_thesis_with_uncovered_lane  # type: ignore[attr-defined]
    next_checks.format_unified_trade_thesis = _format_unified_trade_thesis_with_uncovered_lane  # type: ignore[attr-defined]
    _installed = True


def _format_unified_trade_thesis_with_uncovered_lane(
    *,
    symbol: str,
    technical_report: Any,
    fundamental_report: Any | None,
    filings: list[Any],
    release_digest: Any | None,
    option_context: OptionChainContext | None,
) -> str:
    report = _original_format_unified_trade_thesis(
        symbol=symbol,
        technical_report=technical_report,
        fundamental_report=fundamental_report,
        filings=filings,
        release_digest=release_digest,
        option_context=option_context,
    )
    lane = _uncovered_risk_lane(symbol=symbol, latest=technical_report.daily.latest_close, option_context=option_context)
    if not lane:
        return report

    marker = "\nAutomated next-check results:"
    if marker in report:
        return report.replace(marker, f"\n{lane}\n{marker}", 1)

    marker = "\nSuggested next checks:"
    if marker in report:
        return report.replace(marker, f"\n{lane}\n{marker}", 1)

    return f"{report}\n\n{lane}"


def _uncovered_risk_lane(*, symbol: str, latest: float, option_context: OptionChainContext | None) -> str:
    if option_context is None or not option_context.has_rows:
        return ""

    calls = _rank_uncovered_candidates([candidate for candidate in option_context.candidates if candidate.side == "call"], latest=latest)
    puts = _rank_uncovered_candidates([candidate for candidate in option_context.candidates if candidate.side == "put"], latest=latest)
    call = calls[0] if calls else None
    put = puts[0] if puts else None
    if call is None and put is None:
        return ""

    lines = [
        "Uncovered options risk lane:",
        "- Purpose: show the naked-short avenue explicitly so it can be compared against defined-risk structures.",
        "- This lane is analysis-only in the thesis report. It is not used as the default filled ticket.",
    ]
    if call is not None:
        lines.extend(_short_call_lines(symbol, latest, call))
    if put is not None:
        lines.extend(_short_put_lines(symbol, latest, put))
    lines.extend(
        [
            "- Cockpit interpretation: uncovered options can look attractive because premium is collected upfront, but the tail risk is asymmetric.",
            "- Required before any future live-ticket support: explicit account approval, margin model, assignment model, gap/volatility stress, and separate user-selected strategy mode.",
        ]
    )
    return "\n".join(lines)


def _rank_uncovered_candidates(candidates: list[OptionChainCandidate], *, latest: float) -> list[OptionChainCandidate]:
    usable = [candidate for candidate in candidates if _bid_or_mark(candidate) > 0 and candidate.strike > 0]
    return sorted(
        usable,
        key=lambda candidate: (
            _dte_penalty(candidate),
            abs(candidate.strike - latest),
            _spread_pct(candidate) if _spread_pct(candidate) is not None else 9999.0,
            -(candidate.volume or 0),
        ),
    )


def _short_call_lines(symbol: str, latest: float, call: OptionChainCandidate) -> list[str]:
    credit = _bid_or_mark(call)
    breakeven = call.strike + credit
    shares = 100
    stress_moves = (0.10, 0.25, 0.50, 1.00)
    lines = [
        f"- Naked short call example: sell {call.expiration} {call.strike:g} CALL for roughly ${credit:,.2f} credit.",
        f"  Premium collected: ${credit * shares:,.0f}/contract; breakeven: ${breakeven:,.2f}.",
        "  Max profit: premium collected if the call expires worthless.",
        "  Max loss: undefined / theoretically unlimited because the underlying can keep rising.",
        "  Upside stress from latest close:",
    ]
    for move in stress_moves:
        price = latest * (1 + move)
        pnl = credit * shares - max(price - call.strike, 0.0) * shares
        lines.append(f"    {move:+.0%} to ${price:,.2f}: estimated expiration P/L { _money(pnl) }.")
    return lines


def _short_put_lines(symbol: str, latest: float, put: OptionChainCandidate) -> list[str]:
    credit = _bid_or_mark(put)
    breakeven = put.strike - credit
    shares = 100
    max_loss = max(put.strike - credit, 0.0) * shares
    stress_moves = (-0.10, -0.25, -0.50)
    lines = [
        f"- Naked short put example: sell {put.expiration} {put.strike:g} PUT for roughly ${credit:,.2f} credit.",
        f"  Premium collected: ${credit * shares:,.0f}/contract; breakeven: ${breakeven:,.2f}.",
        "  Max profit: premium collected if the put expires worthless.",
        f"  Max loss if {symbol} went to zero: about {_money(max_loss)}/contract before fees/assignment effects.",
        "  Downside stress from latest close:",
    ]
    for move in stress_moves:
        price = latest * (1 + move)
        pnl = credit * shares - max(put.strike - price, 0.0) * shares
        lines.append(f"    {move:+.0%} to ${price:,.2f}: estimated expiration P/L { _money(pnl) }.")
    zero_pnl = credit * shares - put.strike * shares
    lines.append(f"    -100% to $0.00: estimated expiration P/L { _money(zero_pnl) }.")
    return lines


def _dte_penalty(candidate: OptionChainCandidate) -> int:
    if candidate.dte is None:
        return 10
    if candidate.dte <= 0:
        return 30
    if candidate.dte < 7:
        return 10
    return 0


def _bid_or_mark(candidate: OptionChainCandidate) -> float:
    if candidate.bid is not None and candidate.bid > 0:
        return candidate.bid
    return candidate.mark or 0.0


def _spread_pct(candidate: OptionChainCandidate) -> float | None:
    if candidate.bid is None or candidate.ask is None or candidate.ask <= 0:
        return None
    mid = (candidate.bid + candidate.ask) / 2
    if mid <= 0:
        return None
    return max(candidate.ask - candidate.bid, 0.0) / mid


def _money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"
