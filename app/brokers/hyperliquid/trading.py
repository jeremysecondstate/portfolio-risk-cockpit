from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
import os
from typing import Any

SPOT_EXECUTION_ALIASES = {
    "BTC": "UBTC/USDC",
    "UBTC": "UBTC/USDC",
    "ETH": "UETH/USDC",
    "UETH": "UETH/USDC",
    "ZEC": "UZEC/USDC",
    "UZEC": "UZEC/USDC",
}
HYPERLIQUID_MAX_PRICE_SIGNIFICANT_FIGURES = 5
HYPERLIQUID_MAX_SIZE_DECIMALS = 8


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


@dataclass(frozen=True)
class HyperliquidTriggerTicket:
    coin: str
    is_buy: bool
    size: float
    trigger_price: float
    tpsl: str
    is_market: bool = True
    limit_price: float | None = None

    @property
    def wire_limit_price(self) -> float:
        return self.limit_price if self.limit_price is not None else self.trigger_price

    @property
    def side_label(self) -> str:
        return "BUY" if self.is_buy else "SELL"

    @property
    def notional(self) -> float:
        return round(self.size * self.trigger_price, 2)

    @property
    def kind_label(self) -> str:
        return "take profit" if self.tpsl == "tp" else "stop loss"

    def order_type_payload(self) -> dict[str, Any]:
        return {
            "trigger": {
                "triggerPx": self.trigger_price,
                "isMarket": self.is_market,
                "tpsl": self.tpsl,
            }
        }


