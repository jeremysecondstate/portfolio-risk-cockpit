from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.analytics.empirical_recommendation import (
    CatalystCollisionRead,
    ConfidenceShrinkageRead,
    EmpiricalRecommendationIntelligenceRead,
    SetupReplayRead,
    SupplyAbsorptionRead,
)
from app.analytics.operator_verdict import build_operator_verdict
from app.analytics.recommendation_engine import (
    DataConfidenceRead,
    DataSourceConfidence,
    EvidenceComponent,
    EvidenceVote,
    ExpectedRewardRiskSummary,
    RecommendationEngineRead,
)
from app.ui.schwab_research_workspace_extension import (
    _operator_verdict_cards,
    _operator_verdict_detail_lines,
)


def _component(label: str, score: float) -> EvidenceComponent:
    status = "supportive" if score > 20 else "headwind" if score < -20 else "mixed"
    return EvidenceComponent(
        key=label.lower().replace(" ", "_"),
        label=label,
        vote=EvidenceVote(score=score, weight=1.0, confidence=0.80, reason=f"{label} reason."),
        status=status,
    )


def _data_confidence(score: float = 82.0, grade: str = "High") -> DataConfidenceRead:
    return DataConfidenceRead(
        grade=grade,
        score=score,
        sources=(DataSourceConfidence("Price history", "loaded", score, "Loaded.", weight=1.0),),
        missing=(),
        stale=(),
        reason=f"{grade} data confidence.",
    )


def _reward_risk() -> ExpectedRewardRiskSummary:
    return ExpectedRewardRiskSummary(
        label="Favorable planning EV",
        reward_risk_ratio=2.0,
        planning_probability=0.56,
        expected_value_units=0.30,
        reward_line="Reward/risk: favorable.",
        risk_line="Target reference: $112.00.",
        summary="Scenario EV is positive.",
    )


def _read(
    *,
    label: str = "Wait for confirmation",
    evidence_score: float = 54.0,
    adjusted_score: float | None = None,
    confidence: str = "Medium",
    confidence_score: float = 66.0,
    data_score: float = 82.0,
    data_grade: str = "High",
    warnings: tuple[str, ...] = (),
) -> RecommendationEngineRead:
    adjusted = evidence_score if adjusted_score is None else adjusted_score
    return RecommendationEngineRead(
        symbol="TEST",
        recommendation_label=label,
        confidence=confidence,
        confidence_score=confidence_score,
        evidence_score=evidence_score,
        evidence_vote=evidence_score - 50.0,
        components=(
            _component("Chart setup", evidence_score - 50.0),
            _component("Capital structure / supply", 0.0),
        ),
        data_confidence=_data_confidence(data_score, data_grade),
        expected_reward_risk=_reward_risk(),
        invalidation_lines=("Invalidation: losing $98.00 weakens the setup.",),
        confirmation_lines=("Confirmation: reclaiming $106.00 improves the setup.",),
        position_sizing_notes=("Position sizing note: planning context only; no broker/order behavior changes.",),
        what_would_change=("Volume confirmation fails or improves.",),
        why=("Chart setup is mixed.",),
        warnings=warnings,
        confidence_adjusted_score=adjusted,
    )


def _empirical(*, catalyst_score: float = 20.0, catalyst_label: str = "Low collision") -> EmpiricalRecommendationIntelligenceRead:
    replay = SetupReplayRead(
        sample_count=14,
        lookback=20,
        horizon=5,
        median_forward_return=0.02,
        win_rate=0.58,
        average_similarity=0.70,
        raw_score=62.0,
        confidence=0.70,
        label="Replay supportive",
        summary="14 similar same-symbol windows; median forward return +2.0%.",
    )
    catalyst = CatalystCollisionRead(
        score=catalyst_score,
        label=catalyst_label,
        events=("Catalyst context loaded.",),
        warnings=("Catalyst collision is elevated; avoid treating technical evidence as standalone.",) if catalyst_score >= 45 else (),
        summary=f"{catalyst_label}: {catalyst_score:.0f}/100 from loaded catalyst inputs.",
    )
    supply = SupplyAbsorptionRead(
        read="absorption",
        label="Supply absorption",
        score=74.0,
        level=5.0,
        level_label="warrant strike",
        distance_percent=2.0,
        evidence_lines=("Supply appears absorbed.",),
        confirmation_lines=("Confirmation: hold above $5.00 with normal volume.",),
        invalidation_lines=("Invalidation: lose $5.00 after testing it.",),
    )
    shrinkage = ConfidenceShrinkageRead(
        raw_evidence_score=70.0,
        confidence_adjusted_score=66.0,
        shrink_factor=0.70,
        factors=("data confidence 82/100 x0.82",),
    )
    return EmpiricalRecommendationIntelligenceRead(
        symbol="TEST",
        raw_evidence_score=70.0,
        confidence_adjusted_score=66.0,
        setup_replay=replay,
        catalyst_collision=catalyst,
        option_required_move=None,
        supply_absorption=supply,
        shrinkage=shrinkage,
        regime_label="bullish / breakout / confirmed",
        regime_warnings=(),
        recommendation_lines=("Raw evidence shrinks after empirical controls.",),
        confirmation_lines=supply.confirmation_lines,
        invalidation_lines=supply.invalidation_lines,
        warnings=catalyst.warnings,
    )


