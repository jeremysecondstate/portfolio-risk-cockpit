from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
from statistics import median
from typing import Any


ACTIVE_SET_TARGET = 24
ZERO_EPSILON = 0.00000001
CHAIN_HEALTH_HISTORY_PATH = Path("data") / "hyperliquid_chain_health_history.jsonl"
HISTORICAL_EVIDENCE_MIN_OBSERVATIONS = 30


@dataclass(frozen=True)
class HyperliquidValidatorHealthSnapshot:
    fetched_at: datetime
    validator_summaries: list[dict[str, Any]]
    validator_stats: Any
    validator_l1_votes: Any
    exchange_status: Any | None
    all_mids_ok: bool | None
    all_mids: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raw_validator_summaries: Any | None = None


@dataclass(frozen=True)
class HyperliquidChainHealthAssessment:
    temperature: str
    score: int | None
    headline: str
    key_metrics: dict[str, Any]
    warnings: list[str]
    criticals: list[str]
    counterfactuals: list[str]
    raw_data_notes: list[str]


@dataclass(frozen=True)
class HyperliquidMarketImpactRead:
    execution_risk: str
    liquidity_confidence: str
    hype_price_pressure: str
    evidence_quality: str
    trading_posture: str
    hypothesis: str
    watch_next: list[str]


def normalize_validator_summaries_payload(payload: Any) -> list[dict[str, Any]]:
    return [record for record in _extract_records(payload, ("validators", "validatorSummaries", "summaries", "data", "result")) if isinstance(record, dict)]


def assess_hyperliquid_chain_health(snapshot: HyperliquidValidatorHealthSnapshot) -> HyperliquidChainHealthAssessment:
    warnings = _unique_lines(snapshot.warnings)
    criticals: list[str] = []
    raw_data_notes = _raw_shape_notes(snapshot)
    validators = [validator for validator in snapshot.validator_summaries if isinstance(validator, dict)]
    score = 100

    if not validators:
        _append_unique(warnings, "validatorSummaries unavailable; chain health score cannot make a real validator-set call.")
        return HyperliquidChainHealthAssessment(
            temperature="UNKNOWN",
            score=None,
            headline="Validator data was not sufficient for a real Hyperliquid chain health call.",
            key_metrics=_empty_metrics(snapshot),
            warnings=warnings,
            criticals=criticals,
            counterfactuals=[
                "If validatorSummaries loads on the next run, the cockpit can score active-set depth, jailing, stake concentration, and metadata quality.",
                "If all validator endpoints remain unavailable while allMids also fails, treat the info API path itself as degraded until confirmed elsewhere.",
                "If validatorStats and validatorL1Votes are still missing after summaries load, the score will exclude performance and vote-participation evidence.",
            ],
            raw_data_notes=raw_data_notes or ["validatorSummaries returned no usable validator objects."],
        )

    rows = _validator_rows(validators)
    positive_rows = [row for row in rows if row["stake"] > ZERO_EPSILON]
    top_rows = sorted(positive_rows, key=lambda row: row["stake"], reverse=True)[:ACTIVE_SET_TARGET]
    active_stake = sum(row["stake"] for row in top_rows)
    total_stake = sum(row["stake"] for row in positive_rows)
    top_24_count = len(top_rows)
    jailed_total = sum(1 for row in rows if row["jailed"])
    jailed_top24 = sum(1 for row in top_rows if row["jailed"])
    inactive_top24 = sum(1 for row in top_rows if row["inactive"])

    if not positive_rows:
        criticals.append("Validator summary objects did not expose usable positive stake.")
        raw_data_notes.append("Known stake keys were not found in validatorSummaries; active-set and concentration metrics are unreliable.")
        score -= 45
    elif len(positive_rows) < ACTIVE_SET_TARGET:
        _append_unique(warnings, f"Only {len(positive_rows)} validators exposed positive usable stake; active-set target is {ACTIVE_SET_TARGET}.")
        score -= 35 if len(positive_rows) < 18 else 22

    if jailed_top24:
        criticals.append(f"{jailed_top24} validator(s) in the top-24 stake approximation appear jailed.")
        score -= min(60, jailed_top24 * 25)
    if inactive_top24:
        _append_unique(warnings, f"{inactive_top24} top-24 validator(s) appear inactive or undelegate-only.")
        score -= min(30, inactive_top24 * 12)
    outside_jailed = max(0, jailed_total - jailed_top24)
    if outside_jailed >= 8:
        _append_unique(warnings, f"{outside_jailed} jailed validator(s) were detected outside the top 24.")
        score -= 6
    elif outside_jailed >= 3:
        _append_unique(warnings, f"{outside_jailed} jailed validator(s) were detected outside the top 24.")
        score -= 3
    if jailed_total:
        _append_unique(
            warnings,
            "Jailing removes a validator from consensus/rewards, but this read does not treat jailing as the same thing as slashing.",
        )

    concentration = _stake_concentration(top_rows, active_stake)
    score -= _apply_concentration_flags(concentration, warnings, criticals)
    score -= _apply_commission_flags(rows, top_rows, warnings)
    score -= _apply_metadata_flags(rows, warnings)

    if snapshot.all_mids_ok is False:
        _append_unique(warnings, "allMids sanity check failed or returned an unexpected shape; API confidence is lower.")
        score -= 5
    elif snapshot.all_mids_ok is None:
        _append_unique(warnings, "allMids sanity check was not checked.")

    exchange_penalty, exchange_read = _exchange_status_penalty(snapshot.exchange_status, warnings, criticals)
    score -= exchange_penalty

    stats_metrics, stats_warnings, stats_criticals, stats_penalty, stats_notes = _summarize_validator_stats(
        snapshot.validator_stats,
        top_rows,
    )
    score -= stats_penalty
    for line in stats_warnings:
        _append_unique(warnings, line)
    criticals.extend(line for line in stats_criticals if line not in criticals)
    raw_data_notes.extend(line for line in stats_notes if line not in raw_data_notes)

    vote_metrics, vote_warnings, vote_criticals, vote_penalty, vote_notes = _summarize_l1_votes(
        snapshot.validator_l1_votes,
        top_rows,
    )
    score -= vote_penalty
    for line in vote_warnings:
        _append_unique(warnings, line)
    criticals.extend(line for line in vote_criticals if line not in criticals)
    raw_data_notes.extend(line for line in vote_notes if line not in raw_data_notes)

    score = max(0, min(100, int(round(score))))
    temperature = _temperature(score, criticals)
    stake_display = _stake_display_context(rows, active_stake, total_stake)
    if stake_display["raw_data_note"]:
        raw_data_notes.append(stake_display["raw_data_note"])
    health_score = _chain_operating_health_score(
        positive_validator_count=len(positive_rows),
        jailed_top24=jailed_top24,
        inactive_top24=inactive_top24,
        all_mids_ok=snapshot.all_mids_ok,
        exchange_status_read=exchange_read,
    )
    decentralization_score = _decentralization_score(concentration)
    confidence_score = _data_confidence_score(
        has_summaries=bool(validators),
        has_stats=snapshot.validator_stats is not None,
        has_l1_votes=snapshot.validator_l1_votes is not None,
        all_mids_ok=snapshot.all_mids_ok,
        exchange_status=snapshot.exchange_status,
    )
    key_metrics = {
        "validator_count": len(validators),
        "active_set_target": ACTIVE_SET_TARGET,
        "validators_with_positive_stake": len(positive_rows),
        "top24_active_approximation": top_24_count,
        "total_stake": total_stake,
        "active_stake": active_stake,
        "total_stake_display": stake_display["total_stake_display"],
        "active_stake_display": stake_display["active_stake_display"],
        "stake_unit_label": stake_display["unit_label"],
        "stake_scale": stake_display["scale"],
        "stake_scale_source": stake_display["scale_source"],
        "jailed_total": jailed_total,
        "jailed_top24": jailed_top24,
        "inactive_top24": inactive_top24,
        "outside_jailed": outside_jailed,
        "chain_operating_health_score": health_score,
        "decentralization_score": decentralization_score,
        "data_confidence_score": confidence_score,
        "exchange_status_read": exchange_read,
        "validator_stats_loaded": snapshot.validator_stats is not None,
        "validator_l1_votes_loaded": snapshot.validator_l1_votes is not None,
        "all_mids_ok": snapshot.all_mids_ok,
        **concentration,
        **stats_metrics,
        **vote_metrics,
    }
    return HyperliquidChainHealthAssessment(
        temperature=temperature,
        score=score,
        headline=_headline(temperature, criticals, warnings),
        key_metrics=key_metrics,
        warnings=warnings,
        criticals=criticals,
        counterfactuals=_counterfactuals(top_rows, positive_rows, jailed_total, concentration),
        raw_data_notes=_unique_lines(raw_data_notes),
    )


