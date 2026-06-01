from __future__ import annotations

import unittest
from unittest.mock import patch

from app.brokers.hyperliquid.trading import (
    HyperliquidExecutionAdapter,
    HyperliquidOrderEditTicket,
    HyperliquidOrderTicket,
    HyperliquidTradingConfig,
    HyperliquidTriggerTicket,
    normalize_hyperliquid_trigger_ticket_for_wire,
)
from app.core.portfolio import Portfolio, Position
from app.ui.cash_positions_extension import _portfolio_display_pnl_summary, _position_pnl_value
from app.ui.options_lab_extension import _populate_workspace_open_orders_table, _workspace_holding_rows
from app.ui.hyperliquid_trading_extension import _market_close_limit_price, _normalize_edit_market, _reverse_order_size_for_same_opposite_position, _risk_reward, _selected_hyperliquid_order, _set_hyperliquid_perp_mid_price, normalize_hyperliquid_open_order
from app.ui.hyperliquid_trading_extension import _current_hyperliquid_perp_position, _perp_position_pnl
from app.ui.hyperliquid_perp_ticket_use_mid_fix import (
    LEVERAGE_PNL_EXPLANATION,
    _estimated_margin_required,
    _isolated_liquidation_price,
    _liquidation_readout_lines,
    _perp_case,
    _tpsl_scenario_readout,
)
from app.ui.hyperliquid_submit_no_autosync_fix import _apply_ticket_leverage_if_needed, _attached_tpsl_tickets


class _Broker:
    def __init__(self, portfolio: Portfolio) -> None:
        self._portfolio = portfolio

    def get_portfolio(self) -> Portfolio:
        return self._portfolio


class _App:
    def __init__(self, portfolio: Portfolio) -> None:
        self.broker = _Broker(portfolio)


class _Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class _OpenOrdersTable:
    def __init__(self, selection: tuple[str, ...] = (), children: tuple[str, ...] = ()) -> None:
        self._selection = selection
        self._children = children
        self._items: dict[str, tuple[str, ...]] = {}
        self.inserted: list[tuple[str, tuple[object, ...], tuple[str, ...]]] = []
        self.columns = ("time", "type", "coin", "direction", "size", "price", "edit", "ro", "trigger", "tpsl", "oid")

    def selection(self) -> tuple[str, ...]:
        return self._selection

    def get_children(self) -> tuple[str, ...]:
        return self._children

    def item(self, row_id: str, option: str) -> tuple[str, ...]:
        if option != "values":
            return ()
        return self._items[row_id]

    def __getitem__(self, key: str) -> tuple[str, ...]:
        if key == "columns":
            return self.columns
        raise KeyError(key)

    def delete(self, _row_id: str) -> None:
        return None

    def insert(self, _parent: str, _index: str, *, iid: str, values: tuple[object, ...], tags: tuple[str, ...]) -> None:
        self.inserted.append((iid, values, tags))


