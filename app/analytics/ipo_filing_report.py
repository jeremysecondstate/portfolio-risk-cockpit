from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from app.analytics.ipo_pipeline import (
    IpoDocumentCandidate,
    IpoPipelineRecord,
    ParsedIpoFields,
    analyze_text_risk_flags,
    fetch_ipo_document_text_with_source,
    parse_ipo_filing_text,
    related_prospectus_filing_for_report,
    unique_flags,
)
from app.data.sec_edgar import SecCurrentFiling, SecEdgarClient, normalize_cik


REPORTABLE_IPO_FORMS = {"S-1", "S-1/A", "F-1", "F-1/A", "424B4", "EFFECT"}
DEFAULT_IPO_REPORT_DIR = Path("data") / "sec_ipo_reports"


@dataclass(frozen=True)
class IpoReportFinancialRow:
    label: str
    latest_value: float | None
    prior_value: float | None
    latest_text: str
    prior_text: str
    change_text: str
    read: str


@dataclass(frozen=True)
class IpoFilingReport:
    company_name: str
    form: str
    cik: str
    accession_number: str
    filed_date: str
    source_url: str
    selected_form: str
    selected_accession_number: str
    source_form: str
    source_accession_number: str
    source_document: str
    source_document_size: int | None
    source_selection_reason: str
    parsed: ParsedIpoFields
    business_description: str
    why_interesting: tuple[str, ...]
    traction: tuple[str, ...]
    financial_rows: tuple[IpoReportFinancialRow, ...]
    going_concern: bool
    key_risks: tuple[str, ...]
    balance_sheet: tuple[str, ...]
    dilution: tuple[str, ...]
    notable_terms: tuple[str, ...]
    bull_case: tuple[str, ...]
    bear_case: tuple[str, ...]
    final_key_question: str
    raw_excerpt: str = ""
    generation_method: str = "deterministic"
    headline_bullets: tuple[str, ...] = ()
    ipo_terms: tuple[str, ...] = ()
    financial_summary: tuple[str, ...] = ()
    use_of_proceeds_summary: str = ""
    confidence: str = ""
    not_disclosed_fields: tuple[str, ...] = ()
    not_confidently_extracted_fields: tuple[str, ...] = ()
    model_name: str = ""
    source_section_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class IpoReportPaths:
    output_dir: Path
    markdown_path: Path
    pdf_path: Path


@dataclass(frozen=True)
class GeneratedIpoFilingReport:
    report: IpoFilingReport | None
    paths: IpoReportPaths
    cached: bool = False
    markdown: str = ""


def reportable_ipo_form(form: str) -> bool:
    return form.strip().upper() in REPORTABLE_IPO_FORMS


def generate_ipo_filing_report(
    record: IpoPipelineRecord,
    *,
    client: SecEdgarClient | None = None,
    output_root: str | Path = DEFAULT_IPO_REPORT_DIR,
    force_refresh: bool = False,
) -> GeneratedIpoFilingReport:
    """Fetch the selected primary filing document and save Markdown/PDF reports.

    The expensive SEC fetch and rendering work are intentionally isolated from
    Tkinter. UI callers should run this function from a background thread.
    """

    paths = ipo_report_paths(record, output_root=output_root)
    if not force_refresh and paths.markdown_path.exists() and paths.pdf_path.exists():
        cached_markdown = _safe_read_text(paths.markdown_path)
        if "Parsed source:" in cached_markdown:
            return GeneratedIpoFilingReport(
                report=None,
                paths=paths,
                cached=True,
                markdown=cached_markdown,
            )

    if not reportable_ipo_form(record.form):
        raise ValueError(f"IPO filing reports are supported for S-1/F-1, 424B4, and EFFECT rows, not {record.form or 'unknown form'}.")

    client = client or SecEdgarClient()
    selected_filing = sec_current_filing_from_record(record)
    source_filing = related_prospectus_filing_for_report(client, selected_filing)
    fetched = fetch_ipo_document_text_with_source(client, source_filing)
    return save_ipo_filing_report(
        record,
        fetched.text,
        source_url=fetched.candidate.url,
        source_filing=source_filing,
        source_document=fetched.candidate,
        output_root=output_root,
        force_refresh=True,
    )


def save_ipo_filing_report(
    record: IpoPipelineRecord,
    filing_text: str,
    *,
    source_url: str = "",
    source_filing: SecCurrentFiling | None = None,
    source_document: IpoDocumentCandidate | None = None,
    output_root: str | Path = DEFAULT_IPO_REPORT_DIR,
    force_refresh: bool = False,
) -> GeneratedIpoFilingReport:
    paths = ipo_report_paths(record, output_root=output_root)
    if not force_refresh and paths.markdown_path.exists() and paths.pdf_path.exists():
        return GeneratedIpoFilingReport(
            report=None,
            paths=paths,
            cached=True,
            markdown=_safe_read_text(paths.markdown_path),
        )

    report = build_ipo_filing_report(
        record,
        filing_text,
        source_url=source_url,
        source_filing=source_filing,
        source_document=source_document,
    )
    return write_ipo_filing_report(report, paths)


def write_ipo_filing_report(report: IpoFilingReport, paths: IpoReportPaths) -> GeneratedIpoFilingReport:
    markdown = render_ipo_filing_report_markdown(report)
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(paths.markdown_path, markdown)
    _atomic_write_bytes(paths.pdf_path, markdown_to_pdf_bytes(markdown, title=f"{report.company_name} Filing Report"))
    return GeneratedIpoFilingReport(report=report, paths=paths, cached=False, markdown=markdown)


def ipo_report_paths(
    record: IpoPipelineRecord,
    *,
    output_root: str | Path = DEFAULT_IPO_REPORT_DIR,
    report_name: str = "Filing Report",
) -> IpoReportPaths:
    cik = _safe_cik(record.cik)
    accession = _safe_path_part(record.accession_number or "unknown-accession", fallback="unknown-accession")
    company = _safe_path_part(record.company_name or cik, fallback=cik)
    output_dir = Path(output_root) / f"{cik}_{accession}"
    base_name = f"{company} {report_name}"
    return IpoReportPaths(
        output_dir=output_dir,
        markdown_path=output_dir / f"{base_name}.md",
        pdf_path=output_dir / f"{base_name}.pdf",
    )


