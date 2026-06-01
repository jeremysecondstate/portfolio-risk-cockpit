from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.analytics.options_greeks import (
    SOURCE_CALCULATED,
    SOURCE_SCHWAB,
    SOURCE_UNAVAILABLE,
    build_greek_summary,
    plain_english_greek_readout,
)


def _chain_with_greeks() -> list[dict]:
    return [
        {
            "underlying": "LHX",
            "expiration_label": "Jun 05 2026 (5d)",
            "dte": 5,
            "strike": 305.0,
            "call": {
                "symbol": "LHX_CALL_305",
                "bid": 9.9,
                "ask": 12.4,
                "mark": 11.15,
                "impliedVolatility": 31.0,
                "delta": 0.64,
                "gamma": 0.025,
                "theta": -0.18,
                "vega": 0.11,
                "rho": 0.04,
            },
            "put": {
                "symbol": "LHX_PUT_305",
                "bid": 0.35,
                "ask": 2.25,
                "mark": 1.3,
                "impliedVolatility": 0.31,
            },
        }
    ]


class OptionsGreeksTests(unittest.TestCase):
    def test_schwab_provided_greek_fields_are_preserved(self) -> None:
        summary = build_greek_summary(_chain_with_greeks(), 314.78, selected_contract_symbol="LHX_CALL_305")
        selected = summary.selected

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.delta.source, SOURCE_SCHWAB)
        self.assertEqual(selected.gamma.source, SOURCE_SCHWAB)
        self.assertAlmostEqual(selected.delta.value or 0.0, 0.64)
        self.assertAlmostEqual(selected.implied_volatility.value or 0.0, 0.31)

    def test_missing_greeks_fall_back_to_black_scholes(self) -> None:
        summary = build_greek_summary(_chain_with_greeks(), 314.78, selected_contract_symbol="LHX_PUT_305")
        selected = summary.selected

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.delta.source, SOURCE_CALCULATED)
        self.assertLess(selected.delta.value or 0.0, 0.0)
        self.assertEqual(selected.vega.source, SOURCE_CALCULATED)
        self.assertGreater(selected.vega.value or 0.0, 0.0)

    def test_missing_chain_data_stays_explicitly_unavailable(self) -> None:
        chain = [
            {
                "underlying": "LHX",
                "expiration_label": "Jun 05 2026 (5d)",
                "dte": 5,
                "strike": 305.0,
                "call": {"symbol": "LHX_CALL_305", "bid": 0, "ask": 0},
            }
        ]
        summary = build_greek_summary(chain, None, selected_contract_symbol="LHX_CALL_305")
        selected = summary.selected

        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected.delta.source, SOURCE_UNAVAILABLE)
        self.assertIn("Underlying price is unavailable", " ".join(summary.warnings))

    def test_plain_english_readout_explains_delta_theta_and_sources(self) -> None:
        summary = build_greek_summary(_chain_with_greeks(), 314.78, selected_contract_symbol="LHX_CALL_305")
        lines = plain_english_greek_readout(summary.selected, summary.underlying_price, summary.warnings)
        text = " ".join(lines)

        self.assertIn("$1 move", text)
        self.assertIn("time decay", text)
        self.assertIn("Source mix", text)

    def test_selected_candidate_matches_contract_for_highlight(self) -> None:
        candidate = SimpleNamespace(contract_symbol="", option_type="call", strike=305.0, expiration="Jun 05 2026 (5d)")
        summary = build_greek_summary(_chain_with_greeks(), 314.78, selected_candidate=candidate)

        self.assertIsNotNone(summary.selected)
        assert summary.selected is not None
        self.assertTrue(summary.selected.selected)
        self.assertEqual(summary.selected.contract_symbol, "LHX_CALL_305")


if __name__ == "__main__":
    unittest.main()
