from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

SOURCE_SCHWAB = "Schwab provided"
SOURCE_CALCULATED = "Calculated estimate"
SOURCE_UNAVAILABLE = "Unavailable"


@dataclass(frozen=True)
class GreekValue:
    value: float | None
    source: str
    field_name: str = ""


@dataclass(frozen=True)
class OptionGreekSnapshot:
    underlying: str
    contract_symbol: str
    expiration: str
    dte: int | None
    strike: float | None
    option_type: str
    bid: float | None
    ask: float | None
    mark: float | None
    implied_volatility: GreekValue
    theoretical_value: GreekValue
    delta: GreekValue
    gamma: GreekValue
    theta: GreekValue
    vega: GreekValue
    rho: GreekValue
    source_summary: str
    warnings: tuple[str, ...] = ()
    selected: bool = False


@dataclass(frozen=True)
class GreekSummary:
    underlying: str
    underlying_price: float | None
    rows: list[OptionGreekSnapshot]
    selected: OptionGreekSnapshot | None
    nearest_call: OptionGreekSnapshot | None
    nearest_put: OptionGreekSnapshot | None
    warnings: list[str]
    plain_english: list[str]


@dataclass(frozen=True)
class GreekApproximationRow:
    move: float
    one_day_pnl: float
    full_window_pnl: float


@dataclass(frozen=True)
class GreekThetaOffset:
    one_day_move: float | None
    full_window_move: float | None
    full_window_days: int
    used_dte_fallback: bool


@dataclass(frozen=True)
class GreekContractRank:
    snapshot: OptionGreekSnapshot
    label: str
    score: float
    theta_per_delta: float | None
    premium: float | None
    breakeven: float | None
    breakeven_distance: float | None
    premium_efficiency: float | None
    reason: str


GREEK_APPROXIMATION_MOVES = (-10.0, -5.0, -2.5, 0.0, 2.5, 5.0, 10.0)
SMALL_DELTA = 0.01


def build_greek_summary(
    chain_rows: list[dict[str, Any]],
    underlying_price: float | None,
    *,
    selected_candidate: Any | None = None,
    selected_contract_symbol: str | None = None,
) -> GreekSummary:
    """Normalize Schwab option-chain rows into option Greek snapshots.

    Schwab fields are used first. Sentinel values such as -999 are treated as
    missing, and missing Greeks are estimated locally when there are enough
    price, strike, DTE, and volatility inputs.
    """

    snapshots = [
        snapshot
        for row in chain_rows
        for snapshot in _snapshots_from_chain_row(row, underlying_price)
    ]
    selected = _match_selected_snapshot(snapshots, selected_candidate, selected_contract_symbol)
    nearest_call = _nearest_snapshot(snapshots, underlying_price, "call")
    nearest_put = _nearest_snapshot(snapshots, underlying_price, "put")
    primary = selected or nearest_call or nearest_put
    marked_rows = [_mark_selected(snapshot, primary) for snapshot in snapshots]
    selected = _mark_selected(selected, selected) if selected is not None else None
    nearest_call = _replace_from_marked(marked_rows, nearest_call)
    nearest_put = _replace_from_marked(marked_rows, nearest_put)
    if selected is None and primary is not None:
        selected = _replace_from_marked(marked_rows, primary)

    warnings: list[str] = []
    if not chain_rows:
        warnings.append("No option chain rows are loaded yet.")
    if underlying_price is None or underlying_price <= 0:
        warnings.append("Underlying price is unavailable, so calculated estimates are limited.")
    if any(_uses_source(snapshot, SOURCE_CALCULATED) for snapshot in marked_rows):
        warnings.append("Some Greeks are calculated estimates because Schwab did not provide every sensitivity field.")
    if any(_uses_source(snapshot, SOURCE_UNAVAILABLE) for snapshot in marked_rows):
        warnings.append("Some values remain unavailable because the contract lacked enough data to estimate them.")

    active = selected or nearest_call or nearest_put
    return GreekSummary(
        underlying=_summary_underlying(marked_rows, selected_candidate),
        underlying_price=underlying_price,
        rows=marked_rows,
        selected=selected,
        nearest_call=nearest_call,
        nearest_put=nearest_put,
        warnings=warnings,
        plain_english=plain_english_greek_readout(active, underlying_price, warnings),
    )


def plain_english_greek_readout(
    snapshot: OptionGreekSnapshot | None,
    underlying_price: float | None,
    warnings: list[str] | tuple[str, ...] = (),
) -> list[str]:
    if snapshot is None:
        lines = ["Load an option chain to see option sensitivities."]
        lines.extend(warnings)
        return lines

    lines = [f"{_contract_label(snapshot)} is the active contract for the Greeks readout."]
    if underlying_price is not None:
        lines.append(f"Underlying reference price is ${underlying_price:,.2f}.")
    if snapshot.delta.value is not None:
        lines.append(f"Delta {snapshot.delta.value:+.3f} means one contract moves about ${abs(snapshot.delta.value) * 100:,.0f} for a $1 move in the stock, before gamma, volatility, and time effects.")
    else:
        lines.append("Delta is unavailable for this contract.")
    if snapshot.theta.value is not None:
        lines.append(f"Theta {snapshot.theta.value:+.3f} is about ${abs(snapshot.theta.value) * 100:,.0f} per contract per day of time decay, all else equal.")
    else:
        lines.append("Theta is unavailable for this contract.")
    if snapshot.vega.value is not None:
        lines.append(f"Vega {snapshot.vega.value:+.3f} is about ${abs(snapshot.vega.value) * 100:,.0f} per contract for a one-point implied-volatility move.")
    else:
        lines.append("Vega is unavailable for this contract.")
    if snapshot.gamma.value is not None:
        lines.append(f"Gamma {snapshot.gamma.value:+.4f} shows how much delta changes after a $1 move in the stock.")
    else:
        lines.append("Gamma is unavailable for this contract.")
    if snapshot.rho.value is not None:
        lines.append(f"Rho {snapshot.rho.value:+.3f} estimates the option-price impact from a one-point rate move.")
    else:
        lines.append("Rho is unavailable for this contract.")
    lines.append(f"Source mix: {snapshot.source_summary}.")
    lines.extend(warnings)
    return lines


