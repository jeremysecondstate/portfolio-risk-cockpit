from __future__ import annotations

from app.analytics.earnings_filing_summary import (
    build_earnings_filing_summary,
    format_earnings_filing_summary,
    parse_earnings_filing_summary_from_readout,
)


NVDA_10Q_SAMPLE = """
NVIDIA Corporation and Subsidiaries Condensed Consolidated Statements of Income
Revenue $ 81,615 $ 44,062 Cost of revenue 20,458 17,394 Gross profit 61,157 26,668
Operating income 53,536 21,638 Net income $ 58,321 $ 18,775
Net income per share: Basic $ 2.40 $ 0.77 Diluted $ 2.39 $ 0.76
Net cash provided by operating activities $ 50,344 $ 27,414
Cash, cash equivalents, and marketable debt securities $ 50,335 $ 49,670
Revenue by Market Platform Data Center $ 75,246 $ 39,112 21 % 92 % Hyperscale 37,869 17,599 12 % 115 %
AI Clouds, Industrial, & Enterprise 37,377 21,513 31 % 74 % Edge Computing 6,369 4,950 10 % 29 %
Revenue growth in the first quarter was driven by data center products for accelerated computing and AI solutions.
Blackwell continued to account for the majority of our system shipments.
For the first quarter of fiscal year 2027, three direct customers represented 21%, 17%, and 16% of total revenue.
The availability of data centers, energy, and capital to support the buildout of NVIDIA AI infrastructure by our customers and partners is crucial.
Manufacturing, supply, and capacity commitments were $119 billion.
The change in Other income (expense), net was primarily driven by unrealized gains on investments in publicly-held equity securities of $13.4 billion and non-marketable equity securities of $2.6 billion.
We repurchased 108 million shares of our common stock for $20.2 billion.
"""


def test_nvda_10q_summary_extracts_tables_and_risks() -> None:
    summary = build_earnings_filing_summary(
        "NVDA",
        NVDA_10Q_SAMPLE,
        source_label="SEC 10-Q fallback",
        source_date="2026-05-20",
    )
    metrics = {row.label: row for row in summary.metrics}
    platforms = {row.label: row for row in summary.platform_rows}

    assert metrics["Revenue"].latest_text == "$81.6B"
    assert metrics["Net income"].change_text == "+210.6%"
    assert metrics["Gross margin"].latest_text == "74.9%"
    assert platforms["Data Center"].latest_text == "$75.2B"
    assert any("Customer concentration" in risk for risk in summary.risks)
    assert any("investment gains" in point.lower() for point in summary.quality_points)


def test_formatted_summary_round_trips_for_popout_parser() -> None:
    summary = build_earnings_filing_summary("NVDA", NVDA_10Q_SAMPLE)
    rendered = format_earnings_filing_summary(summary)
    parsed = parse_earnings_filing_summary_from_readout("NVDA", rendered)

    assert parsed.has_structured_data
    assert any(row.label == "Revenue" and row.latest_text == "$81.6B" for row in parsed.metrics)
    assert any(row.label == "Data Center" for row in parsed.platform_rows)
    assert "Earnings Filing Readout" in rendered
