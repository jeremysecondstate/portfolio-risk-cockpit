from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.analytics.research_scoring import BadgeReadout, build_decision_readout
from app.analytics.research_workspace_insights import (
    build_cross_read_conflict_badge,
    build_earnings_workspace_summary,
    build_fundamental_metric_cards,
    build_fundamental_verdict,
    build_macro_metric_cards,
    build_technical_at_glance_read,
)
from app.analytics.stock_research import AdvancedIndicatorSnapshot, PortfolioSymbolContext
from app.macro.models import MacroRelease, MacroSnapshot


def _indicators(*, trend: str = "bullish", momentum: str = "improving") -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        symbol="TEST",
        latest_close=50.0,
        sma_20=49.0,
        sma_50=48.0,
        sma_100=47.0,
        sma_200=46.0,
        ema_12=50.0,
        ema_26=49.0,
        macd=0.3,
        macd_signal=0.1,
        macd_histogram=0.2,
        rsi_14=55.0,
        bollinger_upper=55.0,
        bollinger_middle=50.0,
        bollinger_lower=45.0,
        atr_14=1.2,
        volume_average_20=100_000,
        week_52_high=70.0,
        week_52_low=30.0,
        swing_high=54.0,
        swing_low=45.0,
        fibonacci_levels={},
        trend=trend,
        volatility="normal",
        momentum=momentum,
        support=45.0,
        resistance=54.0,
        notes=[],
    )


def _context(*, quantity: float = 0.0, price: float = 50.0) -> PortfolioSymbolContext:
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
        cash_available=50_000.0,
    )


def _pullback_indicators() -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        symbol="TEST",
        latest_close=100.0,
        sma_20=104.0,
        sma_50=102.0,
        sma_100=94.0,
        sma_200=86.0,
        ema_12=101.0,
        ema_26=102.0,
        macd=-0.4,
        macd_signal=-0.2,
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


def _broken_indicators() -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        symbol="TEST",
        latest_close=80.0,
        sma_20=88.0,
        sma_50=94.0,
        sma_100=98.0,
        sma_200=104.0,
        ema_12=83.0,
        ema_26=90.0,
        macd=-1.4,
        macd_signal=-0.6,
        macd_histogram=-0.8,
        rsi_14=32.0,
        bollinger_upper=100.0,
        bollinger_middle=90.0,
        bollinger_lower=80.0,
        atr_14=3.0,
        volume_average_20=100_000,
        week_52_high=130.0,
        week_52_low=70.0,
        swing_high=112.0,
        swing_low=90.0,
        fibonacci_levels={},
        trend="bearish",
        volatility="elevated",
        momentum="weakening",
        support=90.0,
        resistance=88.0,
        notes=[],
    )


def _structured_fundamentals(*, with_pressure: bool = False) -> str:
    net_income_change = "-6.0%" if with_pressure else "+22.0%"
    operating_change = "-8.0%" if with_pressure else "+18.0%"
    pressure_lines = [
        "- Net income weakened versus the comparable period (-6.0% YoY).",
        "- Operating income compressed versus the comparable period (-8.0% YoY).",
        "- Margin pressure appears in management commentary.",
    ] if with_pressure else [
        "- Net income improved versus the comparable period (+22.0% YoY).",
        "- Operating income expanded versus the comparable period (+18.0% YoY).",
    ]
    return "\n".join(
        [
            "FUNDAMENTAL ANALYSIS - TEST",
            "",
            "Quarterly trend table:",
            "",
            "Revenue:",
            "- FY2026 Q1: $100.00 (form 10-Q, filed 2026-05-01)",
            "  Latest sequential change: +4.0%",
            "  Latest comparable-period change: +30.0%",
            "",
            "Net income:",
            "- FY2026 Q1: $20.00 (form 10-Q, filed 2026-05-01)",
            f"  Latest comparable-period change: {net_income_change}",
            "",
            "Operating income:",
            "- FY2026 Q1: $25.00 (form 10-Q, filed 2026-05-01)",
            f"  Latest comparable-period change: {operating_change}",
            "",
            "Diluted EPS:",
            "- FY2026 Q1: $1.20 (form 10-Q, filed 2026-05-01)",
            "  Latest comparable-period change: +12.0%",
            "",
            "Annual context:",
            "",
            "Operating cash flow:",
            "- FY2025 FY: $90.00 (form 10-K, filed 2026-02-01)",
            "  Latest comparable-period change: +16.0%",
            "",
            "Cockpit interpretation:",
            "- Revenue growth is strong on the latest comparable period (+30.0% YoY).",
            *pressure_lines,
            "- Cash equals roughly 32.0% of reported liabilities in the latest snapshot.",
            "- Liabilities are roughly 42.0% of reported assets.",
            "",
            "Source: SEC companyfacts XBRL JSON.",
        ]
    )


