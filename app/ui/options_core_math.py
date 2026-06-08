from __future__ import annotations

from dataclasses import dataclass

CONTRACT_MULTIPLIER = 100


@dataclass(frozen=True)
class OptionCoreMetrics:
    """Fundamental option math for the What-If Trading Workspace.

    Premium values are per share. Dollar values multiply by the standard
    100-share contract multiplier and the user-entered contract count.
    """

    contract_cost: float
    call_breakeven: float
    put_breakeven: float
    intrinsic_value_call: float
    intrinsic_value_put: float
    selected_intrinsic_value: float
    time_value: float
    notional_exposure: float
    max_loss_long_option: float
    max_profit_long_call: float | None
    max_profit_long_put: float


def calculate_core_option_metrics(
    *,
    stock_price: float,
    strike: float,
    premium: float,
    contracts: int,
    option_type: str,
    fees: float = 0.0,
) -> OptionCoreMetrics:
    """Calculate the core long call/put metrics used by the trading workspace."""

    safe_contracts = max(int(contracts), 1)
    contract_cost = premium * CONTRACT_MULTIPLIER * safe_contracts
    intrinsic_value_call = max(stock_price - strike, 0)
    intrinsic_value_put = max(strike - stock_price, 0)
    selected_intrinsic_value = intrinsic_value_call if option_type == "Call" else intrinsic_value_put
    time_value = max(premium - selected_intrinsic_value, 0)

    return OptionCoreMetrics(
        contract_cost=contract_cost,
        call_breakeven=strike + premium,
        put_breakeven=strike - premium,
        intrinsic_value_call=intrinsic_value_call,
        intrinsic_value_put=intrinsic_value_put,
        selected_intrinsic_value=selected_intrinsic_value,
        time_value=time_value,
        notional_exposure=strike * CONTRACT_MULTIPLIER * safe_contracts,
        max_loss_long_option=contract_cost + fees,
        max_profit_long_call=None,
        max_profit_long_put=max((strike - premium) * CONTRACT_MULTIPLIER * safe_contracts, 0),
    )


def format_core_option_math_lines(
    *,
    stock_price: float,
    strike: float,
    premium: float,
    contracts: int,
    metrics: OptionCoreMetrics,
    money_formatter,
) -> list[str]:
    """Return display-ready formula lines for the Scenario Analysis text box."""

    safe_contracts = max(int(contracts), 1)
    return [
        "Core Option Math:",
        f"- Contract cost: {premium:.2f} × 100 × {safe_contracts} = {money_formatter(metrics.contract_cost)}",
        f"- Call breakeven: {strike:.2f} + {premium:.2f} = {money_formatter(metrics.call_breakeven)}",
        f"- Put breakeven: {strike:.2f} - {premium:.2f} = {money_formatter(metrics.put_breakeven)}",
        f"- Intrinsic value, call: max({stock_price:.2f} - {strike:.2f}, 0) = {money_formatter(metrics.intrinsic_value_call)}",
        f"- Intrinsic value, put: max({strike:.2f} - {stock_price:.2f}, 0) = {money_formatter(metrics.intrinsic_value_put)}",
        f"- Time value: {premium:.2f} - {metrics.selected_intrinsic_value:.2f} = {money_formatter(metrics.time_value)}",
        f"- Notional exposure: {strike:.2f} × 100 × {safe_contracts} = {money_formatter(metrics.notional_exposure)}",
        f"- Max loss, long option: premium paid + fees = {money_formatter(metrics.max_loss_long_option)}",
        "- Max profit, long call: theoretically unlimited; depends on stock upside.",
        f"- Max profit, long put: roughly (strike - premium) × 100 × contracts = {money_formatter(metrics.max_profit_long_put)} if the stock went to zero.",
    ]
