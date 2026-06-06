from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from app.analytics.research_scoring import score_macro_text


COMPONENT_ORDER: tuple[tuple[str, str], ...] = (
    ("chart_setup", "Chart setup"),
    ("prc_pressure_line", "PRC / pressure line"),
    ("volume_confirmation", "Volume confirmation"),
    ("relative_strength", "Relative strength"),
    ("capital_structure_supply", "Capital structure / supply"),
    ("macro_backdrop", "Macro backdrop"),
    ("option_fit", "Option fit"),
    ("portfolio_position_risk", "Portfolio concentration / position risk"),
    ("data_confidence", "Data confidence"),
)


@dataclass(frozen=True)
class EvidenceVote:
    score: float
    weight: float
    confidence: float
    reason: str


@dataclass(frozen=True)
class EvidenceComponent:
    key: str
    label: str
    vote: EvidenceVote
    status: str
    details: tuple[str, ...] = field(default_factory=tuple)
    missing: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DataSourceConfidence:
    source: str
    status: str
    score: float
    reason: str
    fetched_at: str | None = None
    age_hours: float | None = None
    weight: float = 1.0


@dataclass(frozen=True)
class DataConfidenceRead:
    grade: str
    score: float
    sources: tuple[DataSourceConfidence, ...]
    missing: tuple[str, ...]
    stale: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class ExpectedRewardRiskSummary:
    label: str
    reward_risk_ratio: float | None
    planning_probability: float | None
    expected_value_units: float | None
    reward_line: str
    risk_line: str
    summary: str


@dataclass(frozen=True)
class RecommendationEngineRead:
    symbol: str
    recommendation_label: str
    confidence: str
    confidence_score: float
    evidence_score: float
    evidence_vote: float
    components: tuple[EvidenceComponent, ...]
    data_confidence: DataConfidenceRead
    expected_reward_risk: ExpectedRewardRiskSummary
    invalidation_lines: tuple[str, ...]
    confirmation_lines: tuple[str, ...]
    position_sizing_notes: tuple[str, ...]
    what_would_change: tuple[str, ...]
    why: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def build_recommendation_engine_read(
    *,
    symbol: str | None = None,
    command_center_report: Any | None = None,
    capital_structure_indicator: Any | None = None,
    macro_read: Any | None = None,
    macro_text: str = "",
    fundamental_read: Any | None = None,
    fundamentals_text: str = "",
    option_candidates: Sequence[Any] | Any | None = None,
    option_chain_rows: Sequence[dict[str, Any]] | None = None,
    portfolio_context: Any | None = None,
    stock_plan: Any | None = None,
    source_statuses: Sequence[Any] | None = None,
    as_of: datetime | None = None,
) -> RecommendationEngineRead:
    """Build a transparent recommendation readout from already-loaded research layers.

    The helper is intentionally pure: it does not fetch, submit, mutate broker
    state, or assume every layer exists.
    """

    clean_symbol = _clean_symbol(symbol or _get(command_center_report, "symbol") or _get(portfolio_context, "symbol") or "")
    active_capital_indicator = capital_structure_indicator or _get(command_center_report, "capital_structure_indicator")
    data_confidence = build_data_confidence_read(
        command_center_report=command_center_report,
        capital_structure_indicator=active_capital_indicator,
        macro_read=macro_read,
        macro_text=macro_text,
        fundamental_read=fundamental_read,
        fundamentals_text=fundamentals_text,
        option_candidates=option_candidates,
        option_chain_rows=option_chain_rows,
        portfolio_context=portfolio_context,
        source_statuses=source_statuses,
        as_of=as_of,
    )
    components = build_evidence_components(
        command_center_report=command_center_report,
        capital_structure_indicator=active_capital_indicator,
        macro_read=macro_read,
        macro_text=macro_text,
        fundamental_read=fundamental_read,
        fundamentals_text=fundamentals_text,
        option_candidates=option_candidates,
        option_chain_rows=option_chain_rows,
        portfolio_context=portfolio_context,
        data_confidence=data_confidence,
    )
    evidence_score, evidence_vote = score_evidence_components(components)
    confidence, confidence_score = _recommendation_confidence(components, data_confidence, evidence_vote)
    label = _recommendation_label(evidence_score, data_confidence, components, command_center_report, active_capital_indicator)
    reward_risk = build_expected_reward_risk_summary(command_center_report, evidence_score=evidence_score)
    invalidation, confirmation = _trigger_lines(command_center_report)
    sizing_notes = _position_sizing_notes(
        portfolio_context=portfolio_context,
        command_center_report=command_center_report,
        option_candidates=option_candidates,
        stock_plan=stock_plan,
    )
    changes = _what_would_change(
        components=components,
        command_center_report=command_center_report,
        capital_structure_indicator=active_capital_indicator,
        data_confidence=data_confidence,
        macro_read=macro_read,
        fundamental_read=fundamental_read,
    )
    why = _why_lines(components)
    warnings = _dedupe(
        [
            *_list(_get(command_center_report, "warnings")),
            *_list(_get(active_capital_indicator, "warnings")),
            *data_confidence.missing[:3],
        ]
    )[:8]

    return RecommendationEngineRead(
        symbol=clean_symbol or "UNKNOWN",
        recommendation_label=label,
        confidence=confidence,
        confidence_score=confidence_score,
        evidence_score=evidence_score,
        evidence_vote=evidence_vote,
        components=components,
        data_confidence=data_confidence,
        expected_reward_risk=reward_risk,
        invalidation_lines=tuple(invalidation),
        confirmation_lines=tuple(confirmation),
        position_sizing_notes=tuple(sizing_notes),
        what_would_change=tuple(changes),
        why=tuple(why),
        warnings=tuple(warnings),
    )


