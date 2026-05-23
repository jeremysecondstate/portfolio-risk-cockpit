from __future__ import annotations

import tkinter as tk
from tkinter import messagebox
from typing import Any, Type
import webbrowser

from app.brokers.plaid.client import PlaidClient
from app.brokers.plaid.investments_adapter import portfolio_from_plaid_holdings
from app.brokers.plaid.token_store import (
    clear_pending_link,
    clear_plaid_token,
    load_pending_link,
    load_plaid_token,
    save_pending_link,
    save_plaid_token,
)


_TOKEN_KEY = "access" + "_token"
_PUBLIC_KEY = "public" + "_token"


def install_plaid_link_flow(app_cls: Type[tk.Tk]) -> None:
    """Make Robinhood sync connect through Plaid Hosted Link when needed."""
    app_cls.refresh_plaid_holdings = _refresh_or_connect_plaid_holdings  # type: ignore[attr-defined]
    app_cls.clear_plaid_connection = _clear_plaid_connection  # type: ignore[attr-defined]


def _refresh_or_connect_plaid_holdings(self: tk.Tk) -> None:
    try:
        cached = load_plaid_token()
        if cached and cached.get(_TOKEN_KEY):
            _refresh_holdings_with_cached_token(self, str(cached[_TOKEN_KEY]))
            return

        pending = load_pending_link()
        if pending and pending.get("link_token"):
            if _try_complete_pending_link(self, str(pending["link_token"])):
                cached_after_exchange = load_plaid_token()
                if cached_after_exchange and cached_after_exchange.get(_TOKEN_KEY):
                    _refresh_holdings_with_cached_token(self, str(cached_after_exchange[_TOKEN_KEY]))
                return

            hosted_url = str(pending.get("hosted_link_url") or "")
            if hosted_url:
                self._set_preview_text(
                    "PLAID LINK STILL PENDING\n"
                    "========================\n\n"
                    "Finish the Robinhood/Plaid browser flow, then click Sync Robinhood again.\n\n"
                    f"Hosted Link URL:\n{hosted_url}"
                )
                try:
                    webbrowser.open(hosted_url)
                except Exception:
                    pass
                return

        _start_hosted_link(self)
    except Exception as exc:
        messagebox.showerror("Plaid / Robinhood sync failed", str(exc))


def _start_hosted_link(self: tk.Tk) -> None:
    client = PlaidClient()
    payload = client.create_hosted_link_token()
    save_pending_link(payload)

    hosted_url = _hosted_link_url(payload)
    if not hosted_url:
        raise RuntimeError("Plaid did not return a hosted_link_url. Hosted Link may not be enabled for this app/environment.")

    self.plaid_status_var.set("Plaid Link opened. Finish Robinhood login, then click Sync Robinhood again.")
    self._set_preview_text(
        "PLAID LINK OPENED\n"
        "=================\n\n"
        "A browser window should open for Plaid Link. Search for Robinhood, connect your account, then return here and click Sync Robinhood again.\n\n"
        "No Robinhood data has been imported yet."
    )
    webbrowser.open(hosted_url)


def _try_complete_pending_link(self: tk.Tk, link_token: str) -> bool:
    client = PlaidClient()
    payload = client.get_link_token(link_token)
    public_token = _find_value(payload, _PUBLIC_KEY)

    if not public_token:
        return False

    exchange_payload = client.exchange_public_token(str(public_token))
    save_plaid_token(exchange_payload)
    clear_pending_link()
    self.plaid_status_var.set("Plaid Link completed. Refreshing Robinhood holdings...")
    return True


def _refresh_holdings_with_cached_token(self: tk.Tk, token: str) -> None:
    client = PlaidClient()
    payload = client.get_investment_holdings(token)
    portfolio, source_message = portfolio_from_plaid_holdings(payload)
    self.plaid_portfolio = portfolio
    self.plaid_source_message = source_message
    self.plaid_status_var.set(f"Plaid: {len(portfolio.positions)} positions · ${portfolio.total_value:,.2f}")
    self._set_preview_text(_format_plaid_report(portfolio, source_message))


def _clear_plaid_connection(self: tk.Tk) -> None:
    clear_plaid_token()
    clear_pending_link()
    self.plaid_portfolio = None
    self.plaid_source_message = "Plaid: not connected"
    self.plaid_status_var.set(self.plaid_source_message)


def _hosted_link_url(payload: dict[str, Any]) -> str | None:
    direct = payload.get("hosted_link_url")
    if direct:
        return str(direct)
    hosted = payload.get("hosted_link")
    if isinstance(hosted, dict) and hosted.get("hosted_link_url"):
        return str(hosted["hosted_link_url"])
    return None


def _find_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_value(child, key)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_value(child, key)
            if found:
                return found
    return None


def _format_plaid_report(portfolio, source_message: str) -> str:
    lines = [
        "PLAID / ROBINHOOD HOLDINGS",
        "==========================",
        "",
        source_message,
        f"Cash estimate: ${portfolio.cash:,.2f}",
        f"Positions value: ${portfolio.positions_value:,.2f}",
        f"Total value: ${portfolio.total_value:,.2f}",
        "",
        "Positions:",
    ]
    if not portfolio.positions:
        lines.append("- None returned.")
    else:
        for symbol in sorted(portfolio.positions):
            position = portfolio.positions[symbol]
            lines.append(f"- {symbol}: {position.quantity:g} @ ${position.last_price:,.2f} = ${position.market_value:,.2f}")
    lines.extend(["", "Read-only import only. No Robinhood or Schwab order placement happens here."])
    return "\n".join(lines)
