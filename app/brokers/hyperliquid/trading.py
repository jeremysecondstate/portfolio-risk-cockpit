from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any


@dataclass(frozen=True)
class HyperliquidOrderTicket:
    coin: str
    is_buy: bool
    size: float
    limit_price: float
    tif: str
    reduce_only: bool = False

    @property
    def notional(self) -> float:
        return round(self.size * self.limit_price, 2)

    @property
    def side_label(self) -> str:
        return "BUY" if self.is_buy else "SELL"

    def order_type_payload(self) -> dict[str, Any]:
        return {"limit": {"tif": self.tif}}


class HyperliquidTradingConfig:
    """Local environment readiness for Hyperliquid ticket workflow."""

    def __init__(self) -> None:
        self.wallet_address = os.getenv("HYPE_WALLET_ADDRESS", "").strip()
        self.api_address = os.getenv("HYPE_API_ADDRESS", "").strip()
        self.has_signing_secret = bool(os.getenv("HYPE_API_SECRET", "").strip())
        self.live_enabled = os.getenv("HYPERLIQUID_ENABLE_LIVE_ORDERS", "").strip().lower() == "true"
        self.max_live_notional = _float_env("HYPERLIQUID_MAX_LIVE_ORDER_DOLLARS", 500.0)

    def validation_lines(self) -> list[str]:
        lines: list[str] = []
        lines.append(_gate("HYPE_WALLET_ADDRESS", self.wallet_address.startswith("0x") and len(self.wallet_address) == 42))
        lines.append(_gate("HYPE_API_ADDRESS", self.api_address.startswith("0x") and len(self.api_address) == 42))
        lines.append(_gate("HYPE_API_SECRET present", self.has_signing_secret))
        lines.append(_gate("HYPERLIQUID_ENABLE_LIVE_ORDERS=true", self.live_enabled))
        lines.append(_gate(f"Max notional <= ${self.max_live_notional:,.2f}", True))
        return lines

    def validate_for_live(self, ticket: HyperliquidOrderTicket) -> None:
        if not self.wallet_address.startswith("0x") or len(self.wallet_address) != 42:
            raise ValueError("HYPE_WALLET_ADDRESS must be the 42-character Hyperliquid master/sub-account address.")
        if not self.api_address.startswith("0x") or len(self.api_address) != 42:
            raise ValueError("HYPE_API_ADDRESS must be the 42-character Hyperliquid API wallet address.")
        if not self.has_signing_secret:
            raise ValueError("HYPE_API_SECRET is missing from local .env.")
        if not self.live_enabled:
            raise PermissionError("Set HYPERLIQUID_ENABLE_LIVE_ORDERS=true in .env before live Hyperliquid submit.")
        if ticket.size <= 0:
            raise ValueError("Hyperliquid size must be positive.")
        if ticket.limit_price <= 0:
            raise ValueError("Hyperliquid limit price must be positive.")
        if ticket.notional > self.max_live_notional:
            raise PermissionError(
                f"Estimated notional ${ticket.notional:,.2f} exceeds "
                f"HYPERLIQUID_MAX_LIVE_ORDER_DOLLARS=${self.max_live_notional:,.2f}."
            )

    def preview_text(self, ticket: HyperliquidOrderTicket) -> str:
        return "\n".join(
            [
                "HYPERLIQUID TICKET PREVIEW",
                "==========================",
                "",
                "No Hyperliquid order was submitted. This is a local readiness preview.",
                "",
                f"Wallet / account: {_short_address(self.wallet_address)}",
                f"API wallet: {_short_address(self.api_address)}",
                f"Coin: {ticket.coin}",
                f"Side: {ticket.side_label}",
                f"Size: {ticket.size:g}",
                f"Limit price: ${ticket.limit_price:,.4f}",
                f"Estimated notional: ${ticket.notional:,.2f}",
                f"Time in force: {ticket.tif}",
                f"Reduce only: {'yes' if ticket.reduce_only else 'no'}",
                "",
                "Environment readiness:",
                *self.validation_lines(),
                "",
                "Fast live path:",
                "- Set HYPERLIQUID_ENABLE_LIVE_ORDERS=true in local .env.",
                "- Keep HYPE_API_SECRET local only; never commit real secrets.",
                "- LIVE Submit will run env/notional checks and then call the local submit hook.",
            ]
        )

    def live_review_text(self, ticket: HyperliquidOrderTicket) -> str:
        try:
            self.validate_for_live(ticket)
            status = "READY — local submit hook can be called."
        except Exception as exc:
            status = f"BLOCKED — {exc}"

        return "\n".join(
            [
                "HYPERLIQUID LIVE SUBMIT",
                "=======================",
                "",
                status,
                "",
                f"Coin: {ticket.coin}",
                f"Side: {ticket.side_label}",
                f"Size: {ticket.size:g}",
                f"Limit price: ${ticket.limit_price:,.4f}",
                f"Estimated notional: ${ticket.notional:,.2f}",
                f"Reduce only: {'yes' if ticket.reduce_only else 'no'}",
                "",
                "Current gates:",
                *self.validation_lines(),
            ]
        )


class HyperliquidExecutionAdapter:
    """Fast guarded execution adapter with one local SDK hook."""

    def submit(self, ticket: HyperliquidOrderTicket) -> Any:
        config = HyperliquidTradingConfig()
        config.validate_for_live(ticket)
        return self._local_signed_submit(ticket)

    def _local_signed_submit(self, ticket: HyperliquidOrderTicket) -> Any:
        """Wire the local Hyperliquid SDK signed order call here.

        The official SDK's basic order example uses this shape:

            exchange.order("ETH", True, 0.2, 1100, {"limit": {"tif": "Gtc"}})

        Your local hook maps directly to:

            exchange.order(
                ticket.coin,
                ticket.is_buy,
                ticket.size,
                ticket.limit_price,
                ticket.order_type_payload(),
                reduce_only=ticket.reduce_only,
            )

        Configure the SDK with:
        - HYPE_WALLET_ADDRESS as the main/sub-account address
        - HYPE_API_ADDRESS as the API wallet address
        - HYPE_API_SECRET as the API wallet private key
        """
        raise NotImplementedError(
            "Local signed Hyperliquid submit is not wired yet. "
            "Wire HyperliquidExecutionAdapter._local_signed_submit() on your machine."
        )


def normalize_hyperliquid_coin(symbol: str) -> str:
    coin = symbol.strip().upper()
    if coin.startswith("HL:"):
        coin = coin[3:]
    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if coin.endswith(suffix):
            coin = coin[: -len(suffix)]
    if coin == "UBTC":
        coin = "BTC"
    if not coin:
        raise ValueError("Enter a Hyperliquid coin, for example HYPE, BTC, ETH, or SOL.")
    return coin


def _gate(label: str, passed: bool) -> str:
    return f"- {label}: {'PASS' if passed else 'REQUIRED'}"


def _short_address(address: str) -> str:
    if len(address) < 12:
        return address or "--"
    return f"{address[:6]}…{address[-4:]}"


def _float_env(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).strip())
    except ValueError:
        return default