def build_evidence_components(
    *,
    command_center_report: Any | None = None,
    capital_structure_indicator: Any | None = None,
    macro_read: Any | None = None,
    macro_text: str = "",
    fundamental_read: Any | None = None,
    fundamentals_text: str = "",
    option_candidates: Sequence[Any] | Any | None = None,
    option_chain_rows: Sequence[dict[str, Any]] | None = None,
    portfolio_context: Any | None = None,
    data_confidence: DataConfidenceRead | None = None,
) -> tuple[EvidenceComponent, ...]:
    active_data_confidence = data_confidence or build_data_confidence_read(
        command_center_report=command_center_report,
        capital_structure_indicator=capital_structure_indicator,
        macro_read=macro_read,
        macro_text=macro_text,
        fundamental_read=fundamental_read,
        fundamentals_text=fundamentals_text,
        option_candidates=option_candidates,
        option_chain_rows=option_chain_rows,
        portfolio_context=portfolio_context,
    )
    components = (
        _chart_setup_component(command_center_report),
        _prc_component(command_center_report),
        _volume_component(command_center_report),
        _relative_strength_component(command_center_report),
        _capital_structure_component(capital_structure_indicator),
        _macro_component(macro_read, macro_text, fundamental_read, fundamentals_text),
        _option_component(option_candidates, option_chain_rows),
        _portfolio_component(portfolio_context),
        _data_confidence_component(active_data_confidence),
    )
    return components


def score_evidence_components(components: Sequence[EvidenceComponent]) -> tuple[float, float]:
    total_weight = 0.0
    weighted_vote = 0.0
    for component in components:
        vote = component.vote
        effective_weight = max(0.0, vote.weight) * _clamp(vote.confidence, 0.0, 1.0)
        if effective_weight <= 0:
            continue
        weighted_vote += _clamp(vote.score, -100.0, 100.0) * effective_weight
        total_weight += effective_weight
    if total_weight <= 0:
        return 50.0, 0.0
    evidence_vote = _clamp(weighted_vote / total_weight, -100.0, 100.0)
    evidence_score = _clamp(50.0 + evidence_vote / 2.0, 0.0, 100.0)
    return round(evidence_score, 2), round(evidence_vote, 2)


def build_data_confidence_read(
    *,
    command_center_report: Any | None = None,
    capital_structure_indicator: Any | None = None,
    macro_read: Any | None = None,
    macro_text: str = "",
    fundamental_read: Any | None = None,
    fundamentals_text: str = "",
    option_candidates: Sequence[Any] | Any | None = None,
    option_chain_rows: Sequence[dict[str, Any]] | None = None,
    portfolio_context: Any | None = None,
    source_statuses: Sequence[Any] | None = None,
    as_of: datetime | None = None,
) -> DataConfidenceRead:
    now = as_of or datetime.now(timezone.utc)
    rows: list[DataSourceConfidence] = [
        _price_source_confidence(command_center_report),
        _benchmark_source_confidence(command_center_report),
        _prc_source_confidence(command_center_report),
        _capital_source_confidence(capital_structure_indicator),
        _macro_source_confidence(macro_read, macro_text),
        _fundamental_source_confidence(fundamental_read, fundamentals_text),
        _options_source_confidence(option_candidates, option_chain_rows),
        _portfolio_source_confidence(portfolio_context),
    ]
    rows.extend(_status_confidence_rows(source_statuses or (), now=now))
    weighted_total = sum(row.score * max(row.weight, 0.0) for row in rows)
    total_weight = sum(max(row.weight, 0.0) for row in rows)
    score = _clamp(weighted_total / total_weight if total_weight else 0.0, 0.0, 100.0)
    grade = _data_confidence_grade(score)
    missing = tuple(row.source for row in rows if row.score < 45 or row.status in {"missing", "error", "unavailable"})
    stale = tuple(row.source for row in rows if row.status in {"stale", "cached", "limited"} or (row.age_hours is not None and row.age_hours > 24))
    reason = f"{grade} data confidence from {len(rows)} source checks; {len(missing)} missing/error and {len(stale)} stale/cached/limited."
    return DataConfidenceRead(
        grade=grade,
        score=round(score, 2),
        sources=tuple(rows),
        missing=missing,
        stale=stale,
        reason=reason,
    )


def build_expected_reward_risk_summary(
    command_center_report: Any | None,
    *,
    evidence_score: float,
) -> ExpectedRewardRiskSummary:
    ticket_check = _get(command_center_report, "ticket_check")
    ratio = _to_float(_get(ticket_check, "risk_reward_ratio"))
    target = _to_float(_get(ticket_check, "target_price"))
    rr_line = str(_get(ticket_check, "risk_reward") or "Reward/risk is unavailable because entry, stop, or target is missing.")
    risk_read = str(_get(ticket_check, "risk_reward_read") or "unknown")
    probability = None
    ev_units = None
    if ratio is not None and ratio > 0:
        probability = _clamp(0.50 + ((evidence_score - 50.0) / 260.0), 0.35, 0.65)
        ev_units = (probability * ratio) - ((1 - probability) * 1.0)
        label = "Favorable planning EV" if ev_units >= 0.25 and risk_read != "poor" else "Thin planning EV" if ev_units > 0 else "Unfavorable planning EV"
        summary = (
            f"{label}: {rr_line} Evidence-weighted planning probability is {probability:.0%}; "
            f"scenario EV is {ev_units:+.2f}R before fees/slippage. "
            "This is research support and scenario math, not financial advice, a guarantee, or a forecast."
        )
    else:
        label = "Reward/risk not defined"
        summary = "Reward/risk is not defined yet; add a coherent entry, invalidation line, and target before using sizing notes."
    target_text = f"Target reference: {_money(target)}." if target is not None else "Target reference is unavailable."
    return ExpectedRewardRiskSummary(
        label=label,
        reward_risk_ratio=round(ratio, 2) if ratio is not None else None,
        planning_probability=round(probability, 2) if probability is not None else None,
        expected_value_units=round(ev_units, 2) if ev_units is not None else None,
        reward_line=rr_line,
        risk_line=target_text,
        summary=summary,
    )


