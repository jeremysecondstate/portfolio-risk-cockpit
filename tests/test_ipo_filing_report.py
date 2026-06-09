from __future__ import annotations

from app.analytics.ipo_filing_report import (
    build_ipo_filing_report,
    render_ipo_filing_report_markdown,
    save_ipo_filing_report,
)
from app.analytics.ipo_pipeline import IpoPipelineRecord


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
