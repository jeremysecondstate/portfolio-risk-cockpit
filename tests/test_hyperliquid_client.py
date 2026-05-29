from __future__ import annotations

from datetime import datetime
import unittest

from app.brokers.hyperliquid.client import (
    HyperliquidSnapshot,
    format_hyperliquid_snapshot,
    portfolio_from_hyperliquid_snapshot,
)


def _snapshot(
    *,
    clearinghouse_state: dict | None = None,
    spot_state: dict | None = None,
    all_mids: dict | None = None,
    user_fills: list[dict] | None = None,
) -> HyperliquidSnapshot:
    return HyperliquidSnapshot(
        user="0x0000000000000000000000000000000000000000",
        clearinghouse_state=clearinghouse_state or {},
        spot_state=spot_state or {},
        open_orders=[],
        all_mids=all_mids or {},
        spot_meta_and_asset_ctxs=None,
        fetched_at=datetime(2026, 5, 28, 15, 30, 0),
        user_fills=user_fills or [],
    )


class HyperliquidPortfolioTests(unittest.TestCase):
    def test_spot_pnl_uses_current_value_minus_cost_basis(self) -> None:
        snapshot = _snapshot(
            spot_state={
                "balances": [
                    {
                        "coin": "BTC",
                        "total": "0.050062",
                        "costBasis": "3682.40",
                        "usdValue": "3682.40",
                        "pnl": "3682.40",
                    }
                ]
            }
        )

        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        position = portfolio.positions["BTC-SPOT"]

        self.assertEqual(position.market_value, 3682.40)
        self.assertEqual(position.cost_basis, 3682.40)
        self.assertEqual(position.unrealized_profit_loss, 0.0)
        self.assertEqual(position.unrealized_profit_loss_percent, 0.0)

    def test_spot_zero_entry_notional_is_treated_as_unknown_cost(self) -> None:
        snapshot = _snapshot(
            spot_state={
                "balances": [
                    {
                        "coin": "BTC",
                        "total": "0.050062",
                        "usdValue": "3679.04",
                        "entryNtl": "0",
                    }
                ]
            }
        )

        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        position = portfolio.positions["BTC-SPOT"]
        report = format_hyperliquid_snapshot(snapshot, portfolio)

        self.assertAlmostEqual(position.average_cost, 73489.5, delta=0.25)
        self.assertAlmostEqual(position.last_price, 73489.5, delta=0.25)
        self.assertEqual(position.unrealized_profit_loss, 0.0)
        self.assertEqual(position.unrealized_profit_loss_percent, 0.0)
        self.assertIn("entry $3,679.04", report)
        self.assertIn("P&L $0.00 (+0.00%)", report)

    def test_spot_real_cost_basis_still_calculates_pnl(self) -> None:
        snapshot = _snapshot(
            spot_state={
                "balances": [
                    {
                        "coin": "ZEC",
                        "total": "3.08864",
                        "costBasis": "1917.57",
                        "usdValue": "1692.43",
                    }
                ]
            }
        )

        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        position = portfolio.positions["ZEC-SPOT"]

        self.assertEqual(position.cost_basis, 1917.57)
        self.assertEqual(position.market_value, 1692.43)
        self.assertEqual(position.unrealized_profit_loss, -225.14)

    def test_spot_buy_history_creates_cost_basis_and_pnl(self) -> None:
        snapshot = _snapshot(
            spot_state={
                "balances": [
                    {
                        "coin": "BTC",
                        "total": "0.1",
                        "usdValue": "110.00",
                        "entryNtl": "0",
                    }
                ]
            },
            user_fills=[
                {
                    "time": 1,
                    "coin": "BTC/USDC",
                    "side": "B",
                    "sz": "0.1",
                    "px": "1000",
                    "fee": "1",
                    "feeToken": "USDC",
                }
            ],
        )

        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        position = portfolio.positions["BTC-SPOT"]

        self.assertEqual(position.cost_basis, 101.00)
        self.assertEqual(position.market_value, 110.00)
        self.assertEqual(position.unrealized_profit_loss, 9.00)
        self.assertEqual(position.unrealized_profit_loss_percent, 8.91)

    def test_spot_buy_then_partial_sell_updates_remaining_basis(self) -> None:
        snapshot = _snapshot(
            spot_state={
                "balances": [
                    {
                        "coin": "BTC",
                        "total": "0.1",
                        "usdValue": "120.00",
                    }
                ]
            },
            user_fills=[
                {"time": 1, "coin": "BTC/USDC", "side": "B", "sz": "0.2", "px": "1000"},
                {"time": 2, "coin": "BTC/USDC", "side": "A", "sz": "0.1", "px": "1100"},
            ],
        )

        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        position = portfolio.positions["BTC-SPOT"]

        self.assertEqual(position.cost_basis, 100.00)
        self.assertEqual(position.market_value, 120.00)
        self.assertEqual(position.unrealized_profit_loss, 20.00)
        self.assertEqual(position.unrealized_profit_loss_percent, 20.00)

    def test_spot_direct_deposit_without_trade_history_stays_neutral(self) -> None:
        snapshot = _snapshot(
            spot_state={
                "balances": [
                    {
                        "coin": "BTC",
                        "total": "0.05",
                        "usdValue": "500.00",
                    }
                ]
            }
        )

        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        position = portfolio.positions["BTC-SPOT"]

        self.assertEqual(position.cost_basis, 500.00)
        self.assertEqual(position.market_value, 500.00)
        self.assertEqual(position.unrealized_profit_loss, 0.0)
        self.assertEqual(position.unrealized_profit_loss_percent, 0.0)

    def test_perp_pnl_uses_hyperliquid_unrealized_pnl(self) -> None:
        snapshot = _snapshot(
            clearinghouse_state={
                "marginSummary": {"accountValue": "1000"},
                "assetPositions": [
                    {
                        "position": {
                            "coin": "BTC",
                            "szi": "0.075",
                            "entryPx": "74992",
                            "markPx": "73557",
                            "positionValue": "5516.77",
                            "unrealizedPnl": "107.62",
                        }
                    }
                ],
            }
        )

        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        position = portfolio.positions["BTC-PERP"]

        self.assertEqual(position.market_value, 5516.77)
        self.assertEqual(position.cost_basis, 5624.40)
        self.assertEqual(position.unrealized_profit_loss, 107.62)
        self.assertEqual(position.unrealized_profit_loss_percent, 1.91)


if __name__ == "__main__":
    unittest.main()
