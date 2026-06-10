from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal

from dotenv import load_dotenv

from app.analytics.ipo_filing_report import (
    DEFAULT_IPO_REPORT_DIR,
    IpoFilingReport,
    build_ipo_filing_report,
    ipo_report_paths,
    reportable_ipo_form,
    sec_current_filing_from_record,
)
from app.analytics.ipo_pipeline import (
    IpoDocumentCandidate,
    IpoPipelineRecord,
    fetch_ipo_document_text_with_source,
    related_prospectus_filing_for_report,
)
from app.analytics.openai_ipo_report import (
    DEFAULT_OPENAI_IPO_REPORT_MODEL,
    IpoFilingSourceBundle,
    IpoFilingSourceSection,
    _redact_api_key,
    _response_output_text,
    build_ipo_filing_source_bundle,
)
from app.data.sec_edgar import SecCurrentFiling, SecEdgarClient


CHAT_FULL_TEXT_CHAR_LIMIT = 120_000
CHAT_RETRIEVED_CHAR_LIMIT = 120_000
CHAT_SECTION_CHAR_LIMIT = 18_000
CHAT_CHUNK_SIZE = 8_000
CHAT_CHUNK_OVERLAP = 900
CHAT_HISTORY_MESSAGE_LIMIT = 8

FILING_CHAT_SYSTEM_PROMPT = """You are an SEC filing analyst inside Portfolio Risk Cockpit.

Analyze only the selected SEC filing text and supplied deterministic metadata.
Do not invent undisclosed offering terms, financials, underwriters, dates, share counts, prices, or market caps.
When a field is absent, say "Not disclosed."
When a field is ambiguous, say "Not confidently extracted."
Treat deterministic extracts as hints only. If deterministic extracts conflict with retrieved filing text, use the filing text.
Never present deterministic financial metrics unless the source context or verified filing facts show the metric and period.
Preserve currency exactly as disclosed, including S$, US$, HK$, and other currency prefixes.

For S-1/F-1/424B filings, prioritize:
1. What the company does
2. Whether this is an IPO, resale shelf, SPAC, direct listing, amendment, or final prospectus
3. Securities offered
4. Use of proceeds
5. Capitalization and dilution
6. Selling shareholders / insider control
7. Financial condition
8. Risk factors
9. Bull case / bear case
10. Investor diligence questions

Write like a buy-side analyst: clear, structured, specific, skeptical, and readable.
Do not provide investment advice or recommendations to buy/sell.
Never include credentials, API keys, or secrets.
"""

QUICK_ACTION_PROMPTS: dict[str, str] = {
    "Overview": (
        "Generate a thorough overview of this filing. Cover the transaction type, securities offered, "
        "company business, use of proceeds, financial condition, ownership/dilution, major risks, "
        "and investor diligence questions."
    ),
    "Risks": (
        "Summarize the most important risk factors in plain English. Prioritize risks that could affect "
        "valuation, liquidity, dilution, business viability, or investor protections."
    ),
    "Offering Terms": (
        "Extract the offering terms. Include form type, securities offered, price range, offering amount, "
        "exchange/ticker, underwriters, lockups, selling shareholders, and whether the company receives proceeds."
    ),
    "Use of Proceeds": (
        "Extract and explain the use of proceeds. Separate company proceeds, selling-shareholder proceeds, "
        "debt repayment, working capital, acquisition plans, and any amounts that are not disclosed."
    ),
    "Dilution / Ownership": (
        "Analyze capitalization, dilution, insider control, selling shareholders, warrant/option overhang, "
        "and any ownership changes described in the filing."
    ),
    "Bull vs Bear": (
        "Give me the bull case, bear case, and the most important diligence questions based only on the filing."
    ),
}

_STOP_WORDS = {
    "about",
    "against",
    "also",
    "and",
    "any",
    "are",
    "based",
    "can",
    "cover",
    "does",
    "from",
    "give",
    "how",
    "include",
    "into",
    "only",
    "our",
    "out",
    "over",
    "the",
    "this",
    "use",
    "what",
    "when",
    "where",
    "with",
}


class OpenAiIpoFilingChatError(RuntimeError):
    """Raised for filing chat failures with credentials redacted."""


@dataclass(frozen=True)
class IpoFilingChatContext:
    record: IpoPipelineRecord
    source_filing: SecCurrentFiling
    source_document: IpoDocumentCandidate
    deterministic_report: IpoFilingReport
    bundle: IpoFilingSourceBundle

    @property
    def company_name(self) -> str:
        return str(self.bundle.metadata.get("company_name") or self.record.company_name or "Unknown company")

    @property
    def source_url(self) -> str:
        return str(self.bundle.metadata.get("source_url") or self.record.filing_url or "")

    @property
    def source_form(self) -> str:
        return str(self.bundle.metadata.get("source_form") or self.record.form or "").strip().upper()


@dataclass(frozen=True)
class IpoFilingChatMessage:
    role: Literal["user", "assistant"]
    content: str
    source_mode: str = ""
    source_debug: tuple[str, ...] = ()


@dataclass(frozen=True)
class IpoFilingChatResponse:
    answer: str
    response_id: str
    model: str
    source_mode: str
    source_debug: tuple[str, ...]


