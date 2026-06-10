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
    IpoReportPaths,
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


@dataclass(frozen=True)
class IpoFilingSourceSection:
    name: str
    text: str


@dataclass(frozen=True)
class IpoFilingSourceBundle:
    metadata: dict[str, Any]
    deterministic_extracts: dict[str, Any]
    sections: tuple[IpoFilingSourceSection, ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata,
            "deterministic_extracts": self.deterministic_extracts,
            "sections": [asdict(section) for section in self.sections],
        }


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
        "headline_bullets",
        "business_summary",
        "ipo_terms",
        "financial_summary",
        "use_of_proceeds",
        "key_risks",
        "bull_case",
        "bear_case",
        "final_key_question",
        "confidence",
        "not_disclosed_fields",
        "not_confidently_extracted_fields",
    ],
    "properties": {
        "headline_bullets": STRING_ARRAY_SCHEMA,
        "business_summary": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "source_snippets"],
            "properties": {
                "summary": {"type": "string"},
                "source_snippets": SOURCE_SNIPPETS_SCHEMA,
            },
        },
        "ipo_terms": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "shares_offered",
                "price_range",
                "offering_size",
                "ticker",
                "exchange",
                "underwriters",
                "listing_terms",
                "source_snippets",
            ],
            "properties": {
                "shares_offered": {"type": "string"},
                "price_range": {"type": "string"},
                "offering_size": {"type": "string"},
                "ticker": {"type": "string"},
                "exchange": {"type": "string"},
                "underwriters": STRING_ARRAY_SCHEMA,
                "listing_terms": {"type": "string"},
                "source_snippets": SOURCE_SNIPPETS_SCHEMA,
            },
        },
        "financial_summary": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "revenue",
                "net_income_loss",
                "cash",
                "debt",
                "summary",
                "source_snippets",
            ],
            "properties": {
                "revenue": {"type": "string"},
                "net_income_loss": {"type": "string"},
                "cash": {"type": "string"},
                "debt": {"type": "string"},
                "summary": {"type": "string"},
                "source_snippets": SOURCE_SNIPPETS_SCHEMA,
            },
        },
        "use_of_proceeds": {
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "source_snippet"],
            "properties": {
                "summary": {"type": "string"},
                "source_snippet": {"type": "string"},
            },
        },
        "key_risks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["risk", "why_it_matters", "source_snippet"],
                "properties": {
                    "risk": {"type": "string"},
                    "why_it_matters": {"type": "string"},
                    "source_snippet": {"type": "string"},
                },
            },
        },
        "bull_case": STRING_ARRAY_SCHEMA,
        "bear_case": STRING_ARRAY_SCHEMA,
        "final_key_question": {"type": "string"},
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


