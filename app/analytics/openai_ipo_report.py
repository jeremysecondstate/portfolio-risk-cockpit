from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.analytics.ipo_filing_report import (
    DEFAULT_IPO_REPORT_DIR,
    GeneratedIpoFilingReport,
    IpoFilingReport,
    build_ipo_filing_report,
    ipo_report_paths,
    reportable_ipo_form,
    sec_current_filing_from_record,
    write_ipo_filing_report,
)
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


DEFAULT_OPENAI_IPO_REPORT_MODEL = "gpt-5.5"
AI_REPORT_NAME = "AI Filing Report"
FULL_PROSPECTUS_CHAR_LIMIT = 160_000
SECTION_MAP_TARGET_NAMES = (
    "prospectus_summary",
    "business",
    "offering",
    "use_of_proceeds",
    "mdna_financials",
    "risk_factors",
    "underwriting",
)
SECTION_SLICE_PADDING = 1_500


@dataclass(frozen=True)
class IpoFilingSourceSection:
    name: str
    text: str
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True)
class IpoFilingSourceBundle:
    metadata: dict[str, Any]
    deterministic_extracts: dict[str, Any]
    full_text: str
    sections: tuple[IpoFilingSourceSection, ...] = ()
    source_mode: str = "full_prospectus_text"
    section_map: tuple[dict[str, Any], ...] = ()

    @property
    def section_debug(self) -> tuple[dict[str, Any], ...]:
        if self.sections:
            return tuple(_section_debug_payload(section) for section in self.sections)
        return (
            {
                "name": "full_prospectus_text",
                "character_length": len(self.full_text),
                "preview": _shorten(self.full_text, 300),
            },
        )

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "metadata": self.metadata,
            "deterministic_extracts": self.deterministic_extracts,
            "source_mode": self.source_mode,
            "section_debug": list(self.section_debug),
        }
        if self.sections:
            payload["sections"] = [asdict(section) for section in self.sections]
            payload["section_map"] = list(self.section_map)
        else:
            payload["full_prospectus_text"] = self.full_text
        return payload


class OpenAiIpoReportError(RuntimeError):
    """Raised for OpenAI IPO report generation failures with secrets redacted."""


STRING_ARRAY_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
}

SOURCE_SNIPPETS_SCHEMA = {
    "type": "array",
    "items": {"type": "string"},
    "description": "Short snippets copied from the provided filing sections.",
}

IPO_REPORT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "markdown_report",
        "facts",
        "confidence",
        "not_disclosed_fields",
        "not_confidently_extracted_fields",
    ],
    "properties": {
        "markdown_report": {
            "type": "string",
            "description": "A polished Markdown analyst memo based only on the provided filing text and deterministic facts.",
        },
        "facts": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "company_name",
                "form",
                "ticker",
                "exchange",
                "shares_offered",
                "price_range",
                "offering_size",
                "revenue",
                "cash",
                "debt",
                "source_snippets",
            ],
            "properties": {
                "company_name": {"type": "string"},
                "form": {"type": "string"},
                "ticker": {"type": "string"},
                "exchange": {"type": "string"},
                "shares_offered": {"type": "string"},
                "price_range": {"type": "string"},
                "offering_size": {"type": "string"},
                "revenue": {"type": "string"},
                "cash": {"type": "string"},
                "debt": {"type": "string"},
                "source_snippets": SOURCE_SNIPPETS_SCHEMA,
            },
        },
        "confidence": {
            "type": "object",
            "additionalProperties": False,
            "required": ["level", "explanation"],
            "properties": {
                "level": {"type": "string", "enum": ["high", "medium", "low"]},
                "explanation": {"type": "string"},
            },
        },
        "not_disclosed_fields": STRING_ARRAY_SCHEMA,
        "not_confidently_extracted_fields": STRING_ARRAY_SCHEMA,
    },
}


SECTION_MAP_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sections"],
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "start_offset", "end_offset", "confidence", "reason"],
                "properties": {
                    "name": {"type": "string", "enum": list(SECTION_MAP_TARGET_NAMES)},
                    "start_offset": {"type": "integer"},
                    "end_offset": {"type": "integer"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "reason": {"type": "string"},
                },
            },
        }
    },
}


