from __future__ import annotations

import csv
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

import requests

from app.analytics.technical_analysis import parse_quote_snapshot
MARKET_DATA_FILE_PATH_ENV = "MARKET_SCREENER_MARKET_DATA_PATH"
MARKET_DATA_SYMBOL_LIMIT_ENV = "MARKET_SCREENER_MARKET_DATA_SYMBOL_LIMIT"
MARKET_SCREENER_BACKFILL_BATCH_SIZE_ENV = "MARKET_SCREENER_BACKFILL_BATCH_SIZE"
MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS_ENV = "MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS"
DEFAULT_MARKET_DATA_SYMBOL_LIMIT = 100
LOCAL_FILE_CACHE_TTL = timedelta(minutes=10)
FMP_API_KEY_ENV = "FMP_API_KEY"
FMP_BASE_URL_ENV = "FMP_BASE_URL"
FMP_MARKET_DATA_SYMBOL_LIMIT_ENV = "FMP_MARKET_DATA_SYMBOL_LIMIT"
FMP_CACHE_TTL_SECONDS_ENV = "FMP_CACHE_TTL_SECONDS"
MARKET_DATA_FALLBACK_PROVIDER_ENV = "MARKET_SCREENER_FALLBACK_PROVIDER"
MARKET_DATA_FALLBACK_SYMBOL_LIMIT_ENV = "MARKET_SCREENER_FALLBACK_SYMBOL_LIMIT"
ALPHA_VANTAGE_API_KEY_ENV = "ALPHA_VANTAGE_API_KEY"
ALPHA_VANTAGE_BASE_URL_ENV = "ALPHA_VANTAGE_BASE_URL"
ALPHA_VANTAGE_CACHE_TTL_SECONDS_ENV = "ALPHA_VANTAGE_CACHE_TTL_SECONDS"
DEFAULT_FMP_BASE_URL = "https://financialmodelingprep.com/stable"
DEFAULT_FMP_MARKET_DATA_SYMBOL_LIMIT = 100
MAX_FMP_MARKET_DATA_SYMBOL_LIMIT = 5000
DEFAULT_FMP_CACHE_TTL_SECONDS = 900
DEFAULT_MARKET_SCREENER_BACKFILL_BATCH_SIZE = 100
DEFAULT_MARKET_SCREENER_BACKFILL_CACHE_TTL_SECONDS = 3600
DEFAULT_ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"
DEFAULT_ALPHA_VANTAGE_FALLBACK_SYMBOL_LIMIT = 25
DEFAULT_ALPHA_VANTAGE_CACHE_TTL_SECONDS = 900
FMP_QUOTE_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/quote"
FMP_PROFILE_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/profile-symbol"
FMP_PROFILE_BY_CIK_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/profile-cik"
FMP_KEY_METRICS_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/key-metrics"
FMP_KEY_METRICS_TTM_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/key-metrics-ttm"
FMP_RATIOS_TTM_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/ratios-ttm"
FMP_HISTORICAL_PRICE_EOD_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-full"
FMP_INCOME_GROWTH_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/income-statement-growth"
FMP_FINANCIAL_GROWTH_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/financial-statement-growth"
FMP_INCOME_STATEMENT_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/income-statement"
FMP_BALANCE_SHEET_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/balance-sheet-statement"
FMP_CASH_FLOW_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/cashflow-statement"
FMP_CASH_FLOW_GROWTH_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/cashflow-statement-growth"
FMP_SHARES_FLOAT_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/shares-float"
FMP_MARKET_CAP_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/market-cap"
FMP_BATCH_MARKET_CAP_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/batch-market-cap"
FMP_SEC_FILINGS_BY_SYMBOL_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/search-by-symbol"
FMP_ECONOMIC_INDICATORS_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/economics-indicators"
FMP_COMMODITIES_QUOTE_DOC_URL = "https://site.financialmodelingprep.com/developer/docs/stable/commodities-quote"
ALPHA_VANTAGE_DOC_URL = "https://www.alphavantage.co/documentation/"
_SHARED_FMP_CACHE: dict[tuple[str, str, str], tuple[float, Mapping[str, Any]]] = {}
_SHARED_ALPHA_VANTAGE_CACHE: dict[tuple[str, str, str], tuple[float, Mapping[str, Any]]] = {}
_SYMBOL_ALIASES = {
    "BRK-B": "BRK.B",
    "BRK/B": "BRK.B",
    "BRK B": "BRK.B",
    "BF-B": "BF.B",
    "BF/B": "BF.B",
    "BF B": "BF.B",
}


@dataclass(frozen=True)
class MarketDataFieldProvenance:
    field: str
    source: str
    source_url: str | None = None
    source_detail: str = ""
    fetched_at: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MarketDataFieldProvenance":
        return cls(
            field=str(payload.get("field") or "").strip(),
            source=_optional_string(payload.get("source")) or "Market data provider",
            source_url=_optional_string(payload.get("source_url") or payload.get("url")),
            source_detail=_optional_string(payload.get("source_detail")) or "",
            fetched_at=_optional_string(payload.get("fetched_at")) or "",
        )


@dataclass(frozen=True)
class MarketQuoteFundamentalsRecord:
    symbol: str
    company_name: str | None = None
    price: float | None = None
    change_percent: float | None = None
    volume: float | None = None
    avg_volume: float | None = None
    market_cap: float | None = None
    pe_ratio: float | None = None
    eps: float | None = None
    revenue_growth: float | None = None
    source: str = "Market data provider"
    source_url: str | None = None
    fetched_at: str = ""
    exchange: str | None = None
    sector: str | None = None
    industry: str | None = None
    shares_float: float | None = None
    shares_outstanding: float | None = None
    revenue: float | None = None
    net_income: float | None = None
    operating_income: float | None = None
    diluted_eps: float | None = None
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None
    net_income_yoy: float | None = None
    operating_income_yoy: float | None = None
    diluted_eps_yoy: float | None = None
    operating_cash_flow_yoy: float | None = None
    free_cash_flow_yoy: float | None = None
    cash_and_equivalents: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_debt: float | None = None
    cash_to_liabilities: float | None = None
    liabilities_to_assets: float | None = None
    debt_to_liabilities: float | None = None
    enterprise_value: float | None = None
    ev_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    price_to_sales: float | None = None
    field_provenance: tuple[MarketDataFieldProvenance, ...] = ()
    cik: str | None = None
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
    def from_dict(cls, payload: Mapping[str, Any]) -> "MarketQuoteFundamentalsRecord":
        source = _optional_string(payload.get("source")) or "Market data provider"
        source_url = _optional_string(payload.get("source_url") or payload.get("url"))
        fetched_at = _optional_string(payload.get("fetched_at")) or _now()
        values = {
            "symbol": _normalize_symbol(_first_present(payload, "symbol", "ticker")),
            "company_name": _optional_string(_first_present(payload, "company_name", "companyName", "name", "company")),
            "exchange": _optional_string(_first_present(payload, "exchange", "exchangeShortName", "exchange_short_name")),
            "sector": _optional_string(_first_present(payload, "sector")),
            "industry": _optional_string(_first_present(payload, "industry")),
            "price": _optional_float(_first_present(payload, "price", "last", "last_price")),
            "change_percent": _optional_float(_first_present(payload, "change_percent", "percent_change", "changesPercentage", "changePercentage", "changePercent", "change_percent")),
            "volume": _optional_float(_first_present(payload, "volume", "total_volume", "totalVolume")),
            "avg_volume": _optional_float(_first_present(payload, "avg_volume", "average_volume", "avgVolume", "averageVolume")),
            "market_cap": _optional_float(_first_present(payload, "market_cap", "marketCap", "marketCapTTM", "mktCap", "marketCapitalization", "MarketCapitalization")),
            "pe_ratio": _optional_float(_first_present(payload, "pe_ratio", "pe", "peRatio", "peRatioTTM", "PERatio", "priceEarningsRatio", "priceEarningsRatioTTM", "priceToEarningsRatioTTM")),
            "eps": _optional_float(_first_present(payload, "eps", "EPS", "epsTTM", "earnings_per_share", "earningsPerShareTTM", "netIncomePerShareTTM", "epsdiluted", "epsDiluted", "dilutedEPS", "epsdilutedTTM")),
            "revenue_growth": _optional_float(_first_present(payload, "revenue_growth", "revenueGrowth", "revenueGrowthTTM", "growthRevenue", "QuarterlyRevenueGrowthYOY")),
            "shares_float": _optional_float(_first_present(payload, "shares_float", "float", "sharesFloat", "floatShares", "freeFloat")),
            "shares_outstanding": _optional_float(_first_present(payload, "shares_outstanding", "sharesOutstanding", "outstandingShares", "shares_outstanding", "weightedAverageShsOut", "weightedAverageShsOutTTM")),
            "revenue": _optional_float(_first_present(payload, "revenue", "totalRevenue", "revenueTTM")),
            "net_income": _optional_float(_first_present(payload, "net_income", "netIncome", "netIncomeTTM")),
            "operating_income": _optional_float(_first_present(payload, "operating_income", "operatingIncome", "operatingIncomeTTM")),
            "diluted_eps": _optional_float(_first_present(payload, "diluted_eps", "epsdiluted", "epsDiluted", "dilutedEPS", "epsdilutedTTM")),
            "operating_cash_flow": _optional_float(_first_present(payload, "operating_cash_flow", "operatingCashFlow", "netCashProvidedByOperatingActivities", "netCashProvidedByUsedInOperatingActivities")),
            "free_cash_flow": _optional_float(_first_present(payload, "free_cash_flow", "freeCashFlow", "freeCashFlowTTM")),
            "net_income_yoy": _optional_float(_first_present(payload, "net_income_yoy", "growthNetIncome", "netIncomeGrowth")),
            "operating_income_yoy": _optional_float(_first_present(payload, "operating_income_yoy", "growthOperatingIncome", "operatingIncomeGrowth")),
            "diluted_eps_yoy": _optional_float(_first_present(payload, "diluted_eps_yoy", "growthEPSDiluted", "epsDilutedGrowth", "dilutedEPSGrowth")),
            "operating_cash_flow_yoy": _optional_float(_first_present(payload, "operating_cash_flow_yoy", "growthOperatingCashFlow", "operatingCashFlowGrowth")),
            "free_cash_flow_yoy": _optional_float(_first_present(payload, "free_cash_flow_yoy", "growthFreeCashFlow", "freeCashFlowGrowth")),
            "cash_and_equivalents": _optional_float(_first_present(payload, "cash_and_equivalents", "cashAndCashEquivalents", "cashAndShortTermInvestments", "cashCashEquivalentsAndShortTermInvestments")),
            "total_assets": _optional_float(_first_present(payload, "total_assets", "totalAssets")),
            "total_liabilities": _optional_float(_first_present(payload, "total_liabilities", "totalLiabilities", "totalLiabilitiesNetMinorityInterest")),
            "total_debt": _optional_float(_first_present(payload, "total_debt", "totalDebt", "netDebt")),
            "cash_to_liabilities": _optional_float(_first_present(payload, "cash_to_liabilities", "cashToLiabilities")),
            "liabilities_to_assets": _optional_float(_first_present(payload, "liabilities_to_assets", "liabilitiesToAssets")),
            "debt_to_liabilities": _optional_float(_first_present(payload, "debt_to_liabilities", "debtToLiabilities")),
            "enterprise_value": _optional_float(_first_present(payload, "enterprise_value", "enterpriseValue", "enterpriseValueTTM")),
            "ev_to_sales": _optional_float(_first_present(payload, "ev_to_sales", "enterpriseValueOverRevenue", "evToSales", "evToSalesTTM")),
            "ev_to_ebitda": _optional_float(_first_present(payload, "ev_to_ebitda", "enterpriseValueOverEBITDA", "evToEbitda", "evToEBITDA", "evToEbitdaTTM")),
            "price_to_sales": _optional_float(_first_present(payload, "price_to_sales", "priceToSalesRatio", "priceToSalesRatioTTM", "priceToSales")),
            "cik": _normalize_cik(_first_present(payload, "cik", "cik_str", "CIK")) or None,
            "market_cap_currency": _normalize_currency(_first_present(payload, "market_cap_currency", "marketCapCurrency", "market_cap_reported_currency", "reportedCurrency", "currency")),
            "market_cap_rank_value": _optional_float(_first_present(payload, "market_cap_rank_value", "marketCapRankValue", "market_cap_usd", "marketCapUsd", "marketCapUSD", "marketCapUSDTTM")),
            "market_cap_rank_currency": _normalize_currency(_first_present(payload, "market_cap_rank_currency", "marketCapRankCurrency", "market_cap_rank_currency_code")),
            "market_cap_rank_trusted": _optional_bool(_first_present(payload, "market_cap_rank_trusted", "marketCapRankTrusted", "trusted_market_cap", "trustedMarketCap")),
            "market_cap_rank_reason": _optional_string(_first_present(payload, "market_cap_rank_reason", "marketCapRankReason", "market_cap_reason")),
            "instrument_type": _optional_string(_first_present(payload, "instrument_type", "instrumentType", "security_type", "securityType", "asset_type", "assetType", "type")),
            "country": _optional_string(_first_present(payload, "country", "countryName", "domicile", "listing_country", "market_cap_country")),
            "is_adr": _optional_bool(_first_present(payload, "is_adr", "isAdr", "isADR", "adr", "isDepositaryReceipt")),
            "is_etf": _optional_bool(_first_present(payload, "is_etf", "isEtf", "isETF", "etf")),
            "is_fund": _optional_bool(_first_present(payload, "is_fund", "isFund", "fund")),
            "is_otc": _optional_bool(_first_present(payload, "is_otc", "isOtc", "isOTC", "otc")),
        }
        if values["market_cap_rank_value"] is not None and not values["market_cap_rank_currency"]:
            values["market_cap_rank_currency"] = "USD"
        if not values["instrument_type"]:
            if values["is_etf"]:
                values["instrument_type"] = "ETF"
            elif values["is_fund"]:
                values["instrument_type"] = "Fund"
            elif values["is_adr"]:
                values["instrument_type"] = "ADR"
        provenance_payload = payload.get("field_provenance")
        field_provenance = _field_provenance_from_payload(provenance_payload)
        if not field_provenance:
            field_provenance = _provenance_for_values(values, source=source, source_url=source_url, fetched_at=fetched_at)
        return cls(
            **values,
            source=source,
            source_url=source_url,
            fetched_at=fetched_at,
            field_provenance=field_provenance,
        )


@dataclass(frozen=True)
class MarketDataProviderStatus:
    source: str
    status: str
    fetched_at: str
    message: str


@dataclass(frozen=True)
class MarketQuoteFundamentalsSnapshot:
    records: tuple[MarketQuoteFundamentalsRecord, ...]
    fetched_at: str
    statuses: tuple[MarketDataProviderStatus, ...]
    errors: tuple[str, ...] = ()
    diagnostics: Mapping[str, int] = field(default_factory=dict)


