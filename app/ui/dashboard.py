from __future__ import annotations

import json
import os
import secrets
import urllib.parse
import webbrowser

import requests
from dotenv import load_dotenv
from tkinter import simpledialog

import tkinter as tk
from tkinter import ttk, messagebox

from app.brokers.paper import PaperBroker
from app.core.order_checklist import build_manual_order_checklist
from app.core.order_models import OrderRequest, OrderSide, OrderType, TimeInForce
from app.core.position_sizing import calculate_position_size

load_dotenv()


class PortfolioRiskCockpitApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Schwab Trading Cockpit")
        self.geometry("1180x760")
        self.minsize(1060, 680)

        self._configure_style()
        self.broker = PaperBroker()
        self.last_preview = None

        self.symbol_var = tk.StringVar(value="NVDA")
        self.side_var = tk.StringVar(value=OrderSide.BUY.value)
        self.order_type_var = tk.StringVar(value=OrderType.LIMIT.value)
        self.quantity_var = tk.StringVar(value="1")
        self.estimated_price_var = tk.StringVar(value="200.00")
        self.limit_price_var = tk.StringVar(value="200.00")
        self.stop_price_var = tk.StringVar(value="")
        self.time_in_force_var = tk.StringVar(value=TimeInForce.DAY.value)
        self.confirmation_var = tk.StringVar(value="")
        self.risk_percent_var = tk.StringVar(value="1.0")

        self._build_layout()
        self.refresh_portfolio()

    def _configure_style(self) -> None:
        self.option_add("*Font", "{Segoe UI} 10")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Header.TLabel", font=("Segoe UI", 20, "bold"))
        style.configure("Subtle.TLabel", foreground="#666666")
        style.configure("Mode.TLabel", foreground="#0a7f2e", font=("Segoe UI", 11, "bold"))
        style.configure("Danger.TLabel", foreground="#a83232", font=("Segoe UI", 10, "bold"))
        style.configure("Card.TLabelframe", padding=12)
        style.configure("Card.TLabelframe.Label", font=("Segoe UI", 11, "bold"))
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=26)

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill=tk.BOTH, expand=True)

        self._build_header(root)

        body = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        left = ttk.Frame(body, padding=(0, 0, 10, 0))
        right = ttk.Frame(body, padding=(10, 0, 0, 0))
        body.add(left, weight=3)
        body.add(right, weight=2)

        self._build_portfolio_panel(left)
        self._build_order_panel(right)

    def _build_header(self, parent: ttk.Frame) -> None:
        header = ttk.Frame(parent)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Schwab Trading Cockpit", style="Header.TLabel").pack(side=tk.LEFT)

        right = ttk.Frame(header)
        right.pack(side=tk.RIGHT)
        ttk.Label(right, text="SCHWAB TRADING COCKPIT", style="Mode.TLabel").pack(anchor=tk.E)
        ttk.Label(right, text="Live Schwab orders disabled — paper planning only", style="Subtle.TLabel").pack(anchor=tk.E)

    def _build_portfolio_panel(self, parent: ttk.Frame) -> None:
        summary = ttk.LabelFrame(parent, text="Schwab Account Snapshot", style="Card.TLabelframe")
        summary.pack(fill=tk.X)
        summary.columnconfigure((0, 1, 2), weight=1)

        self.cash_value_label = self._metric(summary, "Cash", 0)
        self.positions_value_label = self._metric(summary, "Positions", 1)
        self.total_value_label = self._metric(summary, "Total Value", 2)

        self.snapshot_source_label = ttk.Label(summary, text="Snapshot: --", style="Subtle.TLabel")
        self.snapshot_source_label.grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))

        snapshot_buttons = ttk.Frame(summary)
        snapshot_buttons.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Button(snapshot_buttons, text="Reload Schwab Snapshot", command=self.reload_snapshot).pack(side=tk.LEFT)
        ttk.Button(snapshot_buttons, text="Refresh View", command=self.refresh_portfolio).pack(side=tk.LEFT, padx=(8, 0))

        positions_frame = ttk.LabelFrame(parent, text="Positions", style="Card.TLabelframe")
        positions_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        columns = ("symbol", "qty", "avg_cost", "last", "value", "weight")
        self.positions_table = ttk.Treeview(positions_frame, columns=columns, show="headings", height=18)
        for column, label, width in [
            ("symbol", "Symbol", 86),
            ("qty", "Qty", 92),
            ("avg_cost", "Avg Cost", 112),
            ("last", "Last", 112),
            ("value", "Value", 118),
            ("weight", "Weight", 88),
        ]:
            self.positions_table.heading(column, text=label)
            self.positions_table.column(column, width=width, anchor=tk.E)
        self.positions_table.column("symbol", anchor=tk.W)
        self.positions_table.pack(fill=tk.BOTH, expand=True)

        help_box = ttk.LabelFrame(parent, text="Safety Rules", style="Card.TLabelframe")
        help_box.pack(fill=tk.X, pady=(12, 0))
        ttk.Label(
            help_box,
            text=(
                "Schwab account sync is read-only. Live order placement is disabled until "
                "previewOrder, typed confirmation, max-size checks, margin checks, and audit "
                "logging are fully wired in."
            ),
            wraplength=640,
            style="Subtle.TLabel",
        ).pack(anchor=tk.W)

    def _metric(self, parent: ttk.Frame, title: str, column: int) -> ttk.Label:
        ttk.Label(parent, text=title, style="Subtle.TLabel").grid(row=0, column=column, sticky="w")
        value_label = ttk.Label(parent, text="--", font=("Segoe UI", 16, "bold"))
        value_label.grid(row=1, column=column, sticky="w", pady=(2, 0))
        return value_label

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
        button_bar.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        ttk.Button(button_bar, text="Preview Risk", command=self.preview_order, style="Accent.TButton").pack(side=tk.LEFT)
        ttk.Button(button_bar, text="Schwab Preview", command=self.run_schwab_preview).pack(side=tk.LEFT, padx=(8, 0))
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

    def build_schwab_order_json_from_ui(self) -> dict:
        order = self._parse_order()

        schwab_order = {
            "orderType": order.order_type.value.upper(),
            "session": "NORMAL",
            "duration": order.time_in_force.value.upper(),
            "orderStrategyType": "SINGLE",
            "orderLegCollection": [
                {
                    "instruction": order.side.value.upper(),
                    "quantity": order.quantity,
                    "instrument": {
                        "symbol": order.symbol.strip().upper(),
                        "assetType": "EQUITY",
                    },
                }
            ],
        }

        if order.limit_price is not None:
            schwab_order["price"] = f"{order.limit_price:.2f}"

        if order.stop_price is not None:
            schwab_order["stopPrice"] = f"{order.stop_price:.2f}"

        return schwab_order

    def show_schwab_preview_status(self) -> None:
        try:
            schwab_order = self.build_schwab_order_json_from_ui()
        except Exception as exc:
            messagebox.showerror("Invalid Schwab preview ticket", str(exc))
            return

        import json

        self._set_preview_text(
            "SCHWAB PREVIEW ORDER JSON\n"
            "=========================\n\n"
            "This is the order JSON that will be sent to Schwab previewOrder in the next chunk.\n"
            "No API call was made. No live order was placed.\n\n"
            f"{json.dumps(schwab_order, indent=2)}\n\n"
            "Next chunk: send this JSON to Schwab previewOrder and display Schwab's rejects/warnings here."
        )

    def run_schwab_preview(self) -> None:
        try:
            client_id = os.getenv("SCHWAB_CLIENT_ID")
            client_secret = os.getenv("SCHWAB_CLIENT_SECRET")
            redirect_uri = os.getenv("SCHWAB_REDIRECT_URI")

            if not client_id or not client_secret or not redirect_uri:
                raise RuntimeError(
                    "Missing SCHWAB_CLIENT_ID / SCHWAB_CLIENT_SECRET / SCHWAB_REDIRECT_URI in .env"
                )

            state = secrets.token_urlsafe(24)

            auth_url = (
                    "https://api.schwabapi.com/v1/oauth/authorize?"
                    + urllib.parse.urlencode(
                {
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "scope": "readonly",
                    "state": state,
                }
            )
            )

            webbrowser.open(auth_url)

            auth_code = simpledialog.askstring(
                "Schwab Authorization",
                "After Schwab login redirects to your callback page,\n\npaste the authorization code here:",
            )

            if not auth_code:
                return

            token_response = requests.post(
                "https://api.schwabapi.com/v1/oauth/token",
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={
                    "grant_type": "authorization_code",
                    "code": auth_code,
                    "redirect_uri": redirect_uri,
                },
                auth=(client_id, client_secret),
                timeout=30,
            )

            token_response.raise_for_status()

            access_token = token_response.json()["access_token"]

            headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }

            account_response = requests.get(
                "https://api.schwabapi.com/trader/v1/accounts/accountNumbers",
                headers=headers,
                timeout=30,
            )

            account_response.raise_for_status()

            accounts = account_response.json()

            if not accounts:
                raise RuntimeError("No Schwab accounts returned.")

            account_hash = accounts[0]["hashValue"]

            schwab_order = self.build_schwab_order_json_from_ui()

            preview_response = requests.post(
                f"https://api.schwabapi.com/trader/v1/accounts/{account_hash}/previewOrder",
                headers={
                    **headers,
                    "Content-Type": "application/json",
                },
                json=schwab_order,
                timeout=30,
            )

            preview_payload = preview_response.json()

            self._set_preview_text(
                self.format_schwab_preview_response(preview_response.status_code, preview_payload)
            )

        except Exception as exc:
            messagebox.showerror("Schwab preview failed", str(exc))

    def format_schwab_preview_response(self, status_code: int, payload: dict) -> str:
        strategy = payload.get("orderStrategy", {}) or {}
        balance = strategy.get("orderBalance", {}) or {}
        legs = strategy.get("orderLegs", []) or []
        first_leg = legs[0] if legs else {}
        validation = payload.get("orderValidationResult", {}) or {}

        status = strategy.get("status", "UNKNOWN")
        order_type = strategy.get("orderType", "UNKNOWN")
        duration = strategy.get("duration", "UNKNOWN")
        session = strategy.get("session", "UNKNOWN")
        price = strategy.get("price")
        quantity = strategy.get("quantity")
        order_value = balance.get("orderValue")
        projected_available = balance.get("projectedAvailableFund")
        projected_buying_power = balance.get("projectedBuyingPower")
        projected_commission = balance.get("projectedCommission")

        symbol = first_leg.get("finalSymbol") or (first_leg.get("instrument") or {}).get("symbol", "UNKNOWN")
        side = first_leg.get("instruction", "UNKNOWN")
        bid = first_leg.get("bidPrice")
        ask = first_leg.get("askPrice")
        last = first_leg.get("lastPrice")
        mark = first_leg.get("markPrice")

        lines = [
            "SCHWAB PREVIEW RESULT",
            "=====================",
            "",
            f"HTTP Status: {status_code}",
            f"Schwab Status: {status}",
            "",
            "Order:",
            f"- Symbol: {symbol}",
            f"- Side: {side}",
            f"- Type: {order_type}",
            f"- Quantity: {quantity}",
            f"- Limit price: ${price:,.2f}" if isinstance(price, (int, float)) else f"- Limit price: {price}",
            f"- Duration: {duration}",
            f"- Session: {session}",
            "",
            "Market snapshot:",
            f"- Bid: ${bid:,.2f}" if isinstance(bid, (int, float)) else f"- Bid: {bid}",
            f"- Ask: ${ask:,.2f}" if isinstance(ask, (int, float)) else f"- Ask: {ask}",
            f"- Last: ${last:,.2f}" if isinstance(last, (int, float)) else f"- Last: {last}",
            f"- Mark: ${mark:,.2f}" if isinstance(mark, (int, float)) else f"- Mark: {mark}",
            "",
            "Projected impact:",
            f"- Order value: ${order_value:,.2f}" if isinstance(order_value,
                                                                (int, float)) else f"- Order value: {order_value}",
            f"- Available funds after: ${projected_available:,.2f}" if isinstance(projected_available, (int,
                                                                                                        float)) else f"- Available funds after: {projected_available}",
            f"- Buying power after: ${projected_buying_power:,.2f}" if isinstance(projected_buying_power, (int,
                                                                                                           float)) else f"- Buying power after: {projected_buying_power}",
            f"- Projected commission: ${projected_commission:,.2f}" if isinstance(projected_commission, (int,
                                                                                                         float)) else f"- Projected commission: {projected_commission}",
            "",
        ]

        for bucket in ["rejects", "warns", "alerts", "reviews", "accepts"]:
            items = validation.get(bucket) or []
            if not items:
                continue

            lines.append(f"{bucket.upper()}:")
            for item in items:
                message = item.get("activityMessage") or item.get("message") or str(item)
                severity = item.get("originalSeverity")
                if severity:
                    lines.append(f"- [{severity}] {message}")
                else:
                    lines.append(f"- {message}")
            lines.append("")

        if not validation:
            lines.extend(["Validation:", "- No validation messages returned.", ""])

        lines.append("No live order was placed. This was Schwab previewOrder only.")
        return "\n".join(lines)

    def _grid_row(
        self,
        parent: ttk.Frame,
        row: int,
        label_a: str,
        widget_a: tk.Widget,
        label_b: str,
        widget_b: tk.Widget,
    ) -> None:
        ttk.Label(parent, text=label_a).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=6)
        widget_a.grid(row=row, column=1, sticky="ew", padx=(0, 16), pady=6)
        ttk.Label(parent, text=label_b).grid(row=row, column=2, sticky="w", padx=(0, 8), pady=6)
        widget_b.grid(row=row, column=3, sticky="ew", pady=6)

    def _parse_order(self) -> OrderRequest:
        def optional_float(value: str) -> float | None:
            value = value.strip()
            return float(value) if value else None

        return OrderRequest(
            symbol=self.symbol_var.get(),
            side=OrderSide(self.side_var.get()),
            order_type=OrderType(self.order_type_var.get()),
            quantity=float(self.quantity_var.get()),
            estimated_price=float(self.estimated_price_var.get()),
            limit_price=optional_float(self.limit_price_var.get()),
            stop_price=optional_float(self.stop_price_var.get()),
            time_in_force=TimeInForce(self.time_in_force_var.get()),
            confirmation_text=self.confirmation_var.get(),
        )

    def reload_snapshot(self) -> None:
        try:
            self.broker.reload_portfolio_snapshot()
        except Exception as exc:
            messagebox.showerror("Snapshot reload failed", str(exc))
            return
        self.refresh_portfolio()
        messagebox.showinfo("Snapshot reloaded", self.broker.source_message)

    def refresh_portfolio(self) -> None:
        portfolio = self.broker.get_portfolio()
        self.cash_value_label.configure(text=f"${portfolio.cash:,.2f}")
        self.positions_value_label.configure(text=f"${portfolio.positions_value:,.2f}")
        self.total_value_label.configure(text=f"${portfolio.total_value:,.2f}")
        self.snapshot_source_label.configure(text=f"Snapshot: {self.broker.source_message}")

        for row_id in self.positions_table.get_children():
            self.positions_table.delete(row_id)

        total_value = max(portfolio.total_value, 0.01)
        for symbol in sorted(portfolio.positions):
            p = portfolio.positions[symbol]
            weight = (p.market_value / total_value) * 100
            self.positions_table.insert(
                "",
                tk.END,
                values=(
                    p.symbol,
                    f"{p.quantity:g}",
                    f"${p.average_cost:,.2f}",
                    f"${p.last_price:,.2f}",
                    f"${p.market_value:,.2f}",
                    f"{weight:.1f}%",
                ),
            )

    def preview_order(self) -> None:
        try:
            order = self._parse_order()
            preview = self.broker.preview_order(order)
        except Exception as exc:
            messagebox.showerror("Invalid order", str(exc))
            return

        self.last_preview = preview
        lines = [
            "ORDER PREVIEW",
            "=" * 13,
            f"Symbol: {order.symbol}",
            f"Side: {order.side.value.upper()}",
            f"Type: {order.order_type.value}",
            f"Quantity: {order.quantity:g}",
            f"Estimated notional: ${order.estimated_notional:,.2f}",
            f"Estimated cash after: ${preview.estimated_cash_after:,.2f}",
            f"Estimated position value after: ${preview.estimated_position_value_after:,.2f}",
            "",
            "Status: " + ("BLOCKED" if preview.blocked else "READY FOR PAPER CHECKLIST"),
            "",
            "Warnings:",
        ]
        if preview.warnings:
            lines.extend(f"- {warning}" for warning in preview.warnings)
        else:
            lines.append("- None")

        lines.extend(
            [
                "",
                "Next step:",
                "Click Order Checklist to generate the paper-trade checklist." if not preview.blocked else "Fix the warnings before planning the trade.",
            ]
        )
        self._set_preview_text("\n".join(lines))

    def show_position_size(self) -> None:
        try:
            portfolio = self.broker.get_portfolio()
            entry_price = float(self.estimated_price_var.get())
            stop_price = float(self.stop_price_var.get())
            risk_percent = float(self.risk_percent_var.get())
            plan = calculate_position_size(
                cash_available=portfolio.cash,
                entry_price=entry_price,
                stop_price=stop_price,
                risk_percent_of_cash=risk_percent,
            )
        except Exception as exc:
            messagebox.showerror("Position sizing failed", str(exc))
            return

        text = (
            "POSITION SIZE PLAN\n"
            "==================\n"
            f"Cash basis: ${self.broker.get_portfolio().cash:,.2f}\n"
            f"Entry price: ${entry_price:,.2f}\n"
            f"Stop price: ${stop_price:,.2f}\n"
            f"Risk budget: ${plan.risk_budget:,.2f}\n"
            f"Risk per share: ${plan.risk_per_share:,.2f}\n"
            f"Suggested quantity: {plan.suggested_quantity}\n"
            f"Estimated notional: ${plan.estimated_notional:,.2f}\n\n"
            "This is a sizing helper, not a recommendation."
        )
        self.quantity_var.set(str(plan.suggested_quantity))
        self._set_preview_text(text)

    def show_manual_checklist(self) -> None:
        if self.last_preview is None:
            self.preview_order()
        if self.last_preview is None:
            return
        checklist = build_manual_order_checklist(self.last_preview)
        self._set_preview_text(checklist)

    def submit_order(self) -> None:
        try:
            order = self._parse_order()
            preview = self.broker.preview_order(order)
        except Exception as exc:
            messagebox.showerror("Invalid order", str(exc))
            return

        if preview.blocked:
            self.last_preview = preview
            self.preview_order()
            messagebox.showwarning("Order blocked", "The risk engine blocked this paper order. Review the warnings.")
            return

        ok = messagebox.askyesno(
            "Final paper-order confirmation",
            f"Submit PAPER {order.side.value.upper()} order for {order.quantity:g} {order.symbol}\n"
            f"Estimated notional: ${order.estimated_notional:,.2f}\n\n"
            "This is paper mode only. Continue?",
        )
        if not ok:
            return

        try:
            submitted = self.broker.submit_order(order)
        except Exception as exc:
            messagebox.showerror("Submit failed", str(exc))
            return

        self.confirmation_var.set("")
        self.refresh_portfolio()
        self.preview_order()
        messagebox.showinfo("Paper order submitted", f"Paper order submitted.\nID: {submitted.id}")

    def _set_preview_text(self, content: str) -> None:
        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert(tk.END, content)
        self.preview_text.configure(state=tk.DISABLED)
