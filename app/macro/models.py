from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class MacroRelease:
    category: str
    metric: str
    source: str
    period: str
    release_timestamp: str
    actual: float | None
    prior: float | None
    revision: float | None
    forecast: float | None
    unit: str
    raw_source: str
    freshness_status: str
    fetch_timestamp: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MacroRelease":
        return cls(
            category=str(payload.get("category") or ""),
            metric=str(payload.get("metric") or ""),
            source=str(payload.get("source") or ""),
            period=str(payload.get("period") or ""),
            release_timestamp=str(payload.get("release_timestamp") or ""),
            actual=_optional_float(payload.get("actual")),
            prior=_optional_float(payload.get("prior")),
            revision=_optional_float(payload.get("revision")),
            forecast=_optional_float(payload.get("forecast")),
            unit=str(payload.get("unit") or ""),
            raw_source=str(payload.get("raw_source") or ""),
            freshness_status=str(payload.get("freshness_status") or "cached"),
            fetch_timestamp=str(payload.get("fetch_timestamp") or ""),
            notes=str(payload.get("notes") or ""),
        )


@dataclass(frozen=True)
class MacroSourceStatus:
    source: str
    status: str
    fetched_at: str
    url: str
    message: str = ""
    cached_fallback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MacroSourceStatus":
        return cls(
            source=str(payload.get("source") or ""),
            status=str(payload.get("status") or ""),
            fetched_at=str(payload.get("fetched_at") or ""),
            url=str(payload.get("url") or ""),
            message=str(payload.get("message") or ""),
            cached_fallback=bool(payload.get("cached_fallback")),
        )


@dataclass(frozen=True)
class MacroSnapshot:
    fetched_at: str
    releases: list[MacroRelease]
    source_statuses: list[MacroSourceStatus]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at,
            "releases": [release.to_dict() for release in self.releases],
            "source_statuses": [status.to_dict() for status in self.source_statuses],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "MacroSnapshot":
        return cls(
            fetched_at=str(payload.get("fetched_at") or ""),
            releases=[MacroRelease.from_dict(item) for item in payload.get("releases") or [] if isinstance(item, dict)],
            source_statuses=[MacroSourceStatus.from_dict(item) for item in payload.get("source_statuses") or [] if isinstance(item, dict)],
        )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
