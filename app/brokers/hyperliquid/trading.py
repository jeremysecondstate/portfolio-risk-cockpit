from __future__ import annotations

from dataclasses import dataclass
import os


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


class HyperliquidTradingConfig:
    """Local environment readiness for Hyperliquid ticket workflow."""

    def __init__(self) -> None:
        self.wallet_address = os.getenv("HYPE_WALLET_ADDRESS", "").strip()
        self.api_address = os.getenv("HYPE_API_ADDRESS", "").strip()
        self.has_signing_secret = bool(os.getenv("HYPE_API_SECRET", "").strip())
        self.live_enabled = os.getenv("HYPERLIQUID_ENABLE_LIVE_ORDERS", "").strip().lower() == "true"

    def validation_lines(self) -> list[str]:
        lines: list[str] = []
        lines.append(_gate("HYPE_WALLET_ADDRESS", self.wallet_address.startswith("0x") and len(self.wallet_address) == 42))
        lines.append(_gate("HYPE_API_ADDRESS", self.api_address.startswith("0x") and len(self.api_address) == 42))
        lines.append(_gate("HYPE_API_SECRET present", self.has_signing_secret))
        lines.append(_gate("HYPERLIQUID_ENABLE_LIVE_ORDERS=true", self.live_enabled))
        return lines

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
                "Safety gates before a future signed submit:",
                "- Keep API credentials only in local .env; never commit or screenshot real secrets.",
                "- Confirm symbol, side, size, price, and reduce-only intent.",
                "- Type exact phrase: PLACE LIVE HYPERLIQUID ORDER.",
                "- Accept the final warning dialog.",
            ]
        )

    def live_disabled_text(self, ticket: HyperliquidOrderTicket, confirmation: str) -> str:
        phrase = "PLACE LIVE HYPERLIQUID ORDER"
        typed_gate = confirmation.strip() == phrase
        return "\n".join(
            [
                "HYPERLIQUID LIVE SUBMIT SAFETY REVIEW",
                "====================================",
                "",
                "Status: LIVE HYPERLIQUID SUBMIT IS NOT WIRED IN THIS BUILD.",
                "No order was submitted.",
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
                _gate(f"Type CONFIRM equals {phrase}", typed_gate),
                "",
                "Next engineering step: wire the signed Hyperliquid SDK submit call behind these gates after reviewing the UI workflow.",
            ]
        )


def normalize_hyperliquid_coin(symbol: str) -> str:
    coin = symbol.strip().upper()
    if coin.startswith("HL:"):
        coin = coin[3:]
    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if coin.endswith(suffix):
            coin = coin[: -len(suffix)]
    if not coin:
        raise ValueError("Enter a Hyperliquid coin, for example HYPE, BTC, ETH, or SOL.")
    return coin


def _gate(label: str, passed: bool) -> str:
    return f"- {label}: {'PASS' if passed else 'REQUIRED'}"


def _short_address(address: str) -> str:
    if len(address) < 12:
        return address or "--"
    return f"{address[:6]}…{address[-4:]}"