def sec_current_filing_from_record(record: IpoPipelineRecord) -> SecCurrentFiling:
    return SecCurrentFiling(
        company_name=record.company_name,
        cik=record.cik,
        form=record.form,
        filing_date=record.filed_date,
        accession_number=record.accession_number,
        filing_url=record.filing_url,
        assigned_sic=record.sic or "",
        assigned_sic_description=record.industry or "",
        primary_document=_primary_document_from_url(record.filing_url),
    )


def build_ipo_filing_report(
    record: IpoPipelineRecord,
    filing_text: str,
    *,
    source_url: str = "",
    source_filing: SecCurrentFiling | None = None,
    source_document: IpoDocumentCandidate | None = None,
) -> IpoFilingReport:
    normalized = _normalize_text(filing_text)
    source_form = (source_filing.form if source_filing is not None else record.form).strip().upper()
    source_accession = source_filing.accession_number if source_filing is not None else record.accession_number
    parsed = _enhance_report_fields(record, parse_ipo_filing_text(normalized, form=source_form), normalized)
    risks = tuple(unique_flags([*record.risk_flags, *parsed.risk_flags, *analyze_text_risk_flags(normalized)]))
    going_concern = any("going concern" in risk.lower() for risk in risks) or _has_going_concern_language(normalized)
    business = _business_description(record.company_name, normalized)
    financial_rows = tuple(_financial_rows(normalized))
    balance_sheet = tuple(_balance_sheet_lines(parsed, normalized))
    dilution = tuple(_dilution_lines(parsed, normalized))
    notable_terms = tuple(_notable_term_lines(record, risks, normalized))
    bull_case = tuple(_bull_case_lines(record, parsed, business, financial_rows, balance_sheet))
    bear_case = tuple(_bear_case_lines(parsed, financial_rows, going_concern, risks, dilution, notable_terms))

    return IpoFilingReport(
        company_name=record.company_name.strip() or "Unknown company",
        form=record.form.strip().upper(),
        cik=_safe_cik(record.cik),
        accession_number=record.accession_number,
        filed_date=record.filed_date,
        source_url=source_url or record.filing_url,
        selected_form=record.form.strip().upper(),
        selected_accession_number=record.accession_number,
        source_form=source_form,
        source_accession_number=source_accession,
        source_document=source_document.name if source_document is not None else _primary_document_from_url(source_url or record.filing_url),
        source_document_size=source_document.size if source_document is not None else None,
        source_selection_reason=source_document.selection_reason if source_document is not None else "",
        parsed=parsed,
        business_description=business,
        why_interesting=tuple(_why_interesting_lines(record, normalized, business)),
        traction=tuple(_traction_lines(normalized)),
        financial_rows=financial_rows,
        going_concern=going_concern,
        key_risks=tuple(_risk_lines(risks, going_concern, normalized)),
        balance_sheet=balance_sheet,
        dilution=dilution,
        notable_terms=notable_terms,
        bull_case=bull_case,
        bear_case=bear_case,
        final_key_question=_final_key_question(record.company_name, going_concern, dilution, financial_rows),
        raw_excerpt=_raw_excerpt(filing_text),
    )


def render_ipo_filing_report_markdown(report: IpoFilingReport) -> str:
    if report.generation_method == "openai":
        return _render_openai_ipo_filing_report_markdown(report)

    headline_lines = _headline_lines(report)
    financial_heading = _financial_heading(report.financial_rows)
    lines = [
        f"# {report.company_name} {report.form} Filing Report",
        "",
        f"Source: [{_source_label(report)}]({report.source_url})",
        f"Selected row: {report.selected_form or report.form} accession {report.selected_accession_number or '--'}",
        f"Parsed source: {report.source_form or report.form} accession {report.source_accession_number or '--'}"
        + (f" document {report.source_document}" if report.source_document else ""),
        f"Filed: {report.filed_date or '--'} | CIK: {report.cik} | Accession: {report.accession_number or '--'}",
        "",
        "## The headline",
        *headline_lines,
        "",
        f"## What does {report.company_name} actually do?",
        report.business_description,
        "",
        "## Why is this interesting?",
        *_bullet_lines(report.why_interesting),
        "",
        "## Their traction so far",
        *_bullet_lines(report.traction),
        "",
        f"## {financial_heading}",
        *_financial_table_lines(report.financial_rows),
        "",
    ]

    if report.going_concern:
        lines.extend(
            [
                "## The giant red flag: going concern",
                *_bullet_lines(report.key_risks),
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## Key risks",
                *_bullet_lines(report.key_risks),
                "",
            ]
        )

    lines.extend(
        [
            "## What happens to the balance sheet if the IPO works?",
            *_bullet_lines(report.balance_sheet),
            "",
            "## Dilution: new investors are paying way above book value",
            *_bullet_lines(report.dilution),
            "",
            "## Also: capital structure / reverse split / other notable terms",
            *_bullet_lines(report.notable_terms),
            "",
            "## My plain-English read",
            "",
            "**Bull case**",
            *_bullet_lines(report.bull_case),
            "",
            "**Bear case**",
            *_bullet_lines(report.bear_case),
            "",
            f"**Final key question:** {report.final_key_question}",
            "",
            "## Parser notes",
            "- This report is generated deterministically from the selected SEC filing text. It does not use an LLM or invent undisclosed terms.",
        ]
    )
    if report.source_selection_reason:
        lines.append(f"- Source selection: {report.source_selection_reason}")
    if report.source_document_size is not None:
        lines.append(f"- Source document size from SEC index: {report.source_document_size:,} bytes.")
    if report.raw_excerpt:
        lines.extend(["- Source excerpt used for fallback context:", "", report.raw_excerpt])
    return "\n".join(lines).strip() + "\n"


