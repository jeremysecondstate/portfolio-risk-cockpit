from __future__ import annotations

import tkinter as tk
from typing import Type

from app.analytics import thesis_option_ticket, trade_thesis
from app.analytics.trade_thesis import OptionChainCandidate
from app.ui import unified_trade_thesis_next_checks_extension as next_checks

_installed = False


def install_options_candidate_actionability_extension(app_cls: Type[tk.Tk]) -> None:
    """Prefer actionable option candidates over no-bid/0DTE lottery quotes.

    This keeps the thesis/ticket aligned while avoiding the misleading "free spread"
    effect that can happen when the long leg is offered at $0.01 and the short leg has
    a $0.00 bid but a $0.01 mark.
    """

    global _installed
    if _installed:
        return

    trade_thesis._candidate_score = _candidate_score  # type: ignore[attr-defined]
    trade_thesis._spread_line = _spread_line  # type: ignore[attr-defined]
    thesis_option_ticket._long_leg_score = _ticket_long_leg_score  # type: ignore[attr-defined]
    trade_thesis.OptionChainCandidate.bid_or_mark = property(_conservative_bid_or_mark)  # type: ignore[attr-defined]
    thesis_option_ticket.OptionChainCandidate.bid_or_mark = property(_conservative_bid_or_mark)  # type: ignore[attr-defined]
    next_checks._liquidity_and_spread_lines = _liquidity_and_spread_lines  # type: ignore[attr-defined]

    _installed = True


def _candidate_score(candidate: OptionChainCandidate, target: float | None) -> tuple[float, float, float, int]:
    target_distance = abs(candidate.strike - target) if target is not None else 0.0
    spread_pct = _spread_pct(candidate)
    return (_actionability_penalty(candidate), target_distance, spread_pct if spread_pct is not None else 9999.0, -(candidate.volume or 0))


def _ticket_long_leg_score(candidate: OptionChainCandidate, target: float, spot_price: float) -> tuple[float, float, float, int, float]:
    spread_pct = _spread_pct(candidate)
    return (
        _actionability_penalty(candidate),
        abs(candidate.strike - target),
        spread_pct if spread_pct is not None else 9999.0,
        -(candidate.volume or 0),
        abs(candidate.strike - spot_price),
    )


def _spread_line(label: str, long_leg: OptionChainCandidate, short_leg: OptionChainCandidate) -> str:
    long_debit = _ask_or_mark(long_leg)
    short_credit = _conservative_bid_or_mark(short_leg)
    net_debit = max(long_debit - short_credit, 0.0)
    width = abs(short_leg.strike - long_leg.strike)
    max_reward = max(width - net_debit, 0.0)
    breakeven = _vertical_spread_breakeven(long_leg, net_debit)
    max_loss_dollars = net_debit * 100
    max_reward_dollars = max_reward * 100
    note = ""
    if _is_no_bid(short_leg):
        note = " Uses the short-leg bid, not mark, because the short leg is no-bid."
    return (
        f"- {label}: buy {long_leg.strike:g}, sell {short_leg.strike:g} {short_leg.side.upper()} same expiry if liquid; "
        f"rough conservative net debit ${net_debit:,.2f}, width ${width:,.2f}, breakeven {_format_optional_money(breakeven)}, "
        f"max loss about ${max_loss_dollars:,.0f}/contract, max reward before fees/slippage about ${max_reward_dollars:,.0f}/contract."
        f"{note}\n"
        f"  Expiration scenarios: {_spread_loss_zone(long_leg, short_leg)}; "
        f"breakeven at {_format_optional_money(breakeven)}; "
        f"max reward at/through {_format_money(short_leg.strike)}."
    )