class MarketQuoteFundamentalsProvider(Protocol):
    provider_name: str

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        ...

    def quote_fundamentals_by_cik(
        self,
        ciks: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        ...


class LocalMarketDataFileProvider:
    provider_name = "local_market_data_file"

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        cache_ttl: timedelta = LOCAL_FILE_CACHE_TTL,
    ) -> None:
        self.path = Path(path or os.getenv(MARKET_DATA_FILE_PATH_ENV, "") or "") if (path or os.getenv(MARKET_DATA_FILE_PATH_ENV, "")) else None
        self.cache_ttl = cache_ttl
        self._cached_rows: tuple[MarketQuoteFundamentalsRecord, ...] | None = None
        self._cached_at = 0.0

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        wanted = set(_limited_symbols(symbols, max_symbols))
        if self.path is None:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Local market data file", "unavailable", fetched_at, f"No {MARKET_DATA_FILE_PATH_ENV} file is configured."),),
                diagnostics={"provider_unavailable": 1},
            )
        if not self.path.exists():
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Local market data file", "unavailable", fetched_at, f"Configured market data file does not exist: {self.path}"),),
                diagnostics={"provider_unavailable": 1},
            )

        try:
            rows = self._read_rows(force_refresh=force_refresh)
        except Exception as exc:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Local market data file", "error", fetched_at, f"Could not read local market data file: {exc}"),),
                errors=(str(exc),),
                diagnostics={"provider_unavailable": 1},
            )
        filtered = tuple(record for record in rows if not wanted or record.symbol in wanted)
        enriched = sum(1 for record in filtered if _quote_record_has_any_value(record))
        return MarketQuoteFundamentalsSnapshot(
            records=filtered,
            fetched_at=fetched_at,
            statuses=(
                MarketDataProviderStatus(
                    "Local market data file",
                    "available" if filtered else "empty",
                    fetched_at,
                    f"Loaded {len(filtered)} quote/fundamental row(s) from {self.path}.",
                ),
            ),
            diagnostics={
                "rows_enriched_by_local_file": enriched,
                "rows_provider_returned_no_usable_data": max(0, len(wanted) - enriched) if wanted else 0,
            },
        )

    def _read_rows(self, *, force_refresh: bool) -> tuple[MarketQuoteFundamentalsRecord, ...]:
        now = time.time()
        if self._cached_rows is not None and not force_refresh and now - self._cached_at <= self.cache_ttl.total_seconds():
            return self._cached_rows
        assert self.path is not None
        if self.path.suffix.lower() == ".json":
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            raw_rows = payload.get("records", payload) if isinstance(payload, dict) else payload
            if not isinstance(raw_rows, list):
                raise ValueError("JSON market data file must contain a list or a {'records': [...]} object.")
            rows = tuple(record for record in (MarketQuoteFundamentalsRecord.from_dict(row) for row in raw_rows if isinstance(row, Mapping)) if record.symbol)
        elif self.path.suffix.lower() in {".csv", ".tsv"}:
            delimiter = "\t" if self.path.suffix.lower() == ".tsv" else ","
            with self.path.open("r", encoding="utf-8", newline="") as handle:
                rows = tuple(record for record in (MarketQuoteFundamentalsRecord.from_dict(row) for row in csv.DictReader(handle, delimiter=delimiter)) if record.symbol)
        else:
            raise ValueError("Local market data file must be .json, .csv, or .tsv.")
        self._cached_rows = rows
        self._cached_at = now
        return rows


class SchwabQuoteFundamentalsProvider:
    provider_name = "schwab_quote"

    def __init__(self, schwab_session: Any | None) -> None:
        self.schwab_session = schwab_session

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        get_quote = getattr(self.schwab_session, "get_quote", None)
        if not callable(get_quote):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Schwab quote", "unavailable", fetched_at, "No authenticated Schwab market-data session is available."),),
                diagnostics={"provider_unavailable": 1},
            )
        records: list[MarketQuoteFundamentalsRecord] = []
        errors: list[str] = []
        blocked = 0
        no_usable = 0
        requested = _limited_symbols(symbols, max_symbols)
        for symbol in requested:
            try:
                status_code, payload = get_quote(symbol)
                if int(status_code) != 200:
                    errors.append(f"{symbol}: Schwab quote returned HTTP {status_code}")
                    if int(status_code) in {401, 403, 429}:
                        blocked += 1
                    continue
                snapshot = parse_quote_snapshot(symbol, payload)
                price = snapshot.last or snapshot.mark
                if price is None and snapshot.total_volume is None:
                    errors.append(f"{symbol}: Schwab quote payload had no usable price or volume fields")
                    no_usable += 1
                    continue
                records.append(
                    MarketQuoteFundamentalsRecord(
                        symbol=symbol,
                        price=price,
                        volume=snapshot.total_volume,
                        source="Schwab quote",
                        fetched_at=fetched_at,
                    )
                )
            except Exception as exc:
                errors.append(f"{symbol}: {exc}")
        status = "available" if records else "empty"
        message = f"Loaded {len(records)} Schwab quote row(s) for {len(requested)} capped screener symbol(s)."
        if errors:
            status = "partial" if records else "error"
            message += f" {len(errors)} quote lookup(s) failed."
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(records),
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("Schwab quote", status, fetched_at, message),),
            errors=tuple(errors),
            diagnostics={
                "rows_enriched_by_schwab_quote": len(records),
                "rows_blocked_by_provider_plan_rate_auth_limit": blocked,
                "rows_provider_returned_no_usable_data": no_usable,
            },
        )


class FmpProviderWarning(RuntimeError):
    """Nonblocking FMP provider warning safe to show in source/status UI."""


