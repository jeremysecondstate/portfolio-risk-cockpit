from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping

from dotenv import load_dotenv

from app.analytics.capital_structure_pressure import analyze_capital_structure_pressure, unknown_capital_structure_report
from app.analytics.openai_ipo_report import DEFAULT_OPENAI_IPO_REPORT_MODEL, _redact_api_key, _response_output_text
from app.analytics.technical_analysis import (
    DEFAULT_COMMAND_CENTER_TIMEFRAMES,
    TechnicalTicket,
    build_technical_command_center_report,
    candles_from_price_history,
    format_technical_command_center_report,
    parse_quote_snapshot,
)
from app.data.sec_edgar import SecEdgarClient
from app.storage.trade_memory_store import TradeMemoryStore


LOGGER = logging.getLogger(__name__)

DEFAULT_SYMBOL_CHAT_DIR = Path("data/symbol_chat")
SYMBOL_CHAT_HISTORY_MESSAGE_LIMIT = 8
SYMBOL_CHAT_OPENAI_TIMEOUT_SECONDS = 120.0
SYMBOL_CHAT_REQUEST_CHAR_LIMIT = 70_000
SYMBOL_CHAT_TECHNICAL_TEXT_LIMIT = 18_000
SYMBOL_CHAT_THESIS_TEXT_LIMIT = 8_000
SYMBOL_CHAT_APPROX_CHARS_PER_TOKEN = 4

SYMBOL_CHAT_SYSTEM_PROMPT = """You are a company and stock analysis assistant inside Portfolio Risk Cockpit.

Analyze the selected symbol using only the provided app context and explicitly available sources.
Do not invent missing financials, filings, quotes, ownership, catalysts, or technical levels.
When a field is absent, say "Not available in the provided context." When a conclusion is uncertain, label it clearly.

Focus on company analysis, stock research, technical-analysis explanation, risk factors, catalysts, bull/bear framing, and diligence questions.
Do not place trades. Do not tell the user to buy, sell, or hold. Do not produce personalized investment advice.
You may explain what the data shows, what would support or weaken a thesis, and what the user may want to investigate next.
Write like a buy-side analyst: clear, structured, specific, skeptical, and readable.
Never include credentials, API keys, account identifiers, or secrets.
"""

SYMBOL_CHAT_QUICK_PROMPTS: dict[str, str] = {
    "Overview": (
        "Generate a company and stock overview for this symbol using the available app context. "
        "Cover business profile if available, current position context if available, technical setup if available, "
        "major risks, catalysts, bull/bear case, and diligence questions."
    ),
    "Bull vs Bear": (
        "Give me the bull case and bear case for this symbol using only the available context. "
        "Separate confirmed facts from assumptions and list the most important things to verify next."
    ),
    "Risks": (
        "Summarize the main risks for this company/stock based on the available context. "
        "Include business, financial, technical, liquidity, event, and portfolio-context risks where available."
    ),
    "Technicals": (
        "Explain the technical setup for this symbol using the available technical-analysis context. "
        "Cover trend, momentum, volatility, support/resistance if available, volume confirmation, invalidation levels, and caveats."
    ),
    "What Changed?": (
        "Explain what appears to have changed recently for this symbol based on the available app context, "
        "recent filings, recent orders, technical analysis, and position data. If context is insufficient, say what data is missing."
    ),
    "Diligence Questions": (
        "Generate a focused diligence checklist for this symbol. Prioritize questions that would help evaluate the company, "
        "stock setup, risk/reward, catalysts, filings, financial condition, and thesis durability."
    ),
}

ProgressCallback = Callable[[str], None]


class OpenAiSymbolChatError(RuntimeError):
    """Raised for symbol chat failures with credentials redacted."""


@dataclass(frozen=True)
class SymbolChatContext:
    symbol: str
    company_name: str = ""
    schwab_position: dict[str, Any] = field(default_factory=dict)
    quote_snapshot: dict[str, Any] = field(default_factory=dict)
    technical_analysis: dict[str, Any] = field(default_factory=dict)
    open_orders_summary: tuple[dict[str, Any], ...] = ()
    recent_orders_summary: tuple[dict[str, Any], ...] = ()
    recent_filings_summary: tuple[dict[str, Any], ...] = ()
    saved_thesis_or_notes: str = ""
    source_metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return f"{self.symbol} - {self.company_name}" if self.company_name else self.symbol


@dataclass(frozen=True)
class SymbolChatMessage:
    role: Literal["user", "assistant"]
    content: str
    source_mode: str = ""
    source_debug: tuple[str, ...] = ()


@dataclass(frozen=True)
class SymbolChatResponse:
    answer: str
    response_id: str
    model: str
    source_mode: str
    source_debug: tuple[str, ...]


