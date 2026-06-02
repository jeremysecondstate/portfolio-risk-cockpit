from __future__ import annotations

from datetime import date
import unittest

from app.analytics.option_contract_inspector import (
    build_option_contract_inspector_model,
    format_option_contract_plain_english,
    is_schwab_option_holding,
    parse_occ_option_symbol,
)


AS_OF = date(2026, 6, 2)


class OptionContractInspectorTests(unittest.TestCase):
    def test_occ_parser_parses_crsp_call(self) -> None:
        parsed = parse_occ_option_symbol("CRSP 260605C00055000", as_of=AS_OF)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.underlying, "CRSP")
        self.assertEqual(parsed.expiration, date(2026, 6, 5))
        self.assertEqual(parsed.option_type, "call")
        self.assertEqual(parsed.strike, 55.0)
        self.assertEqual(parsed.dte, 3)

    def test_occ_parser_parses_tsla_call(self) -> None:
        parsed = parse_occ_option_symbol("TSLA 260603C00425000", as_of=AS_OF)

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.underlying, "TSLA")
        self.assertEqual(parsed.expiration, date(2026, 6, 3))
        self.assertEqual(parsed.option_type, "call")
        self.assertEqual(parsed.strike, 425.0)
        self.assertEqual(parsed.dte, 1)

    def test_option_detection_accepts_option_vanilla_type(self) -> None:
        self.assertTrue(is_schwab_option_holding({"symbol": "NOT_PARSEABLE", "type": "OPTION VANILLA"}))

    def test_invalid_option_symbol_does_not_crash(self) -> None:
        self.assertIsNone(parse_occ_option_symbol("BAD SYMBOL"))

        model = build_option_contract_inspector_model(
            {"symbol": "BAD SYMBOL", "type": "OPTION VANILLA", "qty": "1", "value": "$0.00"},
            as_of=AS_OF,
        )

        self.assertIsNone(model.parsed)
        self.assertEqual(model.posture, "NO-READ")
        self.assertIn("Could not fully parse", model.parse_warning)

    def test_long_call_plain_english_explains_direction_and_time_decay(self) -> None:
        model = build_option_contract_inspector_model(
            {"symbol": "CRSP 260605C00055000", "type": "OPTION VANILLA", "qty": "1", "pnl": "$-108.66"},
            underlying_last=50.0,
            as_of=AS_OF,
        )

        text = format_option_contract_plain_english(model)

        self.assertIn("CRSP $55 call", text)
        self.assertIn("benefits if the stock rises", text)
        self.assertIn("time decay matters", text)
        self.assertIn("negative", text)

    def test_wide_spread_produces_liquidity_warning(self) -> None:
        model = build_option_contract_inspector_model(
            {"symbol": "CRSP 260605C00055000", "type": "OPTION VANILLA", "qty": "1"},
            chain_rows=[
                {
                    "underlying": "CRSP",
                    "expiration_date": "2026-06-05",
                    "expiration_label": "Jun 05 2026 (3d)",
                    "dte": 3,
                    "strike": 55.0,
                    "call": {"symbol": "CRSP 260605C00055000", "bid": 1.0, "ask": 1.6, "mark": 1.3},
                }
            ],
            as_of=AS_OF,
        )

        self.assertIn(model.liquidity_grade, {"BAD", "THIN"})
        self.assertIsNotNone(model.spread_percent)
        self.assertIn("wide", model.liquidity_warning.lower())

    def test_short_dte_produces_time_risk_warning(self) -> None:
        model = build_option_contract_inspector_model(
            {"symbol": "TSLA 260603C00425000", "type": "OPTION VANILLA", "qty": "1"},
            as_of=AS_OF,
        )

        self.assertEqual(model.dte, 1)
        self.assertEqual(model.time_bucket, "weekly")
        self.assertIn("close to expiration", model.time_warning)
        self.assertIn(model.posture, {"CAUTIOUS", "DEFENSIVE"})


if __name__ == "__main__":
    unittest.main()