class FmpQuoteFundamentalsProvider:
    provider_name = "fmp_quote_fundamentals"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        session: requests.Session | Any | None = None,
        timeout_seconds: float = 8.0,
        symbol_limit: int | None = None,
        cache_ttl_seconds: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv(FMP_API_KEY_ENV, "")
        self.base_url = (base_url or os.getenv(FMP_BASE_URL_ENV, DEFAULT_FMP_BASE_URL) or DEFAULT_FMP_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.symbol_limit = (
            max(0, min(MAX_FMP_MARKET_DATA_SYMBOL_LIMIT, int(symbol_limit)))
            if symbol_limit is not None
            else _configured_int(FMP_MARKET_DATA_SYMBOL_LIMIT_ENV, DEFAULT_FMP_MARKET_DATA_SYMBOL_LIMIT, minimum=0, maximum=MAX_FMP_MARKET_DATA_SYMBOL_LIMIT)
        )
        self.cache_ttl_seconds = (
            max(0, int(cache_ttl_seconds))
            if cache_ttl_seconds is not None
            else _configured_int(FMP_CACHE_TTL_SECONDS_ENV, DEFAULT_FMP_CACHE_TTL_SECONDS, minimum=0, maximum=86_400)
        )
        self.batch_size = (
            max(1, int(batch_size))
            if batch_size is not None
            else _configured_int(MARKET_SCREENER_BACKFILL_BATCH_SIZE_ENV, DEFAULT_MARKET_SCREENER_BACKFILL_BATCH_SIZE, minimum=1, maximum=500)
        )
        self._cache: dict[tuple[str, str, str], tuple[float, Mapping[str, Any]]] = {} if session is not None else _SHARED_FMP_CACHE

    def profile_classification(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        capped_input = _limited_symbols(symbols, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))
        unavailable = self._unavailable_snapshot(
            source="FMP profile/classification",
            fetched_at=fetched_at,
            requested_count=len(requested),
            skipped_limited=skipped_limited,
            disabled_message="FMP profile/classification enrichment is disabled because the symbol cap is 0.",
        )
        if unavailable is not None:
            return unavailable

        records: list[MarketQuoteFundamentalsRecord] = []
        warnings: list[str] = []
        cache_hits = 0
        calls_attempted = 0
        rows_returned = 0
        try:
            payloads, cache_hits, calls_attempted, rows_returned = self._profile_payloads(requested, force_refresh=force_refresh)
        except FmpProviderWarning as exc:
            warnings.append(str(exc))
            payloads = {}
        for symbol in requested:
            payload = payloads.get(symbol)
            if not payload:
                continue
            record = self._record_from_payload(symbol, payload, source="FMP profile", source_url=FMP_PROFILE_DOC_URL, fetched_at=fetched_at)
            record = _record_with_family_fields(record, "profile_classification")
            if _quote_record_has_any_value(record):
                records.append(record)

        return self._family_snapshot(
            source="FMP profile/classification",
            fetched_at=fetched_at,
            records=tuple(records),
            requested_count=len(requested),
            returned_count=rows_returned,
            calls_attempted=calls_attempted,
            cache_hits=cache_hits,
            warnings=warnings,
            skipped_limited=skipped_limited,
            extra_diagnostics={"rows_enriched_by_fmp_profile": len(records)},
            detail="profile/classification fields only",
        )

    def quote_tape(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        capped_input = _limited_symbols(symbols, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))
        unavailable = self._unavailable_snapshot(
            source="FMP quote/tape",
            fetched_at=fetched_at,
            requested_count=len(requested),
            skipped_limited=skipped_limited,
            disabled_message="FMP quote/tape enrichment is disabled because the symbol cap is 0.",
        )
        if unavailable is not None:
            return unavailable

        warnings: list[str] = []
        cache_hits = 0
        calls_attempted = 0
        rows_returned = 0
        records: list[MarketQuoteFundamentalsRecord] = []
        try:
            payloads, cache_hits, _endpoint, calls_attempted, rows_returned = self._quote_payloads(requested, force_refresh=force_refresh)
        except FmpProviderWarning as exc:
            warnings.append(str(exc))
            payloads = {}
        for symbol, payload in payloads.items():
            record = self._record_from_payload(symbol, payload, source="FMP quote", source_url=FMP_QUOTE_DOC_URL, fetched_at=fetched_at)
            record = _record_with_family_fields(record, "quote_tape")
            if _quote_record_has_any_value(record):
                records.append(record)

        return self._family_snapshot(
            source="FMP quote/tape",
            fetched_at=fetched_at,
            records=tuple(records),
            requested_count=len(requested),
            returned_count=rows_returned,
            calls_attempted=calls_attempted,
            cache_hits=cache_hits,
            warnings=warnings,
            skipped_limited=skipped_limited,
            extra_diagnostics={"rows_enriched_by_fmp_quote": len(records)},
            detail="quote/tape fields only",
        )

    def fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        capped_input = _limited_symbols(symbols, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))
        unavailable = self._unavailable_snapshot(
            source="FMP fundamentals",
            fetched_at=fetched_at,
            requested_count=len(requested),
            skipped_limited=skipped_limited,
            disabled_message="FMP fundamentals enrichment is disabled because the symbol cap is 0.",
        )
        if unavailable is not None:
            return unavailable

        records_by_symbol: dict[str, MarketQuoteFundamentalsRecord] = {}
        warnings: list[str] = []
        cache_hits = 0
        calls_attempted = 0
        rows_returned = 0
        endpoint_rows = {
            "market-capitalization": 0,
            "key-metrics": 0,
            "key-metrics-ttm": 0,
            "ratios-ttm": 0,
            "income-statement-growth": 0,
            "financial-growth": 0,
            "income-statement": 0,
            "balance-sheet-statement": 0,
            "cash-flow-statement": 0,
            "cash-flow-statement-growth": 0,
            "shares-float": 0,
        }
        market_cap_payloads: dict[str, Mapping[str, Any]] = {}
        try:
            market_cap_payloads, market_cap_cache_hits, _market_cap_endpoint, market_cap_calls, market_cap_returned = self._market_cap_payloads(
                requested,
                force_refresh=force_refresh,
            )
            cache_hits += market_cap_cache_hits
            calls_attempted += market_cap_calls
            rows_returned += market_cap_returned
        except FmpProviderWarning as exc:
            warnings.append(str(exc))
            if _is_fmp_limit_warning(str(exc)):
                market_cap_payloads = {}
                requested = ()
        for symbol, payload in market_cap_payloads.items():
            endpoint_record = self._record_from_payload(
                symbol,
                payload,
                source="FMP market cap",
                source_url=FMP_BATCH_MARKET_CAP_DOC_URL,
                fetched_at=fetched_at,
            )
            endpoint_record = _record_with_family_fields(endpoint_record, "fundamentals")
            if endpoint_record.market_cap is None:
                continue
            endpoint_rows["market-capitalization"] += 1
            existing = records_by_symbol.get(symbol)
            records_by_symbol[symbol] = endpoint_record if existing is None else _merge_quote_records(existing, endpoint_record)
        for symbol in requested:
            for endpoint, source, source_url in (*_FMP_DEEP_ENDPOINTS, *_FMP_STATEMENT_ENDPOINTS):
                existing = records_by_symbol.get(symbol)
                if existing is not None and not _record_needs_fmp_endpoint(existing, endpoint):
                    continue
                try:
                    payload, endpoint_cache_hit, endpoint_calls, endpoint_returned = self._single_symbol_payload(endpoint, symbol, force_refresh=force_refresh)
                    cache_hits += int(endpoint_cache_hit)
                    calls_attempted += endpoint_calls
                    rows_returned += endpoint_returned
                except FmpProviderWarning as exc:
                    warnings.append(str(exc))
                    if _is_fmp_limit_warning(str(exc)):
                        break
                    continue
                if not payload:
                    continue
                endpoint_record = self._record_from_payload(symbol, payload, source=source, source_url=source_url, fetched_at=fetched_at)
                endpoint_record = _record_with_family_fields(endpoint_record, "fundamentals")
                if not _quote_record_has_any_value(endpoint_record):
                    continue
                endpoint_rows[endpoint] += 1
                existing = records_by_symbol.get(symbol)
                records_by_symbol[symbol] = endpoint_record if existing is None else _merge_quote_records(existing, endpoint_record)
            if warnings and _is_fmp_limit_warning(warnings[-1]):
                break

        records = tuple(records_by_symbol.values())
        return self._family_snapshot(
            source="FMP fundamentals",
            fetched_at=fetched_at,
            records=records,
            requested_count=len(requested),
            returned_count=rows_returned,
            calls_attempted=calls_attempted,
            cache_hits=cache_hits,
            warnings=warnings,
            skipped_limited=skipped_limited,
            extra_diagnostics={
                "rows_enriched_by_fmp_market_cap": endpoint_rows["market-capitalization"],
                "rows_enriched_by_fmp_key_metrics": endpoint_rows["key-metrics"] + endpoint_rows["key-metrics-ttm"],
                "rows_enriched_by_fmp_ratios": endpoint_rows["ratios-ttm"],
                "rows_enriched_by_fmp_income_growth": endpoint_rows["income-statement-growth"],
                "rows_enriched_by_fmp_financial_growth": endpoint_rows["financial-growth"],
                "rows_enriched_by_fmp_income_statement": endpoint_rows["income-statement"],
                "rows_enriched_by_fmp_balance_sheet": endpoint_rows["balance-sheet-statement"],
                "rows_enriched_by_fmp_cash_flow": endpoint_rows["cash-flow-statement"],
                "rows_enriched_by_fmp_cash_flow_growth": endpoint_rows["cash-flow-statement-growth"],
                "rows_enriched_by_fmp_shares_float": endpoint_rows["shares-float"],
            },
            detail="fundamental/metrics fields only",
        )

    def all_market_data(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        return self.quote_fundamentals(symbols, force_refresh=force_refresh, max_symbols=max_symbols)

    def market_cap_rankings(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        capped_input = _limited_symbols(symbols, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))

        if not _optional_string(self.api_key):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP top-market-cap ranking",
                        "unavailable",
                        fetched_at,
                        f"FMP top-market-cap ranking: 0 rows updated; {skipped_limited} skipped/limited. No {FMP_API_KEY_ENV} is configured.",
                    ),
                ),
                diagnostics={
                    "provider_unavailable": 1,
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                },
            )

        if max_symbols <= 0 or self.symbol_limit <= 0:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP top-market-cap ranking",
                        "disabled",
                        fetched_at,
                        f"FMP top-market-cap ranking: 0 rows updated; {len(capped_input)} skipped/limited because the symbol cap is 0.",
                    ),
                ),
                diagnostics={"rows_skipped_by_configured_symbol_cap": len(capped_input)},
            )

        warnings: list[str] = []
        cache_hits = 0
        calls_attempted = 0
        rows_returned = 0
        payloads: dict[str, Mapping[str, Any]] = {}
        endpoint = "market-capitalization"
        try:
            payloads, cache_hits, endpoint, calls_attempted, rows_returned = self._market_cap_payloads(
                requested,
                force_refresh=force_refresh,
            )
        except FmpProviderWarning as exc:
            warnings.append(str(exc))

        records: list[MarketQuoteFundamentalsRecord] = []
        for symbol, payload in payloads.items():
            record = self._record_from_payload(
                symbol,
                payload,
                source="FMP top-market-cap ranking",
                source_url=FMP_BATCH_MARKET_CAP_DOC_URL if endpoint == "market-capitalization-batch" else FMP_MARKET_CAP_DOC_URL,
                fetched_at=fetched_at,
            )
            if record.market_cap is None:
                continue
            values = record.to_dict()
            values["market_cap_rank_value"] = record.market_cap_rank_value if record.market_cap_rank_value is not None else record.market_cap
            values["market_cap_rank_currency"] = record.market_cap_rank_currency or record.market_cap_currency or "USD"
            values["market_cap_currency"] = record.market_cap_currency or values["market_cap_rank_currency"]
            values["market_cap_rank_trusted"] = record.market_cap_rank_trusted if record.market_cap_rank_trusted is not None else True
            values["market_cap_rank_reason"] = record.market_cap_rank_reason or "FMP market-cap endpoint used for pre-truncation ranking"
            record = _record_with_family_fields(MarketQuoteFundamentalsRecord.from_dict(values), "fundamentals")
            records.append(record)

        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        no_usable_rows = max(0, len(requested) - len(records))
        message = (
            f"FMP top-market-cap ranking: {len(records)} rows updated from {len(requested)} requested symbol(s); "
            f"endpoint {endpoint}; cache used for {cache_hits}; {skipped_limited} skipped/limited; {no_usable_rows} no usable market-cap row(s). "
            f"FMP cap is {self.symbol_limit} symbol(s) via {FMP_MARKET_DATA_SYMBOL_LIMIT_ENV}."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)}"
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(records),
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("FMP top-market-cap ranking", status, fetched_at, _redact_fmp_secret(message, self.api_key)),),
            errors=tuple(_redact_fmp_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "rows_enriched_by_fmp_market_cap": len(records),
                "fmp_cache_hits": cache_hits,
                "provider_rows_requested": len(requested),
                "provider_rows_returned": rows_returned,
                "provider_rows_parsed": len(records),
                "provider_rows_updated": len(records),
                "provider_cache_hits": cache_hits,
                "provider_warnings": len(warnings),
                "provider_calls_attempted": calls_attempted,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_fmp_limit_warning(warning) for warning in warnings) else 0,
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "rows_provider_returned_no_usable_data": no_usable_rows,
            },
        )

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        capped_input = _limited_symbols(symbols, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))

        if not _optional_string(self.api_key):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP quote/fundamentals",
                        "unavailable",
                        fetched_at,
                        f"FMP enrichment: 0 rows updated; {skipped_limited} skipped/limited; cache used for 0. No {FMP_API_KEY_ENV} is configured; Schwab/local providers remain available.",
                    ),
                ),
                diagnostics={
                    "provider_unavailable": 1,
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                },
            )

        if max_symbols <= 0 or self.symbol_limit <= 0:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP quote/fundamentals",
                        "disabled",
                        fetched_at,
                        f"FMP enrichment: 0 rows updated; {len(capped_input)} skipped/limited; cache used for 0. FMP enrichment is disabled because the symbol cap is 0.",
                    ),
                ),
                diagnostics={"rows_skipped_by_configured_symbol_cap": len(capped_input)},
            )

        records_by_symbol: dict[str, MarketQuoteFundamentalsRecord] = {}
        warnings: list[str] = []
        cache_hits = 0
        quote_rows = 0
        profile_rows = 0
        key_metrics_rows = 0
        ratios_rows = 0
        growth_rows = 0
        financial_growth_rows = 0
        shares_float_rows = 0
        market_cap_rows = 0
        historical_eod_rows = 0
        income_statement_rows = 0
        calls_attempted = 0
        provider_rows_returned = 0

        quote_payloads: dict[str, Mapping[str, Any]] = {}
        quote_endpoint = "none"
        quote_blocked = False
        try:
            quote_payloads, quote_cache_hits, quote_endpoint, quote_calls, quote_returned = self._quote_payloads(requested, force_refresh=force_refresh)
            cache_hits += quote_cache_hits
            calls_attempted += quote_calls
            provider_rows_returned += quote_returned
        except FmpProviderWarning as exc:
            warnings.append(str(exc))
            quote_blocked = _is_fmp_limit_warning(str(exc))

        for symbol, payload in quote_payloads.items():
            record = self._record_from_payload(symbol, payload, source="FMP quote", source_url=FMP_QUOTE_DOC_URL, fetched_at=fetched_at)
            if _quote_record_has_any_value(record):
                records_by_symbol[symbol] = record
                quote_rows += 1

        profile_symbols = () if quote_blocked else requested
        for symbol in profile_symbols:
            try:
                profile_payload, profile_cache_hit, _profile_calls, _profile_returned = self._profile_payload(symbol, force_refresh=force_refresh)
                cache_hits += int(profile_cache_hit)
                calls_attempted += _profile_calls
                provider_rows_returned += _profile_returned
            except FmpProviderWarning as exc:
                warnings.append(str(exc))
                if _is_fmp_limit_warning(str(exc)):
                    break
                continue
            if not profile_payload:
                continue
            profile_record = self._record_from_payload(symbol, profile_payload, source="FMP profile", source_url=FMP_PROFILE_DOC_URL, fetched_at=fetched_at)
            if not _quote_record_has_any_value(profile_record):
                continue
            profile_rows += 1
            existing = records_by_symbol.get(symbol)
            records_by_symbol[symbol] = profile_record if existing is None else _merge_quote_records(existing, profile_record)
        fmp_blocked = quote_blocked or any(_is_fmp_limit_warning(warning) for warning in warnings)
        if not fmp_blocked:
            market_cap_symbols = tuple(
                symbol
                for symbol in requested
                if records_by_symbol.get(symbol) is None or records_by_symbol[symbol].market_cap is None
            )
            if market_cap_symbols:
                try:
                    market_cap_payloads, market_cap_cache_hits, market_cap_endpoint, market_cap_calls, market_cap_returned = self._market_cap_payloads(
                        market_cap_symbols,
                        force_refresh=force_refresh,
                    )
                    cache_hits += market_cap_cache_hits
                    calls_attempted += market_cap_calls
                    provider_rows_returned += market_cap_returned
                except FmpProviderWarning as exc:
                    warnings.append(str(exc))
                    if _is_fmp_limit_warning(str(exc)):
                        fmp_blocked = True
                    market_cap_payloads = {}
                    market_cap_endpoint = "market-capitalization"
                for symbol, payload in market_cap_payloads.items():
                    endpoint_record = self._record_from_payload(
                        symbol,
                        payload,
                        source="FMP market cap",
                        source_url=FMP_BATCH_MARKET_CAP_DOC_URL if market_cap_endpoint == "market-capitalization-batch" else FMP_MARKET_CAP_DOC_URL,
                        fetched_at=fetched_at,
                    )
                    endpoint_record = _record_with_family_fields(endpoint_record, "fundamentals")
                    if endpoint_record.market_cap is None:
                        continue
                    market_cap_rows += 1
                    existing = records_by_symbol.get(symbol)
                    records_by_symbol[symbol] = endpoint_record if existing is None else _merge_quote_records(existing, endpoint_record)

        if not fmp_blocked:
            for symbol in requested:
                existing = records_by_symbol.get(symbol)
                if existing is not None and existing.avg_volume is not None:
                    continue
                try:
                    historical_payload, historical_cache_hit, historical_calls, historical_returned = self._historical_eod_payload(symbol, force_refresh=force_refresh)
                    cache_hits += int(historical_cache_hit)
                    calls_attempted += historical_calls
                    provider_rows_returned += historical_returned
                except FmpProviderWarning as exc:
                    warnings.append(str(exc))
                    if _is_fmp_limit_warning(str(exc)):
                        fmp_blocked = True
                        break
                    continue
                if not historical_payload:
                    continue
                historical_record = self._record_from_payload(symbol, historical_payload, source="FMP historical EOD", source_url=FMP_HISTORICAL_PRICE_EOD_DOC_URL, fetched_at=fetched_at)
                historical_record = _record_with_family_fields(historical_record, "quote_tape")
                if historical_record.avg_volume is None and historical_record.change_percent is None and historical_record.volume is None and historical_record.price is None:
                    continue
                historical_eod_rows += 1
                existing = records_by_symbol.get(symbol)
                records_by_symbol[symbol] = historical_record if existing is None else _merge_quote_records(existing, historical_record)

        if not fmp_blocked:
            for symbol in requested:
                existing = records_by_symbol.get(symbol)
                if existing is not None and not _record_needs_deeper_fmp_fields(existing):
                    continue
                for endpoint, source, source_url in (*_FMP_DEEP_ENDPOINTS, *_FMP_VISIBLE_STATEMENT_ENDPOINTS):
                    existing = records_by_symbol.get(symbol)
                    if existing is not None and not _record_needs_visible_fmp_endpoint(existing, endpoint):
                        continue
                    try:
                        payload, endpoint_cache_hit, _endpoint_calls, _endpoint_returned = self._single_symbol_payload(endpoint, symbol, force_refresh=force_refresh)
                        cache_hits += int(endpoint_cache_hit)
                        calls_attempted += _endpoint_calls
                        provider_rows_returned += _endpoint_returned
                    except FmpProviderWarning as exc:
                        warnings.append(str(exc))
                        if _is_fmp_limit_warning(str(exc)):
                            break
                        continue
                    if not payload:
                        continue
                    endpoint_record = self._record_from_payload(symbol, payload, source=source, source_url=source_url, fetched_at=fetched_at)
                    if not _quote_record_has_any_value(endpoint_record):
                        continue
                    if endpoint in {"key-metrics", "key-metrics-ttm"}:
                        key_metrics_rows += 1
                    elif endpoint == "ratios-ttm":
                        ratios_rows += 1
                    elif endpoint == "income-statement-growth":
                        growth_rows += 1
                    elif endpoint == "financial-growth":
                        financial_growth_rows += 1
                    elif endpoint == "shares-float":
                        shares_float_rows += 1
                    elif endpoint == "income-statement":
                        income_statement_rows += 1
                    existing = records_by_symbol.get(symbol)
                    records_by_symbol[symbol] = endpoint_record if existing is None else _merge_quote_records(existing, endpoint_record)
                if warnings and _is_fmp_limit_warning(warnings[-1]):
                    break

        records = tuple(records_by_symbol.values())
        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        no_usable_rows = max(0, len(requested) - len(records))
        paid_mode_text = (
            "paid/high-cap FMP mode is active"
            if self.symbol_limit >= DEFAULT_FMP_MARKET_DATA_SYMBOL_LIMIT
            else "bounded FMP mode is active"
        )
        message = (
            f"FMP enrichment: {len(records)} rows updated; quote rows {quote_rows}; profile rows {profile_rows}; "
            f"market cap {market_cap_rows}; historical EOD {historical_eod_rows}; key metrics {key_metrics_rows}; ratios {ratios_rows}; growth {growth_rows}; "
            f"financial-growth {financial_growth_rows}; income statement {income_statement_rows}; shares-float {shares_float_rows}; "
            f"profile-by-CIK rows 0; quote endpoint {quote_endpoint}; cache used for {cache_hits}; {skipped_limited} skipped/limited; {no_usable_rows} no usable data. "
            f"FMP cap is {self.symbol_limit} symbol(s) via {FMP_MARKET_DATA_SYMBOL_LIMIT_ENV}; {paid_mode_text}."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)} Schwab/local providers remain available."

        return MarketQuoteFundamentalsSnapshot(
            records=records,
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("FMP quote/fundamentals", status, fetched_at, _redact_fmp_secret(message, self.api_key)),),
            errors=tuple(_redact_fmp_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "rows_enriched_by_fmp_quote": quote_rows,
                "rows_enriched_by_fmp_profile": profile_rows,
                "rows_enriched_by_fmp_market_cap": market_cap_rows,
                "rows_enriched_by_fmp_historical_eod": historical_eod_rows,
                "rows_enriched_by_fmp_key_metrics": key_metrics_rows,
                "rows_enriched_by_fmp_ratios": ratios_rows,
                "rows_enriched_by_fmp_income_growth": growth_rows,
                "rows_enriched_by_fmp_financial_growth": financial_growth_rows,
                "rows_enriched_by_fmp_income_statement": income_statement_rows,
                "rows_enriched_by_fmp_shares_float": shares_float_rows,
                "fmp_cache_hits": cache_hits,
                "provider_rows_requested": len(requested),
                "provider_rows_returned": provider_rows_returned,
                "provider_rows_parsed": len(records),
                "provider_rows_updated": len(records),
                "provider_cache_hits": cache_hits,
                "provider_warnings": len(warnings),
                "provider_calls_attempted": calls_attempted,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_fmp_limit_warning(warning) for warning in warnings) else 0,
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "rows_provider_returned_no_usable_data": no_usable_rows,
            },
        )

    def quote_fundamentals_by_cik(
        self,
        ciks: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        capped_input = _limited_ciks(ciks, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))

        if not _optional_string(self.api_key):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP profile-by-CIK",
                        "unavailable",
                        fetched_at,
                        f"FMP profile-by-CIK: 0 rows updated; {skipped_limited} skipped/limited. No {FMP_API_KEY_ENV} is configured.",
                    ),
                ),
                diagnostics={
                    "provider_unavailable": 1,
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                },
            )

        if max_symbols <= 0 or self.symbol_limit <= 0:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "FMP profile-by-CIK",
                        "disabled",
                        fetched_at,
                        f"FMP profile-by-CIK: 0 rows updated; {len(capped_input)} skipped/limited because the symbol cap is 0.",
                    ),
                ),
                diagnostics={"rows_skipped_by_configured_symbol_cap": len(capped_input)},
            )

        records: list[MarketQuoteFundamentalsRecord] = []
        warnings: list[str] = []
        cache_hits = 0
        calls_attempted = 0
        provider_rows_returned = 0
        for cik in requested:
            try:
                payload, cache_hit, endpoint_calls, endpoint_returned = self._profile_by_cik_payload(cik, force_refresh=force_refresh)
                cache_hits += int(cache_hit)
                calls_attempted += endpoint_calls
                provider_rows_returned += endpoint_returned
            except FmpProviderWarning as exc:
                warnings.append(str(exc))
                if _is_fmp_limit_warning(str(exc)):
                    break
                continue
            if not payload:
                continue
            symbol = _normalize_symbol(_first_present(payload, "symbol", "ticker"))
            record = self._record_from_payload(
                symbol,
                payload,
                source="FMP profile-by-CIK",
                source_url=FMP_PROFILE_BY_CIK_DOC_URL,
                fetched_at=fetched_at,
                cik=cik,
            )
            if _quote_record_has_any_value(record) or record.symbol:
                records.append(record)

        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        no_usable_rows = max(0, len(requested) - len(records))
        message = (
            f"FMP profile-by-CIK: {len(records)} rows updated; cache used for {cache_hits}; {skipped_limited} skipped/limited; {no_usable_rows} no usable data. "
            "CIK lookups are only requested for capped filing rows that still need trusted identity/profile data."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)}"

        return MarketQuoteFundamentalsSnapshot(
            records=tuple(records),
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("FMP profile-by-CIK", status, fetched_at, _redact_fmp_secret(message, self.api_key)),),
            errors=tuple(_redact_fmp_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "rows_enriched_by_fmp_profile_by_cik": len(records),
                "fmp_cache_hits": cache_hits,
                "provider_rows_requested": len(requested),
                "provider_rows_returned": provider_rows_returned,
                "provider_rows_parsed": len(records),
                "provider_rows_updated": len(records),
                "provider_cache_hits": cache_hits,
                "provider_warnings": len(warnings),
                "provider_calls_attempted": calls_attempted,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_fmp_limit_warning(warning) for warning in warnings) else 0,
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "rows_provider_returned_no_usable_data": no_usable_rows,
            },
        )

    def filing_metadata(
        self,
        symbol: str,
        *,
        force_refresh: bool = False,
        limit: int = 12,
    ) -> tuple[dict[str, Any], ...]:
        clean_symbol = _normalize_symbol(symbol)
        row_limit = max(1, min(100, int(limit or 12)))
        if not clean_symbol or not _optional_string(self.api_key):
            return ()
        endpoint = "sec-filings-search/symbol"
        cached = self._cache_get(endpoint, clean_symbol, force_refresh=force_refresh)
        if cached is not None:
            cached_rows = cached.get("rows")
            if isinstance(cached_rows, list):
                return tuple(dict(row) for row in cached_rows if isinstance(row, Mapping))[:row_limit]
        to_date = datetime.now(timezone.utc).date()
        from_date = to_date - timedelta(days=370)
        payload = self._get_json(
            endpoint,
            {
                "symbol": clean_symbol,
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "page": "0",
                "limit": str(row_limit),
            },
        )
        rows = _coerce_fmp_rows(payload)
        normalized = tuple(
            row
            for row in (_normalize_fmp_filing_metadata_row(row, clean_symbol) for row in rows)
            if row
        )[:row_limit]
        self._cache_set(endpoint, clean_symbol, {"rows": list(normalized)})
        return normalized

    def sec_filings(
        self,
        symbol: str,
        *,
        force_refresh: bool = False,
        limit: int = 12,
    ) -> tuple[dict[str, Any], ...]:
        return self.filing_metadata(symbol, force_refresh=force_refresh, limit=limit)

    def macro_context(
        self,
        symbol: str | None = None,
        *,
        force_refresh: bool = False,
        limit: int = 2,
    ) -> dict[str, dict[str, Any]]:
        row_limit = max(1, min(10, int(limit or 2)))
        if not _optional_string(self.api_key):
            return {}
        context: dict[str, dict[str, Any]] = {}
        for name, category in _FMP_MACRO_INDICATORS:
            endpoint = "economic-indicators"
            cache_key = f"indicator:{name}"
            cached = self._cache_get(endpoint, cache_key, force_refresh=force_refresh)
            if cached is None:
                payload = self._get_json(endpoint, {"name": name})
                rows = _coerce_fmp_rows(payload)[:row_limit]
                cached = {"rows": list(rows)}
                self._cache_set(endpoint, cache_key, cached)
            rows = _coerce_fmp_rows(cached.get("rows") if isinstance(cached, Mapping) else cached)
            row = _normalize_fmp_macro_indicator_rows(name, category, rows)
            if row:
                context[name] = row
        for commodity_symbol, category, label in _FMP_MACRO_COMMODITY_SYMBOLS:
            endpoint = "quote"
            cache_key = f"commodity:{commodity_symbol}"
            cached = self._cache_get(endpoint, cache_key, force_refresh=force_refresh)
            if cached is None:
                payload = self._get_json(endpoint, {"symbol": commodity_symbol})
                rows = _coerce_fmp_rows(payload)[:1]
                cached = {"rows": list(rows)}
                self._cache_set(endpoint, cache_key, cached)
            rows = _coerce_fmp_rows(cached.get("rows") if isinstance(cached, Mapping) else cached)
            row = _normalize_fmp_macro_quote_row(commodity_symbol, category, label, rows[0] if rows else {})
            if row:
                context[commodity_symbol] = row
        return context

    def _unavailable_snapshot(
        self,
        *,
        source: str,
        fetched_at: str,
        requested_count: int,
        skipped_limited: int,
        disabled_message: str,
    ) -> MarketQuoteFundamentalsSnapshot | None:
        if not _optional_string(self.api_key):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        source,
                        "unavailable",
                        fetched_at,
                        f"{source}: 0 rows updated; requested {requested_count}; {skipped_limited} skipped/limited; cache used for 0. No {FMP_API_KEY_ENV} is configured.",
                    ),
                ),
                diagnostics={
                    "provider_unavailable": 1,
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                    "provider_rows_requested": requested_count,
                },
            )
        if requested_count <= 0 or self.symbol_limit <= 0:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        source,
                        "disabled",
                        fetched_at,
                        f"{source}: 0 rows updated; {skipped_limited} skipped/limited. {disabled_message}",
                    ),
                ),
                diagnostics={
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                    "provider_rows_requested": requested_count,
                },
            )
        return None

    def _family_snapshot(
        self,
        *,
        source: str,
        fetched_at: str,
        records: tuple[MarketQuoteFundamentalsRecord, ...],
        requested_count: int,
        returned_count: int,
        calls_attempted: int,
        cache_hits: int,
        warnings: Iterable[str],
        skipped_limited: int,
        extra_diagnostics: Mapping[str, int],
        detail: str,
    ) -> MarketQuoteFundamentalsSnapshot:
        warning_rows = tuple(warnings)
        no_usable_rows = max(0, requested_count - len(records))
        status = "available" if records else "empty"
        if warning_rows:
            status = "partial" if records else "warning"
        message = (
            f"{source}: {len(records)} rows updated; requested {requested_count}; returned rows {returned_count}; "
            f"parsed rows {len(records)}; provider calls attempted {calls_attempted}; cache used for {cache_hits}; "
            f"{skipped_limited} skipped/limited; {no_usable_rows} no usable data; {detail}. "
            f"FMP cap is {self.symbol_limit} symbol(s); batch size {self.batch_size}."
        )
        if warning_rows:
            message += f" Provider warning: {_short_warning(warning_rows[0], self.api_key)}"
        diagnostics = {
            "provider_rows_requested": requested_count,
            "provider_rows_returned": returned_count,
            "provider_rows_parsed": len(records),
            "provider_rows_updated": len(records),
            "provider_calls_attempted": calls_attempted,
            "provider_cache_hits": cache_hits,
            "provider_warnings": len(warning_rows),
            "fmp_cache_hits": cache_hits,
            "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_fmp_limit_warning(warning) for warning in warning_rows) else 0,
            "rows_skipped_by_configured_symbol_cap": skipped_limited,
            "rows_provider_returned_no_usable_data": no_usable_rows,
        }
        diagnostics.update(extra_diagnostics)
        return MarketQuoteFundamentalsSnapshot(
            records=records,
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus(source, status, fetched_at, _redact_fmp_secret(message, self.api_key)),),
            errors=tuple(_redact_fmp_secret(warning, self.api_key) for warning in warning_rows[:4]),
            diagnostics=diagnostics,
        )

    def _quote_payloads(self, symbols: tuple[str, ...], *, force_refresh: bool) -> tuple[dict[str, Mapping[str, Any]], int, str, int, int]:
        payloads: dict[str, Mapping[str, Any]] = {}
        missing: list[str] = []
        cache_hits = 0
        calls_attempted = 0
        rows_returned = 0
        for symbol in symbols:
            cached = self._cache_get("quote", symbol, force_refresh=force_refresh)
            if cached is None:
                missing.append(symbol)
            else:
                payloads[symbol] = cached
                cache_hits += 1
        if not missing:
            return payloads, cache_hits, "cache", calls_attempted, rows_returned

        endpoints: list[str] = []
        for chunk in _chunk_symbols(tuple(missing), self.batch_size):
            rows, endpoint, endpoint_calls = self._quote_rows_from_batch_endpoint(chunk)
            calls_attempted += endpoint_calls
            rows_returned += len(rows)
            endpoints.append(endpoint)
            for symbol, row in _fmp_quote_rows_by_symbol(rows, chunk).items():
                payloads[symbol] = row
                self._cache_set("quote", symbol, row)
        endpoint_text = endpoints[0] if len(set(endpoints)) == 1 else "+".join(dict.fromkeys(endpoints))
        return payloads, cache_hits, endpoint_text, calls_attempted, rows_returned

    def _quote_rows_from_batch_endpoint(self, missing: tuple[str, ...]) -> tuple[list[Mapping[str, Any]], str, int]:
        primary_endpoint = "quote"
        batch_primary_endpoint = "batch-quote-short"
        fallback_endpoint = "batch-quote"
        warnings: list[str] = []
        calls_attempted = 0
        rows_by_symbol: dict[str, Mapping[str, Any]] = {}
        unresolved: list[str] = []
        for symbol in missing:
            try:
                calls_attempted += 1
                payload = self._get_json(primary_endpoint, {"symbol": symbol})
            except FmpProviderWarning as exc:
                if _is_fmp_limit_warning(str(exc)):
                    raise
                warnings.append(str(exc))
                unresolved.append(symbol)
                continue
            if _fmp_payload_shape_is_unexpected(payload):
                warnings.append(f"FMP {primary_endpoint} returned a malformed payload for {symbol}.")
                unresolved.append(symbol)
                continue
            rows = _coerce_fmp_rows(payload)
            selected = _fmp_quote_rows_by_symbol(rows, (symbol,))
            if not selected:
                warnings.append(f"FMP {primary_endpoint} returned rows without recognizable requested symbol {symbol}.")
                unresolved.append(symbol)
                continue
            rows_by_symbol.update(selected)
        if not unresolved:
            return list(rows_by_symbol.values()), primary_endpoint, calls_attempted

        try:
            calls_attempted += 1
            primary_payload = self._get_json(batch_primary_endpoint, {"symbols": ",".join(unresolved)})
            if not _fmp_payload_shape_is_unexpected(primary_payload):
                rows = _coerce_fmp_rows(primary_payload)
                if not rows:
                    unresolved = []
                elif _fmp_quote_rows_are_recognizable(rows, tuple(unresolved)):
                    rows_by_symbol.update(_fmp_quote_rows_by_symbol(rows, tuple(unresolved)))
                    unresolved = [symbol for symbol in unresolved if symbol not in rows_by_symbol]
                else:
                    warnings.append(f"FMP {batch_primary_endpoint} returned rows without recognizable requested symbols.")
            else:
                warnings.append(f"FMP {batch_primary_endpoint} returned a malformed payload.")
        except FmpProviderWarning as exc:
            if _is_fmp_limit_warning(str(exc)):
                raise
            warnings.append(str(exc))

        if not unresolved:
            return list(rows_by_symbol.values()), f"{primary_endpoint}+{batch_primary_endpoint}", calls_attempted

        try:
            calls_attempted += 1
            fallback_payload = self._get_json(fallback_endpoint, {"symbols": ",".join(unresolved)})
        except FmpProviderWarning as exc:
            message = f"{warnings[0] if warnings else f'FMP {primary_endpoint} was unusable'} FMP {fallback_endpoint} fallback failed: {exc}"
            raise FmpProviderWarning(_redact_fmp_secret(message, self.api_key)) from None
        if _fmp_payload_shape_is_unexpected(fallback_payload):
            message = f"{warnings[0] if warnings else f'FMP {primary_endpoint} was unusable'} FMP {fallback_endpoint} fallback returned a malformed payload."
            raise FmpProviderWarning(_redact_fmp_secret(message, self.api_key)) from None
        rows = _coerce_fmp_rows(fallback_payload)
        if not _fmp_quote_rows_are_recognizable(rows, tuple(unresolved)):
            message = f"{warnings[0] if warnings else f'FMP {primary_endpoint} was unusable'} FMP {fallback_endpoint} fallback returned rows without recognizable requested symbols."
            raise FmpProviderWarning(_redact_fmp_secret(message, self.api_key)) from None
        rows_by_symbol.update(_fmp_quote_rows_by_symbol(rows, tuple(unresolved)))
        return list(rows_by_symbol.values()), f"{primary_endpoint}+{fallback_endpoint}", calls_attempted

    def _market_cap_payloads(self, symbols: tuple[str, ...], *, force_refresh: bool) -> tuple[dict[str, Mapping[str, Any]], int, str, int, int]:
        payloads: dict[str, Mapping[str, Any]] = {}
        missing: list[str] = []
        cache_hits = 0
        calls_attempted = 0
        rows_returned = 0
        for symbol in symbols:
            cached = self._cache_get("market-capitalization", symbol, force_refresh=force_refresh)
            if cached is None:
                missing.append(symbol)
            else:
                payloads[symbol] = cached
                cache_hits += 1
        if not missing:
            return payloads, cache_hits, "cache", calls_attempted, rows_returned

        endpoint = "market-capitalization-batch"
        batch_warning: str | None = None
        try:
            calls_attempted += 1
            payload = self._get_json(endpoint, {"symbols": ",".join(missing)})
            if _fmp_payload_shape_is_unexpected(payload):
                batch_warning = f"FMP {endpoint} returned a malformed payload."
            else:
                rows = _coerce_fmp_rows(payload)
                rows_returned += len(rows)
                for symbol, row in _fmp_quote_rows_by_symbol(rows, tuple(missing)).items():
                    payloads[symbol] = row
                    self._cache_set("market-capitalization", symbol, row)
                missing = [symbol for symbol in missing if symbol not in payloads]
        except FmpProviderWarning as exc:
            if _is_fmp_limit_warning(str(exc)):
                raise
            batch_warning = str(exc)

        if missing:
            for symbol in tuple(missing):
                try:
                    selected, cache_hit, single_calls, single_returned = self._single_symbol_payload(
                        "market-capitalization",
                        symbol,
                        force_refresh=force_refresh,
                    )
                    cache_hits += int(cache_hit)
                    calls_attempted += single_calls
                    rows_returned += single_returned
                except FmpProviderWarning as exc:
                    if _is_fmp_limit_warning(str(exc)):
                        raise
                    batch_warning = f"{batch_warning or f'FMP {endpoint} was unusable'} FMP market-capitalization fallback failed for {symbol}: {exc}"
                    continue
                if selected is not None:
                    payloads[symbol] = selected

        if not payloads and batch_warning:
            raise FmpProviderWarning(_redact_fmp_secret(batch_warning, self.api_key)) from None
        return payloads, cache_hits, endpoint if len(payloads) > 1 else "market-capitalization", calls_attempted, rows_returned

    def _historical_eod_payload(self, symbol: str, *, force_refresh: bool) -> tuple[Mapping[str, Any] | None, bool, int, int]:
        endpoint = "historical-price-eod/full"
        cached = self._cache_get(endpoint, symbol, force_refresh=force_refresh)
        if cached is not None:
            return cached, True, 0, 0
        payload = self._get_json(endpoint, {"symbol": symbol})
        rows = _coerce_fmp_rows(payload)
        normalized = _fmp_historical_eod_summary(symbol, rows)
        if normalized is not None:
            self._cache_set(endpoint, symbol, normalized)
        return normalized, False, 1, len(rows)

    def _profile_payload(self, symbol: str, *, force_refresh: bool) -> tuple[Mapping[str, Any] | None, bool, int, int]:
        cached = self._cache_get("profile", symbol, force_refresh=force_refresh)
        if cached is not None:
            return cached, True, 0, 0
        payload = self._get_json("profile", {"symbol": symbol})
        rows = _coerce_fmp_rows(payload)
        selected = next((row for row in rows if _normalize_symbol(_first_present(row, "symbol", "ticker")) == symbol), rows[0] if rows else None)
        if selected is not None:
            self._cache_set("profile", symbol, selected)
        return selected, False, 1, len(rows)

    def _profile_payloads(self, symbols: tuple[str, ...], *, force_refresh: bool) -> tuple[dict[str, Mapping[str, Any]], int, int, int]:
        payloads: dict[str, Mapping[str, Any]] = {}
        missing: list[str] = []
        cache_hits = 0
        calls_attempted = 0
        rows_returned = 0
        for symbol in symbols:
            cached = self._cache_get("profile", symbol, force_refresh=force_refresh)
            if cached is None:
                missing.append(symbol)
            else:
                payloads[symbol] = cached
                cache_hits += 1
        if not missing:
            return payloads, cache_hits, calls_attempted, rows_returned

        for chunk in _chunk_symbols(tuple(missing), self.batch_size):
            calls_attempted += 1
            payload = self._get_json("profile", {"symbol": ",".join(chunk)})
            rows = _coerce_fmp_rows(payload)
            rows_returned += len(rows)
            rows_by_symbol = _fmp_profile_rows_by_symbol(rows, chunk)
            if len(chunk) > 1 and not rows_by_symbol:
                # Some FMP plans only accept one profile symbol at a time. Fall back without
                # treating that as a hard provider failure.
                for symbol in chunk:
                    selected, selected_cache_hit, selected_calls, selected_returned = self._profile_payload(symbol, force_refresh=force_refresh)
                    cache_hits += int(selected_cache_hit)
                    calls_attempted += selected_calls
                    rows_returned += selected_returned
                    if selected is not None:
                        payloads[symbol] = selected
                continue
            for symbol, row in rows_by_symbol.items():
                payloads[symbol] = row
                self._cache_set("profile", symbol, row)
        return payloads, cache_hits, calls_attempted, rows_returned

    def _profile_by_cik_payload(self, cik: str, *, force_refresh: bool) -> tuple[Mapping[str, Any] | None, bool, int, int]:
        normalized_cik = _normalize_cik(cik)
        if not normalized_cik:
            return None, False, 0, 0
        cached = self._cache_get("profile-cik", normalized_cik, force_refresh=force_refresh)
        if cached is not None:
            return cached, True, 0, 0
        payload = self._get_json("profile-cik", {"cik": _fmp_cik_param(normalized_cik)})
        rows = _coerce_fmp_rows(payload)
        selected = rows[0] if rows else None
        if selected is not None:
            self._cache_set("profile-cik", normalized_cik, selected)
        return selected, False, 1, len(rows)

    def _single_symbol_payload(self, endpoint: str, symbol: str, *, force_refresh: bool) -> tuple[Mapping[str, Any] | None, bool, int, int]:
        cached = self._cache_get(endpoint, symbol, force_refresh=force_refresh)
        if cached is not None:
            return cached, True, 0, 0
        payload = self._get_json(endpoint, {"symbol": symbol})
        rows = _coerce_fmp_rows(payload)
        selected = next((row for row in rows if _normalize_symbol(_first_present(row, "symbol", "ticker")) == symbol), rows[0] if rows else None)
        if selected is not None:
            self._cache_set(endpoint, symbol, selected)
        return selected, False, 1, len(rows)

    def _record_from_payload(
        self,
        symbol: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        source_url: str,
        fetched_at: str,
        cik: str | None = None,
    ) -> MarketQuoteFundamentalsRecord:
        values = dict(payload)
        values.setdefault("symbol", symbol)
        if cik:
            values.setdefault("cik", cik)
        values.update(_normalized_fmp_payload_fields(values))
        values["source"] = source
        values["source_url"] = source_url
        values["fetched_at"] = fetched_at
        return MarketQuoteFundamentalsRecord.from_dict(values)

    def _get_json(self, endpoint: str, params: Mapping[str, str]) -> Any:
        url = f"{self.base_url}/{endpoint.strip('/')}"
        try:
            response = self.session.get(
                url,
                params=dict(params),
                headers={"apikey": str(self.api_key), "User-Agent": "portfolio-risk-cockpit/1.0"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise FmpProviderWarning(_redact_fmp_secret(f"FMP {endpoint} request failed: {exc}", self.api_key)) from None

        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403}:
            raise FmpProviderWarning(f"FMP {endpoint} authentication was rejected (HTTP {status_code}); check {FMP_API_KEY_ENV}.")
        if status_code == 429:
            raise FmpProviderWarning(f"FMP {endpoint} rate or daily plan limit was reached (HTTP 429).")
        if status_code < 200 or status_code >= 300:
            raise FmpProviderWarning(f"FMP {endpoint} returned HTTP {status_code}.")
        try:
            payload = response.json()
        except ValueError:
            raise FmpProviderWarning(f"FMP {endpoint} returned a non-JSON response.") from None
        plan_limit = _detect_fmp_plan_limit(payload)
        if plan_limit:
            raise FmpProviderWarning(f"FMP {endpoint} plan limit response: {plan_limit}")
        return payload

    def _cache_get(self, endpoint: str, symbol: str, *, force_refresh: bool) -> Mapping[str, Any] | None:
        if force_refresh or self.cache_ttl_seconds <= 0:
            return None
        key = (self.base_url, endpoint, symbol)
        cached = self._cache.get(key)
        if cached is None:
            return None
        cached_at, payload = cached
        if time.time() - cached_at > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return payload

    def _cache_set(self, endpoint: str, symbol: str, payload: Mapping[str, Any]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[(self.base_url, endpoint, symbol)] = (time.time(), dict(payload))


class AlphaVantageFallbackProvider:
    provider_name = "alpha_vantage_fallback"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        session: requests.Session | Any | None = None,
        timeout_seconds: float = 8.0,
        symbol_limit: int | None = None,
        cache_ttl_seconds: int | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.getenv(ALPHA_VANTAGE_API_KEY_ENV, "")
        self.base_url = (base_url or os.getenv(ALPHA_VANTAGE_BASE_URL_ENV, DEFAULT_ALPHA_VANTAGE_BASE_URL) or DEFAULT_ALPHA_VANTAGE_BASE_URL).rstrip("/")
        self.session = session or requests.Session()
        self.timeout_seconds = timeout_seconds
        self.symbol_limit = (
            max(0, min(100, int(symbol_limit)))
            if symbol_limit is not None
            else _configured_int(MARKET_DATA_FALLBACK_SYMBOL_LIMIT_ENV, DEFAULT_ALPHA_VANTAGE_FALLBACK_SYMBOL_LIMIT, minimum=0, maximum=100)
        )
        self.cache_ttl_seconds = (
            max(0, int(cache_ttl_seconds))
            if cache_ttl_seconds is not None
            else _configured_int(ALPHA_VANTAGE_CACHE_TTL_SECONDS_ENV, DEFAULT_ALPHA_VANTAGE_CACHE_TTL_SECONDS, minimum=0, maximum=86_400)
        )
        self._cache: dict[tuple[str, str, str], tuple[float, Mapping[str, Any]]] = {} if session is not None else _SHARED_ALPHA_VANTAGE_CACHE

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        capped_input = _limited_symbols(symbols, max_symbols)
        requested = capped_input[: self.symbol_limit]
        skipped_limited = max(0, len(capped_input) - len(requested))

        if not _optional_string(self.api_key):
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Fallback Alpha Vantage",
                        "unavailable",
                        fetched_at,
                        f"Fallback provider requested but no {ALPHA_VANTAGE_API_KEY_ENV} is configured.",
                    ),
                ),
                diagnostics={
                    "provider_unavailable": 1,
                    "rows_skipped_by_configured_symbol_cap": skipped_limited,
                },
            )

        if max_symbols <= 0 or self.symbol_limit <= 0:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(
                    MarketDataProviderStatus(
                        "Fallback Alpha Vantage",
                        "disabled",
                        fetched_at,
                        f"Fallback Alpha Vantage: 0 rows updated; {len(capped_input)} skipped/limited because the fallback symbol cap is 0.",
                    ),
                ),
                diagnostics={"rows_skipped_by_configured_symbol_cap": len(capped_input)},
            )

        records_by_symbol: dict[str, MarketQuoteFundamentalsRecord] = {}
        warnings: list[str] = []
        cache_hits = 0
        quote_rows = 0
        overview_rows = 0
        for symbol in requested:
            try:
                quote_payload, quote_cache_hit = self._payload("GLOBAL_QUOTE", symbol, force_refresh=force_refresh)
                cache_hits += int(quote_cache_hit)
                quote_record = self._quote_record(symbol, quote_payload, fetched_at=fetched_at)
                if _quote_record_has_any_value(quote_record):
                    records_by_symbol[symbol] = quote_record
                    quote_rows += 1
            except RuntimeError as exc:
                warnings.append(str(exc))
                if _is_alpha_vantage_limit_warning(str(exc)):
                    break
            try:
                overview_payload, overview_cache_hit = self._payload("OVERVIEW", symbol, force_refresh=force_refresh)
                cache_hits += int(overview_cache_hit)
                overview_record = self._overview_record(symbol, overview_payload, fetched_at=fetched_at)
                if _quote_record_has_any_value(overview_record):
                    existing = records_by_symbol.get(symbol)
                    records_by_symbol[symbol] = overview_record if existing is None else _merge_quote_records(existing, overview_record)
                    overview_rows += 1
            except RuntimeError as exc:
                warnings.append(str(exc))
                if _is_alpha_vantage_limit_warning(str(exc)):
                    break

        records = tuple(records_by_symbol.values())
        status = "available" if records else "empty"
        if warnings:
            status = "partial" if records else "warning"
        message = (
            f"Fallback Alpha Vantage: {len(records)} rows updated; {skipped_limited} skipped/limited; cache used for {cache_hits}. "
            "Fallback is only attached to visible-page or selected-row enrichment when explicitly configured."
        )
        if warnings:
            message += f" Provider warning: {_short_warning(warnings[0], self.api_key)}"
        return MarketQuoteFundamentalsSnapshot(
            records=records,
            fetched_at=fetched_at,
            statuses=(MarketDataProviderStatus("Fallback Alpha Vantage", status, fetched_at, _redact_fmp_secret(message, self.api_key)),),
            errors=tuple(_redact_fmp_secret(warning, self.api_key) for warning in warnings[:4]),
            diagnostics={
                "rows_enriched_by_fallback_provider": len(records),
                "rows_enriched_by_fallback_quote": quote_rows,
                "rows_enriched_by_fallback_profile": overview_rows,
                "rows_blocked_by_provider_plan_rate_auth_limit": 1 if any(_is_alpha_vantage_limit_warning(warning) for warning in warnings) else 0,
                "rows_skipped_by_configured_symbol_cap": skipped_limited,
                "rows_provider_returned_no_usable_data": max(0, len(requested) - len(records)),
            },
        )

    def _payload(self, function: str, symbol: str, *, force_refresh: bool) -> tuple[Mapping[str, Any], bool]:
        cached = self._cache_get(function, symbol, force_refresh=force_refresh)
        if cached is not None:
            return cached, True
        try:
            response = self.session.get(
                self.base_url,
                params={"function": function, "symbol": symbol, "apikey": self.api_key},
                headers={"User-Agent": "portfolio-risk-cockpit/1.0"},
                timeout=self.timeout_seconds,
            )
        except requests.RequestException as exc:
            raise RuntimeError(_redact_fmp_secret(f"Alpha Vantage {function} request failed: {exc}", self.api_key)) from None
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code in {401, 403, 429}:
            raise RuntimeError(f"Alpha Vantage {function} authentication/rate limit returned HTTP {status_code}.")
        if status_code < 200 or status_code >= 300:
            raise RuntimeError(f"Alpha Vantage {function} returned HTTP {status_code}.")
        try:
            payload = response.json()
        except ValueError:
            raise RuntimeError(f"Alpha Vantage {function} returned a non-JSON response.") from None
        warning = _detect_alpha_vantage_warning(payload)
        if warning:
            raise RuntimeError(warning)
        if not isinstance(payload, Mapping):
            return {}, False
        self._cache_set(function, symbol, payload)
        return payload, False

    def _quote_record(self, symbol: str, payload: Mapping[str, Any], *, fetched_at: str) -> MarketQuoteFundamentalsRecord:
        quote = payload.get("Global Quote") if isinstance(payload.get("Global Quote"), Mapping) else payload
        values = {
            "symbol": symbol,
            "price": _first_present(quote, "05. price", "price"),
            "volume": _first_present(quote, "06. volume", "volume"),
            "change_percent": _first_present(quote, "10. change percent", "change_percent"),
            "source": "Fallback Alpha Vantage quote",
            "source_url": ALPHA_VANTAGE_DOC_URL,
            "fetched_at": fetched_at,
        }
        return MarketQuoteFundamentalsRecord.from_dict(values)

    def _overview_record(self, symbol: str, payload: Mapping[str, Any], *, fetched_at: str) -> MarketQuoteFundamentalsRecord:
        values = {
            "symbol": symbol,
            "exchange": _first_present(payload, "Exchange", "exchange"),
            "sector": _first_present(payload, "Sector", "sector"),
            "industry": _first_present(payload, "Industry", "industry"),
            "market_cap": _first_present(payload, "MarketCapitalization", "marketCap"),
            "pe_ratio": _first_present(payload, "PERatio", "peRatio"),
            "eps": _first_present(payload, "EPS", "eps"),
            "revenue_growth": _first_present(payload, "QuarterlyRevenueGrowthYOY", "revenueGrowth"),
            "shares_outstanding": _first_present(payload, "SharesOutstanding", "sharesOutstanding"),
            "source": "Fallback Alpha Vantage profile",
            "source_url": ALPHA_VANTAGE_DOC_URL,
            "fetched_at": fetched_at,
        }
        return MarketQuoteFundamentalsRecord.from_dict(values)

    def _cache_get(self, endpoint: str, symbol: str, *, force_refresh: bool) -> Mapping[str, Any] | None:
        if force_refresh or self.cache_ttl_seconds <= 0:
            return None
        key = (self.base_url, endpoint, symbol)
        cached = self._cache.get(key)
        if cached is None:
            return None
        cached_at, payload = cached
        if time.time() - cached_at > self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return payload

    def _cache_set(self, endpoint: str, symbol: str, payload: Mapping[str, Any]) -> None:
        if self.cache_ttl_seconds <= 0:
            return
        self._cache[(self.base_url, endpoint, symbol)] = (time.time(), dict(payload))