def _chart_setup_component(report: Any | None) -> EvidenceComponent:
    if report is None:
        return _no_read("chart_setup", "Chart setup", "Technical Command Center report is unavailable.", weight=1.35)
    score = _to_float(_get(report, "overall_score"))
    classification = _get(report, "setup_classification")
    if score is None:
        return _no_read("chart_setup", "Chart setup", "Command Center score is unavailable.", weight=1.35)

    vote = (score - 50.0) * 2.0
    setup = str(_get(classification, "setup") or "").lower()
    timing = str(_get(classification, "timing") or "").lower()
    action_quality = str(_get(classification, "action_quality") or "").lower()
    best_action = str(_get(report, "best_action") or "")
    if setup in {"breakout", "pullback"}:
        vote += 8.0
    if setup in {"breakdown"}:
        vote -= 28.0
    if setup in {"chop", "unknown"}:
        vote -= 10.0
    if timing == "confirmed":
        vote += 8.0
    elif timing in {"failed", "early"}:
        vote -= 8.0
    if action_quality in {"good_entry"}:
        vote += 8.0
    elif action_quality in {"avoid_chase", "protect_or_trim", "no_edge"}:
        vote -= 18.0
    elif "wait" in action_quality:
        vote -= 8.0
    if "avoid" in best_action.lower() or "trim" in best_action.lower():
        vote -= 12.0
    reason = str(_get(classification, "main_reason") or f"Command Center read is {_get(report, 'overall_read') or 'mixed'} at {score:.0f}/100.")
    details = _clean_lines(
        [
            f"Overall read: {_get(report, 'overall_read') or '--'}; best action: {best_action or '--'}.",
            f"Setup: {setup or '--'}; timing: {timing or '--'}; action quality: {action_quality or '--'}.",
            *_list(_get(classification, "lines"))[:2],
        ],
        limit=5,
    )
    return _component(
        "chart_setup",
        "Chart setup",
        vote,
        1.35,
        _confidence_value(_get(report, "confidence")),
        reason,
        details=details,
    )


def _prc_component(report: Any | None) -> EvidenceComponent:
    prc = _preferred_prc(report)
    if prc is None:
        return _no_read("prc_pressure_line", "PRC / pressure line", "PRC Pressure Line output is unavailable.", weight=1.05)
    read = str(_get(prc, "read") or "").lower()
    vote = 0.0
    if "accumulation" in read or "constructive" in read:
        vote = 42.0
    elif "pullback opportunity" in read:
        vote = 28.0
    elif "compression" in read or "wait" in read:
        vote = -4.0
    elif "chasing" in read or "extended" in read:
        vote = -24.0
    elif "distribution" in read or "weak" in read:
        vote = -44.0
    distance = _to_float(_get(prc, "index_distance_percent"))
    slope = _to_float(_get(prc, "index_slope"))
    if distance is not None and distance > 4:
        vote -= 8.0
    if slope is not None:
        vote += _clamp(slope * 10.0, -8.0, 8.0)
    reason = str(_get(prc, "read") or "PRC Pressure Line is mixed.")
    details = _clean_lines(
        [
            f"Pressure line distance: {_signed_percent(distance)}; slope: {_signed_number(slope)}.",
            *_list(_get(prc, "explanation_lines"))[:2],
            *_list(_get(prc, "warnings"))[:2],
        ],
        limit=5,
    )
    missing = tuple(_clean_lines(_list(_get(prc, "warnings")), limit=3))
    return _component(
        "prc_pressure_line",
        "PRC / pressure line",
        vote,
        1.05,
        _confidence_value(_get(prc, "confidence")),
        reason,
        details=details,
        missing=missing,
    )


def _volume_component(report: Any | None) -> EvidenceComponent:
    snapshot = _preferred_snapshot(report)
    if snapshot is None:
        return _no_read("volume_confirmation", "Volume confirmation", "No technical snapshot is available for volume confirmation.", weight=0.95)
    score_component = _get(_get(snapshot, "scores") or {}, "Volume")
    volume_read = _get(snapshot, "volume_read")
    if score_component is not None and _to_float(_get(score_component, "score")) is not None:
        score = _to_float(_get(score_component, "score")) or 50.0
        vote = (score - 50.0) * 2.0
        reason = str(_get(score_component, "reason") or _get(volume_read, "reason") or "Volume score is mixed.")
    else:
        relative_volume = _to_float(_get(volume_read, "relative_volume"))
        up_down = _to_float(_get(volume_read, "up_down_volume_ratio"))
        vote = 0.0
        if relative_volume is not None:
            vote += 22.0 if relative_volume >= 1.5 else 8.0 if relative_volume >= 1.0 else -12.0
        if up_down is not None:
            vote += 16.0 if up_down >= 1.4 else -16.0 if up_down <= 0.75 else 0.0
        read = str(_get(volume_read, "accumulation_read") or "").lower()
        if "distribution" in read:
            vote -= 25.0
        elif "accumulation" in read:
            vote += 18.0
        reason = str(_get(volume_read, "reason") or "Volume confirmation is inferred from relative volume and accumulation read.")
    details = _clean_lines(
        [
            f"Snapshot: {_get(snapshot, 'label') or '--'}; relative volume {_number(_get(volume_read, 'relative_volume'))}; up/down volume {_number(_get(volume_read, 'up_down_volume_ratio'))}.",
            f"Accumulation read: {_get(volume_read, 'accumulation_read') or '--'}.",
            str(_get(volume_read, "reason") or ""),
        ],
        limit=4,
    )
    confidence = 0.85 if _get(volume_read, "relative_volume") is not None else 0.65
    return _component("volume_confirmation", "Volume confirmation", vote, 0.95, confidence, reason, details=details)


def _relative_strength_component(report: Any | None) -> EvidenceComponent:
    if report is None:
        return _no_read("relative_strength", "Relative strength", "Command Center benchmark reads are unavailable.", weight=0.90)
    score_component = _get(_get(report, "scores") or {}, "Relative Strength")
    reads = _list(_get(report, "benchmark_reads"))
    if score_component is not None and _to_float(_get(score_component, "score")) is not None:
        score = _to_float(_get(score_component, "score")) or 50.0
        vote = (score - 50.0) * 2.0
        reason = str(_get(score_component, "reason") or "Relative-strength score is mixed.")
    else:
        spreads = [_to_float(_get(read, "spread_20")) for read in reads]
        spreads = [value for value in spreads if value is not None]
        if not spreads:
            return _no_read("relative_strength", "Relative strength", "Benchmark relative-strength spreads are unavailable.", weight=0.90)
        average_spread = sum(spreads) / len(spreads)
        vote = _clamp(average_spread * 12.0, -45.0, 45.0)
        reason = f"Average 20-period benchmark spread is {average_spread:+.2f} percentage points."
    details = _clean_lines(
        [
            str(_get(score_component, "reason") or ""),
            *[
                f"{_get(read, 'benchmark') or 'Benchmark'}: {_get(read, 'verdict') or 'unknown'}, 20-period spread {_signed_percent(_get(read, 'spread_20'))}."
                for read in reads[:3]
            ],
        ],
        limit=5,
    )
    confidence = 0.85 if reads else 0.65
    return _component("relative_strength", "Relative strength", vote, 0.90, confidence, reason, details=details)


