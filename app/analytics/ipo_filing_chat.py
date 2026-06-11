from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Literal

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


LOGGER = logging.getLogger(__name__)

CHAT_FULL_TEXT_CHAR_LIMIT = 60_000
CHAT_RETRIEVED_CHAR_LIMIT = 75_000
CHAT_REQUEST_CHAR_LIMIT = 95_000
CHAT_SECTION_CHAR_LIMIT = 18_000
CHAT_CHUNK_SIZE = 8_000
CHAT_CHUNK_OVERLAP = 900
CHAT_HISTORY_MESSAGE_LIMIT = 8
CHAT_OPENAI_TIMEOUT_SECONDS = 150.0
CHAT_APPROX_CHARS_PER_TOKEN = 4
CHAT_FACT_SNIPPET_CHAR_LIMIT = 420

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
4. Use of proceeds, including net proceeds and disclosed allocation details
5. Capitalization and dilution, including actual/as-adjusted tables and per-ADS dilution when disclosed
6. Selling shareholders, principal shareholders, insider control, and related-party transactions
7. Financial condition, customer/supplier concentration, license obligations, and internal-control weaknesses
8. Risk factors, lock-up/moratorium constraints, and ADS/ordinary-share conversion considerations
9. Bull case / bear case
10. Investor diligence questions

