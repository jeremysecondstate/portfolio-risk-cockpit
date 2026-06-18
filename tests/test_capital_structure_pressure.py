from __future__ import annotations

import unittest
from datetime import date
from typing import Any

from app.analytics.capital_structure_pressure import (
    CapitalStructureFilingText,
    analyze_capital_structure_pressure,
    capital_structure_technical_modifier,
    classify_capital_pressure_score,
    parse_capital_structure_terms,
    scan_capital_structure_filings,
    unknown_capital_structure_report,
)
from app.analytics.technical_analysis import (
    Candle,
    build_technical_command_center_report,
    format_technical_command_center_report,
)
from app.data.sec_edgar import SecCompany, SecFiling, SecFilingDocument


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


def _all_terms(report: Any) -> list[Any]:
    parsed = report.parsed_terms
    return [
        *parsed.common_share_classes,
        *parsed.preferred_series,
        *parsed.warrants,
        *parsed.convertibles,
        *parsed.offering_programs,
        *parsed.ads_adr_structures,
    ]


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


class FakeSecTextClient:
    def __init__(self) -> None:
        self.recent_calls = 0
        self.document_calls: list[str] = []

    def recent_filings(self, *args: Any, **kwargs: Any) -> list[SecFiling]:
        self.recent_calls += 1
        return []

    def document_text_url(self, filing_url: str, *args: Any, **kwargs: Any) -> str:
        self.document_calls.append(filing_url)
        return (
            "The Common Warrants are exercisable for 1,000,000 shares of common stock "
            "at an exercise price of $5.00 per share. This resale prospectus relates to shares offered by selling stockholders."
        )


