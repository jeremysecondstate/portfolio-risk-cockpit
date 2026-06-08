from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Iterable, Sequence


@dataclass(frozen=True)
class SetupReplayMatch:
    start_index: int
    end_index: int
    similarity: float
    forward_return: float | None
    outcome: str


@dataclass(frozen=True)
class SetupReplayRead:
    sample_count: int
    lookback: int
    horizon: int
    median_forward_return: float | None
    win_rate: float | None
    average_similarity: float | None
    raw_score: float
    confidence: float
    label: str
    summary: str
    warnings: tuple[str, ...] = ()
    matches: tuple[SetupReplayMatch, ...] = ()


@dataclass(frozen=True)
class CatalystCollisionRead:
    score: float
    label: str
    events: tuple[str, ...]
    warnings: tuple[str, ...]
    summary: str


@dataclass(frozen=True)
class OptionRequiredMoveRead:
    strategy_kind: str
    label: str
    status: str
    required_move_pct: float | None
    implied_move_pct: float | None
    excess_move_pct: float | None
    lines: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SupplyAbsorptionRead:
    read: str
    label: str
    score: float
    level: float | None
    level_label: str
    distance_percent: float | None
    evidence_lines: tuple[str, ...]
    confirmation_lines: tuple[str, ...]
    invalidation_lines: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConfidenceShrinkageRead:
    raw_evidence_score: float
    confidence_adjusted_score: float
    shrink_factor: float
    factors: tuple[str, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EmpiricalRecommendationIntelligenceRead:
    symbol: str
    raw_evidence_score: float
    confidence_adjusted_score: float
    setup_replay: SetupReplayRead
    catalyst_collision: CatalystCollisionRead
    option_required_move: OptionRequiredMoveRead | None
    supply_absorption: SupplyAbsorptionRead
    shrinkage: ConfidenceShrinkageRead
    regime_label: str
    regime_warnings: tuple[str, ...]
    recommendation_lines: tuple[str, ...]
    confirmation_lines: tuple[str, ...]
    invalidation_lines: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)


def build_empirical_recommendation_intelligence(
    *,
    symbol: str,
    historical_candles: Sequence[Any] | None = None,
    current_evidence_score: float = 50.0,
    data_confidence_score: float | None = None,
    command_center_report: Any | None = None,
    option_candidates: Sequence[Any] | Any | None = None,
    selected_option_candidate: Any | None = None,
    earnings_text: str = "",
    filings_lines: Sequence[str] | None = None,
    macro_snapshot: Any | None = None,
    capital_structure_indicator: Any | None = None,
    capital_structure_pressure: Any | None = None,
    as_of: datetime | date | None = None,
) -> EmpiricalRecommendationIntelligenceRead:
    """Build deterministic empirical recommendation context.

    This function is pure. It reads only the arguments supplied by the caller and
    never submits orders, fetches network data, writes files, or mutates broker
    state.
    """

    clean_symbol = str(symbol or "UNKNOWN").strip().upper() or "UNKNOWN"
    candles = list(historical_candles or ())
    option = selected_option_candidate or _best_option_candidate(_normalise_candidates(option_candidates))
    active_capital = (
        capital_structure_indicator
        or _get(command_center_report, "capital_structure_indicator")
        or _capital_pressure_context_indicator(capital_structure_pressure or _get(command_center_report, "capital_structure_pressure"))
    )

    replay = build_setup_replay_read(candles)
    catalyst = build_catalyst_collision_read(
        earnings_text=earnings_text,
        filings_lines=filings_lines or (),
        macro_snapshot=macro_snapshot,
        option_candidate=option,
        capital_structure_indicator=active_capital,
        as_of=as_of,
    )
    option_move = build_option_required_move_read(option)
    supply = detect_supply_absorption(candles, capital_structure_indicator=active_capital)
    regime_label, regime_warnings = regime_read(command_center_report)
    shrinkage = shrink_confidence_score(
        raw_evidence_score=current_evidence_score,
        data_confidence_score=data_confidence_score,
        replay=replay,
        catalyst=catalyst,
        option_move=option_move,
        supply=supply,
        regime_warnings=regime_warnings,
    )

    recommendation_lines = _empirical_recommendation_lines(
        replay=replay,
        catalyst=catalyst,
        option_move=option_move,
        supply=supply,
        shrinkage=shrinkage,
        regime_label=regime_label,
    )
    confirmation_lines = _dedupe(
        [
            *_empirical_confirmation_lines(replay, supply, option_move),
            *supply.confirmation_lines,
        ]
    )[:8]
    invalidation_lines = _dedupe(
        [
            *_empirical_invalidation_lines(replay, supply, option_move),
            *supply.invalidation_lines,
        ]
    )[:8]
    warnings = _dedupe(
        [
            *replay.warnings,
            *catalyst.warnings,
            *(option_move.warnings if option_move is not None else ()),
            *supply.warnings,
            *regime_warnings,
            *shrinkage.warnings,
        ]
    )[:12]

    return EmpiricalRecommendationIntelligenceRead(
        symbol=clean_symbol,
        raw_evidence_score=round(float(current_evidence_score), 2),
        confidence_adjusted_score=shrinkage.confidence_adjusted_score,
        setup_replay=replay,
        catalyst_collision=catalyst,
        option_required_move=option_move,
        supply_absorption=supply,
        shrinkage=shrinkage,
        regime_label=regime_label,
        regime_warnings=tuple(regime_warnings),
        recommendation_lines=tuple(recommendation_lines),
        confirmation_lines=tuple(confirmation_lines),
        invalidation_lines=tuple(invalidation_lines),
        warnings=tuple(warnings),
    )


