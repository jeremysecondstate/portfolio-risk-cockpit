from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from app.analytics.research_workspace_insights import (
    OptionCandidate,
    combined_current_model_option_scenarios,
    combined_option_scenarios,
    covered_contract_capacity,
    option_expiration_payoff,
    option_position_readout,
    option_strategy_scenario_move_note,
    option_strategy_scenario_moves,
    suggest_option_candidates,
)
from app.analytics.empirical_recommendation import build_option_required_move_read
from app.analytics.stock_research import AdvancedIndicatorSnapshot, GeneratedStockPosition, PortfolioSymbolContext
from app.ui import schwab_research_workspace_extension as research_ui
from app.ui.schwab_research_workspace_extension import _normalized_candidate_bar_rows
from app.ui.schwab_research_workspace_extension import _option_required_move_card, _option_required_move_summary


def _context(*, quantity: float, is_held: bool = True, last_price: float = 10.0) -> PortfolioSymbolContext:
    return PortfolioSymbolContext(
        symbol="RDW",
        is_held=is_held,
        quantity=quantity,
        average_cost=last_price,
        last_price=last_price,
        market_value=quantity * last_price,
        portfolio_value=100_000.0,
        portfolio_weight=(quantity * last_price) / 100_000.0,
        unrealized_pnl=0.0,
        day_pnl=0.0,
        cash_available=50_000.0,
    )


def _indicators(price: float = 10.0) -> AdvancedIndicatorSnapshot:
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
        momentum="steady",
        support=price * 0.9,
        resistance=price * 1.2,
        notes=[],
    )


def _chain_row(strike: float = 11.0, premium: float = 2.0) -> dict:
    return {
        "underlying": "RDW",
        "strike": strike,
        "expiration_label": "2026-07-17",
        "dte": 44,
        "call": {
            "bid": premium,
            "ask": premium,
            "mark": premium,
            "symbol": "RDW260717C00012000",
            "openInterest": 500,
            "totalVolume": 50,
            "delta": 0.3,
            "theta": -0.02,
            "impliedVolatility": 0.45,
        },
        "put": {
            "bid": 1.0,
            "ask": 1.0,
            "mark": 1.0,
            "symbol": "RDW260717P00012000",
            "openInterest": 500,
            "totalVolume": 50,
            "delta": -0.35,
            "theta": -0.02,
            "impliedVolatility": 0.45,
        },
    }


def _weak_call_chain_row() -> dict:
    row = _chain_row(strike=10.5, premium=2.0)
    row["call"].update(
        {
            "bid": 0.05,
            "ask": 3.95,
            "mark": 2.0,
            "openInterest": 1,
            "totalVolume": 0,
            "delta": 0.05,
            "impliedVolatility": 1.20,
        }
    )
    return row


def _high_iv_call_chain_row() -> dict:
    row = _chain_row(strike=10.5, premium=1.0)
    row["call"].update(
        {
            "bid": 1.0,
            "ask": 1.0,
            "mark": 1.0,
            "openInterest": 800,
            "totalVolume": 150,
            "delta": 0.42,
            "impliedVolatility": 1.20,
        }
    )
    return row


def _bearish_indicators(price: float = 10.0) -> AdvancedIndicatorSnapshot:
    return AdvancedIndicatorSnapshot(
        **{
            **_indicators(price).__dict__,
            "sma_20": price * 1.05,
            "sma_50": price * 1.08,
            "sma_100": price * 1.1,
            "sma_200": price * 1.12,
            "macd_histogram": -0.10,
            "rsi_14": 38.0,
            "trend": "bearish",
            "momentum": "weakening",
            "support": price * 0.92,
            "resistance": price * 1.05,
        }
    )


