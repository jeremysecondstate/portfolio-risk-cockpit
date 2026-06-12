from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from app.analytics.earnings_pipeline import RecentEarningsRecord
from app.analytics.openai_ipo_report import _redact_api_key, _response_output_text
from app.analytics.symbol_chat import (
    OpenAiSymbolChatClient,
    SYMBOL_CHAT_APPROX_CHARS_PER_TOKEN,
    SYMBOL_CHAT_REQUEST_CHAR_LIMIT,
    redact_symbol_chat_secrets,
)


LOGGER = logging.getLogger(__name__)

EARNINGS_AI_REQUEST_CHAR_LIMIT = SYMBOL_CHAT_REQUEST_CHAR_LIMIT

EARNINGS_AI_SYSTEM_PROMPT = """You are an earnings intelligence analyst inside Portfolio Risk Cockpit.

Analyze only the selected Earnings Radar record and the explicit selected-row context in the request.
Do not invent missing revenue, EPS, guidance, catalysts, filing text, or source links.
When a field is absent, say "Not available in the selected Earnings Radar record."
Treat parsed values and risk flags as deterministic extraction outputs, not audited conclusions.
Treat source snippets as filing/exhibit excerpts or index hints only when they are provided.

Keep the response research-only. Do not place trades. Do not tell the user to buy, sell, or hold.
You may explain what changed, what the filing appears to show, what is uncertain, and what to verify next.
Separate confirmed selected-row facts from assumptions, caveats, and diligence questions.
Never include credentials, API keys, account identifiers, or secrets.
"""

EARNINGS_AI_QUICK_PROMPTS: dict[str, str] = {
    "Summarize Drop": (
        "Summarize this earnings drop or formal earnings filing using only the selected Earnings Radar record. "
        "Cover the filing trigger, parsed financial fields, guidance, risk flags, source links, and key missing data."
    ),
    "What Changed?": (
        "Explain what appears to have changed in this earnings event using only the selected row. "
        "Separate parsed facts from items that require filing review."
    ),
    "Bull vs Bear": (
        "Frame a bull case and bear case after this filing using only selected-row facts. "
        "Label assumptions and list what must be verified before relying on either case."
    ),
    "Guidance + Risks": (
        "Analyze the guidance signal and risk flags from this selected row. "
        "Explain what is detected, what is absent, and what source checks should come next."
    ),
    "Diligence Questions": (
        "Generate focused diligence questions for this selected earnings row. "
        "Prioritize filing/exhibit verification, parsed metric quality, guidance, risks, and follow-up data."
    ),
    "Deeper Research?": (
        "Assess whether this selected earnings row deserves deeper research. "
        "Do not make a trading recommendation; explain the evidence, uncertainty, and next verification steps."
    ),
}

EARNINGS_AI_ANALYZE_PROMPT = (
    "Create a concise earnings intelligence brief for the selected Earnings Radar row. "
    "Include: event trigger, filing/exhibit source, parsed financials, guidance signal, risk flags, "
    "missing data, and concrete verification steps. Keep it research-only."
)

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class EarningsAiResponse:
    answer: str
    response_id: str
    model: str
    source_mode: str
    source_debug: tuple[str, ...]


class OpenAiEarningsRadarError(RuntimeError):
    """Raised for row-grounded Earnings Radar OpenAI failures with secrets redacted."""


