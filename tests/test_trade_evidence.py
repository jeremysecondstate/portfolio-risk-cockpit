from __future__ import annotations

import json
import math
import tempfile
import unittest
from pathlib import Path

from app.analytics.research_scoring import build_decision_readout
from app.analytics.stock_research import DataSourceStatus, build_portfolio_symbol_context, build_scenario_rows, calculate_advanced_indicators
from app.analytics.technical_analysis import Candle
from app.analytics.trade_evidence import append_trade_evidence_snapshot, build_trade_evidence_report, format_trade_evidence_report
from app.core.portfolio import Portfolio, Position


def _sample_candles(count: int = 260, *, step: float = 0.45, symbol_noise: float = 1.0) -> list[Candle]:
    candles: list[Candle] = []
    for index in range(count):
        close = 100.0 + index * step + math.sin(index / 6) * symbol_noise
        candles.append(
            Candle(
                datetime_ms=1_700_000_000_000 + index * 86_400_000,
                open=close - 0.30,
                high=close + 1.20,
                low=close - 1.00,
                close=close,
                volume=1_200_000 + index * 1_000,
            )
        )
    return candles


def _option_chain() -> list[dict]:
    return [
        {
            "underlying": "NVDA",
            "expiration_label": "Jun 19 2026 (17d)",
            "dte": 17,
            "strike": 215.0,
            "call": {"bid": 4.8, "ask": 5.2, "mark": 5.0, "impliedVolatility": 0.32, "openInterest": 1200, "totalVolume": 240},
            "put": {"bid": 4.5, "ask": 4.9, "mark": 4.7, "impliedVolatility": 0.34, "openInterest": 900, "totalVolume": 180},
        }
    ]


def _report(
    *,
    symbol: str = "NVDA",
    portfolio: Portfolio | None = None,
    candles: list[Candle] | None = None,
    earnings_text: str = "Latest earnings release showed revenue increased and guidance reaffirmed.",
    macro_text: str = "Inflation cooler and rates down; macro tailwind.",
    chain: list[dict] | None = None,
):
    candles = candles if candles is not None else _sample_candles()
    indicators = calculate_advanced_indicators(symbol, candles)
    last = indicators.latest_close or candles[-1].close
    portfolio = portfolio or Portfolio(cash=90_000.0, positions={symbol: Position(symbol, 4, last - 10, last)})
    context = build_portfolio_symbol_context(portfolio, symbol, fallback_price=last)
    rows = build_scenario_rows(context)
    statuses = [
        DataSourceStatus("Schwab quote", "fresh", "2026-06-02T12:00:00+00:00", "loaded"),
        DataSourceStatus("Schwab price history", "fresh", "2026-06-02T12:00:00+00:00", "loaded"),
        DataSourceStatus("Schwab option chain", "fresh", "2026-06-02T12:00:00+00:00", "loaded"),
    ]
    decision = build_decision_readout(
        indicators=indicators,
        context=context,
        scenario_rows=rows,
        earnings_text=earnings_text,
        fundamentals_text="Revenue growth and free cash flow are positive.",
        macro_text=macro_text,
        statuses=statuses,
    )
    spy = _sample_candles(step=0.12)
    qqq = _sample_candles(step=0.18)
    market_indicators = {
        "SPY": calculate_advanced_indicators("SPY", spy),
        "QQQ": calculate_advanced_indicators("QQQ", qqq),
    }
    market_candles = {"SPY": spy, "QQQ": qqq}
    quote = {"quote": {"bidPrice": last - 0.02, "askPrice": last + 0.02}}
    return build_trade_evidence_report(
        symbol=symbol,
        indicators=indicators,
        context=context,
        decision=decision,
        scenario_rows=rows,
        earnings_text=earnings_text,
        macro_text=macro_text,
        statuses=statuses,
        quote=quote,
        option_chain_rows=chain if chain is not None else _option_chain(),
        symbol_candles=candles,
        market_indicators=market_indicators,
        market_candles=market_candles,
    )


class TradeEvidenceTests(unittest.TestCase):
    def test_constructive_report_has_verdict_scorecard_and_expected_move(self) -> None:
        report = _report()
        text = format_trade_evidence_report(report)

        self.assertEqual(report.posture, "NORMAL")
        self.assertTrue(report.setup_type)
        self.assertTrue(any(grade.category == "Options / IV" for grade in report.grades))
        self.assertTrue(any("expected move" in line.lower() for line in report.options_iv))
        self.assertIn("Verdict:", text)
        self.assertIn("What would make this trade dumb?", text)
        self.assertIn("not a recommendation", text)

    def test_missing_core_data_degrades_to_no_read(self) -> None:
        indicators = calculate_advanced_indicators("ABC", [])
        context = build_portfolio_symbol_context(Portfolio(cash=10_000.0), "ABC", fallback_price=None)
        rows = build_scenario_rows(context)
        statuses = [
            DataSourceStatus("Schwab price history", "error", "now", "offline"),
            DataSourceStatus("Schwab option chain", "error", "now", "offline"),
        ]
        decision = build_decision_readout(
            indicators=indicators,
            context=context,
            scenario_rows=rows,
            earnings_text="Earnings unavailable.",
            fundamentals_text="Fundamentals unavailable.",
            macro_text="",
            statuses=statuses,
        )

        report = build_trade_evidence_report(
            symbol="ABC",
            indicators=indicators,
            context=context,
            decision=decision,
            scenario_rows=rows,
            earnings_text="Earnings unavailable.",
            macro_text="",
            statuses=statuses,
        )

        self.assertEqual(report.posture, "NO-READ")
        self.assertTrue(any(grade.grade == "NO READ" for grade in report.grades))
        self.assertTrue(any("price history" in item.lower() for item in report.missing_data))
        self.assertIn("cannot be judged", report.verdict)

    def test_event_options_and_portfolio_pressure_make_defensive_report(self) -> None:
        candles = _sample_candles()
        indicators = calculate_advanced_indicators("NVDA", candles)
        last = indicators.latest_close or candles[-1].close
        portfolio = Portfolio(cash=500.0, positions={"NVDA": Position("NVDA", 1_000, last - 15, last)})
        chain = [
            {
                "underlying": "NVDA",
                "expiration_label": "Jun 05 2026 (3d)",
                "dte": 3,
                "strike": round(last),
                "call": {"bid": 1.0, "ask": 2.1, "impliedVolatility": 1.15, "openInterest": 5, "totalVolume": 0},
                "put": {"bid": 1.0, "ask": 2.1, "impliedVolatility": 1.20, "openInterest": 5, "totalVolume": 0},
            }
        ]

        report = _report(
            portfolio=portfolio,
            candles=candles,
            earnings_text="Earnings event: today. Awaiting release.",
            macro_text="Inflation hotter and policy hawkish.",
            chain=chain,
        )

        self.assertEqual(report.posture, "DEFENSIVE")
        joined = "\n".join(report.contradictory_evidence + report.dumb_if).lower()
        self.assertIn("event risk", joined)
        self.assertIn("portfolio", joined)
        self.assertIn("option", joined)

    def test_snapshot_logging_writes_jsonl(self) -> None:
        report = _report()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "snapshots.jsonl"
            written = append_trade_evidence_snapshot(report, path)
            payload = json.loads(written.read_text(encoding="utf-8").strip())

        self.assertEqual(payload["symbol"], report.symbol)
        self.assertEqual(payload["posture"], report.posture)
        self.assertTrue(payload["grades"])


if __name__ == "__main__":
    unittest.main()