def _render_openai_ipo_filing_report_markdown(report: IpoFilingReport) -> str:
    lines = [
        f"# {report.company_name} {report.form} AI Filing Report",
        "",
        f"Source: [{_source_label(report)}]({report.source_url})",
        f"Selected row: {report.selected_form or report.form} accession {report.selected_accession_number or '--'}",
        f"Parsed source: {report.source_form or report.form} accession {report.source_accession_number or '--'}"
        + (f" document {report.source_document}" if report.source_document else ""),
        f"Filed: {report.filed_date or '--'} | CIK: {report.cik} | Accession: {report.accession_number or '--'}",
        "",
        "## The headline",
        *_bullet_lines(report.headline_bullets),
        "",
        f"## What does {report.company_name} actually do?",
        report.business_description or "Not confidently extracted",
        "",
        "## IPO terms",
        *_bullet_lines(report.ipo_terms),
        "",
        "## Financial summary",
        *_bullet_lines(report.financial_summary),
        "",
        "## Use of proceeds",
        report.use_of_proceeds_summary or "Not disclosed",
        "",
        "## Key risks",
        *_bullet_lines(report.key_risks),
        "",
        "## My plain-English read",
        "",
        "**Bull case**",
        *_bullet_lines(report.bull_case),
        "",
        "**Bear case**",
        *_bullet_lines(report.bear_case),
        "",
        f"**Final key question:** {report.final_key_question or 'Not confidently extracted'}",
        "",
        "## Confidence and disclosure gaps",
        f"- Confidence: {report.confidence or 'Not confidently extracted'}",
    ]
    if report.not_disclosed_fields:
        lines.append("- Not disclosed: " + ", ".join(report.not_disclosed_fields))
    if report.not_confidently_extracted_fields:
        lines.append("- Not confidently extracted: " + ", ".join(report.not_confidently_extracted_fields))

    lines.extend(
        [
            "",
            "## Parser notes",
            "- This report was generated by OpenAI from deterministic cleaned SEC filing sections and metadata.",
            "- The model was instructed to use only the provided filing text and to mark missing or ambiguous values as Not disclosed or Not confidently extracted.",
        ]
    )
    if report.model_name:
        lines.append(f"- AI model: {report.model_name}")
    if report.source_section_names:
        lines.append(f"- Source sections sent to model: {', '.join(report.source_section_names)}")
    if report.source_selection_reason:
        lines.append(f"- Source selection: {report.source_selection_reason}")
    if report.source_document_size is not None:
        lines.append(f"- Source document size from SEC index: {report.source_document_size:,} bytes.")
    return "\n".join(lines).strip() + "\n"


def markdown_to_pdf_bytes(markdown: str, *, title: str = "IPO Filing Report") -> bytes:
    pages = _pdf_pages(markdown)
    font_object_id = 3
    page_object_ids = [4 + index * 2 for index in range(len(pages))]
    content_object_ids = [5 + index * 2 for index in range(len(pages))]

    objects: list[tuple[int, bytes]] = [
        (1, b"<< /Type /Catalog /Pages 2 0 R >>"),
        (
            2,
            (
                f"<< /Type /Pages /Kids [{' '.join(f'{object_id} 0 R' for object_id in page_object_ids)}] "
                f"/Count {len(page_object_ids)} >>"
            ).encode("ascii"),
        ),
        (font_object_id, b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"),
    ]

    for page_id, content_id, page_lines in zip(page_object_ids, content_object_ids, pages):
        content = _pdf_content_stream(page_lines)
        objects.append(
            (
                page_id,
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                    f"/Resources << /Font << /F1 {font_object_id} 0 R >> >> /Contents {content_id} 0 R >>"
                ).encode("ascii"),
            )
        )
        objects.append((content_id, b"<< /Length " + str(len(content)).encode("ascii") + b" >>\nstream\n" + content + b"\nendstream"))

    return _serialize_pdf_objects(objects, title=title)


def write_markdown_pdf(markdown: str, output_path: str | Path, *, title: str = "IPO Filing Report") -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(path, markdown_to_pdf_bytes(markdown, title=title))
    return path


def _enhance_report_fields(record: IpoPipelineRecord, parsed: ParsedIpoFields, text: str) -> ParsedIpoFields:
    low, high = parsed.price_range_low, parsed.price_range_high
    underwriters = tuple(unique_flags([*parsed.underwriters, *_extract_report_underwriters(text), *record.underwriters]))
    return ParsedIpoFields(
        proposed_ticker=parsed.proposed_ticker or record.proposed_ticker,
        exchange=parsed.exchange or record.exchange,
        offering_amount=parsed.offering_amount or record.offering_amount,
        price_range_low=low,
        price_range_high=high,
        shares_offered=parsed.shares_offered,
        implied_market_cap=parsed.implied_market_cap or record.implied_market_cap,
        revenue=parsed.revenue if parsed.revenue is not None else record.revenue,
        revenue_growth=parsed.revenue_growth if parsed.revenue_growth is not None else record.revenue_growth,
        net_income=parsed.net_income if parsed.net_income is not None else record.net_income,
        gross_margin=parsed.gross_margin if parsed.gross_margin is not None else record.gross_margin,
        cash=parsed.cash if parsed.cash is not None else record.cash,
        debt=parsed.debt if parsed.debt is not None else record.debt,
        use_of_proceeds=parsed.use_of_proceeds or record.use_of_proceeds,
        underwriters=underwriters,
        auditor=parsed.auditor or record.auditor,
        risk_flags=tuple(unique_flags([*parsed.risk_flags, *record.risk_flags])),
        is_foreign_issuer=parsed.is_foreign_issuer if parsed.is_foreign_issuer is not None else record.is_foreign_issuer,
        dilution_per_share=parsed.dilution_per_share,
        going_concern=parsed.going_concern,
        customer_concentration=parsed.customer_concentration,
        related_party_transactions=parsed.related_party_transactions,
        controlled_company=parsed.controlled_company,
        vie_or_china_risk=parsed.vie_or_china_risk,
    )


