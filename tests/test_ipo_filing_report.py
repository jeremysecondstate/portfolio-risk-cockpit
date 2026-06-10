from __future__ import annotations

from datetime import timedelta

from app.analytics.ipo_filing_report import (
    build_ipo_filing_report,
    generate_ipo_filing_report,
    render_ipo_filing_report_markdown,
    save_ipo_filing_report,
)
from app.analytics.ipo_pipeline import (
    IpoPipelineRecord,
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
