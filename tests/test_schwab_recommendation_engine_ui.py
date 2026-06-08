from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.analytics.recommendation_engine import (
    DataConfidenceRead,
    DataSourceConfidence,
    EvidenceComponent,
    EvidenceVote,
    ExpectedRewardRiskSummary,
    RecommendationEngineRead,
)
from app.analytics.empirical_recommendation import (
    CatalystCollisionRead,
    ConfidenceShrinkageRead,
    EmpiricalRecommendationIntelligenceRead,
    SetupReplayRead,
    SupplyAbsorptionRead,
)
from app.ui.schwab_research_workspace_extension import (
    RECOMMENDATION_ENGINE_ADVICE_BOUNDARY,
    _recommendation_contradiction_lines,
    _recommendation_data_gap_lines,
    _recommendation_engine_cards,
    _recommendation_engine_detail_text,
    _recommendation_evidence_rows,
    _recommendation_reward_risk_lines,
    _state_aware_recommendation_lines,
    _recommendation_supporting_lines,
)


def _component(label: str, score: float, *, status: str | None = None, reason: str | None = None) -> EvidenceComponent:
    active_status = status or ("supportive" if score > 0 else "headwind" if score < 0 else "mixed")
    return EvidenceComponent(
        key=label.lower().replace(" ", "_"),
        label=label,
        vote=EvidenceVote(score=score, weight=1.0, confidence=0.8, reason=reason or f"{label} reason."),
        status=active_status,
        details=(),
        missing=(f"{label} missing.",) if active_status == "no_read" else (),
    )


def _data_confidence(*, grade: str = "High", score: float = 82.0, missing: tuple[str, ...] = (), stale: tuple[str, ...] = ()) -> DataConfidenceRead:
    return DataConfidenceRead(
        grade=grade,
        score=score,
        sources=(
            DataSourceConfidence("Price history", "loaded", 88.0, "Loaded.", weight=1.0),
            DataSourceConfidence("Macro backdrop", "stale", 42.0, "Cached.", age_hours=36.0, weight=1.0),
        ),
        missing=missing,
        stale=stale,
        reason=f"{grade} data confidence.",
    )


def _reward_risk(*, label: str = "Favorable planning EV", ev: float | None = 0.42, summary: str = "Scenario EV is positive.") -> ExpectedRewardRiskSummary:
    return ExpectedRewardRiskSummary(
        label=label,
        reward_risk_ratio=2.1,
        planning_probability=0.57,
        expected_value_units=ev,
        reward_line="Reward/risk: favorable at 2.10:1.",
        risk_line="Target reference: $112.00.",
        summary=summary,
    )


def _read(
    *,
    label: str = "Constructive / defined-risk only",
    confidence: str = "High",
    confidence_score: float = 84.0,
    evidence_score: float = 76.0,
    data_confidence: DataConfidenceRead | None = None,
    reward_risk: ExpectedRewardRiskSummary | None = None,
    components: tuple[EvidenceComponent, ...] | None = None,
    empirical_intelligence: EmpiricalRecommendationIntelligenceRead | None = None,
) -> RecommendationEngineRead:
    active_components = components or (
        _component("Chart setup", 48.0),
        _component("Capital structure / supply", -32.0),
        _component("Macro backdrop", 0.0),
        _component("Options", 0.0, status="no_read", reason="No option-chain rows are loaded."),
    )
    return RecommendationEngineRead(
        symbol="TEST",
        recommendation_label=label,
        confidence=confidence,
        confidence_score=confidence_score,
        evidence_score=evidence_score,
        evidence_vote=evidence_score - 50.0,
        components=active_components,
        data_confidence=data_confidence or _data_confidence(),
        expected_reward_risk=reward_risk or _reward_risk(),
        invalidation_lines=("Invalidation: losing $98.00 weakens the setup.",),
        confirmation_lines=("Confirmation: reclaiming $106.00 improves the setup.",),
        position_sizing_notes=("Position sizing note: this readout is planning context only and does not alter broker/order behavior.",),
        what_would_change=("Volume rejects the breakout.",),
        why=("Chart setup is supportive.",),
        warnings=("Data gap warning.",),
        confidence_adjusted_score=empirical_intelligence.confidence_adjusted_score if empirical_intelligence is not None else evidence_score,
        empirical_intelligence=empirical_intelligence,
    )


