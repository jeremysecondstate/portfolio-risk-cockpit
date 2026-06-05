from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.analytics.research_scoring import BadgeReadout
from app.analytics.research_workspace_insights import (
    build_cross_read_conflict_badge,
    build_earnings_workspace_summary,
    build_fundamental_metric_cards,
    build_fundamental_verdict,
    build_technical_at_glance_read,
)
from app.analytics.stock_research import AdvancedIndicatorSnapshot


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


if __name__ == "__main__":
    unittest.main()
