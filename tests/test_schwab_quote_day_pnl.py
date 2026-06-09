from __future__ import annotations

from app.core.portfolio import Portfolio, Position
from app.ui.schwab_sync_report_extension import (
    _apply_quote_day_pnl_overrides,
    _format_quote_day_pnl_report,
)


class DummyQuoteSession:
    def __init__(self, quotes: dict[str, tuple[int, object]]) -> None:
        self.quotes = quotes
        self.calls: list[str] = []

    def get_quote(self, symbol: str) -> tuple[int, object]:
        self.calls.append(symbol)
        return self.quotes[symbol]


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

    assert session.calls == []
    assert portfolio.positions["TXN_OPT"].day_profit_loss == 50.0
