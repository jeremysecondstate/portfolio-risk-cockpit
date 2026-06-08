from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class OperatorActionLine:
    label: str
    action: str
    detail: str
    severity: str = "info"  # good, mixed, bad, info


@dataclass(frozen=True)
class OperatorVerdict:
    symbol: str
    primary_action: str
    primary_label: str
    confidence: str
    confidence_score: float
    summary: str
    right_now: OperatorActionLine
    if_confirms: OperatorActionLine
    if_breaks_down: OperatorActionLine
    best_hedge: OperatorActionLine
    preferred_vehicle: OperatorActionLine
    worst_tempting_trade: OperatorActionLine
    size_guidance: OperatorActionLine
    confirmation: OperatorActionLine
    invalidation: OperatorActionLine
    reasons: tuple[str, ...]
    dumb_reasons: tuple[str, ...]
    what_would_change: tuple[str, ...]
    warnings: tuple[str, ...]


def build_operator_verdict(
    *,
    symbol: str | None = None,
    recommendation_read: Any | None = None,
    empirical_intelligence: Any | None = None,
    command_center_report: Any | None = None,
    portfolio_context: Any | None = None,
    stock_plan: Any | None = None,
    option_candidates: Sequence[Any] | Any | None = None,
    selected_option_candidate: Any | None = None,
) -> OperatorVerdict:
    """Build a private action-plan readout from already-loaded research inputs.

    This helper is intentionally pure. It only reads supplied objects and never
    fetches data, submits orders, mutates broker state, or changes ticket fields.
    """

    clean_symbol = _clean_symbol(
        symbol
        or _get(recommendation_read, "symbol")
        or _get(command_center_report, "symbol")
        or _get(portfolio_context, "symbol")
        or "UNKNOWN"
    )
    empirical = empirical_intelligence or _get(recommendation_read, "empirical_intelligence")
    candidates = _normalise_candidates(option_candidates)
    selected = selected_option_candidate or _best_option_candidate(candidates)
    context = _position_context(portfolio_context)
    score = _score_context(recommendation_read, empirical)
    mismatch = _option_share_mismatch(context, selected, candidates)
    catalyst = _catalyst_context(empirical)
    has_confirmation = _has_real_trigger(_trigger_lines(recommendation_read, "confirmation_lines"), "confirmation line unavailable")
    has_invalidation = _has_real_trigger(_trigger_lines(recommendation_read, "invalidation_lines"), "invalidation line unavailable")
    posture = _right_now_posture(
        recommendation_read=recommendation_read,
        held=context["held"],
        score=score,
        catalyst=catalyst,
        mismatch=mismatch,
        has_confirmation=has_confirmation,
        has_invalidation=has_invalidation,
    )

    confirmation_line = _first_trigger(
        _trigger_lines(recommendation_read, "confirmation_lines"),
        "Confirmation line unavailable; wait for a cleaner trigger before upgrading the read.",
    )
    invalidation_line = _first_trigger(
        _trigger_lines(recommendation_read, "invalidation_lines"),
        "Invalidation line unavailable; define the risk line before sizing.",
    )
    right_now = OperatorActionLine(
        label="Right Now",
        action=posture["action"],
        detail=posture["detail"],
        severity=posture["severity"],
    )
    if_confirms = _if_confirms_line(posture["action"], confirmation_line, score)
    if_breaks = _if_breaks_down_line(context["held"], invalidation_line)
    best_hedge = _best_hedge_line(context, candidates, selected, score, mismatch)
    preferred_vehicle = _preferred_vehicle_line(posture["action"], context["held"], best_hedge, mismatch)
    worst_trade = _worst_tempting_trade_line(
        context=context,
        candidates=candidates,
        selected=selected,
        mismatch=mismatch,
        catalyst=catalyst,
        recommendation_read=recommendation_read,
        empirical=empirical,
    )
    size_guidance = _size_guidance_line(
        posture["action"],
        context=context,
        stock_plan=stock_plan,
        recommendation_read=recommendation_read,
        mismatch=mismatch,
    )
    confirmation = OperatorActionLine("Confirmation", _confirmation_action(posture["action"]), confirmation_line, if_confirms.severity)
    invalidation = OperatorActionLine("Invalidation", "RESPECT THE RISK LINE", invalidation_line, "bad" if has_invalidation else "mixed")

    reasons = _reasons(recommendation_read, empirical, score)
    dumb_reasons = _dumb_reasons(
        mismatch=mismatch,
        catalyst=catalyst,
        recommendation_read=recommendation_read,
        empirical=empirical,
        candidates=candidates,
        context=context,
    )
    changes = _what_would_change(recommendation_read, confirmation_line, invalidation_line)
    warnings = _warnings(recommendation_read, empirical, mismatch, catalyst, candidates, context)
    summary = _summary(clean_symbol, posture["action"], score, context["held"], catalyst, mismatch)

    return OperatorVerdict(
        symbol=clean_symbol,
        primary_action=posture["action"],
        primary_label=posture["label"],
        confidence=score["confidence"],
        confidence_score=score["confidence_score"],
        summary=summary,
        right_now=right_now,
        if_confirms=if_confirms,
        if_breaks_down=if_breaks,
        best_hedge=best_hedge,
        preferred_vehicle=preferred_vehicle,
        worst_tempting_trade=worst_trade,
        size_guidance=size_guidance,
        confirmation=confirmation,
        invalidation=invalidation,
        reasons=tuple(reasons),
        dumb_reasons=tuple(dumb_reasons),
        what_would_change=tuple(changes),
        warnings=tuple(warnings),
    )