def _candidate(*, option_type: str = "call", covered: bool = False, contracts: int = 1, strike: float = 12.0, premium: float = 2.0) -> OptionCandidate:
    return OptionCandidate(
        key="candidate",
        group="Covered Call" if covered else "Long Option",
        strategy="Income / covered-call candidate" if covered else f"Long {option_type}",
        expiration="2026-07-17",
        strike=strike,
        option_type=option_type,
        bid=premium,
        ask=premium,
        mark=premium,
        midpoint=premium,
        max_loss=None if covered else premium * contracts * 100,
        max_gain=None,
        breakeven=10.0 - premium if covered else strike + premium if option_type == "call" else strike - premium,
        why="test",
        works_if="test",
        goes_wrong_if="test",
        relation_to_position="test",
        confidence="Watch",
        contract_symbol="TEST",
        underlying="RDW",
        underlying_price=10.0,
        contract_count=contracts,
        controlled_shares=contracts * 100,
    )


class OptionScenarioMathTests(unittest.TestCase):
    def test_partial_holding_does_not_generate_covered_call(self) -> None:
        candidates = suggest_option_candidates([_chain_row()], _indicators(), _context(quantity=67))
        self.assertFalse(any(candidate.group == "Covered Call" for candidate in candidates))

    def test_valid_covered_call_and_positive_down_move_read(self) -> None:
        candidates = suggest_option_candidates([_chain_row()], _indicators(), _context(quantity=100))
        covered = next(candidate for candidate in candidates if candidate.group == "Covered Call")
        self.assertEqual(covered.contract_count, 1)

        row = combined_option_scenarios(covered, _context(quantity=100), moves=(-0.10,))[0]
        self.assertGreater(row.combined_pnl, 0)
        self.assertIn("retained option premium exceeds stock loss", row.read)

    def test_multiple_contracts_use_floor_share_capacity(self) -> None:
        context = _context(quantity=250)
        self.assertEqual(covered_contract_capacity(context), 2)
        candidates = suggest_option_candidates([_chain_row()], _indicators(), context)
        covered = next(candidate for candidate in candidates if candidate.group == "Covered Call")
        self.assertEqual(covered.contract_count, 2)

        row = combined_option_scenarios(covered, context, moves=(-0.10,))[0]
        self.assertAlmostEqual(row.option_pnl, 400.0)
        self.assertAlmostEqual(row.stock_pnl, -250.0)
        self.assertAlmostEqual(row.combined_pnl, 150.0)

    def test_unheld_position_does_not_generate_covered_call(self) -> None:
        candidates = suggest_option_candidates([_chain_row()], _indicators(), _context(quantity=0, is_held=False))
        self.assertFalse(any(candidate.group == "Covered Call" for candidate in candidates))

    def test_wait_candidate_does_not_receive_fake_option_subscores(self) -> None:
        candidates = suggest_option_candidates([_chain_row()], _indicators(), _context(quantity=0, is_held=False))
        wait = next(candidate for candidate in candidates if candidate.strategy == "No-trade / wait")

        self.assertEqual(wait.contract_count, 0)
        self.assertEqual(wait.controlled_shares, 0)
        self.assertEqual(wait.liquidity_score, 0.0)
        self.assertEqual(wait.greek_score, 0.0)
        self.assertEqual(wait.risk_budget_score, 0.0)
        self.assertIn("not credited with perfect liquidity", " ".join(wait.score_breakdown).lower())

    def test_required_move_card_surfaces_long_option_hurdle(self) -> None:
        candidate = replace(_candidate(option_type="call"), underlying_price=10.0, breakeven=11.20, iv=0.20, dte=30)

        card = _option_required_move_card(candidate)

        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.title, "Required vs Implied")
        self.assertEqual(card.status, "bad")
        self.assertIn("exceeds implied", card.label.lower())

    def test_required_move_summary_uses_covered_call_wording(self) -> None:
        candidate = replace(_candidate(covered=True), iv=0.30, dte=30)

        card = _option_required_move_card(candidate)
        summary = _option_required_move_summary(build_option_required_move_read(candidate))

        self.assertIsNotNone(card)
        assert card is not None
        self.assertIn("covered-call", card.label.lower())
        self.assertIn("not a long-debit required-move hurdle", card.why.lower())
        self.assertIn("covered-call", summary.lower())

    def test_wait_candidate_explains_when_it_outranks_weak_actionable_contract(self) -> None:
        candidates = suggest_option_candidates([_weak_call_chain_row()], _indicators(), _context(quantity=0, is_held=False))

        self.assertEqual(candidates[0].strategy, "No-trade / wait")
        self.assertIn("no actionable option cleared", candidates[0].score_reason.lower())
        self.assertIn("best actionable candidate", " ".join(candidates[0].score_breakdown).lower())

    def test_expiration_payoff_formulas(self) -> None:
        short_call = _candidate(covered=True, strike=12.0, premium=2.0)
        self.assertAlmostEqual(option_expiration_payoff(short_call, 10.0), 200.0)
        self.assertAlmostEqual(option_expiration_payoff(short_call, 15.0), -100.0)

        long_call = _candidate(option_type="call", covered=False, strike=12.0, premium=2.0)
        self.assertAlmostEqual(option_expiration_payoff(long_call, 15.0), 100.0)

        long_put = _candidate(option_type="put", covered=False, strike=10.0, premium=1.0)
        self.assertAlmostEqual(option_expiration_payoff(long_put, 8.0), 100.0)

    def test_current_model_option_scenarios_show_both_share_bases(self) -> None:
        candidate = _candidate(option_type="call", strike=10.0, premium=0.5)
        model_position = GeneratedStockPosition(
            quantity=25.0,
            entry_price=10.0,
            stop_price=9.0,
            risk_dollars=25.0,
            notional=250.0,
            portfolio_weight=0.0025,
            per_share_risk=1.0,
            basis="test model target",
        )

        row = combined_current_model_option_scenarios(candidate, _context(quantity=100), model_position, moves=(0.10,))[0]

        self.assertEqual(row.current_shares, 100.0)
        self.assertEqual(row.model_shares, 25.0)
        self.assertAlmostEqual(row.current_stock_pnl, 100.0)
        self.assertAlmostEqual(row.model_stock_pnl or 0.0, 25.0)
        self.assertAlmostEqual(row.option_pnl, 50.0)
        self.assertAlmostEqual(row.current_combined_pnl, 150.0)
        self.assertAlmostEqual(row.model_combined_pnl or 0.0, 75.0)

    def test_option_strategy_scenario_moves_include_option_specific_points(self) -> None:
        candidate = replace(_candidate(option_type="call", strike=12.0, premium=1.0), dte=30, iv=0.40)
        moves = option_strategy_scenario_moves(candidate, _indicators())
        rounded = {round(move, 4) for move in moves}
        expected_move = round(0.40 * (30 / 365) ** 0.5, 4)

        self.assertIn(-0.10, rounded)
        self.assertIn(0.10, rounded)
        self.assertIn(0.20, rounded)  # strike
        self.assertIn(0.30, rounded)  # breakeven
        self.assertIn(0.32, rounded)  # beyond breakeven
        self.assertIn(expected_move, rounded)
        self.assertIn(-expected_move, rounded)
        self.assertIn(0.05, rounded)  # 1 ATR

    def test_option_strategy_scenario_move_note_labels_key_points(self) -> None:
        candidate = replace(_candidate(option_type="call", strike=12.0, premium=1.0), dte=30, iv=0.40)
        expected_move = round(0.40 * (30 / 365) ** 0.5, 4)

        self.assertIn("breakeven", option_strategy_scenario_move_note(candidate, _indicators(), 0.30))
        self.assertIn("beyond breakeven", option_strategy_scenario_move_note(candidate, _indicators(), 0.32))
        self.assertIn("1 ATR", option_strategy_scenario_move_note(candidate, _indicators(), 0.05))
        self.assertIn("expected move", option_strategy_scenario_move_note(candidate, _indicators(), expected_move))

    def test_option_position_readout_is_candidate_aware(self) -> None:
        model_position = GeneratedStockPosition(
            quantity=25.0,
            entry_price=10.0,
            stop_price=9.0,
            risk_dollars=25.0,
            notional=250.0,
            portfolio_weight=0.0025,
            per_share_risk=1.0,
            basis="test model target",
        )
        put_readout = option_position_readout(_candidate(option_type="put", contracts=1), _context(quantity=45), model_position)
        self.assertIsNotNone(put_readout)
        self.assertEqual(put_readout.title, "Hedge Ratio")
        self.assertIn("100 shares", put_readout.detail)
        self.assertIn("45 current shares", put_readout.detail)
        self.assertIn("25", put_readout.detail)

        covered_readout = option_position_readout(_candidate(covered=True, contracts=1), _context(quantity=45), model_position)
        self.assertIsNotNone(covered_readout)
        self.assertEqual(covered_readout.title, "Coverage")
        self.assertEqual(covered_readout.label, "Not fully covered")

    def test_tiny_holding_protective_put_is_extreme_overhedge(self) -> None:
        model_position = GeneratedStockPosition(
            quantity=7.0,
            entry_price=10.0,
            stop_price=9.0,
            risk_dollars=7.0,
            notional=70.0,
            portfolio_weight=0.0007,
            per_share_risk=1.0,
            basis="test model target",
        )
        candidates = suggest_option_candidates([_chain_row(strike=10.0, premium=0.5)], _bearish_indicators(), _context(quantity=4), stock_plan=model_position)
        protective = next(candidate for candidate in candidates if candidate.group == "Protective Put")

        self.assertEqual(candidates[0].strategy, "No-trade / wait")
        self.assertLess(protective.score, 50.0)
        self.assertTrue(any("extreme over-hedge" in warning.lower() for warning in protective.practical_warnings))
        self.assertIn("too large", protective.avoid_reason.lower())

        readout = option_position_readout(protective, _context(quantity=4), model_position)
        self.assertIsNotNone(readout)
        self.assertEqual(readout.label, "Extreme over-hedge")

    def test_high_iv_earnings_debit_candidate_is_penalized(self) -> None:
        model_position = GeneratedStockPosition(
            quantity=10.0,
            entry_price=10.0,
            stop_price=9.0,
            risk_dollars=10.0,
            notional=100.0,
            portfolio_weight=0.001,
            per_share_risk=1.0,
            basis="test model target",
        )
        candidates = suggest_option_candidates(
            [_high_iv_call_chain_row()],
            _indicators(),
            _context(quantity=0, is_held=False),
            macro_label="Headwind",
            earnings_text="Earnings imminent; event risk soon.",
            risk_budget=10.0,
            stock_plan=model_position,
        )

        self.assertEqual(candidates[0].strategy, "No-trade / wait")
        call = next(candidate for candidate in candidates if candidate.option_type == "call")
        self.assertTrue(any("high iv" in warning.lower() for warning in call.practical_warnings))
        self.assertLess(call.score, 50.0)

    def test_normalized_candidate_bar_rows_preserve_signs(self) -> None:
        rows = [
            SimpleNamespace(move_label="-5%", combined_pnl=-50.0),
            SimpleNamespace(move_label="0%", combined_pnl=0.0),
            SimpleNamespace(move_label="+5%", combined_pnl=100.0),
        ]
        normalized = _normalized_candidate_bar_rows(rows)
        self.assertLess(normalized[0][1], 0)
        self.assertEqual(normalized[1][1], 0)
        self.assertGreater(normalized[2][1], 0)
        self.assertEqual(normalized[0][2], "-$50.00")

    def test_programmatic_candidate_selection_suppresses_treeview_callback(self) -> None:
        callbacks: list[object] = []
        app = SimpleNamespace(after_idle=lambda callback: callbacks.append(callback))

        class FakeTree:
            selected_iid = ""
            focused_iid = ""
            seen_iid = ""

            def selection_set(self, iid: str) -> None:
                self.selected_iid = iid
                research_ui._show_selected_option_candidate(app)

            def focus(self, iid: str) -> None:
                self.focused_iid = iid

            def see(self, iid: str) -> None:
                self.seen_iid = iid

        original_selected = research_ui._selected_option_candidate
        research_ui._selected_option_candidate = lambda _app: (_ for _ in ()).throw(AssertionError("selection handler was not suppressed"))
        try:
            tree = FakeTree()
            research_ui._select_option_candidate_row_silently(app, tree, "candidate_0")

            self.assertEqual(tree.selected_iid, "candidate_0")
            self.assertEqual(tree.focused_iid, "candidate_0")
            self.assertEqual(tree.seen_iid, "candidate_0")
            self.assertTrue(research_ui._option_candidate_selection_guard_active(app))
            self.assertEqual(len(callbacks), 1)

            callbacks[0]()
            self.assertFalse(research_ui._option_candidate_selection_guard_active(app))
        finally:
            research_ui._selected_option_candidate = original_selected

    def test_candidate_selection_handler_does_not_render_full_scenarios(self) -> None:
        calls = {"greeks": 0, "option_scenarios": 0}
        candidate = SimpleNamespace(
            group="No-Trade",
            strategy="No-trade / wait",
            option_type="wait",
            underlying_price=10.0,
            contract_symbol="",
        )

        class FakeVar:
            value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeTree:
            rows: list[tuple] = []

            def get_children(self) -> list[str]:
                return [str(index) for index, _row in enumerate(self.rows)]

            def delete(self, row_id: str) -> None:
                index = int(row_id)
                if 0 <= index < len(self.rows):
                    self.rows[index] = ()

            def insert(self, *_args: object, **kwargs: object) -> None:
                self.rows.append(tuple(kwargs.get("values", ())))

        class FakeBars:
            rows: list[tuple] = []

            def set_rows(self, rows: list[tuple]) -> None:
                self.rows = rows

        frame = SimpleNamespace(
            timeline_var=FakeVar(),
            candidate_scenario_tree=FakeTree(),
            candidate_bars=FakeBars(),
            detail_text=SimpleNamespace(),
        )
        app = SimpleNamespace(
            schwab_research_options_frame=frame,
            schwab_research_option_candidates=[candidate],
            schwab_research_last_payload=SimpleNamespace(earnings_text="", context=_context(quantity=0)),
        )

        originals = {
            "_selected_option_candidate": research_ui._selected_option_candidate,
            "_render_option_strategy_cards": research_ui._render_option_strategy_cards,
            "option_timeline_text": research_ui.option_timeline_text,
            "_render_candidate_score_breakdown": research_ui._render_candidate_score_breakdown,
            "selected_candidate_detail": research_ui.selected_candidate_detail,
            "_set_research_text": research_ui._set_research_text,
            "_options_strategy_popout_text": research_ui._options_strategy_popout_text,
            "_render_greeks": research_ui._render_greeks,
            "_render_option_scenarios_from_top": research_ui._render_option_scenarios_from_top,
            "_render_scenarios": research_ui._render_scenarios,
        }
        try:
            research_ui._selected_option_candidate = lambda _app: candidate
            research_ui._render_option_strategy_cards = lambda *_args, **_kwargs: None
            research_ui.option_timeline_text = lambda *_args, **_kwargs: "timeline"
            research_ui._render_candidate_score_breakdown = lambda *_args, **_kwargs: None
            research_ui.selected_candidate_detail = lambda *_args, **_kwargs: ["detail"]
            research_ui._set_research_text = lambda *_args, **_kwargs: None
            research_ui._options_strategy_popout_text = lambda *_args, **_kwargs: "popout"
            research_ui._render_greeks = lambda *_args, **_kwargs: calls.__setitem__("greeks", calls["greeks"] + 1)
            research_ui._render_option_scenarios_from_top = lambda *_args, **_kwargs: calls.__setitem__("option_scenarios", calls["option_scenarios"] + 1)
            research_ui._render_scenarios = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("full scenario renderer should not run on candidate selection"))

            research_ui._show_selected_option_candidate(app)

            self.assertEqual(frame.timeline_var.value, "timeline")
            self.assertEqual(calls["greeks"], 1)
            self.assertEqual(calls["option_scenarios"], 1)
        finally:
            for name, original in originals.items():
                setattr(research_ui, name, original)


if __name__ == "__main__":
    unittest.main()
