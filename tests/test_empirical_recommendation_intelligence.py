from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from app.analytics.empirical_recommendation import (
    build_catalyst_collision_read,
    build_empirical_recommendation_intelligence,
    build_option_required_move_read,
    build_setup_replay_read,
    detect_supply_absorption,
    shrink_confidence_score,
)
from app.analytics.technical_analysis import Candle


def _trend_candles(count: int = 100, *, start: float = 50.0, step_pct: float = 0.003) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        open_price = price
        close = price * (1.0 + step_pct)
        rows.append(
            Candle(
                datetime_ms=index,
                open=open_price,
                high=max(open_price, close) * 1.004,
                low=min(open_price, close) * 0.996,
                close=close,
                volume=20_000 + (index % 10) * 250,
            )
        )
        price = close
    return rows


def _supply_candles(closes: list[float], *, volume_start: float = 10_000.0) -> list[Candle]:
    rows: list[Candle] = []
    for index, close in enumerate(closes):
        open_price = closes[index - 1] if index else close
        rows.append(
            Candle(
                datetime_ms=index,
                open=open_price,
                high=max(open_price, close, 10.05) + 0.10,
                low=min(open_price, close, 9.95) - 0.10,
                close=close,
                volume=volume_start + index * 500,
            )
        )
    return rows


class EmpiricalRecommendationIntelligenceTests(unittest.TestCase):
    def test_setup_replay_uses_local_same_symbol_history(self) -> None:
        read = build_setup_replay_read(_trend_candles())

        self.assertGreaterEqual(read.sample_count, 10)
        self.assertGreater(read.median_forward_return or 0.0, 0.0)
        self.assertGreater(read.raw_score, 50.0)
        self.assertIn("same-symbol", read.summary)

    def test_setup_replay_low_sample_warns(self) -> None:
        read = build_setup_replay_read(_trend_candles(10))

        self.assertEqual(read.sample_count, 0)
        self.assertTrue(any("Low sample" in warning for warning in read.warnings))

    def test_catalyst_collision_combines_event_inputs(self) -> None:
        as_of = datetime(2026, 6, 7, tzinfo=timezone.utc)
        macro_snapshot = SimpleNamespace(
            releases=[
                SimpleNamespace(metric="CPI", release_timestamp="2026-06-07T12:00:00+00:00", freshness_status="fresh"),
            ]
        )
        option_candidate = SimpleNamespace(dte=5)
        capital_indicator = SimpleNamespace(read="rally_fade_risk", chase_risk_score=82.0)

        read = build_catalyst_collision_read(
            earnings_text="Earnings event: today. Fresh earnings release found.",
            filings_lines=("8-K Item 2.02 Results of Operations and Financial Condition",),
            macro_snapshot=macro_snapshot,
            option_candidate=option_candidate,
            capital_structure_indicator=capital_indicator,
            as_of=as_of,
        )

        self.assertEqual(read.label, "High collision")
        self.assertGreaterEqual(read.score, 70.0)
        self.assertTrue(any("Option expiration" in event for event in read.events))

    def test_long_option_required_move_vs_implied_move(self) -> None:
        candidate = SimpleNamespace(
            strategy="Long call",
            group="Long Option",
            option_type="call",
            underlying_price=100.0,
            breakeven=112.0,
            strike=110.0,
            iv=0.20,
            dte=30,
        )

        read = build_option_required_move_read(candidate)

        self.assertIsNotNone(read)
        assert read is not None
        self.assertEqual(read.status, "bad")
        self.assertEqual(read.label, "Required move exceeds implied")
        self.assertGreater(read.required_move_pct or 0.0, read.implied_move_pct or 0.0)

    def test_covered_call_uses_safe_wording(self) -> None:
        candidate = SimpleNamespace(
            strategy="Income / covered-call candidate",
            group="Covered Call",
            option_type="call",
            underlying_price=100.0,
            breakeven=98.0,
            strike=110.0,
            iv=0.30,
            dte=30,
        )

        read = build_option_required_move_read(candidate)

        self.assertIsNotNone(read)
        assert read is not None
        text = " ".join(read.lines).lower()
        self.assertIn("not a long-debit required-move hurdle", text)
        self.assertNotIn("stock needs to rise", text)
        self.assertIsNone(read.required_move_pct)

    def test_supply_absorption_and_rejection_detection(self) -> None:
        absorption_closes = [9.40 + index * 0.02 for index in range(15)] + [9.80, 10.05, 10.18, 10.25, 10.32]
        rejection_closes = [9.70 + index * 0.01 for index in range(15)] + [10.05, 9.96, 9.90, 9.85, 9.80]

        absorbed = detect_supply_absorption(_supply_candles(absorption_closes), supply_level=10.0, level_label="warrant strike")
        rejected = detect_supply_absorption(_supply_candles(rejection_closes), supply_level=10.0, level_label="warrant strike")

        self.assertEqual(absorbed.read, "absorption")
        self.assertEqual(rejected.read, "rejection")
        self.assertLess(rejected.score, absorbed.score)

    def test_confidence_shrinkage_shows_raw_and_adjusted_scores(self) -> None:
        replay = build_setup_replay_read(_trend_candles(10))
        catalyst = build_catalyst_collision_read()
        supply = detect_supply_absorption(_trend_candles(20), supply_level=60.0)

        read = shrink_confidence_score(
            raw_evidence_score=80.0,
            data_confidence_score=42.0,
            replay=replay,
            catalyst=catalyst,
            option_move=None,
            supply=supply,
            regime_warnings=("Regime warning: choppy or unknown regime reduces confidence in replay analogs.",),
        )

        self.assertEqual(read.raw_evidence_score, 80.0)
        self.assertLess(read.confidence_adjusted_score, read.raw_evidence_score)
        self.assertTrue(any("data confidence" in factor for factor in read.factors))
        self.assertTrue(any("low-sample" in warning for warning in read.warnings))

    def test_empirical_readout_includes_confirmation_and_invalidation_lines(self) -> None:
        read = build_empirical_recommendation_intelligence(
            symbol="TST",
            historical_candles=_trend_candles(75),
            current_evidence_score=76.0,
            data_confidence_score=80.0,
            capital_structure_indicator=SimpleNamespace(nearest_supply_level=60.0, nearest_supply_level_label="warrant strike"),
        )

        self.assertGreater(len(read.confirmation_lines), 0)
        self.assertGreater(len(read.invalidation_lines), 0)
        self.assertLessEqual(read.confidence_adjusted_score, read.raw_evidence_score)


if __name__ == "__main__":
    unittest.main()