def _extract_report_price_range(text: str) -> tuple[float | None, float | None]:
    patterns = (
        r"price\s+range\s+(?:of\s+)?\$?\s*([0-9]+(?:\.[0-9]+)?)\s*(?:-|to|and)\s*\$?\s*([0-9]+(?:\.[0-9]+)?)",
        r"between\s+\$?\s*([0-9]+(?:\.[0-9]+)?)\s+and\s+\$?\s*([0-9]+(?:\.[0-9]+)?)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1)), float(match.group(2))
    return None, None


def _extract_report_shares_offered(text: str) -> float | None:
    patterns = (
        r"we\s+are\s+offering\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|thousand)?\s+(?:ordinary\s+shares|common\s+stock|shares)",
        r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|thousand)?\s+(?:ordinary\s+shares|common\s+stock|shares)\s+(?:are\s+)?(?:being\s+)?offered",
        r"offering\s+of\s+([0-9][0-9,]*(?:\.[0-9]+)?)\s*(million|thousand)?\s+(?:ordinary\s+shares|common\s+stock|shares)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        shares = float(match.group(1).replace(",", ""))
        scale = (match.group(2) or "").lower()
        if scale == "million":
            shares *= 1_000_000
        elif scale == "thousand":
            shares *= 1_000
        return shares
    return None


def _extract_report_underwriters(text: str) -> list[str]:
    patterns = (
        r"(?:representatives of the underwriters|underwriters)\s+(?:are|include|:)\s+([^.;\n]{5,220})",
        r"(?:book-running managers|bookrunners)\s+(?:are|include|:)\s+([^.;\n]{5,220})",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        raw = match.group(1)
        parts = re.split(r",| and ", raw)
        return [part.strip(" .") for part in parts if len(part.strip(" .")) >= 3][:8]
    return []


def _headline_lines(report: IpoFilingReport) -> list[str]:
    parsed = report.parsed
    if report.selected_form != report.source_form:
        first_line = (
            f"- Selected row is {report.selected_form}; this report parses the related "
            f"{report.source_form} prospectus source."
        )
    elif report.form == "424B4":
        first_line = f"- {report.company_name} filed a 424B4 final prospectus for the IPO."
    else:
        first_line = f"- {report.company_name} filed a {report.form} registration statement for a proposed IPO."
    lines = [first_line]
    if parsed.shares_offered is not None:
        lines.append(f"- Shares offered: {_format_shares(parsed.shares_offered)}.")
    else:
        lines.append("- Shares offered: Not confidently extracted.")
    if parsed.price_range_low is not None and parsed.price_range_high is not None:
        lines.append(f"- Price range: {_format_price_range(parsed.price_range_low, parsed.price_range_high)}.")
    else:
        lines.append("- Price range: Not confidently extracted.")
    listing_parts = [part for part in (parsed.proposed_ticker, parsed.exchange) if part]
    if listing_parts:
        lines.append(f"- Proposed listing: {' on '.join(listing_parts)}.")
    if parsed.underwriters:
        lines.append(f"- Underwriter(s): {', '.join(parsed.underwriters)}.")
    if parsed.shares_offered is not None and parsed.price_range_low is not None and parsed.price_range_high is not None:
        midpoint = (parsed.price_range_low + parsed.price_range_high) / 2
        gross_raise = midpoint * parsed.shares_offered
        lines.append(f"- Midpoint gross raise math: {_format_shares(parsed.shares_offered)} x ${midpoint:,.2f} = {_format_money(gross_raise)} before underwriting discounts and expenses.")
    elif parsed.offering_amount is not None:
        lines.append(f"- Maximum aggregate offering amount disclosed: {_format_money(parsed.offering_amount)}.")
    return lines


def _business_description(company_name: str, text: str) -> str:
    sentences: list[str] = []
    for heading in ("Company Overview", "Our Company", "Business Overview", "Business", "Prospectus Summary"):
        section = _section_text(
            text,
            (heading,),
            ("Risk Factors", "The Offering", "Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Management"),
        )
        sentences = _meaningful_sentences(section)
        if sentences:
            break
    if not sentences:
        narrative = _narrative_source_text(text)
        sentences = [
            sentence
            for sentence in _meaningful_sentences(narrative[:20000])
            if re.search(r"\b(we|company|business|provide|develop|operate)\b", sentence, flags=re.IGNORECASE)
        ]
    if not sentences:
        return f"The filing text did not yield a clean plain-English business description for {company_name}."
    return _join_sentences(sentences[:2], limit=600)


def _why_interesting_lines(record: IpoPipelineRecord, text: str, business: str) -> list[str]:
    narrative = _narrative_source_text(text)
    candidates = _sentences_with_terms(
        narrative,
        (
            "market",
            "industry",
            "growth",
            "growing",
            "trend",
            "category",
            "platform",
            "technology",
            "digital",
            "artificial intelligence",
            "healthcare",
            "energy",
        ),
        limit=4,
    )
    lines = [f"The IPO gives public-market investors a new look at {record.company_name}'s category: {_shorten_sentence(business, 220)}"]
    lines.extend(_prefix_sentence("The filing highlights", sentence) for sentence in candidates[:3])
    if len(lines) == 1:
        lines.append("The filing did not surface a clean market-size paragraph in the parsed excerpt, so the category angle needs a manual read of the prospectus summary.")
    return _dedupe(lines)[:4]


def _traction_lines(text: str) -> list[str]:
    narrative = _narrative_source_text(text)
    candidates = _sentences_with_terms(
        narrative,
        (
            "customer",
            "customers",
            "deployment",
            "deployments",
            "contract",
            "contracts",
            "backlog",
            "order",
            "orders",
            "revenue model",
            "subscription",
            "geography",
            "countries",
            "partnership",
            "partners",
        ),
        limit=5,
    )
    if candidates:
        return [_prefix_sentence("Disclosed traction", sentence) for sentence in candidates[:5]]
    return ["The parsed filing text did not disclose clean customer, deployment, backlog, contract, or geography metrics."]


def _financial_rows(text: str) -> list[IpoReportFinancialRow]:
    multiplier = _table_amount_multiplier(text)
    rows: list[IpoReportFinancialRow] = []
    values: dict[str, IpoReportFinancialRow] = {}

    def add(key: str, row: IpoReportFinancialRow | None) -> None:
        if row is not None and key not in values:
            values[key] = row
            rows.append(row)

    revenue = _money_row(text, "Revenue", (r"Revenue", r"Revenues", r"Net sales"), multiplier, "Top-line sales disclosed in the selected filing.")
    cost = _money_row(text, "Cost of revenue", (r"Cost of revenue", r"Cost of revenues", r"Cost of sales"), multiplier, "Direct cost to deliver the product or service.")
    gross_profit = _money_row(text, "Gross profit", (r"Gross profit",), multiplier, "Revenue after direct costs.")
    operating_expenses = _money_row(text, "Operating expenses", (r"Total operating expenses", r"Operating expenses"), multiplier, "Sales, R&D, G&A, and other operating costs.")
    net_income = _money_row(text, "Net income / loss", (r"Net income \(loss\)", r"Net loss", r"Net income"), multiplier, "Bottom-line profit or loss.")

    add("revenue", revenue)
    add("cost", cost)
    add("gross_profit", gross_profit)
    if revenue and gross_profit and revenue.latest_value:
        latest_margin = gross_profit.latest_value / revenue.latest_value * 100
        prior_margin = gross_profit.prior_value / revenue.prior_value * 100 if gross_profit.prior_value is not None and revenue.prior_value else None
        rows.append(
            IpoReportFinancialRow(
                label="Gross margin",
                latest_value=latest_margin,
                prior_value=prior_margin,
                latest_text=f"{latest_margin:.1f}%",
                prior_text=f"{prior_margin:.1f}%" if prior_margin is not None else "--",
                change_text=_change_text(latest_margin, prior_margin, already_percent=True),
                read="How much revenue remains after direct costs.",
            )
        )
    add("operating_expenses", operating_expenses)
    add("net_income", net_income)
    return rows


def _money_row(
    text: str,
    label: str,
    labels: tuple[str, ...],
    default_multiplier: float,
    read: str,
) -> IpoReportFinancialRow | None:
    for pattern_label in labels:
        latest, prior, negative = _find_money_pair_after_label(text, pattern_label, default_multiplier)
        if latest is None:
            continue
        if negative:
            latest = -abs(latest)
            prior = -abs(prior) if prior is not None else None
        return IpoReportFinancialRow(
            label=label,
            latest_value=latest,
            prior_value=prior,
            latest_text=_format_money(latest),
            prior_text=_format_money(prior),
            change_text=_change_text(latest, prior),
            read=read,
        )
    return None


def _find_money_pair_after_label(text: str, label_pattern: str, default_multiplier: float) -> tuple[float | None, float | None, bool]:
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){label_pattern}(?![A-Za-z0-9])"
        rf"[^$\d(]{{0,80}}"
        rf"(\(?\$?\s*-?[0-9][0-9,.]*(?:\s*(?:billion|million|thousand|B|M|K))?\)?)"
        rf"\s+"
        rf"(\(?\$?\s*-?[0-9][0-9,.]*(?:\s*(?:billion|million|thousand|B|M|K))?\)?)",
        re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None, None, False
    latest = _parse_money_amount(match.group(1), default_multiplier=default_multiplier)
    prior = _parse_money_amount(match.group(2), default_multiplier=default_multiplier)
    label_text = label_pattern.lower()
    negative = "loss" in label_text or "deficit" in label_text
    return latest, prior, negative


def _balance_sheet_lines(parsed: ParsedIpoFields, text: str) -> list[str]:
    lines: list[str] = []
    if parsed.cash is not None:
        lines.append(f"Cash and cash equivalents parsed from the filing: {_format_money(parsed.cash)}.")
    if parsed.debt is not None:
        lines.append(f"Debt / indebtedness parsed from the filing: {_format_money(parsed.debt)}.")
    pro_forma_cash = _first_money_near(text, ("pro forma as adjusted cash", "as adjusted cash and cash equivalents", "cash and cash equivalents as adjusted"))
    pro_forma_equity = _first_money_near(text, ("pro forma as adjusted shareholders' equity", "pro forma as adjusted stockholders' equity", "as adjusted equity"))
    if pro_forma_cash is not None:
        lines.append(f"Pro forma as-adjusted cash appears to be {_format_money(pro_forma_cash)}.")
    if pro_forma_equity is not None:
        lines.append(f"Pro forma as-adjusted equity appears to be {_format_money(pro_forma_equity)}.")
    if parsed.use_of_proceeds:
        lines.append(f"Use of proceeds: {_clean_snippet(parsed.use_of_proceeds)}.")
    if not lines:
        lines.append("The parsed filing text did not yield a clean cash, debt, pro forma balance sheet, or use-of-proceeds read.")
    return lines


def _dilution_lines(parsed: ParsedIpoFields, text: str) -> list[str]:
    lines: list[str] = []
    midpoint = None
    if parsed.price_range_low is not None and parsed.price_range_high is not None:
        midpoint = (parsed.price_range_low + parsed.price_range_high) / 2
    ntbv = _per_share_value_near(text, ("pro forma as adjusted net tangible book value per share", "net tangible book value per share"))
    dilution = _per_share_value_near(text, ("immediate dilution", "dilution per share", "dilution to new investors"))
    if midpoint is not None and ntbv is not None:
        lines.append(f"At the ${midpoint:,.2f} midpoint, new investors are paying against disclosed pro forma net tangible book value per share of about ${ntbv:,.2f}.")
    elif ntbv is not None:
        lines.append(f"The filing discloses pro forma net tangible book value per share of about ${ntbv:,.2f}.")
    if dilution is not None:
        lines.append(f"The filing discloses immediate dilution to new investors of about ${dilution:,.2f} per share.")
    dilution_sentence = _first_sentence_with_terms(text, ("dilution", "net tangible book value"))
    if dilution_sentence and not any(dilution_sentence in line for line in lines):
        lines.append(_prefix_sentence("Filing language", dilution_sentence))
    if not lines:
        lines.append("No clean IPO-price versus pro forma net tangible book value dilution table was extracted from the parsed text.")
    return _dedupe(lines)[:4]


def _notable_term_lines(record: IpoPipelineRecord, risks: Iterable[str], text: str) -> list[str]:
    lines: list[str] = []
    term_checks = (
        ("Reverse split", (r"\breverse split\b", r"\bshare split\b")),
        ("Foreign private issuer", (r"\bforeign private issuer\b",)),
        ("Controlled-company status", (r"\bcontrolled company\b",)),
        ("Related-party issues", (r"\brelated[- ]party\b",)),
        ("China/VIE risk", (r"\bvariable interest entity\b", r"\bVIEs?\b", r"\bchina[- ]based\b", r"\bPRC\b")),
        ("Customer concentration", (r"\bcustomer concentration\b", r"\bmajor customers?\b", r"\bsignificant customers?\b")),
        ("Auditor change", (r"\bauditor change\b", r"\bchange in auditor\b", r"\bchanged auditors\b", r"\bauditor resignation\b")),
        ("ADS/ADR structure", (r"\bamerican depositary (?:shares?|receipts?)\b", r"\bamerican depositary\b")),
    )
    for label, patterns in term_checks:
        if label == "Foreign private issuer" and record.is_foreign_issuer:
            sentence = _first_sentence_with_patterns(text, patterns)
            lines.append(f"{label}: {sentence or 'The row/form indicates foreign-issuer treatment.'}")
            continue
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns):
            sentence = _first_sentence_with_patterns(text, patterns)
            lines.append(f"{label}: {sentence or 'Flagged in the filing text.'}")
    for risk in risks:
        if risk and not any(risk.lower() in line.lower() for line in lines):
            lines.append(f"{risk}: flagged by the filing parser.")
    if not lines:
        lines.append("No reverse split, controlled-company, VIE/China, related-party, customer concentration, or auditor-change term was cleanly extracted.")
    return _dedupe(lines)[:8]


