from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from app.analytics.earnings_pipeline import EarningsRadarStore, RecentEarningsRecord
from app.analytics.openai_ipo_report import _redact_api_key, _response_output_text
from app.analytics.symbol_chat import (
    OpenAiSymbolChatClient,
    SYMBOL_CHAT_APPROX_CHARS_PER_TOKEN,
    SYMBOL_CHAT_REQUEST_CHAR_LIMIT,
    redact_symbol_chat_secrets,
)
from app.data.earnings_calendar import AlphaVantageEarningsCalendarClient, UpcomingEarningsRecord
from app.data.market_universe import MarketUniverseEntry, MarketUniverseSnapshot, fetch_market_universe_snapshot
from app.data.sec_edgar import SecEdgarClient


LOGGER = logging.getLogger(__name__)

MARKET_SCREENER_REQUEST_CHAR_LIMIT = SYMBOL_CHAT_REQUEST_CHAR_LIMIT
MARKET_SCREENER_SOURCE_MODE = "market_screener_selected_row"
MISSING_TEXT = "--"

MARKET_SCREENER_AI_SYSTEM_PROMPT = """You are a market intelligence analyst inside Portfolio Risk Cockpit.

Analyze only the selected Market Intelligence Screener row and explicit selected-row context in the request.
Do not invent missing prices, volume, market cap, valuation, EPS, revenue growth, earnings dates, filings, catalysts, or source links.
When a field is absent, say "Not available in the selected Market Intelligence Screener row."
Treat signals and risk flags as deterministic app/provider observations, not audited conclusions.
Treat source snippets as selected-row excerpts only when they are provided.

Keep the response research-only. Do not place trades. Do not tell the user to buy, sell, or hold.
You may explain what looks interesting, what is uncertain, and what diligence should come next.
Separate confirmed selected-row facts from assumptions, caveats, missing data, and verification questions.
Never include credentials, API keys, account identifiers, or secrets.
"""

MARKET_SCREENER_AI_ANALYZE_PROMPT = (
    "Create a concise market intelligence brief for the selected screener row. "
    "Cover why it appears in the screener, event/filing/earnings signals, available metrics, risk flags, "
    "missing data, source links, and concrete diligence steps. Keep it research-only."
)

MARKET_SCREENER_AI_QUICK_PROMPTS: dict[str, str] = {
    "Why Interesting?": (
        "Explain why this selected screener row may be interesting using only the row facts. "
        "Separate observed signals from missing data and verification needs."
    ),
    "Bull vs Bear": (
        "Frame a bull case and bear case using only the selected screener row. "
        "Label assumptions and do not make a buy, sell, or hold recommendation."
    ),
    "Risks + Diligence": (
        "Summarize risks and generate a focused diligence checklist using only the selected row, "
        "source links, signals, risk flags, and explicitly provided snippets."
    ),
}

EVENT_TYPE_OPTIONS = (
    "All",
    "Upcoming earnings",
    "Recent SEC filing",
    "Guidance mentioned",
    "Risk flags",
    "High volume / mover",
    "Schwab holding/watchlist",
)
EARNINGS_WINDOW_OPTIONS = ("All", "Next 7 days", "Next 30 days", "Next 90 days")

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class MarketScreenerSourceStatus:
    source: str
    status: str
    fetched_at: str
    message: str


@dataclass(frozen=True)
class MarketScreenerRecord:
    symbol: str
    company_name: str | None = None
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    price: float | None = None
    market_cap: float | None = None
    volume: float | None = None
    avg_volume: float | None = None
    change_percent: float | None = None
    pe_ratio: float | None = None
    eps: float | None = None
    revenue_growth: float | None = None
    next_earnings_date: str | None = None
    recent_filing_date: str | None = None
    recent_filing_type: str | None = None
    signals: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    source_links: tuple[str, ...] = ()
    fetched_at: str = ""
    cik: str | None = None
    source_excerpt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MarketScreenerRecord":
        values = dict(payload)
        values["symbol"] = _normalize_symbol(values.get("symbol"))
        values["signals"] = tuple(values.get("signals") or ())
        values["risk_flags"] = tuple(values.get("risk_flags") or ())
        values["sources"] = tuple(values.get("sources") or ())
        values["source_links"] = tuple(values.get("source_links") or ())
        return cls(**values)


