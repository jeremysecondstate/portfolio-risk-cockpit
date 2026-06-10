from __future__ import annotations

import json
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from app.analytics.ipo_filing_report import (
    build_ipo_filing_report,
    generate_ipo_filing_report,
    render_ipo_filing_report_markdown,
    save_ipo_filing_report,
)
from app.analytics.openai_ipo_report import (
    IPO_REPORT_JSON_SCHEMA,
    OpenAiIpoReportError,
    build_ipo_filing_source_bundle,
    save_openai_ipo_filing_report,
)
from app.analytics.ipo_pipeline import (
    IpoPipelineRecord,
    analyze_text_risk_flags,
    fetch_ipo_document_text_with_source,
    fetch_ipo_pipeline_snapshot,
    parse_ipo_filing_text,
    related_prospectus_filing_for_report,
    select_ipo_document_candidate,
)
from app.data.sec_edgar import SecCurrentFiling


SAMPLE_F1_TEXT = """
Prospectus Summary
Silentium Ltd. is an acoustic intelligence company that develops active noise control systems for industrial equipment and commercial facilities.
Our platform combines sensors, embedded software, and proprietary acoustic models to reduce machine noise without redesigning the customer's equipment.
The market for active noise control is growing as factories automate and operators face stricter workplace noise rules.
We have 24 enterprise customers, deployments in 12 countries, and multi-year contracts with two global equipment manufacturers.

The Offering
We are offering 2,000,000 ordinary shares.
The estimated initial public offering price is between $6.00 and $8.00 per ordinary share.
We have applied to list our ordinary shares on the Nasdaq Capital Market under the symbol SLNT.
The underwriters are Example Securities LLC and Test Capital LLC.

Selected Consolidated Statements of Operations
($ in thousands)
Revenue 4,200 1,850
Cost of revenue 2,520 1,300
Gross profit 1,680 550
Total operating expenses 8,900 4,700
Net loss 7,100 3,900

Going Concern
Our recurring losses and negative cash flows raise substantial doubt about our ability to continue as a going concern.

Capitalization
Cash and cash equivalents were $1.2 million. Total debt was $3.5 million.
Use of Proceeds We intend to use the net proceeds for sales expansion, product development, working capital, and repayment of indebtedness.

Dilution
At an assumed initial public offering price of $7.00 per share, our pro forma as adjusted net tangible book value per share would be $1.25 and immediate dilution to new investors will be $5.75 per share.

Other Terms
In May 2026, we completed a 1-for-5 reverse split of our ordinary shares.
We are a foreign private issuer and have related party transactions with our founder.
"""


STARFIGHTERS_STYLE_BAD_TEXT = """
Prospectus Summary
Table of Contents
Prospectus Summary 1 Risk Factors 7 Use of Proceeds 26 Determination of Offering Price 26 Management's Discussion and Analysis 33.
Indicate by check mark whether the registrant is a large accelerated filer, an accelerated filer, a non-accelerated filer,
smaller reporting company, or emerging growth company.
We may amend or supplement this prospectus from time to time. You should read the entire prospectus carefully.

Risk Factors
We may issue up to 5.2 million shares in future transactions.
Historical page references run between $1,963 and $1,969 in archived materials and should not be interpreted as an offering price.
Payloads and launch operations may be delayed or damaged before launch.
We expect to derive a substantial amount of our revenues from only a core group of major customers.
If we issue additional Common Stock, stockholders may experience dilution in their ownership of the Company.

Use of Proceeds
USE OF PROCEEDS 26 DETERMINATION OF OFFERING PRICE 26 DIVIDEND POLICY 26 SELLING STOCKHOLDERS 26 PLAN OF DISTRIBUTION 29

Business
Starfighters Space provides commercial astronaut training, high-altitude flight testing, and aerospace research services
for government, academic, and commercial customers.
The company operates specialized aircraft and training facilities to support mission simulation and payload validation.

Selected Consolidated Statements of Operations
($ in thousands)
Operating expenses 4,100 1,900
Net loss 88.6 5.1

Balance Sheets
($ in thousands)
Cash and cash equivalents 31 20
Total debt 1,200 900
"""


