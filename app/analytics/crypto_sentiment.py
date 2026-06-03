from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


POSITIVE_TERMS = (
    "breakout",
    "bid",
    "inflow",
    "upgrade",
    "partnership",
    "adoption",
    "accumulation",
    "strong",
    "bull",
    "rally",
    "record",
)
NEGATIVE_TERMS = (
    "hack",
    "exploit",
    "outflow",
    "liquidation",
    "selloff",
    "lawsuit",
    "downgrade",
    "bear",
    "weak",
    "halt",
    "depeg",
)
ASSET_TERMS = {
    "BTC": ("BTC", "Bitcoin"),
    "ETH": ("ETH", "Ethereum"),
    "SOL": ("SOL", "Solana"),
    "HYPE": ("HYPE", "Hyperliquid"),
}


@dataclass(frozen=True)
class CryptoSentimentSnapshot:
    symbol: str
    label: str
    status: str
    score: float
    headline_count: int
    narratives: list[str]
    provider: str
    provider_status: str
    source_freshness: str
    fetched_at: str
    message: str = ""


class CryptoSentimentProvider(Protocol):
    name: str

    def fetch(self, symbol: str, search_terms: tuple[str, ...]) -> CryptoSentimentSnapshot:
        ...


def build_crypto_sentiment_snapshot(
    symbol: str,
    *,
    headlines: list[str] | None = None,
    provider: CryptoSentimentProvider | None = None,
) -> CryptoSentimentSnapshot:
    clean = symbol.strip().upper()
    terms = ASSET_TERMS.get(clean, (clean,))
    if provider is not None:
        try:
            return provider.fetch(clean, terms)
        except Exception as exc:
            return CryptoSentimentSnapshot(
                clean,
                "Provider Error",
                "error",
                0.0,
                0,
                ["Optional sentiment provider failed; price/exposure analysis still works."],
                getattr(provider, "name", "Custom provider"),
                "error",
                "error",
                _now(),
                str(exc),
            )

    configured_headlines = headlines if headlines is not None else _headlines_from_env()
    if not configured_headlines:
        return CryptoSentimentSnapshot(
            clean,
            "Not Configured",
            "not configured",
            0.0,
            0,
            [
                "No sentiment/news provider is configured.",
                "Set CRYPTO_NEWS_HEADLINES or plug in a provider to add narrative scoring.",
            ],
            "Local keyword scanner",
            "not configured",
            "not configured",
            _now(),
            "Optional sentiment/news inputs are absent.",
        )

    relevant = [headline for headline in configured_headlines if _mentions_asset(headline, terms)]
    scanned = relevant or configured_headlines
    score, narratives = scan_headline_sentiment(scanned)
    label = "Positive" if score >= 20 else "Negative" if score <= -20 else "Mixed"
    status = "fresh" if relevant else "fresh/cache"
    freshness = "fresh" if relevant else "fresh/cache"
    message = "Asset-specific headlines scanned." if relevant else "No asset-specific hit; scanned configured crypto headlines."
    return CryptoSentimentSnapshot(
        clean,
        label,
        status,
        score,
        len(scanned),
        narratives,
        "Local keyword scanner",
        "fresh",
        freshness,
        _now(),
        message,
    )


def scan_headline_sentiment(headlines: list[str]) -> tuple[float, list[str]]:
    if not headlines:
        return 0.0, ["No headlines to scan."]
    positive_hits = 0
    negative_hits = 0
    narratives: list[str] = []
    for headline in headlines:
        lower = headline.lower()
        positive = sum(1 for term in POSITIVE_TERMS if term in lower)
        negative = sum(1 for term in NEGATIVE_TERMS if term in lower)
        positive_hits += positive
        negative_hits += negative
        if positive > negative:
            narratives.append(f"Positive: {headline}")
        elif negative > positive:
            narratives.append(f"Negative: {headline}")
        elif len(narratives) < 4:
            narratives.append(f"Neutral: {headline}")
    raw = (positive_hits - negative_hits) * 18
    score = max(-100.0, min(100.0, raw))
    return score, narratives[:5] or ["Headlines were scanned but no clear narrative keywords were found."]


def _headlines_from_env() -> list[str]:
    raw = os.getenv("CRYPTO_NEWS_HEADLINES", "").strip()
    if not raw:
        return []
    separator = "||" if "||" in raw else "\n"
    return [item.strip() for item in raw.split(separator) if item.strip()]


def _mentions_asset(headline: str, terms: tuple[str, ...]) -> bool:
    upper = headline.upper()
    return any(term.upper() in upper for term in terms if term)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
