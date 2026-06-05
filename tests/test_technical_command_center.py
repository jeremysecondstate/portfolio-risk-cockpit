from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.analytics.technical_analysis import (
    Candle,
    QuoteSnapshot,
    TechnicalTicket,
    TimeframeSpec,
    _classify_prc_read,
    average_true_range,
    build_prc_index_price,
    build_level_proximity_read,
    build_technical_command_center_report,
    build_timeframe_technical_snapshot,
    close_location_value,
    format_technical_command_center_report,
    parse_quote_snapshot,
    rolling_vwap,
    spread_percent,
    support_resistance_zones,
    volume_pressure_score,
    vwap,
)


def _market_ms(year: int, month: int, day: int, hour: int, minute: int) -> int:
    eastern = timezone(timedelta(hours=-4), "EDT")
    return int(datetime(year, month, day, hour, minute, tzinfo=eastern).timestamp() * 1000)


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


def _session_candles(
    year: int,
    month: int,
    day: int,
    *,
    count: int,
    start: float,
    step: float,
    volume: float = 1_000.0,
) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        minute_offset = index * 5
        hour = 9 + (30 + minute_offset) // 60
        minute = (30 + minute_offset) % 60
        open_price = price
        close = price + step
        rows.append(
            Candle(
                datetime_ms=_market_ms(year, month, day, hour, minute),
                open=open_price,
                high=max(open_price, close) + 0.25,
                low=min(open_price, close) - 0.25,
                close=close,
                volume=volume + index * 10,
            )
        )
        price = close
    return rows


def _trend_candles(count: int = 140, *, start: float = 80.0, step: float = 0.15) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        step_value = step if index % 7 else -step * 0.5
        open_price = price
        close = price + step_value
        rows.append(Candle(index, open_price, max(open_price, close) + 0.35, min(open_price, close) - 0.35, close, 1_000 + index * 3))
        price = close
    return rows


def _downtrend_candles(count: int = 140, *, start: float = 120.0, step: float = -0.16) -> list[Candle]:
    rows: list[Candle] = []
    price = start
    for index in range(count):
        step_value = step if index % 7 else abs(step) * 0.4
        open_price = price
        close = price + step_value
        rows.append(Candle(index, open_price, max(open_price, close) + 0.35, min(open_price, close) - 0.35, close, 1_000 + index * 3))
        price = close
    return rows


def _range_breakout_candles(count: int = 100, *, base: float = 100.0, breakout: float = 110.0) -> list[Candle]:
    rows: list[Candle] = []
    for index in range(count - 5):
        close = base + ((index % 10) / 10) * 4
        open_price = close - 0.2 if index % 2 else close + 0.2
        rows.append(Candle(index, open_price, max(open_price, close) + 0.5, min(open_price, close) - 0.5, close, 1_000 + (index % 5) * 20))
    price = base + 4
    for offset in range(5):
        index = count - 5 + offset
        open_price = price
        close = price + (breakout - price) / (5 - offset)
        rows.append(Candle(index, open_price, max(open_price, close) + 0.2, min(open_price, close) - 0.2, close, 2_500 + offset * 300))
        price = close
    return rows


def _range_breakdown_candles(count: int = 100, *, base: float = 100.0, breakdown: float = 94.0) -> list[Candle]:
    rows: list[Candle] = []
    for index in range(count - 6):
        close = base + ((index % 10) / 10) * 4
        open_price = close + 0.2 if index % 2 else close - 0.2
        rows.append(Candle(index, open_price, max(open_price, close) + 0.5, min(open_price, close) - 0.5, close, 1_000 + (index % 4) * 20))
    price = base
    for offset in range(6):
        index = count - 6 + offset
        open_price = price
        close = price + (breakdown - price) / (6 - offset)
        rows.append(Candle(index, open_price, max(open_price, close) + 0.2, min(open_price, close) - 0.2, close, 2_600 + offset * 400))
        price = close
    return rows