def build_setup_replay_read(
    candles: Sequence[Any],
    *,
    lookback: int = 20,
    horizon: int = 5,
    max_matches: int = 24,
    min_similarity: float = 0.34,
) -> SetupReplayRead:
    rows = [_candle_row(candle) for candle in candles]
    rows = [row for row in rows if row is not None]
    minimum = lookback * 2 + horizon + 1
    if len(rows) < minimum:
        warning = f"Low sample: {len(rows)} candle(s) loaded; setup replay needs at least {minimum} for {lookback}-bar windows and {horizon}-bar outcomes."
        return SetupReplayRead(
            sample_count=0,
            lookback=lookback,
            horizon=horizon,
            median_forward_return=None,
            win_rate=None,
            average_similarity=None,
            raw_score=50.0,
            confidence=0.0,
            label="Replay unavailable",
            summary="Same-symbol replay is unavailable because local history is too short.",
            warnings=(warning,),
            matches=(),
        )

    current = rows[-lookback:]
    current_features = _window_features(current)
    if current_features is None:
        return SetupReplayRead(
            sample_count=0,
            lookback=lookback,
            horizon=horizon,
            median_forward_return=None,
            win_rate=None,
            average_similarity=None,
            raw_score=50.0,
            confidence=0.0,
            label="Replay unavailable",
            summary="Same-symbol replay is unavailable because current-window features could not be calculated.",
            warnings=("Current replay window has incomplete price or volume features.",),
            matches=(),
        )

    matches: list[SetupReplayMatch] = []
    last_start = len(rows) - lookback - horizon
    for start in range(0, max(0, last_start)):
        end = start + lookback
        window = rows[start:end]
        features = _window_features(window)
        if features is None:
            continue
        similarity = _feature_similarity(current_features, features)
        if similarity < min_similarity:
            continue
        entry = rows[end - 1]["close"]
        exit_price = rows[end + horizon - 1]["close"] if end + horizon - 1 < len(rows) else None
        forward_return = (exit_price - entry) / entry if exit_price is not None and entry > 0 else None
        outcome = _outcome_label(forward_return)
        matches.append(
            SetupReplayMatch(
                start_index=start,
                end_index=end - 1,
                similarity=round(similarity, 4),
                forward_return=round(forward_return, 4) if forward_return is not None else None,
                outcome=outcome,
            )
        )

    matches = sorted(matches, key=lambda item: item.similarity, reverse=True)[:max_matches]
    returns = [match.forward_return for match in matches if match.forward_return is not None]
    warnings: list[str] = []
    if len(matches) < 10:
        warnings.append(f"Low sample warning: only {len(matches)} similar same-symbol replay window(s) cleared the similarity threshold.")
    if not returns:
        return SetupReplayRead(
            sample_count=len(matches),
            lookback=lookback,
            horizon=horizon,
            median_forward_return=None,
            win_rate=None,
            average_similarity=_mean_or_none(match.similarity for match in matches),
            raw_score=50.0,
            confidence=0.10 if matches else 0.0,
            label="Replay inconclusive",
            summary="Similar windows were found, but forward outcomes were not available.",
            warnings=tuple(warnings or ["Replay forward outcomes were unavailable."]),
            matches=tuple(matches),
        )

    median_return = statistics.median(returns)
    win_rate = sum(1 for value in returns if value > 0) / len(returns)
    avg_similarity = sum(match.similarity for match in matches) / max(len(matches), 1)
    raw_score = _clamp(50.0 + median_return * 360.0 + (win_rate - 0.50) * 44.0 + (avg_similarity - 0.50) * 18.0, 0.0, 100.0)
    confidence = _clamp((len(matches) / max(max_matches, 1)) * 0.62 + avg_similarity * 0.38, 0.0, 1.0)
    label = "Replay supportive" if raw_score >= 60 else "Replay negative" if raw_score <= 42 else "Replay mixed"
    summary = (
        f"{len(matches)} similar same-symbol {lookback}-bar window(s); median {horizon}-bar forward return "
        f"{median_return:+.1%}; win rate {win_rate:.0%}. Historical analogs are decision support, not guarantees."
    )
    return SetupReplayRead(
        sample_count=len(matches),
        lookback=lookback,
        horizon=horizon,
        median_forward_return=round(median_return, 4),
        win_rate=round(win_rate, 4),
        average_similarity=round(avg_similarity, 4),
        raw_score=round(raw_score, 2),
        confidence=round(confidence, 4),
        label=label,
        summary=summary,
        warnings=tuple(warnings),
        matches=tuple(matches),
    )


