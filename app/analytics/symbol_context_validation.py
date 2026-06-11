from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Mapping


ValidationSeverity = Literal["info", "warning", "error"]
ValidationStatus = Literal["valid", "warning", "invalid"]

DEFAULT_PRICE_MISMATCH_THRESHOLD_PERCENT = 5.0
DEFAULT_RANGE_MISMATCH_TOLERANCE_PERCENT = 1.0
DEFAULT_STALE_MARKET_DATA_HOURS = 96.0


@dataclass(frozen=True)
class SymbolContextValidationIssue:
    severity: ValidationSeverity
    code: str
    message: str
    affected_layers: tuple[str, ...] = ()
    suggested_action: str = ""


@dataclass(frozen=True)
class SymbolContextValidationResult:
    status: ValidationStatus
    issues: tuple[SymbolContextValidationIssue, ...]
    safe_context_overrides: dict[str, Any] = field(default_factory=dict)


def validate_symbol_chat_context(
    *,
    symbol: str,
    quote_snapshot: Mapping[str, Any] | None = None,
    research_workspace_context: Mapping[str, Any] | None = None,
    technical_analysis: Mapping[str, Any] | None = None,
    web_enrichment: Mapping[str, Any] | None = None,
    source_metadata: Mapping[str, Any] | None = None,
) -> SymbolContextValidationResult:
    """Validate Symbol Chat market-data layers before they are sent to OpenAI."""

    clean_symbol = _normalize_symbol(symbol)
    quote = dict(quote_snapshot or {})
    research = dict(research_workspace_context or {})
    technical = dict(technical_analysis or {})
    web = dict(web_enrichment or {})
    metadata = dict(source_metadata or {})

    issues: list[SymbolContextValidationIssue] = []
    overrides: dict[str, Any] = {}

    quote_price = _quote_price(quote)
    quote_timestamp = _first_timestamp(
        quote,
        "quote_timestamp",
        "quote_time",
        "trade_timestamp",
        "trade_time",
        "last_timestamp",
        "last_time",
        "mark_timestamp",
        "regular_market_time",
    )

    technical_price = _technical_latest_close(research, technical, web)
    atr_percent = _technical_atr_percent(research, technical)
    mismatch_threshold = max(DEFAULT_PRICE_MISMATCH_THRESHOLD_PERCENT, (atr_percent or 0.0) * 3.0)
    technical_layers = ("quote_snapshot", "technical_analysis", "research_workspace_context")

    if quote_price is not None and technical_price is not None:
        diff_pct = _percent_distance(quote_price, technical_price)
        if diff_pct is not None and diff_pct > mismatch_threshold:
            ratio = max(quote_price, technical_price) / max(min(quote_price, technical_price), 0.000001)
            split_note = _split_ratio_note(ratio)
            issues.append(
                SymbolContextValidationIssue(
                    severity="warning",
                    code="quote_technical_price_mismatch",
                    message=(
                        f"{clean_symbol} quote price {quote_price:.4g} differs from technical latest close "
                        f"{technical_price:.4g} by {diff_pct:.1f}%, above the {mismatch_threshold:.1f}% tolerance."
                        + (f" {split_note}" if split_note else "")
                    ),
                    affected_layers=technical_layers,
                    suggested_action="Demote technical levels that were calculated from the stale or mismatched close.",
                )
            )
            _mark_override(overrides, "technical_levels", "stale_or_misaligned")

    week_high = _technical_number(research, technical, ("week_52_high", "52_week_high", "fifty_two_week_high"))
    week_low = _technical_number(research, technical, ("week_52_low", "52_week_low", "fifty_two_week_low"))
    range_tolerance = max(DEFAULT_RANGE_MISMATCH_TOLERANCE_PERCENT, min(4.0, (atr_percent or 0.0) * 0.5))
    if quote_price is not None and week_high is not None and quote_price > week_high * (1 + range_tolerance / 100.0):
        issues.append(
            SymbolContextValidationIssue(
                severity="warning",
                code="quote_52_week_high_mismatch",
                message=(
                    f"{clean_symbol} quote price {quote_price:.4g} is above the provided 52-week high "
                    f"{week_high:.4g} by more than the {range_tolerance:.1f}% tolerance."
                ),
                affected_layers=("quote_snapshot", "research_workspace_context", "technical_analysis"),
                suggested_action="Treat the provided 52-week range as stale or on a different price basis.",
            )
        )
        _mark_override(overrides, "technical_52_week_range", "stale_or_misaligned")
    if quote_price is not None and week_low is not None and quote_price < week_low * (1 - range_tolerance / 100.0):
        issues.append(
            SymbolContextValidationIssue(
                severity="warning",
                code="quote_52_week_low_mismatch",
                message=(
                    f"{clean_symbol} quote price {quote_price:.4g} is below the provided 52-week low "
                    f"{week_low:.4g} by more than the {range_tolerance:.1f}% tolerance."
                ),
                affected_layers=("quote_snapshot", "research_workspace_context", "technical_analysis"),
                suggested_action="Treat the provided 52-week range as stale or on a different price basis.",
            )
        )
        _mark_override(overrides, "technical_52_week_range", "stale_or_misaligned")

    level_issue = _technical_level_alignment_issue(
        clean_symbol,
        quote_price=quote_price,
        technical_price=technical_price,
        research=research,
        technical=technical,
        mismatch_threshold=mismatch_threshold,
    )
    if level_issue is not None:
        issues.append(level_issue)
        _mark_override(overrides, "technical_levels", "stale_or_misaligned")

    option_issue = _option_alignment_issue(clean_symbol, quote_price=quote_price, research=research, mismatch_threshold=mismatch_threshold)
    if option_issue is not None:
        issues.append(option_issue)
        _mark_override(overrides, "option_context", "stale_or_misaligned")

    loaded_at = _metadata_loaded_at(metadata)
    candle_timestamp = _first_timestamp(
        _nested_mapping(research, ("technicals", "indicator_snapshot")),
        "latest_candle_timestamp",
        "latest_candle_time",
        "candle_timestamp",
        "as_of",
        "timestamp",
    ) or _first_timestamp(
        _nested_mapping(technical, ("snapshot",)),
        "latest_candle_timestamp",
        "latest_candle_time",
        "candle_timestamp",
        "as_of",
        "timestamp",
    ) or _market_news_timestamp(web, "latest_candle_timestamp", "latest_candle_time", "candle_timestamp")

    stale_threshold = timedelta(hours=DEFAULT_STALE_MARKET_DATA_HOURS)
    if quote_timestamp is not None and loaded_at is not None and loaded_at - quote_timestamp > stale_threshold:
        issues.append(
            SymbolContextValidationIssue(
                severity="warning",
                code="stale_quote_timestamp",
                message=(
                    f"{clean_symbol} quote timestamp {quote_timestamp.isoformat(timespec='seconds')} is more than "
                    f"{DEFAULT_STALE_MARKET_DATA_HOURS:.0f} hours older than context load time."
                ),
                affected_layers=("quote_snapshot",),
                suggested_action="Do not treat the quote snapshot as current without a fresh refresh.",
            )
        )
        _mark_override(overrides, "quote_snapshot", "stale")
    if candle_timestamp is not None and loaded_at is not None and loaded_at - candle_timestamp > stale_threshold:
        issues.append(
            SymbolContextValidationIssue(
                severity="warning",
                code="stale_candle_timestamp",
                message=(
                    f"{clean_symbol} candle timestamp {candle_timestamp.isoformat(timespec='seconds')} is more than "
                    f"{DEFAULT_STALE_MARKET_DATA_HOURS:.0f} hours older than context load time."
                ),
                affected_layers=("technical_analysis", "research_workspace_context"),
                suggested_action="Demote technical levels that depend on stale candles.",
            )
        )
        _mark_override(overrides, "technical_levels", "stale_or_misaligned")

    status: ValidationStatus = "valid"
    if any(issue.severity == "error" for issue in issues):
        status = "invalid"
    elif issues:
        status = "warning"
    return SymbolContextValidationResult(status=status, issues=tuple(issues), safe_context_overrides=overrides)


