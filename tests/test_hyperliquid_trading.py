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
from app.ui.options_lab_extension import _populate_workspace_open_orders_table
from app.ui.hyperliquid_trading_extension import _market_close_limit_price, _normalize_edit_market, _reverse_order_size_for_same_opposite_position, _risk_reward, _selected_hyperliquid_order, _set_hyperliquid_perp_mid_price, normalize_hyperliquid_open_order
from app.ui.hyperliquid_trading_extension import _current_hyperliquid_perp_position, _perp_position_pnl
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


if __name__ == "__main__":
    unittest.main()
