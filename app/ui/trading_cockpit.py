from __future__ import annotations
import os
import tkinter as tk
import json
import webbrowser
from datetime import datetime, timedelta, timezone
from tkinter import messagebox, simpledialog, ttk

from app.brokers.paper import PaperBroker
from app.brokers.schwab.account_adapter import portfolio_from_schwab_account
from app.brokers.schwab.session import SchwabSession
from app.core.order_models import OrderSide, OrderType, TimeInForce
from app.ui.dashboard import PortfolioRiskCockpitApp


class SchwabTradingCockpitApp(PortfolioRiskCockpitApp):
    """UI extension for the Schwab Trading Cockpit.

    This keeps the current Schwab preview/recent-orders/open-only functionality
    from the base dashboard and adds a future cancel-order ID field. Live order
    submission remains disabled while the safety-review UI is staged.
    """

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

        self.symbol_var = tk.StringVar(value="NVDA")
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
        self.schwab_status_var = tk.StringVar(value="Schwab session: not connected")
        self.schwab_preview_status_var = tk.StringVar(value="Last Schwab preview: none")
        self.schwab_verification_status_var = tk.StringVar(
            value="Open Only verified: no · Cancel verified: no"
        )

        self._build_layout()
        self.refresh_portfolio()

    def _build_order_panel(self, parent: ttk.Frame) -> None:
        ticket = ttk.LabelFrame(parent, text="Guarded Paper Order Planner", style="Card.TLabelframe")
        ticket.pack(fill=tk.X)
        ticket.columnconfigure(1, weight=1)
        ticket.columnconfigure(3, weight=1)

        self._grid_row(ticket, 0, "Symbol", ttk.Entry(ticket, textvariable=self.symbol_var), "Side", ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"))
        self._grid_row(ticket, 1, "Order type", ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"), "Time", ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=[t.value for t in TimeInForce], state="readonly"))
        self._grid_row(ticket, 2, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var), "Est. price", ttk.Entry(ticket, textvariable=self.estimated_price_var))
        self._grid_row(ticket, 3, "Limit price", ttk.Entry(ticket, textvariable=self.limit_price_var), "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var))
        self._grid_row(ticket, 4, "Risk % cash", ttk.Entry(ticket, textvariable=self.risk_percent_var), "Type CONFIRM", ttk.Entry(ticket, textvariable=self.confirmation_var))

        button_bar = ttk.Frame(ticket)
        button_bar.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        ttk.Button(button_bar, text="Preview Risk", command=self.preview_order, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(button_bar, text="Schwab Preview", command=self.run_schwab_preview).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Recent Orders", command=self.load_schwab_open_orders).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Open Only", command=self.load_schwab_open_orders_only).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Reset Schwab Session", command=self.reset_schwab_session).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Cancel Order", command=self.show_cancel_order_placeholder).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(ticket, text="Cancel order ID").grid(row=5, column=0, sticky="w", padx=(0, 8), pady=6)
        ttk.Entry(ticket, textvariable=self.cancel_order_id_var).grid(row=5, column=1, columnspan=3, sticky="ew", pady=6)
        ttk.Button(button_bar, text="Live Safety", command=self.show_live_submit_safety_review).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="LIVE Submit", command=self.submit_live_schwab_order_guarded).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Position Size", command=self.show_position_size).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Order Checklist", command=self.show_manual_checklist).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Submit Paper Order", command=self.submit_order).pack(side=tk.RIGHT)

        ttk.Label(ticket, textvariable=self.schwab_status_var, style="Subtle.TLabel").grid(
            row=7,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(8, 0),
        )
        ttk.Label(ticket, textvariable=self.schwab_preview_status_var, style="Subtle.TLabel").grid(
            row=8,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(2, 0),
        )
        ttk.Label(ticket, textvariable=self.schwab_verification_status_var, style="Subtle.TLabel").grid(
            row=9,
            column=0,
            columnspan=4,
            sticky="w",
            pady=(2, 0),
        )

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
                "Limit buy = maximum price. Limit sell = minimum price. Stop = trigger order. "
                "Stop-limit = trigger plus minimum/maximum limit, but may not fill."
            ),
            wraplength=430,
            style="Subtle.TLabel",
        ).pack(anchor=tk.W)

    def _authorize_schwab_session(self) -> SchwabSession | None:
        """Return the in-memory Schwab session or create one through the code flow."""
        if self.schwab_session and self.schwab_session.access_token:
            self.schwab_status_var.set("Schwab session: connected for this app run")
            return self.schwab_session

        session = SchwabSession()
        auth_url, _state = session.build_authorization_url()
        self.schwab_status_var.set("Schwab session: authorization required")
        webbrowser.open(auth_url)

        auth_code = simpledialog.askstring(
            "Schwab Authorization",
            "After Schwab login redirects to your callback page,\n\npaste the authorization code here:",
        )
        if not auth_code:
            self.schwab_status_var.set("Schwab session: not connected")
            return None

        session.exchange_authorization_code(auth_code)
        self.schwab_session = session
        self.schwab_status_var.set("Schwab session: connected for this app run")
        return session

    def _sync_schwab_account_snapshot(self, session: SchwabSession) -> str:
        """Load real Schwab balances/positions into the left portfolio panel."""
        status_code, account_payload = session.get_account(fields="positions")
        if status_code != 200:
            raise RuntimeError(f"Schwab account fetch returned HTTP {status_code}: {account_payload}")

        portfolio, source_message = portfolio_from_schwab_account(account_payload)
        self.broker.set_portfolio(portfolio, source_message)
        self.refresh_portfolio()
        return source_message

    def _update_verification_status(self) -> None:
        open_value = "yes" if self.open_only_verified_this_session else "no"
        cancel_value = "yes" if self.cancel_verified_this_session else "no"
        self.schwab_verification_status_var.set(
            f"Open Only verified: {open_value} · Cancel verified: {cancel_value}"
        )

    def reset_schwab_session(self) -> None:
        """Forget the in-memory Schwab token and session-only safety checks."""
        self.schwab_session = None
        self.last_schwab_preview_status = None
        self.open_only_verified_this_session = False
        self.cancel_verified_this_session = False
        self.schwab_status_var.set("Schwab session: not connected")
        self.schwab_preview_status_var.set("Last Schwab preview: none")
        self._update_verification_status()
        self._set_preview_text(
            "SCHWAB SESSION RESET\n"
            "====================\n\n"
            "The in-memory Schwab session and session-only safety checks were cleared.\n"
            "The next Schwab Preview, Recent Orders, or Open Only action will ask you to authorize again.\n\n"
            "No order was submitted, replaced, or canceled."
        )

    def _record_schwab_preview_status(self, preview_payload: dict) -> None:
        strategy = preview_payload.get("orderStrategy", {}) or {}
        status = str(strategy.get("status") or "UNKNOWN").upper()
        self.last_schwab_preview_status = status
        self.schwab_preview_status_var.set(f"Last Schwab preview: {status}")

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
            self.schwab_status_var.set("Schwab session: connected for this app run")
            if isinstance(preview_payload, dict):
                self._record_schwab_preview_status(preview_payload)
            else:
                self.last_schwab_preview_status = "UNKNOWN"
                self.schwab_preview_status_var.set("Last Schwab preview: UNKNOWN")

            preview_text = self.format_schwab_preview_response(status_code, preview_payload)
            if account_sync_message:
                preview_text = f"ACCOUNT SNAPSHOT\n================\n{account_sync_message}\n\n" + preview_text
            self._set_preview_text(preview_text)
        except Exception as exc:
            self.schwab_session = None
            self.last_schwab_preview_status = None
            self.schwab_status_var.set("Schwab session: not connected")
            self.schwab_preview_status_var.set("Last Schwab preview: none")
            messagebox.showerror("Schwab preview failed", str(exc))

    def load_schwab_open_orders(self) -> None:
        try:
            session = self._authorize_schwab_session()
            if session is None:
                return

            to_time = datetime.now(timezone.utc)
            from_time = to_time - timedelta(days=7)
            status_code, orders_payload = session.get_orders(
                from_entered_time=from_time,
                to_entered_time=to_time,
            )
            self.schwab_status_var.set("Schwab session: connected for this app run")
            self._set_preview_text(self.format_schwab_open_orders_response(status_code, orders_payload))
        except Exception as exc:
            self.schwab_session = None
            self.schwab_status_var.set("Schwab session: not connected")
            messagebox.showerror("Load Schwab recent orders failed", str(exc))

    def load_schwab_open_orders_only(self) -> None:
        try:
            session = self._authorize_schwab_session()
            if session is None:
                return

            to_time = datetime.now(timezone.utc)
            from_time = to_time - timedelta(days=7)
            status_code, orders_payload = session.get_orders(
                from_entered_time=from_time,
                to_entered_time=to_time,
            )
            self.schwab_status_var.set("Schwab session: connected for this app run")
            if status_code == 200:
                self.open_only_verified_this_session = True
                self._update_verification_status()
            self._set_preview_text(self.format_schwab_open_orders_only_response(status_code, orders_payload))
        except Exception as exc:
            self.schwab_session = None
            self.schwab_status_var.set("Schwab session: not connected")
            messagebox.showerror("Load Schwab open orders failed", str(exc))

    def _live_readiness_verdict(
            self,
            *,
            limit_status: str,
            quantity_status: str,
            price_status: str,
            preview_gate: str,
    ) -> tuple[str, str, str, str]:
        mechanical_ready = all(
            status == "PASS"
            for status in [limit_status, quantity_status, price_status]
        )
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
            messagebox.showerror("Live safety review failed", str(exc))
            return

        env_gate = "SCHWAB_ENABLE_LIVE_ORDERS=true"
        confirm_phrase = "PLACE LIVE SCHWAB ORDER"
        order_type = order.order_type.value.upper()
        tif = order.time_in_force.value.upper()
        side = order.side.value.upper()
        symbol = order.symbol.strip().upper()
        preview_status = self.last_schwab_preview_status or "NONE"
        preview_gate = "PASS" if preview_status == "ACCEPTED" else "REQUIRED"
        limit_status = "PASS" if order_type == "LIMIT" else "BLOCKED"
        quantity_status = "PASS" if order.quantity > 0 else "BLOCKED"
        price_status = "PASS" if order.limit_price is not None and order.limit_price > 0 else "BLOCKED"
        overall, mechanical_label, broker_label, human_label = self._live_readiness_verdict(
            limit_status=limit_status,
            quantity_status=quantity_status,
            price_status=price_status,
            preview_gate=preview_gate,
        )

        next_action = self._next_live_safety_action(
            limit_status=limit_status,
            quantity_status=quantity_status,
            price_status=price_status,
            preview_gate=preview_gate,
        )
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
            "No order was submitted, replaced, or canceled.\n\n"
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
            self.schwab_status_var.set("Schwab session: connected for this app run")
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
        """Submit a live Schwab order after a fresh accepted Schwab preview.

        Intended flow:
        Schwab Preview → LIVE Submit → Live Cancel if needed.
        """
        enable_live = os.getenv("SCHWAB_ENABLE_LIVE_ORDERS", "").strip().lower()
        if enable_live != "true":
            messagebox.showerror(
                "Live submit blocked",
                "SCHWAB_ENABLE_LIVE_ORDERS=true is required in your local .env.",
            )
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
            messagebox.showerror(
                "Live submit blocked",
                f"Estimated notional ${estimated_notional:,.2f} exceeds "
                f"SCHWAB_MAX_LIVE_ORDER_DOLLARS=${max_notional:,.2f}.",
            )
            return

        confirmation = self.confirmation_var.get().strip()
        if confirmation != "PLACE LIVE SCHWAB ORDER":
            self._set_preview_text(
                "LIVE SCHWAB SUBMIT BLOCKED\n"
                "==========================\n\n"
                "Exact confirmation phrase required.\n\n"
                "Type exactly into the Type CONFIRM field:\n\n"
                "  PLACE LIVE SCHWAB ORDER\n\n"
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

            # Fresh Schwab preview immediately before submit.
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
            self.schwab_status_var.set("Schwab session: connected for this app run")

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
