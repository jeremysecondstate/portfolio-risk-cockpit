from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from app.analytics.capital_structure_pressure import analyze_capital_structure_pressure
from app.analytics.earnings_release import analyze_earnings_sources
from app.data.sec_edgar import SecEdgarClient, SecFiling


DEFAULT_SYMBOL_WEB_PROVIDER = "sec_edgar_public_filings"
DEFAULT_SYMBOL_MARKET_NEWS_PROVIDER = "none"
SYMBOL_WEB_SOURCE_LIMIT = 16
SYMBOL_WEB_FILING_SUMMARY_LIMIT = 4


@dataclass(frozen=True)
class SymbolWebSource:
    title: str
    url: str
    publisher: str = ""
    published_at: str = ""
    source_type: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class SymbolWebEnrichment:
    symbol: str
    company_name: str = ""
    generated_at_utc: str = ""
    provider_name: str = ""
    provider_configured: bool = True
    status: str = "available"
    company_profile: dict[str, Any] = field(default_factory=dict)
    recent_news: tuple[SymbolWebSource, ...] = ()
    earnings_context: tuple[SymbolWebSource, ...] = ()
    recent_filings: tuple[SymbolWebSource, ...] = ()
    filing_summaries: tuple[dict[str, Any], ...] = ()
    capital_structure: dict[str, Any] = field(default_factory=dict)
    sources: tuple[SymbolWebSource, ...] = ()
    source_debug: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class SymbolRecentMarketNewsContext:
    symbol: str
    generated_at_utc: str = ""
    provider_name: str = ""
    provider_configured: bool = True
    status: str = "available"
    market_snapshot: dict[str, Any] = field(default_factory=dict)
    recent_news: tuple[SymbolWebSource, ...] = ()
    earnings_ir: tuple[SymbolWebSource, ...] = ()
    sources: tuple[SymbolWebSource, ...] = ()
    warnings: tuple[str, ...] = ()
    source_debug: tuple[str, ...] = ()
    reason: str = ""


@runtime_checkable
class SymbolWebEnrichmentProvider(Protocol):
    provider_name: str

    def enrich(
        self,
        symbol: str,
        *,
        company_name: str = "",
        recent_filings: Iterable[Mapping[str, Any]] = (),
    ) -> SymbolWebEnrichment | Mapping[str, Any]:
        ...


@runtime_checkable
class SymbolRecentMarketNewsProvider(Protocol):
    provider_name: str

    def enrich_market_news(
        self,
        symbol: str,
        *,
        company_name: str = "",
    ) -> SymbolRecentMarketNewsContext | Mapping[str, Any]:
        ...


