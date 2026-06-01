from __future__ import annotations

from dataclasses import dataclass
import tkinter as tk
from tkinter import messagebox

from app.core.portfolio import Portfolio, Position
from app.ui import hyperliquid_perp_ticket_use_mid_fix as base_what_if

GOLDILOCKS_CASH_BUFFER_PCT = 0.10
GOLDILOCKS_SCAN_STEP_PCT = 5
ZERO_EPSILON = 0.00000001


@dataclass(frozen=True)
class GoldilocksCashBudget:
    quote_asset: str
    available_cash: float
    source_label: str
    is_fallback: bool = False
    cash_buffer_pct: float = GOLDILOCKS_CASH_BUFFER_PCT

    @property
    def cash_buffer(self) -> float:
        return max(self.available_cash, 0.0) * self.cash_buffer_pct

    @property
    def max_spot_add_budget(self) -> float:
        return max(self.available_cash - self.cash_buffer, 0.0)


@dataclass(frozen=True)
class GoldilocksCandidate:
    hedge_ratio: float
    target_spot_qty: float
    spot_adjustment_qty: float
    spot_adjustment_value: float
    net_qty: float
    net_long_pct_of_spot: float
    combined_tp_pnl: float
    combined_sl_pnl: float
    score: float
    reason: str
    affordable: bool


def install_hyperliquid_existing_perp_what_if_extension(app_cls: type[tk.Tk]) -> None:
    """Include already-open perp exposure in dedicated Hyperliquid what-if output."""

    app_cls.run_hyperliquid_perp_what_if = _run_hyperliquid_perp_what_if_with_open_position  # type: ignore[attr-defined]


