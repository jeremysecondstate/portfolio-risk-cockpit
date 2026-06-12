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
SUPPORTED_SPOT_QUOTES = ("USDC", "USDT")
HYPERLIQUID_MAX_PRICE_SIGNIFICANT_FIGURES = 5
HYPERLIQUID_MAX_SIZE_DECIMALS = 8


@dataclass(frozen=True)
class HyperliquidLiveAccountProfile:
    key: str
    label: str
    wallet_address_env_keys: tuple[str, ...]
    api_address_env_keys: tuple[str, ...]
    api_secret_env_keys: tuple[str, ...]


HYPERLIQUID_LIVE_ACCOUNTS: dict[str, HyperliquidLiveAccountProfile] = {
    "jeremy": HyperliquidLiveAccountProfile(
        key="jeremy",
        label="Jeremy",
        wallet_address_env_keys=(
            "HYPE_WALLET_ADDRESS_JEREMY_SECONDSTATE",
            "HYPE_WALLET_ADDRESS",
        ),
        api_address_env_keys=("HYPE_API_ADDRESS",),
        api_secret_env_keys=("HYPE_API_SECRET",),
    ),
    "alex": HyperliquidLiveAccountProfile(
        key="alex",
        label="Alex",
        wallet_address_env_keys=(
            "HYPE_WALLET_ADDRESS_ALEX_SECONDSTATE",
            "HYPE_WALLET_ADDRESS_ALEX",
        ),
        api_address_env_keys=("HYPE_API_ADDRESS_ALEX",),
        api_secret_env_keys=("HYPE_API_SECRET_ALEX",),
    ),
}


def _first_env_value(keys: tuple[str, ...]) -> str:
    for key in keys:
        value = os.getenv(key, "").strip().strip("'\"")
        if value and value.lower() != "key in here":
            return value
    return ""


def _live_account_profile(account_key: str) -> HyperliquidLiveAccountProfile:
    normalized = (account_key or "jeremy").strip().lower()
    try:
        return HYPERLIQUID_LIVE_ACCOUNTS[normalized]
    except KeyError as exc:
        choices = ", ".join(sorted(HYPERLIQUID_LIVE_ACCOUNTS))
        raise ValueError(f"Unknown Hyperliquid live account '{account_key}'. Choices: {choices}") from exc


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


@dataclass(frozen=True)
class HyperliquidSpotMarketResolution:
    requested_symbol: str
    normalized_market: str
    display_market: str
    execution_coin: str
    base_symbol: str
    quote_symbol: str
    mid_price: float | None = None
    mid_basis: str = ""
    candidate_keys: tuple[str, ...] = ()
    spot_meta_loaded: bool = False
    spot_meta_and_asset_ctxs_loaded: bool = False
    nearby_matches: tuple[str, ...] = ()


class HyperliquidSpotMarketLookupError(RuntimeError):
    def __init__(self, resolution: HyperliquidSpotMarketResolution) -> None:
        self.resolution = resolution
        super().__init__(format_hyperliquid_spot_lookup_error(resolution))