class SecEdgarSymbolWebEnrichmentProvider:
    """Official-source web enrichment backed by public SEC endpoints."""

    provider_name = DEFAULT_SYMBOL_WEB_PROVIDER

    def __init__(
        self,
        *,
        sec_client: SecEdgarClient | None = None,
        max_recent_filings: int = SYMBOL_WEB_SOURCE_LIMIT,
        max_filing_summaries: int = SYMBOL_WEB_FILING_SUMMARY_LIMIT,
    ) -> None:
        self.sec_client = sec_client or SecEdgarClient(timeout_seconds=12)
        self.max_recent_filings = max(1, max_recent_filings)
        self.max_filing_summaries = max(0, max_filing_summaries)

    def enrich(
        self,
        symbol: str,
        *,
        company_name: str = "",
        recent_filings: Iterable[Mapping[str, Any]] = (),
    ) -> SymbolWebEnrichment:
        clean_symbol = _normalize_symbol(symbol)
        generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        warnings: list[str] = []
        debug: list[str] = [f"provider={self.provider_name}"]
        company_profile: dict[str, Any] = {"symbol": clean_symbol}
        resolved_company_name = company_name

        try:
            company = self.sec_client.company_for_ticker(clean_symbol)
            resolved_company_name = resolved_company_name or company.title
            company_profile.update(
                {
                    "company_name": company.title,
                    "ticker": company.ticker,
                    "cik": company.cik,
                    "source": "SEC company_tickers.json",
                }
            )
            debug.append("company_profile=sec_company_tickers")
        except Exception as exc:
            warnings.append(f"SEC company profile lookup failed: {exc}")

        filing_rows, filing_objects = self._recent_filings(clean_symbol, recent_filings, warnings, debug)
        recent_filing_sources = tuple(_source_from_filing_row(row) for row in filing_rows[: self.max_recent_filings])
        earnings_sources, earnings_summary = self._earnings_context(clean_symbol, resolved_company_name, warnings, debug)
        filing_summaries = tuple(self._filing_summaries(filing_objects, warnings, debug))
        capital_structure = self._capital_structure(clean_symbol, warnings, debug)

        sources = _dedupe_sources((*recent_filing_sources, *earnings_sources))
        status = "available" if sources or filing_summaries or earnings_summary or capital_structure else "empty"
        reason = "" if status == "available" else "SEC provider returned no readable public filing context."
        if earnings_summary:
            company_profile["earnings_summary"] = earnings_summary

        debug.append(f"sources={len(sources)}")
        debug.append(f"filing_summaries={len(filing_summaries)}")

        return SymbolWebEnrichment(
            symbol=clean_symbol,
            company_name=resolved_company_name,
            generated_at_utc=generated_at,
            provider_name=self.provider_name,
            provider_configured=True,
            status=status,
            company_profile=company_profile,
            earnings_context=earnings_sources,
            recent_filings=recent_filing_sources,
            filing_summaries=filing_summaries,
            capital_structure=capital_structure,
            sources=sources,
            source_debug=tuple(debug),
            warnings=tuple(warnings),
            reason=reason,
        )

    def _recent_filings(
        self,
        symbol: str,
        existing_recent_filings: Iterable[Mapping[str, Any]],
        warnings: list[str],
        debug: list[str],
    ) -> tuple[list[dict[str, Any]], list[SecFiling]]:
        existing_rows = [_compact_filing_row(row) for row in existing_recent_filings if isinstance(row, Mapping)]
        filing_objects: list[SecFiling] = []
        try:
            filing_objects = self.sec_client.recent_filings(symbol, limit=self.max_recent_filings)
            rows = [_filing_row(filing) for filing in filing_objects]
            debug.append(f"recent_filings=sec:{len(rows)}")
            return rows, filing_objects
        except Exception as exc:
            if existing_rows:
                warnings.append(f"SEC recent filings refresh failed; reused Symbol Chat filing metadata: {exc}")
                debug.append(f"recent_filings=reused:{len(existing_rows)}")
                return existing_rows, filing_objects
            warnings.append(f"SEC recent filings unavailable: {exc}")
            debug.append("recent_filings=unavailable")
            return [], filing_objects

    def _earnings_context(
        self,
        symbol: str,
        company_name: str,
        warnings: list[str],
        debug: list[str],
    ) -> tuple[tuple[SymbolWebSource, ...], dict[str, Any]]:
        release = None
        report = None
        try:
            release = self.sec_client.latest_earnings_release(symbol)
            debug.append("earnings_8k=" + ("found" if release is not None else "none"))
        except Exception as exc:
            warnings.append(f"SEC 8-K earnings enrichment failed: {exc}")
            debug.append("earnings_8k=error")
        try:
            report = self.sec_client.latest_formal_earnings_report(symbol)
            debug.append("earnings_formal_report=" + ("found" if report is not None else "none"))
        except Exception as exc:
            warnings.append(f"SEC 10-Q/10-K earnings fallback failed: {exc}")
            debug.append("earnings_formal_report=error")

        if release is None and report is None:
            return (), {}

        digest = analyze_earnings_sources(
            symbol,
            release,
            sec_report=report,
            company_name=company_name,
            latest_sec_filing_date=_latest_sec_date(release, report),
        )
        if digest is None:
            return (), {}

        snippets = _dedupe_text(
            [
                *list(digest.headline_snippets or []),
                *list(digest.guidance_snippets or []),
                *list(digest.margin_cashflow_snippets or []),
                *list(digest.good_bullets or []),
                *list(digest.bad_missing_bullets or []),
                *list(digest.watch_bullets or []),
            ]
        )[:8]
        source = SymbolWebSource(
            title=digest.source_label or "SEC earnings context",
            url=digest.source_url,
            publisher="SEC EDGAR",
            published_at=digest.source_date or digest.filing_date,
            source_type=digest.source_kind or "sec_earnings",
            snippet=_shorten(" ".join(snippets), 1_200),
        )
        summary = {
            "source_label": digest.source_label,
            "source_kind": digest.source_kind,
            "source_date": digest.source_date or digest.filing_date,
            "source_url": digest.source_url,
            "freshness_status": digest.freshness_status,
            "freshness_verdict": digest.freshness_verdict,
            "headline_snippets": list(digest.headline_snippets[:5]),
            "guidance_snippets": list(digest.guidance_snippets[:4]),
            "margin_cashflow_snippets": list(digest.margin_cashflow_snippets[:4]),
            "watch_bullets": list(digest.watch_bullets[:4]),
        }
        debug.append(f"earnings_summary={digest.source_kind or 'available'}")
        return (source,), _drop_empty(summary)

    def _filing_summaries(
        self,
        filings: list[SecFiling],
        warnings: list[str],
        debug: list[str],
    ) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for filing in _prioritized_filings(filings)[: self.max_filing_summaries]:
            try:
                text = self.sec_client.document_text_url(
                    filing.filing_url,
                    cache_name=f"symbol_web_{filing.company.cik}_{filing.accession_no_dashes}_{filing.primary_document or 'primary'}.txt",
                )
            except Exception as exc:
                warnings.append(f"{filing.form} filed {filing.filing_date or '--'} summary failed: {exc}")
                continue
            snippets = _filing_snippets(text)
            summaries.append(
                _drop_empty(
                    {
                        "form": filing.form,
                        "filing_date": filing.filing_date,
                        "report_date": filing.report_date,
                        "description": filing.description,
                        "accession_number": filing.accession_number,
                        "url": filing.filing_url,
                        "source_type": "sec_filing_summary",
                        "summary": _shorten(" ".join(snippets), 1_800) if snippets else _shorten(text, 900),
                        "matched_terms": _matched_filing_terms(text),
                    }
                )
            )
        debug.append(f"filing_text_summaries={len(summaries)}")
        return summaries

    def _capital_structure(self, symbol: str, warnings: list[str], debug: list[str]) -> dict[str, Any]:
        try:
            report = analyze_capital_structure_pressure(symbol, client=self.sec_client)
        except Exception as exc:
            warnings.append(f"Capital-structure filing enrichment failed: {exc}")
            debug.append("capital_structure=error")
            return {}
        status = str(getattr(report, "status", "") or "")
        debug.append(f"capital_structure={status or 'loaded'}")
        signals = [
            _drop_empty(
                {
                    "label": getattr(signal, "label", ""),
                    "severity": getattr(signal, "severity", ""),
                    "summary": getattr(signal, "summary", ""),
                    "source_form": getattr(signal, "source_form", ""),
                    "source_date": getattr(signal, "source_date", ""),
                    "source_url": getattr(signal, "source_url", ""),
                }
            )
            for signal in list(getattr(report, "signals", []) or [])[:6]
        ]
        return _drop_empty(
            {
                "status": status,
                "summary": getattr(report, "summary", ""),
                "highest_severity": getattr(report, "highest_severity", ""),
                "source": "SEC filing text scan",
                "signals": [signal for signal in signals if signal],
                "warnings": list(getattr(report, "warnings", []) or [])[:6],
            }
        )