def _run_hyperliquid_perp_what_if_with_open_position(self: tk.Tk) -> None:
    try:
        base_what_if._ensure_perp_vars(self)
        ticket = self.parse_hyperliquid_ticket()
        leverage = base_what_if._optional_float(getattr(self, "hyperliquid_leverage_var").get(), default=1.0) or 1.0
        fee_rate = base_what_if._optional_float(getattr(self, "hyperliquid_fee_rate_var").get(), default=0.045) or 0.045
        tp_price = base_what_if._optional_float(getattr(self, "hyperliquid_target_price_var").get(), default=None)
        sl_price = base_what_if._optional_float(getattr(self, "stop_price_var").get(), default=None)
        is_long = ticket.is_buy
        if tp_price is None:
            tp_price = ticket.limit_price * (1.05 if is_long else 0.95)
            self.hyperliquid_target_price_var.set(base_what_if._format_price(tp_price))
        if sl_price is None:
            sl_price = ticket.limit_price * (0.97 if is_long else 1.03)
            self.stop_price_var.set(base_what_if._format_price(sl_price))

        proposed_signed_qty = ticket.size if ticket.is_buy else -ticket.size
        existing_perps = _existing_perp_positions_for_coin(self, ticket.coin)
        existing_signed_qty = sum(signed_qty for _position, signed_qty in existing_perps)

        tp_case = base_what_if._perp_case(ticket.limit_price, tp_price, ticket.size, is_long, leverage, fee_rate)
        sl_case = base_what_if._perp_case(ticket.limit_price, sl_price, ticket.size, is_long, leverage, fee_rate)
        tp_readout = base_what_if._tpsl_scenario_readout("TP", ticket.limit_price, tp_price, is_long)
        sl_readout = base_what_if._tpsl_scenario_readout("SL", ticket.limit_price, sl_price, is_long)
        spot_position = base_what_if._spot_position_for_coin(self, ticket.coin)
        tp_spot_lines = _spot_and_existing_perp_scenario_lines(
            "TP field",
            ticket.coin,
            tp_price,
            spot_position,
            existing_perps,
            tp_case["net_pnl"],
        )
        sl_spot_lines = _spot_and_existing_perp_scenario_lines(
            "SL field",
            ticket.coin,
            sl_price,
            spot_position,
            existing_perps,
            sl_case["net_pnl"],
        )

        notional = ticket.limit_price * ticket.size
        margin = notional / leverage if leverage > 0 else notional
        collateral = base_what_if._planning_collateral_usdc(self, margin)
        liquidation_lines = base_what_if._liquidation_readout_lines(ticket.limit_price, ticket.size, is_long, leverage, collateral)
        rr = base_what_if._risk_reward(tp_case["net_pnl"], sl_case["net_pnl"])
        direction = "LONG" if is_long else "SHORT"
        attach = "on" if self.hyperliquid_attach_tpsl_var.get() else "off"
        tpsl_warning_lines = base_what_if._tpsl_warning_lines(tp_readout, sl_readout)
        hedge_lines = _spot_hedge_lines_with_open_perp(
            self,
            ticket.coin,
            proposed_signed_qty,
            existing_signed_qty,
            ticket.limit_price,
        )
        goldilocks_lines = _goldilocks_balance_lines(
            self,
            ticket.coin,
            spot_position,
            existing_perps,
            proposed_signed_qty,
            ticket.limit_price,
            tp_price,
            sl_price,
            tp_case["net_pnl"],
            sl_case["net_pnl"],
            leverage,
        )

        self.hyperliquid_status_var.set("Hyperliquid: what-if ready")
        self._set_preview_text(
            "HYPERLIQUID PERP WHAT-IF\n"
            "========================\n\n"
            f"Market: {ticket.coin}-PERP\n"
            f"Direction: {direction}\n"
            f"Size: {ticket.size:g} {ticket.coin}\n"
            f"Entry / Limit: ${ticket.limit_price:,.4f}\n"
            f"Leverage: {leverage:g}x\n"
            f"Attach TP/SL: {attach}\n"
            f"Order value: ${notional:,.2f}\n"
            f"Estimated margin required: ${margin:,.2f}\n"
            f"Collateral used for liq estimate: ${collateral:,.2f}\n"
            f"Fee estimate: {fee_rate:g}% per side\n"
            + "\n".join(liquidation_lines) + "\n"
            f"{base_what_if.LEVERAGE_PNL_EXPLANATION}\n\n"
            + "\n".join(hedge_lines) + "\n\n"
            + "\n".join(tpsl_warning_lines) + ("\n\n" if tpsl_warning_lines else "")
            + f"{tp_readout.label}\n"
            f"- TP Price: ${tp_price:,.4f}\n"
            f"- Proposed perp gross P&L: ${tp_case['gross_pnl']:+,.2f}\n"
            f"- Proposed perp estimated fees: ${tp_case['fees']:,.2f}\n"
            f"- Proposed perp net gain/loss: ${tp_case['net_pnl']:+,.2f}\n"
            f"- Proposed perp ROI on estimated margin: {tp_case['margin_roi_percent']:+.2f}%\n"
            + "\n".join(tp_spot_lines) + "\n\n"
            f"{sl_readout.label}\n"
            f"- SL Price: ${sl_price:,.4f}\n"
            f"- Proposed perp gross P&L: ${sl_case['gross_pnl']:+,.2f}\n"
            f"- Proposed perp estimated fees: ${sl_case['fees']:,.2f}\n"
            f"- Proposed perp net gain/loss: ${sl_case['net_pnl']:+,.2f}\n"
            f"- Proposed perp ROI on estimated margin: {sl_case['margin_roi_percent']:+.2f}%\n"
            + "\n".join(sl_spot_lines) + "\n\n"
            + "\n".join(goldilocks_lines) + "\n\n"
            "Setup quality\n"
            f"- Reward/risk using proposed perp net P&L: {rr}\n"
            "- Existing open perp scenario P&L uses the synced perp mark as the starting point, with avg-cost context when available.\n"
            "- Spot scenario P&L uses the synced spot mark as the starting point, plus avg-cost context when available.\n"
            "- TP/SL fields are scenario inputs unless Attach TP/SL is on and child-order execution is wired.\n"
            "- Liquidation is an estimate: Hyperliquid can also account for maintenance margin, funding, open orders, and account mode."
        )
    except Exception as exc:
        self.hyperliquid_status_var.set("Hyperliquid: what-if failed")
        messagebox.showerror("Hyperliquid perp what-if failed", str(exc))


