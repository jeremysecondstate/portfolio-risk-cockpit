from __future__ import annotations

from dataclasses import dataclass

from app.analytics.trade_thesis import OptionChainCandidate, OptionChainContext


@dataclass(frozen=True)
class ThesisOptionTicket:
    symbol: str
    strategy: str
    action: str
    option_type: str
    expiration: str
    contracts: int
    strike: float
    short_strike: float
    bid: float | None
    ask: float | None
    mark: float | None
    premium: float
    credit: float
    breakeven: float
    max_loss: float
    max_reward: float | None
    summary: str


def build_thesis_option_ticket(
    *,
    symbol: str,
    directional_bias: str,
    option_context: OptionChainContext | None,
    spot_price: float,
) -> ThesisOptionTicket | None:
    if option_context is None or not option_context.has_rows:
        return None

    if "bearish" in directional_bias or "cautious" in directional_bias:
        return _build_bearish_ticket(symbol=symbol, option_context=option_context, spot_price=spot_price)
    return _build_bullish_ticket(symbol=symbol, option_context=option_context, spot_price=spot_price)


def _build_bullish_ticket(symbol: str, option_context: OptionChainContext, spot_price: float) -> ThesisOptionTicket | None:
    calls = [candidate for candidate in option_context.candidates if candidate.side == "call" and candidate.ask_or_mark > 0]
    if not calls:
        return None

    # Match the Options planning layer: for upside-favored theses, prefer the nearest
    # loaded call strike above spot/resistance, then choose the tighter/more liquid expiry.
    long_target = _nearest_directional_strike(calls, spot_price, prefer_above=True)
    long_leg = sorted(calls, key=lambda candidate: _long_leg_score(candidate, long_target, spot_price))[0]
    short_candidates = [
        candidate
        for candidate in calls
        if candidate.expiration == long_leg.expiration and candidate.strike > long_leg.strike
    ]
    short_leg = sorted(short_candidates, key=lambda candidate: (candidate.strike - long_leg.strike, candidate.spread, -(candidate.volume or 0)))[0] if short_candidates else None
    if short_leg is None:
        debit = long_leg.ask_or_mark
        breakeven = long_leg.strike + debit
        return ThesisOptionTicket(
            symbol=symbol,
            strategy="Long Call",
            action="Buy",
            option_type="Call",
            expiration=long_leg.expiration,
            contracts=1,
            strike=long_leg.strike,
            short_strike=long_leg.strike,
            bid=long_leg.bid,
            ask=long_leg.ask,
            mark=long_leg.mark,
            premium=debit,
            credit=0.0,
            breakeven=breakeven,
            max_loss=debit * 100,
            max_reward=None,
            summary=(
                f"Long call: buy {long_leg.expiration} {long_leg.strike:g} CALL; "
                f"breakeven about ${breakeven:,.2f}. Matches the Options planning layer candidate."
            ),
        )

    debit = max(long_leg.ask_or_mark - short_leg.bid_or_mark, 0.0)
    width = short_leg.strike - long_leg.strike
    breakeven = long_leg.strike + debit
    max_reward = max(width - debit, 0.0) * 100
    return ThesisOptionTicket(
        symbol=symbol,
        strategy="Vertical Debit Spread",
        action="Buy",
        option_type="Call",
        expiration=long_leg.expiration,
        contracts=1,
        strike=long_leg.strike,
        short_strike=short_leg.strike,
        bid=long_leg.bid,
        ask=long_leg.ask,
        mark=long_leg.mark,
        premium=debit,
        credit=short_leg.bid_or_mark,
        breakeven=breakeven,
        max_loss=debit * 100,
        max_reward=max_reward,
        summary=(
            f"Bull call debit spread: buy {long_leg.strike:g} CALL / sell {short_leg.strike:g} CALL "
            f"{long_leg.expiration}; breakeven about ${breakeven:,.2f}. "
            "Matches the Options planning layer candidate."
        ),
    )


