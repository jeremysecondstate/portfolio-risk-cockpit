from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.analytics.stock_research import (
    DataSourceStatus,
    build_portfolio_symbol_context,
    build_scenario_rows,
    calculate_advanced_indicators,
    distance_to_price,
    fibonacci_retracements,
    load_cached_price_history,
    recommended_risk_budget,
    save_cached_price_history,
    suggested_position_size,
    technical_scenario_basis,
    technical_scenario_moves,
)
from app.analytics.research_scoring import (
    build_decision_readout,
    direction_strength_label,
    risk_heat_label,
    scenario_impact_bar_value,
    score_earnings_risk,
    score_macro_text,
    score_risk,
    score_technicals,
)
from app.analytics.technical_analysis import Candle
from app.core.portfolio import Portfolio, Position
from app.ui.schwab_research_workspace_extension import (
    _fetch_sec_layers,
    _source_status_text,
    selected_holding_symbol_from_values,
)
from app.ui.schwab_sync_report_extension import _is_temporary_schwab_provider_error, _schwab_account_refresh_failure_report


def _sample_candles(count: int = 260) -> list[Candle]:
    candles: list[Candle] = []
    for index in range(count):
        close = 100.0 + index * 0.45 + math.sin(index / 5) * 2.0
        candles.append(
            Candle(
                datetime_ms=1_700_000_000_000 + index * 86_400_000,
                open=close - 0.35,
                high=close + 1.25,
                low=close - 1.10,
                close=close,
                volume=1_000_000 + index * 1_000,
            )
        )
    return candles


