from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.analytics.hyperliquid_perp_chat import (
    HyperliquidPerpPosition,
    account_scenario_table,
    build_hyperliquid_perp_chat_context,
    build_ladder_plan,
    combined_exposure,
    hyperliquid_perp_chat_request_payload,
    perp_fee_amount,
    perp_position_pnl,
    perp_scenario,
    redact_hyperliquid_perp_chat_secrets,
    validate_tpsl_direction,
)
from app.core.portfolio import CashPosition, Portfolio, Position


class _Var:
    def __init__(self, value: object = "") -> None:
        self.value = value

    def get(self) -> object:
        return self.value


class _Broker:
    def __init__(self, portfolio: Portfolio) -> None:
        self._portfolio = portfolio

    def get_portfolio(self) -> Portfolio:
        return self._portfolio


def test_long_perp_pnl_scenario_uses_exact_direction_math() -> None:
    assert perp_position_pnl(50.0, 55.0, 10.0, True) == 50.0
    case = perp_scenario(50.0, 55.0, 10.0, True, leverage=5.0, fee_rate_percent=0.0)

    assert case["gross_pnl"] == 50.0
    assert case["net_pnl"] == 50.0
    assert case["estimated_margin"] == 100.0
    assert case["margin_roi_percent"] == 50.0


def test_short_perp_pnl_scenario_uses_exact_direction_math() -> None:
    assert perp_position_pnl(50.0, 45.0, 10.0, False) == 50.0
    case = perp_scenario(50.0, 45.0, 10.0, False, leverage=5.0, fee_rate_percent=0.0)

    assert case["gross_pnl"] == 50.0
    assert case["net_pnl"] == 50.0
    assert case["directional_move_percent"] == 10.0


def test_fee_aware_net_pnl_subtracts_entry_and_exit_notional_fees() -> None:
    fees = perp_fee_amount(100.0, 110.0, 2.0, 0.05)
    case = perp_scenario(100.0, 110.0, 2.0, True, leverage=10.0, fee_rate_percent=0.05)

    assert round(fees, 4) == 0.21
    assert round(case["gross_pnl"], 4) == 20.0
    assert round(case["net_pnl"], 4) == 19.79


def test_ladder_math_sums_to_intended_size_for_weighted_plan() -> None:
    ladder = build_ladder_plan(
        entry_price=50.0,
        size=100.0,
        is_long=True,
        total_size=60.0,
        levels=(
            {"price": 52.0, "weight": 1},
            {"price": 54.0, "weight": 2},
            {"price": 56.0, "weight": 3},
        ),
        fee_rate_percent=0.0,
        leverage=5.0,
    )

    assert [round(row["close_size"], 6) for row in ladder] == [10.0, 20.0, 30.0]
    assert round(sum(row["close_size"] for row in ladder), 6) == 60.0
    assert ladder[-1]["remaining_size_after"] == 40.0


def test_jeremy_alex_combined_exposure_and_scenario_math() -> None:
    positions = (
        HyperliquidPerpPosition("Jeremy", "HYPE", 335.0, 50.0, 55.0),
        HyperliquidPerpPosition("Alex", "HYPE", -220.0, 58.0, 55.0),
    )

    exposure = combined_exposure(positions, reference_price=60.0)
    rows = account_scenario_table(positions, scenario_prices=(60.0,), fee_rate_percent=0.0)

    assert exposure["net_signed_size"] == 115.0
    assert exposure["gross_long_size"] == 335.0
    assert exposure["gross_short_size"] == 220.0
    assert exposure["net_direction"] == "net long"
    assert rows[0]["accounts"]["Jeremy"]["gross_open_pnl"] == 3350.0
    assert rows[0]["accounts"]["Alex"]["gross_open_pnl"] == -440.0
    assert rows[0]["combined"]["gross_open_pnl"] == 2910.0


def test_tpsl_direction_validation_flags_invalid_long_and_short_fields() -> None:
    long_validation = validate_tpsl_direction(50.0, tp_price=49.0, sl_price=51.0, is_long=True)
    short_validation = validate_tpsl_direction(50.0, tp_price=51.0, sl_price=49.0, is_long=False)

    assert long_validation["tp_valid"] is False
    assert long_validation["sl_valid"] is False
    assert len(long_validation["warnings"]) == 2
    assert short_validation["tp_valid"] is False
    assert short_validation["sl_valid"] is False


