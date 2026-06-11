from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.analytics.ipo_filing_chat import (
    CHAT_FULL_TEXT_CHAR_LIMIT,
    CHAT_REQUEST_CHAR_LIMIT,
    CHAT_RETRIEVED_CHAR_LIMIT,
    FILING_CHAT_SYSTEM_PROMPT,
    IpoFilingChatSession,
    OpenAiIpoFilingChatClient,
    OpenAiIpoFilingChatError,
    build_ipo_filing_chat_context,
    filing_context_payload_for_prompt,
    render_ipo_filing_chat_transcript_markdown,
    save_ipo_filing_chat_transcript,
    verified_filing_facts_for_prompt,
)
from app.analytics.openai_ipo_report import build_ipo_filing_source_bundle
from app.analytics.ipo_pipeline import IpoPipelineRecord
from app.data.sec_edgar import SEC_ARCHIVES_BASE_URL
from app.ui import ipo_pipeline_extension
from app.ui.ipo_filing_chat_window import IpoFilingChatWindow


SAMPLE_FILING_TEXT = """
Prospectus Summary
Silentium Ltd. develops active noise control systems for industrial equipment and commercial facilities.
The company has 24 enterprise customers and multi-year contracts with two global equipment manufacturers.

The Offering
We are offering 2,000,000 ordinary shares.
The estimated initial public offering price is between $6.00 and $8.00 per ordinary share.
We have applied to list our ordinary shares on the Nasdaq Capital Market under the symbol SLNT.
The underwriters are Example Securities LLC and Test Capital LLC.

Use of Proceeds
We intend to use the net proceeds for sales expansion, product development, working capital, and repayment of indebtedness.

Selected Consolidated Statements of Operations
($ in thousands)
Revenue 4,200 1,850
Net loss 7,100 3,900

Capitalization
Cash and cash equivalents were $1.2 million. Total debt was $3.5 million.

Dilution
At an assumed initial public offering price of $7.00 per share, immediate dilution to new investors will be $5.75 per share.

Risk Factors
Our recurring losses and negative cash flows raise substantial doubt about our ability to continue as a going concern.
We depend on a limited number of large enterprise customers.
"""


def _metaoptics_f1a_text() -> str:
    return (Path(__file__).parent / "fixtures" / "metaoptics_f1a_excerpt.txt").read_text(encoding="utf-8")


def _metaoptics_record() -> IpoPipelineRecord:
    return IpoPipelineRecord(
        cik="2099681",
        company_name="MetaOptics Ltd",
        proposed_ticker=None,
        form="F-1/A",
        filed_date="2026-06-10",
        ipo_status="Filed",
        sic="",
        sector=None,
        industry="Metalens technology",
        exchange=None,
        filing_url=f"{SEC_ARCHIVES_BASE_URL}/2099681/000121390026067164/ea0270354-12.htm",
        accession_number="0001213900-26-067164",
        is_foreign_issuer=True,
    )


def _nuclea_record() -> IpoPipelineRecord:
    return IpoPipelineRecord(
        cik="2101996",
        company_name="Nuclea Energy Inc.",
        proposed_ticker=None,
        form="F-1/A",
        filed_date="2026-06-09",
        ipo_status="Filed",
        sic="",
        sector=None,
        industry="Nuclear energy technology",
        exchange=None,
        filing_url=f"{SEC_ARCHIVES_BASE_URL}/2101996/000121390026066889/ea0270043-16.htm",
        accession_number="0001213900-26-066889",
        is_foreign_issuer=True,
    )


