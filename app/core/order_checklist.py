from __future__ import annotations

from datetime import datetime

from app.core.order_models import OrderPreview, OrderSide, OrderType


def build_manual_order_checklist(preview: OrderPreview) -> str:
    """Create a copy/paste checklist for manually entering the order in Robinhood."""
    order = preview.order
    action = "BUY" if order.side == OrderSide.BUY else "SELL"
    lines = [
        "ROBINHOOD MANUAL ORDER CHECKLIST",
        "=" * 34,
        f"Created: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "This app does NOT place Robinhood trades.",
        "Manually enter this order in Robinhood only after reviewing every line.",
        "",
        f"Action: {action}",
        f"Symbol: {order.symbol}",
        f"Quantity: {order.quantity:g}",
        f"Order type: {order.order_type.value}",
        f"Estimated/reference price: ${order.estimated_price:,.2f}",
    ]

    if order.order_type in {OrderType.LIMIT, OrderType.STOP_LIMIT}:
        lines.append(f"Limit price: ${order.limit_price:,.2f}" if order.limit_price else "Limit price: MISSING")
    if order.order_type in {OrderType.STOP, OrderType.STOP_LIMIT}:
        lines.append(f"Stop price: ${order.stop_price:,.2f}" if order.stop_price else "Stop price: MISSING")

    lines.extend(
        [
            f"Time in force: {order.time_in_force.value}",
            f"Estimated notional: ${order.estimated_notional:,.2f}",
            "",
            "Risk result:",
            "BLOCKED" if preview.blocked else "READY FOR MANUAL ENTRY",
            "",
            "Estimated impact:",
            f"Cash after: ${preview.estimated_cash_after:,.2f}",
            f"Position value after: ${preview.estimated_position_value_after:,.2f}",
            "",
            "Warnings:",
        ]
    )

    if preview.warnings:
        lines.extend(f"- {warning}" for warning in preview.warnings)
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "Manual Robinhood steps:",
            "1. Open Robinhood / Robinhood Legend.",
            f"2. Search {order.symbol}.",
            f"3. Choose {action}.",
            f"4. Set quantity to {order.quantity:g}.",
            f"5. Choose order type: {order.order_type.value}.",
            "6. Enter limit/stop fields exactly as listed above, if applicable.",
            "7. Preview Robinhood's order ticket.",
            "8. Confirm estimated credit/debit and remaining cash.",
            "9. Submit only if it matches this checklist.",
            "",
            "Safety note: Stop orders can fill below the stop price in fast markets; stop-limit orders may not fill.",
        ]
    )
    return "\n".join(lines)