def format_hyperliquid_chain_health_report(
    snapshot: HyperliquidValidatorHealthSnapshot,
    assessment: HyperliquidChainHealthAssessment,
) -> str:
    metrics = assessment.key_metrics
    score_text = "--" if assessment.score is None else f"{assessment.score} / 100"
    lines = [
        "HYPERLIQUID CHAIN HEALTH",
        "========================",
        f"Temperature: {assessment.temperature}",
        f"Score: {score_text}",
        f"Headline: {assessment.headline}",
        "",
        "Data coverage:",
        f"- validatorSummaries: {'loaded' if snapshot.validator_summaries else 'unavailable'}, {len(snapshot.validator_summaries)} validators",
        f"- validatorStats: {_loaded_label(snapshot.validator_stats)}",
        f"- validatorL1Votes: {_loaded_label(snapshot.validator_l1_votes)}",
        f"- exchangeStatus: {_loaded_label(snapshot.exchange_status)}",
        f"- allMids sanity check: {_all_mids_label(snapshot.all_mids_ok)}",
        f"- Fetched: {snapshot.fetched_at.strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "Validator set:",
        f"- Active set target: {metrics.get('active_set_target', ACTIVE_SET_TARGET)}",
        f"- Validators with positive stake: {metrics.get('validators_with_positive_stake', 0)}",
        f"- Top-24 active approximation: {metrics.get('top24_active_approximation', 0)}",
        f"- Jailed validators: {metrics.get('jailed_total', 0)} total, {metrics.get('jailed_top24', 0)} in top 24",
        f"- Active stake: {_format_stake_display(metrics.get('active_stake_display'), metrics.get('stake_unit_label'))}",
        "",
        "Stake concentration:",
        f"- Top 1: {_format_percent(metrics.get('top1_pct'))}",
        f"- Top 3: {_format_percent(metrics.get('top3_pct'))}",
        f"- Top 5: {_format_percent(metrics.get('top5_pct'))}",
        f"- Top 10: {_format_percent(metrics.get('top10_pct'))}",
        f"- Validators needed to exceed 1/3 active stake: {_format_optional_int(metrics.get('validators_to_exceed_one_third'))}",
        f"- Validators needed to exceed 2/3 active stake: {_format_optional_int(metrics.get('validators_to_exceed_two_thirds'))}",
        f"- HHI concentration index: {_format_number(metrics.get('hhi'))}",
        "",
        "Performance / liveness:",
        f"- Best available performance fields: {_list_or_none(metrics.get('performance_fields'))}",
        f"- Worst flagged validators: {_list_or_none(metrics.get('worst_performers'))}",
        f"- Missing metrics: {_list_or_none(metrics.get('missing_metrics'))}",
        "",
        "L1 votes:",
        f"- Vote records parsed: {metrics.get('l1_vote_records', 0)}",
        f"- Non-participants detected: {metrics.get('l1_vote_non_participants', 0)}",
        f"- Vote power top 1 / top 3: {_format_percent(metrics.get('l1_vote_top1_pct'))} / {_format_percent(metrics.get('l1_vote_top3_pct'))}",
        "",
        "Warnings:",
    ]
    lines.extend(_bullets_or_none(assessment.warnings))
    lines.extend(["", "Criticals:"])
    lines.extend(_bullets_or_none(assessment.criticals))
    lines.extend(["", "Counterfactuals:"])
    lines.extend(_bullets_or_none(assessment.counterfactuals))
    if assessment.raw_data_notes:
        lines.extend(["", "Raw data notes:"])
        lines.extend(_bullets_or_none(assessment.raw_data_notes))
    lines.extend(
        [
            "",
            "Bottom line:",
            f"- {_bottom_line(assessment)}",
            "- This is infrastructure/risk context only, not a recommendation to buy, sell, open, close, or size a position.",
        ]
    )
    return "\n".join(lines)