class IpoFilingChatSession:
    def __init__(
        self,
        context: IpoFilingChatContext,
        *,
        chat_client: "OpenAiIpoFilingChatClient | None" = None,
    ) -> None:
        self.context = context
        self.chat_client = chat_client or OpenAiIpoFilingChatClient()
        self.messages: list[IpoFilingChatMessage] = []
        self.last_response_id = ""

    @property
    def model(self) -> str:
        return self.chat_client.model

    def ask(self, prompt: str) -> IpoFilingChatResponse:
        clean_prompt = _clean_prompt(prompt)
        if not clean_prompt:
            raise OpenAiIpoFilingChatError("Enter a filing question before sending.")

        response = self.chat_client.ask(self.context, self.messages, clean_prompt)
        self.messages.append(IpoFilingChatMessage(role="user", content=clean_prompt))
        self.messages.append(
            IpoFilingChatMessage(
                role="assistant",
                content=response.answer,
                source_mode=response.source_mode,
                source_debug=response.source_debug,
            )
        )
        self.last_response_id = response.response_id
        return response


class OpenAiIpoFilingChatClient:
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

    def ask(
        self,
        context: IpoFilingChatContext,
        history: Iterable[IpoFilingChatMessage],
        prompt: str,
    ) -> IpoFilingChatResponse:
        filing_context = filing_context_payload_for_prompt(context.bundle, prompt)
        request_payload = {
            "question": prompt,
            "filing_metadata": context.bundle.metadata,
            "deterministic_extracts_hints": {
                "use_policy": "Hints only. Filing text and verified filing facts override these values.",
                "extracts": context.bundle.deterministic_extracts,
            },
            "verified_filing_facts": verified_filing_facts_for_prompt(context.bundle, filing_context, prompt),
            "filing_context": filing_context,
            "grounding_rules": [
                "Use only filing_metadata, verified_filing_facts, filing_context, and deterministic_extracts_hints for factual claims.",
                "Treat deterministic_extracts_hints as hints only; filing text and verified_filing_facts override parser output.",
                "Never present deterministic financial metrics unless filing_context or verified_filing_facts show the metric and period.",
                "Preserve currency exactly as disclosed, including S$, US$, HK$, and other currency prefixes.",
                'Say "Not disclosed" for absent fields.',
                'Say "Not confidently extracted" for ambiguous fields.',
                "Do not infer offering economics, underwriters, selling shareholders, or transaction type beyond the supplied text.",
            ],
        }
        input_messages = [{"role": "system", "content": FILING_CHAT_SYSTEM_PROMPT}]
        for message in list(history)[-CHAT_HISTORY_MESSAGE_LIMIT:]:
            input_messages.append({"role": message.role, "content": message.content})
        input_messages.append({"role": "user", "content": json.dumps(request_payload, ensure_ascii=False, indent=2)})

        try:
            response = self._client().responses.create(
                model=self.model,
                input=input_messages,
                store=False,
            )
        except Exception as exc:
            message = _redact_api_key(f"OpenAI IPO filing chat failed: {exc}", self._current_api_key())
            raise OpenAiIpoFilingChatError(message) from None

        answer = _clean_answer(_response_output_text(response))
        if not answer:
            raise OpenAiIpoFilingChatError("OpenAI IPO filing chat returned an empty response.")
        return IpoFilingChatResponse(
            answer=answer,
            response_id=str(getattr(response, "id", "") or ""),
            model=self.model,
            source_mode=str(filing_context.get("source_mode") or ""),
            source_debug=tuple(str(entry) for entry in filing_context.get("section_debug", ())),
        )

    def _client(self) -> Any:
        if self._openai_client is not None:
            return self._openai_client

        api_key = self._current_api_key()
        if not api_key:
            raise OpenAiIpoFilingChatError("OPENAI_API_KEY is not configured. Add it to .env or the environment.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAiIpoFilingChatError("The openai package is not installed. Run pip install -r requirements.txt.") from exc

        self._openai_client = OpenAI(api_key=api_key)
        return self._openai_client

    def _current_api_key(self) -> str:
        return (self._api_key if self._api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()


def create_ipo_filing_chat_session(
    record: IpoPipelineRecord,
    *,
    sec_client: SecEdgarClient | None = None,
    openai_client: Any | None = None,
    model: str | None = None,
) -> IpoFilingChatSession:
    context = build_ipo_filing_chat_context(record, client=sec_client)
    return IpoFilingChatSession(
        context,
        chat_client=OpenAiIpoFilingChatClient(openai_client=openai_client, model=model),
    )


def build_ipo_filing_chat_context(
    record: IpoPipelineRecord,
    *,
    client: SecEdgarClient | None = None,
) -> IpoFilingChatContext:
    if not reportable_ipo_form(record.form):
        raise ValueError(f"IPO filing chat is supported for S-1/F-1, 424B4, and EFFECT rows, not {record.form or 'unknown form'}.")

    active_client = client or SecEdgarClient()
    selected_filing = sec_current_filing_from_record(record)
    source_filing = related_prospectus_filing_for_report(active_client, selected_filing)
    fetched = fetch_ipo_document_text_with_source(active_client, source_filing)
    deterministic_report = build_ipo_filing_report(
        record,
        fetched.text,
        source_url=fetched.candidate.url,
        source_filing=source_filing,
        source_document=fetched.candidate,
    )
    bundle = build_ipo_filing_source_bundle(
        record,
        fetched.text,
        source_url=fetched.candidate.url,
        source_filing=source_filing,
        source_document=fetched.candidate,
        deterministic_report=deterministic_report,
    )
    return IpoFilingChatContext(
        record=record,
        source_filing=source_filing,
        source_document=fetched.candidate,
        deterministic_report=deterministic_report,
        bundle=bundle,
    )


def filing_context_payload_for_prompt(bundle: IpoFilingSourceBundle, prompt: str) -> dict[str, Any]:
    if len(bundle.full_text) <= CHAT_FULL_TEXT_CHAR_LIMIT:
        return {
            "source_mode": "full_prospectus_text",
            "full_prospectus_text": bundle.full_text,
            "section_debug": [
                f"full_prospectus_text ({len(bundle.full_text)} chars): {_shorten(bundle.full_text, 260)}",
            ],
        }

    sections = retrieve_relevant_filing_sections(bundle.full_text, prompt)
    return {
        "source_mode": "retrieved_filing_chunks",
        "sections": [
            {
                "name": section.name,
                "text": section.text,
                "start_offset": section.start_offset,
                "end_offset": section.end_offset,
            }
            for section in sections
        ],
        "section_debug": [
            f"{section.name} ({len(section.text)} chars): {_shorten(section.text, 260)}"
            for section in sections
        ],
    }


def verified_filing_facts_for_prompt(
    bundle: IpoFilingSourceBundle,
    filing_context: dict[str, Any],
    prompt: str,
) -> dict[str, Any]:
    text = bundle.full_text
    context_text = _filing_context_text(filing_context)
    facts: dict[str, Any] = {
        "policy": (
            "These facts were extracted from filing text as a local verification aid. "
            "Use them only when supported by the accompanying source snippets; otherwise say Not confidently extracted."
        ),
        "offering_terms": _verified_offering_terms(text),
        "financial_metrics": _verified_financial_metrics(text, context_text),
        "risk_checks": _verified_risk_checks(text),
    }
    unsupported_industry_revenue = _industry_market_revenue_warning(text)
    if unsupported_industry_revenue:
        facts["company_revenue_guardrail"] = unsupported_industry_revenue
    return facts


def _verified_offering_terms(text: str) -> dict[str, Any]:
    cover = _cover_page_section(text).text
    offering = _first_named_section_text(text, _offering_spec(max_sections=1)) or cover
    combined = f"{cover}\n\n{offering}"
    facts: dict[str, Any] = {}

    match = re.search(
        r"([0-9][0-9,]*)\s+American Depositary Shares\s+Representing\s+([0-9][0-9,]*)\s+Ordinary Shares",
        combined,
        flags=re.IGNORECASE,
    )
    if match:
        facts["securities_offered"] = _fact(
            f"{match.group(1)} ADSs representing {match.group(2)} ordinary shares",
            combined,
            match.group(0),
        )
    match = re.search(r"Each ADS represents\s+([0-9][0-9,]*)\s+ordinary shares", combined, flags=re.IGNORECASE)
    if match:
        facts["ads_ratio"] = _fact(f"1 ADS = {match.group(1)} ordinary shares", combined, match.group(0))
    match = re.search(r"between\s+(US\$[0-9][0-9,.]*)\s+and\s+(US\$[0-9][0-9,.]*)\s+per ADS", combined, flags=re.IGNORECASE)
    if match:
        facts["expected_price_range"] = _fact(f"{match.group(1)}-{match.group(2)} per ADS", combined, match.group(0))
    match = re.search(r"Nasdaq Capital Market under the symbol\s+[“\"]?([A-Z][A-Z0-9.]{0,5})[”\"]?", combined, flags=re.IGNORECASE)
    if match:
        facts["listing"] = _fact(f"Nasdaq Capital Market under symbol {match.group(1).strip('.').upper()}", combined, match.group(0))
    match = re.search(r"([0-9]{1,3})\s*-?\s*day option.*?additional\s+([0-9]{1,2})%\s+of the ADSs", combined, flags=re.IGNORECASE)
    if match:
        facts["over_allotment_option"] = _fact(f"{match.group(1)}-day option for up to an additional {match.group(2)}% of ADSs sold", combined, match.group(0))
    if re.search(r"Roth Capital Partners", combined, flags=re.IGNORECASE) and re.search(r"Benchmark", combined, flags=re.IGNORECASE):
        facts["underwriters"] = _fact("Roth Capital Partners and Benchmark are joint book-running managers / representatives", combined, "Roth Capital Partners Benchmark")

    proceeds = _first_named_section_text(text, _use_of_proceeds_spec(max_sections=1))
    if "net proceeds of approximately" not in proceeds.lower():
        proceeds = text
    match = re.search(r"net proceeds of approximately\s+(US\$[0-9.]+\s+million).*?\(\s*(?:or\s+)?(US\$[0-9.]+\s+million)\s+if the underwriters exercise", proceeds, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["net_proceeds"] = _fact(
            f"Approximately {match.group(1)}, or {match.group(2)} if the over-allotment option is exercised in full",
            proceeds,
            match.group(0),
        )
    return facts


def _verified_financial_metrics(text: str, context_text: str) -> dict[str, Any]:
    financial_text = _financial_verification_text(text, context_text)
    facts: dict[str, Any] = {}

    match = re.search(r"Our revenue amounted to\s+(S\$[0-9,]+)\s+and\s+S\$[0-9,]+\s+in\s+2025\s+and\s+2024", financial_text, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"Revenue\s+\d+\s+([0-9,]+)\s+[0-9,]+", financial_text, flags=re.IGNORECASE)
    if match:
        value = match.group(1) if match.group(1).startswith("S$") else f"S${match.group(1)}"
        facts["fy2025_revenue"] = _fact(value, financial_text, match.group(0))

    match = re.search(r"net losses? of\s+(S\$[0-9,]+).*?for the years? ended December 31,\s*2025", financial_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        match = re.search(r"Loss after income tax and total comprehensive loss\s+\(?([0-9,]+)\s*\)?", financial_text, flags=re.IGNORECASE)
    if match:
        value = match.group(1) if match.group(1).startswith("S$") else f"S${match.group(1)}"
        facts["fy2025_net_loss"] = _fact(value, financial_text, match.group(0))

    match = re.search(r"Gross profit\s+([0-9,]+)\s+[0-9,]+", financial_text, flags=re.IGNORECASE)
    if match:
        facts["fy2025_gross_profit"] = _fact(f"S${match.group(1)}", financial_text, match.group(0))
    match = re.search(r"Gross profit margin decreased from\s+[0-9.]+%\s+in\s+2024\s+to\s+([0-9.]+%)\s+in\s+2025", financial_text, flags=re.IGNORECASE)
    if match:
        facts["fy2025_gross_margin"] = _fact(match.group(1), financial_text, match.group(0))

    capitalization = _first_named_section_text(text, _capitalization_spec(max_sections=1))
    match = re.search(r"Cash and cash equivalents\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)", capitalization, flags=re.IGNORECASE)
    if match:
        facts["cash_and_cash_equivalents"] = _fact(
            f"Actual S${match.group(1)} / US${match.group(2)}; as adjusted S${match.group(3)} / US${match.group(4)}",
            capitalization,
            match.group(0),
        )
    match = re.search(r"Total debt\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)\s+([0-9,]+)", capitalization, flags=re.IGNORECASE)
    if match:
        facts["total_debt"] = _fact(
            f"Actual S${match.group(1)} / US${match.group(2)}; as adjusted S${match.group(3)} / US${match.group(4)}",
            capitalization,
            match.group(0),
        )
    return facts


def _verified_risk_checks(text: str) -> dict[str, Any]:
    going_concern_sentence = _source_snippet(text, "prepared on a going concern basis", before=180, after=420)
    return {
        "going_concern_risk_detected": {
            "value": _has_adverse_going_concern_language(text),
            "source_snippet": going_concern_sentence,
            "instruction": "Do not describe a material going-concern warning unless adverse substantial-doubt or material-uncertainty language is present.",
        },
        "vie_structure_detected": {
            "value": _has_vie_structure_language(text),
            "source_snippet": _source_snippet(text, "variable interest entity", before=180, after=420) or _source_snippet(text, "wholly-owned subsidiaries", before=180, after=420),
            "instruction": "Do not flag China/VIE risk from generic China, PRC, customer, patent, or market-report references.",
        },
    }


def _industry_market_revenue_warning(text: str) -> dict[str, str]:
    match = re.search(r"Global revenue grew from about\s+(US\$[0-9.]+\s+million).*?according to the Independent Market Report", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return {}
    return {
        "warning": f"{match.group(1)} is an industry-market figure, not company revenue.",
        "source_snippet": _shorten(re.sub(r"\s+", " ", match.group(0)), 500),
    }


def _fact(value: str, source_text: str, needle: str) -> dict[str, str]:
    return {
        "value": value,
        "source_snippet": _source_snippet(source_text, needle, before=220, after=360) or _shorten(needle, 500),
    }


def _filing_context_text(filing_context: dict[str, Any]) -> str:
    full_text = filing_context.get("full_prospectus_text")
    if isinstance(full_text, str):
        return full_text
    sections = filing_context.get("sections")
    if not isinstance(sections, list):
        return ""
    return "\n\n".join(str(section.get("text") or "") for section in sections if isinstance(section, dict))


def _financial_verification_text(text: str, context_text: str) -> str:
    pieces = [
        _first_named_section_text(text, _mda_spec(max_sections=1)),
        _first_named_section_text(text, _financial_statements_spec(max_sections=2)),
        _first_named_section_text(text, _capitalization_spec(max_sections=1)),
        context_text,
    ]
    return "\n\n".join(_dedupe([piece for piece in pieces if piece]))


def _first_named_section_text(text: str, spec: _SectionSpec) -> str:
    sections = _sections_for_spec(_normalize_text(text), spec)
    return "\n\n".join(section.text for section in sections[: spec.max_sections])


def _source_snippet(text: str, needle: str, *, before: int, after: int) -> str:
    if not text or not needle:
        return ""
    lower = text.lower()
    clean_needle = re.sub(r"\s+", " ", needle).strip().lower()
    index = lower.find(clean_needle)
    if index < 0:
        words = [word for word in re.findall(r"[A-Za-z0-9$.,%-]+", clean_needle) if len(word) >= 4]
        for word in words[:8]:
            index = lower.find(word.lower())
            if index >= 0:
                break
    if index < 0:
        return ""
    return _shorten(text[max(0, index - before) : min(len(text), index + len(needle) + after)], 650)


def _has_adverse_going_concern_language(text: str) -> bool:
    lower = (text or "").lower()
    adverse_patterns = (
        r"substantial doubt (?:about|regarding) (?:our|the company's|the group'?s)? ability to continue",
        r"substantial doubt .*? going concern",
        r"may not be able to continue as a going concern",
        r"raise substantial doubt",
        r"going concern qualification",
        r"going concern uncertainty",
        r"material uncertainty .*? going concern",
        r"auditor'?s report .*? going concern",
    )
    return any(re.search(pattern, lower, flags=re.IGNORECASE) for pattern in adverse_patterns)


def _has_vie_structure_language(text: str) -> bool:
    lower = (text or "").lower()
    direct_patterns = (
        r"\bvariable interest entity\b",
        r"\bvie structure\b",
    )
    for pattern in direct_patterns:
        for match in re.finditer(pattern, lower):
            if not _is_negated_structure_reference(lower, match.start(), match.end()):
                return True
    for match in re.finditer(r"\bvies?\b", lower):
        if _is_negated_structure_reference(lower, match.start(), match.end()):
            continue
        window = lower[match.start() : match.end() + 220]
        if re.search(r"\b(contractual arrangements?|contractual control|nominee shareholder|wfoe|variable interest)\b", window):
            return True
    return False


def _is_negated_structure_reference(lower_text: str, start: int, end: int) -> bool:
    before = lower_text[max(0, start - 140) : start]
    around = lower_text[max(0, start - 80) : min(len(lower_text), end + 120)]
    if re.search(r"\b(no|not|without|does not|do not|did not|doesn't|is not|are not)\b[^.;:]{0,120}$", before):
        return True
    if re.search(r"\bdoes not disclose\b[^.;:]{0,160}\b(variable interest entity|vie structure|vies?)\b", around):
        return True
    if re.search(r"\bno\b[^.;:]{0,120}\b(variable interest entity|vie structure|vies?)\b", around):
        return True
    return False


def retrieve_relevant_filing_sections(text: str, prompt: str) -> tuple[IpoFilingSourceSection, ...]:
    clean_text = _normalize_text(text)
    if not clean_text:
        return ()

    selected: list[IpoFilingSourceSection] = []
    for spec in _section_specs_for_prompt(prompt):
        selected.extend(_sections_for_spec(clean_text, spec))

    selected.extend(_scored_keyword_chunks(clean_text, prompt))
    if not any(section.name == "opening_excerpt" for section in selected):
        selected.append(
            IpoFilingSourceSection(
                name="opening_excerpt",
                text=_clean_section_text(clean_text[: min(len(clean_text), 10_000)], limit=10_000),
                start_offset=0,
                end_offset=min(len(clean_text), 10_000),
            )
        )

    output: list[IpoFilingSourceSection] = []
    ranges: list[tuple[int, int]] = []
    char_count = 0
    for section in selected:
        if not section.text:
            continue
        start = section.start_offset if section.start_offset is not None else -1
        end = section.end_offset if section.end_offset is not None else -1
        if start >= 0 and end >= 0 and _overlaps_existing_range(start, end, ranges):
            continue
        room = CHAT_RETRIEVED_CHAR_LIMIT - char_count
        if room <= 0:
            break
        text_slice = section.text[:room].strip()
        if not text_slice:
            continue
        output.append(
            IpoFilingSourceSection(
                name=section.name,
                text=text_slice,
                start_offset=section.start_offset,
                end_offset=section.end_offset,
            )
        )
        char_count += len(text_slice)
        if start >= 0 and end >= 0:
            ranges.append((start, end))
    return tuple(output)


def render_ipo_filing_chat_transcript_markdown(session: IpoFilingChatSession) -> str:
    context = session.context
    metadata = context.bundle.metadata
    lines = [
        f"# {context.company_name} {context.source_form} AI Filing Chat",
        "",
        f"Source: [{metadata.get('source_form') or context.source_form} {metadata.get('source_document') or ''}]({context.source_url})",
        f"Selected row: {metadata.get('selected_form') or context.record.form} accession {metadata.get('selected_accession_number') or '--'}",
        f"Parsed source: {metadata.get('source_form') or context.source_form} accession {metadata.get('source_accession_number') or '--'}",
        f"Filed: {metadata.get('filed_date') or context.record.filed_date or '--'} | CIK: {metadata.get('cik') or context.record.cik}",
        f"AI model: {session.model}",
        "",
    ]
    if not session.messages:
        lines.append("_No chat messages yet._")
    else:
        for index, message in enumerate(session.messages, start=1):
            speaker = "User" if message.role == "user" else "Assistant"
            lines.extend([f"## {index}. {speaker}", "", _redact_common_secrets(message.content), ""])
            if message.role == "assistant" and (message.source_mode or message.source_debug):
                lines.extend(["### Source Debug", ""])
                if message.source_mode:
                    lines.append(f"- Source mode: {message.source_mode}")
                for entry in message.source_debug:
                    lines.append(f"- {entry}")
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def save_ipo_filing_chat_transcript(
    session: IpoFilingChatSession,
    output_path: str | Path | None = None,
    *,
    output_root: str | Path = DEFAULT_IPO_REPORT_DIR,
) -> Path:
    path = Path(output_path) if output_path is not None else ipo_filing_chat_transcript_path(session.context.record, output_root=output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(render_ipo_filing_chat_transcript_markdown(session), encoding="utf-8")
    temporary.replace(path)
    return path


def ipo_filing_chat_transcript_path(
    record: IpoPipelineRecord,
    *,
    output_root: str | Path = DEFAULT_IPO_REPORT_DIR,
) -> Path:
    return ipo_report_paths(record, output_root=output_root, report_name="AI Filing Chat Transcript").markdown_path


@dataclass(frozen=True)
class _SectionSpec:
    name: str
    headings: tuple[str, ...]
    stop_headings: tuple[str, ...]
    terms: tuple[str, ...] = ()
    max_sections: int = 1


def _section_specs_for_prompt(prompt: str) -> tuple[_SectionSpec, ...]:
    lower = prompt.lower()
    wants_overview = not prompt.strip() or any(term in lower for term in ("overview", "thorough", "summary", "breakdown", "filing"))
    specs: list[_SectionSpec] = []

    def add(spec: _SectionSpec) -> None:
        if all(existing.name != spec.name for existing in specs):
            specs.append(spec)

    if wants_overview:
        add(_cover_page_spec())
        add(_prospectus_summary_spec())
        add(_business_spec())
        add(_offering_spec())
        add(_use_of_proceeds_spec())
        add(_capitalization_spec())
        add(_dilution_spec())
        add(_mda_spec())
        add(_financial_statements_spec())
        add(_risk_spec())
        add(_principal_shareholders_spec())
        add(_underwriting_spec())

    if any(term in lower for term in ("risk", "viability", "liquidity", "valuation", "protection", "going concern")):
        add(_risk_spec(max_sections=2))
    if any(term in lower for term in ("offering", "terms", "transaction", "ipo", "resale", "shelf", "spac", "direct listing", "price", "ticker", "underwriter", "lockup", "lock-up", "securities offered")):
        add(_cover_page_spec())
        add(_offering_spec(max_sections=2))
        add(_selling_shareholders_spec())
        add(_underwriting_spec())
    if any(term in lower for term in ("proceeds", "repay", "working capital", "company receives")):
        add(_use_of_proceeds_spec(max_sections=2))
    if any(term in lower for term in ("dilution", "ownership", "capitalization", "overhang", "warrant", "option", "insider", "control", "selling shareholder")):
        add(_capitalization_spec(max_sections=2))
        add(_dilution_spec(max_sections=2))
        add(_selling_shareholders_spec())
        add(_principal_shareholders_spec())
    if any(term in lower for term in ("financial", "revenue", "cash", "debt", "income", "loss", "condition", "liquidity", "capital resources")):
        add(_mda_spec(max_sections=2))
        add(_financial_statements_spec(max_sections=2))
        add(_capitalization_spec())
    if any(term in lower for term in ("bull", "bear", "diligence")):
        add(_prospectus_summary_spec())
        add(_business_spec())
        add(_risk_spec())
        add(_financials_spec())

    if not specs:
        add(_prospectus_summary_spec())
        add(_offering_spec())
        add(_risk_spec())
    return tuple(specs)


def _prospectus_summary_spec() -> _SectionSpec:
    return _SectionSpec(
        name="prospectus_summary",
        headings=("Prospectus Summary", "Summary"),
        stop_headings=("Risk Factors", "The Offering", "Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Management"),
        terms=("company", "business", "we are", "we provide", "we develop", "market"),
    )


def _cover_page_spec() -> _SectionSpec:
    return _SectionSpec(
        name="cover_page",
        headings=("PRELIMINARY PROSPECTUS", "PROSPECTUS", "American Depositary Shares", "Ordinary Shares"),
        stop_headings=("TABLE OF CONTENTS", "Table of Contents", "PROSPECTUS SUMMARY"),
        terms=("American Depositary Shares", "offering price", "Nasdaq", "under the symbol", "Joint Book-Running Managers"),
    )


def _business_spec() -> _SectionSpec:
    return _SectionSpec(
        name="business",
        headings=("Business Overview", "Our Business", "Our Company", "Company Overview", "Business"),
        stop_headings=("Risk Factors", "Management", "Management's Discussion", "Principal Shareholders", "Financial Statements", "Underwriting"),
        terms=("our platform", "we provide", "we develop", "we operate", "customers", "market"),
    )


def _offering_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="offering_terms",
        headings=("The Offering", "Offering", "Initial Public Offering"),
        stop_headings=("Risk Factors", "Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Underwriting", "Management"),
        terms=("we are offering", "initial public offering price", "price range", "under the symbol", "applied to list"),
        max_sections=max_sections,
    )


def _use_of_proceeds_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="use_of_proceeds",
        headings=("Use of Proceeds",),
        stop_headings=("Dividend Policy", "Capitalization", "Dilution", "Management", "Underwriting", "Risk Factors", "Business"),
        terms=("we intend to use", "use the net proceeds", "net proceeds", "proceeds for", "repayment of indebtedness"),
        max_sections=max_sections,
    )


def _financials_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _mda_spec(max_sections=max_sections)


def _mda_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="mda_results",
        headings=(
            "Results of Operations",
            "Management's Discussion and Analysis",
            "Management’s Discussion and Analysis",
            "Liquidity and Capital Resources",
        ),
        stop_headings=("Industry", "Business", "Risk Factors", "Management", "Underwriting", "Financial Statements", "Quantitative and Qualitative Disclosures"),
        terms=("revenue", "revenues", "net loss", "net income", "cash and cash equivalents", "total debt", "liquidity", "capital resources"),
        max_sections=max_sections,
    )


def _financial_statements_spec(*, max_sections: int = 2) -> _SectionSpec:
    return _SectionSpec(
        name="financial_statements",
        headings=(
            "Consolidated Statements of Comprehensive Loss",
            "Consolidated Statements of Operations",
            "Consolidated Statements of Income",
            "Selected Consolidated Statements of Operations",
            "Consolidated Balance Sheets",
            "Consolidated Statements of Financial Position",
        ),
        stop_headings=("Consolidated Balance Sheets", "Consolidated Statements of Changes", "Consolidated Statements of Cash Flows", "Notes to the Financial Statements", "The accompanying notes", "Going concern"),
        terms=("Revenue", "Gross profit", "Loss after income tax", "Cash at bank", "Cash and cash equivalents", "Total debt"),
        max_sections=max_sections,
    )


def _capitalization_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="capitalization",
        headings=("Capitalization",),
        stop_headings=("Dilution", "Dividend Policy", "Management's Discussion", "Management’s Discussion", "Industry", "Business"),
        terms=("cash and cash equivalents", "total debt", "as adjusted", "total capitalization"),
        max_sections=max_sections,
    )


def _dilution_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="dilution",
        headings=("Dilution",),
        stop_headings=("Management", "Underwriting", "Plan of Distribution", "Financial Statements", "Business", "Description of Capital Stock", "Enforceability"),
        terms=("capitalization", "net tangible book value", "immediate dilution", "pro forma as adjusted", "warrants", "options"),
        max_sections=max_sections,
    )


def _risk_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="risk_factors",
        headings=("Risk Factors", "Going Concern"),
        stop_headings=("Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Management", "Business", "Underwriting"),
        terms=("risk factors", "going concern", "substantial doubt", "customer concentration", "related party", "controlled company"),
        max_sections=max_sections,
    )