def _spot_hedge_lines_with_open_perp(
    self: tk.Tk,
    coin: str,
    proposed_signed_qty: float,
    existing_signed_qty: float,
    entry: float,
) -> list[str]:
    spot = base_what_if._spot_position_for_coin(self, coin)
    if spot is None:
        return ["Spot + perp exposure context", f"- No synced {coin} spot position found in the cockpit portfolio."]

    spot_qty = spot.quantity
    spot_value = spot_qty * spot.last_price
    proposed_notional = abs(proposed_signed_qty) * entry
    total_perp_signed_qty = existing_signed_qty + proposed_signed_qty
    net_delta = spot_qty + total_perp_signed_qty
    net_label = _net_label(net_delta)
    existing_label = _signed_qty_label(existing_signed_qty, coin)
    proposed_label = _signed_qty_label(proposed_signed_qty, coin)
    total_perp_label = _signed_qty_label(total_perp_signed_qty, coin)
    hedge_ratio = (abs(min(total_perp_signed_qty, 0.0)) / spot_qty * 100.0) if spot_qty > 0 else 0.0

    if total_perp_signed_qty < 0:
        interpretation = "Total short perp exposure offsets your synced spot. A 100% ratio is roughly spot-neutral before fees/funding."
    elif total_perp_signed_qty > 0:
        interpretation = "Total long perp exposure adds to your synced spot exposure. It is not a hedge."
    else:
        interpretation = "No net perp exposure after this ticket; your directional exposure is the synced spot position."

    return [
        "Spot + perp exposure context",
        f"- Synced spot: {spot_qty:g} {coin} worth about ${spot_value:,.2f}",
        f"- Existing synced perp exposure: {existing_label}",
        f"- Proposed ticket delta: {proposed_label}",
        f"- Proposed ticket notional: ${proposed_notional:,.2f}",
        f"- Total perp exposure after ticket: {total_perp_label}",
        f"- Hedge ratio vs spot after ticket: {hedge_ratio:.1f}%",
        f"- Net directional size after open perp + ticket: {net_delta:+g} {coin} ({net_label})",
        f"- {interpretation}",
    ]


def _spot_and_existing_perp_scenario_lines(
    label: str,
    coin: str,
    scenario_price: float,
    spot: Position | None,
    existing_perps: list[tuple[Position, float]],
    proposed_perp_net_pnl: float,
) -> list[str]:
    lines: list[str] = []
    spot_move_pnl = 0.0
    existing_perp_move_pnl = 0.0

    if spot is None:
        lines.append(f"- Spot at {label}: no synced {coin} spot position found.")
    else:
        spot_qty = spot.quantity
        current_spot_value = spot_qty * spot.last_price
        scenario_spot_value = spot_qty * scenario_price
        spot_move_pnl = scenario_spot_value - current_spot_value
        spot_open_pnl = scenario_spot_value - spot.cost_basis
        lines.extend(
            [
                f"- Spot value at {label}: ${scenario_spot_value:,.2f}",
                f"- Spot P&L from synced mark: ${spot_move_pnl:+,.2f}",
                f"- Spot open P&L vs avg cost: ${spot_open_pnl:+,.2f}",
            ]
        )

    if not existing_perps:
        lines.append(f"- Existing perp at {label}: no synced open {coin} perp position found.")
    else:
        existing_perp_open_pnl = 0.0
        existing_perp_notional = 0.0
        for position, signed_qty in existing_perps:
            existing_perp_move_pnl += (scenario_price - position.last_price) * signed_qty
            existing_perp_open_pnl += (scenario_price - position.average_cost) * signed_qty
            existing_perp_notional += abs(signed_qty) * scenario_price
        lines.extend(
            [
                f"- Existing perp value at {label}: ${existing_perp_notional:,.2f}",
                f"- Existing perp P&L from synced mark: ${existing_perp_move_pnl:+,.2f}",
                f"- Existing perp open P&L vs avg cost: ${existing_perp_open_pnl:+,.2f}",
            ]
        )

    combined = spot_move_pnl + existing_perp_move_pnl + proposed_perp_net_pnl
    lines.append(f"- Combined spot + existing perp move + proposed perp net P&L: ${combined:+,.2f}")
    return lines


def _goldilocks_balance_lines(
    self: tk.Tk,
    coin: str,
    spot: Position | None,
    existing_perps: list[tuple[Position, float]],
    proposed_signed_qty: float,
    entry: float,
    tp_price: float,
    sl_price: float,
    proposed_tp_net_pnl: float,
    proposed_sl_net_pnl: float,
    leverage: float,
) -> list[str]:
    try:
        portfolio = self.broker.get_portfolio()
    except Exception:
        portfolio = Portfolio(cash=0.0)
    quote_asset = _selected_quote_asset(self)
    cash_budget = _quote_cash_budget(portfolio, quote_asset)
    current_spot_qty = float(getattr(spot, "quantity", 0.0) or 0.0) if spot is not None else 0.0
    reference_price = float(getattr(spot, "last_price", 0.0) or 0.0) if spot is not None else 0.0
    if reference_price <= 0:
        reference_price = entry
    total_perp_signed_qty = sum(signed_qty for _position, signed_qty in existing_perps) + proposed_signed_qty
    return _build_goldilocks_spot_perp_balance_lines(
        coin=coin,
        current_spot_qty=current_spot_qty,
        reference_price=reference_price,
        total_perp_signed_qty=total_perp_signed_qty,
        existing_perps=existing_perps,
        proposed_tp_net_pnl=proposed_tp_net_pnl,
        proposed_sl_net_pnl=proposed_sl_net_pnl,
        tp_price=tp_price,
        sl_price=sl_price,
        cash_budget=cash_budget,
        leverage=leverage,
    )


