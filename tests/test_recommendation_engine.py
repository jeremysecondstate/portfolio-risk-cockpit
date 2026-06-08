from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from app.analytics.recommendation_engine import (
    COMPONENT_ORDER,
    build_data_confidence_read,
    build_recommendation_engine_read,
    score_evidence_components,
)


AS_OF = datetime(2026, 6, 6, 18, 0, tzinfo=timezone.utc)


def _score(score: float, reason: str) -> SimpleNamespace:
    return SimpleNamespace(score=score, reason=reason)


def _supportive_report() -> SimpleNamespace:
    volume_read = SimpleNamespace(
        relative_volume=1.8,
        up_down_volume_ratio=1.6,
        accumulation_read="accumulation",
        reason="Volume expanded on constructive closes.",
    )
    snapshot = SimpleNamespace(
        key="timing_5m",
        label="10d 5m",
        candle_count=90,
        volume_read=volume_read,
        scores={"Volume": _score(74, "Volume confirms the move.")},
    )
    prc = SimpleNamespace(
        read="Accumulation / constructive",
        index_distance_percent=0.35,
        index_slope=0.45,
        confidence="High",
        explanation_lines=["Pressure line is rising with price above VWAP."],
        warnings=[],
    )
    classification = SimpleNamespace(
        setup="breakout",
        timing="confirmed",
        action_quality="good_entry",
        confidence="High",
        invalidation_level=98.0,
        confirmation_level=106.0,
        main_reason="Breakout is confirmed and the ticket has a defined risk line.",
        lines=["Confirmed breakout above resistance."],
        warnings=[],
    )
    ticket_check = SimpleNamespace(
        risk_reward="Reward/risk: favorable at 2.10:1 using target $112.00.",
        risk_reward_ratio=2.1,
        risk_reward_read="good",
        target_price=112.0,
        risk_note="Modeled stop is below support.",
    )
    return SimpleNamespace(
        symbol="TEST",
        snapshots={"timing_5m": snapshot},
        prc_indexes={"timing_5m": prc},
        benchmark_reads=[SimpleNamespace(benchmark="SPY", verdict="outperforming", spread_20=4.2)],
        scores={"Relative Strength": _score(70, "TEST is outperforming loaded benchmarks.")},
        overall_score=78.0,
        overall_read="Bullish",
        confidence="High",
        best_action="Defined-risk long only if trigger holds",
        setup_classification=classification,
        ticket_check=ticket_check,
        key_triggers=[
            SimpleNamespace(label="Breakout trigger", price=106.0, reason="Move above resistance improves confirmation."),
            SimpleNamespace(label="Invalidation", price=98.0, reason="Break below support weakens the setup."),
        ],
        warnings=[],
        plain_english_plan=[
            "If bullish: prefer confirmation above $106.00.",
            "If bearish: losing $98.00 weakens the setup.",
            "What would change the view: volume rejects the breakout or the macro read turns into a headwind.",
        ],
    )


