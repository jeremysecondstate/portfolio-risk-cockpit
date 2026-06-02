from __future__ import annotations

from datetime import datetime
import unittest

from app.analytics.hyperliquid_chain_health import (
    HyperliquidValidatorHealthSnapshot,
    assess_hyperliquid_chain_health,
    build_hyperliquid_market_impact_read,
    format_hyperliquid_chain_health_report,
    format_hyperliquid_chain_health_human_report,
)
from app.ui.hyperliquid_chain_health_extension import _open_hyperliquid_chain_health_popup


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


def _concentrated_validators() -> list[dict]:
    stakes = [
        7_500,
        7_000,
        6_000,
        5_200,
        4_800,
        3_500,
        3_000,
        2_600,
        2_200,
        1_900,
        1_500,
        1_300,
        1_100,
        950,
        850,
        750,
        650,
        550,
        450,
        350,
        300,
        250,
        200,
        150,
        120,
        110,
        100,
        90,
        80,
        70,
        60,
    ]
    validators = []
    for index, stake in enumerate(stakes, start=1):
        validators.append(
            {
                "name": f"validator-{index:02d}",
                "validator": f"0x{index:040x}",
                "stake": str(stake),
                "commission": "0.05",
                "active": True,
                "jailed": False,
            }
        )
    return validators


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

    def test_absurd_raw_stake_is_not_displayed_as_quadrillion_hype(self) -> None:
        validators = _healthy_validators(24)
        for row in validators:
            row["stake"] = str(int(row["stake"]) * 100_000_000)

        snapshot = _snapshot(validators, validator_stats=[], validator_l1_votes=[], exchange_status={"status": "normal"})
        assessment = assess_hyperliquid_chain_health(snapshot)
        report = format_hyperliquid_chain_health_human_report(snapshot, assessment)

        self.assertEqual(assessment.key_metrics["stake_unit_label"], "HYPE")
        self.assertLess(assessment.key_metrics["active_stake_display"], 1_000_000_000_000)
        self.assertIn("normalized", "\n".join(assessment.raw_data_notes).lower())
        self.assertNotIn("2,370,000,000,000,000.00 HYPE", report)

    def test_jailed_outside_top24_is_caution_not_panic(self) -> None:
        validators = _concentrated_validators()
        for row in validators[24:28]:
            row["jailed"] = True

        snapshot = _snapshot(validators, validator_stats=None, validator_l1_votes=[], exchange_status={"status": "normal"})
        assessment = assess_hyperliquid_chain_health(snapshot)
        report = format_hyperliquid_chain_health_human_report(snapshot, assessment)

        self.assertEqual(assessment.key_metrics["jailed_top24"], 0)
        self.assertIn("main active validator set appears intact", report)
        self.assertIn("0 in the top 24", report)
        self.assertIn("not a standalone bearish HYPE signal", report)
        self.assertIn("does not mean panic", report.lower())
        self.assertNotIn("dangerous", report.lower())

    def test_top24_intact_is_explained_as_main_squad_intact(self) -> None:
        snapshot = _snapshot(_healthy_validators(31), validator_stats=None, validator_l1_votes=[], exchange_status={"status": "normal"})
        assessment = assess_hyperliquid_chain_health(snapshot)
        report = format_hyperliquid_chain_health_human_report(snapshot, assessment)

        self.assertEqual(assessment.key_metrics["jailed_top24"], 0)
        self.assertIn("main squad", report)
        self.assertIn("active set appears intact", report.lower())

    def test_concentration_warning_drives_cautious_posture(self) -> None:
        snapshot = _snapshot(_concentrated_validators(), validator_stats=None, validator_l1_votes=[], exchange_status={"status": "normal"})
        assessment = assess_hyperliquid_chain_health(snapshot)
        market = build_hyperliquid_market_impact_read(snapshot, assessment)
        report = format_hyperliquid_chain_health_human_report(snapshot, assessment)

        self.assertGreater(assessment.key_metrics["top3_pct"], 33.0)
        self.assertGreater(assessment.key_metrics["top5_pct"], 50.0)
        self.assertIn("Stake concentration: Warning", report)
        self.assertEqual(market.trading_posture, "CAUTIOUS")
        self.assertEqual(market.hype_price_pressure, "LOW")

    def test_missing_validator_stats_lowers_confidence_not_chain_operating_health(self) -> None:
        snapshot = _snapshot(_healthy_validators(31), validator_stats=None, validator_l1_votes=[], exchange_status={"status": "normal"})
        assessment = assess_hyperliquid_chain_health(snapshot)
        report = format_hyperliquid_chain_health_human_report(snapshot, assessment)

        self.assertLess(assessment.key_metrics["data_confidence_score"], 100)
        self.assertGreaterEqual(assessment.key_metrics["chain_operating_health_score"], 90)
        self.assertIn("detailed fitness tracker", report)
        self.assertNotIn("chain is broken", report.lower())

    def test_popup_builder_instantiates_without_wallet_address(self) -> None:
        import tkinter as tk

        snapshot = _snapshot(_healthy_validators(31), validator_stats=None, validator_l1_votes=[], exchange_status={"status": "normal"})
        assessment = assess_hyperliquid_chain_health(snapshot)
        report = format_hyperliquid_chain_health_human_report(snapshot, assessment)
        root = tk.Tk()
        root.withdraw()
        try:
            popup = _open_hyperliquid_chain_health_popup(root, snapshot, assessment, report)
            self.assertTrue(popup.winfo_exists())
            popup.destroy()
        finally:
            root.destroy()

    def test_human_report_contains_plain_english_translations_and_trading_impact(self) -> None:
        snapshot = _snapshot(_concentrated_validators(), validator_stats=None, validator_l1_votes=[], exchange_status={"status": "normal"})
        assessment = assess_hyperliquid_chain_health(snapshot)
        report = format_hyperliquid_chain_health_human_report(snapshot, assessment)

        self.assertIn("A validator is a computer/operator helping run the chain.", report)
        self.assertIn("A jailed validator is benched", report)
        self.assertIn("Stake concentration means a few operators have a lot of the control.", report)
        self.assertIn("Trading impact:", report)
        self.assertIn("Historical proof: not available yet", report)


if __name__ == "__main__":
    unittest.main()