def _build_goldilocks_spot_perp_balance_lines(
    *,
    coin: str,
    current_spot_qty: float,
    reference_price: float,
    total_perp_signed_qty: float,
    existing_perps: list[tuple[Position, float]],
    proposed_tp_net_pnl: float,
    proposed_sl_net_pnl: float,
    tp_price: float,
    sl_price: float,
    cash_budget: GoldilocksCashBudget,
    leverage: float = 1.0,
) -> list[str]:
    lines = [
        "Goldilocks Spot / Perp Balance",
        f"- {_goldilocks_diagnosis(coin, current_spot_qty, total_perp_signed_qty)}",
        f"- Available {cash_budget.quote_asset} for spot add: {_money(cash_budget.available_cash)} ({cash_budget.source_label}). "
        f"Cash buffer kept: {_money(cash_budget.cash_buffer)}. Max add budget: {_money(cash_budget.max_spot_add_budget)}.",
    ]
    if cash_budget.is_fallback:
        lines.append("- Cash source note: exact Hyperliquid quote balance was unavailable, so this uses portfolio cash as a fallback.")

    if total_perp_signed_qty > ZERO_EPSILON:
        lines.extend(
            [
                f"- Spot and long perp stack in the same direction. This is amplified long exposure, not a hedge.",
                f"- Current spot: {_qty(current_spot_qty)} {coin}; total long perp after ticket: {_qty(total_perp_signed_qty)} {coin}.",
                f"- Practical adjustment: reduce long perp exposure if you want less leverage, or use a short perp ticket if you want downside protection. Do not add spot unless you intentionally want more unlevered long {coin}.",
            ]
        )
        return lines

    short_perp_abs = abs(min(total_perp_signed_qty, 0.0))
    if short_perp_abs <= ZERO_EPSILON:
        lines.extend(
            [
                "- No short perp remains after this ticket, so there is no protective spot/perp hedge ratio to optimize.",
                f"- If you want a hedge, use a short perp sized against synced spot rather than adding more spot.",
            ]
        )
        return lines

    if current_spot_qty <= ZERO_EPSILON:
        lines.extend(_no_spot_goldilocks_lines(coin, short_perp_abs, reference_price, cash_budget))
        return lines

    candidates = _generate_goldilocks_candidates(
        current_spot_qty=current_spot_qty,
        short_perp_abs=short_perp_abs,
        reference_price=reference_price,
        tp_price=tp_price,
        sl_price=sl_price,
        existing_perps=existing_perps,
        proposed_tp_net_pnl=proposed_tp_net_pnl,
        proposed_sl_net_pnl=proposed_sl_net_pnl,
        cash_budget=cash_budget,
        leverage=leverage,
    )
    recommendations = _select_goldilocks_recommendations(candidates)
    affordable_spot_adds = [candidate for candidate in candidates if candidate.spot_adjustment_qty > ZERO_EPSILON and candidate.affordable]
    unaffordable_ideal = _ideal_unaffordable_candidate(candidates, recommendations)

    if not affordable_spot_adds and any(candidate.spot_adjustment_qty > ZERO_EPSILON for candidate in candidates):
        lines.append("- No spot-add balance is affordable with current synced cash.")

    if recommendations:
        for label, candidate in recommendations:
            lines.extend(_format_goldilocks_recommendation(label, coin, candidate, short_perp_abs, cash_budget))
    else:
        lines.append("- No affordable Goldilocks spot-add candidate was found from this scan.")

    if unaffordable_ideal is not None:
        lines.append(
            "Ideal but not currently affordable: "
            f"target {unaffordable_ideal.hedge_ratio:.0%} hedge would require adding {_money(unaffordable_ideal.spot_adjustment_value)} spot, "
            f"but current available {cash_budget.quote_asset} is {_money(cash_budget.available_cash)}. Not actionable."
        )

    if not recommendations or not any(candidate.net_long_pct_of_spot > 0.10 for _label, candidate in recommendations):
        lines.extend(_cash_tight_alternatives(coin, current_spot_qty, short_perp_abs, reference_price))

    return lines


