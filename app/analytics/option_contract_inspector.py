from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
from typing import Any, Iterable, Mapping


_OCC_WITH_SPACE_RE = re.compile(r"^\s*([A-Z0-9.$]{1,12})\s+(\d{6})([CP])(\d{8})\s*$", re.IGNORECASE)
_OCC_COMPACT_RE = re.compile(r"^\s*([A-Z0-9.$]{1,12}?)(\d{6})([CP])(\d{8})\s*$", re.IGNORECASE)
_MISSING_TEXT = "--"


@dataclass(frozen=True)
class ParsedOptionContract:
    raw_symbol: str
    underlying: str
    expiration: date
    option_type: str
    strike: float
    dte: int

    @property
    def option_type_label(self) -> str:
        return "Call" if self.option_type == "call" else "Put"


@dataclass(frozen=True)
class OptionContractInspectorModel:
    raw_symbol: str
    asset_type: str
    parsed: ParsedOptionContract | None
    parse_warning: str
    underlying: str
    expiration_text: str
    chain_expiration_label: str
    dte: int | None
    option_type: str
    strike: float | None
    quantity: float | None
    last_price: float | None
    current_value: float | None
    pnl: float | None
    pnl_percent: float | None
    average_cost: float | None
    underlying_last: float | None
    moneyness: str
    distance_to_strike: float | None
    distance_percent: float | None
    moneyness_explanation: str
    time_bucket: str
    time_warning: str
    bid: float | None
    ask: float | None
    mark: float | None
    spread: float | None
    spread_percent: float | None
    volume: int | None
    open_interest: int | None
    liquidity_grade: str
    liquidity_warning: str
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    implied_volatility: float | None
    greeks_source: str
    position_risk_lines: list[str] = field(default_factory=list)
    what_can_go_wrong: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    posture: str = "NO-READ"
    summary: str = ""


def parse_occ_option_symbol(symbol: str, *, as_of: date | None = None) -> ParsedOptionContract | None:
    """Parse a Schwab/OCC-style option symbol such as 'CRSP 260605C00055000'."""

    raw_symbol = str(symbol or "").strip().upper()
    if not raw_symbol:
        return None

    match = _OCC_WITH_SPACE_RE.match(raw_symbol) or _OCC_COMPACT_RE.match(raw_symbol)
    if match is None:
        return None

    underlying, date_text, option_code, strike_text = match.groups()
    underlying = underlying.strip().upper()
    if not underlying:
        return None

    try:
        expiration = date(2000 + int(date_text[:2]), int(date_text[2:4]), int(date_text[4:6]))
    except ValueError:
        return None

    strike = int(strike_text) / 1000.0
    today = as_of or date.today()
    return ParsedOptionContract(
        raw_symbol=raw_symbol,
        underlying=underlying,
        expiration=expiration,
        option_type="call" if option_code.upper() == "C" else "put",
        strike=strike,
        dte=(expiration - today).days,
    )


def is_schwab_option_holding(values: Mapping[str, Any]) -> bool:
    """Return True when a Schwab holdings row looks like an option position."""

    normalized = {str(key).strip().lower(): value for key, value in dict(values or {}).items()}
    asset_text = " ".join(
        str(normalized.get(key) or "")
        for key in (
            "type",
            "asset_type",
            "assettype",
            "asset subtype",
            "assetsubtype",
            "instrument_type",
            "instrumenttype",
            "securitytype",
        )
    ).upper()
    if "OPTION" in asset_text:
        return True

    symbol = str(normalized.get("symbol") or "").strip()
    if parse_occ_option_symbol(symbol) is not None:
        return True

    metadata_text = " ".join(str(value) for value in normalized.values()).upper()
    return "OPTION VANILLA" in metadata_text or "PUTCALL" in metadata_text