def _liquidity_and_spread_lines(option_context, ticket):
    if option_context is None or not option_context.has_rows:
        return ["- Spread / liquidity check: skipped because no option-chain rows were loaded."]
    if ticket is None:
        return ["- Spread / liquidity check: no thesis option ticket was built from the loaded chain."]

    legs = next_checks._find_ticket_legs(option_context, ticket)
    lines = ["- Spread / liquidity check:"]
    leg_values = [leg for leg in (legs.long_leg, legs.short_leg if ticket.strategy == "Vertical Debit Spread" else None) if leg is not None]

    if legs.long_leg is not None:
        lines.append(f"  Long leg: {next_checks._leg_liquidity_text(legs.long_leg)}")
    else:
        lines.append("  Long leg: not found in loaded option-chain rows.")
    if ticket.strategy == "Vertical Debit Spread":
        if legs.short_leg is not None:
            lines.append(f"  Short leg: {next_checks._leg_liquidity_text(legs.short_leg)}")
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
        warnings = _actionability_warnings(leg_values)
        if max_reward <= 0:
            lines.append("  Result: FAIL — this spread has no modeled reward after the loaded debit/credit, so do not treat it as an attractive options structure.")
        elif warnings:
            lines.append("  Result: REVIEW/AVOID AS LIVE TRADE — " + " ".join(warnings))
        elif reward_risk < 0.50:
            lines.append("  Result: CAUTION — reward/risk is thin; the directional thesis may be fine, but this exact spread is not compelling without a very high conviction move.")
        else:
            lines.append("  Result: PASS/REVIEW — defined risk is clear; still confirm live quotes, open interest, and slippage before sizing.")
    else:
        lines.append(f"  Long-option economics: max loss about ${max_loss:,.0f}/contract; upside/downside reward remains path-dependent.")
        warnings = _actionability_warnings(leg_values)
        if warnings:
            lines.append("  Result: REVIEW/AVOID AS LIVE TRADE — " + " ".join(warnings))

    if not next_checks._open_interest_available(option_context):
        lines.append("  Open interest: not present in the current chain row model, so this run used bid/ask spread and volume. Add OI to the chain payload if the Schwab response includes it.")
    return lines


def _actionability_penalty(candidate: OptionChainCandidate) -> float:
    penalty = 0.0
    if candidate.dte == 0:
        penalty += 20.0
    if _is_no_bid(candidate):
        penalty += 40.0
    if candidate.ask is None or candidate.ask <= 0:
        penalty += 50.0
    if _ask_or_mark(candidate) <= 0.05:
        penalty += 10.0
    spread_pct = _spread_pct(candidate)
    if spread_pct is None:
        penalty += 10.0
    elif spread_pct > 0.50:
        penalty += 30.0
    elif spread_pct > 0.25:
        penalty += 10.0
    return penalty


def _actionability_warnings(legs: list[OptionChainCandidate]) -> list[str]:
    warnings: list[str] = []
    if any(leg.dte == 0 for leg in legs):
        warnings.append("0DTE options can decay or reprice extremely fast.")
    if any(_is_no_bid(leg) for leg in legs):
        warnings.append("One or more legs is no-bid, so the apparent cheap/free spread may not be realistically fillable.")
    if any((_spread_pct(leg) or 0.0) > 0.50 for leg in legs):
        warnings.append("One or more legs has a bid/ask spread greater than 50% of mid, which makes mark-based math unreliable.")
    if any(_ask_or_mark(leg) <= 0.05 for leg in legs):
        warnings.append("Penny-option pricing makes reward/risk ratios look artificially huge; confirm live executable quotes before trusting the thesis ticket.")
    return warnings


def _conservative_bid_or_mark(candidate: OptionChainCandidate) -> float:
    # Selling an option can only rely on bid for conservative planning. If Schwab does
    # not provide a bid at all, fall back to mark; if bid is explicitly 0, keep 0.
    if candidate.bid is not None:
        return max(candidate.bid, 0.0)
    return candidate.mark or 0.0


def _ask_or_mark(candidate: OptionChainCandidate) -> float:
    return candidate.ask if candidate.ask is not None and candidate.ask > 0 else candidate.mark or 0.0


def _is_no_bid(candidate: OptionChainCandidate) -> bool:
    return candidate.bid is not None and candidate.bid <= 0


def _spread_pct(candidate: OptionChainCandidate) -> float | None:
    if candidate.bid is None or candidate.ask is None or candidate.ask <= 0:
        return None
    spread = max(candidate.ask - candidate.bid, 0.0)
    mid = (candidate.ask + candidate.bid) / 2
    if mid <= 0:
        return None
    return spread / mid


def _vertical_spread_breakeven(long_leg: OptionChainCandidate, net_debit: float) -> float | None:
    if long_leg.side == "call":
        return long_leg.strike + net_debit
    if long_leg.side == "put":
        return long_leg.strike - net_debit
    return None


def _spread_loss_zone(long_leg: OptionChainCandidate, short_leg: OptionChainCandidate) -> str:
    if long_leg.side == "call":
        return f"below {_format_money(long_leg.strike)} = max loss"
    if long_leg.side == "put":
        return f"above {_format_money(long_leg.strike)} = max loss"
    return "outside long strike = max loss"


def _format_money(value: float) -> str:
    return f"${value:,.2f}"


def _format_optional_money(value: float | None) -> str:
    return "--" if value is None else _format_money(value)
