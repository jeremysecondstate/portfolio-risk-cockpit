from __future__ import annotations

from app.ui.account_sources_fix import install_account_sources_fix
from app.ui.advanced_actions_extension import install_advanced_actions_extension
from app.ui.cash_positions_extension import install_cash_positions_extension
from app.ui.hyperliquid_notifications_fix import install_hyperliquid_notifications_fix
from app.ui.hyperliquid_perp_ticket_use_mid_fix import install_hyperliquid_perp_ticket_use_mid_fix
from app.ui.hyperliquid_trading_extension import install_hyperliquid_trading_extension
from app.ui.options_core_math_extension import install_options_core_math_extension
from app.ui.options_lab_extension import install_options_lab_extension
from app.ui.options_resizable_layout_extension import install_options_resizable_layout_extension
from app.ui.polished_theme import install_polished_cockpit_theme
from app.ui.schwab_live_status_extension import install_schwab_live_status_extension
from app.ui.trade_setup_extension import install_trade_setup_extension
from app.ui.trading_cockpit import SchwabTradingCockpitApp
from app.ui.venue_mid_extension import install_venue_mid_extension


def main() -> None:
    install_polished_cockpit_theme(SchwabTradingCockpitApp)
    install_trade_setup_extension(SchwabTradingCockpitApp)
    install_advanced_actions_extension(SchwabTradingCockpitApp)
    install_hyperliquid_trading_extension(SchwabTradingCockpitApp)
    install_hyperliquid_notifications_fix(SchwabTradingCockpitApp)
    install_hyperliquid_perp_ticket_use_mid_fix()
    install_options_lab_extension(SchwabTradingCockpitApp)
    install_venue_mid_extension(SchwabTradingCockpitApp)
    install_options_core_math_extension()
    install_account_sources_fix(SchwabTradingCockpitApp)
    install_options_resizable_layout_extension()
    install_cash_positions_extension(SchwabTradingCockpitApp)
    install_schwab_live_status_extension(SchwabTradingCockpitApp)
    app = SchwabTradingCockpitApp()
    app.mainloop()


if __name__ == "__main__":
    main()