def _risk_lines(risks: Iterable[str], going_concern: bool, text: str) -> list[str]:
    lines: list[str] = []
    if going_concern:
        sentence = _first_sentence_with_terms(text, ("going concern", "substantial doubt about our ability to continue"))
        lines.append(sentence or "The filing contains going-concern language, which means the auditor or company is warning about runway and financing risk.")
    for risk in risks:
        if going_concern and "going concern" in risk.lower():
            continue
        lines.append(f"{risk}: this should be checked directly in the risk factors and notes.")
    if not lines:
        lines.append("No named risk flag was extracted, but the risk-factor section still needs a manual read before treating the IPO as clean.")
    return _dedupe(lines)[:6]


def _bull_case_lines(
    record: IpoPipelineRecord,
    parsed: ParsedIpoFields,
    business: str,
    financial_rows: tuple[IpoReportFinancialRow, ...],
    balance_sheet: tuple[str, ...],
) -> list[str]:
    lines = [f"The bull case is that {record.company_name} turns a public listing into enough capital and credibility to scale the business described in the filing."]
    revenue = _row_by_label(financial_rows, "Revenue")
    if revenue and revenue.latest_value is not None:
        lines.append(f"There is at least a disclosed revenue base: {revenue.latest_text} in the latest parsed period.")
    if parsed.shares_offered is not None and parsed.price_range_low is not None and parsed.price_range_high is not None:
        midpoint = (parsed.price_range_low + parsed.price_range_high) / 2
        lines.append(f"At the midpoint, the offering could add roughly {_format_money(parsed.shares_offered * midpoint)} before fees and expenses.")
    if balance_sheet:
        lines.append(_shorten_sentence(balance_sheet[0], 220))
    lines.append(f"The story to underwrite is simple: {_shorten_sentence(business, 220)}")
    return _dedupe(lines)[:5]