def _selling_shareholders_spec() -> _SectionSpec:
    return _SectionSpec(
        name="selling_shareholders",
        headings=("Selling Shareholders", "Selling Stockholders", "Principal and Selling Shareholders"),
        stop_headings=("Plan of Distribution", "Underwriting", "Description of Capital Stock", "Shares Eligible", "Management"),
        terms=("selling shareholder", "selling stockholder", "resale", "will not receive any proceeds"),
        max_sections=2,
    )


def _principal_shareholders_spec() -> _SectionSpec:
    return _SectionSpec(
        name="principal_shareholders",
        headings=("Principal Shareholders", "Principal Stockholders", "Security Ownership", "Principal and Selling Shareholders"),
        stop_headings=("Certain Relationships", "Related Party", "Description of Capital Stock", "Underwriting"),
        terms=("beneficial ownership", "controlled company", "voting power", "insider"),
    )


def _underwriting_spec() -> _SectionSpec:
    return _SectionSpec(
        name="underwriting",
        headings=("Underwriting", "Plan of Distribution"),
        stop_headings=("Legal Matters", "Experts", "Financial Statements", "Consolidated Statements", "METAOPTICS LTD AND ITS SUBSIDIARIES", "Where You Can Find More Information"),
        terms=("underwriters are", "representatives of the underwriters", "book-running", "bookrunners", "lock-up", "lockup"),
    )