def test_secret_redaction_removes_full_wallets_and_keys() -> None:
    redacted = redact_hyperliquid_perp_chat_secrets(
        "wallet=0x1234567890abcdef1234567890ABCDEF12345678 api_secret=sk-test-secret123456789 "
        "private_key=0x" + "a" * 64
    )

    assert "0x1234567890abcdef1234567890ABCDEF12345678" not in redacted
    assert "sk-test-secret" not in redacted
    assert "private_key=[REDACTED]" in redacted
    assert "0x1234...5678" in redacted


def test_context_builder_distinguishes_accounts_open_orders_and_redacts_addresses() -> None:
    portfolio = Portfolio(
        cash=1_000.0,
        positions={
            "HYPE (Jeremy)-PERP": Position("HYPE (Jeremy)-PERP", 335.0, 50.0, 55.0, open_profit_loss=1675.0),
            "HYPE (Alex)-PERP-SHORT": Position("HYPE (Alex)-PERP-SHORT", 220.0, 58.0, 55.0, open_profit_loss=660.0),
        },
        cash_positions={
            "USDC:JEREMY": CashPosition("USDC", 500.0, "Hyperliquid Perps (Jeremy)"),
            "USDC:ALEX": CashPosition("USDC", 700.0, "Hyperliquid Perps (Alex)"),
        },
    )
    app = SimpleNamespace(
        broker=_Broker(portfolio),
        hyperliquid_perp_coin_var=_Var("HYPE"),
        hyperliquid_perp_side_var=_Var("buy"),
        hyperliquid_perp_quantity_var=_Var("10"),
        hyperliquid_perp_limit_price_var=_Var("55"),
        hyperliquid_perp_target_price_var=_Var("60"),
        hyperliquid_perp_stop_price_var=_Var("52"),
        hyperliquid_perp_leverage_var=_Var("5"),
        hyperliquid_perp_fee_rate_var=_Var("0.045"),
        hyperliquid_open_order_by_oid={
            "jeremy:101": {
                "oid": "101",
                "coin": "HYPE",
                "side": "A",
                "sz": "25",
                "limitPx": "60",
                "accountLabel": "Jeremy",
                "accountKey": "jeremy",
                "accountAddress": "0x1234567890abcdef1234567890ABCDEF12345678",
                "reduceOnly": True,
                "tpsl": "tp",
            }
        },
    )

    context = build_hyperliquid_perp_chat_context(app_context=app)
    payload = hyperliquid_perp_chat_request_payload(context, "Summarize.", timeout_seconds=120)
    serialized = json.dumps(payload, sort_keys=True)

    assert {row["account_label"] for row in context.accounts} == {"Jeremy", "Alex"}
    assert context.deterministic_math["combined_exposure"]["net_signed_size"] == 115.0
    assert context.open_orders[0]["account_address_short"] == "0x1234...5678"
    assert "0x1234567890abcdef1234567890ABCDEF12345678" not in serialized
    assert "scenario_table" in context.deterministic_math


def test_hyperliquid_perp_chat_files_do_not_import_or_call_live_execution_hooks() -> None:
    checked_files = [
        Path("app/analytics/hyperliquid_perp_chat.py"),
        Path("app/ui/hyperliquid_perp_chat_window.py"),
    ]
    forbidden = (
        "HyperliquidExecutionAdapter",
        "_show_hyperliquid_perp_live_submit",
        "show_hyperliquid_perp_live_submit",
        "show_hyperliquid_spot_live_submit",
        "place_position_tpsl",
        "_local_signed_submit",
        ".submit(",
        ".cancel(",
        ".modify_order(",
    )

    for path in checked_files:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source


def test_hyperliquid_perp_strategy_chat_button_is_in_perp_actions() -> None:
    source = Path("app/ui/trading_workspace_extension.py").read_text(encoding="utf-8")

    assert 'text="Perp What-If"' in source
    assert 'text="Perp Strategy Chat"' in source
    assert "open_hyperliquid_perp_strategy_chat" in source