@dataclass(frozen=True)
class MarketScreenerSnapshot:
    records: tuple[MarketScreenerRecord, ...]
    fetched_at: str
    sources: tuple[str, ...]
    statuses: tuple[MarketScreenerSourceStatus, ...]
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class MarketScreenerAiResponse:
    answer: str
    response_id: str
    model: str
    source_mode: str
    source_debug: tuple[str, ...]


class OpenAiMarketScreenerError(RuntimeError):
    """Raised for row-grounded Market Screener OpenAI failures with secrets redacted."""


class OpenAiMarketScreenerClient:
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
        record: MarketScreenerRecord,
        prompt: str,
        *,
        source_snippets: Iterable[str] | None = None,
        progress_callback: ProgressCallback | None = None,
    ) -> MarketScreenerAiResponse:
        started_at = time.perf_counter()
        clean_prompt = _clean_prompt(prompt)
        if not clean_prompt:
            raise OpenAiMarketScreenerError("Enter a market screener analysis question before sending.")

        _notify_progress(progress_callback, "Preparing selected screener row context...")
        request_payload = market_screener_ai_request_payload(
            record,
            clean_prompt,
            source_snippets=source_snippets,
            timeout_seconds=self.timeout_seconds,
        )
        payload_text = _serialize_request_payload(request_payload)
        diagnostics = {
            "request_payload_chars": len(payload_text),
            "request_payload_approx_tokens": _approx_token_count(len(payload_text)),
            "request_payload_char_limit": MARKET_SCREENER_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": self.timeout_seconds,
        }
        request_budget = request_payload.get("request_budget") if isinstance(request_payload, Mapping) else {}
        if isinstance(request_budget, Mapping):
            for key in ("pre_trim_payload_chars", "final_payload_chars", "budget_trimmed"):
                if key in request_budget:
                    diagnostics[f"request_payload_{key}"] = request_budget.get(key)

        input_messages = [
            {"role": "system", "content": MARKET_SCREENER_AI_SYSTEM_PROMPT},
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
                    f"OpenAI market screener analysis timed out after {self.timeout_seconds:g} seconds. "
                    "Try a narrower selected-row question or retry later."
                )
            else:
                message = f"OpenAI market screener analysis failed: {exc}"
            message = _redact_api_key(message, self._symbol_chat_client._current_api_key())
            message = redact_symbol_chat_secrets(message)
            LOGGER.warning(
                "AI market screener request failed symbol=%s elapsed=%.3fs error=%s",
                record.symbol,
                time.perf_counter() - started_at,
                message,
            )
            raise OpenAiMarketScreenerError(message) from None

        diagnostics["openai_seconds"] = round(time.perf_counter() - openai_started, 3)
        diagnostics["total_seconds"] = round(time.perf_counter() - started_at, 3)
        _notify_progress(progress_callback, "OpenAI response received.")
        answer = redact_symbol_chat_secrets(str(_response_output_text(response) or "").strip())
        if not answer:
            raise OpenAiMarketScreenerError("OpenAI market screener analysis returned an empty response.")

        return MarketScreenerAiResponse(
            answer=answer,
            response_id=str(getattr(response, "id", "") or ""),
            model=self.model,
            source_mode=MARKET_SCREENER_SOURCE_MODE,
            source_debug=tuple(_source_debug_lines(request_payload, diagnostics)),
        )