class OpenAiEarningsRadarClient:
    def __init__(
        self,
        *,
        openai_client: Any | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._symbol_chat_client = OpenAiSymbolChatClient(
            openai_client=openai_client,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    @property
    def model(self) -> str:
        return self._symbol_chat_client.model

    @property
    def timeout_seconds(self) -> float:
        return self._symbol_chat_client.timeout_seconds

    def analyze(
        self,
        record: RecentEarningsRecord,
        prompt: str,
        *,
        source_snippets: Iterable[str] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> EarningsAiResponse:
        started_at = time.perf_counter()
        clean_prompt = _clean_prompt(prompt)
        if not clean_prompt:
            raise OpenAiEarningsRadarError("Enter an earnings analysis question before sending.")

        _notify_progress(progress_callback, "Preparing selected earnings context...")
        request_payload = earnings_ai_request_payload(
            record,
            clean_prompt,
            source_snippets=source_snippets,
            timeout_seconds=self.timeout_seconds,
        )
        payload_text = _serialize_request_payload(request_payload)
        diagnostics = {
            "request_payload_chars": len(payload_text),
            "request_payload_approx_tokens": _approx_token_count(len(payload_text)),
            "request_payload_char_limit": EARNINGS_AI_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": self.timeout_seconds,
        }
        request_budget = request_payload.get("request_budget") if isinstance(request_payload, Mapping) else {}
        if isinstance(request_budget, Mapping):
            for key in ("pre_trim_payload_chars", "final_payload_chars", "budget_trimmed"):
                if key in request_budget:
                    diagnostics[f"request_payload_{key}"] = request_budget.get(key)

        input_messages = [
            {"role": "system", "content": EARNINGS_AI_SYSTEM_PROMPT},
            {"role": "user", "content": payload_text},
        ]
        try:
            _notify_progress(progress_callback, f"Calling OpenAI (timeout {self.timeout_seconds:g}s)...")
            openai_started = time.perf_counter()
            response = self._symbol_chat_client._client().responses.create(
                model=self.model,
                input=input_messages,
                store=False,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            if _is_timeout_exception(exc):
                message = (
                    f"OpenAI earnings analysis timed out after {self.timeout_seconds:g} seconds. "
                    "Try a narrower selected-row question or retry later."
                )
            else:
                message = f"OpenAI earnings analysis failed: {exc}"
            message = _redact_api_key(message, self._symbol_chat_client._current_api_key())
            message = redact_symbol_chat_secrets(message)
            LOGGER.warning(
                "AI earnings radar request failed accession=%s elapsed=%.3fs error=%s",
                record.accession_number,
                time.perf_counter() - started_at,
                message,
            )
            raise OpenAiEarningsRadarError(message) from None

        diagnostics["openai_seconds"] = round(time.perf_counter() - openai_started, 3)
        diagnostics["total_seconds"] = round(time.perf_counter() - started_at, 3)
        _notify_progress(progress_callback, "OpenAI response received.")
        answer = redact_symbol_chat_secrets(str(_response_output_text(response) or "").strip())
        if not answer:
            raise OpenAiEarningsRadarError("OpenAI earnings analysis returned an empty response.")

        return EarningsAiResponse(
            answer=answer,
            response_id=str(getattr(response, "id", "") or ""),
            model=self.model,
            source_mode="earnings_radar_selected_record",
            source_debug=tuple(_source_debug_lines(request_payload, diagnostics)),
        )


def earnings_ai_request_payload(
    record: RecentEarningsRecord,
    prompt: str,
    *,
    source_snippets: Iterable[str] | None = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    selected = earnings_ai_record_context(record)
    snippets = _source_snippet_payload(source_snippets or _record_source_snippets(record))
    payload = {
        "question": _clean_prompt(prompt),
        "selected_earnings_record": selected,
        "source_snippets": snippets or _not_available("No source text snippet is available in the selected Earnings Radar record."),
        "grounding_rules": [
            "Use only selected_earnings_record, source_snippets, and this request for factual claims.",
            'Say "Not available in the selected Earnings Radar record." when a requested fact is absent.',
            "Treat parsed metrics as extraction outputs from the existing SEC earnings pipeline, not audited conclusions.",
            "Do not infer missing revenue, EPS, guidance, catalysts, filing text, or source links.",
            "Use filing_url and exhibit_url as source pointers only; do not claim to have opened them unless source_snippets contain text.",
            "Keep the answer research-only. Do not place trades, submit orders, automate broker actions, or tell the user to buy, sell, or hold.",
            "Separate confirmed selected-row facts from assumptions, caveats, missing data, and diligence questions.",
        ],
        "request_budget": {
            "request_payload_char_limit": EARNINGS_AI_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": timeout_seconds,
        },
    }
    return _enforce_request_payload_budget(payload)


def earnings_ai_record_context(record: RecentEarningsRecord) -> dict[str, Any]:
    metrics = {
        "revenue": _number_or_missing(record.revenue, "revenue"),
        "revenue_growth_percent": _number_or_missing(record.revenue_growth, "revenue_growth"),
        "eps": _number_or_missing(record.eps, "eps"),
        "net_income": _number_or_missing(record.net_income, "net_income"),
    }
    context = {
        "company_name": _text_or_missing(record.company_name, "company_name"),
        "ticker": _text_or_missing(record.ticker, "ticker"),
        "cik": _text_or_missing(record.cik, "cik"),
        "form": _text_or_missing(record.form, "form"),
        "item": _text_or_missing(record.items, "item"),
        "filing_type": _text_or_missing(record.filing_type, "filing_type"),
        "filed_date": _text_or_missing(record.filed_date, "filed_date"),
        "acceptance_time": _text_or_missing(record.acceptance_datetime, "acceptance_time"),
        "fiscal_period": _text_or_missing(record.fiscal_period, "fiscal_period"),
        "report_date": _text_or_missing(record.report_date, "report_date"),
        "sector": _text_or_missing(record.sector, "sector"),
        "sic": _text_or_missing(record.sic, "sic"),
        "industry": _text_or_missing(record.industry, "industry"),
        "exchange": _text_or_missing(record.exchange, "exchange"),
        "release_title": _text_or_missing(record.release_title, "release_title"),
        "parsed_metrics": metrics,
        "guidance": {
            "mentioned": bool(record.guidance_flag),
            "read": "Guidance mentioned in parsed row." if record.guidance_flag else "Guidance not detected in parsed row.",
        },
        "risk_flags": list(record.risk_flags) if record.risk_flags else _not_available("No risk flags detected in parsed row."),
        "filing_url": _text_or_missing(record.filing_url, "filing_url"),
        "exhibit_url": _text_or_missing(record.exhibit_url, "exhibit_url"),
        "source_label": _text_or_missing(record.source, "source_label"),
        "accession_number": _text_or_missing(record.accession_number, "accession_number"),
        "missing_fields": _missing_fields(record),
        "analysis_policy": {
            "scope": "selected Earnings Radar record only",
            "research_only": True,
            "trading_instructions_allowed": False,
        },
    }
    return _json_safe(context)


def _record_source_snippets(record: RecentEarningsRecord) -> tuple[str, ...]:
    source_excerpt = getattr(record, "source_excerpt", None)
    if source_excerpt:
        return (str(source_excerpt),)
    return ()


def _source_snippet_payload(snippets: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, snippet in enumerate(snippets, start=1):
        clean = _shorten(snippet, 2_500)
        if clean:
            rows.append({"index": index, "text": clean})
    return rows[:4]


def _missing_fields(record: RecentEarningsRecord) -> list[str]:
    checks = {
        "ticker": record.ticker,
        "fiscal_period": record.fiscal_period,
        "report_date": record.report_date,
        "sector": record.sector,
        "sic": record.sic,
        "industry": record.industry,
        "exchange": record.exchange,
        "release_title": record.release_title,
        "revenue": record.revenue,
        "revenue_growth": record.revenue_growth,
        "eps": record.eps,
        "net_income": record.net_income,
        "exhibit_url": record.exhibit_url,
        "source_excerpt": getattr(record, "source_excerpt", None),
    }
    return [field for field, value in checks.items() if value in (None, "")]


def _source_debug_lines(request_payload: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> list[str]:
    selected = request_payload.get("selected_earnings_record") if isinstance(request_payload, Mapping) else {}
    snippets = request_payload.get("source_snippets") if isinstance(request_payload, Mapping) else []
    lines = []
    if isinstance(selected, Mapping):
        for key in ("ticker", "cik", "form", "filing_type", "filed_date", "accession_number"):
            value = selected.get(key)
            if isinstance(value, Mapping):
                value = value.get("status")
            lines.append(f"{key}={value}")
        missing = selected.get("missing_fields") if isinstance(selected.get("missing_fields"), list) else []
        lines.append(f"missing_field_count={len(missing)}")
    lines.append(f"source_snippet_count={len(snippets) if isinstance(snippets, list) else 0}")
    for key, value in diagnostics.items():
        lines.append(f"{key}={value}")
    return [redact_symbol_chat_secrets(line) for line in lines]


def _enforce_request_payload_budget(payload: dict[str, Any]) -> dict[str, Any]:
    pre_trim_chars = len(_serialize_request_payload(payload))
    trimmed = pre_trim_chars > EARNINGS_AI_REQUEST_CHAR_LIMIT
    if trimmed:
        snippets = payload.get("source_snippets")
        if isinstance(snippets, list):
            for snippet in snippets:
                if isinstance(snippet, dict) and isinstance(snippet.get("text"), str):
                    snippet["text"] = _shorten(snippet["text"], 900)
            payload["source_snippets"] = snippets[:2]
    if len(_serialize_request_payload(payload)) > EARNINGS_AI_REQUEST_CHAR_LIMIT:
        selected = payload.get("selected_earnings_record")
        if isinstance(selected, dict) and isinstance(selected.get("missing_fields"), list):
            selected["missing_fields"] = selected["missing_fields"][:20]
        payload["source_snippets"] = _not_available("Source snippets were omitted to fit the OpenAI request budget.")
        trimmed = True
    request_budget = payload.setdefault("request_budget", {})
    if not isinstance(request_budget, dict):
        payload["request_budget"] = request_budget = {}
    request_budget["request_payload_char_limit"] = EARNINGS_AI_REQUEST_CHAR_LIMIT
    request_budget["pre_trim_payload_chars"] = pre_trim_chars
    request_budget["final_payload_chars"] = len(_serialize_request_payload(payload))
    request_budget["budget_trimmed"] = bool(trimmed)
    return _json_safe(payload)


def _text_or_missing(value: Any, field: str) -> Any:
    clean = str(value or "").strip()
    return redact_symbol_chat_secrets(clean) if clean else _not_available(f"{field} is not available in the selected Earnings Radar record.")


def _number_or_missing(value: Any, field: str) -> Any:
    if value is None:
        return _not_available(f"{field} was not extracted for the selected Earnings Radar record.")
    return value


def _not_available(reason: str) -> dict[str, str]:
    return {"status": "Not available in the selected Earnings Radar record.", "reason": reason}


def _serialize_request_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(_json_safe(payload), ensure_ascii=True, sort_keys=True, indent=2)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                continue
            result[key_text] = _json_safe(item)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return redact_symbol_chat_secrets(value)
        return value
    return redact_symbol_chat_secrets(str(value))


def _shorten(value: Any, limit: int) -> str:
    text = " ".join(redact_symbol_chat_secrets(str(value or "")).split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 80)].rstrip() + f"\n\n[Truncated to {limit} characters for Earnings Radar AI context.]"


def _clean_prompt(prompt: str) -> str:
    return " ".join(str(prompt or "").split())


def _notify_progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        LOGGER.debug("AI earnings radar progress callback failed", exc_info=True)


def _is_timeout_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timed out" in message or "read timed out" in message


def _is_secret_key(key: str) -> bool:
    compact = "".join(char for char in key.lower() if char.isalnum())
    return any(part in compact for part in ("token", "secret", "authorization", "apikey", "password", "credential", "cookie", "hashvalue", "accounthash"))


def _approx_token_count(chars: int) -> int:
    return max(1, int(chars / SYMBOL_CHAT_APPROX_CHARS_PER_TOKEN))