class StockResearchAnalyticsTests(unittest.TestCase):
    def test_advanced_indicators_include_core_stack(self) -> None:
        candles = _sample_candles()
        snapshot = calculate_advanced_indicators("nvda", candles)
        closes = [candle.close for candle in candles]

        self.assertEqual(snapshot.symbol, "NVDA")
        self.assertAlmostEqual(snapshot.sma_20 or 0, sum(closes[-20:]) / 20)
        self.assertIsNotNone(snapshot.sma_200)
        self.assertIsNotNone(snapshot.ema_12)
        self.assertIsNotNone(snapshot.macd)
        self.assertIsNotNone(snapshot.rsi_14)
        self.assertIsNotNone(snapshot.bollinger_upper)
        self.assertIsNotNone(snapshot.atr_14)
        self.assertEqual(snapshot.trend, "bullish")
        self.assertIn("50.0%", snapshot.fibonacci_levels)

    def test_missing_price_history_is_graceful(self) -> None:
        snapshot = calculate_advanced_indicators("SPY", [])

        self.assertIsNone(snapshot.latest_close)
        self.assertEqual(snapshot.trend, "unknown")
        self.assertIn("Price history unavailable", snapshot.notes[0])

    def test_fibonacci_and_distance_helpers(self) -> None:
        levels = fibonacci_retracements(swing_high=110.0, swing_low=100.0)

        self.assertAlmostEqual(levels["50.0%"], 105.0)
        self.assertAlmostEqual(levels["61.8%"], 103.82)
        self.assertAlmostEqual(distance_to_price(100.0, 110.0) or 0, 0.10)

    def test_portfolio_context_and_scenario_math_for_held_symbol(self) -> None:
        portfolio = Portfolio(
            cash=9_000.0,
            positions={"NVDA": Position("NVDA", quantity=10, average_cost=80.0, last_price=100.0, open_profit_loss=200.0)},
        )
        context = build_portfolio_symbol_context(portfolio, "NVDA", fallback_price=100.0)
        rows = build_scenario_rows(context, moves=(-0.10, 0.10))

        self.assertTrue(context.is_held)
        self.assertAlmostEqual(context.portfolio_weight, 0.10)
        self.assertAlmostEqual(rows[0].position_pnl, -100.0)
        self.assertAlmostEqual(rows[0].portfolio_pnl_impact, -0.01)
        self.assertAlmostEqual(rows[1].new_portfolio_value, 10_100.0)

    def test_non_held_symbol_context_uses_quote_without_position_risk(self) -> None:
        portfolio = Portfolio(cash=10_000.0, positions={"GOOG": Position("GOOG", 2, 100.0, 150.0)})

        context = build_portfolio_symbol_context(portfolio, "SPY", fallback_price=500.0)
        rows = build_scenario_rows(context)

        self.assertFalse(context.is_held)
        self.assertEqual(context.market_value, 0.0)
        self.assertTrue(all(row.position_pnl == 0.0 for row in rows))

    def test_position_size_helper_uses_stop_distance(self) -> None:
        size = suggested_position_size(entry_price=100.0, stop_price=95.0, max_risk_dollars=250.0)

        self.assertAlmostEqual(size or 0, 50.0)

    def test_price_history_cache_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "history.json"
            payload = {"candles": [{"close": 1}]}
            with patch("app.analytics.stock_research.PRICE_HISTORY_CACHE_PATH", path):
                save_cached_price_history("NVDA", payload)
                cached = load_cached_price_history("nvda")

        self.assertEqual(cached, payload)

    def test_decision_score_mapping_for_bullish_low_weight_symbol(self) -> None:
        indicators = calculate_advanced_indicators("NVDA", _sample_candles())
        portfolio = Portfolio(cash=99_000.0, positions={"NVDA": Position("NVDA", 5, 120.0, indicators.latest_close or 200.0)})
        context = build_portfolio_symbol_context(portfolio, "NVDA", fallback_price=indicators.latest_close)
        rows = build_scenario_rows(context)

        readout = build_decision_readout(
            indicators=indicators,
            context=context,
            scenario_rows=rows,
            earnings_text="Latest earnings release showed revenue increased and guidance reaffirmed.",
            fundamentals_text="Revenue growth and free cash flow are positive.",
            macro_text="Inflation cooler and rates down; macro tailwind.",
            statuses=[DataSourceStatus("Schwab quote", "fresh", "2026-05-29T12:00:00+00:00")],
        )

        self.assertEqual(readout.overall.label, "Bullish")
        self.assertEqual(readout.position_impact.label, "Small")
        self.assertIn(readout.action_bias.label, {"Add Carefully", "Watch"})
        self.assertEqual(len(readout.top_things), 3)
        self.assertIn("Bias", readout.operator_view)

    def test_macro_and_earnings_risk_mappings(self) -> None:
        self.assertLess(score_macro_text("Inflation hotter and policy hawkish."), 0)
        self.assertGreater(score_macro_text("Inflation cooler and rates down tailwind."), 0)
        self.assertGreaterEqual(score_earnings_risk("Next earnings soon, within 10 trading days."), 70)
        self.assertEqual(score_earnings_risk("Earnings unavailable."), 50.0)

    def test_high_risk_mapping_from_weight_and_volatility(self) -> None:
        indicators = calculate_advanced_indicators("NOC", _sample_candles())
        elevated = indicators.__class__(**{**indicators.__dict__, "volatility": "elevated"})
        portfolio = Portfolio(cash=0.0, positions={"NOC": Position("NOC", 100, 100.0, elevated.latest_close or 200.0)})
        context = build_portfolio_symbol_context(portfolio, "NOC", fallback_price=elevated.latest_close)

        score = score_risk(elevated, context, 90.0, [DataSourceStatus("SEC", "fresh", "now")])

        self.assertGreaterEqual(score, 70)

    def test_scenario_impact_bar_value_is_normalized(self) -> None:
        portfolio = Portfolio(cash=9_000.0, positions={"NVDA": Position("NVDA", 10, 80.0, 100.0)})
        context = build_portfolio_symbol_context(portfolio, "NVDA", fallback_price=100.0)
        down, up = build_scenario_rows(context, moves=(-0.10, 0.10))

        self.assertAlmostEqual(scenario_impact_bar_value(down, 0.01), -100.0)
        self.assertAlmostEqual(scenario_impact_bar_value(up, 0.01), 100.0)

    def test_technical_scenario_moves_use_levels_not_user_input(self) -> None:
        indicators = calculate_advanced_indicators("NOC", _sample_candles())
        portfolio = Portfolio(cash=9_000.0, positions={"NOC": Position("NOC", 2, 100.0, indicators.latest_close or 100.0)})
        context = build_portfolio_symbol_context(portfolio, "NOC", fallback_price=indicators.latest_close)

        moves = technical_scenario_moves(context, indicators)
        basis = technical_scenario_basis(context, indicators)

        self.assertTrue(any(move < 0 for move in moves))
        self.assertTrue(any(move > 0 for move in moves))
        self.assertIn("ATR", basis)
        self.assertLess(max(abs(move) for move in moves), 0.36)

    def test_recommended_risk_budget_clamps_absurd_user_cap(self) -> None:
        indicators = calculate_advanced_indicators("NOC", _sample_candles())
        last = indicators.latest_close or 100.0
        portfolio = Portfolio(cash=9_000.0, positions={"NOC": Position("NOC", 1, 100.0, last)})
        context = build_portfolio_symbol_context(portfolio, "NOC", fallback_price=last)

        budget = recommended_risk_budget(context, indicators, requested_cap=500_000_000.0)

        self.assertIsNotNone(budget)
        self.assertLess(budget or 0, 500_000_000.0)
        self.assertLessEqual(budget or 0, context.portfolio_value * 0.005)

    def test_friendly_meter_labels_are_readable(self) -> None:
        self.assertEqual(direction_strength_label(87), "Very Strong")
        self.assertEqual(direction_strength_label(-12), "Leaning Bearish")
        self.assertEqual(risk_heat_label(78), "Hot")
        self.assertEqual(risk_heat_label(46), "Medium")

    def test_missing_data_produces_unknown_or_info_badges(self) -> None:
        indicators = calculate_advanced_indicators("SPY", [])
        context = build_portfolio_symbol_context(Portfolio(cash=10_000.0), "SPY", fallback_price=None)
        readout = build_decision_readout(
            indicators=indicators,
            context=context,
            scenario_rows=build_scenario_rows(context),
            earnings_text="Earnings unavailable.",
            fundamentals_text="Fundamentals unavailable.",
            macro_text="",
            statuses=[],
        )

        self.assertEqual(readout.trend.label, "Unknown")
        self.assertEqual(readout.valuation.label, "Unknown")
        self.assertTrue(readout.summary)
        self.assertEqual(score_technicals(indicators), 0.0)