def build_catalyst_collision_read(
    *,
    earnings_text: str = "",
    filings_lines: Sequence[str] = (),
    macro_snapshot: Any | None = None,
    option_candidate: Any | None = None,
    capital_structure_indicator: Any | None = None,
    as_of: datetime | date | None = None,
) -> CatalystCollisionRead:
    events: list[str] = []
    warnings: list[str] = []
    score = 0.0
    lower_earnings = str(earnings_text or "").lower()

    if any(term in lower_earnings for term in ("earnings event: today", "earnings today", "event: today")):
        score += 34.0
        events.append("Earnings timing: event appears to be today.")
    elif any(term in lower_earnings for term in ("earnings event: imminent", "earnings imminent", "near-term event", "earnings soon", "upcoming earnings")):
        score += 26.0
        events.append("Earnings timing: near-term earnings/event risk appears in loaded text.")
    elif "earnings calendar" in lower_earnings and "not found" not in lower_earnings:
        score += 12.0
        events.append("Earnings timing: calendar context is present.")

    if any(term in lower_earnings for term in ("fresh earnings release found", "item 2.02", "latest sec filing date", "8-k earnings")):
        score += 18.0
        events.append("Recent EDGAR/earnings drop appears in loaded source text.")
    for line in filings_lines[:8]:
        lower = str(line or "").lower()
        if "8-k" in lower and any(term in lower for term in ("earnings", "2.02", "results", "period")):
            score += 9.0
            events.append("Recent 8-K/earnings filing line is in the loaded filing stack.")
            break

    macro_events = _macro_collision_events(macro_snapshot, as_of=as_of)
    if macro_events:
        score += min(18.0, 6.0 + len(macro_events) * 4.0)
        events.extend(macro_events[:3])

    dte = _to_float(_get(option_candidate, "dte"))
    if dte is not None:
        if dte <= 7:
            score += 24.0
            events.append(f"Option expiration timing: selected candidate has {dte:.0f} DTE.")
        elif dte <= 14:
            score += 16.0
            events.append(f"Option expiration timing: selected candidate has {dte:.0f} DTE.")
        elif dte <= 30:
            score += 8.0
            events.append(f"Option expiration timing: selected candidate has {dte:.0f} DTE.")

    capital_read = str(_get(capital_structure_indicator, "read") or "").lower()
    chase_risk = _to_float(_get(capital_structure_indicator, "chase_risk_score"))
    if any(term in capital_read for term in ("rally_fade", "offering", "dilution")) or (chase_risk is not None and chase_risk >= 70):
        score += 22.0
        events.append("Capital-structure event: filing-derived supply/chase risk is elevated.")
    elif any(term in capital_read for term in ("supply", "verification", "watch")):
        score += 8.0
        events.append("Capital-structure event: filing-derived supply context needs verification.")

    bounded = round(_clamp(score, 0.0, 100.0), 2)
    if bounded >= 70:
        label = "High collision"
        warnings.append("Catalyst collision is high; size/risk assumptions should be more conservative.")
    elif bounded >= 45:
        label = "Elevated collision"
        warnings.append("Catalyst collision is elevated; avoid treating technical evidence as standalone.")
    elif bounded >= 20:
        label = "Moderate collision"
    else:
        label = "Low collision"

    if not events:
        events.append("No major catalyst collision was detected from the loaded earnings, macro, option, or capital-structure inputs.")
    summary = f"{label}: {bounded:.0f}/100 from {len(events)} loaded catalyst input(s). This is an event-risk overlay, not a forecast."
    return CatalystCollisionRead(
        score=bounded,
        label=label,
        events=tuple(_dedupe(events)[:8]),
        warnings=tuple(_dedupe(warnings)),
        summary=summary,
    )


