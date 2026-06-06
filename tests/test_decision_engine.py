from __future__ import annotations

import unittest

from app.analytics.decision_engine import build_thesis_readout
from app.analytics.stock_research import AdvancedIndicatorSnapshot, PortfolioSymbolContext


def _context(*, quantity: float = 0.0, price: float = 100.0, cash: float = 50_000.0) -> PortfolioSymbolContext:
    portfolio_value = 100_000.0
    return PortfolioSymbolContext(
        symbol="TEST",
        is_held=quantity > 0,
        quantity=quantity,
        average_cost=price if quantity else None,
        last_price=price,
        market_value=quantity * price,
        portfolio_value=portfolio_value,
        portfolio_weight=(quantity * price) / portfolio_value,
        unrealized_pnl=0.0 if quantity else None,
        day_pnl=0.0 if quantity else None,
        cash_available=cash,
    )


def _constructive_pullback_indicators() -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        symbol="TEST",
        latest_close=100.0,
        sma_20=103.0,
        sma_50=101.0,
        sma_100=94.0,
        sma_200=86.0,
        ema_12=101.0,
        ema_26=102.0,
        macd=-0.3,
        macd_signal=-0.1,
        macd_histogram=-0.2,
        rsi_14=40.0,
        bollinger_upper=112.0,
        bollinger_middle=104.0,
        bollinger_lower=96.0,
        atr_14=2.5,
        volume_average_20=100_000,
        week_52_high=130.0,
        week_52_low=70.0,
        swing_high=122.0,
        swing_low=97.0,
        fibonacci_levels={"50.0%": 99.5},
        trend="sideways",
        volatility="normal",
        momentum="weakening",
        support=98.0,
        resistance=110.0,
        notes=[],
    )


def _incomplete_indicators() -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        symbol="TEST",
        latest_close=None,
        sma_20=None,
        sma_50=None,
        sma_100=None,
        sma_200=None,
        ema_12=None,
        ema_26=None,
        macd=None,
        macd_signal=None,
        macd_histogram=None,
        rsi_14=None,
        bollinger_upper=None,
        bollinger_middle=None,
        bollinger_lower=None,
        atr_14=None,
        volume_average_20=None,
        week_52_high=None,
        week_52_low=None,
        swing_high=None,
        swing_low=None,
        fibonacci_levels={},
        trend="unknown",
        volatility="unknown",
        momentum="unknown",
        support=None,
        resistance=None,
        notes=[],
    )


def _fundamentals() -> str:
    return "\n".join(
        [
            "FUNDAMENTAL ANALYSIS - TEST",
            "Revenue:",
            "  Latest comparable-period change: +30.0%",
            "Net income:",
            "  Latest comparable-period change: +22.0%",
            "Operating income:",
            "  Latest comparable-period change: +18.0%",
            "Operating cash flow:",
            "  Latest comparable-period change: +16.0%",
            "Cockpit interpretation:",
            "- Revenue growth is strong.",
            "- Net income improved.",
            "- Operating income expanded.",
            "- Cash flow is positive.",
            "Source: SEC companyfacts XBRL JSON.",
        ]
    )


class DecisionEngineOverlayTests(unittest.TestCase):
    def test_constructive_pullback_includes_ev_votes_confidence_and_position_size(self) -> None:
        readout = build_thesis_readout(
            indicators=_constructive_pullback_indicators(),
            context=_context(),
            fundamentals_text=_fundamentals(),
            valuation_score=8.0,
            macro_score=12.0,
            earnings_risk_score=35.0,
            technical_score=-30.0,
            momentum_score=-18.0,
            macro_text="Official Macro Snapshot\nNeutral to slight tailwind.",
        )

        self.assertEqual(readout.setup_type, "pullback")
        self.assertEqual(readout.expected_value.label, "Positive EV")
        self.assertGreater(readout.expected_value.expected_value, 0)
        self.assertGreater(readout.position_sizing.target_shares, 0)
        self.assertEqual(readout.data_confidence.grade, "Medium")
        self.assertIn("options open-interest", " ".join(readout.data_confidence.missing))
        self.assertEqual(len(readout.evidence_votes), 8)
        self.assertIn("Supply absorption", {vote.name for vote in readout.evidence_votes})
        self.assertIn(readout.regime, {"range / mixed evidence", "volatility expansion"})

    def test_missing_price_and_risk_line_caps_confidence_and_blocks_sizing(self) -> None:
        readout = build_thesis_readout(
            indicators=_incomplete_indicators(),
            context=_context(price=0.0, cash=0.0),
            fundamentals_text="Unavailable.",
            valuation_score=None,
            macro_score=0.0,
            earnings_risk_score=35.0,
            technical_score=10.0,
            momentum_score=10.0,
            macro_text="",
        )

        self.assertEqual(readout.data_confidence.grade, "Low")
        self.assertEqual(readout.expected_value.label, "Incomplete")
        self.assertEqual(readout.position_sizing.target_shares, 0)
        self.assertEqual(readout.confidence, "Low")
        self.assertTrue(any("price" in item for item in readout.data_confidence.missing))


if __name__ == "__main__":
    unittest.main()
