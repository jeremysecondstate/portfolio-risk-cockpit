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
from app.ui.schwab_research_workspace_extension import (
    RECOMMENDATION_ENGINE_ADVICE_BOUNDARY,
    _recommendation_contradiction_lines,
    _recommendation_data_gap_lines,
    _recommendation_engine_cards,
    _recommendation_engine_detail_text,
    _recommendation_evidence_rows,
    _recommendation_reward_risk_lines,
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

    def test_reward_risk_and_detail_text_include_advice_boundary(self) -> None:
        read = _read(reward_risk=_reward_risk(summary="Reward/risk is not defined yet.", ev=None))

        self.assertIn(RECOMMENDATION_ENGINE_ADVICE_BOUNDARY, "\n".join(_recommendation_reward_risk_lines(read)))
        self.assertIn(RECOMMENDATION_ENGINE_ADVICE_BOUNDARY, _recommendation_engine_detail_text(read, "TEST"))


if __name__ == "__main__":
    unittest.main()
