from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.analytics.crypto_market_data import (
    CryptoCandleResult,
    fetch_crypto_candles,
    fetch_crypto_candles_with_fallback,
    normalize_timeframe,
    normalize_crypto_symbol,
    parse_coinbase_candles,
    parse_coingecko_market_chart,
)
from app.analytics.crypto_research import build_crypto_decision, build_crypto_exposure, build_crypto_scenarios
from app.analytics.stock_research import calculate_advanced_indicators
from app.analytics.technical_analysis import Candle
from app.core.portfolio import CashPosition, Portfolio, Position
from app.ui.hyperliquid_research_workspace_extension import build_crypto_provider_status_rows, hyperliquid_cash_display_rows


def _sample_crypto_candles(count: int = 260) -> list[Candle]:
    candles: list[Candle] = []
    for index in range(count):
        close = 100.0 + index * 0.35 + math.sin(index / 7) * 3.0
        candles.append(
            Candle(
                datetime_ms=1_700_000_000_000 + index * 86_400_000,
                open=close - 0.75,
                high=close + 1.8,
                low=close - 1.5,
                close=close,
                volume=2_000_000 + index * 2_500,
            )
        )
    return candles


def _hyperliquid_portfolio() -> Portfolio:
    btc_spot = Position("BTC", 0.10, 70_000.0, 75_000.0, open_profit_loss=500.0)
    btc_spot.asset_type = "Spot"  # type: ignore[attr-defined]
    btc_perp = Position("BTC-PERP-SHORT", 0.05, 74_000.0, 75_000.0, open_profit_loss=-50.0)
    btc_perp.asset_type = "Perp Short"  # type: ignore[attr-defined]
    return Portfolio(
        cash=10_000.0,
        positions={"BTC": btc_spot, "BTC-PERP-SHORT": btc_perp},
        cash_positions={"USDC_HL": CashPosition("USDC", 1_000.0, "Hyperliquid")},
    )


class CryptoMarketDataTests(unittest.TestCase):
    def test_parse_coinbase_candles(self) -> None:
        payload = [
            [1_700_086_400, 99.0, 106.0, 101.0, 105.0, 12.5],
            [1_700_000_000, 95.0, 102.0, 100.0, 101.0, 10.0],
        ]

        candles = parse_coinbase_candles(payload)

        self.assertEqual(len(candles), 2)
        self.assertLess(candles[0].datetime_ms, candles[1].datetime_ms)
        self.assertAlmostEqual(candles[-1].close, 105.0)
        self.assertAlmostEqual(candles[-1].volume, 12.5)

    def test_parse_coingecko_market_chart(self) -> None:
        payload = {
            "prices": [[1_700_000_000_000, 100.0], [1_700_086_400_000, 101.5]],
            "total_volumes": [[1_700_000_000_000, 1_000_000.0], [1_700_086_400_000, 2_000_000.0]],
        }

        candles = parse_coingecko_market_chart(payload)

        self.assertEqual(len(candles), 2)
        self.assertAlmostEqual(candles[1].close, 101.5)
        self.assertAlmostEqual(candles[1].volume, 2_000_000.0)

    def test_provider_fallback_uses_second_provider(self) -> None:
        def failing_provider(symbol: str, days: int, timeout_seconds: int, timeframe: str) -> CryptoCandleResult:
            raise RuntimeError("offline")

        def working_provider(symbol: str, days: int, timeout_seconds: int, timeframe: str) -> CryptoCandleResult:
            return CryptoCandleResult(symbol, _sample_crypto_candles(3), "Unit provider", "fresh", "now", timeframe=timeframe)

        result = fetch_crypto_candles_with_fallback("btc", [failing_provider, working_provider], timeframe="4h")

        self.assertEqual(result.source, "Unit provider")
        self.assertEqual(len(result.candles), 3)
        self.assertEqual(result.timeframe, "4h")

    def test_cache_fallback_when_provider_fails(self) -> None:
        cached_result = CryptoCandleResult("BTC", _sample_crypto_candles(4), "Unit cache", "fresh", "cached")
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_path = Path(tmpdir) / "crypto_cache.json"
            with patch("app.analytics.crypto_market_data.CRYPTO_CANDLE_CACHE_PATH", cache_path):
                from app.analytics.crypto_market_data import save_cached_crypto_candles

                save_cached_crypto_candles("BTC", cached_result)
                with patch(
                    "app.analytics.crypto_market_data.fetch_crypto_candles_with_fallback",
                    return_value=CryptoCandleResult("BTC", [], "All providers", "error", "now", "down"),
                ):
                    result = fetch_crypto_candles("BTC")

        self.assertEqual(result.status, "fresh/cache")
        self.assertEqual(len(result.candles), 4)

    def test_symbol_normalization_handles_perp_and_ubtc(self) -> None:
        self.assertEqual(normalize_crypto_symbol("BTC-PERP-SHORT"), "BTC")
        self.assertEqual(normalize_crypto_symbol("UBTC"), "BTC")
        self.assertEqual(normalize_crypto_symbol("hype/usdc"), "HYPE")

    def test_timeframe_normalization(self) -> None:
        self.assertEqual(normalize_timeframe("15M"), "15m")
        self.assertEqual(normalize_timeframe("4h"), "4h")
        self.assertEqual(normalize_timeframe("bad"), "1d")

    def test_provider_status_rows_include_timeframe_and_active_provider(self) -> None:
        result = CryptoCandleResult("BTC", _sample_crypto_candles(5), "Coinbase public candles", "fresh", "now", "BTC-USD 4h candles.", "4h")

        rows = build_crypto_provider_status_rows(result)

        active = rows[0]
        self.assertEqual(active[0], "Coinbase public candles")
        self.assertEqual(active[1], "Fresh")
        self.assertEqual(active[2], "4h")
        self.assertEqual(active[4], "5")

    def test_hyperliquid_cash_rows_are_labeled(self) -> None:
        cash_positions = {
            "spot": SimpleNamespace(display_symbol="USDC", symbol="USDC", source="Hyperliquid", amount=8505.76, market_value=8505.76),
            "margin": SimpleNamespace(display_symbol="USDC", symbol="USDC", source="Hyperliquid", amount=-8895.47, market_value=-8895.47),
        }

        rows = hyperliquid_cash_display_rows(cash_positions)
        labels = [row[1] for row in rows]

        self.assertIn("Spot USDC", labels)
        self.assertIn("Perp USDC / margin adj", labels)
        self.assertIn("Net Hyperliquid USDC", labels)