def _right_now_posture(
    *,
    recommendation_read: Any | None,
    held: bool,
    score: dict[str, Any],
    catalyst: dict[str, Any],
    mismatch: dict[str, Any],
    has_confirmation: bool,
    has_invalidation: bool,
) -> dict[str, str]:
    if recommendation_read is None:
        return {
            "label": "Safe fallback",
            "action": "WAIT / NO TRADE",
            "severity": "info",
            "detail": "No recommendation-engine read is attached, so the private plan stays defensive until evidence is loaded.",
        }

    label_text = str(_get(recommendation_read, "recommendation_label", "") or "").lower()
    evidence = score["evidence_score"]
    adjusted = score["adjusted_score"]
    confidence_score = score["confidence_score"]
    catalyst_score = catalyst["score"]
    mismatch_active = bool(mismatch.get("active"))

    negative = evidence <= 40 or adjusted <= 42 or "avoid" in label_text or "reduce" in label_text
    defensive = evidence <= 48 or adjusted <= 48 or "defensive" in label_text
    positive = evidence >= 62 and adjusted >= 58 and not negative
    strong = evidence >= 72 and adjusted >= 66 and confidence_score >= 60
    low_confidence = confidence_score < 55 or adjusted < 60
    high_catalyst = catalyst_score >= 70
    elevated_catalyst = catalyst_score >= 45

    if mismatch_active:
        if held:
            return {
                "label": "Sizing mismatch",
                "action": "HOLD / NO NEW RISK",
                "severity": "mixed",
                "detail": "Option/share sizing is not clean; prefer shares, trim, or wait over a mismatched option contract.",
            }
        return {
            "label": "Sizing mismatch",
            "action": "WAIT / NO TRADE",
            "severity": "mixed",
            "detail": "The best loaded option idea is oversized versus the position context, so do not add option risk now.",
        }

    if negative and held:
        return {
            "label": "Negative while held",
            "action": "REDUCE OR HEDGE",
            "severity": "bad",
            "detail": "Evidence is negative while shares are held; reduce exposure or use a clean hedge only if sizing fits.",
        }
    if negative and not held:
        return {
            "label": "Negative and not held",
            "action": "AVOID / NO TRADE",
            "severity": "bad",
            "detail": "Evidence is negative and there is no existing position to protect, so the action plan is no new trade.",
        }
    if high_catalyst and held:
        return {
            "label": "Catalyst collision",
            "action": "HOLD / NO NEW RISK",
            "severity": "mixed",
            "detail": f"{catalyst['label']} keeps the plan defensive; do not add before the event risk clears.",
        }
    if high_catalyst and not held:
        return {
            "label": "Catalyst collision",
            "action": "AVOID / NO TRADE",
            "severity": "bad",
            "detail": f"{catalyst['label']} makes a new position unattractive until the event risk is resolved.",
        }
    if strong and has_confirmation and not elevated_catalyst and not low_confidence:
        return {
            "label": "Confirmed constructive",
            "action": "ALLOW SMALL ADD",
            "severity": "good",
            "detail": "Evidence is strong enough for a small add only against the defined trigger and risk line.",
        }
    if positive:
        return {
            "label": "Positive but unconfirmed",
            "action": "WAIT FOR CONFIRMATION",
            "severity": "mixed",
            "detail": "Evidence leans constructive, but confirmation, confidence, or catalyst checks are not clean enough for an add.",
        }
    if held and defensive and has_invalidation:
        return {
            "label": "Breakdown risk while held",
            "action": "HOLD, HEDGE OR REDUCE IF BREAKS",
            "severity": "mixed",
            "detail": "Current exposure can be held, but the risk line decides whether to hedge or reduce.",
        }
    if held:
        return {
            "label": "Mixed while held",
            "action": "HOLD / NO NEW RISK",
            "severity": "mixed",
            "detail": "Evidence is mixed while shares are held; do not add until the trigger and volume confirm.",
        }
    return {
        "label": "Unconfirmed watchlist",
        "action": "WAIT / NO TRADE",
        "severity": "mixed",
        "detail": "The setup is not negative enough to reject outright, but it is not confirmed enough for new risk.",
    }