class HyperliquidTradingConfig:
    """Local environment readiness for Hyperliquid ticket workflow."""

    def __init__(self, account_key: str = "jeremy") -> None:
        self.account = _live_account_profile(account_key)
        self.account_key = self.account.key
        self.account_label = self.account.label
        self.wallet_address = _first_env_value(self.account.wallet_address_env_keys)
        self.api_address = _first_env_value(self.account.api_address_env_keys)
        self.api_secret = _first_env_value(self.account.api_secret_env_keys)
        self.has_signing_secret = bool(self.api_secret)
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
        if not ticket.reduce_only and ticket.notional > self.max_live_notional:
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
                f"Account: {self.account_label}",
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
    """Fast execution adapter with local SDK hooks."""

    def __init__(self, account_key: str = "jeremy") -> None:
        self.account_key = account_key

    def _config(self) -> HyperliquidTradingConfig:
        return HyperliquidTradingConfig(self.account_key)

    def submit(self, ticket: HyperliquidOrderTicket) -> Any:
        normalized_ticket = normalize_hyperliquid_ticket_limit_price(ticket)
        config = self._config()
        config.validate_for_live(normalized_ticket)
        return self._local_signed_submit(normalized_ticket, config)

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

        normalized_tickets = [
            normalize_hyperliquid_trigger_ticket_for_wire(ticket)
            for ticket in tickets
        ]

        config = self._config()
        for ticket in normalized_tickets:
            config.validate_trigger_for_live(ticket)

        return self._local_signed_position_tpsl(normalized_tickets, config)

    def update_leverage(self, coin: str, leverage: int, *, is_cross: bool = True) -> Any:
        config = self._config()
        config.validate_for_live_action()

        normalized_coin = normalize_hyperliquid_coin(coin)

        if leverage < 1:
            raise ValueError("Hyperliquid leverage must be at least 1x.")
        if leverage > 100:
            raise ValueError("Hyperliquid leverage must be 100x or lower.")

        return self._local_signed_update_leverage(
            normalized_coin,
            leverage,
            config,
            is_cross=is_cross,
        )

    def _local_signed_submit(self, ticket: HyperliquidOrderTicket, config: HyperliquidTradingConfig) -> Any:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        api_wallet = Account.from_key(config.api_secret)

        exchange = Exchange(
            api_wallet,
            constants.MAINNET_API_URL,
            account_address=config.wallet_address,
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

    def _local_signed_position_tpsl(
            self,
            tickets: list[HyperliquidTriggerTicket],
            config: HyperliquidTradingConfig,
    ) -> Any:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        api_wallet = Account.from_key(config.api_secret)

        exchange = Exchange(
            api_wallet,
            constants.MAINNET_API_URL,
            account_address=config.wallet_address,
        )

        normalized_tickets = [
            normalize_hyperliquid_trigger_ticket_size_for_exchange(ticket, exchange)
            for ticket in tickets
        ]

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

    def _local_signed_update_leverage(
            self,
            coin: str,
            leverage: int,
            config: HyperliquidTradingConfig,
            *,
            is_cross: bool = True,
    ) -> Any:
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.utils import constants

        api_wallet = Account.from_key(config.api_secret)

        exchange = Exchange(
            api_wallet,
            constants.MAINNET_API_URL,
            account_address=config.wallet_address,
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


def resolve_hyperliquid_spot_market(
    symbol: str,
    *,
    default_quote: str = "USDC",
    all_mids: dict[str, Any] | None = None,
    spot_meta: Any = None,
    spot_meta_and_asset_ctxs: Any = None,
) -> HyperliquidSpotMarketResolution:
    requested_symbol = symbol.strip()
    normalized_market = normalize_hyperliquid_spot_display_market(requested_symbol, quote_asset=default_quote)
    requested_index = _spot_market_index(normalized_market)
    base, quote = _split_normalized_spot_market(normalized_market)
    requested_base_aliases = _spot_symbol_aliases(base)
    requested_market_aliases = _spot_market_aliases(requested_base_aliases, quote)
    spot_meta_loaded = _spot_metadata_payload_loaded(spot_meta)
    spot_meta_and_asset_ctxs_loaded = _spot_metadata_payload_loaded(spot_meta_and_asset_ctxs)

    candidate_keys: list[str] = []
    if requested_index is not None:
        _append_unique(candidate_keys, f"@{requested_index}")

    matches, nearby_matches = _matching_spot_metadata_markets(
        normalized_market,
        requested_base_aliases,
        requested_market_aliases,
        spot_meta=spot_meta,
        spot_meta_and_asset_ctxs=spot_meta_and_asset_ctxs,
    )
    for match in matches:
        for key in _spot_metadata_market_keys(match):
            _append_unique(candidate_keys, key)

    for key in _direct_spot_candidate_keys(normalized_market, requested_base_aliases, quote):
        _append_unique(candidate_keys, key)

    mids = all_mids if isinstance(all_mids, dict) else {}
    for key in candidate_keys:
        price = _all_mids_price(mids, key)
        if price is None:
            continue
        match = _metadata_match_for_key(matches, key)
        return _spot_resolution(
            requested_symbol=requested_symbol,
            normalized_market=normalized_market,
            base=base,
            quote=quote,
            candidate_keys=candidate_keys,
            spot_meta_loaded=spot_meta_loaded,
            spot_meta_and_asset_ctxs_loaded=spot_meta_and_asset_ctxs_loaded,
            nearby_matches=nearby_matches,
            match=match,
            execution_coin=_execution_coin_from_key(key, match, normalized_market),
            mid_price=price,
            mid_basis=f"allMids[{key}]",
        )

    for match in matches:
        price, basis = _spot_asset_ctx_price(match)
        if price is None:
            continue
        return _spot_resolution(
            requested_symbol=requested_symbol,
            normalized_market=normalized_market,
            base=base,
            quote=quote,
            candidate_keys=candidate_keys,
            spot_meta_loaded=spot_meta_loaded,
            spot_meta_and_asset_ctxs_loaded=spot_meta_and_asset_ctxs_loaded,
            nearby_matches=nearby_matches,
            match=match,
            execution_coin=_execution_coin_from_match(match, normalized_market),
            mid_price=price,
            mid_basis=basis,
        )

    fallback_match = matches[0] if matches else None
    return _spot_resolution(
        requested_symbol=requested_symbol,
        normalized_market=normalized_market,
        base=base,
        quote=quote,
        candidate_keys=candidate_keys,
        spot_meta_loaded=spot_meta_loaded,
        spot_meta_and_asset_ctxs_loaded=spot_meta_and_asset_ctxs_loaded,
        nearby_matches=nearby_matches,
        match=fallback_match,
        execution_coin=_execution_coin_from_match(fallback_match, normalized_market),
    )


def format_hyperliquid_spot_lookup_error(resolution: HyperliquidSpotMarketResolution) -> str:
    candidates = ", ".join(resolution.candidate_keys[:24]) or "--"
    if len(resolution.candidate_keys) > 24:
        candidates = f"{candidates}, ... {len(resolution.candidate_keys) - 24} more"
    nearby = ", ".join(resolution.nearby_matches[:10]) or "--"
    if len(resolution.nearby_matches) > 10:
        nearby = f"{nearby}, ... {len(resolution.nearby_matches) - 10} more"
    return "\n".join(
        [
            f"No Hyperliquid spot mid-market price found for {resolution.normalized_market}.",
            f"Normalized market attempted: {resolution.normalized_market}",
            f"Execution/API coin resolved: {resolution.execution_coin or '--'}",
            f"Candidate keys attempted: {candidates}",
            f"spotMetaAndAssetCtxs loaded: {'yes' if resolution.spot_meta_and_asset_ctxs_loaded else 'no'}",
            f"spotMeta loaded: {'yes' if resolution.spot_meta_loaded else 'no'}",
            f"Nearby tokens/markets: {nearby}",
        ]
    )


def normalize_hyperliquid_spot_display_market(symbol: str, quote_asset: str = "USDC") -> str:
    market = _clean_hyperliquid_spot_symbol(symbol)
    if not market:
        raise ValueError("Enter a Hyperliquid spot symbol, for example XAUT, BTC, or XAUT/USDC.")
    if market.startswith("@"):
        return market
    base, quote = _split_normalized_spot_market(market, default_quote=quote_asset)
    if quote not in SUPPORTED_SPOT_QUOTES:
        raise ValueError("Hyperliquid spot orders currently expect USDC- or USDT-quoted spot markets.")
    return f"{_display_spot_base_from_alias(base)}/{quote}"


def normalize_hyperliquid_spot_market(symbol: str, quote_asset: str = "USDC") -> str:
    market = _clean_hyperliquid_spot_symbol(symbol)

    if market.startswith("@"):
        return market

    if "/" in market:
        base, quote = market.split("/", 1)
        if quote not in SUPPORTED_SPOT_QUOTES:
            raise ValueError("Hyperliquid spot orders currently expect USDC- or USDT-quoted spot markets.")
        alias_market = SPOT_EXECUTION_ALIASES.get(base)
        if alias_market and alias_market.endswith(f"/{quote}"):
            return alias_market
        return f"{base}/{quote}"

    quote = _normalize_spot_quote_asset(quote_asset)
    alias_market = SPOT_EXECUTION_ALIASES.get(market)
    if alias_market and alias_market.endswith(f"/{quote}"):
        return alias_market
    return f"{market}/{quote}"


def _spot_resolution(
    *,
    requested_symbol: str,
    normalized_market: str,
    base: str,
    quote: str,
    candidate_keys: list[str],
    spot_meta_loaded: bool,
    spot_meta_and_asset_ctxs_loaded: bool,
    nearby_matches: tuple[str, ...],
    match: dict[str, Any] | None,
    execution_coin: str,
    mid_price: float | None = None,
    mid_basis: str = "",
) -> HyperliquidSpotMarketResolution:
    display_market = _display_market_from_match(match, normalized_market, base, quote)
    display_base, display_quote = _split_normalized_spot_market(display_market)
    return HyperliquidSpotMarketResolution(
        requested_symbol=requested_symbol,
        normalized_market=normalized_market,
        display_market=display_market,
        execution_coin=execution_coin or normalized_market,
        base_symbol=display_base,
        quote_symbol=display_quote,
        mid_price=mid_price,
        mid_basis=mid_basis,
        candidate_keys=tuple(candidate_keys),
        spot_meta_loaded=spot_meta_loaded,
        spot_meta_and_asset_ctxs_loaded=spot_meta_and_asset_ctxs_loaded,
        nearby_matches=nearby_matches,
    )


def _clean_hyperliquid_spot_symbol(symbol: str) -> str:
    market = symbol.strip().upper()
    if market.startswith("HL:"):
        market = market[3:]
    for suffix in ("-PERP-SHORT", "-PERP", "-SPOT"):
        if market.endswith(suffix):
            market = market[: -len(suffix)]
    market = market.replace("-", "/", 1) if "/" not in market and market.count("-") == 1 else market
    return market


def _split_normalized_spot_market(market: str, *, default_quote: str = "USDC") -> tuple[str, str]:
    clean = market.strip().upper()
    if clean.startswith("@"):
        return clean, _normalize_spot_quote_asset(default_quote)
    if "/" in clean:
        base, quote = clean.split("/", 1)
        return base.strip(), _normalize_spot_quote_asset(quote or default_quote)
    return clean, _normalize_spot_quote_asset(default_quote)


def _normalize_spot_quote_asset(quote_asset: str) -> str:
    quote = str(quote_asset or "USDC").strip().upper()
    if quote not in SUPPORTED_SPOT_QUOTES:
        raise ValueError("Hyperliquid spot quote must be USDC or USDT.")
    return quote


def _spot_market_index(market: str) -> int | None:
    clean = market.strip()
    if not clean.startswith("@"):
        return None
    return _to_int(clean[1:])


def _direct_spot_candidate_keys(normalized_market: str, base_aliases: tuple[str, ...], quote: str) -> tuple[str, ...]:
    if normalized_market.startswith("@"):
        return (normalized_market,)

    keys: list[str] = []
    for alias in base_aliases:
        mapped = SPOT_EXECUTION_ALIASES.get(alias)
        if mapped:
            _append_unique(keys, mapped)
            _append_unique(keys, mapped.replace("/", "-"))

    for market in _spot_market_aliases(base_aliases, quote):
        _append_unique(keys, market)
        _append_unique(keys, market.replace("/", "-"))

    for alias in base_aliases:
        _append_unique(keys, alias)
    return tuple(keys)


def _matching_spot_metadata_markets(
    normalized_market: str,
    requested_base_aliases: tuple[str, ...],
    requested_market_aliases: tuple[str, ...],
    *,
    spot_meta: Any,
    spot_meta_and_asset_ctxs: Any,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    requested_index = _spot_market_index(normalized_market)
    _requested_base, requested_quote = _split_normalized_spot_market(normalized_market)
    matches: list[dict[str, Any]] = []
    nearby: list[str] = []
    seen_match_keys: set[tuple[str, int, int | None]] = set()

    for source_name, meta, asset_ctxs in _spot_metadata_sources(spot_meta, spot_meta_and_asset_ctxs):
        universe = meta.get("universe") if isinstance(meta, dict) else None
        tokens = meta.get("tokens") if isinstance(meta, dict) else None
        if not isinstance(universe, list):
            continue

        token_names_by_index = _spot_token_names_by_index(tokens)
        token_labels_by_index = _spot_token_labels_by_index(tokens)
        for market_index, asset in enumerate(universe):
            if not isinstance(asset, dict):
                continue

            market = _spot_metadata_market(source_name, asset, market_index, token_names_by_index, asset_ctxs)
            match_key = (source_name, market_index, market["asset_index"])
            if requested_index is not None:
                match_priority = _requested_index_match_priority(market, requested_index)
                is_match = match_priority is not None
            else:
                match_priority = None
                quote_matches = requested_quote in market["quote_aliases"]
                base_matches = bool(set(requested_base_aliases).intersection(market["base_aliases"]))
                market_matches = bool(set(requested_market_aliases).intersection(market["names"]))
                is_match = quote_matches and (base_matches or market_matches)

            if is_match and match_key not in seen_match_keys:
                market = {**market, "match_priority": match_priority if match_priority is not None else 10}
                matches.append(market)
                seen_match_keys.add(match_key)
                continue

            nearby_label = _nearby_spot_metadata_label(
                normalized_market,
                requested_base_aliases,
                asset,
                market,
                token_labels_by_index,
            )
            if nearby_label:
                _append_unique(nearby, nearby_label)

    matches.sort(key=_spot_metadata_match_sort_key)
    return matches, tuple(nearby)


def _spot_metadata_sources(spot_meta: Any, spot_meta_and_asset_ctxs: Any) -> list[tuple[str, dict[str, Any], list[Any] | None]]:
    sources: list[tuple[str, dict[str, Any], list[Any] | None]] = []
    for source_name, payload in (("spotMetaAndAssetCtxs", spot_meta_and_asset_ctxs), ("spotMeta", spot_meta)):
        meta, asset_ctxs = _parse_spot_metadata_payload(payload)
        if meta is not None:
            sources.append((source_name, meta, asset_ctxs))
    return sources


def _parse_spot_metadata_payload(payload: Any) -> tuple[dict[str, Any] | None, list[Any] | None]:
    if isinstance(payload, list) and payload:
        meta = payload[0] if isinstance(payload[0], dict) else None
        asset_ctxs = payload[1] if len(payload) > 1 and isinstance(payload[1], list) else None
        return meta, asset_ctxs
    if isinstance(payload, dict):
        return payload, None
    return None, None


def _spot_metadata_payload_loaded(payload: Any) -> bool:
    meta, _asset_ctxs = _parse_spot_metadata_payload(payload)
    return meta is not None


def _spot_metadata_market(
    source_name: str,
    asset: dict[str, Any],
    market_index: int,
    token_names_by_index: dict[int, set[str]],
    asset_ctxs: list[Any] | None,
) -> dict[str, Any]:
    token_indices = asset.get("tokens")
    base_index = _to_int(token_indices[0]) if isinstance(token_indices, list) and token_indices else None
    quote_index = _to_int(token_indices[1]) if isinstance(token_indices, list) and len(token_indices) > 1 else None
    base_aliases = _aliases_for_token_index(base_index, token_names_by_index)
    quote_aliases = _aliases_for_token_index(quote_index, token_names_by_index) or ("USDC",)
    asset_index = _to_int(asset.get("index"))

    names: list[str] = []
    for key in ("name", "coin", "token", "symbol"):
        value = asset.get(key)
        if value not in (None, ""):
            _append_unique(names, str(value).strip().upper())
    for base in base_aliases:
        _append_unique(names, base)
        for quote in quote_aliases:
            _append_unique(names, f"{base}/{quote}")
            _append_unique(names, f"{base}-{quote}")
    if asset_index is not None:
        _append_unique(names, f"@{asset_index}")
        _append_unique(names, f"@{10000 + asset_index}")
    _append_unique(names, f"@{market_index}")
    _append_unique(names, f"@{10000 + market_index}")

    index_aliases = {market_index, 10000 + market_index}
    if asset_index is not None:
        index_aliases.update({asset_index, 10000 + asset_index})

    return {
        "source": source_name,
        "asset": asset,
        "asset_ctxs": asset_ctxs,
        "market_index": market_index,
        "asset_index": asset_index,
        "base_index": base_index,
        "quote_index": quote_index,
        "base_aliases": tuple(base_aliases),
        "quote_aliases": tuple(quote_aliases),
        "names": tuple(names),
        "index_aliases": frozenset(index_aliases),
    }


def _spot_metadata_match_sort_key(match: dict[str, Any]) -> tuple[int, int, int]:
    quote_aliases = set(match["quote_aliases"])
    source = str(match["source"])
    return (
        int(match.get("match_priority", 10)),
        0 if "USDC" in quote_aliases else 1,
        0 if source == "spotMetaAndAssetCtxs" else 1,
    )


def _requested_index_match_priority(market: dict[str, Any], requested_index: int) -> int | None:
    asset_name = str(market["asset"].get("name") or "").strip().upper()
    if asset_name == f"@{requested_index}":
        return 0
    asset_index = market["asset_index"]
    if asset_index == requested_index:
        return 1
    if asset_index is not None and 10000 + asset_index == requested_index:
        return 2
    market_index = int(market["market_index"])
    if market_index == requested_index:
        return 3
    if 10000 + market_index == requested_index:
        return 4
    if f"@{requested_index}" in market["names"]:
        return 5
    return None


def _spot_metadata_market_keys(match: dict[str, Any]) -> tuple[str, ...]:
    keys: list[str] = []
    asset = match["asset"]
    asset_name = str(asset.get("name") or "").strip().upper()
    if asset_name:
        _append_unique(keys, asset_name)
    asset_index = match["asset_index"]
    if asset_index is not None:
        _append_unique(keys, f"@{asset_index}")
        _append_unique(keys, f"@{10000 + asset_index}")
    market_index = int(match["market_index"])
    _append_unique(keys, f"@{market_index}")
    _append_unique(keys, f"@{10000 + market_index}")
    for name in match["names"]:
        _append_unique(keys, str(name))
        if "/" in str(name):
            _append_unique(keys, str(name).replace("/", "-"))
    return tuple(keys)


def _metadata_match_for_key(matches: list[dict[str, Any]], key: str) -> dict[str, Any] | None:
    normalized_key = key.strip().upper()
    for match in matches:
        keys = {candidate.strip().upper() for candidate in _spot_metadata_market_keys(match)}
        if normalized_key in keys:
            return match
    return None


def _execution_coin_from_key(key: str, match: dict[str, Any] | None, normalized_market: str) -> str:
    clean_key = key.strip().upper()
    if clean_key.startswith("@"):
        return clean_key
    return _execution_coin_from_match(match, normalized_market)


def _execution_coin_from_match(match: dict[str, Any] | None, normalized_market: str) -> str:
    if match is None:
        return normalize_hyperliquid_spot_market(normalized_market)
    asset_name = str(match["asset"].get("name") or "").strip().upper()
    if asset_name.startswith("@"):
        return asset_name
    asset_index = match["asset_index"]
    if asset_index is not None:
        return f"@{asset_index}"
    if asset_name:
        return asset_name
    return normalize_hyperliquid_spot_market(normalized_market)


def _display_market_from_match(match: dict[str, Any] | None, normalized_market: str, base: str, quote: str) -> str:
    if normalized_market.startswith("@") and match is None:
        return normalized_market
    display_base = _preferred_display_base(match["base_aliases"] if match else (base,), requested_base=base)
    display_quote = _preferred_display_base(match["quote_aliases"] if match else (quote,), requested_base=quote)
    return f"{display_base}/{display_quote}"


def _preferred_display_base(aliases: tuple[str, ...], *, requested_base: str = "") -> str:
    requested = "" if requested_base.strip().startswith("@") else _display_spot_base_from_alias(requested_base) if requested_base else ""
    if requested and requested != "USDC":
        return requested
    cleaned = [_display_spot_base_from_alias(alias) for alias in aliases if alias]
    for alias in cleaned:
        if alias and not alias.startswith("U") and not alias.endswith("0"):
            return alias
    return cleaned[0] if cleaned else requested_base


def _display_spot_base_from_alias(symbol: str) -> str:
    clean = str(symbol or "").strip().upper()
    if "/" in clean:
        clean = clean.split("/", 1)[0]
    if clean in {"USD", "USDC"}:
        return clean
    if clean in {"USDT", "USDT0"}:
        return "USDT"
    if clean.startswith("U") and len(clean) > 1:
        clean = clean[1:]
    if clean.endswith("0") and len(clean) > 1:
        clean = clean[:-1]
    return clean


def _spot_asset_ctx_price(match: dict[str, Any]) -> tuple[float | None, str]:
    asset_ctxs = match.get("asset_ctxs")
    if not isinstance(asset_ctxs, list):
        return None, ""
    for ctx_index, ctx in _spot_asset_context_candidates(match, asset_ctxs):
        if not isinstance(ctx, dict):
            continue
        price = _first_positive_number(ctx, "midPx", "markPx", "oraclePx", "price", "prevDayPx")
        if price is not None:
            return price, f"{match['source']} ctx {ctx_index}"
    return None, ""


def _spot_asset_context_candidates(match: dict[str, Any], asset_ctxs: list[Any]) -> list[tuple[int, Any]]:
    targets = {str(name).strip().upper() for name in match["names"] if str(name).strip().startswith("@")}
    candidates: list[tuple[int, Any]] = []

    for index, ctx in enumerate(asset_ctxs):
        if not isinstance(ctx, dict):
            continue
        coin = str(ctx.get("coin") or "").strip().upper()
        if coin and coin in targets:
            candidates.append((index, ctx))

    for index in (match["asset_index"], match["market_index"]):
        if index is None or index < 0 or index >= len(asset_ctxs):
            continue
        ctx = asset_ctxs[index]
        coin = str(ctx.get("coin") or "").strip().upper() if isinstance(ctx, dict) else ""
        if coin and coin not in targets:
            continue
        if all(existing_index != index for existing_index, _ctx in candidates):
            candidates.append((index, ctx))
    return candidates


def _spot_token_names_by_index(tokens: Any) -> dict[int, set[str]]:
    names_by_index: dict[int, set[str]] = {}
    if not isinstance(tokens, list):
        return names_by_index
    for fallback_index, token in enumerate(tokens):
        if not isinstance(token, dict):
            continue
        names: set[str] = set()
        for key in ("name", "coin", "token", "fullName"):
            value = token.get(key)
            if value not in (None, ""):
                names.update(_spot_symbol_aliases(str(value)))
        if not names:
            continue
        names_by_index.setdefault(fallback_index, set()).update(names)
        explicit_index = _to_int(token.get("index"))
        if explicit_index is not None:
            names_by_index.setdefault(explicit_index, set()).update(names)
    return names_by_index


def _spot_token_labels_by_index(tokens: Any) -> dict[int, str]:
    labels: dict[int, str] = {}
    if not isinstance(tokens, list):
        return labels
    for fallback_index, token in enumerate(tokens):
        if not isinstance(token, dict):
            continue
        label = str(token.get("name") or token.get("fullName") or token.get("coin") or "").strip().upper()
        if not label:
            continue
        labels[fallback_index] = label
        explicit_index = _to_int(token.get("index"))
        if explicit_index is not None:
            labels[explicit_index] = label
    return labels


def _aliases_for_token_index(token_index: int | None, token_names_by_index: dict[int, set[str]]) -> tuple[str, ...]:
    if token_index is None:
        return ()
    aliases: list[str] = []
    for name in sorted(token_names_by_index.get(token_index, set())):
        for alias in _spot_symbol_aliases(name):
            _append_unique(aliases, alias)
    return tuple(aliases)


def _spot_symbol_aliases(symbol: str) -> tuple[str, ...]:
    clean = str(symbol or "").strip().upper()
    if not clean:
        return ()
    if "/" in clean:
        clean = clean.split("/", 1)[0]

    seeds: list[str] = []
    _append_unique(seeds, clean)
    for alias, market in SPOT_EXECUTION_ALIASES.items():
        market_base = market.split("/", 1)[0]
        if clean in {alias, market_base}:
            _append_unique(seeds, alias)
            _append_unique(seeds, market_base)

    aliases: list[str] = []
    for seed in list(seeds):
        for value in _spot_alias_variants(seed):
            _append_unique(aliases, value)
    return tuple(aliases)


def _spot_alias_variants(symbol: str) -> tuple[str, ...]:
    clean = str(symbol or "").strip().upper()
    if not clean:
        return ()
    if clean in {"USDT", "USDT0"}:
        return ("USDT0", "USDT") if clean == "USDT0" else ("USDT", "USDT0")
    variants: list[str] = []
    work = [clean]
    if clean not in {"USD", "USDC"}:
        if clean.startswith("U") and len(clean) > 1:
            work.append(clean[1:])
        else:
            work.append(f"U{clean}")
        if clean.endswith("0") and len(clean) > 1:
            work.append(clean[:-1])
        else:
            work.append(f"{clean}0")

    for value in work:
        _append_unique(variants, value)
        if value not in {"USD", "USDC"} and value.startswith("U") and len(value) > 1:
            _append_unique(variants, value[1:])
        if value not in {"USD", "USDC"} and value.endswith("0") and len(value) > 1:
            _append_unique(variants, value[:-1])
    return tuple(variants)


def _spot_market_aliases(base_aliases: tuple[str, ...], quote: str) -> tuple[str, ...]:
    markets: list[str] = []
    for base in base_aliases:
        _append_unique(markets, f"{base}/{quote}")
    return tuple(markets)


def _nearby_spot_metadata_label(
    normalized_market: str,
    requested_base_aliases: tuple[str, ...],
    asset: dict[str, Any],
    market: dict[str, Any],
    token_labels_by_index: dict[int, str],
) -> str:
    if normalized_market.startswith("@"):
        return ""
    requested_terms = {_display_spot_base_from_alias(alias) for alias in requested_base_aliases}
    requested_terms.update(requested_base_aliases)
    requested_terms = {term for term in requested_terms if term and len(term) >= 2}
    haystack = set(market["names"]) | set(market["base_aliases"])
    if not any(_spot_terms_near(requested, candidate) for requested in requested_terms for candidate in haystack):
        return ""
    token_indices = asset.get("tokens") if isinstance(asset.get("tokens"), list) else []
    token_labels: list[str] = []
    for index in token_indices:
        token_index = _to_int(index)
        token_labels.append(token_labels_by_index.get(token_index, str(index)) if token_index is not None else str(index))
    fallback_market_name = f"@{market['asset_index']}" if market["asset_index"] is not None else ""
    market_name = str(asset.get("name") or fallback_market_name)
    return f"{market_name} tokens {'/'.join(token_labels)}"


def _spot_terms_near(requested: str, candidate: str) -> bool:
    left = requested.strip().upper()
    right = str(candidate or "").strip().upper()
    if "/" in right:
        right = right.split("/", 1)[0]
    if not left or not right:
        return False
    return left in right or right in left


def _all_mids_price(all_mids: dict[str, Any], key: str) -> float | None:
    if not key:
        return None
    price = _to_positive_float(all_mids.get(key))
    if price is not None:
        return price
    upper_key = key.upper()
    for raw_key, raw_value in all_mids.items():
        if str(raw_key).upper() != upper_key:
            continue
        return _to_positive_float(raw_value)
    return None


def _first_positive_number(source: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _to_positive_float(source.get(key))
        if value is not None:
            return value
    return None


def _to_positive_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        return None
    return number if number > 0 else None


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).replace(",", ""))
    except ValueError:
        return None


def _append_unique(values: list[str], value: str) -> None:
    if value and value not in values:
        values.append(value)


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