COMPLETE_SUBMISSION_WITH_EXHIBIT_FIRST = """
<SEC-DOCUMENT>0000000000-26-000001.txt
<DOCUMENT>
<TYPE>EX-99.1
<FILENAME>ex99.htm
<DESCRIPTION>Launch risk exhibit
<TEXT>
Risk Factors
This exhibit says our launch costs were between $1,963 and $1,969 and we may issue 5.2 million shares.
</TEXT>
</DOCUMENT>
<DOCUMENT>
<TYPE>S-1
<FILENAME>forms1.htm
<DESCRIPTION>Registration Statement Prospectus
<TEXT>
Prospectus Summary
Orbital Training Corp. provides astronaut training and flight-test services to commercial customers.
The Offering
We are offering 2,000,000 shares of common stock.
The estimated initial public offering price is between $6.00 and $8.00 per share.
Use of Proceeds
We intend to use the net proceeds for aircraft upgrades, working capital, and training facility expansion.
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>
"""


def _record() -> IpoPipelineRecord:
    return IpoPipelineRecord(
        cik="1234567",
        company_name="Silentium Ltd.",
        proposed_ticker=None,
        form="F-1",
        filed_date="2026-06-09",
        ipo_status="Filed",
        sic="3571",
        sector="Technology",
        industry="Industrial technology",
        exchange=None,
        filing_url="https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/f1.htm",
        accession_number="0001234567-26-000001",
        is_foreign_issuer=True,
    )


def _filing(
    *,
    form: str = "S-1",
    accession: str = "0001234567-26-000001",
    primary_document: str = "s1.htm",
    filing_date: str = "2026-06-01",
) -> SecCurrentFiling:
    accession_no_dashes = accession.replace("-", "")
    return SecCurrentFiling(
        company_name="Example IPO Inc.",
        cik="1234567",
        form=form,
        filing_date=filing_date,
        accession_number=accession,
        filing_url=f"https://www.sec.gov/Archives/edgar/data/1234567/{accession_no_dashes}/{primary_document}",
        primary_document=primary_document,
    )


class FakeSecClient:
    def __init__(self, tmp_path, *, filings=None, index_items=None, documents=None, submissions=None) -> None:
        self.cache_dir = tmp_path
        self.filings = list(filings or [])
        self.index_items = dict(index_items or {})
        self.documents = dict(documents or {})
        self.submissions = dict(submissions or {})
        self.document_fetch_count = 0

    def recent_current_filings(self, form: str, *, limit: int = 100, start: int = 0):
        return [filing for filing in self.filings if filing.form == form]

    def get_submissions_by_cik(self, cik):
        return self.submissions.get(str(cik).zfill(10), self.submissions.get("default", {"cik": str(cik), "filings": {"recent": {}}}))

    def filing_index_items_for_accession(self, cik, accession_number):
        return list(self.index_items.get(accession_number, []))

    def document_text_url(self, url: str, *, cache_name=None, ttl=None):
        self.document_fetch_count += 1
        try:
            return self.documents[url]
        except KeyError as exc:
            raise AssertionError(f"Unexpected document fetch: {url}") from exc


class FakeOpenAiResponses:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload or _fake_ai_report_payload()
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_text=json.dumps(self.payload))


class FakeOpenAiClient:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.responses = FakeOpenAiResponses(payload=payload, error=error)