IPO_REPORT_SYSTEM_PROMPT = """Generate an IPO filing report from the provided SEC filing source bundle.

Rules:
- Use only the provided JSON metadata, deterministic extracts, and cleaned source sections.
- Treat deterministic extracts as derived only from the provided filing sections.
- Say exactly "Not disclosed" when a requested field is absent from the source.
- Say exactly "Not confidently extracted" when the source is ambiguous or too thin.
- Do not invent offering size, price range, revenue, cash, debt, market cap, listing terms, or exchange/ticker details.
- Include short source snippets for important claims. Snippets must come from the provided sections.
- Never include credentials, API keys, or secrets.
- Return only JSON that matches the schema.
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
                temperature=0,
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

    bundle = build_ipo_filing_source_bundle(
        record,
        filing_text,
        source_url=source_url,
        source_filing=source_filing,
        source_document=source_document,
    )
    client = OpenAiIpoReportClient(openai_client=openai_client, model=model)
    payload = client.generate_report(bundle)
    base_report = build_ipo_filing_report(
        record,
        filing_text,
        source_url=source_url,
        source_filing=source_filing,
        source_document=source_document,
    )
    report = _report_from_openai_payload(base_report, payload, model=client.model, source_section_names=tuple(section.name for section in bundle.sections))
    return write_ipo_filing_report(report, paths)


def build_ipo_filing_source_bundle(
    record: IpoPipelineRecord,
    filing_text: str,
    *,
    source_url: str = "",
    source_filing: SecCurrentFiling | None = None,
    source_document: IpoDocumentCandidate | None = None,
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
    return IpoFilingSourceBundle(
        metadata=metadata,
        deterministic_extracts=deterministic_extracts,
        sections=tuple(_cleaned_source_sections(normalized)),
    )


def _report_from_openai_payload(
    base: IpoFilingReport,
    payload: dict[str, Any],
    *,
    model: str,
    source_section_names: tuple[str, ...],
) -> IpoFilingReport:
    business = _business_summary_text(payload["business_summary"])
    return replace(
        base,
        business_description=business,
        why_interesting=(),
        traction=(),
        financial_rows=(),
        key_risks=tuple(_risk_lines(payload["key_risks"])),
        balance_sheet=(),
        dilution=(),
        notable_terms=(),
        bull_case=tuple(_string_list(payload["bull_case"])),
        bear_case=tuple(_string_list(payload["bear_case"])),
        final_key_question=_field_text(payload["final_key_question"], fallback="Not confidently extracted"),
        raw_excerpt="",
        generation_method="openai",
        headline_bullets=tuple(_string_list(payload["headline_bullets"])),
        ipo_terms=tuple(_ipo_terms_lines(payload["ipo_terms"])),
        financial_summary=tuple(_financial_summary_lines(payload["financial_summary"])),
        use_of_proceeds_summary=_use_of_proceeds_text(payload["use_of_proceeds"]),
        confidence=_confidence_text(payload["confidence"]),
        not_disclosed_fields=tuple(_string_list(payload["not_disclosed_fields"])),
        not_confidently_extracted_fields=tuple(_string_list(payload["not_confidently_extracted_fields"])),
        model_name=model,
        source_section_names=source_section_names,
    )


def _cleaned_source_sections(text: str) -> list[IpoFilingSourceSection]:
    specs = (
        (
            "prospectus_summary",
            ("Prospectus Summary", "Summary"),
            ("Risk Factors", "The Offering", "Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Management"),
            9000,
        ),
        (
            "business",
            ("Business Overview", "Our Business", "Our Company", "Business"),
            ("Risk Factors", "Management", "Management's Discussion", "Use of Proceeds", "Financial Statements"),
            9000,
        ),
        (
            "offering",
            ("The Offering", "Offering", "Initial Public Offering"),
            ("Risk Factors", "Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Underwriting", "Management"),
            7000,
        ),
        (
            "use_of_proceeds",
            ("Use of Proceeds",),
            ("Dividend Policy", "Capitalization", "Dilution", "Management", "Underwriting", "Risk Factors", "Business"),
            5000,
        ),
        (
            "financials",
            (
                "Selected Consolidated Financial Data",
                "Selected Consolidated Statements of Operations",
                "Selected Financial Data",
                "Results of Operations",
                "Management's Discussion and Analysis",
            ),
            ("Liquidity", "Capital Resources", "Business", "Risk Factors", "Management", "Financial Statements"),
            10000,
        ),
        (
            "capitalization_and_dilution",
            ("Capitalization", "Dilution"),
            ("Management", "Underwriting", "Plan of Distribution", "Financial Statements", "Business"),
            8000,
        ),
        (
            "risk_factors",
            ("Risk Factors", "Going Concern"),
            ("Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Management", "Business", "Underwriting"),
            12000,
        ),
        (
            "underwriting",
            ("Underwriting", "Plan of Distribution"),
            ("Legal Matters", "Experts", "Financial Statements", "Where You Can Find More Information"),
            6000,
        ),
    )
    sections: list[IpoFilingSourceSection] = []
    seen_text: set[str] = set()
    for name, headings, stops, limit in specs:
        section = _section_between_headings(text, headings, stops, limit=limit)
        section = _clean_section_text(section, limit=limit)
        key = section[:500].lower()
        if section and key not in seen_text:
            seen_text.add(key)
            sections.append(IpoFilingSourceSection(name=name, text=section))

    if not sections:
        sections.append(IpoFilingSourceSection(name="opening_excerpt", text=_clean_section_text(text[:12000], limit=12000)))
    return sections


def _section_between_headings(text: str, headings: tuple[str, ...], stop_headings: tuple[str, ...], *, limit: int) -> str:
    lower = text.lower()
    starts: list[tuple[int, int]] = []
    for heading in headings:
        for match in re.finditer(rf"\b{re.escape(heading.lower())}\b", lower):
            starts.append((match.start(), match.end()))
    for start, content_start in sorted(starts, key=lambda item: item[0]):
        if _looks_like_toc_window(text[max(0, start - 160) : start + 320]):
            continue
        stop_index = len(text)
        for stop in stop_headings:
            match = re.search(rf"\b{re.escape(stop.lower())}\b", lower[content_start + 200 :])
            if match:
                stop_index = min(stop_index, content_start + 200 + match.start())
        section = text[content_start:stop_index].strip()
        if section:
            return section[:limit]
    return ""


def _clean_section_text(text: str, *, limit: int) -> str:
    clean = _normalize_text(text)
    lines = []
    for raw_line in clean.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or _looks_like_table_of_contents(line):
            continue
        lines.append(line)
    return "\n".join(lines)[:limit].strip()


def _parsed_fields_payload(parsed: ParsedIpoFields) -> dict[str, Any]:
    payload = asdict(parsed)
    payload["underwriters"] = list(parsed.underwriters)
    payload["risk_flags"] = list(parsed.risk_flags)
    return payload


def _validate_report_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise OpenAiIpoReportError("OpenAI IPO report response was not a JSON object.")
    missing = [field for field in IPO_REPORT_JSON_SCHEMA["required"] if field not in payload]
    if missing:
        raise OpenAiIpoReportError("OpenAI IPO report response was missing structured fields: " + ", ".join(missing))


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
        f"Shares offered: {_field_text(value.get('shares_offered'))}.",
        f"Price range: {_field_text(value.get('price_range'))}.",
        f"Offering size: {_field_text(value.get('offering_size'))}.",
        f"Ticker / exchange: {_field_text(value.get('ticker'))} / {_field_text(value.get('exchange'))}.",
        f"Underwriters: {', '.join(_string_list(value.get('underwriters'))) or 'Not disclosed'}.",
        f"Listing terms: {_field_text(value.get('listing_terms'))}.",
    ]
    lines.extend(f"Source snippet: {_quote_snippet(snippet)}" for snippet in _string_list(value.get("source_snippets"))[:4])
    return _dedupe_lines(lines)


def _financial_summary_lines(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["Not confidently extracted."]
    lines = [
        f"Revenue: {_field_text(value.get('revenue'))}.",
        f"Net income / loss: {_field_text(value.get('net_income_loss'))}.",
        f"Cash: {_field_text(value.get('cash'))}.",
        f"Debt: {_field_text(value.get('debt'))}.",
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
        line = f"{risk}: {why}"
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


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        clean = _field_text(item, fallback="")
        if clean:
            output.append(clean)
    return output


def _field_text(value: Any, *, fallback: str = "Not disclosed") -> str:
    clean = re.sub(r"\s+", " ", str(value or "")).strip(" -")
    return _shorten(clean, 700) if clean else fallback


def _quote_snippet(value: str) -> str:
    clean = _shorten(_field_text(value, fallback=""), 280)
    return f'"{clean}"' if clean else '"Not disclosed"'


def _dedupe_lines(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = re.sub(r"\s+", " ", value).strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def _shorten(value: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip(" ,.;") + "..."


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