def _generate_goldilocks_candidates(
    *,
    current_spot_qty: float,
    short_perp_abs: float,
    reference_price: float,
    tp_price: float,
    sl_price: float,
    existing_perps: list[tuple[Position, float]],
    proposed_tp_net_pnl: float,
    proposed_sl_net_pnl: float,
    cash_budget: GoldilocksCashBudget,
    leverage: float = 1.0,
    step_pct: int = GOLDILOCKS_SCAN_STEP_PCT,
) -> list[GoldilocksCandidate]:
    if current_spot_qty <= ZERO_EPSILON or short_perp_abs <= ZERO_EPSILON or reference_price <= 0:
        return []

    current_hedge_ratio = short_perp_abs / current_spot_qty
    max_pct = 200 if current_hedge_ratio > 1.25 else 125
    existing_tp_move = _existing_perp_move_pnl(existing_perps, tp_price)
    existing_sl_move = _existing_perp_move_pnl(existing_perps, sl_price)
    candidates: list[GoldilocksCandidate] = []
    for hedge_pct in range(20, max_pct + 1, max(step_pct, 1)):
        hedge_ratio = hedge_pct / 100.0
        target_spot_qty = short_perp_abs / hedge_ratio
        spot_adjustment_qty = target_spot_qty - current_spot_qty
        spot_adjustment_value = spot_adjustment_qty * reference_price
        net_qty = target_spot_qty - short_perp_abs
        net_long_pct = net_qty / target_spot_qty if target_spot_qty > ZERO_EPSILON else 0.0
        spot_tp_pnl = target_spot_qty * (tp_price - reference_price)
        spot_sl_pnl = target_spot_qty * (sl_price - reference_price)
        combined_tp_pnl = spot_tp_pnl + existing_tp_move + proposed_tp_net_pnl
        combined_sl_pnl = spot_sl_pnl + existing_sl_move + proposed_sl_net_pnl
        affordable = spot_adjustment_qty <= ZERO_EPSILON or spot_adjustment_value <= cash_budget.max_spot_add_budget + 0.005
        score = _goldilocks_score(
            hedge_ratio=hedge_ratio,
            net_long_pct=net_long_pct,
            spot_adjustment_value=max(spot_adjustment_value, 0.0),
            combined_tp_pnl=combined_tp_pnl,
            combined_sl_pnl=combined_sl_pnl,
            short_perp_abs=short_perp_abs,
            reference_price=reference_price,
            cash_budget=cash_budget,
            leverage=leverage,
        )
        reason = _goldilocks_reason(hedge_ratio, net_long_pct, spot_adjustment_value, affordable)
        candidates.append(
            GoldilocksCandidate(
                hedge_ratio=hedge_ratio,
                target_spot_qty=target_spot_qty,
                spot_adjustment_qty=spot_adjustment_qty,
                spot_adjustment_value=spot_adjustment_value,
                net_qty=net_qty,
                net_long_pct_of_spot=net_long_pct,
                combined_tp_pnl=combined_tp_pnl,
                combined_sl_pnl=combined_sl_pnl,
                score=score,
                reason=reason,
                affordable=affordable,
            )
        )
    return candidates


def _goldilocks_score(
    *,
    hedge_ratio: float,
    net_long_pct: float,
    spot_adjustment_value: float,
    combined_tp_pnl: float,
    combined_sl_pnl: float,
    short_perp_abs: float,
    reference_price: float,
    cash_budget: GoldilocksCashBudget,
    leverage: float,
) -> float:
    score = 100.0

    if 0.25 <= net_long_pct <= 0.60:
        score += 18.0 - abs(net_long_pct - 0.425) * 20.0
    elif 0.10 <= net_long_pct <= 0.75:
        distance = min(abs(net_long_pct - 0.25), abs(net_long_pct - 0.60))
        score += 4.0 - distance * 35.0
    elif net_long_pct < 0:
        score -= 75.0 + min(abs(net_long_pct) * 80.0, 80.0)
    else:
        score -= 25.0 + min((net_long_pct - 0.75) * 50.0, 40.0)

    if 0.40 <= hedge_ratio <= 0.75:
        score += 12.0 - abs(hedge_ratio - 0.575) * 12.0
    elif 0.25 <= hedge_ratio <= 1.00:
        score -= min(abs(hedge_ratio - 0.575) * 25.0, 12.0)
    elif hedge_ratio > 1.25:
        score -= 35.0 + min((hedge_ratio - 1.25) * 35.0, 35.0)
    else:
        score -= 20.0

    if spot_adjustment_value > ZERO_EPSILON:
        if cash_budget.max_spot_add_budget <= ZERO_EPSILON:
            score -= 45.0
        else:
            budget_usage = spot_adjustment_value / cash_budget.max_spot_add_budget
            if budget_usage > 1.0:
                score -= min((budget_usage - 1.0) * 25.0 + 25.0, 90.0)
            elif budget_usage > 0.75:
                score -= (budget_usage - 0.75) * 30.0

    risk_notional = max(short_perp_abs * reference_price, 1.0)
    score += max(min((combined_sl_pnl / risk_notional) * 18.0, 18.0), -24.0)
    score += max(min((combined_tp_pnl / risk_notional) * 6.0, 8.0), -8.0)

    if leverage >= 25:
        score -= 4.0
    elif leverage >= 10:
        score -= 2.0
    return round(score, 4)