def active_greek_decision_snapshot(summary: GreekSummary) -> OptionGreekSnapshot | None:
    return summary.selected or summary.nearest_call or summary.nearest_put


def greek_dollar_meanings(snapshot: OptionGreekSnapshot) -> list[tuple[str, str]]:
    delta = snapshot.delta.value
    gamma = snapshot.gamma.value
    theta = snapshot.theta.value
    vega = snapshot.vega.value
    rho = snapshot.rho.value
    dte = snapshot.dte
    return [
        (f"Delta {_signed_decimal(delta, digits=3)}", "--" if delta is None else f"{_signed_money(delta * 100, digits=0)} per +$1 stock move"),
        (f"Gamma {_signed_decimal(gamma, digits=4)}", "--" if gamma is None else f"Delta changes by about {_signed_points(gamma * 100)} points per $1 move"),
        (f"Theta {_signed_decimal(theta, digits=3)}", "--" if theta is None else f"{_signed_money(theta * 100, digits=0)} per day"),
        (f"Vega {_signed_decimal(vega, digits=3)}", "--" if vega is None else f"{_signed_money(vega * 100, digits=0)} per +1 vol point"),
        (f"Rho {_signed_decimal(rho, digits=3)}", _rho_meaning(rho, dte)),
    ]


def greek_approximation_rows(
    snapshot: OptionGreekSnapshot,
    moves: tuple[float, ...] = GREEK_APPROXIMATION_MOVES,
    *,
    full_window_days: int | None = None,
) -> list[GreekApproximationRow]:
    delta = snapshot.delta.value
    gamma = snapshot.gamma.value
    theta = snapshot.theta.value
    if delta is None or gamma is None or theta is None:
        return []
    days = _full_window_days(snapshot, full_window_days)
    return [
        GreekApproximationRow(
            move=move,
            one_day_pnl=_greek_approximation_pnl(delta, gamma, theta, move, 1),
            full_window_pnl=_greek_approximation_pnl(delta, gamma, theta, move, days),
        )
        for move in moves
    ]


def theta_offset_moves(snapshot: OptionGreekSnapshot, *, full_window_days: int | None = None) -> GreekThetaOffset:
    days = _full_window_days(snapshot, full_window_days)
    delta = snapshot.delta.value
    theta = snapshot.theta.value
    used_fallback = snapshot.dte is None or snapshot.dte <= 0
    if delta is None or theta is None:
        return GreekThetaOffset(None, None, days, used_fallback)
    denominator = max(abs(delta), SMALL_DELTA)
    return GreekThetaOffset(abs(theta) / denominator, abs(theta * days) / denominator, days, used_fallback)


def rank_greek_contracts(
    summary: GreekSummary,
    option_type: str | None = None,
    *,
    limit: int = 5,
) -> list[GreekContractRank]:
    active = active_greek_decision_snapshot(summary)
    side = (option_type or (active.option_type if active is not None else "")).lower()
    if side not in {"call", "put"}:
        return []
    candidates = [snapshot for snapshot in summary.rows if snapshot.option_type == side and _has_rankable_greeks(snapshot)]
    if not candidates:
        return []
    if active is not None and active.dte is not None:
        same_dte = [snapshot for snapshot in candidates if snapshot.dte == active.dte]
        if len(same_dte) >= 2:
            candidates = same_dte
    underlying_price = summary.underlying_price
    if underlying_price is not None and underlying_price > 0:
        nearby = [
            snapshot
            for snapshot in candidates
            if snapshot.strike is not None and abs(snapshot.strike - underlying_price) / underlying_price <= 0.15
        ]
        if nearby:
            candidates = sorted(nearby, key=lambda snapshot: abs((snapshot.strike or underlying_price) - underlying_price))[:8]
    ranks = [_rank_snapshot(snapshot, underlying_price) for snapshot in candidates]
    return sorted(ranks, key=lambda rank: rank.score, reverse=True)[:limit]