def build_option_required_move_read(candidate: Any | None, *, underlying_price: float | None = None) -> OptionRequiredMoveRead | None:
    if candidate is None:
        return None
    strategy = str(_get(candidate, "strategy") or _get(candidate, "group") or "Option candidate")
    group = str(_get(candidate, "group") or "")
    option_type = str(_get(candidate, "option_type") or "").lower()
    underlying = _to_float(underlying_price) or _to_float(_get(candidate, "underlying_price"))
    strike = _to_float(_get(candidate, "strike"))
    breakeven = _to_float(_get(candidate, "breakeven"))
    required_move = _required_move_pct(option_type, breakeven, underlying)
    implied_move = _implied_move_pct(candidate)
    strategy_kind = _option_strategy_kind(strategy, group, option_type)

    if strategy_kind == "covered_call":
        lines = [
            "Covered-call read: this is not a long-debit required-move hurdle.",
            f"Focus on whether the call cap/assignment zone near {_money(strike)} is acceptable versus the premium received.",
            "Downside remains stock risk, only cushioned by premium; upside is capped above the strike if assigned.",
        ]
        if implied_move is not None:
            lines.append(f"Option-implied one-standard-deviation range is about +/-{implied_move:.1%} through expiration.")
        return OptionRequiredMoveRead(
            strategy_kind=strategy_kind,
            label="Covered-call cap check",
            status="mixed",
            required_move_pct=None,
            implied_move_pct=implied_move,
            excess_move_pct=None,
            lines=tuple(lines),
            warnings=("Covered-call wording avoids long-call breakeven framing because shares and assignment risk drive the trade-off.",),
        )

    if strategy_kind == "collar":
        lines = [
            "Collar read: compare the protected downside band and capped upside band; required-move language is not the right frame.",
            "A collar can reduce downside but gives up some upside and can still carry assignment or gap risk.",
        ]
        if implied_move is not None:
            lines.append(f"Option-implied range is about +/-{implied_move:.1%}; compare that range with the collar floor/cap.")
        return OptionRequiredMoveRead(
            strategy_kind=strategy_kind,
            label="Collar band check",
            status="mixed",
            required_move_pct=None,
            implied_move_pct=implied_move,
            excess_move_pct=None,
            lines=tuple(lines),
            warnings=("Collar wording is band/friction focused, not a standalone long-option breakeven claim.",),
        )

    if option_type not in {"call", "put"}:
        return OptionRequiredMoveRead(
            strategy_kind=strategy_kind,
            label="No option hurdle",
            status="info",
            required_move_pct=None,
            implied_move_pct=implied_move,
            excess_move_pct=None,
            lines=("No long call/put required-move comparison is available for this candidate.",),
            warnings=(),
        )

    if required_move is None or implied_move is None:
        missing = []
        if required_move is None:
            missing.append("breakeven/underlying")
        if implied_move is None:
            missing.append("IV/DTE")
        return OptionRequiredMoveRead(
            strategy_kind=strategy_kind,
            label="Move comparison incomplete",
            status="info",
            required_move_pct=required_move,
            implied_move_pct=implied_move,
            excess_move_pct=None,
            lines=(f"Required-vs-implied move comparison needs {', '.join(missing)} inputs.",),
            warnings=("Option move comparison is incomplete; do not infer edge from missing IV, DTE, breakeven, or underlying price.",),
        )

    excess = required_move - implied_move
    if required_move <= implied_move * 0.80:
        label = "Required move inside implied"
        status = "good"
    elif required_move <= implied_move:
        label = "Required move near implied"
        status = "mixed"
    else:
        label = "Required move exceeds implied"
        status = "bad"
    direction = "rise" if option_type == "call" else "fall"
    sign = "+" if option_type == "call" else "-"
    lines = [
        f"Long {option_type}: stock needs to {direction} about {sign}{required_move:.1%} to reach breakeven by expiration.",
        f"Option-implied move is about +/-{implied_move:.1%}; required minus implied is {excess:+.1%}.",
        "This is hurdle math for decision support, not a probability estimate or forecast.",
    ]
    warnings = []
    if excess > 0:
        warnings.append("Required move is larger than the option-implied move; premium hurdle is demanding.")
    return OptionRequiredMoveRead(
        strategy_kind=strategy_kind,
        label=label,
        status=status,
        required_move_pct=round(required_move, 4),
        implied_move_pct=round(implied_move, 4),
        excess_move_pct=round(excess, 4),
        lines=tuple(lines),
        warnings=tuple(warnings),
    )


