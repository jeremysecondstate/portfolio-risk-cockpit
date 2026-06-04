from __future__ import annotations

import unittest
from datetime import date
from typing import Any

from app.analytics.capital_structure_pressure import (
    CapitalStructureFilingText,
    analyze_capital_structure_pressure,
    capital_structure_technical_modifier,
    classify_capital_pressure_score,
    scan_capital_structure_filings,
    unknown_capital_structure_report,
)
from app.analytics.technical_analysis import (
    Candle,
    build_technical_command_center_report,
    format_technical_command_center_report,
)
from app.data.sec_edgar import SecCompany, SecFiling


AS_OF = date(2026, 6, 4)


def _filing(
    text: str,
    *,
    form: str = "8-K",
    filing_date: str = "2026-04-12",
    source_url: str = "https://www.sec.gov/test-filing.htm",
) -> CapitalStructureFilingText:
    return CapitalStructureFilingText(form=form, filing_date=filing_date, source_url=source_url, text=text)


def _scan(*texts: str) -> Any:
    filings = [_filing(text, source_url=f"https://www.sec.gov/test-{index}.htm") for index, text in enumerate(texts)]
    return scan_capital_structure_filings("TEST", company_name="Test Corp", filings=filings, as_of=AS_OF)


def _candles(count: int, *, start: float = 20.0, step: float = 0.20) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        close = price + step
        rows.append(Candle(index, price, close + 0.20, price - 0.20, close, 1_000 + index * 10))
        price = close
    return rows


class FakeFailingSecClient:
    def recent_filings(self, *args: Any, **kwargs: Any) -> list[SecFiling]:
        raise RuntimeError("offline")


class FakeSecClient:
    def recent_filings(self, *args: Any, **kwargs: Any) -> list[SecFiling]:
        company = SecCompany(ticker="TEST", cik="0001234567", title="Test Corp")
        return [
            SecFiling(
                company=company,
                accession_number="0001234567-26-000001",
                filing_date="2026-04-12",
                report_date="2026-04-12",
                form="424B5",
                primary_document="test.htm",
                description="Prospectus supplement",
            )
        ]

    def document_text_url(self, *args: Any, **kwargs: Any) -> str:
        return (
            "This prospectus supplement describes an at-the-market offering program "
            "under an equity distribution agreement and warns of future dilution."
        )