def fetch_market_screener_snapshot(
    *,
    sec_client: SecEdgarClient | None = None,
    universe_snapshot: MarketUniverseSnapshot | None = None,
    recent_records: Iterable[RecentEarningsRecord] | None = None,
    upcoming_records: Iterable[UpcomingEarningsRecord] | None = None,
    supplemental_records: Iterable[MarketScreenerRecord] | None = None,
    upcoming_provider: Any | None = None,
    universe_limit: int = 750,
    horizon: str = "3month",
    upcoming_symbols: Iterable[str] | None = None,
    force_refresh: bool = False,
    include_fallback_universe: bool = True,
) -> MarketScreenerSnapshot:
    fetched_at = _now()
    statuses: list[MarketScreenerSourceStatus] = []
    errors: list[str] = []
    client = sec_client or SecEdgarClient(timeout_seconds=12)

    if universe_snapshot is None:
        universe_snapshot = fetch_market_universe_snapshot(client, limit=universe_limit, include_fallback=include_fallback_universe)
    statuses.extend(
        MarketScreenerSourceStatus(status.source, status.status, status.fetched_at, status.message)
        for status in universe_snapshot.statuses
    )
    errors.extend(universe_snapshot.errors)

    recent = _load_recent_records(client, recent_records, statuses, errors, fetched_at)
    upcoming = _load_upcoming_records(upcoming_provider, upcoming_records, statuses, errors, fetched_at, horizon, upcoming_symbols, force_refresh)

    records = build_market_screener_records(
        universe_snapshot.records,
        recent,
        upcoming,
        supplemental_records=supplemental_records or (),
        fetched_at=fetched_at,
    )
    statuses.append(_market_data_coverage_status(records, fetched_at))
    sources = tuple(sorted({source for record in records for source in record.sources if source}))
    return MarketScreenerSnapshot(
        records=tuple(records),
        fetched_at=fetched_at,
        sources=sources,
        statuses=tuple(statuses),
        errors=tuple(errors),
    )


def build_market_screener_records(
    universe: Iterable[MarketUniverseEntry],
    recent_records: Iterable[RecentEarningsRecord] = (),
    upcoming_records: Iterable[UpcomingEarningsRecord] = (),
    *,
    supplemental_records: Iterable[MarketScreenerRecord] = (),
    fetched_at: str | None = None,
) -> list[MarketScreenerRecord]:
    fetched_at = fetched_at or _now()
    merged: dict[str, MarketScreenerRecord] = {}

    for entry in universe:
        record = _record_from_universe(entry, fetched_at=fetched_at)
        _merge_into(merged, record)

    for record in recent_records:
        _merge_into(merged, _record_from_recent_earnings(record, fetched_at=fetched_at))

    for record in upcoming_records:
        _merge_into(merged, _record_from_upcoming_earnings(record, fetched_at=fetched_at))

    for record in supplemental_records:
        _merge_into(merged, _normalize_record(record, fetched_at=fetched_at))

    return sorted(merged.values(), key=lambda row: ((row.symbol or "ZZZZ").upper(), (row.company_name or "").lower()))


def filter_market_screener_records(
    records: Iterable[MarketScreenerRecord],
    *,
    search: str = "",
    sector: str = "All",
    exchange: str = "All",
    event_type: str = "All",
    risk_flag: str = "All",
    earnings_date_window: str = "All",
    has_ai_signal: bool = False,
    today: date | None = None,
) -> list[MarketScreenerRecord]:
    search_text = search.strip().lower()
    today = today or datetime.now().date()
    filtered: list[MarketScreenerRecord] = []
    for record in records:
        if search_text and search_text not in _screener_search_text(record):
            continue
        if sector != "All" and (record.sector or MISSING_TEXT) != sector:
            continue
        if exchange != "All" and (record.exchange or MISSING_TEXT) != exchange:
            continue
        if risk_flag == "Any risk flag" and not record.risk_flags:
            continue
        if risk_flag not in {"All", "Any risk flag"} and risk_flag not in record.risk_flags:
            continue
        if not _event_type_matches(record, event_type):
            continue
        if not _earnings_window_matches(record, earnings_date_window, today):
            continue
        if has_ai_signal and not market_screener_has_ai_signal(record):
            continue
        filtered.append(record)
    return filtered


def sort_market_screener_records(
    records: Iterable[MarketScreenerRecord],
    column: str,
    *,
    descending: bool = False,
) -> list[MarketScreenerRecord]:
    rows = list(records)
    present = [record for record in rows if _sort_value(record, column) is not None]
    missing = [record for record in rows if _sort_value(record, column) is None]
    present.sort(key=lambda record: _sort_value(record, column), reverse=descending)
    return present + missing


def market_screener_has_ai_signal(record: MarketScreenerRecord) -> bool:
    return bool(record.signals or record.risk_flags or record.next_earnings_date or record.recent_filing_date)