def _capital(**overrides: object) -> SimpleNamespace:
    values = {
        "read": "clean",
        "technical_score": 78.0,
        "supply_overhang_score": 12.0,
        "chase_risk_score": 18.0,
        "foreign_issuer_confidence_modifier": 0.0,
        "source_count": 3,
        "explanation_lines": ["Filing-derived supply context is clean."],
        "recommendation_lines": ["Capital-structure indicator preserves chart confidence."],
        "warnings": [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _context(**overrides: object) -> SimpleNamespace:
    values = {
        "symbol": "TEST",
        "is_held": True,
        "quantity": 50.0,
        "market_value": 2_500.0,
        "portfolio_value": 100_000.0,
        "portfolio_weight": 0.025,
        "cash_available": 40_000.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _option(**overrides: object) -> SimpleNamespace:
    values = {
        "strategy": "Long call",
        "option_type": "call",
        "score": 74.0,
        "score_reason": "Contract fit is liquid and aligned with the setup.",
        "spread_pct": 0.08,
        "volume": 320,
        "open_interest": 1_100,
        "dte": 35,
        "controlled_shares": 100,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _statuses() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(source="Schwab price history", status="fresh", fetched_at="2026-06-06T17:30:00+00:00", message="Loaded."),
        SimpleNamespace(source="SEC companyfacts", status="fresh/cache", fetched_at="2026-06-06T16:00:00+00:00", message="Loaded."),
    ]


class RecommendationEngineTests(unittest.TestCase):
    def test_supportive_layers_produce_explainable_constructive_read(self) -> None:
        read = build_recommendation_engine_read(
            command_center_report=_supportive_report(),
            capital_structure_indicator=_capital(),
            macro_read=SimpleNamespace(label="Tailwind", score=36.0, why="Rates and inflation language are supportive."),
            fundamental_read=SimpleNamespace(verdict="Strong", action_bias="Supports owning", confidence="High", what_changes=["Margins weaken."]),
            option_candidates=[_option()],
            portfolio_context=_context(),
            source_statuses=_statuses(),
            as_of=AS_OF,
        )

        self.assertEqual(read.symbol, "TEST")
        self.assertEqual([component.key for component in read.components], [key for key, _label in COMPONENT_ORDER])
        self.assertGreater(read.evidence_score, 70)
        self.assertIn(read.recommendation_label, {"Constructive / defined-risk only", "Constructive but wait for trigger"})
        self.assertIn("High", read.data_confidence.grade)
        self.assertGreater(read.expected_reward_risk.expected_value_units or 0, 0)
        self.assertTrue(any("Chart setup" in line for line in read.why))
        self.assertTrue(any("Invalidation" in line for line in read.invalidation_lines))
        self.assertTrue(any("Confirmation" in line or "Breakout trigger" in line for line in read.confirmation_lines))
        summary = read.expected_reward_risk.summary.lower()
        self.assertNotIn("guaranteed returns", summary)
        self.assertNotIn("price prediction", summary)

    def test_missing_layers_degrade_to_no_read_without_crashing(self) -> None:
        read = build_recommendation_engine_read(symbol="MISS", as_of=AS_OF)

        self.assertEqual(read.symbol, "MISS")
        self.assertEqual(read.recommendation_label, "No-read / gather data")
        self.assertLess(read.data_confidence.score, 40)
        self.assertEqual(read.data_confidence.grade, "Very Low")
        self.assertEqual(len(read.components), len(COMPONENT_ORDER))
        self.assertTrue(any(component.status == "no_read" for component in read.components))
        self.assertTrue(read.invalidation_lines)
        self.assertTrue(read.position_sizing_notes)

    def test_supply_and_concentration_blockers_override_bullish_chart(self) -> None:
        read = build_recommendation_engine_read(
            command_center_report=_supportive_report(),
            capital_structure_indicator=_capital(
                read="rally_fade_risk",
                technical_score=35.0,
                chase_risk_score=84.0,
                recommendation_lines=["Avoid chase near parsed supply without volume/VWAP confirmation."],
                warnings=["Rally-fade risk is elevated."],
            ),
            macro_read=SimpleNamespace(label="Headwind", score=-42.0, why="Macro is a headwind."),
            option_candidates=[_option(strategy="No-trade / wait", option_type="--", score=66.0)],
            portfolio_context=_context(quantity=350.0, market_value=17_500.0, portfolio_weight=0.175, cash_available=3_000.0),
            source_statuses=_statuses(),
            as_of=AS_OF,
        )

        self.assertEqual(read.recommendation_label, "Avoid chase / wait for confirmation")
        self.assertLess(read.evidence_score, 60)
        self.assertTrue(any(component.key == "capital_structure_supply" and component.vote.score < -40 for component in read.components))
        self.assertTrue(any("Concentration is elevated" in note for note in read.position_sizing_notes))
        self.assertTrue(any("Rally-fade" in warning or "rally-fade" in warning for warning in read.warnings))

    def test_data_confidence_tracks_status_freshness_and_errors(self) -> None:
        confidence = build_data_confidence_read(
            command_center_report=_supportive_report(),
            capital_structure_indicator=_capital(),
            macro_text="Official Macro Snapshot\nNeutral/mixed.",
            fundamentals_text="Latest reported fundamentals: revenue growth and cash flow.",
            option_candidates=[_option()],
            portfolio_context=_context(),
            source_statuses=[
                SimpleNamespace(source="Fresh feed", status="fresh", fetched_at="2026-06-06T17:45:00+00:00", message="Loaded."),
                SimpleNamespace(source="Old feed", status="fresh", fetched_at="2026-06-01T17:45:00+00:00", message="Old."),
                SimpleNamespace(source="Broken feed", status="error", fetched_at="2026-06-06T17:45:00+00:00", message="Failed."),
            ],
            as_of=AS_OF,
        )

        by_source = {source.source: source for source in confidence.sources}
        self.assertEqual(by_source["Old feed"].status, "stale")
        self.assertEqual(by_source["Broken feed"].status, "error")
        self.assertIn("Old feed", confidence.stale)
        self.assertIn("Broken feed", confidence.missing)
        self.assertLess(confidence.score, 80)

    def test_data_confidence_distinguishes_provider_source_states(self) -> None:
        confidence = build_data_confidence_read(
            command_center_report=_supportive_report(),
            capital_structure_indicator=_capital(),
            macro_text="Official Macro Snapshot\nNeutral/mixed.",
            fundamentals_text="Latest reported fundamentals: revenue growth and cash flow.",
            option_candidates=[_option()],
            portfolio_context=_context(),
            source_statuses=[
                SimpleNamespace(source="Upcoming earnings calendar", status="not configured", fetched_at="2026-06-06T17:45:00+00:00", message="Set ALPHA_VANTAGE_API_KEY to enable."),
                SimpleNamespace(source="Earnings calendar", status="no event found", fetched_at="2026-06-06T17:45:00+00:00", message="No same-day event."),
                SimpleNamespace(source="SEC capital structure pressure", status="no parsed supply level", fetched_at="2026-06-06T17:45:00+00:00", message="Scan loaded but no level parsed."),
                SimpleNamespace(source="SEC filings/earnings", status="error", fetched_at="2026-06-06T17:45:00+00:00", message="Failed."),
            ],
            as_of=AS_OF,
        )

        by_source = {source.source: source for source in confidence.sources}
        self.assertEqual(by_source["Upcoming earnings calendar"].status, "not-configured")
        self.assertEqual(by_source["Earnings calendar"].status, "informational")
        self.assertEqual(by_source["SEC capital structure pressure"].status, "informational")
        self.assertEqual(by_source["SEC filings/earnings"].status, "error")
        self.assertIn("Upcoming earnings calendar", confidence.stale)
        self.assertNotIn("Earnings calendar", confidence.missing)
        self.assertNotIn("SEC capital structure pressure", confidence.missing)
        self.assertIn("SEC filings/earnings", confidence.missing)

    def test_capital_pressure_fallback_prevents_false_missing_capital_read(self) -> None:
        empty_terms = SimpleNamespace(
            common_share_classes=[],
            preferred_series=[],
            warrants=[],
            convertibles=[],
            offering_programs=[],
            ads_adr_structures=[],
        )
        pressure = SimpleNamespace(
            read="Low",
            filings_analyzed=3,
            supply_overhang_score=0.0,
            possible_supply_levels=[],
            parsed_terms=empty_terms,
            signals=[],
            warnings=[],
            explanation_lines=["Capital-structure scan loaded with no parsed supply level."],
            what_would_change=[],
        )

        read = build_recommendation_engine_read(
            command_center_report=_supportive_report(),
            capital_structure_pressure=pressure,
            macro_text="Official Macro Snapshot\nNeutral/mixed.",
            fundamentals_text="Latest reported fundamentals: revenue growth and cash flow.",
            option_candidates=[_option()],
            portfolio_context=_context(),
            as_of=AS_OF,
        )

        by_key = {component.key: component for component in read.components}
        by_source = {source.source: source for source in read.data_confidence.sources}
        self.assertNotEqual(by_key["capital_structure_supply"].status, "no_read")
        self.assertEqual(by_source["Capital structure"].status, "informational")
        self.assertNotIn("Capital structure", read.data_confidence.missing)

    def test_evidence_scoring_is_bounded(self) -> None:
        read = build_recommendation_engine_read(
            command_center_report=SimpleNamespace(
                symbol="WILD",
                snapshots={},
                prc_indexes={},
                benchmark_reads=[],
                scores={},
                overall_score=500.0,
                overall_read="Bullish",
                confidence="High",
                best_action="Defined-risk long only if trigger holds",
                setup_classification=SimpleNamespace(
                    setup="breakout",
                    timing="confirmed",
                    action_quality="good_entry",
                    invalidation_level=None,
                    confirmation_level=None,
                    main_reason="Extreme synthetic score.",
                    lines=[],
                    warnings=[],
                ),
                ticket_check=SimpleNamespace(risk_reward_ratio=None, target_price=None, risk_reward_read="unknown"),
                key_triggers=[],
                warnings=[],
                plain_english_plan=[],
            ),
            capital_structure_indicator=_capital(read="clean", technical_score=500.0),
            option_candidates=[_option(score=500.0)],
            portfolio_context=_context(portfolio_weight=-1.0),
            as_of=AS_OF,
        )
        evidence_score, evidence_vote = score_evidence_components(read.components)

        self.assertGreaterEqual(evidence_score, 0)
        self.assertLessEqual(evidence_score, 100)
        self.assertGreaterEqual(evidence_vote, -100)
        self.assertLessEqual(evidence_vote, 100)
        self.assertTrue(all(-100 <= component.vote.score <= 100 for component in read.components))


if __name__ == "__main__":
    unittest.main()