def classify_greek_contract(snapshot: OptionGreekSnapshot, underlying_price: float | None) -> str:
    delta = abs(snapshot.delta.value or 0.0)
    theta = snapshot.theta.value
    theta_per_delta = abs(theta) / max(delta, SMALL_DELTA) if theta is not None else None
    dte = snapshot.dte
    moneyness = _moneyness(snapshot, underlying_price)
    if theta_per_delta is not None and theta_per_delta >= 0.65:
        return "Premium burn risk"
    if dte is not None and dte <= 7 and (0.35 <= delta <= 0.65 or (moneyness is not None and moneyness <= 0.025)):
        return "Short-dated speed trade"
    if delta >= 0.70:
        return "Stock-like directional exposure"
    if delta <= 0.25:
        return "Too far OTM unless expecting a fast move"
    if 0.45 <= delta <= 0.55:
        return "Balanced ATM contract"
    if snapshot.option_type == "put":
        return "Hedge / downside insurance"
    if dte is not None and dte <= 14:
        return "High-convexity lottery ticket"
    return "Directional option exposure"


def build_greek_decision_section(summary: GreekSummary, *, atr: float | None = None) -> str:
    active = active_greek_decision_snapshot(summary)
    if not summary.rows:
        return "Decision from the Greeks\n\nLoad the option chain to generate Greeks-based decision analysis."
    if active is None:
        return "Decision from the Greeks\n\nGreeks decision unavailable: no usable option contract loaded."
    if not _has_decision_greeks(active):
        return "Decision from the Greeks\n\nGreeks decision unavailable for the active contract: Schwab returned missing/sentinel values and local estimates could not be produced."

    symbol = (summary.underlying or active.underlying or "Underlying").upper()
    underlying_price = summary.underlying_price
    label = _contract_label(active)
    classification = classify_greek_contract(active, underlying_price)
    option_word = active.option_type.lower()
    direction_word = "rise" if option_word == "call" else "fall"
    direction_sign = "+" if option_word == "call" else "-"
    rows = greek_approximation_rows(active)
    offset = theta_offset_moves(active)
    premium = _snapshot_premium(active)
    ranks = rank_greek_contracts(summary, option_word)
    high_convexity = _highest_gamma_contract([rank.snapshot for rank in ranks]) if ranks else None

    intro_price = f"{symbol} is at {_money(underlying_price)}. " if underlying_price is not None else ""
    intro_moneyness = _moneyness_phrase(active, underlying_price)
    source_note = "Schwab-provided values are used where available; calculated estimates are labeled in the source mix."
    lines = [
        "Decision from the Greeks",
        "",
        f"The active contract is {label}. {intro_price}{intro_moneyness}",
        f"Classification: {classification}. This is decision support only; no order is submitted, previewed, staged, or executed.",
        f"Source mix: {active.source_summary}. {source_note}",
        "",
        f"The {active.strike:g} {option_word} means this:" if active.strike is not None else f"The active {option_word} means this:",
        "",
    ]
    lines.extend(_format_two_column_block("Greek", "Meaning in dollars", greek_dollar_meanings(active)))

    lines.extend(["", "Expected P/L from Greek approximation", "", "Assuming IV does not change.", ""])
    pnl_rows = [
        (_move_label(row.move), _money(row.one_day_pnl, digits=0), _money(row.full_window_pnl, digits=0))
        for row in rows
    ]
    lines.extend(_format_three_column_block(f"{symbol} move", "Approx P/L after 1 day", f"Approx P/L after {offset.full_window_days} days", pnl_rows))

    lines.extend(["", "Theta offset math:"])
    if offset.one_day_move is None or offset.full_window_move is None:
        lines.append("- Theta offset is unavailable because delta or theta is missing.")
    else:
        fallback_note = " DTE was unavailable, so the full-window line uses 5 days." if offset.used_dte_fallback else ""
        lines.append(f"- After 1 day, {symbol} needs to {direction_word} about {direction_sign}${offset.one_day_move:,.2f} just to offset theta.")
        lines.append(f"- Over {offset.full_window_days} days, it needs about {direction_sign}${offset.full_window_move:,.2f} just to offset estimated time decay, before considering premium.{fallback_note}")

    lines.extend(["", "Premium and breakeven:"])
    if premium is None:
        lines.append("- No premium = no final buy/no-buy call.")
        lines.append("- The Greeks can classify the contract, but they cannot prove the premium is worth paying without bid/ask/mark.")
    elif active.strike is None or underlying_price is None:
        lines.append(f"- Usable premium: {_money(premium)}. Breakeven distance is unavailable because strike or underlying price is missing.")
    else:
        breakeven = _breakeven(active, premium)
        distance = _breakeven_distance(active, underlying_price, premium)
        atr_text = _atr_distance_text(abs(distance), atr)
        lines.append(f"- Real expiration breakeven: {_money(breakeven)}.")
        lines.append(f"- Distance from current price: {_signed_money(distance)} for this {option_word}.{atr_text}")

    lines.extend(["", "What the chain is saying:"])
    lines.extend(_chain_read_lines(active, symbol))
    if summary.nearest_call is not None:
        lines.append(f"- Nearest ATM call in the loaded chain: {_rank_label(summary.nearest_call)}.")
    if summary.nearest_put is not None:
        lines.append(f"- Nearest ATM put in the loaded chain: {_rank_label(summary.nearest_put)}.")

    lines.extend(["", "My rank based on Greeks:"])
    if not ranks:
        lines.append("- Ranking unavailable because nearby contracts do not have usable delta/theta data.")
    else:
        for index, rank in enumerate(ranks, start=1):
            lines.append(f"{index}. {rank.label} - {rank.reason}.")

    lines.extend(["", "My actual conclusion from the Greeks:"])
    if ranks:
        lines.append(f"- Best {option_word} from this chain: {ranks[0].label}.")
    if high_convexity is not None:
        lines.append(f"- Best high-risk/high-convexity {option_word}: {_rank_label(high_convexity)}.")
    lines.append(f"- The active contract is a {classification.lower()}.")
    if option_word == "call":
        lines.append("- Do not touch far OTM calls unless explicitly betting on a fast upside move.")
        lines.append("- A good company can still be a bad short-dated option buy.")
    else:
        lines.append("- Do not touch far OTM puts unless explicitly betting on immediate downside or buying deliberate insurance.")
    if premium is None:
        lines.append("- No premium = no final buy/no-buy call.")
    elif offset.full_window_move is not None:
        lines.append(f"- Premium exists, so the real test is whether {symbol} can {direction_word} past breakeven before theta and IV changes eat the edge.")
    return "\n".join(lines)