def detect_supply_absorption(
    candles: Sequence[Any],
    *,
    capital_structure_indicator: Any | None = None,
    supply_level: float | None = None,
    level_label: str | None = None,
) -> SupplyAbsorptionRead:
    rows = [_candle_row(candle) for candle in candles]
    rows = [row for row in rows if row is not None]
    level = _to_float(supply_level) or _to_float(_get(capital_structure_indicator, "nearest_supply_level"))
    label = str(level_label or _get(capital_structure_indicator, "nearest_supply_level_label") or "filing-derived supply level")
    if level is None or level <= 0:
        return SupplyAbsorptionRead(
            read="no_level",
            label="No filing level",
            score=50.0,
            level=None,
            level_label=label,
            distance_percent=None,
            evidence_lines=("No filing-derived supply level was available for absorption/rejection detection.",),
            confirmation_lines=("Load a filing-derived warrant, conversion, resale, or offering level before using supply absorption reads.",),
            invalidation_lines=("No supply-level invalidation can be calculated without a parsed level.",),
        )
    if len(rows) < 12:
        warning = f"Low sample: {len(rows)} candle(s) loaded; supply absorption needs at least 12 recent candles."
        latest = rows[-1]["close"] if rows else None
        distance = (latest - level) / level * 100.0 if latest is not None else None
        return SupplyAbsorptionRead(
            read="low_sample",
            label="Supply read limited",
            score=50.0,
            level=level,
            level_label=label,
            distance_percent=round(distance, 2) if distance is not None else None,
            evidence_lines=(warning,),
            confirmation_lines=(f"Confirmation: multiple closes above {_money(level)} with expanding volume would improve the supply read.",),
            invalidation_lines=(f"Invalidation: rejection below {_money(level)} on expanding volume would weaken the absorption case.",),
            warnings=(warning,),
        )

    latest = rows[-1]
    latest_close = latest["close"]
    distance_percent = (latest_close - level) / level * 100.0
    recent = rows[-5:]
    prior = rows[-20:-5] if len(rows) >= 20 else rows[:-5]
    recent_avg_volume = _mean(row["volume"] for row in recent if row["volume"] is not None)
    prior_avg_volume = _mean(row["volume"] for row in prior if row["volume"] is not None)
    volume_ratio = recent_avg_volume / prior_avg_volume if prior_avg_volume > 0 else None
    closes_above = sum(1 for row in recent[-3:] if row["close"] > level)
    closes_below = sum(1 for row in recent[-3:] if row["close"] < level)
    tested_recently = any(row["high"] >= level * 0.995 and row["low"] <= level * 1.005 for row in recent)
    crossed_from_below = any(row["close"] < level for row in rows[-15:-3]) and closes_above >= 2
    rejected_from_level = tested_recently and closes_below >= 2 and latest_close < level

    evidence = [
        f"Nearest {label}: {_money(level)}; latest close {_money(latest_close)} ({distance_percent:+.2f}%).",
        f"Recent closes above/below level: {closes_above}/3 above, {closes_below}/3 below.",
    ]
    if volume_ratio is not None:
        evidence.append(f"Recent volume is {volume_ratio:.2f}x the prior comparison window.")

    if crossed_from_below and latest_close > level and (volume_ratio is None or volume_ratio >= 1.05):
        read = "absorption"
        status_label = "Supply absorption"
        score = 74.0 + (8.0 if volume_ratio is not None and volume_ratio >= 1.30 else 0.0)
        evidence.append("Price crossed and held above the filing-derived level; volume is not contradicting absorption.")
    elif rejected_from_level:
        read = "rejection"
        status_label = "Supply rejection"
        score = 28.0 - (8.0 if volume_ratio is not None and volume_ratio >= 1.20 else 0.0)
        evidence.append("Price tested the filing-derived level and closed back below it.")
    elif abs(distance_percent) <= 5.0:
        read = "watch"
        status_label = "Supply watch"
        score = 50.0
        evidence.append("Price is near the filing-derived level; wait for absorption or rejection evidence.")
    elif latest_close > level:
        read = "above_level"
        status_label = "Above supply level"
        score = 62.0
        evidence.append("Price is above the filing-derived level, but the crossing/volume evidence is not strong enough for an absorption read.")
    else:
        read = "below_level"
        status_label = "Below supply level"
        score = 42.0
        evidence.append("Price remains below the filing-derived level.")

    confirmation = [
        f"Confirmation: hold above {_money(level)} for multiple closes with at least normal volume.",
        "Confirmation: VWAP/volume participation improves the absorption read.",
    ]
    invalidation = [
        f"Invalidation: lose {_money(level)} after testing it, especially on expanding volume.",
        "Invalidation: fresh offering/resale/conversion filing updates the supply level or adds overhang.",
    ]
    warnings = []
    if read in {"watch", "below_level", "rejection"}:
        warnings.append("Filing-derived supply is not absorbed yet; avoid treating the level as support, resistance, target, or a prediction.")
    return SupplyAbsorptionRead(
        read=read,
        label=status_label,
        score=round(_clamp(score, 0.0, 100.0), 2),
        level=level,
        level_label=label,
        distance_percent=round(distance_percent, 2),
        evidence_lines=tuple(evidence),
        confirmation_lines=tuple(confirmation),
        invalidation_lines=tuple(invalidation),
        warnings=tuple(warnings),
    )


