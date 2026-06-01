from __future__ import annotations

import math
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from app.analytics.stock_research import (
    DataSourceStatus,
    GeneratedStockPosition,
    build_current_model_scenario_rows,
    build_planned_stock_context,
    build_portfolio_symbol_context,
    build_scenario_rows,
    calculate_advanced_indicators,
    distance_to_price,
    fibonacci_retracements,
    generated_risk_budget,
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
    _stock_plan_look_lines,
    _normalized_candidate_bar_rows,
    _source_status_text,
    selected_holding_symbol_from_values,
)
from app.ui.schwab_sync_report_extension import (
    _is_temporary_schwab_provider_error,
    _schwab_account_refresh_failure_report,
    _schwab_reauthorization_required_report,
    _should_force_schwab_reauthorization,
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

    def test_generated_risk_budget_uses_portfolio_cash_and_macro(self) -> None:
        indicators = calculate_advanced_indicators("LHX", _sample_candles())
        context = build_portfolio_symbol_context(Portfolio(cash=120_000.0), "LHX", fallback_price=indicators.latest_close)

        headwind = generated_risk_budget(
            context,
            indicators,
            macro_label="Headwind",
            risk_level_label="Medium",
            action_bias_label="Watch",
            earnings_text="No obvious earnings risk bullet was found.",
        )
        tailwind = generated_risk_budget(
            context,
            indicators,
            macro_label="Tailwind",
            risk_level_label="Low",
            action_bias_label="Add carefully",
            earnings_text="No obvious earnings risk bullet was found.",
        )

        self.assertIsNotNone(headwind.amount)
        self.assertIsNotNone(tailwind.amount)
        self.assertLess(headwind.amount or 0, tailwind.amount or 0)
        self.assertLessEqual(headwind.amount or 0, context.portfolio_value * 0.01)
        self.assertTrue(any("cash/liquidity" in factor for factor in headwind.factors))
        self.assertTrue(any("macro headwind" in factor for factor in headwind.factors))

    def test_generated_risk_budget_tight_cash_reduces_new_symbol_budget(self) -> None:
        indicators = calculate_advanced_indicators("LHX", _sample_candles())
        flush_context = build_portfolio_symbol_context(Portfolio(cash=80_000.0), "LHX", fallback_price=indicators.latest_close)
        tight_context = build_portfolio_symbol_context(
            Portfolio(cash=500.0, positions={"CASH_PROXY": Position("CASH_PROXY", 995, 100.0, 100.0)}),
            "LHX",
            fallback_price=indicators.latest_close,
        )

        flush_budget = generated_risk_budget(flush_context, indicators, macro_label="Neutral", risk_level_label="Medium", action_bias_label="Watch")
        tight_budget = generated_risk_budget(tight_context, indicators, macro_label="Neutral", risk_level_label="Medium", action_bias_label="Watch")

        self.assertIsNotNone(flush_budget.amount)
        self.assertIsNotNone(tight_budget.amount)
        self.assertLess(tight_budget.amount or 0, flush_budget.amount or 0)
        self.assertLessEqual(tight_budget.amount or 0, tight_context.cash_available * 0.05)

    def test_planned_stock_context_generates_watchlist_scenario_position(self) -> None:
        indicators = calculate_advanced_indicators("LHX", _sample_candles())
        context = build_portfolio_symbol_context(Portfolio(cash=120_000.0), "LHX", fallback_price=indicators.latest_close)
        risk_budget = generated_risk_budget(
            context,
            indicators,
            macro_label="Headwind",
            risk_level_label="Medium",
            action_bias_label="Watch",
            earnings_text="No obvious earnings risk bullet was found.",
        )

        planned_context, stock_plan = build_planned_stock_context(context, indicators, risk_budget)
        rows = build_scenario_rows(planned_context, moves=(-0.10, 0.10))

        self.assertFalse(planned_context.is_held)
        self.assertGreater(stock_plan.quantity, 0)
        self.assertGreater(stock_plan.notional, 0)
        self.assertLess(stock_plan.notional, context.cash_available)
        self.assertTrue(any(abs(row.position_pnl) > 0 for row in rows))
        self.assertIn("Generated watchlist stock plan", stock_plan.basis)

    def test_planned_stock_context_keeps_actual_held_quantity(self) -> None:
        indicators = calculate_advanced_indicators("NVDA", _sample_candles())
        last = indicators.latest_close or 100.0
        context = build_portfolio_symbol_context(Portfolio(cash=20_000.0, positions={"NVDA": Position("NVDA", 15, 90.0, last)}), "NVDA", fallback_price=last)
        risk_budget = generated_risk_budget(context, indicators, macro_label="Neutral", risk_level_label="Medium", action_bias_label="Watch")

        planned_context, stock_plan = build_planned_stock_context(context, indicators, risk_budget)

        self.assertIs(planned_context, context)
        self.assertEqual(stock_plan.quantity, 15)
        self.assertIn("Current held shares", stock_plan.basis)

    def test_current_model_scenario_rows_show_watchlist_current_zero_and_model_path(self) -> None:
        context = build_portfolio_symbol_context(Portfolio(cash=50_000.0), "LHX", fallback_price=100.0)
        model = GeneratedStockPosition(22.0, 100.0, 95.0, 110.0, 2_200.0, 0.044, 5.0, "Generated watchlist stock plan.")

        down, up = build_current_model_scenario_rows(context, model, moves=(-0.10, 0.10))

        self.assertEqual(down.current_shares, 0.0)
        self.assertEqual(down.current_position_pnl, 0.0)
        self.assertEqual(up.current_position_pnl, 0.0)
        self.assertAlmostEqual(down.model_position_pnl or 0.0, -220.0)
        self.assertAlmostEqual(up.model_position_pnl or 0.0, 220.0)

    def test_current_model_scenario_rows_compute_held_and_model_independently(self) -> None:
        context = build_portfolio_symbol_context(
            Portfolio(cash=10_000.0, positions={"NVDA": Position("NVDA", 15, 90.0, 100.0)}),
            "NVDA",
            fallback_price=100.0,
        )
        model = GeneratedStockPosition(5.0, 100.0, 95.0, 25.0, 500.0, 0.05, 5.0, "Separate model plan.")

        row = build_current_model_scenario_rows(context, model, moves=(-0.10,))[0]

        self.assertAlmostEqual(row.current_position_pnl, -150.0)
        self.assertAlmostEqual(row.model_position_pnl or 0.0, -50.0)
        self.assertNotEqual(row.current_position_pnl, row.model_position_pnl)

    def test_current_model_scenario_rows_tolerate_missing_model_size(self) -> None:
        context = build_portfolio_symbol_context(Portfolio(cash=50_000.0), "LHX", fallback_price=100.0)
        model = GeneratedStockPosition(0.0, 100.0, None, None, 0.0, 0.0, None, "Insufficient price or risk budget.")

        row = build_current_model_scenario_rows(context, model, moves=(0.10,))[0]

        self.assertEqual(row.current_position_pnl, 0.0)
        self.assertIsNone(row.model_position_pnl)
        self.assertIsNone(row.model_portfolio_pnl_impact)

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

    def test_zero_option_scenario_bars_do_not_divide_by_zero(self) -> None:
        rows = [
            SimpleNamespace(move_label="-5%", combined_pnl=0.0),
            SimpleNamespace(move_label="+5%", combined_pnl=0.0),
        ]

        bars = _normalized_candidate_bar_rows(rows)

        self.assertEqual([row[1] for row in bars], [0.0, 0.0])
        self.assertEqual([row[2] for row in bars], ["$0.00", "$0.00"])

    def test_decision_difference_stock_looks_use_generated_watchlist_size(self) -> None:
        indicators = calculate_advanced_indicators("LHX", _sample_candles())
        context = build_portfolio_symbol_context(Portfolio(cash=120_000.0), "LHX", fallback_price=indicators.latest_close)
        risk_budget = generated_risk_budget(context, indicators, macro_label="Headwind", risk_level_label="Medium", action_bias_label="Watch")
        planned_context, stock_plan = build_planned_stock_context(context, indicators, risk_budget)

        lines = _stock_plan_look_lines(planned_context, stock_plan)

        self.assertTrue(lines)
        self.assertTrue(any("shares" in line for line in lines))
        self.assertFalse(any("$0.00 notional" in line for line in lines))

    def test_schwab_http_500_forces_reauthorization_instead_of_token_loop(self) -> None:
        exc = RuntimeError("Schwab account fetch returned HTTP 500: unexpected error")

        self.assertTrue(_should_force_schwab_reauthorization(exc))
        self.assertFalse(_is_temporary_schwab_provider_error(exc))
        report = _schwab_reauthorization_required_report(exc)
        self.assertIn("Cleared the in-memory Schwab session", report)
        self.assertIn("Opened the Schwab authorization page", report)

    def test_schwab_temporary_outage_report_keeps_portfolio_safe(self) -> None:
        exc = RuntimeError("Schwab account endpoint temporarily unavailable")

        self.assertTrue(_is_temporary_schwab_provider_error(exc))
        report = _schwab_account_refresh_failure_report(exc)
        self.assertIn("Kept the current local/cached portfolio visible", report)
        self.assertIn("Did not submit", report)


if __name__ == "__main__":
    unittest.main()