def _if_confirms_line(action: str, confirmation_line: str, score: dict[str, Any]) -> OperatorActionLine:
    if "ALLOW SMALL ADD" in action:
        detail = f"Small add is allowed only while this remains true: {confirmation_line}"
        return OperatorActionLine("If Confirms", "SMALL ADD ONLY ABOVE TRIGGER", detail, "good")
    if action in {"AVOID / NO TRADE", "WAIT / NO TRADE"}:
        detail = f"Upgrade only after the read repairs and this trigger confirms: {confirmation_line}"
        return OperatorActionLine("If Confirms", "WAIT FOR REPAIR", detail, "mixed")
    if score["adjusted_score"] < 60:
        detail = f"Confirmation is necessary but not sufficient because confidence-adjusted evidence is only {score['adjusted_score']:.0f}/100. {confirmation_line}"
        return OperatorActionLine("If Confirms", "WAIT FOR BETTER PROOF", detail, "mixed")
    return OperatorActionLine("If Confirms", "SMALL ADD ALLOWED ONLY ON CONFIRMATION", confirmation_line, "mixed")


def _if_breaks_down_line(held: bool, invalidation_line: str) -> OperatorActionLine:
    action = "REDUCE / HEDGE BELOW RISK LINE" if held else "AVOID BELOW RISK LINE"
    detail = f"{'Reduce exposure or hedge' if held else 'Keep it off the ticket'} if the breakdown line triggers: {invalidation_line}"
    return OperatorActionLine("If Breaks Down", action, detail, "bad")


def _best_hedge_line(
    context: dict[str, Any],
    candidates: list[Any],
    selected: Any | None,
    score: dict[str, Any],
    mismatch: dict[str, Any],
) -> OperatorActionLine:
    if not context["held"]:
        return OperatorActionLine("Best Hedge", "None", "No hedge is needed because there is no current share position to protect.", "info")
    if mismatch.get("active"):
        return OperatorActionLine("Best Hedge", "Trim exposure", mismatch["detail"] + " A trim or wait is cleaner than an oversized option hedge.", "bad")

    protective = _best_strategy_candidate(candidates, ("protective put", "long put", "hedge"))
    collar = _best_strategy_candidate(candidates, ("collar",))
    covered = _best_strategy_candidate(candidates, ("covered call",))
    if collar is not None and _candidate_controls_cleanly(context, collar):
        return OperatorActionLine("Best Hedge", "Collar", _candidate_detail(collar, "Collar candidate is sized cleanly enough to consider as a hedge."), "mixed")
    if protective is not None and _candidate_controls_cleanly(context, protective):
        return OperatorActionLine("Best Hedge", "Protective put", _candidate_detail(protective, "Protective put candidate is sized cleanly enough to consider as a hedge."), "mixed")
    if covered is not None and _candidate_controls_cleanly(context, covered) and 45 <= score["adjusted_score"] <= 64:
        return OperatorActionLine("Best Hedge", "Covered call", _candidate_detail(covered, "Sideways/mixed setup can make a covered call acceptable if assignment/capped-upside risk is understood."), "mixed")
    return OperatorActionLine("Best Hedge", "No clean hedge", "No loaded option hedge fits cleanly; trim exposure or wait is cleaner.", "info")