def market_screener_ai_request_payload(
    record: MarketScreenerRecord,
    prompt: str,
    *,
    source_snippets: Iterable[str] | None = None,
    timeout_seconds: float,
) -> dict[str, Any]:
    selected = market_screener_record_context(record)
    snippets = _source_snippet_payload(source_snippets or _record_source_snippets(record))
    payload = {
        "question": _clean_prompt(prompt),
        "selected_market_screener_record": selected,
        "source_snippets": snippets or _not_available("No source text snippet is available in the selected Market Intelligence Screener row."),
        "grounding_rules": [
            "Use only selected_market_screener_record, source_snippets, and this request for factual claims.",
            'Say "Not available in the selected Market Intelligence Screener row." when a requested fact is absent.',
            "Do not infer missing prices, volume, market cap, valuation, EPS, revenue growth, earnings dates, filings, catalysts, or source links.",
            "Treat signals and risk flags as deterministic app/provider observations, not audited conclusions.",
            "Use source_links as pointers only; do not claim to have opened them unless source_snippets contain text.",
            "Keep the answer research-only. Do not place trades, submit orders, automate broker actions, or tell the user to buy, sell, or hold.",
            "Separate confirmed selected-row facts from assumptions, caveats, missing data, and diligence questions.",
        ],
        "request_budget": {
            "request_payload_char_limit": MARKET_SCREENER_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": timeout_seconds,
        },
    }
    return _enforce_request_payload_budget(payload)


def market_screener_record_context(record: MarketScreenerRecord) -> dict[str, Any]:
    fields = {
        "symbol": _text_or_missing(record.symbol, "symbol"),
        "company_name": _text_or_missing(record.company_name, "company_name"),
        "cik": _text_or_missing(record.cik, "cik"),
        "exchange": _text_or_missing(record.exchange, "exchange"),
        "sector": _text_or_missing(record.sector, "sector"),
        "industry": _text_or_missing(record.industry, "industry"),
        "market_data": {
            "price": _number_or_missing(record.price, "price"),
            "market_cap": _number_or_missing(record.market_cap, "market_cap"),
            "volume": _number_or_missing(record.volume, "volume"),
            "avg_volume": _number_or_missing(record.avg_volume, "avg_volume"),
            "change_percent": _number_or_missing(record.change_percent, "change_percent"),
            "pe_ratio": _number_or_missing(record.pe_ratio, "pe_ratio"),
        },
        "fundamental_fields": {
            "eps": _number_or_missing(record.eps, "eps"),
            "revenue_growth_percent": _number_or_missing(record.revenue_growth, "revenue_growth"),
        },
        "event_fields": {
            "next_earnings_date": _text_or_missing(record.next_earnings_date, "next_earnings_date"),
            "recent_filing_date": _text_or_missing(record.recent_filing_date, "recent_filing_date"),
            "recent_filing_type": _text_or_missing(record.recent_filing_type, "recent_filing_type"),
        },
        "signals": list(record.signals) if record.signals else _not_available("No screener signals are present in the selected row."),
        "risk_flags": list(record.risk_flags) if record.risk_flags else _not_available("No risk flags are present in the selected row."),
        "sources": list(record.sources) if record.sources else _not_available("No source labels are present in the selected row."),
        "source_links": list(record.source_links) if record.source_links else _not_available("No source links are present in the selected row."),
        "fetched_at": _text_or_missing(record.fetched_at, "fetched_at"),
        "missing_fields": _missing_fields(record),
        "analysis_policy": {
            "scope": "selected Market Intelligence Screener row only",
            "research_only": True,
            "trading_instructions_allowed": False,
        },
    }
    return _json_safe(fields)