class CryptoResearchAnalyticsTests(unittest.TestCase):
    def test_crypto_indicators_use_stock_indicator_stack(self) -> None:
        indicators = calculate_advanced_indicators("BTC", _sample_crypto_candles())

        self.assertEqual(indicators.symbol, "BTC")
        self.assertIsNotNone(indicators.sma_20)
        self.assertIsNotNone(indicators.macd)
        self.assertIsNotNone(indicators.rsi_14)
        self.assertIn(indicators.trend, {"bullish", "bearish", "sideways"})

    def test_spot_perp_exposure_and_hedge_ratio(self) -> None:
        exposure = build_crypto_exposure(_hyperliquid_portfolio(), "UBTC")

        self.assertAlmostEqual(exposure.spot_value, 7_500.0)
        self.assertAlmostEqual(exposure.perp_notional, 3_750.0)
        self.assertEqual(exposure.perp_direction, "short")
        self.assertAlmostEqual(exposure.net_exposure, 3_750.0)
        self.assertAlmostEqual(exposure.hedge_ratio or 0.0, 0.5)

    def test_scenario_math_for_spot_plus_short_perp(self) -> None:
        exposure = build_crypto_exposure(_hyperliquid_portfolio(), "BTC")
        rows = build_crypto_scenarios(exposure, 75_000.0, moves=(-0.10, 0.10))

        self.assertAlmostEqual(rows[0].spot_pnl, -750.0)
        self.assertAlmostEqual(rows[0].perp_pnl, 375.0)
        self.assertAlmostEqual(rows[0].net_pnl, -375.0)
        self.assertAlmostEqual(rows[1].spot_pnl, 750.0)
        self.assertAlmostEqual(rows[1].perp_pnl, -375.0)
        self.assertAlmostEqual(rows[1].net_pnl, 375.0)

    def test_decision_handles_missing_candles_without_crashing(self) -> None:
        exposure = build_crypto_exposure(_hyperliquid_portfolio(), "BTC")
        indicators = calculate_advanced_indicators("BTC", [])
        candle_result = CryptoCandleResult("BTC", [], "Unit provider", "error", "now", "no candles")

        decision = build_crypto_decision(indicators=indicators, exposure=exposure, candle_result=candle_result)

        self.assertEqual(decision.trend.label, "Unknown")
        self.assertTrue(decision.summary)
        self.assertEqual(len(decision.top_things), 3)
        self.assertIn("Current setup", decision.operator_view)


if __name__ == "__main__":
    unittest.main()
