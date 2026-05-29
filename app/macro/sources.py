from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.macro.models import MacroRelease, MacroSnapshot, MacroSourceStatus, utc_now_iso

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BEA_URL = "https://apps.bea.gov/api/data"
TREASURY_DAILY_RATES_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/daily_treasury_rates"
CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "macro_cache.json"
DEFAULT_TIMEOUT_SECONDS = 12

BLS_SERIES = {
    "CUUR0000SA0": ("inflation", "CPI", "index"),
    "CUUR0000SA0L1E": ("inflation", "Core CPI", "index"),
    "WPU00000000": ("inflation", "PPI final demand", "index"),
    "CES0000000001": ("labor", "Nonfarm payrolls", "thousands"),
    "LNS14000000": ("labor", "Unemployment rate", "percent"),
    "LNS11300000": ("labor", "Labor force participation", "percent"),
    "CES0500000003": ("labor", "Average hourly earnings", "dollars"),
    "JTS000000000000000JOL": ("labor", "JOLTS job openings", "thousands"),
}


def collect_macro_snapshot(*, force_refresh: bool = False, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> MacroSnapshot:
    cached = load_macro_cache()
    releases: list[MacroRelease] = []
    statuses: list[MacroSourceStatus] = []

    for source_name, fetcher in (
        ("BLS.gov", fetch_bls_releases),
        ("BEA.gov", fetch_bea_releases),
        ("Treasury.gov", fetch_treasury_rates),
    ):
        try:
            fetched_releases, status = fetcher(timeout_seconds=timeout_seconds)
            releases.extend(fetched_releases)
            statuses.append(status)
        except Exception as exc:
            fallback = _cached_releases_for_source(cached, source_name)
            releases.extend(_mark_cached_fallback(fallback, str(exc)))
            statuses.append(
                MacroSourceStatus(
                    source=source_name,
                    status="error" if not fallback else "cached",
                    fetched_at=utc_now_iso(),
                    url=_source_url_for_name(source_name),
                    message=str(exc),
                    cached_fallback=bool(fallback),
                )
            )

    releases.extend(_planned_source_placeholders())
    snapshot = MacroSnapshot(fetched_at=utc_now_iso(), releases=releases, source_statuses=statuses)
    if releases and not all(status.status == "error" for status in statuses):
        save_macro_cache(snapshot)
    elif cached is not None and not force_refresh:
        return MacroSnapshot(fetched_at=utc_now_iso(), releases=cached.releases, source_statuses=statuses + cached.source_statuses)
    return snapshot


def fetch_bls_releases(*, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[list[MacroRelease], MacroSourceStatus]:
    now = utc_now_iso()
    current_year = datetime.now(timezone.utc).year
    payload = {
        "seriesid": list(BLS_SERIES),
        "startyear": str(current_year - 1),
        "endyear": str(current_year),
        "calculations": "true",
    }
    response = requests.post(BLS_URL, json=payload, timeout=timeout_seconds)
    response.raise_for_status()
    data = response.json()
    releases = parse_bls_response(data, fetched_at=now, raw_source=BLS_URL)
    status = "fresh" if releases else "unavailable"
    return releases, MacroSourceStatus("BLS.gov", status, now, BLS_URL, f"{len(releases)} BLS series loaded.")


def parse_bls_response(payload: dict[str, Any], *, fetched_at: str, raw_source: str = BLS_URL) -> list[MacroRelease]:
    releases: list[MacroRelease] = []
    series_items = (((payload or {}).get("Results") or {}).get("series") or [])
    for series in series_items:
        series_id = str(series.get("seriesID") or series.get("seriesid") or "")
        category, metric, unit = BLS_SERIES.get(series_id, ("macro", series_id or "BLS series", ""))
        data = series.get("data") or []
        if not data:
            continue
        latest = data[0]
        prior = data[1] if len(data) > 1 else {}
        period = _bls_period(latest)
        releases.append(
            MacroRelease(
                category=category,
                metric=metric,
                source="BLS.gov",
                period=period,
                release_timestamp=period,
                actual=_to_float(latest.get("value")),
                prior=_to_float(prior.get("value")),
                revision=None,
                forecast=None,
                unit=unit,
                raw_source=raw_source,
                freshness_status="fresh",
                fetch_timestamp=fetched_at,
                notes=_bls_calculation_note(latest),
            )
        )
    return releases


def fetch_bea_releases(*, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[list[MacroRelease], MacroSourceStatus]:
    now = utc_now_iso()
    releases: list[MacroRelease] = []
    for spec in _bea_specs():
        params = {
            "UserID": os.getenv("BEA_API_KEY", "sampleUserID"),
            "method": "GetData",
            "datasetname": "NIPA",
            "TableName": spec["table"],
            "LineNumber": spec["line"],
            "Frequency": spec["frequency"],
            "Year": "X",
            "ResultFormat": "JSON",
        }
        response = requests.get(BEA_URL, params=params, timeout=timeout_seconds)
        response.raise_for_status()
        releases.extend(parse_bea_response(response.json(), metric=spec["metric"], category=spec["category"], unit=spec["unit"], fetched_at=now, raw_source=BEA_URL))
    status = "fresh" if releases else "unavailable"
    return releases, MacroSourceStatus("BEA.gov", status, now, BEA_URL, f"{len(releases)} BEA series loaded.")


def parse_bea_response(
    payload: dict[str, Any],
    *,
    metric: str,
    category: str,
    unit: str,
    fetched_at: str,
    raw_source: str = BEA_URL,
) -> list[MacroRelease]:
    rows = (((payload or {}).get("BEAAPI") or {}).get("Results") or {}).get("Data") or []
    parsed_rows = [row for row in rows if isinstance(row, dict) and row.get("DataValue") not in {None, ""}]
    if not parsed_rows:
        return []
    latest = parsed_rows[0]
    prior = parsed_rows[1] if len(parsed_rows) > 1 else {}
    return [
        MacroRelease(
            category=category,
            metric=metric,
            source="BEA.gov",
            period=str(latest.get("TimePeriod") or ""),
            release_timestamp=str(latest.get("TimePeriod") or ""),
            actual=_to_float(latest.get("DataValue")),
            prior=_to_float(prior.get("DataValue")),
            revision=None,
            forecast=None,
            unit=unit,
            raw_source=raw_source,
            freshness_status="fresh",
            fetch_timestamp=fetched_at,
            notes=str(latest.get("LineDescription") or ""),
        )
    ]


def fetch_treasury_rates(*, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[list[MacroRelease], MacroSourceStatus]:
    now = utc_now_iso()
    response = requests.get(
        TREASURY_DAILY_RATES_URL,
        params={"sort": "-record_date", "page[size]": "2"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    releases = parse_treasury_rates_response(response.json(), fetched_at=now, raw_source=TREASURY_DAILY_RATES_URL)
    status = "fresh" if releases else "unavailable"
    return releases, MacroSourceStatus("Treasury.gov", status, now, TREASURY_DAILY_RATES_URL, f"{len(releases)} Treasury rates loaded.")


def parse_treasury_rates_response(payload: dict[str, Any], *, fetched_at: str, raw_source: str = TREASURY_DAILY_RATES_URL) -> list[MacroRelease]:
    rows = (payload or {}).get("data") or []
    if not rows:
        return []
    latest = rows[0]
    prior = rows[1] if len(rows) > 1 else {}
    releases: list[MacroRelease] = []
    rate_fields = (
        ("2-year Treasury yield", "security_desc_2_yr"),
        ("10-year Treasury yield", "security_desc_10_yr"),
        ("30-year Treasury yield", "security_desc_30_yr"),
    )
    for metric, field in rate_fields:
        releases.append(
            MacroRelease(
                category="treasury",
                metric=metric,
                source="Treasury.gov",
                period=str(latest.get("record_date") or ""),
                release_timestamp=str(latest.get("record_date") or ""),
                actual=_to_float(latest.get(field)),
                prior=_to_float(prior.get(field)),
                revision=None,
                forecast=None,
                unit="percent",
                raw_source=raw_source,
                freshness_status="fresh",
                fetch_timestamp=fetched_at,
            )
        )
    return [release for release in releases if release.actual is not None]


def load_macro_cache(path: Path | None = None) -> MacroSnapshot | None:
    path = path or CACHE_PATH
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return MacroSnapshot.from_dict(payload)
    except Exception:
        return None


def save_macro_cache(snapshot: MacroSnapshot, path: Path | None = None) -> None:
    path = path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(snapshot.to_dict(), handle, indent=2)


def _bea_specs() -> list[dict[str, str]]:
    return [
        {"metric": "Real GDP", "category": "growth", "unit": "annualized percent", "table": "T10101", "line": "1", "frequency": "Q"},
        {"metric": "PCE price index", "category": "inflation", "unit": "annualized percent", "table": "T20804", "line": "1", "frequency": "M"},
        {"metric": "Personal income", "category": "consumer", "unit": "annualized percent", "table": "T20600", "line": "1", "frequency": "M"},
    ]


def _planned_source_placeholders() -> list[MacroRelease]:
    now = utc_now_iso()
    planned = [
        ("DOL.gov", "labor", "Initial jobless claims", "Official weekly claims feed hook planned."),
        ("FederalReserve.gov", "rates", "Fed policy / balance sheet", "Official Federal Reserve feed hook planned."),
        ("EIA.gov", "energy", "Crude and energy inventories", "Official EIA API hook planned; EIA_API_KEY may be used later."),
        ("Census.gov", "housing", "Housing / retail / durable goods", "Official Census API hook planned."),
        ("USDA.gov", "agriculture", "WASDE / agriculture supply-demand", "Official USDA feed hook planned."),
    ]
    return [
        MacroRelease(
            category=category,
            metric=metric,
            source=source,
            period="--",
            release_timestamp="--",
            actual=None,
            prior=None,
            revision=None,
            forecast=None,
            unit="",
            raw_source=source,
            freshness_status="planned",
            fetch_timestamp=now,
            notes=note,
        )
        for source, category, metric, note in planned
    ]


def _cached_releases_for_source(snapshot: MacroSnapshot | None, source: str) -> list[MacroRelease]:
    if snapshot is None:
        return []
    return [release for release in snapshot.releases if release.source == source]


def _mark_cached_fallback(releases: list[MacroRelease], error: str) -> list[MacroRelease]:
    return [
        MacroRelease(
            category=release.category,
            metric=release.metric,
            source=release.source,
            period=release.period,
            release_timestamp=release.release_timestamp,
            actual=release.actual,
            prior=release.prior,
            revision=release.revision,
            forecast=release.forecast,
            unit=release.unit,
            raw_source=release.raw_source,
            freshness_status="cached",
            fetch_timestamp=release.fetch_timestamp,
            notes=f"Cached fallback after source error: {error}",
        )
        for release in releases
    ]


def _source_url_for_name(source: str) -> str:
    return {"BLS.gov": BLS_URL, "BEA.gov": BEA_URL, "Treasury.gov": TREASURY_DAILY_RATES_URL}.get(source, source)


def _bls_period(row: dict[str, Any]) -> str:
    return f"{row.get('year', '')} {row.get('periodName') or row.get('period') or ''}".strip()


def _bls_calculation_note(row: dict[str, Any]) -> str:
    calculations = row.get("calculations") or {}
    pct = calculations.get("pct_changes") or {}
    one_month = pct.get("1")
    return f"1-period percent change {one_month}" if one_month not in {None, ""} else ""


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
