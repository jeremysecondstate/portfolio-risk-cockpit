from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.analytics.hyperliquid_market_data import HYPERLIQUID_INTERVALS, fetch_hyperliquid_candles as fetch_hyperliquid_candle_rows
from app.analytics.technical_analysis import Candle


CRYPTO_CANDLE_CACHE_PATH = Path(__file__).resolve().parents[1] / "data" / "crypto_candle_cache.json"
CRYPTO_CANDLE_CACHE_TTL_SECONDS = 10 * 60

COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "HYPE": "hyperliquid",
    "ZEC": "zcash",
    "SOL": "solana",
    "DOGE": "dogecoin",
    "LINK": "chainlink",
    "AVAX": "avalanche-2",
}


@dataclass(frozen=True)
class CryptoCandleResult:
    symbol: str
    candles: list[Candle]
    source: str
    status: str
    fetched_at: str
    message: str = ""
    timeframe: str = "1d"


Provider = Callable[[str, int, int, str], CryptoCandleResult]


def fetch_crypto_candles(symbol: str, *, days: int = 365, timeout_seconds: int = 8, timeframe: str = "1d") -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    cached = load_cached_crypto_candles(clean, timeframe=timeframe)
    if cached and time.time() - cached.get("cached_at", 0) <= CRYPTO_CANDLE_CACHE_TTL_SECONDS:
        return _result_from_cache(clean, cached, status="fresh/cache", timeframe=timeframe)

    providers: list[Provider] = [fetch_hyperliquid_candles, fetch_coinbase_candles, fetch_kraken_candles, fetch_coingecko_candles]
    result = fetch_crypto_candles_with_fallback(clean, providers, days=days, timeout_seconds=timeout_seconds, timeframe=timeframe)
    if result.candles:
        save_cached_crypto_candles(clean, result)
        return result
    if cached:
        cached_result = _result_from_cache(clean, cached, status="stale", timeframe=timeframe)
        return CryptoCandleResult(clean, cached_result.candles, cached_result.source, "stale", _now(), f"{result.message}; using stale cached candles.", timeframe)
    return result


def fetch_crypto_candles_with_fallback(
    symbol: str,
    providers: list[Provider],
    *,
    days: int = 365,
    timeout_seconds: int = 8,
    timeframe: str = "1d",
) -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    errors: list[str] = []
    for provider in providers:
        try:
            result = provider(clean, days, timeout_seconds, timeframe)
        except Exception as exc:
            errors.append(f"{provider.__name__}: {exc}")
            continue
        if result.candles:
            return result
        errors.append(f"{result.source}: {result.message or result.status}")
    return CryptoCandleResult(clean, [], "Crypto candles", "error", _now(), "; ".join(errors) or "No candle provider returned data.", timeframe)


def fetch_coinbase_candles(symbol: str, days: int, timeout_seconds: int, timeframe: str = "1d") -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    product = f"{clean}-USD"
    timeframe = normalize_timeframe(timeframe)
    granularity_by_timeframe = {"1m": 60, "5m": 300, "15m": 900, "1h": 3_600, "4h": 14_400, "1d": 86_400}
    if timeframe not in granularity_by_timeframe:
        raise ValueError(f"Coinbase fallback does not support {timeframe} candles.")
    granularity = granularity_by_timeframe[timeframe]
    url = f"https://api.exchange.coinbase.com/products/{urllib.parse.quote(product)}/candles?granularity={granularity}"
    payload = _fetch_json(url, timeout_seconds)
    candles = parse_coinbase_candles(payload)
    return CryptoCandleResult(clean, candles[-days:], "Coinbase public candles", "fresh", _now(), f"{product} {timeframe} candles.", timeframe)


def fetch_kraken_candles(symbol: str, days: int, timeout_seconds: int, timeframe: str = "1d") -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    pair = "XBTUSD" if clean == "BTC" else f"{clean}USD"
    interval_by_timeframe = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}
    if timeframe not in interval_by_timeframe:
        raise ValueError(f"Kraken fallback does not support {timeframe} candles.")
    interval = interval_by_timeframe[timeframe]
    url = f"https://api.kraken.com/0/public/OHLC?pair={urllib.parse.quote(pair)}&interval={interval}"
    payload = _fetch_json(url, timeout_seconds)
    candles = parse_kraken_ohlc(payload)
    return CryptoCandleResult(clean, candles[-days:], "Kraken public OHLC", "fresh", _now(), f"{pair} {timeframe} candles.", timeframe)


def fetch_coingecko_candles(symbol: str, days: int, timeout_seconds: int, timeframe: str = "1d") -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    coin_id = COINGECKO_IDS.get(clean)
    if not coin_id:
        raise ValueError(f"No CoinGecko id mapping for {clean}.")
    interval = "&interval=daily" if timeframe == "1d" else ""
    url = f"https://api.coingecko.com/api/v3/coins/{urllib.parse.quote(coin_id)}/market_chart?vs_currency=usd&days={days}{interval}"
    payload = _fetch_json(url, timeout_seconds)
    candles = parse_coingecko_market_chart(payload)
    return CryptoCandleResult(clean, candles[-days:], "CoinGecko market chart", "fresh", _now(), f"{coin_id} {timeframe} market chart.", timeframe)


