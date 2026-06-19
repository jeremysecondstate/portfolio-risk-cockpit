from __future__ import annotations

from types import SimpleNamespace

from app.macro import sources
from app.macro.models import MacroSourceStatus
from app.macro.sources import (
    collect_macro_snapshot,
    fetch_eia_releases,
    parse_census_eits_response,
    parse_eia_petroleum_response,
    parse_federal_reserve_h15_response,
)


def test_parse_census_eits_fixture_payload() -> None:
    releases = parse_census_eits_response(
        [
            ["time", "data_type_code", "time_slot_id", "seasonally_adj", "category_code", "cell_value", "error_data"],
            ["2026", "SM", "2026-05", "yes", "44X72", "731200", ""],
            ["2026", "SM", "2026-04", "yes", "44X72", "725000", ""],
        ],
        metric="Retail sales",
        category="consumer",
        unit="millions of dollars",
        fetched_at="2026-06-18T12:00:00+00:00",
        raw_source=sources.CENSUS_MARTS_URL,
    )

    assert len(releases) == 1
    assert releases[0].source == "Census.gov"
    assert releases[0].actual == 731200.0
    assert releases[0].prior == 725000.0
    assert releases[0].freshness_status == "fresh"


def test_fetch_census_releases_uses_prior_year_fallback_window(monkeypatch) -> None:
    header = ["time", "data_type_code", "time_slot_id", "seasonally_adj", "category_code", "cell_value", "error_data"]
    calls: list[tuple[str, str]] = []

    def fake_get(url, *, params, timeout):
        calls.append((url, params["time"]))
        payload = [header]
        if params["time"] == "2025":
            payload.append(["2025", "SM", "2025-12", "yes", "44X72", "731200", ""])
            payload.append(["2025", "SM", "2025-11", "yes", "44X72", "725000", ""])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)

    monkeypatch.setenv("CENSUS_API_KEY", "test-key")
    monkeypatch.setattr(sources, "_current_utc_year", lambda: 2026)
    monkeypatch.setattr(sources.requests, "get", fake_get)

    releases, status = sources.fetch_census_releases()

    assert len(releases) == 3
    assert status.status == "fresh"
    assert any(window == "2025" for _url, window in calls)
    assert "attempted latest/prior windows" in status.message


def test_parse_federal_reserve_h15_fixture_csv() -> None:
    text = "\n".join(
        [
            "Some metadata,row",
            "Time Period,H15/H15/RIFLGFCY10_N.B,H15/H15/RIFLGFCY02_N.B",
            "2026-06-16,4.10,3.82",
            "2026-06-17,4.20,3.90",
        ]
    )

    releases = parse_federal_reserve_h15_response(text, fetched_at="2026-06-18T12:00:00+00:00")

    by_metric = {release.metric: release for release in releases}
    assert by_metric["10-year Federal Reserve H.15 Treasury yield"].actual == 4.20
    assert by_metric["10-year Federal Reserve H.15 Treasury yield"].prior == 4.10
    assert by_metric["2-year Federal Reserve H.15 Treasury yield"].source == "FederalReserve.gov"


def test_parse_eia_petroleum_fixture_payload() -> None:
    releases = parse_eia_petroleum_response(
        {
            "response": {
                "data": [
                    {"period": "2026-06-12", "series-description": "U.S. Ending Stocks of Crude Oil", "value": "417900", "units": "thousand barrels"},
                    {"period": "2026-06-05", "series-description": "U.S. Ending Stocks of Crude Oil", "value": "420000", "units": "thousand barrels"},
                ]
            }
        },
        fetched_at="2026-06-18T12:00:00+00:00",
    )

    assert len(releases) == 1
    assert releases[0].source == "EIA.gov"
    assert releases[0].actual == 417900.0
    assert releases[0].prior == 420000.0
    assert releases[0].category == "energy"


def test_eia_missing_key_returns_unavailable_status(monkeypatch) -> None:
    monkeypatch.delenv("EIA_API_KEY", raising=False)

    releases, status = fetch_eia_releases()

    assert releases == []
    assert status.source == "EIA.gov"
    assert status.status == "unavailable"
    assert "EIA_API_KEY" in status.message


def test_collect_macro_snapshot_does_not_emit_replaced_planned_sources(monkeypatch) -> None:
    def empty(source: str):
        return [], MacroSourceStatus(source, "unavailable", "2026-06-18T12:00:00+00:00", source, "fixture")

    monkeypatch.setattr(sources, "load_macro_cache", lambda: None)
    monkeypatch.setattr(sources, "save_macro_cache", lambda snapshot: None)
    monkeypatch.setattr(sources, "fetch_bls_releases", lambda timeout_seconds=12: empty("BLS.gov"))
    monkeypatch.setattr(sources, "fetch_bea_releases", lambda timeout_seconds=12: empty("BEA.gov"))
    monkeypatch.setattr(sources, "fetch_treasury_rates", lambda timeout_seconds=12: empty("Treasury.gov"))
    monkeypatch.setattr(sources, "fetch_census_releases", lambda timeout_seconds=12: empty("Census.gov"))
    monkeypatch.setattr(sources, "fetch_federal_reserve_releases", lambda timeout_seconds=12: empty("FederalReserve.gov"))
    monkeypatch.setattr(sources, "fetch_eia_releases", lambda timeout_seconds=12: empty("EIA.gov"))

    snapshot = collect_macro_snapshot(force_refresh=True)

    planned_sources = {release.source for release in snapshot.releases if release.freshness_status == "planned"}
    assert not (planned_sources & {"Census.gov", "FederalReserve.gov", "EIA.gov"})
    statuses = {status.source for status in snapshot.source_statuses}
    assert {"Census.gov", "FederalReserve.gov", "EIA.gov"} <= statuses