def _macro_release(
    metric: str,
    *,
    actual: float | None,
    prior: float | None,
    category: str = "inflation",
    unit: str = "%",
) -> MacroRelease:
    return MacroRelease(
        category=category,
        metric=metric,
        source="Official test source",
        period="2026-05",
        release_timestamp="2026-06-17T12:00:00+00:00",
        actual=actual,
        prior=prior,
        revision=None,
        forecast=None,
        unit=unit,
        raw_source="https://example.test/macro",
        freshness_status="fresh",
        fetch_timestamp="2026-06-17T12:01:00+00:00",
    )


class ResearchWorkspaceInsightTests(unittest.TestCase):
    def test_earnings_source_links_label_search_helpers_separately(self) -> None:
        earnings_text = "\n".join(
            [
                "Earnings Release Explanation - TEST",
                "",
                "Freshness Check",
                "- Earnings event: today",
                "- Latest loaded source date: --",
                "- Latest SEC filing date: 2026-06-04",
                "- Latest company IR release date: --",
                "- Freshness verdict: Earnings expected today, but no fresh company IR or SEC earnings release was found yet.",
                "",
                "Source Details",
                "- Nasdaq earnings calendar (2026-06-05): https://www.nasdaq.com/market-activity/earnings",
                "- Official IR earnings source search (--): https://www.google.com/search?q=TEST+investor+relations+earnings",
                "- SEC 8-K earnings exhibit (2026-06-04): https://www.sec.gov/Archives/test.htm",
            ]
        )

        summary = build_earnings_workspace_summary(
            "TEST",
            earnings_text,
            "Source: SEC companyfacts XBRL JSON.",
            ["10-Q filed 2026-05-01 period FY2026 Q1: https://www.sec.gov/Archives/test-10q.htm"],
        )

        labels = [label for label, _date, _url in summary.source_links]
        self.assertTrue(any(label.startswith("Search helper:") for label in labels))
        self.assertTrue(any(label.startswith("Confirmed source:") for label in labels))
        search_rows = [row for row in summary.source_links if "google.com/search" in row[2]]
        self.assertTrue(all(row[0].startswith("Search helper:") for row in search_rows))

    def test_earnings_summary_labels_sec_10q_fallback_source(self) -> None:
        earnings_text = "\n".join(
            [
                "Earnings Release Explanation - NVDA",
                "",
                "Freshness Check",
                "- Earnings event: unknown",
                "- Loaded source: SEC 10-Q fallback",
                "- Latest loaded source date: 2026-05-28",
                "- Latest SEC filing date: 2026-05-28",
                "- Latest company IR release date: --",
                "- Freshness verdict: No 8-K earnings-release exhibit found; using recent SEC 10-Q financial statements and MD&A as earnings context (quarterly report filed 2026-05-28).",
                "",
                "Latest Quarter Snapshot",
                "- Revenue: Revenue was $44.1 billion, up 69% from a year ago.",
                "- EPS: Diluted earnings per share was $0.76.",
                "- Gross margin / operating margin: Gross margin was 60.5%, and operating margin was 49.5%.",
                "- Segment / platform revenue: Data Center platform revenue was $39.1 billion.",
                "",
                "Source Details",
                "- SEC 10-Q fallback (2026-05-28): https://www.sec.gov/Archives/nvda-10q.htm",
            ]
        )

        summary = build_earnings_workspace_summary(
            "NVDA",
            earnings_text,
            "Source: SEC companyfacts XBRL JSON.",
            ["10-Q filed 2026-05-28 period 2026-04-27: https://www.sec.gov/Archives/nvda-10q.htm"],
        )

        self.assertEqual(summary.earnings_card_label, "SEC 10-Q analyzed")
        self.assertEqual(summary.freshness_label, "SEC 10-Q / recent quarterly report")
        self.assertEqual(summary.snapshot["Latest earnings release"], "No 8-K; SEC 10-Q fallback")
        self.assertTrue(any("SEC 10-Q fallback" in row[0] for row in summary.source_links))
        self.assertTrue(any("recent SEC 10-Q/10-K filing is being used" in line for line in summary.interpretation))

    def test_fundamental_verdict_uses_structured_metrics_and_caps_pressure(self) -> None:
        verdict = build_fundamental_verdict(_structured_fundamentals(with_pressure=True), _indicators(), "Tailwind")

        self.assertNotEqual(verdict.verdict, "Strong")
        self.assertIn(verdict.verdict, {"Mixed", "Weak", "Avoid"})
        self.assertIn("pressure", verdict.investment_read.lower())

    def test_fundamental_metric_cards_use_companyfacts_changes(self) -> None:
        cards = build_fundamental_metric_cards(_structured_fundamentals())
        by_title = {card.title: card for card in cards}

        self.assertEqual(by_title["Revenue Trend"].label, "+30.0%")
        self.assertEqual(by_title["Revenue Trend"].status, "good")
        self.assertEqual(by_title["Operating Profit"].label, "+18.0%")
        self.assertTrue(by_title["Balance Sheet"].label.startswith("Cash 32.0%"))

    def test_macro_unavailable_rows_do_not_score_as_mixed(self) -> None:
        snapshot = MacroSnapshot(
            fetched_at="2026-06-17T12:00:00+00:00",
            releases=[_macro_release("CPI", actual=None, prior=3.1)],
            source_statuses=[],
        )

        cards = build_macro_metric_cards(snapshot)
        by_group = {card.group: card for card in cards}

        self.assertEqual(by_group["Inflation"].simple_read, "Unavailable")
        self.assertEqual(by_group["Growth / Consumer"].simple_read, "Unavailable")
        self.assertEqual(by_group["Overall Macro Backdrop"].simple_read, "Unavailable")
        self.assertIn("no composite macro read was scored", by_group["Overall Macro Backdrop"].interpretation)

    def test_macro_composite_excludes_unavailable_rows_and_labels_provider_proxies(self) -> None:
        snapshot = MacroSnapshot(
            fetched_at="2026-06-17T12:00:00+00:00",
            releases=[
                _macro_release("CPI", actual=3.4, prior=3.1, category="inflation"),
                _macro_release("Payroll", actual=None, prior=175_000, category="labor", unit=""),
            ],
            source_statuses=[],
        )
        provider_context = SimpleNamespace(
            databento_futures_context={
                "CL.FUT": {
                    "price": 74.5,
                    "previous_close": 72.0,
                    "timestamp": "2026-06-17T15:00:00+00:00",
                    "unit": "USD",
                }
            },
            fmp_macro_context={
                "retail_sales": {
                    "category": "consumer",
                    "metric": "Retail sales provider context",
                    "value": 101.2,
                    "prior": 100.0,
                    "date": "2026-05",
                    "unit": "index",
                }
            },
        )

        cards = build_macro_metric_cards(snapshot, provider_context=provider_context)
        by_group = {card.group: card for card in cards}
        proxy_rows = [card for card in cards if card.simple_read == "Proxy Context"]

        self.assertEqual(by_group["Overall Macro Backdrop"].simple_read, "Headwind")
        self.assertTrue(all(row.source in {"Databento market proxy", "FMP provider fallback"} for row in proxy_rows))
        self.assertTrue(any(row.group == "Energy" and row.source == "Databento market proxy" for row in proxy_rows))
        self.assertTrue(any(row.group == "Growth / Consumer" and row.source == "FMP provider fallback" for row in proxy_rows))
        self.assertIn("provider proxy context shown separately", by_group["Overall Macro Backdrop"].interpretation)

    def test_fundamental_combined_read_calls_out_macro_trade_conflict(self) -> None:
        verdict = build_fundamental_verdict(_structured_fundamentals(), _indicators(), "Headwind")

        self.assertEqual(verdict.verdict, "Strong")
        self.assertIn("Conflict:", verdict.combined_read)

    def test_technical_at_glance_prefers_command_center_score(self) -> None:
        decision = SimpleNamespace(technical_score=-70.0)
        command = SimpleNamespace(overall_score=82.0, overall_read="Bullish", confidence="High", best_action="Defined-risk long only if trigger holds")

        read = build_technical_at_glance_read(decision, command)

        self.assertEqual(read.label, "Bullish")
        self.assertAlmostEqual(read.score, 64.0)
        self.assertIn("Command Center", read.why)
        self.assertIn("Conflict:", read.why)

    def test_cross_read_conflict_badge_is_explicit(self) -> None:
        technical = BadgeReadout("Technical Read", "Bullish", "good", 65.0, "Command Center bullish.")

        badge = build_cross_read_conflict_badge("Strong", "Headwind", technical)

        self.assertEqual(badge.label, "Explicit Conflict")
        self.assertIn("Conflict:", badge.why)

    def test_thesis_read_separates_constructive_pullback_from_technical_weakness(self) -> None:
        decision = build_decision_readout(
            indicators=_pullback_indicators(),
            context=_context(quantity=4, price=100.0),
            scenario_rows=[],
            earnings_text="Next earnings not soon.",
            fundamentals_text=_structured_fundamentals(),
            macro_text="Official Macro Snapshot\nNeutral/mixed.",
            statuses=[],
        )

        self.assertEqual(decision.thesis.setup_type, "pullback")
        self.assertIn(decision.thesis.recommendation, {"Accumulate Pullback", "Hold"})
        self.assertNotEqual(decision.action_bias.label, "Avoid")
        self.assertIn("Bearish tape, but constructive pullback candidate", decision.thesis.trade_judgment)
        self.assertEqual(len(decision.thesis.forecast), 4)

    def test_thesis_read_rejects_broken_support_and_weak_fundamentals(self) -> None:
        decision = build_decision_readout(
            indicators=_broken_indicators(),
            context=_context(quantity=0, price=80.0),
            scenario_rows=[],
            earnings_text="Next earnings timing unknown.",
            fundamentals_text=_structured_fundamentals(with_pressure=True),
            macro_text="Official Macro Snapshot\nMacro headwind; higher yield pressure.",
            statuses=[],
        )

        self.assertEqual(decision.thesis.setup_type, "breakdown")
        self.assertEqual(decision.thesis.recommendation, "Avoid")
        self.assertEqual(decision.action_bias.label, "Avoid")
        self.assertTrue(any("support" in warning.lower() for warning in decision.thesis.warnings))

    def test_bullish_trend_and_improving_momentum_adds_carefully(self) -> None:
        decision = build_decision_readout(
            indicators=_indicators(trend="bullish", momentum="improving"),
            context=_context(quantity=0, price=50.0),
            scenario_rows=[],
            earnings_text="Next earnings not soon.",
            fundamentals_text=_structured_fundamentals(),
            macro_text="Official Macro Snapshot\nMacro tailwind; cooler rates.",
            statuses=[],
        )

        self.assertEqual(decision.thesis.recommendation, "Add Carefully")
        self.assertIn(decision.thesis.preferred_vehicle, {"Starter Shares", "Shares"})


if __name__ == "__main__":
    unittest.main()