class SchwabResearchWorkspaceHelperTests(unittest.TestCase):
    def test_selected_holding_symbol_from_tree_values(self) -> None:
        self.assertEqual(selected_holding_symbol_from_values(("nvda", "Equity")), "NVDA")
        self.assertEqual(selected_holding_symbol_from_values(()), "")

    def test_sec_failure_returns_status_instead_of_crashing(self) -> None:
        with patch("app.ui.schwab_research_workspace_extension.SecEdgarClient", side_effect=RuntimeError("offline")):
            earnings_text, fundamentals_text, filings_lines, statuses = _fetch_sec_layers("NVDA")

        self.assertIn("unavailable", earnings_text.lower())
        self.assertIn("unavailable", fundamentals_text.lower())
        self.assertTrue(filings_lines)
        self.assertTrue(all(status.status == "error" for status in statuses))

    def test_source_status_output_contains_source_and_timestamp(self) -> None:
        output = _source_status_text([DataSourceStatus("Schwab quote", "fresh", "2026-05-29T12:00:00+00:00", "loaded")])

        self.assertIn("Schwab quote", output)
        self.assertIn("2026-05-29T12:00:00+00:00", output)

    def test_schwab_http_500_is_treated_as_temporary_provider_failure(self) -> None:
        exc = RuntimeError("Schwab account fetch returned HTTP 500: unexpected error")

        self.assertTrue(_is_temporary_schwab_provider_error(exc))
        report = _schwab_account_refresh_failure_report(exc)
        self.assertIn("Kept the current local/cached portfolio visible", report)
        self.assertIn("Did not submit", report)


if __name__ == "__main__":
    unittest.main()