def _load_recent_records(
    client: SecEdgarClient,
    provided: Iterable[RecentEarningsRecord] | None,
    statuses: list[MarketScreenerSourceStatus],
    errors: list[str],
    fetched_at: str,
) -> tuple[RecentEarningsRecord, ...]:
    if provided is not None:
        rows = tuple(provided)
        statuses.append(
            MarketScreenerSourceStatus(
                "Recent EDGAR earnings",
                "available" if rows else "empty",
                fetched_at,
                f"Merged {len(rows)} recent SEC earnings/filing row(s) from the open Earnings Radar state.",
            )
        )
        return rows

    try:
        cached = EarningsRadarStore(client.cache_dir).load(max_age=None)
    except Exception as exc:
        cached = None
        errors.append(f"Recent EDGAR earnings cache: {exc}")
    if cached is not None:
        statuses.append(
            MarketScreenerSourceStatus(
                "Recent EDGAR earnings",
                "cache",
                fetched_at,
                f"Merged {len(cached.recent)} recent SEC earnings/filing row(s) from cached Earnings Radar data.",
            )
        )
        return tuple(cached.recent)

    statuses.append(
        MarketScreenerSourceStatus(
            "Recent EDGAR earnings",
            "unavailable",
            fetched_at,
            "No cached or loaded Earnings Radar SEC rows are available yet. Refresh Recent EDGAR Drops to enrich the screener.",
        )
    )
    return ()


def _load_upcoming_records(
    provider: Any | None,
    provided: Iterable[UpcomingEarningsRecord] | None,
    statuses: list[MarketScreenerSourceStatus],
    errors: list[str],
    fetched_at: str,
    horizon: str,
    symbols: Iterable[str] | None,
    force_refresh: bool,
) -> tuple[UpcomingEarningsRecord, ...]:
    if provided is not None:
        rows = tuple(provided)
        statuses.append(
            MarketScreenerSourceStatus(
                "Upcoming earnings calendar",
                "available" if rows else "empty",
                fetched_at,
                f"Merged {len(rows)} upcoming earnings row(s) from the open Upcoming Earnings state.",
            )
        )
        return rows

    client = provider or AlphaVantageEarningsCalendarClient()
    try:
        try:
            rows = tuple(client.upcoming_earnings(horizon=horizon, symbols=symbols, force_refresh=force_refresh))
        except TypeError:
            rows = tuple(client.upcoming_earnings(horizon=horizon, symbols=symbols))
        status_text = str(getattr(client, "last_status", "") or "")
        status = "available" if rows else "empty"
        if not rows and "not configured" in status_text.lower():
            status = "unavailable"
        statuses.append(
            MarketScreenerSourceStatus(
                "Upcoming earnings calendar",
                status,
                fetched_at,
                status_text or f"Loaded {len(rows)} upcoming earnings row(s).",
            )
        )
        return rows
    except Exception as exc:
        errors.append(f"Upcoming earnings calendar: {exc}")
        statuses.append(
            MarketScreenerSourceStatus(
                "Upcoming earnings calendar",
                "error",
                fetched_at,
                f"Upcoming earnings provider failed: {exc}",
            )
        )
        return ()


def _market_data_coverage_status(records: Iterable[MarketScreenerRecord], fetched_at: str) -> MarketScreenerSourceStatus:
    rows = list(records)
    has_market_data = any(
        record.price is not None
        or record.volume is not None
        or record.market_cap is not None
        or record.pe_ratio is not None
        or record.change_percent is not None
        for record in rows
    )
    if has_market_data:
        return MarketScreenerSourceStatus(
            "Market quote/fundamental metrics",
            "partial",
            fetched_at,
            "Some rows include local/provider quote or valuation fields; missing fields remain blank and are not inferred.",
        )
    return MarketScreenerSourceStatus(
        "Market quote/fundamental metrics",
        "unavailable",
        fetched_at,
        "No broad quote/fundamental provider is configured for this MVP. Price, volume, market cap, P/E, and change fields remain unavailable unless local rows supply them.",
    )


def _record_from_universe(entry: MarketUniverseEntry, *, fetched_at: str) -> MarketScreenerRecord:
    source_links = (entry.source_url,) if entry.source_url else ()
    return MarketScreenerRecord(
        symbol=_normalize_symbol(entry.symbol),
        company_name=entry.company_name,
        cik=entry.cik,
        exchange=entry.exchange,
        sector=entry.sector,
        industry=entry.industry,
        sources=(entry.source,),
        source_links=source_links,
        fetched_at=fetched_at,
    )


