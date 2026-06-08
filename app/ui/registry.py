from __future__ import annotations

from typing import Type

import tkinter as tk

from app.ui.schwab_trading_tab import install_schwab_trading_tab
from app.ui.advanced_actions_extension import install_advanced_actions_extension
from app.ui.cash_positions_extension import install_cash_positions_extension
from app.ui.company_reports_extension import install_company_reports_extension
from app.ui.hyperliquid_chain_health_extension import install_hyperliquid_chain_health_extension
from app.ui.hyperliquid_assessment_extension import install_hyperliquid_assessment_extension
from app.ui.hyperliquid_cockpit_spot_mid_extension import install_hyperliquid_cockpit_spot_mid_extension
from app.ui.hyperliquid_existing_perp_what_if_extension import install_hyperliquid_existing_perp_what_if_extension
from app.ui.hyperliquid_notifications import install_hyperliquid_notifications
from app.ui.hyperliquid_perp_ticket import install_hyperliquid_perp_ticket
from app.ui.hyperliquid_quote_asset_extension import install_hyperliquid_quote_asset_extension
from app.ui.hyperliquid_research_workspace_extension import install_hyperliquid_research_workspace_extension
from app.ui.hyperliquid_spot_symbol_display_extension import install_hyperliquid_spot_symbol_display_extension
from app.ui.hyperliquid_submit_flow import install_hyperliquid_submit_flow
from app.ui.hyperliquid_symbol_alias_extension import install_hyperliquid_symbol_alias_extension
from app.ui.hyperliquid_trading_extension import install_hyperliquid_trading_extension
from app.ui.earnings_radar_extension import install_earnings_radar_extension
from app.ui.ipo_pipeline_extension import install_ipo_pipeline_extension
from app.ui.options_candidate_actionability_extension import install_options_candidate_actionability_extension
from app.ui.options_core_math_extension import install_options_core_math_extension
from app.ui.options_lab_extension import install_options_lab_extension
from app.ui.options_resizable_layout_extension import install_options_resizable_layout_extension
from app.ui.options_what_if_enhancement_extension import install_options_what_if_enhancement_extension
from app.ui.polished_theme import install_polished_cockpit_theme
from app.ui.schwab_live_status_extension import install_schwab_live_status_extension
from app.ui.schwab_mechanical_submit_extension import install_schwab_mechanical_submit_extension
from app.ui.schwab_oauth_hardening_extension import install_schwab_oauth_hardening_extension
from app.ui.schwab_option_chain_extension import install_schwab_option_chain_extension
from app.ui.schwab_option_chain_visibility import install_schwab_option_chain_visibility
from app.ui.schwab_option_contract_inspector_extension import install_schwab_option_contract_inspector_extension
from app.ui.schwab_option_order_payload_extension import install_schwab_option_order_payload_extension
from app.ui.schwab_options_what_if_scenario_extension import install_schwab_options_what_if_scenario_extension
from app.ui.schwab_output_popout_extension import install_schwab_output_popout_extension
from app.ui.schwab_research_workspace_extension import install_schwab_research_workspace_extension
from app.ui.schwab_sync_report_extension import install_schwab_sync_report_extension
from app.ui.schwab_trade_memory_extension import install_schwab_trade_memory_extension
from app.ui.schwab_workspace_sync_extension import install_schwab_workspace_sync_extension
from app.ui.thesis_option_ticket_extension import install_thesis_option_ticket_extension
from app.ui.trade_setup_extension import install_trade_setup_extension
from app.ui.uncovered_options_risk_lane_extension import install_uncovered_options_risk_lane_extension
from app.ui.unified_refresh_extension import install_unified_refresh_extension
from app.ui.unified_trade_thesis_extension import install_unified_trade_thesis_extension
from app.ui.unified_trade_thesis_next_checks_extension import install_unified_trade_thesis_next_checks_extension
from app.ui.venue_mid_extension import install_venue_mid_extension
from app.ui.workspace_day_pnl_extension import install_workspace_day_pnl_extension


def install_ui_extensions(app_cls: Type[tk.Tk]) -> None:
    """Install all UI extensions in the order required by the cockpit.

    Keep this order explicit while the older patch-style modules are migrated into
    first-class feature modules. The entrypoint should stay small and this registry
    should be the single place to audit extension bootstrapping.
    """

    install_schwab_oauth_hardening_extension()
    install_polished_cockpit_theme(app_cls)
    install_trade_setup_extension(app_cls)
    install_advanced_actions_extension(app_cls)
    install_hyperliquid_trading_extension(app_cls)
    install_unified_refresh_extension(app_cls)
    install_hyperliquid_notifications(app_cls)
    install_options_lab_extension(app_cls)
    install_hyperliquid_chain_health_extension(app_cls)
    install_options_what_if_enhancement_extension(app_cls)
    install_hyperliquid_perp_ticket(app_cls)
    install_hyperliquid_existing_perp_what_if_extension(app_cls)
    install_schwab_workspace_sync_extension(app_cls)
    install_venue_mid_extension(app_cls)
    install_options_core_math_extension()
    install_schwab_trading_tab(app_cls)
    install_schwab_options_what_if_scenario_extension(app_cls)
    install_options_resizable_layout_extension()
    install_cash_positions_extension(app_cls)
    install_workspace_day_pnl_extension(app_cls)
    install_hyperliquid_symbol_alias_extension()
    install_hyperliquid_submit_flow(app_cls)
    install_hyperliquid_cockpit_spot_mid_extension(app_cls)
    install_schwab_option_chain_extension(app_cls)
    install_schwab_option_chain_visibility(app_cls)
    install_schwab_option_contract_inspector_extension(app_cls)
    install_thesis_option_ticket_extension(app_cls)
    install_schwab_output_popout_extension(app_cls)
    install_schwab_live_status_extension(app_cls)
    install_schwab_sync_report_extension(app_cls)
    install_hyperliquid_assessment_extension(app_cls)
    install_company_reports_extension(app_cls)
    install_ipo_pipeline_extension(app_cls)
    install_earnings_radar_extension(app_cls)
    install_unified_trade_thesis_extension(app_cls)
    install_unified_trade_thesis_next_checks_extension(app_cls)
    install_schwab_research_workspace_extension(app_cls)
    install_hyperliquid_research_workspace_extension(app_cls)
    install_options_candidate_actionability_extension(app_cls)
    install_schwab_option_order_payload_extension(app_cls)
    install_schwab_mechanical_submit_extension(app_cls)
    install_schwab_trade_memory_extension(app_cls)
    install_hyperliquid_spot_symbol_display_extension(app_cls)
    install_hyperliquid_quote_asset_extension(app_cls)
    install_uncovered_options_risk_lane_extension(app_cls)
