from __future__ import annotations

from app.ui import cash_positions_extension


HYPERLIQUID_DISPLAY_ALIASES = {
    "UZEC": "ZEC",
    "UZEC/USDC": "ZEC",
}


def install_hyperliquid_symbol_alias_extension() -> None:
    """Normalize venue-specific Hyperliquid spot wrappers to common symbols."""

    cash_positions_extension._HYPERLIQUID_SPOT_ALIASES.update(HYPERLIQUID_DISPLAY_ALIASES)
