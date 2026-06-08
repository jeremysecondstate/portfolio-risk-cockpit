from __future__ import annotations

import unittest

from app.analytics.stock_research import (
    AdvancedIndicatorSnapshot,
    GeneratedStockPosition,
    GeneratedRiskBudget,
    PortfolioSymbolContext,
    build_current_model_scenario_rows,
    build_stop_ladder_plan,
    build_planned_stock_context,
)


def _context(*, quantity: float = 32.0, price: float = 53.13, cash: float = 102_989.39) -> PortfolioSymbolContext:
    market_value = quantity * price
    portfolio_value = 160_193.03
    return PortfolioSymbolContext(
        symbol="HPE",
        is_held=quantity > 0,
        quantity=quantity,
        average_cost=50.0 if quantity else None,
        last_price=price,
        market_value=market_value,
        portfolio_value=portfolio_value,
        portfolio_weight=market_value / portfolio_value,
        unrealized_pnl=0.0,
        day_pnl=0.0,
        cash_available=cash,
    )


def _indicators(*, price: float = 53.13, stop: float = 49.60, support: float | None = None) -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        symbol="HPE",
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
        bollinger_upper=price * 1.08,
        bollinger_middle=price,
        bollinger_lower=price * 0.92,
        atr_14=price - stop,
        volume_average_20=100_000,
        week_52_high=price * 1.3,
        week_52_low=price * 0.7,
        swing_high=price * 1.1,
        swing_low=stop,
        fibonacci_levels={},
        trend="bullish",
        volatility="elevated",
        momentum="improving",
        support=stop if support is None else support,
        resistance=price * 1.03,
        notes=[],
    )


def _risk_budget(amount: float = 27.0) -> GeneratedRiskBudget:
    return GeneratedRiskBudget(
        amount=amount,
        base_amount=amount,
        technical_amount=amount,
        portfolio_cap=1_000.0,
        cash_cap=2_000.0,
        factors=("test",),
    )


def _model_position(*, quantity: float, entry: float, stop: float) -> GeneratedStockPosition:
    return GeneratedStockPosition(
        quantity=quantity,
        entry_price=entry,
        stop_price=stop,
        risk_dollars=None,
        notional=quantity * entry,
        portfolio_weight=0.0,
        per_share_risk=entry - stop,
        basis="test model position",
    )


class StockResearchSizingTests(unittest.TestCase):
    def test_held_position_model_target_is_risk_sized_instead_of_copied(self) -> None:
        context = _context(quantity=32.0)
        planned_context, model_position = build_planned_stock_context(context, _indicators(), _risk_budget(27.0))

        self.assertEqual(model_position.quantity, 7)
        self.assertNotEqual(model_position.quantity, context.quantity)
        self.assertEqual(planned_context.quantity, 7)
        self.assertAlmostEqual(model_position.per_share_risk or 0.0, 3.53)
        self.assertAlmostEqual(model_position.notional, 371.91)
        self.assertAlmostEqual(context.market_value, 1_700.16)
        self.assertIn("Current actual shares: 32", model_position.basis)
        self.assertIn("model target shares: 7", model_position.basis)
        self.assertIn("$27.00 budget / $3.53 per-share risk", model_position.basis)

    def test_current_model_scenario_rows_compare_actual_against_model_target(self) -> None:
        context = _context(quantity=32.0)
        _, model_position = build_planned_stock_context(context, _indicators(), _risk_budget(27.0))

        row = build_current_model_scenario_rows(context, model_position, moves=(-0.10,))[0]

        self.assertEqual(row.current_shares, 32.0)
        self.assertEqual(row.model_shares, 7.0)
        self.assertAlmostEqual(row.current_position_pnl, -170.016)
        self.assertAlmostEqual(row.model_position_pnl or 0.0, -37.191)
        self.assertNotEqual(row.current_position_pnl, row.model_position_pnl)

    def test_unheld_position_keeps_watchlist_model_sizing(self) -> None:
        context = _context(quantity=0.0)
        planned_context, model_position = build_planned_stock_context(context, _indicators(), _risk_budget(27.0))

        self.assertFalse(planned_context.is_held)
        self.assertEqual(model_position.quantity, 7)
        self.assertIn("Current actual shares: 0", model_position.basis)
        self.assertIn("model target shares: 7", model_position.basis)

    def test_two_share_stop_ladder_compares_tactical_and_thesis_risk(self) -> None:
        model_position = _model_position(quantity=2, entry=941.39, stop=867.65)

        plan = build_stop_ladder_plan(
            model_position,
            _indicators(price=941.39, stop=867.65, support=900.0),
            portfolio_value=160_193.03,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(len(plan.tranches), 2)
        self.assertEqual(plan.tranches[0].label, "Tactical warning stop")
        self.assertEqual(plan.tranches[0].shares, 1.0)
        self.assertAlmostEqual(plan.tranches[0].stop_price, 900.0)
        self.assertAlmostEqual(plan.tranches[0].max_loss, 41.39)
        self.assertEqual(plan.tranches[0].remaining_shares, 1.0)
        self.assertEqual(plan.tranches[1].label, "Thesis invalidation stop")
        self.assertAlmostEqual(plan.tranches[1].max_loss, 73.74)
        self.assertAlmostEqual(plan.single_stop_risk, 147.48)
        self.assertAlmostEqual(plan.laddered_risk, 115.13)
        self.assertAlmostEqual(plan.savings, 32.35)
        self.assertEqual(plan.savings_label, "Meaningful savings")

    def test_three_share_stop_ladder_adds_intermediate_tranche_math(self) -> None:
        model_position = _model_position(quantity=3, entry=100.0, stop=90.0)

        plan = build_stop_ladder_plan(
            model_position,
            _indicators(price=100.0, stop=90.0, support=97.0),
            portfolio_value=10_000.0,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual([tranche.shares for tranche in plan.tranches], [1.0, 1.0, 1.0])
        self.assertEqual([tranche.label for tranche in plan.tranches], ["Tactical warning stop", "Intermediate de-risk stop", "Thesis invalidation stop"])
        self.assertAlmostEqual(plan.tranches[0].stop_price, 97.0)
        self.assertAlmostEqual(plan.tranches[1].stop_price, 92.0)
        self.assertAlmostEqual(plan.tranches[2].stop_price, 90.0)
        self.assertAlmostEqual(plan.laddered_risk, 21.0)
        self.assertAlmostEqual(plan.single_stop_risk, 30.0)
        self.assertAlmostEqual(plan.savings, 9.0)

    def test_stop_ladder_labels_negligible_savings_plainly(self) -> None:
        model_position = _model_position(quantity=2, entry=100.0, stop=90.0)

        plan = build_stop_ladder_plan(
            model_position,
            _indicators(price=100.0, stop=90.0, support=90.50),
            portfolio_value=10_000.0,
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertAlmostEqual(plan.savings, 2.0)
        self.assertEqual(plan.savings_label, "Negligible savings")
        self.assertIn("negligible", plan.tradeoff.lower())


if __name__ == "__main__":
    unittest.main()