class SymbolChatSession:
    def __init__(
        self,
        context: SymbolChatContext,
        *,
        chat_client: "OpenAiSymbolChatClient | None" = None,
    ) -> None:
        self.context = context
        self.chat_client = chat_client or OpenAiSymbolChatClient()
        self.messages: list[SymbolChatMessage] = []
        self.last_response_id = ""

    @property
    def model(self) -> str:
        return self.chat_client.model

    def ask(self, prompt: str, *, progress_callback: ProgressCallback | None = None) -> SymbolChatResponse:
        clean_prompt = _clean_prompt(prompt)
        if not clean_prompt:
            raise OpenAiSymbolChatError("Enter a symbol analysis question before sending.")

        response = self.chat_client.ask(self.context, self.messages, clean_prompt, progress_callback=progress_callback)
        self.messages.append(SymbolChatMessage(role="user", content=clean_prompt))
        self.messages.append(
            SymbolChatMessage(
                role="assistant",
                content=response.answer,
                source_mode=response.source_mode,
                source_debug=response.source_debug,
            )
        )
        self.last_response_id = response.response_id
        return response


class OpenAiSymbolChatClient:
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
        self.model = (model or os.getenv("OPENAI_SYMBOL_CHAT_MODEL") or os.getenv("OPENAI_IPO_REPORT_MODEL") or DEFAULT_OPENAI_IPO_REPORT_MODEL).strip()
        self.timeout_seconds = _positive_timeout_seconds(timeout_seconds, os.getenv("OPENAI_SYMBOL_CHAT_TIMEOUT_SECONDS"))

    def ask(
        self,
        context: SymbolChatContext,
        history: Iterable[SymbolChatMessage],
        prompt: str,
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> SymbolChatResponse:
        started_at = time.perf_counter()
        history_messages = list(history)
        _notify_progress(progress_callback, "Preparing symbol context...")
        request_payload = symbol_chat_request_payload(context, prompt, timeout_seconds=self.timeout_seconds)
        payload_text = _serialize_request_payload(request_payload)
        payload_chars = len(payload_text)
        diagnostics = {
            "request_payload_chars": payload_chars,
            "request_payload_approx_tokens": _approx_token_count(payload_chars),
            "request_payload_char_limit": SYMBOL_CHAT_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": self.timeout_seconds,
        }
        LOGGER.debug(
            "AI symbol chat payload ready symbol=%s payload_chars=%s approx_tokens=%s history_messages=%s",
            context.symbol,
            payload_chars,
            diagnostics["request_payload_approx_tokens"],
            len(history_messages),
        )

        input_messages = [{"role": "system", "content": SYMBOL_CHAT_SYSTEM_PROMPT}]
        for message in history_messages[-SYMBOL_CHAT_HISTORY_MESSAGE_LIMIT:]:
            input_messages.append({"role": message.role, "content": message.content})
        input_messages.append({"role": "user", "content": payload_text})

        try:
            _notify_progress(progress_callback, f"Calling OpenAI (timeout {self.timeout_seconds:g}s)...")
            openai_started = time.perf_counter()
            response = self._client().responses.create(
                model=self.model,
                input=input_messages,
                store=False,
                timeout=self.timeout_seconds,
            )
        except Exception as exc:
            if _is_timeout_exception(exc):
                message = (
                    f"OpenAI symbol chat timed out after {self.timeout_seconds:g} seconds. "
                    "Try a narrower question or retry later."
                )
            else:
                message = f"OpenAI symbol chat failed: {exc}"
            message = _redact_api_key(message, self._current_api_key())
            message = redact_symbol_chat_secrets(message)
            LOGGER.warning("AI symbol chat OpenAI request failed symbol=%s elapsed=%.3fs error=%s", context.symbol, time.perf_counter() - started_at, message)
            raise OpenAiSymbolChatError(message) from None

        openai_elapsed = time.perf_counter() - openai_started
        diagnostics["openai_seconds"] = round(openai_elapsed, 3)
        diagnostics["total_seconds"] = round(time.perf_counter() - started_at, 3)
        _notify_progress(progress_callback, "OpenAI response received.")
        answer = _clean_answer(_response_output_text(response))
        if not answer:
            raise OpenAiSymbolChatError("OpenAI symbol chat returned an empty response.")

        return SymbolChatResponse(
            answer=answer,
            response_id=str(getattr(response, "id", "") or ""),
            model=self.model,
            source_mode=_source_mode(context),
            source_debug=tuple(_source_debug_lines(request_payload, diagnostics)),
        )

    def _client(self) -> Any:
        if self._openai_client is not None:
            return self._openai_client

        api_key = self._current_api_key()
        if not api_key:
            raise OpenAiSymbolChatError("OPENAI_API_KEY is not configured. Add it to .env or the environment.")

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise OpenAiSymbolChatError("The openai package is not installed. Run pip install -r requirements.txt.") from exc

        self._openai_client = OpenAI(api_key=api_key, timeout=self.timeout_seconds)
        return self._openai_client

    def _current_api_key(self) -> str:
        return (self._api_key if self._api_key is not None else os.getenv("OPENAI_API_KEY", "")).strip()


def create_symbol_chat_session(
    symbol: str,
    *,
    app_context: Any | None = None,
    schwab_session: Any | None = None,
    sec_client: SecEdgarClient | None = None,
    openai_client: Any | None = None,
    model: str | None = None,
) -> SymbolChatSession:
    context = build_symbol_chat_context(
        symbol,
        app_context=app_context,
        schwab_session=schwab_session,
        sec_client=sec_client,
    )
    return SymbolChatSession(
        context,
        chat_client=OpenAiSymbolChatClient(openai_client=openai_client, model=model),
    )


def build_symbol_chat_context(
    symbol: str,
    *,
    app_context: Any | None = None,
    schwab_session: Any | None = None,
    sec_client: SecEdgarClient | None = None,
    trade_memory_store: TradeMemoryStore | None = None,
) -> SymbolChatContext:
    clean_symbol = normalize_symbol(symbol)
    if not clean_symbol:
        raise ValueError("Symbol is required for AI Symbol Chat.")

    source_metadata: dict[str, Any] = {
        "symbol": clean_symbol,
        "loaded_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "available": [],
        "unavailable": [],
        "warnings": [],
    }

    company_name = _company_name_from_app_payload(app_context, clean_symbol)
    if company_name:
        _mark_available(source_metadata, "company_name", "Loaded from current Schwab research payload.")
    else:
        client = sec_client or SecEdgarClient()
        try:
            company = client.company_for_ticker(clean_symbol)
            company_name = company.title
            _mark_available(source_metadata, "company_name", "Loaded from SEC ticker/CIK map.")
        except Exception as exc:
            _mark_unavailable(source_metadata, "company_name", f"SEC company lookup unavailable: {exc}")

    schwab_position = _position_context(app_context, clean_symbol, source_metadata)
    quote_snapshot = _quote_context(app_context, schwab_session, clean_symbol, schwab_position, source_metadata)
    technical_analysis = _technical_context(app_context, schwab_session, clean_symbol, quote_snapshot, source_metadata)
    open_orders_summary, recent_orders_summary = _orders_context(app_context, schwab_session, clean_symbol, source_metadata)
    recent_filings_summary = _recent_filings_context(clean_symbol, sec_client, source_metadata)
    saved_thesis_or_notes = _saved_thesis_context(app_context, trade_memory_store, clean_symbol, source_metadata)

    return SymbolChatContext(
        symbol=clean_symbol,
        company_name=company_name,
        schwab_position=schwab_position,
        quote_snapshot=quote_snapshot,
        technical_analysis=technical_analysis,
        open_orders_summary=tuple(open_orders_summary),
        recent_orders_summary=tuple(recent_orders_summary),
        recent_filings_summary=tuple(recent_filings_summary),
        saved_thesis_or_notes=saved_thesis_or_notes,
        source_metadata=source_metadata,
    )


def symbol_chat_request_payload(context: SymbolChatContext, prompt: str, *, timeout_seconds: float) -> dict[str, Any]:
    payload = {
        "question": prompt,
        "symbol_context": {
            "symbol": context.symbol,
            "company_name": context.company_name or _not_available(),
            "schwab_position": context.schwab_position or _not_available(),
            "quote_snapshot": context.quote_snapshot or _not_available(),
            "technical_analysis": context.technical_analysis or _not_available(),
            "open_orders_summary": list(context.open_orders_summary) or _not_available(),
            "recent_orders_summary": list(context.recent_orders_summary) or _not_available(),
            "recent_filings_summary": list(context.recent_filings_summary) or _not_available(),
            "saved_thesis_or_notes": context.saved_thesis_or_notes or _not_available(),
            "source_metadata": context.source_metadata,
        },
        "grounding_rules": [
            "Use only symbol_context and conversation history for factual claims.",
            'Say "Not available in the provided context." when a requested fact is absent.',
            "Separate confirmed facts from assumptions, caveats, and questions to verify.",
            "Do not infer undisclosed financials, filings, quotes, ownership, catalysts, or technical levels.",
            "Do not place trades, submit orders, automate broker actions, or tell the user to buy, sell, or hold.",
            "Analysis can discuss evidence, risks, thesis support/weakness, and diligence priorities.",
        ],
        "request_budget": {
            "request_payload_char_limit": SYMBOL_CHAT_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": timeout_seconds,
        },
    }
    return _enforce_request_payload_budget(payload)


def render_symbol_chat_transcript_markdown(session: SymbolChatSession) -> str:
    context = session.context
    metadata = context.source_metadata
    lines = [
        f"# {context.display_name} AI Symbol Chat",
        "",
        f"Symbol: {context.symbol}",
        f"Company: {context.company_name or '--'}",
        f"AI model: {session.model}",
        f"Loaded: {metadata.get('loaded_at_utc') or '--'}",
        "",
        "## Context Availability",
        "",
    ]
    available = [str(item) for item in metadata.get("available", []) if str(item).strip()]
    unavailable = [str(item) for item in metadata.get("unavailable", []) if str(item).strip()]
    lines.append("Available:")
    lines.extend(f"- {redact_symbol_chat_secrets(item)}" for item in (available or ["None"]))
    lines.extend(["", "Unavailable / limited:"])
    lines.extend(f"- {redact_symbol_chat_secrets(item)}" for item in (unavailable or ["None"]))
    lines.append("")

    if not session.messages:
        lines.append("_No chat messages yet._")
    else:
        for index, message in enumerate(session.messages, start=1):
            speaker = "User" if message.role == "user" else "Assistant"
            lines.extend([f"## {index}. {speaker}", "", redact_symbol_chat_secrets(message.content), ""])
            if message.role == "assistant" and (message.source_mode or message.source_debug):
                lines.extend(["### Source Debug", ""])
                if message.source_mode:
                    lines.append(f"- Source mode: {message.source_mode}")
                for entry in message.source_debug:
                    lines.append(f"- {redact_symbol_chat_secrets(entry)}")
                lines.append("")
    return "\n".join(lines).strip() + "\n"


def save_symbol_chat_transcript(
    session: SymbolChatSession,
    output_path: str | Path | None = None,
    *,
    output_root: str | Path = DEFAULT_SYMBOL_CHAT_DIR,
) -> Path:
    path = Path(output_path) if output_path is not None else symbol_chat_transcript_path(session.context.symbol, output_root=output_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(render_symbol_chat_transcript_markdown(session), encoding="utf-8")
    temporary.replace(path)
    return path


def symbol_chat_transcript_path(symbol: str, *, output_root: str | Path = DEFAULT_SYMBOL_CHAT_DIR) -> Path:
    clean = normalize_symbol(symbol) or "SYMBOL"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Path(output_root) / clean / f"{clean}_AI_Symbol_Chat_{timestamp}.md"


def redact_symbol_chat_secrets(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[REDACTED]", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)(access_token|refresh_token|client_secret|api_key|authorization|account_hash|hashValue)\s*[:=]\s*[^,\s)}]+", r"\1=[REDACTED]", text)
    return text


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if "(" in text:
        text = text.split("(", 1)[0].strip()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^A-Z0-9.\-_/]", "", text)
    if text.startswith("HL:"):
        text = text[3:]
    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def _company_name_from_app_payload(app_context: Any | None, symbol: str) -> str:
    payload = getattr(app_context, "schwab_research_last_payload", None)
    if payload is None or normalize_symbol(getattr(payload, "symbol", "")) != symbol:
        return ""
    for attr in ("company_name", "issuer_name", "name"):
        value = str(getattr(payload, attr, "") or "").strip()
        if value:
            return value
    fundamentals = getattr(payload, "fundamentals_snapshot", None)
    for attr in ("company_name", "entity_name", "name"):
        value = str(getattr(fundamentals, attr, "") or "").strip()
        if value:
            return value
    return ""


def _position_context(app_context: Any | None, symbol: str, source_metadata: dict[str, Any]) -> dict[str, Any]:
    portfolio = _portfolio_from_app(app_context)
    if portfolio is None:
        _mark_unavailable(source_metadata, "schwab_position", "No current portfolio object was available.")
        return {}
    position = None
    try:
        position = portfolio.get_position(symbol) if hasattr(portfolio, "get_position") else None
    except Exception:
        position = None
    if position is None:
        position = getattr(portfolio, "positions", {}).get(symbol)
    if position is None:
        _mark_unavailable(source_metadata, "schwab_position", f"No Schwab/current portfolio position for {symbol}.")
        return {"is_held": False, "portfolio_cash": _safe_number(getattr(portfolio, "cash", None)), "portfolio_value": _safe_number(getattr(portfolio, "total_value", None))}

    market_value = _safe_number(getattr(position, "market_value", None))
    portfolio_value = _safe_number(getattr(portfolio, "total_value", None))
    context = {
        "is_held": True,
        "symbol": str(getattr(position, "symbol", symbol) or symbol).upper(),
        "asset_type": str(getattr(position, "asset_type", "") or ""),
        "quantity": _safe_number(getattr(position, "quantity", None)),
        "average_cost": _safe_number(getattr(position, "average_cost", None)),
        "last_price": _safe_number(getattr(position, "last_price", None)),
        "market_value": market_value,
        "cost_basis": _safe_number(getattr(position, "cost_basis", None)),
        "unrealized_pnl": _safe_number(getattr(position, "unrealized_profit_loss", None)),
        "unrealized_pnl_percent": _safe_number(getattr(position, "unrealized_profit_loss_percent", None)),
        "day_pnl": _safe_number(getattr(position, "day_profit_loss", None)),
        "day_pnl_percent": _safe_number(getattr(position, "day_profit_loss_percent", None)),
        "portfolio_cash": _safe_number(getattr(portfolio, "cash", None)),
        "portfolio_value": portfolio_value,
        "portfolio_weight": (market_value / portfolio_value) if market_value is not None and portfolio_value and portfolio_value > 0 else None,
    }
    _mark_available(source_metadata, "schwab_position", "Loaded from current broker/portfolio position.")
    return {key: value for key, value in context.items() if value not in (None, "")}


def _portfolio_from_app(app_context: Any | None) -> Any | None:
    for attr in ("cockpit_source_portfolio", "current_portfolio"):
        portfolio = getattr(app_context, attr, None)
        if portfolio is not None:
            return portfolio
    broker = getattr(app_context, "broker", None)
    getter = getattr(broker, "get_portfolio", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


def _quote_context(
    app_context: Any | None,
    schwab_session: Any | None,
    symbol: str,
    position_context: Mapping[str, Any],
    source_metadata: dict[str, Any],
) -> dict[str, Any]:
    payload = getattr(app_context, "schwab_research_last_payload", None)
    if payload is not None and normalize_symbol(getattr(payload, "symbol", "")) == symbol:
        quote = getattr(payload, "quote", None)
        if isinstance(quote, Mapping):
            parsed = parse_quote_snapshot(symbol, quote)
            _mark_available(source_metadata, "quote_snapshot", "Loaded from current Schwab research payload.")
            return _quote_snapshot_dict(parsed)

    if schwab_session is not None and hasattr(schwab_session, "get_quote"):
        try:
            status_code, quote_payload = schwab_session.get_quote(symbol)
            if status_code == 200:
                parsed = parse_quote_snapshot(symbol, quote_payload)
                _mark_available(source_metadata, "quote_snapshot", "Loaded from Schwab market-data quote.")
                return _quote_snapshot_dict(parsed)
            _mark_unavailable(source_metadata, "quote_snapshot", f"Schwab quote returned HTTP {status_code}: {_shorten(quote_payload, 300)}")
        except Exception as exc:
            _mark_unavailable(source_metadata, "quote_snapshot", f"Schwab quote unavailable: {exc}")

    last_price = _safe_number(position_context.get("last_price"))
    if last_price is not None:
        _mark_available(source_metadata, "quote_snapshot", "Using current position last price as limited quote context.")
        return {"symbol": symbol, "last": last_price, "source": "position_last_price"}

    _mark_unavailable(source_metadata, "quote_snapshot", "No quote context was available.")
    return {}


def _technical_context(
    app_context: Any | None,
    schwab_session: Any | None,
    symbol: str,
    quote_context: Mapping[str, Any],
    source_metadata: dict[str, Any],
) -> dict[str, Any]:
    payload = getattr(app_context, "schwab_research_last_payload", None)
    if payload is not None and normalize_symbol(getattr(payload, "symbol", "")) == symbol:
        command_report = getattr(payload, "command_center_report", None)
        if command_report is not None:
            _mark_available(source_metadata, "technical_analysis", "Loaded from current Schwab Research Workspace command-center report.")
            return _technical_report_context(command_report, source="schwab_research_last_payload")

    preview_text = _text_widget_content(getattr(app_context, "schwab_trading_preview_text", None))
    if preview_text and symbol in preview_text.upper() and "TECHNICAL" in preview_text.upper():
        _mark_available(source_metadata, "technical_analysis", "Loaded from Schwab Trading output pane.")
        return {
            "source": "schwab_trading_preview_text",
            "summary_text": _shorten(preview_text, SYMBOL_CHAT_TECHNICAL_TEXT_LIMIT),
        }

    if schwab_session is not None and hasattr(schwab_session, "get_price_history"):
        try:
            report, warnings = _build_live_technical_report(schwab_session, symbol, quote_context)
            _mark_available(source_metadata, "technical_analysis", "Built from Schwab price-history candles for symbol chat context.")
            for warning in warnings:
                _mark_warning(source_metadata, warning)
            return _technical_report_context(report, source="live_schwab_price_history")
        except Exception as exc:
            _mark_unavailable(source_metadata, "technical_analysis", f"Live Schwab technical context unavailable: {exc}")

    _mark_unavailable(source_metadata, "technical_analysis", "No technical-analysis context was available.")
    return {}


def _build_live_technical_report(schwab_session: Any, symbol: str, quote_context: Mapping[str, Any]) -> tuple[Any, list[str]]:
    timeframe_candles: dict[str, Any] = {}
    warnings: list[str] = []
    for spec in DEFAULT_COMMAND_CENTER_TIMEFRAMES:
        try:
            status_code, payload = schwab_session.get_price_history(
                symbol,
                period_type=spec.period_type,
                period=spec.period,
                frequency_type=spec.frequency_type,
                frequency=spec.frequency,
                need_extended_hours_data=False,
            )
            if status_code != 200:
                warnings.append(f"{spec.label} price history returned HTTP {status_code}.")
                timeframe_candles[spec.key] = []
                continue
            timeframe_candles[spec.key] = candles_from_price_history(payload)
        except Exception as exc:
            warnings.append(f"{spec.label} price history failed: {exc}")
            timeframe_candles[spec.key] = []

    quote_snapshot = None
    if quote_context:
        quote_snapshot = parse_quote_snapshot(symbol, {symbol: {"quote": dict(quote_context)}})
    try:
        capital = analyze_capital_structure_pressure(symbol)
    except Exception as exc:
        capital = unknown_capital_structure_report(symbol, warnings=[f"Capital structure overlay unavailable: {exc}"])

    report = build_technical_command_center_report(
        symbol,
        timeframe_candles,
        quote_snapshot=quote_snapshot,
        warnings=warnings,
        capital_structure_pressure=capital,
    )
    return report, warnings


def _technical_report_context(report: Any, *, source: str) -> dict[str, Any]:
    context = {
        "source": source,
        "symbol": str(getattr(report, "symbol", "") or ""),
        "overall_read": str(getattr(report, "overall_read", "") or ""),
        "overall_score": _safe_number(getattr(report, "overall_score", None)),
        "confidence": str(getattr(report, "confidence", "") or ""),
        "setup": str(getattr(getattr(report, "setup_classification", None), "setup", "") or ""),
        "action_quality": str(getattr(getattr(report, "setup_classification", None), "action_quality", "") or ""),
        "warnings": list(getattr(report, "warnings", []) or [])[:8],
    }
    try:
        summary_text = _analysis_only_technical_text(report)
    except Exception:
        summary_text = str(report)
    context["summary_text"] = _shorten(summary_text, SYMBOL_CHAT_TECHNICAL_TEXT_LIMIT)
    return {key: value for key, value in context.items() if value not in (None, "", [])}


def _analysis_only_technical_text(report: Any) -> str:
    text = format_technical_command_center_report(report)
    lines: list[str] = []
    skip_section = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        upper = line.upper()
        if upper in {"TICKET CHECK", "PLAIN-ENGLISH PLAN"}:
            skip_section = True
            continue
        if skip_section and upper in {"DATA WARNINGS"}:
            skip_section = False
        if skip_section:
            continue
        if line.lower().startswith("- best action:"):
            continue
        lines.append(raw_line)
    lines.extend(
        [
            "",
            "SYMBOL CHAT POLICY",
            "- Technical context above is for explanation and diligence only.",
            "- Do not convert technical reads into buy, sell, hold, or order-placement recommendations.",
        ]
    )
    return "\n".join(lines).strip()


def _orders_context(app_context: Any | None, schwab_session: Any | None, symbol: str, source_metadata: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    open_orders = _orders_from_table(app_context, "schwab_open_orders_table", "schwab_open_orders_by_iid", symbol)
    recent_orders = _orders_from_table(app_context, "schwab_recent_orders_table", "schwab_recent_orders_by_iid", symbol)
    if open_orders or recent_orders:
        if open_orders:
            _mark_available(source_metadata, "open_orders_summary", f"Loaded {len(open_orders)} selected-symbol open order(s) from Schwab Open Orders table.")
        else:
            _mark_unavailable(source_metadata, "open_orders_summary", f"No selected-symbol open orders in the loaded Schwab table for {symbol}.")
        if recent_orders:
            _mark_available(source_metadata, "recent_orders_summary", f"Loaded {len(recent_orders)} selected-symbol recent order(s) from Schwab Recent Orders table.")
        else:
            _mark_unavailable(source_metadata, "recent_orders_summary", f"No selected-symbol recent orders in the loaded Schwab table for {symbol}.")
        return open_orders[:8], recent_orders[:12]

    if schwab_session is not None and hasattr(schwab_session, "get_orders"):
        try:
            to_time = datetime.now(timezone.utc)
            from_time = to_time - timedelta(days=30)
            status_code, payload = schwab_session.get_orders(from_entered_time=from_time, to_entered_time=to_time)
            if status_code == 200 and isinstance(payload, list):
                parsed = [_order_summary(order) for order in payload if isinstance(order, Mapping)]
                matching = [order for order in parsed if normalize_symbol(order.get("symbol")) == symbol]
                active = [order for order in matching if str(order.get("status", "")).upper() in {"AWAITING_PARENT_ORDER", "AWAITING_CONDITION", "AWAITING_STOP_CONDITION", "QUEUED", "WORKING", "PENDING_ACTIVATION", "ACCEPTED"}]
                _mark_available(source_metadata, "recent_orders_summary", f"Loaded Schwab account orders from the last 30 days; {len(matching)} matched {symbol}.")
                if active:
                    _mark_available(source_metadata, "open_orders_summary", f"{len(active)} selected-symbol order(s) are active/open.")
                else:
                    _mark_unavailable(source_metadata, "open_orders_summary", f"No active/open Schwab orders found for {symbol}.")
                return active[:8], matching[:12]
            _mark_unavailable(source_metadata, "recent_orders_summary", f"Schwab orders returned HTTP {status_code}: {_shorten(payload, 300)}")
        except Exception as exc:
            _mark_unavailable(source_metadata, "recent_orders_summary", f"Schwab orders unavailable: {exc}")

    _mark_unavailable(source_metadata, "open_orders_summary", "No open-order context was available.")
    _mark_unavailable(source_metadata, "recent_orders_summary", "No recent-order context was available.")
    return [], []


def _orders_from_table(app_context: Any | None, table_attr: str, cache_attr: str, symbol: str) -> list[dict[str, Any]]:
    table = getattr(app_context, table_attr, None)
    cache = getattr(table, cache_attr, {}) if table is not None else {}
    orders = list(cache.values()) if isinstance(cache, Mapping) else []
    parsed = [_order_summary(order) for order in orders if isinstance(order, Mapping)]
    return [order for order in parsed if normalize_symbol(order.get("symbol")) == symbol]


def _order_summary(order: Mapping[str, Any]) -> dict[str, Any]:
    legs = order.get("orderLegCollection") or order.get("orderLegs") or []
    first_leg = legs[0] if isinstance(legs, list) and legs and isinstance(legs[0], Mapping) else {}
    instrument = first_leg.get("instrument") if isinstance(first_leg.get("instrument"), Mapping) else {}
    return {
        "entered_time": _first_text(order, "enteredTime", "enteredDateTime", "closeTime", "cancelTime"),
        "order_id": _first_text(order, "orderId", "orderID"),
        "symbol": normalize_symbol(instrument.get("symbol") or first_leg.get("finalSymbol") or order.get("symbol")),
        "instruction": str(first_leg.get("instruction") or order.get("instruction") or "").upper(),
        "quantity": _safe_number(first_leg.get("quantity", order.get("quantity"))),
        "order_type": str(order.get("orderType") or "").upper(),
        "limit_price": _safe_number(order.get("price")),
        "stop_price": _safe_number(order.get("stopPrice")),
        "duration": str(order.get("duration") or order.get("timeInForce") or "").upper(),
        "session": str(order.get("session") or "").upper(),
        "status": str(order.get("status") or "").upper(),
    }


def _recent_filings_context(symbol: str, sec_client: SecEdgarClient | None, source_metadata: dict[str, Any]) -> list[dict[str, Any]]:
    client = sec_client or SecEdgarClient()
    try:
        filings = client.recent_filings(symbol, limit=8)
    except Exception as exc:
        _mark_unavailable(source_metadata, "recent_filings_summary", f"SEC recent filings unavailable: {exc}")
        return []
    rows = [
        {
            "form": filing.form,
            "filing_date": filing.filing_date,
            "report_date": filing.report_date,
            "description": filing.description,
            "accession_number": filing.accession_number,
            "url": filing.filing_url,
        }
        for filing in filings
    ]
    if rows:
        _mark_available(source_metadata, "recent_filings_summary", f"Loaded {len(rows)} recent SEC filing(s).")
    else:
        _mark_unavailable(source_metadata, "recent_filings_summary", f"No recent SEC filings returned for {symbol}.")
    return rows


def _saved_thesis_context(app_context: Any | None, trade_memory_store: TradeMemoryStore | None, symbol: str, source_metadata: dict[str, Any]) -> str:
    pieces: list[str] = []
    payload = getattr(app_context, "schwab_research_last_payload", None)
    if payload is not None and normalize_symbol(getattr(payload, "symbol", "")) == symbol:
        overview = _text_widget_content(getattr(getattr(app_context, "schwab_research_overview_text", None), "detail_text", None))
        if not overview:
            overview = _short_research_payload_summary(payload)
        if overview:
            pieces.append("Current Schwab Research Workspace summary:\n" + _shorten(overview, SYMBOL_CHAT_THESIS_TEXT_LIMIT // 2))

    store = trade_memory_store or getattr(app_context, "schwab_trade_memory_store", None) or TradeMemoryStore()
    try:
        snapshots = store.find_snapshots_for_symbol(symbol)
    except Exception:
        snapshots = []
    if snapshots:
        latest = snapshots[0]
        lines = [
            f"Latest saved Trade Memory snapshot: {latest.get('snapshot_id') or '--'}",
            f"Created: {latest.get('created_at') or '--'}",
            f"Thesis status: {latest.get('thesis_status') or '--'}",
            f"Summary: {latest.get('plain_english_summary') or '--'}",
        ]
        notes = latest.get("notes") if isinstance(latest.get("notes"), Mapping) else {}
        freeform = str(notes.get("freeform") or "").strip()
        if freeform:
            lines.append(f"Freeform notes: {freeform}")
        pieces.append("\n".join(lines))

    result = "\n\n".join(piece for piece in pieces if piece.strip())
    if result:
        _mark_available(source_metadata, "saved_thesis_or_notes", "Loaded current research and/or local Trade Memory notes.")
        return _shorten(result, SYMBOL_CHAT_THESIS_TEXT_LIMIT)
    _mark_unavailable(source_metadata, "saved_thesis_or_notes", "No saved thesis or local notes were available for this symbol.")
    return ""


def _short_research_payload_summary(payload: Any) -> str:
    parts = []
    for attr in ("decision", "operator_verdict", "recommendation_engine_read"):
        value = getattr(payload, attr, None)
        if value is not None:
            parts.append(f"{attr}: {value}")
    return "\n".join(parts)


def _quote_snapshot_dict(snapshot: Any) -> dict[str, Any]:
    return {
        "symbol": getattr(snapshot, "symbol", ""),
        "bid": getattr(snapshot, "bid", None),
        "ask": getattr(snapshot, "ask", None),
        "last": getattr(snapshot, "last", None),
        "mark": getattr(snapshot, "mark", None),
        "total_volume": getattr(snapshot, "total_volume", None),
        "data_quality_warnings": list(getattr(snapshot, "data_quality_warnings", []) or []),
    }


def _technical_ticket_from_context(app_context: Any | None) -> TechnicalTicket:
    return TechnicalTicket(
        side=_string_var_value(app_context, "side_var", "buy"),
        quantity=_safe_number(_string_var_value(app_context, "quantity_var", "")),
        entry_price=_safe_number(_string_var_value(app_context, "limit_price_var", "")) or _safe_number(_string_var_value(app_context, "estimated_price_var", "")),
        stop_price=_safe_number(_string_var_value(app_context, "stop_price_var", "")),
        portfolio_value=_safe_number(getattr(_portfolio_from_app(app_context), "total_value", None)),
    )


def _text_widget_content(widget: Any) -> str:
    if widget is None:
        return ""
    try:
        return str(widget.get("1.0", "end")).strip()
    except Exception:
        return ""


def _string_var_value(app_context: Any | None, name: str, default: str = "") -> str:
    var = getattr(app_context, name, None)
    try:
        return str(var.get()).strip()
    except Exception:
        return default


def _source_mode(context: SymbolChatContext) -> str:
    available = context.source_metadata.get("available", [])
    if not available:
        return "symbol_only"
    return "symbol_context_bundle"


def _source_debug_lines(request_payload: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> list[str]:
    context = request_payload.get("symbol_context") if isinstance(request_payload, Mapping) else {}
    metadata = context.get("source_metadata") if isinstance(context, Mapping) else {}
    lines = []
    available = metadata.get("available", []) if isinstance(metadata, Mapping) else []
    unavailable = metadata.get("unavailable", []) if isinstance(metadata, Mapping) else []
    lines.append(f"available_context={len(available)}")
    lines.append(f"unavailable_context={len(unavailable)}")
    lines.extend(_diagnostic_debug_lines(diagnostics))
    return lines


def _diagnostic_debug_lines(diagnostics: Mapping[str, Any]) -> list[str]:
    return [f"{key}={value}" for key, value in diagnostics.items()]


def _enforce_request_payload_budget(payload: dict[str, Any]) -> dict[str, Any]:
    serialized = _serialize_request_payload(payload)
    if len(serialized) <= SYMBOL_CHAT_REQUEST_CHAR_LIMIT:
        return payload
    context = payload.get("symbol_context")
    if isinstance(context, dict):
        technical = context.get("technical_analysis")
        if isinstance(technical, dict) and technical.get("summary_text"):
            technical["summary_text"] = _shorten(str(technical["summary_text"]), 8_000)
        thesis = context.get("saved_thesis_or_notes")
        if isinstance(thesis, str):
            context["saved_thesis_or_notes"] = _shorten(thesis, 4_000)
    serialized = _serialize_request_payload(payload)
    if len(serialized) <= SYMBOL_CHAT_REQUEST_CHAR_LIMIT:
        return payload
    if isinstance(context, dict):
        context["recent_orders_summary"] = _short_list(context.get("recent_orders_summary"), 6)
        context["open_orders_summary"] = _short_list(context.get("open_orders_summary"), 4)
        context["recent_filings_summary"] = _short_list(context.get("recent_filings_summary"), 6)
    return payload


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


def _short_list(value: Any, limit: int) -> Any:
    if isinstance(value, list):
        return value[:limit]
    return value


def _clean_prompt(prompt: str) -> str:
    return re.sub(r"\s+", " ", str(prompt or "").strip())


def _clean_answer(answer: str) -> str:
    return redact_symbol_chat_secrets(str(answer or "").strip())


def _positive_timeout_seconds(value: float | None, env_value: str | None) -> float:
    for candidate in (value, env_value, SYMBOL_CHAT_OPENAI_TIMEOUT_SECONDS):
        try:
            parsed = float(candidate)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return SYMBOL_CHAT_OPENAI_TIMEOUT_SECONDS


def _is_timeout_exception(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    return "timeout" in name or "timed out" in message or "read timed out" in message


def _notify_progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        LOGGER.debug("AI symbol chat progress callback failed", exc_info=True)


def _safe_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).replace("$", "").replace(",", "").replace("%", "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _shorten(value: Any, limit: int) -> str:
    text = redact_symbol_chat_secrets(str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 80)].rstrip() + f"\n\n[Truncated to {limit} characters for Symbol Chat context.]"


def _not_available() -> dict[str, str]:
    return {"status": "Not available in the provided context."}


def _mark_available(metadata: dict[str, Any], key: str, detail: str) -> None:
    metadata.setdefault("available", []).append(f"{key}: {detail}")


def _mark_unavailable(metadata: dict[str, Any], key: str, detail: str) -> None:
    metadata.setdefault("unavailable", []).append(f"{key}: {detail}")


def _mark_warning(metadata: dict[str, Any], detail: str) -> None:
    metadata.setdefault("warnings", []).append(detail)


def _approx_token_count(chars: int) -> int:
    return max(1, int(chars / SYMBOL_CHAT_APPROX_CHARS_PER_TOKEN))


def _is_secret_key(key: str) -> bool:
    compact = re.sub(r"[^a-z0-9]", "", key.lower())
    return any(part in compact for part in ("token", "secret", "authorization", "apikey", "password", "credential", "cookie", "hashvalue", "accounthash"))
