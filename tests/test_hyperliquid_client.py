from __future__ import annotations

from datetime import datetime
import unittest

from app.brokers.hyperliquid.client import (
    HyperliquidSnapshot,
    build_custom_hyperliquid_pnl,
    format_hyperliquid_snapshot,
    portfolio_from_hyperliquid_snapshot,
)


def _snapshot(
    *,
    clearinghouse_state: dict | None = None,
    spot_state: dict | None = None,
    all_mids: dict | None = None,
    user_fills: list[dict] | None = None,
    user_non_funding_ledger_updates: list[dict] | None = None,
    user_funding: list[dict] | None = None,
    historical_prices: dict[tuple[str, int], float] | None = None,
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
        user_non_funding_ledger_updates=user_non_funding_ledger_updates or [],
        user_funding=user_funding or [],
        historical_prices=historical_prices or {},
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
        self.assertIsNone(position.unrealized_profit_loss_percent)
        self.assertFalse(position.unrealized_profit_loss_known)
        self.assertTrue(position.cost_basis_estimated)
        self.assertIn("entry --", report)
        self.assertIn("P&L -- (--)", report)

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
        self.assertIsNone(position.unrealized_profit_loss_percent)
        self.assertFalse(position.unrealized_profit_loss_known)

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

    def test_btc_deposit_builds_custom_basis_lot(self) -> None:
        snapshot = _snapshot(
            spot_state={"balances": [{"coin": "BTC", "total": "0.1", "usdValue": "120.00"}]},
            user_non_funding_ledger_updates=[
                {
                    "time": 10,
                    "delta": {
                        "type": "spotTransfer",
                        "token": "BTC",
                        "amount": "0.1",
                        "usdcValue": "100.00",
                        "user": "0x1111111111111111111111111111111111111111",
                        "destination": "0x0000000000000000000000000000000000000000",
                    },
                }
            ],
        )

        custom = build_custom_hyperliquid_pnl(snapshot)
        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        position = portfolio.positions["BTC-SPOT"]

        self.assertEqual(custom.coins["BTC"].deposit_basis_usd, 100.00)
        self.assertEqual(custom.coins["BTC"].unrealized_pnl, 20.00)
        self.assertEqual(custom.coins["BTC"].total_pnl, 20.00)
        self.assertEqual(custom.coins["BTC"].basis_status, "Exact from full history")
        self.assertEqual(position.custom_profit_loss, 20.00)
        self.assertEqual(position.basis_status, "Exact from full history")

    def test_btc_sale_consumes_deposit_lot_fifo(self) -> None:
        snapshot = _snapshot(
            spot_state={"balances": [{"coin": "BTC", "total": "0.05", "usdValue": "75.00"}]},
            user_non_funding_ledger_updates=[
                {
                    "time": 10,
                    "delta": {
                        "type": "spotTransfer",
                        "token": "BTC",
                        "amount": "0.1",
                        "usdcValue": "100.00",
                        "user": "0x1111111111111111111111111111111111111111",
                        "destination": "0x0000000000000000000000000000000000000000",
                    },
                }
            ],
            user_fills=[
                {
                    "time": 20,
                    "coin": "BTC/USDC",
                    "side": "A",
                    "sz": "0.05",
                    "px": "1200",
                    "fee": "1",
                    "feeToken": "USDC",
                }
            ],
        )

        custom = build_custom_hyperliquid_pnl(snapshot)
        btc = custom.coins["BTC"]

        self.assertEqual(btc.realized_pnl, 9.00)
        self.assertEqual(btc.unrealized_pnl, 25.00)
        self.assertEqual(btc.total_pnl, 34.00)
        self.assertEqual(btc.remaining_cost_basis_usd, 50.00)
        self.assertEqual(btc.sale_proceeds_usd, 60.00)
        self.assertEqual(btc.fees_usd, 1.00)

    def test_plain_sell_direction_is_treated_as_spot_fill(self) -> None:
        snapshot = _snapshot(
            spot_state={"balances": [{"coin": "BTC", "total": "0.05", "usdValue": "75.00"}]},
            user_non_funding_ledger_updates=[
                {
                    "time": 10,
                    "delta": {
                        "type": "spotTransfer",
                        "token": "BTC",
                        "amount": "0.1",
                        "usdcValue": "100.00",
                        "user": "0x1111111111111111111111111111111111111111",
                        "destination": "0x0000000000000000000000000000000000000000",
                    },
                }
            ],
            user_fills=[
                {
                    "time": 20,
                    "coin": "BTC",
                    "dir": "Sell",
                    "side": "A",
                    "sz": "0.05",
                    "px": "1200",
                }
            ],
        )

        custom = build_custom_hyperliquid_pnl(snapshot)
        btc = custom.coins["BTC"]

        self.assertEqual(btc.basis_status, "Exact from full history")
        self.assertEqual(btc.sale_proceeds_usd, 60.00)
        self.assertEqual(btc.total_pnl, 35.00)

    def test_missing_disposition_shows_partial_pnl_instead_of_incomplete(self) -> None:
        snapshot = _snapshot(
            spot_state={"balances": [{"coin": "BTC", "total": "0.05", "usdValue": "75.00"}]},
            user_non_funding_ledger_updates=[
                {
                    "time": 10,
                    "delta": {
                        "type": "spotTransfer",
                        "token": "BTC",
                        "amount": "0.1",
                        "usdcValue": "100.00",
                        "user": "0x1111111111111111111111111111111111111111",
                        "destination": "0x0000000000000000000000000000000000000000",
                    },
                }
            ],
        )

        custom = build_custom_hyperliquid_pnl(snapshot)
        btc = custom.coins["BTC"]

        self.assertEqual(btc.basis_status, "Partial history")
        self.assertEqual(btc.total_pnl, -25.00)
        self.assertNotIn("missing disposition", btc.basis_status.lower())

    def test_hype_missing_basis_is_incomplete_not_zero(self) -> None:
        snapshot = _snapshot(
            spot_state={"balances": [{"coin": "HYPE", "total": "70", "usdValue": "5100.00"}]},
        )

        custom = build_custom_hyperliquid_pnl(snapshot)
        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        position = portfolio.positions["HYPE-SPOT"]

        self.assertIsNone(custom.coins["HYPE"].total_pnl)
        self.assertIsNone(custom.net_custom_pnl)
        self.assertEqual(custom.coins["HYPE"].basis_status, "History unavailable")
        self.assertIsNone(position.custom_profit_loss)
        self.assertEqual(position.custom_pnl_status, "History unavailable")

    def test_hype_perp_close_is_not_mixed_into_spot_custom_pnl(self) -> None:
        snapshot = _snapshot(
            spot_state={"balances": [{"coin": "HYPE", "total": "1", "usdValue": "100.00"}]},
            user_non_funding_ledger_updates=[
                {
                    "time": 10,
                    "delta": {
                        "type": "spotTransfer",
                        "token": "HYPE",
                        "amount": "1",
                        "usdcValue": "80.00",
                        "user": "0x1111111111111111111111111111111111111111",
                        "destination": "0x0000000000000000000000000000000000000000",
                    },
                }
            ],
            user_fills=[
                {
                    "time": 20,
                    "coin": "HYPE",
                    "dir": "Close Short",
                    "side": "B",
                    "sz": "1",
                    "px": "72",
                    "closedPnl": "12.34",
                }
            ],
        )

        custom = build_custom_hyperliquid_pnl(snapshot)

        self.assertEqual(custom.coins["HYPE"].total_pnl, 20.00)
        self.assertEqual(custom.coins["HYPE"].realized_pnl, 0.00)
        self.assertEqual(custom.perp_realized_pnl, 12.34)

    def test_account_custom_pnl_uses_current_value_with_deposits_and_withdrawals(self) -> None:
        snapshot = _snapshot(
            clearinghouse_state={"marginSummary": {"accountValue": "25.00"}},
            spot_state={"balances": [{"coin": "USDC", "total": "10"}, {"coin": "BTC", "total": "0.1", "usdValue": "120.00"}]},
            user_non_funding_ledger_updates=[
                {"time": 1, "delta": {"type": "deposit", "usdc": "100"}},
                {"time": 2, "delta": {"type": "withdraw", "usdc": "5", "fee": "1"}},
                {
                    "time": 3,
                    "delta": {
                        "type": "spotTransfer",
                        "token": "BTC",
                        "amount": "0.1",
                        "usdcValue": "100.00",
                        "user": "0x1111111111111111111111111111111111111111",
                        "destination": "0x0000000000000000000000000000000000000000",
                    },
                },
            ],
        )

        custom = build_custom_hyperliquid_pnl(snapshot)

        self.assertEqual(custom.current_account_value, 155.00)
        self.assertEqual(custom.deposits_usd, 200.00)
        self.assertEqual(custom.withdrawals_usd, 5.00)
        self.assertEqual(custom.net_custom_pnl, -40.00)

    def test_report_includes_custom_breakdown_sections(self) -> None:
        snapshot = _snapshot(
            spot_state={"balances": [{"coin": "BTC", "total": "0.1", "usdValue": "120.00"}]},
            user_non_funding_ledger_updates=[
                {
                    "time": 10,
                    "delta": {
                        "type": "spotTransfer",
                        "token": "BTC",
                        "amount": "0.1",
                        "usdcValue": "100.00",
                        "user": "0x1111111111111111111111111111111111111111",
                        "destination": "0x0000000000000000000000000000000000000000",
                    },
                }
            ],
        )

        portfolio, _message = portfolio_from_hyperliquid_snapshot(snapshot)
        report = format_hyperliquid_snapshot(snapshot, portfolio)

        self.assertIn("CUSTOM HYPERLIQUID P&L", report)
        self.assertIn("Account:", report)
        self.assertIn("BTC deposit basis: $100.00", report)
        self.assertIn("Perps:", report)


if __name__ == "__main__":
    unittest.main()