def _capital_structure_component(indicator: Any | None) -> EvidenceComponent:
    if indicator is None:
        return _no_read("capital_structure_supply", "Capital structure / supply", "Capital-structure indicator is unavailable.", weight=0.85)
    read = str(_get(indicator, "read") or "supply_context").lower()
    vote_by_read = {
        "supply_absorption": 30.0,
        "clean": 26.0,
        "supply_context": 0.0,
        "float_quality_watch": -12.0,
        "verification_needed": -16.0,
        "dilution_sensitive": -34.0,
        "offering_pressure": -36.0,
        "rally_fade_risk": -46.0,
        "option_size_mismatch": -46.0,
    }
    vote = vote_by_read.get(read, 0.0)
    technical_score = _to_float(_get(indicator, "technical_score"))
    if technical_score is not None:
        vote += _clamp((technical_score - 50.0) * 0.35, -15.0, 15.0)
    chase_risk = _to_float(_get(indicator, "chase_risk_score"))
    if chase_risk is not None and chase_risk >= 70:
        vote -= 10.0
    source_count = _to_float(_get(indicator, "source_count")) or 0.0
    confidence = _clamp(0.55 + min(source_count, 5.0) * 0.07, 0.45, 0.92)
    if _to_float(_get(indicator, "foreign_issuer_confidence_modifier")) is not None and (_to_float(_get(indicator, "foreign_issuer_confidence_modifier")) or 0.0) < 0:
        confidence -= 0.12
    reason = str(_first_line(_get(indicator, "recommendation_lines")) or _get(indicator, "read") or "Supply context is mixed.")
    details = _clean_lines(
        [
            f"Read: {read}; technical score {_number(technical_score)}; chase risk {_number(chase_risk)}.",
            *_list(_get(indicator, "explanation_lines"))[:3],
            *_list(_get(indicator, "recommendation_lines"))[:2],
        ],
        limit=6,
    )
    return _component(
        "capital_structure_supply",
        "Capital structure / supply",
        vote,
        0.85,
        confidence,
        reason,
        details=details,
        missing=tuple(_clean_lines(_list(_get(indicator, "warnings")), limit=4)),
    )


def _macro_component(macro_read: Any | None, macro_text: str, fundamental_read: Any | None, fundamentals_text: str) -> EvidenceComponent:
    score: float | None = None
    confidence = 0.65
    reason = ""
    details: list[str] = []

    if macro_read is not None:
        macro_badge = _get(macro_read, "macro_backdrop") or macro_read
        score = _to_float(_get(macro_badge, "score"))
        label = _get(macro_badge, "label")
        why = _get(macro_badge, "why")
        if score is None and label:
            score = _macro_label_score(str(label))
        reason = str(why or f"Macro read is {label or 'mixed'}.")
        details.append(f"Macro read: {label or '--'}; score {_signed_number(score)}.")
        confidence = 0.85
    elif macro_text.strip():
        score = score_macro_text(macro_text)
        reason = "Macro text scored from loaded research summary."
        details.append(f"Macro text score: {_signed_number(score)}.")

    fundamental_adjustment, fundamental_detail = _fundamental_adjustment(fundamental_read, fundamentals_text)
    if fundamental_detail:
        details.append(fundamental_detail)
    if score is None and fundamental_adjustment == 0:
        return _no_read("macro_backdrop", "Macro backdrop", "Macro and fundamental backdrop reads are unavailable.", weight=0.80)
    base_score = score or 0.0
    vote = _clamp(base_score + fundamental_adjustment, -100.0, 100.0)
    if not reason:
        reason = "Fundamental context was available, but macro context was not loaded."
        confidence = 0.55
    return _component("macro_backdrop", "Macro backdrop", vote, 0.80, confidence, reason, details=tuple(_clean_lines(details, limit=5)))


def _option_component(option_candidates: Sequence[Any] | Any | None, option_chain_rows: Sequence[dict[str, Any]] | None) -> EvidenceComponent:
    candidates = _normalise_candidates(option_candidates)
    if candidates:
        candidate = _best_option_candidate(candidates)
        score = _to_float(_get(candidate, "score"))
        strategy = str(_get(candidate, "strategy") or _get(candidate, "group") or "Option candidate")
        option_type = str(_get(candidate, "option_type") or _get(candidate, "side") or "").lower()
        if score is None:
            score = _option_quality_score(candidate)
        vote = (score - 50.0) * 2.0
        if option_type not in {"call", "put"} or "wait" in strategy.lower() or "no-trade" in strategy.lower():
            vote = min(vote, -12.0 if score >= 50 else vote)
        reason = str(_get(candidate, "score_reason") or _get(candidate, "why") or f"{strategy} scored {score:.0f}/100.")
        details = _clean_lines(
            [
                f"Best option candidate: {strategy}; type {option_type or '--'}; score {score:.0f}/100.",
                f"Spread {_percent_value(_get(candidate, 'spread_pct'))}; volume {_number(_get(candidate, 'volume'))}; OI {_number(_get(candidate, 'open_interest'))}; DTE {_number(_get(candidate, 'dte'))}.",
                *_list(_get(candidate, "score_breakdown"))[:3],
                str(_get(candidate, "avoid_reason") or ""),
            ],
            limit=6,
        )
        return _component("option_fit", "Option fit", vote, 0.85, 0.82, reason, details=details)

    rows = [row for row in option_chain_rows or () if isinstance(row, dict)]
    if rows:
        score, reason = _option_chain_rows_score(rows)
        vote = (score - 50.0) * 2.0
        return _component(
            "option_fit",
            "Option fit",
            vote,
            0.85,
            0.68,
            reason,
            details=(f"{len(rows)} option-chain row(s) loaded; candidate-level scoring was not provided.",),
        )
    return _no_read("option_fit", "Option fit", "No option candidates or option-chain rows are loaded.", weight=0.85)


