from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from app.brokers.paper import PaperBroker
from app.core.order_models import OrderRequest, OrderSide, OrderType, TimeInForce


class PortfolioRiskCockpitApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Portfolio Risk Cockpit")
        self.geometry("980x680")
        self.minsize(920, 600)

        self.broker = PaperBroker()

        self.symbol_var = tk.StringVar(value="AMD")
        self.side_var = tk.StringVar(value=OrderSide.BUY.value)
        self.order_type_var = tk.StringVar(value=OrderType.LIMIT.value)
        self.quantity_var = tk.StringVar(value="1")
        self.estimated_price_var = tk.StringVar(value="450.45")
        self.limit_price_var = tk.StringVar(value="450.00")
        self.stop_price_var = tk.StringVar(value="")
        self.time_in_force_var = tk.StringVar(value=TimeInForce.DAY.value)
        self.confirmation_var = tk.StringVar(value="")

        self._build_layout()
        self.refresh_portfolio()

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(root)
        header.pack(fill=tk.X)

        ttk.Label(header, text="Portfolio Risk Cockpit", font=("Segoe UI", 18, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, text="PAPER MODE", foreground="green", font=("Segoe UI", 12, "bold")).pack(side=tk.RIGHT)

        body = ttk.PanedWindow(root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        left = ttk.Frame(body, padding=8)
        right = ttk.Frame(body, padding=8)
        body.add(left, weight=3)
        body.add(right, weight=2)

        self._build_portfolio_panel(left)
        self._build_order_panel(right)

    def _build_portfolio_panel(self, parent: ttk.Frame) -> None:
        summary = ttk.LabelFrame(parent, text="Account Snapshot", padding=10)
        summary.pack(fill=tk.X)

        self.cash_label = ttk.Label(summary, text="Cash: --")
        self.cash_label.pack(anchor=tk.W)
        self.positions_value_label = ttk.Label(summary, text="Positions: --")
        self.positions_value_label.pack(anchor=tk.W)
        self.total_value_label = ttk.Label(summary, text="Total: --")
        self.total_value_label.pack(anchor=tk.W)

        positions_frame = ttk.LabelFrame(parent, text="Paper Positions", padding=10)
        positions_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        columns = ("symbol", "qty", "avg_cost", "last", "value")
        self.positions_table = ttk.Treeview(positions_frame, columns=columns, show="headings", height=18)
        for column, label, width in [
            ("symbol", "Symbol", 80),
            ("qty", "Qty", 100),
            ("avg_cost", "Avg Cost", 110),
            ("last", "Last", 110),
            ("value", "Value", 120),
        ]:
            self.positions_table.heading(column, text=label)
            self.positions_table.column(column, width=width, anchor=tk.E)
        self.positions_table.column("symbol", anchor=tk.W)
        self.positions_table.pack(fill=tk.BOTH, expand=True)

        ttk.Button(parent, text="Refresh", command=self.refresh_portfolio).pack(anchor=tk.E, pady=(10, 0))

    def _build_order_panel(self, parent: ttk.Frame) -> None:
        ticket = ttk.LabelFrame(parent, text="Guarded Order Ticket", padding=10)
        ticket.pack(fill=tk.X)

        self._row(ticket, "Symbol", ttk.Entry(ticket, textvariable=self.symbol_var))
        self._row(ticket, "Side", ttk.Combobox(ticket, textvariable=self.side_var, values=[s.value for s in OrderSide], state="readonly"))
        self._row(ticket, "Order type", ttk.Combobox(ticket, textvariable=self.order_type_var, values=[o.value for o in OrderType], state="readonly"))
        self._row(ticket, "Quantity", ttk.Entry(ticket, textvariable=self.quantity_var))
        self._row(ticket, "Est. price", ttk.Entry(ticket, textvariable=self.estimated_price_var))
        self._row(ticket, "Limit price", ttk.Entry(ticket, textvariable=self.limit_price_var))
        self._row(ticket, "Stop price", ttk.Entry(ticket, textvariable=self.stop_price_var))
        self._row(ticket, "Time in force", ttk.Combobox(ticket, textvariable=self.time_in_force_var, values=[t.value for t in TimeInForce], state="readonly"))
        self._row(ticket, "Type CONFIRM", ttk.Entry(ticket, textvariable=self.confirmation_var))

        buttons = ttk.Frame(ticket)
        buttons.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(buttons, text="Preview Risk", command=self.preview_order).pack(side=tk.LEFT)
        ttk.Button(buttons, text="Submit Paper Order", command=self.submit_order).pack(side=tk.RIGHT)

        results = ttk.LabelFrame(parent, text="Risk Preview", padding=10)
        results.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        self.preview_text = tk.Text(results, height=18, wrap=tk.WORD)
        self.preview_text.pack(fill=tk.BOTH, expand=True)
        self.preview_text.insert(tk.END, "Create an order and click Preview Risk.\n")
        self.preview_text.configure(state=tk.DISABLED)

    def _row(self, parent: ttk.Frame, label: str, widget: tk.Widget) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X, pady=4)
        ttk.Label(frame, text=label, width=14).pack(side=tk.LEFT)
        widget.pack(side=tk.RIGHT, fill=tk.X, expand=True)

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

    def refresh_portfolio(self) -> None:
        portfolio = self.broker.get_portfolio()
        self.cash_label.configure(text=f"Cash: ${portfolio.cash:,.2f}")
        self.positions_value_label.configure(text=f"Positions: ${portfolio.positions_value:,.2f}")
        self.total_value_label.configure(text=f"Total: ${portfolio.total_value:,.2f}")

        for row_id in self.positions_table.get_children():
            self.positions_table.delete(row_id)

        for symbol in sorted(portfolio.positions):
            p = portfolio.positions[symbol]
            self.positions_table.insert(
                "",
                tk.END,
                values=(
                    p.symbol,
                    f"{p.quantity:g}",
                    f"${p.average_cost:,.2f}",
                    f"${p.last_price:,.2f}",
                    f"${p.market_value:,.2f}",
                ),
            )

    def preview_order(self) -> None:
        try:
            order = self._parse_order()
            preview = self.broker.preview_order(order)
        except Exception as exc:
            messagebox.showerror("Invalid order", str(exc))
            return

        lines = [
            f"Symbol: {order.symbol}",
            f"Side: {order.side.value.upper()}",
            f"Type: {order.order_type.value}",
            f"Quantity: {order.quantity:g}",
            f"Estimated notional: ${order.estimated_notional:,.2f}",
            f"Estimated cash after: ${preview.estimated_cash_after:,.2f}",
            f"Estimated position value after: ${preview.estimated_position_value_after:,.2f}",
            "",
            "Status: " + ("BLOCKED" if preview.blocked else "READY, pending final review"),
            "",
            "Warnings:",
        ]
        if preview.warnings:
            lines.extend(f"- {warning}" for warning in preview.warnings)
        else:
            lines.append("- None")

        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert(tk.END, "\n".join(lines))
        self.preview_text.configure(state=tk.DISABLED)

    def submit_order(self) -> None:
        try:
            order = self._parse_order()
            preview = self.broker.preview_order(order)
        except Exception as exc:
            messagebox.showerror("Invalid order", str(exc))
            return

        if preview.blocked:
            self.preview_order()
            messagebox.showwarning("Order blocked", "The risk engine blocked this order. Review the warnings.")
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
