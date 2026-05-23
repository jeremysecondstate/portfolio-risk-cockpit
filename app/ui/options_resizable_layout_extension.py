from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from app.ui import options_lab, options_lab_extension
from app.ui.polished_theme import _make_paned

_installed = False


def install_options_resizable_layout_extension() -> None:
    """Give the Options What-If Lab cockpit-style draggable panes."""

    global _installed
    if _installed:
        return

    options_lab.build_options_lab_tab = _build_resizable_options_lab_tab
    options_lab_extension.build_options_lab_tab = _build_resizable_options_lab_tab
    _installed = True


def _build_resizable_options_lab_tab(app: tk.Tk, parent: ttk.Frame) -> None:
    """Build the options lab with draggable horizontal and vertical splitters."""

    options_lab._init_options_vars(app)

    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(1, weight=1)

    options_lab._build_options_disclaimer(parent)

    body = _make_paned(parent, tk.HORIZONTAL)
    body.grid(row=1, column=0, sticky="nsew")

    left_shell = ttk.Frame(body, style="Canvas.TFrame")
    right_shell = ttk.Frame(body, style="Canvas.TFrame")
    body.add(left_shell, minsize=420, stretch="always")
    body.add(right_shell, minsize=520, stretch="always")
    app.after_idle(lambda: body.sash_place(0, max(460, int(parent.winfo_width() * 0.42)), 0))

    _build_resizable_scenario_builder(app, left_shell)
    _build_resizable_options_output(app, right_shell)
    options_lab.run_options_what_if(app)


