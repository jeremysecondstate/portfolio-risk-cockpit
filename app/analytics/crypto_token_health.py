from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.analytics.hyperliquid_market_data import MarketDataSourceStatus, post_hyperliquid_info


@dataclass(frozen=True)
class TokenHealthSnapshot:
    symbol: str
    label: str
    status: str
    fetched_at: str
    metrics: dict[str, str] = field(default_factory=dict)
    source_statuses: list[MarketDataSourceStatus] = field(default_factory=list)
    message: str = ""


def fetch_token_health_snapshot(symbol: str, *, timeout_seconds: int = 8) -> TokenHealthSnapshot:
    clean = symbol.strip().upper()
    statuses: list[MarketDataSourceStatus] = []
    try:
        payload = post_hyperliquid_info({"type": "spotMetaAndAssetCtxs"}, timeout_seconds=timeout_seconds)
        statuses.append(MarketDataSourceStatus("Hyperliquid", "spotMetaAndAssetCtxs", "fresh", _now(), "", None, "Spot token metadata loaded."))
    except Exception as exc:
        statuses.append(MarketDataSourceStatus("Hyperliquid", "spotMetaAndAssetCtxs", "error", _now(), "", None, str(exc)))
        return TokenHealthSnapshot(clean, "Unavailable", "unavailable", _now(), {}, statuses, "Hyperliquid spot metadata unavailable.")
    return parse_token_health_snapshot(clean, payload, source_statuses=statuses)


def parse_token_health_snapshot(
    symbol: str,
    payload: Any,
    *,
    source_statuses: list[MarketDataSourceStatus] | None = None,
) -> TokenHealthSnapshot:
    clean = symbol.strip().upper()
    metrics: dict[str, str] = {}
    token = _find_token(clean, payload)
    market = _find_market(clean, payload)
    if token:
        for label, key in (
            ("Token index", "index"),
            ("Name", "name"),
            ("Full name", "fullName"),
            ("Wei decimals", "weiDecimals"),
            ("Size decimals", "szDecimals"),
            ("Token ID", "tokenId"),
            ("EVM contract", "evmContract"),
        ):
            value = token.get(key)
            if value not in (None, ""):
                metrics[label] = str(value)
    if market:
        for label, key in (("Spot market", "name"), ("Market index", "index"), ("Is canonical", "isCanonical")):
            value = market.get(key)
            if value not in (None, ""):
                metrics[label] = str(value)

    if metrics:
        return TokenHealthSnapshot(clean, "Metadata Found", "fresh", _now(), metrics, source_statuses or [], "Hyperliquid spot token metadata is available.")
    return TokenHealthSnapshot(
        clean,
        "Unavailable",
        "unavailable",
        _now(),
        {},
        source_statuses or [],
        "No Hyperliquid spot token metadata matched this symbol.",
    )


def _find_token(symbol: str, payload: Any) -> dict[str, Any] | None:
    meta = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else {}
    tokens = meta.get("tokens") if isinstance(meta, dict) else None
    if not isinstance(tokens, list):
        return None
    for token in tokens:
        if not isinstance(token, dict):
            continue
        names = {str(token.get(key) or "").strip().upper() for key in ("name", "fullName", "token")}
        if symbol in names:
            return token
    return None


def _find_market(symbol: str, payload: Any) -> dict[str, Any] | None:
    meta = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], dict) else {}
    universe = meta.get("universe") if isinstance(meta, dict) else None
    tokens = meta.get("tokens") if isinstance(meta, dict) else None
    token_names_by_index: dict[int, set[str]] = {}
    if isinstance(tokens, list):
        for index, token in enumerate(tokens):
            if not isinstance(token, dict):
                continue
            names = {str(token.get(key) or "").strip().upper() for key in ("name", "fullName", "token") if token.get(key)}
            explicit = _to_int(token.get("index"))
            token_names_by_index.setdefault(index, set()).update(names)
            if explicit is not None:
                token_names_by_index.setdefault(explicit, set()).update(names)
    if not isinstance(universe, list):
        return None
    for market in universe:
        if not isinstance(market, dict):
            continue
        names = {str(market.get(key) or "").strip().upper() for key in ("name", "coin", "symbol") if market.get(key)}
        token_indexes = market.get("tokens")
        if isinstance(token_indexes, list):
            for index in token_indexes:
                token_index = _to_int(index)
                if token_index is not None:
                    names.update(token_names_by_index.get(token_index, set()))
        if symbol in names or any(name.startswith(f"{symbol}/") or name.startswith(f"{symbol}-") for name in names):
            return market
    return None


def _to_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