_FMP_DEEP_ENDPOINTS = (
    ("key-metrics", "FMP key metrics", FMP_KEY_METRICS_DOC_URL),
    ("ratios-ttm", "FMP ratios TTM", FMP_RATIOS_TTM_DOC_URL),
    ("key-metrics-ttm", "FMP key metrics TTM", FMP_KEY_METRICS_TTM_DOC_URL),
    ("income-statement-growth", "FMP income growth", FMP_INCOME_GROWTH_DOC_URL),
    ("financial-growth", "FMP financial growth", FMP_FINANCIAL_GROWTH_DOC_URL),
    ("shares-float", "FMP shares float", FMP_SHARES_FLOAT_DOC_URL),
)

_FMP_VISIBLE_STATEMENT_ENDPOINTS = (
    ("income-statement", "FMP income statement", FMP_INCOME_STATEMENT_DOC_URL),
)

_FMP_STATEMENT_ENDPOINTS = (
    ("income-statement", "FMP income statement", FMP_INCOME_STATEMENT_DOC_URL),
    ("balance-sheet-statement", "FMP balance sheet", FMP_BALANCE_SHEET_DOC_URL),
    ("cash-flow-statement", "FMP cash flow", FMP_CASH_FLOW_DOC_URL),
    ("cash-flow-statement-growth", "FMP cash flow growth", FMP_CASH_FLOW_GROWTH_DOC_URL),
)

