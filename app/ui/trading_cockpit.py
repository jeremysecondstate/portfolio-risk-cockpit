from __future__ import annotations
import os
import tkinter as tk
import json
import webbrowser
from datetime import datetime, timedelta, timezone
from tkinter import messagebox, simpledialog, ttk

from app.analytics.technical_analysis import analyze_candles, candles_from_price_history, compare_timeframes
from app.brokers.hyperliquid.client import (
    HyperliquidInfoClient,
    format_hyperliquid_snapshot,
    portfolio_from_hyperliquid_snapshot,
)
from app.brokers.paper import PaperBroker
from app.brokers.schwab.account_adapter import portfolio_from_schwab_account
from app.brokers.schwab.session import SchwabSession, schwab_auth_error_requires_reauthorization
from app.brokers.schwab.token_store import clear_token_payload
from app.core.order_models import SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, OrderSide, OrderType, TimeInForce
from app.core.portfolio import Portfolio, Position
from app.ui.dashboard import PortfolioRiskCockpitApp


class SchwabTradingCockpitApp(PortfolioRiskCockpitApp):
    """UI extension for the Schwab Trading Cockpit."""

    def __init__(self) -> None:
        tk.Tk.__init__(self)
        self.title("Schwab Trading Cockpit")
        self.geometry("1180x760")
        self.minsize(1060, 680)

        self._configure_style()
        self.broker = PaperBroker()
        self.last_preview = None
        self.schwab_session: SchwabSession | None = None
        self.last_schwab_preview_status: str | None = None
        self.open_only_verified_this_session = False
        self.cancel_verified_this_session = False
        self.last_hyperliquid_cash_adjustment = 0.0

        self.symbol_var = tk.StringVar(value="")
        self.side_var = tk.StringVar(value=OrderSide.BUY.value)
        self.order_type_var = tk.StringVar(value=OrderType.LIMIT.value)
        self.quantity_var = tk.StringVar(value="1")
        self.estimated_price_var = tk.StringVar(value="200.00")
        self.limit_price_var = tk.StringVar(value="200.00")
        self.stop_price_var = tk.StringVar(value="")
        self.time_in_force_var = tk.StringVar(value=TimeInForce.DAY.value)
        self.confirmation_var = tk.StringVar(value="")
        self.cancel_order_id_var = tk.StringVar(value="")
        self.cancel_confirmation_var = tk.StringVar(value="")
        self.risk_percent_var = tk.StringVar(value="1.0")
        self.schwab_status_var = tk.StringVar(value="Schwab: not connected")
        self.schwab_preview_status_var = tk.StringVar(value="")
        self.schwab_verification_status_var = tk.StringVar(value="")

        self._build_layout()
        self.refresh_portfolio()

    def _build_order_panel(self, parent: ttk.Frame) -> None:
        ticket = ttk.LabelFrame(parent, text="Guarded Paper Order Planner", style="Card.TLabelframe")
        ticket.pack(fill=tk.X)
        ticket.columnconfigure(1, weight=1)
        ticket.columnconfigure(3, weight=1)

        self._grid_row(ticket, 0, "Symbol", ttk.Entry(ticket, textvariable=self.symbol_var), "Side", ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"))
        self._grid_row(ticket, 1, "Order type", ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"), "Time", ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES, state="readonly"))
        self._grid_row(ticket, 2, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var), "Entry / Limit", ttk.Entry(ticket, textvariable=self.limit_price_var))
        self._grid_row(ticket, 3, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var))
        ttk.Label(ticket, text="Cancel order ID").grid(row=4, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=4, column=1, columnspan=3, sticky="ew", pady=6)

        button_bar = ttk.Frame(ticket)
        button_bar.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        ttk.Button(button_bar, text="Preview Risk", command=self.preview_order, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(button_bar, text="Connect Schwab", command=self.run_schwab_preview).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Connect Hyperliquid", command=self.sync_hyperliquid_account).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Refresh Schwab", command=self.load_schwab_open_orders).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Recent Orders", command=self.load_schwab_open_orders).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Open Only", command=self.load_schwab_open_orders_only).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Reset Session", command=self.reset_schwab_session).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Cancel Order", command=self.show_cancel_order_placeholder).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Technical Analysis", command=self.show_technical_analysis).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="NOT UNIQUE BUTTON", command=self.show_live_submit_safety_review).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="LIVE Submit", command=self.submit_live_schwab_order_guarded).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Position Size", command=self.show_position_size).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Order Checklist", command=self.show_manual_checklist).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Submit Paper Order", command=self.submit_order).pack(side=tk.RIGHT)

        ttk.Label(ticket, textvariable=self.schwab_status_var, style="Subtle.TLabel").grid(row=6, column=0, columnspan=4, sticky="w", pady=(8, 0))

        results = ttk.LabelFrame(parent, text="Risk Preview + Instructions", style="Card.TLabelframe")
        results.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        self.preview_text = tk.Text(results, height=21, wrap=tk.WORD, font=("Consolas", 10), padx=10, pady=10)
        self.preview_text.pack(fill=tk.BOTH, expand=True)
        self._set_preview_text(
            "Create an order and click Preview Risk.\n\n"
            "Reminder: live Schwab orders are disabled. This creates a safe paper plan."
        )

        explainer = ttk.LabelFrame(parent, text="Order Type Cheat Sheet", style="Card.TLabelframe")
        explainer.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(
            explainer,
            text=(
                "Limit buy = maximum price. Limit sell = minimum exit price. Stop = "
                "trigger price. Stop-limit = trigger plus limit, but may not fill."
            ),
            wraplength=430,
            style="Subtle.TLabel",
        ).pack(anchor=tk.W)

    def sync_hyperliquid_account(self) -> None:
        default_address = os.getenv("HYPERLIQUID_USER_ADDRESS", "").strip()
        address = default_address or simpledialog.askstring(
            "Hyperliquid Sync",
            "Enter your Hyperliquid master/sub-account wallet address.\n\n"
            "Use the account address, not the API/agent wallet address.",
        )
        if not address:
            return

        try:
            client = HyperliquidInfoClient()
            snapshot = client.fetch_snapshot(address)
            hyperliquid_portfolio, hyperliquid_source_message = portfolio_from_hyperliquid_snapshot(snapshot)
            merged_portfolio = self._merge_hyperliquid_portfolio(hyperliquid_portfolio)

            base_source_message = self.broker.source_message.split(" + Loaded Hyperliquid account ")[0]
            source_message = f"{base_source_message} + {hyperliquid_source_message}"
            self.broker.set_portfolio(merged_portfolio, source_message)
            self.last_hyperliquid_cash_adjustment = hyperliquid_portfolio.cash
            self.refresh_portfolio()
            self._set_preview_text(format_hyperliquid_snapshot(snapshot, hyperliquid_portfolio))
            messagebox.showinfo("Hyperliquid synced", hyperliquid_source_message)
        except Exception as exc:
            messagebox.showerror("Hyperliquid sync failed", str(exc))

    def _merge_hyperliquid_portfolio(self, hyperliquid_portfolio: Portfolio) -> Portfolio:
        current = self.broker.get_portfolio()
        non_hyperliquid_cash = round(current.cash - self.last_hyperliquid_cash_adjustment, 2)
        positions = {symbol: position for symbol, position in current.positions.items() if not symbol.startswith("HL:")}

        for symbol, position in hyperliquid_portfolio.positions.items():
            display_symbol = f"HL:{symbol}"
            positions[display_symbol] = Position(
                symbol=display_symbol,
                quantity=position.quantity,
                average_cost=position.average_cost,
                last_price=position.last_price,
                day_profit_loss=position.day_profit_loss,
                day_profit_loss_percent=position.day_profit_loss_percent,
                open_profit_loss=position.open_profit_loss,
                unrealized_profit_loss_known=position.unrealized_profit_loss_known,
                cost_basis_estimated=position.cost_basis_estimated,
                raw_profit_loss=position.raw_profit_loss,
                custom_profit_loss=position.custom_profit_loss,
                custom_realized_profit_loss=position.custom_realized_profit_loss,
                custom_unrealized_profit_loss=position.custom_unrealized_profit_loss,
                custom_pnl_status=position.custom_pnl_status,
                basis_status=position.basis_status,
            )

        return Portfolio(cash=round(non_hyperliquid_cash + hyperliquid_portfolio.cash, 2), positions=positions)

    def _authorize_schwab_session(self, *, interactive: bool = True) -> SchwabSession | None:
        if self.schwab_session:
            try:
                self.schwab_session.ensure_access_token()
                self.schwab_status_var.set("Schwab: connected")
                return self.schwab_session
            except Exception as exc:
                has_saved_authorization = self.schwab_session.has_cached_authorization()
                if schwab_auth_error_requires_reauthorization(exc):
                    self.schwab_session.clear_cached_authorization()
                    self.schwab_session = None
                    self.schwab_status_var.set("Schwab: login required")
                    if not interactive:
                        raise RuntimeError("Schwab saved authorization was rejected; manual login is required.") from exc
                elif has_saved_authorization:
                    self.schwab_status_var.set("Schwab: token refresh failed; saved authorization kept")
                    raise
                else:
                    self.schwab_session = None

        session = SchwabSession()
        if session.has_cached_authorization():
            try:
                session.ensure_access_token()
                self.schwab_session = session
                self.schwab_status_var.set("Schwab: connected")
                return session
            except Exception as exc:
                if schwab_auth_error_requires_reauthorization(exc):
                    session.clear_cached_authorization()
                    self.schwab_status_var.set("Schwab: login required")
                    if not interactive:
                        raise RuntimeError("Schwab saved authorization was rejected; manual login is required.") from exc
                else:
                    self.schwab_status_var.set("Schwab: token refresh failed; saved authorization kept")
                    raise

        if not interactive:
            self.schwab_status_var.set("Schwab: login required")
            raise RuntimeError("Schwab saved authorization is unavailable; manual login is required.")

        auth_url, _state = session.build_authorization_url()
        self.schwab_status_var.set("Schwab: authorization required")
        webbrowser.open(auth_url)

        auth_code = simpledialog.askstring(
            "Schwab Authorization",
            "After Schwab login redirects to your callback page,\n\npaste the authorization code here:",
        )
        if not auth_code:
            self.schwab_status_var.set("Schwab: not connected")
            return None

        session.exchange_authorization_code(auth_code)
        self.schwab_session = session
        self.schwab_status_var.set("Schwab: connected")
        return session

    def _sync_schwab_account_snapshot(self, session: SchwabSession) -> str:
        status_code, account_payload = session.get_account(fields="positions")
        if status_code != 200:
            raise RuntimeError(f"Schwab account fetch returned HTTP {status_code}: {account_payload}")

        portfolio, source_message = portfolio_from_schwab_account(account_payload)
        self.broker.set_portfolio(portfolio, source_message)
        self.last_hyperliquid_cash_adjustment = 0.0
        self.refresh_portfolio()
        return source_message

    def _update_verification_status(self) -> None:
        self.schwab_verification_status_var.set("")

    def reset_schwab_session(self) -> None:
        clear_token_payload()
        self.schwab_session = None
        self.last_schwab_preview_status = None
        self.open_only_verified_this_session = False
        self.cancel_verified_this_session = False
        self.schwab_status_var.set("Schwab: not connected")
        self.schwab_preview_status_var.set("")
        self._update_verification_status()
        self._set_preview_text(
            "SCHWAB SESSION RESET\n"
            "====================\n\n"
            "The in-memory Schwab session, saved local token cache, and session-only safety checks were cleared.\n"
            "The next Schwab action will ask you to authorize again.\n\n"
        )

    def _record_schwab_preview_status(self, preview_payload: dict) -> None:
        strategy = preview_payload.get("orderStrategy", {}) or {}
        status = str(strategy.get("status") or "UNKNOWN").upper()
        self.last_schwab_preview_status = status

    def run_schwab_preview(self) -> None:
        try:
            session = self._authorize_schwab_session()
            if session is None:
                return

            account_sync_message = None
            try:
                account_sync_message = self._sync_schwab_account_snapshot(session)
            except Exception as exc:
                account_sync_message = f"Schwab account sync failed: {exc}"

            status_code, preview_payload = session.preview_order(self.build_schwab_order_json_from_ui())
            self.schwab_status_var.set("Schwab: connected")
            if isinstance(preview_payload, dict):
                self._record_schwab_preview_status(preview_payload)
            else:
                self.last_schwab_preview_status = "UNKNOWN"

            preview_text = self.format_schwab_preview_response(status_code, preview_payload)
            if account_sync_message:
                preview_text = f"ACCOUNT SNAPSHOT\n================\n{account_sync_message}\n\n" + preview_text
            self._set_preview_text(preview_text)
        except Exception as exc:
            self.schwab_session = None
            self.last_schwab_preview_status = None
            self.schwab_status_var.set("Schwab: not connected")
            self.schwab_preview_status_var.set("")
            messagebox.showerror("Schwab preview failed", str(exc))

    def show_technical_analysis(self) -> None:
        symbol = self.symbol_var.get().strip().upper()
        if not symbol:
            messagebox.showerror("Technical analysis failed", "Enter a symbol first.")
            return

        try:
            session = self._authorize_schwab_session()
            if session is None:
                return

            intraday_status_code, intraday_payload = session.get_price_history(symbol, period_type="day", period=10, frequency_type="minute", frequency=5, need_extended_hours_data=False)
            if intraday_status_code != 200:
                raise RuntimeError(f"Schwab intraday price history returned HTTP {intraday_status_code}: {intraday_payload}")
            daily_status_code, daily_payload = session.get_price_history(symbol, period_type="year", period=1, frequency_type="daily", frequency=1, need_extended_hours_data=False)
            if daily_status_code != 200:
                raise RuntimeError(f"Schwab daily price history returned HTTP {daily_status_code}: {daily_payload}")

            intraday_report = analyze_candles(symbol, candles_from_price_history(intraday_payload))
            daily_report = analyze_candles(symbol, candles_from_price_history(daily_payload))
            report = compare_timeframes(symbol, intraday_report, daily_report)
            self.schwab_status_var.set("Schwab: connected")
            self._set_preview_text(self.format_technical_analysis_report(report))
        except Exception as exc:
            messagebox.showerror("Technical analysis failed", str(exc))

    def format_technical_analysis_report(self, report) -> str:
        lines = [
            f"MULTI-TIMEFRAME TECHNICAL ANALYSIS — {report.symbol}",
            "=" * (39 + len(report.symbol)),
            "",
            "Timeframes:",
            "- Intraday: 10 trading days of 5-minute Schwab candles for short-term momentum/timing.",
            "- Daily: 1 year of 1-day Schwab candles for bigger-picture trend context.",
            "",
        ]
        self._append_single_timeframe_report(lines, "DAILY CONTEXT", report.daily)
        lines.append("")
        self._append_single_timeframe_report(lines, "INTRADAY TIMING", report.intraday)
        lines.extend(["", "Timeframe comparison:"])
        lines.extend(f"- {line}" for line in report.comparison_lines)
        lines.extend(["", "Notes:", "- RSI is a momentum oscillator; 70+ is commonly treated as overbought and 30 or below as oversold.", "- MACD compares short and long exponential moving averages; MACD above signal is bullish momentum, below signal is bearish momentum.", "- Daily candles are better for context. 5-minute candles are better for timing. This is analysis, not a recommendation."])
        return "\n".join(lines)

    def _append_single_timeframe_report(self, lines: list[str], heading: str, report) -> None:
        lines.extend([heading, "-" * len(heading), f"Candles analyzed: {report.candle_count}", f"Latest close: ${report.latest_close:,.2f}", f"Overall bias: {report.overall_bias.value}", f"20-period SMA: {_format_optional_number(report.sma_fast)}", f"50-period SMA: {_format_optional_number(report.sma_slow)}", f"RSI(14): {_format_optional_number(report.rsi)}", f"MACD(12,26,9): {_format_optional_number(report.macd)}", f"MACD signal: {_format_optional_number(report.macd_signal)}", f"MACD histogram: {_format_optional_number(report.macd_histogram)}", "Interpretation:"])
        lines.extend(f"- {line}" for line in report.lines)

    def load_schwab_open_orders(self) -> None:
        try:
            session = self._authorize_schwab_session()
            if session is None:
                return
            to_time = datetime.now(timezone.utc)
            from_time = to_time - timedelta(days=7)
            status_code, orders_payload = session.get_orders(from_entered_time=from_time, to_entered_time=to_time)
            self.schwab_status_var.set("Schwab: connected")
            self._set_preview_text(self.format_schwab_open_orders_response(status_code, orders_payload))
        except Exception as exc:
            self.schwab_session = None
            self.schwab_status_var.set("Schwab: not connected")
            messagebox.showerror("Load Schwab recent orders failed", str(exc))

    def load_schwab_open_orders_only(self) -> None:
        try:
            session = self._authorize_schwab_session()
            if session is None:
                return
            to_time = datetime.now(timezone.utc)
            from_time = to_time - timedelta(days=7)
            status_code, orders_payload = session.get_orders(from_entered_time=from_time, to_entered_time=to_time)
            self.schwab_status_var.set("Schwab: connected")
            if status_code == 200:
                self.open_only_verified_this_session = True
                self._update_verification_status()
            self._set_preview_text(self.format_schwab_open_orders_only_response(status_code, orders_payload))
        except Exception as exc:
            self.schwab_session = None
            self.schwab_status_var.set("Schwab: not connected")
            messagebox.showerror("Load Schwab open orders failed", str(exc))

    def _live_readiness_verdict(
            self,
            *,
            limit_status: str,
            quantity_status: str,
            price_status: str,
            preview_gate: str,
    ) -> tuple[str, str, str, str]:
        mechanical_ready = all(status == "PASS" for status in [limit_status, quantity_status, price_status])
        broker_ready = preview_gate == "PASS"
        human_ready = False
        mechanical_label = "PASS" if mechanical_ready else "PARTIAL"
        broker_label = "PASS" if broker_ready else "PARTIAL"
        human_label = "REQUIRED" if not human_ready else "PASS"
        overall = "DISABLED — live submit endpoint is not wired"
        return overall, mechanical_label, broker_label, human_label

    def _next_live_safety_action(
            self,
            *,
            limit_status: str,
            quantity_status: str,
            price_status: str,
            preview_gate: str,
    ) -> str:
        if limit_status != "PASS":
            return "Set order type to LIMIT."
        if quantity_status != "PASS":
            return "Enter a positive quantity."
        if price_status != "PASS":
            return "Enter a positive limit price."
        if preview_gate != "PASS":
            return "Run Schwab Preview and confirm the Schwab status is ACCEPTED."
        return "Core gates are green. Intended flow: Schwab Preview → Live Submit → Live Cancel if needed."

    def show_live_submit_safety_review(self) -> None:
        try:
            order = self._parse_order()
            schwab_order = self.build_schwab_order_json_from_ui()
        except Exception as exc:
            messagebox.showerror("NOT UNIQUE BUTTON review failed", str(exc))
            return

        env_gate = "SCHWAB_ENABLE_LIVE_ORDERS=true"
        confirm_phrase = "PLACE"
        order_type = order.order_type.value.upper()
        tif = order.time_in_force.value.upper()
        side = order.side.value.upper()
        symbol = order.symbol.strip().upper()
        preview_status = self.last_schwab_preview_status or "NONE"
        preview_gate = "PASS" if preview_status == "ACCEPTED" else "REQUIRED"
        limit_status = "PASS" if order_type == "LIMIT" else "BLOCKED"
        quantity_status = "PASS" if order.quantity > 0 else "BLOCKED"
        price_status = "PASS" if order.limit_price is not None and order.limit_price > 0 else "BLOCKED"
        overall, mechanical_label, broker_label, human_label = self._live_readiness_verdict(limit_status=limit_status, quantity_status=quantity_status, price_status=price_status, preview_gate=preview_gate)
        next_action = self._next_live_safety_action(limit_status=limit_status, quantity_status=quantity_status, price_status=price_status, preview_gate=preview_gate)
        formatted_schwab_order = json.dumps(schwab_order, indent=2)

        self._set_preview_text(
            "LIVE SUBMIT SAFETY REVIEW\n"
            "=========================\n\n"
            f"Overall live readiness: {overall}\n"
            f"Next required action: {next_action}\n"
            f"Mechanical ticket gates: {mechanical_label}\n"
            f"Broker/session gates: {broker_label}\n"
            f"Human confirmation gates: {human_label}\n\n"
            "Status: LIVE SUBMIT DISABLED.\n"
            "Current ticket:\n"
            f"- Symbol: {symbol}\n"
            f"- Side: {side}\n"
            f"- Type: {order_type}\n"
            f"- Quantity: {order.quantity:g}\n"
            f"- Limit price: {order.limit_price}\n"
            f"- Time in force: {tif}\n\n"
            "Current Schwab readiness state:\n"
            f"- Last Schwab preview status: {preview_status}\n"
            "- Open Only: available separately\n"
            "- Cancel: available separately\n\n"
            "Safety gates required before any future live submit:\n"
            f"- LIMIT order only: {limit_status}\n"
            f"- Positive quantity: {quantity_status}\n"
            f"- Positive limit price: {price_status}\n"
            "- Schwab previewOrder must be run immediately before submit: REQUIRED\n"
            f"- Schwab previewOrder status must be ACCEPTED: {preview_gate}\n"
            f"- Local .env must explicitly contain {env_gate}: REQUIRED\n"
            f"- User must type exact phrase: {confirm_phrase}: REQUIRED\n"
            "- Final warning dialog must be accepted: REQUIRED\n\n"
            "Schwab order JSON that would be previewed/submitted in a future live-submit phase:\n"
            f"{formatted_schwab_order}\n\n"
            "This screen is informational only. The live submit endpoint is not wired here."
        )

    def show_cancel_order_placeholder(self) -> None:
        order_id = self.cancel_order_id_var.get().strip()
        if not order_id:
            messagebox.showerror("Cancel blocked", "Enter an active Schwab order ID first.")
            return

        try:
            session = self._authorize_schwab_session()
            if session is None:
                return
            status_code, payload = session.cancel_order(order_id)
            self.schwab_status_var.set("Schwab: connected")
            if 200 <= status_code < 300:
                self.cancel_verified_this_session = True
                self._update_verification_status()
            self._set_preview_text(
                "SCHWAB CANCEL ORDER RESULT\n"
                "==========================\n\n"
                f"HTTP Status: {status_code}\n"
                f"Order ID: {order_id}\n\n"
                f"Response: {payload if payload is not None else '(empty response body)'}\n\n"
                "Next step: click Open Only to verify the order is no longer active.\n\n"
                "No order was submitted or replaced."
            )
        except Exception as exc:
            messagebox.showerror("Schwab cancel failed", str(exc))

    def submit_live_schwab_order_guarded(self) -> None:
        enable_live = os.getenv("SCHWAB_ENABLE_LIVE_ORDERS", "").strip().lower()
        if enable_live != "true":
            messagebox.showerror("Live submit blocked", "SCHWAB_ENABLE_LIVE_ORDERS=true is required in your local .env.")
            return

        try:
            order = self._parse_order()
            schwab_order = self.build_schwab_order_json_from_ui()
        except Exception as exc:
            messagebox.showerror("Live submit blocked", str(exc))
            return

        order_type = order.order_type.value.upper()
        if order_type != "LIMIT":
            messagebox.showerror("Live submit blocked", "Only LIMIT orders are allowed for this cockpit.")
            return
        if order.quantity <= 0:
            messagebox.showerror("Live submit blocked", "Quantity must be positive.")
            return
        if order.limit_price is None or order.limit_price <= 0:
            messagebox.showerror("Live submit blocked", "A positive limit price is required.")
            return

        max_notional = float(os.getenv("SCHWAB_MAX_LIVE_ORDER_DOLLARS", "500"))
        estimated_notional = order.quantity * order.limit_price
        if estimated_notional > max_notional:
            messagebox.showerror("Live submit blocked", f"Estimated notional ${estimated_notional:,.2f} exceeds SCHWAB_MAX_LIVE_ORDER_DOLLARS=${max_notional:,.2f}.")
            return

        confirmation = self.confirmation_var.get().strip()
        if confirmation != "PLACE":
            self._set_preview_text(
                "LIVE SCHWAB SUBMIT BLOCKED\n"
                "==========================\n\n"
                "Exact confirmation phrase required.\n\n"
                "Type exactly into the confirmation field:\n\n"
                "  PLACE\n\n"
                "No live order was submitted."
            )
            return

        ok = messagebox.askyesno(
            "FINAL LIVE SCHWAB ORDER CONFIRMATION",
            "This will submit a LIVE Schwab order.\n\n"
            f"Symbol: {order.symbol.strip().upper()}\n"
            f"Side: {order.side.value.upper()}\n"
            f"Type: {order_type}\n"
            f"Quantity: {order.quantity:g}\n"
            f"Limit price: {order.limit_price}\n"
            f"Estimated notional: ${estimated_notional:,.2f}\n\n"
            "This order can fill. Continue?",
        )
        if not ok:
            return

        try:
            session = self._authorize_schwab_session()
            if session is None:
                return
            preview_status_code, preview_payload = session.preview_order(schwab_order)
            if isinstance(preview_payload, dict):
                self._record_schwab_preview_status(preview_payload)
            strategy = (preview_payload or {}).get("orderStrategy", {}) if isinstance(preview_payload, dict) else {}
            schwab_status = str(strategy.get("status") or "UNKNOWN").upper()
            if preview_status_code != 200 or schwab_status != "ACCEPTED":
                self._set_preview_text(
                    "LIVE SCHWAB SUBMIT BLOCKED\n"
                    "==========================\n\n"
                    f"Immediate preview HTTP status: {preview_status_code}\n"
                    f"Immediate preview Schwab status: {schwab_status}\n\n"
                    "Schwab previewOrder must return ACCEPTED immediately before submit.\n\n"
                    "No live order was submitted."
                )
                return
            submit_status_code, submit_payload, location = session.submit_live_order(schwab_order)
            self.schwab_status_var.set("Schwab: connected")
            self._set_preview_text(
                "LIVE SCHWAB ORDER SUBMIT RESULT\n"
                "===============================\n\n"
                f"HTTP Status: {submit_status_code}\n"
                f"Location: {location or '(none returned)'}\n"
                f"Response: {submit_payload if submit_payload is not None else '(empty response body)'}\n\n"
                "Next step: use Cancel Order if this is a test order you want to cancel."
            )
        except Exception as exc:
            messagebox.showerror("Live Schwab submit failed", str(exc))


def _format_optional_number(value: float | None) -> str:
    return "--" if value is None else f"{value:,.3f}"