def _fake_ai_report_payload() -> dict:
    return {
        "headline_bullets": [
            "Silentium filed for an IPO based on ordinary shares.",
            "The price range is $6.00 to $8.00 per share.",
        ],
        "business_summary": {
            "summary": "Silentium develops active noise control systems for industrial equipment and commercial facilities.",
            "source_snippets": [
                "Silentium Ltd. is an acoustic intelligence company that develops active noise control systems."
            ],
        },
        "ipo_terms": {
            "shares_offered": "2,000,000 ordinary shares",
            "price_range": "$6.00 to $8.00 per ordinary share",
            "offering_size": "Not confidently extracted",
            "ticker": "SLNT",
            "exchange": "Nasdaq Capital Market",
            "underwriters": ["Example Securities LLC", "Test Capital LLC"],
            "listing_terms": "Ordinary shares proposed for Nasdaq listing.",
            "source_snippets": [
                "We are offering 2,000,000 ordinary shares.",
                "between $6.00 and $8.00 per ordinary share",
            ],
        },
        "financial_summary": {
            "revenue": "$4.2 million",
            "net_income_loss": "Net loss of $7.1 million",
            "cash": "$1.2 million",
            "debt": "$3.5 million",
            "summary": "Revenue increased while the company remained loss-making.",
            "source_snippets": ["Revenue 4,200 1,850", "Net loss 7,100 3,900"],
        },
        "use_of_proceeds": {
            "summary": "Sales expansion, product development, working capital, and repayment of indebtedness.",
            "source_snippet": "We intend to use the net proceeds for sales expansion, product development, working capital, and repayment of indebtedness.",
        },
        "key_risks": [
            {
                "risk": "Going concern",
                "why_it_matters": "The filing says recurring losses raise substantial doubt about continuing as a going concern.",
                "source_snippet": "raise substantial doubt about our ability to continue as a going concern",
            }
        ],
        "bull_case": ["The company has disclosed enterprise customers and international deployments."],
        "bear_case": ["The company is loss-making and dependent on IPO proceeds."],
        "final_key_question": "Can Silentium turn IPO proceeds into durable growth before financing pressure returns?",
        "confidence": {"level": "high", "explanation": "Core terms and risk snippets were present in the provided filing sections."},
        "not_disclosed_fields": ["Market cap"],
        "not_confidently_extracted_fields": ["Offering size"],
    }


def _submission(*, cik: str = "0001234567") -> dict:
    return {
        "cik": cik,
        "name": "Example IPO Inc.",
        "sic": "7372",
        "sicDescription": "Software",
        "filings": {
            "recent": {
                "form": ["EFFECT", "424B4", "S-1/A", "S-1"],
                "accessionNumber": [
                    "0001234567-26-000004",
                    "0001234567-26-000003",
                    "0001234567-26-000002",
                    "0001234567-26-000001",
                ],
                "filingDate": ["2026-06-07", "2026-06-06", "2026-06-05", "2026-06-01"],
                "reportDate": ["", "", "", ""],
                "primaryDocument": ["effect.htm", "424b4.htm", "s1a.htm", "s1.htm"],
                "acceptanceDateTime": ["2026-06-07T12:00:00", "2026-06-06T12:00:00", "2026-06-05T12:00:00", "2026-06-01T12:00:00"],
            }
        },
    }


def test_ipo_filing_report_parser_extracts_offering_risk_and_financials() -> None:
    report = build_ipo_filing_report(_record(), SAMPLE_F1_TEXT, source_url="https://www.sec.gov/test/f1.htm")
    financials = {row.label: row for row in report.financial_rows}

    assert report.parsed.price_range_low == 6.0
    assert report.parsed.price_range_high == 8.0
    assert report.parsed.shares_offered == 2_000_000
    assert "Example Securities LLC" in report.parsed.underwriters
    assert report.parsed.proposed_ticker == "SLNT"
    assert report.parsed.exchange == "Nasdaq Capital Market"
    assert report.going_concern
    assert financials["Revenue"].latest_text == "$4.2M"
    assert financials["Gross margin"].latest_text == "40.0%"
    assert financials["Net income / loss"].latest_value == -7_100_000
    assert any("$1.25" in line and "$5.75" in line for line in report.dilution)
    assert any("Reverse split" in line for line in report.notable_terms)
    assert any("related" in line.lower() for line in report.notable_terms)


def test_ipo_text_parser_extracts_key_terms_for_pipeline_cache() -> None:
    parsed = parse_ipo_filing_text(SAMPLE_F1_TEXT, form="F-1")

    assert parsed.price_range_low == 6.0
    assert parsed.price_range_high == 8.0
    assert parsed.shares_offered == 2_000_000
    assert "Example Securities LLC" in parsed.underwriters
    assert parsed.revenue == 4_200_000
    assert parsed.net_income == -7_100_000
    assert parsed.gross_margin == 40.0
    assert parsed.cash == 1_200_000
    assert parsed.debt == 3_500_000
    assert parsed.dilution_per_share == 5.75
    assert parsed.going_concern is True


