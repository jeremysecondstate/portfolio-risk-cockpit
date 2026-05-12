from __future__ import annotations

from dataclasses import dataclass

from app.config.settings import settings
from app.core.order_models import OrderRequest, OrderSide, OrderType, OrderPreview
from app.core.portfolio import Portfolio


@dataclass(frozen=True)
class RiskLimits:
    max_order_dollars: float = settings.max_order_dollars
    max_position_percent: float = settings.max_position_percent
    require_confirmation_text: str = settings.require_confirmation_text
    allow_margin: bool = False


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()

    def preview(self, portfolio: Portfolio, order: OrderRequest) -> OrderPreview:
        warnings: list[str] = []
        blocked = False

        if not order.symbol:
            warnings.append("Ticker symbol is required.")
            blocked = True

        if order.quantity <= 0:
            warnings.append("Quantity must be greater than zero.")
            blocked = True

        if order.estimated_price <= 0:
            warnings.append("Estimated price must be greater than zero.")
            blocked = True

        if order.order_type == OrderType.MARKET:
            warnings.append("Market orders can fill at a worse price than expected. Consider using a limit order.")

        if order.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT} and not order.limit_price:
            warnings.append("A limit price is required for limit and stop-limit orders.")
            blocked = True

        if order.order_type in {OrderType.STOP, OrderType.STOP_LIMIT} and not order.stop_price:
            warnings.append("A stop price is required for stop and stop-limit orders.")
            blocked = True

        if order.estimated_notional > self.limits.max_order_dollars:
            warnings.append(
                f"Order is about ${order.estimated_notional:,.2f}, above the max order limit "
                f"of ${self.limits.max_order_dollars:,.2f}."
            )
            blocked = True

        current_position = portfolio.get_position(order.symbol)
        current_position_value = current_position.market_value if current_position else 0.0

        if order.side == OrderSide.BUY:
            estimated_cash_after = round(portfolio.cash - order.estimated_notional, 2)
            estimated_position_value_after = round(current_position_value + order.estimated_notional, 2)

            if estimated_cash_after < 0 and not self.limits.allow_margin:
                warnings.append("This buy would exceed available cash. Margin is not allowed in this app.")
                blocked = True
        else:
            estimated_cash_after = round(portfolio.cash + order.estimated_notional, 2)
            estimated_position_value_after = round(current_position_value - order.estimated_notional, 2)

            if current_position is None:
                warnings.append(f"No {order.symbol} position exists in the paper portfolio.")
                blocked = True
            elif order.quantity > current_position.quantity:
                warnings.append(
                    f"Sell quantity exceeds current position. You own {current_position.quantity:g} shares of {order.symbol}."
                )
                blocked = True

        total_value_after = portfolio.total_value
        if order.side == OrderSide.BUY:
            total_value_after = max(portfolio.total_value, 0.01)
        position_percent_after = (max(estimated_position_value_after, 0.0) / total_value_after) * 100

        if order.side == OrderSide.BUY and position_percent_after > self.limits.max_position_percent:
            warnings.append(
                f"Position would be about {position_percent_after:.1f}% of portfolio value, above the "
                f"{self.limits.max_position_percent:.1f}% max-position rule."
            )
            blocked = True

        if order.confirmation_text != self.limits.require_confirmation_text:
            warnings.append(f"Type {self.limits.require_confirmation_text!r} before submitting.")
            blocked = True

        return OrderPreview(
            order=order,
            warnings=warnings,
            blocked=blocked,
            estimated_cash_after=estimated_cash_after,
            estimated_position_value_after=round(max(estimated_position_value_after, 0.0), 2),
        )
