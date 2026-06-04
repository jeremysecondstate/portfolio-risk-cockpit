from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.analytics.research_workspace_insights import (
    OptionCandidate,
    combined_option_scenarios,
    covered_contract_capacity,
    option_expiration_payoff,
    suggest_option_candidates,
)
from app.analytics.stock_research import AdvancedIndicatorSnapshot, PortfolioSymbolContext
from app.ui.schwab_research_workspace_extension import _normalized_candidate_bar_rows


def _context(*, quantity: float, is_held: bool = True, last_price: float = 10.0) -> PortfolioSymbolContext:
    return PortfolioSymbolContext(
        symbol="RDW",
        is_held=is_held,
        quantity=quantity,
        average_cost=last_price,
        last_price=last_price,
        market_value=quantity * last_price,
        portfolio_value=100_000.0,
        portfolio_weight=(quantity * last_price) / 100_000.0,
        unrealized_pnl=0.0,
        day_pnl=0.0,
        cash_available=50_000.0,
    )


def _indicators(price: float = 10.0) -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        symbol="RDW",
        latest_close=price,
        sma_20=price,
        sma_50=price,
        sma_100=price,
        sma_200=price,
        ema_12=price,
        ema_26=price,
        macd=0.1,
        macd_signal=0.05,
        macd_histogram=0.05,
        rsi_14=55.0,
        bollinger_upper=price * 1.1,
        bollinger_middle=price,
        bollinger_lower=price * 0.9,
        atr_14=0.5,
        volume_average_20=100_000,
        week_52_high=price * 1.5,
        week_52_low=price * 0.5,
        swing_high=price * 1.2,
        swing_low=price * 0.8,
        fibonacci_levels={},
        trend="bullish",
        volatility="normal",
        momentum="steady",
        support=price * 0.9,
        resistance=price * 1.2,
        notes=[],
    )


def _chain_row(strike: float = 11.0, premium: float = 2.0) -> dict:
    return {
        "underlying": "RDW",
        "strike": strike,
        "expiration_label": "2026-07-17",
        "dte": 44,
        "call": {
            "bid": premium,
            "ask": premium,
            "mark": premium,
            "symbol": "RDW260717C00012000",
            "openInterest": 500,
            "totalVolume": 50,
            "delta": 0.3,
            "theta": -0.02,
            "impliedVolatility": 0.45,
        },
        "put": {
            "bid": 1.0,
            "ask": 1.0,
            "mark": 1.0,
            "symbol": "RDW260717P00012000",
            "openInterest": 500,
            "totalVolume": 50,
            "delta": -0.35,
            "theta": -0.02,
            "impliedVolatility": 0.45,
        },
    }


def _candidate(*, option_type: str = "call", covered: bool = False, contracts: int = 1, strike: float = 12.0, premium: float = 2.0) -> OptionCandidate:
    return OptionCandidate(
        key="candidate",
        group="Covered Call" if covered else "Long Option",
        strategy="Income / covered-call candidate" if covered else f"Long {option_type}",
        expiration="2026-07-17",
        strike=strike,
        option_type=option_type,
        bid=premium,
        ask=premium,
        mark=premium,
        midpoint=premium,
        max_loss=None if covered else premium * contracts * 100,
        max_gain=None,
        breakeven=10.0 - premium if covered else strike + premium if option_type == "call" else strike - premium,
        why="test",
        works_if="test",
        goes_wrong_if="test",
        relation_to_position="test",
        confidence="Watch",
        contract_symbol="TEST",
        underlying="RDW",
        underlying_price=10.0,
        contract_count=contracts,
        controlled_shares=contracts * 100,
    )


class OptionScenarioMathTests(unittest.TestCase):
    def test_partial_holding_does_not_generate_covered_call(self) -> None:
        candidates = suggest_option_candidates([_chain_row()], _indicators(), _context(quantity=67))
        self.assertFalse(any(candidate.group == "Covered Call" for candidate in candidates))

    def test_valid_covered_call_and_positive_down_move_read(self) -> None:
        candidates = suggest_option_candidates([_chain_row()], _indicators(), _context(quantity=100))
        covered = next(candidate for candidate in candidates if candidate.group == "Covered Call")
        self.assertEqual(covered.contract_count, 1)

        row = combined_option_scenarios(covered, _context(quantity=100), moves=(-0.10,))[0]
        self.assertGreater(row.combined_pnl, 0)
        self.assertIn("retained option premium exceeds stock loss", row.read)

    def test_multiple_contracts_use_floor_share_capacity(self) -> None:
        context = _context(quantity=250)
        self.assertEqual(covered_contract_capacity(context), 2)
        candidates = suggest_option_candidates([_chain_row()], _indicators(), context)
        covered = next(candidate for candidate in candidates if candidate.group == "Covered Call")
        self.assertEqual(covered.contract_count, 2)

        row = combined_option_scenarios(covered, context, moves=(-0.10,))[0]
        self.assertAlmostEqual(row.option_pnl, 400.0)
        self.assertAlmostEqual(row.stock_pnl, -250.0)
        self.assertAlmostEqual(row.combined_pnl, 150.0)

    def test_unheld_position_does_not_generate_covered_call(self) -> None:
        candidates = suggest_option_candidates([_chain_row()], _indicators(), _context(quantity=0, is_held=False))
        self.assertFalse(any(candidate.group == "Covered Call" for candidate in candidates))

    def test_expiration_payoff_formulas(self) -> None:
        short_call = _candidate(covered=True, strike=12.0, premium=2.0)
        self.assertAlmostEqual(option_expiration_payoff(short_call, 10.0), 200.0)
        self.assertAlmostEqual(option_expiration_payoff(short_call, 15.0), -100.0)

        long_call = _candidate(option_type="call", covered=False, strike=12.0, premium=2.0)
        self.assertAlmostEqual(option_expiration_payoff(long_call, 15.0), 100.0)

        long_put = _candidate(option_type="put", covered=False, strike=10.0, premium=1.0)
        self.assertAlmostEqual(option_expiration_payoff(long_put, 8.0), 100.0)

    def test_normalized_candidate_bar_rows_preserve_signs(self) -> None:
        rows = [
            SimpleNamespace(move_label="-5%", combined_pnl=-50.0),
            SimpleNamespace(move_label="0%", combined_pnl=0.0),
            SimpleNamespace(move_label="+5%", combined_pnl=100.0),
        ]
        normalized = _normalized_candidate_bar_rows(rows)
        self.assertLess(normalized[0][1], 0)
        self.assertEqual(normalized[1][1], 0)
        self.assertGreater(normalized[2][1], 0)
        self.assertEqual(normalized[0][2], "-$50.00")


if __name__ == "__main__":
    unittest.main()