def _select_goldilocks_recommendations(candidates: list[GoldilocksCandidate]) -> list[tuple[str, GoldilocksCandidate]]:
    affordable = sorted([candidate for candidate in candidates if candidate.affordable], key=lambda candidate: candidate.score, reverse=True)
    if not affordable:
        return []

    long_biased = [candidate for candidate in affordable if candidate.net_long_pct_of_spot > 0.10 and candidate.hedge_ratio <= 1.0]
    best = long_biased[0] if long_biased else affordable[0]
    selected: list[tuple[str, GoldilocksCandidate]] = [("Best Balance", best)]

    defensive_pool = [candidate for candidate in affordable if candidate.hedge_ratio > best.hedge_ratio + ZERO_EPSILON]
    if defensive_pool:
        selected.append(("More Defensive", max(defensive_pool, key=lambda candidate: candidate.score)))

    bullish_pool = [candidate for candidate in affordable if candidate.hedge_ratio < best.hedge_ratio - ZERO_EPSILON]
    if bullish_pool:
        selected.append(("More Bullish", max(bullish_pool, key=lambda candidate: candidate.score)))

    used = {id(candidate) for _label, candidate in selected}
    fallback_labels = ["More Defensive", "More Bullish"]
    for candidate in affordable:
        if len(selected) >= 3:
            break
        if id(candidate) in used:
            continue
        label = fallback_labels[len(selected) - 1] if len(selected) <= 2 else f"Alternative {len(selected)}"
        if any(existing_label == label for existing_label, _candidate in selected):
            label = f"Alternative {len(selected)}"
        selected.append((label, candidate))
        used.add(id(candidate))

    return selected[:3]


def _ideal_unaffordable_candidate(
    candidates: list[GoldilocksCandidate],
    recommendations: list[tuple[str, GoldilocksCandidate]],
) -> GoldilocksCandidate | None:
    unaffordable = [candidate for candidate in candidates if not candidate.affordable and candidate.spot_adjustment_qty > ZERO_EPSILON]
    if not unaffordable:
        return None
    candidate = max(unaffordable, key=lambda row: row.score)
    if not recommendations:
        return candidate
    best_actionable_score = max(row.score for _label, row in recommendations)
    return candidate if candidate.score >= best_actionable_score + 3.0 else None


def _format_goldilocks_recommendation(
    label: str,
    coin: str,
    candidate: GoldilocksCandidate,
    short_perp_abs: float,
    cash_budget: GoldilocksCashBudget,
) -> list[str]:
    add_value = max(candidate.spot_adjustment_value, 0.0)
    remaining_cash = cash_budget.available_cash - add_value
    spot_action = _spot_adjustment_text(candidate.spot_adjustment_qty, coin, candidate.spot_adjustment_value)
    return [
        f"- {label}: target {candidate.hedge_ratio:.0%} hedge. {spot_action}. "
        f"Net {coin} becomes {_signed_qty(candidate.net_qty)} ({candidate.net_long_pct_of_spot:.0%} net long).",
        f"  - Expected combined P&L at TP: {_signed_money(candidate.combined_tp_pnl)}; at SL: {_signed_money(candidate.combined_sl_pnl)}.",
        f"  - Cash check: available {cash_budget.quote_asset} {_money(cash_budget.available_cash)}; buffer {_money(cash_budget.cash_buffer)}; "
        f"max add budget {_money(cash_budget.max_spot_add_budget)}; spot add cost {_money(add_value)}; remaining cash {_money(remaining_cash)}.",
        f"  - Perp action: keep total short perp at {_qty(short_perp_abs)} {coin} for this spot-balance candidate.",
        f"  - Score {candidate.score:.1f}: {candidate.reason}",
    ]


