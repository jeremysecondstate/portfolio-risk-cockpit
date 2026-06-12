from __future__ import annotations

from dataclasses import replace
import os
import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import NamedTuple, Type

from app.analytics.hyperliquid_assessment import format_hyperliquid_position_assessment
from app.brokers.hyperliquid.client import (
    HyperliquidInfoClient,
    HyperliquidSnapshot,
    format_hyperliquid_snapshot,
    portfolio_from_hyperliquid_snapshot,
)
from app.core.portfolio import CashPosition, Portfolio, Position


class HyperliquidAccountTarget(NamedTuple):
    label: str
    address: str


# Jeremy keeps the original single-account env names so existing .env files keep working.
# Alex can be added with the account-specific address without changing the UI layout.
HYPERLIQUID_ACCOUNT_ENV_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Jeremy", ("HYPE_WALLET_ADDRESS_JEREMY_SECONDSTATE", "HYPE_WALLET_ADDRESS")),
    ("Alex", ("HYPE_WALLET_ADDRESS_ALEX_SECONDSTATE", "HYPE_ALEX_WALLET_ADDRESS")),
)
HYPERLIQUID_ADDRESS_ENV_KEYS = tuple(
    key
    for _label, keys in HYPERLIQUID_ACCOUNT_ENV_GROUPS
    for key in keys
)
_PLACEHOLDER_ENV_VALUES = {"key in here", "changeme", "todo", "none", "null"}


def install_hyperliquid_assessment_extension(app_cls: Type[tk.Tk]) -> None:
    """Append a position/risk assessment to every Hyperliquid account sync."""

    app_cls.sync_hyperliquid_account = _sync_hyperliquid_account_with_assessment  # type: ignore[method-assign]


def _clean_env_value(value: str) -> str:
    cleaned = value.strip().strip("'\"")
    return "" if cleaned.lower() in _PLACEHOLDER_ENV_VALUES else cleaned


def _hyperliquid_accounts_from_env() -> list[HyperliquidAccountTarget]:
    accounts: list[HyperliquidAccountTarget] = []
    seen_addresses: set[str] = set()
    for label, keys in HYPERLIQUID_ACCOUNT_ENV_GROUPS:
        for key in keys:
            address = _clean_env_value(os.getenv(key, ""))
            if not address:
                continue
            normalized = address.lower()
            if normalized not in seen_addresses:
                accounts.append(HyperliquidAccountTarget(label, address))
                seen_addresses.add(normalized)
            break
    return accounts


def _hyperliquid_address_from_env() -> str:
    accounts = _hyperliquid_accounts_from_env()
    return accounts[0].address if accounts else ""


def _prompt_for_hyperliquid_account() -> HyperliquidAccountTarget | None:
    address = simpledialog.askstring(
        "Hyperliquid Sync",
        "Enter your Hyperliquid master/sub-account wallet address.\n\n"
        "Tip: save HYPE_WALLET_ADDRESS_JEREMY_SECONDSTATE=0x... and "
        "HYPE_WALLET_ADDRESS_ALEX_SECONDSTATE=0x... in .env to sync both accounts.\n\n"
        "Use the account address, not the API/agent wallet address.",
    )
    if not address:
        return None
    return HyperliquidAccountTarget("Manual", address)