def format_hyperliquid_chain_health_human_report(
    snapshot: HyperliquidValidatorHealthSnapshot,
    assessment: HyperliquidChainHealthAssessment,
) -> str:
    metrics = assessment.key_metrics
    market = build_hyperliquid_market_impact_read(snapshot, assessment)
    score_text = "--" if assessment.score is None else f"{assessment.score}/100"
    concentration_status = _concentration_status(metrics)
    validator_set_status = _validator_set_status(metrics)
    jailed_status = _jailed_status(metrics)
    data_status = _data_confidence_status(metrics)
    market_status = _market_evidence_status(market)

    lines = [
        "HYPERLIQUID CHAIN VIBE CHECK",
        "============================",
        "",
        f"Vibe: {assessment.temperature} - {_vibe_word(assessment.temperature, market.trading_posture)}",
        f"Score: {score_text}",
        "",
        "Plain-English read:",
        _plain_english_read(assessment, market),
        "",
        "What this means:",
    ]
    lines.extend(f"- {line}" for line in plain_english_chain_health_explanations(assessment))
    lines.extend(
        [
            "",
            "Trading impact:",
            f"- Execution risk: {_title_label(market.execution_risk)}",
            f"- Liquidity confidence: {_title_label(market.liquidity_confidence)}",
            f"- HYPE price pressure from validator data alone: {_title_label(market.hype_price_pressure)}",
            f"- Overall trading posture: {_title_label(market.trading_posture)}",
            "- This is infrastructure/risk context, not a buy/sell recommendation.",
            "",
            "Market / price-impact hypothesis:",
            market.hypothesis,
            "",
            "What would change the read:",
            "- Greener if validatorStats loads, top validators look healthy, jailed count stays outside the main squad, and public API checks remain normal.",
            "- Redder if top-24 validators are jailed/offline, allMids or exchangeStatus degrades, or the top few validators become even more dominant.",
            "- More market-relevant if validator issues coincide with HYPE price weakness, falling liquidity/open interest, wider spreads, or negative news.",
            "",
            "Simple scorecard:",
            f"- Main validator set: {validator_set_status}",
            f"- Jailed validators: {jailed_status}",
            f"- Stake concentration: {concentration_status}",
            f"- API/data confidence: {data_status}",
            f"- Market impact evidence: {market_status}",
            "",
            "Split scores:",
            f"- Chain operating health: {_format_score(metrics.get('chain_operating_health_score'))}",
            f"- Validator concentration / decentralization: {_format_score(metrics.get('decentralization_score'))}",
            f"- Data confidence: {_format_score(metrics.get('data_confidence_score'))}",
            "",
            "Key numbers, translated:",
            f"- Validators found: {metrics.get('validator_count', 0)}",
            f"- Active validator target: {metrics.get('active_set_target', ACTIVE_SET_TARGET)}",
            f"- Main squad approximation: {metrics.get('top24_active_approximation', 0)} of {ACTIVE_SET_TARGET}",
            f"- Jailed validators: {metrics.get('jailed_total', 0)} total, {metrics.get('jailed_top24', 0)} in the top 24",
            f"- Active stake display: {_format_stake_display(metrics.get('active_stake_display'), metrics.get('stake_unit_label'))}",
            f"- Top 3 validators control: {_format_percent(metrics.get('top3_pct'))} of active stake",
            f"- Top 5 validators control: {_format_percent(metrics.get('top5_pct'))} of active stake",
            f"- Validators needed to control more than 1/3: {_format_optional_int(metrics.get('validators_to_exceed_one_third'))}",
            f"- Validators needed to control more than 2/3: {_format_optional_int(metrics.get('validators_to_exceed_two_thirds'))}",
            "",
            "What to watch next:",
        ]
    )
    lines.extend(f"- {line}" for line in market.watch_next)
    lines.extend(
        [
            "",
            "Historical evidence: not enough yet",
            _historical_evidence_paragraph(),
            "",
            "Bottom line:",
            _human_bottom_line(assessment, market),
        ]
    )
    if assessment.raw_data_notes:
        lines.extend(["", "Raw data notes:"])
        lines.extend(f"- {line}" for line in assessment.raw_data_notes)
    return "\n".join(lines)


def plain_english_chain_health_explanations(assessment: HyperliquidChainHealthAssessment) -> list[str]:
    missing_metrics = set(assessment.key_metrics.get("missing_metrics") or [])
    lines = [
        "A validator is a computer/operator helping run the chain.",
        "The top 24 are the main squad currently running the chain by stake.",
        "A jailed validator is benched because it was not behaving or responding correctly.",
        "Stake concentration means a few operators have a lot of the control.",
    ]
    if "validatorStats" in missing_metrics or not assessment.key_metrics.get("validator_stats_loaded"):
        lines.append("validatorStats missing means we cannot see the detailed fitness tracker for each validator.")
    if assessment.key_metrics.get("jailed_top24", 0) == 0:
        lines.append("No jailed validator was found in the main squad, so the active set appears intact from summary data.")
    return lines


def build_hyperliquid_market_impact_read(
    snapshot: HyperliquidValidatorHealthSnapshot,
    assessment: HyperliquidChainHealthAssessment,
) -> HyperliquidMarketImpactRead:
    metrics = assessment.key_metrics
    if assessment.temperature == "UNKNOWN" or not metrics.get("validator_count"):
        return HyperliquidMarketImpactRead(
            execution_risk="UNKNOWN",
            liquidity_confidence="UNKNOWN",
            hype_price_pressure="UNKNOWN",
            evidence_quality="WEAK",
            trading_posture="NO_READ",
            hypothesis=(
                "Validator data was not complete enough to judge chain conditions. That is a no-read, not proof that "
                "the chain is healthy or broken."
            ),
            watch_next=[
                "Rerun the check after validatorSummaries is available.",
                "Confirm allMids and exchangeStatus are loading.",
                "Use external status/news sources if the API remains unavailable.",
            ],
        )

    jailed_top24 = int(metrics.get("jailed_top24", 0) or 0)
    inactive_top24 = int(metrics.get("inactive_top24", 0) or 0)
    outside_jailed = int(metrics.get("outside_jailed", 0) or 0)
    data_confidence = float(metrics.get("data_confidence_score", 0) or 0)
    concentration = _concentration_status(metrics)
    exchange_read = str(metrics.get("exchange_status_read") or "").lower()
    api_degraded = snapshot.all_mids_ok is False or any(token in exchange_read for token in ("halt", "offline", "outage", "down", "degraded", "maintenance"))

    if jailed_top24 or inactive_top24 or (api_degraded and concentration == "Bad"):
        execution_risk = "HIGH"
    elif api_degraded or concentration in {"Warning", "Bad"}:
        execution_risk = "MEDIUM"
    else:
        execution_risk = "LOW"

    if snapshot.all_mids_ok is False or jailed_top24:
        liquidity_confidence = "LOW"
    elif data_confidence < 70 or api_degraded or concentration in {"Warning", "Bad"}:
        liquidity_confidence = "MEDIUM"
    else:
        liquidity_confidence = "HIGH"

    if jailed_top24 and api_degraded:
        hype_price_pressure = "MEDIUM"
    else:
        hype_price_pressure = "LOW"

    if data_confidence >= 85 and metrics.get("validator_stats_loaded") and metrics.get("validator_l1_votes_loaded"):
        evidence_quality = "MEDIUM"
    else:
        evidence_quality = "WEAK"

    if jailed_top24 or inactive_top24 or snapshot.all_mids_ok is False:
        trading_posture = "DEFENSIVE"
    elif concentration in {"Warning", "Bad"} or outside_jailed or data_confidence < 85:
        trading_posture = "CAUTIOUS"
    else:
        trading_posture = "NORMAL"

    hypothesis = (
        "Validator concentration by itself does not mean price goes down. It becomes price-relevant if it turns into "
        "user-visible problems: downtime, delayed trading, failed transactions, social panic, or major validator failures. "
        "Current read: caution flag, not a standalone bearish HYPE signal."
    )
    watch_next = [
        "Whether any top-24 validator becomes jailed, inactive, or visibly slow.",
        "Whether validatorStats starts loading and shows strong uptime/participation.",
        "Whether allMids and exchangeStatus remain normal during busy trading periods.",
        "Whether HYPE price weakness lines up with liquidity stress, wider spreads, open-interest drops, or negative news.",
    ]
    return HyperliquidMarketImpactRead(
        execution_risk=execution_risk,
        liquidity_confidence=liquidity_confidence,
        hype_price_pressure=hype_price_pressure,
        evidence_quality=evidence_quality,
        trading_posture=trading_posture,
        hypothesis=hypothesis,
        watch_next=watch_next,
    )


