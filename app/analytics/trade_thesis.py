from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.analytics.earnings_release import EarningsReleaseDigest, format_earnings_release_digest
from app.analytics.fundamental_analysis import FundamentalReport, build_interpretation_lines
from app.analytics.report_calendar import format_next_report_watch_line
from app.analytics.technical_analysis import MultiTimeframeTechnicalReport, SignalBias
from app.data.sec_edgar import SecFiling


@dataclass(frozen=True)
class OptionChainCandidate:
    side: str
    expiration: str
    strike: float
    bid: float | None
    ask: float | None
    mark: float | None
    volume: int | None
    dte: int | None
    contract_symbol: str


@dataclass(frozen=True)
class OptionChainContext:
    underlying: str
    candidates: list[OptionChainCandidate]

    @property
    def has_rows(self) -> bool:
        return bool(self.candidates)


def format_unified_trade_thesis(
    *,
    symbol: str,
    technical_report: MultiTimeframeTechnicalReport,
    fundamental_report: FundamentalReport | None,
    filings: list[SecFiling],
    release_digest: EarningsReleaseDigest | None,
    option_context: OptionChainContext | None,
) -> str:
    directional_bias = _directional_bias(technical_report, fundamental_report, release_digest)
    latest_close = technical_report.daily.latest_close
    support_level, resistance_level = _support_resistance_proxy(technical_report)

    lines = [
        f"UNIFIED TRADE THESIS — {symbol}",
        "=" * (24 + len(symbol)),
        "",
        "Purpose: combine technicals, fast earnings-release context, SEC filing cadence, formal fundamentals, and any loaded option-chain data.",
        "This is scenario planning, not a trade recommendation.",
        "",
        f"Overall directional read: {directional_bias.upper()}",
        f"Latest daily close: ${latest_close:,.2f}",
        f"Key invalidation / confirmation map: support proxy {_format_optional_money(support_level)}, resistance proxy {_format_optional_money(resistance_level)}",
        "",
        "Technical read:",
        f"- Daily bias: {technical_report.daily.overall_bias.value}; intraday bias: {technical_report.intraday.overall_bias.value}",
        f"- Daily RSI: {_format_optional_number(technical_report.daily.rsi)}; intraday RSI: {_format_optional_number(technical_report.intraday.rsi)}",
        f"- Daily MACD histogram: {_format_optional_number(technical_report.daily.macd_histogram)}; intraday MACD histogram: {_format_optional_number(technical_report.intraday.macd_histogram)}",
        *[f"- {line}" for line in technical_report.comparison_lines[:3]],
        "",
        "Fast earnings-release layer:",
        *[f"  {line}" for line in format_earnings_release_digest(release_digest).splitlines()],
        "",
        "SEC timing:",
        f"- {format_next_report_watch_line(filings)}",
        "",
        "Fundamental read-through:",
    ]

    if fundamental_report is None:
        lines.append("- Fundamental XBRL layer unavailable for this run.")
    else:
        lines.extend(f"- {line}" for line in build_interpretation_lines(fundamental_report, compact=True))

    lines.extend(
        [
            "",
            "Scenario map:",
            *_scenario_lines(directional_bias, latest_close, support_level, resistance_level),
            "",
            "Options planning layer:",
            *_option_structure_lines(directional_bias, option_context, support_level, resistance_level),
            "",
            "Suggested next checks:",
            "- If using options, load/refresh the option chain immediately before planning; stale chains can mislead pricing and liquidity reads.",
            "- Compare spread width and volume/open interest before choosing any contract.",
            "- Reconcile the fast 8-K earnings-release layer with the formal 10-Q/10-K when the filing arrives.",
            "- Keep position sizing separate from thesis quality; a good thesis can still be a bad risk/reward trade.",
        ]
    )
    return "\n".join(lines)