def _bear_case_lines(
    parsed: ParsedIpoFields,
    financial_rows: tuple[IpoReportFinancialRow, ...],
    going_concern: bool,
    risks: Iterable[str],
    dilution: tuple[str, ...],
    notable_terms: tuple[str, ...],
) -> list[str]:
    lines: list[str] = []
    net_income = _row_by_label(financial_rows, "Net income / loss")
    if going_concern:
        lines.append("The going-concern language is the biggest problem: the IPO may be less about growth capital and more about staying funded.")
    if net_income and net_income.latest_value is not None and net_income.latest_value < 0:
        lines.append(f"The company is losing money: parsed latest net loss is {net_income.latest_text}.")
    if parsed.revenue is not None and parsed.revenue <= 0:
        lines.append("The parser found no meaningful revenue, so public buyers may be underwriting a very early-stage issuer.")
    if dilution:
        lines.append(_shorten_sentence(dilution[0], 240))
    for risk in risks:
        if "going concern" not in risk.lower():
            lines.append(f"{risk} adds diligence risk.")
            break
    if notable_terms:
        lines.append(_shorten_sentence(notable_terms[0], 240))
    if not lines:
        lines.append("The bear case is that offering terms, financial scale, and risk factors are not strong enough to support the proposed valuation.")
    return _dedupe(lines)[:5]


def _final_key_question(
    company_name: str,
    going_concern: bool,
    dilution: tuple[str, ...],
    financial_rows: tuple[IpoReportFinancialRow, ...],
) -> str:
    revenue = _row_by_label(financial_rows, "Revenue")
    if going_concern:
        return f"Can {company_name} turn IPO proceeds into durable revenue before financing pressure forces another dilutive raise?"
    if dilution:
        return f"Is {company_name}'s growth and traction strong enough to justify the gap between the IPO price and book value?"
    if revenue and revenue.latest_value is not None:
        return f"Can {company_name} convert its current revenue base into a business that deserves public-company costs and scrutiny?"
    return f"What specific milestone would prove that {company_name} is more than an IPO financing story?"