def regime_read(command_center_report: Any | None) -> tuple[str, list[str]]:
    classification = _get(command_center_report, "setup_classification")
    regime = str(_get(classification, "regime") or "").lower()
    setup = str(_get(classification, "setup") or "").lower()
    timing = str(_get(classification, "timing") or "").lower()
    action_quality = str(_get(classification, "action_quality") or "").lower()
    warnings: list[str] = []
    label_parts = [part for part in (regime, setup, timing) if part]
    label = " / ".join(label_parts) if label_parts else "unknown"
    if any(term in label for term in ("bearish", "breakdown", "failed")):
        warnings.append("Regime warning: bearish/breakdown context shrinks bullish evidence confidence.")
    if any(term in label for term in ("chop", "range", "unknown")):
        warnings.append("Regime warning: choppy or unknown regime reduces confidence in replay analogs.")
    if any(term in action_quality for term in ("avoid", "protect", "no_edge")):
        warnings.append(f"Regime warning: action quality is {action_quality.replace('_', ' ') or 'weak'}.")
    snapshot_values = _dict_values(_get(command_center_report, "snapshots"))
    vol_percentiles = [_to_float(_get(snapshot, "vol_regime_percentile")) for snapshot in snapshot_values]
    if any(value is not None and value >= 80 for value in vol_percentiles):
        warnings.append("Regime warning: elevated realized-volatility regime increases outcome dispersion.")
    return label, _dedupe(warnings)


def shrink_confidence_score(
    *,
    raw_evidence_score: float,
    data_confidence_score: float | None,
    replay: SetupReplayRead,
    catalyst: CatalystCollisionRead,
    option_move: OptionRequiredMoveRead | None,
    supply: SupplyAbsorptionRead,
    regime_warnings: Sequence[str] = (),
) -> ConfidenceShrinkageRead:
    raw = _clamp(float(raw_evidence_score), 0.0, 100.0)
    data = _clamp(float(data_confidence_score), 0.0, 100.0) if data_confidence_score is not None else None
    factors: list[str] = []
    warnings: list[str] = []

    data_factor = 0.62 if data is None else _clamp(data / 100.0, 0.35, 1.0)
    if data is None:
        factors.append("data confidence missing x0.62")
        warnings.append("Confidence shrinkage used a fallback because data confidence was missing.")
    elif data < 60:
        factors.append(f"data confidence {data:.0f}/100 x{data_factor:.2f}")
    else:
        factors.append(f"data confidence {data:.0f}/100 x{data_factor:.2f}")

    sample_factor = _clamp(0.34 + replay.confidence * 0.66, 0.34, 1.0)
    factors.append(f"setup replay confidence x{sample_factor:.2f}")
    if replay.sample_count < 10:
        warnings.append("Confidence shrinkage applied a low-sample replay penalty.")

    catalyst_factor = _clamp(1.0 - catalyst.score * 0.0032, 0.58, 1.0)
    factors.append(f"catalyst collision {catalyst.score:.0f}/100 x{catalyst_factor:.2f}")

    regime_factor = 0.84 if regime_warnings else 1.0
    factors.append(f"regime x{regime_factor:.2f}")

    option_factor = 1.0
    if option_move is not None and option_move.status == "bad":
        option_factor = 0.88
        warnings.append("Confidence shrinkage applied an option required-move penalty.")
    factors.append(f"option hurdle x{option_factor:.2f}")

    supply_factor = 1.0
    if supply.read == "rejection":
        supply_factor = 0.82
        warnings.append("Confidence shrinkage applied a supply rejection penalty.")
    elif supply.read in {"watch", "below_level"}:
        supply_factor = 0.92
    elif supply.read == "absorption":
        supply_factor = 1.0
    factors.append(f"supply absorption x{supply_factor:.2f}")

    shrink = _clamp(data_factor * sample_factor * catalyst_factor * regime_factor * option_factor * supply_factor, 0.20, 1.0)
    adjusted = _clamp(50.0 + (raw - 50.0) * shrink, 0.0, 100.0)
    return ConfidenceShrinkageRead(
        raw_evidence_score=round(raw, 2),
        confidence_adjusted_score=round(adjusted, 2),
        shrink_factor=round(shrink, 4),
        factors=tuple(factors),
        warnings=tuple(_dedupe(warnings)),
    )


def empirical_readout_lines(read: EmpiricalRecommendationIntelligenceRead | None) -> list[str]:
    if read is None:
        return ["Empirical Recommendation Intelligence is unavailable for this payload."]
    lines = [
        f"Raw evidence score: {read.raw_evidence_score:.0f}/100.",
        f"Confidence-adjusted score: {read.confidence_adjusted_score:.0f}/100 (shrink factor {read.shrinkage.shrink_factor:.2f}).",
        f"Setup Replay: {read.setup_replay.summary}",
        f"Catalyst Collision: {read.catalyst_collision.summary}",
        f"Supply Absorption: {read.supply_absorption.label}. {' '.join(read.supply_absorption.evidence_lines[:2])}",
    ]
    if read.option_required_move is not None:
        lines.append(f"Option Required vs Implied: {read.option_required_move.label}. {' '.join(read.option_required_move.lines[:2])}")
    if read.regime_warnings:
        lines.extend(read.regime_warnings[:3])
    lines.extend(read.recommendation_lines[:5])
    return _dedupe(lines)


