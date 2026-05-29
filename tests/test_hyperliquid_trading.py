from __future__ import annotations

import unittest
from unittest.mock import patch

from app.brokers.hyperliquid.trading import (
    HyperliquidOrderEditTicket,
    HyperliquidTradingConfig,
    HyperliquidTriggerTicket,
    normalize_hyperliquid_trigger_ticket_for_wire,
)
from app.ui.hyperliquid_trading_extension import _normalize_edit_market, normalize_hyperliquid_open_order


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


if __name__ == "__main__":
    unittest.main()