def save_hyperliquid_chain_health_observation(
    snapshot: HyperliquidValidatorHealthSnapshot,
    assessment: HyperliquidChainHealthAssessment,
    path: Path | str = CHAIN_HEALTH_HISTORY_PATH,
) -> int:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics = assessment.key_metrics
    row = {
        "timestamp": snapshot.fetched_at.isoformat(),
        "temperature": assessment.temperature,
        "score": assessment.score,
        "top1_pct": metrics.get("top1_pct"),
        "top3_pct": metrics.get("top3_pct"),
        "top5_pct": metrics.get("top5_pct"),
        "top10_pct": metrics.get("top10_pct"),
        "jailed_total": metrics.get("jailed_total"),
        "jailed_top24": metrics.get("jailed_top24"),
        "missing_endpoints": _missing_endpoints(snapshot),
        "hype_mid": _hype_mid(snapshot.all_mids),
    }
    with output_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return chain_health_history_count(output_path)


def chain_health_history_count(path: Path | str = CHAIN_HEALTH_HISTORY_PATH) -> int:
    output_path = Path(path)
    if not output_path.exists():
        return 0
    try:
        with output_path.open("r", encoding="utf-8") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return 0


def _stake_display_context(rows: list[dict[str, Any]], active_stake: float, total_stake: float) -> dict[str, Any]:
    scale, source = _stake_scale(rows, total_stake)
    note = ""
    if scale is None:
        if total_stake > 1_000_000_000_000:
            unit_label = "raw stake units"
            note = (
                "Stake values look like raw smallest units, but no safe decimal scale was detected; "
                "absolute stake is labeled raw units. Concentration percentages remain valid."
            )
        else:
            unit_label = "HYPE"
        return {
            "active_stake_display": active_stake,
            "total_stake_display": total_stake,
            "unit_label": unit_label,
            "scale": None,
            "scale_source": "none",
            "raw_data_note": note,
        }

    if source.startswith("endpoint"):
        note = f"Stake display normalized using endpoint-provided decimals ({source})."
    elif source.startswith("inferred"):
        note = f"Stake values looked like raw smallest units; display normalized by {scale:g} ({source})."
    return {
        "active_stake_display": active_stake / scale,
        "total_stake_display": total_stake / scale,
        "unit_label": "HYPE",
        "scale": scale,
        "scale_source": source,
        "raw_data_note": note,
    }


def _stake_scale(rows: list[dict[str, Any]], total_stake: float) -> tuple[float | None, str]:
    max_stake = max((row.get("stake", 0.0) for row in rows), default=0.0)
    if max_stake <= 1_000_000_000 and total_stake <= 10_000_000_000:
        return None, "none"

    for row in rows:
        raw = row.get("raw")
        if not isinstance(raw, dict):
            continue
        decimals = _first_number(raw, "stakeDecimals", "stake_decimals", "tokenDecimals", "token_decimals", "decimals")
        if decimals is None:
            continue
        decimals_int = int(decimals)
        if decimals_int in {6, 8, 18}:
            return float(10**decimals_int), f"endpoint decimals={decimals_int}"

    candidates = (100_000_000.0, 1_000_000_000_000_000_000.0)
    plausible: list[tuple[float, float]] = []
    for scale in candidates:
        scaled_total = total_stake / scale
        if 1_000 <= scaled_total <= 1_000_000_000:
            plausible.append((scale, scaled_total))
    if plausible:
        scale, _scaled_total = sorted(plausible, key=lambda item: abs(item[1] - 100_000_000))[0]
        return scale, "inferred plausible HYPE supply scale"
    return None, "raw"


def _chain_operating_health_score(
    *,
    positive_validator_count: int,
    jailed_top24: int,
    inactive_top24: int,
    all_mids_ok: bool | None,
    exchange_status_read: str,
) -> int:
    score = 100
    if positive_validator_count < ACTIVE_SET_TARGET:
        score -= 30
    score -= min(60, jailed_top24 * 25)
    score -= min(30, inactive_top24 * 12)
    if all_mids_ok is False:
        score -= 15
    status = str(exchange_status_read).lower()
    if any(token in status for token in ("halt", "offline", "outage", "down")):
        score -= 35
    elif any(token in status for token in ("degraded", "maintenance", "partial", "delayed")):
        score -= 15
    return max(0, min(100, score))


def _decentralization_score(metrics: dict[str, Any]) -> int:
    score = 100
    top1 = metrics.get("top1_pct")
    top3 = metrics.get("top3_pct")
    top5 = metrics.get("top5_pct")
    one_third = metrics.get("validators_to_exceed_one_third")
    two_thirds = metrics.get("validators_to_exceed_two_thirds")
    if top1 is not None:
        score -= 25 if top1 > 25 else 10 if top1 > 15 else 0
    if top3 is not None and top3 > 33:
        score -= 15
    if top5 is not None:
        score -= 25 if top5 > 66 else 15 if top5 > 50 else 0
    if one_third is not None and one_third < 4:
        score -= 10
    if two_thirds is not None and two_thirds < 8:
        score -= 10
    return max(0, min(100, int(score)))


def _data_confidence_score(
    *,
    has_summaries: bool,
    has_stats: bool,
    has_l1_votes: bool,
    all_mids_ok: bool | None,
    exchange_status: Any,
) -> int:
    score = 100
    if not has_summaries:
        score -= 70
    if not has_stats:
        score -= 20
    if not has_l1_votes:
        score -= 10
    if all_mids_ok is False:
        score -= 15
    elif all_mids_ok is None:
        score -= 5
    if exchange_status is None:
        score -= 10
    return max(0, min(100, int(score)))


def _concentration_status(metrics: dict[str, Any]) -> str:
    top1 = metrics.get("top1_pct")
    top3 = metrics.get("top3_pct")
    top5 = metrics.get("top5_pct")
    if (top1 is not None and top1 > 25) or (top5 is not None and top5 > 66):
        return "Bad"
    if (
        (top1 is not None and top1 > 15)
        or (top3 is not None and top3 > 33)
        or (top5 is not None and top5 > 50)
        or (metrics.get("validators_to_exceed_one_third") is not None and metrics.get("validators_to_exceed_one_third") < 4)
        or (metrics.get("validators_to_exceed_two_thirds") is not None and metrics.get("validators_to_exceed_two_thirds") < 8)
    ):
        return "Warning"
    return "OK"


def _validator_set_status(metrics: dict[str, Any]) -> str:
    if metrics.get("top24_active_approximation", 0) < ACTIVE_SET_TARGET or metrics.get("jailed_top24", 0) or metrics.get("inactive_top24", 0):
        return "Bad"
    if metrics.get("validators_with_positive_stake", 0) < ACTIVE_SET_TARGET + 3:
        return "Warning"
    return "OK"