def _preferred_vehicle_line(action: str, held: bool, best_hedge: OperatorActionLine, mismatch: dict[str, Any]) -> OperatorActionLine:
    if mismatch.get("active"):
        return OperatorActionLine("Preferred Vehicle", "Shares / wait", "Avoid the mismatched option. If risk is upgraded later, use share-sized exposure first.", "mixed")
    if action == "ALLOW SMALL ADD":
        return OperatorActionLine("Preferred Vehicle", "Shares", "Use share-sized exposure against the trigger; do not auto-fill or submit any order.", "good")
    if action in {"REDUCE OR HEDGE", "HOLD, HEDGE OR REDUCE IF BREAKS"} and best_hedge.action in {"Protective put", "Collar", "Covered call"}:
        return OperatorActionLine("Preferred Vehicle", "Option", f"Only the hedge lane is acceptable here: {best_hedge.action}.", "mixed")
    if action in {"AVOID / NO TRADE", "WAIT / NO TRADE"}:
        return OperatorActionLine("Preferred Vehicle", "No trade", "No new vehicle is preferred until evidence changes.", "bad" if action == "AVOID / NO TRADE" else "mixed")
    if held:
        return OperatorActionLine("Preferred Vehicle", "Wait", "Hold the existing shares only; new risk waits for confirmation.", "mixed")
    return OperatorActionLine("Preferred Vehicle", "Wait", "The setup needs confirmation before choosing shares or options.", "mixed")


def _worst_tempting_trade_line(
    *,
    context: dict[str, Any],
    candidates: list[Any],
    selected: Any | None,
    mismatch: dict[str, Any],
    catalyst: dict[str, Any],
    recommendation_read: Any | None,
    empirical: Any | None,
) -> OperatorActionLine:
    covered_short = _covered_call_shortfall(context, candidates)
    if covered_short:
        return OperatorActionLine("Worst Tempting Trade", "Do not sell uncovered calls", covered_short, "bad")
    if mismatch.get("active"):
        return OperatorActionLine("Worst Tempting Trade", "Avoid oversized option", mismatch["detail"], "bad")
    if _has_supply_chase_risk(recommendation_read, empirical):
        return OperatorActionLine("Worst Tempting Trade", "Do not chase supply", "Chasing a breakout into filing-derived supply without absorption confirmation.", "bad")
    if catalyst["score"] >= 45:
        return OperatorActionLine("Worst Tempting Trade", "Do not front-run catalyst", "Adding exposure before earnings/catalyst risk is known.", "bad")
    if _data_confidence_low(recommendation_read):
        return OperatorActionLine("Worst Tempting Trade", "Do not ignore data gaps", "Ignoring stale or missing data-confidence gaps.", "bad")
    if _get(empirical, "setup_replay") is not None:
        return OperatorActionLine("Worst Tempting Trade", "Do not overfit replay", "Treating historical replay as a prediction instead of context.", "mixed")
    if selected is not None and not _is_wait_candidate(selected):
        return OperatorActionLine("Worst Tempting Trade", "Do not force the option", "Paying option premium before the trigger has proved the setup.", "mixed")
    return OperatorActionLine("Worst Tempting Trade", "Do not invent a trade", "Forcing action when the readout says wait.", "mixed")


def _size_guidance_line(
    action: str,
    *,
    context: dict[str, Any],
    stock_plan: Any | None,
    recommendation_read: Any | None,
    mismatch: dict[str, Any],
) -> OperatorActionLine:
    if mismatch.get("active"):
        return OperatorActionLine("Size Guidance", "No option add", mismatch["detail"], "bad")
    plan_quantity = _to_float(_get(stock_plan, "quantity"))
    plan_notional = _to_float(_get(stock_plan, "notional"))
    if plan_quantity is not None and plan_quantity > 0:
        detail = f"Model stock plan is {plan_quantity:g} share(s), notional {_money(plan_notional)}; this is planning context only."
        severity = "good" if action == "ALLOW SMALL ADD" else "mixed"
        return OperatorActionLine("Size Guidance", "Use model share size only", detail, severity)
    sizing_notes = _clean_lines(_list(_get(recommendation_read, "position_sizing_notes")), limit=2)
    if sizing_notes:
        return OperatorActionLine("Size Guidance", "Planning only", " ".join(sizing_notes), "info")
    if context["held"]:
        return OperatorActionLine("Size Guidance", "No add", "Existing exposure is the size until a risk line and trigger are defined.", "mixed")
    return OperatorActionLine("Size Guidance", "Starter only after trigger", "No model stock size is loaded; do not size from impulse.", "mixed")