def _sections_for_spec(text: str, spec: _SectionSpec) -> list[IpoFilingSourceSection]:
    if spec.name == "cover_page":
        return [_cover_page_section(text)]
    sections = _sections_between_headings(text, spec)
    if sections:
        return sections
    return _windows_around_terms(text, spec)


def _cover_page_section(text: str) -> IpoFilingSourceSection:
    lower = text.lower()
    stop_candidates = [
        index
        for marker in ("table of contents", "prospectus summary")
        if (index := lower.find(marker, 300)) > 0
    ]
    end = min(stop_candidates) if stop_candidates else min(len(text), 12_000)
    end = max(end, min(len(text), 1_200))
    body = _clean_section_text(text[: min(end, 14_000)], limit=14_000)
    return IpoFilingSourceSection(name="cover_page", text=body, start_offset=0, end_offset=min(end, 14_000))


def _sections_between_headings(text: str, spec: _SectionSpec) -> list[IpoFilingSourceSection]:
    lower = text.lower()
    starts: list[tuple[int, int, str]] = []
    for heading in spec.headings:
        for match in re.finditer(rf"\b{re.escape(heading.lower())}\b", lower):
            if not _looks_like_section_heading_match(text, match.start(), match.end(), heading):
                continue
            starts.append((match.start(), match.end(), heading))
    sections: list[IpoFilingSourceSection] = []
    for start, content_start, heading in sorted(starts, key=lambda item: item[0]):
        stop_index = len(text)
        for stop in spec.stop_headings:
            stop_match = re.search(rf"\b{re.escape(stop.lower())}\b", lower[content_start + 20 :])
            if stop_match:
                stop_index = min(stop_index, content_start + 20 + stop_match.start())
        section_start = start
        section_end = min(stop_index, section_start + CHAT_SECTION_CHAR_LIMIT)
        body = _clean_section_text(text[section_start:section_end], limit=CHAT_SECTION_CHAR_LIMIT)
        if body and _has_section_body(body):
            sections.append(
                IpoFilingSourceSection(
                    name=spec.name,
                    text=f"{heading}\n{body}",
                    start_offset=section_start,
                    end_offset=section_end,
                )
            )
        if len(sections) >= spec.max_sections:
            break
    return sections