def build_option_contract_inspector_model(
    holding_values: Mapping[str, Any],
    *,
    position: Any = None,
    portfolio_value: float | None = None,
    underlying_last: float | None = None,
    chain_rows: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None = None,
    as_of: date | None = None,
) -> OptionContractInspectorModel:
    """Build a plain data model for the Schwab option inspector popup."""

    values = dict(holding_values or {})
    raw_symbol = _first_text(values, "symbol") or str(getattr(position, "symbol", "") or "").strip().upper()
    asset_type = _first_text(values, "type", "asset_type", "assetType") or str(getattr(position, "asset_type", "") or "")
    parsed = parse_occ_option_symbol(raw_symbol, as_of=as_of)
    parse_warning = "" if parsed is not None else "Could not fully parse this option symbol."

    quantity = _first_number_from_values(values, "qty", "quantity")
    if quantity is None and position is not None:
        quantity = _to_float(getattr(position, "quantity", None))

    last_price = _first_number_from_values(values, "last", "last_price", "price")
    if last_price is None and position is not None:
        last_price = _to_float(getattr(position, "last_price", None))

    current_value = _first_number_from_values(values, "value", "market_value")
    if current_value is None and position is not None:
        current_value = _to_float(getattr(position, "market_value", None))

    pnl = _first_number_from_values(values, "pnl", "pnl_text", "unrealized_profit_loss")
    if pnl is None and position is not None:
        raw_pnl = _to_float(getattr(position, "open_profit_loss", None))
        pnl = raw_pnl if raw_pnl is not None else _to_float(getattr(position, "unrealized_profit_loss", None))

    average_cost = _to_float(getattr(position, "average_cost", None)) if position is not None else None
    pnl_percent = _pnl_percent(pnl, average_cost, quantity, current_value)

    normalized_chain_rows = _normalize_chain_rows(chain_rows)
    matched_row, matched_contract = _find_matching_chain_contract(parsed, raw_symbol, normalized_chain_rows)

    bid = _first_number(matched_contract, "bid", "bidPrice")
    ask = _first_number(matched_contract, "ask", "askPrice")
    mark = _first_number(matched_contract, "mark", "markPrice", "last", "lastPrice")
    if mark is None and bid is not None and ask is not None and ask >= bid:
        mark = (bid + ask) / 2.0
    spread = _spread(bid, ask)
    spread_percent = _spread_percent(spread, mark)
    volume = _first_int(matched_contract, "totalVolume", "volume")
    open_interest = _first_int(matched_contract, "openInterest", "open_interest")
    liquidity_grade, liquidity_warning = _liquidity_read(bid, ask, mark, spread_percent, volume, open_interest)

    contract_underlying = parsed.underlying if parsed is not None else _first_text(matched_row, "underlying") or raw_symbol
    row_underlying_price = _first_number(matched_row, "underlyingPrice", "underlying_price", "lastUnderlyingPrice")
    contract_underlying_price = _first_number(matched_contract, "underlyingPrice", "underlyingLastPrice")
    final_underlying_last = underlying_last if underlying_last is not None else row_underlying_price or contract_underlying_price

    moneyness, distance, distance_percent, moneyness_explanation = _moneyness_read(parsed, final_underlying_last)
    dte = parsed.dte if parsed is not None else _first_int(matched_row, "dte", "daysToExpiration")
    time_bucket, time_warning = _time_risk(dte)
    expiration_text = _expiration_text(parsed, matched_row)
    chain_expiration_label = _first_text(matched_row, "expiration_label") or expiration_text

    delta = _clean_greek(_first_number(matched_contract, "delta"))
    gamma = _clean_greek(_first_number(matched_contract, "gamma"))
    theta = _clean_greek(_first_number(matched_contract, "theta"))
    vega = _clean_greek(_first_number(matched_contract, "vega"))
    implied_volatility = _normalize_iv(_first_number(matched_contract, "impliedVolatility", "volatility", "theoreticalVolatility"))
    greeks_source = "Schwab option chain" if any(value is not None for value in (delta, gamma, theta, vega, implied_volatility)) else "Unavailable"

    flags = _risk_flags(parsed, dte, pnl, pnl_percent, liquidity_grade, quantity, parse_warning)
    position_risk_lines = _position_risk_lines(quantity, current_value, average_cost, portfolio_value, parsed)
    what_can_go_wrong = _what_can_go_wrong(parsed, dte, liquidity_grade, quantity, pnl)
    posture = _posture(parsed, dte, liquidity_grade, quantity, pnl_percent, parse_warning, matched_contract)

    model = OptionContractInspectorModel(
        raw_symbol=raw_symbol,
        asset_type=asset_type or "OPTION",
        parsed=parsed,
        parse_warning=parse_warning,
        underlying=contract_underlying,
        expiration_text=expiration_text,
        chain_expiration_label=chain_expiration_label,
        dte=dte,
        option_type=parsed.option_type_label if parsed is not None else _option_type_from_contract(matched_contract),
        strike=parsed.strike if parsed is not None else _first_number(matched_row, "strike"),
        quantity=quantity,
        last_price=last_price,
        current_value=current_value,
        pnl=pnl,
        pnl_percent=pnl_percent,
        average_cost=average_cost,
        underlying_last=final_underlying_last,
        moneyness=moneyness,
        distance_to_strike=distance,
        distance_percent=distance_percent,
        moneyness_explanation=moneyness_explanation,
        time_bucket=time_bucket,
        time_warning=time_warning,
        bid=bid,
        ask=ask,
        mark=mark,
        spread=spread,
        spread_percent=spread_percent,
        volume=volume,
        open_interest=open_interest,
        liquidity_grade=liquidity_grade,
        liquidity_warning=liquidity_warning,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        implied_volatility=implied_volatility,
        greeks_source=greeks_source,
        position_risk_lines=position_risk_lines,
        what_can_go_wrong=what_can_go_wrong,
        flags=flags,
        posture=posture,
    )
    return _with_summary(model)


