from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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


Provider = Callable[[str, int, int], CryptoCandleResult]


def fetch_crypto_candles(symbol: str, *, days: int = 365, timeout_seconds: int = 8) -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    cached = load_cached_crypto_candles(clean)
    if cached and time.time() - cached.get("cached_at", 0) <= CRYPTO_CANDLE_CACHE_TTL_SECONDS:
        return _result_from_cache(clean, cached, status="fresh/cache")

    providers: list[Provider] = [fetch_coinbase_candles, fetch_kraken_candles, fetch_coingecko_candles]
    result = fetch_crypto_candles_with_fallback(clean, providers, days=days, timeout_seconds=timeout_seconds)
    if result.candles:
        save_cached_crypto_candles(clean, result)
        return result
    if cached:
        cached_result = _result_from_cache(clean, cached, status="stale")
        return CryptoCandleResult(clean, cached_result.candles, cached_result.source, "stale", _now(), f"{result.message}; using stale cached candles.")
    return result


def fetch_crypto_candles_with_fallback(
    symbol: str,
    providers: list[Provider],
    *,
    days: int = 365,
    timeout_seconds: int = 8,
) -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    errors: list[str] = []
    for provider in providers:
        try:
            result = provider(clean, days, timeout_seconds)
        except Exception as exc:
            errors.append(f"{provider.__name__}: {exc}")
            continue
        if result.candles:
            return result
        errors.append(f"{result.source}: {result.message or result.status}")
    return CryptoCandleResult(clean, [], "Crypto candles", "error", _now(), "; ".join(errors) or "No candle provider returned data.")


def fetch_coinbase_candles(symbol: str, days: int, timeout_seconds: int) -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    product = f"{clean}-USD"
    granularity = 86_400
    url = f"https://api.exchange.coinbase.com/products/{urllib.parse.quote(product)}/candles?granularity={granularity}"
    payload = _fetch_json(url, timeout_seconds)
    candles = parse_coinbase_candles(payload)
    return CryptoCandleResult(clean, candles[-days:], "Coinbase public candles", "fresh", _now(), f"{product} daily candles.")


def fetch_kraken_candles(symbol: str, days: int, timeout_seconds: int) -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    pair = "XBTUSD" if clean == "BTC" else f"{clean}USD"
    url = f"https://api.kraken.com/0/public/OHLC?pair={urllib.parse.quote(pair)}&interval=1440"
    payload = _fetch_json(url, timeout_seconds)
    candles = parse_kraken_ohlc(payload)
    return CryptoCandleResult(clean, candles[-days:], "Kraken public OHLC", "fresh", _now(), f"{pair} daily candles.")


def fetch_coingecko_candles(symbol: str, days: int, timeout_seconds: int) -> CryptoCandleResult:
    clean = normalize_crypto_symbol(symbol)
    coin_id = COINGECKO_IDS.get(clean)
    if not coin_id:
        raise ValueError(f"No CoinGecko id mapping for {clean}.")
    url = f"https://api.coingecko.com/api/v3/coins/{urllib.parse.quote(coin_id)}/market_chart?vs_currency=usd&days={days}&interval=daily"
    payload = _fetch_json(url, timeout_seconds)
    candles = parse_coingecko_market_chart(payload)
    return CryptoCandleResult(clean, candles[-days:], "CoinGecko market chart", "fresh", _now(), f"{coin_id} daily market chart.")


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


def load_cached_crypto_candles(symbol: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(CRYPTO_CANDLE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    cached = payload.get(normalize_crypto_symbol(symbol))
    return cached if isinstance(cached, dict) else None


def save_cached_crypto_candles(symbol: str, result: CryptoCandleResult) -> None:
    CRYPTO_CANDLE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        payload = json.loads(CRYPTO_CANDLE_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    payload[normalize_crypto_symbol(symbol)] = {
        "cached_at": time.time(),
        "source": result.source,
        "fetched_at": result.fetched_at,
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


def _result_from_cache(symbol: str, cached: dict[str, Any], *, status: str) -> CryptoCandleResult:
    candles: list[Candle] = []
    for item in cached.get("candles") or []:
        if not isinstance(item, dict):
            continue
        try:
            candles.append(Candle(int(item["datetime_ms"]), float(item["open"]), float(item["high"]), float(item["low"]), float(item["close"]), float(item.get("volume") or 0.0)))
        except Exception:
            continue
    return CryptoCandleResult(symbol, candles, str(cached.get("source") or "Crypto candle cache"), status, str(cached.get("fetched_at") or _now()), "Loaded cached candles.")


def _fetch_json(url: str, timeout_seconds: int) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": "portfolio-risk-cockpit/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