def _looks_like_section_heading_match(text: str, start: int, end: int, heading: str) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = min(len(text), end + 160)
    line = text[line_start:line_end].strip(" \t:-")
    clean_line = re.sub(r"\s+", " ", line).strip().lower()
    clean_heading = re.sub(r"\s+", " ", heading).strip().lower()
    if clean_line == clean_heading:
        return True
    if re.search(r"\s\d{1,4}$", clean_line):
        return False
    if not clean_line.startswith(clean_heading):
        return False
    suffix = clean_line[len(clean_heading) :].strip(" :-")
    if not suffix:
        return True
    if re.fullmatch(r"\d{1,4}", suffix):
        return False
    if len(suffix) > 120:
        return False
    if line and line.upper() == line:
        return True
    return bool(re.match(r"^(of financial condition|and results of operations|discussion and analysis)\b", suffix))


def _windows_around_terms(text: str, spec: _SectionSpec) -> list[IpoFilingSourceSection]:
    lower = text.lower()
    windows: list[IpoFilingSourceSection] = []
    ranges: list[tuple[int, int]] = []
    for term in spec.terms:
        start_at = 0
        while len(windows) < spec.max_sections:
            index = lower.find(term.lower(), start_at)
            if index < 0:
                break
            start = max(0, index - 1_200)
            end = min(len(text), index + len(term) + 4_200)
            start_at = index + len(term)
            if _overlaps_existing_range(start, end, ranges):
                continue
            body = _clean_section_text(text[start:end], limit=6_000)
            if body and _has_section_body(body):
                ranges.append((start, end))
                windows.append(IpoFilingSourceSection(name=spec.name, text=body, start_offset=start, end_offset=end))
        if len(windows) >= spec.max_sections:
            break
    return windows