def _nuclea_f1a_excerpt() -> str:
    return """
NUCLEA ENERGY INC.
PRELIMINARY PROSPECTUS
5,555,556 Common Shares
We estimate that the initial public offering price will be between US$8.00 and US$10.00 per Common Share.
The assumed initial public offering price is US$9.00 per Common Share, the midpoint of the range.
We have applied to list our Common Shares on the NYSE under the symbol "NCLA".
This offering will not close unless the NYSE approves our Common Shares for listing.
Joseph Gunnar & Co., LLC
Sole Book-Runner

TABLE OF CONTENTS
Explanatory Note 1
Prospectus Summary 3
Risk Factors 12
Use of Proceeds 35
Capitalization 40
Dilution 42
Taxation 120
Financial Statements F-1

Explanatory Note
This Registration Statement contains two prospectuses: a Public Offering Prospectus and a Resale Prospectus.
The Public Offering Prospectus relates to the initial public offering by the Company of 5,555,556 Common Shares.
The Resale Prospectus relates to the resale from time to time by the Selling Shareholders of 2,817,294 Common Shares.
The Resale Offering is separate from the initial public offering and we will not receive any proceeds from shares sold by Selling Shareholders.
No resale sales may be made until the Common Shares sold in the initial public offering begin trading on the NYSE.
Consummation of the Resale Prospectus offering is conditioned on consummation of the initial public offering.
The Resale Prospectus omits Capitalization and Dilution, includes a Selling Shareholders section, and replaces Underwriting with a Selling Shareholder Plan of Distribution.
Alternate Pages for the Resale Prospectus include a Selling Shareholders table and plan of distribution.

Prospectus Summary
Nuclea Energy Inc. is a development-stage nuclear energy technology company focused on the Morpheus Microreactor.

The Offering
We are offering 5,555,556 Common Shares. The underwriter has a 45-day over-allotment option to purchase up to an additional 15% or 833,333 Common Shares.

Use of Proceeds
We intend to use net proceeds for reactor development, licensing activities, working capital, and the Moltex Asset Acquisition.

Recent Developments
Moltex Asset Acquisition. We entered into an exclusivity agreement for the proposed acquisition of assets from Moltex Energy Limited.
The purchase price is \u00a36,183,793, equivalent to CAD$11.5 million or approximately US$8.5 million.
The target assets consist primarily of intellectual property and related rights, including patents, patent applications, technical know-how, engineering designs, technical documentation, experimental and modeling data, software, and regulatory work product.
The assets are expected to relate to Moltex's Stable Salt Reactor - Wasteburner (SSR-W) and WAste To Stable Salt (WATSS) spent fuel recycling process.
Moltex Energy Limited is in administration and the assets are distressed assets. Based on available information, Nuclea does not expect to assume historical liabilities.
We paid a non-refundable exclusivity fee of \u00a3268,861, a first extension fee of \u00a3110,000, and a second extension fee of \u00a3400,000.
We may obtain a further extension through July 8, 2026 for \u00a3200,000. The transaction remains subject to due diligence and negotiation of a definitive sale agreement.

Regulatory Status
We have begun preliminary pre-application engagement with the NRC through NuMark Associates.
We have also had limited informal exchange with CNSC's advanced reactor review division.
No formal applications have been submitted in either jurisdiction.
Our projected timeline depends on funding, manufacturing partners, test site and community support, design iterations, and regulatory process changes.
We expect approximately US$100 million of capital will be required through 2028 for development, demonstration, and licensing activities, with potentially higher spending for 2029 through 2031 that is not yet clear.

Management's Discussion and Analysis
Going Concern
We have recurring losses and negative cash flows from operations. Conditions and events raise substantial doubt about the Company's ability to continue as a going concern within one year.
Management's plans do not alleviate the substantial doubt because they depend on financing outside the Company's control and no binding financing arrangements are in place.

Financial Statements
Consolidated Balance Sheets
Cash and cash equivalents 1,200 800
Total assets 2,400 1,100
Total liabilities 6,800 4,500
Consolidated Statements of Operations
Revenue 0 0
Net loss 12,500 8,100
Notes to Consolidated Financial Statements
Note 3 Going Concern
Conditions and events raise substantial doubt about the Company's ability to continue as a going concern within one year.

Taxation
A U.S. Holder generally will recognize capital gain or loss equal to the difference between the amount realized upon the disposition of Common Shares and such U.S. Holder's tax basis.
Such capital gain or loss generally will be treated as long-term capital gain or loss if the U.S. Holder's holding period exceeds one year.
"""


def _record(*, form: str = "F-1") -> IpoPipelineRecord:
    return IpoPipelineRecord(
        cik="1234567",
        company_name="Silentium Ltd.",
        proposed_ticker=None,
        form=form,
        filed_date="2026-06-09",
        ipo_status="Filed",
        sic="3571",
        sector="Technology",
        industry="Industrial technology",
        exchange=None,
        filing_url=f"{SEC_ARCHIVES_BASE_URL}/1234567/000123456726000001/f1.htm",
        accession_number="0001234567-26-000001",
        is_foreign_issuer=True,
    )