def configured_default_symbol_web_provider(sec_client: SecEdgarClient | None = None) -> SymbolWebEnrichmentProvider | None:
    provider_id = os.getenv("SYMBOL_CHAT_WEB_ENRICHMENT_PROVIDER", DEFAULT_SYMBOL_WEB_PROVIDER).strip().lower()
    if provider_id in {"", "none", "disabled", "off", "unconfigured"}:
        return None
    if provider_id in {"sec", "sec_edgar", "sec-edgar", DEFAULT_SYMBOL_WEB_PROVIDER}:
        return SecEdgarSymbolWebEnrichmentProvider(sec_client=sec_client)
    return None


def configured_default_symbol_market_news_provider() -> SymbolRecentMarketNewsProvider | None:
    provider_id = os.getenv("SYMBOL_CHAT_MARKET_NEWS_PROVIDER", DEFAULT_SYMBOL_MARKET_NEWS_PROVIDER).strip().lower()
    if provider_id in {"", "none", "disabled", "off", "unconfigured"}:
        return None
    return None


def symbol_web_enrichment_to_payload(enrichment: SymbolWebEnrichment | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(enrichment, SymbolWebEnrichment):
        payload = asdict(enrichment)
    else:
        payload = dict(enrichment)

    payload.setdefault("mode", "enabled")
    payload.setdefault("enabled", True)
    payload.setdefault("status", "available")
    payload.setdefault("provider_configured", True)
    payload.setdefault("provider_name", payload.get("source") or "symbol_web_enrichment_provider")
    payload.setdefault("generated_at_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    payload["sources"] = _coerce_sources(payload.get("sources") or _derived_sources(payload))
    payload["recent_news"] = _coerce_sources(payload.get("recent_news"))
    payload["earnings_context"] = _coerce_sources(payload.get("earnings_context") or payload.get("earnings"))
    payload["recent_filings"] = _coerce_sources(payload.get("recent_filings") or payload.get("filings"))
    payload["filing_summaries"] = _coerce_mapping_list(payload.get("filing_summaries"))
    if payload.get("recent_market_news") is not None:
        payload["recent_market_news"] = symbol_market_news_to_payload(payload.get("recent_market_news"))
    payload["warnings"] = _coerce_text_list(payload.get("warnings"))
    payload["source_debug"] = _coerce_text_list(payload.get("source_debug"))
    return _drop_empty(payload)


def symbol_market_news_to_payload(context: SymbolRecentMarketNewsContext | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(context, SymbolRecentMarketNewsContext):
        payload = asdict(context)
    else:
        payload = dict(context)

    payload.setdefault("mode", "recent_market_news")
    payload.setdefault("enabled", True)
    payload.setdefault("status", "available")
    payload.setdefault("provider_configured", True)
    payload.setdefault("provider_name", payload.get("source") or "symbol_market_news_provider")
    payload.setdefault("generated_at_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    snapshot = payload.get("market_snapshot")
    payload["market_snapshot"] = {str(key): _json_safe_scalar(value) for key, value in snapshot.items()} if isinstance(snapshot, Mapping) else {}
    payload["recent_news"] = _coerce_sources(payload.get("recent_news"))
    payload["earnings_ir"] = _coerce_sources(payload.get("earnings_ir") or payload.get("earnings_context"))
    payload["sources"] = _coerce_sources(payload.get("sources") or _derived_sources(payload))
    payload["warnings"] = _coerce_text_list(payload.get("warnings"))
    payload["source_debug"] = _coerce_text_list(payload.get("source_debug"))
    return _drop_empty(payload)


def unavailable_symbol_web_enrichment_payload(
    symbol: str,
    reason: str,
    *,
    provider_name: str = "",
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "mode": "requested_unavailable",
        "enabled": True,
        "status": "unavailable",
        "provider_configured": False,
        "provider_name": provider_name or "none",
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": _normalize_symbol(symbol),
        "reason": reason,
        "sources": [],
        "warnings": [reason],
        "source_debug": [f"provider={provider_name or 'none'}", "status=unavailable", "sources=0"],
    }


def unavailable_symbol_market_news_payload(
    symbol: str,
    reason: str,
    *,
    provider_name: str = "",
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    return {
        "mode": "recent_market_news",
        "enabled": True,
        "status": "unavailable",
        "provider_configured": False,
        "provider_name": provider_name or "none",
        "generated_at_utc": generated_at_utc or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": _normalize_symbol(symbol),
        "market_snapshot": {},
        "recent_news": [],
        "earnings_ir": [],
        "sources": [],
        "reason": reason,
        "warnings": [reason],
        "source_debug": [f"provider={provider_name or 'none'}", "status=unavailable", "sources=0"],
    }


def provider_display_name(provider_name: str) -> str:
    clean = str(provider_name or "").replace("_", " ").replace("-", " ").strip()
    return clean or "web enrichment provider"


def _recent_filing_title(row: Mapping[str, Any]) -> str:
    form = str(row.get("form") or "SEC filing").strip()
    date = str(row.get("filing_date") or row.get("published_at") or "").strip()
    description = str(row.get("description") or "").strip()
    pieces = [form]
    if date:
        pieces.append(f"filed {date}")
    if description:
        pieces.append(description)
    return " - ".join(pieces)


def _source_from_filing_row(row: Mapping[str, Any]) -> SymbolWebSource:
    return SymbolWebSource(
        title=_recent_filing_title(row),
        url=str(row.get("url") or ""),
        publisher="SEC EDGAR",
        published_at=str(row.get("filing_date") or ""),
        source_type="sec_filing",
        snippet=str(row.get("description") or ""),
    )


def _filing_row(filing: SecFiling) -> dict[str, Any]:
    return {
        "form": filing.form,
        "filing_date": filing.filing_date,
        "report_date": filing.report_date,
        "description": filing.description,
        "accession_number": filing.accession_number,
        "url": filing.filing_url,
    }


def _compact_filing_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return _drop_empty(
        {
            "form": row.get("form"),
            "filing_date": row.get("filing_date"),
            "report_date": row.get("report_date"),
            "description": row.get("description"),
            "accession_number": row.get("accession_number"),
            "url": row.get("url"),
        }
    )


def _prioritized_filings(filings: list[SecFiling]) -> list[SecFiling]:
    priority = {
        "10-Q": 0,
        "10-K": 1,
        "8-K": 2,
        "S-3": 3,
        "S-3/A": 3,
        "424B5": 4,
        "424B3": 4,
        "F-1": 5,
        "F-1/A": 5,
        "S-1": 5,
        "S-1/A": 5,
    }
    indexed = list(enumerate(filings))
    indexed.sort(key=lambda item: (priority.get(item[1].form.upper(), 20), item[0]))
    return [filing for _index, filing in indexed]


def _filing_snippets(text: str) -> list[str]:
    normalized = _collapse_text(text)
    snippets: list[str] = []
    keyword_groups = (
        ("revenue", "net income", "operating income"),
        ("risk factors", "liquidity", "cash"),
        ("guidance", "outlook", "expects"),
        ("dilution", "offering", "resale"),
        ("debt", "convertible", "warrants"),
    )
    for group in keyword_groups:
        sentence = _sentence_with(normalized, group)
        if sentence:
            snippets.append(sentence)
    return _dedupe_text(snippets)


def _matched_filing_terms(text: str) -> list[str]:
    lower = text.lower()
    terms = [
        "revenue",
        "net income",
        "guidance",
        "risk factors",
        "liquidity",
        "cash flow",
        "offering",
        "resale",
        "dilution",
        "warrants",
        "convertible",
    ]
    return [term for term in terms if term in lower][:10]


def _sentence_with(text: str, keywords: tuple[str, ...]) -> str:
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        lower = sentence.lower()
        if any(keyword in lower for keyword in keywords):
            return _shorten(sentence, 420)
    return ""


def _latest_sec_date(*sources: Any) -> str:
    dates = []
    for source in sources:
        filing = getattr(source, "filing", None)
        value = getattr(filing, "filing_date", "") if filing is not None else ""
        if value:
            dates.append(str(value))
    return max(dates) if dates else ""


def _derived_sources(payload: Mapping[str, Any]) -> list[Any]:
    sources: list[Any] = []
    for key in ("recent_news", "earnings_context", "earnings_ir", "recent_filings", "filings", "earnings"):
        value = payload.get(key)
        if isinstance(value, list):
            sources.extend(value)
        elif isinstance(value, tuple):
            sources.extend(value)
    return sources


def _coerce_sources(value: Any) -> list[dict[str, Any]]:
    rows = []
    if value is None:
        return rows
    items = value if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if isinstance(item, SymbolWebSource):
            row = asdict(item)
        elif isinstance(item, Mapping):
            row = dict(item)
        else:
            row = {"title": str(item)}
        rows.append(_drop_empty({str(key): _json_safe_scalar(val) for key, val in row.items()}))
    return rows


def _coerce_mapping_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    rows = []
    for item in items:
        if isinstance(item, Mapping):
            rows.append(_drop_empty({str(key): _json_safe_scalar(val) for key, val in item.items()}))
    return rows


def _coerce_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else [value]
    return [str(item) for item in items if str(item).strip()]


def _json_safe_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_scalar(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_safe_scalar(item) for key, item in value.items()}
    return str(value)


def _dedupe_sources(sources: Iterable[SymbolWebSource]) -> tuple[SymbolWebSource, ...]:
    result: list[SymbolWebSource] = []
    seen: set[tuple[str, str]] = set()
    for source in sources:
        key = (source.title.lower(), source.url)
        if key in seen:
            continue
        seen.add(key)
        result.append(source)
        if len(result) >= SYMBOL_WEB_SOURCE_LIMIT:
            break
    return tuple(result)


def _dedupe_text(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = _collapse_text(value)
        if not clean:
            continue
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(clean)
    return result


def _drop_empty(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {}, ())}


def _shorten(value: Any, limit: int) -> str:
    text = _collapse_text(str(value or ""))
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 40)].rstrip() + f" [truncated to {limit} chars]"


def _collapse_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalize_symbol(value: Any) -> str:
    return re.sub(r"[^A-Z0-9.\-_/]", "", str(value or "").strip().upper())