def _empirical_recommendation_lines(
    *,
    replay: SetupReplayRead,
    catalyst: CatalystCollisionRead,
    option_move: OptionRequiredMoveRead | None,
    supply: SupplyAbsorptionRead,
    shrinkage: ConfidenceShrinkageRead,
    regime_label: str,
) -> list[str]:
    lines = [
        f"Raw evidence {shrinkage.raw_evidence_score:.0f}/100 shrinks to {shrinkage.confidence_adjusted_score:.0f}/100 after empirical confidence controls.",
        f"Setup replay: {replay.label}; {replay.summary}",
        f"Catalyst collision: {catalyst.label} ({catalyst.score:.0f}/100).",
        f"Regime context: {regime_label}.",
    ]
    if option_move is not None:
        lines.append(f"Option hurdle: {option_move.label}.")
    lines.append(f"Supply read: {supply.label}.")
    return _dedupe(lines)


def _empirical_confirmation_lines(
    replay: SetupReplayRead,
    supply: SupplyAbsorptionRead,
    option_move: OptionRequiredMoveRead | None,
) -> list[str]:
    lines = []
    if replay.sample_count >= 10 and replay.raw_score >= 60:
        lines.append("Empirical confirmation: current setup continues to resemble historically positive same-symbol windows.")
    elif replay.sample_count < 10:
        lines.append("Empirical confirmation: collect more local candle history before upgrading the replay read.")
    if supply.read == "absorption":
        lines.append("Empirical confirmation: filing-derived supply appears absorbed only while price holds above the parsed level.")
    if option_move is not None and option_move.status in {"good", "mixed"} and option_move.strategy_kind in {"long_call", "long_put"}:
        lines.append("Empirical confirmation: option hurdle remains acceptable only if price moves toward breakeven inside the implied range.")
    return lines


def _empirical_invalidation_lines(
    replay: SetupReplayRead,
    supply: SupplyAbsorptionRead,
    option_move: OptionRequiredMoveRead | None,
) -> list[str]:
    lines = []
    if replay.sample_count >= 10 and replay.raw_score <= 42:
        lines.append("Empirical invalidation: same-symbol replay windows skew negative; do not override this without fresh confirmation.")
    if supply.read in {"watch", "rejection", "below_level"}:
        lines.append("Empirical invalidation: filing-derived supply is not absorbed yet.")
    if option_move is not None and option_move.status == "bad":
        lines.append("Empirical invalidation: required option move exceeds implied move; debit hurdle is demanding.")
    return lines


def _window_features(rows: Sequence[dict[str, float]]) -> dict[str, float] | None:
    if len(rows) < 5:
        return None
    closes = [row["close"] for row in rows if row["close"] > 0]
    highs = [row["high"] for row in rows if row["high"] > 0]
    lows = [row["low"] for row in rows if row["low"] > 0]
    volumes = [max(row["volume"], 0.0) for row in rows]
    if len(closes) != len(rows) or not highs or not lows:
        return None
    returns = [(closes[index] / closes[index - 1] - 1.0) for index in range(1, len(closes)) if closes[index - 1] > 0]
    high = max(highs)
    low = min(lows)
    spread = max(high - low, 0.000001)
    first_close = closes[0]
    last_close = closes[-1]
    full_volume = _mean(volumes)
    tail_volume = _mean(volumes[-5:])
    return {
        "total_return": last_close / first_close - 1.0,
        "early_return": closes[min(4, len(closes) - 1)] / first_close - 1.0,
        "late_return": last_close / closes[max(0, len(closes) - 6)] - 1.0,
        "volatility": statistics.pstdev(returns) if len(returns) >= 2 else 0.0,
        "close_location": (last_close - low) / spread,
        "range_pct": spread / max(last_close, 0.000001),
        "volume_ratio": tail_volume / full_volume if full_volume > 0 else 1.0,
    }


def _feature_similarity(current: dict[str, float], candidate: dict[str, float]) -> float:
    scales = {
        "total_return": 0.14,
        "early_return": 0.10,
        "late_return": 0.08,
        "volatility": 0.035,
        "close_location": 0.42,
        "range_pct": 0.10,
        "volume_ratio": 0.85,
    }
    distance = 0.0
    for key, scale in scales.items():
        distance += min(abs(current.get(key, 0.0) - candidate.get(key, 0.0)) / scale, 3.0)
    normalized = distance / max(len(scales), 1)
    return _clamp(math.exp(-normalized), 0.0, 1.0)