@dataclass(frozen=True)
class HyperliquidOrderEditTicket:
    coin: str
    is_buy: bool
    size: float
    limit_price: float
    tif: str = "Gtc"
    reduce_only: bool = False
    is_trigger: bool = False
    trigger_price: float | None = None
    trigger_kind: str = "sl"
    is_market_trigger: bool = True
    close_position: bool = False

    @property
    def side_label(self) -> str:
        return "BUY" if self.is_buy else "SELL"

    @property
    def size_label(self) -> str:
        return "Close Position" if self.close_position else f"{self.size:g}"

    @property
    def wire_limit_price(self) -> float:
        if self.is_trigger and self.is_market_trigger and self.trigger_price is not None:
            return self.trigger_price
        return self.limit_price

    @property
    def notional(self) -> float:
        if self.close_position:
            return 0.0
        return round(self.size * self.wire_limit_price, 2)

    def order_type_payload(self) -> dict[str, Any]:
        if self.is_trigger:
            if self.trigger_price is None:
                raise ValueError("Trigger edits require a trigger price.")
            return {
                "trigger": {
                    "triggerPx": self.trigger_price,
                    "isMarket": self.is_market_trigger,
                    "tpsl": self.trigger_kind,
                }
            }
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
        self.validate_for_live_action()
        if ticket.size <= 0:
            raise ValueError("Hyperliquid size must be positive.")
        if ticket.limit_price <= 0:
            raise ValueError("Hyperliquid limit price must be positive.")
        if ticket.notional > self.max_live_notional:
            raise PermissionError(
                f"Estimated notional ${ticket.notional:,.2f} exceeds "
                f"HYPERLIQUID_MAX_LIVE_ORDER_DOLLARS=${self.max_live_notional:,.2f}."
            )
        if not self.wallet_address.startswith("0x") or len(self.wallet_address) != 42:
            raise ValueError("HYPE_WALLET_ADDRESS must be the 42-character Hyperliquid master/sub-account address.")
        if not self.api_address.startswith("0x") or len(self.api_address) != 42:
            raise ValueError("HYPE_API_ADDRESS must be the 42-character Hyperliquid API wallet address.")
        if not self.has_signing_secret:
            raise ValueError("HYPE_API_SECRET is missing from local .env.")
        if not self.live_enabled:
            raise PermissionError("Set HYPERLIQUID_ENABLE_LIVE_ORDERS=true in .env before live Hyperliquid submit.")

    def validate_trigger_for_live(self, ticket: HyperliquidTriggerTicket) -> None:
        self.validate_for_live_action()
        if ticket.size <= 0:
            raise ValueError("Hyperliquid trigger size must be positive.")
        if ticket.trigger_price <= 0:
            raise ValueError("Hyperliquid trigger price must be positive.")
        if ticket.wire_limit_price <= 0:
            raise ValueError("Hyperliquid trigger limit price must be positive.")
        if ticket.tpsl not in {"tp", "sl"}:
            raise ValueError("Hyperliquid trigger must be take-profit or stop-loss.")
        if ticket.notional > self.max_live_notional:
            raise PermissionError(
                f"Estimated trigger notional ${ticket.notional:,.2f} exceeds "
                f"HYPERLIQUID_MAX_LIVE_ORDER_DOLLARS=${self.max_live_notional:,.2f}."
            )

    def validate_edit_for_live(self, ticket: HyperliquidOrderEditTicket) -> None:
        self.validate_for_live_action()
        if not ticket.close_position and ticket.size <= 0:
            raise ValueError("Hyperliquid size must be positive for numeric-size orders.")
        if ticket.close_position and not ticket.is_trigger:
            raise ValueError("Close Position size mode is only supported for trigger/TP-SL orders.")
        if ticket.wire_limit_price <= 0:
            raise ValueError("Hyperliquid price must be positive.")
        if ticket.is_trigger:
            if ticket.trigger_price is None or ticket.trigger_price <= 0:
                raise ValueError("Hyperliquid trigger price must be positive.")
            if ticket.trigger_kind not in {"tp", "sl"}:
                raise ValueError("Hyperliquid trigger must be take-profit or stop-loss.")
        if ticket.notional > self.max_live_notional:
            raise PermissionError(
                f"Estimated edit notional ${ticket.notional:,.2f} exceeds "
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

    def validate_for_live_action(self) -> None:
        if not self.wallet_address.startswith("0x") or len(self.wallet_address) != 42:
            raise ValueError("HYPE_WALLET_ADDRESS must be the 42-character Hyperliquid master/sub-account address.")
        if not self.api_address.startswith("0x") or len(self.api_address) != 42:
            raise ValueError("HYPE_API_ADDRESS must be the 42-character Hyperliquid API wallet address.")
        if not self.has_signing_secret:
            raise ValueError("HYPE_API_SECRET is missing from local .env.")
        if not self.live_enabled:
            raise PermissionError("Set HYPERLIQUID_ENABLE_LIVE_ORDERS=true in .env before live Hyperliquid actions.")


class HyperliquidExecutionAdapter:
    """Fast guarded execution adapter with local SDK hooks."""

    def submit(self, ticket: HyperliquidOrderTicket) -> Any:
        normalized_ticket = normalize_hyperliquid_ticket_limit_price(ticket)
        config = HyperliquidTradingConfig()
        config.validate_for_live(normalized_ticket)
        return self._local_signed_submit(normalized_ticket)

    def cancel(self, coin: str, order_id: int) -> Any:
        config = HyperliquidTradingConfig()
        config.validate_for_live_action()

        normalized_coin = coin.strip()
        if not normalized_coin:
            raise ValueError("Hyperliquid cancel requires a coin/market for the order.")
        if order_id <= 0:
            raise ValueError("Hyperliquid cancel requires a positive order ID.")

        return self._local_signed_cancel(normalized_coin, order_id)

    def modify_order(self, order_id: int, ticket: HyperliquidOrderTicket) -> Any:
        normalized_ticket = normalize_hyperliquid_ticket_limit_price(ticket)
        config = HyperliquidTradingConfig()
        config.validate_for_live(normalized_ticket)
        if order_id <= 0:
            raise ValueError("Hyperliquid edit requires a positive order ID.")
        return self._local_signed_modify(order_id, normalized_ticket)

    def modify_order_edit(self, order_id: int, ticket: HyperliquidOrderEditTicket) -> Any:
        normalized_ticket = normalize_hyperliquid_order_edit_ticket_for_wire(ticket)
        config = HyperliquidTradingConfig()
        config.validate_edit_for_live(normalized_ticket)
        if order_id <= 0:
            raise ValueError("Hyperliquid edit requires a positive order ID.")
        return self._local_signed_modify_edit(order_id, normalized_ticket)

    def place_position_tpsl(self, tickets: list[HyperliquidTriggerTicket]) -> Any:
        if not tickets:
            raise ValueError("Enter at least one TP or SL trigger price.")
        normalized_tickets = [normalize_hyperliquid_trigger_ticket_for_wire(ticket) for ticket in tickets]
        config = HyperliquidTradingConfig()
        for ticket in normalized_tickets:
            config.validate_trigger_for_live(ticket)
        return self._local_signed_position_tpsl(normalized_tickets)

    def update_leverage(self, coin: str, leverage: int, *, is_cross: bool = True) -> Any:
        config = HyperliquidTradingConfig()
        config.validate_for_live_action()
        normalized_coin = normalize_hyperliquid_coin(coin)
        if leverage < 1:
            raise ValueError("Hyperliquid leverage must be at least 1x.")
        if leverage > 100:
            raise ValueError("Hyperliquid leverage must be 100x or lower.")
        return self._local_signed_update_leverage(normalized_coin, leverage, is_cross=is_cross)

    def _local_signed_submit(self, ticket: HyperliquidOrderTicket) -> Any:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        api_secret = os.getenv("HYPE_API_SECRET", "").strip()
        wallet_address = os.getenv("HYPE_WALLET_ADDRESS", "").strip()

        api_wallet = Account.from_key(api_secret)

        exchange = Exchange(
            api_wallet,
            constants.MAINNET_API_URL,
            account_address=wallet_address,
        )
        normalized_ticket = normalize_hyperliquid_ticket_size_for_exchange(ticket, exchange)

        return exchange.order(
            normalized_ticket.coin,
            normalized_ticket.is_buy,
            normalized_ticket.size,
            normalized_ticket.limit_price,
            normalized_ticket.order_type_payload(),
            reduce_only=normalized_ticket.reduce_only,
        )

    def _local_signed_cancel(self, coin: str, order_id: int) -> Any:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        api_secret = os.getenv("HYPE_API_SECRET", "").strip()
        wallet_address = os.getenv("HYPE_WALLET_ADDRESS", "").strip()

        api_wallet = Account.from_key(api_secret)

        exchange = Exchange(
            api_wallet,
            constants.MAINNET_API_URL,
            account_address=wallet_address,
        )

        return exchange.cancel(coin, order_id)

    def _local_signed_modify(self, order_id: int, ticket: HyperliquidOrderTicket) -> Any:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        api_secret = os.getenv("HYPE_API_SECRET", "").strip()
        wallet_address = os.getenv("HYPE_WALLET_ADDRESS", "").strip()

        api_wallet = Account.from_key(api_secret)

        exchange = Exchange(
            api_wallet,
            constants.MAINNET_API_URL,
            account_address=wallet_address,
        )
        normalized_ticket = normalize_hyperliquid_ticket_size_for_exchange(ticket, exchange)

        return exchange.modify_order(
            order_id,
            normalized_ticket.coin,
            normalized_ticket.is_buy,
            normalized_ticket.size,
            normalized_ticket.limit_price,
            normalized_ticket.order_type_payload(),
            reduce_only=normalized_ticket.reduce_only,
        )

    def _local_signed_modify_edit(self, order_id: int, ticket: HyperliquidOrderEditTicket) -> Any:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        api_secret = os.getenv("HYPE_API_SECRET", "").strip()
        wallet_address = os.getenv("HYPE_WALLET_ADDRESS", "").strip()

        api_wallet = Account.from_key(api_secret)

        exchange = Exchange(
            api_wallet,
            constants.MAINNET_API_URL,
            account_address=wallet_address,
        )
        normalized_ticket = normalize_hyperliquid_order_edit_ticket_size_for_exchange(ticket, exchange)

        return exchange.modify_order(
            order_id,
            normalized_ticket.coin,
            normalized_ticket.is_buy,
            normalized_ticket.size,
            normalized_ticket.wire_limit_price,
            normalized_ticket.order_type_payload(),
            reduce_only=normalized_ticket.reduce_only,
        )

    def _local_signed_position_tpsl(self, tickets: list[HyperliquidTriggerTicket]) -> Any:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        api_secret = os.getenv("HYPE_API_SECRET", "").strip()
        wallet_address = os.getenv("HYPE_WALLET_ADDRESS", "").strip()

        api_wallet = Account.from_key(api_secret)

        exchange = Exchange(
            api_wallet,
            constants.MAINNET_API_URL,
            account_address=wallet_address,
        )
        normalized_tickets = [normalize_hyperliquid_trigger_ticket_size_for_exchange(ticket, exchange) for ticket in tickets]
        order_requests = [
            {
                "coin": ticket.coin,
                "is_buy": ticket.is_buy,
                "sz": ticket.size,
                "limit_px": ticket.wire_limit_price,
                "order_type": ticket.order_type_payload(),
                "reduce_only": True,
            }
            for ticket in normalized_tickets
        ]
        return exchange.bulk_orders(order_requests, grouping="positionTpsl")

    def _local_signed_update_leverage(self, coin: str, leverage: int, *, is_cross: bool = True) -> Any:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        api_secret = os.getenv("HYPE_API_SECRET", "").strip()
        wallet_address = os.getenv("HYPE_WALLET_ADDRESS", "").strip()

        api_wallet = Account.from_key(api_secret)

        exchange = Exchange(
            api_wallet,
            constants.MAINNET_API_URL,
            account_address=wallet_address,
        )

        return exchange.update_leverage(int(leverage), coin, is_cross=is_cross)


def normalize_hyperliquid_ticket_limit_price(ticket: HyperliquidOrderTicket) -> HyperliquidOrderTicket:
    normalized_price = normalize_hyperliquid_limit_price(ticket.limit_price, is_buy=ticket.is_buy)
    if normalized_price == ticket.limit_price:
        return ticket
    return HyperliquidOrderTicket(
        coin=ticket.coin,
        is_buy=ticket.is_buy,
        size=ticket.size,
        limit_price=normalized_price,
        tif=ticket.tif,
        reduce_only=ticket.reduce_only,
    )


def normalize_hyperliquid_ticket_for_wire(ticket: HyperliquidOrderTicket) -> HyperliquidOrderTicket:
    normalized_price_ticket = normalize_hyperliquid_ticket_limit_price(ticket)
    normalized_size = normalize_hyperliquid_size(normalized_price_ticket.size)
    if normalized_size == normalized_price_ticket.size:
        return normalized_price_ticket
    return HyperliquidOrderTicket(
        coin=normalized_price_ticket.coin,
        is_buy=normalized_price_ticket.is_buy,
        size=normalized_size,
        limit_price=normalized_price_ticket.limit_price,
        tif=normalized_price_ticket.tif,
        reduce_only=normalized_price_ticket.reduce_only,
    )


def normalize_hyperliquid_ticket_size_for_exchange(ticket: HyperliquidOrderTicket, exchange: Any) -> HyperliquidOrderTicket:
    decimals = _hyperliquid_size_decimals_for_exchange(ticket.coin, exchange)
    normalized_size = normalize_hyperliquid_size(ticket.size, max_decimals=decimals)
    if normalized_size == ticket.size:
        return ticket
    return HyperliquidOrderTicket(
        coin=ticket.coin,
        is_buy=ticket.is_buy,
        size=normalized_size,
        limit_price=ticket.limit_price,
        tif=ticket.tif,
        reduce_only=ticket.reduce_only,
    )


def normalize_hyperliquid_trigger_ticket_for_wire(ticket: HyperliquidTriggerTicket) -> HyperliquidTriggerTicket:
    normalized_trigger_price = normalize_hyperliquid_limit_price(ticket.trigger_price, is_buy=ticket.is_buy)
    normalized_limit_price = (
        normalize_hyperliquid_limit_price(ticket.limit_price, is_buy=ticket.is_buy)
        if ticket.limit_price is not None
        else None
    )
    normalized_size = normalize_hyperliquid_size(ticket.size)
    if (
        normalized_trigger_price == ticket.trigger_price
        and normalized_limit_price == ticket.limit_price
        and normalized_size == ticket.size
    ):
        return ticket
    return HyperliquidTriggerTicket(
        coin=ticket.coin,
        is_buy=ticket.is_buy,
        size=normalized_size,
        trigger_price=normalized_trigger_price,
        tpsl=ticket.tpsl,
        is_market=ticket.is_market,
        limit_price=normalized_limit_price,
    )


def normalize_hyperliquid_order_edit_ticket_for_wire(ticket: HyperliquidOrderEditTicket) -> HyperliquidOrderEditTicket:
    normalized_size = 0.0 if ticket.close_position else normalize_hyperliquid_size(ticket.size)
    normalized_trigger_price = (
        normalize_hyperliquid_limit_price(ticket.trigger_price, is_buy=ticket.is_buy)
        if ticket.trigger_price is not None
        else None
    )
    normalized_limit_price = normalize_hyperliquid_limit_price(ticket.limit_price, is_buy=ticket.is_buy)
    if (
        normalized_size == ticket.size
        and normalized_trigger_price == ticket.trigger_price
        and normalized_limit_price == ticket.limit_price
    ):
        return ticket
    return HyperliquidOrderEditTicket(
        coin=ticket.coin,
        is_buy=ticket.is_buy,
        size=normalized_size,
        limit_price=normalized_limit_price,
        tif=ticket.tif,
        reduce_only=ticket.reduce_only,
        is_trigger=ticket.is_trigger,
        trigger_price=normalized_trigger_price,
        trigger_kind=ticket.trigger_kind,
        is_market_trigger=ticket.is_market_trigger,
        close_position=ticket.close_position,
    )


def normalize_hyperliquid_order_edit_ticket_size_for_exchange(
    ticket: HyperliquidOrderEditTicket, exchange: Any
) -> HyperliquidOrderEditTicket:
    normalized_size = 0.0
    if not ticket.close_position:
        decimals = _hyperliquid_size_decimals_for_exchange(ticket.coin, exchange)
        normalized_size = normalize_hyperliquid_size(ticket.size, max_decimals=decimals)
    if normalized_size == ticket.size:
        return ticket
    return HyperliquidOrderEditTicket(
        coin=ticket.coin,
        is_buy=ticket.is_buy,
        size=normalized_size,
        limit_price=ticket.limit_price,
        tif=ticket.tif,
        reduce_only=ticket.reduce_only,
        is_trigger=ticket.is_trigger,
        trigger_price=ticket.trigger_price,
        trigger_kind=ticket.trigger_kind,
        is_market_trigger=ticket.is_market_trigger,
        close_position=ticket.close_position,
    )


def normalize_hyperliquid_trigger_ticket_size_for_exchange(
    ticket: HyperliquidTriggerTicket, exchange: Any
) -> HyperliquidTriggerTicket:
    decimals = _hyperliquid_size_decimals_for_exchange(ticket.coin, exchange)
    normalized_size = normalize_hyperliquid_size(ticket.size, max_decimals=decimals)
    if normalized_size == ticket.size:
        return ticket
    return HyperliquidTriggerTicket(
        coin=ticket.coin,
        is_buy=ticket.is_buy,
        size=normalized_size,
        trigger_price=ticket.trigger_price,
        tpsl=ticket.tpsl,
        is_market=ticket.is_market,
        limit_price=ticket.limit_price,
    )


def _hyperliquid_size_decimals_for_exchange(coin: str, exchange: Any) -> int:
    info = exchange.info
    mapped_coin = info.name_to_coin.get(coin, coin)
    asset = info.coin_to_asset.get(mapped_coin)
    if asset is None:
        return HYPERLIQUID_MAX_SIZE_DECIMALS
    decimals = info.asset_to_sz_decimals.get(asset)
    if decimals is None:
        return HYPERLIQUID_MAX_SIZE_DECIMALS
    return int(decimals)


def normalize_hyperliquid_size(size: float, max_decimals: int = HYPERLIQUID_MAX_SIZE_DECIMALS) -> float:
    try:
        decimal_size = Decimal(str(size))
    except InvalidOperation as exc:
        raise ValueError("Hyperliquid size must be a number.") from exc
    if decimal_size <= 0:
        raise ValueError("Hyperliquid size must be positive.")

    decimals = max(0, min(HYPERLIQUID_MAX_SIZE_DECIMALS, int(max_decimals)))
    quant = Decimal("1").scaleb(-decimals)
    normalized = decimal_size.quantize(quant, rounding=ROUND_FLOOR)
    if normalized <= 0:
        raise ValueError("Hyperliquid size is too small after exchange precision rounding.")
    return float(normalized)


def normalize_hyperliquid_limit_price(price: float, *, is_buy: bool) -> float:
    """Round a limit price onto Hyperliquid's accepted price grid.

    Hyperliquid rejects prices that are not on the exchange tick grid. A practical
    rule that avoids the common rejection is to round to the allowed 5-significant-
    figure price increment, rounding buys down and sells up so the adjustment does
    not make the user's limit more aggressive.
    """

    try:
        decimal_price = Decimal(str(price))
    except InvalidOperation as exc:
        raise ValueError("Hyperliquid limit price must be a number.") from exc
    if decimal_price <= 0:
        raise ValueError("Hyperliquid limit price must be positive.")

    tick = _hyperliquid_price_tick(decimal_price)
    if tick <= 0:
        return float(decimal_price)
    quotient = decimal_price / tick
    rounding = ROUND_FLOOR if is_buy else ROUND_CEILING
    normalized = quotient.to_integral_value(rounding=rounding) * tick
    return float(normalized)


def format_hyperliquid_limit_price(price: float) -> str:
    text = format(Decimal(str(price)).normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _hyperliquid_price_tick(price: Decimal) -> Decimal:
    adjusted = price.adjusted()
    exponent = adjusted - HYPERLIQUID_MAX_PRICE_SIGNIFICANT_FIGURES + 1
    return Decimal("1").scaleb(exponent)


def normalize_hyperliquid_spot_market(symbol: str) -> str:
    market = symbol.strip().upper()

    if market.startswith("HL:"):
        market = market[3:]

    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if market.endswith(suffix):
            market = market[: -len(suffix)]

    if market.startswith("@"):
        return market

    if "/" in market:
        base, quote = market.split("/", 1)
        if quote != "USDC":
            raise ValueError("Hyperliquid Cockpit spot orders currently expect USDC-quoted spot markets.")
        return SPOT_EXECUTION_ALIASES.get(base, f"{base}/USDC")

    return SPOT_EXECUTION_ALIASES.get(market, f"{market}/USDC")


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
