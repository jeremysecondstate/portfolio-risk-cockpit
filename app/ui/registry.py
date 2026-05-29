from __future__ import annotations

from typing import Type

import tkinter as tk

from app.ui.account_sources_fix import install_account_sources_fix
from app.ui.advanced_actions_extension import install_advanced_actions_extension
from app.ui.cash_positions_extension import install_cash_positions_extension
from app.ui.company_reports_extension import install_company_reports_extension
from app.ui.hyperliquid_assessment_extension import install_hyperliquid_assessment_extension
from app.ui.hyperliquid_cockpit_spot_mid_extension import install_hyperliquid_cockpit_spot_mid_extension
from app.ui.hyperliquid_existing_perp_what_if_extension import install_hyperliquid_existing_perp_what_if_extension
from app.ui.hyperliquid_notifications_fix import install_hyperliquid_notifications_fix
from app.ui.hyperliquid_perp_ticket_use_mid_fix import install_hyperliquid_perp_ticket_use_mid_fix
from app.ui.hyperliquid_submit_no_autosync_fix import install_hyperliquid_submit_no_autosync_fix
from app.ui.hyperliquid_symbol_alias_extension import install_hyperliquid_symbol_alias_extension
from app.ui.hyperliquid_trading_extension import install_hyperliquid_trading_extension
from app.ui.options_candidate_actionability_extension import install_options_candidate_actionability_extension
from app.ui.options_core_math_extension import install_options_core_math_extension
from app.ui.options_lab_extension import install_options_lab_extension
from app.ui.options_resizable_layout_extension import install_options_resizable_layout_extension
from app.ui.options_what_if_enhancement_extension import install_options_what_if_enhancement_extension
from app.ui.polished_theme import install_polished_cockpit_theme
from app.ui.schwab_live_status_extension import install_schwab_live_status_extension
from app.ui.schwab_mechanical_submit_extension import install_schwab_mechanical_submit_extension
from app.ui.schwab_option_chain_extension import install_schwab_option_chain_extension
from app.ui.schwab_option_chain_visible_fix import install_schwab_option_chain_visible_fix
from app.ui.schwab_option_order_payload_extension import install_schwab_option_order_payload_extension
from app.ui.schwab_options_what_if_scenario_extension import install_schwab_options_what_if_scenario_extension
from app.ui.schwab_output_popout_extension import install_schwab_output_popout_extension
from app.ui.schwab_research_workspace_extension import install_schwab_research_workspace_extension
from app.ui.schwab_sync_report_extension import install_schwab_sync_report_extension
from app.ui.schwab_workspace_sync_extension import install_schwab_workspace_sync_extension
from app.ui.thesis_option_ticket_extension import install_thesis_option_ticket_extension
from app.ui.trade_setup_extension import install_trade_setup_extension
from app.ui.unified_refresh_extension import install_unified_refresh_extension
from app.ui.unified_trade_thesis_extension import install_unified_trade_thesis_extension
from app.ui.unified_trade_thesis_next_checks_extension import install_unified_trade_thesis_next_checks_extension
from app.ui.venue_mid_extension import install_venue_mid_extension


def install_ui_extensions(app_cls: Type[tk.Tk]) -> None:
    """Install all UI extensions in the order required by the cockpit.

    Keep this order explicit while the older patch-style modules are migrated into
    first-class feature modules. The entrypoint should stay small and this registry
    should be the single place to audit extension bootstrapping.
    """

    install_polished_cockpit_theme(app_cls)
    install_trade_setup_extension(app_cls)
    install_advanced_actions_extension(app_cls)
    install_hyperliquid_trading_extension(app_cls)
    install_unified_refresh_extension(app_cls)
    install_hyperliquid_notifications_fix(app_cls)
    install_options_lab_extension(app_cls)
    install_options_what_if_enhancement_extension(app_cls)
    install_hyperliquid_perp_ticket_use_mid_fix(app_cls)
    install_hyperliquid_existing_perp_what_if_extension(app_cls)
    install_schwab_workspace_sync_extension(app_cls)
    install_venue_mid_extension(app_cls)
    install_options_core_math_extension()
    install_account_sources_fix(app_cls)
    install_schwab_options_what_if_scenario_extension(app_cls)
    install_options_resizable_layout_extension()
    install_cash_positions_extension(app_cls)
    install_hyperliquid_symbol_alias_extension()
    install_hyperliquid_submit_no_autosync_fix(app_cls)
    install_hyperliquid_cockpit_spot_mid_extension(app_cls)
    install_schwab_option_chain_extension(app_cls)
    install_schwab_option_chain_visible_fix(app_cls)
    install_thesis_option_ticket_extension(app_cls)
    install_schwab_output_popout_extension(app_cls)
    install_schwab_live_status_extension(app_cls)
    install_schwab_sync_report_extension(app_cls)
    install_hyperliquid_assessment_extension(app_cls)
    install_company_reports_extension(app_cls)
    install_unified_trade_thesis_extension(app_cls)
    install_unified_trade_thesis_next_checks_extension(app_cls)
    install_schwab_research_workspace_extension(app_cls)
    install_options_candidate_actionability_extension(app_cls)
    install_schwab_option_order_payload_extension(app_cls)
    install_schwab_mechanical_submit_extension(app_cls)