def option_context_from_rows(rows: dict[str, dict[str, Any]] | None) -> OptionChainContext | None:
    if not rows:
        return None
    candidates: list[OptionChainCandidate] = []
    underlying = ""
    for row in rows.values():
        if not isinstance(row, dict):
            continue
        underlying = underlying or str(row.get("underlying") or "")
        for side in ("call", "put"):
            contract = row.get(side)
            if not isinstance(contract, dict):
                continue
            candidates.append(
                OptionChainCandidate(
                    side=side,
                    expiration=str(row.get("expiration_label") or row.get("expiration_date") or ""),
                    strike=_to_float(row.get("strike")) or 0.0,
                    bid=_first_number(contract, "bid"),
                    ask=_first_number(contract, "ask"),
                    mark=_first_number(contract, "mark"),
                    volume=_first_int(contract, "totalVolume", "volume"),
                    dte=_to_int(row.get("dte")) or _first_int(contract, "daysToExpiration"),
                    contract_symbol=str(contract.get("symbol") or ""),
                )
            )
    if not candidates:
        return None
    return OptionChainContext(underlying=underlying, candidates=candidates)


def _directional_bias(
    technical_report: MultiTimeframeTechnicalReport,
    fundamental_report: FundamentalReport | None,
    release_digest: EarningsReleaseDigest | None,
) -> str:
    score = 0
    if technical_report.daily.overall_bias == SignalBias.BULLISH:
        score += 2
    elif technical_report.daily.overall_bias == SignalBias.BEARISH:
        score -= 2
    if technical_report.intraday.overall_bias == SignalBias.BULLISH:
        score += 1
    elif technical_report.intraday.overall_bias == SignalBias.BEARISH:
        score -= 1

    if fundamental_report is not None:
        read = " ".join(build_interpretation_lines(fundamental_report, compact=True)).lower()
        if any(word in read for word in ("strong", "improved", "growing", "expanded")):
            score += 1
        if any(word in read for word in ("contracting", "weakened", "compressed")):
            score -= 1

    if release_digest is not None:
        release_text = " ".join(release_digest.headline_snippets + release_digest.guidance_snippets).lower()
        if any(word in release_text for word in ("record", "growth", "expects", "above", "raised", "increase")):
            score += 1
        if any(word in release_text for word in ("decline", "lower", "headwind", "pressure", "decrease", "below")):
            score -= 1

    if score >= 3:
        return "bullish / upside-favored"
    if score <= -3:
        return "bearish / downside-favored"
    if score > 0:
        return "constructive but confirmation needed"
    if score < 0:
        return "cautious / downside risk elevated"
    return "mixed / wait for confirmation"


def _support_resistance_proxy(report: MultiTimeframeTechnicalReport) -> tuple[float | None, float | None]:
    latest = report.daily.latest_close
    sma_fast = report.daily.sma_fast
    sma_slow = report.daily.sma_slow
    levels = [level for level in (sma_fast, sma_slow) if level is not None]
    below = [level for level in levels if level <= latest]
    above = [level for level in levels if level >= latest]
    support = max(below) if below else min(levels) if levels else None
    resistance = min(above) if above else max(levels) if levels else None
    return support, resistance


def _scenario_lines(bias: str, latest: float, support: float | None, resistance: float | None) -> list[str]:
    lines = []
    if "bullish" in bias or "constructive" in bias:
        lines.append(f"- Bull case: continuation holds above {_format_optional_money(support)} and pushes through {_format_optional_money(resistance)}.")
        lines.append(f"- Bear risk: loss of {_format_optional_money(support)} would weaken the upside thesis and favor waiting/repricing.")
    elif "bearish" in bias or "cautious" in bias:
        lines.append(f"- Bear case: price fails near {_format_optional_money(resistance)} and loses {_format_optional_money(support)}.")
        lines.append(f"- Bull risk: reclaiming {_format_optional_money(resistance)} would challenge the downside thesis.")
    else:
        lines.append(f"- Base case: mixed signal around ${latest:,.2f}; wait for a break above {_format_optional_money(resistance)} or below {_format_optional_money(support)}.")
    lines.append("- Event risk: earnings-release/call commentary can overpower technical levels, especially near reporting windows.")
    return lines