def _build_resizable_scenario_builder(app: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(0, weight=1)

    stack = _make_paned(parent, tk.VERTICAL)
    stack.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

    quote_shell = ttk.Frame(stack, style="Canvas.TFrame")
    ticket_shell = ttk.Frame(stack, style="Canvas.TFrame")
    context_shell = ttk.Frame(stack, style="Canvas.TFrame")
    technical_shell = ttk.Frame(stack, style="Canvas.TFrame")

    stack.add(quote_shell, minsize=82, stretch="never")
    stack.add(ticket_shell, minsize=310, stretch="always")
    stack.add(context_shell, minsize=145, stretch="never")
    stack.add(technical_shell, minsize=125, stretch="never")

    quote = ttk.LabelFrame(quote_shell, text="Symbol Quote", style="Card.TLabelframe")
    quote.pack(fill=tk.BOTH, expand=True)
    quote.columnconfigure(1, weight=1)
    quote.columnconfigure(3, weight=1)
    options_lab._grid_pair(
        quote,
        0,
        "Symbol",
        ttk.Entry(quote, textvariable=app.options_symbol_var),
        "Underlying",
        ttk.Entry(quote, textvariable=app.options_underlying_price_var),
    )

    ticket = ttk.LabelFrame(ticket_shell, text="Option Trade Ticket", style="Card.TLabelframe")
    ticket.pack(fill=tk.BOTH, expand=True)
    ticket.columnconfigure(1, weight=1)
    ticket.columnconfigure(3, weight=1)
    ticket.rowconfigure(9, weight=1)

    options_lab._grid_pair(ticket, 0, "Action", ttk.Combobox(ticket, textvariable=app.options_action_var, values=options_lab.ACTIONS, state="readonly"), "Strategy", ttk.Combobox(ticket, textvariable=app.options_strategy_var, values=options_lab.STRATEGIES, state="readonly"))
    options_lab._grid_pair(ticket, 1, "Contracts", ttk.Entry(ticket, textvariable=app.options_contracts_var), "Expiration", ttk.Entry(ticket, textvariable=app.options_expiration_var))
    options_lab._grid_pair(ticket, 2, "Strike", ttk.Entry(ticket, textvariable=app.options_strike_var), "Call / Put", ttk.Combobox(ticket, textvariable=app.options_type_var, values=options_lab.OPTION_TYPES, state="readonly"))
    options_lab._grid_pair(ticket, 3, "Bid", ttk.Entry(ticket, textvariable=app.options_bid_var), "Ask", ttk.Entry(ticket, textvariable=app.options_ask_var))
    options_lab._grid_pair(ticket, 4, "Mark", ttk.Entry(ticket, textvariable=app.options_mark_var), "Limit / Debit", ttk.Entry(ticket, textvariable=app.options_premium_var))
    options_lab._grid_pair(ticket, 5, "Order type", ttk.Combobox(ticket, textvariable=app.options_order_type_var, values=options_lab.ORDER_TYPES, state="readonly"), "Time in force", ttk.Combobox(ticket, textvariable=app.options_tif_var, values=options_lab.TIME_IN_FORCE, state="readonly"))
    options_lab._grid_pair(ticket, 6, "Short strike", ttk.Entry(ticket, textvariable=app.options_short_strike_var), "Credit", ttk.Entry(ticket, textvariable=app.options_credit_var))
    options_lab._grid_pair(ticket, 7, "Shares", ttk.Entry(ticket, textvariable=app.options_quantity_var), "Stop price", ttk.Entry(ticket, textvariable=app.options_stop_price_var))
    options_lab._grid_pair(ticket, 8, "Target price", ttk.Entry(ticket, textvariable=app.options_target_price_var), "ATR %", ttk.Entry(ticket, textvariable=app.options_atr_var))

    buttons = ttk.Frame(ticket, style="Panel.TFrame")
    buttons.grid(row=9, column=0, columnspan=4, sticky="sew", pady=(12, 0))
    ttk.Button(buttons, text="Run What-If", command=lambda: options_lab.run_options_what_if(app), style="Accent.TButton").pack(side=tk.LEFT)
    ttk.Button(buttons, text="Sync Current Portfolio", command=lambda: options_lab.load_options_portfolio_values(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(buttons, text="Use Holding Price", command=lambda: options_lab.use_current_symbol_holding_price(app)).pack(side=tk.LEFT, padx=(8, 0))
    ttk.Button(buttons, text="Use Mid as Limit", command=lambda: options_lab.use_mid_as_limit(app)).pack(side=tk.LEFT, padx=(8, 0))

    context = ttk.LabelFrame(context_shell, text="Account + Positions Context", style="Card.TLabelframe")
    context.pack(fill=tk.BOTH, expand=True)
    context.columnconfigure(0, weight=1)
    context.columnconfigure(1, weight=1)

    app.options_portfolio_source_label = ttk.Label(context, text="Source: --", style="Subtle.TLabel")
    app.options_portfolio_source_label.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
    app.options_account_context_label = ttk.Label(context, text="Account: --", style="Subtle.TLabel")
    app.options_account_context_label.grid(row=1, column=0, sticky="w", padx=(0, 10), pady=2)
    app.options_symbol_context_label = ttk.Label(context, text="Selected symbol: --", style="Subtle.TLabel")
    app.options_symbol_context_label.grid(row=1, column=1, sticky="w", pady=2)
    app.options_projected_context_label = ttk.Label(context, text="Projected: --", style="Subtle.TLabel")
    app.options_projected_context_label.grid(row=2, column=0, sticky="w", padx=(0, 10), pady=2)
    app.options_exposure_context_label = ttk.Label(context, text="Exposure: --", style="Subtle.TLabel")
    app.options_exposure_context_label.grid(row=2, column=1, sticky="w", pady=2)

    technical = ttk.LabelFrame(technical_shell, text="Manual Technical Context", style="Card.TLabelframe")
    technical.pack(fill=tk.BOTH, expand=True)
    technical.columnconfigure(1, weight=1)
    technical.columnconfigure(3, weight=1)
    options_lab._grid_pair(technical, 0, "RSI", ttk.Entry(technical, textvariable=app.options_rsi_var), "20 SMA", ttk.Entry(technical, textvariable=app.options_sma_20_var))
    options_lab._grid_pair(technical, 1, "50 SMA", ttk.Entry(technical, textvariable=app.options_sma_50_var), "200 SMA", ttk.Entry(technical, textvariable=app.options_sma_200_var))
    options_lab._grid_pair(technical, 2, "Support", ttk.Entry(technical, textvariable=app.options_support_var), "Resistance", ttk.Entry(technical, textvariable=app.options_resistance_var))


def _build_resizable_options_output(app: tk.Tk, parent: ttk.Frame) -> None:
    parent.columnconfigure(0, weight=1)
    parent.rowconfigure(0, weight=1)

    stack = _make_paned(parent, tk.VERTICAL)
    stack.grid(row=0, column=0, sticky="nsew", padx=(8, 0))

    metrics_shell = ttk.Frame(stack, style="Canvas.TFrame")
    summary_shell = ttk.Frame(stack, style="Canvas.TFrame")
    output_shell = ttk.Frame(stack, style="Canvas.TFrame")
    stack.add(metrics_shell, minsize=160, stretch="never")
    stack.add(summary_shell, minsize=90, stretch="never")
    stack.add(output_shell, minsize=260, stretch="always")

    metrics = ttk.LabelFrame(metrics_shell, text="Risk + Margin Snapshot", style="Card.TLabelframe")
    metrics.pack(fill=tk.BOTH, expand=True)
    metrics.columnconfigure((0, 1, 2), weight=1)
    app.options_max_loss_label = options_lab._metric(metrics, "Max Loss", 0, 0)
    app.options_max_profit_label = options_lab._metric(metrics, "Max Profit", 0, 1)
    app.options_breakeven_label = options_lab._metric(metrics, "Breakeven", 0, 2)
    app.options_margin_label = options_lab._metric(metrics, "BP Effect", 2, 0)
    app.options_portfolio_risk_label = options_lab._metric(metrics, "Portfolio Risk", 2, 1)
    app.options_reward_risk_label = options_lab._metric(metrics, "Reward/Risk", 2, 2)

    summary = ttk.LabelFrame(summary_shell, text="Selected Order", style="Card.TLabelframe")
    summary.pack(fill=tk.BOTH, expand=True)
    summary.columnconfigure(0, weight=1)
    app.options_order_summary_label = ttk.Label(summary, text="--", style="Subtle.TLabel", wraplength=820)
    app.options_order_summary_label.grid(row=0, column=0, sticky="w")

    output = ttk.LabelFrame(output_shell, text="Scenario Analysis", style="Card.TLabelframe")
    output.pack(fill=tk.BOTH, expand=True)
    output.rowconfigure(0, weight=1)
    output.columnconfigure(0, weight=1)

    app.options_output_text = tk.Text(output, height=18, wrap=tk.WORD, font=("Consolas", 10), padx=10, pady=10)
    app.options_output_text.grid(row=0, column=0, sticky="nsew")
    scrollbar = ttk.Scrollbar(output, orient=tk.VERTICAL, command=app.options_output_text.yview)
    scrollbar.grid(row=0, column=1, sticky="ns")
    app.options_output_text.configure(yscrollcommand=scrollbar.set)