def _outcome_label(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value >= 0.03:
        return "positive"
    if value <= -0.03:
        return "negative"
    return "mixed"


def _macro_collision_events(macro_snapshot: Any | None, *, as_of: datetime | date | None) -> list[str]:
    releases = _list(_get(macro_snapshot, "releases"))
    if not releases:
        return []
    active_now = _as_datetime(as_of)
    events: list[str] = []
    for release in releases:
        metric = str(_get(release, "metric") or _get(release, "category") or "Macro release")
        timestamp = str(_get(release, "release_timestamp") or "")
        freshness = str(_get(release, "freshness_status") or "").lower()
        parsed = _parse_datetime(timestamp)
        if parsed is not None:
            days = abs((active_now - parsed).total_seconds()) / 86400.0
            if days <= 3:
                events.append(f"Macro event: {metric} release is within {days:.1f} day(s) of the analysis timestamp.")
                continue
        if "fresh" in freshness:
            events.append(f"Macro event: fresh {metric} release is loaded.")
    return _dedupe(events)


def _required_move_pct(option_type: str, breakeven: float | None, underlying: float | None) -> float | None:
    if breakeven is None or underlying is None or underlying <= 0:
        return None
    if option_type == "call":
        return max(0.0, (breakeven - underlying) / underlying)
    if option_type == "put":
        return max(0.0, (underlying - breakeven) / underlying)
    return None


def _implied_move_pct(candidate: Any | None) -> float | None:
    iv = _to_float(_get(candidate, "iv"))
    if iv is None:
        iv = _to_float(_get(candidate, "impliedVolatility")) or _to_float(_get(candidate, "implied_volatility"))
    dte = _to_float(_get(candidate, "dte"))
    if iv is None or iv <= 0 or dte is None or dte <= 0:
        return None
    if iv > 1.5:
        iv /= 100.0
    return max(0.0, iv * math.sqrt(dte / 365.0))


def _option_strategy_kind(strategy: str, group: str, option_type: str) -> str:
    text = f"{strategy} {group}".lower()
    if "collar" in text:
        return "collar"
    if option_type == "call" and "covered" in text:
        return "covered_call"
    if option_type == "call":
        return "long_call"
    if option_type == "put":
        return "long_put"
    return "no_trade"


def _best_option_candidate(candidates: Sequence[Any]) -> Any | None:
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: _to_float(_get(item, "score")) or 0.0, reverse=True)[0]


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


def _capital_pressure_context_indicator(report: Any | None) -> Any | None:
    if report is None:
        return None
    read = str(_get(report, "read") or "").strip()
    if read.lower() == "unknown" and not _capital_pressure_is_no_parsed_context(report):
        return None
    levels = _list(_get(report, "possible_supply_levels"))
    nearest = levels[0] if levels else None
    level = _to_float(_get(nearest, "price"))
    level_label = _get(nearest, "label") if nearest is not None else None
    parsed_count = _capital_pressure_parsed_term_count(report)
    if parsed_count == 0 and not levels:
        context_read = "no_parsed_level"
    elif read.lower() == "high":
        context_read = "dilution_sensitive"
    elif read.lower() == "low":
        context_read = "clean"
    else:
        context_read = "supply_context"
    return SimpleNamespace(
        read=context_read,
        chase_risk_score=0.0,
        nearest_supply_level=level,
        nearest_supply_level_label=level_label,
        warnings=() if context_read == "no_parsed_level" else tuple(_list(_get(report, "warnings"))),
    )


def _capital_pressure_is_no_parsed_context(report: Any | None) -> bool:
    warnings = " ".join(str(item or "").lower() for item in _list(_get(report, "warnings")))
    return "no recent capital-structure sec filing forms" in warnings or (_to_float(_get(report, "filings_analyzed")) or 0.0) > 0


def _capital_pressure_parsed_term_count(report: Any | None) -> int:
    parsed = _get(report, "parsed_terms")
    total = 0
    for key in (
        "common_share_classes",
        "preferred_series",
        "warrants",
        "convertibles",
        "offering_programs",
        "ads_adr_structures",
    ):
        total += len(_list(_get(parsed, key)))
    total += len(_list(_get(report, "signals")))
    return total


def _candle_row(candle: Any) -> dict[str, float] | None:
    open_price = _to_float(_get(candle, "open"))
    high = _to_float(_get(candle, "high"))
    low = _to_float(_get(candle, "low"))
    close = _to_float(_get(candle, "close"))
    volume = _to_float(_get(candle, "volume")) or 0.0
    if open_price is None or high is None or low is None or close is None:
        return None
    if min(open_price, high, low, close) <= 0:
        return None
    return {"open": open_price, "high": high, "low": low, "close": close, "volume": volume}


def _as_datetime(value: datetime | date | None) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _get(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _dict_values(value: Any) -> list[Any]:
    return list(value.values()) if isinstance(value, dict) else []


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").replace("%", "")
        number = float(value)
        if not math.isfinite(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _mean(values: Iterable[float]) -> float:
    rows = [float(value) for value in values if value is not None]
    return sum(rows) / len(rows) if rows else 0.0


def _mean_or_none(values: Iterable[float]) -> float | None:
    rows = [float(value) for value in values if value is not None]
    return round(sum(rows) / len(rows), 4) if rows else None


def _money(value: Any) -> str:
    number = _to_float(value)
    return "--" if number is None else f"${number:,.2f}"


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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