def _jailed_status(metrics: dict[str, Any]) -> str:
    if metrics.get("jailed_top24", 0):
        return "Bad"
    if metrics.get("jailed_total", 0):
        return "Warning"
    return "OK"


def _data_confidence_status(metrics: dict[str, Any]) -> str:
    score = metrics.get("data_confidence_score")
    if score is None or score < 50:
        return "Bad"
    if score < 85:
        return "Warning"
    return "OK"


def _market_evidence_status(market: HyperliquidMarketImpactRead) -> str:
    if market.evidence_quality == "STRONG":
        return "Strong"
    if market.evidence_quality == "MEDIUM":
        return "Medium"
    return "Weak"


def _plain_english_read(assessment: HyperliquidChainHealthAssessment, market: HyperliquidMarketImpactRead) -> str:
    metrics = assessment.key_metrics
    if assessment.temperature == "UNKNOWN":
        return "There is not enough validator data to make a real chain-health call."
    if metrics.get("jailed_top24", 0):
        return "The chain may still be running, but a main-squad validator appears benched. That is an infrastructure risk flag."
    if _concentration_status(metrics) in {"Warning", "Bad"}:
        return (
            "The chain does not look broken. The main active validator set appears intact, and jailed validators are outside "
            "the main squad. The main concern is concentration: a few validators control a lot of stake."
        )
    if market.trading_posture == "NORMAL":
        return "The chain looks normal from the available validator summary and API sanity checks."
    return assessment.headline


def _human_bottom_line(assessment: HyperliquidChainHealthAssessment, market: HyperliquidMarketImpactRead) -> str:
    if market.trading_posture == "NO_READ":
        return "No-read: not enough validator data to judge chain infrastructure risk."
    if market.trading_posture == "NORMAL":
        return "Normal: infrastructure risk looks low from the available data."
    if market.trading_posture == "DEFENSIVE":
        return "Defensive: infrastructure risk is elevated; avoid assuming perfect execution or liquidity."
    return (
        f"{assessment.temperature} does not mean panic. It means chain is probably operating, but do not ignore "
        "infrastructure risk before using size or leverage."
    )


def _vibe_word(temperature: str, posture: str) -> str:
    if posture == "NO_READ" or temperature == "UNKNOWN":
        return "No-read"
    if posture == "DEFENSIVE":
        return "Defensive"
    if posture == "CAUTIOUS":
        return "Caution"
    return "Normal"


def _title_label(value: str) -> str:
    return value.replace("_", " ").title()