def _spot_adjustment_text(spot_adjustment_qty: float, coin: str, spot_adjustment_value: float) -> str:
    if spot_adjustment_qty > ZERO_EPSILON:
        return f"Add {_qty(spot_adjustment_qty)} {coin} spot (~{_money(spot_adjustment_value)})"
    if spot_adjustment_qty < -ZERO_EPSILON:
        return f"Reduce {_qty(abs(spot_adjustment_qty))} {coin} spot (~{_money(abs(spot_adjustment_value))})"
    return "Keep current spot size"


def _goldilocks_reason(hedge_ratio: float, net_long_pct: float, spot_adjustment_value: float, affordable: bool) -> str:
    if not affordable:
        return "good balance math, but the spot add exceeds the cash-aware budget."
    if hedge_ratio > 1.0:
        return "very defensive and still net short; use only if intentional."
    if 0.40 <= hedge_ratio <= 0.75 and 0.25 <= net_long_pct <= 0.60:
        return "keeps a long BTC bias while adding meaningful downside protection."
    if hedge_ratio < 0.40:
        return "more bullish; upside participation stays high, but downside protection is lighter."
    if net_long_pct < 0.10:
        return "close to neutral; more protective, but long exposure is mostly gone."
    if spot_adjustment_value <= ZERO_EPSILON:
        return "uses current spot or less, so it does not consume cash."
    return "cash-aware long-biased hedge balance from the scan."


def _goldilocks_diagnosis(coin: str, current_spot_qty: float, total_perp_signed_qty: float) -> str:
    if current_spot_qty <= ZERO_EPSILON:
        return f"No synced spot position exists, so spot/perp hedge balance cannot be calculated from current holdings."
    if total_perp_signed_qty > ZERO_EPSILON:
        return f"Current structure: stacked long. Spot and long perp both point long; this is not a hedge."
    short_perp_abs = abs(min(total_perp_signed_qty, 0.0))
    if short_perp_abs <= ZERO_EPSILON:
        return f"Current structure: spot-only long {coin}. There is no short perp hedge after this ticket."
    hedge_ratio = short_perp_abs / current_spot_qty
    net_qty = current_spot_qty - short_perp_abs
    if net_qty < -ZERO_EPSILON:
        return (
            f"Current structure: over-hedged. The total short perp is {hedge_ratio:.2f}x current spot, "
            f"so the combined book flips net short. That is protection only if you intentionally want to be short {coin}."
        )
    if hedge_ratio >= 0.75:
        return f"Current structure: near-neutral hedge. You remain slightly net long, but most spot upside is offset."
    if hedge_ratio >= 0.25:
        return f"Current structure: partial hedge. You remain net long, and the short perp offsets part of spot downside."
    return f"Current structure: light hedge. You are mostly net long, with only modest downside offset from the short perp."


def _no_spot_goldilocks_lines(
    coin: str,
    short_perp_abs: float,
    reference_price: float,
    cash_budget: GoldilocksCashBudget,
) -> list[str]:
    lines = [
        f"- No synced spot position exists, so spot/perp hedge balance cannot be calculated from current holdings.",
        f"- Total short perp after ticket: {_qty(short_perp_abs)} {coin}. Spot required to support that short as a hedge:",
    ]
    for hedge_ratio in (0.50, 0.75, 1.00):
        required_spot = short_perp_abs / hedge_ratio
        required_value = required_spot * reference_price
        affordable = required_value <= cash_budget.max_spot_add_budget + 0.005
        status = "affordable" if affordable else "not affordable"
        lines.append(f"  - {hedge_ratio:.0%} hedge: {_qty(required_spot)} {coin} spot (~{_money(required_value)}), {status} with current cash budget.")
    affordable_spot = cash_budget.max_spot_add_budget / reference_price if reference_price > 0 else 0.0
    coverage = affordable_spot / short_perp_abs if short_perp_abs > ZERO_EPSILON else 0.0
    lines.extend(
        [
            f"- Affordable spot support at max budget: {_qty(affordable_spot)} {coin}, covering {coverage:.0%} of the short perp.",
            "- Alternatives: reduce short perp size, use a smaller hedge ratio, wait/add cash, or manually buy spot later.",
        ]
    )
    return lines


