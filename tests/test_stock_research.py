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
    save_cached_price_history,
    suggested_position_size,
)
from app.analytics.technical_analysis import Candle
from app.core.portfolio import Portfolio, Position
from app.ui.schwab_research_workspace_extension import (
    _fetch_sec_layers,
    _source_status_text,
    selected_holding_symbol_from_values,
)


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


if __name__ == "__main__":
    unittest.main()
