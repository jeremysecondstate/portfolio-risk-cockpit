from __future__ import annotations

from app.brokers.base import Broker
from app.core.audit_log import AuditLog
from app.core.order_models import OrderRequest, OrderPreview, SubmittedOrder, OrderSide
from app.core.portfolio import Portfolio
from app.core.portfolio_io import load_portfolio_snapshot
from app.core.risk_engine import RiskEngine


class PaperBroker(Broker):
    """Safe local broker simulator.

    This never talks to a real brokerage account. It lets us test the UI,
    risk checks, confirmations, and audit logs before any live integration.
    """

    mode = "paper"

    def __init__(self, portfolio: Portfolio | None = None) -> None:
        if portfolio is None:
            portfolio, source_message = load_portfolio_snapshot()
        else:
            source_message = "Loaded explicit in-memory portfolio"

        self._portfolio = portfolio
        self.source_message = source_message
        self._risk_engine = RiskEngine()
        self._audit_log = AuditLog()

    def get_portfolio(self) -> Portfolio:
        return self._portfolio

    def set_portfolio(self, portfolio: Portfolio, source_message: str) -> None:
        """Replace the simulator state with a freshly loaded planning portfolio."""
        self._portfolio = portfolio
        self.source_message = source_message

    def reload_portfolio_snapshot(self) -> None:
        self._portfolio, self.source_message = load_portfolio_snapshot()

    def preview_order(self, order: OrderRequest) -> OrderPreview:
        return self._risk_engine.preview(self._portfolio, order)

    def submit_order(self, order: OrderRequest) -> SubmittedOrder:
        preview = self.preview_order(order)
        if preview.blocked:
            raise ValueError("Order blocked by risk engine: " + " | ".join(preview.warnings))

        signed_quantity = order.quantity if order.side == OrderSide.BUY else -order.quantity
        cash_delta = order.estimated_notional if order.side == OrderSide.SELL else -order.estimated_notional

        self._portfolio.cash = round(self._portfolio.cash + cash_delta, 2)
        self._portfolio.upsert_position(order.symbol, signed_quantity, order.estimated_price)

        submitted = SubmittedOrder.create(order=order, broker_mode=self.mode)
        self._audit_log.append(submitted)
        return submitted