def _confirmation_action(action: str) -> str:
    if action == "ALLOW SMALL ADD":
        return "HOLD ABOVE TRIGGER"
    if action in {"AVOID / NO TRADE", "WAIT / NO TRADE"}:
        return "NEEDED BEFORE ANY TRADE"
    return "NEEDED BEFORE NEW RISK"


def _reasons(recommendation_read: Any | None, empirical: Any | None, score: dict[str, Any]) -> list[str]:
    lines = [
        f"Recommendation score: evidence {score['evidence_score']:.0f}/100, confidence-adjusted {score['adjusted_score']:.0f}/100.",
    ]
    lines.extend(_list(_get(recommendation_read, "why"))[:5])
    if empirical is not None:
        lines.append(
            f"Empirical controls: raw {_number(_get(empirical, 'raw_evidence_score'))}/100, adjusted {_number(_get(empirical, 'confidence_adjusted_score'))}/100."
        )
        catalyst = _get(empirical, "catalyst_collision")
        if catalyst is not None:
            lines.append(str(_get(catalyst, "summary", "") or "Catalyst collision read is loaded."))
    return _dedupe(_clean_lines(lines, limit=8)) or ["No loaded reasons; fallback verdict is defensive."]


def _dumb_reasons(
    *,
    mismatch: dict[str, Any],
    catalyst: dict[str, Any],
    recommendation_read: Any | None,
    empirical: Any | None,
    candidates: list[Any],
    context: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    if mismatch.get("active"):
        lines.append(mismatch["detail"])
    covered_short = _covered_call_shortfall(context, candidates)
    if covered_short:
        lines.append(covered_short)
    if catalyst["score"] >= 45:
        lines.append("Adding exposure before earnings/catalyst risk is known.")
    if _has_supply_chase_risk(recommendation_read, empirical):
        lines.append("Chasing a breakout into filing-derived supply without absorption confirmation.")
    if _data_confidence_low(recommendation_read):
        lines.append("Ignoring stale or missing data-confidence gaps.")
    warnings = _clean_lines([*_list(_get(recommendation_read, "warnings")), *_list(_get(empirical, "warnings"))], limit=3)
    lines.extend(warnings)
    if not lines:
        lines.append("Treating historical replay or scenario math as a prediction instead of context.")
    return _dedupe(_clean_lines(lines, limit=8))


def _what_would_change(recommendation_read: Any | None, confirmation_line: str, invalidation_line: str) -> list[str]:
    lines = _clean_lines(_list(_get(recommendation_read, "what_would_change")), limit=6)
    lines.extend([confirmation_line, invalidation_line])
    return _dedupe(_clean_lines(lines, limit=8)) or ["Fresh source coverage and cleaner price confirmation would sharpen the verdict."]


def _warnings(
    recommendation_read: Any | None,
    empirical: Any | None,
    mismatch: dict[str, Any],
    catalyst: dict[str, Any],
    candidates: list[Any],
    context: dict[str, Any],
) -> list[str]:
    lines = [
        *_list(_get(recommendation_read, "warnings")),
        *_list(_get(empirical, "warnings")),
    ]
    if recommendation_read is None:
        lines.append("Missing recommendation read; fallback verdict is wait/no trade.")
    if mismatch.get("active"):
        lines.append(mismatch["detail"])
    covered_short = _covered_call_shortfall(context, candidates)
    if covered_short:
        lines.append(covered_short)
    if catalyst["score"] >= 70:
        lines.append(f"{catalyst['label']} pushes the plan toward wait/avoid.")
    lines.append("Operator Verdict is planning context only; it does not submit orders, mutate broker state, fetch data, or change ticket fields.")
    return _dedupe(_clean_lines(lines, limit=10))


def _summary(symbol: str, action: str, score: dict[str, Any], held: bool, catalyst: dict[str, Any], mismatch: dict[str, Any]) -> str:
    pieces = [
        f"{symbol}: {action}.",
        f"Evidence {score['evidence_score']:.0f}/100; adjusted {score['adjusted_score']:.0f}/100; confidence {score['confidence']}.",
    ]
    pieces.append("Held position." if held else "Not currently held.")
    if catalyst["score"] >= 45:
        pieces.append(f"{catalyst['label']} keeps sizing conservative.")
    if mismatch.get("active"):
        pieces.append("Option/share sizing mismatch is the main self-critique.")
    return " ".join(pieces)


def _score_context(recommendation_read: Any | None, empirical: Any | None) -> dict[str, Any]:
    evidence = _to_float(_get(recommendation_read, "evidence_score"))
    adjusted = _to_float(_get(recommendation_read, "confidence_adjusted_score"))
    if adjusted is None:
        adjusted = _to_float(_get(empirical, "confidence_adjusted_score"))
    if evidence is None:
        evidence = 50.0
    if adjusted is None:
        adjusted = evidence
    confidence = str(_get(recommendation_read, "confidence", "Low") or "Low")
    confidence_score = _to_float(_get(recommendation_read, "confidence_score"))
    if confidence_score is None:
        confidence_score = 25.0 if recommendation_read is None else 50.0
    return {
        "evidence_score": _clamp(evidence, 0.0, 100.0),
        "adjusted_score": _clamp(adjusted, 0.0, 100.0),
        "confidence": confidence,
        "confidence_score": round(_clamp(confidence_score, 0.0, 100.0), 2),
    }


def _catalyst_context(empirical: Any | None) -> dict[str, Any]:
    catalyst = _get(empirical, "catalyst_collision")
    score = _to_float(_get(catalyst, "score")) or 0.0
    return {
        "score": _clamp(score, 0.0, 100.0),
        "label": str(_get(catalyst, "label", "Low collision") or "Low collision"),
        "summary": str(_get(catalyst, "summary", "") or ""),
    }


def _position_context(portfolio_context: Any | None) -> dict[str, Any]:
    quantity = _to_float(_get(portfolio_context, "quantity")) or 0.0
    held = bool(_get(portfolio_context, "is_held")) or quantity > 0
    return {
        "held": held,
        "quantity": max(quantity, 0.0),
        "weight": _to_float(_get(portfolio_context, "portfolio_weight")) or 0.0,
        "market_value": _to_float(_get(portfolio_context, "market_value")) or 0.0,
        "last_price": _to_float(_get(portfolio_context, "last_price")),
    }


def _option_share_mismatch(context: dict[str, Any], selected: Any | None, candidates: list[Any]) -> dict[str, Any]:
    contract = selected if _is_option_contract(selected) else _best_contract_candidate(candidates)
    if contract is None:
        return {"active": False, "detail": ""}
    controlled = _controlled_shares(contract)
    quantity = context["quantity"]
    if context["held"] and quantity > 0 and controlled >= 100 and controlled > quantity * 1.5:
        detail = f"One option contract controls {controlled:g} shares while the current position is only {quantity:g} shares."
        return {"active": True, "detail": detail, "controlled": controlled, "quantity": quantity}
    return {"active": False, "detail": "", "controlled": controlled, "quantity": quantity}


def _covered_call_shortfall(context: dict[str, Any], candidates: list[Any]) -> str:
    covered = _best_strategy_candidate(candidates, ("covered call",))
    if covered is None:
        return ""
    controlled = _controlled_shares(covered)
    quantity = context["quantity"]
    if quantity < controlled:
        return f"Using a covered-call idea without enough shares to cover the contract: {quantity:g} shares versus {controlled:g} controlled shares."
    return ""


def _best_strategy_candidate(candidates: list[Any], terms: tuple[str, ...]) -> Any | None:
    matches = []
    for candidate in candidates:
        text = _candidate_text(candidate)
        if any(term in text for term in terms):
            matches.append(candidate)
    return _best_option_candidate(matches)


def _best_contract_candidate(candidates: list[Any]) -> Any | None:
    contracts = [candidate for candidate in candidates if _is_option_contract(candidate)]
    return _best_option_candidate(contracts)


def _candidate_controls_cleanly(context: dict[str, Any], candidate: Any | None) -> bool:
    if candidate is None:
        return False
    controlled = _controlled_shares(candidate)
    return context["quantity"] >= controlled >= 100


def _candidate_detail(candidate: Any, fallback: str) -> str:
    parts = [
        str(_get(candidate, "strategy", "") or _get(candidate, "group", "") or "").strip(),
        str(_get(candidate, "expiration", "") or "").strip(),
        f"strike {_money(_get(candidate, 'strike'))}" if _get(candidate, "strike") is not None else "",
        str(_get(candidate, "why", "") or _get(candidate, "score_reason", "") or "").strip(),
    ]
    text = "; ".join(part for part in parts if part)
    return text or fallback


def _candidate_text(candidate: Any | None) -> str:
    return " ".join(
        str(_get(candidate, key, "") or "").lower()
        for key in ("strategy", "group", "option_type", "why", "relation_to_position")
    )


def _is_option_contract(candidate: Any | None) -> bool:
    return str(_get(candidate, "option_type", "") or "").lower() in {"call", "put"}


def _is_wait_candidate(candidate: Any | None) -> bool:
    text = _candidate_text(candidate)
    return "wait" in text or "no trade" in text or not _is_option_contract(candidate)


def _controlled_shares(candidate: Any | None) -> float:
    controlled = _to_float(_get(candidate, "controlled_shares"))
    if controlled is not None and controlled > 0:
        return controlled
    contracts = _to_float(_get(candidate, "contract_count"))
    if contracts is not None and contracts > 0:
        return contracts * 100.0
    return 100.0


def _has_supply_chase_risk(recommendation_read: Any | None, empirical: Any | None) -> bool:
    text = " ".join(
        [
            str(_get(recommendation_read, "recommendation_label", "")),
            " ".join(str(line) for line in _list(_get(recommendation_read, "warnings"))),
            " ".join(str(line) for line in _list(_get(recommendation_read, "what_would_change"))),
            " ".join(str(line) for line in _list(_get(empirical, "warnings"))),
        ]
    ).lower()
    supply = _get(empirical, "supply_absorption")
    supply_read = str(_get(supply, "read", "") or "").lower()
    return any(term in text for term in ("supply", "chase", "rally-fade", "rally fade", "offering")) or supply_read in {"watch", "rejection", "below_level"}


def _data_confidence_low(recommendation_read: Any | None) -> bool:
    data = _get(recommendation_read, "data_confidence")
    score = _to_float(_get(data, "score"))
    return score is not None and score < 60


def _trigger_lines(recommendation_read: Any | None, field: str) -> list[str]:
    return _clean_lines(_list(_get(recommendation_read, field)), limit=8)


def _first_trigger(lines: list[str], fallback: str) -> str:
    return lines[0] if lines else fallback


def _has_real_trigger(lines: list[str], fallback_marker: str) -> bool:
    if not lines:
        return False
    text = " ".join(lines).lower()
    return fallback_marker not in text and "unavailable" not in text


def _normalise_candidates(value: Sequence[Any] | Any | None) -> list[Any]:
    if value is None:
        return []
    if hasattr(value, "candidates"):
        return list(getattr(value, "candidates") or [])
    if isinstance(value, (str, bytes, dict)):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _best_option_candidate(candidates: Sequence[Any]) -> Any | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: _to_float(_get(item, "score")) or 0.0, reverse=True)[0]


def _get(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _clean_lines(values: Iterable[Any], *, limit: int) -> list[str]:
    lines: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        if not text:
            continue
        if len(text) > 360:
            text = text[:357].rstrip() + "..."
        lines.append(text)
        if len(lines) >= limit:
            break
    return lines


def _dedupe(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").replace("%", "")
        number = float(value)
        if number != number:
            return None
        return number
    except (TypeError, ValueError):
        return None


def _clean_symbol(symbol: Any) -> str:
    return str(symbol or "UNKNOWN").strip().upper() or "UNKNOWN"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _money(value: Any) -> str:
    number = _to_float(value)
    return "--" if number is None else f"${number:,.2f}"


def _number(value: Any) -> str:
    number = _to_float(value)
    return "--" if number is None else f"{number:.0f}"
