from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.analytics.technical_analysis import (
    CapitalStructureIndicatorRead,
    Candle,
    QuoteSnapshot,
    TechnicalTicket,
    build_technical_command_center_report,
)
from app.ui.schwab_research_workspace_extension import (
    PRC_PRESSURE_LINE_NOTICE,
    CAPITAL_STRUCTURE_LEVEL_DISCLAIMER,
    _technical_capital_structure_cards,
    _technical_capital_structure_note_rows,
    _technical_capital_structure_supply_rows,
    _technical_prc_rows,
    _technical_score_breakdown_rows,
    _technical_setup_cards,
    _technical_ticket_check_rows,
    _technical_timeframe_stack_rows,
    _technical_warning_rows,
)


def _candles(count: int, *, start: float = 100.0, step: float = 0.20) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        open_price = price
        close = price + step
        rows.append(
            Candle(
                datetime_ms=index,
                open=open_price,
                high=max(open_price, close) + 0.35,
                low=min(open_price, close) - 0.35,
                close=close,
                volume=10_000 + index * 25,
            )
        )
        price = close
    return rows


def _capital_indicator(**overrides: object) -> CapitalStructureIndicatorRead:
    values = {
        "technical_score": 82.0,
        "read": "clean",
        "supply_overhang_score": 12.0,
        "dilution_pressure_score": 8.0,
        "warrant_conversion_proximity_score": 18.0,
        "offering_activity_score": 0.0,
        "float_quality_score": 92.0,
        "foreign_issuer_confidence_modifier": 0.0,
        "option_exposure_mismatch_score": 0.0,
        "chase_risk_score": 16.0,
        "nearest_supply_level": 5.0,
        "nearest_supply_level_label": "warrant strike",
        "nearest_supply_level_distance_percent": 3.25,
        "source_count": 2,
        "explanation_lines": ["Supply overhang risk is low.", "Float quality is clean."],
        "warnings": [],
        "recommendation_lines": ["Capital-structure indicator is clean enough to preserve chart confidence."],
    }
    values.update(overrides)
    return CapitalStructureIndicatorRead(**values)  # type: ignore[arg-type]