def _format_two_column_block(header_a: str, header_b: str, rows: list[tuple[str, str]]) -> list[str]:
    width_a = max([len(header_a), *(len(str(row[0])) for row in rows)], default=len(header_a))
    width_b = max([len(header_b), *(len(str(row[1])) for row in rows)], default=len(header_b))
    lines = [
        f"{header_a:<{width_a}}  {header_b:<{width_b}}",
        f"{'-' * width_a}  {'-' * width_b}",
    ]
    lines.extend(f"{str(left):<{width_a}}  {str(right):<{width_b}}" for left, right in rows)
    return lines


def _format_three_column_block(header_a: str, header_b: str, header_c: str, rows: list[tuple[str, str, str]]) -> list[str]:
    width_a = max([len(header_a), *(len(str(row[0])) for row in rows)], default=len(header_a))
    width_b = max([len(header_b), *(len(str(row[1])) for row in rows)], default=len(header_b))
    width_c = max([len(header_c), *(len(str(row[2])) for row in rows)], default=len(header_c))
    lines = [
        f"{header_a:<{width_a}}  {header_b:>{width_b}}  {header_c:>{width_c}}",
        f"{'-' * width_a}  {'-' * width_b}  {'-' * width_c}",
    ]
    lines.extend(f"{str(first):<{width_a}}  {str(second):>{width_b}}  {str(third):>{width_c}}" for first, second, third in rows)
    return lines


def _snapshots_from_chain_row(row: dict[str, Any], underlying_price: float | None) -> list[OptionGreekSnapshot]:
    snapshots: list[OptionGreekSnapshot] = []
    for option_type in ("call", "put"):
        contract = row.get(option_type)
        if isinstance(contract, dict):
            snapshots.append(_snapshot_from_contract(row, contract, option_type, underlying_price))
    return snapshots


def _snapshot_from_contract(row: dict[str, Any], contract: dict[str, Any], option_type: str, underlying_price: float | None) -> OptionGreekSnapshot:
    underlying = str(row.get("underlying") or _first_text(contract, "underlying") or "").upper()
    contract_symbol = str(_first_text(contract, "symbol", "contractSymbol", "optionSymbol") or "")
    expiration = str(row.get("expiration_label") or _first_text(contract, "expirationDate") or row.get("expiration_date") or "--")
    dte = _first_int(row, "dte") or _first_int(contract, "daysToExpiration")
    strike = _first_number(contract, "strikePrice", "strike") or _to_float(row.get("strike"))
    bid = _first_number(contract, "bid")
    ask = _first_number(contract, "ask")
    mark = _first_number(contract, "mark", "markPrice")
    mark_warnings: list[str] = []
    if mark is None:
        mark = _midpoint(bid, ask)
        if mark is not None:
            mark_warnings.append("Mark is using the bid/ask midpoint because Schwab mark was absent.")

    iv_raw, iv_key = _first_number_with_key(contract, "impliedVolatility", "volatility", "theoreticalVolatility")
    iv = _normalise_implied_volatility(iv_raw)
    iv_source = SOURCE_SCHWAB if iv is not None else SOURCE_UNAVAILABLE
    if iv is None:
        estimated_iv = _estimate_implied_volatility(option_type, underlying_price, strike, dte, mark)
        if estimated_iv is not None:
            iv = estimated_iv
            iv_source = SOURCE_CALCULATED
            iv_key = "mark"

    calculated = _calculated_greeks(option_type, underlying_price, strike, dte, iv)
    theoretical_value = _theoretical_value(contract, calculated)
    delta = _greek_value(contract, "delta", calculated.get("delta"))
    gamma = _greek_value(contract, "gamma", calculated.get("gamma"))
    theta = _greek_value(contract, "theta", calculated.get("theta"))
    vega = _greek_value(contract, "vega", calculated.get("vega"))
    rho = _greek_value(contract, "rho", calculated.get("rho"))
    implied_volatility = GreekValue(iv, iv_source, iv_key)
    warnings = tuple(mark_warnings + _calculation_warnings(underlying_price, strike, dte, iv, delta, gamma, theta, vega, rho))
    source_summary = _source_summary((delta, gamma, theta, vega, rho, implied_volatility))
    return OptionGreekSnapshot(
        underlying=underlying,
        contract_symbol=contract_symbol,
        expiration=expiration,
        dte=dte,
        strike=strike,
        option_type=option_type,
        bid=bid,
        ask=ask,
        mark=mark,
        implied_volatility=implied_volatility,
        theoretical_value=theoretical_value,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho,
        source_summary=source_summary,
        warnings=warnings,
    )