def _portfolio_component(context: Any | None) -> EvidenceComponent:
    if context is None:
        return _no_read("portfolio_position_risk", "Portfolio concentration / position risk", "Portfolio context is unavailable.", weight=0.85)
    weight = _to_float(_get(context, "portfolio_weight")) or 0.0
    cash = _to_float(_get(context, "cash_available")) or 0.0
    value = _to_float(_get(context, "portfolio_value")) or 0.0
    is_held = bool(_get(context, "is_held"))
    cash_ratio = cash / value if value > 0 else None
    vote = 8.0 if not is_held else 4.0
    abs_weight = abs(weight)
    if abs_weight >= 0.15:
        vote -= 65.0
    elif abs_weight >= 0.10:
        vote -= 48.0
    elif abs_weight >= 0.05:
        vote -= 24.0
    elif is_held:
        vote += 4.0
    if cash_ratio is not None:
        if cash_ratio < 0.05:
            vote -= 18.0
        elif cash_ratio >= 0.25:
            vote += 5.0
    reason = f"Portfolio weight {abs_weight:.2%}; cash ratio {_percent_value(cash_ratio)}."
    details = _clean_lines(
        [
            f"Held: {'yes' if is_held else 'no'}; quantity {_number(_get(context, 'quantity'))}; market value {_money(_get(context, 'market_value'))}.",
            f"Portfolio value {_money(value)}; cash {_money(cash)}.",
        ],
        limit=4,
    )
    return _component("portfolio_position_risk", "Portfolio concentration / position risk", vote, 0.85, 0.90, reason, details=details)


def _data_confidence_component(data_confidence: DataConfidenceRead) -> EvidenceComponent:
    vote = _clamp((data_confidence.score - 60.0) * 1.25, -50.0, 35.0)
    details = _clean_lines(
        [
            data_confidence.reason,
            *[f"{source.source}: {source.status} ({source.score:.0f}/100)." for source in data_confidence.sources[:5]],
        ],
        limit=6,
    )
    return _component(
        "data_confidence",
        "Data confidence",
        vote,
        0.90,
        1.0,
        data_confidence.reason,
        details=details,
        missing=data_confidence.missing[:5],
    )


def _price_source_confidence(report: Any | None) -> DataSourceConfidence:
    snapshots = _dict_values(_get(report, "snapshots"))
    usable = [item for item in snapshots if (_to_float(_get(item, "candle_count")) or 0) >= 35]
    if usable:
        best_count = max((_to_float(_get(item, "candle_count")) or 0) for item in snapshots)
        score = 88.0 if len(usable) >= 2 else 74.0
        return DataSourceConfidence("Price history", "loaded", score, f"{len(usable)} usable technical timeframe(s); max candles {best_count:.0f}.", weight=1.35)
    if snapshots:
        return DataSourceConfidence("Price history", "limited", 42.0, "Price history exists but has too few candles for full reads.", weight=1.35)
    return DataSourceConfidence("Price history", "missing", 12.0, "No Technical Command Center price-history snapshot was supplied.", weight=1.35)


def _benchmark_source_confidence(report: Any | None) -> DataSourceConfidence:
    reads = _list(_get(report, "benchmark_reads"))
    usable = [read for read in reads if str(_get(read, "verdict") or "").lower() != "unknown"]
    if usable:
        return DataSourceConfidence("Benchmark / relative strength", "loaded", 82.0, f"{len(usable)} benchmark read(s) loaded.", weight=0.80)
    if reads:
        return DataSourceConfidence("Benchmark / relative strength", "limited", 45.0, "Benchmark reads are present but unresolved.", weight=0.80)
    return DataSourceConfidence("Benchmark / relative strength", "missing", 32.0, "Benchmark relative-strength data is not loaded.", weight=0.80)


def _prc_source_confidence(report: Any | None) -> DataSourceConfidence:
    prcs = _dict_values(_get(report, "prc_indexes"))
    if not prcs:
        return DataSourceConfidence("PRC Pressure Line", "missing", 30.0, "No PRC Pressure Line read was supplied.", weight=0.75)
    confidence_scores = [_confidence_value(_get(prc, "confidence")) * 100 for prc in prcs]
    score = max(confidence_scores) if confidence_scores else 60.0
    return DataSourceConfidence("PRC Pressure Line", "loaded", score, f"{len(prcs)} PRC read(s) loaded.", weight=0.75)


def _capital_source_confidence(indicator: Any | None) -> DataSourceConfidence:
    if indicator is None:
        return DataSourceConfidence("Capital structure", "missing", 38.0, "Capital-structure indicator is not loaded.", weight=0.70)
    source_count = _to_float(_get(indicator, "source_count")) or 0.0
    warnings = len(_list(_get(indicator, "warnings")))
    score = _clamp(58.0 + min(source_count, 5.0) * 7.0 - warnings * 4.0, 35.0, 90.0)
    return DataSourceConfidence("Capital structure", "loaded", score, f"Capital-structure indicator has {source_count:.0f} source(s) and {warnings} warning(s).", weight=0.70)


def _macro_source_confidence(macro_read: Any | None, macro_text: str) -> DataSourceConfidence:
    if macro_read is not None:
        return DataSourceConfidence("Macro backdrop", "loaded", 78.0, "Macro read object was supplied.", weight=0.80)
    if macro_text.strip():
        return DataSourceConfidence("Macro backdrop", "loaded", 66.0, "Macro text summary was supplied.", weight=0.80)
    return DataSourceConfidence("Macro backdrop", "missing", 35.0, "Macro backdrop is not loaded.", weight=0.80)


def _fundamental_source_confidence(fundamental_read: Any | None, fundamentals_text: str) -> DataSourceConfidence:
    if fundamental_read is not None:
        confidence = _confidence_value(_get(fundamental_read, "confidence"))
        score = 70.0 if confidence <= 0.5 else confidence * 100
        return DataSourceConfidence("Fundamentals", "loaded", score, "Fundamental read object was supplied.", weight=0.70)
    if fundamentals_text.strip() and "unavailable" not in fundamentals_text.lower():
        return DataSourceConfidence("Fundamentals", "loaded", 64.0, "Fundamental text summary was supplied.", weight=0.70)
    return DataSourceConfidence("Fundamentals", "missing", 34.0, "Fundamental read is missing or unavailable.", weight=0.70)