def _build_bearish_ticket(symbol: str, option_context: OptionChainContext, spot_price: float) -> ThesisOptionTicket | None:
    puts = [candidate for candidate in option_context.candidates if candidate.side == "put" and candidate.ask_or_mark > 0]
    if not puts:
        return None

    # Match the Options planning layer: for downside-favored theses, prefer the nearest
    # loaded put strike below spot/support, then choose the tighter/more liquid expiry.
    long_target = _nearest_directional_strike(puts, spot_price, prefer_above=False)
    long_leg = sorted(puts, key=lambda candidate: _long_leg_score(candidate, long_target, spot_price))[0]
    short_candidates = [
        candidate
        for candidate in puts
        if candidate.expiration == long_leg.expiration and candidate.strike < long_leg.strike
    ]
    short_leg = sorted(short_candidates, key=lambda candidate: (long_leg.strike - candidate.strike, candidate.spread, -(candidate.volume or 0)))[0] if short_candidates else None
    if short_leg is None:
        debit = long_leg.ask_or_mark
        breakeven = long_leg.strike - debit
        return ThesisOptionTicket(
            symbol=symbol,
            strategy="Long Put",
            action="Buy",
            option_type="Put",
            expiration=long_leg.expiration,
            contracts=1,
            strike=long_leg.strike,
            short_strike=long_leg.strike,
            bid=long_leg.bid,
            ask=long_leg.ask,
            mark=long_leg.mark,
            premium=debit,
            credit=0.0,
            breakeven=breakeven,
            max_loss=debit * 100,
            max_reward=None,
            summary=(
                f"Long put: buy {long_leg.expiration} {long_leg.strike:g} PUT; "
                f"breakeven about ${breakeven:,.2f}. Matches the Options planning layer candidate."
            ),
        )

    debit = max(long_leg.ask_or_mark - short_leg.bid_or_mark, 0.0)
    width = long_leg.strike - short_leg.strike
    breakeven = long_leg.strike - debit
    max_reward = max(width - debit, 0.0) * 100
    return ThesisOptionTicket(
        symbol=symbol,
        strategy="Vertical Debit Spread",
        action="Buy",
        option_type="Put",
        expiration=long_leg.expiration,
        contracts=1,
        strike=long_leg.strike,
        short_strike=short_leg.strike,
        bid=long_leg.bid,
        ask=long_leg.ask,
        mark=long_leg.mark,
        premium=debit,
        credit=short_leg.bid_or_mark,
        breakeven=breakeven,
        max_loss=debit * 100,
        max_reward=max_reward,
        summary=(
            f"Bear put debit spread: buy {long_leg.strike:g} PUT / sell {short_leg.strike:g} PUT "
            f"{long_leg.expiration}; breakeven about ${breakeven:,.2f}. "
            "Matches the Options planning layer candidate."
        ),
    )


def _nearest_directional_strike(candidates: list[OptionChainCandidate], spot_price: float, *, prefer_above: bool) -> float:
    strikes = sorted({candidate.strike for candidate in candidates if candidate.strike > 0})
    if prefer_above:
        directional = [strike for strike in strikes if strike > spot_price]
        return directional[0] if directional else min(strikes, key=lambda strike: abs(strike - spot_price))
    directional = [strike for strike in strikes if strike < spot_price]
    return directional[-1] if directional else min(strikes, key=lambda strike: abs(strike - spot_price))


def _long_leg_score(candidate: OptionChainCandidate, target: float, spot_price: float) -> tuple[float, float, int, float]:
    return (
        abs(candidate.strike - target),
        candidate.spread,
        -(candidate.volume or 0),
        abs(candidate.strike - spot_price),
    )


@property
def _ask_or_mark(self: OptionChainCandidate) -> float:
    return self.ask if self.ask is not None and self.ask > 0 else self.mark or 0.0


@property
def _bid_or_mark(self: OptionChainCandidate) -> float:
    return self.bid if self.bid is not None and self.bid > 0 else self.mark or 0.0


@property
def _spread(self: OptionChainCandidate) -> float:
    if self.bid is None or self.ask is None:
        return 9999.0
    return max(self.ask - self.bid, 0.0)


# Attach computed properties without changing the shared dataclass shape.
OptionChainCandidate.ask_or_mark = _ask_or_mark  # type: ignore[attr-defined]
OptionChainCandidate.bid_or_mark = _bid_or_mark  # type: ignore[attr-defined]
OptionChainCandidate.spread = _spread  # type: ignore[attr-defined]