def test_report_rejects_absurd_low_confidence_offering_terms() -> None:
    record = IpoPipelineRecord(
        cik="1947016",
        company_name="Starfighters Space, Inc.",
        proposed_ticker="FJET",
        form="S-1",
        filed_date="2026-06-09",
        ipo_status="Filed",
        sic=None,
        sector=None,
        industry=None,
        exchange="NYSE American",
        filing_url="https://www.sec.gov/test/starfighters.txt",
        accession_number="0001062993-26-003129",
    )
    report = build_ipo_filing_report(record, STARFIGHTERS_STYLE_BAD_TEXT, source_url=record.filing_url)
    markdown = render_ipo_filing_report_markdown(report)
    financials = {row.label: row for row in report.financial_rows}

    assert report.parsed.price_range_low is None
    assert report.parsed.price_range_high is None
    assert report.parsed.shares_offered is None
    assert "Price range: Not confidently extracted." in markdown
    assert "Shares offered: Not confidently extracted." in markdown
    assert "Midpoint gross raise math" not in markdown
    assert "At the midpoint" not in markdown
    assert "Implications of Being an Emerging Growth Company" not in report.business_description
    assert "check mark" not in markdown.lower()
    assert "USE OF PROCEEDS 26" not in markdown
    assert "ADS/ADR structure" not in markdown
    assert financials["Net income / loss"].latest_value == -88_600
    assert any("Cash and cash equivalents parsed from the filing: $31.0K." in line for line in report.balance_sheet)
    assert any("Debt / indebtedness parsed from the filing: $1.2M." in line for line in report.balance_sheet)


def test_complete_submission_parser_uses_prospectus_document_block(tmp_path) -> None:
    filing = _filing(primary_document="forms1.htm")
    complete_url = "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/0001234567-26-000001.txt"
    client = FakeSecClient(
        tmp_path,
        index_items={
            filing.accession_number: [
                {"name": "forms1.htm", "type": "S-1", "description": "Registration Statement", "size": "1000"},
                {"name": "0001234567-26-000001.txt", "type": "", "description": "Complete submission text file", "size": "200000"},
            ]
        },
        documents={
            filing.filing_url: "Short cover page.",
            complete_url: COMPLETE_SUBMISSION_WITH_EXHIBIT_FIRST,
        },
    )

    fetched = fetch_ipo_document_text_with_source(client, filing)
    parsed = parse_ipo_filing_text(fetched.text, form="S-1")

    assert "This exhibit says" not in fetched.text
    assert parsed.price_range_low == 6.0
    assert parsed.price_range_high == 8.0
    assert parsed.shares_offered == 2_000_000


def test_risk_flags_require_word_boundaries_and_evidence() -> None:
    flags = analyze_text_risk_flags(
        "Payloads can be delayed before launch. The address line says Prince Street. "
        "We use ads to market the service and ADR means average daily rate in this paragraph."
    )

    assert "ADS/ADR structure" not in flags
    assert "China/VIE structure" not in flags


def test_document_candidate_ranking_chooses_prospectus_html(tmp_path) -> None:
    filing = _filing(primary_document="0001234567-26-000001-index.htm")
    client = FakeSecClient(
        tmp_path,
        index_items={
            filing.accession_number: [
                {"name": "0001234567-26-000001-index.htm", "type": "", "description": "Index", "size": "800"},
                {"name": "FilingSummary.xml", "type": "XML", "description": "Filing summary", "size": "2000"},
                {"name": "logo.jpg", "type": "GRAPHIC", "description": "Graphic", "size": "1000"},
                {"name": "ex99-1.htm", "type": "EX-99.1", "description": "Exhibit 99.1", "size": "20000"},
                {"name": "s1.htm", "type": "S-1", "description": "Registration Statement Prospectus", "size": "90000"},
                {"name": "0001234567-26-000001.txt", "type": "", "description": "Complete submission text file", "size": "120000"},
            ]
        },
    )

    candidate = select_ipo_document_candidate(client, filing)

    assert candidate is not None
    assert candidate.name == "s1.htm"
    assert candidate.document_type == "S-1"
    assert "matches selected SEC form" in candidate.selection_reason