def _pullback_to_support_candles(count: int = 100) -> list[Candle]:
    rows: list[Candle] = []
    for index in range(count - 12):
        close = 101 + (index % 12) * 0.75
        open_price = close + (0.2 if index % 2 == 0 else -0.2)
        rows.append(Candle(index, open_price, max(open_price, close) + 0.45, min(open_price, close) - 0.45, close, 1_000 + (index % 4) * 25))
    for offset in range(12):
        index = count - 12 + offset
        close = 109 - offset * 0.6
        if offset == 11:
            close = 102.4
        open_price = close + 0.5
        rows.append(Candle(index, open_price, max(open_price, close) + 0.25, min(open_price, close) - 0.25, close, 900 - offset * 10))
    return rows


def _washed_support_hold_candles(count: int = 110) -> list[Candle]:
    rows: list[Candle] = []
    for index in range(count - 20):
        close = 101 + (index % 16) * 0.85
        open_price = close + (0.2 if index % 2 == 0 else -0.2)
        rows.append(Candle(index, open_price, max(open_price, close) + 0.45, min(open_price, close) - 0.45, close, 1_000 + (index % 4) * 25))
    closes = [113, 112, 111, 110, 109, 108, 107, 106, 105, 104, 103, 102, 101.3, 100.95, 100.75, 100.7, 100.72, 100.76, 100.82, 100.9]
    for offset, close in enumerate(closes):
        index = count - 20 + offset
        open_price = close + 0.65 if offset < 16 else close - 0.25
        volume = 900 if offset < 16 else 2_500 + offset * 100
        rows.append(Candle(index, open_price, max(open_price, close) + 0.20, min(open_price, close) - 0.20, close, volume))
    return rows