class FakeSecClient:
    def __init__(self, text: str) -> None:
        self.text = text
        self.cache_dir = "."

    def filing_index_items_for_accession(self, _cik, _accession_number):
        return [{"name": "f1.htm", "type": "F-1", "description": "Registration Statement Prospectus", "size": "100000"}]

    def document_text_url(self, _url: str, *, cache_name=None, ttl=None):
        return self.text


class FakeOpenAiResponses:
    def __init__(self, *, answer: str = "## Offering terms\nThe filing says Silentium is offering ordinary shares.") -> None:
        self.answer = answer
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.answer, id=f"resp_{len(self.calls)}")


class FakeOpenAiClient:
    def __init__(self, *, answer: str = "## Analyst answer\nNot disclosed fields are labeled.") -> None:
        self.responses = FakeOpenAiResponses(answer=answer)


def test_filing_chat_context_fetches_selected_filing_and_builds_bundle() -> None:
    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))

    assert context.company_name == "Silentium Ltd."
    assert context.source_form == "F-1"
    assert context.source_document.name == "f1.htm"
    assert context.bundle.metadata["source_document"] == "f1.htm"
    assert context.bundle.metadata["source_url"].endswith("/f1.htm")
    assert context.bundle.deterministic_extracts["parsed_fields"]["price_range_low"] == 6.0
    assert "active noise control systems" in context.bundle.full_text


def test_filing_chat_uses_responses_api_with_store_false_and_local_history() -> None:
    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))
    fake_openai = FakeOpenAiClient(answer="## Offering terms\nThe filing discloses 2,000,000 ordinary shares.")
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(openai_client=fake_openai, model="gpt-test"),
    )

    response = session.ask("Extract the offering terms and do not invent missing fields.")

    assert response.answer.startswith("## Offering terms")
    assert response.response_id == "resp_1"
    assert len(session.messages) == 2
    call = fake_openai.responses.calls[0]
    assert call["model"] == "gpt-test"
    assert call["store"] is False
    assert "text" not in call
    assert call["input"][0]["role"] == "system"
    assert FILING_CHAT_SYSTEM_PROMPT in call["input"][0]["content"]
    request_payload = json.loads(call["input"][-1]["content"])
    assert request_payload["question"] == "Extract the offering terms and do not invent missing fields."
    assert request_payload["filing_context"]["source_mode"] == "full_prospectus_text"
    assert "2,000,000 ordinary shares" in request_payload["filing_context"]["full_prospectus_text"]
    assert "OPENAI_API_KEY" not in json.dumps(call)


def test_filing_chat_missing_api_key_raises_user_friendly_error() -> None:
    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(api_key="", model="gpt-test"),
    )

    with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
        try:
            session.ask("Summarize risks.")
        except OpenAiIpoFilingChatError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected OpenAiIpoFilingChatError")

    assert "OPENAI_API_KEY is not configured" in message
    assert "sk-" not in message


def test_filing_chat_redacts_api_key_from_openai_errors() -> None:
    class FailingResponses:
        def create(self, **_kwargs):
            raise RuntimeError("request failed for sk-test-secret123456789")

    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))
    failing_openai = SimpleNamespace(responses=FailingResponses())
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(openai_client=failing_openai, api_key="sk-test-secret123456789"),
    )

    try:
        session.ask("Summarize risks.")
    except OpenAiIpoFilingChatError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected OpenAiIpoFilingChatError")

    assert "sk-test-secret" not in message
    assert "[REDACTED]" in message