class SchwabTechnicalCommandCenterUiTests(unittest.TestCase):
    def test_command_center_rows_tolerate_missing_report(self) -> None:
        setup_cards = _technical_setup_cards(None)

        self.assertEqual(setup_cards[0].label, "Unavailable")
        self.assertEqual(_technical_capital_structure_cards(None)[0].label, "Unavailable")
        self.assertIn("not built", _technical_capital_structure_supply_rows(None)[0][-1])
        self.assertIn(CAPITAL_STRUCTURE_LEVEL_DISCLAIMER, _technical_capital_structure_note_rows(None)[-1][-1])
        self.assertIn("not built", _technical_timeframe_stack_rows(None)[0][-1])
        self.assertIn("not built", _technical_prc_rows(None)[0][5])
        self.assertIn("not built", _technical_score_breakdown_rows(None)[0][2])
        self.assertIn("not built", _technical_ticket_check_rows(None)[0][2])
        self.assertEqual(_technical_warning_rows(None), [])

    def test_capital_structure_rows_tolerate_missing_indicator(self) -> None:
        report = SimpleNamespace(capital_structure_indicator=None)

        self.assertEqual(_technical_capital_structure_cards(report)[0].label, "No Parsed Supply")
        self.assertIn("No capital-structure", _technical_capital_structure_supply_rows(report)[0][-1])
        self.assertEqual(_technical_capital_structure_note_rows(report)[0][0], "Status")

    def test_capital_structure_cards_and_rows_surface_clean_indicator(self) -> None:
        report = SimpleNamespace(capital_structure_indicator=_capital_indicator())

        cards = _technical_capital_structure_cards(report)
        status_by_title = {card.title: card.status for card in cards}
        self.assertEqual(status_by_title["Capital Read"], "good")
        self.assertEqual(status_by_title["Technical Score"], "good")
        self.assertEqual(status_by_title["Supply Overhang"], "good")
        self.assertNotIn("Option Mismatch", status_by_title)

        supply_rows = _technical_capital_structure_supply_rows(report)
        self.assertEqual(supply_rows[0][0], "warrant strike")
        self.assertEqual(supply_rows[0][1], "$5.00")
        self.assertEqual(supply_rows[0][2], "+3.25%")
        self.assertIn("clean enough", supply_rows[0][3])

        note_rows = _technical_capital_structure_note_rows(report)
        self.assertIn(("Explanation", "Supply overhang risk is low."), note_rows)
        self.assertIn(("Disclaimer", CAPITAL_STRUCTURE_LEVEL_DISCLAIMER), note_rows)

    def test_capital_structure_cards_map_high_risk_indicator(self) -> None:
        report = SimpleNamespace(
            capital_structure_indicator=_capital_indicator(
                read="rally_fade_risk",
                technical_score=38.0,
                dilution_pressure_score=66.0,
                offering_activity_score=70.0,
                option_exposure_mismatch_score=75.0,
                chase_risk_score=81.0,
                warnings=["Price is near parsed supply without confirmation."],
                recommendation_lines=["Avoid chase near parsed filing supply."],
            )
        )

        cards = _technical_capital_structure_cards(report)
        status_by_title = {card.title: card.status for card in cards}
        self.assertEqual(status_by_title["Capital Read"], "bad")
        self.assertEqual(status_by_title["Dilution Pressure"], "bad")
        self.assertEqual(status_by_title["Offering / ATM"], "bad")
        self.assertEqual(status_by_title["Option Mismatch"], "bad")
        self.assertEqual(status_by_title["Chase Risk"], "bad")
        self.assertIn(("Warning", "Price is near parsed supply without confirmation."), _technical_capital_structure_note_rows(report))

    def test_command_center_rows_surface_existing_report_data(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {"daily_1y": _candles(90), "timing_5m": _candles(80, start=118.0, step=0.05)},
            quote_snapshot=QuoteSnapshot("TST", bid=121.95, ask=122.05, last=122.0, mark=122.0),
            ticket=TechnicalTicket(side="buy", quantity=25, entry_price=122.0, stop_price=119.0, portfolio_value=100_000),
        )

        setup_titles = [card.title for card in _technical_setup_cards(report)]
        timeframe_rows = _technical_timeframe_stack_rows(report)
        prc_rows = _technical_prc_rows(report)
        score_rows = _technical_score_breakdown_rows(report)
        ticket_rows = _technical_ticket_check_rows(report)

        self.assertEqual(setup_titles, ["Regime", "Setup", "Timing", "Action Quality", "Confirmation", "Invalidation"])
        self.assertTrue(any(row[0] == "1y daily" and row[1] == "Regime" for row in timeframe_rows))
        self.assertTrue(any(row[0] == "10d 5m" and row[1] == "Timing" for row in timeframe_rows))
        self.assertTrue(all(len(row) == 7 for row in prc_rows))
        self.assertTrue(any(row[0] == "Overall" for row in score_rows))
        self.assertTrue(any(row[0] == "Ticket Quality" for row in score_rows))
        self.assertTrue(any(row[0] == "Entry location" for row in ticket_rows))
        self.assertTrue(any(row[0] == "Verdict" for row in ticket_rows))

    def test_missing_candles_and_warnings_shape_without_crashing(self) -> None:
        report = build_technical_command_center_report("TST", {"daily_1y": []}, warnings=["5m fetch failed"])

        timeframe_rows = _technical_timeframe_stack_rows(report)
        prc_rows = _technical_prc_rows(report)
        warning_rows = _technical_warning_rows(report)

        self.assertIn("No candles available", timeframe_rows[0][-1])
        self.assertEqual(prc_rows[0][0], "Unavailable")
        self.assertIn(PRC_PRESSURE_LINE_NOTICE, prc_rows[0][5])
        self.assertIn(("Command Center", "5m fetch failed"), warning_rows)


if __name__ == "__main__":
    unittest.main()