def test_effect_report_resolves_to_related_final_prospectus(tmp_path) -> None:
    effect = _filing(
        form="EFFECT",
        accession="0001234567-26-000004",
        primary_document="effect.htm",
        filing_date="2026-06-07",
    )
    final = _filing(
        form="424B4",
        accession="0001234567-26-000003",
        primary_document="424b4.htm",
        filing_date="2026-06-06",
    )
    final_url = final.filing_url
    client = FakeSecClient(
        tmp_path,
        filings=[effect],
        submissions={"0001234567": _submission()},
        index_items={
            "0001234567-26-000003": [
                {"name": "424b4.htm", "type": "424B4", "description": "Final prospectus", "size": "100000"},
            ]
        },
        documents={final_url: SAMPLE_F1_TEXT * 30},
    )

    source = related_prospectus_filing_for_report(client, effect)
    assert source.form == "424B4"
    assert source.accession_number == "0001234567-26-000003"

    record = IpoPipelineRecord(
        cik=effect.cik,
        company_name=effect.company_name,
        proposed_ticker=None,
        form=effect.form,
        filed_date=effect.filing_date,
        ipo_status="Effective",
        sic=None,
        sector=None,
        industry=None,
        exchange=None,
        filing_url=effect.filing_url,
        accession_number=effect.accession_number,
    )
    generated = generate_ipo_filing_report(record, client=client, output_root=tmp_path / "reports", force_refresh=True)

    assert generated.report is not None
    assert generated.report.selected_form == "EFFECT"
    assert generated.report.source_form == "424B4"
    assert "Selected row: EFFECT accession 0001234567-26-000004" in generated.markdown
    assert "Parsed source: 424B4 accession 0001234567-26-000003 document 424b4.htm" in generated.markdown


def test_complete_submission_fallback_when_primary_is_too_thin(tmp_path) -> None:
    filing = _filing(primary_document="s1.htm")
    complete_url = "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/0001234567-26-000001.txt"
    client = FakeSecClient(
        tmp_path,
        index_items={
            filing.accession_number: [
                {"name": "s1.htm", "type": "S-1", "description": "Registration Statement", "size": "1000"},
                {"name": "0001234567-26-000001.txt", "type": "", "description": "Complete submission text file", "size": "200000"},
            ]
        },
        documents={
            filing.filing_url: "Short cover page.",
            complete_url: SAMPLE_F1_TEXT * 30,
        },
    )

    fetched = fetch_ipo_document_text_with_source(client, filing)

    assert fetched.candidate.name == "0001234567-26-000001.txt"
    assert "Complete-submission fallback" in fetched.candidate.selection_reason
    assert "Prospectus Summary" in fetched.text


def test_pipeline_reuses_parsed_field_cache_without_refetching_document(tmp_path) -> None:
    filing = _filing(primary_document="s1.htm")
    client = FakeSecClient(
        tmp_path,
        filings=[filing],
        submissions={"0001234567": {"cik": "0001234567", "name": "Example IPO Inc.", "filings": {"recent": {}}}},
        index_items={
            filing.accession_number: [
                {"name": "s1.htm", "type": "S-1", "description": "Registration Statement Prospectus", "size": "90000"},
            ]
        },
        documents={filing.filing_url: SAMPLE_F1_TEXT * 30},
    )

    first = fetch_ipo_pipeline_snapshot(client, force_refresh=True, parse_documents=True, per_form_limit=20)
    assert first.records[0].parse_status == "Parsed"
    assert client.document_fetch_count == 1

    (tmp_path / "ipo_pipeline_records.json").unlink()
    client.document_fetch_count = 0
    second = fetch_ipo_pipeline_snapshot(
        client,
        force_refresh=False,
        parse_documents=True,
        per_form_limit=20,
        cache_max_age=timedelta(microseconds=0),
    )

    assert second.records[0].parse_status == "Cached"
    assert second.records[0].parsed_from_cache is True
    assert client.document_fetch_count == 0