def _options_source_confidence(option_candidates: Sequence[Any] | Any | None, option_chain_rows: Sequence[dict[str, Any]] | None) -> DataSourceConfidence:
    candidates = _normalise_candidates(option_candidates)
    if candidates:
        return DataSourceConfidence("Options", "loaded", 76.0, f"{len(candidates)} option candidate(s) supplied.", weight=0.70)
    rows = [row for row in option_chain_rows or () if isinstance(row, dict)]
    if rows:
        return DataSourceConfidence("Options", "loaded", 66.0, f"{len(rows)} raw option-chain row(s) supplied.", weight=0.70)
    return DataSourceConfidence("Options", "missing", 36.0, "No option candidates or chain rows are loaded.", weight=0.70)


def _portfolio_source_confidence(context: Any | None) -> DataSourceConfidence:
    if context is None:
        return DataSourceConfidence("Portfolio context", "missing", 36.0, "Portfolio context is unavailable.", weight=0.65)
    if _to_float(_get(context, "portfolio_value")) is None:
        return DataSourceConfidence("Portfolio context", "limited", 48.0, "Portfolio context lacks portfolio value.", weight=0.65)
    return DataSourceConfidence("Portfolio context", "loaded", 82.0, "Portfolio value, exposure, and cash context are available.", weight=0.65)


def _status_confidence_rows(statuses: Sequence[Any], *, now: datetime) -> list[DataSourceConfidence]:
    rows: list[DataSourceConfidence] = []
    for status in statuses:
        source = str(_get(status, "source") or _get(status, "provider") or "Source status")
        state = str(_get(status, "status") or "").strip().lower()
        fetched_at = str(_get(status, "fetched_at") or _get(status, "fetch_timestamp") or "") or None
        score, normalized = _score_status_text(state)
        age_hours = _age_hours(fetched_at, now=now)
        if age_hours is not None:
            if age_hours > 72:
                score = min(score, 45.0)
                normalized = "stale"
            elif age_hours > 24:
                score = min(score, 62.0)
                normalized = "cached" if normalized == "loaded" else normalized
        message = str(_get(status, "message") or "")
        reason = message or f"Source reported status '{state or 'unknown'}'."
        rows.append(
            DataSourceConfidence(
                source=source,
                status=normalized,
                score=score,
                reason=reason,
                fetched_at=fetched_at,
                age_hours=round(age_hours, 2) if age_hours is not None else None,
                weight=0.40,
            )
        )
    return rows


def _score_status_text(status: str) -> tuple[float, str]:
    lower = status.lower()
    if "error" in lower or "failed" in lower:
        return 15.0, "error"
    if "stale" in lower:
        return 35.0, "stale"
    if "not found" in lower or "unavailable" in lower:
        return 32.0, "missing"
    if "pending" in lower:
        return 45.0, "limited"
    if "limited" in lower or "supplemental" in lower:
        return 55.0, "limited"
    if "cached" in lower or "cache" in lower:
        return 64.0, "cached"
    if "fresh" in lower or "loaded" in lower or "ok" in lower or "success" in lower:
        return 86.0, "loaded"
    if "not-applicable" in lower or "not applicable" in lower:
        return 70.0, "not-applicable"
    return 50.0, lower or "unknown"


def _recommendation_confidence(
    components: Sequence[EvidenceComponent],
    data_confidence: DataConfidenceRead,
    evidence_vote: float,
) -> tuple[str, float]:
    available = [component for component in components if component.status != "no_read"]
    coverage = len(available) / max(len(components), 1)
    positive = sum(1 for component in available if component.vote.score >= 25)
    negative = sum(1 for component in available if component.vote.score <= -25)
    conflict_penalty = 10.0 if positive and negative else 0.0
    confidence_score = (
        data_confidence.score * 0.48
        + coverage * 100.0 * 0.27
        + min(abs(evidence_vote), 70.0) * 0.25
        - conflict_penalty
    )
    confidence_score = _clamp(confidence_score, 0.0, 100.0)
    if data_confidence.score < 40:
        confidence_score = min(confidence_score, 42.0)
    elif data_confidence.score < 60:
        confidence_score = min(confidence_score, 62.0)
    if confidence_score >= 75:
        return "High", round(confidence_score, 2)
    if confidence_score >= 55:
        return "Medium", round(confidence_score, 2)
    return "Low", round(confidence_score, 2)


def _recommendation_label(
    evidence_score: float,
    data_confidence: DataConfidenceRead,
    components: Sequence[EvidenceComponent],
    report: Any | None,
    capital_indicator: Any | None,
) -> str:
    if data_confidence.score < 35:
        return "No-read / gather data"
    best_action = str(_get(report, "best_action") or "").lower()
    capital_read = str(_get(capital_indicator, "read") or "").lower()
    blockers = [
        component
        for component in components
        if component.key in {"capital_structure_supply", "option_fit", "portfolio_position_risk"}
        and component.vote.score <= -42
    ]
    if capital_read == "supply_absorption" and evidence_score >= 54:
        return "Watch for absorption"
    if "avoid chase" in best_action or any(component.key == "capital_structure_supply" for component in blockers):
        return "Avoid chase / wait for confirmation" if evidence_score >= 42 else "Avoid or reduce risk"
    if evidence_score >= 72 and not blockers:
        return "Constructive / defined-risk only"
    if evidence_score >= 62:
        return "Watch / pullback only" if blockers else "Constructive but wait for trigger"
    if evidence_score >= 48:
        return "Wait for confirmation"
    if evidence_score >= 38:
        return "Defensive / avoid new risk"
    return "Avoid or reduce risk"


