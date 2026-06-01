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


def build_greek_summary(
    chain_rows: list[dict[str, Any]],
    underlying_price: float | None,
    *,
    selected_candidate: Any | None = None,
    selected_contract_symbol: str | None = None,
) -> GreekSummary:
    """Normalize Schwab option-chain contracts into Greek snapshots.

    Schwab fields are used first. Missing Greeks are estimated with Black-Scholes
    when the contract has enough price/volatility inputs. Values that cannot be
    sourced or estimated remain explicitly unavailable.
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
    marked_rows = [
        _mark_selected(snapshot, primary)
        for snapshot in snapshots
    ]
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

    return GreekSummary(
        underlying=_summary_underlying(marked_rows, selected_candidate),
        underlying_price=underlying_price,
        rows=marked_rows,
        selected=selected,
        nearest_call=nearest_call,
        nearest_put=nearest_put,
        warnings=warnings,
        plain_english=plain_english_greek_readout(selected or nearest_call or nearest_put, underlying_price, warnings),
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

    contract_label = _contract_label(snapshot)
    lines = [f"{contract_label} is the active contract for the Greeks readout."]
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
    if snapshot.rho.value is not None:
        lines.append(f"Rho {snapshot.rho.value:+.3f} estimates the option-price impact from a one-point rate move.")
    lines.append(f"Source mix: {snapshot.source_summary}.")
    lines.extend(warnings)
    return lines


def _snapshots_from_chain_row(row: dict[str, Any], underlying_price: float | None) -> list[OptionGreekSnapshot]:
    snapshots: list[OptionGreekSnapshot] = []
    for option_type in ("call", "put"):
        contract = row.get(option_type)
        if isinstance(contract, dict):
            snapshots.append(_snapshot_from_contract(row, contract, option_type, underlying_price))
    return snapshots


def _snapshot_from_contract(
    row: dict[str, Any],
    contract: dict[str, Any],
    option_type: str,
    underlying_price: float | None,
) -> OptionGreekSnapshot:
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
        theta_annual = (
            -(underlying_price * disc_q * pdf * sigma) / (2.0 * sqrt_t)
            + risk_free_rate * strike * disc_r * _normal_cdf(-d2)
            - dividend_yield * underlying_price * disc_q * _normal_cdf(-d1)
        )
        rho = -strike * t * disc_r * _normal_cdf(-d2) / 100.0
        value = strike * disc_r * _normal_cdf(-d2) - underlying_price * disc_q * _normal_cdf(-d1)
    else:
        delta = disc_q * _normal_cdf(d1)
        theta_annual = (
            -(underlying_price * disc_q * pdf * sigma) / (2.0 * sqrt_t)
            - risk_free_rate * strike * disc_r * _normal_cdf(d2)
            + dividend_yield * underlying_price * disc_q * _normal_cdf(d1)
        )
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


def _match_selected_snapshot(
    snapshots: list[OptionGreekSnapshot],
    selected_candidate: Any | None,
    selected_contract_symbol: str | None,
) -> OptionGreekSnapshot | None:
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


def _nearest_snapshot(
    snapshots: list[OptionGreekSnapshot],
    underlying_price: float | None,
    option_type: str,
) -> OptionGreekSnapshot | None:
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


def _calculation_warnings(
    underlying_price: float | None,
    strike: float | None,
    dte: int | None,
    iv: float | None,
    *values: GreekValue,
) -> list[str]:
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
    if SOURCE_SCHWAB in sources and SOURCE_CALCULATED in sources:
        return "Schwab + calculated"
    if SOURCE_CALCULATED in sources:
        return SOURCE_CALCULATED
    if SOURCE_SCHWAB in sources:
        return SOURCE_SCHWAB
    return SOURCE_UNAVAILABLE


def _uses_source(snapshot: OptionGreekSnapshot, source: str) -> bool:
    return any(
        value.source == source
        for value in (snapshot.delta, snapshot.gamma, snapshot.theta, snapshot.vega, snapshot.rho, snapshot.implied_volatility)
    )


def _summary_underlying(rows: list[OptionGreekSnapshot], selected_candidate: Any | None) -> str:
    if rows:
        return rows[0].underlying
    return str(getattr(selected_candidate, "underlying", "") or "").upper()


def _contract_label(snapshot: OptionGreekSnapshot) -> str:
    strike = "--" if snapshot.strike is None else f"{snapshot.strike:g}"
    return f"{snapshot.underlying or 'Option'} {snapshot.expiration} {strike} {snapshot.option_type.upper()}"


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
        if parsed is not None:
            return parsed, key
    return None, ""


def _first_int(source: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _case_insensitive_get(source, key)
        if value in (None, ""):
            continue
        try:
            return int(float(str(value).replace(",", "")))
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
