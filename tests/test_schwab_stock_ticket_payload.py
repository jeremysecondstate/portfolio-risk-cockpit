from __future__ import annotations

import tkinter as tk
import unittest

from app.ui.schwab_trading_tab import _build_schwab_stock_order_json_from_ui


class _FakeApp:
    def __init__(self) -> None:
        self.master = tk.Tcl()
        self.symbol_var = tk.StringVar(master=self.master, value="RDW")
        self.side_var = tk.StringVar(master=self.master, value="sell")
        self.quantity_var = tk.StringVar(master=self.master, value="33")
        self.order_type_var = tk.StringVar(master=self.master, value="stop_limit")
        self.limit_price_var = tk.StringVar(master=self.master, value="17.98")
        self.stop_price_var = tk.StringVar(master=self.master, value="15.26")
        self.time_in_force_var = tk.StringVar(master=self.master, value="Day")
        self.schwab_stock_session_var = tk.StringVar(master=self.master, value="PM")
        self.schwab_stock_position_effect_var = tk.StringVar(master=self.master, value="CLOSING")


class SchwabStockTicketPayloadTests(unittest.TestCase):
    def test_stock_ticket_includes_session_position_effect_and_order_type(self) -> None:
        payload = _build_schwab_stock_order_json_from_ui(_FakeApp())  # type: ignore[arg-type]
        leg = payload["orderLegCollection"][0]

        self.assertEqual(payload["orderType"], "STOP_LIMIT")
        self.assertEqual(payload["session"], "PM")
        self.assertEqual(payload["duration"], "DAY")
        self.assertEqual(payload["price"], "17.98")
        self.assertEqual(payload["stopPrice"], "15.26")
        self.assertEqual(leg["instruction"], "SELL")
        self.assertEqual(leg["positionEffect"], "CLOSING")

    def test_extended_duration_derives_session_when_session_is_normal(self) -> None:
        app = _FakeApp()
        app.order_type_var.set("limit")
        app.stop_price_var.set("")
        app.side_var.set("buy")
        app.schwab_stock_position_effect_var.set("OPENING")
        app.schwab_stock_session_var.set("NORMAL")
        app.time_in_force_var.set("Day (EXT 13h)")

        payload = _build_schwab_stock_order_json_from_ui(app)  # type: ignore[arg-type]
        leg = payload["orderLegCollection"][0]

        self.assertEqual(payload["orderType"], "LIMIT")
        self.assertEqual(payload["session"], "SEAMLESS")
        self.assertEqual(payload["duration"], "DAY")
        self.assertEqual(leg["positionEffect"], "OPENING")


if __name__ == "__main__":
    unittest.main()