def _context(*, held: bool = True, quantity: float = 50.0) -> SimpleNamespace:
    return SimpleNamespace(
        symbol="TEST",
        is_held=held,
        quantity=quantity,
        market_value=quantity * 50.0,
        portfolio_value=100_000.0,
        portfolio_weight=(quantity * 50.0) / 100_000.0,
        last_price=50.0,
    )


def _option(**overrides: object) -> SimpleNamespace:
    values = {
        "strategy": "Starter Long Call",
        "group": "Starter Long Call",
        "option_type": "call",
        "score": 74.0,
        "controlled_shares": 100,
        "contract_count": 1,
        "expiration": "2026-07-17",
        "strike": 55.0,
        "why": "Liquid contract aligned with the setup.",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class OperatorVerdictTests(unittest.TestCase):
    def test_held_mixed_setup_holds_without_new_risk(self) -> None:
        verdict = build_operator_verdict(
            recommendation_read=_read(evidence_score=54.0),
            portfolio_context=_context(held=True, quantity=50),
        )

        self.assertEqual(verdict.right_now.action, "HOLD / NO NEW RISK")

    def test_not_held_negative_setup_avoids_trade(self) -> None:
        verdict = build_operator_verdict(
            recommendation_read=_read(label="Avoid or reduce risk", evidence_score=32.0, adjusted_score=34.0),
            portfolio_context=_context(held=False, quantity=0),
        )

        self.assertEqual(verdict.right_now.action, "AVOID / NO TRADE")

    def test_positive_confirmed_setup_allows_small_add(self) -> None:
        verdict = build_operator_verdict(
            recommendation_read=_read(
                label="Constructive / defined-risk only",
                evidence_score=78.0,
                adjusted_score=74.0,
                confidence="High",
                confidence_score=84.0,
            ),
            empirical_intelligence=_empirical(catalyst_score=18.0),
            portfolio_context=_context(held=True, quantity=150),
        )

        self.assertEqual(verdict.right_now.action, "ALLOW SMALL ADD")

    def test_invalidation_line_appears_when_breakdown_context_available(self) -> None:
        verdict = build_operator_verdict(recommendation_read=_read(), portfolio_context=_context())

        self.assertIn("$98.00", verdict.if_breaks_down.detail)
        self.assertIn("$98.00", verdict.invalidation.detail)

    def test_confirmation_line_appears_when_trigger_available(self) -> None:
        verdict = build_operator_verdict(recommendation_read=_read(), portfolio_context=_context())

        self.assertIn("$106.00", verdict.if_confirms.detail)
        self.assertIn("$106.00", verdict.confirmation.detail)

    def test_one_option_contract_vs_small_position_creates_worst_trade_warning(self) -> None:
        verdict = build_operator_verdict(
            recommendation_read=_read(evidence_score=70.0, adjusted_score=68.0),
            portfolio_context=_context(held=True, quantity=20),
            option_candidates=[_option()],
        )

        self.assertEqual(verdict.right_now.action, "HOLD / NO NEW RISK")
        self.assertIn("current position is only 20 shares", verdict.worst_tempting_trade.detail)
        self.assertTrue(any("current position is only 20 shares" in warning for warning in verdict.warnings))

    def test_missing_recommendation_read_produces_safe_fallback(self) -> None:
        verdict = build_operator_verdict(symbol="MISS", recommendation_read=None, portfolio_context=None)

        self.assertEqual(verdict.symbol, "MISS")
        self.assertEqual(verdict.right_now.action, "WAIT / NO TRADE")
        self.assertTrue(verdict.warnings)

    def test_catalyst_collision_pushes_new_risk_toward_avoid(self) -> None:
        verdict = build_operator_verdict(
            recommendation_read=_read(
                label="Constructive / defined-risk only",
                evidence_score=78.0,
                adjusted_score=74.0,
                confidence="High",
                confidence_score=84.0,
            ),
            empirical_intelligence=_empirical(catalyst_score=78.0, catalyst_label="High collision"),
            portfolio_context=_context(held=False, quantity=0),
        )

        self.assertEqual(verdict.right_now.action, "AVOID / NO TRADE")
        self.assertTrue(any("wait/avoid" in warning for warning in verdict.warnings))

    def test_low_confidence_adjusted_score_reduces_aggressiveness(self) -> None:
        verdict = build_operator_verdict(
            recommendation_read=_read(
                label="Constructive / defined-risk only",
                evidence_score=78.0,
                adjusted_score=55.0,
                confidence="High",
                confidence_score=84.0,
            ),
            portfolio_context=_context(held=False, quantity=0),
        )

        self.assertNotEqual(verdict.right_now.action, "ALLOW SMALL ADD")

    def test_ui_helpers_render_operator_cards_and_detail_lines(self) -> None:
        verdict = build_operator_verdict(
            recommendation_read=_read(
                label="Constructive / defined-risk only",
                evidence_score=78.0,
                adjusted_score=74.0,
                confidence="High",
                confidence_score=84.0,
            ),
            portfolio_context=_context(held=True, quantity=150),
        )

        cards = _operator_verdict_cards(verdict)
        titles = {card.title for card in cards}
        detail = "\n".join(_operator_verdict_detail_lines(verdict))

        self.assertEqual(len(cards), 8)
        self.assertIn("Right Now", titles)
        self.assertIn("Worst Tempting Trade", titles)
        self.assertIn("Primary action", detail)
        self.assertIn("Confirmation", detail)


if __name__ == "__main__":
    unittest.main()