def _empirical_read() -> EmpiricalRecommendationIntelligenceRead:
    replay = SetupReplayRead(
        sample_count=14,
        lookback=20,
        horizon=5,
        median_forward_return=0.035,
        win_rate=0.64,
        average_similarity=0.78,
        raw_score=68.0,
        confidence=0.72,
        label="Replay supportive",
        summary="14 similar same-symbol windows; median forward return +3.5%.",
    )
    catalyst = CatalystCollisionRead(
        score=28.0,
        label="Moderate collision",
        events=("Upcoming earnings context is present.",),
        warnings=(),
        summary="Moderate collision: 28/100 from loaded catalyst inputs.",
    )
    supply = SupplyAbsorptionRead(
        read="absorption",
        label="Supply absorption",
        score=76.0,
        level=5.0,
        level_label="warrant strike",
        distance_percent=2.4,
        evidence_lines=("Nearest warrant strike: $5.00; latest close is above the level.",),
        confirmation_lines=("Confirmation: hold above $5.00 with normal volume.",),
        invalidation_lines=("Invalidation: lose $5.00 after testing it.",),
    )
    shrinkage = ConfidenceShrinkageRead(
        raw_evidence_score=76.0,
        confidence_adjusted_score=68.0,
        shrink_factor=0.69,
        factors=("data confidence 82/100 x0.82", "setup replay confidence x0.81"),
    )
    return EmpiricalRecommendationIntelligenceRead(
        symbol="TEST",
        raw_evidence_score=76.0,
        confidence_adjusted_score=68.0,
        setup_replay=replay,
        catalyst_collision=catalyst,
        option_required_move=None,
        supply_absorption=supply,
        shrinkage=shrinkage,
        regime_label="bullish / breakout / early",
        regime_warnings=(),
        recommendation_lines=("Raw evidence shrinks to adjusted evidence after empirical confidence controls.",),
        confirmation_lines=supply.confirmation_lines,
        invalidation_lines=supply.invalidation_lines,
    )


def _payload(*, price: float = 120.0) -> SimpleNamespace:
    classification = SimpleNamespace(
        setup="breakout",
        timing="early",
        action_quality="wait_for_trigger",
        confirmation_level=106.0,
        invalidation_level=98.0,
    )
    report = SimpleNamespace(
        overall_read="Bullish",
        overall_score=82.0,
        best_action="Defined-risk long only if trigger holds",
        setup_classification=classification,
    )
    return SimpleNamespace(
        context=SimpleNamespace(last_price=price),
        quote=None,
        command_center_report=report,
        statuses=(SimpleNamespace(source="Schwab quote", status="fresh", fetched_at="2026-06-08T20:15:00+00:00"),),
    )