def _record_from_recent_earnings(record: RecentEarningsRecord, *, fetched_at: str) -> MarketScreenerRecord:
    signals = [
        record.filing_type or "Recent SEC filing",
        f"{record.form} {record.items}".strip(),
    ]
    if record.guidance_flag:
        signals.append("Guidance mentioned")
    if record.exhibit_url:
        signals.append("Earnings exhibit available")
    source_links = [record.filing_url]
    if record.exhibit_url:
        source_links.append(record.exhibit_url)
    return MarketScreenerRecord(
        symbol=_normalize_symbol(record.ticker),
        company_name=record.company_name,
        cik=record.cik,
        exchange=record.exchange,
        sector=record.sector,
        industry=record.industry,
        eps=record.eps,
        revenue_growth=record.revenue_growth,
        recent_filing_date=record.filed_date,
        recent_filing_type=f"{record.form} {record.items or record.filing_type}".strip(),
        signals=tuple(_dedupe_texts(signals)),
        risk_flags=tuple(record.risk_flags),
        sources=(record.source or "SEC EDGAR",),
        source_links=tuple(_dedupe_texts(source_links)),
        fetched_at=fetched_at,
        source_excerpt=getattr(record, "source_excerpt", None),
    )


def _record_from_upcoming_earnings(record: UpcomingEarningsRecord, *, fetched_at: str) -> MarketScreenerRecord:
    source_links = (record.source_url,) if record.source_url else ()
    return MarketScreenerRecord(
        symbol=_normalize_symbol(record.symbol),
        company_name=record.company_name,
        next_earnings_date=record.report_date,
        signals=("Upcoming earnings",),
        sources=(record.source or "Upcoming earnings calendar",),
        source_links=source_links,
        fetched_at=fetched_at,
    )


def _normalize_record(record: MarketScreenerRecord, *, fetched_at: str) -> MarketScreenerRecord:
    return MarketScreenerRecord(
        symbol=_normalize_symbol(record.symbol),
        company_name=record.company_name,
        exchange=record.exchange,
        sector=record.sector,
        industry=record.industry,
        price=record.price,
        market_cap=record.market_cap,
        volume=record.volume,
        avg_volume=record.avg_volume,
        change_percent=record.change_percent,
        pe_ratio=record.pe_ratio,
        eps=record.eps,
        revenue_growth=record.revenue_growth,
        next_earnings_date=record.next_earnings_date,
        recent_filing_date=record.recent_filing_date,
        recent_filing_type=record.recent_filing_type,
        signals=tuple(_dedupe_texts(record.signals)),
        risk_flags=tuple(_dedupe_texts(record.risk_flags)),
        sources=tuple(_dedupe_texts(record.sources)),
        source_links=tuple(_dedupe_texts(record.source_links)),
        fetched_at=record.fetched_at or fetched_at,
        cik=record.cik,
        source_excerpt=record.source_excerpt,
    )


def _merge_into(records: dict[str, MarketScreenerRecord], incoming: MarketScreenerRecord) -> None:
    key = _record_key(incoming)
    if not key:
        return
    existing = records.get(key)
    records[key] = incoming if existing is None else merge_market_screener_record(existing, incoming)


def merge_market_screener_record(existing: MarketScreenerRecord, incoming: MarketScreenerRecord) -> MarketScreenerRecord:
    return MarketScreenerRecord(
        symbol=existing.symbol or incoming.symbol,
        company_name=existing.company_name or incoming.company_name,
        exchange=existing.exchange or incoming.exchange,
        sector=existing.sector or incoming.sector,
        industry=existing.industry or incoming.industry,
        price=_prefer_number(incoming.price, existing.price),
        market_cap=_prefer_number(incoming.market_cap, existing.market_cap),
        volume=_prefer_number(incoming.volume, existing.volume),
        avg_volume=_prefer_number(incoming.avg_volume, existing.avg_volume),
        change_percent=_prefer_number(incoming.change_percent, existing.change_percent),
        pe_ratio=_prefer_number(incoming.pe_ratio, existing.pe_ratio),
        eps=_prefer_number(incoming.eps, existing.eps),
        revenue_growth=_prefer_number(incoming.revenue_growth, existing.revenue_growth),
        next_earnings_date=_earlier_date(existing.next_earnings_date, incoming.next_earnings_date),
        recent_filing_date=_later_date(existing.recent_filing_date, incoming.recent_filing_date),
        recent_filing_type=incoming.recent_filing_type or existing.recent_filing_type,
        signals=tuple(_dedupe_texts((*existing.signals, *incoming.signals))),
        risk_flags=tuple(_dedupe_texts((*existing.risk_flags, *incoming.risk_flags))),
        sources=tuple(_dedupe_texts((*existing.sources, *incoming.sources))),
        source_links=tuple(_dedupe_texts((*existing.source_links, *incoming.source_links))),
        fetched_at=incoming.fetched_at or existing.fetched_at,
        cik=existing.cik or incoming.cik,
        source_excerpt=existing.source_excerpt or incoming.source_excerpt,
    )