def _option_structure_lines(
    bias: str,
    option_context: OptionChainContext | None,
    support: float | None,
    resistance: float | None,
) -> list[str]:
    if option_context is None or not option_context.has_rows:
        return [
            "- No loaded option-chain rows found. Load Option Chain first, then rerun Tech Analysis for contract-aware structures.",
            "- Without a chain, scenario ideas stay generic: calls for upside, puts for downside, debit spreads for defined-risk directional exposure.",
        ]

    calls = _rank_candidates([candidate for candidate in option_context.candidates if candidate.side == "call"], target=resistance)
    puts = _rank_candidates([candidate for candidate in option_context.candidates if candidate.side == "put"], target=support)
    best_call = calls[0] if calls else None
    second_call = calls[1] if len(calls) > 1 else None
    best_put = puts[0] if puts else None
    second_put = puts[1] if len(puts) > 1 else None

    lines = []
    if "bullish" in bias or "constructive" in bias:
        lines.append(_candidate_line("Bullish candidate", best_call))
        if best_call and second_call:
            lines.append(_spread_line("Bull call debit spread idea", best_call, second_call))
        lines.append("- Prefer defined-risk structures if implied volatility/spreads are wide or if earnings timing is close.")
    elif "bearish" in bias or "cautious" in bias:
        lines.append(_candidate_line("Bearish candidate", best_put))
        if best_put and second_put:
            lines.append(_spread_line("Bear put debit spread idea", best_put, second_put))
        lines.append("- Prefer defined-risk puts/spreads over open-ended short exposure.")
    else:
        lines.append(_candidate_line("Upside watch candidate", best_call))
        lines.append(_candidate_line("Downside watch candidate", best_put))
        lines.append("- Mixed thesis: consider waiting for a level break before choosing call-vs-put direction.")
    return lines


def _rank_candidates(candidates: list[OptionChainCandidate], *, target: float | None) -> list[OptionChainCandidate]:
    if not candidates:
        return []
    return sorted(candidates, key=lambda candidate: _candidate_score(candidate, target))


def _candidate_score(candidate: OptionChainCandidate, target: float | None) -> tuple[float, float, int]:
    ask = candidate.ask if candidate.ask is not None and candidate.ask > 0 else 9999.0
    spread = _spread(candidate)
    target_distance = abs(candidate.strike - target) if target is not None else 0.0
    volume_rank = -(candidate.volume or 0)
    return (target_distance, spread, volume_rank if isinstance(volume_rank, int) else 0)


def _candidate_line(label: str, candidate: OptionChainCandidate | None) -> str:
    if candidate is None:
        return f"- {label}: no usable loaded contract found."
    return (
        f"- {label}: {candidate.expiration} {candidate.strike:g} {candidate.side.upper()} "
        f"bid/ask {_format_optional_number(candidate.bid)}/{_format_optional_number(candidate.ask)}, "
        f"mark {_format_optional_number(candidate.mark)}, vol {candidate.volume if candidate.volume is not None else '--'}, "
        f"DTE {candidate.dte if candidate.dte is not None else '--'}."
    )


def _spread_line(label: str, long_leg: OptionChainCandidate, short_leg: OptionChainCandidate) -> str:
    long_debit = long_leg.ask or long_leg.mark or 0.0
    short_credit = short_leg.bid or short_leg.mark or 0.0
    net_debit = max(long_debit - short_credit, 0.0)
    width = abs(short_leg.strike - long_leg.strike)
    max_reward = max(width - net_debit, 0.0)
    return (
        f"- {label}: buy {long_leg.strike:g}, sell {short_leg.strike:g} same expiry if liquid; "
        f"rough net debit ${net_debit:,.2f}, width ${width:,.2f}, max reward before fees/slippage about ${max_reward:,.2f}."
    )


def _spread(candidate: OptionChainCandidate) -> float:
    if candidate.bid is None or candidate.ask is None:
        return 9999.0
    return max(candidate.ask - candidate.bid, 0.0)


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        result = _to_float(value)
        if result is not None:
            return result
    return None


def _first_int(source: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _to_int(source.get(key))
        if value is not None:
            return value
    return None


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None


def _format_optional_money(value: float | None) -> str:
    return "--" if value is None else f"${value:,.2f}"


def _format_optional_number(value: float | None) -> str:
    return "--" if value is None else f"{value:,.2f}"
