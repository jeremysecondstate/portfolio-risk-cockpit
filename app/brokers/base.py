from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.order_models import OrderRequest, OrderPreview, SubmittedOrder
from app.core.portfolio import Portfolio


class Broker(ABC):
    mode: str

    @abstractmethod
    def get_portfolio(self) -> Portfolio:
        raise NotImplementedError

    @abstractmethod
    def preview_order(self, order: OrderRequest) -> OrderPreview:
        raise NotImplementedError

    @abstractmethod
    def submit_order(self, order: OrderRequest) -> SubmittedOrder:
        raise NotImplementedError
