from __future__ import annotations

from datetime import datetime
import unittest

from app.analytics.hyperliquid_chain_health import (
    HyperliquidValidatorHealthSnapshot,
    assess_hyperliquid_chain_health,
    format_hyperliquid_chain_health_report,
)


def _snapshot(
    validators: list[dict],
    *,
    validator_stats=None,
    validator_l1_votes=None,
    all_mids_ok: bool | None = True,
    exchange_status=None,
    warnings: list[str] | None = None,
) -> HyperliquidValidatorHealthSnapshot:
    return HyperliquidValidatorHealthSnapshot(
        fetched_at=datetime(2026, 6, 1, 12, 0, 0),
        validator_summaries=validators,
        validator_stats=validator_stats,
        validator_l1_votes=validator_l1_votes,
        exchange_status=exchange_status,
        all_mids_ok=all_mids_ok,
        warnings=warnings or [],
    )


def _healthy_validators(count: int = 30) -> list[dict]:
    return [
        {
            "name": f"validator-{index:02d}",
            "validator": f"0x{index:040x}",
            "stake": str(1000 - index),
            "commission": "0.05",
            "active": True,
            "jailed": False,
        }
        for index in range(1, count + 1)
    ]


class HyperliquidChainHealthTests(unittest.TestCase):
    def test_healthy_validator_set_scores_green(self) -> None:
        validators = _healthy_validators()
        stats = [{"validator": row["validator"], "uptime": "99.8"} for row in validators]
        votes = [{"validator": row["validator"], "voted": True, "weight": row["stake"]} for row in validators]

        assessment = assess_hyperliquid_chain_health(
            _snapshot(validators, validator_stats=stats, validator_l1_votes=votes, exchange_status={"status": "normal"})
        )
        report = format_hyperliquid_chain_health_report(
            _snapshot(validators, validator_stats=stats, validator_l1_votes=votes, exchange_status={"status": "normal"}),
            assessment,
        )

        self.assertEqual(assessment.temperature, "GREEN")
        self.assertGreaterEqual(assessment.score or 0, 85)
        self.assertIn("Temperature: GREEN", report)
        self.assertIn("Top-24 active approximation: 24", report)

    def test_missing_validator_summaries_is_unknown(self) -> None:
        snapshot = _snapshot([], validator_stats=None, validator_l1_votes=None, all_mids_ok=False)

        assessment = assess_hyperliquid_chain_health(snapshot)
        report = format_hyperliquid_chain_health_report(snapshot, assessment)

        self.assertEqual(assessment.temperature, "UNKNOWN")
        self.assertIsNone(assessment.score)
        self.assertIn("validatorSummaries unavailable", "\n".join(assessment.warnings))
        self.assertIn("Operational read: unknown", report)

    def test_jailed_top_validator_is_orange_or_red(self) -> None:
        validators = _healthy_validators()
        validators[0]["jailed"] = True
        validators[0]["isJailed"] = True

        assessment = assess_hyperliquid_chain_health(
            _snapshot(validators, validator_stats=[], validator_l1_votes=[], exchange_status={"status": "normal"})
        )

        self.assertIn(assessment.temperature, {"ORANGE", "RED"})
        self.assertTrue(any("top-24" in line.lower() and "jailed" in line.lower() for line in assessment.criticals))

    def test_concentration_risk_adds_critical(self) -> None:
        validators = _healthy_validators(24)
        validators[0]["stake"] = "10000"
        for row in validators[1:]:
            row["stake"] = "200"

        assessment = assess_hyperliquid_chain_health(
            _snapshot(validators, validator_stats=[], validator_l1_votes=[], exchange_status={"status": "normal"})
        )

        self.assertGreater(assessment.key_metrics["top1_pct"], 25.0)
        self.assertTrue(any("Largest validator" in line for line in assessment.criticals))
        self.assertIn(assessment.temperature, {"ORANGE", "RED"})

    def test_unknown_schema_does_not_crash(self) -> None:
        snapshot = _snapshot(
            [{"weird": {"nested": "object"}}, {"name": "known-but-no-stake"}],
            validator_stats={"unexpected": "shape"},
            validator_l1_votes={"unexpected": "shape"},
            exchange_status={"status": "normal"},
        )

        assessment = assess_hyperliquid_chain_health(snapshot)
        report = format_hyperliquid_chain_health_report(snapshot, assessment)

        self.assertIn(assessment.temperature, {"ORANGE", "RED"})
        self.assertIn("Raw data notes:", report)
        self.assertIn("usable positive stake", "\n".join(assessment.criticals + assessment.raw_data_notes))


if __name__ == "__main__":
    unittest.main()