def _chop_candles(count: int = 91, *, base: float = 100.0, amplitude: float = 2.0) -> list[Candle]:
    rows: list[Candle] = []
    for index in range(count):
        close = base + ((index % 20) - 10) / 10 * amplitude
        open_price = close + (0.15 if index % 2 else -0.15)
        rows.append(Candle(index, open_price, max(open_price, close) + 0.4, min(open_price, close) - 0.4, close, 1_000 + (index % 3) * 10))
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

    def test_intraday_gap_uses_latest_regular_session(self) -> None:
        prior_session = _session_candles(2026, 6, 3, count=78, start=95.0, step=5.0 / 78)
        prior_session[-1] = Candle(
            prior_session[-1].datetime_ms,
            prior_session[-1].open,
            prior_session[-1].high,
            prior_session[-1].low,
            100.0,
            prior_session[-1].volume,
        )
        current_session = _session_candles(2026, 6, 4, count=12, start=105.0, step=0.10)
        spec = TimeframeSpec("timing_5m", "10d 5m", "day", 10, "minute", 5, "timing")

        snapshot = build_timeframe_technical_snapshot("RDW", spec, [*prior_session, *current_session])

        self.assertIn("Regular-session gap up +5.00%", snapshot.gap_read)
        opening = current_session[:6]
        self.assertAlmostEqual(snapshot.opening_range_high or 0, max(candle.high for candle in opening))
        self.assertAlmostEqual(snapshot.opening_range_low or 0, min(candle.low for candle in opening))

    def test_minute_snapshot_exposes_session_rolling_and_multi_day_vwap(self) -> None:
        old_session = _session_candles(2026, 6, 3, count=20, start=10.0, step=0.01, volume=10_000)
        current_session = _session_candles(2026, 6, 4, count=30, start=50.0, step=0.05, volume=1_000)
        spec = TimeframeSpec("timing_5m", "10d 5m", "day", 10, "minute", 5, "timing")

        snapshot = build_timeframe_technical_snapshot("RDW", spec, [*old_session, *current_session])

        self.assertIsNotNone(snapshot.session_vwap)
        self.assertIsNotNone(snapshot.rolling_vwap_20)
        self.assertIsNotNone(snapshot.multi_day_vwap)
        self.assertEqual(snapshot.vwap, snapshot.session_vwap)
        assert snapshot.session_vwap is not None
        assert snapshot.multi_day_vwap is not None
        self.assertGreater(snapshot.session_vwap, snapshot.multi_day_vwap)
        self.assertTrue(any("session" in line and "multi-day" in line for line in snapshot.lines))

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

    def test_command_center_passed_quote_avoids_missing_quote_prc_warning(self) -> None:
        rows = _candles(80, start=20.0, step=0.12)
        quote = QuoteSnapshot("RDW", bid=29.55, ask=29.60, last=29.58, mark=29.57, total_volume=100_000)

        report = build_technical_command_center_report("RDW", {"daily_1y": rows}, quote_snapshot=quote)
        warnings = " ".join(warning for prc in report.prc_indexes.values() for warning in prc.warnings)

        self.assertNotIn("Quote data unavailable", warnings)
        self.assertNotIn("Quote bid/ask unavailable", warnings)

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

    def test_report_labels_pressure_line_and_vwap_anchors(self) -> None:
        daily = _candles(80, start=20.0, step=0.12)
        intraday = [
            *_session_candles(2026, 6, 3, count=20, start=10.0, step=0.01, volume=10_000),
            *_session_candles(2026, 6, 4, count=30, start=50.0, step=0.05, volume=1_000),
        ]
        quote = QuoteSnapshot("RDW", bid=29.55, ask=29.60, last=29.58, mark=29.57)

        report = build_technical_command_center_report(
            "RDW",
            {"daily_1y": daily, "timing_5m": intraday},
            quote_snapshot=quote,
        )
        text = format_technical_command_center_report(report)

        self.assertIn("PRC PRESSURE LINE", text)
        self.assertNotIn("PRC INDEX PRICE", text)
        self.assertIn("session VWAP", text)
        self.assertIn("multi-day VWAP", text)

    def test_setup_classifies_clean_bullish_breakout(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {
                "daily_1y": _trend_candles(),
                "setup_30m": _range_breakout_candles(),
                "timing_5m": _range_breakout_candles(90, base=105.0, breakout=112.0),
            },
            ticket=TechnicalTicket(side="buy", quantity=10, entry_price=112.0, stop_price=108.0, portfolio_value=100_000),
        )

        classification = report.setup_classification

        self.assertEqual(classification.regime, "bullish")
        self.assertEqual(classification.setup, "breakout")
        self.assertEqual(classification.timing, "confirmed")
        self.assertEqual(classification.action_quality, "good_entry")
        self.assertIsNotNone(classification.confirmation_level)

    def test_setup_classifies_bullish_daily_pullback_with_weak_intraday_timing(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {
                "daily_1y": _trend_candles(),
                "setup_30m": _pullback_to_support_candles(),
                "timing_5m": _pullback_to_support_candles(80),
            },
            ticket=TechnicalTicket(side="buy", quantity=10, entry_price=102.5, stop_price=100.8, portfolio_value=100_000),
        )

        classification = report.setup_classification

        self.assertEqual(classification.regime, "bullish")
        self.assertEqual(classification.setup, "pullback")
        self.assertEqual(classification.timing, "early")
        self.assertEqual(classification.action_quality, "wait_for_trigger")
        self.assertEqual(report.ticket_check.entry_quality, "entry near support")

    def test_setup_classifies_bearish_breakdown_below_support(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {
                "daily_1y": _downtrend_candles(),
                "setup_30m": _range_breakdown_candles(),
                "timing_5m": _range_breakdown_candles(80, base=98.0, breakdown=92.0),
            },
            ticket=TechnicalTicket(side="buy", quantity=10, entry_price=94.0, stop_price=91.0, portfolio_value=100_000),
        )

        classification = report.setup_classification

        self.assertEqual(classification.regime, "bearish")
        self.assertEqual(classification.setup, "breakdown")
        self.assertEqual(classification.timing, "failed")
        self.assertEqual(classification.action_quality, "protect_or_trim")
        self.assertTrue(any("Support broke" in warning for warning in classification.warnings))

    def test_setup_classifies_range_chop_no_edge(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {
                "daily_1y": _chop_candles(111),
                "setup_30m": _chop_candles(),
                "timing_5m": _chop_candles(),
            },
            ticket=TechnicalTicket(side="buy", quantity=10, entry_price=100.0, stop_price=98.0, portfolio_value=100_000),
        )

        classification = report.setup_classification

        self.assertEqual(classification.regime, "range")
        self.assertEqual(classification.setup, "chop")
        self.assertEqual(classification.action_quality, "no_edge")
        self.assertIsNotNone(classification.level_proximity)
        assert classification.level_proximity is not None
        self.assertEqual(classification.level_proximity.range_position, "middle")

    def test_oversold_with_support_broken_stays_breakdown(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {
                "daily_1y": _downtrend_candles(),
                "setup_30m": _range_breakdown_candles(),
                "timing_5m": _range_breakdown_candles(80, base=98.0, breakdown=92.0),
            },
        )

        classification = report.setup_classification

        self.assertEqual(classification.setup, "breakdown")
        self.assertEqual(classification.timing, "failed")
        self.assertTrue(any(read.rsi is not None and read.rsi < 25 for read in classification.rsi_context))
        self.assertTrue(any("oversold RSI" in warning or "RSI below 25" in warning for warning in classification.warnings))

    def test_oversold_support_holding_with_improving_volume_is_constructive_pullback(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {
                "daily_1y": _trend_candles(),
                "setup_30m": _washed_support_hold_candles(),
                "timing_5m": _washed_support_hold_candles(90),
            },
            ticket=TechnicalTicket(side="buy", quantity=10, entry_price=100.9, stop_price=100.0, portfolio_value=100_000),
        )

        classification = report.setup_classification

        self.assertEqual(classification.setup, "pullback")
        self.assertEqual(classification.timing, "confirmed")
        self.assertEqual(classification.action_quality, "good_entry")
        self.assertTrue(any(read.zone == "washed_out" for read in classification.rsi_context))
        self.assertIsNotNone(classification.level_proximity)
        assert classification.level_proximity is not None
        self.assertEqual(classification.level_proximity.range_position, "near_support")

    def test_ticket_flags_stop_inside_atr_noise(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {
                "daily_1y": _chop_candles(111),
                "setup_30m": _chop_candles(),
                "timing_5m": _chop_candles(),
            },
            ticket=TechnicalTicket(side="buy", quantity=10, entry_price=100.0, stop_price=99.7, portfolio_value=100_000),
        )

        self.assertIn("inside normal noise", report.ticket_check.stop_quality)
        self.assertIn("inside normal ATR noise", " ".join(report.ticket_check.lines))
        self.assertIn("inside normal ATR noise", " ".join(report.setup_classification.warnings))

    def test_ticket_flags_good_entry_near_support_with_defined_stop(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {
                "daily_1y": _trend_candles(),
                "setup_30m": _washed_support_hold_candles(),
                "timing_5m": _washed_support_hold_candles(90),
            },
            ticket=TechnicalTicket(side="buy", quantity=10, entry_price=100.9, stop_price=100.0, portfolio_value=100_000),
        )

        self.assertEqual(report.ticket_check.entry_quality, "entry near support")
        self.assertIn("below support", report.ticket_check.stop_quality)
        self.assertEqual(report.ticket_check.risk_reward_read, "good")
        self.assertIn("Order is coherent", report.ticket_check.verdict)

    def test_ticket_flags_chase_entry_near_resistance(self) -> None:
        report = build_technical_command_center_report(
            "TST",
            {
                "daily_1y": _chop_candles(111),
                "setup_30m": _chop_candles(),
                "timing_5m": _chop_candles(),
            },
            ticket=TechnicalTicket(side="buy", quantity=10, entry_price=102.2, stop_price=99.5, portfolio_value=100_000),
        )

        self.assertIn("chasing near resistance", report.ticket_check.entry_quality)
        self.assertEqual(report.ticket_check.risk_reward_read, "poor")
        self.assertEqual(report.setup_classification.action_quality, "avoid_chase")


if __name__ == "__main__":
    unittest.main()
