from __future__ import annotations

import unittest

from app.brokers.schwab import order_management
from app.ui import options_lab_extension
from app.ui.schwab_order_management_extension import _fill_main_ticket_from_schwab_row


class _Var:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: object) -> None:
        self.value = str(value)


class _App:
    def __init__(self) -> None:
        self.symbol_var = _Var()
        self.side_var = _Var()
        self.order_type_var = _Var()
        self.quantity_var = _Var()
        self.limit_price_var = _Var()
        self.estimated_price_var = _Var()
        self.stop_price_var = _Var()
        self.time_in_force_var = _Var()
        self.api_called = False

    def _authorize_schwab_session(self):  # pragma: no cover - should not be called
        self.api_called = True
        raise AssertionError("fill-main-ticket-only must not authorize Schwab")


class _SelectionTable:
    def __init__(self, row: order_management.SchwabOrderRow) -> None:
        self._schwab_order_rows_by_iid = {"selected": row}

    def selection(self) -> list[str]:
        return ["selected"]


class SchwabOrderManagementTests(unittest.TestCase):
    def _sample_order(self, *, status: str = "WORKING") -> dict[str, object]:
        return {
            "orderId": 12345,
            "enteredTime": "2026-06-03T15:30:00+0000",
            "status": status,
            "orderType": "LIMIT",
            "price": 18.75,
            "duration": "DAY",
            "session": "NORMAL",
            "filledQuantity": 2,
            "orderLegCollection": [
                {
                    "instruction": "BUY",
                    "quantity": 10,
                    "instrument": {"symbol": "RDW", "assetType": "EQUITY"},
                }
            ],
        }

    def test_parses_schwab_order_into_table_row(self) -> None:
        row = order_management.order_to_row(self._sample_order(), account_hash="abcd1234wxyz")

        self.assertEqual(row.order_id, "12345")
        self.assertEqual(row.status, "WORKING")
        self.assertEqual(row.symbol, "RDW")
        self.assertEqual(row.asset_type, "EQUITY")
        self.assertEqual(row.instruction, "BUY")
        self.assertEqual(row.quantity, 10)
        self.assertEqual(row.filled_quantity, 2)
        self.assertEqual(row.remaining_quantity, 8)
        self.assertEqual(row.masked_account_hash, "abcd...wxyz")

    def test_identifies_active_and_terminal_statuses(self) -> None:
        self.assertTrue(order_management.is_open_order_status("WORKING"))
        self.assertTrue(order_management.is_open_order_status("queued"))
        self.assertFalse(order_management.is_open_order_status("FILLED"))
        self.assertTrue(order_management.is_terminal_order_status("CANCELED"))

    def test_builds_replacement_json_from_edited_fields(self) -> None:
        row = order_management.order_to_row(self._sample_order(), account_hash="acct")

        payload = order_management.build_replacement_order_json(
            row,
            {
                "symbol": "rdw",
                "instruction": "SELL",
                "quantity": "8",
                "order_type": "LIMIT",
                "limit_price": "19.25",
                "stop_price": "",
                "time_in_force": "GTC",
                "asset_type": "EQUITY",
            },
        )

        self.assertEqual(payload["orderType"], "LIMIT")
        self.assertEqual(payload["duration"], "GOOD_TILL_CANCEL")
        self.assertEqual(payload["price"], "19.25")
        leg = payload["orderLegCollection"][0]
        self.assertEqual(leg["instruction"], "SELL")
        self.assertEqual(leg["quantity"], 8.0)
        self.assertEqual(leg["instrument"]["symbol"], "RDW")

    def test_refuses_to_replace_terminal_orders(self) -> None:
        row = order_management.order_to_row(self._sample_order(status="FILLED"), account_hash="acct")

        with self.assertRaisesRegex(ValueError, "not eligible"):
            order_management.validate_replace_allowed(row, "REPLACE 12345")

    def test_requires_typed_confirmation_for_replace_and_cancel(self) -> None:
        row = order_management.order_to_row(self._sample_order(), account_hash="acct")

        self.assertFalse(order_management.confirmation_matches("REPLACE", row.order_id, "replace 12345"))
        self.assertTrue(order_management.confirmation_matches("REPLACE", row.order_id, "REPLACE 12345"))
        with self.assertRaisesRegex(ValueError, "Type exactly"):
            order_management.validate_cancel_allowed(row, "CANCEL")

    def test_fill_main_ticket_only_does_not_authorize_or_call_schwab(self) -> None:
        row = order_management.order_to_row(self._sample_order(), account_hash="acct")
        app = _App()

        _fill_main_ticket_from_schwab_row(app, row)

        self.assertFalse(app.api_called)
        self.assertEqual(app.symbol_var.get(), "RDW")
        self.assertEqual(app.side_var.get(), "buy")
        self.assertEqual(app.order_type_var.get(), "limit")
        self.assertEqual(app.quantity_var.get(), "8")
        self.assertEqual(app.limit_price_var.get(), "18.75")

    def test_double_click_handler_opens_dialog_without_api_call(self) -> None:
        row = order_management.order_to_row(self._sample_order(), account_hash="acct")

        class App:
            schwab_open_orders_table = _SelectionTable(row)

            def __init__(self) -> None:
                self.opened = None
                self.api_called = False

            def show_schwab_working_order_dialog(self, selected_row) -> None:
                self.opened = selected_row

            def _authorize_schwab_session(self):  # pragma: no cover - should not be called
                self.api_called = True
                raise AssertionError("double-click must not call Schwab")

        app = App()

        options_lab_extension._open_selected_schwab_order_dialog(app)

        self.assertIs(app.opened, row)
        self.assertFalse(app.api_called)


if __name__ == "__main__":
    unittest.main()
