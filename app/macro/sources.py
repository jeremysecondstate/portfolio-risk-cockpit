from __future__ import annotations

import json
import os
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from app.macro.models import MacroRelease, MacroSnapshot, MacroSourceStatus, utc_now_iso

BLS_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BEA_URL = "https://apps.bea.gov/api/data"
TREASURY_DAILY_RATES_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/daily_treasury_rates"
CENSUS_MARTS_URL = "https://api.census.gov/data/timeseries/eits/marts"
CENSUS_RESCONST_URL = "https://api.census.gov/data/timeseries/eits/resconst"
CENSUS_M3_URL = "https://api.census.gov/data/timeseries/eits/m3"
FEDERAL_RESERVE_H15_URL = "https://www.federalreserve.gov/datadownload/Output.aspx"
EIA_WEEKLY_PETROLEUM_URL = "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "macro_cache.json"
DEFAULT_TIMEOUT_SECONDS = 12
CENSUS_API_KEY_ENV = "CENSUS_API_KEY"
EIA_API_KEY_ENV = "EIA_API_KEY"
MACRO_ENABLE_CENSUS_ENV = "MACRO_ENABLE_CENSUS"
MACRO_ENABLE_FEDERAL_RESERVE_ENV = "MACRO_ENABLE_FEDERAL_RESERVE"
MACRO_ENABLE_EIA_ENV = "MACRO_ENABLE_EIA"
FEDERAL_RESERVE_H15_DAILY_SERIES = "bf17364827e38702b42a58cf8eaa3f78"
REPLACED_PLANNED_MACRO_SOURCES = {"Census.gov", "FederalReserve.gov", "EIA.gov"}

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
    cached = _without_replaced_planned_sources(load_macro_cache())
    releases: list[MacroRelease] = []
    statuses: list[MacroSourceStatus] = []

    for source_name, fetcher in (
        ("BLS.gov", fetch_bls_releases),
        ("BEA.gov", fetch_bea_releases),
        ("Treasury.gov", fetch_treasury_rates),
        ("Census.gov", fetch_census_releases),
        ("FederalReserve.gov", fetch_federal_reserve_releases),
        ("EIA.gov", fetch_eia_releases),
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


def fetch_census_releases(*, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[list[MacroRelease], MacroSourceStatus]:
    now = utc_now_iso()
    if not _env_flag(MACRO_ENABLE_CENSUS_ENV, True):
        return [], MacroSourceStatus("Census.gov", "disabled", now, CENSUS_MARTS_URL, f"Census macro source disabled by {MACRO_ENABLE_CENSUS_ENV}.")
    api_key = os.getenv(CENSUS_API_KEY_ENV, "").strip()
    if not api_key:
        return [], MacroSourceStatus("Census.gov", "unavailable", now, CENSUS_MARTS_URL, f"No {CENSUS_API_KEY_ENV} is configured; Census EITS queries require a key.")

    releases: list[MacroRelease] = []
    for spec in _census_specs():
        params = {
            "get": "data_type_code,time_slot_id,seasonally_adj,category_code,cell_value,error_data",
            "for": "us:*",
            "time": str(datetime.now(timezone.utc).year),
            "key": api_key,
        }
        response = requests.get(spec["url"], params=params, timeout=timeout_seconds)
        response.raise_for_status()
        releases.extend(
            parse_census_eits_response(
                response.json(),
                metric=spec["metric"],
                category=spec["category"],
                unit=spec["unit"],
                fetched_at=now,
                raw_source=spec["url"],
            )
        )
    status = "fresh" if releases else "unavailable"
    return releases, MacroSourceStatus("Census.gov", status, now, CENSUS_MARTS_URL, f"{len(releases)} Census EITS series loaded.")


def parse_census_eits_response(
    payload: Any,
    *,
    metric: str,
    category: str,
    unit: str,
    fetched_at: str,
    raw_source: str,
) -> list[MacroRelease]:
    rows = _table_payload_rows(payload)
    parsed_rows = [row for row in rows if _to_float(row.get("cell_value")) is not None]
    if not parsed_rows:
        return []
    parsed_rows.sort(key=lambda row: (str(row.get("time") or ""), str(row.get("time_slot_id") or "")), reverse=True)
    latest = parsed_rows[0]
    prior = parsed_rows[1] if len(parsed_rows) > 1 else {}
    period = str(latest.get("time") or latest.get("time_slot_id") or "")
    return [
        MacroRelease(
            category=category,
            metric=metric,
            source="Census.gov",
            period=period,
            release_timestamp=period,
            actual=_to_float(latest.get("cell_value")),
            prior=_to_float(prior.get("cell_value")),
            revision=None,
            forecast=None,
            unit=unit,
            raw_source=raw_source,
            freshness_status="fresh",
            fetch_timestamp=fetched_at,
            notes=_census_note(latest),
        )
    ]


def fetch_federal_reserve_releases(*, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[list[MacroRelease], MacroSourceStatus]:
    now = utc_now_iso()
    if not _env_flag(MACRO_ENABLE_FEDERAL_RESERVE_ENV, True):
        return [], MacroSourceStatus("FederalReserve.gov", "disabled", now, FEDERAL_RESERVE_H15_URL, f"Federal Reserve macro source disabled by {MACRO_ENABLE_FEDERAL_RESERVE_ENV}.")
    response = requests.get(
        FEDERAL_RESERVE_H15_URL,
        params={
            "rel": "H15",
            "series": FEDERAL_RESERVE_H15_DAILY_SERIES,
            "lastobs": "5",
            "from": "",
            "to": "",
            "filetype": "csv",
            "label": "include",
            "layout": "seriescolumn",
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    releases = parse_federal_reserve_h15_response(response.text, fetched_at=now, raw_source=FEDERAL_RESERVE_H15_URL)
    status = "fresh" if releases else "unavailable"
    return releases, MacroSourceStatus("FederalReserve.gov", status, now, FEDERAL_RESERVE_H15_URL, f"{len(releases)} Federal Reserve H.15 series loaded.")


def parse_federal_reserve_h15_response(text: str, *, fetched_at: str, raw_source: str = FEDERAL_RESERVE_H15_URL) -> list[MacroRelease]:
    rows = [row for row in csv.reader(str(text or "").splitlines()) if any(cell.strip() for cell in row)]
    header_index = next((index for index, row in enumerate(rows) if row and row[0].strip().lower() in {"time period", "date"}), None)
    if header_index is None:
        return []
    header = [cell.strip() for cell in rows[header_index]]
    data_rows = [row for row in rows[header_index + 1:] if row and _looks_like_period(row[0])]
    if not data_rows:
        return []
    latest = data_rows[-1]
    prior = data_rows[-2] if len(data_rows) > 1 else []
    releases: list[MacroRelease] = []
    for index, column in enumerate(header[1:], start=1):
        metric = _federal_reserve_metric_name(column)
        if not metric:
            continue
        actual = _to_float(latest[index] if index < len(latest) else None)
        if actual is None:
            continue
        releases.append(
            MacroRelease(
                category="rates",
                metric=metric,
                source="FederalReserve.gov",
                period=str(latest[0]),
                release_timestamp=str(latest[0]),
                actual=actual,
                prior=_to_float(prior[index] if index < len(prior) else None),
                revision=None,
                forecast=None,
                unit="percent",
                raw_source=raw_source,
                freshness_status="fresh",
                fetch_timestamp=fetched_at,
                notes="Federal Reserve H.15 selected interest rates.",
            )
        )
    return releases


def fetch_eia_releases(*, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> tuple[list[MacroRelease], MacroSourceStatus]:
    now = utc_now_iso()
    if not _env_flag(MACRO_ENABLE_EIA_ENV, True):
        return [], MacroSourceStatus("EIA.gov", "disabled", now, EIA_WEEKLY_PETROLEUM_URL, f"EIA macro source disabled by {MACRO_ENABLE_EIA_ENV}.")
    api_key = os.getenv(EIA_API_KEY_ENV, "").strip()
    if not api_key:
        return [], MacroSourceStatus("EIA.gov", "unavailable", now, EIA_WEEKLY_PETROLEUM_URL, f"No {EIA_API_KEY_ENV} is configured; EIA petroleum inventory queries require a key.")
    response = requests.get(
        EIA_WEEKLY_PETROLEUM_URL,
        params={
            "api_key": api_key,
            "frequency": "weekly",
            "data[0]": "value",
            "facets[series][]": "WCESTUS1",
            "sort[0][column]": "period",
            "sort[0][direction]": "desc",
            "offset": "0",
            "length": "2",
        },
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    releases = parse_eia_petroleum_response(response.json(), fetched_at=now, raw_source=EIA_WEEKLY_PETROLEUM_URL)
    status = "fresh" if releases else "unavailable"
    return releases, MacroSourceStatus("EIA.gov", status, now, EIA_WEEKLY_PETROLEUM_URL, f"{len(releases)} EIA petroleum inventory series loaded.")


def parse_eia_petroleum_response(payload: dict[str, Any], *, fetched_at: str, raw_source: str = EIA_WEEKLY_PETROLEUM_URL) -> list[MacroRelease]:
    rows = (((payload or {}).get("response") or {}).get("data") or [])
    parsed_rows = [row for row in rows if isinstance(row, dict) and _to_float(row.get("value")) is not None]
    if not parsed_rows:
        return []
    parsed_rows.sort(key=lambda row: str(row.get("period") or ""), reverse=True)
    latest = parsed_rows[0]
    prior = parsed_rows[1] if len(parsed_rows) > 1 else {}
    metric = str(latest.get("series-description") or latest.get("seriesDescription") or latest.get("series") or "Crude oil inventories")
    return [
        MacroRelease(
            category="energy",
            metric=metric,
            source="EIA.gov",
            period=str(latest.get("period") or ""),
            release_timestamp=str(latest.get("period") or ""),
            actual=_to_float(latest.get("value")),
            prior=_to_float(prior.get("value")),
            revision=None,
            forecast=None,
            unit=str(latest.get("units") or latest.get("unit") or "thousand barrels"),
            raw_source=raw_source,
            freshness_status="fresh",
            fetch_timestamp=fetched_at,
            notes="EIA weekly petroleum status inventory series.",
        )
    ]


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


def _census_specs() -> list[dict[str, str]]:
    return [
        {"metric": "Retail sales", "category": "consumer", "unit": "millions of dollars", "url": CENSUS_MARTS_URL},
        {"metric": "New residential construction", "category": "housing", "unit": "thousands", "url": CENSUS_RESCONST_URL},
        {"metric": "Durable goods manufacturers orders", "category": "growth", "unit": "millions of dollars", "url": CENSUS_M3_URL},
    ]


def _planned_source_placeholders() -> list[MacroRelease]:
    now = utc_now_iso()
    planned = [
        ("DOL.gov", "labor", "Initial jobless claims", "Official weekly claims feed hook planned."),
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
    return [
        release
        for release in snapshot.releases
        if release.source == source and not (release.source in REPLACED_PLANNED_MACRO_SOURCES and release.freshness_status == "planned")
    ]


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
    return {
        "BLS.gov": BLS_URL,
        "BEA.gov": BEA_URL,
        "Treasury.gov": TREASURY_DAILY_RATES_URL,
        "Census.gov": CENSUS_MARTS_URL,
        "FederalReserve.gov": FEDERAL_RESERVE_H15_URL,
        "EIA.gov": EIA_WEEKLY_PETROLEUM_URL,
    }.get(source, source)


def _bls_period(row: dict[str, Any]) -> str:
    return f"{row.get('year', '')} {row.get('periodName') or row.get('period') or ''}".strip()


def _bls_calculation_note(row: dict[str, Any]) -> str:
    calculations = row.get("calculations") or {}
    pct = calculations.get("pct_changes") or {}
    one_month = pct.get("1")
    return f"1-period percent change {one_month}" if one_month not in {None, ""} else ""


def _without_replaced_planned_sources(snapshot: MacroSnapshot | None) -> MacroSnapshot | None:
    if snapshot is None:
        return None
    releases = [
        release
        for release in snapshot.releases
        if not (release.source in REPLACED_PLANNED_MACRO_SOURCES and release.freshness_status == "planned")
    ]
    return MacroSnapshot(fetched_at=snapshot.fetched_at, releases=releases, source_statuses=snapshot.source_statuses)


def _table_payload_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or not payload:
        return []
    header = payload[0]
    if not isinstance(header, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in payload[1:]:
        if not isinstance(raw, list):
            continue
        rows.append({str(header[index]): raw[index] for index in range(min(len(header), len(raw)))})
    return rows


def _census_note(row: dict[str, Any]) -> str:
    parts = []
    for key in ("data_type_code", "seasonally_adj", "category_code", "error_data"):
        value = row.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return "Census EITS " + ", ".join(parts) if parts else "Census EITS official series."


def _federal_reserve_metric_name(column: str) -> str:
    text = str(column or "").strip()
    upper = text.upper()
    if not text:
        return ""
    if "RIFLGFCY10" in upper or "10" in upper and "TREASURY" in upper:
        return "10-year Federal Reserve H.15 Treasury yield"
    if "RIFLGFCY02" in upper or "2" in upper and "TREASURY" in upper:
        return "2-year Federal Reserve H.15 Treasury yield"
    if "RIFLGFCY30" in upper or "30" in upper and "TREASURY" in upper:
        return "30-year Federal Reserve H.15 Treasury yield"
    if "RIFSPFF" in upper or "FEDERAL FUNDS" in upper:
        return "Federal funds effective rate"
    if "RIFSPBLP" in upper or "BANK PRIME" in upper:
        return "Bank prime loan rate"
    if upper.startswith("H15/"):
        return text
    return ""


def _looks_like_period(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return any(char.isdigit() for char in text)


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _env_flag(env_name: str, default: bool) -> bool:
    raw = os.getenv(env_name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}
