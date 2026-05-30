from __future__ import annotations

import unittest

from app.analytics.research_workspace_insights import (
    build_earnings_workspace_summary,
    build_fundamental_verdict,
    build_macro_metric_cards,
    build_risk_plan,
    build_technical_narrative,
    combined_option_scenarios,
    confirmation_text,
    fibonacci_explanation,
    indicator_agreement_classification,
    inflation_read_from_metrics,
    macro_why_it_matters,
    option_expiration_payoff,
    option_midpoint,
    protective_put_benefit,
    suggest_option_candidates,
    ticket_fields_for_option_candidate,
)
from app.analytics.stock_research import AdvancedIndicatorSnapshot, PortfolioSymbolContext
from app.macro.models import MacroRelease, MacroSnapshot


def _release(category: str, metric: str, actual: float, prior: float) -> MacroRelease:
    return MacroRelease(
        category=category,
        metric=metric,
        source="BLS.gov",
        period="Apr 2026",
        release_timestamp="2026-05-15",
        actual=actual,
        prior=prior,
        revision=None,
        forecast=None,
        unit="%",
        raw_source="https://example.test",
        freshness_status="fresh",
        fetch_timestamp="2026-05-29T12:00:00+00:00",
    )


def _indicators(trend: str = "bullish", momentum: str = "improving") -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        symbol="GOOG",
        latest_close=100.0,
        sma_20=99.0,
        sma_50=97.0,
        sma_100=95.0,
        sma_200=90.0,
        ema_12=101.0,
        ema_26=98.0,
        macd=1.2,
        macd_signal=0.8,
        macd_histogram=0.4 if momentum == "improving" else -0.4,
        rsi_14=58.0 if momentum == "improving" else 38.0,
        bollinger_upper=110.0,
        bollinger_middle=100.0,
        bollinger_lower=90.0,
        atr_14=2.0,
        volume_average_20=1_000_000,
        week_52_high=130.0,
        week_52_low=70.0,
        swing_high=108.0,
        swing_low=92.0,
        fibonacci_levels={"50.0%": 100.0, "61.8%": 98.11},
        trend=trend,
        volatility="normal",
        momentum=momentum,
        support=96.0,
        resistance=104.0,
        notes=["sample"],
    )


def _context(held: bool = False) -> PortfolioSymbolContext:
    return PortfolioSymbolContext(
        symbol="GOOG",
        is_held=held,
        quantity=15.0 if held else 0.0,
        average_cost=90.0 if held else None,
        last_price=100.0,
        market_value=1500.0 if held else 0.0,
        portfolio_value=20_000.0,
        portfolio_weight=0.075 if held else 0.0,
        unrealized_pnl=150.0 if held else None,
        day_pnl=None,
    )


def _chain() -> list[dict]:
    return [
        {
            "underlying": "GOOG",
            "expiration_label": "Jun 19 2026 (21d)",
            "dte": 21,
            "strike": 100.0,
            "call": {"bid": 4.8, "ask": 5.2, "mark": 5.0, "symbol": "GOOG_CALL_100"},
            "put": {"bid": 4.1, "ask": 4.5, "mark": 4.3, "symbol": "GOOG_PUT_100"},
        },
        {
            "underlying": "GOOG",
            "expiration_label": "Jun 19 2026 (21d)",
            "dte": 21,
            "strike": 108.0,
            "call": {"bid": 1.8, "ask": 2.2, "mark": 2.0, "symbol": "GOOG_CALL_108"},
            "put": {"bid": 9.0, "ask": 9.6, "mark": 9.3, "symbol": "GOOG_PUT_108"},
        },
        {
            "underlying": "GOOG",
            "expiration_label": "Jun 19 2026 (21d)",
            "dte": 21,
            "strike": 95.0,
            "call": {"bid": 8.0, "ask": 8.8, "mark": 8.4, "symbol": "GOOG_CALL_95"},
            "put": {"bid": 1.9, "ask": 2.1, "mark": 2.0, "symbol": "GOOG_PUT_95"},
        },
    ]