def _format_score(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{int(value)}/100"
    except (TypeError, ValueError):
        return "--"


def _format_stake_display(value: Any, unit_label: Any) -> str:
    if value is None:
        return "--"
    unit = str(unit_label or "raw stake units")
    try:
        return f"{float(value):,.2f} {unit}"
    except (TypeError, ValueError):
        return f"-- {unit}"


def _historical_evidence_paragraph() -> str:
    count = chain_health_history_count()
    return (
        "Historical proof: not available yet. This cockpit has not collected enough chain-health snapshots to prove whether "
        f"this exact validator setup predicts HYPE price moves. Current saved observations: {count}/{HISTORICAL_EVIDENCE_MIN_OBSERVATIONS}. "
        "For now, this is a risk hypothesis, not a backtested signal."
    )


def _missing_endpoints(snapshot: HyperliquidValidatorHealthSnapshot) -> list[str]:
    missing: list[str] = []
    if not snapshot.validator_summaries:
        missing.append("validatorSummaries")
    if snapshot.validator_stats is None:
        missing.append("validatorStats")
    if snapshot.validator_l1_votes is None:
        missing.append("validatorL1Votes")
    if snapshot.exchange_status is None:
        missing.append("exchangeStatus")
    if snapshot.all_mids_ok is not True:
        missing.append("allMids")
    return missing


def _hype_mid(all_mids: dict[str, Any] | None) -> float | None:
    if not isinstance(all_mids, dict):
        return None
    for key in ("HYPE", "HYPE/USDC", "@107"):
        value = _to_float(all_mids.get(key))
        if value is not None and value > 0:
            return value
    upper = {str(key).upper(): value for key, value in all_mids.items()}
    for key in ("HYPE", "HYPE/USDC", "@107"):
        value = _to_float(upper.get(key))
        if value is not None and value > 0:
            return value
    return None


def _validator_rows(validators: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, validator in enumerate(validators, start=1):
        rows.append(
            {
                "raw": validator,
                "index": index,
                "name": _validator_name(validator) or f"validator-{index}",
                "address": _validator_address(validator),
                "stake": _validator_stake(validator) or 0.0,
                "commission": _validator_commission(validator),
                "jailed": _is_jailed_validator(validator),
                "inactive": _is_inactive_validator(validator),
                "missing_identity": _validator_name(validator) is None,
            }
        )
    return rows


def _stake_concentration(rows: list[dict[str, Any]], active_stake: float) -> dict[str, Any]:
    stakes = [row["stake"] for row in rows]
    return {
        "top1_pct": _stake_share(stakes[:1], active_stake),
        "top3_pct": _stake_share(stakes[:3], active_stake),
        "top5_pct": _stake_share(stakes[:5], active_stake),
        "top10_pct": _stake_share(stakes[:10], active_stake),
        "validators_to_exceed_one_third": _validators_to_exceed(stakes, active_stake / 3.0),
        "validators_to_exceed_two_thirds": _validators_to_exceed(stakes, active_stake * 2.0 / 3.0),
        "hhi": sum((stake / active_stake * 100.0) ** 2 for stake in stakes) if active_stake > ZERO_EPSILON else None,
    }


def _apply_concentration_flags(metrics: dict[str, Any], warnings: list[str], criticals: list[str]) -> int:
    penalty = 0
    top1 = metrics.get("top1_pct")
    top3 = metrics.get("top3_pct")
    top5 = metrics.get("top5_pct")
    one_third = metrics.get("validators_to_exceed_one_third")
    two_thirds = metrics.get("validators_to_exceed_two_thirds")

    if top1 is not None and top1 > 25.0:
        criticals.append(f"Largest validator controls {top1:.1f}% of active-set stake approximation.")
        penalty += 18
    elif top1 is not None and top1 > 15.0:
        _append_unique(warnings, f"Largest validator controls {top1:.1f}% of active-set stake approximation.")
        penalty += 6

    if top3 is not None and top3 > 33.0:
        _append_unique(warnings, f"Top 3 validators control {top3:.1f}% of active-set stake approximation.")
        penalty += 5

    if top5 is not None and top5 > 66.0:
        criticals.append(f"Top 5 validators control {top5:.1f}% of active-set stake approximation.")
        penalty += 16
    elif top5 is not None and top5 > 50.0:
        _append_unique(warnings, f"Top 5 validators control {top5:.1f}% of active-set stake approximation.")
        penalty += 5

    if one_third is not None and one_third < 4:
        _append_unique(warnings, f"Only {one_third} validator(s) are needed to exceed one-third of active-set stake.")
        penalty += 5
    if two_thirds is not None and two_thirds < 8:
        _append_unique(warnings, f"Only {two_thirds} validator(s) are needed to exceed two-thirds of active-set stake.")
        penalty += 4
    return penalty


def _apply_commission_flags(rows: list[dict[str, Any]], top_rows: list[dict[str, Any]], warnings: list[str]) -> int:
    commissions = [row["commission"] for row in rows if row["commission"] is not None]
    if not commissions:
        _append_unique(warnings, "Validator commission data unavailable; delegator-quality context is incomplete.")
        return 0

    network_median = median(commissions)
    outliers = [row for row in rows if row["commission"] is not None and row["commission"] >= 30.0]
    if outliers:
        _append_unique(warnings, f"{len(outliers)} validator(s) show commission at or above 30%.")
    top_outliers = [
        row
        for row in top_rows
        if row["commission"] is not None and row["commission"] >= max(15.0, network_median + 10.0)
    ]
    if top_outliers:
        names = ", ".join(row["name"] for row in top_outliers[:3])
        _append_unique(warnings, f"Top validators with commission materially above median ({network_median:.1f}%): {names}.")
    return min(5, len(outliers) + len(top_outliers))


def _apply_metadata_flags(rows: list[dict[str, Any]], warnings: list[str]) -> int:
    penalty = 0
    missing_identity = sum(1 for row in rows if row["missing_identity"])
    if missing_identity:
        _append_unique(warnings, f"{missing_identity} validator(s) are missing obvious name/moniker metadata.")
        penalty += 2

    names = [row["name"].strip().lower() for row in rows if not row["missing_identity"]]
    duplicate_names = len(names) - len(set(names))
    if duplicate_names:
        _append_unique(warnings, f"{duplicate_names} duplicate validator name/moniker entries were detected.")
        penalty += 2

    addresses = [row["address"].strip().lower() for row in rows if row["address"]]
    duplicate_addresses = len(addresses) - len(set(addresses))
    if duplicate_addresses:
        _append_unique(warnings, f"{duplicate_addresses} duplicate validator address entries were detected.")
        penalty += 4
    return penalty


def _summarize_validator_stats(
    payload: Any,
    top_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str], int, list[str]]:
    metrics = {
        "performance_fields": [],
        "worst_performers": [],
        "missing_metrics": [],
    }
    warnings: list[str] = []
    criticals: list[str] = []
    notes: list[str] = []
    penalty = 0

    if payload is None:
        warnings.append("validatorStats unavailable; score excludes per-validator performance metrics.")
        metrics["missing_metrics"].append("validatorStats")
        return metrics, warnings, criticals, penalty, notes

    records = _extract_records(payload, ("stats", "validatorStats", "validators", "data", "result"))
    if not records:
        warnings.append("validatorStats loaded but schema was not recognized; score excludes per-validator performance metrics.")
        metrics["missing_metrics"].append("recognized validatorStats schema")
        notes.append(f"validatorStats raw shape: {_shape_label(payload)}")
        return metrics, warnings, criticals, penalty, notes

    top_addresses = {str(row["address"]).lower() for row in top_rows if row["address"]}
    top_names = {str(row["name"]).lower() for row in top_rows if row["name"]}
    fields: set[str] = set()
    worst: list[tuple[int, str]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        label = _validator_name(record) or _validator_address(record) or "unknown validator"
        is_top = _record_matches_top_validator(record, top_addresses, top_names)
        uptime = _percent_number(_first_number(record, "uptime", "uptimePct", "uptimePercent", "availability", "participationRate", "signRate", "signedRate"))
        missed = _first_number(record, "missedBlocks", "missed_blocks", "missedRounds", "missed_votes", "missedVotes", "missed")
        latency = _first_number(record, "latency", "latencyMs", "avgLatencyMs", "responseMs")

        if uptime is not None:
            fields.add("uptime/participation")
            if uptime < 90.0 and is_top:
                criticals.append(f"Top-24 validator {label} shows very weak uptime/participation ({uptime:.1f}%).")
                penalty += 20
                worst.append((0, f"{label} uptime {uptime:.1f}%"))
            elif uptime < 95.0 and is_top:
                warnings.append(f"Top-24 validator {label} shows low uptime/participation ({uptime:.1f}%).")
                penalty += 12
                worst.append((1, f"{label} uptime {uptime:.1f}%"))
            elif uptime < 98.0:
                warnings.append(f"Validator {label} shows soft uptime/participation ({uptime:.1f}%).")
                penalty += 5 if is_top else 2
                worst.append((2, f"{label} uptime {uptime:.1f}%"))
        if missed is not None:
            fields.add("missed blocks/rounds")
            if missed >= 100 and is_top:
                warnings.append(f"Top-24 validator {label} shows high missed block/round count ({missed:g}).")
                penalty += 5
                worst.append((3, f"{label} missed {missed:g}"))
        if latency is not None:
            fields.add("latency")

    if not fields:
        warnings.append("validatorStats loaded but no known uptime, missed-block, latency, or participation fields were found.")
        metrics["missing_metrics"].append("known validatorStats performance fields")
        notes.append(f"validatorStats raw shape: {_shape_label(payload)}")
    metrics["performance_fields"] = sorted(fields)
    metrics["worst_performers"] = [line for _rank, line in sorted(worst, key=lambda item: item[0])[:5]]
    return metrics, _unique_lines(warnings), _unique_lines(criticals), min(35, penalty), notes


def _summarize_l1_votes(
    payload: Any,
    top_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str], int, list[str]]:
    metrics = {
        "l1_vote_records": 0,
        "l1_vote_non_participants": 0,
        "l1_vote_top1_pct": None,
        "l1_vote_top3_pct": None,
    }
    warnings: list[str] = []
    criticals: list[str] = []
    notes: list[str] = []
    penalty = 0

    if payload is None:
        warnings.append("validatorL1Votes unavailable; score excludes L1 vote participation metrics.")
        return metrics, warnings, criticals, penalty, notes

    records = _extract_records(payload, ("votes", "validatorL1Votes", "validators", "data", "result"))
    if not records:
        warnings.append("validatorL1Votes loaded but schema was not recognized; vote participation could not be scored.")
        notes.append(f"validatorL1Votes raw shape: {_shape_label(payload)}")
        return metrics, warnings, criticals, penalty, notes

    top_addresses = {str(row["address"]).lower() for row in top_rows if row["address"]}
    top_names = {str(row["name"]).lower() for row in top_rows if row["name"]}
    weights: list[float] = []
    non_participants = 0
    top_non_participants = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        weight = _first_number(record, "weight", "votePower", "votingPower", "stake", "power")
        if weight is not None and weight > ZERO_EPSILON:
            weights.append(weight)
        voted = _first_bool(record, "voted", "didVote", "signed", "participated", "hasVoted")
        if voted is False:
            non_participants += 1
            if _record_matches_top_validator(record, top_addresses, top_names):
                top_non_participants += 1

    metrics["l1_vote_records"] = len(records)
    metrics["l1_vote_non_participants"] = non_participants
    if weights:
        total_weight = sum(weights)
        ordered = sorted(weights, reverse=True)
        metrics["l1_vote_top1_pct"] = _stake_share(ordered[:1], total_weight)
        metrics["l1_vote_top3_pct"] = _stake_share(ordered[:3], total_weight)
        if metrics["l1_vote_top1_pct"] is not None and metrics["l1_vote_top1_pct"] > 30.0:
            warnings.append(f"L1 vote power appears concentrated: top voter weight is {metrics['l1_vote_top1_pct']:.1f}%.")
            penalty += 5
    else:
        notes.append("validatorL1Votes loaded without recognizable vote-power fields.")

    if top_non_participants:
        warnings.append(f"{top_non_participants} top-24 validator(s) appear not to have participated in L1 votes.")
        penalty += min(20, top_non_participants * 10)
    elif non_participants:
        warnings.append(f"{non_participants} validator(s) appear not to have participated in L1 votes.")
        penalty += min(8, non_participants * 2)

    return metrics, _unique_lines(warnings), criticals, penalty, notes


def _exchange_status_penalty(payload: Any, warnings: list[str], criticals: list[str]) -> tuple[int, str]:
    if payload is None:
        _append_unique(warnings, "exchangeStatus unavailable or unsupported; exchange-level sanity signal is missing.")
        return 0, "unavailable"
    text = _first_string(payload, "status", "exchangeStatus", "state", "message") if isinstance(payload, dict) else str(payload)
    normalized = text.strip().lower() if text else _shape_label(payload).lower()
    if any(token in normalized for token in ("halt", "offline", "outage", "down")):
        criticals.append(f"exchangeStatus looks severe: {text or _shape_label(payload)}.")
        return 25, text or "severe"
    if any(token in normalized for token in ("degraded", "maintenance", "partial", "delayed")):
        _append_unique(warnings, f"exchangeStatus indicates degraded conditions: {text or _shape_label(payload)}.")
        return 10, text or "degraded"
    return 0, text or "loaded"


def _counterfactuals(
    top_rows: list[dict[str, Any]],
    positive_rows: list[dict[str, Any]],
    jailed_total: int,
    concentration: dict[str, Any],
) -> list[str]:
    lines: list[str] = []
    active_stake = sum(row["stake"] for row in top_rows)
    if top_rows and active_stake > ZERO_EPSILON:
        largest = top_rows[0]
        remaining = [row for row in top_rows if row is not largest]
        remaining_stake = sum(row["stake"] for row in remaining)
        remaining_top5 = _stake_share([row["stake"] for row in remaining[:5]], remaining_stake)
        lines.append(
            f"If the largest validator ({largest['name']}) went offline or was jailed, the main squad would lose its biggest member and remaining top-5 share would be {_format_percent(remaining_top5)}."
        )
    if len(top_rows) >= 3 and active_stake > ZERO_EPSILON:
        top3_stake = sum(row["stake"] for row in top_rows[:3])
        lines.append(
            f"If the top 3 validators were impaired together, about {_format_percent(_stake_share([top3_stake], active_stake))} of the active-set stake approximation would be affected."
        )
    if jailed_total:
        lines.append(f"If all currently jailed validators recovered, jailed count would normalize from {jailed_total} to 0; concentration and performance checks would still matter.")
    else:
        one_third = concentration.get("validators_to_exceed_one_third")
        lines.append(f"If one top-24 validator were jailed, the clean-liveness count would immediately fall below the current read; current one-third threshold count is {_format_optional_int(one_third)}.")
    reserve_depth = max(0, len(positive_rows) - ACTIVE_SET_TARGET)
    lines.append(f"If the smallest active validators dropped out, positive-stake reserve depth beyond the 24-validator target is {reserve_depth}.")
    return lines[:6]


def _raw_shape_notes(snapshot: HyperliquidValidatorHealthSnapshot) -> list[str]:
    notes: list[str] = []
    if snapshot.errors:
        notes.extend(snapshot.errors)
    if snapshot.raw_validator_summaries is not None and snapshot.validator_summaries:
        notes.append(f"validatorSummaries raw shape: {_shape_label(snapshot.raw_validator_summaries)}")
    return notes


def _empty_metrics(snapshot: HyperliquidValidatorHealthSnapshot) -> dict[str, Any]:
    return {
        "validator_count": 0,
        "active_set_target": ACTIVE_SET_TARGET,
        "validators_with_positive_stake": 0,
        "top24_active_approximation": 0,
        "total_stake": 0.0,
        "active_stake": 0.0,
        "total_stake_display": 0.0,
        "active_stake_display": 0.0,
        "stake_unit_label": "HYPE",
        "stake_scale": None,
        "stake_scale_source": "none",
        "jailed_total": 0,
        "jailed_top24": 0,
        "outside_jailed": 0,
        "inactive_top24": 0,
        "chain_operating_health_score": 0,
        "decentralization_score": 0,
        "data_confidence_score": 0,
        "top1_pct": None,
        "top3_pct": None,
        "top5_pct": None,
        "top10_pct": None,
        "validators_to_exceed_one_third": None,
        "validators_to_exceed_two_thirds": None,
        "hhi": None,
        "performance_fields": [],
        "worst_performers": [],
        "missing_metrics": ["validatorSummaries"],
        "l1_vote_records": 0,
        "l1_vote_non_participants": 0,
        "l1_vote_top1_pct": None,
        "l1_vote_top3_pct": None,
        "exchange_status_read": "unavailable" if snapshot.exchange_status is None else "loaded",
        "validator_stats_loaded": snapshot.validator_stats is not None,
        "validator_l1_votes_loaded": snapshot.validator_l1_votes is not None,
        "all_mids_ok": snapshot.all_mids_ok,
    }


def _temperature(score: int, criticals: list[str]) -> str:
    if criticals and score >= 70:
        return "ORANGE"
    if score >= 85:
        return "GREEN"
    if score >= 70:
        return "YELLOW"
    if score >= 50:
        return "ORANGE"
    return "RED"


def _headline(temperature: str, criticals: list[str], warnings: list[str]) -> str:
    if temperature == "GREEN":
        return "Validator set looks operational, with no major chain-health flags from available data."
    if temperature == "YELLOW":
        return "Validator set looks operational, but concentration or data gaps are worth watching."
    if temperature == "ORANGE":
        return "Validator set looks degraded enough that chain infrastructure risk deserves attention."
    if temperature == "RED":
        return "Validator set looks dangerous from available data; infrastructure risk is elevated."
    if criticals:
        return criticals[0]
    if warnings:
        return warnings[0]
    return "Validator set health could not be determined."


def _bottom_line(assessment: HyperliquidChainHealthAssessment) -> str:
    if assessment.temperature == "GREEN":
        return "Operational read: normal enough for routine monitoring."
    if assessment.temperature == "YELLOW":
        return "Operational read: mostly normal, but keep concentration and missing metrics on the screen."
    if assessment.temperature == "ORANGE":
        return "Operational read: degraded - do not ignore chain infrastructure risk before sizing trades."
    if assessment.temperature == "RED":
        return "Operational read: dangerous - available validator or exchange data shows severe infrastructure risk."
    return "Operational read: unknown - validator data was not sufficient for a real health call."


def _record_matches_top_validator(record: dict[str, Any], top_addresses: set[str], top_names: set[str]) -> bool:
    address = (_validator_address(record) or "").lower()
    name = (_validator_name(record) or "").lower()
    return bool((address and address in top_addresses) or (name and name in top_names))


def _extract_records(payload: Any, keys: tuple[str, ...]) -> list[Any]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return payload
    if isinstance(payload, tuple):
        return list(payload)
    if not isinstance(payload, dict):
        return []

    for key in keys:
        value = _value_for_key(payload, key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested_values = list(value.values())
            if nested_values and all(isinstance(item, dict) for item in nested_values):
                return nested_values
            return [value]

    values = list(payload.values())
    if values and all(isinstance(item, dict) for item in values):
        return values
    if _looks_like_validator(payload) or any(_value_for_key(payload, key) is not None for key in ("stake", "name", "address", "validator")):
        return [payload]
    return []


def _looks_like_validator(payload: dict[str, Any]) -> bool:
    return any(_value_for_key(payload, key) is not None for key in ("stake", "delegatedStake", "totalStake", "votingPower", "commission", "jailed", "isJailed"))


def _value_for_key(source: dict[str, Any], key: str, *, depth: int = 2) -> Any:
    if key in source:
        return source[key]
    lower_key = key.lower()
    for raw_key, value in source.items():
        if str(raw_key).lower() == lower_key:
            return value
    if depth <= 0:
        return None
    for value in source.values():
        if isinstance(value, dict):
            found = _value_for_key(value, key, depth=depth - 1)
            if found is not None:
                return found
    return None


def _first_number(source: Any, *keys: str) -> float | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = _value_for_key(source, key)
        number = _to_float(value)
        if number is not None:
            return number
    return None


def _first_string(source: Any, *keys: str) -> str | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = _value_for_key(source, key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _first_bool(source: Any, *keys: str) -> bool | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = _value_for_key(source, key)
        parsed = _to_bool(value)
        if parsed is not None:
            return parsed
    return None


def _is_jailed_validator(source: dict[str, Any]) -> bool:
    jailed = _first_bool(source, "isJailed", "jailed", "is_jailed", "jail", "tombstoned")
    if jailed is not None:
        return jailed
    jail_until = _first_number(source, "unjailableAfter", "jailedUntil", "jailUntil", "jailed_until")
    if jail_until is not None and jail_until > 0:
        return True
    status = (_first_string(source, "status", "state", "validatorStatus", "liveness") or "").lower()
    return any(token in status for token in ("jail", "tombstone"))


def _is_inactive_validator(source: dict[str, Any]) -> bool:
    active = _first_bool(source, "active", "isActive", "enabled", "isEnabled")
    if active is False:
        return True
    status = (_first_string(source, "status", "state", "validatorStatus", "liveness") or "").lower()
    return any(token in status for token in ("inactive", "disabled", "undelegate", "not active", "offline"))


def _validator_address(source: dict[str, Any]) -> str | None:
    return _first_string(source, "validator", "validatorAddress", "address", "addr", "node", "id", "account", "signer", "pubKey", "publicKey")


def _validator_name(source: dict[str, Any]) -> str | None:
    return _first_string(source, "name", "moniker", "validatorName", "displayName", "description")


def _validator_stake(source: dict[str, Any]) -> float | None:
    return _first_number(
        source,
        "stake",
        "delegatedStake",
        "delegated_stake",
        "totalStake",
        "total_stake",
        "votingPower",
        "votePower",
        "validatorPower",
        "power",
        "bondedStake",
        "effectiveStake",
    )


def _validator_commission(source: dict[str, Any]) -> float | None:
    raw = _first_number(source, "commission", "commissionRate", "commission_rate", "fee", "delegationFee", "validatorFee")
    if raw is None:
        return None
    if 0.0 <= raw <= 1.0:
        return raw * 100.0
    if raw <= 100.0:
        return raw
    if raw <= 10000.0:
        return raw / 100.0
    return raw


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("_", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return float(text)
    except ValueError:
        match = re.search(r"-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", text, flags=re.IGNORECASE)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None


def _to_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"true", "yes", "y", "1", "active", "enabled", "voted", "signed"}:
        return True
    if text in {"false", "no", "n", "0", "inactive", "disabled", "not voted", "unsigned"}:
        return False
    return None


def _percent_number(value: float | None) -> float | None:
    if value is None:
        return None
    if 0.0 <= value <= 1.0:
        return value * 100.0
    return value


def _stake_share(stakes: list[float], total: float) -> float | None:
    if total <= ZERO_EPSILON:
        return None
    return sum(stakes) / total * 100.0


def _validators_to_exceed(stakes: list[float], threshold: float) -> int | None:
    if threshold <= ZERO_EPSILON:
        return None
    running = 0.0
    for index, stake in enumerate(stakes, start=1):
        running += stake
        if running > threshold:
            return index
    return None


def _loaded_label(payload: Any) -> str:
    if payload is None:
        return "unavailable"
    records = _extract_records(payload, ("stats", "validatorStats", "votes", "validatorL1Votes", "validators", "data", "result"))
    if records:
        return f"loaded, {len(records)} record(s)"
    return f"loaded, {_shape_label(payload)}"


def _all_mids_label(value: bool | None) -> str:
    if value is True:
        return "ok"
    if value is False:
        return "failed"
    return "not checked"


def _shape_label(payload: Any) -> str:
    if payload is None:
        return "none"
    if isinstance(payload, list):
        return f"list[{len(payload)}]"
    if isinstance(payload, dict):
        keys = ", ".join(str(key) for key in list(payload.keys())[:6])
        suffix = "..." if len(payload) > 6 else ""
        return f"dict[{len(payload)} keys: {keys}{suffix}]"
    return type(payload).__name__


def _format_percent(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "--"


def _format_number(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "--"


def _format_optional_int(value: Any) -> str:
    if value is None:
        return "--"
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "--"


def _list_or_none(values: Any) -> str:
    if not values:
        return "None"
    if isinstance(values, (list, tuple, set)):
        return ", ".join(str(value) for value in values) or "None"
    return str(values)


def _bullets_or_none(lines: list[str]) -> list[str]:
    if not lines:
        return ["- None"]
    return [f"- {line}" for line in lines]


def _append_unique(lines: list[str], line: str) -> None:
    if line not in lines:
        lines.append(line)


def _unique_lines(lines: list[str]) -> list[str]:
    output: list[str] = []
    for line in lines:
        _append_unique(output, line)
    return output
