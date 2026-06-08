from __future__ import annotations

import tkinter as tk
from tkinter import ttk
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
    _capital_structure_pressure_status,
    _capital_structure_empirical_cards,
    _capital_structure_empirical_note_rows,
    _capital_structure_empirical_supply_rows,
    _bind_notebook_tab_detach_drag,
    _current_research_tab_detail,
    _detail_button_text,
    _open_readout_popout,
    _overview_tab,
    _readout_launcher,
    _set_research_text,
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
from app.ui.research_widgets import Checklist


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


def _empty_terms() -> SimpleNamespace:
    return SimpleNamespace(
        common_share_classes=[],
        preferred_series=[],
        warrants=[],
        convertibles=[],
        offering_programs=[],
        ads_adr_structures=[],
    )


def _tk_root(testcase: unittest.TestCase) -> tk.Tk:
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        testcase.skipTest(f"Tk display unavailable: {exc}")
    root.withdraw()
    return root


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

    def test_capital_structure_no_level_rows_explain_pressure_report(self) -> None:
        pressure = SimpleNamespace(
            read="High",
            filings_analyzed=3,
            supply_overhang_score=67,
            possible_supply_levels=[],
            parsed_terms=_empty_terms(),
            signals=[SimpleNamespace(label="Shelf / registration capacity")],
            warnings=[],
            explanation_lines=["High pressure came from recent registration and resale language."],
        )
        report = SimpleNamespace(capital_structure_indicator=None, capital_structure_pressure=pressure)

        cards = _technical_capital_structure_cards(report)
        supply_rows = _technical_capital_structure_supply_rows(report)
        note_rows = _technical_capital_structure_note_rows(report)

        self.assertEqual(cards[0].label, "No Source Level")
        self.assertIn("no supported warrant exercise", supply_rows[0][-1])
        self.assertIn("no supply level is inferred", supply_rows[0][-1])
        self.assertIn(("Explanation", "High pressure came from recent registration and resale language."), note_rows)

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

    def test_empirical_supply_helpers_surface_absorption_read(self) -> None:
        read = SimpleNamespace(
            empirical_intelligence=SimpleNamespace(
                supply_absorption=SimpleNamespace(
                    read="absorption",
                    label="Supply absorption",
                    score=78.0,
                    level=5.0,
                    level_label="warrant strike",
                    distance_percent=2.5,
                    evidence_lines=("Nearest warrant strike: $5.00; latest close is above the level.",),
                    confirmation_lines=("Confirmation: hold above $5.00 with normal volume.",),
                    invalidation_lines=("Invalidation: lose $5.00 after testing it.",),
                    warnings=(),
                )
            )
        )

        cards = _capital_structure_empirical_cards(read)
        supply_rows = _capital_structure_empirical_supply_rows(read)
        note_rows = _capital_structure_empirical_note_rows(read)

        self.assertEqual(cards[0].title, "Empirical Supply")
        self.assertEqual(cards[0].status, "good")
        self.assertEqual(supply_rows[0][0], "Empirical: warrant strike")
        self.assertEqual(supply_rows[0][1], "$5.00")
        self.assertEqual(supply_rows[0][2], "+2.50%")
        self.assertTrue(any(row[0] == "Empirical supply" for row in note_rows))

    def test_capital_structure_pressure_status_distinguishes_no_level_from_failure(self) -> None:
        no_level = SimpleNamespace(
            read="Low",
            filings_analyzed=2,
            supply_overhang_score=0,
            possible_supply_levels=[],
            parsed_terms=_empty_terms(),
            signals=[],
            warnings=[],
        )
        failed = SimpleNamespace(
            read="Unknown",
            filings_analyzed=0,
            supply_overhang_score=0,
            possible_supply_levels=[],
            parsed_terms=_empty_terms(),
            signals=[],
            warnings=["Capital structure overlay unavailable: SEC recent filings fetch failed."],
        )

        self.assertEqual(_capital_structure_pressure_status(no_level).status, "no parsed supply level")
        self.assertEqual(_capital_structure_pressure_status(failed).status, "error")

    def test_capital_structure_pressure_status_keeps_signal_context_fresh(self) -> None:
        signal_no_level = SimpleNamespace(
            read="High",
            filings_analyzed=4,
            supply_overhang_score=67,
            possible_supply_levels=[],
            parsed_terms=_empty_terms(),
            signals=[SimpleNamespace(label="Shelf / registration capacity")],
            warnings=[],
        )

        status = _capital_structure_pressure_status(signal_no_level)

        self.assertEqual(status.status, "fresh/cache")
        self.assertIn("no source-backed supply price level", status.message)

    def test_detail_button_text_formats_popout_content(self) -> None:
        text = _detail_button_text("Warnings", ["First warning", "Second warning"])

        self.assertIn("Warnings", text)
        self.assertIn("- First warning", text)
        self.assertIn("- Second warning", text)

    def test_readout_launcher_reuses_and_refreshes_popout(self) -> None:
        root = _tk_root(self)
        try:
            frame = ttk.Frame(root)
            frame.grid()
            source = _readout_launcher(frame, title="Warnings", button_text="Warnings", row=0)
            self.assertEqual(source._readout_button.cget("text"), "Warnings")  # type: ignore[attr-defined]
            self.assertEqual(source._readout_launcher.grid_info().get("padx"), (0, 10))  # type: ignore[attr-defined]

            _set_research_text(source, "First warning")
            _open_readout_popout(source)
            window = source._readout_window  # type: ignore[attr-defined]
            target = source._readout_popout_text  # type: ignore[attr-defined]
            self.assertTrue(window.winfo_exists())
            self.assertIn("First warning", target.get("1.0", tk.END))

            _set_research_text(source, "Updated warning")
            self.assertIn("Updated warning", target.get("1.0", tk.END))
            _open_readout_popout(source)
            self.assertIs(source._readout_window, window)  # type: ignore[attr-defined]
        finally:
            root.destroy()

    def test_focus_current_tab_detail_and_detach_binding_still_work(self) -> None:
        root = _tk_root(self)
        try:
            notebook = ttk.Notebook(root)
            notebook.grid()
            tab = ttk.Frame(notebook)
            notebook.add(tab, text="Technicals")
            notebook.select(tab)
            root.schwab_research_tabs = notebook  # type: ignore[attr-defined]

            technical_frame = ttk.Frame(root)
            detail = tk.Text(technical_frame)
            technical_frame.detail_text = detail  # type: ignore[attr-defined]
            root.schwab_research_technicals_frame = technical_frame  # type: ignore[attr-defined]

            self.assertIs(_current_research_tab_detail(root), detail)
            _bind_notebook_tab_detach_drag(root, notebook)
            self.assertTrue(notebook.bind("<B1-Motion>"))
        finally:
            root.destroy()

    def test_overview_narrative_sections_are_launchers_not_inline_checklists(self) -> None:
        root = _tk_root(self)
        try:
            notebook = ttk.Notebook(root)
            notebook.grid()
            frame = _overview_tab(notebook)

            for container_name in (
                "recommendation_evidence_lists",
                "recommendation_planning",
                "recommendation_triggers",
                "recommendation_followups",
            ):
                container = getattr(frame, container_name)
                self.assertGreater(len(container.winfo_children()), 0)
                self.assertFalse(any(isinstance(child, Checklist) for child in container.winfo_children()))

            launcher_buttons = [
                frame.recommendation_supporting_text._readout_button.cget("text"),  # type: ignore[attr-defined]
                frame.recommendation_contradictions_text._readout_button.cget("text"),  # type: ignore[attr-defined]
                frame.recommendation_reward_risk_text._readout_button.cget("text"),  # type: ignore[attr-defined]
                frame.recommendation_position_sizing_text._readout_button.cget("text"),  # type: ignore[attr-defined]
                frame.recommendation_invalidation_text._readout_button.cget("text"),  # type: ignore[attr-defined]
                frame.recommendation_confirmation_text._readout_button.cget("text"),  # type: ignore[attr-defined]
            ]
            self.assertEqual(
                launcher_buttons,
                [
                    "Supporting Evidence",
                    "Contradictions",
                    "Reward/Risk + Planning EV",
                    "Position Sizing",
                    "Invalidation Lines",
                    "Confirmation Lines",
                ],
            )
        finally:
            root.destroy()

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

    def test_setup_cards_mark_prior_confirmation_trigger_cleared(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {"daily_1y": _candles(90), "timing_5m": _candles(80, start=118.0, step=0.05)},
            quote_snapshot=QuoteSnapshot("TST", bid=121.95, ask=122.05, last=122.0, mark=122.0),
            ticket=TechnicalTicket(side="buy", quantity=25, entry_price=122.0, stop_price=119.0, portfolio_value=100_000),
        )
        confirmation_level = report.setup_classification.confirmation_level
        self.assertIsNotNone(confirmation_level)

        cards = {card.title: card for card in _technical_setup_cards(report, float(confirmation_level or 0) + 5.0)}

        self.assertEqual(cards["Confirmation"].label, "Prior trigger cleared")
        self.assertEqual(cards["Confirmation"].status, "good")
        self.assertIn("Next confirmation", cards["Confirmation"].why)
        self.assertNotIn("Price level the setup needs to confirm", cards["Confirmation"].why)

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