def _trigger_lines(report: Any | None) -> tuple[list[str], list[str]]:
    invalidation: list[str] = []
    confirmation: list[str] = []
    classification = _get(report, "setup_classification")
    invalidation_level = _to_float(_get(classification, "invalidation_level"))
    confirmation_level = _to_float(_get(classification, "confirmation_level"))
    if invalidation_level is not None:
        invalidation.append(f"Invalidation: losing {_money(invalidation_level)} weakens the current setup.")
    if confirmation_level is not None:
        confirmation.append(f"Confirmation: reclaiming or holding above {_money(confirmation_level)} improves the setup.")
    for trigger in _list(_get(report, "key_triggers")):
        label = str(_get(trigger, "label") or "")
        price = _to_float(_get(trigger, "price"))
        reason = str(_get(trigger, "reason") or "")
        line = f"{label}: {_money(price)} - {reason}".strip()
        if "invalid" in label.lower() or "risk" in label.lower():
            invalidation.append(line)
        elif "confirm" in label.lower() or "breakout" in label.lower() or "pullback" in label.lower():
            confirmation.append(line)
    if not invalidation:
        invalidation.append("Invalidation line unavailable; define the risk line before sizing.")
    if not confirmation:
        confirmation.append("Confirmation line unavailable; wait for a cleaner trigger before upgrading the read.")
    return _dedupe(invalidation)[:4], _dedupe(confirmation)[:4]


def _position_sizing_notes(
    *,
    portfolio_context: Any | None,
    command_center_report: Any | None,
    option_candidates: Sequence[Any] | Any | None,
    stock_plan: Any | None,
) -> list[str]:
    notes = ["Position sizing note: this readout is planning context only and does not alter broker/order behavior."]
    if portfolio_context is None:
        notes.append("Portfolio context is unavailable, so concentration and cash caps cannot be assessed.")
    else:
        weight = _to_float(_get(portfolio_context, "portfolio_weight")) or 0.0
        cash = _to_float(_get(portfolio_context, "cash_available")) or 0.0
        value = _to_float(_get(portfolio_context, "portfolio_value")) or 0.0
        notes.append(f"Current exposure is {_percent_value(abs(weight))} of portfolio; cash ratio {_percent_value(cash / value if value > 0 else None)}.")
        if abs(weight) >= 0.10:
            notes.append("Concentration is elevated; new risk should be smaller or require stronger confirmation.")
        elif abs(weight) >= 0.05:
            notes.append("Position is meaningful; keep any add-on sized from a defined invalidation line.")
        elif bool(_get(portfolio_context, "is_held")):
            notes.append("Existing position is modest; sizing still depends on stop distance and event risk.")
    ticket_check = _get(command_center_report, "ticket_check")
    risk_note = str(_get(ticket_check, "risk_note") or "")
    if risk_note:
        notes.append(f"Command Center ticket risk: {risk_note}")
    rr_read = str(_get(ticket_check, "risk_reward_read") or "")
    if rr_read == "poor":
        notes.append("Reward/risk is poor; reduce size or wait for a better entry/stop/target map.")
    if stock_plan is not None:
        notes.append(
            f"Model stock plan: {_number(_get(stock_plan, 'quantity'))} shares, notional {_money(_get(stock_plan, 'notional'))}, portfolio weight {_percent_value(_get(stock_plan, 'portfolio_weight'))}."
        )
    option = _best_option_candidate(_normalise_candidates(option_candidates))
    if option is not None:
        controlled = _to_float(_get(option, "controlled_shares"))
        quantity = _to_float(_get(portfolio_context, "quantity")) if portfolio_context is not None else None
        if controlled is not None and quantity is not None and quantity > 0 and controlled > quantity * 1.5:
            notes.append(f"Option contract exposure controls about {controlled:g} shares versus {quantity:g} current shares; avoid accidental oversizing.")
    return _dedupe(notes)[:7]


def _what_would_change(
    *,
    components: Sequence[EvidenceComponent],
    command_center_report: Any | None,
    capital_structure_indicator: Any | None,
    data_confidence: DataConfidenceRead,
    macro_read: Any | None,
    fundamental_read: Any | None,
) -> list[str]:
    lines: list[str] = []
    for line in _list(_get(command_center_report, "plain_english_plan")):
        if "what would change" in str(line).lower() or "if bullish" in str(line).lower() or "if bearish" in str(line).lower():
            lines.append(str(line))
    lines.extend(str(line) for line in _list(_get(capital_structure_indicator, "recommendation_lines"))[:2])
    lines.extend(str(line) for line in _list(_get(macro_read, "changes_view"))[:2])
    lines.extend(str(line) for line in _list(_get(fundamental_read, "what_changes"))[:2])
    for source in data_confidence.missing[:3]:
        lines.append(f"Data confidence improves if {source} is loaded or refreshed.")
    for component in sorted(components, key=lambda item: item.vote.score)[:2]:
        if component.vote.score <= -25 and component.status != "no_read":
            lines.append(f"{component.label} would improve if its current headwind is resolved: {component.vote.reason}")
    return _dedupe(_clean_lines(lines, limit=8)) or ["Fresh source coverage and cleaner price confirmation would sharpen the view."]


def _why_lines(components: Sequence[EvidenceComponent]) -> list[str]:
    available = [component for component in components if component.status != "no_read"]
    if not available:
        return ["No loaded evidence components were available, so the recommendation is a no-read."]
    ranked = sorted(available, key=lambda component: abs(component.vote.score) * component.vote.confidence, reverse=True)
    lines: list[str] = []
    for component in ranked[:5]:
        direction = "supportive" if component.vote.score >= 20 else "headwind" if component.vote.score <= -20 else "mixed"
        lines.append(f"{component.label}: {direction} ({component.vote.score:+.0f}) - {component.vote.reason}")
    return lines


def _component(
    key: str,
    label: str,
    score: float,
    weight: float,
    confidence: float,
    reason: str,
    *,
    details: Iterable[str] = (),
    missing: Iterable[str] = (),
) -> EvidenceComponent:
    bounded_score = _clamp(score, -100.0, 100.0)
    status = "supportive" if bounded_score >= 20 else "headwind" if bounded_score <= -20 else "mixed"
    return EvidenceComponent(
        key=key,
        label=label,
        vote=EvidenceVote(
            score=round(bounded_score, 2),
            weight=max(0.0, weight),
            confidence=round(_clamp(confidence, 0.0, 1.0), 4),
            reason=str(reason or "Evidence is mixed.")[:320],
        ),
        status=status,
        details=tuple(_clean_lines(details, limit=8)),
        missing=tuple(_clean_lines(missing, limit=8)),
    )