IPO_REPORT_SYSTEM_PROMPT = """Write an IPO filing analyst memo from the provided SEC prospectus source.

Rules:
- Use only the provided metadata, deterministic extracts, and filing text or mapped filing sections.
- Treat deterministic extracts as supporting facts, not a substitute for reading the filing context.
- Say exactly "Not disclosed" when a requested field is absent from the source.
- Say exactly "Not confidently extracted" when the source is ambiguous or too thin.
- Do not invent offering size, price range, revenue, cash, debt, market cap, listing terms, or exchange/ticker details.
- Produce a polished Markdown analyst memo. Do not return a stitched field-by-field template.
- Include a small "Source excerpts" section with a few short snippets for the most important claims, but do not append a snippet after every bullet.
- Include sections for executive read, business, IPO terms, financial read, use of proceeds, key risks, bull case, bear case, and final key question when supported by the filing.
- Do not include the outer report title, SEC source header, parser notes, or fenced code blocks in markdown_report.
- Never include credentials, API keys, or secrets.
- Return only JSON that matches the schema.
"""


SECTION_MAP_SYSTEM_PROMPT = """Map a large IPO prospectus into broad analyst-relevant sections.

Use exact character offsets into the provided full_prospectus_text. Return only offsets for sections you can identify from the text. Prefer larger ranges that include the section body, tables, and nearby subsections over tiny heading previews.
"""