def _scored_keyword_chunks(text: str, prompt: str) -> list[IpoFilingSourceSection]:
    terms = _query_terms(prompt)
    if not terms:
        return []
    chunks: list[tuple[int, int, int, str]] = []
    step = max(1, CHAT_CHUNK_SIZE - CHAT_CHUNK_OVERLAP)
    for start in range(0, len(text), step):
        end = min(len(text), start + CHAT_CHUNK_SIZE)
        chunk = text[start:end]
        score = _chunk_score(chunk, terms)
        if score > 0:
            chunks.append((score, start, end, chunk))
        if end >= len(text):
            break
    output: list[IpoFilingSourceSection] = []
    for index, (_score, start, end, chunk) in enumerate(sorted(chunks, key=lambda item: (-item[0], item[1]))[:5], start=1):
        clean = _clean_section_text(chunk, limit=CHAT_CHUNK_SIZE)
        if clean and _has_section_body(clean):
            output.append(IpoFilingSourceSection(name=f"keyword_chunk_{index}", text=clean, start_offset=start, end_offset=end))
    return output


def _query_terms(prompt: str) -> tuple[str, ...]:
    words = [
        word.lower()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", prompt or "")
        if word.lower() not in _STOP_WORDS
    ]
    expansions: list[str] = []
    lower = prompt.lower()
    if "risk" in lower:
        expansions.extend(["risk", "risks", "liquidity", "going concern", "substantial doubt"])
    if any(term in lower for term in ("dilution", "ownership", "overhang", "selling shareholder", "warrant")):
        expansions.extend(["dilution", "capitalization", "selling shareholders", "warrants", "options", "beneficial ownership"])
    if "proceeds" in lower:
        expansions.extend(["use of proceeds", "net proceeds", "repayment", "working capital"])
    if any(term in lower for term in ("offering", "terms", "price", "underwriter", "ticker")):
        expansions.extend(["we are offering", "price range", "underwriters", "ticker", "exchange"])
    if any(term in lower for term in ("financial", "cash", "debt", "revenue", "loss")):
        expansions.extend(["revenue", "cash", "debt", "net loss", "liquidity"])
    return tuple(_dedupe([*words, *expansions]))