class CapitalStructurePressureTests(unittest.TestCase):
    def test_detects_shelf_and_atm_language(self) -> None:
        report = _scan(
            "The company filed a shelf registration statement on Form S-3. "
            "The prospectus supplement covers securities offered through an at-the-market ATM program."
        )
        self.assertIn("Shelf / registration capacity", {signal.label for signal in report.signals})
        self.assertGreaterEqual(report.supply_overhang_score, 25)
        self.assertEqual(report.read, "Moderate")

    def test_detects_resale_prospectus_and_selling_stockholders(self) -> None:
        report = _scan("This resale prospectus relates to shares offered by selling stockholders.")
        self.assertIn("Shelf / registration capacity", {signal.label for signal in report.signals})

    def test_detects_warrants_and_exercise_price(self) -> None:
        report = _scan("The company issued common warrants to purchase shares at an exercise price of $5.00 per share.")
        self.assertIn("Warrants", {signal.label for signal in report.signals})
        self.assertTrue(any(level.level_type == "warrant_strike" and level.price == 5.0 for level in report.possible_supply_levels))

    def test_detects_preferred_stock_and_conversion_price(self) -> None:
        report = _scan(
            "The Series A Preferred Stock is convertible preferred stock with a liquidation preference. "
            "The conversion price is $2.25 per share."
        )
        labels = {signal.label for signal in report.signals}
        self.assertIn("Preferred stock", labels)
        self.assertIn("Convertibles / notes", labels)
        self.assertTrue(any(level.level_type == "conversion_price" and level.price == 2.25 for level in report.possible_supply_levels))

    def test_detects_convertible_notes(self) -> None:
        report = _scan("The issuer sold convertible senior notes with a conversion rate subject to adjustment.")
        self.assertIn("Convertibles / notes", {signal.label for signal in report.signals})

    def test_detects_share_classes_and_voting_control(self) -> None:
        report = _scan(
            "The company has Class A common stock and Class B common stock. "
            "The dual class structure gives founders high vote voting power."
        )
        self.assertIn("Share classes / voting control", {signal.label for signal in report.signals})

    def test_detects_dilution_warning(self) -> None:
        report = _scan(
            "Investors will experience substantial dilution and future dilution. "
            "We may issue additional shares of common stock."
        )
        self.assertIn("Dilution warning", {signal.label for signal in report.signals})

    def test_repeated_words_do_not_double_count_same_group_in_same_filing(self) -> None:
        report = _scan("warrant " * 40 + "common warrants with an exercise price of $3.50.")
        warrant_signals = [signal for signal in report.signals if signal.label == "Warrants"]
        self.assertEqual(len(warrant_signals), 1)
        self.assertLessEqual(report.supply_overhang_score, 35)

    def test_maps_scores_to_pressure_reads(self) -> None:
        self.assertEqual(classify_capital_pressure_score(0), "Low")
        self.assertEqual(classify_capital_pressure_score(24), "Low")
        self.assertEqual(classify_capital_pressure_score(25), "Moderate")
        self.assertEqual(classify_capital_pressure_score(54), "Moderate")
        self.assertEqual(classify_capital_pressure_score(55), "High")

    def test_returns_unknown_on_no_filings_or_fetch_failure(self) -> None:
        empty = scan_capital_structure_filings("TEST", filings=[], as_of=AS_OF)
        self.assertEqual(empty.read, "Unknown")

        failed = analyze_capital_structure_pressure("TEST", client=FakeFailingSecClient(), as_of=AS_OF)
        self.assertEqual(failed.read, "Unknown")
        self.assertIn("overlay unavailable", " ".join(failed.warnings))

    def test_extracts_price_levels_conservatively(self) -> None:
        no_level = _scan("The exercise price was not determined. Gross proceeds may be $50 million.")
        self.assertEqual(no_level.possible_supply_levels, [])

        level = _scan("Warrants are exercisable at $7.25 per share.")
        self.assertTrue(any(item.level_type == "warrant_strike" and item.price == 7.25 for item in level.possible_supply_levels))

    def test_analyzer_uses_fake_sec_client_without_network(self) -> None:
        report = analyze_capital_structure_pressure("TEST", client=FakeSecClient(), max_documents=1, as_of=AS_OF)
        self.assertEqual(report.company_name, "Test Corp")
        self.assertEqual(report.filings_analyzed, 1)
        self.assertIn("Shelf / registration capacity", {signal.label for signal in report.signals})

    def test_technical_modifier_language(self) -> None:
        high = _scan(
            "A shelf registration statement on Form S-3 supports an at-the-market program. "
            "Selling stockholders may resell shares. Warrants have an exercise price of $5.00. "
            "Convertible senior notes have a conversion price of $2.25. The filing warns of substantial dilution."
        )
        self.assertIn("supply-fragile", capital_structure_technical_modifier("Bullish", high))
        self.assertIn("reinforced", capital_structure_technical_modifier("Bearish", high))

    def test_command_center_report_includes_capital_structure_section(self) -> None:
        high = _scan(
            "A registration statement on Form S-3 includes a prospectus supplement, an ATM program, "
            "selling stockholders, warrants with an exercise price of $5.00, convertible notes with a "
            "conversion price of $2.25, preferred stock, substantial dilution, a reverse stock split, "
            "going concern language, Nasdaq compliance, Class A common stock, and Class B common stock."
        )
        report = build_technical_command_center_report(
            "TEST",
            {"daily_1y": _candles(90), "timing_5m": _candles(80)},
            capital_structure_pressure=high,
        )
        text = format_technical_command_center_report(report)
        self.assertIn("CAPITAL STRUCTURE PRESSURE", text)
        self.assertIn("Supply overhang score", text)
        self.assertIn("Breakouts need stronger volume", text)

    def test_command_center_report_renders_when_overlay_is_unknown(self) -> None:
        report = build_technical_command_center_report(
            "TEST",
            {"daily_1y": _candles(90), "timing_5m": _candles(80)},
            capital_structure_pressure=unknown_capital_structure_report(
                "TEST",
                warnings=["Capital structure overlay unavailable: offline"],
            ),
        )
        text = format_technical_command_center_report(report)
        self.assertIn("TECHNICAL COMMAND CENTER - TEST", text)
        self.assertIn("Capital structure overlay unavailable", text)


if __name__ == "__main__":
    unittest.main()