def _record_key(record: MarketScreenerRecord) -> str:
    symbol = _normalize_symbol(record.symbol)
    if symbol:
        return f"SYMBOL:{symbol}"
    cik = str(record.cik or "").strip()
    return f"CIK:{cik}" if cik else ""


def _event_type_matches(record: MarketScreenerRecord, event_type: str) -> bool:
    if event_type == "All":
        return True
    if event_type == "Upcoming earnings":
        return bool(record.next_earnings_date)
    if event_type == "Recent SEC filing":
        return bool(record.recent_filing_date)
    if event_type == "Guidance mentioned":
        return "Guidance mentioned" in record.signals
    if event_type == "Risk flags":
        return bool(record.risk_flags)
    if event_type == "High volume / mover":
        high_volume = record.volume is not None and record.avg_volume not in (None, 0) and record.volume >= record.avg_volume * 1.5
        mover = record.change_percent is not None and abs(record.change_percent) >= 5.0
        return bool(high_volume or mover)
    if event_type == "Schwab holding/watchlist":
        return any(signal in {"Schwab holding", "Watchlist"} for signal in record.signals)
    return True


def _earnings_window_matches(record: MarketScreenerRecord, window: str, today: date) -> bool:
    if window == "All":
        return True
    days = {"Next 7 days": 7, "Next 30 days": 30, "Next 90 days": 90}.get(window)
    if days is None:
        return True
    event_date = _parse_date(record.next_earnings_date)
    if event_date is None:
        return False
    delta = (event_date - today).days
    return 0 <= delta <= days


def _sort_value(record: MarketScreenerRecord, column: str) -> Any:
    return {
        "symbol": record.symbol or None,
        "company": (record.company_name or "").lower() or None,
        "exchange": record.exchange,
        "sector": record.sector,
        "industry": record.industry,
        "price": record.price,
        "market_cap": record.market_cap,
        "volume": record.volume,
        "avg_volume": record.avg_volume,
        "change_percent": record.change_percent,
        "pe_ratio": record.pe_ratio,
        "eps": record.eps,
        "revenue_growth": record.revenue_growth,
        "next_earnings": record.next_earnings_date,
        "recent_filing": record.recent_filing_date,
        "recent_type": record.recent_filing_type,
        "signals": len(record.signals),
        "risk_flags": len(record.risk_flags),
        "sources": len(record.sources),
    }.get(column)


def _screener_search_text(record: MarketScreenerRecord) -> str:
    return " ".join(
        [
            record.symbol,
            record.company_name or "",
            record.cik or "",
            record.exchange or "",
            record.sector or "",
            record.industry or "",
            record.recent_filing_type or "",
            " ".join(record.signals),
            " ".join(record.risk_flags),
            " ".join(record.sources),
        ]
    ).lower()


def _missing_fields(record: MarketScreenerRecord) -> list[str]:
    checks = {
        "company_name": record.company_name,
        "exchange": record.exchange,
        "sector": record.sector,
        "industry": record.industry,
        "price": record.price,
        "market_cap": record.market_cap,
        "volume": record.volume,
        "avg_volume": record.avg_volume,
        "change_percent": record.change_percent,
        "pe_ratio": record.pe_ratio,
        "eps": record.eps,
        "revenue_growth": record.revenue_growth,
        "next_earnings_date": record.next_earnings_date,
        "recent_filing_date": record.recent_filing_date,
        "recent_filing_type": record.recent_filing_type,
        "signals": record.signals,
        "risk_flags": record.risk_flags,
        "source_links": record.source_links,
        "source_excerpt": record.source_excerpt,
    }
    return [field for field, value in checks.items() if value in (None, "", ())]


