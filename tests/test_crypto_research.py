from __future__ import annotations

import math
import os
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
from app.analytics.crypto_sentiment import build_crypto_sentiment_snapshot
from app.analytics.hyperliquid_market_data import (
    MarketDataSourceStatus,
    analyze_order_book,
    analyze_perp_structure,
    build_multi_timeframe_crypto_snapshot,
    funding_carry_cost,
    parse_hyperliquid_candles,
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

    def test_parse_hyperliquid_candle_snapshot(self) -> None:
        payload = [
            {"t": 1_700_000_060_000, "o": "101", "h": "104", "l": "100", "c": "103", "v": "7.5"},
            {"t": 1_700_000_000_000, "o": "100", "h": "102", "l": "99", "c": "101", "v": "5"},
        ]

        candles = parse_hyperliquid_candles(payload)

        self.assertEqual(len(candles), 2)
        self.assertLess(candles[0].datetime_ms, candles[1].datetime_ms)
        self.assertAlmostEqual(candles[-1].close, 103.0)
        self.assertAlmostEqual(candles[-1].volume, 7.5)

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
        self.assertEqual(normalize_timeframe("1M"), "1M")
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

    def test_multi_timeframe_aggregation_scores_alignment(self) -> None:
        status = MarketDataSourceStatus("Hyperliquid", "candleSnapshot", "fresh", "now", "1h", 260, "ok")
        snapshot = build_multi_timeframe_crypto_snapshot(
            "BTC",
            {
                "5m": (_sample_crypto_candles(), status),
                "1h": (_sample_crypto_candles(), status),
                "1d": (_sample_crypto_candles(), status),
            },
        )

        self.assertEqual(snapshot.symbol, "BTC")
        self.assertEqual(len(snapshot.timeframe_reads), 3)
        self.assertGreater(snapshot.alignment.source_confidence_score, 0)
        self.assertIn(snapshot.alignment.label, {"Bullish Stack", "Leaning Bullish", "Mixed"})

    def test_liquidity_depth_and_slippage_calculations(self) -> None:
        payload = {
            "levels": [
                [{"px": "99.9", "sz": "10"}, {"px": "99.5", "sz": "20"}],
                [{"px": "100.1", "sz": "12"}, {"px": "100.5", "sz": "18"}],
            ]
        }

        liquidity = analyze_order_book("BTC", payload, order_sizes_usd=(1_000.0,))

        self.assertAlmostEqual(liquidity.mid_price or 0, 100.0)
        self.assertAlmostEqual(liquidity.spread_bps or 0, 20.0)
        self.assertEqual(liquidity.depth_buckets[-1].bps, 100)
        self.assertTrue(liquidity.slippage)

    def test_perp_structure_and_funding_carry(self) -> None:
        meta_payload = [
            {"universe": [{"name": "BTC", "maxLeverage": 50}]},
            [{"markPx": "100", "oraclePx": "99", "midPx": "100.1", "funding": "0.0002", "openInterest": "1000", "dayNtlVlm": "5000000"}],
        ]

        perp = analyze_perp_structure(
            "BTC",
            meta_payload=meta_payload,
            predicted_payload=["BTC", [["HlPerp", "0.0003"]]],
            funding_history_payload=[{"fundingRate": "0.0001"}, {"fundingRate": "0.0002"}, {"fundingRate": "0.00025"}],
            oi_cap_payload=["ETH"],
            perp_notional=10_000.0,
            perp_direction="long",
        )

        self.assertTrue(perp.is_perp_enabled)
        self.assertAlmostEqual(perp.current_funding or 0, 0.0002)
        self.assertAlmostEqual(perp.carry_cost_8h or 0, 2.0)
        self.assertEqual(funding_carry_cost(10_000.0, "short", 0.0002), -2.0)

    def test_sentiment_fallback_is_not_configured(self) -> None:
        with patch.dict(os.environ, {"CRYPTO_NEWS_HEADLINES": ""}):
            sentiment = build_crypto_sentiment_snapshot("BTC")

        self.assertEqual(sentiment.label, "Not Configured")
        self.assertEqual(sentiment.status, "not configured")
        self.assertTrue(sentiment.narratives)

    def test_decision_uses_crypto_native_labels_and_components(self) -> None:
        indicators = calculate_advanced_indicators("BTC", _sample_crypto_candles())
        exposure = build_crypto_exposure(_hyperliquid_portfolio(), "BTC")
        candle_result = CryptoCandleResult("BTC", _sample_crypto_candles(), "Unit provider", "fresh", "now")
        status = MarketDataSourceStatus("Hyperliquid", "candleSnapshot", "fresh", "now", "1h", 260, "ok")
        multi = build_multi_timeframe_crypto_snapshot("BTC", {"1h": (_sample_crypto_candles(), status), "1d": (_sample_crypto_candles(), status)})
        liquidity = analyze_order_book(
            "BTC",
            {"levels": [[{"px": "99.9", "sz": "100"}], [{"px": "100.1", "sz": "100"}]]},
            order_sizes_usd=(1_000.0,),
        )
        perp = analyze_perp_structure(
            "BTC",
            meta_payload=[{"universe": [{"name": "BTC", "maxLeverage": 50}]}, [{"funding": "0.00001"}]],
            perp_notional=exposure.perp_notional,
            perp_direction=exposure.perp_direction,
        )
        sentiment = build_crypto_sentiment_snapshot("BTC", headlines=["Bitcoin adoption headline signals strong inflow"])

        decision = build_crypto_decision(
            indicators=indicators,
            exposure=exposure,
            candle_result=candle_result,
            multi_timeframe=multi,
            liquidity=liquidity,
            perp_structure=perp,
            sentiment=sentiment,
        )

        self.assertEqual(decision.funding_bias.title, "Perp Carry")
        self.assertEqual(decision.action_bias.title, "Operator Action")
        self.assertEqual(decision.sentiment.title, "Social / News")
        self.assertEqual(decision.liquidity.title, "Liquidity")
        self.assertIn("Trend", decision.score_components)
        self.assertTrue(decision.why_bullets)

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