class ResearchWorkspaceInsightTests(unittest.TestCase):
    def test_macro_metric_cards_and_inflation_mapping(self) -> None:
        snapshot = MacroSnapshot(
            fetched_at="2026-05-29T12:00:00+00:00",
            releases=[_release("inflation", "CPI", 3.4, 3.2), _release("inflation", "Core CPI", 3.7, 3.7), _release("inflation", "PPI", 2.5, 2.7)],
            source_statuses=[],
        )
        cards = build_macro_metric_cards(snapshot)

        self.assertGreaterEqual(len(cards), 6)
        self.assertEqual(inflation_read_from_metrics(cards), "Hot")
        self.assertIn("CPI moved from", cards[0].interpretation)

    def test_missing_macro_history_is_graceful(self) -> None:
        cards = build_macro_metric_cards(None)

        self.assertIn("Historical comparison unavailable", cards[0].interpretation)

    def test_macro_why_it_matters_by_symbol_sector(self) -> None:
        self.assertIn("growth/tech", macro_why_it_matters("GOOG", None, "Headwind").lower())
        self.assertIn("industrials/defense", macro_why_it_matters("NOC", None, "Mixed").lower())

    def test_technical_explanations_and_agreement(self) -> None:
        indicators = _indicators()

        self.assertIn("$104.00", confirmation_text(indicators))
        self.assertIn("Fibonacci retracement levels", fibonacci_explanation(indicators))
        self.assertEqual(indicator_agreement_classification(indicators, "Tailwind")[0], "Bullish")

    def test_held_and_non_held_position_explanations(self) -> None:
        held = build_technical_narrative(_indicators(), _context(held=True), "Mixed")
        watch = build_technical_narrative(_indicators(), _context(held=False), "Mixed")

        self.assertIn("-5% move", held.position_meaning)
        self.assertIn("possible new trade", watch.position_meaning)

    def test_option_candidate_selection_by_setup(self) -> None:
        bullish = suggest_option_candidates(_chain(), _indicators(), _context(), macro_label="Tailwind")
        bearish = suggest_option_candidates(_chain(), _indicators("bearish", "weakening"), _context(), macro_label="Headwind")
        mixed = suggest_option_candidates(_chain(), _indicators("sideways", "neutral"), _context(), macro_label="Headwind")

        self.assertTrue(any(candidate.option_type == "call" for candidate in bullish))
        self.assertTrue(any(candidate.option_type == "put" for candidate in bearish))
        self.assertEqual(mixed[0].strategy, "No-trade / wait")

    def test_held_stock_suggests_hedge_or_covered_call(self) -> None:
        candidates = suggest_option_candidates(_chain(), _indicators("sideways", "neutral"), _context(held=True), macro_label="Headwind")

        self.assertTrue(any("Protective put" in candidate.strategy or "covered-call" in candidate.strategy for candidate in candidates))

    def test_option_midpoint_breakeven_and_payoff_math(self) -> None:
        candidate = suggest_option_candidates(_chain(), _indicators(), _context(), macro_label="Tailwind")[0]

        self.assertEqual(option_midpoint(4.8, 5.2), 5.0)
        self.assertAlmostEqual(candidate.breakeven or 0.0, (candidate.strike or 0.0) + (candidate.midpoint or 0.0))
        self.assertAlmostEqual(option_expiration_payoff(candidate, (candidate.strike or 0.0) + 10.0), 500.0)

    def test_use_this_option_ticket_fields_are_fill_only_model(self) -> None:
        candidate = suggest_option_candidates(_chain(), _indicators(), _context(), macro_label="Tailwind")[0]
        fields = ticket_fields_for_option_candidate(candidate)

        self.assertEqual(fields["symbol"], "GOOG")
        self.assertEqual(fields["order_type"], "LIMIT")
        self.assertEqual(fields["action"], "Buy")
        self.assertNotIn("submit", fields)

    def test_put_payoff_and_combined_scenarios(self) -> None:
        candidate = next(item for item in suggest_option_candidates(_chain(), _indicators("bearish", "weakening"), _context(held=True), macro_label="Headwind") if item.option_type == "put")
        rows = combined_option_scenarios(candidate, _context(held=True), moves=(-0.10, 0.10))

        self.assertGreater(option_expiration_payoff(candidate, 80.0), 0)
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0].combined_pnl, rows[0].stock_pnl)

    def test_stock_plus_call_combined_scenario(self) -> None:
        candidate = next(item for item in suggest_option_candidates(_chain(), _indicators(), _context(held=True), macro_label="Tailwind") if item.option_type == "call")
        row = combined_option_scenarios(candidate, _context(held=True), moves=(0.10,))[0]

        self.assertGreater(row.option_value, 0)
        self.assertAlmostEqual(row.combined_pnl, row.stock_pnl + row.option_pnl)

    def test_protective_put_hedge_benefit_math(self) -> None:
        candidate = next(item for item in suggest_option_candidates(_chain(), _indicators("sideways", "neutral"), _context(held=True), macro_label="Headwind") if item.option_type == "put")

        self.assertIsNotNone(protective_put_benefit(candidate, _context(held=True), move=-0.10))

    def test_covered_call_basic_payoff_if_implemented(self) -> None:
        candidate = next(item for item in suggest_option_candidates(_chain(), _indicators("sideways", "neutral"), _context(held=True), macro_label="Headwind") if "covered-call" in item.strategy)

        self.assertGreater(option_expiration_payoff(candidate, 100.0), 0)
        self.assertLess(option_expiration_payoff(candidate, 125.0), 0)

    def test_option_candidate_scoring_and_wait_when_premium_not_attractive(self) -> None:
        candidates = suggest_option_candidates(_chain(), _indicators("sideways", "neutral"), _context(), macro_label="Headwind")

        self.assertEqual(candidates[0].strategy, "No-trade / wait")
        self.assertGreaterEqual(next(item for item in candidates if item.option_type == "call").score, 0)

    def test_fundamentals_verdict_and_investment_trade_read(self) -> None:
        strong = build_fundamental_verdict("Revenue growth is strong. Net income improved. Operating cash flow and companyfacts 10-Q data are positive.", _indicators(), "Tailwind")
        mixed = build_fundamental_verdict("Revenue mixed. Net income pressure.", _indicators("sideways", "neutral"), "Headwind")
        missing = build_fundamental_verdict("Fundamentals unavailable.", _indicators(), "Mixed")

        self.assertEqual(strong.verdict, "Strong")
        self.assertEqual(strong.action_bias, "Supports owning")
        self.assertIn("Investment read", strong.investment_read)
        self.assertEqual(mixed.verdict, "Mixed")
        self.assertEqual(missing.verdict, "Unknown")

    def test_risk_plan_recommended_move_and_concrete_levels(self) -> None:
        candidate = next(item for item in suggest_option_candidates(_chain(), _indicators(), _context(held=True), macro_label="Headwind") if item.option_type == "put")
        plan = build_risk_plan(_indicators(), _context(held=True), "Headwind", "Strong", candidate, 500.0)

        self.assertIn(plan.recommendation, {"Hedge with put", "Watch", "Add carefully", "Speculative call only"})
        self.assertIn("$104.00", plan.confirmation)
        self.assertIn("$96.00", plan.risk_line)

    def test_earnings_summary_source_links_and_fallback(self) -> None:
        summary = build_earnings_workspace_summary(
            "GOOG",
            "No recent 8-K earnings-release exhibit was found.",
            "Revenue increased. Net income growth improved. Source: SEC companyfacts XBRL JSON.",
            ["10-Q filed 2026-04-25 period 2026-03-31: https://sec.example/10q"],
        )

        self.assertIn("10-Q", summary.snapshot["Latest 10-Q / 10-K"])
        self.assertTrue(summary.source_links)
        self.assertIn("does not appear to include a fresh earnings release", " ".join(summary.interpretation))


if __name__ == "__main__":
    unittest.main()