def test_ipo_filing_report_renders_markdown_and_pdf_from_same_content(tmp_path) -> None:
    record = _record()
    report = build_ipo_filing_report(record, SAMPLE_F1_TEXT, source_url="https://www.sec.gov/test/f1.htm")
    markdown = render_ipo_filing_report_markdown(report)

    assert "# Silentium Ltd. F-1 Filing Report" in markdown
    assert "## The headline" in markdown
    assert "## The giant red flag: going concern" in markdown
    assert "## Dilution: new investors are paying way above book value" in markdown
    assert "| Revenue | $4.2M | $1.9M | +127.0% |" in markdown
    assert "**Final key question:**" in markdown

    generated = save_ipo_filing_report(
        record,
        SAMPLE_F1_TEXT,
        source_url="https://www.sec.gov/test/f1.htm",
        output_root=tmp_path,
        force_refresh=True,
    )

    assert generated.paths.markdown_path.exists()
    assert generated.paths.pdf_path.exists()
    assert generated.paths.output_dir.name == "0001234567_0001234567-26-000001"
    assert generated.paths.markdown_path.read_text(encoding="utf-8") == generated.markdown
    pdf_bytes = generated.paths.pdf_path.read_bytes()
    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert b"Silentium Ltd. F-1 Filing Report" in pdf_bytes

    cached = save_ipo_filing_report(
        record,
        "Revenue 1 1",
        source_url="https://www.sec.gov/test/f1.htm",
        output_root=tmp_path,
        force_refresh=False,
    )
    assert cached.cached
    assert cached.paths == generated.paths
    assert cached.markdown == generated.markdown


def test_openai_ipo_report_uses_responses_structured_outputs_with_mocked_client(tmp_path) -> None:
    fake_openai = FakeOpenAiClient()
    generated = save_openai_ipo_filing_report(
        _record(),
        SAMPLE_F1_TEXT,
        source_url="https://www.sec.gov/test/f1.htm",
        output_root=tmp_path,
        force_refresh=True,
        openai_client=fake_openai,
        model="gpt-test",
    )

    call = fake_openai.responses.calls[0]
    assert call["model"] == "gpt-test"
    assert "temperature" not in call
    assert call["store"] is False
    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True
    assert call["text"]["format"]["schema"] == IPO_REPORT_JSON_SCHEMA
    assert "OPENAI_API_KEY" not in json.dumps(call)
    assert "2,000,000 ordinary shares" in call["input"][1]["content"]

    assert generated.report is not None
    assert generated.report.generation_method == "openai"
    assert generated.paths.markdown_path.name == "Silentium Ltd AI Filing Report.md"
    assert generated.paths.markdown_path.exists()
    assert generated.paths.pdf_path.exists()
    assert "# Silentium Ltd. F-1 AI Filing Report" in generated.markdown
    assert "Source snippet:" in generated.markdown
    assert "AI model: gpt-test" in generated.markdown
    assert "Not disclosed: Market cap" in generated.markdown
    assert generated.paths.pdf_path.read_bytes().startswith(b"%PDF-1.4")


def test_openai_source_bundle_uses_cleaned_sections_and_metadata() -> None:
    bundle = build_ipo_filing_source_bundle(
        _record(),
        SAMPLE_F1_TEXT,
        source_url="https://www.sec.gov/test/f1.htm",
    )

    assert bundle.metadata["company_name"] == "Silentium Ltd."
    assert bundle.metadata["source_form"] == "F-1"
    assert bundle.deterministic_extracts["parsed_fields"]["price_range_low"] == 6.0
    section_names = {section.name for section in bundle.sections}
    assert "prospectus_summary" in section_names
    assert "offering" in section_names
    assert "risk_factors" in section_names
    assert all("Table of Contents" not in section.text for section in bundle.sections)


def test_openai_ipo_report_redacts_api_key_from_errors(tmp_path) -> None:
    fake_openai = FakeOpenAiClient(error=RuntimeError("request failed for sk-test-secret123456789"))
    with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-secret123456789"}):
        try:
            save_openai_ipo_filing_report(
                _record(),
                SAMPLE_F1_TEXT,
                source_url="https://www.sec.gov/test/f1.htm",
                output_root=tmp_path,
                force_refresh=True,
                openai_client=fake_openai,
            )
        except OpenAiIpoReportError as exc:
            message = str(exc)
        else:
            raise AssertionError("Expected OpenAiIpoReportError")

    assert "sk-test-secret" not in message
    assert "[REDACTED]" in message