def _financial_heading(rows: tuple[IpoReportFinancialRow, ...]) -> str:
    revenue = _row_by_label(rows, "Revenue")
    if revenue and revenue.latest_value is not None and abs(revenue.latest_value) < 10_000_000:
        return "The financials are tiny"
    return "The financials"


def _financial_table_lines(rows: tuple[IpoReportFinancialRow, ...]) -> list[str]:
    if not rows:
        return ["- The parser did not extract a clean two-period financial table from the filing text."]
    lines = [
        "| Metric | Latest parsed period | Prior parsed period | Change | Plain-English read |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (row.label, row.latest_text, row.prior_text, row.change_text, row.read)
            )
            + " |"
        )
    return lines


def _section_text(text: str, headings: tuple[str, ...], stop_headings: tuple[str, ...]) -> str:
    lower = text.lower()
    start_index = -1
    for heading in headings:
        match = re.search(rf"\b{re.escape(heading.lower())}\b", lower)
        if match:
            start_index = match.end()
            break
    if start_index < 0:
        return ""
    stop_index = len(text)
    for stop in stop_headings:
        match = re.search(rf"\b{re.escape(stop.lower())}\b", lower[start_index + 200 :])
        if match:
            stop_index = min(stop_index, start_index + 200 + match.start())
    return text[start_index:stop_index][:5000]


def _narrative_source_text(text: str) -> str:
    sections: list[str] = []
    for heading in ("Company Overview", "Our Company", "Business Overview", "Business", "Prospectus Summary"):
        section = _section_text(
            text,
            (heading,),
            ("Risk Factors", "The Offering", "Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Management"),
        )
        if section and not _looks_like_boilerplate_or_toc(section):
            sections.append(section)
    return "\n".join(sections) if sections else text[:20000]


def _meaningful_sentences(text: str) -> list[str]:
    output: list[str] = []
    for sentence in _split_sentences(text):
        clean = _clean_snippet(sentence)
        if len(clean) < 40 or _looks_like_table_of_contents(clean) or _looks_like_boilerplate_sentence(clean):
            continue
        output.append(clean)
        if len(output) >= 8:
            break
    return output


def _sentences_with_terms(text: str, terms: tuple[str, ...], *, limit: int) -> list[str]:
    output: list[str] = []
    for sentence in _split_sentences(text[:80000]):
        lower = f" {sentence.lower()} "
        if any(term in lower for term in terms):
            clean = _clean_snippet(sentence)
            if len(clean) >= 35 and not _looks_like_table_of_contents(clean) and not _looks_like_boilerplate_sentence(clean):
                output.append(clean)
        if len(output) >= limit:
            break
    return _dedupe(output)


def _first_sentence_with_terms(text: str, terms: tuple[str, ...]) -> str:
    matches = _sentences_with_terms(text, terms, limit=1)
    return matches[0] if matches else ""


def _first_sentence_with_patterns(text: str, patterns: tuple[str, ...]) -> str:
    for sentence in _split_sentences(text[:80000]):
        clean = _clean_snippet(sentence)
        if len(clean) < 35 or _looks_like_table_of_contents(clean) or _looks_like_boilerplate_sentence(clean):
            continue
        if any(re.search(pattern, clean, flags=re.IGNORECASE) for pattern in patterns):
            return clean
    return ""


def _split_sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip())


def _join_sentences(sentences: Iterable[str], *, limit: int) -> str:
    joined = " ".join(sentence.strip() for sentence in sentences if sentence.strip())
    return _shorten_sentence(joined, limit)


def _prefix_sentence(prefix: str, sentence: str) -> str:
    sentence = sentence.strip()
    if not sentence:
        return prefix + "."
    return f"{prefix}: {sentence}"


def _shorten_sentence(value: str, limit: int) -> str:
    clean = _clean_snippet(value)
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip(" ,.;") + "..."


def _raw_excerpt(text: str) -> str:
    sentences = _meaningful_sentences(_normalize_text(text)[:12000])
    return _join_sentences(sentences[:3], limit=900) if sentences else ""


def _normalize_text(text: str) -> str:
    text = re.sub(r"[\t\r\f\v]+", " ", text or "")
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


def _clean_snippet(value: str) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip(" -")
    return clean.strip()


def _looks_like_table_of_contents(value: str) -> bool:
    lower = value.lower()
    return (
        "table of contents" in lower
        or bool(re.search(r"\.{4,}\s*\d+$", value))
        or len(re.findall(r"\b\d+\b", value)) > 12
        or len(re.findall(r"\b[A-Z][A-Z '&/-]{3,}\s+\d{1,3}\b", value)) >= 3
    )


def _looks_like_boilerplate_or_toc(value: str) -> bool:
    return _looks_like_table_of_contents(value) or _looks_like_boilerplate_sentence(value)


def _looks_like_boilerplate_sentence(value: str) -> bool:
    lower = value.lower()
    boilerplate_terms = (
        "indicate by check mark",
        "large accelerated filer",
        "smaller reporting company",
        "emerging growth company",
        "we may amend or supplement this prospectus",
        "you should read the entire prospectus",
        "this prospectus contains forward-looking statements",
        "factors that could contribute to such differences",
        "not limited to",
        "risk factors",
    )
    return any(term in lower for term in boilerplate_terms)


def _has_going_concern_language(text: str) -> bool:
    lower = text.lower()
    return "going concern" in lower or "substantial doubt about our ability to continue" in lower