class SchwabRecommendationEngineUiTests(unittest.TestCase):
    def test_missing_read_returns_safe_placeholder_cards_and_rows(self) -> None:
        cards = _recommendation_engine_cards(None)
        rows = _recommendation_evidence_rows(None)

        self.assertEqual(cards[0].title, "Recommendation")
        self.assertEqual(cards[0].label, "Unavailable")
        self.assertEqual(rows[0][0], "Recommendation Engine")
        self.assertIn(RECOMMENDATION_ENGINE_ADVICE_BOUNDARY, "\n".join(_recommendation_reward_risk_lines(None)))

    def test_constructive_read_maps_to_good_and_mixed_statuses(self) -> None:
        cards = _recommendation_engine_cards(_read())
        status_by_title = {card.title: card.status for card in cards}

        self.assertEqual(status_by_title["Recommendation"], "good")
        self.assertEqual(status_by_title["Confidence"], "good")
        self.assertEqual(status_by_title["Evidence Score"], "good")
        self.assertEqual(status_by_title["Data Confidence"], "good")
        self.assertEqual(status_by_title["Reward/Risk / EV"], "good")

    def test_avoid_and_no_read_map_to_bad_or_mixed_statuses(self) -> None:
        avoid_cards = _recommendation_engine_cards(
            _read(
                label="Avoid or reduce risk",
                confidence="Low",
                confidence_score=34.0,
                evidence_score=31.0,
                data_confidence=_data_confidence(grade="Very Low", score=32.0, missing=("Price history",)),
                reward_risk=_reward_risk(label="Unfavorable planning EV", ev=-0.28),
            )
        )
        no_read_cards = _recommendation_engine_cards(
            _read(label="No-read / gather data", confidence="Low", confidence_score=38.0, evidence_score=50.0)
        )

        avoid_status = {card.title: card.status for card in avoid_cards}
        no_read_status = {card.title: card.status for card in no_read_cards}
        self.assertEqual(avoid_status["Recommendation"], "bad")
        self.assertEqual(avoid_status["Confidence"], "bad")
        self.assertEqual(avoid_status["Reward/Risk / EV"], "bad")
        self.assertEqual(no_read_status["Recommendation"], "mixed")

    def test_evidence_rows_are_bounded_and_safe_with_missing_fields(self) -> None:
        read = SimpleNamespace(
            components=(
                SimpleNamespace(label=None, status=None, vote=SimpleNamespace(score=None, confidence=None, reason="x" * 500)),
            )
        )
        rows = _recommendation_evidence_rows(read)

        self.assertEqual(rows[0][0], "Unknown")
        self.assertEqual(rows[0][1], "--")
        self.assertEqual(rows[0][2], "--")
        self.assertLessEqual(len(rows[0][4]), 220)

    def test_supporting_and_contradiction_lists_split_positive_and_negative_votes(self) -> None:
        read = _read(
            components=(
                _component("Chart setup", 42.0, reason="Breakout confirmed."),
                _component("Capital supply", -38.0, reason="Parsed supply is nearby."),
                _component("Options", 0.0, status="no_read", reason="No option rows."),
            )
        )

        self.assertTrue(any("Chart setup" in line for line in _recommendation_supporting_lines(read)))
        self.assertTrue(any("Capital supply" in line for line in _recommendation_contradiction_lines(read)))
        self.assertFalse(any("Options" in line for line in _recommendation_supporting_lines(read)))

    def test_data_confidence_gaps_handle_missing_stale_and_empty_sources(self) -> None:
        read = _read(data_confidence=_data_confidence(grade="Low", score=48.0, missing=("Options",), stale=("Macro backdrop",)))
        empty = _read(
            data_confidence=DataConfidenceRead("High", 90.0, (), (), (), "No gaps."),
            components=(_component("Chart setup", 24.0),),
        )

        gap_lines = _recommendation_data_gap_lines(read)
        empty_lines = _recommendation_data_gap_lines(empty)

        self.assertTrue(any("Options" in line for line in gap_lines))
        self.assertTrue(any("Macro backdrop" in line for line in gap_lines))
        self.assertTrue(any("source check" in line for line in empty_lines))

    def test_data_confidence_gaps_include_source_remediation(self) -> None:
        read = _read(
            data_confidence=DataConfidenceRead(
                "Medium",
                62.0,
                (
                    DataSourceConfidence("Upcoming earnings calendar", "not-configured", 50.0, "Set ALPHA_VANTAGE_API_KEY to enable.", weight=0.4),
                    DataSourceConfidence("SEC filings/earnings", "error", 15.0, "SEC source failed.", weight=0.4),
                    DataSourceConfidence("SEC capital structure pressure", "informational", 62.0, "No parsed supply level.", weight=0.4),
                ),
                missing=("SEC filings/earnings",),
                stale=("Upcoming earnings calendar",),
                reason="Provider states are mixed.",
            )
        )

        lines = _recommendation_data_gap_lines(read, limit=5)

        self.assertTrue(any("Set ALPHA_VANTAGE_API_KEY" in line for line in lines))
        self.assertTrue(any("Run / refresh Earnings Radar" in line for line in lines))
        self.assertFalse(any(line.startswith("Missing/error: SEC capital structure pressure") for line in lines))

    def test_reward_risk_and_detail_text_include_advice_boundary(self) -> None:
        read = _read(reward_risk=_reward_risk(summary="Reward/risk is not defined yet.", ev=None))

        self.assertIn(RECOMMENDATION_ENGINE_ADVICE_BOUNDARY, "\n".join(_recommendation_reward_risk_lines(read)))
        self.assertIn(RECOMMENDATION_ENGINE_ADVICE_BOUNDARY, _recommendation_engine_detail_text(read, "TEST"))

    def test_empirical_intelligence_cards_and_detail_render(self) -> None:
        read = _read(empirical_intelligence=_empirical_read())

        titles = {card.title for card in _recommendation_engine_cards(read)}
        detail = _recommendation_engine_detail_text(read, "TEST")

        self.assertIn("Adjusted Score", titles)
        self.assertIn("Setup Replay", titles)
        self.assertIn("Catalyst Collision", titles)
        self.assertIn("Supply Absorption", titles)
        self.assertIn("Empirical Recommendation Intelligence", detail)
        self.assertIn("Confidence-adjusted score", detail)

    def test_confirmation_lines_are_state_aware_when_price_already_above_trigger(self) -> None:
        read = _read()
        payload = _payload(price=120.0)

        lines = _state_aware_recommendation_lines(
            payload,
            read,
            "confirmation_lines",
            kind="confirmation",
            fallback="Confirmation line unavailable.",
            limit=5,
        )
        detail = _recommendation_engine_detail_text(read, "TEST", payload=payload)

        self.assertTrue(any("Already above $106.00" in line for line in lines))
        self.assertTrue(any("not a pending reclaim" in line for line in lines))
        self.assertTrue(any("timing improves from early" in line for line in lines))
        self.assertFalse(any("Needs reclaim above $106.00" in line for line in lines))
        self.assertIn("Current quote: $120.00", detail)
        self.assertIn("Already above $106.00", detail)

        change_lines = _state_aware_recommendation_lines(
            payload,
            read,
            "what_would_change",
            kind="change",
            fallback="Fresh evidence or cleaner confirmation would change the view.",
            limit=5,
        )
        self.assertTrue(any("Because price is already above $106.00" in line for line in change_lines))

    def test_invalidation_lines_are_state_aware_when_price_is_below_risk_line(self) -> None:
        read = _read()
        payload = _payload(price=94.0)

        lines = _state_aware_recommendation_lines(
            payload,
            read,
            "invalidation_lines",
            kind="invalidation",
            fallback="Invalidation line unavailable.",
            limit=5,
        )

        self.assertTrue(any("Invalidated / avoid below $98.00" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