def _sync_hyperliquid_account_with_assessment(self: tk.Tk) -> None:
    """Read one or more Hyperliquid accounts, merge them, and show exposure assessment.

    If account-specific env vars are present, sync each configured account into the
    same read-only balances table. Existing single-account env vars still work.
    """

    accounts = _hyperliquid_accounts_from_env()
    if not accounts:
        prompted = _prompt_for_hyperliquid_account()
        if prompted is None:
            return
        accounts = [prompted]

    try:
        client = HyperliquidInfoClient()
        labeled_portfolios: list[Portfolio] = []
        source_messages: list[str] = []
        reports: list[str] = []
        primary_open_orders: list[dict[str, object]] | None = None

        for account in accounts:
            snapshot = client.fetch_snapshot(account.address)
            if primary_open_orders is None:
                primary_open_orders = snapshot.open_orders

            raw_portfolio, hyperliquid_source_message = portfolio_from_hyperliquid_snapshot(snapshot)
            labeled_portfolio = _account_labeled_portfolio(raw_portfolio, account.label)
            labeled_portfolios.append(labeled_portfolio)
            source_messages.append(_account_source_message(hyperliquid_source_message, account.label))

            raw_report = format_hyperliquid_snapshot(snapshot, raw_portfolio)
            assessment = format_hyperliquid_position_assessment(snapshot, raw_portfolio)
            reports.append(_account_report(account, snapshot, raw_report, assessment))

        orders_table = getattr(self, "hyperliquid_workspace_open_orders_table", None)
        if orders_table is not None:
            try:
                from app.ui.trading_workspace_extension import _populate_workspace_open_orders_table

                # Keep order actions pointed at the primary account until signed multi-account
                # routing is added. Balances/positions below are still synced for every account.
                _populate_workspace_open_orders_table(orders_table, primary_open_orders or [])
            except Exception:
                pass

        hyperliquid_portfolio = _combine_hyperliquid_portfolios(labeled_portfolios)
        merged_portfolio = self._merge_hyperliquid_portfolio(hyperliquid_portfolio)

        base_source_message = _base_source_message_without_hyperliquid(self.broker.source_message)
        source_message = f"{base_source_message} + {' + '.join(source_messages)}"
        self.broker.set_portfolio(merged_portfolio, source_message)
        self.current_portfolio = merged_portfolio
        self.last_hyperliquid_cash_adjustment = hyperliquid_portfolio.cash
        self.refresh_portfolio()

        if hasattr(self, "hyperliquid_status_var"):
            self.hyperliquid_status_var.set("Hyperliquid: synced")

        self._set_preview_text("\n\n".join(reports))
    except Exception as exc:
        if hasattr(self, "hyperliquid_status_var"):
            self.hyperliquid_status_var.set("Hyperliquid: sync failed")
        messagebox.showerror("Hyperliquid sync failed", str(exc))


def _account_labeled_portfolio(portfolio: Portfolio, label: str) -> Portfolio:
    positions: dict[str, Position] = {}
    for symbol, position in portfolio.positions.items():
        labeled_symbol = _account_labeled_symbol(symbol, label)
        labeled_position = replace(position, symbol=labeled_symbol)
        setattr(labeled_position, "hyperliquid_account_label", label)
        positions[labeled_symbol] = labeled_position

    cash_positions: dict[str, CashPosition] = {}
    for key, cash in portfolio.cash_positions.items():
        labeled_key = f"{key}:{_account_key_label(label)}"
        cash_positions[labeled_key] = replace(cash, source=_account_labeled_cash_source(cash.source, label))

    return Portfolio(
        cash=portfolio.cash,
        positions=positions,
        cash_positions=cash_positions,
    )


def _account_labeled_symbol(symbol: str, label: str) -> str:
    clean = symbol.strip().upper()
    suffixes = ("-PERP-SHORT", "-PERP", "-SPOT")
    for suffix in suffixes:
        if clean.endswith(suffix):
            base = clean[: -len(suffix)]
            return f"{base} ({label}){suffix}"
    return f"{clean} ({label})"


def _account_labeled_cash_source(source: str, label: str) -> str:
    clean = source.strip() or "Hyperliquid"
    if "hyperliquid" not in clean.lower():
        clean = f"Hyperliquid {clean}".strip()
    if f"({label})" in clean:
        return clean
    return f"{clean} ({label})"


def _account_key_label(label: str) -> str:
    return "".join(ch for ch in label.upper() if ch.isalnum()) or "ACCOUNT"


def _combine_hyperliquid_portfolios(portfolios: list[Portfolio]) -> Portfolio:
    positions: dict[str, Position] = {}
    cash_positions: dict[str, CashPosition] = {}
    cash = 0.0
    for portfolio in portfolios:
        cash += float(portfolio.cash or 0.0)
        positions.update(portfolio.positions)
        cash_positions.update(portfolio.cash_positions)
    return Portfolio(
        cash=round(cash, 2),
        positions=positions,
        cash_positions=cash_positions,
    )


def _account_source_message(source_message: str, label: str) -> str:
    prefix = "Loaded Hyperliquid account "
    if source_message.startswith(prefix):
        return source_message.replace(prefix, f"Loaded Hyperliquid account {label} ", 1)
    return f"Loaded Hyperliquid account {label}: {source_message}"


def _base_source_message_without_hyperliquid(source_message: str) -> str:
    base = source_message
    for marker in (" + Loaded Hyperliquid account ", " + Loaded Hyperliquid accounts "):
        if marker in base:
            base = base.split(marker, 1)[0]
    return base


def _account_report(
    account: HyperliquidAccountTarget,
    snapshot: HyperliquidSnapshot,
    raw_report: str,
    assessment: str,
) -> str:
    return (
        f"HYPERLIQUID ACCOUNT ({account.label.upper()})\n"
        f"{'=' * (22 + len(account.label))}\n\n"
        f"Read-only wallet: {snapshot.user[:6]}...{snapshot.user[-4:]}\n\n"
        f"{raw_report}\n\n{assessment}"
    )
