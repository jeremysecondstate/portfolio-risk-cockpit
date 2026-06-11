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
SYMBOL_CHAT_RESEARCH_WORKSPACE_TEXT_LIMIT = 18_000
SYMBOL_CHAT_RESEARCH_SECTION_TEXT_LIMIT = 3_500
SYMBOL_CHAT_WEB_TEXT_LIMIT = 10_000
SYMBOL_CHAT_APPROX_CHARS_PER_TOKEN = 4

SYMBOL_CHAT_SYSTEM_PROMPT = """You are a company and stock analysis assistant inside Portfolio Risk Cockpit.

Analyze the selected symbol using only the provided app context and explicitly available sources.
Do not invent missing financials, filings, quotes, ownership, catalysts, or technical levels.
When a field is absent, say "Not available in the provided context." When a conclusion is uncertain, label it clearly.
Treat explicit schwab_position data as authoritative for current position context. Do not say the user does not hold a position unless schwab_position.is_held is explicitly false from a loaded Schwab holdings, Schwab account, or portfolio source.
Prefer research_workspace_context over thinner rebuilt technical context when both are available; it is deterministic app analysis already produced before this OpenAI call.
Separate app-local deterministic context from optional web_enrichment facts. If web_enrichment is absent or unavailable, do not imply that public web research was performed.
Treat keyword-only capital-structure, supply, dilution, or filing-risk flags as low-confidence prompts for verification unless actual filing text or a verified filing summary is included.

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
    research_workspace_context: dict[str, Any] = field(default_factory=dict)
    technical_analysis: dict[str, Any] = field(default_factory=dict)
    open_orders_summary: tuple[dict[str, Any], ...] = ()
    recent_orders_summary: tuple[dict[str, Any], ...] = ()
    recent_filings_summary: tuple[dict[str, Any], ...] = ()
    saved_thesis_or_notes: str = ""
    web_enrichment: dict[str, Any] = field(default_factory=dict)
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
    use_web_enrichment: bool = False,
    web_enrichment_provider: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> SymbolChatSession:
    context = build_symbol_chat_context(
        symbol,
        app_context=app_context,
        schwab_session=schwab_session,
        sec_client=sec_client,
        use_web_enrichment=use_web_enrichment,
        web_enrichment_provider=web_enrichment_provider,
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
    use_web_enrichment: bool = False,
    web_enrichment_provider: Callable[[str], Mapping[str, Any] | None] | None = None,
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

    schwab_position = _position_context(app_context, schwab_session, clean_symbol, source_metadata)
    quote_snapshot = _quote_context(app_context, schwab_session, clean_symbol, schwab_position, source_metadata)
    research_workspace_context = _research_workspace_context(app_context, clean_symbol, source_metadata)
    technical_analysis = _technical_context(app_context, schwab_session, clean_symbol, quote_snapshot, source_metadata)
    open_orders_summary, recent_orders_summary = _orders_context(app_context, schwab_session, clean_symbol, source_metadata)
    recent_filings_summary = _recent_filings_context(clean_symbol, sec_client, source_metadata)
    saved_thesis_or_notes = _saved_thesis_context(app_context, trade_memory_store, clean_symbol, source_metadata)
    web_enrichment = _web_enrichment_context(app_context, clean_symbol, use_web_enrichment, web_enrichment_provider, source_metadata)

    return SymbolChatContext(
        symbol=clean_symbol,
        company_name=company_name,
        schwab_position=schwab_position,
        quote_snapshot=quote_snapshot,
        research_workspace_context=research_workspace_context,
        technical_analysis=technical_analysis,
        open_orders_summary=tuple(open_orders_summary),
        recent_orders_summary=tuple(recent_orders_summary),
        recent_filings_summary=tuple(recent_filings_summary),
        saved_thesis_or_notes=saved_thesis_or_notes,
        web_enrichment=web_enrichment,
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
            "research_workspace_context": context.research_workspace_context or _not_available(),
            "technical_analysis": context.technical_analysis or _not_available(),
            "open_orders_summary": list(context.open_orders_summary) or _not_available(),
            "recent_orders_summary": list(context.recent_orders_summary) or _not_available(),
            "recent_filings_summary": list(context.recent_filings_summary) or _not_available(),
            "saved_thesis_or_notes": context.saved_thesis_or_notes or _not_available(),
            "web_enrichment": context.web_enrichment or _not_available(),
            "source_metadata": context.source_metadata,
        },
        "grounding_rules": [
            "Use only symbol_context and conversation history for factual claims.",
            "Use research_workspace_context as the primary deterministic app analysis when it is available.",
            "Use schwab_position as the authority for current position context; do not infer not-held status from recent orders or missing fields.",
            "If schwab_position is unavailable, say position context is unavailable instead of saying the symbol is not held.",
            "Keep app-local context and optional web_enrichment facts separate in the answer.",
            "Treat form-only or keyword-only filing/capital-structure flags as unverified until actual filing text or a verified summary is present.",
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


def _position_context(app_context: Any | None, schwab_session: Any | None, symbol: str, source_metadata: dict[str, Any]) -> dict[str, Any]:
    portfolio = _portfolio_from_app(app_context)
    summary = _portfolio_summary_from_app(app_context, portfolio)

    table_context = _position_context_from_holdings_tables(app_context, symbol, summary)
    if table_context:
        _mark_available(source_metadata, "schwab_position", f"Loaded from {table_context.get('source', 'visible Schwab holdings table')}.")
        return table_context

    research_context = _position_context_from_research_payload(app_context, symbol)
    if research_context:
        _mark_available(source_metadata, "schwab_position", "Loaded from current Schwab Research Workspace portfolio context.")
        return _merge_portfolio_summary(research_context, summary)

    if portfolio is not None:
        portfolio_context = _position_context_from_portfolio(portfolio, symbol, source="current_broker_portfolio")
        if portfolio_context:
            if portfolio_context.get("is_held") is True:
                _mark_available(source_metadata, "schwab_position", "Loaded from current broker/portfolio position.")
            elif _portfolio_source_is_loaded(portfolio, summary):
                _mark_unavailable(source_metadata, "schwab_position", f"No {symbol} position in the loaded current broker/portfolio object.")
            else:
                portfolio_context = {}
        if portfolio_context:
            return _merge_portfolio_summary(portfolio_context, summary)

    live_context = _position_context_from_live_schwab_account(schwab_session, symbol, source_metadata)
    if live_context:
        return live_context

    if _has_loaded_schwab_holdings_table(app_context):
        _mark_unavailable(source_metadata, "schwab_position", f"No {symbol} row in the loaded Schwab Holdings table.")
        return _not_held_position_context(symbol, "loaded_schwab_holdings_table", summary)

    _mark_unavailable(source_metadata, "schwab_position", "No Schwab holdings, Schwab account, or current portfolio position source was available.")
    return {}


def _position_context_from_portfolio(portfolio: Any, symbol: str, *, source: str) -> dict[str, Any]:
    position = None
    try:
        position = portfolio.get_position(symbol) if hasattr(portfolio, "get_position") else None
    except Exception:
        position = None
    if position is None:
        position = getattr(portfolio, "positions", {}).get(symbol)
    if position is None:
        return _not_held_position_context(
            symbol,
            source,
            {
                "portfolio_cash": _safe_number(getattr(portfolio, "cash", None)),
                "portfolio_value": _safe_number(getattr(portfolio, "total_value", None)),
                "positions_value": _safe_number(getattr(portfolio, "positions_value", None)),
                "portfolio_unrealized_pnl": _safe_number(getattr(portfolio, "unrealized_profit_loss", None)),
                "portfolio_day_pnl": _safe_number(getattr(portfolio, "day_profit_loss", None)),
            },
        )

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
        "positions_value": _safe_number(getattr(portfolio, "positions_value", None)),
        "portfolio_unrealized_pnl": _safe_number(getattr(portfolio, "unrealized_profit_loss", None)),
        "portfolio_day_pnl": _safe_number(getattr(portfolio, "day_profit_loss", None)),
        "portfolio_weight": (market_value / portfolio_value) if market_value is not None and portfolio_value and portfolio_value > 0 else None,
        "source": source,
    }
    return {key: value for key, value in context.items() if value not in (None, "")}


def _position_context_from_holdings_tables(app_context: Any | None, symbol: str, summary: Mapping[str, Any]) -> dict[str, Any]:
    table = getattr(app_context, "schwab_workspace_holdings_table", None)
    if table is None:
        return {}

    selected = _matching_holdings_row(table, symbol, selected_only=True)
    if selected is not None:
        return _position_context_from_holdings_row(symbol, selected, summary, source="selected_schwab_holdings_table")

    visible = _matching_holdings_row(table, symbol, selected_only=False)
    if visible is not None:
        return _position_context_from_holdings_row(symbol, visible, summary, source="visible_schwab_holdings_table")

    return {}


def _matching_holdings_row(table: Any, symbol: str, *, selected_only: bool) -> dict[str, Any] | None:
    row_ids: list[Any] = []
    if selected_only:
        try:
            row_ids = list(table.selection() or [])
        except Exception:
            row_ids = []
    else:
        try:
            row_ids = list(table.get_children() or [])
        except Exception:
            row_ids = []

    for row_id in row_ids:
        values = _tree_values_by_column(table, row_id)
        if not values:
            continue
        if str(values.get("type", "")).strip().lower() == "cash":
            continue
        if normalize_symbol(values.get("symbol")) == symbol:
            day_pnl = _day_pnl_from_companion_table(table, row_id)
            if day_pnl not in (None, ""):
                values["day_pnl"] = day_pnl
            return values
    return None


def _tree_values_by_column(table: Any, row_id: Any) -> dict[str, Any]:
    try:
        raw_values = table.item(row_id, "values")
        columns = tuple(table["columns"])
    except Exception:
        return {}
    return {str(column): raw_values[index] for index, column in enumerate(columns) if index < len(raw_values)}


def _day_pnl_from_companion_table(table: Any, row_id: Any) -> Any:
    day_table = getattr(table, "_day_pnl_table", None)
    if day_table is None:
        return None
    try:
        raw_values = day_table.item(row_id, "values")
    except Exception:
        return None
    if isinstance(raw_values, (list, tuple)) and raw_values:
        return raw_values[0]
    return None


def _position_context_from_holdings_row(symbol: str, row: Mapping[str, Any], summary: Mapping[str, Any], *, source: str) -> dict[str, Any]:
    quantity = _display_number(row.get("qty"))
    last_price = _display_number(row.get("last"))
    market_value = _display_number(row.get("value"))
    if last_price is None and market_value is not None and quantity not in (None, 0):
        last_price = abs(market_value / quantity)
    portfolio_value = _safe_number(summary.get("portfolio_value"))
    context = {
        "is_held": quantity is None or abs(quantity) > 0.00000001,
        "symbol": symbol,
        "asset_type": str(row.get("type") or ""),
        "quantity": quantity,
        "last_price": last_price,
        "market_value": market_value,
        "unrealized_pnl": _display_number(row.get("pnl")),
        "day_pnl": _display_number(row.get("day_pnl")),
        "portfolio_cash": _safe_number(summary.get("portfolio_cash")),
        "portfolio_value": portfolio_value,
        "positions_value": _safe_number(summary.get("positions_value")),
        "portfolio_unrealized_pnl": _safe_number(summary.get("portfolio_unrealized_pnl")),
        "portfolio_day_pnl": _safe_number(summary.get("portfolio_day_pnl")),
        "portfolio_weight": (market_value / portfolio_value) if market_value is not None and portfolio_value and portfolio_value > 0 else None,
        "source": source,
    }
    return {key: value for key, value in context.items() if value not in (None, "")}


def _position_context_from_research_payload(app_context: Any | None, symbol: str) -> dict[str, Any]:
    payload = getattr(app_context, "schwab_research_last_payload", None)
    if payload is None or normalize_symbol(getattr(payload, "symbol", "")) != symbol:
        return {}
    context = getattr(payload, "context", None)
    if context is None or normalize_symbol(getattr(context, "symbol", symbol)) != symbol:
        return {}
    result = {
        "is_held": bool(getattr(context, "is_held", False)),
        "symbol": symbol,
        "quantity": _safe_number(getattr(context, "quantity", None)),
        "average_cost": _safe_number(getattr(context, "average_cost", None)),
        "last_price": _safe_number(getattr(context, "last_price", None)),
        "market_value": _safe_number(getattr(context, "market_value", None)),
        "unrealized_pnl": _safe_number(getattr(context, "unrealized_pnl", None)),
        "day_pnl": _safe_number(getattr(context, "day_pnl", None)),
        "portfolio_cash": _safe_number(getattr(context, "cash_available", None)),
        "portfolio_value": _safe_number(getattr(context, "portfolio_value", None)),
        "portfolio_weight": _safe_number(getattr(context, "portfolio_weight", None)),
        "source": "schwab_research_workspace_portfolio_context",
    }
    return {key: value for key, value in result.items() if value not in (None, "")}


def _position_context_from_live_schwab_account(schwab_session: Any | None, symbol: str, source_metadata: dict[str, Any]) -> dict[str, Any]:
    if schwab_session is None or not hasattr(schwab_session, "get_account"):
        return {}
    try:
        status_code, account_payload = schwab_session.get_account(fields="positions")
        if status_code != 200:
            _mark_unavailable(source_metadata, "schwab_position", f"Live Schwab account positions returned HTTP {status_code}: {_shorten(account_payload, 300)}")
            return {}
        from app.brokers.schwab.account_adapter import portfolio_from_schwab_account

        portfolio, _source_message = portfolio_from_schwab_account(account_payload)
        context = _position_context_from_portfolio(portfolio, symbol, source="live_schwab_account_positions")
        if context.get("is_held") is True:
            _mark_available(source_metadata, "schwab_position", "Loaded from live Schwab account positions.")
        else:
            _mark_unavailable(source_metadata, "schwab_position", f"No {symbol} position in live Schwab account positions.")
        return context
    except Exception as exc:
        _mark_unavailable(source_metadata, "schwab_position", f"Live Schwab account positions unavailable: {exc}")
        return {}


def _not_held_position_context(symbol: str, source: str, summary: Mapping[str, Any]) -> dict[str, Any]:
    context = {
        "is_held": False,
        "symbol": symbol,
        "portfolio_cash": _safe_number(summary.get("portfolio_cash")),
        "portfolio_value": _safe_number(summary.get("portfolio_value")),
        "positions_value": _safe_number(summary.get("positions_value")),
        "portfolio_unrealized_pnl": _safe_number(summary.get("portfolio_unrealized_pnl")),
        "portfolio_day_pnl": _safe_number(summary.get("portfolio_day_pnl")),
        "source": source,
    }
    return {key: value for key, value in context.items() if value not in (None, "")}


def _merge_portfolio_summary(context: Mapping[str, Any], summary: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(context)
    for target, source in (
        ("portfolio_cash", "portfolio_cash"),
        ("portfolio_value", "portfolio_value"),
        ("positions_value", "positions_value"),
        ("portfolio_unrealized_pnl", "portfolio_unrealized_pnl"),
        ("portfolio_day_pnl", "portfolio_day_pnl"),
    ):
        if merged.get(target) in (None, "", 0) and summary.get(source) not in (None, ""):
            merged[target] = summary.get(source)
    market_value = _safe_number(merged.get("market_value"))
    portfolio_value = _safe_number(merged.get("portfolio_value"))
    if merged.get("portfolio_weight") in (None, "") and market_value is not None and portfolio_value and portfolio_value > 0:
        merged["portfolio_weight"] = market_value / portfolio_value
    return {key: value for key, value in merged.items() if value not in (None, "")}


def _portfolio_summary_from_app(app_context: Any | None, portfolio: Any | None) -> dict[str, float]:
    values = {
        "portfolio_cash": _first_display_number(app_context, ("cash_value_label", "schwab_research_cash_var", "options_cash_available_var")),
        "positions_value": _first_display_number(app_context, ("positions_value_label", "schwab_research_positions_var")),
        "portfolio_value": _first_display_number(app_context, ("total_value_label", "schwab_research_total_var", "options_portfolio_value_var")),
        "portfolio_unrealized_pnl": _first_display_number(app_context, ("pnl_value_label", "schwab_research_pnl_var")),
        "portfolio_day_pnl": _first_display_number(app_context, ("day_pnl_value_label", "day_pnl_var")),
    }
    if values["positions_value"] is None:
        values["positions_value"] = _positions_value_from_holdings_table(app_context)
    if portfolio is not None:
        fallback = {
            "portfolio_cash": _safe_number(getattr(portfolio, "cash", None)),
            "positions_value": _safe_number(getattr(portfolio, "positions_value", None)),
            "portfolio_value": _safe_number(getattr(portfolio, "total_value", None)),
            "portfolio_unrealized_pnl": _safe_number(getattr(portfolio, "unrealized_profit_loss", None)),
            "portfolio_day_pnl": _safe_number(getattr(portfolio, "day_profit_loss", None)),
        }
        for key, value in fallback.items():
            if values.get(key) in (None, ""):
                values[key] = value
    if values["portfolio_value"] is None and values["portfolio_cash"] is not None and values["positions_value"] is not None:
        values["portfolio_value"] = round(values["portfolio_cash"] + values["positions_value"], 2)
    return {key: value for key, value in values.items() if value is not None}


def _first_display_number(app_context: Any | None, attr_names: tuple[str, ...]) -> float | None:
    for name in attr_names:
        value = _display_number(_app_attr_display_value(app_context, name))
        if value is not None:
            return value
    return None


def _app_attr_display_value(app_context: Any | None, name: str) -> Any:
    value = getattr(app_context, name, None)
    if value is None:
        return None
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            pass
    cget = getattr(value, "cget", None)
    if callable(cget):
        try:
            return cget("text")
        except Exception:
            pass
    return value


def _positions_value_from_holdings_table(app_context: Any | None) -> float | None:
    table = getattr(app_context, "schwab_workspace_holdings_table", None)
    if table is None:
        return None
    total = 0.0
    found = False
    try:
        row_ids = list(table.get_children() or [])
    except Exception:
        return None
    for row_id in row_ids:
        values = _tree_values_by_column(table, row_id)
        if str(values.get("type", "")).strip().lower() == "cash":
            continue
        value = _display_number(values.get("value"))
        if value is not None:
            total += value
            found = True
    return round(total, 2) if found else None


def _has_loaded_schwab_holdings_table(app_context: Any | None) -> bool:
    table = getattr(app_context, "schwab_workspace_holdings_table", None)
    if table is None:
        return False
    try:
        return bool(table.get_children())
    except Exception:
        try:
            return bool(table.selection())
        except Exception:
            return False


def _portfolio_source_is_loaded(portfolio: Any, summary: Mapping[str, Any]) -> bool:
    positions = getattr(portfolio, "positions", None)
    if isinstance(positions, Mapping) and positions:
        return True
    for key in ("portfolio_value", "portfolio_cash", "positions_value"):
        value = _safe_number(summary.get(key))
        if value is not None and abs(value) > 0.00000001:
            return True
    return False


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


def _research_workspace_context(app_context: Any | None, symbol: str, source_metadata: dict[str, Any]) -> dict[str, Any]:
    payload = getattr(app_context, "schwab_research_last_payload", None)
    if payload is None:
        _mark_unavailable(source_metadata, "research_workspace_context", "No Schwab Research Workspace payload was available.")
        return {}
    if normalize_symbol(getattr(payload, "symbol", "")) != symbol:
        _mark_unavailable(
            source_metadata,
            "research_workspace_context",
            f"Loaded Schwab Research Workspace payload is for {getattr(payload, 'symbol', '--')}, not {symbol}.",
        )
        return {}

    context = {
        "source": "schwab_research_workspace",
        "symbol": symbol,
        "workspace_status": _app_attr_display_value(app_context, "schwab_research_status_var") or "",
        "security_kind": str(getattr(payload, "security_kind", "") or ""),
        "reporting_profile": str(getattr(payload, "reporting_profile", "") or ""),
        "portfolio_context": _portfolio_symbol_context_dict(getattr(payload, "context", None)),
        "at_a_glance": _research_at_glance_context(payload),
        "technicals": _research_technical_context(payload),
        "risk_scenarios": _research_scenario_context(app_context, payload),
        "options_strategy": _research_options_context(app_context, payload),
        "greeks": _research_greeks_context(app_context, payload),
        "earnings_news": _research_text_section(
            "earnings_news",
            getattr(payload, "earnings_text", "") or _text_widget_content(getattr(app_context, "schwab_research_earnings_text", None)),
        ),
        "fundamentals": _research_text_section(
            "fundamentals",
            getattr(payload, "fundamentals_text", "") or _text_widget_content(getattr(app_context, "schwab_research_fundamentals_text", None)),
        ),
        "macro_context": _research_text_section(
            "macro_context",
            getattr(payload, "macro_text", "") or _text_widget_content(getattr(app_context, "schwab_research_macro_text", None)),
        ),
        "overview_text": _shorten(
            _text_widget_content(getattr(app_context, "schwab_research_overview_text", None)) or _short_research_payload_summary(payload),
            SYMBOL_CHAT_RESEARCH_SECTION_TEXT_LIMIT,
        ),
        "source_statuses": _source_status_context(getattr(payload, "statuses", []) or []),
        "filing_context_note": (
            "Filing/capital-structure flags from deterministic scanners are verification prompts unless actual filing text "
            "or a verified filing-analysis summary is present in this context."
        ),
        "analysis_policy": "Analysis-only deterministic app context. No broker action is authorized by this readout.",
    }
    compact = _drop_empty(context)
    if compact:
        _mark_available(source_metadata, "research_workspace_context", "Loaded current Schwab Research + Risk Workspace deterministic analysis.")
    return _limit_research_workspace_context(compact)


def _research_at_glance_context(payload: Any) -> dict[str, Any]:
    decision = getattr(payload, "decision", None)
    thesis = getattr(decision, "thesis", None)
    read = getattr(payload, "recommendation_engine_read", None)
    verdict = getattr(payload, "operator_verdict", None)
    return _drop_empty(
        {
            "overall": _badge_context(getattr(decision, "overall", None)),
            "risk_level": _badge_context(getattr(decision, "risk_level", None)),
            "action_bias": _badge_context(getattr(decision, "action_bias", None)),
            "position_impact": _badge_context(getattr(decision, "position_impact", None)),
            "macro_backdrop": _badge_context(getattr(decision, "macro_backdrop", None)),
            "thesis": _drop_empty(
                {
                    "trade_judgment": str(getattr(thesis, "trade_judgment", "") or ""),
                    "recommendation": str(getattr(thesis, "recommendation", "") or ""),
                    "preferred_vehicle": str(getattr(thesis, "preferred_vehicle", "") or ""),
                    "confidence": str(getattr(thesis, "confidence", "") or ""),
                    "invalidation": str(getattr(thesis, "invalidation", "") or ""),
                }
            ),
            "summary": _short_text_list(getattr(decision, "summary", []) or [], limit=6),
            "what_matters": _short_text_list(getattr(decision, "matters", []) or [], limit=6),
            "what_would_change": _short_text_list(getattr(decision, "changes_view", []) or [], limit=6),
            "operator_verdict": _operator_verdict_context(verdict),
            "recommendation_engine": _recommendation_engine_context(read),
        }
    )


def _research_technical_context(payload: Any) -> dict[str, Any]:
    indicators = getattr(payload, "indicators", None)
    report = getattr(payload, "command_center_report", None)
    return _drop_empty(
        {
            "indicator_snapshot": _drop_empty(
                {
                    "latest_close": _safe_number(getattr(indicators, "latest_close", None)),
                    "trend": str(getattr(indicators, "trend", "") or ""),
                    "momentum": str(getattr(indicators, "momentum", "") or ""),
                    "volatility": str(getattr(indicators, "volatility", "") or ""),
                    "support": _safe_number(getattr(indicators, "support", None)),
                    "resistance": _safe_number(getattr(indicators, "resistance", None)),
                    "week_52_high": _safe_number(getattr(indicators, "week_52_high", None)),
                    "week_52_low": _safe_number(getattr(indicators, "week_52_low", None)),
                    "rsi_14": _safe_number(getattr(indicators, "rsi_14", None)),
                    "atr_14": _safe_number(getattr(indicators, "atr_14", None)),
                    "notes": _short_text_list(getattr(indicators, "notes", []) or [], limit=6),
                }
            ),
            "command_center": _drop_empty(
                {
                    "overall_read": str(getattr(report, "overall_read", "") or ""),
                    "overall_score": _safe_number(getattr(report, "overall_score", None)),
                    "confidence": str(getattr(report, "confidence", "") or ""),
                    "setup": str(getattr(getattr(report, "setup_classification", None), "setup", "") or ""),
                    "action_quality": str(getattr(getattr(report, "setup_classification", None), "action_quality", "") or ""),
                    "warnings": _short_text_list(getattr(report, "warnings", []) or [], limit=8),
                }
            ),
        }
    )


def _research_scenario_context(app_context: Any | None, payload: Any) -> dict[str, Any]:
    rows = []
    for row in list(getattr(payload, "scenario_rows", []) or [])[:8]:
        rows.append(
            _drop_empty(
                {
                    "scenario": str(getattr(row, "scenario", "") or ""),
                    "symbol_price": _safe_number(getattr(row, "symbol_price", None)),
                    "position_pnl": _safe_number(getattr(row, "position_pnl", None)),
                    "portfolio_pnl_impact": _safe_number(getattr(row, "portfolio_pnl_impact", None)),
                    "new_portfolio_value": _safe_number(getattr(row, "new_portfolio_value", None)),
                    "probability": _safe_number(getattr(row, "probability", None)),
                    "likelihood": str(getattr(row, "likelihood", "") or ""),
                    "why": str(getattr(row, "why", "") or ""),
                }
            )
        )
    frame = getattr(app_context, "schwab_research_scenarios_frame", None)
    scenario_text = _text_widget_content(getattr(frame, "scenario_note_text", None))
    return _drop_empty(
        {
            "scenario_rows": [row for row in rows if row],
            "summary_text": _shorten(scenario_text, SYMBOL_CHAT_RESEARCH_SECTION_TEXT_LIMIT) if scenario_text else "",
            "generated_risk_budget": _app_attr_display_value(app_context, "schwab_research_max_risk_var") or "",
        }
    )


def _research_options_context(app_context: Any | None, payload: Any) -> dict[str, Any]:
    frame = getattr(app_context, "schwab_research_options_frame", None)
    if frame is None:
        frame = getattr(app_context, "schwab_options_strategy_frame", None)
    detail = _text_widget_content(getattr(frame, "detail_text", None))
    candidates = getattr(app_context, "schwab_research_option_candidates", []) or []
    return _drop_empty(
        {
            "loaded_chain_rows": len(getattr(payload, "option_chain_rows", None) or []),
            "underlying_price": _safe_number(getattr(payload, "option_chain_underlying_price", None)),
            "candidate_count": len(candidates) if isinstance(candidates, list) else None,
            "summary_text": _shorten(detail, SYMBOL_CHAT_RESEARCH_SECTION_TEXT_LIMIT) if detail else "",
        }
    )


def _research_greeks_context(app_context: Any | None, payload: Any) -> dict[str, Any]:
    summary = getattr(payload, "greek_summary", None)
    frame = getattr(app_context, "schwab_research_greeks_frame", None)
    detail = _text_widget_content(getattr(frame, "detail_text", None))
    return _drop_empty(
        {
            "source": str(getattr(summary, "source", "") or ""),
            "plain_english": _short_text_list(getattr(summary, "plain_english", []) or [], limit=5),
            "warnings": _short_text_list(getattr(summary, "warnings", []) or [], limit=5),
            "summary_text": _shorten(detail, SYMBOL_CHAT_RESEARCH_SECTION_TEXT_LIMIT) if detail else "",
        }
    )


def _research_text_section(name: str, text: Any) -> dict[str, Any]:
    clean = str(text or "").strip()
    if not clean:
        return {}
    return {
        "source": name,
        "summary_text": _shorten(clean, SYMBOL_CHAT_RESEARCH_SECTION_TEXT_LIMIT),
    }


def _portfolio_symbol_context_dict(context: Any) -> dict[str, Any]:
    if context is None:
        return {}
    return _drop_empty(
        {
            "symbol": str(getattr(context, "symbol", "") or ""),
            "is_held": bool(getattr(context, "is_held", False)),
            "quantity": _safe_number(getattr(context, "quantity", None)),
            "average_cost": _safe_number(getattr(context, "average_cost", None)),
            "last_price": _safe_number(getattr(context, "last_price", None)),
            "market_value": _safe_number(getattr(context, "market_value", None)),
            "portfolio_value": _safe_number(getattr(context, "portfolio_value", None)),
            "portfolio_weight": _safe_number(getattr(context, "portfolio_weight", None)),
            "unrealized_pnl": _safe_number(getattr(context, "unrealized_pnl", None)),
            "day_pnl": _safe_number(getattr(context, "day_pnl", None)),
            "cash_available": _safe_number(getattr(context, "cash_available", None)),
        }
    )


def _badge_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    return _drop_empty(
        {
            "title": str(getattr(value, "title", "") or ""),
            "label": str(getattr(value, "label", "") or ""),
            "status": str(getattr(value, "status", "") or ""),
            "score": _safe_number(getattr(value, "score", None)),
            "why": str(getattr(value, "why", "") or ""),
        }
    )


def _operator_verdict_context(verdict: Any) -> dict[str, Any]:
    if verdict is None:
        return {}
    return _drop_empty(
        {
            "primary_action": str(getattr(verdict, "primary_action", "") or ""),
            "primary_label": str(getattr(verdict, "primary_label", "") or ""),
            "confidence": str(getattr(verdict, "confidence", "") or ""),
            "confidence_score": _safe_number(getattr(verdict, "confidence_score", None)),
            "summary": str(getattr(verdict, "summary", "") or ""),
            "right_now": _operator_action_line_context(getattr(verdict, "right_now", None)),
            "if_confirms": _operator_action_line_context(getattr(verdict, "if_confirms", None)),
            "if_breaks_down": _operator_action_line_context(getattr(verdict, "if_breaks_down", None)),
            "best_hedge": _operator_action_line_context(getattr(verdict, "best_hedge", None)),
            "preferred_vehicle": _operator_action_line_context(getattr(verdict, "preferred_vehicle", None)),
            "confirmation": _operator_action_line_context(getattr(verdict, "confirmation", None)),
            "invalidation": _operator_action_line_context(getattr(verdict, "invalidation", None)),
            "reasons": _short_text_list(getattr(verdict, "reasons", []) or [], limit=6),
            "warnings": _short_text_list(getattr(verdict, "warnings", []) or [], limit=6),
            "what_would_change": _short_text_list(getattr(verdict, "what_would_change", []) or [], limit=6),
        }
    )


def _operator_action_line_context(line: Any) -> dict[str, Any]:
    if line is None:
        return {}
    return _drop_empty(
        {
            "label": str(getattr(line, "label", "") or ""),
            "action": str(getattr(line, "action", "") or ""),
            "detail": str(getattr(line, "detail", "") or ""),
            "severity": str(getattr(line, "severity", "") or ""),
        }
    )


def _recommendation_engine_context(read: Any) -> dict[str, Any]:
    if read is None:
        return {}
    components = []
    for component in list(getattr(read, "components", []) or [])[:10]:
        vote = getattr(component, "vote", None)
        components.append(
            _drop_empty(
                {
                    "key": str(getattr(component, "key", "") or ""),
                    "label": str(getattr(component, "label", "") or ""),
                    "status": str(getattr(component, "status", "") or ""),
                    "score": _safe_number(getattr(vote, "score", None)),
                    "confidence": _safe_number(getattr(vote, "confidence", None)),
                    "reason": str(getattr(vote, "reason", "") or ""),
                    "details": _short_text_list(getattr(component, "details", []) or [], limit=3),
                    "missing": _short_text_list(getattr(component, "missing", []) or [], limit=3),
                }
            )
        )
    reward_risk = getattr(read, "expected_reward_risk", None)
    data_confidence = getattr(read, "data_confidence", None)
    return _drop_empty(
        {
            "recommendation_label": str(getattr(read, "recommendation_label", "") or ""),
            "confidence": str(getattr(read, "confidence", "") or ""),
            "confidence_score": _safe_number(getattr(read, "confidence_score", None)),
            "evidence_score": _safe_number(getattr(read, "evidence_score", None)),
            "confidence_adjusted_score": _safe_number(getattr(read, "confidence_adjusted_score", None)),
            "why": _short_text_list(getattr(read, "why", []) or [], limit=6),
            "warnings": _short_text_list(getattr(read, "warnings", []) or [], limit=6),
            "confirmation_lines": _short_text_list(getattr(read, "confirmation_lines", []) or [], limit=6),
            "invalidation_lines": _short_text_list(getattr(read, "invalidation_lines", []) or [], limit=6),
            "position_sizing_notes": _short_text_list(getattr(read, "position_sizing_notes", []) or [], limit=6),
            "what_would_change": _short_text_list(getattr(read, "what_would_change", []) or [], limit=6),
            "expected_reward_risk": _drop_empty(
                {
                    "label": str(getattr(reward_risk, "label", "") or ""),
                    "reward_risk_ratio": _safe_number(getattr(reward_risk, "reward_risk_ratio", None)),
                    "planning_probability": _safe_number(getattr(reward_risk, "planning_probability", None)),
                    "summary": str(getattr(reward_risk, "summary", "") or ""),
                    "reward_line": str(getattr(reward_risk, "reward_line", "") or ""),
                    "risk_line": str(getattr(reward_risk, "risk_line", "") or ""),
                }
            ),
            "data_confidence": _drop_empty(
                {
                    "grade": str(getattr(data_confidence, "grade", "") or ""),
                    "score": _safe_number(getattr(data_confidence, "score", None)),
                    "reason": str(getattr(data_confidence, "reason", "") or ""),
                    "missing": _short_text_list(getattr(data_confidence, "missing", []) or [], limit=5),
                    "stale": _short_text_list(getattr(data_confidence, "stale", []) or [], limit=5),
                }
            ),
            "components": [component for component in components if component],
        }
    )


def _source_status_context(statuses: Iterable[Any]) -> list[dict[str, Any]]:
    rows = []
    for status in list(statuses)[:12]:
        rows.append(
            _drop_empty(
                {
                    "source": str(getattr(status, "source", "") or ""),
                    "status": str(getattr(status, "status", "") or ""),
                    "fetched_at": str(getattr(status, "fetched_at", "") or ""),
                    "message": str(getattr(status, "message", "") or ""),
                }
            )
        )
    return [row for row in rows if row]


def _short_text_list(values: Iterable[Any], *, limit: int) -> list[str]:
    result = []
    for value in values:
        clean = str(value or "").strip()
        if clean:
            result.append(_shorten(clean, 600))
        if len(result) >= limit:
            break
    return result


def _drop_empty(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {}, ())}


def _limit_research_workspace_context(context: dict[str, Any]) -> dict[str, Any]:
    serialized = _serialize_request_payload(context)
    if len(serialized) <= SYMBOL_CHAT_RESEARCH_WORKSPACE_TEXT_LIMIT:
        return context
    limited = dict(context)
    for section_name in ("earnings_news", "fundamentals", "macro_context", "risk_scenarios", "options_strategy", "greeks"):
        section = limited.get(section_name)
        if isinstance(section, dict) and section.get("summary_text"):
            section["summary_text"] = _shorten(section["summary_text"], 1_600)
    if isinstance(limited.get("overview_text"), str):
        limited["overview_text"] = _shorten(limited["overview_text"], 1_800)
    return limited


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


def _web_enrichment_context(
    app_context: Any | None,
    symbol: str,
    enabled: bool,
    provider: Callable[[str], Mapping[str, Any] | None] | None,
    source_metadata: dict[str, Any],
) -> dict[str, Any]:
    if not enabled:
        source_metadata["web_enrichment_mode"] = "disabled"
        return {}

    active_provider = provider or getattr(app_context, "symbol_chat_web_enrichment_provider", None)
    source_metadata["web_enrichment_mode"] = "requested"
    if not callable(active_provider):
        _mark_unavailable(source_metadata, "web_enrichment", "Web enrichment was requested, but no Symbol Chat web-enrichment provider is configured.")
        return {
            "enabled": True,
            "status": "unavailable",
            "reason": "No web-enrichment provider is configured in this build.",
            "source": "symbol_chat_web_enrichment_stub",
        }

    try:
        result = active_provider(symbol)
    except Exception as exc:
        _mark_unavailable(source_metadata, "web_enrichment", f"Web enrichment provider failed: {exc}")
        return {
            "enabled": True,
            "status": "error",
            "reason": str(exc),
            "source": "symbol_chat_web_enrichment_provider",
        }

    if not isinstance(result, Mapping) or not result:
        _mark_unavailable(source_metadata, "web_enrichment", "Web enrichment provider returned no public context.")
        return {
            "enabled": True,
            "status": "empty",
            "source": "symbol_chat_web_enrichment_provider",
        }

    context = _json_safe(dict(result))
    if isinstance(context, Mapping):
        compact = dict(context)
    else:
        compact = {"summary": context}
    compact.setdefault("enabled", True)
    compact.setdefault("status", "available")
    compact.setdefault("source", "symbol_chat_web_enrichment_provider")
    _mark_available(source_metadata, "web_enrichment", "Loaded optional public web/search enrichment from configured provider.")
    return _limit_web_enrichment_context(compact)


def _limit_web_enrichment_context(context: dict[str, Any]) -> dict[str, Any]:
    if len(_serialize_request_payload(context)) <= SYMBOL_CHAT_WEB_TEXT_LIMIT:
        return context
    limited = dict(context)
    for key in ("summary", "company_profile", "recent_news", "earnings", "fundamentals", "filings"):
        value = limited.get(key)
        if isinstance(value, str):
            limited[key] = _shorten(value, 1_500)
        elif isinstance(value, list):
            limited[key] = value[:6]
    if len(_serialize_request_payload(limited)) <= SYMBOL_CHAT_WEB_TEXT_LIMIT:
        return limited
    limited["truncation_note"] = f"Web enrichment was truncated to fit the {SYMBOL_CHAT_WEB_TEXT_LIMIT} character sub-budget."
    return limited


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
    if context.web_enrichment:
        if str(context.web_enrichment.get("status") or "").lower() == "available":
            return "symbol_context_bundle_plus_web_enrichment"
        return "symbol_context_bundle_web_enrichment_requested_unavailable"
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
    layers = sorted({str(item).split(":", 1)[0].strip() for item in available if str(item).strip()})
    lines.append("context_layers=" + (",".join(layers) if layers else "none"))
    if isinstance(metadata, Mapping) and metadata.get("web_enrichment_mode"):
        lines.append(f"web_enrichment_mode={metadata.get('web_enrichment_mode')}")
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
        research = context.get("research_workspace_context")
        if isinstance(research, dict):
            for section_name in ("earnings_news", "fundamentals", "macro_context", "risk_scenarios", "options_strategy", "greeks"):
                section = research.get(section_name)
                if isinstance(section, dict) and section.get("summary_text"):
                    section["summary_text"] = _shorten(str(section["summary_text"]), 1_500)
            if isinstance(research.get("overview_text"), str):
                research["overview_text"] = _shorten(str(research["overview_text"]), 1_500)
        web = context.get("web_enrichment")
        if isinstance(web, dict):
            context["web_enrichment"] = _limit_web_enrichment_context(web)
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


def _display_number(value: Any) -> float | None:
    parsed = _safe_number(value)
    if parsed is not None:
        return parsed
    text = str(value or "").strip()
    if not text or text in {"--", "N/A", "n/a"}:
        return None
    match = re.search(r"\(?\s*-?\$?\s*\d[\d,]*(?:\.\d+)?\s*%?\)?", text)
    if not match:
        return None
    token = match.group(0)
    negative = "(" in token and ")" in token
    token = token.replace("$", "").replace(",", "").replace("%", "").replace("(", "").replace(")", "").strip()
    try:
        number = float(token)
    except ValueError:
        return None
    return -abs(number) if negative else number


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
