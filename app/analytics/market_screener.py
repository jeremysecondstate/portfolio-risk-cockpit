from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from app.analytics.earnings_pipeline import EarningsRadarStore, RecentEarningsRecord
from app.analytics.ipo_pipeline import sector_for_sic
from app.analytics.openai_ipo_report import _redact_api_key, _response_output_text
from app.analytics.symbol_chat import (
    OpenAiSymbolChatClient,
    SYMBOL_CHAT_APPROX_CHARS_PER_TOKEN,
    SYMBOL_CHAT_REQUEST_CHAR_LIMIT,
    redact_symbol_chat_secrets,
)
from app.data.earnings_calendar import UpcomingEarningsRecord, configured_upcoming_earnings_provider
from app.data.market_data_provider import (
    DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    MarketQuoteFundamentalsRecord,
    MarketQuoteFundamentalsSnapshot,
    configured_market_data_provider,
)
from app.data.market_universe import MarketUniverseEntry, MarketUniverseSnapshot, fetch_market_universe_snapshot
from app.data.sec_edgar import SEC_SUBMISSIONS_URL, SEC_TICKER_URL, TICKER_CACHE_TTL, SecEdgarClient


LOGGER = logging.getLogger(__name__)

MARKET_SCREENER_REQUEST_CHAR_LIMIT = SYMBOL_CHAT_REQUEST_CHAR_LIMIT
MARKET_SCREENER_SOURCE_MODE = "market_screener_selected_row"
ASK_SCREENER_SOURCE_MODE = "market_screener_ask_screener_plan"
ASK_SCREENER_REQUEST_CHAR_LIMIT = 12_000
ASK_SCREENER_DEFAULT_LIMIT = 250
ASK_SCREENER_MAX_LIMIT = 500
ASK_SCREENER_MAX_FILTERS = 12
MISSING_TEXT = "--"
ASK_SCREENER_AUTO_ENRICH_ENV = "ASK_SCREENER_AUTO_ENRICH"
ASK_SCREENER_PROFILE_ENRICH_LIMIT_ENV = "ASK_SCREENER_PROFILE_ENRICH_LIMIT"
ASK_SCREENER_QUOTE_ENRICH_LIMIT_ENV = "ASK_SCREENER_QUOTE_ENRICH_LIMIT"
ASK_SCREENER_FUNDAMENTAL_ENRICH_LIMIT_ENV = "ASK_SCREENER_FUNDAMENTAL_ENRICH_LIMIT"
ASK_SCREENER_DATABENTO_TAPE_ENRICH_LIMIT_ENV = "ASK_SCREENER_DATABENTO_TAPE_ENRICH_LIMIT"
ASK_SCREENER_REQUIRE_CONFIRM_ABOVE_ENV = "ASK_SCREENER_REQUIRE_CONFIRM_ABOVE"
MARKET_SCREENER_PROVIDER_BACKFILL_ENABLED_ENV = "MARKET_SCREENER_PROVIDER_BACKFILL_ENABLED"
MARKET_SCREENER_PROFILE_BACKFILL_LIMIT_ENV = "MARKET_SCREENER_PROFILE_BACKFILL_LIMIT"
MARKET_SCREENER_QUOTE_BACKFILL_LIMIT_ENV = "MARKET_SCREENER_QUOTE_BACKFILL_LIMIT"
MARKET_SCREENER_FUNDAMENTAL_BACKFILL_LIMIT_ENV = "MARKET_SCREENER_FUNDAMENTAL_BACKFILL_LIMIT"
MARKET_SCREENER_DATABENTO_BACKFILL_LIMIT_ENV = "MARKET_SCREENER_DATABENTO_BACKFILL_LIMIT"
MARKET_SCREENER_BACKFILL_BATCH_SIZE_ENV = "MARKET_SCREENER_BACKFILL_BATCH_SIZE"
MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS_ENV = "MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS"
MARKET_SCREENER_CONFIRM_BACKFILL_ABOVE_ENV = "MARKET_SCREENER_CONFIRM_BACKFILL_ABOVE"
ASK_SCREENER_SMALL_CAP_MAX_MARKET_CAP_ENV = "ASK_SCREENER_SMALL_CAP_MAX_MARKET_CAP"
ASK_SCREENER_PENNY_STOCK_MAX_PRICE_ENV = "ASK_SCREENER_PENNY_STOCK_MAX_PRICE"
ASK_SCREENER_ENV_NAME_DATABENTO_ENABLED = "MARKET_SCREENER_ENABLE_DATABENTO_EQUITIES"
DEFAULT_ASK_SCREENER_AUTO_ENRICH = True
DEFAULT_ASK_SCREENER_PROFILE_ENRICH_LIMIT = 2000
DEFAULT_ASK_SCREENER_QUOTE_ENRICH_LIMIT = 2000
DEFAULT_ASK_SCREENER_FUNDAMENTAL_ENRICH_LIMIT = 1000
DEFAULT_ASK_SCREENER_DATABENTO_TAPE_ENRICH_LIMIT = 2000
DEFAULT_ASK_SCREENER_REQUIRE_CONFIRM_ABOVE = 3000
DEFAULT_MARKET_SCREENER_PROVIDER_BACKFILL_ENABLED = True
DEFAULT_MARKET_SCREENER_PROFILE_BACKFILL_LIMIT = 2000
DEFAULT_MARKET_SCREENER_QUOTE_BACKFILL_LIMIT = 2000
DEFAULT_MARKET_SCREENER_FUNDAMENTAL_BACKFILL_LIMIT = 1000
DEFAULT_MARKET_SCREENER_DATABENTO_BACKFILL_LIMIT = 2000
DEFAULT_MARKET_SCREENER_BACKFILL_BATCH_SIZE = 100
DEFAULT_MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS = 3600
DEFAULT_MARKET_SCREENER_CONFIRM_BACKFILL_ABOVE = 3000
DEFAULT_ASK_SCREENER_SMALL_CAP_MAX_MARKET_CAP = 2_000_000_000.0
DEFAULT_ASK_SCREENER_PENNY_STOCK_MAX_PRICE = 5.0
ASK_SCREENER_PROVIDER_ACTION_EXECUTE_LOCAL_ONLY = "execute_local_only"
ASK_SCREENER_PROVIDER_ACTION_ENRICH_THEN_EXECUTE = "enrich_then_execute"
ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT = "ask_for_confirmation_before_large_enrichment"
ASK_SCREENER_PROVIDER_ACTION_MISSING_CONFIG = "cannot_execute_missing_provider_config"
MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION = "profile_classification"
MARKET_SCREENER_ENRICH_QUOTE_TAPE = "quote_tape"
MARKET_SCREENER_ENRICH_FUNDAMENTALS = "fundamentals"
MARKET_SCREENER_ENRICH_ALL_MARKET_DATA = "all_market_data"
MARKET_SCREENER_ENRICH_VISIBLE_PAGE = "visible_page"
MARKET_SCREENER_ENRICH_SELECTED_ROW = "selected_row"
MARKET_SCREENER_ENRICH_ASK_SCREENER_CANDIDATE_SET = "ask_screener_candidate_set"
MARKET_SCREENER_ENRICHMENT_MODES = frozenset(
    {
        MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION,
        MARKET_SCREENER_ENRICH_QUOTE_TAPE,
        MARKET_SCREENER_ENRICH_FUNDAMENTALS,
        MARKET_SCREENER_ENRICH_ALL_MARKET_DATA,
        MARKET_SCREENER_ENRICH_VISIBLE_PAGE,
        MARKET_SCREENER_ENRICH_SELECTED_ROW,
        MARKET_SCREENER_ENRICH_ASK_SCREENER_CANDIDATE_SET,
    }
)

MARKET_SCREENER_AI_SYSTEM_PROMPT = """You are a market intelligence analyst inside Portfolio Risk Cockpit.

Analyze only the selected Market Intelligence Screener row and explicit selected-row context in the request.
Do not invent missing prices, volume, market cap, valuation, EPS, revenue growth, earnings dates, filings, catalysts, or source links.
When a field is absent, say "Not available in the selected Market Intelligence Screener row."
Treat signals and risk flags as deterministic app/provider observations, not audited conclusions.
Treat source snippets as selected-row excerpts only when they are provided.
Treat cross-asset Databento CME/futures/options context, when present, as macro context only; never use it to fill selected-equity market-data or fundamental fields.

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

ASK_SCREENER_PLANNER_SYSTEM_PROMPT = """You plan deterministic filters for Portfolio Risk Cockpit's Market Intelligence Screener.

