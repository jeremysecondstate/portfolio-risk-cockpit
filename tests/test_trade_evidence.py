from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.analytics.stock_research import AdvancedIndicatorSnapshot
from app.analytics.technical_analysis import Candle, TimeframeSpec, build_timeframe_technical_snapshot
from app.analytics.trade_evidence import multi_timeframe_layer


def _market_ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    eastern = timezone(timedelta(hours=-4), "EDT")
    return int(datetime(year, month, day, hour, minute, tzinfo=eastern).timestamp() * 1000)


def _session_candles(year: int, month: int, day: int, *, count: int, start: float, step: float) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        minute_offset = index * 5
        hour = 9 + (30 + minute_offset) // 60
        minute = (30 + minute_offset) % 60
        close = price + step
        rows.append(
            Candle(
                datetime_ms=_market_ms(year, month, day, hour, minute),
                open=price,
                high=max(price, close) + 0.20,
                low=min(price, close) - 0.20,
                close=close,
                volume=1_000 + index * 10,
            )
        )
        price = close
    return rows


def _indicators(price: float = 50.0) -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        symbol="RDW",
        latest_close=price,
        sma_20=price,
        sma_50=price,
        sma_100=price,
        sma_200=price,
        ema_12=price,
        ema_26=price,
        macd=0.1,
        macd_signal=0.05,
        macd_histogram=0.05,
        rsi_14=55.0,
        bollinger_upper=price * 1.1,
        bollinger_middle=price,
        bollinger_lower=price * 0.9,
        atr_14=0.5,
        volume_average_20=100_000,
        week_52_high=price * 1.5,
        week_52_low=price * 0.5,
        swing_high=price * 1.2,
        swing_low=price * 0.8,
        fibonacci_levels={},
        trend="bullish",
        volatility="normal",
        momentum="improving",
        support=price * 0.9,
        resistance=price * 1.2,
        notes=[],
    )


class TradeEvidenceTests(unittest.TestCase):
    def test_multi_timeframe_layer_uses_command_center_intraday_snapshot(self) -> None:
        daily = [
            Candle(index, 45.0 + index * 0.05, 45.5 + index * 0.05, 44.8 + index * 0.05, 45.2 + index * 0.05, 1_000)
            for index in range(90)
        ]
        intraday = [
            *_session_candles(2026, 6, 3, count=20, start=48.0, step=0.02),
            *_session_candles(2026, 6, 4, count=40, start=50.0, step=0.04),
        ]
        spec = TimeframeSpec("timing_5m", "10d 5m", "day", 10, "minute", 5, "timing")
        snapshot = build_timeframe_technical_snapshot("RDW", spec, intraday)

        layer = multi_timeframe_layer(
            _indicators(price=daily[-1].close),
            daily,
            command_center_snapshots={"timing_5m": snapshot},
        )

        self.assertTrue(any("Intraday timing" in line for line in layer.lines))
        self.assertFalse(any("Intraday 5m/15m technical context" in item for item in layer.missing))
        self.assertIsNotNone(layer.score)


if __name__ == "__main__":
    unittest.main()