def _record_source_snippets(record: MarketScreenerRecord) -> tuple[str, ...]:
    source_excerpt = getattr(record, "source_excerpt", None)
    return (str(source_excerpt),) if source_excerpt else ()


def _source_snippet_payload(snippets: Iterable[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, snippet in enumerate(snippets, start=1):
        clean = _shorten(snippet, 2_500)
        if clean:
            rows.append({"index": index, "text": clean})
    return rows[:4]


def _source_debug_lines(request_payload: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> list[str]:
    selected = request_payload.get("selected_market_screener_record") if isinstance(request_payload, Mapping) else {}
    snippets = request_payload.get("source_snippets") if isinstance(request_payload, Mapping) else []
    lines = []
    if isinstance(selected, Mapping):
        for key in ("symbol", "company_name", "next_earnings_date", "recent_filing_date", "recent_filing_type"):
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
    trimmed = pre_trim_chars > MARKET_SCREENER_REQUEST_CHAR_LIMIT
    if trimmed:
        snippets = payload.get("source_snippets")
        if isinstance(snippets, list):
            for snippet in snippets:
                if isinstance(snippet, dict) and isinstance(snippet.get("text"), str):
                    snippet["text"] = _shorten(snippet["text"], 900)
            payload["source_snippets"] = snippets[:2]
    if len(_serialize_request_payload(payload)) > MARKET_SCREENER_REQUEST_CHAR_LIMIT:
        selected = payload.get("selected_market_screener_record")
        if isinstance(selected, dict) and isinstance(selected.get("missing_fields"), list):
            selected["missing_fields"] = selected["missing_fields"][:20]
        payload["source_snippets"] = _not_available("Source snippets were omitted to fit the OpenAI request budget.")
        trimmed = True
    request_budget = payload.setdefault("request_budget", {})
    if not isinstance(request_budget, dict):
        payload["request_budget"] = request_budget = {}
    request_budget["request_payload_char_limit"] = MARKET_SCREENER_REQUEST_CHAR_LIMIT
    request_budget["pre_trim_payload_chars"] = pre_trim_chars
    request_budget["final_payload_chars"] = len(_serialize_request_payload(payload))
    request_budget["budget_trimmed"] = bool(trimmed)
    return _json_safe(payload)


def _text_or_missing(value: Any, field: str) -> Any:
    clean = str(value or "").strip()
    return redact_symbol_chat_secrets(clean) if clean else _not_available(f"{field} is not available in the selected Market Intelligence Screener row.")


def _number_or_missing(value: Any, field: str) -> Any:
    if value is None:
        return _not_available(f"{field} was not provided for the selected Market Intelligence Screener row.")
    return value


def _not_available(reason: str = "Not available in the selected Market Intelligence Screener row.") -> dict[str, str]:
    return {"status": "Not available in the selected Market Intelligence Screener row.", "reason": reason}


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
    return text[: max(0, limit - 80)].rstrip() + f"\n\n[Truncated to {limit} characters for Market Screener AI context.]"


def _clean_prompt(prompt: str) -> str:
    return " ".join(str(prompt or "").split())


def _notify_progress(callback: ProgressCallback | None, message: str) -> None:
    if callback is None:
        return
    try:
        callback(message)
    except Exception:
        LOGGER.debug("AI market screener progress callback failed", exc_info=True)


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


def _prefer_number(primary: float | None, fallback: float | None) -> float | None:
    return primary if primary is not None else fallback


def _earlier_date(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    left_date = _parse_date(left)
    right_date = _parse_date(right)
    if left_date is None:
        return right
    if right_date is None:
        return left
    return left if left_date <= right_date else right


def _later_date(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    left_date = _parse_date(left)
    right_date = _parse_date(right)
    if left_date is None:
        return right
    if right_date is None:
        return left
    return left if left_date >= right_date else right


def _parse_date(value: str | None) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _dedupe_texts(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = redact_symbol_chat_secrets(str(value or "").strip())
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace("/", ".")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
