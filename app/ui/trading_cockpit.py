from __future__ import annotations

import tkinter as tk
import webbrowser
from datetime import datetime, timedelta, timezone
from tkinter import messagebox, simpledialog, ttk

from app.brokers.paper import PaperBroker
from app.brokers.schwab.session import SchwabSession
from app.core.order_models import OrderSide, OrderType, TimeInForce
from app.ui.dashboard import PortfolioRiskCockpitApp


class SchwabTradingCockpitApp(PortfolioRiskCockpitApp):
    """UI extension for the Schwab Trading Cockpit.

    This keeps the current Schwab preview/recent-orders/open-only functionality
    from the base dashboard and adds a future cancel-order ID field. The cancel
    button remains placeholder-only and does not call Schwab DELETE.
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
        self._grid_row(ticket, 5, "Cancel order ID", ttk.Entry(ticket, textvariable=self.cancel_order_id_var), "Cancel confirm", ttk.Entry(ticket, textvariable=self.cancel_confirmation_var))

        button_bar = ttk.Frame(ticket)
        button_bar.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        ttk.Button(button_bar, text="Preview Risk", command=self.preview_order, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(button_bar, text="Schwab Preview", command=self.run_schwab_preview).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Recent Orders", command=self.load_schwab_open_orders).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Open Only", command=self.load_schwab_open_orders_only).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Reset Schwab Session", command=self.reset_schwab_session).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Cancel Order", command=self.show_cancel_order_placeholder).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Position Size", command=self.show_position_size).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Order Checklist", command=self.show_manual_checklist).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(button_bar, text="Submit Paper Order", command=self.submit_order).pack(side=tk.RIGHT)

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
            return self.schwab_session

        session = SchwabSession()
        auth_url, _state = session.build_authorization_url()
        webbrowser.open(auth_url)

        auth_code = simpledialog.askstring(
            "Schwab Authorization",
            "After Schwab login redirects to your callback page,\n\npaste the authorization code here:",
        )
        if not auth_code:
            return None

        session.exchange_authorization_code(auth_code)
        self.schwab_session = session
        return session

    def reset_schwab_session(self) -> None:
        """Forget the in-memory Schwab token for this app run."""
        self.schwab_session = None
        self._set_preview_text(
            "SCHWAB SESSION RESET\n"
            "====================\n\n"
            "The in-memory Schwab session was cleared.\n"
            "The next Schwab Preview, Recent Orders, or Open Only action will ask you to authorize again.\n\n"
            "No order was submitted, replaced, or canceled."
        )

    def run_schwab_preview(self) -> None:
        try:
            session = self._authorize_schwab_session()
            if session is None:
                return

            status_code, preview_payload = session.preview_order(self.build_schwab_order_json_from_ui())
            self._set_preview_text(self.format_schwab_preview_response(status_code, preview_payload))
        except Exception as exc:
            self.schwab_session = None
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
            self._set_preview_text(self.format_schwab_open_orders_response(status_code, orders_payload))
        except Exception as exc:
            self.schwab_session = None
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
            self._set_preview_text(self.format_schwab_open_orders_only_response(status_code, orders_payload))
        except Exception as exc:
            self.schwab_session = None
            messagebox.showerror("Load Schwab open orders failed", str(exc))

    def show_cancel_order_placeholder(self) -> None:
        order_id = self.cancel_order_id_var.get().strip() or "(none entered)"
        confirmation = self.cancel_confirmation_var.get().strip() or "(none entered)"

        self._set_preview_text(
            "SCHWAB CANCEL ORDER\n"
            "===================\n\n"
            "Status: placeholder only.\n\n"
            f"Entered cancel order ID: {order_id}\n"
            f"Entered cancel confirmation: {confirmation}\n\n"
            "No cancel request was sent to Schwab.\n"
            "No order was submitted, replaced, or canceled.\n\n"
            "Future cancel workflow:\n"
            "1. Load Open Only orders.\n"
            "2. Select or paste a known active/open order ID.\n"
            "3. Confirm the order ID, symbol, side, quantity, price, and status.\n"
            "4. Type a strict confirmation phrase.\n"
            "5. Send Schwab DELETE /accounts/{accountHash}/orders/{orderId}.\n"
            "6. Reload Open Only orders to verify the order is canceled.\n\n"
            "Cancel support will only be wired after we have a safe active order to test."
        )
