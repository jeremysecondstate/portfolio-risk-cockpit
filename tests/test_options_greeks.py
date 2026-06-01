from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.analytics.options_greeks import (
    SOURCE_CALCULATED,
    SOURCE_SCHWAB,
    SOURCE_UNAVAILABLE,
    build_greek_summary,
    build_greek_decision_section,
    greek_approximation_rows,
    greek_dollar_meanings,
    plain_english_greek_readout,
    rank_greek_contracts,
    theta_offset_moves,
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


def _decision_chain() -> list[dict]:
    return [
        {
            "underlying": "LHX",
            "expiration_label": "Jun 05 2026 (4d)",
            "dte": 4,
            "strike": 310.0,
            "call": {"symbol": "LHX_CALL_310", "bid": 8.1, "ask": 8.7, "mark": 8.4, "impliedVolatility": 0.31, "delta": 0.666, "gamma": 0.034, "theta": -0.249, "vega": 0.156, "rho": 0.035},
            "put": {"symbol": "LHX_PUT_310", "bid": 2.2, "ask": 2.8, "mark": 2.5, "impliedVolatility": 0.31, "delta": -0.332, "gamma": 0.031, "theta": -0.224, "vega": 0.145, "rho": -0.021},
        },
        {
            "underlying": "LHX",
            "expiration_label": "Jun 05 2026 (4d)",
            "dte": 4,
            "strike": 312.5,
            "call": {"symbol": "LHX_CALL_312_5", "bid": 5.8, "ask": 6.4, "mark": 6.1, "impliedVolatility": 0.31, "delta": 0.581, "gamma": 0.035, "theta": -0.269, "vega": 0.166, "rho": 0.031},
            "put": {"symbol": "LHX_PUT_312_5", "bid": 3.0, "ask": 3.6, "mark": 3.3, "impliedVolatility": 0.31, "delta": -0.419, "gamma": 0.034, "theta": -0.252, "vega": 0.162, "rho": -0.026},
        },
        {
            "underlying": "LHX",
            "expiration_label": "Jun 05 2026 (4d)",
            "dte": 4,
            "strike": 315.0,
            "call": {"symbol": "LHX_CALL_315", "bid": 3.9, "ask": 4.5, "mark": 4.2, "impliedVolatility": 0.31, "delta": 0.491, "gamma": 0.034, "theta": -0.310, "vega": 0.172, "rho": 0.026},
            "put": {"symbol": "LHX_PUT_315", "bid": 4.1, "ask": 4.7, "mark": 4.4, "impliedVolatility": 0.31, "delta": -0.509, "gamma": 0.034, "theta": -0.297, "vega": 0.171, "rho": -0.031},
        },
        {
            "underlying": "LHX",
            "expiration_label": "Jun 05 2026 (4d)",
            "dte": 4,
            "strike": 320.0,
            "call": {"symbol": "LHX_CALL_320", "bid": 1.0, "ask": 1.4, "mark": 1.2, "impliedVolatility": 0.31, "delta": 0.220, "gamma": 0.025, "theta": -0.180, "vega": 0.118, "rho": 0.014},
            "put": {"symbol": "LHX_PUT_320", "bid": 7.2, "ask": 8.0, "mark": 7.6, "impliedVolatility": 0.31, "delta": -0.780, "gamma": 0.026, "theta": -0.201, "vega": 0.120, "rho": -0.045},
        },
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

    def test_greek_dollar_meanings_format_contract_sensitivities(self) -> None:
        summary = build_greek_summary(_decision_chain(), 315.18, selected_contract_symbol="LHX_CALL_315")
        assert summary.selected is not None

        text = "\n".join(f"{greek}: {meaning}" for greek, meaning in greek_dollar_meanings(summary.selected))

        self.assertIn("Delta +0.491: +$49 per +$1 stock move", text)
        self.assertIn("Theta -0.310: -$31 per day", text)
        self.assertIn("Vega +0.172: +$17 per +1 vol point", text)
        self.assertIn("Rho +0.026: basically irrelevant here", text)

    def test_greek_approximation_rows_use_second_order_math(self) -> None:
        summary = build_greek_summary(_decision_chain(), 315.18, selected_contract_symbol="LHX_CALL_315")
        assert summary.selected is not None

        rows = greek_approximation_rows(summary.selected)
        plus_250 = next(row for row in rows if row.move == 2.5)
        flat = next(row for row in rows if row.move == 0.0)

        self.assertAlmostEqual(plus_250.one_day_pnl, 102.375, places=3)
        self.assertAlmostEqual(plus_250.full_window_pnl, 9.375, places=3)
        self.assertAlmostEqual(flat.one_day_pnl, -31.0, places=3)
        self.assertAlmostEqual(flat.full_window_pnl, -124.0, places=3)

    def test_theta_offset_moves_use_one_day_and_dte_windows(self) -> None:
        summary = build_greek_summary(_decision_chain(), 315.18, selected_contract_symbol="LHX_CALL_315")
        assert summary.selected is not None

        offset = theta_offset_moves(summary.selected)

        self.assertEqual(offset.full_window_days, 4)
        self.assertAlmostEqual(offset.one_day_move or 0.0, 0.631, places=3)
        self.assertAlmostEqual(offset.full_window_move or 0.0, 2.525, places=3)

    def test_rank_nearby_contracts_by_greek_efficiency(self) -> None:
        summary = build_greek_summary(_decision_chain(), 315.18, selected_contract_symbol="LHX_CALL_315")

        ranks = rank_greek_contracts(summary, "call")

        self.assertGreaterEqual(len(ranks), 3)
        self.assertEqual(ranks[0].label, "310 call")
        self.assertIn("theta/delta", ranks[0].reason)
        self.assertIn("315 call", [rank.label for rank in ranks])

    def test_greek_decision_section_requires_premium_for_final_call(self) -> None:
        chain = [
            {
                "underlying": "LHX",
                "expiration_label": "Jun 05 2026 (4d)",
                "dte": 4,
                "strike": 315.0,
                "call": {"symbol": "LHX_CALL_315", "impliedVolatility": 0.31, "delta": 0.491, "gamma": 0.034, "theta": -0.310, "vega": 0.172, "rho": 0.026},
            }
        ]
        summary = build_greek_summary(chain, 315.18, selected_contract_symbol="LHX_CALL_315")

        section = build_greek_decision_section(summary)

        self.assertIn("Decision from the Greeks", section)
        self.assertIn("No premium = no final buy/no-buy call.", section)

    def test_greek_decision_section_handles_missing_greeks(self) -> None:
        chain = [
            {
                "underlying": "LHX",
                "expiration_label": "Jun 05 2026 (4d)",
                "dte": 4,
                "strike": 315.0,
                "call": {"symbol": "LHX_CALL_315", "bid": 0, "ask": 0},
            }
        ]
        summary = build_greek_summary(chain, None, selected_contract_symbol="LHX_CALL_315")

        section = build_greek_decision_section(summary)

        self.assertIn("Greeks decision unavailable for the active contract", section)

    def test_sentinel_greek_values_are_unavailable_without_estimates(self) -> None:
        chain = [
            {
                "underlying": "LHX",
                "expiration_label": "Jun 05 2026 (4d)",
                "dte": 4,
                "strike": 315.0,
                "call": {
                    "symbol": "LHX_CALL_315",
                    "delta": "-999.000",
                    "gamma": -999.0,
                    "theta": -999,
                    "vega": -999.00,
                    "rho": -999.000,
                },
            }
        ]
        summary = build_greek_summary(chain, None, selected_contract_symbol="LHX_CALL_315")
        assert summary.selected is not None

        self.assertIsNone(summary.selected.delta.value)
        self.assertEqual(summary.selected.delta.source, SOURCE_UNAVAILABLE)
        self.assertEqual(summary.selected.gamma.source, SOURCE_UNAVAILABLE)
        self.assertNotEqual(summary.selected.source_summary, SOURCE_SCHWAB)
        self.assertEqual(greek_approximation_rows(summary.selected), [])
        self.assertEqual(rank_greek_contracts(summary, "call"), [])

    def test_sentinel_greek_values_fall_back_to_calculated_estimates(self) -> None:
        chain = [
            {
                "underlying": "LHX",
                "expiration_label": "Jun 05 2026 (4d)",
                "dte": 4,
                "strike": 315.0,
                "call": {
                    "symbol": "LHX_CALL_315",
                    "mark": 4.55,
                    "impliedVolatility": 0.332,
                    "delta": -999,
                    "gamma": -999,
                    "theta": -999,
                    "vega": -999,
                    "rho": -999,
                },
            }
        ]
        summary = build_greek_summary(chain, 315.18, selected_contract_symbol="LHX_CALL_315")
        assert summary.selected is not None

        self.assertEqual(summary.selected.delta.source, SOURCE_CALCULATED)
        self.assertEqual(summary.selected.theta.source, SOURCE_CALCULATED)
        self.assertNotEqual(summary.selected.delta.value, -999)
        self.assertNotEqual(summary.selected.source_summary, SOURCE_SCHWAB)
        self.assertTrue(greek_approximation_rows(summary.selected))

    def test_valid_negative_put_delta_and_theta_remain_valid(self) -> None:
        chain = [
            {
                "underlying": "LHX",
                "expiration_label": "Jun 05 2026 (4d)",
                "dte": 4,
                "strike": 315.0,
                "put": {
                    "symbol": "LHX_PUT_315",
                    "impliedVolatility": 0.39,
                    "delta": -0.511,
                    "gamma": 0.034,
                    "theta": -0.310,
                    "vega": 0.171,
                    "rho": -0.031,
                },
            }
        ]
        summary = build_greek_summary(chain, 315.18, selected_contract_symbol="LHX_PUT_315")
        assert summary.selected is not None

        self.assertEqual(summary.selected.delta.source, SOURCE_SCHWAB)
        self.assertEqual(summary.selected.theta.source, SOURCE_SCHWAB)
        self.assertAlmostEqual(summary.selected.delta.value or 0.0, -0.511)
        self.assertAlmostEqual(summary.selected.theta.value or 0.0, -0.310)


if __name__ == "__main__":
    unittest.main()
