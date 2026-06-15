from __future__ import annotations

import tkinter as tk
import unittest
from typing import Any

from app.core.order_models import (
    SCHWAB_EQUITY_TIME_IN_FORCE_ALIASES,
    SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES,
    normalize_time_in_force,
    schwab_equity_session_duration,
)
from app.ui.schwab_exto_time_in_force_extension import install_schwab_exto_time_in_force_extension
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
        self.time_in_force_var = tk.StringVar(master=self.master, value="DAY")
        self.schwab_stock_session_var = tk.StringVar(master=self.master, value="PM")
        self.schwab_stock_position_effect_var = tk.StringVar(master=self.master, value="CLOSING")


class _FakeInstallApp:
    def _build_layout(self, *args: Any, **kwargs: Any) -> None:
        return None


class SchwabStockTicketPayloadTests(unittest.TestCase):
    def test_stock_ticket_includes_position_effect_and_order_type(self) -> None:
        payload = _build_schwab_stock_order_json_from_ui(_FakeApp())  # type: ignore[arg-type]
        leg = payload["orderLegCollection"][0]

        self.assertEqual(payload["orderType"], "STOP_LIMIT")
        self.assertEqual(payload["session"], "NORMAL")
        self.assertEqual(payload["duration"], "DAY")
        self.assertEqual(payload["price"], "17.98")
        self.assertEqual(payload["stopPrice"], "15.26")
        self.assertEqual(leg["instruction"], "SELL")
        self.assertEqual(leg["positionEffect"], "CLOSING")

    def test_extended_tif_derives_session_and_duration(self) -> None:
        app = _FakeApp()
        app.order_type_var.set("limit")
        app.stop_price_var.set("")
        app.side_var.set("buy")
        app.schwab_stock_position_effect_var.set("OPENING")
        app.schwab_stock_session_var.set("NORMAL")
        app.time_in_force_var.set("EXT")

        payload = _build_schwab_stock_order_json_from_ui(app)  # type: ignore[arg-type]
        leg = payload["orderLegCollection"][0]

        self.assertEqual(payload["orderType"], "LIMIT")
        self.assertEqual(payload["session"], "SEAMLESS")
        self.assertEqual(payload["duration"], "DAY")
        self.assertEqual(leg["positionEffect"], "OPENING")

    def test_tos_time_in_force_choices_are_available_in_order(self) -> None:
        self.assertEqual(
            SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES,
            ("DAY", "GTC", "EXT", "GTC_EXT", "EXTO", "GTC_EXTO", "AM", "PM"),
        )

    def test_exo_is_not_a_visible_choice_or_alias(self) -> None:
        self.assertNotIn("EXO", SCHWAB_EQUITY_TIME_IN_FORCE_CHOICES)
        self.assertNotIn("exo", SCHWAB_EQUITY_TIME_IN_FORCE_ALIASES)
        with self.assertRaises(ValueError):
            normalize_time_in_force("EXO")

    def test_supported_tifs_map_to_documented_api_session_and_duration(self) -> None:
        expected = {
            "DAY": ("NORMAL", "DAY"),
            "GTC": ("NORMAL", "GOOD_TILL_CANCEL"),
            "EXT": ("SEAMLESS", "DAY"),
            "GTC_EXT": ("SEAMLESS", "GOOD_TILL_CANCEL"),
            "AM": ("AM", "DAY"),
            "PM": ("PM", "DAY"),
        }
        for tif, api_fields in expected.items():
            with self.subTest(tif=tif):
                self.assertEqual(schwab_equity_session_duration(tif), api_fields)

    def test_ticket_maps_supported_tifs_to_payload_fields(self) -> None:
        expected = {
            "DAY": ("NORMAL", "DAY"),
            "GTC": ("NORMAL", "GOOD_TILL_CANCEL"),
            "EXT": ("SEAMLESS", "DAY"),
            "GTC_EXT": ("SEAMLESS", "GOOD_TILL_CANCEL"),
            "AM": ("AM", "DAY"),
            "PM": ("PM", "DAY"),
        }
        for tif, (session, duration) in expected.items():
            with self.subTest(tif=tif):
                app = _FakeApp()
                app.order_type_var.set("limit")
                app.stop_price_var.set("")
                app.time_in_force_var.set(tif)

                payload = _build_schwab_stock_order_json_from_ui(app)  # type: ignore[arg-type]

                self.assertEqual(payload["session"], session)
                self.assertEqual(payload["duration"], duration)

    def test_exto_visible_tifs_are_blocked_because_api_session_is_undocumented(self) -> None:
        for tif in ("EXTO", "GTC_EXTO"):
            with self.subTest(tif=tif):
                app = _FakeApp()
                app.order_type_var.set("limit")
                app.stop_price_var.set("")
                app.time_in_force_var.set(tif)

                with self.assertRaisesRegex(ValueError, "does not list EXTO"):
                    _build_schwab_stock_order_json_from_ui(app)  # type: ignore[arg-type]

    def test_extended_hours_tifs_require_limit_orders(self) -> None:
        cases = [
            ("EXT", "market"),
            ("GTC_EXT", "stop_limit"),
            ("AM", "stop"),
            ("PM", "market"),
        ]
        for tif, order_type in cases:
            with self.subTest(tif=tif, order_type=order_type):
                app = _FakeApp()
                app.order_type_var.set(order_type)
                app.time_in_force_var.set(tif)

                with self.assertRaisesRegex(ValueError, "must use Order type LIMIT"):
                    _build_schwab_stock_order_json_from_ui(app)  # type: ignore[arg-type]

    def test_standalone_stop_order_includes_stop_price(self) -> None:
        app = _FakeApp()
        app.order_type_var.set("stop")
        app.limit_price_var.set("")
        app.stop_price_var.set("15.26")
        app.time_in_force_var.set("DAY")

        payload = _build_schwab_stock_order_json_from_ui(app)  # type: ignore[arg-type]

        self.assertEqual(payload["orderType"], "STOP")
        self.assertEqual(payload["stopPrice"], "15.26")
        self.assertNotIn("price", payload)

    def test_standalone_stop_limit_order_includes_stop_price(self) -> None:
        app = _FakeApp()
        app.order_type_var.set("stop_limit")
        app.limit_price_var.set("17.98")
        app.stop_price_var.set("15.26")
        app.time_in_force_var.set("GTC")

        payload = _build_schwab_stock_order_json_from_ui(app)  # type: ignore[arg-type]

        self.assertEqual(payload["orderType"], "STOP_LIMIT")
        self.assertEqual(payload["duration"], "GOOD_TILL_CANCEL")
        self.assertEqual(payload["price"], "17.98")
        self.assertEqual(payload["stopPrice"], "15.26")

    def test_stop_price_on_non_stop_order_is_blocked(self) -> None:
        app = _FakeApp()
        app.order_type_var.set("limit")
        app.stop_price_var.set("15.26")

        with self.assertRaisesRegex(ValueError, "Stop price can only be used"):
            _build_schwab_stock_order_json_from_ui(app)  # type: ignore[arg-type]

    def test_exto_session_override_is_not_emitted_after_ui_extension_install(self) -> None:
        install_schwab_exto_time_in_force_extension(_FakeInstallApp)  # type: ignore[arg-type]
        app = _FakeApp()
        app.order_type_var.set("limit")
        app.stop_price_var.set("")
        app.schwab_stock_session_var.set("EXTO")
        app.time_in_force_var.set("DAY")

        payload = _build_schwab_stock_order_json_from_ui(app)  # type: ignore[arg-type]

        self.assertEqual(payload["session"], "NORMAL")
        self.assertEqual(payload["duration"], "DAY")


if __name__ == "__main__":
    unittest.main()