_FMP_MACRO_INDICATORS = (
    ("GDP", "Growth / Consumer"),
    ("CPI", "Inflation"),
    ("unemploymentRate", "Labor"),
    ("federalFunds", "Rates / Treasury"),
)

_FMP_MACRO_COMMODITY_SYMBOLS = (
    ("CLUSD", "Energy", "Crude oil commodity quote"),
    ("BZUSD", "Energy", "Brent crude commodity quote"),
    ("NGUSD", "Energy", "Natural gas commodity quote"),
)


def _record_needs_deeper_fmp_fields(record: MarketQuoteFundamentalsRecord) -> bool:
    return any(
        value is None
        for value in (
            record.market_cap,
            record.pe_ratio,
            record.eps,
            record.revenue_growth,
            record.shares_float,
            record.shares_outstanding,
            record.cik,
        )
    )


def _record_needs_fmp_endpoint(record: MarketQuoteFundamentalsRecord, endpoint: str) -> bool:
    if endpoint in {"key-metrics", "key-metrics-ttm"}:
        return record.market_cap is None or record.pe_ratio is None or record.eps is None
    if endpoint == "ratios-ttm":
        return record.pe_ratio is None
    if endpoint == "income-statement-growth":
        return record.revenue_growth is None
    if endpoint == "financial-growth":
        return record.revenue_growth is None
    if endpoint == "income-statement":
        return record.eps is None or record.revenue is None or record.net_income is None or record.operating_income is None
    if endpoint == "market-capitalization":
        return record.market_cap is None
    if endpoint == "balance-sheet-statement":
        return (
            record.cash_and_equivalents is None
            or record.total_assets is None
            or record.total_liabilities is None
            or record.total_debt is None
            or record.cash_to_liabilities is None
        )
    if endpoint == "cash-flow-statement":
        return record.operating_cash_flow is None or record.free_cash_flow is None
    if endpoint == "cash-flow-statement-growth":
        return record.operating_cash_flow_yoy is None or record.free_cash_flow_yoy is None
    if endpoint == "shares-float":
        return record.shares_float is None or record.shares_outstanding is None
    return _record_needs_deeper_fmp_fields(record)