class OpenAiIpoReportClient:
    def __init__(
        self,
        *,
        openai_client: Any | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        load_dotenv()
        self._openai_client = openai_client
        self._api_key = api_key
        self.model = (model or os.getenv("OPENAI_IPO_REPORT_MODEL") or DEFAULT_OPENAI_IPO_REPORT_MODEL).strip()

    def generate_report(self, bundle: IpoFilingSourceBundle) -> dict[str, Any]:
        if len(bundle.full_text) <= FULL_PROSPECTUS_CHAR_LIMIT:
            return self._generate_markdown_report(bundle)

        section_map = self.map_large_prospectus_sections(bundle)
        mapped_bundle = bundle_with_mapped_sections(bundle, section_map)
        return self._generate_markdown_report(mapped_bundle)

    def map_large_prospectus_sections(self, bundle: IpoFilingSourceBundle) -> dict[str, Any]:
        request_payload = json.dumps(
            {
                "metadata": bundle.metadata,
                "target_sections": list(SECTION_MAP_TARGET_NAMES),
                "full_prospectus_text": bundle.full_text,
            },
            ensure_ascii=False,
            indent=2,
        )
        try:
            response = self._client().responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": SECTION_MAP_SYSTEM_PROMPT},
                    {"role": "user", "content": request_payload},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "ipo_filing_section_map",
                        "schema": SECTION_MAP_JSON_SCHEMA,
                        "strict": True,
                    }
                },
                store=False,
            )
        except Exception as exc:
            message = _redact_api_key(f"OpenAI IPO section-map generation failed: {exc}", self._current_api_key())
            raise OpenAiIpoReportError(message) from None

        try:
            payload = json.loads(_response_output_text(response))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OpenAiIpoReportError(f"OpenAI IPO section-map response was not valid structured JSON: {exc}") from None
        _validate_section_map_payload(payload)
        return payload

    def _generate_markdown_report(self, bundle: IpoFilingSourceBundle) -> dict[str, Any]:
        request_payload = json.dumps(bundle.to_payload(), ensure_ascii=False, indent=2)
        try:
            response = self._client().responses.create(
                model=self.model,
                input=[
                    {"role": "system", "content": IPO_REPORT_SYSTEM_PROMPT},
                    {"role": "user", "content": request_payload},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "ipo_filing_report",
                        "schema": IPO_REPORT_JSON_SCHEMA,
                        "strict": True,
                    }
                },
                store=False,
            )
        except Exception as exc:
            message = _redact_api_key(f"OpenAI IPO report generation failed: {exc}", self._current_api_key())
            raise OpenAiIpoReportError(message) from None

        try:
            payload = json.loads(_response_output_text(response))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OpenAiIpoReportError(f"OpenAI IPO report response was not valid structured JSON: {exc}") from None

        _validate_report_payload(payload)
        payload["_source_mode"] = bundle.source_mode
        payload["_source_section_names"] = [section.name for section in bundle.sections] if bundle.sections else ("full_prospectus_text",)
        payload["_source_section_debug"] = [_section_debug_line(section) for section in bundle.sections] if bundle.sections else (
            f"full_prospectus_text ({len(bundle.full_text)} chars): {_shorten(bundle.full_text, 300)}",
        )
        return payload

    def _client(self) -> Any:
        if self._openai_client is not None:
            return self._openai_client

        api_key = self._current_api_key()
        if not api_key:
            raise OpenAiIpoReportError("OPENAI_API_KEY is not configured. Add it to .env or the environment.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAiIpoReportError("The openai package is not installed. Run pip install -r requirements.txt.") from exc

        self._openai_client = OpenAI(api_key=api_key)
        return self._openai_client

    def _current_api_key(self) -> str:
        return (self._api_key if self._api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()


def generate_openai_ipo_filing_report(
    record: IpoPipelineRecord,
    *,
    client: SecEdgarClient | None = None,
    openai_client: Any | None = None,
    output_root: str | Path = DEFAULT_IPO_REPORT_DIR,
    force_refresh: bool = False,
    model: str | None = None,
) -> GeneratedIpoFilingReport:
    paths = ipo_report_paths(record, output_root=output_root, report_name=AI_REPORT_NAME)
    if not force_refresh and paths.markdown_path.exists() and paths.pdf_path.exists():
        cached_markdown = _safe_read_text(paths.markdown_path)
        if "This report was generated by OpenAI" in cached_markdown:
            return GeneratedIpoFilingReport(report=None, paths=paths, cached=True, markdown=cached_markdown)

    if not reportable_ipo_form(record.form):
        raise ValueError(f"AI IPO filing reports are supported for S-1/F-1, 424B4, and EFFECT rows, not {record.form or 'unknown form'}.")

    client = client or SecEdgarClient()
    selected_filing = sec_current_filing_from_record(record)
    source_filing = related_prospectus_filing_for_report(client, selected_filing)
    fetched = fetch_ipo_document_text_with_source(client, source_filing)
    return save_openai_ipo_filing_report(
        record,
        fetched.text,
        source_url=fetched.candidate.url,
        source_filing=source_filing,
        source_document=fetched.candidate,
        output_root=output_root,
        force_refresh=True,
        openai_client=openai_client,
        model=model,
    )


def save_openai_ipo_filing_report(
    record: IpoPipelineRecord,
    filing_text: str,
    *,
    source_url: str = "",
    source_filing: SecCurrentFiling | None = None,
    source_document: IpoDocumentCandidate | None = None,
    output_root: str | Path = DEFAULT_IPO_REPORT_DIR,
    force_refresh: bool = False,
    openai_client: Any | None = None,
    model: str | None = None,
) -> GeneratedIpoFilingReport:
    paths = ipo_report_paths(record, output_root=output_root, report_name=AI_REPORT_NAME)
    if not force_refresh and paths.markdown_path.exists() and paths.pdf_path.exists():
        cached_markdown = _safe_read_text(paths.markdown_path)
        if "This report was generated by OpenAI" in cached_markdown:
            return GeneratedIpoFilingReport(report=None, paths=paths, cached=True, markdown=cached_markdown)

    base_report = build_ipo_filing_report(
        record,
        filing_text,
        source_url=source_url,
        source_filing=source_filing,
        source_document=source_document,
    )
    bundle = build_ipo_filing_source_bundle(
        record,
        filing_text,
        source_url=source_url,
        source_filing=source_filing,
        source_document=source_document,
        deterministic_report=base_report,
    )
    client = OpenAiIpoReportClient(openai_client=openai_client, model=model)
    payload = client.generate_report(bundle)
    report = _report_from_openai_payload(
        base_report,
        payload,
        model=client.model,
    )
    return write_ipo_filing_report(report, paths)


def build_ipo_filing_source_bundle(
    record: IpoPipelineRecord,
    filing_text: str,
    *,
    source_url: str = "",
    source_filing: SecCurrentFiling | None = None,
    source_document: IpoDocumentCandidate | None = None,
    deterministic_report: IpoFilingReport | None = None,
) -> IpoFilingSourceBundle:
    normalized = _normalize_text(filing_text)
    source_form = (source_filing.form if source_filing is not None else record.form).strip().upper()
    source_accession = source_filing.accession_number if source_filing is not None else record.accession_number
    parsed = parse_ipo_filing_text(normalized, form=source_form)
    risks = unique_flags([*record.risk_flags, *parsed.risk_flags, *analyze_text_risk_flags(normalized)])

    metadata = {
        "company_name": record.company_name.strip() or "Unknown company",
        "cik": _safe_cik(record.cik),
        "filed_date": record.filed_date,
        "selected_form": record.form.strip().upper(),
        "selected_accession_number": record.accession_number,
        "source_form": source_form,
        "source_accession_number": source_accession,
        "source_url": source_url or record.filing_url,
        "source_document": source_document.name if source_document is not None else _primary_document_from_url(source_url or record.filing_url),
        "source_document_size": source_document.size if source_document is not None else None,
        "source_selection_reason": source_document.selection_reason if source_document is not None else "",
    }
    deterministic_extracts = {
        "parsed_fields": _parsed_fields_payload(parsed),
        "risk_flags": risks,
    }
    if deterministic_report is None:
        deterministic_report = build_ipo_filing_report(
            record,
            filing_text,
            source_url=source_url,
            source_filing=source_filing,
            source_document=source_document,
        )
    deterministic_extracts["deterministic_financial_rows"] = [
        {
            "label": row.label,
            "latest_text": row.latest_text,
            "prior_text": row.prior_text,
            "change_text": row.change_text,
            "read": row.read,
        }
        for row in deterministic_report.financial_rows
    ]
    deterministic_extracts["deterministic_balance_sheet"] = list(deterministic_report.balance_sheet)
    deterministic_extracts["deterministic_dilution"] = list(deterministic_report.dilution)
    return IpoFilingSourceBundle(
        metadata=metadata,
        deterministic_extracts=deterministic_extracts,
        full_text=normalized,
    )


def bundle_with_mapped_sections(bundle: IpoFilingSourceBundle, section_map_payload: dict[str, Any]) -> IpoFilingSourceBundle:
    sections: list[IpoFilingSourceSection] = []
    clean_map: list[dict[str, Any]] = []
    text_length = len(bundle.full_text)
    for entry in section_map_payload.get("sections", []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if name not in SECTION_MAP_TARGET_NAMES:
            continue
        start = _bounded_int(entry.get("start_offset"), 0, text_length)
        end = _bounded_int(entry.get("end_offset"), 0, text_length)
        if end <= start:
            continue
        padded_start = max(0, start - SECTION_SLICE_PADDING)
        padded_end = min(text_length, end + SECTION_SLICE_PADDING)
        body = _clean_section_text(bundle.full_text[padded_start:padded_end], limit=45_000)
        if not body:
            continue
        sections.append(IpoFilingSourceSection(name=name, text=body, start_offset=padded_start, end_offset=padded_end))
        clean_map.append(
            {
                "name": name,
                "start_offset": start,
                "end_offset": end,
                "confidence": str(entry.get("confidence") or ""),
                "reason": str(entry.get("reason") or ""),
            }
        )

    if not sections:
        sections = tuple(_cleaned_source_sections(bundle.full_text))
        clean_map = [
            {
                "name": section.name,
                "start_offset": section.start_offset,
                "end_offset": section.end_offset,
                "confidence": "low",
                "reason": "Deterministic fallback section slice.",
            }
            for section in sections
        ]
    return IpoFilingSourceBundle(
        metadata=bundle.metadata,
        deterministic_extracts=bundle.deterministic_extracts,
        full_text=bundle.full_text,
        sections=tuple(sections),
        source_mode="mapped_large_sections",
        section_map=tuple(clean_map),
    )


def _report_from_openai_payload(
    base: IpoFilingReport,
    payload: dict[str, Any],
    *,
    model: str,
) -> IpoFilingReport:
    facts = payload.get("facts") if isinstance(payload.get("facts"), dict) else {}
    source_section_names = tuple(str(value) for value in payload.get("_source_section_names", ("full_prospectus_text",)))
    source_section_debug = tuple(str(value) for value in payload.get("_source_section_debug", ()))
    return replace(
        base,
        business_description="",
        why_interesting=(),
        traction=(),
        financial_rows=(),
        key_risks=(),
        balance_sheet=(),
        dilution=(),
        notable_terms=(),
        bull_case=(),
        bear_case=(),
        final_key_question="",
        raw_excerpt="",
        generation_method="openai",
        headline_bullets=(),
        ipo_terms=(),
        financial_summary=(),
        use_of_proceeds_summary="",
        confidence=_confidence_text(payload["confidence"]),
        not_disclosed_fields=tuple(_pretty_label_list(payload["not_disclosed_fields"])),
        not_confidently_extracted_fields=tuple(_pretty_label_list(payload["not_confidently_extracted_fields"])),
        model_name=model,
        source_section_names=source_section_names,
        source_section_debug=source_section_debug,
        source_mode=str(payload.get("_source_mode") or "full_prospectus_text"),
        ai_markdown_report=_clean_model_markdown(str(payload["markdown_report"])),
        facts_json=json.dumps(_facts_metadata_payload(facts, payload), indent=2, ensure_ascii=False),
    )


def _cleaned_source_sections(text: str) -> list[IpoFilingSourceSection]:
    specs = (
        (
            "prospectus_summary",
            ("Prospectus Summary", "Summary"),
            ("Risk Factors", "The Offering", "Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Management"),
            9000,
            ("company", "business", "we are", "we provide", "we develop", "customers", "market"),
            600,
            2200,
        ),
        (
            "business",
            ("Business Overview", "Our Business", "Our Company", "Company Overview", "Prospectus Summary", "Business"),
            ("Risk Factors", "The Offering", "Use of Proceeds", "Management", "Management's Discussion", "Financial Statements", "Underwriting"),
            12000,
            ("our platform", "we provide", "we develop", "we operate", "our customers", "enterprise customers", "contracts", "market"),
            900,
            3200,
        ),
        (
            "offering",
            ("The Offering", "Offering", "Initial Public Offering"),
            ("Risk Factors", "Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Underwriting", "Management"),
            7000,
            ("we are offering", "initial public offering price", "price range", "under the symbol", "applied to list"),
            600,
            2600,
        ),
        (
            "use_of_proceeds",
            ("Use of Proceeds",),
            ("Dividend Policy", "Capitalization", "Dilution", "Management", "Underwriting", "Risk Factors", "Business"),
            8000,
            ("we intend to use", "use the net proceeds", "net proceeds", "proceeds for", "repayment of indebtedness"),
            700,
            3200,
        ),
        (
            "financials",
            (
                "Selected Consolidated Financial Data",
                "Selected Consolidated Statements of Operations",
                "Consolidated Statements of Operations",
                "Selected Financial Data",
                "Results of Operations",
                "Management's Discussion and Analysis",
                "Liquidity and Capital Resources",
            ),
            ("Business", "Risk Factors", "Management", "Underwriting", "Financial Statements", "Quantitative and Qualitative Disclosures"),
            16000,
            ("revenue", "revenues", "net loss", "net income", "cash and cash equivalents", "total debt", "liquidity", "capital resources"),
            900,
            4200,
        ),
        (
            "capitalization_and_dilution",
            ("Capitalization", "Dilution"),
            ("Management", "Underwriting", "Plan of Distribution", "Financial Statements", "Business"),
            8000,
            ("capitalization", "net tangible book value", "immediate dilution", "pro forma as adjusted"),
            700,
            3000,
        ),
        (
            "risk_factors",
            ("Risk Factors", "Going Concern"),
            ("Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Management", "Business", "Underwriting"),
            12000,
            ("going concern", "substantial doubt", "risk factors", "customer concentration", "related party", "controlled company"),
            700,
            3600,
        ),
        (
            "underwriting",
            ("Underwriting", "Plan of Distribution"),
            ("Legal Matters", "Experts", "Financial Statements", "Where You Can Find More Information"),
            9000,
            ("underwriters are", "representatives of the underwriters", "book-running", "bookrunners", "under the symbol", "applied to list"),
            800,
            3600,
        ),
    )
    sections: list[IpoFilingSourceSection] = []
    seen_text: set[str] = set()
    for name, headings, stops, limit, terms, window_before, window_after in specs:
        section = _rich_section_text(
            text,
            headings=headings,
            stop_headings=stops,
            terms=terms,
            limit=limit,
            window_before=window_before,
            window_after=window_after,
        )
        key = f"{name}:{section[:500].lower()}"
        if section and key not in seen_text:
            seen_text.add(key)
            sections.append(IpoFilingSourceSection(name=name, text=section))

    if not sections:
        sections.append(IpoFilingSourceSection(name="opening_excerpt", text=_clean_section_text(text[:12000], limit=12000)))
    return sections


def _rich_section_text(
    text: str,
    *,
    headings: tuple[str, ...],
    stop_headings: tuple[str, ...],
    terms: tuple[str, ...],
    limit: int,
    window_before: int,
    window_after: int,
) -> str:
    pieces = [
        *_sections_between_headings(
            text,
            headings,
            stop_headings,
            limit=limit,
            min_body_chars=min(900, max(250, limit // 12)),
            max_sections=2,
        ),
        *_windows_around_terms(
            text,
            terms,
            before=window_before,
            after=window_after,
            max_windows=4,
        ),
    ]
    return _merge_section_pieces(pieces, limit=limit)


def _sections_between_headings(
    text: str,
    headings: tuple[str, ...],
    stop_headings: tuple[str, ...],
    *,
    limit: int,
    min_body_chars: int,
    max_sections: int,
) -> list[str]:
    lower = text.lower()
    starts: list[tuple[int, int]] = []
    for heading in headings:
        for match in re.finditer(rf"\b{re.escape(heading.lower())}\b", lower):
            starts.append((match.start(), match.end()))
    sections: list[str] = []
    for start, content_start in sorted(starts, key=lambda item: item[0]):
        if _looks_like_toc_window(text[max(0, start - 160) : start + 320]):
            continue
        stop_index = len(text)
        for stop in stop_headings:
            match = re.search(rf"\b{re.escape(stop.lower())}\b", lower[content_start + min_body_chars :])
            if match:
                stop_index = min(stop_index, content_start + min_body_chars + match.start())
        section = text[content_start:stop_index].strip()
        if section and _has_section_body(section):
            sections.append(section[:limit])
        if len(sections) >= max_sections:
            break
    return sections


def _windows_around_terms(
    text: str,
    terms: tuple[str, ...],
    *,
    before: int,
    after: int,
    max_windows: int,
) -> list[str]:
    lower = text.lower()
    windows: list[str] = []
    seen_ranges: list[tuple[int, int]] = []
    for term in terms:
        start_at = 0
        while len(windows) < max_windows:
            index = lower.find(term.lower(), start_at)
            if index < 0:
                break
            start = max(0, index - before)
            end = min(len(text), index + len(term) + after)
            start_at = index + len(term)
            if any(start <= old_end and end >= old_start for old_start, old_end in seen_ranges):
                continue
            window = text[start:end].strip()
            if window and _has_section_body(window):
                seen_ranges.append((start, end))
                windows.append(window)
        if len(windows) >= max_windows:
            break
    return windows


def _merge_section_pieces(pieces: list[str], *, limit: int) -> str:
    output: list[str] = []
    seen: set[str] = set()
    for piece in pieces:
        clean = _clean_section_text(piece, limit=limit)
        key = clean[:300].lower()
        if not clean or key in seen:
            continue
        seen.add(key)
        output.append(clean)
    return "\n\n".join(output)[:limit].strip()


def _clean_section_text(text: str, *, limit: int) -> str:
    clean = _normalize_text(text)
    lines = []
    for raw_line in clean.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or _looks_like_table_of_contents(line):
            continue
        lines.append(line)
    return "\n".join(lines)[:limit].strip()


def _has_section_body(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", text)
    if len(words) < 12:
        return False
    return any(len(word) >= 4 for word in words)


def _parsed_fields_payload(parsed: ParsedIpoFields) -> dict[str, Any]:
    payload = asdict(parsed)
    payload["underwriters"] = list(parsed.underwriters)
    payload["risk_flags"] = list(parsed.risk_flags)
    return payload


def _section_debug_payload(section: IpoFilingSourceSection) -> dict[str, Any]:
    return {
        "name": section.name,
        "character_length": len(section.text),
        "preview": _shorten(section.text, 300),
    }


def _section_debug_line(section: IpoFilingSourceSection) -> str:
    debug = _section_debug_payload(section)
    return f"{debug['name']} ({debug['character_length']} chars): {debug['preview']}"


def _validate_report_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise OpenAiIpoReportError("OpenAI IPO report response was not a JSON object.")
    missing = [field for field in IPO_REPORT_JSON_SCHEMA["required"] if field not in payload]
    if missing:
        raise OpenAiIpoReportError("OpenAI IPO report response was missing structured fields: " + ", ".join(missing))
    if not str(payload.get("markdown_report") or "").strip():
        raise OpenAiIpoReportError("OpenAI IPO report response did not include markdown_report.")


def _validate_section_map_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise OpenAiIpoReportError("OpenAI IPO section-map response was not a JSON object.")
    sections = payload.get("sections")
    if not isinstance(sections, list):
        raise OpenAiIpoReportError("OpenAI IPO section-map response did not include a sections list.")


def _response_output_text(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = getattr(response, "output", None)
    if isinstance(output, list):
        pieces: list[str] = []
        for item in output:
            content = _get_attr_or_key(item, "content")
            if not isinstance(content, list):
                continue
            for part in content:
                text = _get_attr_or_key(part, "text")
                if isinstance(text, str):
                    pieces.append(text)
        if pieces:
            return "".join(pieces)
    return ""


def _get_attr_or_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _business_summary_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "Not confidently extracted"
    summary = _field_text(value.get("summary"), fallback="Not confidently extracted")
    snippets = _string_list(value.get("source_snippets"))
    if snippets:
        return summary + "\n\nSource snippets: " + "; ".join(_quote_snippet(snippet) for snippet in snippets[:3])
    return summary


def _ipo_terms_lines(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["Not confidently extracted."]
    lines = [
        _labeled_sentence("Shares offered", value.get("shares_offered")),
        _labeled_sentence("Price range", value.get("price_range")),
        _labeled_sentence("Offering size", value.get("offering_size")),
        _labeled_sentence("Ticker / exchange", f"{_field_text(value.get('ticker'))} / {_field_text(value.get('exchange'))}"),
        _labeled_sentence("Underwriters", ", ".join(_string_list(value.get("underwriters"))) or "Not disclosed"),
        _labeled_sentence("Listing terms", value.get("listing_terms")),
    ]
    lines.extend(f"Source snippet: {_quote_snippet(snippet)}" for snippet in _string_list(value.get("source_snippets"))[:4])
    return _dedupe_lines(lines)


def _financial_summary_lines(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["Not confidently extracted."]
    lines = [
        _labeled_sentence("Revenue", value.get("revenue")),
        _labeled_sentence("Net income / loss", value.get("net_income_loss")),
        _labeled_sentence("Cash", value.get("cash")),
        _labeled_sentence("Debt", value.get("debt")),
        _field_text(value.get("summary"), fallback="Not confidently extracted"),
    ]
    lines.extend(f"Source snippet: {_quote_snippet(snippet)}" for snippet in _string_list(value.get("source_snippets"))[:4])
    return _dedupe_lines(lines)


def _use_of_proceeds_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "Not disclosed"
    summary = _field_text(value.get("summary"), fallback="Not disclosed")
    snippet = _field_text(value.get("source_snippet"), fallback="")
    return f"{summary}\n\nSource snippet: {_quote_snippet(snippet)}" if snippet else summary


def _risk_lines(values: Any) -> list[str]:
    if not isinstance(values, list):
        return ["Not confidently extracted."]
    lines: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        risk = _field_text(value.get("risk"), fallback="Not confidently extracted")
        why = _field_text(value.get("why_it_matters"), fallback="Not confidently extracted")
        snippet = _field_text(value.get("source_snippet"), fallback="")
        line = _labeled_sentence(risk, why)
        if snippet:
            line += f" Source snippet: {_quote_snippet(snippet)}"
        lines.append(line)
    return _dedupe_lines(lines) or ["Not confidently extracted."]


def _confidence_text(value: Any) -> str:
    if not isinstance(value, dict):
        return "Not confidently extracted"
    level = _field_text(value.get("level"), fallback="low").lower()
    explanation = _field_text(value.get("explanation"), fallback="Not confidently extracted")
    return f"{level}: {explanation}"


def _facts_metadata_payload(facts: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    output = {
        key: _field_text(facts.get(key), fallback="Not disclosed")
        for key in (
            "company_name",
            "form",
            "ticker",
            "exchange",
            "shares_offered",
            "price_range",
            "offering_size",
            "revenue",
            "cash",
            "debt",
        )
    }
    snippets = _string_list(facts.get("source_snippets"))
    if snippets:
        output["source_snippets"] = snippets[:6]
    not_disclosed = _pretty_label_list(payload.get("not_disclosed_fields"))
    not_confident = _pretty_label_list(payload.get("not_confidently_extracted_fields"))
    if not_disclosed:
        output["not_disclosed"] = not_disclosed
    if not_confident:
        output["not_confidently_extracted"] = not_confident
    return output


def _clean_model_markdown(value: str) -> str:
    clean = value.strip()
    clean = re.sub(r"^```(?:markdown|md)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    clean = re.sub(r"(?m)^# .+\n+", "", clean, count=1)
    return _normalize_markdown_punctuation(clean).strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        clean = _field_text(item, fallback="")
        if clean:
            output.append(clean)
    return output


def _pretty_label_list(value: Any) -> list[str]:
    return _dedupe_lines([_pretty_label(item) for item in _string_list(value)])


def _pretty_label(value: str) -> str:
    clean = re.sub(r"[_\-]+", " ", value or "")
    clean = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    if not clean:
        return ""
    words = clean.lower().split()
    acronym_map = {"ipo": "IPO", "sec": "SEC", "cik": "CIK", "md&a": "MD&A", "nyse": "NYSE", "nasdaq": "Nasdaq"}
    pretty_words = [acronym_map.get(word, word) for word in words]
    pretty_words[0] = pretty_words[0] if pretty_words[0].isupper() else pretty_words[0].capitalize()
    return " ".join(pretty_words)


def _labeled_sentence(label: str, value: Any, *, fallback: str = "Not disclosed") -> str:
    return f"{label}: {_sentence_text(_field_text(value, fallback=fallback))}"


def _sentence_text(value: str) -> str:
    clean = _normalize_punctuation(value)
    return clean if clean.endswith((".", "!", "?")) else clean + "."


def _field_text(value: Any, *, fallback: str = "Not disclosed") -> str:
    clean = _normalize_punctuation(str(value or "")).strip(" -")
    return _shorten(clean, 700) if clean else fallback


def _quote_snippet(value: str) -> str:
    clean = _shorten(_field_text(value, fallback=""), 280)
    return f'"{clean}"' if clean else '"Not disclosed"'


def _dedupe_lines(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = _normalize_punctuation(value).strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def _normalize_punctuation(value: str) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip()
    clean = re.sub(r"\s+([,.;:!?])", r"\1", clean)
    clean = re.sub(r"([.!?]){2,}", r"\1", clean)
    clean = re.sub(r"([,;:])([.!?])", r"\2", clean)
    return clean


def _normalize_markdown_punctuation(value: str) -> str:
    lines = [_normalize_punctuation(line) if line.strip() else "" for line in (value or "").splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _shorten(value: str, limit: int) -> str:
    clean = _normalize_punctuation(value)
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip(" ,.;") + "..."


def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, parsed))


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _redact_api_key(message: str, api_key: str) -> str:
    redacted = message
    if api_key:
        redacted = redacted.replace(api_key, "[REDACTED]")
    redacted = re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[REDACTED]", redacted)
    return redacted


def _normalize_text(text: str) -> str:
    text = re.sub(r"[\t\r\f\v]+", " ", text or "")
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


def _looks_like_toc_window(value: str) -> bool:
    lower = value.lower()
    return "table of contents" in lower or len(re.findall(r"\b[A-Z][A-Z '&/-]{3,}\s+\d{1,3}\b", value)) >= 3


def _looks_like_table_of_contents(value: str) -> bool:
    lower = value.lower()
    return (
        "table of contents" in lower
        or bool(re.search(r"\.{4,}\s*\d+$", value))
        or len(re.findall(r"\b\d+\b", value)) > 16
        or len(re.findall(r"\b[A-Z][A-Z '&/-]{3,}\s+\d{1,3}\b", value)) >= 3
    )


def _safe_cik(cik: str) -> str:
    try:
        return normalize_cik(cik)
    except ValueError:
        digits = re.sub(r"\D", "", str(cik or ""))
        return digits.zfill(10) if digits else "0000000000"


def _primary_document_from_url(url: str) -> str:
    if not url:
        return ""
    name = url.rstrip("/").rsplit("/", 1)[-1]
    return "" if name.endswith("-index.htm") else name
