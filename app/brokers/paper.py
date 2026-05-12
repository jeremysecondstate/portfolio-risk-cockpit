from __future__ import annotations

from app.core.audit_log import AuditLog
from app.core.order_models import OrderRequest, OrderPreview, SubmittedOrder, OrderSide
from app.core.portfolio import Portfolio, current_foundation_portfolio
from app.core.risk_engine import RiskEngine
from app.brokers.base import Broker


class PaperBroker(Broker):
    """Safe local broker simulator.

    This never talks to a real brokerage account. It lets us test the UI,
    risk checks, confirmations, and audit logs before any live integration.
    """

    mode = "paper"

    def __init__(self, portfolio: Portfolio | None = None) -> None:
        self._portfolio = portfolio or current_foundation_portfolio()
        self._risk_engine = RiskEngine()
        self._audit_log = AuditLog()

    def get_portfolio(self) -> Portfolio:
        return self._portfolio

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