def _greek_value(contract: dict[str, Any], field_name: str, calculated: float | None) -> GreekValue:
    value, key = _first_number_with_key(contract, field_name)
    if value is not None:
        return GreekValue(value, SOURCE_SCHWAB, key)
    if calculated is not None:
        return GreekValue(calculated, SOURCE_CALCULATED, "Black-Scholes")
    return GreekValue(None, SOURCE_UNAVAILABLE, "")


def _theoretical_value(contract: dict[str, Any], calculated: dict[str, float | None]) -> GreekValue:
    value, key = _first_number_with_key(contract, "theoreticalOptionValue", "theoreticalValue")
    if value is not None:
        return GreekValue(value, SOURCE_SCHWAB, key)
    calculated_value = calculated.get("theoretical_value")
    if calculated_value is not None:
        return GreekValue(calculated_value, SOURCE_CALCULATED, "Black-Scholes")
    return GreekValue(None, SOURCE_UNAVAILABLE, "")


def _calculated_greeks(
    option_type: str,
    underlying_price: float | None,
    strike: float | None,
    dte: int | None,
    implied_volatility: float | None,
    *,
    risk_free_rate: float = 0.05,
    dividend_yield: float = 0.0,
) -> dict[str, float | None]:
    if underlying_price is None or strike is None or dte is None or implied_volatility is None:
        return {}
    if underlying_price <= 0 or strike <= 0 or dte <= 0 or implied_volatility <= 0:
        return {}

    t = dte / 365.0
    sigma = implied_volatility
    sqrt_t = math.sqrt(t)
    d1 = (math.log(underlying_price / strike) + (risk_free_rate - dividend_yield + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc_q = math.exp(-dividend_yield * t)
    disc_r = math.exp(-risk_free_rate * t)
    pdf = _normal_pdf(d1)

    gamma = disc_q * pdf / (underlying_price * sigma * sqrt_t)
    vega = underlying_price * disc_q * pdf * sqrt_t / 100.0
    if option_type == "put":
        delta = disc_q * (_normal_cdf(d1) - 1.0)
        theta_annual = (-(underlying_price * disc_q * pdf * sigma) / (2.0 * sqrt_t) + risk_free_rate * strike * disc_r * _normal_cdf(-d2) - dividend_yield * underlying_price * disc_q * _normal_cdf(-d1))
        rho = -strike * t * disc_r * _normal_cdf(-d2) / 100.0
        value = strike * disc_r * _normal_cdf(-d2) - underlying_price * disc_q * _normal_cdf(-d1)
    else:
        delta = disc_q * _normal_cdf(d1)
        theta_annual = (-(underlying_price * disc_q * pdf * sigma) / (2.0 * sqrt_t) - risk_free_rate * strike * disc_r * _normal_cdf(d2) + dividend_yield * underlying_price * disc_q * _normal_cdf(d1))
        rho = strike * t * disc_r * _normal_cdf(d2) / 100.0
        value = underlying_price * disc_q * _normal_cdf(d1) - strike * disc_r * _normal_cdf(d2)

    return {
        "delta": delta,
        "gamma": gamma,
        "theta": theta_annual / 365.0,
        "vega": vega,
        "rho": rho,
        "theoretical_value": max(value, 0.0),
    }


def _estimate_implied_volatility(
    option_type: str,
    underlying_price: float | None,
    strike: float | None,
    dte: int | None,
    option_price: float | None,
    *,
    risk_free_rate: float = 0.05,
    dividend_yield: float = 0.0,
) -> float | None:
    if underlying_price is None or strike is None or dte is None or option_price is None:
        return None
    if underlying_price <= 0 or strike <= 0 or dte <= 0 or option_price <= 0:
        return None
    intrinsic = max(0.0, underlying_price - strike) if option_type == "call" else max(0.0, strike - underlying_price)
    if option_price < intrinsic:
        return None

    low = 0.0001
    high = 5.0
    low_price = _black_scholes_price(option_type, underlying_price, strike, dte / 365.0, low, risk_free_rate, dividend_yield)
    high_price = _black_scholes_price(option_type, underlying_price, strike, dte / 365.0, high, risk_free_rate, dividend_yield)
    if option_price < low_price or option_price > high_price:
        return None
    for _ in range(80):
        mid = (low + high) / 2.0
        model_price = _black_scholes_price(option_type, underlying_price, strike, dte / 365.0, mid, risk_free_rate, dividend_yield)
        if model_price < option_price:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _black_scholes_price(
    option_type: str,
    underlying_price: float,
    strike: float,
    t: float,
    sigma: float,
    risk_free_rate: float,
    dividend_yield: float,
) -> float:
    if t <= 0 or sigma <= 0:
        return max(0.0, underlying_price - strike) if option_type == "call" else max(0.0, strike - underlying_price)
    sqrt_t = math.sqrt(t)
    d1 = (math.log(underlying_price / strike) + (risk_free_rate - dividend_yield + 0.5 * sigma * sigma) * t) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    disc_q = math.exp(-dividend_yield * t)
    disc_r = math.exp(-risk_free_rate * t)
    if option_type == "put":
        return strike * disc_r * _normal_cdf(-d2) - underlying_price * disc_q * _normal_cdf(-d1)
    return underlying_price * disc_q * _normal_cdf(d1) - strike * disc_r * _normal_cdf(d2)


def _match_selected_snapshot(snapshots: list[OptionGreekSnapshot], selected_candidate: Any | None, selected_contract_symbol: str | None) -> OptionGreekSnapshot | None:
    requested_symbol = (selected_contract_symbol or str(getattr(selected_candidate, "contract_symbol", "") or "")).strip()
    if requested_symbol:
        match = next((snapshot for snapshot in snapshots if snapshot.contract_symbol and snapshot.contract_symbol == requested_symbol), None)
        if match is not None:
            return match
    if selected_candidate is None:
        return None
    candidate_type = str(getattr(selected_candidate, "option_type", "") or "").lower()
    candidate_strike = _to_float(getattr(selected_candidate, "strike", None))
    candidate_expiration = _normalise_expiration_label(str(getattr(selected_candidate, "expiration", "") or ""))
    matches = []
    for snapshot in snapshots:
        if candidate_type and snapshot.option_type != candidate_type:
            continue
        if candidate_strike is not None and snapshot.strike is not None and abs(snapshot.strike - candidate_strike) > 0.01:
            continue
        if candidate_expiration and _normalise_expiration_label(snapshot.expiration) != candidate_expiration:
            continue
        matches.append(snapshot)
    return matches[0] if matches else None


def _nearest_snapshot(snapshots: list[OptionGreekSnapshot], underlying_price: float | None, option_type: str) -> OptionGreekSnapshot | None:
    candidates = [snapshot for snapshot in snapshots if snapshot.option_type == option_type and snapshot.strike is not None]
    if not candidates:
        return None
    reference = underlying_price if underlying_price is not None and underlying_price > 0 else candidates[0].strike or 0.0
    return sorted(candidates, key=lambda item: (abs((item.strike or 0.0) - reference), item.dte if item.dte is not None else 99999))[0]


def _mark_selected(snapshot: OptionGreekSnapshot | None, selected: OptionGreekSnapshot | None) -> OptionGreekSnapshot | None:
    if snapshot is None:
        return None
    is_selected = selected is not None and _same_contract(snapshot, selected)
    if snapshot.selected == is_selected:
        return snapshot
    return OptionGreekSnapshot(**{**snapshot.__dict__, "selected": is_selected})


def _replace_from_marked(marked_rows: list[OptionGreekSnapshot], target: OptionGreekSnapshot | None) -> OptionGreekSnapshot | None:
    if target is None:
        return None
    return next((row for row in marked_rows if _same_contract(row, target)), target)


def _same_contract(left: OptionGreekSnapshot, right: OptionGreekSnapshot) -> bool:
    if left.contract_symbol and right.contract_symbol:
        return left.contract_symbol == right.contract_symbol
    return left.option_type == right.option_type and left.strike == right.strike and left.expiration == right.expiration


def _calculation_warnings(underlying_price: float | None, strike: float | None, dte: int | None, iv: float | None, *values: GreekValue) -> list[str]:
    if not any(value.source == SOURCE_UNAVAILABLE for value in values):
        return []
    missing = []
    if underlying_price is None or underlying_price <= 0:
        missing.append("underlying price")
    if strike is None or strike <= 0:
        missing.append("strike")
    if dte is None or dte <= 0:
        missing.append("days to expiration")
    if iv is None or iv <= 0:
        missing.append("implied volatility")
    return [f"Missing {', '.join(missing)} for calculated Greeks."] if missing else []


def _source_summary(values: tuple[GreekValue, ...]) -> str:
    sources = {value.source for value in values}
    if sources <= {SOURCE_SCHWAB}:
        return SOURCE_SCHWAB
    if sources <= {SOURCE_CALCULATED}:
        return SOURCE_CALCULATED
    if sources <= {SOURCE_UNAVAILABLE}:
        return SOURCE_UNAVAILABLE
    return "Mixed"


def _uses_source(snapshot: OptionGreekSnapshot, source: str) -> bool:
    return any(value.source == source for value in (snapshot.delta, snapshot.gamma, snapshot.theta, snapshot.vega, snapshot.rho, snapshot.implied_volatility))


def _summary_underlying(rows: list[OptionGreekSnapshot], selected_candidate: Any | None) -> str:
    if rows:
        return rows[0].underlying
    return str(getattr(selected_candidate, "underlying", "") or "").upper()


def _contract_label(snapshot: OptionGreekSnapshot) -> str:
    strike = "--" if snapshot.strike is None else f"{snapshot.strike:g}"
    return f"{snapshot.underlying or 'Option'} {snapshot.expiration} {strike} {snapshot.option_type.upper()}"


def _has_decision_greeks(snapshot: OptionGreekSnapshot) -> bool:
    return all(value.value is not None for value in (snapshot.delta, snapshot.gamma, snapshot.theta, snapshot.vega))


def _has_rankable_greeks(snapshot: OptionGreekSnapshot) -> bool:
    return snapshot.delta.value is not None and snapshot.theta.value is not None


def _full_window_days(snapshot: OptionGreekSnapshot, explicit_days: int | None = None) -> int:
    if explicit_days is not None and explicit_days > 0:
        return int(explicit_days)
    if snapshot.dte is not None and snapshot.dte > 0:
        return int(snapshot.dte)
    return 5


def _greek_approximation_pnl(delta: float, gamma: float, theta: float, move: float, days: int) -> float:
    return delta * move * 100.0 + 0.5 * gamma * move * move * 100.0 + theta * days * 100.0


def _rank_snapshot(snapshot: OptionGreekSnapshot, underlying_price: float | None) -> GreekContractRank:
    delta = abs(snapshot.delta.value or 0.0)
    theta = snapshot.theta.value
    theta_per_delta = None if theta is None else abs(theta) / max(delta, SMALL_DELTA)
    premium = _snapshot_premium(snapshot)
    breakeven = _breakeven(snapshot, premium) if premium is not None else None
    breakeven_distance = _breakeven_distance(snapshot, underlying_price, premium) if underlying_price is not None and premium is not None else None
    premium_efficiency = delta / premium if premium is not None and premium > 0 else None
    moneyness = _moneyness(snapshot, underlying_price)
    score = delta * 80.0
    if theta_per_delta is not None:
        score -= theta_per_delta * 35.0
    if premium_efficiency is not None:
        score += min(premium_efficiency * 60.0, 18.0)
    if breakeven_distance is not None and underlying_price is not None and underlying_price > 0:
        score -= max(breakeven_distance, 0.0) / underlying_price * 180.0
    if moneyness is not None:
        score -= moneyness * 35.0
    if snapshot.dte is not None and snapshot.dte <= 7:
        if 0.35 <= delta <= 0.75:
            score += 8.0
        elif delta < 0.25:
            score -= 12.0
    reason = _rank_reason(snapshot, theta_per_delta, premium, breakeven_distance, premium_efficiency)
    return GreekContractRank(snapshot, _rank_label(snapshot), score, theta_per_delta, premium, breakeven, breakeven_distance, premium_efficiency, reason)


def _rank_reason(snapshot: OptionGreekSnapshot, theta_per_delta: float | None, premium: float | None, breakeven_distance: float | None, premium_efficiency: float | None) -> str:
    parts = [f"delta {_signed_decimal(snapshot.delta.value, digits=3)}"]
    if theta_per_delta is not None:
        parts.append(f"theta/delta {theta_per_delta:.2f}")
    if premium is not None:
        parts.append(f"premium {_money(premium)}")
    if breakeven_distance is not None:
        parts.append(f"breakeven move {_signed_money(breakeven_distance)}")
    if premium_efficiency is not None:
        parts.append(f"delta per $1 premium {premium_efficiency:.2f}")
    return "; ".join(parts)


def _rank_label(snapshot: OptionGreekSnapshot) -> str:
    strike = "--" if snapshot.strike is None else f"{snapshot.strike:g}"
    return f"{strike} {snapshot.option_type}"


def _snapshot_premium(snapshot: OptionGreekSnapshot) -> float | None:
    for value in (snapshot.mark, _midpoint(snapshot.bid, snapshot.ask), snapshot.ask, snapshot.bid):
        if value is not None and value > 0:
            return value
    return None


def _breakeven(snapshot: OptionGreekSnapshot, premium: float | None) -> float | None:
    if snapshot.strike is None or premium is None:
        return None
    return snapshot.strike - premium if snapshot.option_type == "put" else snapshot.strike + premium


def _breakeven_distance(snapshot: OptionGreekSnapshot, underlying_price: float | None, premium: float | None) -> float | None:
    breakeven = _breakeven(snapshot, premium)
    if breakeven is None or underlying_price is None:
        return None
    return underlying_price - breakeven if snapshot.option_type == "put" else breakeven - underlying_price


def _moneyness(snapshot: OptionGreekSnapshot, underlying_price: float | None) -> float | None:
    if snapshot.strike is None or underlying_price is None or underlying_price <= 0:
        return None
    return abs(snapshot.strike - underlying_price) / underlying_price


def _moneyness_phrase(snapshot: OptionGreekSnapshot, underlying_price: float | None) -> str:
    if snapshot.strike is None or underlying_price is None or underlying_price <= 0:
        return "Moneyness is unavailable because strike or underlying price is missing."
    distance = snapshot.strike - underlying_price
    pct = abs(distance) / underlying_price
    if pct <= 0.01:
        return "It is basically at the money."
    if snapshot.option_type == "call":
        return f"It is in the money by {_money(abs(distance))}." if distance < 0 else f"It is out of the money by {_money(abs(distance))}."
    return f"It is in the money by {_money(abs(distance))}." if distance > 0 else f"It is out of the money by {_money(abs(distance))}."


def _highest_gamma_contract(snapshots: list[OptionGreekSnapshot]) -> OptionGreekSnapshot | None:
    with_gamma = [snapshot for snapshot in snapshots if snapshot.gamma.value is not None]
    if not with_gamma:
        return None
    return sorted(with_gamma, key=lambda snapshot: abs(snapshot.gamma.value or 0.0), reverse=True)[0]


def _chain_read_lines(snapshot: OptionGreekSnapshot, symbol: str) -> list[str]:
    lines: list[str] = []
    delta = snapshot.delta.value
    theta = snapshot.theta.value
    gamma = snapshot.gamma.value
    vega = snapshot.vega.value
    if delta is not None:
        abs_delta = abs(delta)
        if 0.45 <= abs_delta <= 0.55:
            lines.append(f"- Delta {_signed_decimal(delta, digits=3)} says the market is pricing this as a coin flip around the strike.")
        elif abs_delta >= 0.70:
            lines.append(f"- Delta {_signed_decimal(delta, digits=3)} is stock-like for an option; less lottery, more directional exposure.")
        elif abs_delta <= 0.25:
            lines.append(f"- Delta {_signed_decimal(delta, digits=3)} is low; this contract needs a fast move before it starts behaving like stock.")
        else:
            lines.append(f"- Delta {_signed_decimal(delta, digits=3)} gives directional exposure, but it is still not stock.")
    if theta is not None:
        dte_text = "with DTE unknown" if snapshot.dte is None else f"with only {snapshot.dte} days left"
        lines.append(f"- Theta is {_signed_money(theta * 100, digits=0)}/day {dte_text}. That decay is not background noise.")
    if gamma is not None:
        if snapshot.option_type == "call":
            lines.append(f"- Gamma means if {symbol} moves up quickly, delta can expand; if it drops, delta can collapse fast.")
        else:
            lines.append(f"- Gamma means if {symbol} drops quickly, put delta can expand; if it rallies, hedge value can collapse fast.")
    if vega is not None:
        lines.append(f"- Vega is {_signed_money(vega * 100, digits=0)} per vol point. IV expansion helps; IV crush takes money out.")
    if snapshot.rho.value is not None and snapshot.dte is not None and snapshot.dte <= 30:
        lines.append("- Rho is minor for this short-dated contract.")
    return lines


def _rho_meaning(rho: float | None, dte: int | None) -> str:
    if rho is None:
        return "--"
    if dte is not None and dte <= 30:
        return "basically irrelevant here"
    return f"{_signed_money(rho * 100, digits=0)} per +1 rate point"


def _atr_distance_text(distance: float | None, atr: float | None) -> str:
    if distance is None or atr is None or atr <= 0:
        return ""
    return f" That is {distance / atr:.1f} ATR."


def _move_label(value: float) -> str:
    if abs(value) < 0.005:
        return "$0"
    return f"{'+' if value > 0 else '-'}${abs(value):,.2f}".replace(".00", "")


def _signed_decimal(value: float | None, *, digits: int) -> str:
    if value is None:
        return "--"
    return f"{value:+.{digits}f}"


def _signed_points(value: float) -> str:
    formatted = f"{abs(value):.1f}".rstrip("0").rstrip(".")
    return f"{'-' if value < 0 else '+'}{formatted}"


def _signed_money(value: float | None, *, digits: int = 2) -> str:
    if value is None:
        return "--"
    rounded = round(value, digits)
    if abs(rounded) < (0.5 if digits == 0 else 0.005):
        return "$0" if digits == 0 else "$0.00"
    sign = "-" if rounded < 0 else "+"
    return f"{sign}${abs(rounded):,.{digits}f}".replace(".00", "" if digits == 0 else ".00")


def _money(value: float | None, *, digits: int = 2) -> str:
    if value is None:
        return "--"
    sign = "-$" if value < 0 else "$"
    return f"{sign}{abs(value):,.{digits}f}".replace(".00", "" if digits == 0 else ".00")


def _normalise_expiration_label(value: str) -> str:
    clean = value.lower().strip()
    if "(" in clean:
        clean = clean.split("(", 1)[0].strip()
    return " ".join(clean.split())


def _first_text(source: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _case_insensitive_get(source, key)
        if value not in (None, ""):
            return str(value)
    return None


def _first_number(source: dict[str, Any], *keys: str) -> float | None:
    value, _key = _first_number_with_key(source, *keys)
    return value


def _first_number_with_key(source: dict[str, Any], *keys: str) -> tuple[float | None, str]:
    for key in keys:
        value = _case_insensitive_get(source, key)
        if value in (None, ""):
            continue
        parsed = _to_float(value)
        if parsed is not None and not _is_missing_schwab_sentinel(parsed):
            return parsed, key
    return None, ""


def _first_int(source: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _case_insensitive_get(source, key)
        if value in (None, ""):
            continue
        try:
            parsed = float(str(value).replace(",", ""))
            if _is_missing_schwab_sentinel(parsed):
                continue
            return int(parsed)
        except (TypeError, ValueError):
            continue
    return None


def _case_insensitive_get(source: dict[str, Any], key: str) -> Any:
    if key in source:
        return source.get(key)
    key_lower = key.lower()
    for existing_key, value in source.items():
        if str(existing_key).lower() == key_lower:
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _is_missing_schwab_sentinel(value: Any) -> bool:
    parsed = _to_float(value)
    return parsed is not None and parsed <= -900.0


def _midpoint(bid: float | None, ask: float | None) -> float | None:
    if bid is not None and ask is not None and bid >= 0 and ask >= 0:
        return (bid + ask) / 2.0
    return bid if bid is not None and bid > 0 else ask if ask is not None and ask > 0 else None


def _normalise_implied_volatility(value: float | None) -> float | None:
    if value is None or value <= 0:
        return None
    if value > 3:
        return value / 100.0
    return value


def _normal_pdf(value: float) -> float:
    return math.exp(-0.5 * value * value) / math.sqrt(2.0 * math.pi)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))
