from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.core.portfolio import Portfolio, Position


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True)
class RiskAlert:
    severity: AlertSeverity
    title: str
    message: str
    symbol: str | None = None


@dataclass(frozen=True)
class RiskAlertLimits:
    max_position_weight_percent: float = 35.0
    watch_position_weight_percent: float = 20.0
    min_cash_percent: float = 5.0
    max_cash_percent: float = 40.0
    position_unrealized_loss_percent: float = -15.0
    position_day_loss_percent: float = -5.0
    portfolio_day_loss_dollars: float = -250.0
    tiny_position_value: float = 25.0
    max_tiny_positions: int = 5


def evaluate_portfolio_risk(
    portfolio: Portfolio,
    limits: RiskAlertLimits | None = None,
) -> list[RiskAlert]:
    """Evaluate deterministic portfolio-level risk alerts.

    These rules intentionally use only current Schwab/account snapshot data so
    the cockpit can provide useful risk feedback before we add quote/history
    services.
    """
    limits = limits or RiskAlertLimits()
    alerts: list[RiskAlert] = []
    total_value = max(portfolio.total_value, 0.01)

    cash_percent = (portfolio.cash / total_value) * 100
    if cash_percent < limits.min_cash_percent:
        alerts.append(
            RiskAlert(
                severity=AlertSeverity.WARNING,
                title="Low cash buffer",
                message=f"Cash is {cash_percent:.1f}% of portfolio value, below the {limits.min_cash_percent:.1f}% buffer.",
            )
        )
    elif cash_percent > limits.max_cash_percent:
        alerts.append(
            RiskAlert(
                severity=AlertSeverity.INFO,
                title="High cash allocation",
                message=f"Cash is {cash_percent:.1f}% of portfolio value, above the {limits.max_cash_percent:.1f}% watch level.",
            )
        )

    day_pnl = portfolio.day_profit_loss
    if day_pnl is not None and day_pnl <= limits.portfolio_day_loss_dollars:
        alerts.append(
            RiskAlert(
                severity=AlertSeverity.WARNING,
                title="Portfolio day loss",
                message=f"Portfolio day P&L is ${day_pnl:,.2f}, below the ${limits.portfolio_day_loss_dollars:,.2f} alert level.",
            )
        )

    tiny_positions = [
        position for position in portfolio.positions.values()
        if abs(position.market_value) < limits.tiny_position_value
    ]
    if len(tiny_positions) > limits.max_tiny_positions:
        alerts.append(
            RiskAlert(
                severity=AlertSeverity.INFO,
                title="Many tiny positions",
                message=(
                    f"{len(tiny_positions)} positions are below ${limits.tiny_position_value:,.2f} market value. "
                    "Consider whether these add noise to the cockpit."
                ),
            )
        )

    for position in sorted(portfolio.positions.values(), key=lambda p: abs(p.market_value), reverse=True):
        alerts.extend(_evaluate_position(position, total_value, limits))

    if not alerts:
        alerts.append(
            RiskAlert(
                severity=AlertSeverity.INFO,
                title="No major snapshot alerts",
                message="Current portfolio snapshot is inside the configured risk thresholds.",
            )
        )

    severity_rank = {
        AlertSeverity.CRITICAL: 0,
        AlertSeverity.WARNING: 1,
        AlertSeverity.INFO: 2,
    }
    return sorted(alerts, key=lambda alert: severity_rank[alert.severity])


def _evaluate_position(
    position: Position,
    total_value: float,
    limits: RiskAlertLimits,
) -> list[RiskAlert]:
    alerts: list[RiskAlert] = []
    weight_percent = (position.market_value / total_value) * 100

    if weight_percent >= limits.max_position_weight_percent:
        alerts.append(
            RiskAlert(
                severity=AlertSeverity.CRITICAL,
                symbol=position.symbol,
                title="Oversized position",
                message=(
                    f"{position.symbol} is {weight_percent:.1f}% of portfolio value, "
                    f"above the {limits.max_position_weight_percent:.1f}% hard limit."
                ),
            )
        )
    elif weight_percent >= limits.watch_position_weight_percent:
        alerts.append(
            RiskAlert(
                severity=AlertSeverity.WARNING,
                symbol=position.symbol,
                title="Position concentration",
                message=(
                    f"{position.symbol} is {weight_percent:.1f}% of portfolio value, "
                    f"above the {limits.watch_position_weight_percent:.1f}% watch level."
                ),
            )
        )

    pnl_percent = position.unrealized_profit_loss_percent
    if pnl_percent is not None and pnl_percent <= limits.position_unrealized_loss_percent:
        alerts.append(
            RiskAlert(
                severity=AlertSeverity.WARNING,
                symbol=position.symbol,
                title="Unrealized loss",
                message=(
                    f"{position.symbol} unrealized P&L is {pnl_percent:.1f}% "
                    f"(${position.unrealized_profit_loss:,.2f})."
                ),
            )
        )

    if position.day_profit_loss_percent is not None and position.day_profit_loss_percent <= limits.position_day_loss_percent:
        alerts.append(
            RiskAlert(
                severity=AlertSeverity.WARNING,
                symbol=position.symbol,
                title="Large day move",
                message=f"{position.symbol} day P&L is {position.day_profit_loss_percent:.1f}%.",
            )
        )

    return alerts
