from __future__ import annotations

import unittest

from app.brokers.hyperliquid.trading import (
    HyperliquidTriggerTicket,
    normalize_hyperliquid_trigger_ticket_for_wire,
)
from app.ui.hyperliquid_trading_extension import _normalize_edit_market


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


if __name__ == "__main__":
    unittest.main()