def _record_needs_visible_fmp_endpoint(record: MarketQuoteFundamentalsRecord, endpoint: str) -> bool:
    if endpoint == "income-statement":
        return record.eps is None
    return _record_needs_fmp_endpoint(record, endpoint)


def _record_with_family_fields(record: MarketQuoteFundamentalsRecord, family: str) -> MarketQuoteFundamentalsRecord:
    profile_metadata = {
        "country",
        "instrument_type",
        "is_adr",
        "is_etf",
        "is_fund",
        "is_otc",
        "market_cap_currency",
    }
    market_cap_metadata = {
        "market_cap_currency",
        "market_cap_rank_value",
        "market_cap_rank_currency",
        "market_cap_rank_trusted",
        "market_cap_rank_reason",
        "country",
        "instrument_type",
        "is_adr",
        "is_etf",
        "is_fund",
        "is_otc",
    }
    fields_by_family = {
        "profile_classification": {"company_name", "exchange", "sector", "industry", "cik", *profile_metadata},
        "quote_tape": {"price", "change_percent", "volume", "avg_volume"},
        "fundamentals": {
            "market_cap",
            "pe_ratio",
            "eps",
            "revenue_growth",
            "shares_float",
            "shares_outstanding",
            "revenue",
            "net_income",
            "operating_income",
            "diluted_eps",
            "operating_cash_flow",
            "free_cash_flow",
            "net_income_yoy",
            "operating_income_yoy",
            "diluted_eps_yoy",
            "operating_cash_flow_yoy",
            "free_cash_flow_yoy",
            "cash_and_equivalents",
            "total_assets",
            "total_liabilities",
            "total_debt",
            "cash_to_liabilities",
            "liabilities_to_assets",
            "debt_to_liabilities",
            "enterprise_value",
            "ev_to_sales",
            "ev_to_ebitda",
            "price_to_sales",
            *market_cap_metadata,
        },
    }
    allowed = fields_by_family.get(family)
    if not allowed:
        return record
    values = record.to_dict()
    for field in (
        "company_name",
        "exchange",
        "sector",
        "industry",
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
        "revenue",
        "net_income",
        "operating_income",
        "diluted_eps",
        "operating_cash_flow",
        "free_cash_flow",
        "net_income_yoy",
        "operating_income_yoy",
        "diluted_eps_yoy",
        "operating_cash_flow_yoy",
        "free_cash_flow_yoy",
        "cash_and_equivalents",
        "total_assets",
        "total_liabilities",
        "total_debt",
        "cash_to_liabilities",
        "liabilities_to_assets",
        "debt_to_liabilities",
        "enterprise_value",
        "ev_to_sales",
        "ev_to_ebitda",
        "price_to_sales",
        "cik",
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
    ):
        if field not in allowed:
            values[field] = None
    values["field_provenance"] = tuple(row for row in record.field_provenance if row.field in allowed)
    return MarketQuoteFundamentalsRecord.from_dict(values)


def _normalized_fmp_payload_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for target, keys in (
        ("company_name", ("companyName", "company_name", "name", "company")),
        ("market_cap", ("marketCap", "marketCapTTM", "mktCap", "marketCapitalization", "MarketCapitalization")),
        ("pe_ratio", ("pe", "peRatio", "peRatioTTM", "PERatio", "priceEarningsRatio", "priceEarningsRatioTTM", "priceToEarningsRatioTTM")),
        ("eps", ("eps", "EPS", "epsTTM", "earningsPerShareTTM", "netIncomePerShareTTM", "epsdiluted", "epsDiluted", "dilutedEPS", "epsdilutedTTM")),
        ("shares_float", ("sharesFloat", "floatShares", "freeFloat", "float")),
        ("shares_outstanding", ("sharesOutstanding", "outstandingShares", "weightedAverageShsOut", "weightedAverageShsOutTTM")),
        ("revenue", ("revenue", "totalRevenue", "revenueTTM")),
        ("net_income", ("netIncome", "netIncomeTTM")),
        ("operating_income", ("operatingIncome", "operatingIncomeTTM")),
        ("diluted_eps", ("epsdiluted", "epsDiluted", "dilutedEPS", "epsdilutedTTM")),
        ("operating_cash_flow", ("operatingCashFlow", "netCashProvidedByOperatingActivities", "netCashProvidedByUsedInOperatingActivities")),
        ("free_cash_flow", ("freeCashFlow", "freeCashFlowTTM")),
        ("cash_and_equivalents", ("cashAndCashEquivalents", "cashAndShortTermInvestments", "cashCashEquivalentsAndShortTermInvestments")),
        ("total_assets", ("totalAssets",)),
        ("total_liabilities", ("totalLiabilities", "totalLiabilitiesNetMinorityInterest")),
        ("total_debt", ("totalDebt", "netDebt")),
        ("enterprise_value", ("enterpriseValue", "enterpriseValueTTM")),
        ("ev_to_sales", ("enterpriseValueOverRevenue", "evToSales", "evToSalesTTM")),
        ("ev_to_ebitda", ("enterpriseValueOverEBITDA", "evToEbitda", "evToEBITDA", "evToEbitdaTTM")),
        ("price_to_sales", ("priceToSalesRatio", "priceToSalesRatioTTM", "priceToSales")),
        ("exchange", ("exchangeShortName", "exchange")),
        ("cik", ("cik", "CIK", "cik_str")),
        ("market_cap_currency", ("marketCapCurrency", "reportedCurrency", "currency")),
        ("market_cap_rank_value", ("marketCapUSD", "marketCapUsd", "market_cap_usd", "marketCapUSDTTM")),
        ("market_cap_rank_currency", ("marketCapRankCurrency", "market_cap_rank_currency")),
        ("instrument_type", ("instrumentType", "securityType", "assetType", "type")),
        ("country", ("country", "countryName", "domicile")),
        ("market_cap_rank_reason", ("marketCapRankReason", "market_cap_rank_reason")),
    ):
        value = _first_present(payload, *keys)
        if value not in (None, ""):
            normalized[target] = value
    for target, keys in (
        ("market_cap_rank_trusted", ("marketCapRankTrusted", "market_cap_rank_trusted", "trustedMarketCap", "trusted_market_cap")),
        ("is_adr", ("isAdr", "isADR", "is_adr", "adr", "isDepositaryReceipt")),
        ("is_etf", ("isEtf", "isETF", "is_etf", "etf")),
        ("is_fund", ("isFund", "is_fund", "fund")),
        ("is_otc", ("isOtc", "isOTC", "is_otc", "otc")),
    ):
        value = _optional_bool(_first_present(payload, *keys))
        if value is not None:
            normalized[target] = value
    growth = _fmp_percent_value(_first_present(payload, "revenueGrowth", "revenueGrowthTTM", "growthRevenue", "QuarterlyRevenueGrowthYOY"))
    if growth is not None:
        normalized["revenue_growth"] = growth
    for target, keys in (
        ("net_income_yoy", ("growthNetIncome", "growthNetIncomeRatio", "netIncomeGrowth")),
        ("operating_income_yoy", ("growthOperatingIncome", "growthOperatingIncomeRatio", "operatingIncomeGrowth")),
        ("diluted_eps_yoy", ("growthEPSDiluted", "growthEpsDiluted", "epsDilutedGrowth", "dilutedEPSGrowth")),
        ("operating_cash_flow_yoy", ("growthOperatingCashFlow", "growthNetCashProvidedByOperatingActivities", "operatingCashFlowGrowth")),
        ("free_cash_flow_yoy", ("growthFreeCashFlow", "freeCashFlowGrowth")),
    ):
        value = _fmp_percent_value(_first_present(payload, *keys))
        if value is not None:
            normalized[target] = value
    if "total_debt" not in normalized:
        short_debt = _optional_float(_first_present(payload, "shortTermDebt"))
        long_debt = _optional_float(_first_present(payload, "longTermDebt"))
        if short_debt is not None or long_debt is not None:
            normalized["total_debt"] = (short_debt or 0.0) + (long_debt or 0.0)
    cash = _optional_float(normalized.get("cash_and_equivalents"))
    assets = _optional_float(normalized.get("total_assets"))
    liabilities = _optional_float(normalized.get("total_liabilities"))
    debt = _optional_float(normalized.get("total_debt"))
    if cash is not None and liabilities not in (None, 0):
        normalized["cash_to_liabilities"] = (cash / abs(liabilities)) * 100
    if liabilities is not None and assets not in (None, 0):
        normalized["liabilities_to_assets"] = (liabilities / assets) * 100
    if debt is not None and liabilities not in (None, 0):
        normalized["debt_to_liabilities"] = (debt / abs(liabilities)) * 100
    return normalized


def _fmp_percent_value(value: Any) -> float | None:
    parsed = _optional_float(value)
    if parsed is None:
        return None
    if abs(parsed) <= 1:
        return parsed * 100
    return parsed