class FakeSecondPassSecClient:
    def __init__(self, *, exhibit_text: str) -> None:
        self.exhibit_text = exhibit_text
        self.index_calls = 0
        self.exhibit_calls: list[str] = []

    def recent_filings(self, *args: Any, **kwargs: Any) -> list[SecFiling]:
        company = SecCompany(ticker="TEST", cik="0001234567", title="Test Corp")
        return [
            SecFiling(
                company=company,
                accession_number="0001234567-26-000001",
                filing_date="2026-04-12",
                report_date="2026-04-12",
                form="8-K",
                primary_document="test-8k.htm",
                description="Material agreement",
            )
        ]

    def document_text_url(self, filing_url: str, *args: Any, **kwargs: Any) -> str:
        return (
            "The company entered into a securities purchase agreement, issued warrants, "
            "and warned investors about future dilution. The exercise price will be determined later."
        )

    def filing_documents(self, filing: SecFiling) -> list[SecFilingDocument]:
        self.index_calls += 1
        return [
            SecFilingDocument(
                filing=filing,
                document="ex-4-1.htm",
                description="Warrant agreement",
                type="EX-4.1",
                sequence="2",
            )
        ]

    def document_text(self, document: SecFilingDocument) -> str:
        self.exhibit_calls.append(document.url)
        return self.exhibit_text


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

    def test_parses_series_a_preferred_terms(self) -> None:
        report = _scan(
            "The company designated 10,000 shares of Series A Preferred Stock. "
            "Each share of Series A Preferred Stock has a liquidation preference of $1,000 per share. "
            "The Series A Preferred Stock is convertible into common stock at a conversion price of $2.50 per share. "
            "Holders have voting rights on an as-converted basis and redemption rights after June 30, 2028."
        )
        preferred = report.parsed_terms.preferred_series[0]
        self.assertEqual(preferred.series_name, "Series A Preferred Stock")
        self.assertEqual(preferred.shares, 10_000)
        self.assertEqual(preferred.conversion_price, 2.5)
        self.assertEqual(preferred.liquidation_preference, 1000.0)
        self.assertIsNotNone(preferred.voting_language)
        self.assertIsNotNone(preferred.conversion_language)
        self.assertIsNotNone(preferred.redemption_language)
        self.assertTrue(any("Preferred conversion overhang" in line for line in report.parsed_terms.technical_impact_lines))

    def test_parses_series_b_preferred_redemption_voting_conversion_terms(self) -> None:
        report = _scan(
            "The Series B Convertible Preferred Stock is redeemable beginning July 1, 2027. "
            "Holders vote together with common stock on an as-converted basis. "
            "The Series B Convertible Preferred Stock is convertible into common stock at a conversion price of $3.75 per share."
        )
        preferred = report.parsed_terms.preferred_series[0]
        self.assertEqual(preferred.series_name, "Series B Convertible Preferred Stock")
        self.assertEqual(preferred.conversion_price, 3.75)
        self.assertIsNotNone(preferred.redemption_language)
        self.assertIsNotNone(preferred.voting_language)
        self.assertIsNotNone(preferred.conversion_language)

    def test_detects_convertible_notes(self) -> None:
        report = _scan("The issuer sold convertible senior notes with a conversion rate subject to adjustment.")
        self.assertIn("Convertibles / notes", {signal.label for signal in report.signals})

    def test_parses_common_and_prefunded_warrants(self) -> None:
        report = _scan(
            "The Common Warrants are exercisable for 1,250,000 shares of common stock at an exercise price of $5.00 per share "
            "and expire on June 30, 2028. The Pre-Funded Warrants are exercisable for 500,000 shares of common stock at a "
            "nominal exercise price of $0.001 per share and may be exercised on a cashless exercise basis."
        )
        common = next(item for item in report.parsed_terms.warrants if item.instrument_type == "common warrant")
        prefunded = next(item for item in report.parsed_terms.warrants if item.instrument_type == "pre-funded warrant")
        self.assertEqual(common.underlying_shares, 1_250_000)
        self.assertEqual(common.exercise_price, 5.0)
        self.assertEqual(common.expiration_date, "june 30, 2028")
        self.assertEqual(prefunded.exercise_price, 0.001)
        self.assertIsNotNone(prefunded.cashless_exercise_language)
        self.assertTrue(any(level.level_type == "warrant_strike" and level.price == 0.001 for level in report.possible_supply_levels))

    def test_parses_placement_agent_warrants(self) -> None:
        report = _scan(
            "The Placement Agent Warrants are exercisable for 125,000 shares of common stock at an exercise price of $6.25 per share "
            "and expire on August 15, 2029."
        )
        warrant = report.parsed_terms.warrants[0]
        self.assertEqual(warrant.instrument_type, "placement agent warrant")
        self.assertEqual(warrant.series_name, "Placement Agent Warrants")
        self.assertEqual(warrant.underlying_shares, 125_000)
        self.assertEqual(warrant.exercise_price, 6.25)
        self.assertEqual(warrant.expiration_date, "august 15, 2029")

    def test_detects_share_classes_and_voting_control(self) -> None:
        report = _scan(
            "The company has Class A common stock and Class B common stock. "
            "The dual class structure gives founders high vote voting power."
        )
        self.assertIn("Share classes / voting control", {signal.label for signal in report.signals})

    def test_parses_class_a_and_class_b_common_terms(self) -> None:
        report = _scan(
            "The authorized capital includes 10,000,000 shares of Class A common stock entitled to one vote per share "
            "and 2,000,000 shares of Class B common stock entitled to ten votes per share. "
            "The Class B common stock is convertible into Class A common stock at any time."
        )
        by_class = {item.class_name: item for item in report.parsed_terms.common_share_classes}
        self.assertEqual(by_class["Class A Common Stock"].shares, 10_000_000)
        self.assertEqual(by_class["Class B Common Stock"].shares, 2_000_000)
        self.assertIsNotNone(by_class["Class A Common Stock"].voting_language)
        self.assertIsNone(by_class["Class A Common Stock"].conversion_language)
        self.assertIsNotNone(by_class["Class B Common Stock"].conversion_language)

    def test_parses_high_vote_super_voting_and_non_voting_classes(self) -> None:
        report = _scan(
            "The company has 1,000,000 shares of high-vote common stock with twenty votes per share "
            "and 5,000,000 shares of non-voting common stock with no voting rights. "
            "The super-voting common stock controls the vote."
        )
        by_class = {item.class_name: item for item in report.parsed_terms.common_share_classes}
        self.assertEqual(by_class["High-Vote Common Stock"].shares, 1_000_000)
        self.assertEqual(by_class["Non-Voting Common Stock"].shares, 5_000_000)
        self.assertIn("Super-Voting Common Stock", by_class)
        self.assertIsNotNone(by_class["High-Vote Common Stock"].voting_language)
        self.assertIsNotNone(by_class["Non-Voting Common Stock"].voting_language)

    def test_parses_atm_and_resale_program_terms(self) -> None:
        report = _scan(
            "Under the sales agreement and equity distribution agreement, we may offer and sell up to $75,000,000 of common stock "
            "from time to time in an at-the-market offering program.",
            "This resale prospectus relates to the resale of 4,000,000 shares of common stock by selling stockholders.",
        )
        atm = next(item for item in report.parsed_terms.offering_programs if item.program_type == "ATM program")
        resale = next(item for item in report.parsed_terms.offering_programs if item.program_type == "Resale prospectus")
        self.assertEqual(atm.amount, 75_000_000)
        self.assertEqual(resale.shares, 4_000_000)
        self.assertTrue(any("ATM overhang warning" in line for line in report.parsed_terms.technical_impact_lines))
        self.assertTrue(any("Resale prospectus overhang warning" in line for line in report.parsed_terms.technical_impact_lines))

    def test_parses_s3_shelf_and_424b5_offering_price(self) -> None:
        report = scan_capital_structure_filings(
            "TEST",
            company_name="Test Corp",
            filings=[
                _filing(
                    "This Form S-3 shelf registration statement and prospectus supplement on Form 424B5 "
                    "offers 2,500,000 shares of common stock at a public offering price of $4.20 per share.",
                    form="424B5",
                )
            ],
            as_of=AS_OF,
        )
        programs = report.parsed_terms.offering_programs
        self.assertTrue(any(program.program_type == "Shelf registration" for program in programs))
        self.assertTrue(any(program.program_type == "Offering" for program in programs))
        self.assertTrue(any(program.shares == 2_500_000 for program in programs))
        self.assertTrue(any(program.offering_price == 4.2 for program in programs))
        self.assertTrue(any(level.level_type == "offering_price" and level.price == 4.2 for level in report.possible_supply_levels))

    def test_parses_convertible_notes_terms(self) -> None:
        report = _scan(
            "The company issued $10,000,000 aggregate principal amount of 8.00% Convertible Senior Notes due 2029. "
            "The notes have a conversion rate of 25.0000 shares of common stock per $1,000 principal amount, "
            "equivalent to a conversion price of $40.00 per share. The notes mature on May 1, 2029."
        )
        note = report.parsed_terms.convertibles[0]
        self.assertEqual(note.principal_amount, 10_000_000)
        self.assertEqual(note.coupon_rate, "8.00%")
        self.assertEqual(note.conversion_price, 40.0)
        self.assertIn("25.0000 shares", note.conversion_rate or "")
        self.assertEqual(note.maturity_date, "may 1, 2029")

    def test_parses_ads_adr_foreign_issuer_terms(self) -> None:
        report = _scan(
            "We are a foreign private issuer. American Depositary Shares, or ADSs, are evidenced by American Depositary Receipts. "
            "Each ADS represents two Class A ordinary shares. Holders of ADSs may instruct the depositary to vote the ordinary shares."
        )
        ads = report.parsed_terms.ads_adr_structures[0]
        self.assertEqual(ads.structure_name, "Foreign Private Issuer")
        self.assertIn("two class a ordinary shares", ads.ratio or "")
        self.assertEqual(ads.ordinary_share_class, "Class A Ordinary Shares")
        self.assertTrue(any("ADS/ADR" in warning for warning in report.warnings))

    def test_ambiguous_terms_do_not_produce_fake_values(self) -> None:
        filing = _filing(
            "The company may issue warrants and convertible notes. The exercise price was not determined. "
            "The conversion price will be determined in the future. Gross proceeds may be $50 million. "
            "The maturity date has not been determined."
        )
        parsed = parse_capital_structure_terms([filing])
        self.assertEqual(parsed.warrants, [])
        self.assertEqual(len(parsed.convertibles), 1)
        self.assertIsNone(parsed.convertibles[0].conversion_price)
        self.assertIsNone(parsed.convertibles[0].maturity_date)
        report = scan_capital_structure_filings("TEST", filings=[filing], as_of=AS_OF)
        self.assertEqual(report.possible_supply_levels, [])

    def test_every_parsed_term_is_source_backed(self) -> None:
        report = _scan(
            "The company has 1,000 shares of Class A common stock with one vote per share. "
            "The Series A Preferred Stock has a liquidation preference of $1,000 and a conversion price of $2.50. "
            "Common Warrants are exercisable for 100,000 shares at an exercise price of $5.00 and expire on June 30, 2028. "
            "The issuer sold $5,000,000 aggregate principal amount of Convertible Notes with a conversion price of $10.00 and maturity date of May 1, 2029. "
            "This resale prospectus relates to the resale of 300,000 shares by selling stockholders. "
            "Each ADS represents one ordinary share."
        )
        self.assertGreater(len(_all_terms(report)), 0)
        for term in _all_terms(report):
            self.assertEqual(term.source_form, "8-K")
            self.assertEqual(term.source_date, "2026-04-12")
            self.assertTrue(term.source_url.startswith("https://www.sec.gov/test-"))
            self.assertTrue(term.excerpt)

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

    def test_fmp_metadata_narrows_sec_text_fetches_without_replacing_source_text(self) -> None:
        client = FakeSecTextClient()
        metadata = [
            {
                "symbol": "TEST",
                "companyName": "Test Corp",
                "cik": "1234567",
                "form": "424B5",
                "filingDate": "2026-04-12",
                "accessionNumber": "0001234567-26-000001",
                "primaryDocument": "test-424b5.htm",
                "finalLink": "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/test-424b5.htm",
            },
            {
                "symbol": "TEST",
                "companyName": "Test Corp",
                "cik": "1234567",
                "form": "4",
                "filingDate": "2026-04-11",
                "accessionNumber": "0001234567-26-000002",
                "primaryDocument": "test-form4.htm",
            },
        ]

        report = analyze_capital_structure_pressure(
            "TEST",
            client=client,
            fmp_profile={"company_name": "Test Corp", "cik": "1234567"},
            fmp_filing_metadata=metadata,
            max_documents=4,
            as_of=AS_OF,
        )

        self.assertEqual(client.recent_calls, 0)
        self.assertEqual(len(client.document_calls), 1)
        self.assertEqual(report.source_label, "FMP metadata + SEC source text")
        self.assertEqual(report.source_diagnostics["sec_filing_text_documents_fetched"], 1)
        self.assertIn("Warrants", {signal.label for signal in report.signals})
        self.assertTrue(report.parsed_terms.warrants)
        self.assertTrue(report.parsed_terms.warrants[0].excerpt)

    def test_second_pass_exhibit_scan_fills_missing_pressure_level(self) -> None:
        client = FakeSecondPassSecClient(
            exhibit_text=(
                "The Common Warrants are exercisable for 2,000,000 shares of common stock "
                "at an exercise price of $3.50 per share."
            )
        )

        report = analyze_capital_structure_pressure("TEST", client=client, max_documents=2, as_of=AS_OF)

        self.assertEqual(client.index_calls, 1)
        self.assertEqual(len(client.exhibit_calls), 1)
        self.assertTrue(any(level.level_type == "warrant_strike" and level.price == 3.5 for level in report.possible_supply_levels))
        self.assertEqual(report.source_diagnostics["relevant_exhibits_considered"], 1)
        self.assertEqual(report.source_diagnostics["second_pass_documents_fetched"], 1)
        self.assertIn("second_pass_result", report.source_diagnostics)

    def test_no_level_after_pressure_scan_gets_explicit_diagnostic(self) -> None:
        client = FakeSecondPassSecClient(
            exhibit_text=(
                "The warrant agreement describes common warrants and future dilution, "
                "but the exercise price will be determined in a later notice."
            )
        )

        report = analyze_capital_structure_pressure("TEST", client=client, max_documents=2, as_of=AS_OF)

        self.assertTrue(report.signals)
        self.assertEqual(report.possible_supply_levels, [])
        self.assertEqual(report.source_diagnostics["no_level_reason"], "Pressure signals were found, but no explicit price level was parsed.")
        self.assertEqual(report.source_diagnostics["second_pass_documents_fetched"], 1)
        self.assertIn("Pressure signals were found, but no explicit price level was parsed.", report.explanation_lines)
        self.assertIn("not a failed fetch", " ".join(report.explanation_lines))

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