def symbol_context_validation_to_payload(result: SymbolContextValidationResult | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(result, SymbolContextValidationResult):
        payload = asdict(result)
    else:
        payload = dict(result)
    payload.setdefault("status", "valid")
    payload["issues"] = [_issue_payload(issue) for issue in payload.get("issues") or []]
    overrides = payload.get("safe_context_overrides")
    payload["safe_context_overrides"] = dict(overrides) if isinstance(overrides, Mapping) else {}
    payload["issue_count"] = len(payload["issues"])
    return payload


def _issue_payload(issue: Any) -> dict[str, Any]:
    if isinstance(issue, SymbolContextValidationIssue):
        row = asdict(issue)
    elif isinstance(issue, Mapping):
        row = dict(issue)
    else:
        row = {"severity": "warning", "code": "validation_issue", "message": str(issue)}
    affected = row.get("affected_layers")
    if isinstance(affected, str):
        row["affected_layers"] = [affected]
    elif isinstance(affected, tuple):
        row["affected_layers"] = list(affected)
    elif not isinstance(affected, list):
        row["affected_layers"] = []
    return {str(key): value for key, value in row.items() if value not in (None, "", [], {}, ())}


def _option_alignment_issue(
    symbol: str,
    *,
    quote_price: float | None,
    research: Mapping[str, Any],
    mismatch_threshold: float,
) -> SymbolContextValidationIssue | None:
    if quote_price is None:
        return None
    option_values = _option_reference_prices(research)
    mismatches = []
    for label, value in option_values:
        diff_pct = _percent_distance(value, quote_price)
        if diff_pct is not None and diff_pct > mismatch_threshold:
            mismatches.append((label, value, diff_pct))
    if not mismatches:
        return None
    label, value, diff_pct = max(mismatches, key=lambda item: item[2])
    return SymbolContextValidationIssue(
        severity="warning",
        code="option_underlying_mismatch",
        message=(
            f"{symbol} {label} {value:.4g} differs from quote price {quote_price:.4g} by "
            f"{diff_pct:.1f}%, above the {mismatch_threshold:.1f}% tolerance."
        ),
        affected_layers=("quote_snapshot", "research_workspace_context.options_strategy", "research_workspace_context.greeks"),
        suggested_action="Treat option-chain and Greek context as stale unless refreshed from the same quote basis.",
    )


def _technical_level_alignment_issue(
    symbol: str,
    *,
    quote_price: float | None,
    technical_price: float | None,
    research: Mapping[str, Any],
    technical: Mapping[str, Any],
    mismatch_threshold: float,
) -> SymbolContextValidationIssue | None:
    if quote_price is None or technical_price is None:
        return None
    levels = _technical_levels(research, technical)
    if not levels:
        return None

    mismatched_levels = []
    near_technical_threshold = max(10.0, mismatch_threshold)
    for label, value in levels:
        quote_distance = _percent_distance(value, quote_price)
        technical_distance = _percent_distance(value, technical_price)
        if quote_distance is None or technical_distance is None:
            continue
        if quote_distance > mismatch_threshold and technical_distance <= near_technical_threshold:
            mismatched_levels.append((label, value, quote_distance, technical_distance))
    if not mismatched_levels:
        return None

    labels = ", ".join(label for label, _value, _quote_distance, _technical_distance in mismatched_levels[:6])
    return SymbolContextValidationIssue(
        severity="warning",
        code="technical_levels_track_stale_close",
        message=(
            f"{symbol} technical levels ({labels}) are closer to the technical close {technical_price:.4g} "
            f"than to the quote price {quote_price:.4g}; levels may be stale or on a different price basis."
        ),
        affected_layers=("technical_analysis", "research_workspace_context.technicals"),
        suggested_action="Demote support, resistance, confirmation, and invalidation levels before model analysis.",
    )


def _quote_price(quote: Mapping[str, Any]) -> float | None:
    for key in ("mark", "last", "last_price", "regular_market_last_price", "close", "close_price"):
        value = _number(quote.get(key))
        if value is not None and value > 0:
            return value
    bid = _number(quote.get("bid"))
    ask = _number(quote.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return None


def _technical_latest_close(
    research: Mapping[str, Any],
    technical: Mapping[str, Any],
    web: Mapping[str, Any],
) -> float | None:
    candidates = (
        _nested_value(research, ("technicals", "indicator_snapshot", "latest_close")),
        _nested_value(technical, ("snapshot", "latest_close")),
        technical.get("latest_close"),
        _market_news_value(web, "latest_candle_close"),
        _market_news_value(web, "latest_close"),
    )
    for value in candidates:
        number = _number(value)
        if number is not None and number > 0:
            return number
    return None


def _technical_atr_percent(research: Mapping[str, Any], technical: Mapping[str, Any]) -> float | None:
    candidates = (
        _nested_value(research, ("technicals", "indicator_snapshot", "atr_percent")),
        _nested_value(research, ("technicals", "indicator_snapshot", "atr_14_percent")),
        _nested_value(technical, ("snapshot", "atr_percent")),
        technical.get("atr_percent"),
    )
    for value in candidates:
        number = _number(value)
        if number is not None and number >= 0:
            return number * 100 if 0 < number < 1 else number
    atr = _number(_nested_value(research, ("technicals", "indicator_snapshot", "atr_14"))) or _number(
        _nested_value(technical, ("snapshot", "atr_14"))
    )
    latest = _technical_latest_close(research, technical, {})
    if atr is not None and latest is not None and latest > 0:
        return abs(atr / latest) * 100
    return None


def _technical_number(
    research: Mapping[str, Any],
    technical: Mapping[str, Any],
    keys: tuple[str, ...],
) -> float | None:
    mappings = (
        _nested_mapping(research, ("technicals", "indicator_snapshot")),
        _nested_mapping(technical, ("snapshot",)),
        _nested_mapping(technical, ("levels",)),
        technical,
    )
    for mapping in mappings:
        for key in keys:
            value = _number(mapping.get(key))
            if value is not None and value > 0:
                return value
    return None


def _technical_levels(research: Mapping[str, Any], technical: Mapping[str, Any]) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    indicator = _nested_mapping(research, ("technicals", "indicator_snapshot"))
    for key in ("support", "resistance"):
        _append_level(rows, key, indicator.get(key))

    command = _nested_mapping(research, ("technicals", "command_center"))
    for key in ("confirmation_level", "invalidation_level"):
        _append_level(rows, key, command.get(key))

    levels = _nested_mapping(technical, ("levels",))
    for key in ("support", "resistance", "confirmation", "invalidation", "confirmation_level", "invalidation_level"):
        _append_level(rows, key, levels.get(key))
    for trigger in technical.get("key_triggers") or []:
        if isinstance(trigger, Mapping):
            _append_level(rows, str(trigger.get("label") or "trigger"), trigger.get("price"))
    return _dedupe_levels(rows)


def _option_reference_prices(research: Mapping[str, Any]) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    options = _nested_mapping(research, ("options_strategy",))
    greeks = _nested_mapping(research, ("greeks",))
    for label, value in (
        ("option-chain underlying price", options.get("underlying_price")),
        ("option-chain source price", options.get("source_price")),
        ("Greek source price", greeks.get("source_price")),
        ("Greek underlying price", greeks.get("underlying_price")),
    ):
        number = _number(value)
        if number is not None and number > 0:
            rows.append((label, number))
    return rows


def _append_level(rows: list[tuple[str, float]], label: str, value: Any) -> None:
    number = _number(value)
    if number is not None and number > 0:
        rows.append((label, number))


def _dedupe_levels(rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    result: list[tuple[str, float]] = []
    seen: set[tuple[str, float]] = set()
    for label, value in rows:
        key = (label, round(value, 4))
        if key in seen:
            continue
        seen.add(key)
        result.append((label, value))
    return result


def _metadata_loaded_at(metadata: Mapping[str, Any]) -> datetime | None:
    for key in ("loaded_at_utc", "generated_at_utc", "as_of", "timestamp"):
        parsed = _parse_timestamp(metadata.get(key))
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc)


def _market_news_value(web: Mapping[str, Any], key: str) -> Any:
    recent = web.get("recent_market_news")
    if not isinstance(recent, Mapping):
        return None
    snapshot = recent.get("market_snapshot")
    if isinstance(snapshot, Mapping) and key in snapshot:
        return snapshot.get(key)
    return recent.get(key)


def _market_news_timestamp(web: Mapping[str, Any], *keys: str) -> datetime | None:
    recent = web.get("recent_market_news")
    if not isinstance(recent, Mapping):
        return None
    snapshot = recent.get("market_snapshot")
    if isinstance(snapshot, Mapping):
        parsed = _first_timestamp(snapshot, *keys)
        if parsed is not None:
            return parsed
    return _first_timestamp(recent, *keys)


def _first_timestamp(mapping: Mapping[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        parsed = _parse_timestamp(mapping.get(key))
        if parsed is not None:
            return parsed
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        if isinstance(value, (int, float)) and value > 0:
            raw = float(value)
            if raw > 10_000_000_000:
                raw = raw / 1000.0
            try:
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"\d{10,13}", text):
            try:
                raw = float(text)
                if raw > 10_000_000_000:
                    raw = raw / 1000.0
                return datetime.fromtimestamp(raw, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        text = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _nested_value(mapping: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _nested_mapping(mapping: Mapping[str, Any], path: tuple[str, ...]) -> Mapping[str, Any]:
    value = _nested_value(mapping, path)
    return value if isinstance(value, Mapping) else {}


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "").replace("%", "")
    if not text or text in {"--", "N/A", "n/a"}:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _percent_distance(value: float, reference: float) -> float | None:
    if reference == 0:
        return None
    return abs((value - reference) / reference) * 100.0


def _split_ratio_note(ratio: float) -> str:
    for candidate in (2, 3, 4, 5, 10, 20):
        if abs(ratio - candidate) / candidate <= 0.05:
            return f"The ratio is close to a {candidate}:1 split-style price-basis mismatch."
    return ""


def _mark_override(overrides: dict[str, Any], layer: str, status: str) -> None:
    overrides[layer] = {
        "status": status,
        "instruction": "Do not rely on this layer's affected levels as current until refreshed or reconciled.",
    }


def _normalize_symbol(value: Any) -> str:
    return re.sub(r"[^A-Z0-9.\-_/]", "", str(value or "").strip().upper())