class CompositeMarketDataProvider:
    provider_name = "composite_market_data"

    def __init__(self, providers: Iterable[MarketQuoteFundamentalsProvider]) -> None:
        self.providers = tuple(providers)

    def profile_classification(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        return self._family_snapshot("profile_classification", symbols, force_refresh=force_refresh, max_symbols=max_symbols)

    def quote_tape(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        return self._family_snapshot("quote_tape", symbols, force_refresh=force_refresh, max_symbols=max_symbols)

    def fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        return self._family_snapshot("fundamentals", symbols, force_refresh=force_refresh, max_symbols=max_symbols)

    def all_market_data(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        return self.quote_fundamentals(symbols, force_refresh=force_refresh, max_symbols=max_symbols)

    def market_cap_rankings(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        requested = _limited_symbols(symbols, max_symbols)
        if not self.providers:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Top market-cap ranking provider", "unavailable", fetched_at, "No market-cap ranking provider is configured."),),
                diagnostics={"provider_unavailable": 1},
            )

        merged: dict[str, MarketQuoteFundamentalsRecord] = {}
        statuses: list[MarketDataProviderStatus] = []
        errors: list[str] = []
        diagnostics: dict[str, int] = {}
        for provider in self.providers:
            method = getattr(provider, "market_cap_rankings", None)
            if not callable(method) or provider is self:
                continue
            snapshot = method(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            statuses.extend(snapshot.statuses)
            errors.extend(snapshot.errors)
            _merge_diagnostics(diagnostics, snapshot.diagnostics)
            for record in snapshot.records:
                filtered = _record_with_family_fields(record, "fundamentals")
                key = _normalize_symbol(filtered.symbol)
                if not key or filtered.market_cap is None:
                    continue
                existing = merged.get(key)
                merged[key] = filtered if existing is None else _merge_quote_records(existing, filtered)
        if not statuses:
            statuses.append(
                MarketDataProviderStatus(
                    "Top market-cap ranking provider",
                    "unavailable",
                    fetched_at,
                    "No configured provider supports pre-truncation market-cap ranking.",
                )
            )
            diagnostics["provider_unavailable"] = diagnostics.get("provider_unavailable", 0) + 1
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(merged.values()),
            fetched_at=fetched_at,
            statuses=tuple(statuses),
            errors=tuple(errors),
            diagnostics=diagnostics,
        )

    def quote_fundamentals(
        self,
        symbols: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        requested = _limited_symbols(symbols, max_symbols)
        if not self.providers:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Market quote/fundamental provider", "unavailable", fetched_at, "No market quote/fundamental provider is configured."),),
            )

        merged: dict[str, MarketQuoteFundamentalsRecord] = {}
        statuses: list[MarketDataProviderStatus] = []
        errors: list[str] = []
        diagnostics: dict[str, int] = {}
        for provider in self.providers:
            snapshot = provider.quote_fundamentals(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            statuses.extend(snapshot.statuses)
            errors.extend(snapshot.errors)
            _merge_diagnostics(diagnostics, snapshot.diagnostics)
            for record in snapshot.records:
                existing = merged.get(record.symbol)
                merged[record.symbol] = record if existing is None else _merge_quote_records(existing, record)
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(merged.values()),
            fetched_at=fetched_at,
            statuses=tuple(statuses),
            errors=tuple(errors),
            diagnostics=diagnostics,
        )

    def _family_snapshot(
        self,
        family: str,
        symbols: Iterable[str],
        *,
        force_refresh: bool,
        max_symbols: int,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        requested = _limited_symbols(symbols, max_symbols)
        if not self.providers:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Market quote/fundamental provider", "unavailable", fetched_at, "No market quote/fundamental provider is configured."),),
                diagnostics={"provider_unavailable": 1},
            )

        merged: dict[str, MarketQuoteFundamentalsRecord] = {}
        statuses: list[MarketDataProviderStatus] = []
        errors: list[str] = []
        diagnostics: dict[str, int] = {}
        for provider in self.providers:
            method = getattr(provider, family, None)
            snapshot: MarketQuoteFundamentalsSnapshot | None = None
            if callable(method):
                snapshot = method(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            elif family == "quote_tape" and callable(getattr(provider, "quote_fundamentals", None)):
                snapshot = provider.quote_fundamentals(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            elif family == "profile_classification" and isinstance(provider, LocalMarketDataFileProvider):
                snapshot = provider.quote_fundamentals(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            elif family == "fundamentals" and isinstance(provider, LocalMarketDataFileProvider):
                snapshot = provider.quote_fundamentals(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            if snapshot is None:
                continue
            statuses.extend(snapshot.statuses)
            errors.extend(snapshot.errors)
            _merge_diagnostics(diagnostics, snapshot.diagnostics)
            for record in snapshot.records:
                filtered = _record_with_family_fields(record, family)
                key = _normalize_symbol(filtered.symbol)
                if not key or not _quote_record_has_any_value(filtered):
                    continue
                existing = merged.get(key)
                merged[key] = filtered if existing is None else _merge_quote_records(existing, filtered)
        if not statuses:
            statuses.append(
                MarketDataProviderStatus(
                    f"Market {family.replace('_', ' ')} provider",
                    "unavailable",
                    fetched_at,
                    f"No configured provider supports {family.replace('_', ' ')} enrichment.",
                )
            )
            diagnostics["provider_unavailable"] = diagnostics.get("provider_unavailable", 0) + 1
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(merged.values()),
            fetched_at=fetched_at,
            statuses=tuple(statuses),
            errors=tuple(errors),
            diagnostics=diagnostics,
        )

    def quote_fundamentals_by_cik(
        self,
        ciks: Iterable[str],
        *,
        force_refresh: bool = False,
        max_symbols: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT,
    ) -> MarketQuoteFundamentalsSnapshot:
        fetched_at = _now()
        requested = _limited_ciks(ciks, max_symbols)
        if not self.providers:
            return MarketQuoteFundamentalsSnapshot(
                records=(),
                fetched_at=fetched_at,
                statuses=(MarketDataProviderStatus("Market quote/fundamental provider", "unavailable", fetched_at, "No market quote/fundamental provider is configured."),),
                diagnostics={"provider_unavailable": 1},
            )

        merged: dict[str, MarketQuoteFundamentalsRecord] = {}
        statuses: list[MarketDataProviderStatus] = []
        errors: list[str] = []
        diagnostics: dict[str, int] = {}
        for provider in self.providers:
            by_cik = getattr(provider, "quote_fundamentals_by_cik", None)
            if not callable(by_cik) or provider is self:
                continue
            snapshot = by_cik(requested, force_refresh=force_refresh, max_symbols=max_symbols)
            statuses.extend(snapshot.statuses)
            errors.extend(snapshot.errors)
            _merge_diagnostics(diagnostics, snapshot.diagnostics)
            for record in snapshot.records:
                key = _normalize_symbol(record.symbol) or _normalize_cik(record.cik)
                if not key:
                    continue
                existing = merged.get(key)
                merged[key] = record if existing is None else _merge_quote_records(existing, record)
        if not statuses:
            statuses.append(
                MarketDataProviderStatus(
                    "FMP profile-by-CIK",
                    "unavailable",
                    fetched_at,
                    "No configured market-data provider supports CIK-based quote/profile lookup.",
                )
            )
            diagnostics["provider_unavailable"] = diagnostics.get("provider_unavailable", 0) + 1
        return MarketQuoteFundamentalsSnapshot(
            records=tuple(merged.values()),
            fetched_at=fetched_at,
            statuses=tuple(statuses),
            errors=tuple(errors),
            diagnostics=diagnostics,
        )

    def filing_metadata(
        self,
        symbol: str,
        *,
        force_refresh: bool = False,
        limit: int = 12,
    ) -> tuple[dict[str, Any], ...]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for provider in self.providers:
            method = getattr(provider, "filing_metadata", None) or getattr(provider, "sec_filings", None)
            if not callable(method):
                continue
            try:
                provider_rows = method(symbol, force_refresh=force_refresh, limit=limit)
            except TypeError:
                provider_rows = method(symbol, limit=limit)
            for row in provider_rows or ():
                if not isinstance(row, Mapping):
                    continue
                key = (
                    str(row.get("accessionNumber") or row.get("accession_number") or row.get("filingDate") or row.get("date") or ""),
                    str(row.get("form") or row.get("type") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                rows.append(dict(row))
                if len(rows) >= limit:
                    return tuple(rows)
        return tuple(rows)

    def sec_filings(
        self,
        symbol: str,
        *,
        force_refresh: bool = False,
        limit: int = 12,
    ) -> tuple[dict[str, Any], ...]:
        return self.filing_metadata(symbol, force_refresh=force_refresh, limit=limit)


def configured_market_data_provider(
    *,
    schwab_session: Any | None = None,
    local_path: str | Path | None = None,
    include_fallback_provider: bool = False,
    fmp_symbol_limit: int | None = None,
    databento_symbol_limit: int | None = None,
    cache_ttl_seconds: int | None = None,
    batch_size: int | None = None,
) -> CompositeMarketDataProvider:
    providers: list[MarketQuoteFundamentalsProvider] = [LocalMarketDataFileProvider(local_path)]
    if schwab_session is not None:
        providers.append(SchwabQuoteFundamentalsProvider(schwab_session))
    from app.data.databento_provider import configured_databento_equities_provider

    providers.append(
        configured_databento_equities_provider(
            symbol_limit=databento_symbol_limit,
            cache_ttl_seconds=cache_ttl_seconds,
            batch_size=batch_size,
        )
    )
    providers.append(
        FmpQuoteFundamentalsProvider(
            symbol_limit=fmp_symbol_limit,
            cache_ttl_seconds=cache_ttl_seconds,
            batch_size=batch_size,
        )
    )
    if include_fallback_provider:
        fallback_provider = configured_fallback_market_data_provider()
        if fallback_provider is not None:
            providers.append(fallback_provider)
    return CompositeMarketDataProvider(providers)


def configured_fallback_market_data_provider() -> MarketQuoteFundamentalsProvider | None:
    provider_name = str(os.getenv(MARKET_DATA_FALLBACK_PROVIDER_ENV, "") or "").strip().lower().replace("-", "_")
    if provider_name not in {"alpha_vantage", "alphavantage"}:
        return None
    return AlphaVantageFallbackProvider()


def configured_market_data_symbol_limit(default: int = DEFAULT_MARKET_DATA_SYMBOL_LIMIT) -> int:
    return _configured_int(MARKET_DATA_SYMBOL_LIMIT_ENV, default, minimum=0, maximum=1000)


def _merge_quote_records(left: MarketQuoteFundamentalsRecord, right: MarketQuoteFundamentalsRecord) -> MarketQuoteFundamentalsRecord:
    field_provenance = _merge_quote_field_provenance(left, right)
    return MarketQuoteFundamentalsRecord(
        symbol=left.symbol or right.symbol,
        company_name=_prefer_ladder_field(left.company_name, right.company_name),
        exchange=_prefer_ladder_field(left.exchange, right.exchange),
        sector=_prefer_ladder_field(left.sector, right.sector),
        industry=_prefer_ladder_field(left.industry, right.industry),
        price=_prefer_ladder_field(left.price, right.price),
        change_percent=_prefer_ladder_field(left.change_percent, right.change_percent),
        volume=_prefer_ladder_field(left.volume, right.volume),
        avg_volume=_prefer_ladder_field(left.avg_volume, right.avg_volume),
        market_cap=_prefer_ladder_field(left.market_cap, right.market_cap),
        pe_ratio=_prefer_ladder_field(left.pe_ratio, right.pe_ratio),
        eps=_prefer_ladder_field(left.eps, right.eps),
        revenue_growth=_prefer_ladder_field(left.revenue_growth, right.revenue_growth),
        shares_float=_prefer_ladder_field(left.shares_float, right.shares_float),
        shares_outstanding=_prefer_ladder_field(left.shares_outstanding, right.shares_outstanding),
        revenue=_prefer_ladder_field(left.revenue, right.revenue),
        net_income=_prefer_ladder_field(left.net_income, right.net_income),
        operating_income=_prefer_ladder_field(left.operating_income, right.operating_income),
        diluted_eps=_prefer_ladder_field(left.diluted_eps, right.diluted_eps),
        operating_cash_flow=_prefer_ladder_field(left.operating_cash_flow, right.operating_cash_flow),
        free_cash_flow=_prefer_ladder_field(left.free_cash_flow, right.free_cash_flow),
        net_income_yoy=_prefer_ladder_field(left.net_income_yoy, right.net_income_yoy),
        operating_income_yoy=_prefer_ladder_field(left.operating_income_yoy, right.operating_income_yoy),
        diluted_eps_yoy=_prefer_ladder_field(left.diluted_eps_yoy, right.diluted_eps_yoy),
        operating_cash_flow_yoy=_prefer_ladder_field(left.operating_cash_flow_yoy, right.operating_cash_flow_yoy),
        free_cash_flow_yoy=_prefer_ladder_field(left.free_cash_flow_yoy, right.free_cash_flow_yoy),
        cash_and_equivalents=_prefer_ladder_field(left.cash_and_equivalents, right.cash_and_equivalents),
        total_assets=_prefer_ladder_field(left.total_assets, right.total_assets),
        total_liabilities=_prefer_ladder_field(left.total_liabilities, right.total_liabilities),
        total_debt=_prefer_ladder_field(left.total_debt, right.total_debt),
        cash_to_liabilities=_prefer_ladder_field(left.cash_to_liabilities, right.cash_to_liabilities),
        liabilities_to_assets=_prefer_ladder_field(left.liabilities_to_assets, right.liabilities_to_assets),
        debt_to_liabilities=_prefer_ladder_field(left.debt_to_liabilities, right.debt_to_liabilities),
        enterprise_value=_prefer_ladder_field(left.enterprise_value, right.enterprise_value),
        ev_to_sales=_prefer_ladder_field(left.ev_to_sales, right.ev_to_sales),
        ev_to_ebitda=_prefer_ladder_field(left.ev_to_ebitda, right.ev_to_ebitda),
        price_to_sales=_prefer_ladder_field(left.price_to_sales, right.price_to_sales),
        source=", ".join(dict.fromkeys([source for source in (left.source, right.source) if source])),
        source_url=left.source_url or right.source_url,
        fetched_at=left.fetched_at or right.fetched_at,
        field_provenance=field_provenance,
        cik=_prefer_ladder_field(left.cik, right.cik),
        market_cap_currency=_prefer_ladder_field(left.market_cap_currency, right.market_cap_currency),
        market_cap_rank_value=_prefer_ladder_field(left.market_cap_rank_value, right.market_cap_rank_value),
        market_cap_rank_currency=_prefer_ladder_field(left.market_cap_rank_currency, right.market_cap_rank_currency),
        market_cap_rank_trusted=_prefer_ladder_field(left.market_cap_rank_trusted, right.market_cap_rank_trusted),
        market_cap_rank_reason=_prefer_ladder_field(left.market_cap_rank_reason, right.market_cap_rank_reason),
        instrument_type=_prefer_ladder_field(left.instrument_type, right.instrument_type),
        country=_prefer_ladder_field(left.country, right.country),
        is_adr=_prefer_ladder_field(left.is_adr, right.is_adr),
        is_etf=_prefer_ladder_field(left.is_etf, right.is_etf),
        is_fund=_prefer_ladder_field(left.is_fund, right.is_fund),
        is_otc=_prefer_ladder_field(left.is_otc, right.is_otc),
    )


def _merge_quote_field_provenance(
    left: MarketQuoteFundamentalsRecord,
    right: MarketQuoteFundamentalsRecord,
) -> tuple[MarketDataFieldProvenance, ...]:
    left_by_field = _quote_provenance_by_field(left)
    right_by_field = _quote_provenance_by_field(right)
    merged: list[MarketDataFieldProvenance] = []
    for field in _QUOTE_VALUE_FIELDS:
        selected = right_by_field.get(field) if _field_was_selected_from_right(left, right, field) else left_by_field.get(field)
        if selected is None:
            selected = left_by_field.get(field) or right_by_field.get(field)
        if selected is not None:
            merged.append(selected)
    return _dedupe_field_provenance(merged)


def _quote_record_has_any_value(record: MarketQuoteFundamentalsRecord) -> bool:
    return any(
        value is not None
        for value in (
            record.exchange,
            record.company_name,
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
            record.revenue,
            record.net_income,
            record.operating_income,
            record.diluted_eps,
            record.operating_cash_flow,
            record.free_cash_flow,
            record.net_income_yoy,
            record.operating_income_yoy,
            record.diluted_eps_yoy,
            record.operating_cash_flow_yoy,
            record.free_cash_flow_yoy,
            record.cash_and_equivalents,
            record.total_assets,
            record.total_liabilities,
            record.total_debt,
            record.cash_to_liabilities,
            record.liabilities_to_assets,
            record.debt_to_liabilities,
            record.enterprise_value,
            record.ev_to_sales,
            record.ev_to_ebitda,
            record.price_to_sales,
            record.market_cap_currency,
            record.market_cap_rank_value,
            record.market_cap_rank_currency,
            record.market_cap_rank_trusted,
            record.market_cap_rank_reason,
            record.instrument_type,
            record.country,
            record.is_adr,
            record.is_etf,
            record.is_fund,
            record.is_otc,
        )
    )


_QUOTE_VALUE_FIELDS = (
    "company_name",
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
    "revenue",
    "net_income",
    "operating_income",
    "diluted_eps",
    "operating_cash_flow",
    "free_cash_flow",
    "net_income_yoy",
    "operating_income_yoy",
    "diluted_eps_yoy",
    "operating_cash_flow_yoy",
    "free_cash_flow_yoy",
    "cash_and_equivalents",
    "total_assets",
    "total_liabilities",
    "total_debt",
    "cash_to_liabilities",
    "liabilities_to_assets",
    "debt_to_liabilities",
    "enterprise_value",
    "ev_to_sales",
    "ev_to_ebitda",
    "price_to_sales",
    "cik",
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


def _field_was_selected_from_right(
    left: MarketQuoteFundamentalsRecord,
    right: MarketQuoteFundamentalsRecord,
    field: str,
) -> bool:
    right_value = getattr(right, field)
    left_value = getattr(left, field)
    return right_value is not None and left_value is None


def _quote_provenance_by_field(record: MarketQuoteFundamentalsRecord) -> dict[str, MarketDataFieldProvenance]:
    rows = _field_provenance_from_payload(getattr(record, "field_provenance", ()))
    if not rows:
        rows = _provenance_for_values(
            {field: getattr(record, field) for field in _QUOTE_VALUE_FIELDS},
            source=record.source,
            source_url=record.source_url,
            fetched_at=record.fetched_at,
        )
    return {row.field: row for row in rows if row.field}


def _field_provenance_from_payload(payload: Any) -> tuple[MarketDataFieldProvenance, ...]:
    if not payload:
        return ()
    rows: list[MarketDataFieldProvenance] = []
    for item in payload if isinstance(payload, (list, tuple)) else ():
        if isinstance(item, MarketDataFieldProvenance):
            row = item
        elif isinstance(item, Mapping):
            row = MarketDataFieldProvenance.from_dict(item)
        else:
            continue
        if row.field:
            rows.append(row)
    return _dedupe_field_provenance(rows)


def _provenance_for_values(
    values: Mapping[str, Any],
    *,
    source: str,
    source_url: str | None,
    fetched_at: str,
    source_detail: str = "",
) -> tuple[MarketDataFieldProvenance, ...]:
    rows = [
        MarketDataFieldProvenance(field=field, source=source, source_url=source_url, source_detail=source_detail, fetched_at=fetched_at)
        for field, value in values.items()
        if value is not None and field in _QUOTE_VALUE_FIELDS
    ]
    return _dedupe_field_provenance(rows)


def _dedupe_field_provenance(rows: Iterable[MarketDataFieldProvenance]) -> tuple[MarketDataFieldProvenance, ...]:
    seen: set[tuple[str, str, str, str, str]] = set()
    result: list[MarketDataFieldProvenance] = []
    for row in rows:
        key = (row.field, row.source, row.source_url or "", row.source_detail, row.fetched_at)
        if not row.field or key in seen:
            continue
        seen.add(key)
        result.append(row)
    return tuple(result)


def _prefer_ladder_field(left: Any, right: Any) -> Any:
    return left if left is not None else right


def _record_is_newer(incoming: MarketQuoteFundamentalsRecord, existing: MarketQuoteFundamentalsRecord) -> bool:
    incoming_time = _parse_timestamp(incoming.fetched_at)
    existing_time = _parse_timestamp(existing.fetched_at)
    if incoming_time is None:
        return existing_time is None and bool(incoming.fetched_at and not existing.fetched_at)
    if existing_time is None:
        return True
    return incoming_time > existing_time


def _parse_timestamp(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _limited_symbols(symbols: Iterable[str], max_symbols: int) -> tuple[str, ...]:
    if max_symbols <= 0:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for symbol in symbols:
        clean = _normalize_symbol(symbol)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if len(result) >= max_symbols:
            break
    return tuple(result)


def _limited_ciks(ciks: Iterable[str], max_symbols: int) -> tuple[str, ...]:
    if max_symbols <= 0:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for cik in ciks:
        clean = _normalize_cik(cik)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if len(result) >= max_symbols:
            break
    return tuple(result)


def _chunk_symbols(symbols: tuple[str, ...], chunk_size: int) -> tuple[tuple[str, ...], ...]:
    size = max(1, int(chunk_size))
    return tuple(tuple(symbols[index : index + size]) for index in range(0, len(symbols), size))


def _normalize_symbol(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    symbol = _SYMBOL_ALIASES.get(symbol, symbol)
    symbol = symbol.replace("/", ".")
    symbol = _SYMBOL_ALIASES.get(symbol, symbol)
    return symbol if symbol and len(symbol) <= 16 else ""


def _normalize_cik(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(10) if digits else ""


def _fmp_cik_param(value: Any) -> str:
    normalized = _normalize_cik(value)
    return normalized.lstrip("0") or normalized


def _optional_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("$", "")
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return None


def _normalize_currency(value: Any) -> str | None:
    text = _optional_string(value)
    if not text:
        return None
    clean = text.strip().upper()
    aliases = {
        "$": "USD",
        "US$": "USD",
        "U.S. DOLLAR": "USD",
        "US DOLLAR": "USD",
        "UNITED STATES DOLLAR": "USD",
        "USDOLLAR": "USD",
    }
    return aliases.get(clean, clean[:3] if len(clean) > 3 and clean[:3].isalpha() else clean)


def _first_present(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _configured_int(env_name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(env_name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _coerce_fmp_rows(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, Mapping)]
    if isinstance(payload, Mapping):
        rows = payload.get("data") or payload.get("results") or payload.get("records")
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, Mapping)]
        return [payload]
    return []


def _normalize_fmp_filing_metadata_row(row: Mapping[str, Any], fallback_symbol: str) -> dict[str, Any]:
    filing_url = _optional_string(_first_present(row, "finalLink", "filing_url", "filingUrl", "link", "url", "reportUrl")) or ""
    accession = _optional_string(_first_present(row, "accessionNumber", "accession_number", "accessionNo", "accession")) or _accession_from_fmp_filing_url(filing_url)
    primary_document = _optional_string(_first_present(row, "primaryDocument", "primary_document", "document")) or _document_from_fmp_filing_url(filing_url)
    form = _optional_string(_first_present(row, "form", "type", "filingType", "formType")) or ""
    if not accession or not primary_document or not form:
        return {}
    symbol = _normalize_symbol(_first_present(row, "symbol", "ticker")) or fallback_symbol
    payload = {
        "symbol": symbol,
        "companyName": _optional_string(_first_present(row, "companyName", "company_name", "company", "name")) or "",
        "cik": _normalize_cik(_first_present(row, "cik", "CIK", "cik_str")),
        "form": form.upper(),
        "filingDate": _optional_string(_first_present(row, "filingDate", "fillingDate", "filedDate", "date", "acceptedDate")) or "",
        "reportDate": _optional_string(_first_present(row, "reportDate", "periodOfReport")) or "",
        "acceptedDate": _optional_string(_first_present(row, "acceptedDate", "accepted_date")) or "",
        "accessionNumber": accession,
        "primaryDocument": primary_document,
        "finalLink": filing_url,
        "description": _optional_string(_first_present(row, "description", "title")) or "FMP filing metadata prefilter",
        "source": "FMP filing metadata",
        "source_url": FMP_SEC_FILINGS_BY_SYMBOL_DOC_URL,
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def _normalize_fmp_macro_indicator_rows(name: str, category: str, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    latest = rows[0]
    prior = rows[1] if len(rows) > 1 else {}
    value = _optional_float(_first_present(latest, "value", "actual", "price", "close"))
    prior_value = _optional_float(_first_present(prior, "value", "actual", "price", "close"))
    if value is None and prior_value is None:
        return {}
    return {
        "category": category,
        "metric": _fmp_macro_indicator_label(name),
        "value": value,
        "prior": prior_value,
        "date": _optional_string(_first_present(latest, "date", "period")) or "",
        "unit": _fmp_macro_indicator_unit(name),
        "source": "FMP economic indicators proxy",
        "source_url": FMP_ECONOMIC_INDICATORS_DOC_URL,
    }


def _normalize_fmp_macro_quote_row(symbol: str, category: str, label: str, row: Mapping[str, Any]) -> dict[str, Any]:
    if not row:
        return {}
    value = _optional_float(_first_present(row, "price", "close", "previousClose"))
    prior = _optional_float(_first_present(row, "previousClose", "open"))
    if value is None and prior is None:
        return {}
    return {
        "category": category,
        "symbol": _normalize_symbol(_first_present(row, "symbol")) or symbol,
        "metric": label,
        "price": value,
        "previous_close": prior,
        "date": _optional_string(_first_present(row, "timestamp", "date")) or "",
        "unit": "USD",
        "source": "FMP commodity quote proxy",
        "source_url": FMP_COMMODITIES_QUOTE_DOC_URL,
    }


def _fmp_historical_eod_summary(symbol: str, rows: list[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    sorted_rows = sorted(rows, key=lambda row: str(_first_present(row, "date", "timestamp", "time") or ""), reverse=True)
    latest = sorted_rows[0]
    close = _optional_float(_first_present(latest, "close", "adjClose", "price"))
    volume = _optional_float(_first_present(latest, "volume"))
    direct_change = _fmp_percent_value(_first_present(latest, "changePercent", "change_percent", "changesPercentage"))
    previous_close = _optional_float(_first_present(latest, "previousClose", "prevClose"))
    if direct_change is None and previous_close in (None, 0) and len(sorted_rows) > 1:
        previous_close = _optional_float(_first_present(sorted_rows[1], "close", "adjClose", "price"))
    change_percent = direct_change
    if change_percent is None and previous_close not in (None, 0) and close is not None:
        change_percent = ((close - previous_close) / abs(previous_close)) * 100
    volumes = [
        parsed
        for row in sorted_rows[:30]
        if (parsed := _optional_float(_first_present(row, "volume"))) is not None
    ]
    avg_volume = sum(volumes) / len(volumes) if volumes else None
    values = {
        "symbol": symbol,
        "price": close,
        "volume": volume,
        "avgVolume": avg_volume,
        "changesPercentage": change_percent,
    }
    return {key: value for key, value in values.items() if value is not None}


def _fmp_macro_indicator_label(name: str) -> str:
    return {
        "GDP": "GDP economic indicator",
        "CPI": "CPI economic indicator",
        "unemploymentRate": "Unemployment rate economic indicator",
        "federalFunds": "Federal funds economic indicator",
    }.get(name, name)


def _fmp_macro_indicator_unit(name: str) -> str:
    return "%" if name in {"CPI", "unemploymentRate", "federalFunds"} else ""


def _accession_from_fmp_filing_url(url: str) -> str:
    match = re.search(r"(\d{10})[-/]?(\d{2})[-/]?(\d{6})", str(url or ""))
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def _document_from_fmp_filing_url(url: str) -> str:
    text = str(url or "").strip().rstrip("/")
    if not text:
        return ""
    document = text.rsplit("/", 1)[-1]
    return document if "." in document else ""


def _fmp_payload_shape_is_unexpected(payload: Any) -> bool:
    if isinstance(payload, list):
        return False
    if isinstance(payload, Mapping):
        for key in ("data", "results", "records"):
            if key in payload and not isinstance(payload.get(key), list):
                return True
        return False
    return True


def _fmp_quote_rows_by_symbol(rows: Iterable[Mapping[str, Any]], requested: tuple[str, ...]) -> dict[str, Mapping[str, Any]]:
    requested_set = set(requested)
    selected: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = _normalize_symbol(_first_present(row, "symbol", "ticker"))
        if not symbol and len(requested) == 1:
            symbol = requested[0]
        if not symbol or symbol not in requested_set:
            continue
        selected[symbol] = row
    return selected


def _fmp_profile_rows_by_symbol(rows: Iterable[Mapping[str, Any]], requested: tuple[str, ...]) -> dict[str, Mapping[str, Any]]:
    requested_set = set(requested)
    selected: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        symbol = _normalize_symbol(_first_present(row, "symbol", "ticker"))
        if not symbol and len(requested) == 1:
            symbol = requested[0]
        if not symbol or symbol not in requested_set:
            continue
        selected[symbol] = row
    return selected


def _fmp_quote_rows_are_recognizable(rows: list[Mapping[str, Any]], requested: tuple[str, ...]) -> bool:
    if not rows:
        return True
    return bool(_fmp_quote_rows_by_symbol(rows, requested))


def _detect_fmp_plan_limit(payload: Any) -> str | None:
    fragments: list[str] = []
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if isinstance(value, (str, int, float)):
                fragments.append(f"{key}: {value}")
    elif isinstance(payload, list):
        for row in payload[:3]:
            if isinstance(row, Mapping):
                for key, value in row.items():
                    if isinstance(value, (str, int, float)):
                        fragments.append(f"{key}: {value}")
    text = " ".join(str(fragment) for fragment in fragments)
    compact = text.lower()
    if not compact:
        return None
    limit_terms = ("limit", "rate", "quota", "plan", "upgrade", "premium", "not available")
    error_terms = ("error", "message", "reach", "exceeded", "forbidden", "unauthorized")
    if any(term in compact for term in limit_terms) and any(term in compact for term in error_terms):
        return _short_warning(text, None)
    return None


def _detect_alpha_vantage_warning(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("Error Message", "Note", "Information"):
        value = payload.get(key)
        if value:
            return f"Alpha Vantage provider warning: {value}"
    compact = " ".join(str(value) for value in payload.values() if isinstance(value, (str, int, float))).lower()
    if compact and any(term in compact for term in ("limit", "premium", "rate", "apikey", "invalid api")):
        return f"Alpha Vantage provider warning: {_short_warning(compact, None)}"
    return None


def _is_fmp_limit_warning(message: str) -> bool:
    compact = str(message or "").lower()
    return any(term in compact for term in ("limit", "quota", "rate", "plan", "429", "403", "401"))


def _is_alpha_vantage_limit_warning(message: str) -> bool:
    compact = str(message or "").lower()
    return any(term in compact for term in ("limit", "premium", "rate", "apikey", "invalid api", "429", "403", "401"))


def _merge_diagnostics(target: dict[str, int], source: Mapping[str, int] | None) -> None:
    for key, value in (source or {}).items():
        try:
            amount = int(value)
        except (TypeError, ValueError):
            continue
        if amount:
            target[str(key)] = target.get(str(key), 0) + amount


def _short_warning(message: str, api_key: str | None) -> str:
    clean = " ".join(_redact_fmp_secret(str(message or ""), api_key).split())
    return clean[:240] + ("..." if len(clean) > 240 else "")


def _redact_fmp_secret(message: str, api_key: str | None) -> str:
    text = str(message or "")
    clean_key = str(api_key or "").strip()
    if clean_key:
        text = text.replace(clean_key, "[REDACTED]")
    text = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(apikey['\"]?\s*[:=]\s*['\"]?)[^,'\"\s)}]+", r"\1[REDACTED]", text)
    return text


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