def _no_read(key: str, label: str, reason: str, *, weight: float) -> EvidenceComponent:
    return EvidenceComponent(
        key=key,
        label=label,
        vote=EvidenceVote(score=0.0, weight=weight, confidence=0.0, reason=reason),
        status="no_read",
        details=(),
        missing=(reason,),
    )


def _preferred_snapshot(report: Any | None) -> Any | None:
    snapshots = _get(report, "snapshots")
    if isinstance(snapshots, dict):
        for key in ("timing_5m", "setup_30m", "timing_1m", "daily_1y"):
            snapshot = snapshots.get(key)
            if snapshot is not None and (_to_float(_get(snapshot, "candle_count")) or 0) > 0:
                return snapshot
        for snapshot in snapshots.values():
            if snapshot is not None:
                return snapshot
    return None


def _preferred_prc(report: Any | None) -> Any | None:
    prcs = _get(report, "prc_indexes")
    if isinstance(prcs, dict):
        for key in ("timing_5m", "setup_30m", "timing_1m", "daily_1y"):
            if prcs.get(key) is not None:
                return prcs[key]
        for prc in prcs.values():
            if prc is not None:
                return prc
    return None


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
    return sorted(candidates, key=lambda item: _to_float(_get(item, "score")) if _to_float(_get(item, "score")) is not None else _option_quality_score(item), reverse=True)[0]


def _option_quality_score(candidate: Any) -> float:
    score = 50.0
    bid = _to_float(_get(candidate, "bid"))
    ask = _to_float(_get(candidate, "ask"))
    mark = _to_float(_get(candidate, "mark"))
    volume = _to_float(_get(candidate, "volume"))
    dte = _to_float(_get(candidate, "dte"))
    if bid is not None and ask is not None and ask > 0 and ask >= bid:
        mid = max((bid + ask) / 2.0, 0.01)
        spread = (ask - bid) / mid
        if spread <= 0.10:
            score += 18.0
        elif spread <= 0.25:
            score += 6.0
        else:
            score -= 16.0
    elif mark is None:
        score -= 8.0
    if volume is not None:
        score += 10.0 if volume >= 100 else -8.0 if volume < 10 else 0.0
    if dte is not None:
        score += 5.0 if 14 <= dte <= 60 else -8.0 if dte < 7 else 0.0
    return _clamp(score, 0.0, 100.0)


def _option_chain_rows_score(rows: Sequence[dict[str, Any]]) -> tuple[float, str]:
    scores: list[float] = []
    for row in rows[:20]:
        for side in ("call", "put"):
            contract = row.get(side)
            if isinstance(contract, dict):
                scores.append(_option_quality_score(contract))
    if not scores:
        return 45.0, "Option-chain rows are loaded, but bid/ask/volume fields are incomplete."
    return sum(scores) / len(scores), f"Raw option-chain quality averaged {sum(scores) / len(scores):.0f}/100 across loaded near-chain contracts."


def _fundamental_adjustment(fundamental_read: Any | None, fundamentals_text: str) -> tuple[float, str]:
    text_parts = [
        str(_get(fundamental_read, "verdict") or ""),
        str(_get(fundamental_read, "action_bias") or ""),
        str(_get(fundamental_read, "investment_read") or ""),
        str(_get(fundamental_read, "combined_read") or ""),
        fundamentals_text,
    ]
    text = " ".join(text_parts).lower()
    if not text.strip():
        return 0.0, ""
    if any(term in text for term in ("avoid", "unfavorable", "going concern", "weak", "deteriorat", "cash used")):
        return -14.0, "Fundamental read is a headwind."
    if any(term in text for term in ("strong", "favorable", "supports owning", "profitable", "revenue growth", "cash flow")):
        return 10.0, "Fundamental read is supportive."
    if "unavailable" in text or "unknown" in text:
        return 0.0, "Fundamental read is unavailable."
    return 0.0, "Fundamental read is mixed."


def _macro_label_score(label: str) -> float:
    lower = label.lower()
    if "tailwind" in lower or "support" in lower:
        return 45.0
    if "headwind" in lower or "hostile" in lower:
        return -45.0
    return 0.0


def _confidence_value(value: Any) -> float:
    if isinstance(value, (int, float)):
        numeric = float(value)
        return _clamp(numeric / 100.0 if numeric > 1 else numeric, 0.0, 1.0)
    lower = str(value or "").lower()
    if "very high" in lower:
        return 0.95
    if "high" in lower:
        return 0.88
    if "medium-high" in lower:
        return 0.78
    if "medium-low" in lower:
        return 0.58
    if "medium" in lower:
        return 0.70
    if "low" in lower:
        return 0.45
    return 0.60


def _data_confidence_grade(score: float) -> str:
    if score >= 75:
        return "High"
    if score >= 60:
        return "Medium"
    if score >= 40:
        return "Low"
    return "Very Low"


def _get(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _dict_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return list(value.values())
    return []


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _first_line(value: Any) -> str:
    lines = _clean_lines(_list(value), limit=1)
    return lines[0] if lines else ""


def _clean_lines(values: Iterable[Any], *, limit: int) -> list[str]:
    lines: list[str] = []
    for value in values:
        text = str(value or "").strip()
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
        return float(value)
    except (TypeError, ValueError):
        return None


def _age_hours(value: str | None, *, now: datetime) -> float | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now.tzinfo or timezone.utc)
    active_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    return max((active_now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() / 3600.0, 0.0)


def _clean_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _money(value: Any) -> str:
    number = _to_float(value)
    return "--" if number is None else f"${number:,.2f}"


def _number(value: Any) -> str:
    number = _to_float(value)
    return "--" if number is None else f"{number:,.1f}"


def _signed_number(value: Any) -> str:
    number = _to_float(value)
    return "--" if number is None else f"{number:+.1f}"


def _signed_percent(value: Any) -> str:
    number = _to_float(value)
    return "--" if number is None else f"{number:+.2f}%"


def _percent_value(value: Any) -> str:
    number = _to_float(value)
    return "--" if number is None else f"{number:.1%}"