Return one JSON object only. Do not return markdown, prose, recommendations, trade actions, or analysis.
Use only the allowed filter schema and compact snapshot metadata in the request.
Do not ask for, include, infer, expose, or log credentials, API keys, account identifiers, source excerpts, source links, or secrets.
Do not invent missing prices, volume, market cap, valuation, EPS, revenue growth, earnings dates, filings, or market data.
The app will validate your JSON and execute it locally against the screener rows.
"""

ASK_SCREENER_TEXT_FIELDS = {
    "symbol",
    "company_name",
    "cik",
    "exchange",
    "sector",
    "industry",
    "classification",
    "recent_filing_type",
    "data_label",
    "signals",
    "risk_flags",
    "sources",
}
ASK_SCREENER_NUMERIC_FIELDS = {
    "price",
    "market_cap",
    "volume",
    "avg_volume",
    "change_percent",
    "pe_ratio",
    "eps",
    "revenue_growth",
    "shares_float",
    "shares_outstanding",
    "portfolio_quantity",
    "portfolio_average_cost",
    "portfolio_market_value",
    "portfolio_unrealized_pnl",
    "portfolio_weight",
    "data_completeness_score",
}
ASK_SCREENER_DATE_FIELDS = {
    "next_earnings_date",
    "recent_filing_date",
}
ASK_SCREENER_BOOLEAN_FIELDS = {
    "is_my_holding",
    "has_market_data",
    "has_quote",
    "has_fundamentals",
    "has_recent_filing",
    "has_upcoming_earnings",
    "high_volume_mover",
    "momentum_proxy",
    "recent_catalyst",
    "missing_price_data",
    "has_positive_revenue_growth",
    "has_negative_eps",
    "has_missing_data",
}
ASK_SCREENER_PROVIDER_AWARE_FIELDS = frozenset(
    {
        "sector",
        "industry",
        "exchange",
        "price",
        "change_percent",
        "volume",
        "avg_volume",
        "market_cap",
        "pe_ratio",
        "eps",
        "revenue_growth",
        "shares_float",
        "shares_outstanding",
        "recent_filing_date",
        "next_earnings_date",
    }
)
ASK_SCREENER_CLASSIFICATION_FIELDS = frozenset({"sector", "industry", "exchange"})
ASK_SCREENER_TAPE_FIELDS = frozenset({"price", "change_percent", "volume", "avg_volume"})
ASK_SCREENER_FUNDAMENTAL_FIELDS = frozenset(
    {"market_cap", "pe_ratio", "eps", "revenue_growth", "shares_float", "shares_outstanding"}
)
ASK_SCREENER_EVENT_FIELDS = frozenset({"recent_filing_date", "next_earnings_date"})
ASK_SCREENER_ALLOWED_FIELDS = frozenset(
    ASK_SCREENER_TEXT_FIELDS
    | ASK_SCREENER_NUMERIC_FIELDS
    | ASK_SCREENER_DATE_FIELDS
    | ASK_SCREENER_BOOLEAN_FIELDS
)
ASK_SCREENER_TEXT_OPERATORS = {"eq", "neq", "contains", "not_contains", "contains_any", "in", "not_in", "exists", "missing"}
ASK_SCREENER_NUMERIC_OPERATORS = {"eq", "neq", "gt", "gte", "lt", "lte", "exists", "missing"}
ASK_SCREENER_DATE_OPERATORS = ASK_SCREENER_NUMERIC_OPERATORS | {"within_next_days"}
ASK_SCREENER_BOOLEAN_OPERATORS = {"eq", "neq", "is_true", "is_false", "exists", "missing"}
ASK_SCREENER_OPERATOR_ALIASES = {
    "=": "eq",
    "==": "eq",
    "is": "eq",
    "equals": "eq",
    "!=": "neq",
    "<>": "neq",
    "not": "neq",
    ">": "gt",
    "above": "gt",
    "greater_than": "gt",
    ">=": "gte",
    "at_least": "gte",
    "<": "lt",
    "below": "lt",
    "less_than": "lt",
    "<=": "lte",
    "at_most": "lte",
    "present": "exists",
    "has": "exists",
    "not_missing": "exists",
    "absent": "missing",
    "missing": "missing",
    "is_true": "is_true",
    "true": "is_true",
    "is_false": "is_false",
    "false": "is_false",
}
ASK_SCREENER_FIELD_ALIASES = {
    "ticker": "symbol",
    "name": "company_name",
    "company": "company_name",
    "profile": "classification",
    "classification_text": "classification",
    "sector_industry": "classification",
    "industry_classification": "classification",
    "rev_growth": "revenue_growth",
    "revenue_growth_percent": "revenue_growth",
    "growth": "revenue_growth",
    "marketcap": "market_cap",
    "market_capitalization": "market_cap",
    "avg_vol": "avg_volume",
    "average_volume": "avg_volume",
    "change": "change_percent",
    "percent_change": "change_percent",
    "pe": "pe_ratio",
    "p_e": "pe_ratio",
    "float": "shares_float",
    "outstanding_shares": "shares_outstanding",
    "next_earnings": "next_earnings_date",
    "earnings_date": "next_earnings_date",
    "recent_filing": "recent_filing_date",
    "filing_date": "recent_filing_date",
    "filing_type": "recent_filing_type",
    "completeness": "data_completeness_score",
    "data_completeness": "data_completeness_score",
    "holding": "is_my_holding",
    "my_holding": "is_my_holding",
    "holdings": "is_my_holding",
    "quote_enriched": "has_market_data",
    "has_price_data": "has_market_data",
    "fundamentals": "has_fundamentals",
    "recent_filings": "has_recent_filing",
    "upcoming_earnings": "has_upcoming_earnings",
    "earnings_soon": "has_upcoming_earnings",
    "high_volume": "high_volume_mover",
    "mover": "high_volume_mover",
    "momentum": "momentum_proxy",
    "momentum_stock": "momentum_proxy",
    "recent_catalysts": "recent_catalyst",
    "catalysts": "recent_catalyst",
    "catalyst": "recent_catalyst",
    "missing_price": "missing_price_data",
    "missing_data": "has_missing_data",
    "blank_data": "has_missing_data",
    "positive_revenue_growth": "has_positive_revenue_growth",
    "negative_eps": "has_negative_eps",
}

EVENT_TYPE_OPTIONS = (
    "All",
    "My Holdings",
    "Quote-enriched",
    "Fundamentals available",
    "Upcoming earnings",
    "Recent SEC filing",
    "Guidance mentioned",
    "Risk flags",
    "High volume / mover",
    "Schwab holding/watchlist",
)
EARNINGS_WINDOW_OPTIONS = ("All", "Next 7 days", "Next 30 days", "Next 90 days")
DATA_COMPLETENESS_OPTIONS = (
    "All",
    "High completeness (>=75%)",
    "Partial completeness (40-74%)",
    "Low completeness (<40%)",
    "Has field provenance",
)

MAJOR_US_LARGE_CAP_SYMBOLS = (
    "MSFT",
    "AAPL",
    "NVDA",
    "GOOG",
    "GOOGL",
    "AMZN",
    "META",
    "BRK.B",
    "LLY",
    "AVGO",
    "TSLA",
    "JPM",
    "V",
)

SEC_CHART_FIELD_SOURCE_WARNING = (
    "SEC records may be used only by the legacy universe override or as filing context; visible Market Screener "
    "identity, profile, market, and fundamental fields must come from FMP, Databento, or internal app data."
)

_SEC_VISIBLE_CHART_FIELDS = frozenset(
    {
        "company_name",
        "exchange",
        "sector",
        "industry",
        "eps",
        "revenue_growth",
    }
)
_SYMBOL_ALIASES = {
    "BRK-B": "BRK.B",
    "BRK/B": "BRK.B",
    "BRK B": "BRK.B",
    "BF-B": "BF.B",
    "BF/B": "BF.B",
    "BF B": "BF.B",
}
US_PRIMARY_EXCHANGES = {
    "NASDAQ",
    "NASD",
    "XNAS",
    "NYSE",
    "XNYS",
    "NYSE AMERICAN",
    "NYSEAMERICAN",
    "AMEX",
    "ARCX",
    "NYSE ARCA",
    "BATS",
    "CBOE",
    "IEX",
}
OTC_EXCHANGE_MARKERS = ("OTC", "PINK", "PINX", "GREY", "GREY MARKET", "EXPERT")
KNOWN_FOREIGN_ADR_SYMBOLS = {
    "SONY",
    "HMC",
    "BCH",
    "TM",
    "NVO",
    "ASML",
    "BABA",
    "TSM",
    "SHOP",
    "SAP",
    "SNY",
    "BP",
    "SHEL",
    "AZN",
    "RIO",
}

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True)
class MarketScreenerSourceStatus:
    source: str
    status: str
    fetched_at: str
    message: str


@dataclass(frozen=True)
class MarketScreenerCoverageDiagnostics:
    total_rows: int = 0
    rows_with_cik: int = 0
    rows_missing_cik: int = 0
    rows_with_symbol: int = 0
    rows_missing_symbol: int = 0
    rows_resolved_by_sec_cik_mapping: int = 0
    rows_resolved_by_sec_submissions_metadata: int = 0
    rows_enriched_by_local_file: int = 0
    rows_enriched_by_schwab_quote: int = 0
    rows_enriched_by_databento_equities: int = 0
    rows_enriched_by_fmp_quote: int = 0
    rows_enriched_by_fmp_profile: int = 0
    rows_enriched_by_fmp_profile_by_cik: int = 0
    rows_enriched_by_fmp_market_cap: int = 0
    rows_enriched_by_fmp_historical_eod: int = 0
    rows_enriched_by_fmp_key_metrics: int = 0
    rows_enriched_by_fmp_ratios: int = 0
    rows_enriched_by_fmp_income_growth: int = 0
    rows_enriched_by_fmp_financial_growth: int = 0
    rows_enriched_by_fmp_income_statement: int = 0
    rows_enriched_by_fmp_shares_float: int = 0
    rows_enriched_by_fmp_sec_filings: int = 0
    rows_enriched_by_fallback_provider: int = 0
    fmp_cache_hits: int = 0
    databento_equities_symbols_attempted: int = 0
    databento_equities_chunks_attempted: int = 0
    databento_equities_cache_hits: int = 0
    databento_equities_provider_warnings: int = 0
    databento_cme_context_rows: int = 0
    databento_cme_cache_hits: int = 0
    databento_dataset_mismatch_warnings: int = 0
    rows_blocked_by_provider_plan_rate_auth_limit: int = 0
    rows_skipped_by_configured_symbol_cap: int = 0
    rows_provider_returned_no_usable_data: int = 0
    provider_unavailable: int = 0
    unresolved_rows: int = 0
    rows_still_missing_exchange_sector_industry: int = 0
    rows_with_profile_classification: int = 0
    rows_with_price: int = 0
    rows_missing_price: int = 0
    rows_with_volume: int = 0
    rows_missing_volume: int = 0
    rows_with_avg_volume: int = 0
    rows_missing_avg_volume: int = 0
    rows_with_fundamentals: int = 0
    rows_with_market_cap: int = 0
    rows_with_trusted_usd_market_cap: int = 0
    rows_with_trusted_primary_market_cap: int = 0
    rows_with_trusted_non_primary_market_cap: int = 0
    rows_with_untrusted_market_cap: int = 0
    rows_with_non_usd_market_cap: int = 0
    rows_with_ambiguous_market_cap: int = 0
    rows_missing_market_cap: int = 0
    major_us_large_caps_present: int = 0
    major_us_large_caps_absent: int = 0
    rows_with_revenue_growth: int = 0
    rows_missing_revenue_growth: int = 0
    rows_with_float_or_shares: int = 0
    rows_missing_float_or_shares: int = 0
    provider_calls_attempted: int = 0
    provider_rows_requested: int = 0
    provider_rows_returned: int = 0
    provider_rows_parsed: int = 0
    provider_rows_updated: int = 0
    provider_cache_hits: int = 0
    provider_warnings: int = 0
    rows_still_missing_price_volume: int = 0
    rows_still_missing_fundamentals: int = 0
    market_cap_coverage_incomplete: int = 0

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True)
class MarketScreenerFieldProvenance:
    field: str
    source: str
    source_detail: str = ""
    source_link: str | None = None
    fetched_at: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MarketScreenerFieldProvenance":
        return cls(
            field=str(payload.get("field") or "").strip(),
            source=str(payload.get("source") or "").strip() or "Unknown source",
            source_detail=str(payload.get("source_detail") or "").strip(),
            source_link=_optional_text(payload.get("source_link") or payload.get("source_url") or payload.get("url")),
            fetched_at=str(payload.get("fetched_at") or "").strip(),
        )


@dataclass(frozen=True)
class MarketScreenerMarketCapRank:
    display_market_cap: float | None
    ranking_market_cap: float | None
    currency: str
    category: str
    trusted: bool
    used_for_ranking: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    shares_float: float | None = None
    shares_outstanding: float | None = None
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
    portfolio_quantity: float | None = None
    portfolio_average_cost: float | None = None
    portfolio_market_value: float | None = None
    portfolio_unrealized_pnl: float | None = None
    portfolio_weight: float | None = None
    field_provenance: tuple[MarketScreenerFieldProvenance, ...] = ()
    market_cap_currency: str | None = None
    market_cap_rank_value: float | None = None
    market_cap_rank_currency: str | None = None
    market_cap_rank_trusted: bool | None = None
    market_cap_rank_reason: str | None = None
    instrument_type: str | None = None
    country: str | None = None
    is_adr: bool | None = None
    is_etf: bool | None = None
    is_fund: bool | None = None
    is_otc: bool | None = None

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
        values["field_provenance"] = _field_provenance_from_payload(values.get("field_provenance"))
        for key in ("market_cap_currency", "market_cap_rank_currency"):
            values[key] = _normalize_currency_code(values.get(key)) or None
        for key in ("is_adr", "is_etf", "is_fund", "is_otc", "market_cap_rank_trusted"):
            values[key] = _bool_or_none(values.get(key))
        return cls(**values)


@dataclass(frozen=True)
class MarketScreenerSnapshot:
    records: tuple[MarketScreenerRecord, ...]
    fetched_at: str
    sources: tuple[str, ...]
    statuses: tuple[MarketScreenerSourceStatus, ...]
    errors: tuple[str, ...] = ()
    diagnostics: MarketScreenerCoverageDiagnostics = field(default_factory=MarketScreenerCoverageDiagnostics)


@dataclass(frozen=True)
class MarketScreenerAiResponse:
    answer: str
    response_id: str
    model: str
    source_mode: str
    source_debug: tuple[str, ...]


@dataclass(frozen=True)
class AskScreenerFilter:
    field: str
    operator: str
    value: Any = None


@dataclass(frozen=True)
class AskScreenerSort:
    field: str
    descending: bool = False


@dataclass(frozen=True)
class AskScreenerPlan:
    filters: tuple[AskScreenerFilter, ...] = ()
    sort: AskScreenerSort | None = None
    limit: int = ASK_SCREENER_DEFAULT_LIMIT
    intent: str = ""
    clear_filters: bool = False
    required_fields: tuple[str, ...] = ()
    provider_enrichment_needed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "filters": [asdict(row) for row in self.filters],
            "sort": asdict(self.sort) if self.sort is not None else None,
            "limit": self.limit,
            "intent": self.intent,
            "clear_filters": self.clear_filters,
            "required_fields": list(self.required_fields),
            "provider_enrichment_needed": self.provider_enrichment_needed,
        }


@dataclass(frozen=True)
class AskScreenerFieldCoverage:
    field: str
    available_count: int
    missing_count: int
    total_rows: int
    coverage_ratio: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AskScreenerProviderConfig:
    auto_enrich: bool = DEFAULT_ASK_SCREENER_AUTO_ENRICH
    profile_enrich_limit: int = DEFAULT_ASK_SCREENER_PROFILE_ENRICH_LIMIT
    quote_enrich_limit: int = DEFAULT_ASK_SCREENER_QUOTE_ENRICH_LIMIT
    fundamental_enrich_limit: int = DEFAULT_ASK_SCREENER_FUNDAMENTAL_ENRICH_LIMIT
    databento_tape_enrich_limit: int = DEFAULT_ASK_SCREENER_DATABENTO_TAPE_ENRICH_LIMIT
    require_confirm_above: int = DEFAULT_ASK_SCREENER_REQUIRE_CONFIRM_ABOVE
    fmp_configured: bool = False
    databento_equities_configured: bool = False
    local_market_data_configured: bool = False
    schwab_quote_configured: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketScreenerBackfillConfig:
    enabled: bool = DEFAULT_MARKET_SCREENER_PROVIDER_BACKFILL_ENABLED
    profile_limit: int = DEFAULT_MARKET_SCREENER_PROFILE_BACKFILL_LIMIT
    quote_limit: int = DEFAULT_MARKET_SCREENER_QUOTE_BACKFILL_LIMIT
    fundamental_limit: int = DEFAULT_MARKET_SCREENER_FUNDAMENTAL_BACKFILL_LIMIT
    databento_limit: int = DEFAULT_MARKET_SCREENER_DATABENTO_BACKFILL_LIMIT
    batch_size: int = DEFAULT_MARKET_SCREENER_BACKFILL_BATCH_SIZE
    cache_ttl_seconds: int = DEFAULT_MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS
    confirm_above: int = DEFAULT_MARKET_SCREENER_CONFIRM_BACKFILL_ABOVE

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketScreenerEnrichmentReport:
    mode: str
    total_rows: int
    rows_with_profile_classification: int
    rows_with_price: int
    rows_with_volume: int
    rows_with_avg_volume: int
    rows_with_fundamentals: int
    rows_with_trusted_usd_market_cap: int
    rows_with_trusted_primary_market_cap: int
    rows_with_trusted_non_primary_market_cap: int
    rows_with_untrusted_market_cap: int
    rows_with_non_usd_market_cap: int
    rows_with_ambiguous_market_cap: int
    rows_missing_market_cap: int
    major_us_large_caps_present: int
    major_us_large_caps_absent: int
    rows_with_revenue_growth: int
    rows_with_float_or_shares: int
    rows_missing_profile_classification: int
    rows_missing_price: int
    rows_missing_volume: int
    rows_missing_avg_volume: int
    rows_missing_fundamentals: int
    rows_missing_revenue_growth: int
    rows_missing_float_or_shares: int
    provider_calls_attempted: int = 0
    provider_rows_requested: int = 0
    provider_rows_returned: int = 0
    provider_rows_parsed: int = 0
    rows_updated: int = 0
    cache_hits: int = 0
    warnings: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MarketScreenerEnrichmentResult:
    mode: str
    records: tuple[MarketScreenerRecord, ...]
    requested_symbols: tuple[str, ...]
    fetched_at: str
    report: MarketScreenerEnrichmentReport
    statuses: tuple[MarketScreenerSourceStatus, ...] = ()
    errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "records": [record.to_dict() for record in self.records],
            "requested_symbols": list(self.requested_symbols),
            "fetched_at": self.fetched_at,
            "report": self.report.to_dict(),
            "statuses": [asdict(status) for status in self.statuses],
            "errors": list(self.errors),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class AskScreenerEnrichmentDecision:
    action: str
    required_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    candidate_symbol_count: int
    symbols_to_enrich: tuple[str, ...] = ()
    provider_groups: tuple[str, ...] = ()
    reason: str = ""
    missing_provider_config: tuple[str, ...] = ()
    max_symbols: int = 0

    @property
    def needs_provider_enrichment(self) -> bool:
        return self.action in {
            ASK_SCREENER_PROVIDER_ACTION_ENRICH_THEN_EXECUTE,
            ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AskScreenerExecutionResult:
    plan: AskScreenerPlan
    records: tuple[MarketScreenerRecord, ...]
    total_input_rows: int
    total_matched_rows: int
    limited: bool
    summary: str
    all_records: tuple[MarketScreenerRecord, ...] = ()
    notes: tuple[str, ...] = ()
    source_mode: str = ASK_SCREENER_SOURCE_MODE
    enrichment_status: str = "local-only"
    providers_used: tuple[str, ...] = ()
    symbols_requested: int = 0
    rows_updated: int = 0
    remaining_missing_fields: Mapping[str, int] = field(default_factory=dict)
    more_enrichment_may_help: bool = False
    enrichment_decision: AskScreenerEnrichmentDecision | None = None


class OpenAiMarketScreenerError(RuntimeError):
    """Raised for row-grounded Market Screener OpenAI failures with secrets redacted."""


class AskScreenerPlanValidationError(ValueError):
    """Raised when an Ask Screener filter plan is unsupported or unsafe to execute."""


class AskScreenerPlannerError(RuntimeError):
    """Raised for Ask Screener planner failures with secrets redacted."""


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


class OpenAiAskScreenerPlannerClient:
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

    def plan(
        self,
        question: str,
        records: Iterable[MarketScreenerRecord],
        *,
        progress_callback: ProgressCallback | None = None,
    ) -> AskScreenerPlan:
        started_at = time.perf_counter()
        clean_question = _clean_prompt(question)
        if not clean_question:
            raise AskScreenerPlannerError("Enter an Ask Screener question before sending.")

        _notify_progress(progress_callback, "Preparing compact screener schema and snapshot metadata...")
        request_payload = ask_screener_planner_request_payload(
            clean_question,
            records,
            timeout_seconds=self.timeout_seconds,
        )
        payload_text = _serialize_request_payload(request_payload)
        diagnostics = {
            "request_payload_chars": len(payload_text),
            "request_payload_approx_tokens": _approx_token_count(len(payload_text)),
            "request_payload_char_limit": ASK_SCREENER_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": self.timeout_seconds,
        }
        input_messages = [
            {"role": "system", "content": ASK_SCREENER_PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": payload_text},
        ]
        try:
            _notify_progress(progress_callback, f"Calling OpenAI for filter plan (timeout {self.timeout_seconds:g}s)...")
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
                    f"OpenAI Ask Screener planner timed out after {self.timeout_seconds:g} seconds. "
                    "Try a more direct filter request or use one of the local Ask Screener phrases."
                )
            else:
                message = f"OpenAI Ask Screener planner failed: {exc}"
            message = _redact_api_key(message, self._symbol_chat_client._current_api_key())
            message = redact_symbol_chat_secrets(message)
            LOGGER.warning(
                "Ask Screener planner request failed elapsed=%.3fs error=%s",
                time.perf_counter() - started_at,
                message,
            )
            raise AskScreenerPlannerError(message) from None

        diagnostics["openai_seconds"] = round(time.perf_counter() - openai_started, 3)
        diagnostics["total_seconds"] = round(time.perf_counter() - started_at, 3)
        _notify_progress(progress_callback, "OpenAI filter plan received.")
        output_text = redact_symbol_chat_secrets(str(_response_output_text(response) or "").strip())
        try:
            payload = _parse_json_object(output_text)
            plan = validate_ask_screener_plan(payload)
        except Exception as exc:
            message = redact_symbol_chat_secrets(str(exc))
            LOGGER.warning(
                "Ask Screener planner returned malformed or unsupported JSON elapsed=%.3fs diagnostics=%s error=%s",
                time.perf_counter() - started_at,
                diagnostics,
                message,
            )
            raise AskScreenerPlannerError(f"Ask Screener could not use the model plan: {message}") from None
        return plan


def fetch_market_screener_snapshot(
    *,
    sec_client: SecEdgarClient | None = None,
    universe_snapshot: MarketUniverseSnapshot | None = None,
    recent_records: Iterable[RecentEarningsRecord] | None = None,
    upcoming_records: Iterable[UpcomingEarningsRecord] | None = None,
    supplemental_records: Iterable[MarketScreenerRecord] | None = None,
    market_data_records: Iterable[MarketQuoteFundamentalsRecord] | None = None,
    upcoming_provider: Any | None = None,
    market_data_provider: Any | None = None,
    universe_limit: int = 750,
    market_data_symbol_limit: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
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
    supplemental = tuple(supplemental_records or ())
    identity_records: tuple[MarketUniverseEntry, ...] = ()
    universe_records = tuple(universe_snapshot.records)

    base_records = build_market_screener_records(
        universe_records,
        recent,
        upcoming,
        supplemental_records=supplemental,
        fetched_at=fetched_at,
    )
    sec_submission_records: tuple[MarketScreenerRecord, ...] = ()
    supplemental_with_sec_metadata = supplemental
    all_market_data_symbols = _market_data_candidate_symbols(base_records, max(10_000, market_data_symbol_limit))
    market_data_symbols = all_market_data_symbols[: max(0, market_data_symbol_limit)]
    provider_diagnostics: dict[str, int] = {}
    active_market_data_provider = None if market_data_records is not None else (market_data_provider or configured_market_data_provider())
    market_data = _load_market_data_records(
        active_market_data_provider,
        market_data_records,
        statuses,
        errors,
        fetched_at,
        market_data_symbols,
        force_refresh,
        market_data_symbol_limit,
        provider_diagnostics,
    )
    fmp_filing_records = _load_provider_filing_metadata_records(
        active_market_data_provider,
        base_records,
        statuses,
        errors,
        fetched_at,
        force_refresh,
        market_data_symbol_limit,
        provider_diagnostics,
    )
    cik_market_data = _load_market_data_records_by_cik(
        active_market_data_provider,
        statuses,
        errors,
        fetched_at,
        _market_data_candidate_ciks(base_records, market_data_symbol_limit),
        force_refresh,
        market_data_symbol_limit,
        provider_diagnostics,
    )
    _load_databento_cme_context_statuses(
        statuses,
        errors,
        fetched_at,
        force_refresh,
        provider_diagnostics,
    )
    all_market_data = (*market_data, *cik_market_data)
    provider_diagnostics["rows_skipped_by_configured_symbol_cap"] = provider_diagnostics.get("rows_skipped_by_configured_symbol_cap", 0) + max(
        0,
        len(all_market_data_symbols) - max(0, market_data_symbol_limit),
    )
    provider_diagnostics["rows_resolved_by_sec_cik_mapping"] = provider_diagnostics.get("rows_resolved_by_sec_cik_mapping", 0) + len(identity_records)
    provider_diagnostics["rows_resolved_by_sec_submissions_metadata"] = provider_diagnostics.get("rows_resolved_by_sec_submissions_metadata", 0) + len(sec_submission_records)
    records = build_market_screener_records(
        universe_records,
        recent,
        upcoming,
        supplemental_records=(*supplemental_with_sec_metadata, *fmp_filing_records),
        market_data_records=all_market_data,
        fetched_at=fetched_at,
    )
    diagnostics = _build_market_screener_diagnostics(records, statuses, provider_diagnostics)
    statuses.append(_market_data_coverage_status(records, all_market_data, market_data_symbol_limit, fetched_at, diagnostics))
    statuses.append(_source_ladder_diagnostics_status(diagnostics, fetched_at))
    sources = tuple(sorted({source for record in records for source in record.sources if source}))
    return MarketScreenerSnapshot(
        records=tuple(records),
        fetched_at=fetched_at,
        sources=sources,
        statuses=tuple(statuses),
        errors=tuple(errors),
        diagnostics=diagnostics,
    )


def build_market_screener_records(
    universe: Iterable[MarketUniverseEntry],
    recent_records: Iterable[RecentEarningsRecord] = (),
    upcoming_records: Iterable[UpcomingEarningsRecord] = (),
    *,
    supplemental_records: Iterable[MarketScreenerRecord] = (),
    market_data_records: Iterable[MarketQuoteFundamentalsRecord] = (),
    fetched_at: str | None = None,
) -> list[MarketScreenerRecord]:
    fetched_at = fetched_at or _now()
    merged: dict[str, MarketScreenerRecord] = {}

    for entry in universe:
        record = _strip_sec_visible_chart_fields(_record_from_universe(entry, fetched_at=fetched_at))
        _merge_into(merged, record)

    for record in recent_records:
        _merge_into(merged, _strip_sec_visible_chart_fields(_record_from_recent_earnings(record, fetched_at=fetched_at)))

    for record in upcoming_records:
        _merge_into(merged, _record_from_upcoming_earnings(record, fetched_at=fetched_at))

    for record in supplemental_records:
        _merge_into(merged, _strip_sec_visible_chart_fields(_normalize_record(record, fetched_at=fetched_at)))

    for record in market_data_records:
        _merge_into(merged, _record_from_market_data(record, fetched_at=fetched_at))

    return sorted(merged.values(), key=lambda row: ((row.symbol or "ZZZZ").upper(), (row.company_name or "").lower()))


def merge_market_data_records_into_screener_records(
    records: Iterable[MarketScreenerRecord],
    market_data_records: Iterable[MarketQuoteFundamentalsRecord],
    *,
    fetched_at: str | None = None,
) -> list[MarketScreenerRecord]:
    fetched_at = fetched_at or _now()
    rows = [_normalize_record(record, fetched_at=fetched_at) for record in records]
    indexes_by_key: dict[str, int] = {}
    for index, record in enumerate(rows):
        for key in _record_keys(record):
            indexes_by_key.setdefault(key, index)
    for market_data_record in market_data_records:
        incoming = _record_from_market_data(market_data_record, fetched_at=fetched_at)
        key = next((candidate for candidate in _record_keys(incoming) if candidate in indexes_by_key), "")
        if not key:
            continue
        index = indexes_by_key[key]
        rows[index] = merge_market_screener_record(rows[index], incoming)
        for updated_key in _record_keys(rows[index]):
            indexes_by_key[updated_key] = index
    return rows


def enrich_market_screener_records(
    records: Iterable[MarketScreenerRecord],
    mode: str,
    *,
    provider: Any | None = None,
    config: MarketScreenerBackfillConfig | None = None,
    scope_records: Iterable[MarketScreenerRecord] | None = None,
    selected_record: MarketScreenerRecord | None = None,
    candidate_records: Iterable[MarketScreenerRecord] | None = None,
    symbols: Iterable[str] | None = None,
    required_fields: Iterable[str] = (),
    force_refresh: bool = False,
    include_fallback_provider: bool = True,
) -> MarketScreenerEnrichmentResult:
    clean_mode = str(mode or "").strip().lower()
    if clean_mode not in MARKET_SCREENER_ENRICHMENT_MODES:
        raise ValueError(f"Unsupported Market Screener enrichment mode: {mode}")
    config = config or market_screener_backfill_config_from_env()
    fetched_at = _now()
    base_rows = [_normalize_record(record, fetched_at=fetched_at) for record in records]
    if not config.enabled:
        report = market_screener_enrichment_report(base_rows, mode=clean_mode)
        status = MarketScreenerSourceStatus(
            "Market Screener provider backfill",
            "disabled",
            fetched_at,
            f"Provider backfill disabled by {MARKET_SCREENER_PROVIDER_BACKFILL_ENABLED_ENV}=false.",
        )
        return MarketScreenerEnrichmentResult(clean_mode, tuple(base_rows), (), fetched_at, report, statuses=(status,))

    families = _market_screener_enrichment_families(clean_mode, required_fields)
    scoped_rows = _market_screener_scope_rows(
        base_rows,
        clean_mode,
        scope_records=scope_records,
        selected_record=selected_record,
        candidate_records=candidate_records,
    )
    active_provider = provider or configured_market_data_provider(
        include_fallback_provider=include_fallback_provider,
        fmp_symbol_limit=max(config.profile_limit, config.quote_limit, config.fundamental_limit),
        databento_symbol_limit=config.databento_limit,
        cache_ttl_seconds=config.cache_ttl_seconds,
        batch_size=config.batch_size,
    )

    enriched_rows = base_rows
    statuses: list[MarketScreenerSourceStatus] = []
    errors: list[str] = []
    diagnostics: dict[str, int] = {}
    requested_symbols: list[str] = []
    before_rows = tuple(base_rows)
    for family in families:
        family_fields = _market_screener_family_fields(family)
        family_symbols = _market_screener_stage_symbols(
            enriched_rows,
            scoped_rows,
            family_fields,
            symbols=symbols,
            max_symbols=_market_screener_family_limit(family, config),
        )
        if not family_symbols:
            statuses.append(
                MarketScreenerSourceStatus(
                    f"Market Screener {family.replace('_', ' ')} backfill",
                    "empty",
                    fetched_at,
                    f"No symbol-bearing rows need {family.replace('_', ' ')} enrichment in this scope.",
                )
            )
            continue
        requested_symbols.extend(family_symbols)
        try:
            snapshot = _market_screener_provider_family_snapshot(
                active_provider,
                family,
                family_symbols,
                force_refresh=force_refresh,
                max_symbols=_market_screener_family_limit(family, config),
            )
        except Exception as exc:
            clean_error = redact_symbol_chat_secrets(str(exc))
            errors.append(f"{family}: {clean_error}")
            statuses.append(
                MarketScreenerSourceStatus(
                    f"Market Screener {family.replace('_', ' ')} backfill",
                    "error",
                    fetched_at,
                    f"Provider failure was nonblocking: {clean_error}",
                )
            )
            diagnostics["provider_warnings"] = diagnostics.get("provider_warnings", 0) + 1
            continue
        statuses.extend(MarketScreenerSourceStatus(status.source, status.status, status.fetched_at, status.message) for status in snapshot.statuses)
        errors.extend(redact_symbol_chat_secrets(error) for error in snapshot.errors)
        _merge_counter_mapping(diagnostics, snapshot.diagnostics)
        enriched_rows = merge_market_data_records_into_screener_records(enriched_rows, snapshot.records, fetched_at=snapshot.fetched_at)

    changed_rows = _count_screener_rows_changed(before_rows, enriched_rows, _market_screener_fields_for_families(families))
    diagnostics["provider_rows_updated"] = max(diagnostics.get("provider_rows_updated", 0), changed_rows)
    diagnostics["provider_cache_hits"] = max(diagnostics.get("provider_cache_hits", 0), diagnostics.get("fmp_cache_hits", 0) + diagnostics.get("databento_equities_cache_hits", 0))
    diagnostics["provider_warnings"] = max(
        diagnostics.get("provider_warnings", 0),
        diagnostics.get("databento_equities_provider_warnings", 0) + diagnostics.get("databento_dataset_mismatch_warnings", 0),
    )
    report = market_screener_enrichment_report(
        enriched_rows,
        mode=clean_mode,
        provider_diagnostics=diagnostics,
        rows_updated=changed_rows,
    )
    statuses.append(_market_screener_enrichment_report_status(report, fetched_at))
    return MarketScreenerEnrichmentResult(
        clean_mode,
        tuple(enriched_rows),
        tuple(_dedupe_texts(requested_symbols)),
        fetched_at,
        report,
        statuses=tuple(statuses),
        errors=tuple(errors),
    )


def market_screener_enrichment_report(
    records: Iterable[MarketScreenerRecord],
    *,
    mode: str,
    provider_diagnostics: Mapping[str, int] | None = None,
    rows_updated: int = 0,
) -> MarketScreenerEnrichmentReport:
    diagnostics = provider_diagnostics or {}
    rows = list(records)
    total = len(rows)
    rows_with_profile = sum(1 for record in rows if _record_has_profile_classification(record))
    rows_with_price = sum(1 for record in rows if record.price is not None)
    rows_with_volume = sum(1 for record in rows if record.volume is not None)
    rows_with_avg_volume = sum(1 for record in rows if record.avg_volume is not None)
    rows_with_fundamentals = sum(1 for record in rows if market_screener_record_has_fundamentals(record))
    market_cap_ranks = [market_screener_market_cap_rank(record) for record in rows]
    major_present = sum(1 for symbol in MAJOR_US_LARGE_CAP_SYMBOLS if any(_normalize_symbol(record.symbol) == symbol for record in rows))
    rows_with_revenue_growth = sum(1 for record in rows if record.revenue_growth is not None)
    rows_with_float_or_shares = sum(1 for record in rows if record.shares_float is not None or record.shares_outstanding is not None)
    return MarketScreenerEnrichmentReport(
        mode=str(mode),
        total_rows=total,
        rows_with_profile_classification=rows_with_profile,
        rows_with_price=rows_with_price,
        rows_with_volume=rows_with_volume,
        rows_with_avg_volume=rows_with_avg_volume,
        rows_with_fundamentals=rows_with_fundamentals,
        rows_with_trusted_usd_market_cap=sum(1 for rank in market_cap_ranks if rank.trusted and rank.currency == "USD"),
        rows_with_trusted_primary_market_cap=sum(1 for rank in market_cap_ranks if rank.category == "us_primary_common"),
        rows_with_trusted_non_primary_market_cap=sum(1 for rank in market_cap_ranks if rank.category == "trusted_non_primary"),
        rows_with_untrusted_market_cap=sum(1 for rank in market_cap_ranks if rank.ranking_market_cap is not None and not rank.trusted),
        rows_with_non_usd_market_cap=sum(1 for rank in market_cap_ranks if rank.ranking_market_cap is not None and rank.currency not in {"", "USD", "unknown"}),
        rows_with_ambiguous_market_cap=sum(1 for rank in market_cap_ranks if rank.category == "untrusted_ambiguous"),
        rows_missing_market_cap=sum(1 for rank in market_cap_ranks if rank.ranking_market_cap is None),
        major_us_large_caps_present=major_present,
        major_us_large_caps_absent=max(0, len(MAJOR_US_LARGE_CAP_SYMBOLS) - major_present),
        rows_with_revenue_growth=rows_with_revenue_growth,
        rows_with_float_or_shares=rows_with_float_or_shares,
        rows_missing_profile_classification=max(0, total - rows_with_profile),
        rows_missing_price=max(0, total - rows_with_price),
        rows_missing_volume=max(0, total - rows_with_volume),
        rows_missing_avg_volume=max(0, total - rows_with_avg_volume),
        rows_missing_fundamentals=max(0, total - rows_with_fundamentals),
        rows_missing_revenue_growth=max(0, total - rows_with_revenue_growth),
        rows_missing_float_or_shares=max(0, total - rows_with_float_or_shares),
        provider_calls_attempted=_counter(diagnostics, "provider_calls_attempted"),
        provider_rows_requested=_counter(diagnostics, "provider_rows_requested"),
        provider_rows_returned=_counter(diagnostics, "provider_rows_returned"),
        provider_rows_parsed=_counter(diagnostics, "provider_rows_parsed"),
        rows_updated=max(0, int(rows_updated)),
        cache_hits=_counter(diagnostics, "provider_cache_hits") or _counter(diagnostics, "fmp_cache_hits") + _counter(diagnostics, "databento_equities_cache_hits"),
        warnings=_counter(diagnostics, "provider_warnings")
        or _counter(diagnostics, "databento_equities_provider_warnings")
        + _counter(diagnostics, "databento_dataset_mismatch_warnings")
        + _counter(diagnostics, "rows_blocked_by_provider_plan_rate_auth_limit"),
    )


def _market_screener_enrichment_families(mode: str, required_fields: Iterable[str]) -> tuple[str, ...]:
    if mode == MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION:
        return (MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION,)
    if mode == MARKET_SCREENER_ENRICH_QUOTE_TAPE:
        return (MARKET_SCREENER_ENRICH_QUOTE_TAPE,)
    if mode == MARKET_SCREENER_ENRICH_FUNDAMENTALS:
        return (MARKET_SCREENER_ENRICH_FUNDAMENTALS,)
    if mode == MARKET_SCREENER_ENRICH_ASK_SCREENER_CANDIDATE_SET:
        groups = _ask_screener_provider_groups_for_fields(required_fields or ASK_SCREENER_PROVIDER_AWARE_FIELDS)
        families: list[str] = []
        if "fmp_profile" in groups:
            families.append(MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION)
        if "databento_tape" in groups:
            families.append(MARKET_SCREENER_ENRICH_QUOTE_TAPE)
        if "fmp_fundamentals" in groups:
            families.append(MARKET_SCREENER_ENRICH_FUNDAMENTALS)
        return tuple(families) or (
            MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION,
            MARKET_SCREENER_ENRICH_QUOTE_TAPE,
            MARKET_SCREENER_ENRICH_FUNDAMENTALS,
        )
    return (
        MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION,
        MARKET_SCREENER_ENRICH_QUOTE_TAPE,
        MARKET_SCREENER_ENRICH_FUNDAMENTALS,
    )


def _market_screener_scope_rows(
    rows: Iterable[MarketScreenerRecord],
    mode: str,
    *,
    scope_records: Iterable[MarketScreenerRecord] | None,
    selected_record: MarketScreenerRecord | None,
    candidate_records: Iterable[MarketScreenerRecord] | None,
) -> tuple[MarketScreenerRecord, ...]:
    all_rows = tuple(rows)
    if mode == MARKET_SCREENER_ENRICH_SELECTED_ROW:
        return (selected_record,) if selected_record is not None else tuple(scope_records or all_rows[:1])
    if mode == MARKET_SCREENER_ENRICH_VISIBLE_PAGE:
        return tuple(scope_records or all_rows)
    if mode == MARKET_SCREENER_ENRICH_ASK_SCREENER_CANDIDATE_SET:
        return tuple(candidate_records or scope_records or all_rows)
    return all_rows


def _market_screener_family_fields(family: str) -> tuple[str, ...]:
    if family == MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION:
        return ("company_name", "exchange", "sector", "industry", "cik")
    if family == MARKET_SCREENER_ENRICH_QUOTE_TAPE:
        return ("price", "change_percent", "volume", "avg_volume")
    if family == MARKET_SCREENER_ENRICH_FUNDAMENTALS:
        return ("market_cap", "pe_ratio", "eps", "revenue_growth", "shares_float", "shares_outstanding")
    return tuple(sorted(ASK_SCREENER_PROVIDER_AWARE_FIELDS))


def _market_screener_fields_for_families(families: Iterable[str]) -> tuple[str, ...]:
    fields: list[str] = []
    for family in families:
        fields.extend(_market_screener_family_fields(family))
    return tuple(_dedupe_texts(fields))


def _market_screener_family_limit(family: str, config: MarketScreenerBackfillConfig) -> int:
    if family == MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION:
        return config.profile_limit
    if family == MARKET_SCREENER_ENRICH_QUOTE_TAPE:
        return max(config.quote_limit, config.databento_limit)
    if family == MARKET_SCREENER_ENRICH_FUNDAMENTALS:
        return config.fundamental_limit
    return max(config.profile_limit, config.quote_limit, config.fundamental_limit, config.databento_limit)


def _market_screener_stage_symbols(
    all_rows: Iterable[MarketScreenerRecord],
    scoped_rows: Iterable[MarketScreenerRecord],
    fields: Iterable[str],
    *,
    symbols: Iterable[str] | None,
    max_symbols: int,
) -> tuple[str, ...]:
    if max_symbols <= 0:
        return ()
    if symbols is not None:
        return tuple(_dedupe_texts(_normalize_symbol(symbol) for symbol in symbols if _normalize_symbol(symbol)))[:max_symbols]
    scoped_keys = {_record_key(record) for record in scoped_rows if _record_key(record)}
    candidate_rows = [record for record in all_rows if _record_key(record) in scoped_keys] if scoped_keys else list(scoped_rows)
    result: list[str] = []
    seen: set[str] = set()
    for record in sorted(candidate_rows, key=lambda row: (_ask_screener_enrichment_priority(row), _normalize_symbol(row.symbol))):
        symbol = _normalize_symbol(record.symbol)
        if not symbol or symbol in seen:
            continue
        if not any(not _has_value(_ask_screener_field_value(record, field)) for field in fields):
            continue
        seen.add(symbol)
        result.append(symbol)
        if len(result) >= max_symbols:
            break
    return tuple(result)


def _market_screener_provider_family_snapshot(
    provider: Any,
    family: str,
    symbols: tuple[str, ...],
    *,
    force_refresh: bool,
    max_symbols: int,
) -> MarketQuoteFundamentalsSnapshot:
    method = getattr(provider, family, None)
    if family == MARKET_SCREENER_ENRICH_PROFILE_CLASSIFICATION:
        method = getattr(provider, "profile_classification", None)
    elif family == MARKET_SCREENER_ENRICH_QUOTE_TAPE:
        method = getattr(provider, "quote_tape", None)
    elif family == MARKET_SCREENER_ENRICH_FUNDAMENTALS:
        method = getattr(provider, "fundamentals", None)
    if callable(method):
        return method(symbols, force_refresh=force_refresh, max_symbols=max_symbols)
    return provider.quote_fundamentals(symbols, force_refresh=force_refresh, max_symbols=max_symbols)


def _count_screener_rows_changed(
    before: Iterable[MarketScreenerRecord],
    after: Iterable[MarketScreenerRecord],
    fields: Iterable[str],
) -> int:
    before_by_key = {_record_key(record): record for record in before if _record_key(record)}
    changed = 0
    for record in after:
        key = _record_key(record)
        old = before_by_key.get(key)
        if old is None:
            continue
        if any(_ask_screener_field_value(old, field) != _ask_screener_field_value(record, field) for field in fields):
            changed += 1
    return changed


def _market_screener_enrichment_report_status(report: MarketScreenerEnrichmentReport, fetched_at: str) -> MarketScreenerSourceStatus:
    return MarketScreenerSourceStatus(
        "Market Screener enrichment coverage",
        "available",
        fetched_at,
        (
            f"{report.mode}: total rows {report.total_rows}; profile/classification {report.rows_with_profile_classification}; "
            f"price {report.rows_with_price}; volume {report.rows_with_volume}; avg volume {report.rows_with_avg_volume}; "
            f"fundamentals {report.rows_with_fundamentals}; revenue growth {report.rows_with_revenue_growth}; float/shares {report.rows_with_float_or_shares}; "
            f"missing price {report.rows_missing_price}; missing volume {report.rows_missing_volume}; missing avg volume {report.rows_missing_avg_volume}; "
            f"missing fundamentals {report.rows_missing_fundamentals}; provider calls attempted {report.provider_calls_attempted}; "
            f"rows updated {report.rows_updated}; cache hits {report.cache_hits}; warnings {report.warnings}."
        ),
    )


def _record_has_profile_classification(record: MarketScreenerRecord) -> bool:
    return bool(_has_value(record.company_name) and _has_value(record.exchange) and _has_value(record.sector) and _has_value(record.industry))


def market_screener_record_has_market_data(record: MarketScreenerRecord) -> bool:
    return any(
        value is not None
        for value in (
            record.price,
            record.market_cap,
            record.volume,
            record.avg_volume,
            record.change_percent,
            record.pe_ratio,
            record.eps,
            record.revenue_growth,
            record.shares_float,
            record.shares_outstanding,
        )
    )


def market_screener_record_has_price_volume_data(record: MarketScreenerRecord) -> bool:
    return record.price is not None or record.volume is not None


def market_screener_record_has_quote_fields(record: MarketScreenerRecord) -> bool:
    return any(
        value is not None
        for value in (
            record.price,
            record.market_cap,
            record.volume,
            record.avg_volume,
            record.change_percent,
            record.pe_ratio,
            record.eps,
        )
    )


def market_screener_record_has_fundamentals(record: MarketScreenerRecord) -> bool:
    return any(value is not None for value in (record.market_cap, record.pe_ratio, record.eps, record.revenue_growth))


def market_screener_is_my_holding(record: MarketScreenerRecord) -> bool:
    signals = set(record.signals)
    sources = " ".join(record.sources).lower()
    return (
        "Schwab holding" in signals
        or "Watchlist" in signals
        or "local app holdings" in sources
        or record.portfolio_quantity is not None
        or record.portfolio_market_value is not None
    )


def market_screener_data_label(record: MarketScreenerRecord) -> str:
    parts: list[str] = []
    if market_screener_is_my_holding(record):
        parts.append("Holding")
    if market_screener_record_has_quote_fields(record):
        parts.append("Quote")
    elif market_screener_record_has_market_data(record):
        parts.append("Market data")
    if _record_has_source_label(record, "fmp"):
        parts.append("FMP")
    if record.recent_filing_date:
        parts.append("Filing")
    if record.next_earnings_date:
        parts.append("Earnings")
    return " + ".join(_dedupe_texts(parts)) if parts else "Universe only"


def market_screener_data_completeness(record: MarketScreenerRecord) -> dict[str, Any]:
    checks = (
        ("symbol", record.symbol),
        ("company_name", record.company_name),
        ("exchange", record.exchange),
        ("sector", record.sector),
        ("industry", record.industry),
        ("price", record.price),
        ("change_percent", record.change_percent),
        ("volume", record.volume),
        ("avg_volume", record.avg_volume),
        ("market_cap", record.market_cap),
        ("pe_ratio", record.pe_ratio),
        ("eps", record.eps),
        ("revenue_growth", record.revenue_growth),
        ("shares_float_or_outstanding", record.shares_float if record.shares_float is not None else record.shares_outstanding),
        ("event_context", record.next_earnings_date or record.recent_filing_date or record.recent_filing_type or record.signals or record.risk_flags),
        ("source_links", record.source_links),
        ("field_provenance", record.field_provenance),
        ("portfolio_context", record.portfolio_quantity if record.portfolio_quantity is not None else record.portfolio_market_value),
    )
    present = [field for field, value in checks if _has_value(value)]
    missing = [field for field, value in checks if not _has_value(value)]
    score = int(round((len(present) / max(len(checks), 1)) * 100))
    if score >= 75:
        label = "High"
    elif score >= 40:
        label = "Partial"
    else:
        label = "Low"
    return {
        "score": score,
        "label": label,
        "present_fields": tuple(present),
        "missing_fields": tuple(missing),
        "missing_source_families": _market_screener_missing_source_families(record),
    }


def market_screener_data_completeness_score(record: MarketScreenerRecord) -> int:
    return int(market_screener_data_completeness(record)["score"])


def market_screener_data_completeness_label(record: MarketScreenerRecord) -> str:
    completeness = market_screener_data_completeness(record)
    return f"{completeness['score']}% {str(completeness['label']).lower()}"


def _market_screener_missing_source_families(record: MarketScreenerRecord) -> tuple[str, ...]:
    families: list[str] = []
    if _missing_named_fields(record, ("exchange", "sector", "industry")):
        families.append("profile")
    if _missing_named_fields(record, ("price", "volume", "change_percent", "avg_volume")):
        families.append("quote/tape")
    if _missing_named_fields(record, ("market_cap", "pe_ratio", "eps", "revenue_growth", "shares_float", "shares_outstanding")):
        families.append("fundamentals")
    if not record.next_earnings_date:
        families.append("earnings calendar")
    if not record.recent_filing_date:
        families.append("FMP filings")
    return tuple(families)


def filter_market_screener_records(
    records: Iterable[MarketScreenerRecord],
    *,
    search: str = "",
    sector: str = "All",
    exchange: str = "All",
    event_type: str = "All",
    risk_flag: str = "All",
    earnings_date_window: str = "All",
    data_completeness: str = "All",
    has_ai_signal: bool = False,
    has_price_volume_data: bool = False,
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
        if not _data_completeness_matches(record, data_completeness):
            continue
        if has_ai_signal and not market_screener_has_ai_signal(record):
            continue
        if has_price_volume_data and not market_screener_record_has_price_volume_data(record):
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
    if column == "market_cap":
        return sorted(rows, key=lambda record: _market_cap_sort_key(record, descending=descending))
    present = [record for record in rows if _sort_value(record, column) is not None]
    missing = [record for record in rows if _sort_value(record, column) is None]
    present.sort(key=lambda record: _sort_value(record, column), reverse=descending)
    return present + missing


def market_screener_market_cap_rank(record: MarketScreenerRecord) -> MarketScreenerMarketCapRank:
    display_cap = _float_or_none(record.market_cap)
    explicit_rank = _float_or_none(record.market_cap_rank_value)
    raw_rank = explicit_rank if explicit_rank is not None else display_cap
    currency = _normalize_currency_code(record.market_cap_rank_currency or record.market_cap_currency)
    explicit_trust = record.market_cap_rank_trusted
    if raw_rank is None or raw_rank < 0:
        return MarketScreenerMarketCapRank(
            display_market_cap=display_cap,
            ranking_market_cap=None,
            currency=currency or "unknown",
            category="missing_or_invalid",
            trusted=False,
            used_for_ranking=False,
            reason="missing or invalid market cap; sorted below rows with usable caps",
        )

    non_primary_reason = _non_primary_market_cap_reason(record)
    if explicit_trust is False:
        return MarketScreenerMarketCapRank(
            display_market_cap=display_cap,
            ranking_market_cap=raw_rank,
            currency=currency or "unknown",
            category="untrusted_explicit",
            trusted=False,
            used_for_ranking=False,
            reason=record.market_cap_rank_reason or "provider marked market cap as untrusted for ranking",
        )

    if currency and currency != "USD":
        return MarketScreenerMarketCapRank(
            display_market_cap=display_cap,
            ranking_market_cap=raw_rank,
            currency=currency,
            category="untrusted_non_usd",
            trusted=False,
            used_for_ranking=False,
            reason=f"market cap currency is {currency}, not USD; raw display cap is not used ahead of trusted USD caps",
        )

    if explicit_rank is not None and (currency in {"", "USD"} or not currency) and explicit_trust is not False:
        trusted = explicit_trust is True or currency == "USD" or _is_us_primary_common_equity(record)
        if trusted:
            category = "trusted_non_primary" if non_primary_reason else "us_primary_common"
            reason = record.market_cap_rank_reason or (
                f"provider supplied normalized USD ranking market cap; {non_primary_reason}"
                if non_primary_reason
                else "provider supplied normalized USD ranking market cap"
            )
            return MarketScreenerMarketCapRank(
                display_market_cap=display_cap,
                ranking_market_cap=raw_rank,
                currency="USD",
                category=category,
                trusted=True,
                used_for_ranking=True,
                reason=reason,
            )

    if non_primary_reason:
        if currency == "USD":
            return MarketScreenerMarketCapRank(
                display_market_cap=display_cap,
                ranking_market_cap=raw_rank,
                currency="USD",
                category="trusted_non_primary",
                trusted=True,
                used_for_ranking=True,
                reason=f"USD market cap is usable but demoted below U.S. primary/common equities because {non_primary_reason}",
            )
        return MarketScreenerMarketCapRank(
            display_market_cap=display_cap,
            ranking_market_cap=raw_rank,
            currency=currency or "unknown",
            category="untrusted_non_primary",
            trusted=False,
            used_for_ranking=False,
            reason=f"{non_primary_reason}; currency/source is ambiguous, so raw cap is sorted below trusted USD caps",
        )

    if currency == "USD" or _is_us_primary_common_equity(record):
        return MarketScreenerMarketCapRank(
            display_market_cap=display_cap,
            ranking_market_cap=raw_rank,
            currency="USD",
            category="us_primary_common",
            trusted=True,
            used_for_ranking=True,
            reason="trusted USD market cap for a U.S. primary/common equity row",
        )

    return MarketScreenerMarketCapRank(
        display_market_cap=display_cap,
        ranking_market_cap=raw_rank,
        currency=currency or "unknown",
        category="untrusted_ambiguous",
        trusted=False,
        used_for_ranking=False,
        reason="market cap currency, listing country, or instrument type is ambiguous; raw cap is sorted below trusted USD caps",
    )


def market_screener_major_cap_diagnostic_lines(
    records: Iterable[MarketScreenerRecord],
    diagnostics: MarketScreenerCoverageDiagnostics | None = None,
) -> list[str]:
    rows_by_symbol = {_normalize_symbol(record.symbol): record for record in records if _normalize_symbol(record.symbol)}
    lines: list[str] = []
    for symbol in MAJOR_US_LARGE_CAP_SYMBOLS:
        record = rows_by_symbol.get(symbol)
        if record is None:
            cap_detail = ""
            if diagnostics is not None and diagnostics.rows_skipped_by_configured_symbol_cap:
                cap_detail = f" {diagnostics.rows_skipped_by_configured_symbol_cap} row(s) were skipped by configured provider caps during enrichment."
            lines.append(
                f"{symbol} absent: no row with this symbol is present in the loaded screener set. "
                f"It may be outside the loaded universe, filtered out before this view, blocked by provider caps, or returned with no usable provider identity.{cap_detail}"
            )
            continue
        rank = market_screener_market_cap_rank(record)
        if rank.trusted and rank.category == "us_primary_common":
            lines.append(f"{symbol} present: trusted USD market-cap rank is available.")
        elif record.market_cap is None:
            lines.append(f"{symbol} present but not ranked by market cap: market cap is missing.")
        else:
            lines.append(f"{symbol} present but market-cap rank is not primary trusted: {rank.reason}")
    return lines


def market_screener_has_ai_signal(record: MarketScreenerRecord) -> bool:
    return bool(record.signals or record.risk_flags or record.next_earnings_date or record.recent_filing_date)


def ask_screener_config_from_env(*, schwab_quote_configured: bool = False) -> AskScreenerProviderConfig:
    local_path = str(os.getenv("MARKET_SCREENER_MARKET_DATA_PATH", "") or "").strip()
    databento_enabled = _env_flag(ASK_SCREENER_ENV_NAME_DATABENTO_ENABLED, default=False)
    databento_dataset = str(os.getenv("DATABENTO_EQUITIES_DATASET", "") or "").strip()
    databento_schema = str(os.getenv("DATABENTO_EQUITIES_SCHEMA", "") or "").strip()
    return AskScreenerProviderConfig(
        auto_enrich=_env_flag(ASK_SCREENER_AUTO_ENRICH_ENV, default=DEFAULT_ASK_SCREENER_AUTO_ENRICH),
        profile_enrich_limit=_env_int(ASK_SCREENER_PROFILE_ENRICH_LIMIT_ENV, DEFAULT_ASK_SCREENER_PROFILE_ENRICH_LIMIT, minimum=0, maximum=10_000),
        quote_enrich_limit=_env_int(ASK_SCREENER_QUOTE_ENRICH_LIMIT_ENV, DEFAULT_ASK_SCREENER_QUOTE_ENRICH_LIMIT, minimum=0, maximum=10_000),
        fundamental_enrich_limit=_env_int(ASK_SCREENER_FUNDAMENTAL_ENRICH_LIMIT_ENV, DEFAULT_ASK_SCREENER_FUNDAMENTAL_ENRICH_LIMIT, minimum=0, maximum=10_000),
        databento_tape_enrich_limit=_env_int(ASK_SCREENER_DATABENTO_TAPE_ENRICH_LIMIT_ENV, DEFAULT_ASK_SCREENER_DATABENTO_TAPE_ENRICH_LIMIT, minimum=0, maximum=10_000),
        require_confirm_above=_env_int(ASK_SCREENER_REQUIRE_CONFIRM_ABOVE_ENV, DEFAULT_ASK_SCREENER_REQUIRE_CONFIRM_ABOVE, minimum=0, maximum=100_000),
        fmp_configured=_secret_configured(os.getenv("FMP_API_KEY", "")),
        databento_equities_configured=bool(
            databento_enabled
            and _secret_configured(os.getenv("DATABENTO_API_KEY", ""))
            and databento_dataset
            and databento_schema
        ),
        local_market_data_configured=bool(local_path and os.path.exists(local_path)),
        schwab_quote_configured=bool(schwab_quote_configured),
    )


def market_screener_backfill_config_from_env() -> MarketScreenerBackfillConfig:
    return MarketScreenerBackfillConfig(
        enabled=_env_flag(MARKET_SCREENER_PROVIDER_BACKFILL_ENABLED_ENV, default=DEFAULT_MARKET_SCREENER_PROVIDER_BACKFILL_ENABLED),
        profile_limit=_env_int(MARKET_SCREENER_PROFILE_BACKFILL_LIMIT_ENV, DEFAULT_MARKET_SCREENER_PROFILE_BACKFILL_LIMIT, minimum=0, maximum=100_000),
        quote_limit=_env_int(MARKET_SCREENER_QUOTE_BACKFILL_LIMIT_ENV, DEFAULT_MARKET_SCREENER_QUOTE_BACKFILL_LIMIT, minimum=0, maximum=100_000),
        fundamental_limit=_env_int(MARKET_SCREENER_FUNDAMENTAL_BACKFILL_LIMIT_ENV, DEFAULT_MARKET_SCREENER_FUNDAMENTAL_BACKFILL_LIMIT, minimum=0, maximum=100_000),
        databento_limit=_env_int(MARKET_SCREENER_DATABENTO_BACKFILL_LIMIT_ENV, DEFAULT_MARKET_SCREENER_DATABENTO_BACKFILL_LIMIT, minimum=0, maximum=100_000),
        batch_size=_env_int(MARKET_SCREENER_BACKFILL_BATCH_SIZE_ENV, DEFAULT_MARKET_SCREENER_BACKFILL_BATCH_SIZE, minimum=1, maximum=500),
        cache_ttl_seconds=_env_int(MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS_ENV, DEFAULT_MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS, minimum=0, maximum=604_800),
        confirm_above=_env_int(MARKET_SCREENER_CONFIRM_BACKFILL_ABOVE_ENV, DEFAULT_MARKET_SCREENER_CONFIRM_BACKFILL_ABOVE, minimum=0, maximum=1_000_000),
    )


def analyze_ask_screener_field_coverage(
    records: Iterable[MarketScreenerRecord],
    fields: Iterable[str] = ASK_SCREENER_PROVIDER_AWARE_FIELDS,
) -> tuple[AskScreenerFieldCoverage, ...]:
    rows = list(records)
    total = len(rows)
    coverage: list[AskScreenerFieldCoverage] = []
    for field in _normalize_required_fields(fields, include_provider_only=True):
        available = sum(1 for record in rows if _has_value(_ask_screener_field_value(record, field)))
        missing = max(0, total - available)
        coverage.append(
            AskScreenerFieldCoverage(
                field=field,
                available_count=available,
                missing_count=missing,
                total_rows=total,
                coverage_ratio=(available / total) if total else 0.0,
            )
        )
    return tuple(coverage)


def ask_screener_required_fields(plan: Mapping[str, Any] | AskScreenerPlan) -> tuple[str, ...]:
    validated = validate_ask_screener_plan(plan)
    return validated.required_fields


def ask_screener_enrichment_decision(
    records: Iterable[MarketScreenerRecord],
    plan: Mapping[str, Any] | AskScreenerPlan,
    *,
    config: AskScreenerProviderConfig | None = None,
    provider_configured: bool | None = None,
) -> AskScreenerEnrichmentDecision:
    validated = validate_ask_screener_plan(plan)
    config = config or ask_screener_config_from_env()
    rows = list(records)
    required_fields = validated.required_fields
    provider_fields = tuple(field for field in required_fields if field in ASK_SCREENER_PROVIDER_AWARE_FIELDS)
    if validated.clear_filters or not provider_fields:
        return AskScreenerEnrichmentDecision(
            action=ASK_SCREENER_PROVIDER_ACTION_EXECUTE_LOCAL_ONLY,
            required_fields=required_fields,
            missing_fields=(),
            candidate_symbol_count=0,
            reason="The validated Ask Screener plan does not require provider-enrichable fields.",
        )

    coverage_by_field = {row.field: row for row in analyze_ask_screener_field_coverage(rows, provider_fields)}
    missing_fields = tuple(field for field, row in coverage_by_field.items() if row.missing_count > 0)
    if not missing_fields:
        return AskScreenerEnrichmentDecision(
            action=ASK_SCREENER_PROVIDER_ACTION_EXECUTE_LOCAL_ONLY,
            required_fields=required_fields,
            missing_fields=(),
            candidate_symbol_count=0,
            reason="Required fields are already populated locally.",
        )
    if not config.auto_enrich:
        return AskScreenerEnrichmentDecision(
            action=ASK_SCREENER_PROVIDER_ACTION_EXECUTE_LOCAL_ONLY,
            required_fields=required_fields,
            missing_fields=missing_fields,
            candidate_symbol_count=0,
            reason=f"{ASK_SCREENER_AUTO_ENRICH_ENV}=false, so Ask Screener will execute against local fields only.",
        )

    provider_groups = _ask_screener_provider_groups_for_fields(provider_fields)
    missing_provider_config = () if provider_configured else _missing_ask_screener_provider_config(provider_groups, config)
    if missing_provider_config and provider_configured is not True:
        return AskScreenerEnrichmentDecision(
            action=ASK_SCREENER_PROVIDER_ACTION_MISSING_CONFIG,
            required_fields=required_fields,
            missing_fields=missing_fields,
            candidate_symbol_count=0,
            provider_groups=provider_groups,
            reason="Provider enrichment is required, but the relevant provider configuration is missing or disabled.",
            missing_provider_config=missing_provider_config,
        )

    max_symbols = _ask_screener_enrichment_cap(provider_fields, config)
    candidate_symbols = _ask_screener_enrichment_candidate_symbols(rows, required_fields=provider_fields)
    symbols_to_enrich = candidate_symbols[:max_symbols]
    if not symbols_to_enrich:
        return AskScreenerEnrichmentDecision(
            action=ASK_SCREENER_PROVIDER_ACTION_EXECUTE_LOCAL_ONLY,
            required_fields=required_fields,
            missing_fields=missing_fields,
            candidate_symbol_count=0,
            provider_groups=provider_groups,
            reason="No symbol-bearing rows require provider enrichment for the requested fields.",
            max_symbols=max_symbols,
        )

    action = ASK_SCREENER_PROVIDER_ACTION_ENRICH_THEN_EXECUTE
    reason = f"Provider enrichment can fill missing fields before executing the local filter plan; capped at {max_symbols} symbol(s)."
    if config.require_confirm_above > 0 and len(candidate_symbols) > config.require_confirm_above:
        action = ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT
        reason = (
            f"{len(candidate_symbols)} symbol(s) have missing required fields, above "
            f"{ASK_SCREENER_REQUIRE_CONFIRM_ABOVE_ENV}={config.require_confirm_above}; confirmation is required before enrichment."
        )
    return AskScreenerEnrichmentDecision(
        action=action,
        required_fields=required_fields,
        missing_fields=missing_fields,
        candidate_symbol_count=len(candidate_symbols),
        symbols_to_enrich=symbols_to_enrich,
        provider_groups=provider_groups,
        reason=reason,
        max_symbols=max_symbols,
    )


def execute_provider_aware_ask_screener_plan(
    records: Iterable[MarketScreenerRecord],
    plan: Mapping[str, Any] | AskScreenerPlan,
    *,
    provider: Any | None = None,
    config: AskScreenerProviderConfig | None = None,
    provider_configured: bool | None = None,
    force_refresh: bool = False,
    today: date | None = None,
    allow_large_enrichment: bool = False,
) -> AskScreenerExecutionResult:
    validated = validate_ask_screener_plan(plan)
    rows = list(records)
    decision = ask_screener_enrichment_decision(rows, validated, config=config, provider_configured=provider_configured)
    if decision.action == ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT and allow_large_enrichment:
        decision = replace(decision, action=ASK_SCREENER_PROVIDER_ACTION_ENRICH_THEN_EXECUTE)

    if decision.action != ASK_SCREENER_PROVIDER_ACTION_ENRICH_THEN_EXECUTE:
        result = execute_ask_screener_plan(rows, validated, today=today)
        status = "local-only"
        more_help = bool(decision.missing_fields and decision.action != ASK_SCREENER_PROVIDER_ACTION_MISSING_CONFIG)
        if decision.action == ASK_SCREENER_PROVIDER_ACTION_MISSING_CONFIG:
            status = "missing-provider-config"
            more_help = False
        elif decision.action == ASK_SCREENER_PROVIDER_ACTION_CONFIRM_LARGE_ENRICHMENT:
            status = "confirmation-required"
            more_help = True
        return _with_ask_screener_enrichment_status(
            result,
            decision=decision,
            enrichment_status=status,
            providers_used=(),
            symbols_requested=0,
            rows_updated=0,
            remaining_missing_fields=_remaining_missing_required_fields(rows, validated.required_fields),
            more_enrichment_may_help=more_help,
            notes=_ask_screener_decision_notes(decision),
        )

    active_provider = provider or configured_market_data_provider(
        include_fallback_provider=True,
        fmp_symbol_limit=max(config.profile_enrich_limit, config.quote_enrich_limit, config.fundamental_enrich_limit) if config else None,
        databento_symbol_limit=config.databento_tape_enrich_limit if config else None,
    )
    requested_symbols = decision.symbols_to_enrich
    enrichment_result: MarketScreenerEnrichmentResult | None = None
    provider_error: str | None = None
    enriched_rows = rows
    try:
        enrichment_result = enrich_market_screener_records(
            rows,
            MARKET_SCREENER_ENRICH_ASK_SCREENER_CANDIDATE_SET,
            provider=active_provider,
            config=_backfill_config_from_ask_screener_config(config),
            symbols=requested_symbols,
            required_fields=decision.required_fields,
            force_refresh=force_refresh,
        )
        enriched_rows = list(enrichment_result.records)
    except Exception as exc:
        provider_error = redact_symbol_chat_secrets(str(exc))

    result = execute_ask_screener_plan(enriched_rows, validated, today=today)
    providers_used = _ask_screener_enrichment_providers_used(enrichment_result)
    rows_updated = enrichment_result.report.rows_updated if enrichment_result is not None else 0
    symbols_requested_count = len(enrichment_result.requested_symbols) if enrichment_result is not None else len(requested_symbols)
    remaining_missing = _remaining_missing_required_fields(enriched_rows, validated.required_fields)
    more_help = bool(
        remaining_missing
        and (
            decision.candidate_symbol_count > symbols_requested_count
            or rows_updated < symbols_requested_count
            or any(count > 0 for count in remaining_missing.values())
        )
    )
    notes = list(result.notes)
    notes.extend(_ask_screener_decision_notes(decision))
    if provider_error:
        notes.append(f"Provider error was nonblocking and redacted: {provider_error}")
    elif enrichment_result is not None and enrichment_result.errors:
        notes.append(f"Provider warnings were nonblocking and redacted: {enrichment_result.errors[0]}")
    if enrichment_result is not None:
        notes.append(
            f"Coverage after {enrichment_result.mode}: price {enrichment_result.report.rows_with_price}/{enrichment_result.report.total_rows}, "
            f"volume {enrichment_result.report.rows_with_volume}/{enrichment_result.report.total_rows}, "
            f"fundamentals {enrichment_result.report.rows_with_fundamentals}/{enrichment_result.report.total_rows}; "
            f"provider calls {enrichment_result.report.provider_calls_attempted}, cache hits {enrichment_result.report.cache_hits}, warnings {enrichment_result.report.warnings}."
        )
    return _with_ask_screener_enrichment_status(
        result,
        decision=decision,
        enrichment_status="provider-enriched" if rows_updated else "provider-attempted",
        providers_used=providers_used,
        symbols_requested=symbols_requested_count,
        rows_updated=rows_updated,
        remaining_missing_fields=remaining_missing,
        more_enrichment_may_help=more_help,
        notes=tuple(notes),
    )


def _with_ask_screener_enrichment_status(
    result: AskScreenerExecutionResult,
    *,
    decision: AskScreenerEnrichmentDecision,
    enrichment_status: str,
    providers_used: Iterable[str],
    symbols_requested: int,
    rows_updated: int,
    remaining_missing_fields: Mapping[str, int],
    more_enrichment_may_help: bool,
    notes: Iterable[str] = (),
) -> AskScreenerExecutionResult:
    clean_providers = tuple(_dedupe_texts(providers_used))
    clean_remaining = {field: int(count) for field, count in remaining_missing_fields.items() if int(count) > 0}
    summary = ask_screener_result_summary(
        result.plan,
        total_input_rows=result.total_input_rows,
        total_matched_rows=result.total_matched_rows,
        total_output_rows=len(result.records),
        limited=result.limited,
        enrichment_status=enrichment_status,
        providers_used=clean_providers,
        symbols_requested=symbols_requested,
        rows_updated=rows_updated,
        remaining_missing_fields=clean_remaining,
        more_enrichment_may_help=more_enrichment_may_help,
    )
    merged_notes = tuple(_dedupe_texts((*result.notes, *notes)))
    return replace(
        result,
        summary=summary,
        notes=merged_notes,
        enrichment_status=enrichment_status,
        providers_used=clean_providers,
        symbols_requested=max(0, int(symbols_requested)),
        rows_updated=max(0, int(rows_updated)),
        remaining_missing_fields=clean_remaining,
        more_enrichment_may_help=bool(more_enrichment_may_help),
        enrichment_decision=decision,
    )


def _ask_screener_decision_notes(decision: AskScreenerEnrichmentDecision) -> tuple[str, ...]:
    notes = [decision.reason] if decision.reason else []
    if decision.missing_provider_config:
        notes.append("Missing provider config: " + ", ".join(decision.missing_provider_config))
    if decision.missing_fields:
        notes.append("Required fields with missing local coverage: " + ", ".join(decision.missing_fields))
    return tuple(redact_symbol_chat_secrets(note) for note in notes if note)


def _ask_screener_providers_used(snapshot: MarketQuoteFundamentalsSnapshot | None) -> tuple[str, ...]:
    if snapshot is None:
        return ()
    sources = [record.source for record in snapshot.records if record.source]
    sources.extend(
        status.source
        for status in snapshot.statuses
        if status.source and str(status.status).lower() not in {"disabled", "unavailable"}
    )
    return tuple(_dedupe_texts(sources))


def _ask_screener_enrichment_providers_used(result: MarketScreenerEnrichmentResult | None) -> tuple[str, ...]:
    if result is None:
        return ()
    return tuple(
        _dedupe_texts(
            status.source
            for status in result.statuses
            if status.source and str(status.status).lower() not in {"disabled", "unavailable", "empty"}
        )
    )


def _backfill_config_from_ask_screener_config(config: AskScreenerProviderConfig | None) -> MarketScreenerBackfillConfig:
    base = market_screener_backfill_config_from_env()
    if config is None:
        return base
    return MarketScreenerBackfillConfig(
        enabled=config.auto_enrich,
        profile_limit=config.profile_enrich_limit,
        quote_limit=config.quote_enrich_limit,
        fundamental_limit=config.fundamental_enrich_limit,
        databento_limit=config.databento_tape_enrich_limit,
        batch_size=base.batch_size,
        cache_ttl_seconds=base.cache_ttl_seconds,
        confirm_above=config.require_confirm_above,
    )


def _remaining_missing_required_fields(records: Iterable[MarketScreenerRecord], required_fields: Iterable[str]) -> dict[str, int]:
    rows = list(records)
    remaining: dict[str, int] = {}
    for field in _normalize_required_fields(required_fields, include_provider_only=True):
        missing = sum(1 for record in rows if not _has_value(_ask_screener_field_value(record, field)))
        if missing:
            remaining[field] = missing
    return remaining


def _ask_screener_enrichment_candidate_symbols(
    records: Iterable[MarketScreenerRecord],
    *,
    required_fields: Iterable[str],
) -> tuple[str, ...]:
    fields = _normalize_required_fields(required_fields, include_provider_only=True)
    seen: set[str] = set()
    prioritized: list[tuple[int, str]] = []
    for record in records:
        symbol = _normalize_symbol(record.symbol)
        if not symbol or symbol in seen:
            continue
        if not any(not _has_value(_ask_screener_field_value(record, field)) for field in fields):
            continue
        seen.add(symbol)
        prioritized.append((_ask_screener_enrichment_priority(record), symbol))
    return tuple(symbol for _rank, symbol in sorted(prioritized, key=lambda row: (row[0], row[1])))


def _ask_screener_enrichment_priority(record: MarketScreenerRecord) -> int:
    non_portfolio_signals = [signal for signal in record.signals if signal not in {"Schwab holding", "Watchlist"}]
    if record.recent_filing_date or record.next_earnings_date or record.risk_flags or non_portfolio_signals:
        return 1
    return 2


def _ask_screener_enrichment_cap(fields: Iterable[str], config: AskScreenerProviderConfig) -> int:
    provider_fields = set(_normalize_required_fields(fields, include_provider_only=True))
    caps: list[int] = []
    if provider_fields & ASK_SCREENER_CLASSIFICATION_FIELDS:
        caps.append(config.profile_enrich_limit)
    if provider_fields & ASK_SCREENER_TAPE_FIELDS:
        caps.append(min(config.quote_enrich_limit, config.databento_tape_enrich_limit))
    if provider_fields & ASK_SCREENER_FUNDAMENTAL_FIELDS:
        caps.append(min(config.quote_enrich_limit, config.fundamental_enrich_limit))
    if not caps:
        return 0
    return max(0, min(caps))


def _ask_screener_provider_groups_for_fields(fields: Iterable[str]) -> tuple[str, ...]:
    provider_fields = set(_normalize_required_fields(fields, include_provider_only=True))
    groups: list[str] = []
    if provider_fields & ASK_SCREENER_CLASSIFICATION_FIELDS:
        groups.append("fmp_profile")
    if provider_fields & ASK_SCREENER_TAPE_FIELDS:
        groups.append("databento_tape")
    if provider_fields & ASK_SCREENER_FUNDAMENTAL_FIELDS:
        groups.append("fmp_fundamentals")
    return tuple(groups)


def _missing_ask_screener_provider_config(
    provider_groups: Iterable[str],
    config: AskScreenerProviderConfig,
) -> tuple[str, ...]:
    missing: list[str] = []
    groups = set(provider_groups)
    local_can_fill = config.local_market_data_configured
    if "fmp_profile" in groups and not (config.fmp_configured or local_can_fill):
        missing.append("FMP_API_KEY for FMP profile/classification enrichment")
    if "fmp_fundamentals" in groups and not (config.fmp_configured or local_can_fill):
        missing.append("FMP_API_KEY for FMP quote/fundamental enrichment")
    if "databento_tape" in groups and not (
        config.databento_equities_configured
        or config.fmp_configured
        or config.local_market_data_configured
        or config.schwab_quote_configured
    ):
        missing.append("DATABENTO_API_KEY plus enabled US equities dataset/schema for Databento tape enrichment")
    return tuple(missing)


def _normalize_required_fields(fields: Iterable[Any], *, include_provider_only: bool = False) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in fields:
        try:
            field = _normalize_ask_screener_field(value)
        except AskScreenerPlanValidationError:
            continue
        normalized.extend(_ask_screener_underlying_fields(field))
    allowed = ASK_SCREENER_PROVIDER_AWARE_FIELDS if include_provider_only else ASK_SCREENER_ALLOWED_FIELDS
    return tuple(field for field in _dedupe_texts(normalized) if field in allowed)


def _derive_ask_screener_required_fields(
    filters: Iterable[AskScreenerFilter],
    sort: AskScreenerSort | None,
    explicit_fields: Iterable[Any] = (),
) -> tuple[str, ...]:
    fields: list[str] = []
    fields.extend(_normalize_required_fields(explicit_fields))
    for filter_row in filters:
        fields.extend(_ask_screener_underlying_fields(filter_row.field))
    if sort is not None:
        fields.extend(_ask_screener_underlying_fields(sort.field))
    return tuple(_dedupe_texts(field for field in fields if field in ASK_SCREENER_ALLOWED_FIELDS))


def _ask_screener_underlying_fields(field: str) -> tuple[str, ...]:
    if field == "classification":
        return ("sector", "industry", "exchange")
    if field == "high_volume_mover":
        return ("change_percent", "volume", "avg_volume")
    if field == "momentum_proxy":
        return ("change_percent", "volume", "avg_volume")
    if field == "recent_catalyst":
        return ("recent_filing_date", "next_earnings_date", "signals", "risk_flags")
    if field == "has_market_data" or field == "has_quote":
        return ("price", "volume", "change_percent", "avg_volume", "market_cap")
    if field == "has_fundamentals":
        return tuple(sorted(ASK_SCREENER_FUNDAMENTAL_FIELDS))
    if field == "has_recent_filing":
        return ("recent_filing_date",)
    if field == "has_upcoming_earnings":
        return ("next_earnings_date",)
    if field == "missing_price_data":
        return ("price",)
    if field == "has_positive_revenue_growth":
        return ("revenue_growth",)
    if field == "has_negative_eps":
        return ("eps",)
    if field == "has_missing_data":
        return tuple(sorted(ASK_SCREENER_PROVIDER_AWARE_FIELDS))
    return (field,)


def _env_flag(name: str, *, default: bool) -> bool:
    raw = str(os.getenv(name, "") or "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        parsed = int(float(raw))
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return default
    try:
        parsed = float(raw.replace("$", "").replace(",", ""))
    except ValueError:
        return default
    return max(minimum, min(maximum, parsed))


def _secret_configured(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return text.upper() not in {"THIS IS NOT A KEY", "NOT_A_KEY", "CHANGEME", "CHANGE_ME", "YOUR_API_KEY"}


def validate_ask_screener_plan(payload: Mapping[str, Any] | AskScreenerPlan) -> AskScreenerPlan:
    if isinstance(payload, AskScreenerPlan):
        payload = payload.to_dict()
    if not isinstance(payload, Mapping):
        raise AskScreenerPlanValidationError("Ask Screener plan must be a JSON object.")

    clear_filters = _coerce_bool(payload.get("clear_filters"), default=False)
    raw_filters = payload.get("filters", ())
    if raw_filters in (None, ""):
        raw_filters = ()
    if not isinstance(raw_filters, (list, tuple)):
        raise AskScreenerPlanValidationError("Ask Screener filters must be a list.")
    if len(raw_filters) > ASK_SCREENER_MAX_FILTERS:
        raise AskScreenerPlanValidationError(f"Ask Screener supports at most {ASK_SCREENER_MAX_FILTERS} filters.")

    filters = tuple(_validate_ask_screener_filter(item) for item in raw_filters)
    sort = _validate_ask_screener_sort(payload.get("sort") or payload.get("order_by"))
    limit = _validate_ask_screener_limit(payload.get("limit"))
    intent = _shorten(_clean_prompt(str(payload.get("intent") or payload.get("description") or "")), 180)
    raw_required_fields = payload.get("required_fields") or payload.get("required_screener_fields") or ()
    if isinstance(raw_required_fields, str):
        raw_required_fields = re.split(r"[,;\s]+", raw_required_fields)
    if not isinstance(raw_required_fields, (list, tuple, set)):
        raw_required_fields = ()
    required_fields = _derive_ask_screener_required_fields(filters, sort, raw_required_fields)
    provider_enrichment_needed = _coerce_bool(
        payload.get("provider_enrichment_needed", payload.get("needs_provider_enrichment")),
        default=bool(set(required_fields) & ASK_SCREENER_PROVIDER_AWARE_FIELDS),
    )

    if not clear_filters and not filters and sort is None:
        raise AskScreenerPlanValidationError("Ask Screener plan must include filters, sorting, or clear_filters=true.")
    return AskScreenerPlan(
        filters=filters,
        sort=sort,
        limit=limit,
        intent=intent,
        clear_filters=clear_filters,
        required_fields=required_fields,
        provider_enrichment_needed=provider_enrichment_needed,
    )


def execute_ask_screener_plan(
    records: Iterable[MarketScreenerRecord],
    plan: Mapping[str, Any] | AskScreenerPlan,
    *,
    today: date | None = None,
) -> AskScreenerExecutionResult:
    validated = validate_ask_screener_plan(plan)
    today = today or datetime.now().date()
    rows = list(records)
    if validated.clear_filters:
        matched = rows
    else:
        matched = [record for record in rows if all(_ask_screener_filter_matches(record, row, today=today) for row in validated.filters)]
    ordered = _sort_ask_screener_records(matched, validated.sort) if validated.sort is not None else matched
    effective_limit = len(ordered) if validated.clear_filters else validated.limit
    limited = len(ordered) > effective_limit
    result_rows = tuple(ordered[:effective_limit])
    notes = ("Missing market/fundamental values were not inferred; filters only used loaded Market Screener fields.",)
    summary = ask_screener_result_summary(
        validated,
        total_input_rows=len(rows),
        total_matched_rows=len(matched),
        total_output_rows=len(result_rows),
        limited=limited,
        enrichment_status="local-only",
        providers_used=(),
        symbols_requested=0,
        rows_updated=0,
        remaining_missing_fields=_remaining_missing_required_fields(rows, validated.required_fields),
        more_enrichment_may_help=False,
    )
    return AskScreenerExecutionResult(
        plan=validated,
        records=result_rows,
        total_input_rows=len(rows),
        total_matched_rows=len(matched),
        limited=limited,
        summary=summary,
        all_records=tuple(rows),
        notes=notes,
        enrichment_status="local-only",
        remaining_missing_fields=_remaining_missing_required_fields(rows, validated.required_fields),
    )


def ask_screener_result_summary(
    plan: AskScreenerPlan,
    *,
    total_input_rows: int,
    total_matched_rows: int,
    total_output_rows: int,
    limited: bool,
    enrichment_status: str = "local-only",
    providers_used: Iterable[str] = (),
    symbols_requested: int = 0,
    rows_updated: int = 0,
    remaining_missing_fields: Mapping[str, int] | None = None,
    more_enrichment_may_help: bool = False,
) -> str:
    if plan.clear_filters:
        return redact_symbol_chat_secrets(f"Ask Screener cleared filters. Showing {total_output_rows} of {total_input_rows} row(s).")
    label = plan.intent or "Ask Screener"
    parts = [f"{label}: matched {total_matched_rows} of {total_input_rows} row(s)"]
    if limited:
        parts.append(f"showing first {total_output_rows} by the validated plan limit")
    else:
        parts.append(f"showing {total_output_rows}")
    if plan.filters:
        parts.append("filters: " + "; ".join(_ask_screener_filter_label(row) for row in plan.filters))
    if plan.sort is not None:
        direction = "descending" if plan.sort.descending else "ascending"
        parts.append(f"sorted by {plan.sort.field} {direction}")
    parts.append("missing values were not inferred")
    parts.append(f"status: {enrichment_status}")
    clean_providers = tuple(_dedupe_texts(providers_used))
    if clean_providers:
        parts.append("providers used: " + ", ".join(clean_providers))
    parts.append(f"symbols requested: {max(0, int(symbols_requested))}")
    parts.append(f"rows updated: {max(0, int(rows_updated))}")
    parts.append(f"matches found: {total_matched_rows}")
    remaining = {field: int(count) for field, count in (remaining_missing_fields or {}).items() if int(count) > 0}
    if remaining:
        preview = ", ".join(f"{field}={count}" for field, count in sorted(remaining.items())[:6])
        extra = len(remaining) - 6
        parts.append("remaining missing fields: " + (f"{preview}, +{extra} more" if extra > 0 else preview))
    else:
        parts.append("remaining missing fields: none")
    parts.append(f"more enrichment may help: {'yes' if more_enrichment_may_help else 'no'}")
    return redact_symbol_chat_secrets(". ".join(parts) + ".")


def parse_ask_screener_fallback(
    question: str,
    *,
    records: Iterable[MarketScreenerRecord] = (),
) -> AskScreenerPlan | None:
    clean = _clean_prompt(question)
    lower = clean.lower()
    if not lower:
        return None
    if any(phrase in lower for phrase in ("clear filters", "reset filters", "remove filters", "show all", "clear screener")):
        return AskScreenerPlan(intent="Clear filters", clear_filters=True, limit=ASK_SCREENER_MAX_LIMIT)

    filters: list[AskScreenerFilter] = []
    sort: AskScreenerSort | None = None
    intent = _shorten(clean, 180)
    limit = _parse_ask_screener_limit(lower) or ASK_SCREENER_DEFAULT_LIMIT
    explicit_required_fields: list[str] = []

    if any(phrase in lower for phrase in ("my holdings", "my holding", "holdings", "watchlist")):
        filters.append(AskScreenerFilter("is_my_holding", "is_true"))
    if any(phrase in lower for phrase in ("quote enriched", "quote-enriched", "quote data", "with quotes", "market data")):
        filters.append(AskScreenerFilter("has_market_data", "is_true"))
    if "fundamental" in lower:
        filters.append(AskScreenerFilter("has_fundamentals", "is_true"))
    if any(phrase in lower for phrase in ("recent filing", "recent filings", "sec filing", "sec filings", "filings")):
        filters.append(AskScreenerFilter("has_recent_filing", "is_true"))
        sort = AskScreenerSort("recent_filing_date", descending=True)
    if "earning" in lower and any(phrase in lower for phrase in ("soon", "upcoming", "next", "calendar")):
        filters.append(AskScreenerFilter("next_earnings_date", "within_next_days", 30))
        sort = AskScreenerSort("next_earnings_date", descending=False)
    if any(phrase in lower for phrase in ("highest volume", "highest-volume", "top volume", "most volume", "largest volume", "volume today")):
        filters.append(AskScreenerFilter("volume", "exists"))
        sort = AskScreenerSort("volume", descending=True)
        explicit_required_fields.extend(("price", "volume"))
    if any(phrase in lower for phrase in ("high volume", "volume mover", "mover", "big volume")):
        filters.append(AskScreenerFilter("high_volume_mover", "is_true"))
        sort = AskScreenerSort("volume", descending=True)
    if "momentum" in lower:
        filters.append(AskScreenerFilter("momentum_proxy", "is_true"))
        sort = AskScreenerSort("change_percent", descending=True)
        explicit_required_fields.extend(("change_percent", "volume", "avg_volume"))
    if any(phrase in lower for phrase in ("recent catalyst", "recent catalysts", "catalyst", "catalysts", "ai-worthy", "ai worthy")):
        filters.append(AskScreenerFilter("recent_catalyst", "is_true"))
        sort = sort or AskScreenerSort("recent_filing_date", descending=True)
        explicit_required_fields.extend(("recent_filing_date", "next_earnings_date"))
    if "small cap" in lower or "small-cap" in lower:
        filters.append(AskScreenerFilter("market_cap", "lt", _env_float(ASK_SCREENER_SMALL_CAP_MAX_MARKET_CAP_ENV, DEFAULT_ASK_SCREENER_SMALL_CAP_MAX_MARKET_CAP, minimum=1.0, maximum=1_000_000_000_000.0)))
        sort = sort or AskScreenerSort("market_cap", descending=False)
    if "penny stock" in lower or "penny stocks" in lower:
        filters.append(AskScreenerFilter("price", "lt", _env_float(ASK_SCREENER_PENNY_STOCK_MAX_PRICE_ENV, DEFAULT_ASK_SCREENER_PENNY_STOCK_MAX_PRICE, minimum=0.01, maximum=100.0)))
        sort = sort or AskScreenerSort("price", descending=False)
    if any(phrase in lower for phrase in ("missing price", "missing prices", "no price", "without price", "blank price")):
        filters.append(AskScreenerFilter("price", "missing"))
    elif any(phrase in lower for phrase in ("missing data", "blank data", "missing fields", "blank fields", "incomplete data")):
        filters.append(AskScreenerFilter("has_missing_data", "is_true"))
    if any(phrase in lower for phrase in ("positive revenue growth", "revenue growth positive", "growing revenue")):
        filters.append(AskScreenerFilter("revenue_growth", "gt", 0))
        sort = AskScreenerSort("revenue_growth", descending=True)
    if any(phrase in lower for phrase in ("negative eps", "eps negative", "loss-making", "loss making")):
        filters.append(AskScreenerFilter("eps", "lt", 0))
        sort = AskScreenerSort("eps", descending=False)

    filters.extend(_ask_screener_sector_exchange_filters(lower, records))
    if not filters and sort is None:
        return None
    try:
        return validate_ask_screener_plan(
            AskScreenerPlan(
                filters=tuple(filters),
                sort=sort,
                limit=limit,
                intent=intent,
                required_fields=tuple(explicit_required_fields),
            )
        )
    except AskScreenerPlanValidationError:
        return None


def ask_screener_planner_request_payload(
    question: str,
    records: Iterable[MarketScreenerRecord],
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    payload = {
        "question": _clean_prompt(question),
        "response_contract": {
            "json_only": True,
            "shape": {
                "intent": "short description",
                "filters": [{"field": "allowed_field", "operator": "allowed_operator", "value": "operator-specific value"}],
                "sort": {"field": "allowed_field", "descending": True},
                "limit": ASK_SCREENER_DEFAULT_LIMIT,
                "clear_filters": False,
                "required_fields": ["fields needed to execute filters/sort"],
                "provider_enrichment_needed": True,
            },
        },
        "schema": _ask_screener_schema_payload(),
        "snapshot_metadata": ask_screener_snapshot_metadata(records),
        "safety_rules": [
            "Return JSON only.",
            "Do not include source excerpts, source links, API keys, credentials, account identifiers, or secrets.",
            "Do not request or emit all screener rows.",
            "Do not infer missing market data; use missing/exists filters when data is absent.",
            "'up to N' means limit=N.",
            "'Technology / Electronics' means classification contains any of technology/electronic across sector, industry, or profile classification text.",
            "'highest volume today' means sort by volume descending and require price/volume data.",
            "'momentum' means positive change_percent, volume present, and volume above avg_volume when avg_volume is available.",
            "'recent catalysts' means recent filing, upcoming earnings, guidance, risk flags, or AI-worthy deterministic signals.",
            "No buy/sell/hold recommendations and no trade actions.",
        ],
        "request_budget": {
            "request_payload_char_limit": ASK_SCREENER_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": timeout_seconds,
        },
    }
    return _enforce_ask_screener_planner_budget(_json_safe(payload))


def ask_screener_snapshot_metadata(records: Iterable[MarketScreenerRecord]) -> dict[str, Any]:
    rows = list(records)
    return {
        "total_rows": len(rows),
        "categorical_counts": {
            "sector": _top_counts(record.sector or MISSING_TEXT for record in rows),
            "exchange": _top_counts(record.exchange or MISSING_TEXT for record in rows),
            "data_label": _top_counts(market_screener_data_label(record) for record in rows),
            "signals": _top_counts(signal for record in rows for signal in record.signals),
            "risk_flags": _top_counts(flag for record in rows for flag in record.risk_flags),
        },
        "boolean_counts": {
            field: {
                "true": sum(1 for record in rows if bool(_ask_screener_field_value(record, field))),
                "false": sum(1 for record in rows if not bool(_ask_screener_field_value(record, field))),
            }
            for field in sorted(ASK_SCREENER_BOOLEAN_FIELDS)
        },
        "numeric_ranges": {
            field: _numeric_metadata(rows, field)
            for field in sorted(ASK_SCREENER_NUMERIC_FIELDS)
        },
        "date_ranges": {
            field: _date_metadata(rows, field)
            for field in sorted(ASK_SCREENER_DATE_FIELDS)
        },
        "field_coverage": {
            row.field: {
                "available_count": row.available_count,
                "missing_count": row.missing_count,
                "coverage_ratio": round(row.coverage_ratio, 4),
            }
            for row in analyze_ask_screener_field_coverage(rows)
        },
        "omitted_row_data": "Full screener rows, symbols, company names, source excerpts, and source links are intentionally not included.",
    }


def _validate_ask_screener_filter(payload: Any) -> AskScreenerFilter:
    if isinstance(payload, AskScreenerFilter):
        payload = asdict(payload)
    if not isinstance(payload, Mapping):
        raise AskScreenerPlanValidationError("Each Ask Screener filter must be an object.")
    field = _normalize_ask_screener_field(payload.get("field"))
    operator = _normalize_ask_screener_operator(payload.get("operator") or payload.get("op"))
    _validate_ask_screener_operator(field, operator)
    value = _sanitize_ask_screener_value(field, operator, payload.get("value"))
    return AskScreenerFilter(field=field, operator=operator, value=value)


def _validate_ask_screener_sort(payload: Any) -> AskScreenerSort | None:
    if payload in (None, "", False):
        return None
    if isinstance(payload, AskScreenerSort):
        payload = asdict(payload)
    if isinstance(payload, str):
        return AskScreenerSort(_normalize_ask_screener_field(payload), descending=False)
    if not isinstance(payload, Mapping):
        raise AskScreenerPlanValidationError("Ask Screener sort must be an object or field name.")
    field = _normalize_ask_screener_field(payload.get("field"))
    raw_direction = str(payload.get("direction") or "").strip().lower()
    descending = _coerce_bool(payload.get("descending"), default=raw_direction in {"desc", "descending", "down"})
    return AskScreenerSort(field=field, descending=descending)


def _validate_ask_screener_limit(value: Any) -> int:
    if value in (None, ""):
        return ASK_SCREENER_DEFAULT_LIMIT
    try:
        parsed = int(float(str(value).strip()))
    except (TypeError, ValueError):
        raise AskScreenerPlanValidationError("Ask Screener limit must be a number.") from None
    return max(1, min(ASK_SCREENER_MAX_LIMIT, parsed))


def _normalize_ask_screener_field(value: Any) -> str:
    field = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    field = ASK_SCREENER_FIELD_ALIASES.get(field, field)
    if field not in ASK_SCREENER_ALLOWED_FIELDS:
        raise AskScreenerPlanValidationError(f"Unsupported Ask Screener field: {field or '(blank)'}.")
    return field


def _normalize_ask_screener_operator(value: Any) -> str:
    operator = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    operator = ASK_SCREENER_OPERATOR_ALIASES.get(operator, operator)
    if not operator:
        raise AskScreenerPlanValidationError("Ask Screener filter operator is required.")
    return operator


def _validate_ask_screener_operator(field: str, operator: str) -> None:
    allowed = _allowed_ask_screener_operators(field)
    if operator not in allowed:
        raise AskScreenerPlanValidationError(f"Unsupported operator {operator!r} for Ask Screener field {field!r}.")


def _allowed_ask_screener_operators(field: str) -> set[str]:
    if field in ASK_SCREENER_TEXT_FIELDS:
        return ASK_SCREENER_TEXT_OPERATORS
    if field in ASK_SCREENER_NUMERIC_FIELDS:
        return ASK_SCREENER_NUMERIC_OPERATORS
    if field in ASK_SCREENER_DATE_FIELDS:
        return ASK_SCREENER_DATE_OPERATORS
    if field in ASK_SCREENER_BOOLEAN_FIELDS:
        return ASK_SCREENER_BOOLEAN_OPERATORS
    return set()


def _sanitize_ask_screener_value(field: str, operator: str, value: Any) -> Any:
    if operator in {"exists", "missing", "is_true", "is_false"}:
        return None
    if operator in {"in", "not_in", "contains_any"}:
        if not isinstance(value, (list, tuple, set)):
            value = [value]
        values = [_sanitize_ask_screener_scalar_value(field, operator, item) for item in value]
        return tuple(item for item in values[:20] if item not in (None, ""))
    return _sanitize_ask_screener_scalar_value(field, operator, value)


def _sanitize_ask_screener_scalar_value(field: str, operator: str, value: Any) -> Any:
    if field in ASK_SCREENER_NUMERIC_FIELDS:
        number = _float_or_none(value)
        if number is None:
            raise AskScreenerPlanValidationError(f"Ask Screener field {field!r} requires a numeric value.")
        return number
    if field in ASK_SCREENER_DATE_FIELDS:
        if operator == "within_next_days":
            number = _float_or_none(value)
            if number is None:
                raise AskScreenerPlanValidationError("within_next_days requires a numeric day count.")
            return max(0, min(365, int(number)))
        text = _clean_prompt(str(value or ""))[:40]
        if _parse_date(text) is None:
            raise AskScreenerPlanValidationError(f"Ask Screener field {field!r} requires a YYYY-MM-DD date value.")
        return text[:10]
    if field in ASK_SCREENER_BOOLEAN_FIELDS:
        parsed = _bool_or_none(value)
        if parsed is None:
            raise AskScreenerPlanValidationError(f"Ask Screener field {field!r} requires a boolean value.")
        return parsed
    return _shorten(_clean_prompt(str(value or "")), 120)


def _ask_screener_filter_matches(record: MarketScreenerRecord, filter_row: AskScreenerFilter, *, today: date) -> bool:
    value = _ask_screener_field_value(record, filter_row.field)
    operator = filter_row.operator
    if operator == "exists":
        return _has_value(value)
    if operator == "missing":
        return not _has_value(value)
    if operator == "is_true":
        return bool(value)
    if operator == "is_false":
        return not bool(value)
    if filter_row.field in ASK_SCREENER_NUMERIC_FIELDS:
        return _numeric_filter_matches(value, operator, filter_row.value)
    if filter_row.field in ASK_SCREENER_DATE_FIELDS:
        return _date_filter_matches(value, operator, filter_row.value, today=today)
    if filter_row.field in ASK_SCREENER_BOOLEAN_FIELDS:
        return _boolean_filter_matches(value, operator, filter_row.value)
    return _text_filter_matches(value, operator, filter_row.value)


def _ask_screener_field_value(record: MarketScreenerRecord, field: str) -> Any:
    if field == "classification":
        return tuple(value for value in (record.sector, record.industry, record.exchange, record.recent_filing_type) if value)
    if field == "data_label":
        return market_screener_data_label(record)
    if field == "data_completeness_score":
        return market_screener_data_completeness_score(record)
    if field == "is_my_holding":
        return market_screener_is_my_holding(record)
    if field == "has_market_data":
        return market_screener_record_has_market_data(record)
    if field == "has_quote":
        return market_screener_record_has_quote_fields(record)
    if field == "has_fundamentals":
        return market_screener_record_has_fundamentals(record)
    if field == "has_recent_filing":
        return bool(record.recent_filing_date)
    if field == "has_upcoming_earnings":
        return bool(record.next_earnings_date)
    if field == "high_volume_mover":
        return _event_type_matches(record, "High volume / mover")
    if field == "momentum_proxy":
        if record.change_percent is None or record.change_percent <= 0 or record.volume is None:
            return False
        if record.avg_volume in (None, 0):
            return True
        return record.volume > record.avg_volume
    if field == "recent_catalyst":
        signal_text = " ".join(record.signals).lower()
        return bool(
            record.recent_filing_date
            or record.next_earnings_date
            or record.risk_flags
            or record.signals
            or "guidance" in signal_text
        )
    if field == "missing_price_data":
        return record.price is None
    if field == "has_positive_revenue_growth":
        return record.revenue_growth is not None and record.revenue_growth > 0
    if field == "has_negative_eps":
        return record.eps is not None and record.eps < 0
    if field == "has_missing_data":
        return any(not _has_value(_ask_screener_field_value(record, item)) for item in ASK_SCREENER_PROVIDER_AWARE_FIELDS)
    return getattr(record, field, None)


def _numeric_filter_matches(value: Any, operator: str, target: Any) -> bool:
    number = _float_or_none(value)
    target_number = _float_or_none(target)
    if number is None or target_number is None:
        return False
    if operator == "eq":
        return number == target_number
    if operator == "neq":
        return number != target_number
    if operator == "gt":
        return number > target_number
    if operator == "gte":
        return number >= target_number
    if operator == "lt":
        return number < target_number
    if operator == "lte":
        return number <= target_number
    return False


def _date_filter_matches(value: Any, operator: str, target: Any, *, today: date) -> bool:
    event_date = _parse_date(str(value or ""))
    if event_date is None:
        return False
    if operator == "within_next_days":
        days = int(target or 0)
        delta = (event_date - today).days
        return 0 <= delta <= days
    target_date = _parse_date(str(target or ""))
    if target_date is None:
        return False
    if operator == "eq":
        return event_date == target_date
    if operator == "neq":
        return event_date != target_date
    if operator == "gt":
        return event_date > target_date
    if operator == "gte":
        return event_date >= target_date
    if operator == "lt":
        return event_date < target_date
    if operator == "lte":
        return event_date <= target_date
    return False


def _boolean_filter_matches(value: Any, operator: str, target: Any) -> bool:
    actual = bool(value)
    expected = bool(target)
    if operator == "eq":
        return actual == expected
    if operator == "neq":
        return actual != expected
    return False


def _text_filter_matches(value: Any, operator: str, target: Any) -> bool:
    values = _ask_screener_text_values(value)
    targets = tuple(_ask_screener_text_values(target if isinstance(target, (list, tuple, set)) else (target,)))
    if not targets:
        return False
    if operator == "eq":
        return any(value_text == targets[0] for value_text in values)
    if operator == "neq":
        return all(value_text != targets[0] for value_text in values)
    if operator in {"contains", "contains_any"}:
        return any(target in value_text for value_text in values for target in targets)
    if operator == "not_contains":
        return all(target not in value_text for value_text in values for target in targets)
    if operator == "in":
        return any(value_text in targets for value_text in values)
    if operator == "not_in":
        return all(value_text not in targets for value_text in values)
    return False


def _ask_screener_text_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        return tuple(_clean_prompt(str(item or "")).lower() for item in value if _clean_prompt(str(item or "")))
    text = _clean_prompt(str(value or "")).lower()
    return (text,) if text else ()


def _sort_ask_screener_records(records: Iterable[MarketScreenerRecord], sort: AskScreenerSort | None) -> list[MarketScreenerRecord]:
    rows = list(records)
    if sort is None:
        return rows

    def sort_value(record: MarketScreenerRecord) -> Any:
        value = _ask_screener_field_value(record, sort.field)
        if isinstance(value, (list, tuple, set)):
            return len(value)
        if sort.field in ASK_SCREENER_TEXT_FIELDS:
            text_values = _ask_screener_text_values(value)
            return text_values[0] if text_values else None
        if sort.field in ASK_SCREENER_DATE_FIELDS:
            return _parse_date(str(value or ""))
        return value

    present = [record for record in rows if _has_value(sort_value(record))]
    missing = [record for record in rows if not _has_value(sort_value(record))]
    present.sort(key=sort_value, reverse=sort.descending)
    return present + missing


def _ask_screener_filter_label(filter_row: AskScreenerFilter) -> str:
    if filter_row.operator in {"exists", "missing", "is_true", "is_false"}:
        return f"{filter_row.field} {filter_row.operator}"
    return f"{filter_row.field} {filter_row.operator} {filter_row.value}"


def _parse_ask_screener_limit(lower: str) -> int | None:
    match = re.search(r"\b(?:top|first|limit|show|up to|upto)\s+(\d{1,4})\b", lower)
    if not match:
        return None
    return _validate_ask_screener_limit(match.group(1))


def _ask_screener_sector_exchange_filters(lower: str, records: Iterable[MarketScreenerRecord]) -> tuple[AskScreenerFilter, ...]:
    rows = list(records)
    filters: list[AskScreenerFilter] = []
    sectors = _metadata_labels(record.sector for record in rows)
    industries = _metadata_labels(record.industry for record in rows)
    exchanges = _metadata_labels(record.exchange for record in rows)
    common_exchanges = {"nasdaq": "NASDAQ", "nyse": "NYSE", "amex": "AMEX", "arca": "NYSE Arca"}
    classification_terms = _ask_screener_classification_terms(lower, (*sectors, *industries))
    if classification_terms:
        filters.append(AskScreenerFilter("classification", "contains_any", classification_terms))
        return tuple(filters)
    for label in sorted(sectors, key=len, reverse=True):
        label_lower = label.lower()
        if label_lower != MISSING_TEXT.lower() and (f"sector {label_lower}" in lower or f"{label_lower} sector" in lower or f"in {label_lower}" in lower):
            filters.append(AskScreenerFilter("sector", "eq", label))
            break
    for label in sorted((*exchanges, *common_exchanges.values()), key=len, reverse=True):
        label_lower = label.lower()
        alias_hit = any(alias in lower for alias, value in common_exchanges.items() if value.lower() == label_lower)
        if label_lower != MISSING_TEXT.lower() and (alias_hit or f"exchange {label_lower}" in lower or f"on {label_lower}" in lower):
            filters.append(AskScreenerFilter("exchange", "eq", label))
            break
    return tuple(filters)


def _ask_screener_classification_terms(lower: str, labels: Iterable[str]) -> tuple[str, ...]:
    terms: list[str] = []
    if "technology" in lower or "tech " in f"{lower} " or " tech" in lower:
        terms.append("technology")
    if "electronics" in lower or "electronic" in lower:
        terms.append("electronic")
    for label in labels:
        clean = _clean_prompt(label)
        if clean and clean.lower() in lower:
            terms.append(clean)
    return tuple(_dedupe_texts(term.lower() for term in terms if term))


def _metadata_labels(values: Iterable[Any]) -> tuple[str, ...]:
    labels = []
    for value in values:
        text = _clean_prompt(str(value or ""))
        if text:
            labels.append(text)
    return tuple(dict.fromkeys(labels))


def _ask_screener_schema_payload() -> dict[str, Any]:
    return {
        "fields": {
            "text": sorted(ASK_SCREENER_TEXT_FIELDS),
            "numeric": sorted(ASK_SCREENER_NUMERIC_FIELDS),
            "date": sorted(ASK_SCREENER_DATE_FIELDS),
            "boolean": sorted(ASK_SCREENER_BOOLEAN_FIELDS),
        },
        "operators": {
            "text": sorted(ASK_SCREENER_TEXT_OPERATORS),
            "numeric": sorted(ASK_SCREENER_NUMERIC_OPERATORS),
            "date": sorted(ASK_SCREENER_DATE_OPERATORS),
            "boolean": sorted(ASK_SCREENER_BOOLEAN_OPERATORS),
        },
        "provider_aware_fields": sorted(ASK_SCREENER_PROVIDER_AWARE_FIELDS),
        "derived_fields": {
            "classification": "OR text over sector, industry, exchange, and profile classification text",
            "momentum_proxy": "change_percent > 0, volume present, and volume > avg_volume when avg_volume is available",
            "recent_catalyst": "recent filing, upcoming earnings, guidance, risk flags, or deterministic AI-worthy signals",
            "has_missing_data": "one or more provider-aware fields is blank",
        },
        "max_filters": ASK_SCREENER_MAX_FILTERS,
        "max_limit": ASK_SCREENER_MAX_LIMIT,
    }


def _top_counts(values: Iterable[Any], *, limit: int = 30) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for value in values:
        text = _shorten(_clean_prompt(str(value or "")), 80)
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    return [
        {"value": label, "count": count}
        for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _numeric_metadata(records: Iterable[MarketScreenerRecord], field: str) -> dict[str, Any]:
    rows = list(records)
    values = [_float_or_none(_ask_screener_field_value(record, field)) for record in rows]
    present = [value for value in values if value is not None]
    payload: dict[str, Any] = {"available_count": len(present), "missing_count": len(rows) - len(present)}
    if present:
        payload["min"] = min(present)
        payload["max"] = max(present)
    return payload


def _date_metadata(records: Iterable[MarketScreenerRecord], field: str) -> dict[str, Any]:
    rows = list(records)
    values = [_parse_date(str(_ask_screener_field_value(record, field) or "")) for record in rows]
    present = [value for value in values if value is not None]
    payload: dict[str, Any] = {"available_count": len(present), "missing_count": len(rows) - len(present)}
    if present:
        payload["min"] = min(present).isoformat()
        payload["max"] = max(present).isoformat()
    return payload


def _enforce_ask_screener_planner_budget(payload: dict[str, Any]) -> dict[str, Any]:
    if len(_serialize_request_payload(payload)) <= ASK_SCREENER_REQUEST_CHAR_LIMIT:
        return payload
    metadata = payload.get("snapshot_metadata")
    if isinstance(metadata, dict):
        categorical = metadata.get("categorical_counts")
        if isinstance(categorical, dict):
            for key, rows in list(categorical.items()):
                if isinstance(rows, list):
                    categorical[key] = rows[:12]
        payload["snapshot_metadata"] = metadata
    if len(_serialize_request_payload(payload)) > ASK_SCREENER_REQUEST_CHAR_LIMIT and isinstance(metadata, dict):
        metadata["numeric_ranges"] = {}
        metadata["date_ranges"] = {}
    payload["request_budget"]["budget_trimmed"] = True
    payload["request_budget"]["final_payload_chars"] = len(_serialize_request_payload(payload))
    return payload


def _parse_json_object(value: str) -> Mapping[str, Any]:
    text = str(value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AskScreenerPlanValidationError(f"Model did not return valid JSON: {exc.msg}.") from None
    if not isinstance(payload, Mapping):
        raise AskScreenerPlanValidationError("Model JSON must be an object.")
    return payload


def market_screener_ai_request_payload(
    record: MarketScreenerRecord,
    prompt: str,
    *,
    source_snippets: Iterable[str] | None = None,
    cross_asset_context: Any | None = None,
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
            "Use field_provenance to label where selected-row fields came from; do not use it to infer missing values.",
            "Use cross_asset_context only as separate CME/futures/options or macro context when it is explicitly present; do not treat it as selected-equity facts.",
            "Keep the answer research-only. Do not place trades, submit orders, automate broker actions, or tell the user to buy, sell, or hold.",
            "Separate confirmed selected-row facts from assumptions, caveats, missing data, and diligence questions.",
        ],
        "request_budget": {
            "request_payload_char_limit": MARKET_SCREENER_REQUEST_CHAR_LIMIT,
            "openai_timeout_seconds": timeout_seconds,
        },
    }
    if cross_asset_context:
        payload["cross_asset_context"] = _cross_asset_context_payload(cross_asset_context)
    return _enforce_request_payload_budget(payload)


def _cross_asset_context_payload(value: Any) -> Any:
    if hasattr(value, "records") and not isinstance(value, Mapping):
        records = getattr(value, "records", ()) or ()
        payload = {
            "scope": "cross_asset_context_only",
            "use_policy": "CME/futures/options or macro context only; not selected-equity market data.",
            "records": [_json_safe(_object_to_mapping(record)) for record in records],
        }
        statuses = getattr(value, "statuses", ()) or ()
        if statuses:
            payload["statuses"] = [_json_safe(_object_to_mapping(status)) for status in statuses]
        return _redact_context_payload(payload)
    if isinstance(value, Mapping):
        payload = dict(value)
    elif isinstance(value, (list, tuple)):
        payload = list(value)
    else:
        payload = _object_to_mapping(value)
    return _redact_context_payload(_json_safe(payload))


def market_screener_record_context(record: MarketScreenerRecord) -> dict[str, Any]:
    completeness = market_screener_data_completeness(record)
    fields = {
        "symbol": _text_or_missing(record.symbol, "symbol"),
        "company_name": _text_or_missing(record.company_name, "company_name"),
        "cik": _text_or_missing(record.cik, "cik"),
        "exchange": _text_or_missing(record.exchange, "exchange"),
        "sector": _text_or_missing(record.sector, "sector"),
        "industry": _text_or_missing(record.industry, "industry"),
        "data_label": market_screener_data_label(record),
        "data_completeness": {
            "score": completeness["score"],
            "label": completeness["label"],
            "present_fields": list(completeness["present_fields"]),
            "missing_fields": list(completeness["missing_fields"]),
        },
        "portfolio_context": {
            "is_my_holding": market_screener_is_my_holding(record),
            "quantity": _number_or_missing(record.portfolio_quantity, "portfolio_quantity"),
            "average_cost": _number_or_missing(record.portfolio_average_cost, "portfolio_average_cost"),
            "market_value": _number_or_missing(record.portfolio_market_value, "portfolio_market_value"),
            "unrealized_pnl": _number_or_missing(record.portfolio_unrealized_pnl, "portfolio_unrealized_pnl"),
            "portfolio_weight": _number_or_missing(record.portfolio_weight, "portfolio_weight"),
            "source": "Local Schwab holdings" if market_screener_is_my_holding(record) else _not_available("The selected row is not marked as a loaded Schwab holding."),
        },
        "market_data": {
            "price": _number_or_missing(record.price, "price"),
            "market_cap": _number_or_missing(record.market_cap, "market_cap"),
            "market_cap_currency": _text_or_missing(record.market_cap_currency, "market_cap_currency"),
            "market_cap_ranking": market_screener_market_cap_rank(record).to_dict(),
            "volume": _number_or_missing(record.volume, "volume"),
            "avg_volume": _number_or_missing(record.avg_volume, "avg_volume"),
            "change_percent": _number_or_missing(record.change_percent, "change_percent"),
            "pe_ratio": _number_or_missing(record.pe_ratio, "pe_ratio"),
        },
        "fundamental_fields": {
            "eps": _number_or_missing(record.eps, "eps"),
            "revenue_growth_percent": _number_or_missing(record.revenue_growth, "revenue_growth"),
            "shares_float": _number_or_missing(record.shares_float, "shares_float"),
            "shares_outstanding": _number_or_missing(record.shares_outstanding, "shares_outstanding"),
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
        "field_provenance": _field_provenance_payload(record),
        "fetched_at": _text_or_missing(record.fetched_at, "fetched_at"),
        "missing_fields": _missing_fields(record),
        "missing_field_reasons": market_screener_record_missing_reason_lines(record),
        "analysis_policy": {
            "scope": "selected Market Intelligence Screener row only",
            "research_only": True,
            "trading_instructions_allowed": False,
        },
    }
    return _json_safe(fields)


def market_screener_record_missing_reason_lines(record: MarketScreenerRecord) -> list[str]:
    reasons: list[str] = []
    if not _normalize_cik(record.cik):
        reasons.append("missing CIK: SEC CIK/ticker and submissions metadata cannot resolve this row without a trusted CIK.")
    if not _normalize_symbol(record.symbol):
        reasons.append("missing ticker: no trusted SEC/provider symbol is present; symbol was not guessed from company name.")
    profile_missing = _missing_named_fields(record, ("exchange", "sector", "industry"))
    if profile_missing:
        reasons.append(
            f"missing profile fields: {', '.join(profile_missing)}; requires FMP profile/profile-by-CIK, Databento reference/security-master fields when configured, local seed data, or configured fallback profile data. "
            "SEC metadata is candidate context only for Market Screener chart fields; Databento CME context does not supply selected-equity profile fields."
        )
    tape_missing = _missing_named_fields(record, ("price", "volume", "change_percent", "avg_volume"))
    if tape_missing:
        reasons.append(
            f"missing quote/tape fields: {', '.join(tape_missing)}; requires Schwab quote, Databento US Equities with an entitled intraday equity tape schema, local file/cache, FMP quote, or configured fallback quote data. "
            "Databento CME context is unsupported for selected-equity quote columns."
        )
    fundamental_missing = _missing_named_fields(record, ("market_cap", "pe_ratio", "eps", "revenue_growth", "shares_float", "shares_outstanding"))
    if fundamental_missing:
        reasons.append(
            f"missing FMP/profile fundamental fields: {', '.join(fundamental_missing)}; requires local seed data, FMP quote/profile/market-cap/key-metrics/ratios/income-growth/financial-growth/income-statement/shares-float endpoints, Databento shares outstanding where available, or configured fallback profile data. "
            "SEC filing data is not used for Market Screener chart fundamentals; Databento CME context is unsupported for selected-equity fundamentals."
        )
    rank = market_screener_market_cap_rank(record)
    if record.market_cap is not None and not rank.trusted:
        reasons.append(f"market cap ranking not trusted: {rank.reason}")
    elif record.market_cap is not None and rank.category != "us_primary_common":
        reasons.append(f"market cap ranking demoted: {rank.reason}")
    return reasons


def _missing_named_fields(record: MarketScreenerRecord, fields: Iterable[str]) -> list[str]:
    return [field for field in fields if getattr(record, field, None) in (None, "", ())]


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

    client = provider or configured_upcoming_earnings_provider()
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


def _load_sec_cik_identity_records(
    client: SecEdgarClient,
    universe: Iterable[MarketUniverseEntry],
    recent: Iterable[RecentEarningsRecord],
    supplemental: Iterable[MarketScreenerRecord],
    statuses: list[MarketScreenerSourceStatus],
    errors: list[str],
    fetched_at: str,
) -> tuple[MarketUniverseEntry, ...]:
    existing_by_cik: dict[str, list[str]] = {}
    for entry in universe:
        cik = _normalize_cik(entry.cik)
        symbol = _normalize_symbol(entry.symbol)
        if cik and symbol:
            existing_by_cik.setdefault(cik, []).append(symbol)

    requested_ciks = _ciks_needing_identity(recent, supplemental)
    unresolved_ciks = tuple(cik for cik in requested_ciks if cik not in existing_by_cik)
    if not unresolved_ciks:
        statuses.append(
            MarketScreenerSourceStatus(
                "SEC CIK/ticker identity",
                "available" if requested_ciks else "empty",
                fetched_at,
                f"Resolved 0 additional symbol(s); {len(requested_ciks)} CIK row(s) were already covered by loaded universe rows.",
            )
        )
        return ()

    try:
        payload = client._fetch_json(SEC_TICKER_URL, cache_name="company_tickers.json", ttl=TICKER_CACHE_TTL)
    except Exception as exc:
        errors.append(f"SEC CIK/ticker identity: {exc}")
        statuses.append(
            MarketScreenerSourceStatus(
                "SEC CIK/ticker identity",
                "unavailable",
                fetched_at,
                f"SEC CIK/ticker identity map unavailable: {exc}",
            )
        )
        return ()
    if not isinstance(payload, dict):
        statuses.append(
            MarketScreenerSourceStatus(
                "SEC CIK/ticker identity",
                "unavailable",
                fetched_at,
                "SEC CIK/ticker identity map returned an unexpected response shape.",
            )
        )
        return ()

    by_cik: dict[str, list[MarketUniverseEntry]] = {}
    for item in payload.values():
        if not isinstance(item, Mapping):
            continue
        cik = _normalize_cik(item.get("cik_str"))
        symbol = _normalize_symbol(item.get("ticker"))
        if not cik or not symbol or cik not in unresolved_ciks:
            continue
        by_cik.setdefault(cik, []).append(
            MarketUniverseEntry(
                symbol=symbol,
                company_name=_optional_text(item.get("title")),
                cik=cik,
                source="SEC CIK/ticker identity",
                source_url=SEC_TICKER_URL,
            )
        )

    resolved: list[MarketUniverseEntry] = []
    ambiguous = 0
    for cik in unresolved_ciks:
        candidates = by_cik.get(cik, [])
        symbols = sorted({_normalize_symbol(entry.symbol) for entry in candidates if _normalize_symbol(entry.symbol)})
        if len(symbols) != 1:
            if len(symbols) > 1:
                ambiguous += 1
            continue
        selected = next(entry for entry in candidates if _normalize_symbol(entry.symbol) == symbols[0])
        resolved.append(selected)
    status = "available" if resolved else "empty"
    statuses.append(
        MarketScreenerSourceStatus(
            "SEC CIK/ticker identity",
            status,
            fetched_at,
            (
                f"Resolved {len(resolved)} symbol(s) by exact SEC CIK match; "
                f"{len(unresolved_ciks) - len(resolved)} CIK row(s) remain unresolved"
                + (f"; {ambiguous} ambiguous multi-ticker CIK row(s) were not guessed. " if ambiguous else ". ")
                + SEC_CHART_FIELD_SOURCE_WARNING
            ),
        )
    )
    return tuple(resolved)


def _load_sec_submission_metadata_records(
    client: SecEdgarClient,
    records: Iterable[MarketScreenerRecord],
    statuses: list[MarketScreenerSourceStatus],
    errors: list[str],
    fetched_at: str,
    *,
    max_ciks: int,
) -> tuple[MarketScreenerRecord, ...]:
    candidate_ciks = _sec_submission_candidate_ciks(records, max_ciks)
    if not candidate_ciks:
        statuses.append(
            MarketScreenerSourceStatus(
                "SEC submissions metadata",
                "empty",
                fetched_at,
                f"No capped filing CIK rows needed SEC submissions identity context. {SEC_CHART_FIELD_SOURCE_WARNING}",
            )
        )
        return ()

    enriched: list[MarketScreenerRecord] = []
    metadata_errors = 0
    for cik in candidate_ciks:
        try:
            payload = client.get_submissions_by_cik(cik)
        except Exception as exc:
            errors.append(f"{cik} SEC submissions metadata: {exc}")
            metadata_errors += 1
            continue
        record = _record_from_sec_submission_metadata(cik, payload, fetched_at=fetched_at)
        if record is not None:
            enriched.append(record)
    status = "available" if enriched else "empty"
    if not enriched and metadata_errors:
        status = "unavailable"
    statuses.append(
        MarketScreenerSourceStatus(
            "SEC submissions metadata",
            status,
            fetched_at,
            f"Loaded SEC submissions identity context for {len(enriched)} of {len(candidate_ciks)} capped CIK row(s). {SEC_CHART_FIELD_SOURCE_WARNING}",
        )
    )
    return tuple(enriched)


def _load_market_data_records(
    provider: Any | None,
    provided: Iterable[MarketQuoteFundamentalsRecord] | None,
    statuses: list[MarketScreenerSourceStatus],
    errors: list[str],
    fetched_at: str,
    symbols: Iterable[str],
    force_refresh: bool,
    max_symbols: int,
    provider_diagnostics: dict[str, int],
) -> tuple[MarketQuoteFundamentalsRecord, ...]:
    if max_symbols <= 0:
        statuses.append(
            MarketScreenerSourceStatus(
                "Market quote/fundamental provider",
                "disabled",
                fetched_at,
                "Market quote/fundamental enrichment is disabled because the symbol cap is 0.",
            )
        )
        provider_diagnostics["rows_skipped_by_configured_symbol_cap"] = provider_diagnostics.get("rows_skipped_by_configured_symbol_cap", 0) + len(tuple(symbols))
        return ()
    requested = tuple(symbols)
    if provided is not None:
        rows = tuple(provided)
        statuses.append(
            MarketScreenerSourceStatus(
                "Market quote/fundamental records",
                "available" if rows else "empty",
                fetched_at,
                f"Merged {len(rows)} supplied quote/fundamental row(s).",
            )
        )
        return rows
    active_provider = provider or configured_market_data_provider()
    try:
        snapshot = active_provider.quote_fundamentals(
            requested,
            force_refresh=force_refresh,
            max_symbols=max_symbols,
        )
    except Exception as exc:
        errors.append(f"Market quote/fundamental provider: {exc}")
        statuses.append(
            MarketScreenerSourceStatus(
                "Market quote/fundamental provider",
                "error",
                fetched_at,
                f"Market quote/fundamental provider failed: {exc}",
            )
        )
        return ()

    statuses.extend(MarketScreenerSourceStatus(status.source, status.status, status.fetched_at, status.message) for status in snapshot.statuses)
    errors.extend(snapshot.errors)
    _merge_counter_mapping(provider_diagnostics, snapshot.diagnostics)
    if requested:
        provider_diagnostics["provider_rows_requested"] = max(provider_diagnostics.get("provider_rows_requested", 0), len(requested))
        provider_diagnostics["provider_calls_attempted"] = max(provider_diagnostics.get("provider_calls_attempted", 0), 1)
    return tuple(snapshot.records)


def _load_provider_filing_metadata_records(
    provider: Any | None,
    records: Iterable[MarketScreenerRecord],
    statuses: list[MarketScreenerSourceStatus],
    errors: list[str],
    fetched_at: str,
    force_refresh: bool,
    max_symbols: int,
    provider_diagnostics: dict[str, int],
) -> tuple[MarketScreenerRecord, ...]:
    if provider is None or max_symbols <= 0:
        return ()
    filing_method = getattr(provider, "sec_filings", None) or getattr(provider, "filing_metadata", None)
    if not callable(filing_method):
        return ()
    candidate_rows = [record for record in records if not record.recent_filing_date]
    requested = _market_data_candidate_symbols(candidate_rows, max_symbols)
    if not requested:
        statuses.append(
            MarketScreenerSourceStatus(
                "FMP SEC filings by symbol",
                "empty",
                fetched_at,
                "No capped symbol-bearing rows needed FMP SEC filing metadata fallback.",
            )
        )
        return ()

    enriched: list[MarketScreenerRecord] = []
    failures = 0
    for symbol in requested:
        try:
            rows = filing_method(symbol, force_refresh=force_refresh, limit=1)
        except TypeError:
            rows = filing_method(symbol, limit=1)
        except Exception as exc:
            clean_error = _redact_market_screener_provider_error(str(exc))
            errors.append(f"FMP SEC filings by symbol {symbol}: {clean_error}")
            failures += 1
            continue
        first = next((row for row in rows if isinstance(row, Mapping)), None)
        record = _record_from_provider_filing_metadata(symbol, first, fetched_at=fetched_at)
        if record is not None:
            enriched.append(record)
    status = "available" if enriched else "empty"
    if not enriched and failures:
        status = "unavailable"
    statuses.append(
        MarketScreenerSourceStatus(
            "FMP SEC filings by symbol",
            status,
            fetched_at,
            (
                f"Loaded FMP SEC filing metadata for {len(enriched)} of {len(requested)} capped symbol row(s); "
                f"{failures} nonblocking failure(s)."
            ),
        )
    )
    provider_diagnostics["rows_enriched_by_fmp_sec_filings"] = provider_diagnostics.get("rows_enriched_by_fmp_sec_filings", 0) + len(enriched)
    if failures:
        provider_diagnostics["provider_warnings"] = provider_diagnostics.get("provider_warnings", 0) + failures
    return tuple(enriched)


def _redact_market_screener_provider_error(value: str) -> str:
    text = redact_symbol_chat_secrets(str(value or ""))
    for env_name in ("FMP_API_KEY", "DATABENTO_API_KEY", "ALPHA_VANTAGE_API_KEY", "OPENAI_API_KEY"):
        secret = os.getenv(env_name, "").strip()
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(apikey['\"]?\s*[:=]\s*['\"]?)[^,'\"\s)}]+", r"\1[REDACTED]", text)
    return text


def _load_market_data_records_by_cik(
    provider: Any | None,
    statuses: list[MarketScreenerSourceStatus],
    errors: list[str],
    fetched_at: str,
    ciks: Iterable[str],
    force_refresh: bool,
    max_symbols: int,
    provider_diagnostics: dict[str, int],
) -> tuple[MarketQuoteFundamentalsRecord, ...]:
    requested = tuple(ciks)
    if not requested or max_symbols <= 0 or provider is None:
        return ()
    by_cik = getattr(provider, "quote_fundamentals_by_cik", None)
    if not callable(by_cik):
        return ()
    try:
        snapshot = by_cik(requested, force_refresh=force_refresh, max_symbols=max_symbols)
    except Exception as exc:
        errors.append(f"Market quote/fundamental provider CIK lookup: {exc}")
        statuses.append(
            MarketScreenerSourceStatus(
                "FMP profile-by-CIK",
                "error",
                fetched_at,
                f"CIK-based market-data lookup failed: {exc}",
            )
        )
        return ()
    statuses.extend(MarketScreenerSourceStatus(status.source, status.status, status.fetched_at, status.message) for status in snapshot.statuses)
    errors.extend(snapshot.errors)
    _merge_counter_mapping(provider_diagnostics, snapshot.diagnostics)
    return tuple(snapshot.records)


def _load_databento_cme_context_statuses(
    statuses: list[MarketScreenerSourceStatus],
    errors: list[str],
    fetched_at: str,
    force_refresh: bool,
    provider_diagnostics: dict[str, int],
) -> None:
    try:
        from app.data.databento_provider import configured_databento_cme_context_provider
    except Exception as exc:
        statuses.append(
            MarketScreenerSourceStatus(
                "Databento CME context",
                "unavailable",
                fetched_at,
                f"Databento CME context provider could not be loaded: {exc}",
            )
        )
        provider_diagnostics["provider_unavailable"] = provider_diagnostics.get("provider_unavailable", 0) + 1
        return
    try:
        snapshot = configured_databento_cme_context_provider().context(force_refresh=force_refresh)
    except Exception as exc:
        errors.append(f"Databento CME context: {exc}")
        statuses.append(
            MarketScreenerSourceStatus(
                "Databento CME context",
                "error",
                fetched_at,
                f"Databento CME context failed: {exc}",
            )
        )
        provider_diagnostics["provider_unavailable"] = provider_diagnostics.get("provider_unavailable", 0) + 1
        return
    statuses.extend(MarketScreenerSourceStatus(status.source, status.status, status.fetched_at, status.message) for status in snapshot.statuses)
    errors.extend(snapshot.errors)
    _merge_counter_mapping(provider_diagnostics, snapshot.diagnostics)


def _market_data_coverage_status(
    records: Iterable[MarketScreenerRecord],
    market_data_records: Iterable[MarketQuoteFundamentalsRecord],
    max_symbols: int,
    fetched_at: str,
    diagnostics: MarketScreenerCoverageDiagnostics,
) -> MarketScreenerSourceStatus:
    rows = list(records)
    provider_rows = [record for record in market_data_records if _quote_record_has_any_value(record)]
    provider_symbols = {_normalize_symbol(record.symbol) for record in provider_rows if _normalize_symbol(record.symbol)}
    sources = tuple(sorted({record.source for record in provider_rows if record.source}))
    source_text = ", ".join(sources) if sources else "configured market-data providers"
    limit_text = max(0, min(100, max_symbols))
    summary = market_screener_diagnostics_summary(diagnostics)
    coverage_note = _market_cap_coverage_note(rows, diagnostics)
    if provider_symbols:
        return MarketScreenerSourceStatus(
            "Market data enrichment",
            "partial",
            fetched_at,
            (
                f"{summary} Market data: enriched {len(provider_symbols)} of {len(rows)} rows via {source_text}. "
                f"Initial refresh requested up to {limit_text} symbol(s). Increase MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT up to 100, "
                "or use page/selected-row enrichment. Missing market cap, P/E, EPS, revenue growth, avg volume, and change % stay blank unless a provider/local file supplies them. "
                f"{coverage_note}"
            ),
        )
    return MarketScreenerSourceStatus(
        "Market data enrichment",
        "disabled" if max_symbols <= 0 else "unavailable",
        fetched_at,
        (
            f"{summary} Market data: enriched 0 of {len(rows)} rows via {source_text}. "
            "No quote/fundamental fields were supplied by the configured capped providers; missing fields stay blank and are not inferred. "
            f"{coverage_note}"
        ),
    )


def _market_cap_coverage_note(
    records: Iterable[MarketScreenerRecord],
    diagnostics: MarketScreenerCoverageDiagnostics,
) -> str:
    rows = list(records)
    total = diagnostics.total_rows or len(rows)
    with_cap = diagnostics.rows_with_market_cap or max(0, total - diagnostics.rows_missing_market_cap)
    incomplete = bool(diagnostics.market_cap_coverage_incomplete or diagnostics.rows_missing_market_cap)
    if total <= 0 or not incomplete:
        return "Market-cap ranking coverage is complete for loaded rows."
    cap_rows = [record for record in rows if market_screener_market_cap_rank(record).ranking_market_cap is not None]
    holding_cap_rows = [record for record in cap_rows if market_screener_is_my_holding(record)]
    holding_only_note = ""
    if cap_rows and len(cap_rows) == len(holding_cap_rows):
        holding_only_note = " Market caps currently exist only on portfolio/holding rows; holding labels are display metadata and do not make the global ranking complete."
    skipped_note = ""
    if diagnostics.rows_skipped_by_configured_symbol_cap:
        skipped_note = f" {diagnostics.rows_skipped_by_configured_symbol_cap} row(s) were skipped by configured provider caps."
    return (
        f"Global market-cap ranking coverage incomplete: {with_cap} of {total} loaded row(s) have market caps; "
        f"{diagnostics.rows_missing_market_cap} row(s) are missing market caps before page-1 ranking.{skipped_note}{holding_only_note}"
    )


def _source_ladder_diagnostics_status(
    diagnostics: MarketScreenerCoverageDiagnostics,
    fetched_at: str,
) -> MarketScreenerSourceStatus:
    detail = "; ".join(market_screener_diagnostics_detail_lines(diagnostics))
    return MarketScreenerSourceStatus(
        "Source ladder diagnostics",
        "available",
        fetched_at,
        detail,
    )


def market_screener_diagnostics_summary(diagnostics: MarketScreenerCoverageDiagnostics) -> str:
    rows_with_market_cap = diagnostics.rows_with_market_cap or max(0, diagnostics.total_rows - diagnostics.rows_missing_market_cap)
    parts = [
        f"market caps {rows_with_market_cap}/{diagnostics.total_rows}",
        f"Resolved {diagnostics.rows_resolved_by_sec_cik_mapping} symbols via SEC CIK",
        f"SEC submissions {diagnostics.rows_resolved_by_sec_submissions_metadata}",
        f"Schwab quotes {diagnostics.rows_enriched_by_schwab_quote}",
        f"FMP profiles {diagnostics.rows_enriched_by_fmp_profile + diagnostics.rows_enriched_by_fmp_profile_by_cik}",
        f"FMP filings {diagnostics.rows_enriched_by_fmp_sec_filings}",
        f"trusted USD caps {diagnostics.rows_with_trusted_usd_market_cap}",
        f"untrusted caps {diagnostics.rows_with_untrusted_market_cap}",
        f"{diagnostics.rows_skipped_by_configured_symbol_cap} skipped by cap",
        f"{diagnostics.unresolved_rows} unresolved",
    ]
    fmp_deep = (
        diagnostics.rows_enriched_by_fmp_market_cap
        + diagnostics.rows_enriched_by_fmp_historical_eod
        + diagnostics.rows_enriched_by_fmp_key_metrics
        + diagnostics.rows_enriched_by_fmp_ratios
        + diagnostics.rows_enriched_by_fmp_income_growth
        + diagnostics.rows_enriched_by_fmp_financial_growth
        + diagnostics.rows_enriched_by_fmp_income_statement
        + diagnostics.rows_enriched_by_fmp_shares_float
    )
    if diagnostics.rows_enriched_by_local_file:
        parts.insert(2, f"local {diagnostics.rows_enriched_by_local_file}")
    if diagnostics.rows_enriched_by_fmp_quote:
        parts.insert(-2, f"FMP quotes {diagnostics.rows_enriched_by_fmp_quote}")
    if fmp_deep:
        parts.insert(-2, f"FMP deep fields {fmp_deep}")
    if diagnostics.rows_enriched_by_databento_equities:
        parts.insert(-2, f"Databento equities {diagnostics.rows_enriched_by_databento_equities}")
    if diagnostics.databento_equities_chunks_attempted:
        parts.insert(-2, f"Databento chunks {diagnostics.databento_equities_chunks_attempted}")
    if diagnostics.databento_cme_context_rows:
        parts.insert(-2, f"Databento CME context {diagnostics.databento_cme_context_rows}")
    if diagnostics.rows_enriched_by_fallback_provider:
        parts.insert(-2, f"fallback {diagnostics.rows_enriched_by_fallback_provider}")
    if diagnostics.rows_blocked_by_provider_plan_rate_auth_limit:
        parts.insert(-2, f"{diagnostics.rows_blocked_by_provider_plan_rate_auth_limit} blocked by provider auth/plan/rate")
    if diagnostics.provider_calls_attempted:
        parts.insert(-2, f"provider calls {diagnostics.provider_calls_attempted}")
    if diagnostics.provider_cache_hits:
        parts.insert(-2, f"provider cache hits {diagnostics.provider_cache_hits}")
    if diagnostics.provider_warnings:
        parts.insert(-2, f"provider warnings {diagnostics.provider_warnings}")
    if diagnostics.market_cap_coverage_incomplete or diagnostics.rows_missing_market_cap:
        parts.insert(1, "market-cap ranking incomplete")
    return "; ".join(parts) + "."


def market_screener_diagnostics_detail_lines(diagnostics: MarketScreenerCoverageDiagnostics) -> list[str]:
    labels = (
        ("Total screener rows", diagnostics.total_rows),
        ("Rows with CIK", diagnostics.rows_with_cik),
        ("Rows missing CIK", diagnostics.rows_missing_cik),
        ("Rows with ticker/symbol", diagnostics.rows_with_symbol),
        ("Rows missing symbol", diagnostics.rows_missing_symbol),
        ("Rows resolved by SEC CIK mapping", diagnostics.rows_resolved_by_sec_cik_mapping),
        ("Rows resolved by SEC submissions metadata", diagnostics.rows_resolved_by_sec_submissions_metadata),
        ("Rows enriched by local file", diagnostics.rows_enriched_by_local_file),
        ("Rows enriched by Schwab quote", diagnostics.rows_enriched_by_schwab_quote),
        ("Rows enriched by Databento US Equities", diagnostics.rows_enriched_by_databento_equities),
        ("Rows enriched by FMP quote", diagnostics.rows_enriched_by_fmp_quote),
        ("Rows enriched by FMP profile", diagnostics.rows_enriched_by_fmp_profile),
        ("Rows enriched by FMP profile-by-CIK", diagnostics.rows_enriched_by_fmp_profile_by_cik),
        ("Rows enriched by FMP market cap", diagnostics.rows_enriched_by_fmp_market_cap),
        ("Rows enriched by FMP historical EOD", diagnostics.rows_enriched_by_fmp_historical_eod),
        ("Rows enriched by FMP key metrics", diagnostics.rows_enriched_by_fmp_key_metrics),
        ("Rows enriched by FMP ratios", diagnostics.rows_enriched_by_fmp_ratios),
        ("Rows enriched by FMP income growth", diagnostics.rows_enriched_by_fmp_income_growth),
        ("Rows enriched by FMP financial growth", diagnostics.rows_enriched_by_fmp_financial_growth),
        ("Rows enriched by FMP income statement", diagnostics.rows_enriched_by_fmp_income_statement),
        ("Rows enriched by FMP shares float", diagnostics.rows_enriched_by_fmp_shares_float),
        ("Rows enriched by FMP SEC filings", diagnostics.rows_enriched_by_fmp_sec_filings),
        ("Rows enriched by fallback provider", diagnostics.rows_enriched_by_fallback_provider),
        ("FMP cache hits", diagnostics.fmp_cache_hits),
        ("Databento US Equities symbols attempted", diagnostics.databento_equities_symbols_attempted),
        ("Databento US Equities chunks attempted", diagnostics.databento_equities_chunks_attempted),
        ("Databento US Equities cache hits", diagnostics.databento_equities_cache_hits),
        ("Databento US Equities provider warnings", diagnostics.databento_equities_provider_warnings),
        ("Databento CME context rows", diagnostics.databento_cme_context_rows),
        ("Databento CME cache hits", diagnostics.databento_cme_cache_hits),
        ("Databento dataset mismatch warnings", diagnostics.databento_dataset_mismatch_warnings),
        ("Rows blocked by provider plan/rate/auth limit", diagnostics.rows_blocked_by_provider_plan_rate_auth_limit),
        ("Provider unavailable count", diagnostics.provider_unavailable),
        ("Rows skipped by configured symbol cap", diagnostics.rows_skipped_by_configured_symbol_cap),
        ("Rows provider returned with no usable data", diagnostics.rows_provider_returned_no_usable_data),
        ("Unresolved rows", diagnostics.unresolved_rows),
        ("Rows still missing exchange/sector/industry", diagnostics.rows_still_missing_exchange_sector_industry),
        ("Rows with profile/classification", diagnostics.rows_with_profile_classification),
        ("Rows with price", diagnostics.rows_with_price),
        ("Rows missing price", diagnostics.rows_missing_price),
        ("Rows with volume", diagnostics.rows_with_volume),
        ("Rows missing volume", diagnostics.rows_missing_volume),
        ("Rows with avg volume", diagnostics.rows_with_avg_volume),
        ("Rows missing avg volume", diagnostics.rows_missing_avg_volume),
        ("Rows with fundamentals", diagnostics.rows_with_fundamentals),
        ("Rows with market cap", diagnostics.rows_with_market_cap),
        ("Rows with trusted USD market cap", diagnostics.rows_with_trusted_usd_market_cap),
        ("Rows with trusted primary market cap rank", diagnostics.rows_with_trusted_primary_market_cap),
        ("Rows with trusted non-primary market cap rank", diagnostics.rows_with_trusted_non_primary_market_cap),
        ("Rows with untrusted market cap", diagnostics.rows_with_untrusted_market_cap),
        ("Rows with non-USD market cap", diagnostics.rows_with_non_usd_market_cap),
        ("Rows with ambiguous market cap", diagnostics.rows_with_ambiguous_market_cap),
        ("Rows missing market cap", diagnostics.rows_missing_market_cap),
        ("Major U.S. large-cap symbols present", diagnostics.major_us_large_caps_present),
        ("Major U.S. large-cap symbols absent", diagnostics.major_us_large_caps_absent),
        ("Rows with revenue growth", diagnostics.rows_with_revenue_growth),
        ("Rows missing revenue growth", diagnostics.rows_missing_revenue_growth),
        ("Rows with float/shares", diagnostics.rows_with_float_or_shares),
        ("Rows missing float/shares", diagnostics.rows_missing_float_or_shares),
        ("Provider calls attempted", diagnostics.provider_calls_attempted),
        ("Provider rows requested", diagnostics.provider_rows_requested),
        ("Provider rows returned", diagnostics.provider_rows_returned),
        ("Provider rows parsed", diagnostics.provider_rows_parsed),
        ("Provider rows updated", diagnostics.provider_rows_updated),
        ("Provider cache hits", diagnostics.provider_cache_hits),
        ("Provider warnings", diagnostics.provider_warnings),
        ("Rows still missing price/volume", diagnostics.rows_still_missing_price_volume),
        ("Rows still missing fundamentals", diagnostics.rows_still_missing_fundamentals),
        ("Market-cap coverage incomplete", diagnostics.market_cap_coverage_incomplete),
    )
    return [f"{label}: {value}" for label, value in labels]


def _build_market_screener_diagnostics(
    records: Iterable[MarketScreenerRecord],
    statuses: Iterable[MarketScreenerSourceStatus],
    provider_diagnostics: Mapping[str, int],
) -> MarketScreenerCoverageDiagnostics:
    rows = list(records)
    status_rows = list(statuses)
    provider_unavailable = _counter(provider_diagnostics, "provider_unavailable")
    provider_unavailable = max(
        provider_unavailable,
        sum(1 for status in status_rows if status.status in {"unavailable", "error"} and _status_is_provider_related(status)),
    )
    blocked = max(
        _counter(provider_diagnostics, "rows_blocked_by_provider_plan_rate_auth_limit"),
        sum(1 for status in status_rows if _status_message_mentions_provider_limit(status)),
    )
    rows_with_profile = sum(1 for record in rows if _record_has_profile_classification(record))
    rows_with_price = sum(1 for record in rows if record.price is not None)
    rows_with_volume = sum(1 for record in rows if record.volume is not None)
    rows_with_avg_volume = sum(1 for record in rows if record.avg_volume is not None)
    rows_with_fundamentals = sum(1 for record in rows if market_screener_record_has_fundamentals(record))
    market_cap_ranks = [market_screener_market_cap_rank(record) for record in rows]
    rows_with_market_cap = sum(1 for rank in market_cap_ranks if rank.ranking_market_cap is not None)
    rows_with_revenue_growth = sum(1 for record in rows if record.revenue_growth is not None)
    rows_with_float_or_shares = sum(1 for record in rows if record.shares_float is not None or record.shares_outstanding is not None)
    total_rows = len(rows)
    return MarketScreenerCoverageDiagnostics(
        total_rows=total_rows,
        rows_with_cik=sum(1 for record in rows if _normalize_cik(record.cik)),
        rows_missing_cik=sum(1 for record in rows if not _normalize_cik(record.cik)),
        rows_with_symbol=sum(1 for record in rows if _normalize_symbol(record.symbol)),
        rows_missing_symbol=sum(1 for record in rows if not _normalize_symbol(record.symbol)),
        rows_resolved_by_sec_cik_mapping=max(
            _counter(provider_diagnostics, "rows_resolved_by_sec_cik_mapping"),
            _count_rows_with_source(rows, "SEC CIK/ticker identity") + _count_rows_with_source(rows, "SEC company_tickers.json"),
        ),
        rows_resolved_by_sec_submissions_metadata=max(
            _counter(provider_diagnostics, "rows_resolved_by_sec_submissions_metadata"),
            _count_rows_with_source(rows, "SEC submissions metadata"),
        ),
        rows_enriched_by_local_file=max(
            _counter(provider_diagnostics, "rows_enriched_by_local_file"),
            _count_rows_with_source(rows, "Local market data file"),
        ),
        rows_enriched_by_schwab_quote=max(
            _counter(provider_diagnostics, "rows_enriched_by_schwab_quote"),
            _count_rows_with_source(rows, "Schwab quote"),
        ),
        rows_enriched_by_databento_equities=max(
            _counter(provider_diagnostics, "rows_enriched_by_databento_equities"),
            _count_rows_with_source(rows, "Databento US Equities"),
        ),
        rows_enriched_by_fmp_quote=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_quote"),
            _count_rows_with_source(rows, "FMP quote"),
        ),
        rows_enriched_by_fmp_profile=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_profile"),
            _count_rows_with_source(rows, "FMP profile"),
        ),
        rows_enriched_by_fmp_profile_by_cik=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_profile_by_cik"),
            _count_rows_with_source(rows, "FMP profile-by-CIK"),
        ),
        rows_enriched_by_fmp_market_cap=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_market_cap"),
            _count_rows_with_source(rows, "FMP market cap"),
        ),
        rows_enriched_by_fmp_historical_eod=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_historical_eod"),
            _count_rows_with_source(rows, "FMP historical EOD"),
        ),
        rows_enriched_by_fmp_key_metrics=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_key_metrics"),
            _count_rows_with_source(rows, "FMP key metrics"),
        ),
        rows_enriched_by_fmp_ratios=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_ratios"),
            _count_rows_with_source(rows, "FMP ratios"),
        ),
        rows_enriched_by_fmp_income_growth=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_income_growth"),
            _count_rows_with_source(rows, "FMP income growth"),
        ),
        rows_enriched_by_fmp_financial_growth=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_financial_growth"),
            _count_rows_with_source(rows, "FMP financial growth"),
        ),
        rows_enriched_by_fmp_income_statement=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_income_statement"),
            _count_rows_with_source(rows, "FMP income statement"),
        ),
        rows_enriched_by_fmp_shares_float=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_shares_float"),
            _count_rows_with_source(rows, "FMP shares float"),
        ),
        rows_enriched_by_fmp_sec_filings=max(
            _counter(provider_diagnostics, "rows_enriched_by_fmp_sec_filings"),
            _count_rows_with_source(rows, "FMP filing metadata"),
        ),
        rows_enriched_by_fallback_provider=max(
            _counter(provider_diagnostics, "rows_enriched_by_fallback_provider"),
            _count_rows_with_source(rows, "Fallback Alpha Vantage"),
        ),
        fmp_cache_hits=_counter(provider_diagnostics, "fmp_cache_hits"),
        databento_equities_symbols_attempted=_counter(provider_diagnostics, "databento_equities_symbols_attempted"),
        databento_equities_chunks_attempted=_counter(provider_diagnostics, "databento_equities_chunks_attempted"),
        databento_equities_cache_hits=_counter(provider_diagnostics, "databento_equities_cache_hits"),
        databento_equities_provider_warnings=_counter(provider_diagnostics, "databento_equities_provider_warnings"),
        databento_cme_context_rows=_counter(provider_diagnostics, "databento_cme_context_rows"),
        databento_cme_cache_hits=_counter(provider_diagnostics, "databento_cme_cache_hits"),
        databento_dataset_mismatch_warnings=_counter(provider_diagnostics, "databento_dataset_mismatch_warnings"),
        rows_blocked_by_provider_plan_rate_auth_limit=blocked,
        rows_skipped_by_configured_symbol_cap=_counter(provider_diagnostics, "rows_skipped_by_configured_symbol_cap"),
        rows_provider_returned_no_usable_data=_counter(provider_diagnostics, "rows_provider_returned_no_usable_data"),
        provider_unavailable=provider_unavailable,
        unresolved_rows=sum(1 for record in rows if not _normalize_symbol(record.symbol)),
        rows_still_missing_exchange_sector_industry=sum(1 for record in rows if not (record.exchange and record.sector and record.industry)),
        rows_with_profile_classification=rows_with_profile,
        rows_with_price=rows_with_price,
        rows_missing_price=max(0, len(rows) - rows_with_price),
        rows_with_volume=rows_with_volume,
        rows_missing_volume=max(0, len(rows) - rows_with_volume),
        rows_with_avg_volume=rows_with_avg_volume,
        rows_missing_avg_volume=max(0, total_rows - rows_with_avg_volume),
        rows_with_fundamentals=rows_with_fundamentals,
        rows_with_market_cap=rows_with_market_cap,
        rows_with_trusted_usd_market_cap=sum(1 for rank in market_cap_ranks if rank.trusted and rank.currency == "USD"),
        rows_with_trusted_primary_market_cap=sum(1 for rank in market_cap_ranks if rank.category == "us_primary_common"),
        rows_with_trusted_non_primary_market_cap=sum(1 for rank in market_cap_ranks if rank.category == "trusted_non_primary"),
        rows_with_untrusted_market_cap=sum(1 for rank in market_cap_ranks if rank.ranking_market_cap is not None and not rank.trusted),
        rows_with_non_usd_market_cap=sum(1 for rank in market_cap_ranks if rank.ranking_market_cap is not None and rank.currency not in {"", "USD", "unknown"}),
        rows_with_ambiguous_market_cap=sum(1 for rank in market_cap_ranks if rank.category == "untrusted_ambiguous"),
        rows_missing_market_cap=max(0, total_rows - rows_with_market_cap),
        rows_with_revenue_growth=rows_with_revenue_growth,
        rows_missing_revenue_growth=max(0, total_rows - rows_with_revenue_growth),
        rows_with_float_or_shares=rows_with_float_or_shares,
        rows_missing_float_or_shares=max(0, total_rows - rows_with_float_or_shares),
        provider_calls_attempted=_counter(provider_diagnostics, "provider_calls_attempted"),
        provider_rows_requested=_counter(provider_diagnostics, "provider_rows_requested"),
        provider_rows_returned=_counter(provider_diagnostics, "provider_rows_returned"),
        provider_rows_parsed=_counter(provider_diagnostics, "provider_rows_parsed"),
        provider_rows_updated=_counter(provider_diagnostics, "provider_rows_updated"),
        provider_cache_hits=_counter(provider_diagnostics, "provider_cache_hits")
        or _counter(provider_diagnostics, "fmp_cache_hits")
        + _counter(provider_diagnostics, "databento_equities_cache_hits")
        + _counter(provider_diagnostics, "databento_cme_cache_hits"),
        provider_warnings=_counter(provider_diagnostics, "provider_warnings")
        or _counter(provider_diagnostics, "databento_equities_provider_warnings")
        + _counter(provider_diagnostics, "databento_dataset_mismatch_warnings")
        + blocked,
        rows_still_missing_price_volume=sum(1 for record in rows if record.price is None or record.volume is None),
        rows_still_missing_fundamentals=sum(1 for record in rows if not market_screener_record_has_fundamentals(record)),
        market_cap_coverage_incomplete=int(rows_with_market_cap < total_rows),
    )


def _count_rows_with_source(records: Iterable[MarketScreenerRecord], needle: str) -> int:
    lower = needle.lower()
    count = 0
    for record in records:
        source_text = " ".join(record.sources).lower()
        provenance_text = " ".join(row.source for row in record.field_provenance).lower()
        if lower in source_text or lower in provenance_text:
            count += 1
    return count


def _status_is_provider_related(status: MarketScreenerSourceStatus) -> bool:
    source = status.source.lower()
    return any(term in source for term in ("market", "quote", "fmp", "schwab", "fallback", "alpha vantage", "local", "databento"))


def _status_message_mentions_provider_limit(status: MarketScreenerSourceStatus) -> bool:
    text = f"{status.status} {status.message}".lower()
    return any(term in text for term in ("auth", "plan", "rate", "quota", "401", "403", "429", "unauthorized", "forbidden"))


def _counter(counters: Mapping[str, int], key: str) -> int:
    try:
        return max(0, int(counters.get(key, 0)))
    except (TypeError, ValueError):
        return 0


def _merge_counter_mapping(target: dict[str, int], source: Mapping[str, int] | None) -> None:
    for key, value in (source or {}).items():
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if amount:
            target[str(key)] = target.get(str(key), 0) + amount


def _quote_record_has_any_value(record: MarketQuoteFundamentalsRecord) -> bool:
    return any(
        value is not None
        for value in (
            record.exchange,
            record.sector,
            record.industry,
            record.price,
            record.market_cap,
            record.volume,
            record.avg_volume,
            record.change_percent,
            record.pe_ratio,
            record.eps,
            record.revenue_growth,
            record.shares_float,
            record.shares_outstanding,
        )
    )


def _market_data_candidate_symbols(records: Iterable[MarketScreenerRecord], limit: int) -> tuple[str, ...]:
    if limit <= 0:
        return ()
    rows = [record for record in records if _normalize_symbol(record.symbol)]
    major_cap_order = {symbol: index for index, symbol in enumerate(MAJOR_US_LARGE_CAP_SYMBOLS)}

    def priority(record: MarketScreenerRecord) -> tuple[int, int, str]:
        symbol = _normalize_symbol(record.symbol)
        broad_universe_row = _record_has_broad_universe_source(record)
        if broad_universe_row and symbol in major_cap_order:
            return 0, major_cap_order[symbol], symbol
        if broad_universe_row:
            return 1, 0, symbol
        if record.next_earnings_date or record.recent_filing_date or record.risk_flags:
            return 2, 0, symbol
        return 3, 0, symbol

    seen: set[str] = set()
    result: list[str] = []
    for record in sorted(rows, key=priority):
        symbol = _normalize_symbol(record.symbol)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
        if len(result) >= limit:
            break
    return tuple(result)


def _record_has_broad_universe_source(record: MarketScreenerRecord) -> bool:
    source_text = " ".join(record.sources).lower()
    return any(
        label in source_text
        for label in (
            "fmp stock-list",
            "fmp company-screener",
            "provider market universe",
            "sec company_tickers.json",
            "local market universe seed",
            "built-in fallback universe",
        )
    )


def _market_data_candidate_ciks(records: Iterable[MarketScreenerRecord], limit: int) -> tuple[str, ...]:
    if limit <= 0:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for record in records:
        cik = _normalize_cik(record.cik)
        if not cik or cik in seen:
            continue
        source_text = " ".join(record.sources).lower()
        filing_like = bool(record.recent_filing_date or "sec edgar" in source_text or "recent edgar" in source_text)
        needs_identity_or_profile = (
            not _normalize_symbol(record.symbol)
            or not record.exchange
            or not record.sector
            or not record.industry
            or not market_screener_record_has_fundamentals(record)
        )
        if filing_like and needs_identity_or_profile:
            seen.add(cik)
            result.append(cik)
        if len(result) >= limit:
            break
    return tuple(result)


def _ciks_needing_identity(
    recent: Iterable[RecentEarningsRecord],
    supplemental: Iterable[MarketScreenerRecord],
) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for record in recent:
        cik = _normalize_cik(record.cik)
        if cik and not _normalize_symbol(record.ticker) and cik not in seen:
            seen.add(cik)
            result.append(cik)
    for record in supplemental:
        cik = _normalize_cik(record.cik)
        if cik and not _normalize_symbol(record.symbol) and cik not in seen:
            seen.add(cik)
            result.append(cik)
    return tuple(result)


def _sec_submission_candidate_ciks(records: Iterable[MarketScreenerRecord], max_ciks: int) -> tuple[str, ...]:
    if max_ciks <= 0:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for record in records:
        cik = _normalize_cik(record.cik)
        if not cik or cik in seen:
            continue
        source_text = " ".join(record.sources).lower()
        filing_like = bool(record.recent_filing_date or "sec edgar" in source_text or "recent edgar" in source_text)
        needs_metadata = not record.symbol or not record.exchange or not record.sector or not record.industry
        if filing_like and needs_metadata:
            seen.add(cik)
            result.append(cik)
        if len(result) >= max_ciks:
            break
    return tuple(result)


def _record_from_sec_submission_metadata(cik: str, payload: Mapping[str, Any], *, fetched_at: str) -> MarketScreenerRecord | None:
    normalized_cik = _normalize_cik(cik)
    tickers = _clean_text_sequence(payload.get("tickers"))
    exchanges = _clean_text_sequence(payload.get("exchanges"))
    symbol = _normalize_symbol(tickers[0]) if len(tickers) == 1 else ""
    exchange = exchanges[0] if len(exchanges) == 1 else None
    sic = _optional_text(payload.get("sic"))
    industry = _optional_text(payload.get("sicDescription"))
    sector = sector_for_sic(sic, industry) if (sic or industry) else None
    company_name = _optional_text(payload.get("name") or payload.get("entityName"))
    source_url = SEC_SUBMISSIONS_URL.format(cik=normalized_cik)
    values = {
        "symbol": symbol,
        "company_name": company_name,
        "cik": normalized_cik,
        "exchange": exchange,
        "sector": sector,
        "industry": industry,
    }
    if not any(_has_value(value) for key, value in values.items() if key != "cik"):
        return None
    return MarketScreenerRecord(
        symbol=symbol,
        company_name=company_name,
        cik=normalized_cik,
        exchange=exchange,
        sector=sector,
        industry=industry,
        sources=("SEC submissions metadata",),
        source_links=(source_url,),
        fetched_at=fetched_at,
        source_excerpt=_sec_submission_metadata_excerpt(sic, industry, tickers, exchanges),
        field_provenance=_provenance_for_values(
            values,
            source="SEC submissions metadata",
            source_link=source_url,
            fetched_at=fetched_at,
            source_detail="CIK submissions metadata",
        ),
    )


def _clean_text_sequence(value: Any) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(_dedupe_texts(item for item in value if _optional_text(item)))
    clean = _optional_text(value)
    return (clean,) if clean else ()


def _sec_submission_metadata_excerpt(
    sic: str | None,
    industry: str | None,
    tickers: Iterable[str],
    exchanges: Iterable[str],
) -> str | None:
    parts = []
    if sic:
        parts.append(f"SEC SIC {sic}")
    if industry:
        parts.append(industry)
    ticker_text = ", ".join(tickers)
    exchange_text = ", ".join(exchanges)
    if ticker_text:
        parts.append(f"tickers: {ticker_text}")
    if exchange_text:
        parts.append(f"exchanges: {exchange_text}")
    return "; ".join(parts) or None


def _record_from_universe(entry: MarketUniverseEntry, *, fetched_at: str) -> MarketScreenerRecord:
    source_links = (entry.source_url,) if entry.source_url else ()
    symbol = _normalize_symbol(entry.symbol)
    values = {
        "symbol": symbol,
        "company_name": entry.company_name,
        "cik": entry.cik,
        "exchange": entry.exchange,
        "sector": entry.sector,
        "industry": entry.industry,
    }
    return MarketScreenerRecord(
        symbol=symbol,
        company_name=entry.company_name,
        cik=entry.cik,
        exchange=entry.exchange,
        sector=entry.sector,
        industry=entry.industry,
        sources=(entry.source,),
        source_links=source_links,
        fetched_at=fetched_at,
        field_provenance=_provenance_for_values(values, source=entry.source, source_link=entry.source_url, fetched_at=fetched_at),
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
    source = record.source or "SEC EDGAR"
    values = {
        "symbol": _normalize_symbol(record.ticker),
        "company_name": record.company_name,
        "cik": record.cik,
        "exchange": record.exchange,
        "sector": record.sector,
        "industry": record.industry,
        "eps": record.eps,
        "revenue_growth": record.revenue_growth,
        "recent_filing_date": record.filed_date,
        "recent_filing_type": f"{record.form} {record.items or record.filing_type}".strip(),
        "signals": tuple(_dedupe_texts(signals)),
        "risk_flags": tuple(record.risk_flags),
        "source_excerpt": getattr(record, "source_excerpt", None),
    }
    return MarketScreenerRecord(
        symbol=values["symbol"],
        company_name=record.company_name,
        cik=record.cik,
        exchange=record.exchange,
        sector=record.sector,
        industry=record.industry,
        eps=record.eps,
        revenue_growth=record.revenue_growth,
        recent_filing_date=record.filed_date,
        recent_filing_type=f"{record.form} {record.items or record.filing_type}".strip(),
        signals=values["signals"],
        risk_flags=tuple(record.risk_flags),
        sources=(source,),
        source_links=tuple(_dedupe_texts(source_links)),
        fetched_at=fetched_at,
        source_excerpt=getattr(record, "source_excerpt", None),
        field_provenance=_provenance_for_values(values, source=source, source_link=record.filing_url, fetched_at=fetched_at),
    )


def _record_from_provider_filing_metadata(
    symbol: str,
    payload: Mapping[str, Any] | None,
    *,
    fetched_at: str,
) -> MarketScreenerRecord | None:
    if not payload:
        return None
    filing_date = _optional_text(
        payload.get("filingDate")
        or payload.get("fillingDate")
        or payload.get("filedDate")
        or payload.get("date")
        or payload.get("acceptedDate")
    )
    form = _optional_text(payload.get("form") or payload.get("type") or payload.get("filingType") or payload.get("formType"))
    if not filing_date or not form:
        return None
    filing_url = _optional_text(payload.get("finalLink") or payload.get("filing_url") or payload.get("filingUrl") or payload.get("link") or payload.get("url"))
    clean_symbol = _normalize_symbol(payload.get("symbol") or payload.get("ticker") or symbol)
    source = _optional_text(payload.get("source")) or "FMP filing metadata"
    source_url = _optional_text(payload.get("source_url") or payload.get("sourceUrl")) or filing_url
    values = {
        "symbol": clean_symbol,
        "company_name": _optional_text(payload.get("companyName") or payload.get("company_name") or payload.get("company") or payload.get("name")),
        "cik": _normalize_cik(payload.get("cik") or payload.get("CIK") or payload.get("cik_str")) or None,
        "recent_filing_date": filing_date[:10],
        "recent_filing_type": form.upper(),
        "signals": ("Recent SEC filing",),
        "source_links": (filing_url,) if filing_url else (),
    }
    return MarketScreenerRecord(
        symbol=values["symbol"],
        company_name=values["company_name"],
        cik=values["cik"],
        recent_filing_date=values["recent_filing_date"],
        recent_filing_type=values["recent_filing_type"],
        signals=values["signals"],
        sources=(source,),
        source_links=values["source_links"],
        fetched_at=fetched_at,
        field_provenance=_provenance_for_values(values, source=source, source_link=source_url, fetched_at=fetched_at),
    )


def _record_from_upcoming_earnings(record: UpcomingEarningsRecord, *, fetched_at: str) -> MarketScreenerRecord:
    source_links = (record.source_url,) if record.source_url else ()
    source = record.source or "Upcoming earnings calendar"
    values = {
        "symbol": _normalize_symbol(record.symbol),
        "company_name": record.company_name,
        "next_earnings_date": record.report_date,
        "signals": ("Upcoming earnings",),
    }
    return MarketScreenerRecord(
        symbol=values["symbol"],
        company_name=record.company_name,
        next_earnings_date=record.report_date,
        signals=("Upcoming earnings",),
        sources=(source,),
        source_links=source_links,
        fetched_at=fetched_at,
        field_provenance=_provenance_for_values(values, source=source, source_link=record.source_url, fetched_at=fetched_at),
    )


def _record_from_market_data(record: MarketQuoteFundamentalsRecord, *, fetched_at: str) -> MarketScreenerRecord:
    source_links = (record.source_url,) if record.source_url else ()
    signals: list[str] = []
    high_volume = record.volume is not None and record.avg_volume not in (None, 0) and record.volume >= record.avg_volume * 1.5
    mover = record.change_percent is not None and abs(record.change_percent) >= 5.0
    if high_volume:
        signals.append("High volume")
    if mover:
        signals.append("Mover")
    values = {
        "symbol": _normalize_symbol(record.symbol),
        "company_name": getattr(record, "company_name", None),
        "cik": _normalize_cik(getattr(record, "cik", None)) or None,
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
        "shares_float": record.shares_float,
        "shares_outstanding": record.shares_outstanding,
        "market_cap_currency": _normalize_currency_code(getattr(record, "market_cap_currency", None)) or None,
        "market_cap_rank_value": getattr(record, "market_cap_rank_value", None),
        "market_cap_rank_currency": _normalize_currency_code(getattr(record, "market_cap_rank_currency", None)) or None,
        "market_cap_rank_trusted": getattr(record, "market_cap_rank_trusted", None),
        "market_cap_rank_reason": getattr(record, "market_cap_rank_reason", None),
        "instrument_type": getattr(record, "instrument_type", None),
        "country": getattr(record, "country", None),
        "is_adr": getattr(record, "is_adr", None),
        "is_etf": getattr(record, "is_etf", None),
        "is_fund": getattr(record, "is_fund", None),
        "is_otc": getattr(record, "is_otc", None),
        "signals": tuple(signals),
    }
    source = record.source or "Market quote/fundamental provider"
    record_fetched_at = record.fetched_at or fetched_at
    return MarketScreenerRecord(
        symbol=values["symbol"],
        company_name=values["company_name"],
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
        shares_float=record.shares_float,
        shares_outstanding=record.shares_outstanding,
        market_cap_currency=values["market_cap_currency"],
        market_cap_rank_value=values["market_cap_rank_value"],
        market_cap_rank_currency=values["market_cap_rank_currency"],
        market_cap_rank_trusted=values["market_cap_rank_trusted"],
        market_cap_rank_reason=values["market_cap_rank_reason"],
        instrument_type=values["instrument_type"],
        country=values["country"],
        is_adr=values["is_adr"],
        is_etf=values["is_etf"],
        is_fund=values["is_fund"],
        is_otc=values["is_otc"],
        signals=tuple(signals),
        sources=(source,),
        source_links=source_links,
        fetched_at=record_fetched_at,
        cik=values["cik"],
        field_provenance=_market_data_provenance(record, values, source=source, source_link=record.source_url, fetched_at=record_fetched_at),
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
        shares_float=record.shares_float,
        shares_outstanding=record.shares_outstanding,
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
        portfolio_quantity=record.portfolio_quantity,
        portfolio_average_cost=record.portfolio_average_cost,
        portfolio_market_value=record.portfolio_market_value,
        portfolio_unrealized_pnl=record.portfolio_unrealized_pnl,
        portfolio_weight=record.portfolio_weight,
        field_provenance=_field_provenance_from_payload(record.field_provenance) or _provenance_for_existing_record(record, fetched_at=fetched_at),
        market_cap_currency=_normalize_currency_code(record.market_cap_currency) or None,
        market_cap_rank_value=record.market_cap_rank_value,
        market_cap_rank_currency=_normalize_currency_code(record.market_cap_rank_currency) or None,
        market_cap_rank_trusted=record.market_cap_rank_trusted,
        market_cap_rank_reason=record.market_cap_rank_reason,
        instrument_type=record.instrument_type,
        country=record.country,
        is_adr=record.is_adr,
        is_etf=record.is_etf,
        is_fund=record.is_fund,
        is_otc=record.is_otc,
    )


def _strip_sec_visible_chart_fields(record: MarketScreenerRecord) -> MarketScreenerRecord:
    if not _record_has_sec_owned_source(record):
        return record
    provenance_by_field = _provenance_by_field(record.field_provenance)
    strip_fields: set[str] = set()
    record_sources_are_sec_only = bool(record.sources) and all(_source_is_sec_owned(source) for source in record.sources)
    for field in _SEC_VISIBLE_CHART_FIELDS:
        if not _has_value(getattr(record, field, None)):
            continue
        provenance = provenance_by_field.get(field)
        if provenance is not None:
            if _source_is_sec_owned(provenance.source):
                strip_fields.add(field)
        elif record_sources_are_sec_only:
            strip_fields.add(field)
    if not strip_fields:
        return record
    replacements = {field: None for field in strip_fields}
    filtered_provenance = tuple(
        row
        for row in record.field_provenance
        if not (row.field in strip_fields and _source_is_sec_owned(row.source))
    )
    return replace(record, **replacements, field_provenance=filtered_provenance)


def _record_has_sec_owned_source(record: MarketScreenerRecord) -> bool:
    return any(_source_is_sec_owned(source) for source in (*record.sources, *(row.source for row in record.field_provenance)))


def _source_is_sec_owned(source: str | None) -> bool:
    text = str(source or "").strip().lower()
    if not text:
        return False
    if "fmp" in text:
        return False
    return "sec" in text or "edgar" in text


def _merge_into(records: dict[str, MarketScreenerRecord], incoming: MarketScreenerRecord) -> None:
    keys = _record_keys(incoming)
    if not keys:
        return
    existing_key = next((key for key in keys if key in records), "")
    existing = records.get(existing_key) if existing_key else None
    merged = incoming if existing is None else merge_market_screener_record(existing, incoming)
    canonical_key = _record_key(merged)
    if not canonical_key:
        return
    if existing_key and existing_key != canonical_key:
        records.pop(existing_key, None)
    for alias in _record_keys(merged):
        if alias != canonical_key and alias in records:
            merged = merge_market_screener_record(records.pop(alias), merged)
    records[canonical_key] = merged


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
        shares_float=_prefer_number(incoming.shares_float, existing.shares_float),
        shares_outstanding=_prefer_number(incoming.shares_outstanding, existing.shares_outstanding),
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
        portfolio_quantity=_prefer_number(incoming.portfolio_quantity, existing.portfolio_quantity),
        portfolio_average_cost=_prefer_number(incoming.portfolio_average_cost, existing.portfolio_average_cost),
        portfolio_market_value=_prefer_number(incoming.portfolio_market_value, existing.portfolio_market_value),
        portfolio_unrealized_pnl=_prefer_number(incoming.portfolio_unrealized_pnl, existing.portfolio_unrealized_pnl),
        portfolio_weight=_prefer_number(incoming.portfolio_weight, existing.portfolio_weight),
        field_provenance=_merge_screener_field_provenance(existing, incoming),
        market_cap_currency=_prefer_market_cap_metadata(incoming.market_cap_currency, existing.market_cap_currency, incoming),
        market_cap_rank_value=_prefer_number(incoming.market_cap_rank_value, existing.market_cap_rank_value),
        market_cap_rank_currency=_prefer_market_cap_metadata(incoming.market_cap_rank_currency, existing.market_cap_rank_currency, incoming),
        market_cap_rank_trusted=_prefer_market_cap_metadata(incoming.market_cap_rank_trusted, existing.market_cap_rank_trusted, incoming),
        market_cap_rank_reason=_prefer_market_cap_metadata(incoming.market_cap_rank_reason, existing.market_cap_rank_reason, incoming),
        instrument_type=_prefer_market_cap_metadata(incoming.instrument_type, existing.instrument_type, incoming),
        country=_prefer_market_cap_metadata(incoming.country, existing.country, incoming),
        is_adr=_prefer_market_cap_metadata(incoming.is_adr, existing.is_adr, incoming),
        is_etf=_prefer_market_cap_metadata(incoming.is_etf, existing.is_etf, incoming),
        is_fund=_prefer_market_cap_metadata(incoming.is_fund, existing.is_fund, incoming),
        is_otc=_prefer_market_cap_metadata(incoming.is_otc, existing.is_otc, incoming),
    )


def _record_key(record: MarketScreenerRecord) -> str:
    symbol = _normalize_symbol(record.symbol)
    if symbol:
        return f"SYMBOL:{symbol}"
    cik = _normalize_cik(record.cik)
    return f"CIK:{cik}" if cik else ""


def _record_keys(record: MarketScreenerRecord) -> tuple[str, ...]:
    keys: list[str] = []
    symbol = _normalize_symbol(record.symbol)
    cik = _normalize_cik(record.cik)
    if symbol:
        keys.append(f"SYMBOL:{symbol}")
    if cik:
        keys.append(f"CIK:{cik}")
    return tuple(dict.fromkeys(keys))


def _event_type_matches(record: MarketScreenerRecord, event_type: str) -> bool:
    if event_type == "All":
        return True
    if event_type == "Quote-enriched":
        return market_screener_record_has_market_data(record)
    if event_type == "Fundamentals available":
        return market_screener_record_has_fundamentals(record)
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
    if event_type in {"My Holdings", "Schwab holding/watchlist"}:
        return market_screener_is_my_holding(record)
    return True


def _data_completeness_matches(record: MarketScreenerRecord, data_completeness: str) -> bool:
    if data_completeness == "All":
        return True
    score = market_screener_data_completeness_score(record)
    if data_completeness == "High completeness (>=75%)":
        return score >= 75
    if data_completeness == "Partial completeness (40-74%)":
        return 40 <= score < 75
    if data_completeness == "Low completeness (<40%)":
        return score < 40
    if data_completeness == "Has field provenance":
        return bool(record.field_provenance)
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
        "float_shares": record.shares_float if record.shares_float is not None else record.shares_outstanding,
        "shares_float": record.shares_float,
        "shares_outstanding": record.shares_outstanding,
        "data_status": market_screener_data_label(record),
        "data_completeness": market_screener_data_completeness_score(record),
        "my_holding": market_screener_is_my_holding(record),
        "next_earnings": record.next_earnings_date,
        "recent_filing": record.recent_filing_date,
        "recent_type": record.recent_filing_type,
        "signals": len(record.signals),
        "risk_flags": len(record.risk_flags),
        "sources": len(record.sources),
    }.get(column)


def _is_us_primary_common_equity(record: MarketScreenerRecord) -> bool:
    exchange = _clean_exchange(record.exchange)
    if not exchange or exchange not in US_PRIMARY_EXCHANGES:
        return False
    if _is_otc_record(record) or _is_adr_record(record) or _is_fund_or_etf_record(record):
        return False
    country = _clean_country(record.country)
    return not country or country in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}


def _non_primary_market_cap_reason(record: MarketScreenerRecord) -> str:
    reasons: list[str] = []
    if _is_adr_record(record):
        reasons.append("the row appears to be an ADR/foreign depositary receipt")
    country = _clean_country(record.country)
    if country and country not in {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}:
        reasons.append(f"listing/company country is {record.country}")
    if _is_fund_or_etf_record(record):
        reasons.append("the instrument appears to be an ETF/fund/trust rather than primary common equity")
    if _is_otc_record(record):
        reasons.append("the listing appears to be OTC or pink-sheet")
    return "; ".join(dict.fromkeys(reasons))


def _is_adr_record(record: MarketScreenerRecord) -> bool:
    if record.is_adr is True:
        return True
    symbol = _normalize_symbol(record.symbol)
    if symbol in KNOWN_FOREIGN_ADR_SYMBOLS:
        return True
    text = " ".join(str(value or "") for value in (record.instrument_type, record.company_name, *record.sources)).upper()
    return bool(re.search(r"\b(ADR|ADS|DEPOSITARY|FOREIGN ORDINARY|SPONSORED ADR)\b", text))


def _is_fund_or_etf_record(record: MarketScreenerRecord) -> bool:
    if record.is_etf is True or record.is_fund is True:
        return True
    text = " ".join(str(value or "") for value in (record.instrument_type, record.company_name, record.industry)).upper()
    return any(token in text for token in ("ETF", "ETN", "FUND", "INDEX"))


def _is_otc_record(record: MarketScreenerRecord) -> bool:
    if record.is_otc is True:
        return True
    exchange = _clean_exchange(record.exchange)
    return any(marker in exchange for marker in OTC_EXCHANGE_MARKERS)


def _clean_exchange(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def _clean_country(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip().upper())
    aliases = {"UNITED STATES OF AMERICA": "UNITED STATES", "U.S.": "US", "U.S.A.": "USA"}
    return aliases.get(text, text)


def _normalize_currency_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    aliases = {
        "$": "USD",
        "US$": "USD",
        "U.S. DOLLAR": "USD",
        "US DOLLAR": "USD",
        "UNITED STATES DOLLAR": "USD",
        "USDOLLAR": "USD",
    }
    if text in aliases:
        return aliases[text]
    return text[:3] if len(text) > 3 and text[:3].isalpha() else text


def _market_cap_sort_key(record: MarketScreenerRecord, *, descending: bool) -> tuple[Any, ...]:
    rank = market_screener_market_cap_rank(record)
    market_cap = _float_or_none(rank.ranking_market_cap)
    has_market_cap = market_cap is not None and market_cap >= 0
    if not has_market_cap:
        source_rank = 2
    elif rank.used_for_ranking or rank.trusted:
        source_rank = 0
    else:
        source_rank = 1
    return (
        source_rank,
        0.0 if not has_market_cap else (-market_cap if descending else market_cap),
        _market_cap_category_sort_key(rank.category),
        *_market_cap_fallback_sort_key(record),
    )


def _market_cap_category_sort_key(category: str) -> int:
    return {
        "us_primary_common": 0,
        "trusted_non_primary": 1,
        "untrusted_non_usd": 2,
        "untrusted_non_primary": 3,
        "untrusted_explicit": 4,
        "untrusted_ambiguous": 5,
        "missing_or_invalid": 9,
    }.get(str(category or ""), 8)


def _market_cap_fallback_sort_key(record: MarketScreenerRecord) -> tuple[Any, ...]:
    price_volume_fields = (record.price, record.volume, record.avg_volume, record.change_percent)
    price_volume_count = sum(1 for value in price_volume_fields if value is not None)
    symbol = (record.symbol or "ZZZZ").upper()
    company = (record.company_name or "").lower()
    return (
        -market_screener_data_completeness_score(record),
        -price_volume_count,
        -int(market_screener_has_ai_signal(record)),
        symbol,
        company,
    )


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
            market_screener_data_label(record),
            " ".join(record.signals),
            " ".join(record.risk_flags),
            " ".join(record.sources),
            " ".join(f"{row.field} {row.source}" for row in record.field_provenance),
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
        "shares_float": record.shares_float,
        "shares_outstanding": record.shares_outstanding,
        "next_earnings_date": record.next_earnings_date,
        "recent_filing_date": record.recent_filing_date,
        "recent_filing_type": record.recent_filing_type,
        "signals": record.signals,
        "risk_flags": record.risk_flags,
        "source_links": record.source_links,
        "source_excerpt": record.source_excerpt,
        "portfolio_quantity": record.portfolio_quantity,
        "portfolio_average_cost": record.portfolio_average_cost,
        "portfolio_market_value": record.portfolio_market_value,
        "portfolio_unrealized_pnl": record.portfolio_unrealized_pnl,
        "portfolio_weight": record.portfolio_weight,
        "market_cap_currency": record.market_cap_currency,
        "market_cap_rank_value": record.market_cap_rank_value,
        "market_cap_rank_currency": record.market_cap_rank_currency,
        "market_cap_rank_trusted": record.market_cap_rank_trusted,
        "market_cap_rank_reason": record.market_cap_rank_reason,
        "instrument_type": record.instrument_type,
        "country": record.country,
        "is_adr": record.is_adr,
        "is_etf": record.is_etf,
        "is_fund": record.is_fund,
        "is_otc": record.is_otc,
        "field_provenance": record.field_provenance,
    }
    return [field for field, value in checks.items() if value in (None, "", ())]


def _field_provenance_payload(record: MarketScreenerRecord) -> Any:
    rows = _field_provenance_from_payload(record.field_provenance)
    if not rows:
        return _not_available("No field-level provenance is present in the selected Market Intelligence Screener row.")
    payload: list[dict[str, str]] = []
    for row in rows:
        item = {
            "field": row.field,
            "source": row.source,
        }
        if row.source_detail:
            item["source_detail"] = row.source_detail
        if row.source_link:
            item["source_link"] = row.source_link
        if row.fetched_at:
            item["fetched_at"] = row.fetched_at
        payload.append(item)
    return payload


def _field_provenance_from_payload(payload: Any) -> tuple[MarketScreenerFieldProvenance, ...]:
    if not payload:
        return ()
    rows: list[MarketScreenerFieldProvenance] = []
    for item in payload if isinstance(payload, (list, tuple)) else ():
        if isinstance(item, MarketScreenerFieldProvenance):
            row = item
        elif isinstance(item, Mapping):
            row = MarketScreenerFieldProvenance.from_dict(item)
        else:
            continue
        if row.field and row.source:
            rows.append(row)
    return _dedupe_field_provenance(rows)


_SCREENER_SINGLE_VALUE_FIELDS = (
    "symbol",
    "company_name",
    "cik",
    "exchange",
    "sector",
    "industry",
    "price",
    "market_cap",
    "volume",
    "avg_volume",
    "change_percent",
    "pe_ratio",
    "eps",
    "revenue_growth",
    "shares_float",
    "shares_outstanding",
    "next_earnings_date",
    "recent_filing_date",
    "recent_filing_type",
    "source_excerpt",
    "portfolio_quantity",
    "portfolio_average_cost",
    "portfolio_market_value",
    "portfolio_unrealized_pnl",
    "portfolio_weight",
    "market_cap_currency",
    "market_cap_rank_value",
    "market_cap_rank_currency",
    "market_cap_rank_trusted",
    "market_cap_rank_reason",
    "instrument_type",
    "country",
    "is_adr",
    "is_etf",
    "is_fund",
    "is_otc",
)
_INCOMING_NUMERIC_FIELDS = {
    "price",
    "market_cap",
    "volume",
    "avg_volume",
    "change_percent",
    "pe_ratio",
    "eps",
    "revenue_growth",
    "shares_float",
    "shares_outstanding",
    "portfolio_quantity",
    "portfolio_average_cost",
    "portfolio_market_value",
    "portfolio_unrealized_pnl",
    "portfolio_weight",
    "market_cap_rank_value",
}
_MARKET_CAP_METADATA_FIELDS = {
    "market_cap_currency",
    "market_cap_rank_value",
    "market_cap_rank_currency",
    "market_cap_rank_trusted",
    "market_cap_rank_reason",
    "instrument_type",
    "country",
    "is_adr",
    "is_etf",
    "is_fund",
    "is_otc",
}


def _merge_screener_field_provenance(
    existing: MarketScreenerRecord,
    incoming: MarketScreenerRecord,
) -> tuple[MarketScreenerFieldProvenance, ...]:
    existing_by_field = _provenance_by_field(existing.field_provenance)
    incoming_by_field = _provenance_by_field(incoming.field_provenance)
    selected: list[MarketScreenerFieldProvenance] = []
    for field in _SCREENER_SINGLE_VALUE_FIELDS:
        row = incoming_by_field.get(field) if _screener_field_selected_from_incoming(existing, incoming, field) else existing_by_field.get(field)
        if row is None:
            row = existing_by_field.get(field) or incoming_by_field.get(field)
        if row is not None:
            selected.append(row)
    selected.extend(row for row in existing.field_provenance if row.field not in _SCREENER_SINGLE_VALUE_FIELDS)
    selected.extend(row for row in incoming.field_provenance if row.field not in _SCREENER_SINGLE_VALUE_FIELDS)
    return _dedupe_field_provenance(selected)


def _screener_field_selected_from_incoming(existing: MarketScreenerRecord, incoming: MarketScreenerRecord, field: str) -> bool:
    incoming_value = getattr(incoming, field)
    existing_value = getattr(existing, field)
    if field == "symbol" and _has_value(incoming_value):
        existing_provenance = _provenance_by_field(existing.field_provenance).get("symbol")
        incoming_provenance = _provenance_by_field(incoming.field_provenance).get("symbol")
        if (
            existing_provenance is not None
            and incoming_provenance is not None
            and _source_is_sec_owned(existing_provenance.source)
            and not _source_is_sec_owned(incoming_provenance.source)
        ):
            return True
    if field in _MARKET_CAP_METADATA_FIELDS and field != "market_cap_rank_value":
        return _has_value(incoming_value) and (incoming.market_cap is not None or not _has_value(existing_value))
    if field in _INCOMING_NUMERIC_FIELDS:
        return incoming_value is not None
    if field == "next_earnings_date":
        selected = _earlier_date(existing.next_earnings_date, incoming.next_earnings_date)
        return _has_value(incoming.next_earnings_date) and selected == incoming.next_earnings_date and selected != existing.next_earnings_date
    if field == "recent_filing_date":
        selected = _later_date(existing.recent_filing_date, incoming.recent_filing_date)
        return _has_value(incoming.recent_filing_date) and selected == incoming.recent_filing_date and selected != existing.recent_filing_date
    if field == "recent_filing_type":
        return _has_value(incoming.recent_filing_type)
    return not _has_value(existing_value) and _has_value(incoming_value)


def _provenance_by_field(rows: Iterable[MarketScreenerFieldProvenance]) -> dict[str, MarketScreenerFieldProvenance]:
    result: dict[str, MarketScreenerFieldProvenance] = {}
    for row in _field_provenance_from_payload(tuple(rows)):
        result.setdefault(row.field, row)
    return result


def _market_data_provenance(
    record: MarketQuoteFundamentalsRecord,
    values: Mapping[str, Any],
    *,
    source: str,
    source_link: str | None,
    fetched_at: str,
) -> tuple[MarketScreenerFieldProvenance, ...]:
    rows: list[MarketScreenerFieldProvenance] = []
    raw_rows = getattr(record, "field_provenance", ()) or ()
    for item in raw_rows if isinstance(raw_rows, (list, tuple)) else ():
        if isinstance(item, Mapping):
            field = str(item.get("field") or "").strip()
            row_source = str(item.get("source") or source).strip() or source
            row_link = _optional_text(item.get("source_url") or item.get("source_link") or source_link)
            row_detail = str(item.get("source_detail") or "market data enrichment").strip()
            row_fetched_at = str(item.get("fetched_at") or fetched_at).strip()
        else:
            field = str(getattr(item, "field", "") or "").strip()
            row_source = str(getattr(item, "source", "") or source).strip() or source
            row_link = _optional_text(getattr(item, "source_url", None) or getattr(item, "source_link", None) or source_link)
            row_detail = str(getattr(item, "source_detail", "") or "market data enrichment").strip()
            row_fetched_at = str(getattr(item, "fetched_at", "") or fetched_at).strip()
        if field and _has_value(values.get(field)):
            if field == "market_cap":
                market_cap_detail = _market_cap_source_detail(record)
                if market_cap_detail:
                    row_detail = f"{row_detail}; {market_cap_detail}" if row_detail else market_cap_detail
            rows.append(
                MarketScreenerFieldProvenance(
                    field=field,
                    source=redact_symbol_chat_secrets(row_source),
                    source_detail=redact_symbol_chat_secrets(row_detail),
                    source_link=row_link,
                    fetched_at=row_fetched_at,
                )
            )
    if _has_value(values.get("signals")):
        rows.append(MarketScreenerFieldProvenance("signals", redact_symbol_chat_secrets(source), "market data enrichment", source_link, fetched_at))
    covered_fields = {row.field for row in rows}
    fallback_values = {field: value for field, value in values.items() if field not in covered_fields}
    fallback_rows = _provenance_for_values(fallback_values, source=source, source_link=source_link, fetched_at=fetched_at, source_detail="market data enrichment")
    return _dedupe_field_provenance((*rows, *(_market_cap_provenance_with_detail(row, record) for row in fallback_rows)))


def _market_cap_provenance_with_detail(
    row: MarketScreenerFieldProvenance,
    record: MarketQuoteFundamentalsRecord,
) -> MarketScreenerFieldProvenance:
    if row.field != "market_cap":
        return row
    detail = _market_cap_source_detail(record)
    if not detail:
        return row
    source_detail = f"{row.source_detail}; {detail}" if row.source_detail else detail
    return MarketScreenerFieldProvenance(row.field, row.source, source_detail, row.source_link, row.fetched_at)


def _market_cap_source_detail(record: MarketQuoteFundamentalsRecord) -> str:
    parts: list[str] = []
    currency = _normalize_currency_code(getattr(record, "market_cap_rank_currency", None) or getattr(record, "market_cap_currency", None))
    if currency:
        parts.append(f"market cap currency={currency}")
    if getattr(record, "market_cap_rank_value", None) is not None:
        parts.append("provider supplied normalized ranking cap")
    if getattr(record, "market_cap_rank_trusted", None) is not None:
        parts.append(f"provider trusted rank={'yes' if record.market_cap_rank_trusted else 'no'}")
    instrument = str(getattr(record, "instrument_type", "") or "").strip()
    if instrument:
        parts.append(f"instrument={instrument}")
    country = str(getattr(record, "country", "") or "").strip()
    if country:
        parts.append(f"country={country}")
    flags = [name for name in ("is_adr", "is_etf", "is_fund", "is_otc") if getattr(record, name, None) is True]
    if flags:
        parts.append("flags=" + ",".join(flags))
    reason = str(getattr(record, "market_cap_rank_reason", "") or "").strip()
    if reason:
        parts.append(f"rank reason={reason}")
    return "; ".join(parts)


def _provenance_for_existing_record(record: MarketScreenerRecord, *, fetched_at: str) -> tuple[MarketScreenerFieldProvenance, ...]:
    source = record.sources[0] if record.sources else "Market Screener row"
    source_link = record.source_links[0] if record.source_links else None
    values = {
        "symbol": record.symbol,
        "company_name": record.company_name,
        "cik": record.cik,
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
        "shares_float": record.shares_float,
        "shares_outstanding": record.shares_outstanding,
        "next_earnings_date": record.next_earnings_date,
        "recent_filing_date": record.recent_filing_date,
        "recent_filing_type": record.recent_filing_type,
        "signals": record.signals,
        "risk_flags": record.risk_flags,
        "source_links": record.source_links,
        "source_excerpt": record.source_excerpt,
        "portfolio_quantity": record.portfolio_quantity,
        "portfolio_average_cost": record.portfolio_average_cost,
        "portfolio_market_value": record.portfolio_market_value,
        "portfolio_unrealized_pnl": record.portfolio_unrealized_pnl,
        "portfolio_weight": record.portfolio_weight,
        "market_cap_currency": record.market_cap_currency,
        "market_cap_rank_value": record.market_cap_rank_value,
        "market_cap_rank_currency": record.market_cap_rank_currency,
        "market_cap_rank_trusted": record.market_cap_rank_trusted,
        "market_cap_rank_reason": record.market_cap_rank_reason,
        "instrument_type": record.instrument_type,
        "country": record.country,
        "is_adr": record.is_adr,
        "is_etf": record.is_etf,
        "is_fund": record.is_fund,
        "is_otc": record.is_otc,
    }
    return _provenance_for_values(values, source=source, source_link=source_link, fetched_at=record.fetched_at or fetched_at)


def _provenance_for_values(
    values: Mapping[str, Any],
    *,
    source: str,
    source_link: str | None,
    fetched_at: str,
    source_detail: str = "",
) -> tuple[MarketScreenerFieldProvenance, ...]:
    clean_source = redact_symbol_chat_secrets(str(source or "").strip() or "Unknown source")
    clean_detail = redact_symbol_chat_secrets(str(source_detail or "").strip())
    clean_link = _optional_text(source_link)
    rows = [
        MarketScreenerFieldProvenance(field=str(field), source=clean_source, source_detail=clean_detail, source_link=clean_link, fetched_at=fetched_at)
        for field, value in values.items()
        if _has_value(value)
    ]
    return _dedupe_field_provenance(rows)


def _dedupe_field_provenance(rows: Iterable[MarketScreenerFieldProvenance]) -> tuple[MarketScreenerFieldProvenance, ...]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[MarketScreenerFieldProvenance] = []
    for row in rows:
        key = (row.field, row.source, row.source_detail, row.source_link or "", row.fetched_at)
        if not row.field or not row.source or key in seen:
            continue
        seen.add(key)
        result.append(
            MarketScreenerFieldProvenance(
                field=redact_symbol_chat_secrets(row.field),
                source=redact_symbol_chat_secrets(row.source),
                source_detail=redact_symbol_chat_secrets(row.source_detail),
                source_link=_optional_text(row.source_link),
                fetched_at=redact_symbol_chat_secrets(row.fetched_at),
            )
        )
    return tuple(result)


def _record_has_source_label(record: MarketScreenerRecord, needle: str) -> bool:
    lower = str(needle or "").lower()
    return bool(lower and lower in " ".join(record.sources).lower())


def _has_value(value: Any) -> bool:
    return value not in (None, "", ())


def _optional_text(value: Any) -> str | None:
    text = redact_symbol_chat_secrets(str(value or "").strip())
    return text or None


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
        completeness = selected.get("data_completeness") if isinstance(selected.get("data_completeness"), Mapping) else {}
        if isinstance(completeness, Mapping):
            lines.append(f"data_completeness_score={completeness.get('score')}")
        provenance = selected.get("field_provenance")
        lines.append(f"field_provenance_count={len(provenance) if isinstance(provenance, list) else 0}")
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
        if isinstance(selected, dict) and isinstance(selected.get("field_provenance"), list):
            selected["field_provenance"] = selected["field_provenance"][:40]
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


def _object_to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except Exception:
            payload = {}
        if isinstance(payload, Mapping):
            return payload
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not key.startswith("_")}
    return {"value": value}


def _redact_context_payload(value: Any) -> Any:
    return _json_safe(value)


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


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _coerce_bool(value: Any, *, default: bool) -> bool:
    parsed = _bool_or_none(value)
    return default if parsed is None else parsed


def _prefer_number(primary: float | None, fallback: float | None) -> float | None:
    return primary if primary is not None else fallback


def _prefer_market_cap_metadata(primary: Any, fallback: Any, incoming: MarketScreenerRecord) -> Any:
    if _has_value(primary):
        return primary
    if incoming.market_cap is not None and primary is not None:
        return primary
    return fallback


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
    symbol = str(value or "").strip().upper()
    symbol = _SYMBOL_ALIASES.get(symbol, symbol)
    symbol = symbol.replace("/", ".")
    symbol = _SYMBOL_ALIASES.get(symbol, symbol)
    return symbol


def _normalize_cik(value: Any) -> str:
    digits = "".join(char for char in str(value or "") if char.isdigit())
    return digits.zfill(10) if digits else ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