def fetch_hyperliquid_candles(symbol: str, days: int, timeout_seconds: int, timeframe: str = "1d") -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    timeframe = normalize_timeframe(timeframe)
    candles, status = fetch_hyperliquid_candle_rows(clean, days=days, timeout_seconds=timeout_seconds, interval=timeframe)
    return CryptoCandleResult(clean, candles, "Hyperliquid candleSnapshot", status.status, status.fetched_at, status.message, timeframe)


def parse_coinbase_candles(payload: Any) -> list[Candle]:
    if not isinstance(payload, list):
        raise ValueError("Coinbase candles expected a list.")
    candles: list[Candle] = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 6:
            continue
        timestamp, low, high, open_, close, volume = row[:6]
        candles.append(
            Candle(
                datetime_ms=int(float(timestamp) * 1000),
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume),
            )
        )
    return sorted(candles, key=lambda candle: candle.datetime_ms)


def parse_kraken_ohlc(payload: Any) -> list[Candle]:
    if not isinstance(payload, dict):
        raise ValueError("Kraken OHLC expected an object.")
    errors = payload.get("error") or []
    if errors:
        raise ValueError(", ".join(str(error) for error in errors))
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("Kraken OHLC missing result object.")
    rows: list[Any] = []
    for key, value in result.items():
        if key != "last" and isinstance(value, list):
            rows = value
            break
    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            continue
        timestamp, open_, high, low, close, _vwap, volume = row[:7]
        candles.append(Candle(int(float(timestamp) * 1000), float(open_), float(high), float(low), float(close), float(volume)))
    return sorted(candles, key=lambda candle: candle.datetime_ms)


def parse_coingecko_market_chart(payload: Any) -> list[Candle]:
    if not isinstance(payload, dict):
        raise ValueError("CoinGecko market chart expected an object.")
    prices = payload.get("prices") or []
    volumes = payload.get("total_volumes") or []
    if not isinstance(prices, list):
        raise ValueError("CoinGecko market chart missing prices.")
    volume_by_time = {int(item[0]): float(item[1]) for item in volumes if isinstance(item, list) and len(item) >= 2}
    candles: list[Candle] = []
    for item in prices:
        if not isinstance(item, list) or len(item) < 2:
            continue
        timestamp = int(item[0])
        close = float(item[1])
        volume = volume_by_time.get(timestamp, 0.0)
        candles.append(Candle(timestamp, close, close, close, close, volume))
    return sorted(candles, key=lambda candle: candle.datetime_ms)


def normalize_crypto_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if not text:
        return ""
    if text.startswith("@"):
        return text
    text = text.replace("/USDC", "").replace("-PERP", "").replace("-SPOT", "")
    text = text.replace("-SHORT", "").replace("-LONG", "")
    if text.startswith("U") and len(text) > 2 and text[1:] in {"BTC", "ETH", "SOL", "HYPE", "ZEC"}:
        text = text[1:]
    return text.split("-")[0].strip()


def normalize_timeframe(timeframe: str) -> str:
    raw = str(timeframe or "1d").strip()
    if raw == "1M":
        return "1M"
    value = raw.lower()
    aliases = {"1mo": "1M", "1mon": "1M", "1month": "1M", "month": "1M"}
    value = aliases.get(value, value)
    return value if value in HYPERLIQUID_INTERVALS else "1d"


def _cache_key(symbol: str, timeframe: str) -> str:
    return f"{normalize_crypto_symbol(symbol)}:{normalize_timeframe(timeframe)}"


def load_cached_crypto_candles(symbol: str, *, timeframe: str = "1d") -> dict[str, Any] | None:
    try:
        payload = json.loads(CRYPTO_CANDLE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    clean = normalize_crypto_symbol(symbol)
    cached = payload.get(_cache_key(clean, timeframe)) or payload.get(clean)
    return cached if isinstance(cached, dict) else None


def save_cached_crypto_candles(symbol: str, result: CryptoCandleResult) -> None:
    CRYPTO_CANDLE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(CRYPTO_CANDLE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    payload[_cache_key(symbol, result.timeframe)] = {
        "cached_at": time.time(),
        "source": result.source,
        "fetched_at": result.fetched_at,
        "timeframe": result.timeframe,
        "candles": [
            {
                "datetime_ms": candle.datetime_ms,
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in result.candles
        ],
    }
    CRYPTO_CANDLE_CACHE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _result_from_cache(symbol: str, cached: dict[str, Any], *, status: str, timeframe: str = "1d") -> CryptoCandleResult:
    candles: list[Candle] = []
    for item in cached.get("candles") or []:
        if not isinstance(item, dict):
            continue
        try:
            candles.append(Candle(int(item["datetime_ms"]), float(item["open"]), float(item["high"]), float(item["low"]), float(item["close"]), float(item.get("volume") or 0.0)))
        except Exception:
            continue
    cached_timeframe = normalize_timeframe(str(cached.get("timeframe") or timeframe))
    return CryptoCandleResult(symbol, candles, str(cached.get("source") or "Crypto candle cache"), status, str(cached.get("fetched_at") or _now()), "Loaded cached candles.", cached_timeframe)


def _fetch_json(url: str, timeout_seconds: int) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "portfolio-risk-cockpit/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