def _first_money_near(text: str, terms: tuple[str, ...]) -> float | None:
    lower = text.lower()
    multiplier = _table_amount_multiplier(text)
    for term in terms:
        index = lower.find(term)
        if index < 0:
            continue
        window = text[index : index + 420]
        match = re.search(r"\$?\s*([0-9][0-9,.]*(?:\s*(?:billion|million|thousand|B|M|K))?)", window, flags=re.IGNORECASE)
        if match:
            return _parse_money_amount(match.group(1), default_multiplier=multiplier)
    return None


def _per_share_value_near(text: str, terms: tuple[str, ...]) -> float | None:
    lower = text.lower()
    for term in terms:
        index = lower.find(term)
        if index < 0:
            continue
        window = text[index : index + 520]
        matches = re.findall(r"\$?\s*([0-9]+(?:\.[0-9]+)?)", window)
        values = [float(value) for value in matches if 0 <= float(value) < 200]
        if values:
            return values[-1] if "dilution" in term else values[0]
    return None


def _parse_money_amount(value: str | None, *, default_multiplier: float) -> float | None:
    if value is None:
        return None
    raw = value.strip()
    negative = raw.startswith("-") or (raw.startswith("(") and raw.endswith(")"))
    clean = raw.strip("()").replace("$", "").replace(",", "").replace(" ", "")
    multiplier = default_multiplier
    lower = clean.lower()
    for suffix, scale in (("billion", 1_000_000_000), ("million", 1_000_000), ("thousand", 1_000), ("b", 1_000_000_000), ("m", 1_000_000), ("k", 1_000)):
        if lower.endswith(suffix):
            multiplier = scale
            clean = clean[: -len(suffix)]
            break
    try:
        parsed = float(clean) * multiplier
    except ValueError:
        return None
    return -abs(parsed) if negative else parsed


def _table_amount_multiplier(text: str) -> float:
    head = text[:12000].lower()
    if "in thousands" in head or "$ in thousands" in head:
        return 1_000
    if "in millions" in head or "$ in millions" in head:
        return 1_000_000
    return 1


def _change_text(latest: float | None, prior: float | None, *, already_percent: bool = False) -> str:
    if latest is None or prior in (None, 0):
        return "--"
    change = latest - prior if already_percent else (latest - prior) / abs(prior) * 100
    return f"{change:+.1f}%" if not already_percent else f"{change:+.1f} pts"


def _format_money(value: float | None) -> str:
    if value is None:
        return "--"
    sign = "-" if value < 0 else ""
    amount = abs(value)
    if amount >= 1_000_000_000:
        return f"{sign}${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"{sign}${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"{sign}${amount / 1_000:.1f}K"
    return f"{sign}${amount:,.2f}"


def _format_shares(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f} million shares"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f} thousand shares"
    return f"{value:,.0f} shares"


def _format_price_range(low: float, high: float) -> str:
    if low == high:
        return f"${low:,.2f}"
    return f"${low:,.2f} to ${high:,.2f}"


def _source_label(report: IpoFilingReport) -> str:
    document = f" {report.source_document}" if report.source_document else ""
    return f"SEC {report.source_form or report.form}{document}"


def _row_by_label(rows: Iterable[IpoReportFinancialRow], label: str) -> IpoReportFinancialRow | None:
    for row in rows:
        if row.label == label:
            return row
    return None


def _bullet_lines(values: Iterable[str]) -> list[str]:
    lines = [f"- {value.strip()}" for value in values if value and value.strip()]
    return lines or ["- Not cleanly extracted from the filing text."]


def _markdown_cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = _clean_snippet(value)
        key = clean.lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return output


def _safe_cik(cik: str) -> str:
    try:
        return normalize_cik(cik)
    except ValueError:
        digits = re.sub(r"\D", "", str(cik or ""))
        return digits.zfill(10) if digits else "0000000000"


def _safe_path_part(value: str, *, fallback: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(value or ""))
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    return clean[:120] if clean else fallback


def _primary_document_from_url(url: str) -> str:
    if not url:
        return ""
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return "" if name.endswith("-index.htm") else name


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _atomic_write_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _pdf_pages(markdown: str) -> list[list[str]]:
    wrapped: list[str] = []
    for raw_line in markdown.splitlines():
        if not raw_line.strip():
            wrapped.append("")
            continue
        prefix = ""
        line = raw_line
        if raw_line.startswith("- "):
            prefix = "  "
        pieces = textwrap.wrap(line, width=96, subsequent_indent=prefix, break_long_words=False, replace_whitespace=False)
        wrapped.extend(pieces or [""])
    page_size = 54
    pages = [wrapped[index : index + page_size] for index in range(0, len(wrapped), page_size)]
    return pages or [[""]]


def _pdf_content_stream(lines: list[str]) -> bytes:
    output = [b"BT", b"/F1 9 Tf", b"50 760 Td", b"12 TL"]
    for index, line in enumerate(lines):
        if index:
            output.append(b"T*")
        output.append(_pdf_string(line) + b" Tj")
    output.append(b"ET")
    return b"\n".join(output)


def _pdf_string(value: str) -> bytes:
    encoded = value.encode("latin-1", errors="replace")
    encoded = encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")
    return b"(" + encoded + b")"


def _serialize_pdf_objects(objects: list[tuple[int, bytes]], *, title: str) -> bytes:
    objects = sorted(objects, key=lambda item: item[0])
    max_id = max(object_id for object_id, _payload in objects)
    info_id = max_id + 1
    objects.append((info_id, b"<< /Title " + _pdf_string(title) + b" /Creator (Portfolio Risk Cockpit) >>"))

    payload = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    offsets: dict[int, int] = {}
    for object_id, body in objects:
        offsets[object_id] = len(payload)
        payload += f"{object_id} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(payload)
    size = info_id + 1
    payload += f"xref\n0 {size}\n".encode("ascii")
    payload += b"0000000000 65535 f \n"
    for object_id in range(1, size):
        payload += f"{offsets.get(object_id, 0):010d} 00000 n \n".encode("ascii")
    payload += (
        f"trailer\n<< /Size {size} /Root 1 0 R /Info {info_id} 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return payload