def format_option_contract_plain_english(model: OptionContractInspectorModel) -> str:
    """Return a concise plain-English explanation for the inspector header/copy."""

    if model.parsed is None:
        return (
            f"{model.raw_symbol or 'This holding'} looks like an option position, but the contract symbol could not be fully parsed. "
            "Review the raw symbol, position size, P&L, and any available chain data before relying on the readout."
        )

    parsed = model.parsed
    direction = "bullish" if parsed.option_type == "call" else "bearish"
    benefits = "rises" if parsed.option_type == "call" else "falls"
    pnl_text = _pnl_plain_text(model.pnl)
    quantity_text = _quantity_plain_text(model.quantity)
    liquidity_text = model.liquidity_grade.lower() if model.liquidity_grade != "UNKNOWN" else "unknown"

    sentences = [
        (
            f"This is a {parsed.underlying} {_format_strike(parsed.strike)} {parsed.option_type} "
            f"expiring {_long_date(parsed.expiration)}."
        ),
        (
            f"{quantity_text}. It is a {direction} option that generally benefits if the stock {benefits}, "
            f"and time decay matters more as expiration gets closer."
        ),
        f"Current P&L is {pnl_text}, and liquidity/data quality is {liquidity_text}.",
    ]
    if model.moneyness != "Unknown":
        sentences.append(model.moneyness_explanation)
    if model.time_warning:
        sentences.append(model.time_warning)
    if model.liquidity_warning:
        sentences.append(model.liquidity_warning)
    return " ".join(sentence for sentence in sentences if sentence)


def _with_summary(model: OptionContractInspectorModel) -> OptionContractInspectorModel:
    return OptionContractInspectorModel(**{**model.__dict__, "summary": format_option_contract_plain_english(model)})


