from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.portfolio import Portfolio, Position
from app.ui.schwab_sync_report_extension import (
    _apply_quote_day_pnl_overrides,
    _format_quote_day_pnl_report,
)


class DummyQuoteSession:
    def __init__(self, quotes: dict[str, tuple[int, object]], orders: tuple[int, object] | None = None) -> None:
        self.quotes = quotes
        self.orders = orders if orders is not None else (200, [])
        self.quote_calls: list[str] = []
        self.order_call_count = 0

    def get_quote(self, symbol: str) -> tuple[int, object]:
        self.quote_calls.append(symbol)
        return self.quotes[symbol]

    def get_orders(self, *, from_entered_time: datetime, to_entered_time: datetime) -> tuple[int, Any]:
        assert from_entered_time.tzinfo is not None
        assert to_entered_time.tzinfo is not None
        self.order_call_count += 1
        return self.orders


def test_schwab_quote_net_change_overrides_suspect_account_day_pnl() -> None:
    portfolio = Portfolio(
        cash=0.0,
        positions={
            "TXN": Position(
                symbol="TXN",
                quantity=5,
                average_cost=318.354,
                last_price=277.50,
                day_profit_loss=808.25,
                day_profit_loss_percent=58.25,
            )
        },
    )
    setattr(portfolio.positions["TXN"], "asset_type", "EQUITY")
    session = DummyQuoteSession(
        {
            "TXN": (
                200,
                {
                    "TXN": {
                        "quote": {
                            "lastPrice": 277.50,
                            "netChange": 1.616,
                            "netPercentChange": 0.58,
                        }
                    }
                },
            )
        }
    )

    _apply_quote_day_pnl_overrides(session, portfolio)

    position = portfolio.positions["TXN"]
    assert position.day_profit_loss == 8.08
    assert position.day_profit_loss_percent == 0.58
    assert getattr(position, "day_profit_loss_source") == "Schwab quote netChange × quantity"
    assert getattr(portfolio, "schwab_quote_day_pnl_symbols") == ["TXN"]
    assert getattr(portfolio, "schwab_quote_day_pnl_differences") == ["TXN: account $808.25 → quote $8.08"]
    assert "Overrode materially different account-position Day P&L" in _format_quote_day_pnl_report(portfolio)


def test_schwab_quote_day_pnl_adjusts_same_day_buys_to_fill_basis() -> None:
    portfolio = Portfolio(
        cash=0.0,
        positions={
            "MU": Position(
                symbol="MU",
                quantity=3,
                average_cost=898.55,
                last_price=902.52,
                day_profit_loss=-138.56,
                day_profit_loss_percent=-4.88,
            )
        },
    )
    setattr(portfolio.positions["MU"], "asset_type", "EQUITY")
    session = DummyQuoteSession(
        {
            "MU": (
                200,
                {
                    "MU": {
                        "quote": {
                            "lastPrice": 902.52,
                            "netChange": -46.19,
                            "netPercentChange": -4.87,
                        }
                    }
                },
            )
        },
        orders=(
            200,
            [
                {
                    "enteredTime": datetime.now(timezone.utc).isoformat(),
                    "status": "FILLED",
                    "filledQuantity": 3,
                    "orderLegCollection": [
                        {
                            "instruction": "BUY",
                            "quantity": 3,
                            "instrument": {"symbol": "MU", "assetType": "EQUITY"},
                        }
                    ],
                    "orderActivityCollection": [
                        {
                            "executionLegs": [
                                {"quantity": 3, "price": 898.55},
                            ]
                        }
                    ],
                }
            ],
        ),
    )

    _apply_quote_day_pnl_overrides(session, portfolio)

    position = portfolio.positions["MU"]
    assert position.day_profit_loss == 11.91
    assert position.day_profit_loss_percent == 0.44
    assert getattr(position, "day_profit_loss_source") == "Schwab quote netChange × overnight quantity + last-minus-fill for today's buys"
    assert getattr(portfolio, "schwab_quote_day_pnl_same_day_adjustments") == ["MU: 3 share(s) opened today at avg $898.55"]
    assert "Same-day entry adjustment" in _format_quote_day_pnl_report(portfolio)


def test_schwab_quote_day_pnl_blends_overnight_and_same_day_quantities() -> None:
    portfolio = Portfolio(
        cash=0.0,
        positions={
            "GOOG": Position(
                symbol="GOOG",
                quantity=17,
                average_cost=209.44,
                last_price=360.20,
                day_profit_loss=-16.57,
            )
        },
    )
    setattr(portfolio.positions["GOOG"], "asset_type", "EQUITY")
    session = DummyQuoteSession(
        {
            "GOOG": (
                200,
                {
                    "GOOG": {
                        "quote": {
                            "lastPrice": 360.20,
                            "netChange": -0.97,
                        }
                    }
                },
            )
        },
        orders=(
            200,
            [
                {
                    "status": "FILLED",
                    "filledQuantity": 2,
                    "price": 359.50,
                    "orderLegCollection": [
                        {
                            "instruction": "BUY",
                            "quantity": 2,
                            "instrument": {"symbol": "GOOG", "assetType": "EQUITY"},
                        }
                    ],
                }
            ],
        ),
    )

    _apply_quote_day_pnl_overrides(session, portfolio)

    # 15 overnight shares at -0.97 plus 2 same-day shares at 360.20 - 359.50.
    assert portfolio.positions["GOOG"].day_profit_loss == -13.15


def test_schwab_quote_day_pnl_falls_back_when_quote_unavailable() -> None:
    portfolio = Portfolio(
        cash=0.0,
        positions={
            "RDW": Position(
                symbol="RDW",
                quantity=100,
                average_cost=21.4212,
                last_price=15.14,
                day_profit_loss=-343.00,
            )
        },
    )
    setattr(portfolio.positions["RDW"], "asset_type", "EQUITY")
    session = DummyQuoteSession({"RDW": (503, {"error": "temporarily unavailable"})})

    _apply_quote_day_pnl_overrides(session, portfolio)

    assert portfolio.positions["RDW"].day_profit_loss == -343.00
    assert getattr(portfolio, "schwab_quote_day_pnl_symbols") == []
    assert getattr(portfolio, "schwab_quote_day_pnl_fallbacks") == ["RDW"]
    assert "kept Schwab account-position Day P&L" in _format_quote_day_pnl_report(portfolio)


def test_schwab_quote_day_pnl_skips_options() -> None:
    portfolio = Portfolio(
        cash=0.0,
        positions={
            "TXN_OPT": Position(
                symbol="TXN_OPT",
                quantity=1,
                average_cost=1.0,
                last_price=1.5,
                day_profit_loss=50.0,
            )
        },
    )
    setattr(portfolio.positions["TXN_OPT"], "asset_type", "EQUITY OPTION")
    session = DummyQuoteSession({})

    _apply_quote_day_pnl_overrides(session, portfolio)

    assert session.quote_calls == []
    assert portfolio.positions["TXN_OPT"].day_profit_loss == 50.0