class HyperliquidTradingTests(unittest.TestCase):
    def test_edit_market_normalizes_spot_and_perp_contexts_separately(self) -> None:
        self.assertEqual(_normalize_edit_market("BTC", "Spot"), "UBTC/USDC")
        self.assertEqual(_normalize_edit_market("BTC", "Perp"), "BTC")
        self.assertEqual(_normalize_edit_market("UBTC/USDC", "Spot"), "UBTC/USDC")

    def test_trigger_ticket_uses_hyperliquid_tpsl_payload(self) -> None:
        ticket = HyperliquidTriggerTicket(
            coin="BTC",
            is_buy=True,
            size=0.075,
            trigger_price=81698.5,
            tpsl="sl",
        )

        normalized = normalize_hyperliquid_trigger_ticket_for_wire(ticket)

        self.assertEqual(normalized.coin, "BTC")
        self.assertEqual(normalized.order_type_payload()["trigger"]["tpsl"], "sl")
        self.assertTrue(normalized.order_type_payload()["trigger"]["isMarket"])
        self.assertGreater(normalized.trigger_price, 0)

    def test_normal_perp_limit_order_normalizes_for_numeric_edit(self) -> None:
        order = normalize_hyperliquid_open_order(
            {
                "oid": 123,
                "coin": "BTC",
                "side": "A",
                "sz": "0.05",
                "limitPx": "74000",
                "orderType": "Limit",
                "reduceOnly": False,
                "tif": "Gtc",
            }
        )

        self.assertEqual(order.context, "Perp")
        self.assertEqual(order.direction, "Sell")
        self.assertEqual(order.size_label, "0.05")
        self.assertFalse(order.close_position)
        self.assertEqual(order.price_label, "74000")

    def test_stop_market_close_position_displays_hyperliquid_labels(self) -> None:
        order = normalize_hyperliquid_open_order(
            {
                "oid": 444774652117,
                "coin": "BTC",
                "side": "B",
                "sz": "0.0",
                "orderType": "Stop Market",
                "reduceOnly": True,
                "triggerPx": "81698",
                "triggerCondition": "Price above 81698",
                "isTrigger": True,
                "isPositionTpsl": True,
            }
        )

        self.assertEqual(order.direction, "Close Short")
        self.assertEqual(order.size_label, "Close Position")
        self.assertEqual(order.price_label, "Market")
        self.assertEqual(order.trigger_condition, "Price above 81698")
        self.assertEqual(order.tpsl_label, "SL")

    def test_close_position_trigger_edit_does_not_require_positive_size(self) -> None:
        ticket = HyperliquidOrderEditTicket(
            coin="BTC",
            is_buy=True,
            size=0.0,
            limit_price=81698.0,
            reduce_only=True,
            is_trigger=True,
            trigger_price=81698.0,
            trigger_kind="sl",
            is_market_trigger=True,
            close_position=True,
        )

        with patch.dict(
            "os.environ",
            {
                "HYPE_WALLET_ADDRESS": "0x0000000000000000000000000000000000000000",
                "HYPE_API_ADDRESS": "0x0000000000000000000000000000000000000001",
                "HYPE_API_SECRET": "not-a-real-secret",
                "HYPERLIQUID_ENABLE_LIVE_ORDERS": "true",
            },
        ):
            HyperliquidTradingConfig().validate_edit_for_live(ticket)

    def test_trigger_condition_falls_back_to_above_or_below(self) -> None:
        above = normalize_hyperliquid_open_order(
            {"coin": "BTC", "side": "B", "sz": "0", "reduceOnly": True, "isTrigger": True, "triggerPx": "81698"}
        )
        below = normalize_hyperliquid_open_order(
            {"coin": "BTC", "side": "A", "sz": "0", "reduceOnly": True, "isTrigger": True, "triggerPx": "70000"}
        )

        self.assertEqual(above.trigger_condition, "Price above 81698")
        self.assertEqual(below.trigger_condition, "Price below 70000")

    def test_current_perp_position_falls_back_to_broker_portfolio(self) -> None:
        portfolio = Portfolio(
            cash=0,
            positions={"ZEC-PERP-SHORT": Position("ZEC-PERP-SHORT", 4, 520.0, 506.0, open_profit_loss=56.0)},
        )

        position, is_short = _current_hyperliquid_perp_position(_App(portfolio), "ZEC")  # type: ignore[arg-type]

        self.assertTrue(is_short)
        self.assertEqual(position.symbol, "ZEC-PERP-SHORT")

    def test_perp_position_pnl_handles_short_and_long(self) -> None:
        self.assertAlmostEqual(_perp_position_pnl(500.0, 450.0, 4.0, True), 200.0)
        self.assertAlmostEqual(_perp_position_pnl(500.0, 550.0, 4.0, False), 200.0)

    def test_risk_reward_formats_valid_position_size_ratio(self) -> None:
        self.assertEqual(_risk_reward(150.0, -50.0), "3.00 : 1")

    def test_risk_reward_requires_positive_reward_and_stop_loss(self) -> None:
        self.assertEqual(_risk_reward(0.0, -50.0), "n/a - TP must be profitable and SL must be a loss")
        self.assertEqual(_risk_reward(50.0, 10.0), "n/a - TP must be profitable and SL must be a loss")

    def test_market_close_limit_price_crosses_the_book(self) -> None:
        self.assertAlmostEqual(_market_close_limit_price(100.0, is_short=True), 101.0)
        self.assertAlmostEqual(_market_close_limit_price(100.0, is_short=False), 99.0)

    def test_reverse_order_size_flips_to_same_size_opposite_position(self) -> None:
        self.assertEqual(_reverse_order_size_for_same_opposite_position(25.0), 50.0)

    def test_position_editor_mid_button_uses_perp_mid_lookup(self) -> None:
        target = _Var("")

        with patch("app.ui.hyperliquid_trading_extension._lookup_hyperliquid_perp_mid", return_value=71.7105) as lookup:
            mid = _set_hyperliquid_perp_mid_price("HYPE", target)  # type: ignore[arg-type]

        self.assertEqual(mid, 71.7105)
        self.assertEqual(target.get(), "71.7105")
        lookup.assert_called_once_with("HYPE")

    def test_selected_order_does_not_auto_edit_wrong_order_when_id_is_stale(self) -> None:
        app = type(
            "App",
            (),
            {
                "cancel_order_id_var": _Var("stale-oid"),
                "hyperliquid_open_order_by_oid": {"123": {"oid": 123, "coin": "BTC"}},
            },
        )()

        self.assertIsNone(_selected_hyperliquid_order(app))  # type: ignore[arg-type]
        self.assertEqual(app.cancel_order_id_var.get(), "stale-oid")

    def test_selected_order_requires_explicit_selection_when_orders_are_loaded(self) -> None:
        app = type(
            "App",
            (),
            {
                "cancel_order_id_var": _Var("123"),
                "hyperliquid_open_order_by_oid": {"123": {"oid": 123, "coin": "BTC"}},
                "hyperliquid_workspace_open_orders_table": _OpenOrdersTable(children=("row-1",)),
            },
        )()

        self.assertIsNone(_selected_hyperliquid_order(app))  # type: ignore[arg-type]
        self.assertEqual(app.cancel_order_id_var.get(), "123")

    def test_selected_order_uses_selected_table_row_oid(self) -> None:
        table = _OpenOrdersTable(selection=("row-2",), children=("row-1", "row-2"))
        table._items["row-2"] = ("16:55", "Limit", "HYPE", "Close Short", "1", "71.951", "Edit", "Yes", "N/A", "TP", "456")
        app = type(
            "App",
            (),
            {
                "cancel_order_id_var": _Var("123"),
                "hyperliquid_open_order_by_oid": {"123": {"oid": 123, "coin": "BTC"}, "456": {"oid": 456, "coin": "HYPE"}},
                "hyperliquid_workspace_open_orders_table": table,
            },
        )()

        self.assertEqual(_selected_hyperliquid_order(app), {"oid": 456, "coin": "HYPE"})  # type: ignore[arg-type]
        self.assertEqual(app.cancel_order_id_var.get(), "456")

    def test_workspace_open_orders_rows_include_visible_edit_action(self) -> None:
        table = _OpenOrdersTable()

        _populate_workspace_open_orders_table(
            table,  # type: ignore[arg-type]
            [
                {
                    "oid": 123,
                    "coin": "HYPE",
                    "side": "B",
                    "sz": "1",
                    "limitPx": "71.951",
                    "reduceOnly": True,
                    "orderType": "Limit",
                }
            ],
        )

        self.assertEqual(table.inserted[0][1][6], "Edit")
        self.assertEqual(table.inserted[0][1][-1], "123")

    def test_hyperliquid_workspace_holdings_use_one_best_pnl_column(self) -> None:
        spot = Position(
            "BTC",
            0.05,
            100_000.0,
            100_000.0,
            unrealized_profit_loss_known=False,
            custom_profit_loss=-450.37,
        )
        setattr(spot, "asset_type", "Spot")
        perp = Position(
            "HYPE-PERP-SHORT",
            19,
            59.82,
            73.59,
            open_profit_loss=-261.65,
            raw_profit_loss=-261.65,
        )
        setattr(perp, "asset_type", "Perp Short")
        portfolio = Portfolio(cash=0, positions={"BTC": spot, "HYPE-PERP-SHORT": perp})

        rows = _workspace_holding_rows(portfolio, "Hyperliquid")
        values = {str(row["symbol"]): row for row in rows}

        self.assertEqual(values["BTC"]["pnl"], -450.37)
        self.assertEqual(values["BTC"]["pnl_text"], "$-450.37")
        self.assertEqual(values["HYPE-PERP-SHORT"]["pnl"], -261.65)
        self.assertEqual(values["HYPE-PERP-SHORT"]["pnl_text"], "$-261.65")

    def test_cockpit_pnl_summary_uses_hyperliquid_custom_fallback(self) -> None:
        spot = Position(
            "HYPE",
            10,
            50.0,
            50.0,
            unrealized_profit_loss_known=False,
            custom_profit_loss=125.0,
        )
        setattr(spot, "asset_type", "Spot")
        equity = Position("GOOG", 2, 100.0, 125.0)
        portfolio = Portfolio(cash=0, positions={"HYPE": spot, "GOOG": equity})

        self.assertEqual(_position_pnl_value(spot), 125.0)
        self.assertEqual(_portfolio_display_pnl_summary(portfolio), (175.0, 25.0))

    def test_update_leverage_normalizes_coin_and_calls_exchange_hook(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "HYPE_WALLET_ADDRESS": "0x0000000000000000000000000000000000000000",
                "HYPE_API_ADDRESS": "0x0000000000000000000000000000000000000001",
                "HYPE_API_SECRET": "not-a-real-secret",
                "HYPERLIQUID_ENABLE_LIVE_ORDERS": "true",
            },
        ), patch.object(HyperliquidExecutionAdapter, "_local_signed_update_leverage", return_value={"ok": True}) as update:
            result = HyperliquidExecutionAdapter().update_leverage("zec-perp-short", 10, is_cross=True)

        self.assertEqual(result, {"ok": True})
        update.assert_called_once_with("ZEC", 10, is_cross=True)

    def test_live_submit_applies_ticket_leverage_for_non_reduce_only_perp(self) -> None:
        app = type(
            "App",
            (),
            {
                "hyperliquid_leverage_var": _Var("10"),
                "hyperliquid_margin_mode_var": _Var("Isolated"),
            },
        )()
        adapter = type("Adapter", (), {"update_leverage": lambda self, coin, leverage, *, is_cross: (coin, leverage, is_cross)})()
        ticket = HyperliquidOrderTicket("ZEC", is_buy=False, size=5, limit_price=512.65, tif="Gtc")

        result = _apply_ticket_leverage_if_needed(app, adapter, ticket)  # type: ignore[arg-type]

        self.assertEqual(result, ("ZEC", 10, False))

    def test_live_submit_skips_ticket_leverage_for_reduce_only_close(self) -> None:
        app = type(
            "App",
            (),
            {
                "hyperliquid_leverage_var": _Var("10"),
                "hyperliquid_margin_mode_var": _Var("Cross"),
            },
        )()
        adapter = type("Adapter", (), {"update_leverage": lambda self, coin, leverage, *, is_cross: self.fail("should not update leverage")})()
        ticket = HyperliquidOrderTicket("ZEC", is_buy=True, size=5, limit_price=512.65, tif="Gtc", reduce_only=True)

        self.assertIsNone(_apply_ticket_leverage_if_needed(app, adapter, ticket))  # type: ignore[arg-type]

    def test_reduce_only_close_is_not_blocked_by_new_trade_notional_cap(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "HYPE_WALLET_ADDRESS": "0x0000000000000000000000000000000000000000",
                "HYPE_API_ADDRESS": "0x0000000000000000000000000000000000000001",
                "HYPE_API_SECRET": "not-a-real-secret",
                "HYPERLIQUID_ENABLE_LIVE_ORDERS": "true",
                "HYPERLIQUID_MAX_LIVE_ORDER_DOLLARS": "500",
            },
        ):
            HyperliquidTradingConfig().validate_for_live(
                HyperliquidOrderTicket("ZEC", is_buy=True, size=5, limit_price=512.65, tif="Ioc", reduce_only=True)
            )

    def test_attached_tpsl_creates_short_stop_loss_child_order(self) -> None:
        app = type(
            "App",
            (),
            {
                "hyperliquid_attach_tpsl_var": _Var("1"),
                "hyperliquid_target_price_var": _Var(""),
                "hyperliquid_bad_price_var": _Var("550"),
                "stop_price_var": _Var(""),
            },
        )()
        ticket = HyperliquidOrderTicket("ZEC", is_buy=False, size=5, limit_price=512.65, tif="Gtc")

        children = _attached_tpsl_tickets(app, ticket)  # type: ignore[arg-type]

        self.assertEqual(len(children), 1)
        self.assertTrue(children[0].is_buy)
        self.assertEqual(children[0].tpsl, "sl")
        self.assertEqual(children[0].trigger_price, 550)

    def test_attached_tpsl_ignores_blank_prices(self) -> None:
        app = type(
            "App",
            (),
            {
                "hyperliquid_attach_tpsl_var": _Var("1"),
                "hyperliquid_target_price_var": _Var(""),
                "hyperliquid_bad_price_var": _Var(""),
                "stop_price_var": _Var(""),
            },
        )()
        ticket = HyperliquidOrderTicket("ZEC", is_buy=False, size=5, limit_price=512.65, tif="Gtc")

        self.assertEqual(_attached_tpsl_tickets(app, ticket), [])  # type: ignore[arg-type]

    def test_long_tp_below_entry_flags_invalid_take_profit(self) -> None:
        readout = _tpsl_scenario_readout("TP", 72_675.50, 35_000.0, True)

        self.assertFalse(readout.valid)
        self.assertEqual(readout.label, "TP field scenario - INVALID for LONG take-profit")
        self.assertEqual(readout.warning, "TP is below entry for a LONG. This is a loss scenario, not take profit.")

    def test_long_sl_above_entry_flags_invalid_stop_loss(self) -> None:
        readout = _tpsl_scenario_readout("SL", 72_675.50, 81_000.0, True)

        self.assertFalse(readout.valid)
        self.assertEqual(readout.label, "SL field scenario - INVALID for LONG stop-loss")
        self.assertEqual(readout.warning, "SL is above entry for a LONG. This is a profit scenario, not stop loss.")

    def test_short_tp_above_entry_flags_invalid_take_profit(self) -> None:
        readout = _tpsl_scenario_readout("TP", 72_675.50, 81_000.0, False)

        self.assertFalse(readout.valid)
        self.assertEqual(readout.label, "TP field scenario - INVALID for SHORT take-profit")
        self.assertEqual(readout.warning, "TP is above entry for a SHORT. This is a loss scenario, not take profit.")

    def test_short_sl_below_entry_flags_invalid_stop_loss(self) -> None:
        readout = _tpsl_scenario_readout("SL", 72_675.50, 35_000.0, False)

        self.assertFalse(readout.valid)
        self.assertEqual(readout.label, "SL field scenario - INVALID for SHORT stop-loss")
        self.assertEqual(readout.warning, "SL is below entry for a SHORT. This is a profit scenario, not stop loss.")

    def test_perp_gross_pnl_is_independent_of_leverage(self) -> None:
        case_5x = _perp_case(72_675.50, 81_000.0, 0.10, True, 5.0, 0.0)
        case_30x = _perp_case(72_675.50, 81_000.0, 0.10, True, 30.0, 0.0)

        self.assertAlmostEqual(case_5x["gross_pnl"], case_30x["gross_pnl"])
        self.assertAlmostEqual(case_5x["net_pnl"], case_30x["net_pnl"])
        self.assertEqual(LEVERAGE_PNL_EXPLANATION, "Leverage does not change dollar P&L for a fixed contract size. It changes margin required, ROI on margin, and liquidation distance.")

    def test_higher_leverage_lowers_margin_and_raises_roi_magnitude(self) -> None:
        entry = 72_675.50
        exit_price = 81_000.0
        size = 0.10
        case_5x = _perp_case(entry, exit_price, size, True, 5.0, 0.0)
        case_30x = _perp_case(entry, exit_price, size, True, 30.0, 0.0)

        self.assertLess(_estimated_margin_required(entry, size, 30.0), _estimated_margin_required(entry, size, 5.0))
        self.assertGreater(abs(case_30x["margin_roi_percent"]), abs(case_5x["margin_roi_percent"]))

    def test_liquidation_readout_labels_cross_and_isolated_estimates(self) -> None:
        lines = _liquidation_readout_lines(72_675.50, 0.10, True, 30.0, 500_000.0)
        text = "\n".join(lines)

        self.assertIn("Isolated-style liquidation estimate using ticket margin/leverage", text)
        self.assertIn("Cross-margin rough liquidation estimate using account collateral", text)
        self.assertIn("not an isolated liquidation estimate", text)
        self.assertNotIn("Rough liquidation estimate: $0.0000", text)

    def test_isolated_liquidation_moves_toward_entry_as_leverage_increases(self) -> None:
        long_5x = _isolated_liquidation_price(100.0, 1.0, True, 5.0)
        long_30x = _isolated_liquidation_price(100.0, 1.0, True, 30.0)
        short_5x = _isolated_liquidation_price(100.0, 1.0, False, 5.0)
        short_30x = _isolated_liquidation_price(100.0, 1.0, False, 30.0)

        assert long_5x is not None and long_30x is not None and short_5x is not None and short_30x is not None
        self.assertGreater(long_30x, long_5x)
        self.assertLess(short_30x, short_5x)
        self.assertLess(100.0 - long_30x, 100.0 - long_5x)
        self.assertLess(short_30x - 100.0, short_5x - 100.0)


if __name__ == "__main__":
    unittest.main()
