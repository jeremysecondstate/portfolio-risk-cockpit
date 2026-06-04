from __future__ import annotations

import unittest

from app.analytics.technical_analysis import (
    Candle,
    QuoteSnapshot,
    TechnicalTicket,
    TimeframeSpec,
    _classify_prc_read,
    average_true_range,
    build_prc_index_price,
    build_technical_command_center_report,
    build_timeframe_technical_snapshot,
    close_location_value,
    parse_quote_snapshot,
    rolling_vwap,
    spread_percent,
    support_resistance_zones,
    volume_pressure_score,
    vwap,
)


def _candles(
    count: int,
    *,
    start: float = 20.0,
    step: float = 0.10,
    volume: float = 1_000.0,
) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        open_price = price
        close = price + step
        high = max(open_price, close) + 0.20
        low = min(open_price, close) - 0.20
        rows.append(
            Candle(
                datetime_ms=index,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume + index * 10,
            )
        )
        price = close
    return rows


class TechnicalCommandCenterTests(unittest.TestCase):
    def test_atr_uses_true_range(self) -> None:
        rows = _candles(20, start=10.0, step=0.50)
        self.assertAlmostEqual(average_true_range(rows, 14), 0.90)

    def test_vwap_uses_typical_price_weighted_by_volume(self) -> None:
        rows = [
            Candle(1, 10.0, 12.0, 9.0, 11.0, 100.0),
            Candle(2, 11.0, 14.0, 10.0, 13.0, 300.0),
        ]
        expected = (((12.0 + 9.0 + 11.0) / 3) * 100 + ((14.0 + 10.0 + 13.0) / 3) * 300) / 400
        self.assertAlmostEqual(vwap(rows), expected)

    def test_rolling_vwap_returns_period_aligned_values(self) -> None:
        rows = [
            Candle(1, 10.0, 12.0, 9.0, 11.0, 100.0),
            Candle(2, 11.0, 14.0, 10.0, 13.0, 300.0),
        ]
        values = rolling_vwap(rows, 2)
        expected = (((12.0 + 9.0 + 11.0) / 3) * 100 + ((14.0 + 10.0 + 13.0) / 3) * 300) / 400
        self.assertIsNone(values[0])
        self.assertAlmostEqual(values[1], expected)

    def test_close_location_value_is_centered_between_negative_and_positive_one(self) -> None:
        self.assertAlmostEqual(close_location_value(Candle(1, 9.0, 12.0, 8.0, 12.0, 100.0)), 1.0)
        self.assertAlmostEqual(close_location_value(Candle(1, 9.0, 12.0, 8.0, 8.0, 100.0)), -1.0)
        self.assertAlmostEqual(close_location_value(Candle(1, 9.0, 12.0, 8.0, 10.0, 100.0)), 0.0)

    def test_volume_pressure_score_is_bounded(self) -> None:
        rows = _candles(30, start=10.0, step=0.20, volume=1_000)
        self.assertGreater(volume_pressure_score(rows), 0)
        self.assertLessEqual(volume_pressure_score(rows), 1.0)
        weak_rows = _candles(30, start=20.0, step=-0.20, volume=1_000)
        self.assertLess(volume_pressure_score(weak_rows), 0)
        self.assertGreaterEqual(volume_pressure_score(weak_rows), -1.0)

    def test_spread_percent_uses_sane_bid_ask(self) -> None:
        quote = QuoteSnapshot("RDW", bid=10.0, ask=10.10)
        self.assertAlmostEqual(spread_percent(quote), (0.10 / 10.05) * 100)
        self.assertIsNone(spread_percent(QuoteSnapshot("RDW", bid=10.0, ask=9.99)))

    def test_quote_parser_supports_nested_schwab_shapes(self) -> None:
        snapshot = parse_quote_snapshot(
            "RDW",
            {
                "RDW": {
                    "quote": {
                        "bidPrice": "10.00",
                        "askPrice": "10.10",
                        "lastPrice": "10.08",
                        "mark": "10.05",
                        "totalVolume": "12345",
                    }
                }
            },
        )
        self.assertEqual(snapshot.symbol, "RDW")
        self.assertEqual(snapshot.bid, 10.0)
        self.assertEqual(snapshot.ask, 10.10)
        self.assertEqual(snapshot.last, 10.08)
        self.assertEqual(snapshot.mark, 10.05)
        self.assertEqual(snapshot.total_volume, 12345.0)

    def test_support_resistance_zones_extract_nearby_levels(self) -> None:
        rows = _candles(90, start=20.0, step=0.03)
        rows[-8] = Candle(82, 22.0, 22.2, 21.0, 21.6, 1_500)
        rows[-4] = Candle(86, 22.4, 24.0, 22.2, 23.0, 1_500)
        support, resistance = support_resistance_zones(rows, atr_14=0.6)
        self.assertTrue(any(level.center <= rows[-1].close for level in support))
        self.assertTrue(any(level.center >= rows[-1].close for level in resistance))

    def test_relative_strength_and_scores_are_in_report(self) -> None:
        symbol = _candles(80, start=20.0, step=0.20)
        spy = _candles(80, start=400.0, step=0.50)
        report = build_technical_command_center_report(
            "RDW",
            {"daily_1y": symbol, "timing_5m": symbol[-60:]},
            benchmark_candles={"SPY": spy},
            ticket=TechnicalTicket(side="buy", quantity=10, entry_price=symbol[-1].close, stop_price=symbol[-1].close - 2.0, portfolio_value=100_000),
        )
        self.assertIn("Trend", report.scores)
        self.assertEqual(report.benchmark_reads[0].benchmark, "SPY")
        self.assertIsNotNone(report.benchmark_reads[0].spread_20)
        self.assertGreaterEqual(report.overall_score, 0)
        self.assertLessEqual(report.overall_score, 100)

    def test_prc_formula_works_without_quote(self) -> None:
        rows = _candles(80, start=20.0, step=0.12)
        prc = build_prc_index_price("RDW", "1y daily", rows, relative_strength_score=None)
        self.assertIsNotNone(prc.index_price)
        self.assertIn("Quote data unavailable", " ".join(prc.warnings))
        self.assertIn("synthetic internal indicator", " ".join(prc.explanation_lines))

    def test_prc_formula_uses_quote_and_relative_strength_inputs(self) -> None:
        rows = _candles(80, start=20.0, step=0.12)
        quote = QuoteSnapshot("RDW", bid=29.55, ask=29.60, last=29.58, mark=29.57, total_volume=100_000)
        prc = build_prc_index_price("RDW", "1y daily", rows, quote_snapshot=quote, relative_strength_score=0.75, benchmark_data_available=True)
        self.assertIsNotNone(prc.components)
        assert prc.components is not None
        self.assertGreater(prc.components.relative_strength_adjustment, 0)
        self.assertEqual(prc.confidence, "High")

    def test_prc_clamps_extreme_output(self) -> None:
        rows = [Candle(1, 100.0, 1000.0, 900.0, 100.0, 1_000.0)]
        prc = build_prc_index_price("RDW", "single", rows)
        self.assertIsNotNone(prc.index_price)
        assert prc.index_price is not None
        self.assertLessEqual(prc.index_price, 108.0)

    def test_prc_read_classification_variants(self) -> None:
        self.assertEqual(
            _classify_prc_read(index_distance_percent=0.20, index_slope=0.40, volume_pressure=0.50, atr_percent=1.0, vwap_distance_percent=0.2, range_state="normal"),
            "Accumulation / constructive",
        )
        self.assertEqual(
            _classify_prc_read(index_distance_percent=3.20, index_slope=0.40, volume_pressure=0.50, atr_percent=1.0, vwap_distance_percent=0.2, range_state="normal"),
            "Chasing / extended",
        )
        self.assertEqual(
            _classify_prc_read(index_distance_percent=-1.20, index_slope=-0.30, volume_pressure=-0.50, atr_percent=1.0, vwap_distance_percent=-0.2, range_state="normal"),
            "Distribution / weak",
        )
        self.assertEqual(
            _classify_prc_read(index_distance_percent=0.10, index_slope=0.0, volume_pressure=0.05, atr_percent=1.0, vwap_distance_percent=0.0, range_state="compressing"),
            "Compression / wait",
        )
        self.assertEqual(
            _classify_prc_read(index_distance_percent=-1.20, index_slope=0.30, volume_pressure=0.05, atr_percent=1.0, vwap_distance_percent=-0.2, range_state="normal"),
            "Pullback opportunity",
        )

    def test_not_enough_data_returns_none_indicators_without_crashing(self) -> None:
        spec = TimeframeSpec("tiny", "tiny", "day", 1, "minute", 5, "timing")
        snapshot = build_timeframe_technical_snapshot("RDW", spec, _candles(5))
        self.assertEqual(snapshot.candle_count, 5)
        self.assertIsNone(snapshot.sma_20)
        self.assertIsNone(snapshot.atr_14)
        self.assertIn("Trend", snapshot.scores)

    def test_missing_timeframe_becomes_warning_not_failure(self) -> None:
        report = build_technical_command_center_report("RDW", {"daily_1y": []}, warnings=["5m fetch failed"])
        self.assertIn("5m fetch failed", report.warnings)
        self.assertEqual(report.confidence, "Low")
        self.assertEqual(report.snapshots["daily_1y"].candle_count, 0)

    def test_missing_quote_and_benchmark_data_do_not_fail_prc(self) -> None:
        rows = _candles(40, start=20.0, step=0.05)
        report = build_technical_command_center_report("RDW", {"daily_1y": rows})
        self.assertIn("daily_1y", report.prc_indexes)
        self.assertIsNotNone(report.prc_indexes["daily_1y"].index_price)
        self.assertTrue(any("Benchmark relative-strength data unavailable" in warning for warning in report.warnings))


if __name__ == "__main__":
    unittest.main()