def _cash_tight_alternatives(coin: str, current_spot_qty: float, short_perp_abs: float, reference_price: float) -> list[str]:
    reduce_for_75 = max(short_perp_abs - current_spot_qty * 0.75, 0.0)
    reduce_for_100 = max(short_perp_abs - current_spot_qty, 0.0)
    lines = ["- Cash-aware alternatives:"]
    if reduce_for_75 > ZERO_EPSILON:
        lines.append(f"  - Reduce total short perp by {_qty(reduce_for_75)} {coin} to reach about a 75% hedge using current spot only.")
    if reduce_for_100 > ZERO_EPSILON:
        lines.append(f"  - Reduce total short perp by {_qty(reduce_for_100)} {coin} to avoid being net short using current spot only.")
    lines.append("  - Wait/add cash or manually buy spot later before sizing a larger short hedge.")
    return lines


def _quote_cash_budget(portfolio: Portfolio, quote_asset: str) -> GoldilocksCashBudget:
    quote = (quote_asset or "USDC").strip().upper()
    hyperliquid_cash_seen = False
    exact_cash = 0.0
    for cash in getattr(portfolio, "cash_positions", {}).values():
        symbol = str(getattr(cash, "symbol", "")).upper()
        source = str(getattr(cash, "source", ""))
        source_upper = source.upper()
        if "HYPERLIQUID" in source_upper:
            hyperliquid_cash_seen = True
        if symbol == quote and "HYPERLIQUID" in source_upper and "PERP" not in source_upper:
            exact_cash += max(float(getattr(cash, "amount", 0.0) or 0.0), 0.0)

    if exact_cash > ZERO_EPSILON:
        return GoldilocksCashBudget(quote, exact_cash, "synced Hyperliquid quote balance", is_fallback=False)
    if hyperliquid_cash_seen:
        return GoldilocksCashBudget(quote, 0.0, "synced Hyperliquid quote balance not found", is_fallback=False)
    fallback_cash = max(float(getattr(portfolio, "cash", 0.0) or 0.0), 0.0)
    return GoldilocksCashBudget(quote, fallback_cash, "portfolio cash fallback", is_fallback=True)


def _selected_quote_asset(self: tk.Tk) -> str:
    for name in ("hyperliquid_spot_quote_asset_var", "hyperliquid_quote_asset_var"):
        var = getattr(self, name, None)
        try:
            value = str(var.get()).strip().upper()
        except Exception:
            value = ""
        if value in {"USDC", "USDH"}:
            return value
    return "USDC"


def _existing_perp_move_pnl(existing_perps: list[tuple[Position, float]], scenario_price: float) -> float:
    total = 0.0
    for position, signed_qty in existing_perps:
        total += (scenario_price - position.last_price) * signed_qty
    return total


def _existing_perp_positions_for_coin(self: tk.Tk, coin: str) -> list[tuple[Position, float]]:
    try:
        portfolio = self.broker.get_portfolio()
    except Exception:
        return []

    target_prefixes = (f"{coin}-PERP", f"HL:{coin}-PERP")
    matches: list[tuple[Position, float]] = []
    for raw_symbol, position in portfolio.positions.items():
        symbol = raw_symbol.upper()
        if not symbol.startswith(target_prefixes):
            continue
        signed_qty = _signed_perp_quantity(symbol, position.quantity)
        if abs(signed_qty) > 0.00000001:
            matches.append((position, signed_qty))
    return matches


def _signed_perp_quantity(symbol: str, quantity: float) -> float:
    if symbol.endswith("-PERP-SHORT"):
        return -abs(quantity)
    if symbol.endswith("-PERP"):
        return abs(quantity)
    return quantity


def _signed_qty_label(signed_qty: float, coin: str) -> str:
    if abs(signed_qty) <= 0.00000001:
        return f"0 {coin}"
    side = "long" if signed_qty > 0 else "short"
    return f"{signed_qty:+g} {coin} ({side})"


def _net_label(signed_qty: float) -> str:
    if signed_qty > 0.00000001:
        return "net long"
    if signed_qty < -0.00000001:
        return "net short"
    return "flat"


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _signed_money(value: float) -> str:
    return f"+${value:,.2f}" if value >= 0 else f"-${abs(value):,.2f}"


def _qty(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") or "0"


def _signed_qty(value: float) -> str:
    if abs(value) <= ZERO_EPSILON:
        return "0"
    sign = "+" if value > 0 else "-"
    return f"{sign}{_qty(abs(value))}"