def test_large_filing_chat_context_uses_relevant_retrieved_chunks() -> None:
    filler = "Generic prospectus boilerplate about the company and securities.\n" * ((CHAT_FULL_TEXT_CHAR_LIMIT // 62) + 100)
    large_text = (
        "Prospectus Summary\nSilentium sells acoustic control systems.\n"
        + filler
        + "\nRisk Factors\nOur liquidity is limited and recurring losses raise substantial doubt about our ability to continue as a going concern.\n"
        + "Customer concentration could materially affect revenue.\n"
        + "\nDilution\nImmediate dilution to new investors will be material because the IPO price exceeds net tangible book value.\n"
    )
    bundle = build_ipo_filing_source_bundle(_record(), large_text, source_url=_record().filing_url)

    payload = filing_context_payload_for_prompt(bundle, "Summarize risk factors and dilution.")
    joined = "\n".join(section["text"] for section in payload["sections"])

    assert payload["source_mode"] == "retrieved_filing_chunks"
    assert "full_prospectus_text" not in payload
    assert len(joined) < len(large_text)
    assert "substantial doubt" in joined
    assert "Immediate dilution" in joined


def test_metaoptics_overview_retrieval_forces_core_filing_sections() -> None:
    text = _metaoptics_f1a_text()
    filler = "Generic prospectus boilerplate that should not outrank named sections.\n" * ((CHAT_FULL_TEXT_CHAR_LIMIT // 70) + 100)
    bundle = build_ipo_filing_source_bundle(_metaoptics_record(), text + "\n" + filler, source_url=_metaoptics_record().filing_url)

    payload = filing_context_payload_for_prompt(bundle, "Generate an overview of this F-1 document.")
    names = [section["name"] for section in payload["sections"]]
    joined = "\n".join(section["text"] for section in payload["sections"])

    assert payload["source_mode"] == "retrieved_filing_chunks"
    assert {
        "cover_page",
        "prospectus_summary",
        "offering_terms",
        "use_of_proceeds",
        "capitalization",
        "dilution",
        "mda_results",
        "financial_statements",
        "risk_factors",
        "customer_supplier_concentration",
        "license_obligations",
        "icfr_material_weaknesses",
        "principal_shareholders",
        "related_party_transactions",
        "shares_eligible_future_sale",
        "underwriting",
        "ads_sgx_conversion",
    } <= set(names)
    sections_by_name = {section["name"]: section for section in payload["sections"]}
    for name in (
        "capitalization",
        "dilution",
        "risk_factors",
        "principal_shareholders",
        "related_party_transactions",
        "use_of_proceeds",
    ):
        assert "TABLE OF CONTENTS" not in sections_by_name[name]["text"]
        assert "CAPITALIZATION 37" not in sections_by_name[name]["text"]
        assert "DILUTION 38" not in sections_by_name[name]["text"]
        assert "PRINCIPAL SHAREHOLDERS 119" not in sections_by_name[name]["text"]
    assert "3,000,000 American Depositary Shares Representing 36,000,000 Ordinary Shares" in joined
    assert "US$14.3 million" in joined
    assert "Cash and cash equivalents 8,789,537 6,845,071 27,141,117 21,136,822" in joined
    assert "US$5.16 per" in joined
    assert "US$0.84 per" in joined
    assert "Revenue 787,388" in joined
    assert "Loss after income tax and total comprehensive loss (5,445,573)" in joined
    assert "Haur-Jye Technology, Taiwan" in joined
    assert "74.6% of" in joined
    assert "MMI Systems Pte Ltd, Singapore" in joined
    assert "81.0% of" in joined
    assert "Accelerate Technologies Pte Ltd" in joined
    assert "S$3.0 million" in joined
    assert "S$5.0 million" in joined
    assert "segregation of duties" in joined
    assert "purchases and payables" in " ".join(joined.split())
    assert "RELATED PARTY TRANSACTIONS" in joined
    assert "September 8, 2026" in " ".join(joined.split())
    assert "convert ordinary shares into ADSs" in joined
    assert "Roth Capital Partners, LLC and The Benchmark Company, LLC" in joined
    assert all("offsets=" in entry and "toc_like=False" in entry for entry in payload["section_debug"] if "opening_excerpt" not in entry)


def test_metaoptics_chat_payload_marks_parser_values_as_hints_and_adds_verified_facts() -> None:
    context = build_ipo_filing_chat_context(_metaoptics_record(), client=FakeSecClient(_metaoptics_f1a_text()))
    fake_openai = FakeOpenAiClient(answer="## Overview\nGrounded answer.")
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(openai_client=fake_openai, model="gpt-test"),
    )

    session.ask("Generate an overview of this F-1 document.")

    request_payload = json.loads(fake_openai.responses.calls[0]["input"][-1]["content"])
    facts = request_payload["verified_filing_facts"]
    offering = facts["offering_terms"]
    financials = facts["financial_metrics"]
    dilution = facts["dilution"]
    concentration = facts["customer_supplier_concentration"]
    licenses = facts["license_obligations"]
    icfr = facts["icfr_material_weaknesses"]
    related = facts["shareholders_and_related_parties"]
    lockup = facts["lockup_moratorium"]
    conversion = facts["ads_sgx_conversion"]
    risk_checks = facts["risk_checks"]

    assert "deterministic_extracts" not in request_payload
    assert request_payload["deterministic_extracts_hints"]["use_policy"].startswith("Hints only")
    assert request_payload["overview_answer_requirements"]["required_topics"] == [
        "cover-page exchange/ticker, price range/midpoint, and named underwriters or bookrunners",
        "dual public-offering plus resale-prospectus structures, selling-shareholder supply, resale proceeds, and resale timing/conditions",
        "net proceeds and use-of-proceeds allocation",
        "material recent developments or acquisitions, including price, assets, fees, and closing/diligence risk",
        "regulatory status, pre-application engagement, formal application status, timeline dependencies, and capital needs",
        "dilution and pro forma/as-adjusted net tangible book value",
        "actual versus as-adjusted capitalization, including disclosed currency translations",
        "substantial-doubt going-concern language when present",
        "customer and supplier concentration",
        "license or commercialization obligations",
        "specific ICFR material weakness details",
        "principal shareholders and related-party transactions",
        "U.S. lock-up and SGX-ST moratorium or future-sale restrictions",
        "ADS/SGX ordinary-share conversion and price-reconciliation considerations",
    ]
    assert offering["securities_offered"]["value"] == "3,000,000 ADSs representing 36,000,000 ordinary shares"
    assert offering["ads_ratio"]["value"] == "1 ADS = 12 ordinary shares"
    assert offering["expected_price_range"]["value"] == "US$5.00-US$7.00 per ADS"
    assert offering["listing"]["value"] == "Nasdaq Capital Market under symbol MOT"
    assert offering["net_proceeds"]["value"] == "Approximately US$14.3 million, or US$16.8 million if the over-allotment option is exercised in full"
    assert financials["fy2025_revenue"]["value"] == "S$787,388"
    assert financials["fy2025_net_loss"]["value"] == "S$5,445,573"
    assert financials["cash_and_cash_equivalents"]["value"] == "Actual S$8,789,537 / US$6,845,071; as adjusted S$27,141,117 / US$21,136,822"
    assert financials["total_debt"]["value"] == "Actual S$2,106,147 / US$1,640,215; as adjusted S$2,106,147 / US$1,640,215"
    assert dilution["immediate_dilution_per_ads"]["value"] == "US$5.16 per ADS"
    assert dilution["pro_forma_as_adjusted_ntbv_per_ads"]["value"] == "US$0.84 per ADS"
    assert concentration["largest_customer"]["value"] == "Haur-Jye Technology, Taiwan accounted for 74.6% of FY2025 revenue"
    assert concentration["major_supplier"]["value"] == "MMI Systems Pte Ltd, Singapore accounted for 81.0% of FY2025 purchases"
    assert "principal shareholders" in concentration["supplier_related_party_link"]["value"]
    assert licenses["licensed_ip_counterparty"]["value"] == "Key IP is licensed from Accelerate Technologies / A*STAR"
    assert licenses["august_2023_gross_revenue_threshold"]["value"] == "S$3.0 million gross revenue threshold within five years from August 1, 2023"
    assert licenses["december_2023_gross_revenue_threshold"]["value"] == "S$5.0 million gross revenue threshold within five years from December 25, 2023"
    assert "waivers or amendments" in licenses["commercialization_obligation_status"]["value"]
    assert "segregation of duties" in icfr["specific_weaknesses"]["value"]
    assert "purchases and payables controls" in icfr["specific_weaknesses"]["value"]
    assert related["directors_and_officers_ownership"]["value"] == "Directors and executive officers as a group hold 35.9% before the offering"
    assert "Angelling Capital Holdings Limited" in related["angelling_principal_shareholder"]["value"]
    assert lockup["us_offering_lockup"]["value"].startswith("180-day lock-up")
    assert lockup["sgx_moratorium"]["value"] == "SGX-ST Catalist moratorium restrictions remain in place until September 8, 2026"
    assert conversion["sgx_listing"]["value"] == "Ordinary shares trade on SGX-ST Catalist under stock code 9MT"
    assert conversion["ads_ratio"]["value"] == "1 ADS = 12 ordinary shares"
    assert "reconcile SGX ordinary-share trading" in conversion["ads_sgx_price_reconciliation"]["value"]
    assert facts["company_revenue_guardrail"]["warning"] == "US$2.3 million is an industry-market figure, not company revenue."
    assert risk_checks["going_concern_risk_detected"]["value"] is False
    assert risk_checks["vie_structure_detected"]["value"] is False


def test_nuclea_overview_verified_facts_cover_dual_prospectus_failure_mode() -> None:
    filler = "Generic F-1/A boilerplate that should not crowd out named Nuclea sections.\n" * ((CHAT_FULL_TEXT_CHAR_LIMIT // 75) + 80)
    text = _nuclea_f1a_excerpt() + "\n" + filler
    record = _nuclea_record()
    bundle = build_ipo_filing_source_bundle(record, text, source_url=record.filing_url)

    payload = filing_context_payload_for_prompt(bundle, "Create an overview report of the Nuclea Energy Inc. F-1-A filing.")
    facts = verified_filing_facts_for_prompt(bundle, payload, "Create an overview report of the Nuclea Energy Inc. F-1-A filing.")
    section_names = {section["name"] for section in payload["sections"]}

    assert payload["source_mode"] == "retrieved_filing_chunks"
    assert {"cover_page", "explanatory_note", "recent_developments", "regulatory_status", "financial_statements"} <= section_names

    offering = facts["offering_terms"]
    assert offering["securities_offered"]["value"] == "5,555,556 Common Shares offered by the company"
    assert offering["listing"]["value"] == "NYSE under symbol NCLA"
    assert offering["listing_condition"]["value"] == "Offering closing is conditioned on exchange listing approval"
    assert offering["expected_price_range"]["value"] == "US$8.00-US$10.00 per Common Share"
    assert offering["assumed_midpoint_price"]["value"] == "US$9.00 per Common Share"
    assert offering["underwriters"]["value"] == "Joseph Gunnar & Co., LLC is sole book-runner / representative"
    assert offering["over_allotment_option"]["value"] == "15% / 833,333 Common Shares over-allotment option"

    dual = facts["dual_prospectus_structure"]
    assert dual["structure_detected"]["value"].startswith("Registration statement contains separate")
    assert dual["public_offering"]["value"] == "Public Offering Prospectus covers company sale of 5,555,556 Common Shares"
    assert dual["resale_offering"]["value"] == "Resale Prospectus covers resale from time to time by Selling Shareholders of 2,817,294 Common Shares"
    assert dual["resale_proceeds"]["value"] == "Company will not receive proceeds from shares sold by Selling Shareholders"
    assert dual["resale_trading_condition"]["value"] == "No resale sales occur until IPO shares begin trading on the exchange"
    assert dual["resale_ipo_condition"]["value"] == "Resale offering is conditioned on consummation of the initial public offering"
    assert "replace Underwriting" in dual["alternate_page_changes"]["value"]

    risk_checks = facts["risk_checks"]
    assert risk_checks["going_concern_risk_detected"]["value"] is True
    assert "substantial doubt" in risk_checks["going_concern_risk_detected"]["source_snippet"]
    assert "do not alleviate" in risk_checks["going_concern_risk_detected"]["source_snippet"]

    recent = facts["recent_developments"]
    assert recent["material_acquisition_detected"]["value"] == "Moltex Asset Acquisition is disclosed as a material recent development"
    assert recent["purchase_price"]["value"] == "\u00a36,183,793 / CAD$11.5 million / approximately US$8.5 million"
    assert "patents/applications" in recent["target_assets"]["value"]
    assert recent["distressed_status"]["value"] == "Moltex Energy Limited is in administration and the assets are distressed assets"
    assert recent["extension_and_exclusivity_fees"]["value"] == (
        "non-refundable exclusivity fee \u00a3268,861; first extension fee \u00a3110,000; "
        "second extension fee \u00a3400,000; possible further extension fee \u00a3200,000"
    )
    assert recent["closing_risk"]["value"] == "Terms remain subject to due diligence and negotiation of a definitive sale agreement"

    regulatory = facts["regulatory_status"]
    assert regulatory["nrc_pre_application"]["value"] == "Preliminary NRC pre-application engagement through NuMark Associates is disclosed"
    assert regulatory["cnsc_informal_exchange"]["value"] == "Limited informal exchange with CNSC's advanced reactor review division is disclosed"
    assert regulatory["formal_applications"]["value"] == "No formal NRC/CNSC applications have been submitted"
    assert regulatory["development_capital_need"]["value"] == "About US$100 million required through 2028 for development, demonstration, and licensing activities"
    assert regulatory["later_spending_uncertainty"]["value"] == "Potentially higher 2029-2031 spending remains unclear"


def test_nuclea_taxation_text_is_not_labeled_financial_statements() -> None:
    filler = "Generic securities-law filler.\n" * ((CHAT_FULL_TEXT_CHAR_LIMIT // 30) + 80)
    text = _nuclea_f1a_excerpt() + "\n" + filler
    record = _nuclea_record()
    bundle = build_ipo_filing_source_bundle(record, text, source_url=record.filing_url)

    payload = filing_context_payload_for_prompt(bundle, "Create an overview report of the Nuclea Energy Inc. F-1-A filing.")
    financial_sections = [section for section in payload["sections"] if section["name"] == "financial_statements"]

    assert financial_sections
    assert any("Consolidated Balance Sheets" in section["text"] for section in financial_sections)
    assert all("U.S. Holder" not in section["text"] for section in financial_sections)
    assert all("capital gain or loss" not in section["text"] for section in financial_sections)
    assert all("amount realized upon the disposition" not in entry for entry in payload["section_debug"] if entry.startswith("financial_statements"))


def test_large_overview_chat_payload_is_bounded_fast_and_uses_timeout() -> None:
    text = _metaoptics_f1a_text()
    filler = "Generic F-1/A overview boilerplate that should stay out of the final payload.\n" * 2_000
    context = build_ipo_filing_chat_context(_metaoptics_record(), client=FakeSecClient(text + "\n" + filler))
    fake_openai = FakeOpenAiClient(answer="## Overview\nBounded grounded answer.")
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(openai_client=fake_openai, model="gpt-test", timeout_seconds=42),
    )
    progress_events: list[str] = []

    started = time.perf_counter()
    response = session.ask("Generate an overview of this filing.", progress_callback=progress_events.append)
    elapsed = time.perf_counter() - started

    call = fake_openai.responses.calls[0]
    payload_text = call["input"][-1]["content"]
    payload = json.loads(payload_text)
    sections = payload["filing_context"]["sections"]
    names = {section["name"] for section in sections}

    assert elapsed < 8.0
    assert len(payload_text) <= CHAT_REQUEST_CHAR_LIMIT
    assert payload["filing_context"]["retrieved_char_count"] <= CHAT_RETRIEVED_CHAR_LIMIT
    assert call["store"] is False
    assert call["timeout"] == 42
    assert "Retrieving filing sections..." in progress_events
    assert "Verifying filing facts..." in progress_events
    assert any(event.startswith("Calling OpenAI") for event in progress_events)
    assert {"cover_page", "offering_terms", "use_of_proceeds", "capitalization", "dilution", "risk_factors", "underwriting", "ads_sgx_conversion"} <= names
    assert any("payload_chars=" in entry for entry in response.source_debug)


def test_filing_chat_timeout_error_is_friendly_and_passes_finite_timeout() -> None:
    class TimeoutResponses:
        def __init__(self) -> None:
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            raise TimeoutError("request timed out for sk-test-secret123456789")

    failing_openai = SimpleNamespace(responses=TimeoutResponses())
    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(
            openai_client=failing_openai,
            api_key="sk-test-secret123456789",
            model="gpt-test",
            timeout_seconds=12,
        ),
    )

    try:
        session.ask("Summarize risks.")
    except OpenAiIpoFilingChatError as exc:
        message = str(exc)
    else:
        raise AssertionError("Expected OpenAiIpoFilingChatError")

    assert failing_openai.responses.calls[0]["timeout"] == 12
    assert "timed out after 12 seconds" in message
    assert "sk-test-secret" not in message


def test_filing_chat_window_reenables_controls_on_prompt_error() -> None:
    window = object.__new__(IpoFilingChatWindow)
    window._closed = False
    window._request_generation = 1
    window._request_running = True
    window._cancel_requested = False
    window.status_var = FakeStatus()
    enabled_states = []
    system_lines = []
    window._set_controls_enabled = lambda enabled: enabled_states.append(enabled)
    window._append_system_line = lambda content: system_lines.append(content)

    with patch("app.ui.ipo_filing_chat_window.messagebox.showerror") as showerror:
        IpoFilingChatWindow._finish_prompt_error(window, OpenAiIpoFilingChatError("timed out"), 1)

    assert enabled_states == [True]
    assert window._request_running is False
    assert window.status_var.value == "OpenAI filing chat failed."
    assert system_lines == ["OpenAI request failed: timed out"]
    showerror.assert_called_once_with("AI Filing Chat failed", "timed out")


def test_filing_chat_transcript_saves_markdown(tmp_path) -> None:
    context = build_ipo_filing_chat_context(_record(), client=FakeSecClient(SAMPLE_FILING_TEXT * 20))
    fake_openai = FakeOpenAiClient(answer="Not disclosed fields should stay labeled.")
    session = IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(openai_client=fake_openai, model="gpt-test"),
    )
    session.ask("What is the market cap?")

    markdown = render_ipo_filing_chat_transcript_markdown(session)
    saved = save_ipo_filing_chat_transcript(session, tmp_path / "chat.md")

    assert "# Silentium Ltd. F-1 AI Filing Chat" in markdown
    assert "## 1. User" in markdown
    assert "## 2. Assistant" in markdown
    assert "### Source Debug" in markdown
    assert "- Source mode: full_prospectus_text" in markdown
    assert saved.read_text(encoding="utf-8") == markdown


class FakeTree:
    def __init__(self, selection):
        self._selection = selection

    def selection(self):
        return self._selection


class FakeStatus:
    def __init__(self) -> None:
        self.value = ""

    def set(self, value: str) -> None:
        self.value = value


def test_open_ai_filing_chat_handler_rejects_missing_selection() -> None:
    app = SimpleNamespace(ipo_pipeline_table=FakeTree(()), ipo_pipeline_row_map={}, ipo_pipeline_status_var=FakeStatus())

    with patch.object(ipo_pipeline_extension.messagebox, "showinfo") as showinfo:
        ipo_pipeline_extension._open_selected_ai_filing_chat(app)  # type: ignore[arg-type]

    showinfo.assert_called_once_with("Open AI Filing Chat", "Select an IPO pipeline row first.")


def test_open_ai_filing_chat_handler_rejects_non_reportable_form() -> None:
    app = SimpleNamespace(
        ipo_pipeline_table=FakeTree(("row1",)),
        ipo_pipeline_row_map={"row1": _record(form="8-K")},
        ipo_pipeline_status_var=FakeStatus(),
    )

    with patch.object(ipo_pipeline_extension.messagebox, "showinfo") as showinfo:
        ipo_pipeline_extension._open_selected_ai_filing_chat(app)  # type: ignore[arg-type]

    showinfo.assert_called_once_with("Open AI Filing Chat", "AI filing chat is available for S-1/F-1, 424B4, and EFFECT rows.")


def test_open_ai_filing_chat_handler_opens_window_for_valid_row() -> None:
    app = SimpleNamespace(
        ipo_pipeline_table=FakeTree(("row1",)),
        ipo_pipeline_row_map={"row1": _record()},
        ipo_pipeline_status_var=FakeStatus(),
    )

    with patch.object(ipo_pipeline_extension, "open_ipo_filing_chat_window") as open_window:
        ipo_pipeline_extension._open_selected_ai_filing_chat(app)  # type: ignore[arg-type]

    open_window.assert_called_once()
    assert "Opened AI filing chat for Silentium Ltd." == app.ipo_pipeline_status_var.value