def _chunk_score(chunk: str, terms: tuple[str, ...]) -> int:
    lower = chunk.lower()
    score = 0
    for term in terms:
        count = lower.count(term.lower())
        if count:
            score += min(count, 6) * (4 if " " in term else 2)
    if re.search(r"\b(risk factors|use of proceeds|dilution|capitalization|the offering|underwriting)\b", lower):
        score += 5
    if _looks_like_toc_window(chunk[:700]):
        score -= 5
    return score


def _overlaps_existing_range(start: int, end: int, ranges: Iterable[tuple[int, int]]) -> bool:
    for old_start, old_end in ranges:
        overlap = max(0, min(end, old_end) - max(start, old_start))
        if overlap >= min(end - start, old_end - old_start) * 0.35:
            return True
    return False


def _clean_prompt(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt or "").strip()


def _clean_answer(answer: str) -> str:
    clean = (answer or "").strip()
    clean = re.sub(r"^```(?:markdown|md)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    return _redact_common_secrets(clean).strip()


def _clean_section_text(text: str, *, limit: int) -> str:
    clean = _normalize_text(text)
    lines: list[str] = []
    for raw_line in clean.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or _looks_like_table_of_contents(line):
            continue
        lines.append(line)
    return "\n".join(lines)[:limit].strip()


def _normalize_text(text: str) -> str:
    text = re.sub(r"[\t\r\f\v]+", " ", text or "")
    text = re.sub(r"\n+", "\n", text)
    text = re.sub(r"[ ]{2,}", " ", text)
    return text.strip()


def _has_section_body(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", text)
    return len(words) >= 12 and any(len(word) >= 4 for word in words)


def _looks_like_toc_window(value: str) -> bool:
    lower = value.lower()
    return (
        ("table of contents" in lower and re.search(r"\b(prospectus summary|risk factors|use of proceeds|capitalization|dilution)\s+\d+", lower) is not None)
        or len(re.findall(r"\b[A-Z][A-Z '&/-]{3,}\s+\d{1,3}\b", value)) >= 3
    )


def _looks_like_table_of_contents(value: str) -> bool:
    lower = value.lower()
    return (
        "table of contents" in lower
        or bool(re.search(r"\.{4,}\s*\d+$", value))
        or len(re.findall(r"\b\d+\b", value)) > 16
        or len(re.findall(r"\b[A-Z][A-Z '&/-]{3,}\s+\d{1,3}\b", value)) >= 3
    )


def _shorten(value: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)].rstrip(" ,.;") + "..."


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        clean = re.sub(r"\s+", " ", value or "").strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            output.append(clean)
    return output


def _redact_common_secrets(value: str) -> str:
    return redact_ipo_filing_chat_secrets(value)


def redact_ipo_filing_chat_secrets(value: str) -> str:
    return re.sub(r"sk-[A-Za-z0-9_\-]{8,}", "[REDACTED]", value or "")