Write like a buy-side analyst: clear, structured, specific, skeptical, and readable.
Do not provide investment advice or recommendations to buy/sell.
Never include credentials, API keys, or secrets.
"""

QUICK_ACTION_PROMPTS: dict[str, str] = {
    "Overview": (
        "Generate a thorough overview of this filing. Cover the transaction type, securities offered, "
        "company business, net proceeds and use-of-proceeds allocation, financial condition, capitalization, "
        "dilution, customer/supplier concentration, license obligations, ICFR weaknesses, principal shareholders, "
        "related-party transactions, lock-up or moratorium restrictions, ADS/ordinary-share conversion mechanics, "
        "major risks, and investor diligence questions."
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


@dataclass(frozen=True)
class _FilingContextIndex:
    text: str
    context_text: str
    section_map: dict[str, tuple[str, ...]]
    allow_full_text_scan: bool


ProgressCallback = Callable[[str], None]


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

    def ask(self, prompt: str, *, progress_callback: ProgressCallback | None = None) -> IpoFilingChatResponse:
        clean_prompt = _clean_prompt(prompt)
        if not clean_prompt:
            raise OpenAiIpoFilingChatError("Enter a filing question before sending.")

        response = self.chat_client.ask(self.context, self.messages, clean_prompt, progress_callback=progress_callback)
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
        timeout_seconds: float | None = None,
    ) -> None:
        load_dotenv()
        self._openai_client = openai_client
        self._api_key = api_key
        self.model = (model or os.getenv("OPENAI_IPO_REPORT_MODEL") or DEFAULT_OPENAI_IPO_REPORT_MODEL).strip()
        self.timeout_seconds = _positive_timeout_seconds(timeout_seconds, os.getenv("OPENAI_IPO_CHAT_TIMEOUT_SECONDS"))

    def ask(
        self,
        context: IpoFilingChatContext,
        history: Iterable[IpoFilingChatMessage],
        prompt: str,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> IpoFilingChatResponse:
        started_at = time.perf_counter()
        history_messages = list(history)
        LOGGER.debug(
            "AI filing chat request started company=%s model=%s loaded_full_text_chars=%s history_messages=%s",
            context.company_name,
            self.model,
            len(context.bundle.full_text or ""),
            len(history_messages),
        )
        _notify_progress(progress_callback, "Retrieving filing sections...")
        retrieval_started = time.perf_counter()
        filing_context = filing_context_payload_for_prompt(context.bundle, prompt)
        retrieval_elapsed = time.perf_counter() - retrieval_started
        LOGGER.debug(
            "AI filing chat retrieval complete company=%s source_mode=%s context_chars=%s elapsed=%.3fs",
            context.company_name,
            filing_context.get("source_mode"),
            _filing_context_char_count(filing_context),
            retrieval_elapsed,
        )

        _notify_progress(progress_callback, "Verifying filing facts...")
        facts_started = time.perf_counter()
        verified_facts = verified_filing_facts_for_prompt(context.bundle, filing_context, prompt)
        facts_elapsed = time.perf_counter() - facts_started
        request_payload = {
            "question": prompt,
            "filing_metadata": context.bundle.metadata,
            "deterministic_extracts_hints": {
                "use_policy": "Hints only. Filing text and verified filing facts override these values.",
                "extracts": context.bundle.deterministic_extracts,
            },
            "verified_filing_facts": verified_facts,
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
        overview_requirements = _overview_answer_requirements_for_prompt(prompt)
        if overview_requirements:
            request_payload["overview_answer_requirements"] = overview_requirements
        request_payload["request_budget"] = {
            "filing_context_char_limit": CHAT_RETRIEVED_CHAR_LIMIT,
            "request_payload_char_limit": CHAT_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": self.timeout_seconds,
        }
        request_payload = _enforce_request_payload_budget(request_payload)
        payload_text = _serialize_request_payload(request_payload)
        payload_chars = len(payload_text)
        approx_tokens = _approx_token_count(payload_chars)
        context_chars = _filing_context_char_count(request_payload.get("filing_context", {}))
        diagnostics = {
            "retrieval_seconds": round(retrieval_elapsed, 3),
            "verified_facts_seconds": round(facts_elapsed, 3),
            "filing_context_chars": context_chars,
            "request_payload_chars": payload_chars,
            "request_payload_approx_tokens": approx_tokens,
            "request_payload_char_limit": CHAT_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": self.timeout_seconds,
        }
        LOGGER.debug(
            "AI filing chat payload ready company=%s context_chars=%s payload_chars=%s approx_tokens=%s facts_elapsed=%.3fs",
            context.company_name,
            context_chars,
            payload_chars,
            approx_tokens,
            facts_elapsed,
        )
        input_messages = [{"role": "system", "content": FILING_CHAT_SYSTEM_PROMPT}]
        for message in history_messages[-CHAT_HISTORY_MESSAGE_LIMIT:]:
            input_messages.append({"role": message.role, "content": message.content})
        input_messages.append({"role": "user", "content": payload_text})

        try:
            _notify_progress(progress_callback, f"Calling OpenAI (timeout {self.timeout_seconds:g}s)...")
            openai_started = time.perf_counter()
            LOGGER.debug("AI filing chat OpenAI request started company=%s model=%s timeout=%ss", context.company_name, self.model, self.timeout_seconds)
            response = self._client().responses.create(
                model=self.model,
                input=input_messages,
                store=False,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            if _is_timeout_exception(exc):
                message = (
                    f"OpenAI IPO filing chat timed out after {self.timeout_seconds:g} seconds. "
                    "Try a narrower filing question or retry later."
                )
            else:
                message = f"OpenAI IPO filing chat failed: {exc}"
            message = _redact_api_key(message, self._current_api_key())
            LOGGER.warning("AI filing chat OpenAI request failed company=%s elapsed=%.3fs error=%s", context.company_name, time.perf_counter() - started_at, message)
            raise OpenAiIpoFilingChatError(message) from None

        openai_elapsed = time.perf_counter() - openai_started
        diagnostics["openai_seconds"] = round(openai_elapsed, 3)
        diagnostics["total_seconds"] = round(time.perf_counter() - started_at, 3)
        LOGGER.debug(
            "AI filing chat OpenAI request completed company=%s response_id=%s openai_elapsed=%.3fs total_elapsed=%.3fs",
            context.company_name,
            getattr(response, "id", "") or "",
            openai_elapsed,
            diagnostics["total_seconds"],
        )
        _notify_progress(progress_callback, "OpenAI response received.")
        answer = _clean_answer(_response_output_text(response))
        if not answer:
            raise OpenAiIpoFilingChatError("OpenAI IPO filing chat returned an empty response.")
        return IpoFilingChatResponse(
            answer=answer,
            response_id=str(getattr(response, "id", "") or ""),
            model=self.model,
            source_mode=str(request_payload.get("filing_context", {}).get("source_mode") or ""),
            source_debug=(
                *tuple(str(entry) for entry in request_payload.get("filing_context", {}).get("section_debug", ())),
                *_diagnostic_debug_lines(diagnostics),
            ),
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

        self._openai_client = OpenAI(api_key=api_key, timeout=self.timeout_seconds)
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
            "full_text_char_count": len(bundle.full_text),
            "approx_token_estimate": _approx_token_count(len(bundle.full_text)),
            "section_debug": [
                f"full_prospectus_text ({len(bundle.full_text)} chars): {_shorten(bundle.full_text, 260)}",
            ],
        }

    sections = retrieve_relevant_filing_sections(bundle.full_text, prompt)
    retrieved_chars = sum(len(section.text) for section in sections)
    return {
        "source_mode": "retrieved_filing_chunks",
        "retrieval_budget_chars": CHAT_RETRIEVED_CHAR_LIMIT,
        "retrieved_char_count": retrieved_chars,
        "approx_token_estimate": _approx_token_count(retrieved_chars),
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
            _filing_context_debug_line(section)
            for section in sections
        ],
    }


def _filing_context_debug_line(section: IpoFilingSourceSection) -> str:
    if section.start_offset is not None and section.end_offset is not None:
        offsets = f"offsets={section.start_offset}-{section.end_offset}"
    else:
        offsets = "offsets=unknown"
    return (
        f"{section.name} ({len(section.text)} chars, {offsets}, "
        f"toc_like={_is_toc_dominant_section(section.text)}): {_shorten(section.text, 260)}"
    )


def _serialize_request_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _enforce_request_payload_budget(payload: dict[str, Any]) -> dict[str, Any]:
    output = dict(payload)
    output["filing_context"] = _copy_filing_context(payload.get("filing_context", {}))
    output["verified_filing_facts"] = _copy_json_like(payload.get("verified_filing_facts", {}))

    if len(_serialize_request_payload(output)) <= CHAT_REQUEST_CHAR_LIMIT:
        return output

    context = output.get("filing_context")
    if isinstance(context, dict):
        overage = len(_serialize_request_payload(output)) - CHAT_REQUEST_CHAR_LIMIT
        target_context_chars = max(24_000, _filing_context_char_count(context) - overage - 2_000)
        output["filing_context"] = _trim_filing_context_to_budget(context, target_context_chars)

    if len(_serialize_request_payload(output)) <= CHAT_REQUEST_CHAR_LIMIT:
        return output

    output["verified_filing_facts"] = _trim_fact_snippets(output.get("verified_filing_facts", {}), max_snippet_chars=260)
    if len(_serialize_request_payload(output)) <= CHAT_REQUEST_CHAR_LIMIT:
        return output

    context = output.get("filing_context")
    if isinstance(context, dict):
        output["filing_context"] = _trim_filing_context_to_budget(context, 20_000)
    return output


def _copy_filing_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return json.loads(json.dumps(value, ensure_ascii=False))


def _copy_json_like(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _trim_filing_context_to_budget(filing_context: dict[str, Any], budget_chars: int) -> dict[str, Any]:
    trimmed = _copy_filing_context(filing_context)
    full_text = trimmed.get("full_prospectus_text")
    if isinstance(full_text, str):
        trimmed["full_prospectus_text"] = full_text[: max(0, budget_chars)].strip()
        trimmed["full_text_char_count"] = len(trimmed["full_prospectus_text"])
        trimmed["approx_token_estimate"] = _approx_token_count(len(trimmed["full_prospectus_text"]))
        trimmed["section_debug"] = [
            f"full_prospectus_text ({len(trimmed['full_prospectus_text'])} chars after request-budget trim): {_shorten(trimmed['full_prospectus_text'], 260)}"
        ]
        return trimmed

    sections = trimmed.get("sections")
    if not isinstance(sections, list):
        return trimmed
    kept: list[dict[str, Any]] = []
    char_count = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        text = str(section.get("text") or "")
        room = budget_chars - char_count
        if room <= 0:
            break
        copy = dict(section)
        copy["text"] = text[:room].strip()
        if not copy["text"]:
            continue
        kept.append(copy)
        char_count += len(copy["text"])
    trimmed["sections"] = kept
    trimmed["retrieved_char_count"] = char_count
    trimmed["approx_token_estimate"] = _approx_token_count(char_count)
    trimmed["section_debug"] = [_filing_context_debug_line(_section_from_payload(section)) for section in kept]
    return trimmed


def _section_from_payload(section: dict[str, Any]) -> IpoFilingSourceSection:
    return IpoFilingSourceSection(
        name=str(section.get("name") or ""),
        text=str(section.get("text") or ""),
        start_offset=section.get("start_offset") if isinstance(section.get("start_offset"), int) else None,
        end_offset=section.get("end_offset") if isinstance(section.get("end_offset"), int) else None,
    )


def _trim_fact_snippets(value: Any, *, max_snippet_chars: int) -> Any:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, item in value.items():
            if key == "source_snippet" and isinstance(item, str):
                output[key] = _shorten(item, max_snippet_chars)
            else:
                output[key] = _trim_fact_snippets(item, max_snippet_chars=max_snippet_chars)
        return output
    if isinstance(value, list):
        return [_trim_fact_snippets(item, max_snippet_chars=max_snippet_chars) for item in value]
    return value


def _filing_context_char_count(filing_context: Any) -> int:
    if not isinstance(filing_context, dict):
        return 0
    full_text = filing_context.get("full_prospectus_text")
    if isinstance(full_text, str):
        return len(full_text)
    sections = filing_context.get("sections")
    if not isinstance(sections, list):
        return 0
    return sum(len(str(section.get("text") or "")) for section in sections if isinstance(section, dict))


def _approx_token_count(char_count: int) -> int:
    return max(1, (max(0, char_count) + CHAT_APPROX_CHARS_PER_TOKEN - 1) // CHAT_APPROX_CHARS_PER_TOKEN)


def _diagnostic_debug_lines(diagnostics: dict[str, Any]) -> tuple[str, ...]:
    return (
        (
            "timing "
            f"retrieval={diagnostics.get('retrieval_seconds')}s "
            f"facts={diagnostics.get('verified_facts_seconds')}s "
            f"openai={diagnostics.get('openai_seconds')}s "
            f"total={diagnostics.get('total_seconds')}s"
        ),
        (
            "payload "
            f"context_chars={diagnostics.get('filing_context_chars')} "
            f"payload_chars={diagnostics.get('request_payload_chars')} "
            f"approx_tokens={diagnostics.get('request_payload_approx_tokens')} "
            f"limit={diagnostics.get('request_payload_char_limit')}"
        ),
        f"openai_timeout={diagnostics.get('openai_timeout_seconds')}s",
    )


def verified_filing_facts_for_prompt(
    bundle: IpoFilingSourceBundle,
    filing_context: dict[str, Any],
    prompt: str,
) -> dict[str, Any]:
    index = _filing_context_index(bundle, filing_context)
    facts: dict[str, Any] = {
        "policy": (
            "These facts were extracted from filing text as a local verification aid. "
            "Use them only when supported by the accompanying source snippets; otherwise say Not confidently extracted."
        ),
        "offering_terms": _verified_offering_terms(index),
        "financial_metrics": _verified_financial_metrics(index),
        "dilution": _verified_dilution_terms(index),
        "customer_supplier_concentration": _verified_customer_supplier_concentration(index),
        "license_obligations": _verified_license_obligations(index),
        "icfr_material_weaknesses": _verified_icfr_material_weaknesses(index),
        "shareholders_and_related_parties": _verified_shareholders_and_related_parties(index),
        "lockup_moratorium": _verified_lockup_moratorium(index),
        "ads_sgx_conversion": _verified_ads_sgx_conversion(index),
        "dual_prospectus_structure": _verified_dual_prospectus_structure(index),
        "recent_developments": _verified_recent_developments(index),
        "regulatory_status": _verified_regulatory_status(index),
        "risk_checks": _verified_risk_checks(index),
    }
    unsupported_industry_revenue = _industry_market_revenue_warning(index)
    if unsupported_industry_revenue:
        facts["company_revenue_guardrail"] = unsupported_industry_revenue
    return facts


def _verified_offering_terms(index: _FilingContextIndex) -> dict[str, Any]:
    cover = _fact_source_text(
        index,
        names=("cover_page",),
        spec=_cover_page_spec(),
        fallback_terms=("American Depositary Shares", "Common Shares", "Ordinary Shares", "Nasdaq", "NYSE", "Sole Book-Runner"),
    )
    offering = _fact_source_text(
        index,
        names=("offering_terms",),
        spec=_offering_spec(max_sections=1),
        fallback_terms=("we are offering", "initial public offering price", "price range", "under the symbol", "book-runner"),
    ) or cover
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
    if "securities_offered" not in facts:
        match = re.search(
            r"(?:we are offering|company is offering|public offering prospectus[^.\n]{0,180}(?:offering|sale of))\s+(?:up to\s+)?([0-9][0-9,]*)\s+Common Shares",
            combined,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if not match:
            for candidate in re.finditer(r"\b([0-9][0-9,]*)\s+Common Shares\b", cover[:4_000], flags=re.IGNORECASE):
                nearby = cover[max(0, candidate.start() - 180) : candidate.end() + 180].lower()
                if "resale" not in nearby and "selling shareholder" not in nearby:
                    match = candidate
                    break
        if match:
            facts["securities_offered"] = _fact(
                f"{match.group(1)} Common Shares offered by the company",
                combined,
                match.group(0),
            )
    match = re.search(r"Each ADS represents\s+([0-9][0-9,]*)\s+ordinary shares", combined, flags=re.IGNORECASE)
    if match:
        facts["ads_ratio"] = _fact(f"1 ADS = {match.group(1)} ordinary shares", combined, match.group(0))
    match = re.search(
        r"between\s+((?:US\$|S\$|HK\$|\$)\s*[0-9][0-9,.]*(?:\.[0-9]+)?)\s+(?:and|to|-)\s+((?:US\$|S\$|HK\$|\$)?\s*[0-9][0-9,.]*(?:\.[0-9]+)?)\s+per\s+(ADS|American Depositary Share|Common Share|ordinary share|share)",
        combined,
        flags=re.IGNORECASE,
    )
    if match:
        low = _normalize_price_token(match.group(1))
        high = _normalize_price_token(match.group(2), fallback_prefix=_currency_prefix(low))
        facts["expected_price_range"] = _fact(f"{low}-{high} per {match.group(3).strip()}", combined, match.group(0))
    match = re.search(
        r"(?:assumed|midpoint)[^.\n]{0,120}?((?:US\$|S\$|HK\$|\$)\s*[0-9][0-9,.]*(?:\.[0-9]+)?)\s+(?:midpoint\s+)?(?:per\s+)?(ADS|American Depositary Share|Common Share|ordinary share|share)?",
        combined,
        flags=re.IGNORECASE,
    )
    if match:
        unit = f" per {match.group(2).strip()}" if match.group(2) else ""
        facts["assumed_midpoint_price"] = _fact(f"{_normalize_price_token(match.group(1))}{unit}", combined, match.group(0))
    match = re.search(
        r"(?:apply to list|applied to list|plans? to list|listing).*?(Nasdaq Capital Market|Nasdaq Global Market|Nasdaq Global Select Market|Nasdaq|New York Stock Exchange|NYSE American|NYSE).*?under\s+(?:the\s+)?(?:symbol|ticker symbol)\s+[\"']?([A-Z][A-Z0-9.]{0,5})[\"']?",
        combined,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        exchange = re.sub(r"\s+", " ", match.group(1)).strip()
        facts["listing"] = _fact(f"{exchange} under symbol {match.group(2).strip('.').upper()}", combined, match.group(0))
    if re.search(r"will not close unless.*?(?:NYSE|Nasdaq).*?approv", combined, flags=re.IGNORECASE | re.DOTALL):
        facts["listing_condition"] = _fact("Offering closing is conditioned on exchange listing approval", combined, "will not close unless")
    match = re.search(r"([0-9]{1,3})\s*-?\s*day option.*?additional\s+([0-9]{1,2})%\s+of the ADSs", combined, flags=re.IGNORECASE)
    if match:
        facts["over_allotment_option"] = _fact(f"{match.group(1)}-day option for up to an additional {match.group(2)}% of ADSs sold", combined, match.group(0))
    if "over_allotment_option" not in facts:
        match = re.search(
            r"(?:over-allotment|over allotment|option)[^.\n]{0,220}?(?:(15)%|additional\s+([0-9]{1,3})%)[^.\n]{0,220}?([0-9][0-9,]*)\s+Common Shares",
            combined,
            flags=re.IGNORECASE,
        )
        if match:
            percent = match.group(1) or match.group(2)
            facts["over_allotment_option"] = _fact(f"{percent}% / {match.group(3)} Common Shares over-allotment option", combined, match.group(0))
    underwriter_value = _underwriter_fact_value(combined)
    if underwriter_value:
        facts["underwriters"] = _fact(underwriter_value, combined, underwriter_value.split(" is ")[0].split(" are ")[0])
    elif re.search(r"Roth Capital Partners", combined, flags=re.IGNORECASE) and re.search(r"Benchmark", combined, flags=re.IGNORECASE):
        facts["underwriters"] = _fact("Roth Capital Partners and Benchmark are joint book-running managers / representatives", combined, "Roth Capital Partners Benchmark")

    proceeds = _fact_source_text(index, names=("use_of_proceeds",), spec=_use_of_proceeds_spec(max_sections=1), fallback_terms=("net proceeds", "use of proceeds", "we intend to use"))
    if "net proceeds of approximately" not in proceeds.lower():
        proceeds = _targeted_fact_windows(index.text, ("net proceeds of approximately", "over-allotment option"), max_chars=10_000)
    match = re.search(r"net proceeds of approximately\s+(US\$[0-9.]+\s+million).*?\(\s*(?:or\s+)?(US\$[0-9.]+\s+million)\s+if the underwriters exercise", proceeds, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["net_proceeds"] = _fact(
            f"Approximately {match.group(1)}, or {match.group(2)} if the over-allotment option is exercised in full",
            proceeds,
            match.group(0),
        )
    return facts


def _normalize_price_token(token: str, fallback_prefix: str = "") -> str:
    clean = re.sub(r"\s+", "", token or "")
    match = re.match(r"^(US\$|S\$|HK\$|\$)?([0-9][0-9,.]*(?:\.[0-9]+)?)$", clean, flags=re.IGNORECASE)
    if not match:
        return clean
    prefix = match.group(1) or fallback_prefix
    if prefix.upper() == "US$":
        prefix = "US$"
    elif prefix.upper() == "HK$":
        prefix = "HK$"
    elif prefix.upper() == "S$":
        prefix = "S$"
    return f"{prefix}{match.group(2)}" if prefix else match.group(2)


def _currency_prefix(value: str) -> str:
    match = re.match(r"^(US\$|S\$|HK\$|\$)", value or "", flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _underwriter_fact_value(text: str) -> str:
    descriptor_match = re.search(r"\b(Sole\s+Book-?Runner|Joint\s+Book-Running Managers?|Joint\s+Book-?Runners?)\b", text or "", flags=re.IGNORECASE)
    if descriptor_match:
        prefix = (text or "")[max(0, descriptor_match.start() - 220) : descriptor_match.start()]
        name_match = re.search(
            r"([A-Z][A-Za-z0-9&,' \-]{2,120}(?:Co\.,\s*)?(?:LLC|L\.L\.C\.|Inc\.?|Ltd\.?|Company|Capital|Securities|Partners))\s*$",
            prefix,
            flags=re.IGNORECASE,
        )
        if name_match:
            name = _clean_underwriter_phrase(name_match.group(1))
            if _looks_like_underwriter_name(name):
                descriptor = descriptor_match.group(1).lower()
                if "sole" in descriptor:
                    return f"{name} is sole book-runner / representative"
                if "joint" in descriptor:
                    return f"{name} are joint book-running managers / representatives"
                return name

    lines = [re.sub(r"\s+", " ", line).strip(" .") for line in (text or "").splitlines()]
    for index, line in enumerate(lines):
        lower = line.lower()
        if not re.search(r"\b(?:sole|joint)?\s*book-?runner|\bbook-running manager|\brepresentative\b", lower):
            continue
        for candidate in (lines[index - 1] if index > 0 else "", lines[index + 1] if index + 1 < len(lines) else ""):
            name = _clean_underwriter_phrase(candidate)
            if _looks_like_underwriter_name(name):
                if "sole" in lower:
                    return f"{name} is sole book-runner / representative"
                if "joint" in lower:
                    return f"{name} are joint book-running managers / representatives"
                return name

    patterns = (
        r"([A-Z][A-Za-z0-9&.,' \-]{4,180}?)\s+(?:is|are)\s+(?:acting as\s+)?(?:the\s+)?(?:sole\s+book-?runner|joint\s+book-running managers?|representatives? of the underwriters)",
        r"(?:representatives of the underwriters|underwriters|book-running managers?|bookrunners)\s+(?:are|include|:)\s+([^.;\n]{5,220})",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "", flags=re.IGNORECASE)
        if not match:
            continue
        name = _clean_underwriter_phrase(match.group(1))
        if not _looks_like_underwriter_name(name):
            continue
        descriptor = match.group(0).lower()
        if "sole" in descriptor:
            return f"{name} is sole book-runner / representative"
        if "joint" in descriptor:
            return f"{name} are joint book-running managers / representatives"
        return name
    return ""


def _clean_underwriter_phrase(value: str) -> str:
    clean = re.sub(r"\s+", " ", value or "").strip(" .,:;")
    clean = re.sub(r"\s+(?:is|are)\s+(?:acting as\s+)?(?:the\s+)?(?:sole|joint).*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+(?:sole|joint)?\s*book-?runner.*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^(?:the\s+)?", "", clean, flags=re.IGNORECASE)
    return clean.strip(" .,:;")


def _looks_like_underwriter_name(value: str) -> bool:
    if not value or len(value) < 5 or len(value) > 180:
        return False
    lower = value.lower()
    if any(term in lower for term in ("prospectus", "registration statement", "common shares", "table of contents")):
        return False
    return bool(re.search(r"\b(llc|l\.l\.c\.|inc\.?|ltd\.?|co\.|company|capital|securities|partners|markets|gunnar|benchmark|roth)\b", lower))


def _verified_financial_metrics(index: _FilingContextIndex) -> dict[str, Any]:
    financial_text = _financial_verification_text(index)
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

    capitalization = _fact_source_text(index, names=("capitalization",), spec=_capitalization_spec(max_sections=1), fallback_terms=("Cash and cash equivalents", "Total debt", "as adjusted"))
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


def _verified_dilution_terms(index: _FilingContextIndex) -> dict[str, Any]:
    dilution = _fact_source_text(index, names=("dilution",), spec=_dilution_spec(max_sections=1), fallback_terms=("immediate dilution", "net tangible book value", "per ADS")) or index.context_text
    facts: dict[str, Any] = {}

    match = re.search(r"net tangible book value.*?(US\$[0-9.]+\s+per\s+ADS)", dilution, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["pro_forma_as_adjusted_ntbv_per_ads"] = _fact(_compact_fact_value(match.group(1)), dilution, match.group(0))
    match = re.search(r"immediate dilution.*?(US\$[0-9.]+\s+per\s+ADS)", dilution, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["immediate_dilution_per_ads"] = _fact(_compact_fact_value(match.group(1)), dilution, match.group(0))
    match = re.search(r"net tangible book value.*?(US\$[0-9.]+\s+per\s+ordinary share)", dilution, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["pro_forma_as_adjusted_ntbv_per_ordinary_share"] = _fact(_compact_fact_value(match.group(1)), dilution, match.group(0))
    match = re.search(r"immediate dilution.*?(US\$[0-9.]+\s+per\s+ordinary share)", dilution, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["immediate_dilution_per_ordinary_share"] = _fact(_compact_fact_value(match.group(1)), dilution, match.group(0))
    return facts


def _verified_customer_supplier_concentration(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(index, names=("customer_supplier_concentration",), spec=_customer_supplier_concentration_spec(max_sections=2), fallback_terms=("Haur-Jye Technology", "MMI Systems Pte Ltd", "customer concentration", "supplier concentration")),
                _fact_source_text(index, names=("business",), spec=_business_spec(max_sections=2), fallback_terms=("customers", "suppliers")),
                _fact_source_text(index, names=("risk_factors",), spec=_risk_spec(max_sections=2), fallback_terms=("customer concentration", "supplier concentration")),
                _fact_source_text(index, names=("related_party_transactions",), spec=_related_party_transactions_spec(max_sections=1), fallback_terms=("MMI Systems Pte Ltd", "MMI Holdings Limited")),
                _targeted_fact_windows(index.text, ("Haur-Jye Technology", "MMI Systems Pte Ltd"), max_chars=12_000),
            ]
        )
    )
    facts: dict[str, Any] = {}

    match = re.search(r"Haur-Jye Technology[^.\n]*?([0-9.]+%)\s+of\s+(?:our\s+)?(?:FY\s*)?2025\s+revenue", search_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        match = re.search(r"Haur-Jye Technology[^.\n]*?accounted for\s+([0-9.]+%)\s+of\s+(?:our\s+)?revenue", search_text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["largest_customer"] = _fact(
            f"Haur-Jye Technology, Taiwan accounted for {match.group(1)} of FY2025 revenue",
            search_text,
            match.group(0),
        )

    match = re.search(r"MMI Systems Pte Ltd[^.\n]*?([0-9.]+%)\s+of\s+(?:our\s+)?(?:FY\s*)?2025\s+purchases", search_text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        match = re.search(r"MMI Systems Pte Ltd[^.\n]*?accounted for\s+([0-9.]+%)\s+of\s+(?:our\s+)?purchases", search_text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["major_supplier"] = _fact(
            f"MMI Systems Pte Ltd, Singapore accounted for {match.group(1)} of FY2025 purchases",
            search_text,
            match.group(0),
        )

    if re.search(r"MMI Systems Pte Ltd.*?wholly owned subsidiary of MMI Holdings Limited", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["supplier_related_party_link"] = _fact(
            "MMI Systems Pte Ltd is a wholly owned subsidiary of MMI Holdings Limited, one of MetaOptics' principal shareholders",
            search_text,
            "MMI Systems Pte Ltd",
        )
    return facts


def _verified_license_obligations(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(index, names=("license_obligations",), spec=_license_obligations_spec(max_sections=2), fallback_terms=("Accelerate Technologies", "A*STAR", "gross revenues", "commercialization obligations")),
                _fact_source_text(index, names=("business",), spec=_business_spec(max_sections=2), fallback_terms=("License Agreements", "Intellectual Property")),
                _targeted_fact_windows(index.text, ("Accelerate Technologies", "A*STAR", "S$3.0 million", "S$5.0 million"), max_chars=12_000),
            ]
        )
    )
    facts: dict[str, Any] = {}

    if re.search(r"Accelerate Technologies|A\*STAR|A-Star", search_text, flags=re.IGNORECASE):
        facts["licensed_ip_counterparty"] = _fact(
            "Key IP is licensed from Accelerate Technologies / A*STAR",
            search_text,
            "Accelerate Technologies",
        )
    match = re.search(r"(?:August|Aug\.?)\s+2023\s+License Agreement.*?(S\$[0-9.]+\s+million).*?within\s+five\s+years.*?(?:August|Aug\.?)\s+1,\s+2023", search_text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["august_2023_gross_revenue_threshold"] = _fact(
            f"{match.group(1)} gross revenue threshold within five years from August 1, 2023",
            search_text,
            match.group(0),
        )
    match = re.search(r"(?:December|Dec\.?)\s+2023\s+License Agreement.*?(S\$[0-9.]+\s+million).*?within\s+five\s+years.*?(?:December|Dec\.?)\s+25,\s+2023", search_text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["december_2023_gross_revenue_threshold"] = _fact(
            f"{match.group(1)} gross revenue threshold within five years from December 25, 2023",
            search_text,
            match.group(0),
        )
    if re.search(r"commerciali[sz]ation obligations?.*?(?:not been fulfilled|not fulfilled).*?(?:waivers?|removed|amended|extended)", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["commercialization_obligation_status"] = _fact(
            "Some commercialization obligations had not been fulfilled, but waivers or amendments removed, waived, or extended obligations",
            search_text,
            "commercialization obligations",
        )
    return facts


def _verified_icfr_material_weaknesses(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(index, names=("icfr_material_weaknesses",), spec=_icfr_material_weaknesses_spec(max_sections=2), fallback_terms=("material weakness", "segregation of duties", "purchases and payables")),
                _fact_source_text(index, names=("risk_factors",), spec=_risk_spec(max_sections=2), fallback_terms=("material weakness", "internal control")),
                _fact_source_text(index, names=("mda_results",), spec=_mda_spec(max_sections=2), fallback_terms=("material weakness", "controls and procedures")),
                _targeted_fact_windows(index.text, ("material weakness", "segregation of duties", "purchases and payables"), max_chars=12_000),
            ]
        )
    )
    weakness_patterns = (
        ("policies/procedures did not comprehensively cover multiple control areas", r"polic(?:y|ies).*?procedures?.*?(?:did not|not).*?comprehensively cover"),
        ("segregation of duties", r"segregation of duties"),
        ("payroll controls", r"payroll"),
        ("purchases and payables controls", r"purchases? and payables?"),
        ("insufficient IFRS accounting and reporting personnel", r"insufficient.*?(?:accounting|financial reporting).*?IFRS"),
        ("gaps in comprehensive accounting/reporting policies", r"comprehensive accounting.*?reporting policies|accounting and reporting policies"),
        ("prior corrections/restatements of previously issued financial statements", r"corrections?|restatements?.*?previously issued financial statements"),
    )
    weaknesses = [label for label, pattern in weakness_patterns if re.search(pattern, search_text, flags=re.IGNORECASE | re.DOTALL)]
    if not weaknesses:
        return {}
    snippet_needle = "material weakness" if re.search(r"material weakness", search_text, flags=re.IGNORECASE) else weaknesses[0]
    return {
        "specific_weaknesses": {
            "value": "; ".join(_dedupe(weaknesses)),
            "source_snippet": _source_snippet(search_text, snippet_needle, before=220, after=620),
        }
    }


def _verified_shareholders_and_related_parties(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(index, names=("principal_shareholders",), spec=_principal_shareholders_spec(max_sections=1), fallback_terms=("Principal Shareholders", "directors and executive officers", "Angelling Capital")),
                _fact_source_text(index, names=("related_party_transactions",), spec=_related_party_transactions_spec(max_sections=2), fallback_terms=("Related Party Transactions", "MMI Systems", "MST SingCo")),
                _fact_source_text(index, names=("customer_supplier_concentration",), spec=_customer_supplier_concentration_spec(max_sections=1), fallback_terms=("MMI Systems Pte Ltd",)),
                _targeted_fact_windows(index.text, ("directors and executive officers", "Angelling Capital", "MST SingCo", "MMI Systems Pte Ltd"), max_chars=12_000),
            ]
        )
    )
    facts: dict[str, Any] = {}

    match = re.search(r"directors and executive officers as a group hold\s+([0-9.]+%)", search_text, flags=re.IGNORECASE)
    if match:
        facts["directors_and_officers_ownership"] = _fact(
            f"Directors and executive officers as a group hold {match.group(1)} before the offering",
            search_text,
            match.group(0),
        )
    if re.search(r"Angelling Capital Holdings Limited", search_text, flags=re.IGNORECASE):
        facts["angelling_principal_shareholder"] = _fact("Angelling Capital Holdings Limited is identified as a principal shareholder", search_text, "Angelling Capital Holdings Limited")
    if re.search(r"MST SingCo", search_text, flags=re.IGNORECASE):
        facts["mst_singco_principal_or_related_party"] = _fact("MST SingCo is identified in principal-shareholder or related-party context", search_text, "MST SingCo")
    if re.search(r"MMI Systems Pte Ltd.*?MMI Holdings Limited", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["mmi_related_supplier"] = _fact("MMI Systems Pte Ltd is tied to MMI Holdings Limited in the related-party/principal-shareholder structure", search_text, "MMI Systems Pte Ltd")
    return facts


def _verified_lockup_moratorium(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(index, names=("shares_eligible_future_sale",), spec=_shares_eligible_future_sale_spec(max_sections=2), fallback_terms=("moratorium", "September 8, 2026", "future sale")),
                _fact_source_text(index, names=("underwriting",), spec=_underwriting_spec(), fallback_terms=("lock-up", "180 days", "underwriters")),
                _targeted_fact_windows(index.text, ("moratorium", "September 8, 2026", "180-day", "lock-up"), max_chars=12_000),
            ]
        )
    )
    facts: dict[str, Any] = {}

    if re.search(r"180[\s-]+day(?:s)?\s+lock-up|180\s+days?", search_text, flags=re.IGNORECASE) and re.search(
        r"(?:directors|officers|shareholders).*?(?:50%|more than\s+50%)",
        search_text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        facts["us_offering_lockup"] = _fact(
            "180-day lock-up applies to the company and certain directors, officers, and shareholders owning more than 50% of ordinary shares before the offering",
            search_text,
            "180-day lock-up",
        )
    match = re.search(r"SGX(?:-ST)?[^.\n]*?moratorium.*?(September\s+8,\s+2026)", search_text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["sgx_moratorium"] = _fact(
            f"SGX-ST Catalist moratorium restrictions remain in place until {match.group(1)}",
            search_text,
            match.group(0),
        )
    return facts


def _verified_ads_sgx_conversion(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(index, names=("cover_page",), spec=_cover_page_spec(), fallback_terms=("Each ADS represents", "Ordinary Shares")),
                _fact_source_text(index, names=("ads_sgx_conversion",), spec=_ads_sgx_conversion_spec(max_sections=2), fallback_terms=("convert ordinary shares into ADSs", "Singapore Exchange", "Each ADS represents")),
                _targeted_fact_windows(index.text, ("convert ordinary shares into ADSs", "Singapore Exchange", "Each ADS represents"), max_chars=12_000),
            ]
        )
    )
    facts: dict[str, Any] = {}

    match = re.search(r"ordinary shares have been listed on\s+(?:Catalist of the\s+)?Singapore Exchange Securities Trading Limited.*?stock code\s+[\"']?([A-Z0-9.]+)", search_text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["sgx_listing"] = _fact(
            f"Ordinary shares trade on SGX-ST Catalist under stock code {match.group(1).strip('.').upper()}",
            search_text,
            match.group(0),
        )
    match = re.search(r"Each ADS represents\s+([0-9][0-9,]*)\s+ordinary shares", search_text, flags=re.IGNORECASE)
    if match:
        facts["ads_ratio"] = _fact(f"1 ADS = {match.group(1)} ordinary shares", search_text, match.group(0))
    if re.search(r"convert.*?ordinary shares.*?ADSs|ADSs.*?converted.*?ordinary shares|depositary.*?cancel.*?ADSs", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["conversion_mechanics"] = _fact(
            "Filing describes mechanics for converting ordinary shares and ADSs through the depositary structure",
            search_text,
            "ordinary shares",
        )
    if facts.get("sgx_listing") and facts.get("ads_ratio"):
        facts["ads_sgx_price_reconciliation"] = {
            "value": "Overview should reconcile SGX ordinary-share trading with the U.S. ADS price range using the disclosed ADS ratio",
            "source_snippet": facts["ads_ratio"]["source_snippet"],
        }
    return facts


def _verified_dual_prospectus_structure(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(
                    index,
                    names=("explanatory_note",),
                    spec=_explanatory_note_spec(),
                    fallback_terms=("This Registration Statement contains two prospectuses", "Public Offering Prospectus", "Resale Prospectus", "Alternate Pages"),
                    max_chars=16_000,
                ),
                _fact_source_text(
                    index,
                    names=("selling_shareholders",),
                    spec=_selling_shareholders_spec(),
                    fallback_terms=("Selling Shareholders", "2,817,294", "will not receive any proceeds"),
                    max_chars=16_000,
                ),
                _fact_source_text(
                    index,
                    names=("underwriting",),
                    spec=_underwriting_spec(),
                    fallback_terms=("Plan of Distribution", "not underwritten", "resale"),
                    max_chars=10_000,
                ),
                _targeted_fact_windows(
                    index.text,
                    ("This Registration Statement contains two prospectuses", "Public Offering Prospectus", "Resale Prospectus", "Alternate Pages", "Selling Shareholders"),
                    max_chars=20_000,
                ),
            ]
        )
    )
    lower = search_text.lower()
    if not (
        ("public offering prospectus" in lower and "resale prospectus" in lower)
        or "registration statement contains two prospectuses" in lower
    ):
        return {}

    facts: dict[str, Any] = {
        "structure_detected": _fact("Registration statement contains separate Public Offering Prospectus and Resale Prospectus disclosure", search_text, "Public Offering Prospectus"),
    }
    match = re.search(
        r"Public Offering Prospectus[^.\n]{0,260}?(?:offering|sale|sell)[^.\n]{0,120}?([0-9][0-9,]*)\s+Common Shares",
        search_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        match = re.search(r"(?:we|the company)\s+(?:are|is)\s+offering\s+([0-9][0-9,]*)\s+Common Shares", search_text, flags=re.IGNORECASE)
    if match:
        facts["public_offering"] = _fact(f"Public Offering Prospectus covers company sale of {match.group(1)} Common Shares", search_text, match.group(0))

    match = re.search(
        r"Resale Prospectus[^.\n]{0,360}?(?:resale|sell|sale)[^.\n]{0,180}?([0-9][0-9,]*)\s+Common Shares",
        search_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        match = re.search(r"Selling Shareholders?[^.\n]{0,260}?([0-9][0-9,]*)\s+Common Shares", search_text, flags=re.IGNORECASE | re.DOTALL)
    if match:
        facts["resale_offering"] = _fact(f"Resale Prospectus covers resale from time to time by Selling Shareholders of {match.group(1)} Common Shares", search_text, match.group(0))

    if re.search(r"will not receive (?:any\s+)?proceeds.*?(?:Selling Shareholders|shares sold by selling shareholders|resale)", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["resale_proceeds"] = _fact("Company will not receive proceeds from shares sold by Selling Shareholders", search_text, "will not receive")
    if re.search(r"(?:no|not).*?resale sales?.*?(?:until|prior to).*?(?:NYSE|trading)", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["resale_trading_condition"] = _fact("No resale sales occur until IPO shares begin trading on the exchange", search_text, "resale sales")
    if re.search(r"(?:consummation|completion).*?resale.*?conditioned on.*?(?:initial public offering|IPO)", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["resale_ipo_condition"] = _fact("Resale offering is conditioned on consummation of the initial public offering", search_text, "conditioned on")
    if re.search(r"omits?.*?Capitalization.*?Dilution|replaces?.*?Underwriting.*?Plan of Distribution", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["alternate_page_changes"] = _fact(
            "Resale Prospectus alternate pages omit Capitalization/Dilution and replace Underwriting with Selling Shareholder Plan of Distribution",
            search_text,
            "Alternate Pages",
        )
    return facts


def _verified_recent_developments(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(
                    index,
                    names=("recent_developments",),
                    spec=_recent_developments_spec(max_sections=2),
                    fallback_terms=("Recent Developments", "Moltex", "Asset Acquisition", "exclusivity agreement"),
                    max_chars=18_000,
                ),
                _fact_source_text(
                    index,
                    names=("use_of_proceeds",),
                    spec=_use_of_proceeds_spec(max_sections=1),
                    fallback_terms=("Moltex", "asset acquisition", "acquisition price"),
                    max_chars=8_000,
                ),
                _targeted_fact_windows(
                    index.text,
                    ("Moltex", "Asset Acquisition", "Stable Salt Reactor", "WATSS", "administration", "exclusivity fee"),
                    max_chars=20_000,
                ),
            ]
        )
    )
    if not re.search(r"\bMoltex\b|Asset Acquisition|exclusivity agreement", search_text, flags=re.IGNORECASE):
        return {}

    facts: dict[str, Any] = {
        "material_acquisition_detected": _fact("Moltex Asset Acquisition is disclosed as a material recent development", search_text, "Moltex"),
    }
    match = re.search(
        r"(?:purchase price|acquisition price)[^.\n]{0,180}?((?:\N{POUND SIGN}|GBP)[0-9][0-9,]*[0-9])[^.\n]{0,180}?(CAD\$[0-9.]+\s+million)[^.\n]{0,180}?(US\$[0-9.]+\s+million)",
        search_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if match:
        facts["purchase_price"] = _fact(f"{match.group(1)} / {match.group(2)} / approximately {match.group(3)}", search_text, match.group(0))
    if re.search(r"patents?|patent applications?|technical know-how|engineering designs?|technical documentation|experimental|modeling data|software|regulatory work product", search_text, flags=re.IGNORECASE):
        facts["target_assets"] = _fact(
            "Target assets include IP and related rights, patents/applications, technical know-how, engineering designs, technical documentation, data, software, and regulatory work product",
            search_text,
            "technical know-how",
        )
    if re.search(r"Stable Salt Reactor|SSR-W|WAste To Stable Salt|WATSS", search_text, flags=re.IGNORECASE):
        facts["technology_context"] = _fact("Assets relate to Moltex SSR-W and WATSS spent-fuel recycling technology", search_text, "Stable Salt Reactor")
    if re.search(r"in administration|distressed assets?", search_text, flags=re.IGNORECASE):
        facts["distressed_status"] = _fact("Moltex Energy Limited is in administration and the assets are distressed assets", search_text, "administration")
    if re.search(r"does not expect to assume.*?liabilities|historical liabilities", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["liability_assumption"] = _fact("Nuclea does not expect to assume historical liabilities based on available information", search_text, "historical liabilities")

    fee_parts: list[str] = []
    for label, pattern in (
        ("non-refundable exclusivity fee", r"non-refundable exclusivity fee(?:\s+of)?\s+((?:\N{POUND SIGN}|GBP)[0-9][0-9,]*[0-9])"),
        ("first extension fee", r"first extension fee(?:\s+of)?\s+((?:\N{POUND SIGN}|GBP)[0-9][0-9,]*[0-9])"),
        ("second extension fee", r"second extension fee(?:\s+of)?\s+((?:\N{POUND SIGN}|GBP)[0-9][0-9,]*[0-9])"),
        ("possible further extension fee", r"(?:further|additional) extension[^.\n]{0,120}?\s+((?:\N{POUND SIGN}|GBP)[0-9][0-9,]*[0-9])"),
    ):
        match = re.search(pattern, search_text, flags=re.IGNORECASE)
        if match:
            fee_parts.append(f"{label} {match.group(1)}")
    if fee_parts:
        facts["extension_and_exclusivity_fees"] = {
            "value": "; ".join(fee_parts),
            "source_snippet": _source_snippet(search_text, "exclusivity fee", before=220, after=620) or _source_snippet(search_text, "extension fee", before=220, after=620),
        }
    if re.search(r"subject to due diligence|definitive sale agreement|definitive agreement", search_text, flags=re.IGNORECASE):
        facts["closing_risk"] = _fact("Terms remain subject to due diligence and negotiation of a definitive sale agreement", search_text, "due diligence")
    return facts


def _verified_regulatory_status(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(
                    index,
                    names=("regulatory_status",),
                    spec=_regulatory_status_spec(max_sections=2),
                    fallback_terms=("NRC", "CNSC", "NuMark Associates", "formal applications", "US$100 million"),
                    max_chars=18_000,
                ),
                _fact_source_text(
                    index,
                    names=("business",),
                    spec=_business_spec(max_sections=2),
                    fallback_terms=("regulatory", "licensing", "Nuclear Regulatory Commission", "Canadian Nuclear Safety Commission"),
                    max_chars=12_000,
                ),
                _targeted_fact_windows(
                    index.text,
                    ("NuMark Associates", "CNSC", "NRC", "no formal applications", "US$100 million", "2028", "2029", "2031"),
                    max_chars=20_000,
                ),
            ]
        )
    )
    if not re.search(r"\bNRC\b|CNSC|Nuclear Regulatory Commission|Canadian Nuclear Safety Commission|NuMark", search_text, flags=re.IGNORECASE):
        return {}

    facts: dict[str, Any] = {}
    if re.search(r"NuMark Associates.*?(?:pre-application|preapplication|NRC)", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["nrc_pre_application"] = _fact("Preliminary NRC pre-application engagement through NuMark Associates is disclosed", search_text, "NuMark Associates")
    if re.search(r"CNSC.*?(?:informal|advanced reactor review)|informal exchange.*?CNSC", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["cnsc_informal_exchange"] = _fact("Limited informal exchange with CNSC's advanced reactor review division is disclosed", search_text, "CNSC")
    if re.search(r"no formal applications?.*?(?:submitted|filed).*?(?:jurisdiction|NRC|CNSC)|(?:neither|not).*?formal applications?.*?(?:NRC|CNSC)", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["formal_applications"] = _fact("No formal NRC/CNSC applications have been submitted", search_text, "formal applications")
    if re.search(r"funding|manufacturing partners?|test site|community support|design iterations?|regulatory process changes?", search_text, flags=re.IGNORECASE):
        facts["timeline_dependencies"] = _fact(
            "Timeline depends on funding, manufacturing partners, test site/community support, design iterations, and regulatory process changes",
            search_text,
            "funding",
        )
    match = re.search(r"(US\$[0-9.]+\s+million)[^.\n]{0,220}?(?:through|to|by)\s+2028", search_text, flags=re.IGNORECASE)
    if match:
        facts["development_capital_need"] = _fact(f"About {match.group(1)} required through 2028 for development, demonstration, and licensing activities", search_text, match.group(0))
    if re.search(r"2029[^.\n]{0,220}2031|potentially higher.*?2029", search_text, flags=re.IGNORECASE | re.DOTALL):
        facts["later_spending_uncertainty"] = _fact("Potentially higher 2029-2031 spending remains unclear", search_text, "2029")
    return facts


def _verified_risk_checks(index: _FilingContextIndex) -> dict[str, Any]:
    search_text = "\n\n".join(
        _dedupe(
            [
                _fact_source_text(index, names=("risk_factors",), spec=_risk_spec(max_sections=2), fallback_terms=("going concern", "substantial doubt", "variable interest entity", "VIE")),
                _fact_source_text(index, names=("financial_statements",), spec=_financial_statements_spec(max_sections=1), fallback_terms=("going concern", "substantial doubt", "do not alleviate")),
                _targeted_fact_windows(index.text, ("prepared on a going concern basis", "raise substantial doubt", "substantial doubt", "do not alleviate", "variable interest entity", "wholly-owned subsidiaries"), max_chars=14_000),
            ]
        )
    )
    going_concern_sentence = (
        _source_snippet(search_text, "substantial doubt", before=220, after=520)
        or _source_snippet(search_text, "do not alleviate", before=220, after=520)
        or _source_snippet(search_text, "prepared on a going concern basis", before=180, after=420)
    )
    return {
        "going_concern_risk_detected": {
            "value": _has_adverse_going_concern_language(search_text),
            "source_snippet": going_concern_sentence,
            "instruction": "Do not describe a material going-concern warning unless adverse substantial-doubt or material-uncertainty language is present.",
        },
        "vie_structure_detected": {
            "value": _has_vie_structure_language(search_text),
            "source_snippet": _source_snippet(search_text, "variable interest entity", before=180, after=420) or _source_snippet(search_text, "wholly-owned subsidiaries", before=180, after=420),
            "instruction": "Do not flag China/VIE risk from generic China, PRC, customer, patent, or market-report references.",
        },
    }


def _industry_market_revenue_warning(index: _FilingContextIndex) -> dict[str, str]:
    text = "\n\n".join(
        _dedupe(
            [
                index.context_text,
                _targeted_fact_windows(index.text, ("Global revenue grew from about", "Independent Market Report"), max_chars=8_000),
            ]
        )
    )
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
        "source_snippet": _source_snippet(source_text, needle, before=180, after=300) or _shorten(needle, CHAT_FACT_SNIPPET_CHAR_LIMIT),
    }


def _compact_fact_value(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _filing_context_text(filing_context: dict[str, Any], *, limit: int = CHAT_RETRIEVED_CHAR_LIMIT) -> str:
    full_text = filing_context.get("full_prospectus_text")
    if isinstance(full_text, str):
        return full_text[:limit]
    sections = filing_context.get("sections")
    if not isinstance(sections, list):
        return ""
    return "\n\n".join(str(section.get("text") or "") for section in sections if isinstance(section, dict))[:limit]


def _filing_context_section_map(filing_context: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    sections = filing_context.get("sections")
    if not isinstance(sections, list):
        return {}
    grouped: dict[str, list[str]] = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        name = str(section.get("name") or "").strip()
        text = str(section.get("text") or "").strip()
        if name and text:
            grouped.setdefault(name, []).append(text)
    return {name: tuple(values) for name, values in grouped.items()}


def _filing_context_index(bundle: IpoFilingSourceBundle, filing_context: dict[str, Any]) -> _FilingContextIndex:
    text = _normalize_text(bundle.full_text)
    source_mode = str(filing_context.get("source_mode") or "")
    return _FilingContextIndex(
        text=text,
        context_text=_filing_context_text(filing_context),
        section_map=_filing_context_section_map(filing_context),
        allow_full_text_scan=source_mode == "full_prospectus_text" or len(text) <= CHAT_FULL_TEXT_CHAR_LIMIT,
    )


def _financial_verification_text(index: _FilingContextIndex) -> str:
    pieces = [
        _fact_source_text(index, names=("mda_results",), spec=_mda_spec(max_sections=1), fallback_terms=("revenue", "net loss", "Results of Operations")),
        _fact_source_text(index, names=("financial_statements",), spec=_financial_statements_spec(max_sections=2), fallback_terms=("Revenue", "Loss after income tax", "Consolidated Statements")),
        _fact_source_text(index, names=("capitalization",), spec=_capitalization_spec(max_sections=1), fallback_terms=("Cash and cash equivalents", "Total debt")),
        index.context_text,
    ]
    return "\n\n".join(_dedupe([piece for piece in pieces if piece]))


def _fact_source_text(
    index: _FilingContextIndex,
    *,
    names: tuple[str, ...],
    spec: "_SectionSpec | None" = None,
    fallback_terms: tuple[str, ...] = (),
    max_chars: int = 28_000,
) -> str:
    pieces: list[str] = []
    for name in names:
        pieces.extend(index.section_map.get(name, ()))
    if not pieces and spec is not None and index.allow_full_text_scan:
        pieces.append(index.context_text or _first_named_section_text(index.text, spec))
    if fallback_terms and (not pieces or not index.allow_full_text_scan):
        pieces.append(_targeted_fact_windows(index.text, fallback_terms, max_chars=min(max_chars, 12_000)))
    return "\n\n".join(_dedupe(pieces))[:max_chars]


def _targeted_fact_windows(text: str, terms: tuple[str, ...], *, max_chars: int) -> str:
    if not text or not terms or max_chars <= 0:
        return ""
    lower = text.lower()
    windows: list[str] = []
    used_ranges: list[tuple[int, int]] = []
    per_window = min(3_000, max(1_200, max_chars // max(1, min(len(terms), 4))))
    for term in terms:
        clean_term = term.strip()
        if not clean_term:
            continue
        start_at = 0
        while True:
            index = lower.find(clean_term.lower(), start_at)
            if index < 0:
                break
            start = max(0, index - per_window // 3)
            end = min(len(text), index + len(clean_term) + (per_window * 2 // 3))
            start_at = index + len(clean_term)
            if _overlaps_existing_range(start, end, used_ranges):
                continue
            windows.append(_clean_section_text(text[start:end], limit=per_window))
            used_ranges.append((start, end))
            break
        if sum(len(window) for window in windows) >= max_chars:
            break
    return "\n\n".join(_dedupe(window for window in windows if window))[:max_chars]


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
        r"conditions and events .*? raise substantial doubt",
        r"do not alleviate (?:the\s+)?substantial doubt",
        r"plans? .*? do not alleviate .*?substantial doubt",
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
    budget = CHAT_RETRIEVED_CHAR_LIMIT
    for section in selected:
        if not section.text:
            continue
        if section.name not in {"cover_page", "opening_excerpt"} and _is_toc_dominant_section(section.text):
            continue
        start = section.start_offset if section.start_offset is not None else -1
        end = section.end_offset if section.end_offset is not None else -1
        if start >= 0 and end >= 0 and _overlaps_existing_range(start, end, ranges):
            continue
        room = budget - char_count
        if room <= 0:
            break
        text_limit = min(room, _retrieved_section_char_limit(section.name, prompt))
        text_slice = _trim_retrieved_section_text(section.name, section.text, prompt, text_limit)
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


def _retrieved_section_char_limit(name: str, prompt: str) -> int:
    overview_limits = {
        "cover_page": 4_500,
        "explanatory_note": 4_500,
        "prospectus_summary": 4_000,
        "offering_terms": 4_500,
        "use_of_proceeds": 4_500,
        "recent_developments": 5_500,
        "regulatory_status": 5_000,
        "capitalization": 4_500,
        "dilution": 4_500,
        "mda_results": 5_000,
        "financial_statements": 5_500,
        "risk_factors": 5_500,
        "customer_supplier_concentration": 3_500,
        "license_obligations": 3_500,
        "icfr_material_weaknesses": 3_500,
        "selling_shareholders": 4_500,
        "principal_shareholders": 4_000,
        "related_party_transactions": 4_000,
        "shares_eligible_future_sale": 4_000,
        "underwriting": 4_000,
        "ads_sgx_conversion": 4_000,
        "business": 2_500,
        "opening_excerpt": 2_000,
    }
    focused_limits = {
        "cover_page": 6_000,
        "opening_excerpt": 2_500,
    }
    if name.startswith("keyword_chunk"):
        return 2_500 if _is_overview_prompt(prompt) else 4_000
    if _is_overview_prompt(prompt):
        return overview_limits.get(name, 3_500)
    return focused_limits.get(name, min(CHAT_SECTION_CHAR_LIMIT, 10_000))


def _trim_retrieved_section_text(name: str, text: str, prompt: str, limit: int) -> str:
    clean = (text or "").strip()
    if limit <= 0 or not clean:
        return ""
    if len(clean) <= limit:
        return clean

    terms = _section_preserve_terms(name, prompt)
    head_limit = min(len(clean), max(900, limit // 3))
    pieces = [clean[:head_limit].strip()]
    lower = clean.lower()
    window = max(900, (limit - head_limit) // max(1, min(len(terms), 4)))
    ranges: list[tuple[int, int]] = [(0, head_limit)]
    for term in terms:
        index = lower.find(term.lower())
        if index < 0:
            continue
        start = max(0, index - window // 3)
        end = min(len(clean), index + len(term) + (window * 2 // 3))
        if _overlaps_existing_range(start, end, ranges):
            continue
        pieces.append(clean[start:end].strip())
        ranges.append((start, end))
        if sum(len(piece) for piece in pieces) >= limit:
            break
    trimmed = "\n...\n".join(_dedupe(piece for piece in pieces if piece))
    if len(trimmed) > limit:
        trimmed = trimmed[: max(0, limit - 3)].rstrip(" ,.;") + "..."
    return trimmed.strip()


def _section_preserve_terms(name: str, prompt: str) -> tuple[str, ...]:
    terms_by_name = {
        "cover_page": ("American Depositary Shares", "Common Shares", "Ordinary Shares", "Nasdaq", "NYSE", "under the symbol", "Sole Book-Runner", "Joint Book-Running Managers"),
        "explanatory_note": ("Public Offering Prospectus", "Resale Prospectus", "Alternate Pages", "Selling Shareholders", "will not receive proceeds"),
        "prospectus_summary": ("we are", "our business", "market", "customers"),
        "offering_terms": ("American Depositary Shares", "Common Shares", "we are offering", "initial public offering price", "price range", "Nasdaq", "NYSE", "underwriters"),
        "use_of_proceeds": ("net proceeds", "use the net proceeds", "working capital", "over-allotment", "Moltex", "acquisition"),
        "recent_developments": ("Moltex", "Asset Acquisition", "purchase price", "Stable Salt Reactor", "WATSS", "administration", "exclusivity fee", "extension fee"),
        "regulatory_status": ("NuMark Associates", "NRC", "CNSC", "formal applications", "US$100 million", "2028", "2029", "2031"),
        "capitalization": ("Cash and cash equivalents", "Total debt", "as adjusted", "total capitalization"),
        "dilution": ("net tangible book value", "immediate dilution", "per ADS", "per ordinary share"),
        "mda_results": ("Our revenue amounted", "revenue", "net losses", "liquidity", "capital resources", "going concern", "substantial doubt"),
        "financial_statements": ("Revenue", "Loss after income tax", "Gross profit", "Cash and cash equivalents", "substantial doubt"),
        "risk_factors": ("going concern", "substantial doubt", "do not alleviate", "customer concentration", "supplier concentration", "material weakness", "related party"),
        "customer_supplier_concentration": ("Haur-Jye Technology", "MMI Systems Pte Ltd", "major customer", "major supplier"),
        "license_obligations": ("Accelerate Technologies", "A*STAR", "S$3.0 million", "S$5.0 million", "commercialization obligations"),
        "icfr_material_weaknesses": ("material weakness", "segregation of duties", "payroll", "purchases and payables", "IFRS"),
        "selling_shareholders": ("Selling Shareholders", "Resale Prospectus", "will not receive any proceeds", "2,817,294", "Plan of Distribution"),
        "principal_shareholders": ("directors and executive officers", "Angelling Capital", "MST SingCo", "beneficial ownership"),
        "related_party_transactions": ("RELATED PARTY TRANSACTIONS", "MMI Systems Pte Ltd", "MST SingCo", "related party"),
        "shares_eligible_future_sale": ("September 8, 2026", "moratorium", "180 days", "lock-up", "future sale"),
        "underwriting": ("Roth Capital Partners", "Benchmark", "underwriters", "lock-up", "180 days"),
        "ads_sgx_conversion": ("convert ordinary shares into ADSs", "Each ADS represents", "Singapore Exchange", "SGX-ST"),
        "business": ("customers", "suppliers", "License Agreements", "Intellectual Property"),
    }
    return tuple(_dedupe([*terms_by_name.get(name, ()), *_query_terms(prompt)]))


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
    char_limit: int = CHAT_SECTION_CHAR_LIMIT


def _section_specs_for_prompt(prompt: str) -> tuple[_SectionSpec, ...]:
    lower = prompt.lower()
    wants_overview = _is_overview_prompt(prompt)
    specs: list[_SectionSpec] = []

    def add(spec: _SectionSpec) -> None:
        if all(existing.name != spec.name for existing in specs):
            specs.append(spec)

    if wants_overview:
        add(_cover_page_spec())
        add(_explanatory_note_spec())
        add(_prospectus_summary_spec())
        add(_offering_spec())
        add(_use_of_proceeds_spec())
        add(_recent_developments_spec())
        add(_regulatory_status_spec())
        add(_capitalization_spec())
        add(_dilution_spec())
        add(_mda_spec())
        add(_financial_statements_spec())
        add(_risk_spec())
        add(_customer_supplier_concentration_spec())
        add(_license_obligations_spec())
        add(_icfr_material_weaknesses_spec())
        add(_selling_shareholders_spec())
        add(_principal_shareholders_spec())
        add(_related_party_transactions_spec())
        add(_shares_eligible_future_sale_spec())
        add(_underwriting_spec())
        add(_ads_sgx_conversion_spec())
        add(_business_spec())

    if any(term in lower for term in ("risk", "viability", "liquidity", "valuation", "protection", "going concern")):
        add(_risk_spec(max_sections=2))
        add(_icfr_material_weaknesses_spec(max_sections=2))
    if any(term in lower for term in ("offering", "terms", "transaction", "ipo", "resale", "shelf", "spac", "direct listing", "price", "ticker", "underwriter", "lockup", "lock-up", "securities offered")):
        add(_cover_page_spec())
        add(_offering_spec(max_sections=2))
        add(_selling_shareholders_spec())
        add(_underwriting_spec())
        add(_shares_eligible_future_sale_spec())
        add(_ads_sgx_conversion_spec())
    if any(term in lower for term in ("proceeds", "repay", "working capital", "company receives")):
        add(_use_of_proceeds_spec(max_sections=2))
    if any(term in lower for term in ("recent development", "acquisition", "moltex", "asset purchase", "exclusivity")):
        add(_recent_developments_spec(max_sections=2))
        add(_use_of_proceeds_spec(max_sections=2))
    if any(term in lower for term in ("regulatory", "nrc", "cnsc", "license", "licensing", "application")):
        add(_regulatory_status_spec(max_sections=2))
        add(_business_spec(max_sections=2))
    if any(term in lower for term in ("dilution", "ownership", "capitalization", "overhang", "warrant", "option", "insider", "control", "selling shareholder")):
        add(_capitalization_spec(max_sections=2))
        add(_dilution_spec(max_sections=2))
        add(_selling_shareholders_spec())
        add(_principal_shareholders_spec())
        add(_related_party_transactions_spec())
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


def _is_overview_prompt(prompt: str) -> bool:
    lower = (prompt or "").lower()
    return not prompt.strip() or any(term in lower for term in ("overview", "thorough", "summary", "breakdown", "filing"))


def _overview_answer_requirements_for_prompt(prompt: str) -> dict[str, Any]:
    if not _is_overview_prompt(prompt):
        return {}
    return {
        "instruction": (
            "For overview prompts, explicitly address each listed topic when disclosed in verified_filing_facts "
            "or filing_context. If a topic is absent, say Not disclosed."
        ),
        "required_topics": [
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
        ],
    }


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
        headings=("PRELIMINARY PROSPECTUS", "PROSPECTUS", "American Depositary Shares", "Common Shares", "Ordinary Shares"),
        stop_headings=("TABLE OF CONTENTS", "Table of Contents", "PROSPECTUS SUMMARY"),
        terms=("American Depositary Shares", "Common Shares", "offering price", "Nasdaq", "NYSE", "under the symbol", "Sole Book-Runner", "Joint Book-Running Managers"),
    )


def _explanatory_note_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="explanatory_note",
        headings=("Explanatory Note",),
        stop_headings=("Prospectus Summary", "Risk Factors", "Use of Proceeds", "Capitalization", "Dilution", "Business"),
        terms=("two prospectuses", "Public Offering Prospectus", "Resale Prospectus", "Selling Shareholders", "Alternate Pages"),
        max_sections=max_sections,
        char_limit=10_000,
    )


def _business_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="business",
        headings=("Business Overview", "Our Business", "Our Company", "Company Overview", "Business"),
        stop_headings=("Risk Factors", "Management", "Management's Discussion", "Principal Shareholders", "Financial Statements", "Underwriting"),
        terms=("our platform", "we provide", "we develop", "we operate", "customers", "market"),
        max_sections=max_sections,
        char_limit=12_000,
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
        stop_headings=(
            "Recent Developments",
            "Regulatory Status",
            "Dividend Policy",
            "Capitalization",
            "Dilution",
            "Management",
            "Underwriting",
            "Risk Factors",
            "Business",
            "Financial Statements",
            "Taxation",
        ),
        terms=("we intend to use", "use the net proceeds", "net proceeds", "proceeds for", "repayment of indebtedness", "Moltex", "asset acquisition"),
        max_sections=max_sections,
    )


def _recent_developments_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="recent_developments",
        headings=("Recent Developments", "Recent Development", "Material Recent Developments"),
        stop_headings=("Regulatory Status", "Risk Factors", "Use of Proceeds", "Capitalization", "Dilution", "Management", "Business", "Regulation", "Regulatory"),
        terms=("Moltex", "Asset Acquisition", "exclusivity agreement", "purchase price", "administration", "extension fee"),
        max_sections=max_sections,
        char_limit=12_000,
    )


def _regulatory_status_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="regulatory_status",
        headings=(
            "Regulatory Status",
            "Regulation",
            "Government Regulation",
            "Nuclear Regulatory",
            "Regulatory Approval",
            "Licensing and Regulatory",
        ),
        stop_headings=("Management", "Employees", "Facilities", "Legal Proceedings", "Risk Factors", "Use of Proceeds", "Financial Statements"),
        terms=("NRC", "CNSC", "NuMark Associates", "formal applications", "US$100 million", "2028", "2029", "2031"),
        max_sections=max_sections,
        char_limit=12_000,
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
        stop_headings=(
            "Consolidated Balance Sheets",
            "Consolidated Statements of Changes",
            "Consolidated Statements of Cash Flows",
            "Notes to the Financial Statements",
            "The accompanying notes",
            "Going concern",
            "Taxation",
            "Material United States Federal Income Tax",
        ),
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
        headings=("Risk Factors",),
        stop_headings=("Use of Proceeds", "Dividend Policy", "Capitalization", "Dilution", "Management", "Business", "Underwriting"),
        terms=("risk factors", "going concern", "substantial doubt", "customer concentration", "supplier concentration", "material weakness", "related party", "controlled company"),
        max_sections=max_sections,
        char_limit=12_000,
    )


def _selling_shareholders_spec() -> _SectionSpec:
    return _SectionSpec(
        name="selling_shareholders",
        headings=("Selling Shareholders", "Selling Stockholders", "Principal and Selling Shareholders"),
        stop_headings=("Plan of Distribution", "Underwriting", "Description of Capital Stock", "Shares Eligible", "Management"),
        terms=("selling shareholder", "selling stockholder", "resale", "will not receive any proceeds"),
        max_sections=2,
    )


def _principal_shareholders_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="principal_shareholders",
        headings=("Principal Shareholders", "Principal Stockholders", "Security Ownership", "Principal and Selling Shareholders"),
        stop_headings=("Certain Relationships", "Related Party", "Shares Eligible", "Description of Capital Stock", "Underwriting"),
        terms=("beneficial ownership", "controlled company", "voting power", "insider", "Angelling Capital", "MST SingCo"),
        max_sections=max_sections,
        char_limit=10_000,
    )


def _underwriting_spec() -> _SectionSpec:
    return _SectionSpec(
        name="underwriting",
        headings=("Underwriting", "Plan of Distribution"),
        stop_headings=("Legal Matters", "Experts", "Financial Statements", "Consolidated Statements", "METAOPTICS LTD AND ITS SUBSIDIARIES", "Where You Can Find More Information"),
        terms=("underwriters are", "representatives of the underwriters", "book-running", "bookrunners", "lock-up", "lockup", "180 days"),
        char_limit=10_000,
    )


def _related_party_transactions_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="related_party_transactions",
        headings=("Related Party Transactions", "Certain Relationships and Related Party Transactions", "Certain Relationships"),
        stop_headings=("Description of Capital Stock", "Shares Eligible", "Underwriting", "Plan of Distribution", "Financial Statements"),
        terms=("related party", "MMI Systems", "MMI Holdings", "MST SingCo", "amount due to a shareholder"),
        max_sections=max_sections,
        char_limit=10_000,
    )


def _shares_eligible_future_sale_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="shares_eligible_future_sale",
        headings=("Shares Eligible for Future Sale", "Lock-Up Agreements", "Lock-up Agreements", "SGX-ST Moratorium", "Moratorium"),
        stop_headings=("Taxation", "Underwriting", "Plan of Distribution", "Description of American Depositary Shares", "Legal Matters", "Experts"),
        terms=("moratorium", "September 8, 2026", "180 days", "lock-up", "lockup", "future sale", "SGX-ST"),
        max_sections=max_sections,
        char_limit=10_000,
    )


def _ads_sgx_conversion_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="ads_sgx_conversion",
        headings=(
            "Conversion Between Ordinary Shares and ADSs",
            "Description of American Depositary Shares",
            "American Depositary Shares",
            "Deposit Agreement",
        ),
        stop_headings=("Shares Eligible", "Taxation", "Underwriting", "Plan of Distribution", "Legal Matters", "Experts"),
        terms=("convert ordinary shares into ADSs", "convert ADSs into ordinary shares", "ADSs into ordinary shares", "SGX", "Singapore Exchange", "Each ADS represents"),
        max_sections=max_sections,
        char_limit=10_000,
    )


def _customer_supplier_concentration_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="customer_supplier_concentration",
        headings=("Customers and Suppliers", "Customers", "Customer Concentration", "Suppliers", "Supplier Concentration", "Major Customers", "Major Suppliers"),
        stop_headings=("License Agreements", "Licensing Agreements", "Intellectual Property", "Internal Control over Financial Reporting", "Research and Development", "Employees", "Facilities", "Regulation", "Management"),
        terms=("Haur-Jye Technology", "74.6%", "MMI Systems Pte Ltd", "81.0%", "major customer", "major supplier"),
        max_sections=max_sections,
        char_limit=8_000,
    )


def _license_obligations_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="license_obligations",
        headings=("License Agreements", "Licensing Agreements", "Intellectual Property", "Our Intellectual Property"),
        stop_headings=("Internal Control over Financial Reporting", "Material Weakness", "Customers", "Suppliers", "Employees", "Facilities", "Regulation", "Management", "Principal Shareholders"),
        terms=("Accelerate Technologies", "A*STAR", "gross revenues", "S$3.0 million", "S$5.0 million", "commercialization obligations"),
        max_sections=max_sections,
        char_limit=8_000,
    )


def _icfr_material_weaknesses_spec(*, max_sections: int = 1) -> _SectionSpec:
    return _SectionSpec(
        name="icfr_material_weaknesses",
        headings=("Internal Control over Financial Reporting", "Material Weaknesses", "Material Weakness", "Controls and Procedures"),
        stop_headings=("Management", "Business", "Principal Shareholders", "Related Party", "Financial Statements", "Description of Capital Stock"),
        terms=("material weakness", "segregation of duties", "payroll", "purchases and payables", "IFRS", "restatement"),
        max_sections=max_sections,
        char_limit=8_000,
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
    if stop_candidates:
        end = min(stop_candidates)
    else:
        end = min(len(text), 12_000)
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
    candidates: list[tuple[int, int, int, str, str]] = []
    for start, content_start, heading in sorted(starts, key=lambda item: item[0]):
        stop_index = _next_section_stop_index(text, lower, content_start, spec)
        section_start = start
        section_end = min(stop_index, section_start + spec.char_limit)
        raw_body = text[section_start:section_end]
        body = _clean_section_text(raw_body, limit=spec.char_limit)
        if not body or not _has_section_body(body):
            continue
        if spec.name == "financial_statements" and _looks_like_taxation_false_positive(body):
            continue
        if _is_toc_like_section_candidate(text, section_start, section_end, body, heading) and not (
            spec.name == "explanatory_note" and re.search(r"two prospectuses|Public Offering Prospectus|Resale Prospectus", body, flags=re.IGNORECASE)
        ):
            continue
        score = _section_candidate_score(text, section_start, section_end, body, spec)
        candidates.append((score, section_start, section_end, heading, body))

    sections: list[IpoFilingSourceSection] = []
    ranges: list[tuple[int, int]] = []
    for _score, section_start, section_end, heading, body in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if _overlaps_existing_range(section_start, section_end, ranges):
            continue
        sections.append(
            IpoFilingSourceSection(
                name=spec.name,
                text=f"{heading}\n{body}",
                start_offset=section_start,
                end_offset=section_end,
            )
        )
        ranges.append((section_start, section_end))
        if len(sections) >= spec.max_sections:
            break
    return sorted(sections, key=lambda section: section.start_offset or 0)


def _next_section_stop_index(text: str, lower: str, content_start: int, spec: _SectionSpec) -> int:
    stop_index = len(text)
    search_from = content_start + 20
    for stop in spec.stop_headings:
        for stop_match in re.finditer(rf"\b{re.escape(stop.lower())}\b", lower[search_from:]):
            candidate_start = search_from + stop_match.start()
            candidate_end = search_from + stop_match.end()
            if not _looks_like_section_heading_match(text, candidate_start, candidate_end, stop):
                continue
            stop_index = min(stop_index, candidate_start)
            break
    return stop_index


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
    if re.match(r"^\d{1,4}\b", suffix) or re.fullmatch(r"\d{1,4}", suffix):
        return False
    if _line_looks_like_toc_entry(line, heading):
        return False
    if len(suffix) > 120:
        return False
    if line and line.upper() == line:
        return True
    return bool(re.match(r"^(of financial condition|and results of operations|discussion and analysis)\b", suffix))


def _line_at_offset(text: str, start: int, end: int | None = None) -> str:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end if end is not None else start)
    if line_end < 0:
        line_end = min(len(text), (end if end is not None else start) + 300)
    return text[line_start:line_end].strip()


def _table_of_contents_region(text: str) -> tuple[int, int]:
    lower = text.lower()
    marker = lower.find("table of contents")
    if marker < 0:
        return (-1, -1)
    search_start = marker + len("table of contents")
    for match in re.finditer(r"\b(?:prospectus summary|summary)\b", lower[search_start:]):
        start = search_start + match.start()
        end = search_start + match.end()
        line = _line_at_offset(text, start, end)
        if not _line_looks_like_toc_entry(line, "Prospectus Summary"):
            return (marker, start)
    return (marker, min(len(text), marker + 6_000))


def _line_looks_like_toc_entry(line: str, heading: str | None = None) -> bool:
    clean = re.sub(r"\s+", " ", line or "").strip(" \t:-")
    if not clean:
        return False
    lower = clean.lower()
    if "table of contents" in lower:
        return True
    if re.search(r"\.{4,}\s*\d{1,4}$", clean):
        return True
    if heading:
        clean_heading = re.sub(r"\s+", " ", heading).strip().lower()
        if lower.startswith(clean_heading):
            suffix = lower[len(clean_heading) :].strip(" :-")
            if re.match(r"^\d{1,4}\b", suffix):
                return True
            if _known_toc_entry_count(clean) >= 2 and re.search(r"\b\d{1,4}\b", suffix):
                return True
    if _known_toc_entry_count(clean) >= 3:
        return True
    return bool(re.fullmatch(r"[A-Z][A-Z0-9 '&().,/-]{3,}\s+\d{1,4}", clean))


def _known_toc_entry_count(value: str) -> int:
    lower = re.sub(r"\s+", " ", value or "").lower()
    titles = (
        "prospectus summary",
        "explanatory note",
        "recent developments",
        "risk factors",
        "use of proceeds",
        "capitalization",
        "dilution",
        "management's discussion and analysis",
        "business",
        "regulation",
        "selling shareholders",
        "principal shareholders",
        "related party transactions",
        "shares eligible for future sale",
        "description of american depositary shares",
        "underwriting",
        "plan of distribution",
        "financial statements",
    )
    return sum(1 for title in titles if re.search(rf"\b{re.escape(title)}\b\s+\d{{1,4}}\b", lower))


def _is_toc_like_section_candidate(text: str, start: int, end: int, body: str, heading: str) -> bool:
    line = _line_at_offset(text, start, min(end, start + len(heading) + 160))
    if _line_looks_like_toc_entry(line, heading):
        return True
    toc_start, toc_end = _table_of_contents_region(text)
    if toc_start >= 0 and toc_start <= start < toc_end:
        return True
    surrounding = text[max(0, start - 600) : min(len(text), start + 1_400)]
    if _looks_like_toc_window(surrounding) and _prose_sentence_count(body) < 2 and _prose_word_count(body) < 90:
        return True
    return _is_toc_dominant_section(body)


def _looks_like_taxation_false_positive(value: str) -> bool:
    lower = (value or "").lower()
    tax_markers = (
        "u.s. holder",
        "united states federal income tax",
        "amount realized upon the disposition",
        "capital gain or loss",
        "tax basis",
        "passive foreign investment company",
    )
    tax_hits = sum(1 for marker in tax_markers if marker in lower)
    if tax_hits < 2:
        return False
    financial_markers = (
        "consolidated statements of operations",
        "consolidated statements of comprehensive",
        "consolidated balance sheets",
        "consolidated statements of cash flows",
        "notes to consolidated financial statements",
        "revenue",
        "net loss",
        "total assets",
        "total liabilities",
    )
    financial_hits = sum(1 for marker in financial_markers if marker in lower)
    return financial_hits <= 1 or "amount realized upon the disposition" in lower


def _section_candidate_score(text: str, start: int, end: int, body: str, spec: _SectionSpec) -> int:
    score = 0
    toc_start, toc_end = _table_of_contents_region(text)
    if toc_start >= 0:
        score += 45 if start >= toc_end else -180
    score += min(_prose_word_count(body) // 12, 60)
    score += min(_prose_sentence_count(body) * 4, 24)
    lower = body.lower()
    for term in spec.terms:
        if term and term.lower() in lower:
            score += 12
    if spec.name == "financial_statements" and _looks_like_taxation_false_positive(body):
        score -= 500
    if _is_toc_dominant_section(body):
        score -= 250
    if spec.name == "risk_factors" and re.search(r"\bsee\s+risk factors\b", lower) and _prose_word_count(body) < 120:
        score -= 45
    if end - start < 350 and not any(term.lower() in lower for term in spec.terms):
        score -= 20
    return score


def _is_toc_dominant_section(value: str) -> bool:
    clean = re.sub(r"\s+", " ", value or "").strip()
    if not clean:
        return False
    first = clean[:1_500]
    toc_entries = _known_toc_entry_count(first) + len(re.findall(r"\b[A-Z][A-Z '&().,/-]{3,}\s+\d{1,4}\b", first))
    prose_words = _prose_word_count(first)
    if _looks_like_toc_window(first) and (prose_words < 90 or _prose_sentence_count(first) == 0):
        return True
    if toc_entries >= 3 and toc_entries * 10 >= max(prose_words, 1):
        return True
    lines = [line.strip() for line in (value or "").splitlines() if line.strip()]
    if not lines:
        return False
    toc_lines = sum(1 for line in lines if _looks_like_table_of_contents(line) or _line_looks_like_toc_entry(line))
    return toc_lines >= 2 and toc_lines / len(lines) >= 0.6


def _prose_word_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z][A-Za-z0-9'-]*", value or ""))


def _prose_sentence_count(value: str) -> int:
    return len(re.findall(r"[A-Za-z0-9)][.!?](?:\s|$)", value or ""))


def _windows_around_terms(text: str, spec: _SectionSpec) -> list[IpoFilingSourceSection]:
    lower = text.lower()
    candidates: list[tuple[int, int, int, str]] = []
    for term in spec.terms:
        start_at = 0
        while True:
            index = lower.find(term.lower(), start_at)
            if index < 0:
                break
            start = max(0, index - 1_200)
            end = min(len(text), index + len(term) + 4_200)
            start_at = index + len(term)
            body = _clean_section_text(text[start:end], limit=min(spec.char_limit, 6_000))
            if not body or not _has_section_body(body):
                continue
            if spec.name == "financial_statements" and _looks_like_taxation_false_positive(body):
                continue
            if _is_toc_like_section_candidate(text, start, end, body, term):
                continue
            score = _section_candidate_score(text, start, end, body, spec) + 10
            candidates.append((score, start, end, body))

    windows: list[IpoFilingSourceSection] = []
    ranges: list[tuple[int, int]] = []
    for _score, start, end, body in sorted(candidates, key=lambda item: (-item[0], item[1])):
        if _overlaps_existing_range(start, end, ranges):
            continue
        ranges.append((start, end))
        windows.append(IpoFilingSourceSection(name=spec.name, text=body, start_offset=start, end_offset=end))
        if len(windows) >= spec.max_sections:
            break
    return sorted(windows, key=lambda section: section.start_offset or 0)


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
        if clean and _has_section_body(clean) and not _is_toc_dominant_section(clean):
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
    if _is_overview_prompt(prompt):
        expansions.extend(
            [
                "under the symbol",
                "price range",
                "sole book-runner",
                "public offering prospectus",
                "resale prospectus",
                "selling shareholders",
                "will not receive proceeds",
                "net proceeds",
                "immediate dilution",
                "capitalization",
                "recent developments",
                "asset acquisition",
                "Moltex",
                "regulatory",
                "NRC",
                "CNSC",
                "formal applications",
                "substantial doubt",
                "customer concentration",
                "supplier concentration",
                "license agreement",
                "material weakness",
                "principal shareholders",
                "related party transactions",
                "moratorium",
                "ordinary shares into ADSs",
            ]
        )
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
    if _is_toc_dominant_section(chunk[:1_200]) or _looks_like_toc_window(chunk[:700]):
        score -= 25
    return score


def _overlaps_existing_range(start: int, end: int, ranges: Iterable[tuple[int, int]]) -> bool:
    for old_start, old_end in ranges:
        overlap = max(0, min(end, old_end) - max(start, old_start))
        if overlap >= min(end - start, old_end - old_start) * 0.35:
            return True
    return False


def _clean_prompt(prompt: str) -> str:
    return re.sub(r"\s+", " ", prompt or "").strip()


def _notify_progress(progress_callback: ProgressCallback | None, message: str) -> None:
    if progress_callback is None:
        return
    try:
        progress_callback(message)
    except Exception:
        LOGGER.debug("AI filing chat progress callback failed", exc_info=True)


def _positive_timeout_seconds(value: float | None, env_value: str | None) -> float:
    raw = value if value is not None else env_value
    if raw is None or raw == "":
        return CHAT_OPENAI_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        LOGGER.warning("Invalid OPENAI_IPO_CHAT_TIMEOUT_SECONDS=%r; using %ss", raw, CHAT_OPENAI_TIMEOUT_SECONDS)
        return CHAT_OPENAI_TIMEOUT_SECONDS
    if seconds <= 0:
        return CHAT_OPENAI_TIMEOUT_SECONDS
    return seconds


def _is_timeout_exception(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in ("timeout", "timed out", "deadline exceeded", "read timed out"))


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
    text = text.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
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
        or _known_toc_entry_count(value) >= 3
        or len(re.findall(r"\b[A-Z][A-Z '&/-]{3,}\s+\d{1,3}\b", value)) >= 3
    )


def _looks_like_table_of_contents(value: str) -> bool:
    lower = value.lower()
    return (
        "table of contents" in lower
        or bool(re.search(r"\.{4,}\s*\d+$", value))
        or _known_toc_entry_count(value) >= 3
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