def _normalize_chain_rows(
    chain_rows: Iterable[Mapping[str, Any]] | Mapping[str, Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    if chain_rows is None:
        return []
    if isinstance(chain_rows, Mapping):
        return [row for row in chain_rows.values() if isinstance(row, Mapping)]
    return [row for row in chain_rows if isinstance(row, Mapping)]


def _find_matching_chain_contract(
    parsed: ParsedOptionContract | None,
    raw_symbol: str,
    chain_rows: list[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    normalized_symbol = _normalize_symbol(raw_symbol)
    fallback_row: Mapping[str, Any] = {}
    fallback_contract: Mapping[str, Any] = {}

    for row in chain_rows:
        for side in ("call", "put"):
            contract = row.get(side)
            if not isinstance(contract, Mapping):
                continue
            contract_symbol = _normalize_symbol(str(contract.get("symbol") or ""))
            if contract_symbol and contract_symbol == normalized_symbol:
                return row, contract

            if parsed is None or side != parsed.option_type:
                continue
            if fallback_contract:
                continue
            if _row_matches_parsed_contract(row, contract, parsed):
                fallback_row = row
                fallback_contract = contract

    return fallback_row, fallback_contract


def _row_matches_parsed_contract(row: Mapping[str, Any], contract: Mapping[str, Any], parsed: ParsedOptionContract) -> bool:
    underlying = str(row.get("underlying") or contract.get("underlying") or "").strip().upper()
    if underlying and underlying != parsed.underlying:
        return False

    row_strike = _first_number(row, "strike")
    if row_strike is None or abs(row_strike - parsed.strike) > 0.0001:
        return False

    row_expiration = _first_text(row, "expiration_date")
    contract_expiration = _first_text(contract, "expirationDate", "expiration")
    if row_expiration and row_expiration[:10] == parsed.expiration.isoformat():
        return True
    if contract_expiration and contract_expiration[:10] == parsed.expiration.isoformat():
        return True

    row_dte = _first_int(row, "dte", "daysToExpiration")
    return row_dte == parsed.dte


def _moneyness_read(
    parsed: ParsedOptionContract | None,
    underlying_last: float | None,
) -> tuple[str, float | None, float | None, str]:
    if parsed is None or underlying_last is None or parsed.strike <= 0:
        return "Unknown", None, None, "Underlying price is unavailable, so moneyness cannot be read."

    if parsed.option_type == "call":
        intrinsic_distance = underlying_last - parsed.strike
        threshold_word = "above"
    else:
        intrinsic_distance = parsed.strike - underlying_last
        threshold_word = "below"

    distance_percent = intrinsic_distance / parsed.strike * 100.0
    if abs(distance_percent) <= 1.0:
        label = "At the money"
    elif intrinsic_distance > 0:
        label = "In the money"
    else:
        label = "Out of the money"

    explanation = (
        f"This {parsed.option_type} needs the stock {threshold_word} {_money(parsed.strike)} "
        "by expiration to have intrinsic value."
    )
    if label == "Out of the money":
        explanation += " It is out of the money right now, so it needs the stock to move before expiration."
    return label, intrinsic_distance, distance_percent, explanation


def _time_risk(dte: int | None) -> tuple[str, str]:
    if dte is None:
        return "unknown", "Expiration timing is unavailable."
    if dte < 0:
        return "expired", "This contract appears to be past expiration. Confirm the symbol and account data."
    if dte == 0:
        return "same-day", "This contract expires today. Small stock moves and time decay can change the value quickly."
    if dte <= 7:
        return "weekly", "This contract is close to expiration. Small stock moves and time decay can change the value quickly."
    if dte <= 14:
        return "short-dated", "This is a short-dated option. Time decay can matter quickly."
    if dte <= 45:
        return "swing", "This has some time left, but theta still matters if the stock stalls."
    return "longer-dated", "This has a longer time window, though option value can still change quickly."


def _liquidity_read(
    bid: float | None,
    ask: float | None,
    mark: float | None,
    spread_percent: float | None,
    volume: int | None,
    open_interest: int | None,
) -> tuple[str, str]:
    if bid is None or ask is None or mark is None:
        return "UNKNOWN", "Bid/ask data is unavailable, so execution quality is unknown."
    if bid <= 0 or ask <= 0 or ask < bid or mark <= 0:
        return "BAD", "Bid/ask data looks unusable. The displayed value may not be reliable."
    if spread_percent is None:
        return "UNKNOWN", "Bid/ask spread quality is unavailable."

    low_activity = (volume is not None and volume < 10) or (open_interest is not None and open_interest < 50)
    if spread_percent >= 0.30:
        return "BAD", "The bid/ask spread is wide. The mark price may look better than what you can actually fill."
    if spread_percent >= 0.15 or low_activity:
        return "THIN", "Wide spreads mean the displayed value may be harder to realize."
    if spread_percent <= 0.05 and not low_activity:
        return "GOOD", "Bid/ask quality looks reasonable from the loaded chain data."
    return "OK", "Bid/ask quality is usable, but still review the live quote before acting."


def _position_risk_lines(
    quantity: float | None,
    current_value: float | None,
    average_cost: float | None,
    portfolio_value: float | None,
    parsed: ParsedOptionContract | None,
) -> list[str]:
    lines: list[str] = []
    abs_quantity = abs(quantity) if quantity is not None else None

    if current_value is not None:
        lines.append(f"Displayed position value: {_money(current_value)}.")
    else:
        lines.append("Displayed position value is unavailable.")

    if quantity is not None and quantity < 0:
        lines.append("Short option detected: assignment/exercise risk can exceed the displayed option value.")
    elif parsed is not None:
        lines.append("For a long option, the current displayed value is the main premium-at-risk estimate when debit basis is unknown.")

    if average_cost is not None and abs_quantity is not None:
        lines.append(f"Average cost shown by the portfolio source: {_money(average_cost)} across {abs_quantity:g} contract(s).")

    if current_value is not None and portfolio_value is not None and portfolio_value > 0:
        lines.append(f"Portfolio concentration: {abs(current_value) / portfolio_value:.2%} of loaded account value.")

    return lines


def _what_can_go_wrong(
    parsed: ParsedOptionContract | None,
    dte: int | None,
    liquidity_grade: str,
    quantity: float | None,
    pnl: float | None,
) -> list[str]:
    lines: list[str] = []
    if parsed is None:
        lines.append("The contract cannot be fully parsed, so expiration/strike/type may be wrong.")
    else:
        if parsed.option_type == "call":
            lines.append("If the stock does not move above the strike, the option can lose value or expire worthless.")
        else:
            lines.append("If the stock does not move below the strike, the option can lose value or expire worthless.")
    if dte is not None and dte <= 7:
        lines.append("Expiration is close, so theta and small stock moves can dominate the position.")
    if liquidity_grade in {"THIN", "BAD", "UNKNOWN"}:
        lines.append("Execution quality may be poor if spreads are wide or data is missing.")
    if quantity is not None and quantity < 0:
        lines.append("Short contracts can create assignment/exercise obligations.")
    if pnl is not None and pnl < 0:
        lines.append("The current P&L is negative; check whether the original reason for the trade still holds.")
    return lines


def _risk_flags(
    parsed: ParsedOptionContract | None,
    dte: int | None,
    pnl: float | None,
    pnl_percent: float | None,
    liquidity_grade: str,
    quantity: float | None,
    parse_warning: str,
) -> list[str]:
    flags: list[str] = []
    if parse_warning:
        flags.append(parse_warning)
    if dte is not None and dte <= 7:
        flags.append("Short DTE")
    if liquidity_grade in {"THIN", "BAD", "UNKNOWN"}:
        flags.append(f"Liquidity {liquidity_grade}")
    if quantity is not None and quantity < 0:
        flags.append("Short option exposure")
    if pnl_percent is not None and pnl_percent <= -40:
        flags.append("Large unrealized loss")
    elif pnl is not None and pnl < 0:
        flags.append("Negative P&L")
    if parsed is None:
        flags.append("No parsed contract identity")
    return flags


def _posture(
    parsed: ParsedOptionContract | None,
    dte: int | None,
    liquidity_grade: str,
    quantity: float | None,
    pnl_percent: float | None,
    parse_warning: str,
    matched_contract: Mapping[str, Any],
) -> str:
    if parsed is None:
        return "NO-READ"
    if not matched_contract and liquidity_grade == "UNKNOWN":
        return "CAUTIOUS"
    if quantity is not None and quantity < 0:
        return "DEFENSIVE"
    if dte is not None and dte <= 1:
        return "DEFENSIVE"
    if liquidity_grade == "BAD":
        return "DEFENSIVE"
    if pnl_percent is not None and pnl_percent <= -50:
        return "DEFENSIVE"
    if parse_warning or (dte is not None and dte <= 7) or liquidity_grade in {"THIN", "UNKNOWN"}:
        return "CAUTIOUS"
    return "NORMAL"


def _expiration_text(parsed: ParsedOptionContract | None, row: Mapping[str, Any]) -> str:
    if parsed is not None:
        return _long_date(parsed.expiration)
    row_text = _first_text(row, "expiration_label", "expiration_date", "expiration")
    return row_text or _MISSING_TEXT


def _option_type_from_contract(contract: Mapping[str, Any]) -> str:
    raw = _first_text(contract, "putCall", "optionType", "type").lower()
    if raw.startswith("p"):
        return "Put"
    if raw.startswith("c"):
        return "Call"
    return _MISSING_TEXT


def _pnl_percent(
    pnl: float | None,
    average_cost: float | None,
    quantity: float | None,
    current_value: float | None,
) -> float | None:
    if pnl is None:
        return None
    basis = None
    if average_cost is not None and quantity is not None:
        basis = abs(average_cost * quantity)
    if basis is None or basis <= 0:
        basis = abs(current_value or 0.0)
    if basis <= 0:
        return None
    return pnl / basis * 100.0


def _pnl_plain_text(pnl: float | None) -> str:
    if pnl is None:
        return "unknown"
    if pnl > 0:
        return "positive"
    if pnl < 0:
        return "negative"
    return "flat"


def _quantity_plain_text(quantity: float | None) -> str:
    if quantity is None:
        return "Contract quantity is unknown"
    abs_quantity = abs(quantity)
    plural = "contract" if abs_quantity == 1 else "contracts"
    if quantity < 0:
        return f"You are short {abs_quantity:g} {plural}"
    return f"You own {abs_quantity:g} {plural}"


def _spread(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or ask < bid:
        return None
    return ask - bid


def _spread_percent(spread: float | None, mark: float | None) -> float | None:
    if spread is None or mark is None or mark <= 0:
        return None
    return spread / mark


def _clean_greek(value: float | None) -> float | None:
    if value is None or abs(value + 999.0) < 0.0001:
        return None
    return value


def _normalize_iv(value: float | None) -> float | None:
    if value is None or abs(value + 999.0) < 0.0001:
        return None
    return value / 100.0 if value > 3 else value


def _normalize_symbol(symbol: str) -> str:
    return re.sub(r"\s+", "", str(symbol or "")).upper()


def _first_text(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _first_number_from_values(source: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _first_number(source: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        parsed = _to_float(source.get(key))
        if parsed is not None:
            return parsed
    return None


def _first_int(source: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        parsed = _to_float(source.get(key))
        if parsed is not None:
            return int(parsed)
    return None


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text == _MISSING_TEXT:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace("$", "").replace(",", "").replace("%", "").strip()
    if text.startswith("+"):
        text = text[1:]
    try:
        parsed = float(text)
    except ValueError:
        return None
    return -parsed if negative else parsed


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _long_date(value: date) -> str:
    return f"{value.strftime('%b')} {value.day}, {value.year}"


def _format_strike(value: float) -> str:
    if value.is_integer():
        return f"${int(value)}"
    return f"${value:,.2f}".rstrip("0").rstrip(".")
